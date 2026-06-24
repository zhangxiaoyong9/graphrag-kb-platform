"""Atomic (non-unit) indexing steps."""

from pathlib import Path

import pandas as pd
from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository


def _data_root(repo: Repository, step) -> Path:
    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        return Path(kb.data_root)


def finalize_graph(repo: Repository, adapter, step) -> None:
    root = _data_root(repo, step)
    entities = pd.read_parquet(root / "entities.parquet")
    relationships = pd.read_parquet(root / "relationships.parquet")
    e2, r2 = adapter.finalize_entities_relationships(entities, relationships)
    e2.to_parquet(root / "entities.parquet")
    r2.to_parquet(root / "relationships.parquet")


def create_communities(repo: Repository, adapter, step) -> None:
    root = _data_root(repo, step)
    relationships = pd.read_parquet(root / "relationships.parquet")
    communities = adapter.cluster_relationships(relationships)
    communities.to_parquet(root / "communities.parquet")
