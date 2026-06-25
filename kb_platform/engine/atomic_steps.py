"""Atomic (non-unit) indexing steps."""

import json
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


def merge_delta(repo: Repository, adapter, step) -> None:
    """Re-merge ALL on-disk chunk extractions (old cached + new) -> entities/relationships parquet.

    No LLM: all extractions are cached on disk under ``data_root/extractions/*.json``.
    """
    from kb_platform.graph.adapter import ExtractionResult

    root = _data_root(repo, step)
    extraction_dir = root / "extractions"
    results: list[ExtractionResult] = []
    if extraction_dir.exists():
        for p in sorted(extraction_dir.glob("*.json")):
            raw = json.loads(p.read_text())
            results.append(
                ExtractionResult(
                    entities=pd.DataFrame(raw["entities"]),
                    relationships=pd.DataFrame(raw["relationships"]),
                )
            )
    entities, relationships = adapter.merge_extractions(results)
    entities.to_parquet(root / "entities.parquet")
    relationships.to_parquet(root / "relationships.parquet")


def generate_text_embeddings(repo: Repository, adapter, step, vector_store) -> None:
    """Read 3 parquet collections (text_units, entities, community_reports) →
    batch embed via ``adapter.embed_items`` → upsert into ``vector_store``.

    MVP: uses FakeVectorStore in-memory; real LanceDB lands in Task 3.
    """
    root = _data_root(repo, step)
    collections = [
        ("text_unit", root / "text_units.parquet", lambda r: " ".join(str(r.get(c, "")) for c in ["text"])),
        ("entity", root / "entities.parquet", lambda r: f"{r.get('title', '')} {str(r.get('description', ''))}"),
        ("community", root / "community_reports.parquet", lambda r: str(r.get("full_content", ""))),
    ]
    for index_name, parquet_path, text_fn in collections:
        if not parquet_path.exists():
            continue
        df = pd.read_parquet(parquet_path)
        if df.empty:
            continue
        texts = [text_fn(row) for row in df.to_dict("records")]
        vectors = adapter.embed_items(texts)
        items = [{"id": str(i), "text": texts[i], "vector": vectors[i]} for i in range(len(texts))]
        vector_store.upsert(index_name, items)
