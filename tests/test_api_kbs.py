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


def test_create_and_list_kbs(client):
    r = client.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "min_unit_success_ratio": 1.0},
    )
    assert r.status_code == 201 and r.json()["name"] == "kb1"
    assert client.get("/kbs").json()[0]["name"] == "kb1"


def test_upload_document_text(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post("/kbs/1/documents", json={"title": "d1", "text": "hello world"})
    assert r.status_code == 201
    docs = client.get("/kbs/1/documents").json()
    assert len(docs) == 1 and docs[0]["title"] == "d1"


def test_get_kb(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    kb_id = r.json()["id"]
    got = client.get(f"/kbs/{kb_id}").json()
    assert got["name"] == "kb1" and got["method"] == "standard"


def test_get_kb_missing(client):
    assert client.get("/kbs/999").status_code == 404


def test_upload_document_file(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post(
        "/kbs/1/documents",
        files={"file": ("note.txt", b"file body text", "text/plain")},
    )
    assert r.status_code == 201 and r.json()["title"] == "note.txt"
    docs = client.get("/kbs/1/documents").json()
    assert len(docs) == 1


def test_upload_document_missing_input(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    assert client.post("/kbs/1/documents").status_code == 400


def test_create_kb_422_on_missing_name(client):
    r = client.post("/kbs", json={"method": "standard"})  # missing name
    assert r.status_code == 422


def test_create_kb_response_shape(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    body = r.json()
    assert set(body.keys()) == {"id", "name", "method"}  # response_model restricts fields
