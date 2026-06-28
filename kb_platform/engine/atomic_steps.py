"""Atomic (non-unit) indexing steps."""

import json
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository

logger = logging.getLogger(__name__)


def _data_root(repo: Repository, step) -> Path:
    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        return Path(kb.data_root)


def finalize_graph(repo: Repository, adapter, step) -> None:
    root = _data_root(repo, step)
    entities = pd.read_parquet(root / "entities.parquet")
    relationships = pd.read_parquet(root / "relationships.parquet")
    e2, r2 = adapter.finalize_entities_relationships(entities, relationships)
    e2.to_parquet(root / "entities.parquet")
    r2.to_parquet(root / "relationships.parquet")


def create_communities(repo: Repository, adapter, step) -> None:
    root = _data_root(repo, step)
    relationships = pd.read_parquet(root / "relationships.parquet")
    communities = adapter.cluster_relationships(relationships)
    communities.to_parquet(root / "communities.parquet")


def merge_delta(repo: Repository, adapter, step) -> None:
    """Re-merge on-disk chunk extractions whose chunk still exists in the control plane.

    Extractions are cached per chunk under ``data_root/extractions/<chunk_id>.json``.
    A document deletion removes the Chunk row but leaves its extraction file behind,
    so globbing every file would re-merge orphans and keep deleted entities alive.
    The chunk table is the source of truth: only extractions whose ``chunk_id`` is
    still present are loaded, and orphan files are best-effort pruned (an unlink
    failure never blocks the merge — the filter already guarantees correctness).
    No LLM.
    """
    from kb_platform.graph.adapter import ExtractionResult

    root = _data_root(repo, step)
    job = repo.get_job(step.job_id)
    live = {c.chunk_id for c in repo.get_chunks(job.kb_id)}
    extraction_dir = root / "extractions"
    results: list[ExtractionResult] = []
    if extraction_dir.exists():
        for p in sorted(extraction_dir.glob("*.json")):
            if p.stem not in live:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    logger.warning("merge_delta: could not prune orphan extraction %s", p)
                continue
            raw = json.loads(p.read_text())
            results.append(
                ExtractionResult(
                    entities=pd.DataFrame(raw["entities"]),
                    relationships=pd.DataFrame(raw["relationships"]),
                )
            )
    entities, relationships = adapter.merge_extractions(results)
    entities.to_parquet(root / "entities.parquet")
    relationships.to_parquet(root / "relationships.parquet")


def write_text_units_parquet(data_root: Path, chunks) -> None:
    """Write text_units.parquet from a list of Chunk rows.

    Shared by the full path (``_chunk_documents``) and the incremental wrap-up
    (``update_clean_state``) so both produce identical parquet. Columns mirror
    graphrag's text_units layout: id (=chunk_id), text, document_ids, n_tokens.
    No-op when there are no chunks (leaves any existing file untouched).
    """
    if not chunks:
        return
    pd.DataFrame(
        [
            {
                "id": c.chunk_id,
                "text": c.text,
                "document_ids": [str(c.document_id)],
                "n_tokens": 0,
            }
            for c in chunks
        ]
    ).to_parquet(data_root / "text_units.parquet")


def update_clean_state(repo: Repository, adapter, step) -> None:  # noqa: ARG001
    """Rebuild text_units.parquet from the chunk table (incremental wrap-up).

    ``load_update_documents`` writes new chunk rows to the DB but never updates
    text_units.parquet, so without this step the embeddings step would miss the
    new chunks' text (local search over text units would silently skip new
    documents). Rebuild from ALL chunks (old + new) right before embeddings.
    """
    root = _data_root(repo, step)
    job = repo.get_job(step.job_id)
    write_text_units_parquet(root, repo.get_chunks(job.kb_id))


def _as_text(value) -> str:
    """Flatten a cell that may be a str, list, or numpy array of str (e.g. the
    merged ``description`` column, which ``merge_extractions`` aggregates as a
    list and which parquet round-trips as a numpy array)."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    try:
        import numpy as np  # noqa: PLC0415

        if isinstance(value, np.ndarray):
            return " ".join(str(v) for v in value.tolist())
    except Exception:  # noqa: BLE001
        pass
    return str(value) if value is not None else ""


def generate_text_embeddings(repo: Repository, adapter, step, vector_store) -> None:
    """Read 3 parquet collections → batch embed via ``adapter.embed_items`` →
    upsert into ``vector_store``.

    Index names are graphrag's canonical embedding names
    (``entity_description`` / ``text_unit_text`` / ``community_full_content``)
    and the document ``id`` is the parquet row's identity column, so the
    query read path can map embedding hits back to entities/text-units.
    """
    root = _data_root(repo, step)
    # (embedding_name, parquet, id_column, text_fn)
    collections = [
        (
            "text_unit_text",
            root / "text_units.parquet",
            lambda r: str(r.get("id") or r.get("chunk_id") or ""),
            lambda r: _as_text(r.get("text")),
        ),
        (
            "entity_description",
            root / "entities.parquet",
            lambda r: str(r.get("title") or r.get("id") or ""),
            lambda r: f"{r.get('title', '')} {_as_text(r.get('description'))}",
        ),
        (
            "community_full_content",
            root / "community_reports.parquet",
            lambda r: str(r.get("community") or r.get("id") or ""),
            lambda r: _as_text(r.get("full_content")),
        ),
    ]
    for index_name, parquet_path, id_fn, text_fn in collections:
        if not parquet_path.exists():
            continue
        df = pd.read_parquet(parquet_path)
        if df.empty:
            continue
        records = df.to_dict("records")
        texts = [text_fn(row) for row in records]
        vectors = adapter.embed_items(texts)
        items = [
            {"id": id_fn(records[i]), "text": texts[i], "vector": vectors[i]}
            for i in range(len(records))
        ]
        vector_store.upsert(index_name, items)
