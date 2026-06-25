"""Phase 3a Task 2: delta manifest + delta-filtered extract_graph.

Verifies the headline incremental guarantee: extract_graph only processes
NEW chunks; old chunks (with on-disk extractions) are never re-LLM'd.
"""

import asyncio
import json
import pathlib

import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, Chunk, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def repo_with_old_index(tmp_path):
    """A KB that already has a full index (old chunks in extractions/ + entities.parquet)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="old", text="Old ACME Org text " * 200)
    # Simulate old chunks already in DB + on-disk extractions (old chunks
    # should not be re-extracted by the incremental pass).
    fake = FakeGraphAdapter()
    old_chunks = fake.chunk_document(1, repo.get_documents(1)[0].text)
    with session_scope(engine) as s:
        for i, c in enumerate(old_chunks):
            s.add(Chunk(chunk_id=c.chunk_id, kb_id=1, document_id=1, ordinal=i, text=c.text))
    # Write old extractions to disk (simulates a prior full job having run).
    (pathlib.Path(tmp_path) / "extractions").mkdir(exist_ok=True)
    for c in old_chunks:
        r = fake.extract_chunk_sync(c.chunk_id, c.text)
        (pathlib.Path(tmp_path) / "extractions" / f"{c.chunk_id}.json").write_text(
            json.dumps({
                "entities": r.entities.to_dict("records"),
                "relationships": r.relationships.to_dict("records"),
            })
        )
    return repo, str(tmp_path), {c.chunk_id for c in old_chunks}


def test_delta_extract_only_processes_new_chunks(repo_with_old_index):
    """ExtractGraphDeltaStrategy.next_units_batch returns ONLY new chunk_ids."""
    from kb_platform.engine.incremental import ExtractGraphDeltaStrategy, register_delta_strategies
    from kb_platform.engine.strategy import STRATEGIES, register_strategy
    from kb_platform.engine.unit_worker import UnitWorker

    repo, data_root, old_ids = repo_with_old_index
    register_delta_strategies()  # placeholder; real registration in Task 4
    # Add a new document -> chunk it -> new chunk_id set.
    repo.add_document(kb_id=1, title="new", text="New Globex Corp text " * 200)
    fake = FakeGraphAdapter()
    new_doc = repo.get_documents(1)[1]
    new_chunks = fake.chunk_document(new_doc.id, new_doc.text)
    new_ids = {c.chunk_id for c in new_chunks}
    # In the real incremental flow, load_update_documents chunks the new doc
    # and inserts the rows; replicate that here so the delta strategy can see them.
    with session_scope(repo.engine) as s:
        for i, c in enumerate(new_chunks):
            s.add(Chunk(chunk_id=c.chunk_id, kb_id=1, document_id=new_doc.id, ordinal=i, text=c.text))
    # Build an incremental extract step; delta manifest = new chunk_ids.
    job = repo.create_job(
        kb_id=1,
        type="incremental",
        specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)],
    )
    step = job.steps[0]
    # Run with the delta strategy (only new chunks). Save/restore the global
    # "extract_graph" strategy so this test does not pollute other tests that
    # rely on the default full-index ExtractGraphStrategy.
    saved = STRATEGIES.get("extract_graph")
    try:
        register_strategy("extract_graph", ExtractGraphDeltaStrategy(new_ids))
        worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
        asyncio.run(worker.run_unit_fanout(step))
    finally:
        if saved is not None:
            register_strategy("extract_graph", saved)
    # Assert: only new chunks processed (unit.subject_id only contains new chunk_ids).
    processed = {u.subject_id for u in repo.list_units(step.id)}
    assert processed == new_ids
    assert not (processed & old_ids)  # not a single old chunk was re-extracted


def test_read_delta_manifest_round_trips(tmp_path):
    """read_delta_manifest reads what load_update_documents writes."""
    from kb_platform.engine.incremental import read_delta_manifest

    p = pathlib.Path(tmp_path) / "delta_manifest.json"
    p.write_text(json.dumps(["a", "b", "c"]))
    assert read_delta_manifest(str(tmp_path)) == {"a", "b", "c"}


def test_read_delta_manifest_missing_returns_empty_set(tmp_path):
    from kb_platform.engine.incremental import read_delta_manifest

    assert read_delta_manifest(str(tmp_path)) == set()


def test_load_update_documents_chunks_only_new_docs(tmp_path):
    """load_update_documents chunks docs that have no chunks yet and writes manifest."""
    from kb_platform.engine.incremental import load_update_documents

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    fake = FakeGraphAdapter()
    # old doc: already chunked
    repo.add_document(kb_id=1, title="old", text="Old ACME Org text " * 200)
    old_doc = repo.get_documents(1)[0]
    old_chunks = fake.chunk_document(old_doc.id, old_doc.text)
    with session_scope(engine) as s:
        for i, c in enumerate(old_chunks):
            s.add(Chunk(chunk_id=c.chunk_id, kb_id=1, document_id=old_doc.id, ordinal=i, text=c.text))
    # new doc: not yet chunked
    repo.add_document(kb_id=1, title="new", text="New Globex Corp text " * 200)
    new_doc = repo.get_documents(1)[1]
    expected_new_ids = {c.chunk_id for c in fake.chunk_document(new_doc.id, new_doc.text)}

    job = repo.create_job(
        kb_id=1,
        type="incremental",
        specs=[StepSpec("load_update_documents", StepKind.ATOMIC)],
    )
    load_update_documents(repo, fake, job.steps[0])

    # Manifest contains exactly the new doc's chunk_ids.
    manifest_path = pathlib.Path(tmp_path) / "delta_manifest.json"
    written = set(json.loads(manifest_path.read_text()))
    assert written == expected_new_ids
    # Old doc chunk_ids must NOT appear in the manifest.
    assert written.isdisjoint({c.chunk_id for c in old_chunks})
