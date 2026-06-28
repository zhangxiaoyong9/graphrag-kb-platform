"""Tests for kb_platform.engine.kb_stats (stats.json snapshot, best-effort)."""

import json

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.kb_stats import write_kb_stats


@pytest.fixture()
def repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return Repository(engine), tmp_path


def test_write_kb_stats_counts_parquet_and_db_rows(repo):
    repo, root = repo
    # Two parquet artifacts with known row counts.
    pd.DataFrame({"title": ["A", "B", "C"]}).to_parquet(root / "entities.parquet")
    pd.DataFrame({"source": ["A"], "target": ["B"]}).to_parquet(root / "relationships.parquet")
    pd.DataFrame({"community": [10, 20]}).to_parquet(root / "communities.parquet")
    pd.DataFrame({"community": [10], "full_content": ["x"]}).to_parquet(root / "community_reports.parquet")
    pd.DataFrame({"id": ["c1", "c2"]}).to_parquet(root / "text_units.parquet")
    repo.add_document(kb_id=1, title="d1", text="x")
    repo.add_document(kb_id=1, title="d2", text="y")

    write_kb_stats(repo, kb_id=1)

    stats = json.loads((root / "stats.json").read_text())
    assert stats["entity_count"] == 3
    assert stats["relationship_count"] == 1
    assert stats["community_count"] == 2
    assert stats["community_report_count"] == 1
    assert stats["text_unit_count"] == 2
    assert stats["document_count"] == 2
    assert "updated_at" in stats


def test_write_kb_stats_missing_parquet_is_zero_and_never_raises(repo):
    repo, root = repo
    # No parquet at all; one document + its chunks in the DB.
    repo.add_document(kb_id=1, title="d1", text="hello world foo bar " * 50)
    # Manually add a chunk row so chunk_count > 0 without running the pipeline.
    from kb_platform.db.models import Chunk

    with session_scope(repo.engine) as s:
        s.add(Chunk(chunk_id="c1", kb_id=1, document_id=1, ordinal=0, text="t"))

    write_kb_stats(repo, kb_id=1)  # must not raise despite missing parquet

    stats = json.loads((root / "stats.json").read_text())
    assert stats["entity_count"] == 0
    assert stats["community_count"] == 0
    assert stats["document_count"] == 1
    assert stats["chunk_count"] == 1


def test_write_kb_stats_unknown_kb_is_noop(repo):
    repo, root = repo
    write_kb_stats(repo, kb_id=999)  # KB row absent
    assert not (root / "stats.json").exists()
