import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import (
    create_communities,
    finalize_graph,
    write_text_units_parquet,
)
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    pd.DataFrame([
        {"title": "A", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1},
        {"title": "B", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1},
    ]).to_parquet(f"{tmp_path}/entities.parquet")
    pd.DataFrame([
        {"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
    ]).to_parquet(f"{tmp_path}/relationships.parquet")
    repo = Repository(engine)
    return repo, str(tmp_path)


def test_finalize_graph_adds_degrees(setup):
    repo, data_root = setup
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("finalize_graph", StepKind.ATOMIC)]).steps[0]
    finalize_graph(repo, FakeGraphAdapter(), step)
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    rels = pd.read_parquet(f"{data_root}/relationships.parquet")
    assert "degree" in ents.columns
    assert "combined_degree" in rels.columns


def test_create_communities_writes_parquet(setup):
    repo, data_root = setup
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("create_communities", StepKind.ATOMIC)]).steps[0]
    create_communities(repo, FakeGraphAdapter(), step)
    comms = pd.read_parquet(f"{data_root}/communities.parquet")
    assert {"level", "community_id", "parent", "entity_ids"} <= set(comms.columns)
    assert len(comms) >= 1


def test_write_text_units_parquet_empty_writes_zero_rows_with_columns(tmp_path):
    out = tmp_path / "text_units.parquet"
    write_text_units_parquet(tmp_path, [])
    assert out.exists()  # 不再 no-op
    df = pd.read_parquet(out)
    assert list(df.columns) == ["id", "text", "document_ids", "n_tokens"]
    assert len(df) == 0
