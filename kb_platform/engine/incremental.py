"""Incremental indexing: new-doc load + delta manifest + delta extract strategy.

This module implements the core "don't re-parse old" guarantee of Phase 3a:

* ``load_update_documents`` chunks only documents that have no chunks yet
  (= new documents) and writes their chunk_ids to ``delta_manifest.json``.
* ``read_delta_manifest`` reads that manifest back as a set of chunk_ids.
* ``ExtractGraphDeltaStrategy(new_chunk_ids)`` subclasses ``ExtractGraphStrategy``
  and overrides ``next_units_batch`` so that only chunks in ``new_chunk_ids``
  (with PENDING/None unit) are returned. Old chunks are never re-LLM'd.
* ``register_delta_strategies`` is a placeholder; the real registration of the
  delta extract strategy happens in the orchestrator during an incremental job
  (Task 4), where the manifest is read and the strategy is constructed with the
  concrete new_chunk_ids.
"""

import json
from pathlib import Path

from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import UnitStatus
from kb_platform.db.models import Chunk, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject
from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy

MANIFEST = "delta_manifest.json"


def load_update_documents(repo: Repository, adapter, step) -> None:
    """Chunk documents that have no chunks yet (= new) and write delta_manifest.json.

    A document is considered "new" if no row exists in the ``chunk`` table for
    its ``document_id``. This is deterministic and does not depend on timestamps,
    which keeps the function testable and idempotent across job retries.
    """
    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        data_root = Path(kb.data_root)
    new_chunk_ids: list[str] = []
    for doc in repo.get_documents(job.kb_id):
        # Skip documents that already have chunks (= already indexed, old).
        with session_scope(repo.engine) as s:
            has_chunk = s.scalar(select(Chunk).where(Chunk.document_id == doc.id).limit(1))
        if has_chunk is not None:
            continue
        pieces = adapter.chunk_document(doc.id, doc.text or "")
        chunks: list[Chunk] = []
        for ordinal, p in enumerate(pieces):
            chunks.append(Chunk(chunk_id=p.chunk_id, kb_id=job.kb_id, document_id=doc.id, ordinal=ordinal, text=p.text))
            new_chunk_ids.append(p.chunk_id)
        repo.add_chunks(chunks)
    (data_root / MANIFEST).write_text(json.dumps(new_chunk_ids))


def read_delta_manifest(data_root) -> set[str]:
    """Read delta_manifest.json under ``data_root`` and return its chunk_ids as a set.

    Returns an empty set if the manifest does not exist (e.g. first run or a
    full job that never wrote one).
    """
    p = Path(data_root) / MANIFEST
    return set(json.loads(p.read_text())) if p.exists() else set()


class ExtractGraphDeltaStrategy(ExtractGraphStrategy):
    """``extract_graph`` variant that only processes chunks in ``new_chunk_ids``.

    Inherits ``run_unit``, ``persist`` and ``finalize`` from ``ExtractGraphStrategy``
    (Phase 2a). Only ``next_units_batch`` is overridden to filter to new chunks,
    which is the entire point of the incremental guarantee: old chunks never get
    re-scheduled for LLM extraction.
    """

    def __init__(self, new_chunk_ids: set[str]) -> None:
        self._new = set(new_chunk_ids)

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        job = repo.get_job(step.job_id)
        chunks = repo.get_chunks(job.kb_id)
        pending: list[Subject] = []
        for c in chunks:
            if c.chunk_id not in self._new:
                continue
            u = repo.get_unit_by_subject(step.id, "chunk", c.chunk_id)
            if u is None or u.status == UnitStatus.PENDING:
                pending.append(Subject("chunk", c.chunk_id))
        return pending or None


def register_delta_strategies() -> None:
    """Placeholder for delta-strategy registration.

    The real registration of ``ExtractGraphDeltaStrategy`` happens in the
    orchestrator when running an incremental job (Task 4): it reads
    ``read_delta_manifest(data_root)`` and then calls
    ``register_strategy("extract_graph", ExtractGraphDeltaStrategy(new_ids))``.
    This function exists so tests can exercise the import/registration surface
    without coupling to the orchestrator wiring, and remains a no-op here so it
    never clobbers the default full-index strategy registered at import time.
    """
    return None
