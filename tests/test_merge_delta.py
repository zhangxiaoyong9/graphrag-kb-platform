import json

import pandas as pd

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import merge_delta
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


def test_merge_delta_combines_old_and_new_extractions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # 老抽取:ACME
    (tmp_path / "extractions").mkdir()
    (tmp_path / "extractions" / "old.json").write_text(json.dumps({
        "entities": [{"title": "ACME", "type": "ORG", "description": "old desc", "source_id": "old"}],
        "relationships": [],
    }))
    # 新抽取:ACME(老实体,描述增长)+ GLOBEX(新实体)
    (tmp_path / "extractions" / "new.json").write_text(json.dumps({
        "entities": [
            {"title": "ACME", "type": "ORG", "description": "new desc", "source_id": "new"},
            {"title": "GLOBEX", "type": "ORG", "description": "globex", "source_id": "new"},
        ],
        "relationships": [{"source": "ACME", "target": "GLOBEX", "weight": 1.0, "description": "acquires", "source_id": "new"}],
    }))
    step = repo.create_job(kb_id=1, type="incremental", specs=[StepSpec("merge_delta", StepKind.ATOMIC)]).steps[0]
    merge_delta(repo, FakeGraphAdapter(), step)
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    rels = pd.read_parquet(tmp_path / "relationships.parquet")
    titles = set(ents["title"])
    assert titles == {"ACME", "GLOBEX"}
    acme = ents[ents["title"] == "ACME"].iloc[0]
    assert acme["frequency"] == 2  # 老+新两条描述合并
    assert len(rels) == 1 and rels.iloc[0]["source"] == "ACME" and rels.iloc[0]["target"] == "GLOBEX"
