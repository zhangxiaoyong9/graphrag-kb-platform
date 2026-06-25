import json

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.reconsolidate import reconsolidate


@pytest.mark.asyncio
async def test_reconsolidate_clears_flag_and_incorporates_late_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # A needs_reconsolidation extract_graph unit whose extraction is on disk.
    step = repo.create_job(
        kb_id=1,
        type="full",
        specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)],
    ).steps[0]
    uid = repo.add_unit(step.id, "chunk", "late-chunk").id
    repo.set_unit_succeeded(uid, llm_raw_output="x")
    repo.mark_needs_reconsolidation(uid)

    # The late unit's extraction is already persisted on disk (2a/2b).
    extractions = tmp_path / "extractions"
    extractions.mkdir()
    (extractions / "late-chunk.json").write_text(
        json.dumps(
            {
                "entities": [
                    {"title": "LATE", "type": "ORG", "description": "d", "source_id": "late-chunk"}
                ],
                "relationships": [],
            }
        )
    )

    await reconsolidate(repo, FakeGraphAdapter(), kb_id=1, data_root=str(tmp_path))

    # Flag cleared.
    assert repo.get_unit_by_subject(step.id, "chunk", "late-chunk").needs_reconsolidation is False
    # Late entity incorporated into parquet.
    assert "LATE" in set(pd.read_parquet(tmp_path / "entities.parquet")["title"])


@pytest.mark.asyncio
async def test_reconsolidate_noop_when_no_flags(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # No needs_reconsolidation units -> nothing written, no error.
    await reconsolidate(repo, FakeGraphAdapter(), kb_id=1, data_root=str(tmp_path))
    assert not (tmp_path / "entities.parquet").exists()
