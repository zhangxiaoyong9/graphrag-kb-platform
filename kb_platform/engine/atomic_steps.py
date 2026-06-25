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


def _as_text(value) -> str:
    """Flatten a cell that may be a str, list, or numpy array of str (e.g. the
    merged ``description`` column, which ``merge_extractions`` aggregates as a
    list and which parquet round-trips as a numpy array)."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    try:
        import numpy as np  # noqa: PLC0415

        if isinstance(value, np.ndarray):
            return " ".join(str(v) for v in value.tolist())
    except Exception:  # noqa: BLE001
        pass
    return str(value) if value is not None else ""


def generate_text_embeddings(repo: Repository, adapter, step, vector_store) -> None:
    """Read 3 parquet collections → batch embed via ``adapter.embed_items`` →
    upsert into ``vector_store``.

    Index names are graphrag's canonical embedding names
    (``entity_description`` / ``text_unit_text`` / ``community_full_content``)
    and the document ``id`` is the parquet row's identity column, so the
    query read path can map embedding hits back to entities/text-units.
    """
    root = _data_root(repo, step)
    # (embedding_name, parquet, id_column, text_fn)
    collections = [
        (
            "text_unit_text",
            root / "text_units.parquet",
            lambda r: str(r.get("id") or r.get("chunk_id") or ""),
            lambda r: _as_text(r.get("text")),
        ),
        (
            "entity_description",
            root / "entities.parquet",
            lambda r: str(r.get("title") or r.get("id") or ""),
            lambda r: f"{r.get('title', '')} {_as_text(r.get('description'))}",
        ),
        (
            "community_full_content",
            root / "community_reports.parquet",
            lambda r: str(r.get("community") or r.get("id") or ""),
            lambda r: _as_text(r.get("full_content")),
        ),
    ]
    for index_name, parquet_path, id_fn, text_fn in collections:
        if not parquet_path.exists():
            continue
        df = pd.read_parquet(parquet_path)
        if df.empty:
            continue
        records = df.to_dict("records")
        texts = [text_fn(row) for row in records]
        vectors = adapter.embed_items(texts)
        items = [
            {"id": id_fn(records[i]), "text": texts[i], "vector": vectors[i]}
            for i in range(len(records))
        ]
        vector_store.upsert(index_name, items)
