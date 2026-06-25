import pandas as pd

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import generate_text_embeddings
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.graph.vector_store import FakeVectorStore


def test_embeddings_writes_three_indexes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    pd.DataFrame([{"title": "ACME", "type": "ORG", "description": "desc", "text_unit_ids": ["c1"], "frequency": 1}]).to_parquet(tmp_path / "entities.parquet")
    pd.DataFrame([{"chunk_id": "c1", "text": "chunk text", "ordinal": 0}]).to_parquet(tmp_path / "text_units.parquet")  # 简化 schema
    pd.DataFrame([{"title": "R", "summary": "s", "findings": [], "rank": 0.5, "full_content": "report", "level": 0, "community": "C0"}]).to_parquet(tmp_path / "community_reports.parquet")
    repo = Repository(engine)
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("generate_text_embeddings", StepKind.ATOMIC)]).steps[0]
    vs = FakeVectorStore(dim=8)
    vs.connect()
    generate_text_embeddings(repo, FakeGraphAdapter(), step, vs)
    assert len(vs._store["text_unit"]) >= 1
    assert len(vs._store["entity"]) >= 1
    assert len(vs._store["community"]) >= 1
