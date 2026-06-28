import json
from pathlib import Path

import pandas as pd

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, Chunk, Document, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import merge_delta
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return Repository(engine)


def _merge_step(repo):
    return repo.create_job(kb_id=1, type="incremental", specs=[StepSpec("merge_delta", StepKind.ATOMIC)]).steps[0]


def _ext(tmp_path, name, entities, relationships=None):
    (tmp_path / "extractions").mkdir(exist_ok=True)
    (tmp_path / "extractions" / f"{name}.json").write_text(
        json.dumps({"entities": entities, "relationships": relationships or []})
    )


def _add_doc_with_chunks(repo, doc_id, chunk_ids):
    with session_scope(repo.engine) as s:
        s.add(Document(id=doc_id, kb_id=1, title=f"d{doc_id}", source_uri="", content_hash=f"h{doc_id}", status="parsed", bytes=1, text="t"))
        s.flush()  # materialize the Document row so FK on Chunk passes under foreign_keys=ON
        for ordinal, cid in enumerate(chunk_ids):
            s.add(Chunk(chunk_id=cid, kb_id=1, document_id=doc_id, ordinal=ordinal, text=cid))


def test_merge_delta_combines_extractions_for_live_chunks(tmp_path):
    repo = _setup(tmp_path)
    _add_doc_with_chunks(repo, 1, ["old", "new"])  # 两个 chunk 都存活
    _ext(tmp_path, "old", [{"title": "ACME", "type": "ORG", "description": "old desc", "source_id": "old"}])
    _ext(tmp_path, "new", [
        {"title": "ACME", "type": "ORG", "description": "new desc", "source_id": "new"},
        {"title": "GLOBEX", "type": "ORG", "description": "globex", "source_id": "new"},
    ], relationships=[{"source": "ACME", "target": "GLOBEX", "weight": 1.0, "description": "acquires", "source_id": "new"}])
    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    rels = pd.read_parquet(tmp_path / "relationships.parquet")
    assert set(ents["title"]) == {"ACME", "GLOBEX"}
    assert ents[ents["title"] == "ACME"].iloc[0]["frequency"] == 2
    assert len(rels) == 1 and rels.iloc[0]["source"] == "ACME" and rels.iloc[0]["target"] == "GLOBEX"


def test_merge_delta_filters_and_prunes_orphan_extractions(tmp_path):
    repo = _setup(tmp_path)
    _add_doc_with_chunks(repo, 1, ["keep1", "keep2"])  # gone 不在表里
    _ext(tmp_path, "keep1", [{"title": "KEEP1", "type": "ORG", "description": "k1", "source_id": "keep1"}])
    _ext(tmp_path, "keep2", [{"title": "KEEP2", "type": "ORG", "description": "k2", "source_id": "keep2"}])
    _ext(tmp_path, "gone", [{"title": "GONE", "type": "ORG", "description": "g", "source_id": "gone"}])
    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    assert set(ents["title"]) == {"KEEP1", "KEEP2"}          # 孤儿 GONE 不合并
    assert not (tmp_path / "extractions" / "gone.json").exists()  # 孤儿文件被清
    assert (tmp_path / "extractions" / "keep1.json").exists()     # 存活文件保留
    assert (tmp_path / "extractions" / "keep2.json").exists()


def test_merge_delta_prune_failure_is_best_effort(tmp_path, monkeypatch):
    repo = _setup(tmp_path)
    _add_doc_with_chunks(repo, 1, ["keep"])
    _ext(tmp_path, "keep", [{"title": "KEEP", "type": "ORG", "description": "k", "source_id": "keep"}])
    _ext(tmp_path, "gone", [{"title": "GONE", "type": "ORG", "description": "g", "source_id": "gone"}])

    def boom(self, missing_ok=False):  # noqa: ARG001
        raise OSError("disk on fire")
    monkeypatch.setattr(Path, "unlink", boom)

    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))  # 不抛
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    assert set(ents["title"]) == {"KEEP"}                      # 过滤已保证正确
    assert (tmp_path / "extractions" / "gone.json").exists()   # 清理失败，文件仍在


def test_merge_delta_empty_when_no_live_chunks(tmp_path):
    repo = _setup(tmp_path)
    # 不加任何 chunk → live 为空；但磁盘上有两个孤儿 extraction
    _ext(tmp_path, "a", [{"title": "A", "type": "ORG", "description": "a", "source_id": "a"}])
    _ext(tmp_path, "b", [{"title": "B", "type": "ORG", "description": "b", "source_id": "b"}])
    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    rels = pd.read_parquet(tmp_path / "relationships.parquet")
    assert len(ents) == 0 and len(rels) == 0                   # 空 schema
    assert set(ents.columns) == {"title", "type", "description", "text_unit_ids", "frequency"}
    assert not (tmp_path / "extractions" / "a.json").exists()  # 孤儿被清
