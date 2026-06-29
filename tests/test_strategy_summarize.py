import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    # 预置 entities.parquet:description 为 list
    ents = pd.DataFrame(
        [
            {
                "title": "ACME",
                "type": "ORG",
                "description": ["d1", "d2"],
                "text_unit_ids": ["c1", "c2"],
                "frequency": 2,
            },
            {
                "title": "SOLO",
                "type": "ORG",
                "description": ["only"],
                "text_unit_ids": ["c1"],
                "frequency": 1,
            },
        ]
    )
    ents.to_parquet(f"{tmp_path}/entities.parquet")
    repo = Repository(engine)
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT)]
    ).steps[0]
    return repo, step, str(tmp_path)


@pytest.fixture()
def setup_slash_title(tmp_path):
    """Like `setup` but with an entity title containing '/' and ':' — the case
    that used to crash persist with FileNotFoundError (title became a subdir)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    ents = pd.DataFrame(
        [
            {
                "title": "http://example.com/A/B",
                "type": "ORG",
                "description": ["d1", "d2"],
                "text_unit_ids": ["c1", "c2"],
                "frequency": 2,
            }
        ]
    )
    ents.to_parquet(f"{tmp_path}/entities.parquet")
    repo = Repository(engine)
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT)]
    ).steps[0]
    return repo, step, str(tmp_path)


def test_only_multi_desc_entities_get_units(setup):
    repo, step, _ = setup
    strat = SummarizeDescriptionsStrategy()
    batch = strat.next_units_batch(repo, step)
    assert batch is not None and {s.subject_id for s in batch} == {"ACME"}  # SOLO 单描述,不出现在批


@pytest.mark.asyncio
async def test_summarize_writes_merged_descriptions_back(setup):
    repo, step, data_root = setup
    from kb_platform.engine.strategy import default_strategies
    from kb_platform.engine.unit_worker import UnitWorker

    worker = UnitWorker(
        repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, strategies=default_strategies()
    )
    await worker.run_unit_fanout(step)
    from kb_platform.db.enums import StepStatus

    assert repo.get_step(step.id).status == StepStatus.SUCCEEDED
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    acme = ents[ents["title"] == "ACME"].iloc[0]
    assert (
        isinstance(acme["description"], str)
        and "d1" in acme["description"]
        and "d2" in acme["description"]
    )
    solo = ents[ents["title"] == "SOLO"].iloc[0]
    assert solo["description"] == "only"  # 未合并,原值保留


@pytest.mark.asyncio
async def test_summarize_handles_slash_in_entity_title(setup_slash_title):
    """Regression: an entity title with '/' (e.g. a URL) must not crash the
    persist step. Previously the title was used verbatim as a filename, turning
    `summaries/http:/x/y.json` into a non-existent subdir → FileNotFoundError."""
    repo, step, data_root = setup_slash_title
    from kb_platform.engine.strategy import default_strategies
    from kb_platform.engine.unit_worker import UnitWorker
    from kb_platform.db.enums import StepStatus

    worker = UnitWorker(
        repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, strategies=default_strategies()
    )
    await worker.run_unit_fanout(step)

    assert repo.get_step(step.id).status == StepStatus.SUCCEEDED
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    row = ents[ents["title"] == "http://example.com/A/B"].iloc[0]
    assert isinstance(row["description"], str) and "d1" in row["description"] and "d2" in row["description"]


def test_subject_filename_is_filesystem_safe():
    from kb_platform.engine.strategy import subject_filename

    # '/' and ':' must not leak into the path component (no subdir traversal).
    name = subject_filename("http://example.com/A/B")
    assert "/" not in name and ":" not in name
    assert name.endswith(".json")
    # Deterministic + injective: distinct titles with identical sanitized stems
    # must still map to different files (hash suffix disambiguates).
    assert subject_filename("A/B") == subject_filename("A/B")
    assert subject_filename("A/B") != subject_filename("A_B")
