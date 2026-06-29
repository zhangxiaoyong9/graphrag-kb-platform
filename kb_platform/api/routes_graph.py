# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Graph visualization data: Top-N entities by degree, or a search neighborhood."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Request

from kb_platform.graph.adapter import cell_to_text

router = APIRouter()

CAP = 500


def _read(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the three parquet artifacts, returning empty frames if missing."""
    ents_path = root / "entities.parquet"
    rels_path = root / "relationships.parquet"
    comms_path = root / "communities.parquet"
    ents = (
        pd.read_parquet(ents_path)
        if ents_path.exists()
        else pd.DataFrame(columns=["title", "type", "degree"])
    )
    rels = (
        pd.read_parquet(rels_path)
        if rels_path.exists()
        else pd.DataFrame(columns=["source", "target", "weight", "description"])
    )
    comms = (
        pd.read_parquet(comms_path)
        if comms_path.exists()
        else pd.DataFrame(columns=["community_id", "entity_ids"])
    )
    return ents, rels, comms


def _title_community(comms: pd.DataFrame) -> dict[str, str]:
    """Build a title -> community_id map from communities.entity_ids membership."""
    out: dict[str, str] = {}
    for _, row in comms.iterrows():
        cid = str(row["community_id"])
        ids = row.get("entity_ids")
        if ids is None:
            continue
        for title in list(ids):
            out[str(title)] = cid
    return out


def _bfs(seeds: list[str], rels: pd.DataFrame, hop: int, limit: int) -> set[str]:
    """Breadth-first expansion from `seeds` over undirected relationships."""
    if not seeds:
        return set()
    adj: dict[str, list[str]] = {}
    for _, r in rels.iterrows():
        src, tgt = str(r["source"]), str(r["target"])
        adj.setdefault(src, []).append(tgt)
        adj.setdefault(tgt, []).append(src)
    seen: set[str] = set(seeds)
    queue: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
    while queue and len(seen) < limit:
        node, depth = queue.popleft()
        if depth >= hop:
            continue
        for neighbor in adj.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, depth + 1))
    return seen


@router.get("/kbs/{kb_id}/graph")
def graph(  # noqa: ANN201
    kb_id: int,
    request: Request,
    limit: int = 200,
    q: str = "",
    hop: int = 1,
):
    """Return graph-viz data for a KB.

    Without ``q``: the Top-N entities by degree (N = ``limit``, capped at 500).
    With ``q``: title-substring matches (case-insensitive) plus a ``hop``-level
    BFS neighborhood over relationships.

    Edges are always restricted to the selected node set.
    """
    from kb_platform.api.routes_export import _data_root

    root = _data_root(request, kb_id)
    ents, rels, comms = _read(root)
    if ents.empty:
        return {"nodes": [], "edges": []}

    tc = _title_community(comms)
    limit = max(1, min(limit, CAP))

    if q:
        qlower = q.lower()
        seeds = [str(t) for t in ents["title"] if qlower in str(t).lower()]
        selected = _bfs(seeds, rels, hop, limit)
    else:
        ordered = ents.sort_values("degree", ascending=False).head(limit)
        selected = set(ordered["title"].astype(str))

    selected_ents = ents[ents["title"].astype(str).isin(selected)]
    nodes = []
    for _, row in selected_ents.iterrows():
        title = str(row["title"])
        nodes.append(
            {
                "id": title,
                "title": title,
                "type": cell_to_text(row.get("type")),
                "degree": int(row["degree"]) if pd.notna(row.get("degree")) else 0,
                "community": tc.get(title),
            }
        )

    selected_edges = rels[
        rels["source"].astype(str).isin(selected) & rels["target"].astype(str).isin(selected)
    ]
    edges = [
        {
            "source": str(r["source"]),
            "target": str(r["target"]),
            "weight": float(r["weight"]) if pd.notna(r.get("weight")) else 0.0,
            "description": cell_to_text(r.get("description")),
        }
        for _, r in selected_edges.iterrows()
    ]
    return {"nodes": nodes, "edges": edges}
