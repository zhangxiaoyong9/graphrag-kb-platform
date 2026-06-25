import hashlib
import json


def test_delta_reports_skips_seen_ctx(tmp_path, monkeypatch):
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import CommunityReportsDeltaStrategy
    import pandas as pd

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    Base.metadata.create_all(repo.engine)
    pd.DataFrame(
        [
            {"level": 0, "community_id": "0", "parent": "0", "entity_ids": ["A", "B"]},
            {"level": 0, "community_id": "1", "parent": "1", "entity_ids": ["C"]},
        ]
    ).to_parquet(tmp_path / "communities.parquet")
    pd.DataFrame(
        [
            {"title": "A", "description": "a"},
            {"title": "B", "description": "b"},
            {"title": "C", "description": "c"},
        ]
    ).to_parquet(tmp_path / "entities.parquet")
    pd.DataFrame(columns=["source", "target", "description"]).to_parquet(
        tmp_path / "relationships.parquet"
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
                name="community_reports",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.flush()
        step = s.get(Step, 1)

    strat = CommunityReportsDeltaStrategy()
    # Compute community 0's ctx hash, then pretend it already succeeded:
    ctx0 = strat._context(tmp_path, "0")
    h0 = hashlib.sha512(json.dumps(ctx0, default=str).encode()).hexdigest()
    monkeypatch.setattr(repo, "has_succeeded_input_hash", lambda kb, kind, h: h == h0)

    batch = strat.next_units_batch(repo, step)
    subjects = {s.subject_id for s in (batch or [])}
    assert subjects == {"1"}  # community 0 skipped (ctx seen), community 1 emitted


def test_delta_reports_finalize_reuses_sidecar(tmp_path):
    import pandas as pd
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import CommunityReportsDeltaStrategy

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    Base.metadata.create_all(repo.engine)
    pd.DataFrame(
        [{"level": 0, "community_id": "0", "parent": "0", "entity_ids": ["A"]}]
    ).to_parquet(tmp_path / "communities.parquet")
    pd.DataFrame([{"title": "A", "description": "a"}]).to_parquet(tmp_path / "entities.parquet")
    pd.DataFrame(columns=["source", "target", "description"]).to_parquet(
        tmp_path / "relationships.parquet"
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
                name="community_reports",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.flush()
        step = s.get(Step, 1)
    strat = CommunityReportsDeltaStrategy()
    ctx0 = strat._context(tmp_path, "0")
    h0 = hashlib.sha512(json.dumps(ctx0, default=str).encode()).hexdigest()
    # Sidecar: prior report content for this exact ctx:
    (tmp_path / "reports_by_hash").mkdir()
    (tmp_path / "reports_by_hash" / f"{h0}.json").write_text(
        json.dumps(
            {
                "title": "OLD TITLE",
                "summary": "OLD",
                "findings": ["OLD"],
                "rank": 0.1,
                "full_content": "OLD FULL",
                "level": 0,
                "community": "9",
            }
        )
    )
    # No unit for community 0 this job (it was skipped as seen). finalize must still emit it.
    strat.finalize(repo, repo.get_job(1), step, tmp_path, 1.0)
    out = pd.read_parquet(tmp_path / "community_reports.parquet")
    assert "OLD FULL" in list(out["full_content"])
    assert str(out.iloc[0]["community"]) == "0"  # remapped to the NEW community id
