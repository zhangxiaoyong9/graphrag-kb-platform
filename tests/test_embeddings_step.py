import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import generate_text_embeddings
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.graph.vector_store import FakeVectorStore


@pytest.mark.asyncio
async def test_embeddings_writes_three_indexes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    # entities: identity = title; description may be a list (merge_extractions aggregates it)
    pd.DataFrame(
        [
            {
                "title": "ACME",
                "type": "ORG",
                "description": ["d1", "d2"],
                "text_unit_ids": ["c1"],
                "frequency": 1,
            }
        ]
    ).to_parquet(tmp_path / "entities.parquet")
    # text_units: identity = id (chunk step writes `id`)
    pd.DataFrame(
        [{"id": "c1", "text": "chunk text", "document_ids": ["1"], "n_tokens": 0}]
    ).to_parquet(tmp_path / "text_units.parquet")
    # community_reports: identity = community
    pd.DataFrame(
        [
            {
                "title": "R",
                "summary": "s",
                "findings": [],
                "rank": 0.5,
                "full_content": "report",
                "level": 0,
                "community": "C0",
            }
        ]
    ).to_parquet(tmp_path / "community_reports.parquet")
    repo = Repository(engine)
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("generate_text_embeddings", StepKind.ATOMIC)]
    ).steps[0]
    vs = FakeVectorStore(dim=8)
    vs.connect()
    await generate_text_embeddings(repo, FakeGraphAdapter(), step, vs)
    # Canonical graphrag embedding names (graphrag.config.embeddings)
    assert len(vs._store["text_unit_text"]) >= 1
    assert len(vs._store["entity_description"]) >= 1
    assert len(vs._store["community_full_content"]) >= 1
    # id column carries the row identity (entity title / chunk id / community)
    assert vs._store["entity_description"][0]["id"] == "ACME"
    assert vs._store["text_unit_text"][0]["id"] == "c1"
    assert vs._store["community_full_content"][0]["id"] == "C0"
    # list-valued description is flattened to a string before embedding
    assert isinstance(vs._store["entity_description"][0]["text"], str)
    assert "d1" in vs._store["entity_description"][0]["text"]
