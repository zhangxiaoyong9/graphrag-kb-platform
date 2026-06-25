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


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return TestClient(create_app(Repository(engine), data_root=str(tmp_path)))


def _seed_kb_with_parquets(client, tmp_path):
    r = client.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}"},
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    # The KB's data_root is the app-level data_root (tmp_path), since the
    # create_kb handler stores request.app.state.data_root verbatim.
    data_root = tmp_path
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
