# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Tests for the index export endpoint (/kbs/{id}/export)."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from conftest import seed_profile


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    seed_profile(c)
    return c


def _seed_kb_with_parquets(client, tmp_path):
    r = client.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1},
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    # The KB's data_root defaults to {global_resolve}/{kb_id} (per-KB isolation).
    data_root = tmp_path / str(kb_id)
    data_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"title": "Alpha&Beta", "type": "CONCEPT", "degree": 1, "description": "d"}]
    ).to_parquet(data_root / "entities.parquet")
    pd.DataFrame(
        [{"source": "Alpha&Beta", "target": "Alpha&Beta", "weight": 1.0, "description": "s"}]
    ).to_parquet(data_root / "relationships.parquet")
    return kb_id, data_root


def test_export_graphml(client, tmp_path):
    kb_id, _ = _seed_kb_with_parquets(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/export?format=graphml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/graphml+xml")
    assert "Alpha&amp;Beta" in r.text


def test_export_zip(client, tmp_path):
    kb_id, _ = _seed_kb_with_parquets(client, tmp_path)
    r = client.get(f"/kbs/{kb_id}/export?format=zip")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "entities.parquet" in names
    assert "graph.graphml" in names
    # graphml in the zip is well-formed
    import xml.etree.ElementTree as ET

    ET.fromstring(z.read("graph.graphml"))


def test_export_missing_kb_404(client):
    assert client.get("/kbs/999/export?format=graphml").status_code == 404


def test_export_bad_format_400(client, tmp_path):
    kb_id, _ = _seed_kb_with_parquets(client, tmp_path)
    assert client.get(f"/kbs/{kb_id}/export?format=bogus").status_code == 400


# --- Cypher export tests -----------------------------------------------------
# These use their own staging helpers that return (client, kb_id, root) so they
# can run without the module-level `client` fixture (and so they can stage the
# text_units.parquet file the cypher writer now reads).


def _stage_kb_with_parquet(tmp_path) -> tuple:
    """Stage a KB with entities/relationships/text_units parquet on disk.

    Returns (client, kb_id, root). Mirrors the existing `_seed_kb_with_parquets`
    pattern but also writes text_units.parquet and returns the client too.
    """
    import numpy as np

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    seed_profile(c)

    r = c.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1},
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    root = tmp_path / str(kb_id)
    root.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        [
            {
                "title": "Alpha&Beta",
                "type": "CONCEPT",
                "description": "d",
                "text_unit_ids": np.array(["c1"], dtype=object),
                "frequency": 1.0,
                "degree": 1,
            }
        ]
    ).to_parquet(root / "entities.parquet")
    pd.DataFrame(
        [
            {
                "source": "Alpha&Beta",
                "target": "Alpha&Beta",
                "description": "self-loop",
                "text_unit_ids": np.array(["c1"], dtype=object),
                "weight": 1.0,
                "combined_degree": 2,
            }
        ]
    ).to_parquet(root / "relationships.parquet")
    pd.DataFrame(
        [
            {
                "id": "c1",
                "text": "chunk text",
                "document_ids": np.array(["d1"], dtype=object),
                "n_tokens": 10,
            }
        ]
    ).to_parquet(root / "text_units.parquet")
    return c, kb_id, root


def _stage_empty_kb(tmp_path) -> tuple:
    """Stage a KB row with no parquet files at all (empty data_root)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    seed_profile(c)

    r = c.post(
        "/kbs",
        json={"name": "empty", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1},
    )
    assert r.status_code == 201, r.text
    return c, r.json()["id"], tmp_path / str(r.json()["id"])


def test_export_cypher_returns_text_plain_with_preamble(tmp_path):
    client, kb_id, root = _stage_kb_with_parquet(tmp_path)
    resp = client.get(f"/kbs/{kb_id}/export?format=cypher")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "CREATE CONSTRAINT entity_title_unique" in resp.text


def test_export_zip_includes_graph_cypher(tmp_path):
    client, kb_id, root = _stage_kb_with_parquet(tmp_path)
    resp = client.get(f"/kbs/{kb_id}/export?format=zip")
    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    names = archive.namelist()
    assert "graph.graphml" in names
    assert "graph.cypher" in names
    assert "CREATE CONSTRAINT entity_title_unique" in archive.read("graph.cypher").decode()


def test_export_cypher_missing_artifacts_still_returns_preamble(tmp_path):
    client, kb_id, root = _stage_empty_kb(tmp_path)
    resp = client.get(f"/kbs/{kb_id}/export?format=cypher")
    assert resp.status_code == 200
    assert "CREATE CONSTRAINT entity_title_unique" in resp.text


def test_export_unknown_format_returns_400(tmp_path):
    client, kb_id, root = _stage_kb_with_parquet(tmp_path)
    resp = client.get(f"/kbs/{kb_id}/export?format=bogus")
    assert resp.status_code == 400
