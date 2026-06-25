# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Tests for the graph visualization endpoint (/kbs/{id}/graph)."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return TestClient(create_app(Repository(engine), data_root=str(tmp_path)))


def _seed(client, tmp_path):
    """Seed a KB with entities (varying degree), relationships, communities."""
    r = client.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}"},
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    data_root = tmp_path

    pd.DataFrame(
        [
            {"title": "Alpha", "type": "CONCEPT", "degree": 5, "description": "a"},
            {"title": "Beta", "type": "PERSON", "degree": 3, "description": "b"},
            {"title": "Gamma", "type": "CONCEPT", "degree": 1, "description": "g"},
            {"title": "Delta", "type": "PLACE", "degree": 0, "description": "d"},
        ]
    ).to_parquet(data_root / "entities.parquet")

    pd.DataFrame(
        [
            {"source": "Alpha", "target": "Beta", "weight": 2.0, "description": "ab"},
            {"source": "Beta", "target": "Gamma", "weight": 1.0, "description": "bg"},
            {"source": "Gamma", "target": "Delta", "weight": 1.0, "description": "gd"},
        ]
    ).to_parquet(data_root / "relationships.parquet")

    pd.DataFrame(
        [
            {"community_id": 10, "entity_ids": ["Alpha", "Beta"]},
            {"community_id": 20, "entity_ids": ["Gamma", "Delta"]},
        ]
    ).to_parquet(data_root / "communities.parquet")
    return kb_id


def _titles(resp):
    return sorted(n["title"] for n in resp.json()["nodes"])


def test_top_n_by_degree_respects_limit(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/graph?limit=2")
    assert r.status_code == 200
    body = r.json()
    # The two highest-degree entities are Alpha (5) and Beta (3).
    assert _titles(r) == ["Alpha", "Beta"]
    # Each node carries community coloring.
    communities = {n["title"]: n["community"] for n in body["nodes"]}
    assert communities["Alpha"] == "10"
    assert communities["Beta"] == "10"
    # Edges only among selected nodes (Alpha-Beta exists).
    edges = {(e["source"], e["target"]) for e in body["edges"]}
    assert ("Alpha", "Beta") in edges
    # Beta-Gamma excluded because Gamma is not selected.
    assert all("Gamma" not in e and "Delta" not in e for pair in edges for e in pair)


def test_node_schema(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/graph?limit=1")
    node = r.json()["nodes"][0]
    assert set(node.keys()) == {"id", "title", "type", "degree", "community"}
    assert node["title"] == node["id"] == "Alpha"
    assert node["type"] == "CONCEPT"
    assert node["degree"] == 5


def test_edge_schema(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/graph?limit=2")
    edge = r.json()["edges"][0]
    assert set(edge.keys()) == {"source", "target", "weight", "description"}


def test_limit_capped_at_500(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/graph?limit=10000")
    assert r.status_code == 200
    assert len(r.json()["nodes"]) <= 500


def test_search_neighborhood_q_and_hop(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    # Search "amm" (substring, case-insensitive) matches "Gamma".
    # 1-hop neighborhood of Gamma: Beta, Delta (and Gamma itself).
    r = client.get(f"/kbs/{kb_id}/graph?q=amm&hop=1")
    assert r.status_code == 200
    assert _titles(r) == ["Beta", "Delta", "Gamma"]


def test_search_hop_two(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    # Gamma 2-hop: Beta, Delta (1-hop) then Alpha (from Beta).
    r = client.get(f"/kbs/{kb_id}/graph?q=amm&hop=2")
    assert r.status_code == 200
    assert _titles(r) == ["Alpha", "Beta", "Delta", "Gamma"]


def test_search_no_match(client, tmp_path):
    kb_id = _seed(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/graph?q=zzz&hop=1")
    assert r.status_code == 200
    assert r.json() == {"nodes": [], "edges": []}


def test_empty_graph_when_no_parquet(client, tmp_path):
    """A KB with no entities.parquet returns an empty graph, not a crash."""
    r = client.post(
        "/kbs",
        json={"name": "empty", "method": "standard", "settings_yaml": "{}"},
    )
    kb_id = r.json()["id"]
    out = client.get(f"/kbs/{kb_id}/graph").json()
    assert out == {"nodes": [], "edges": []}


def test_missing_kb_404(client):
    assert client.get("/kbs/999/graph").status_code == 404
