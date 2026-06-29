import hashlib
import json

from kb_platform.engine.strategy import subject_filename


def _entities_parquet(tmp_path, rows):
    import pandas as pd

    pd.DataFrame(rows).to_parquet(tmp_path / "entities.parquet")


def test_delta_summarize_skips_unchanged_emits_changed(tmp_path, monkeypatch):
    """Only entities whose description hash differs from the last succeeded unit are emitted."""
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import SummarizeDeltaStrategy

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    Base.metadata.create_all(repo.engine)
    _entities_parquet(
        tmp_path,
        [
            {"title": "A", "type": "X", "description": ["a1", "a2"]},  # 2 descs -> candidate
            {"title": "B", "type": "X", "description": ["b1", "b2"]},  # 2 descs -> candidate
        ],
    )
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="incremental", status=JobStatus.RUNNING))
        s.flush()
        s.add(
            Step(
                id=1,
                job_id=1,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.flush()
        step = s.get(Step, 1)

    # Pretend A was already summarized with its CURRENT description set -> unchanged.
    a_hash = hashlib.sha512(json.dumps(["a1", "a2"]).encode()).hexdigest()
    monkeypatch.setattr(
        repo,
        "last_succeeded_input_hash",
        lambda kb, kind, stype, sid: a_hash if sid == "A" else None,
    )

    strat = SummarizeDeltaStrategy()
    batch = strat.next_units_batch(repo, step)
    subjects = {s.subject_id for s in (batch or [])}
    assert subjects == {"B"}  # A skipped (hash matches), B emitted (no history)


def test_delta_summarize_finalize_carries_over(tmp_path):
    """Finalize merges this job's summaries AND on-disk summaries for unchanged entities."""
    import pandas as pd
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase, Job, Step, Unit
    from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus, UnitKind
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import SummarizeDeltaStrategy

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    Base.metadata.create_all(repo.engine)
    _entities_parquet(
        tmp_path,
        [
            {"title": "A", "type": "X", "description": ["a1", "a2"]},
            {"title": "B", "type": "X", "description": ["b1", "b2"]},
        ],
    )
    (tmp_path / "summaries").mkdir()
    # B was summarized in a PRIOR job (carry-over, not a unit in this job):
    (tmp_path / "summaries" / subject_filename("B")).write_text(
        json.dumps({"summary": "B carried over"})
    )
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="incremental", status=JobStatus.RUNNING))
        s.flush()
        s.add(
            Step(
                id=1,
                job_id=1,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.flush()
        # This job summarized A only:
        s.add(
            Unit(
                step_id=1,
                kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
                subject_type="entity",
                subject_id="A",
                status=UnitStatus.SUCCEEDED,
            )
        )
        s.flush()
        step = s.get(Step, 1)
    (tmp_path / "summaries" / subject_filename("A")).write_text(json.dumps({"summary": "A fresh"}))

    from kb_platform.graph.adapter import FakeGraphAdapter

    status = SummarizeDeltaStrategy().finalize(repo, FakeGraphAdapter(), step, tmp_path, 1.0)
    assert str(status).endswith("succeeded")
    out = pd.read_parquet(tmp_path / "entities.parquet")
    desc = dict(zip(out["title"], out["description"]))
    assert desc["A"] == "A fresh"
    assert desc["B"] == "B carried over"  # carried over from disk despite no unit this job
