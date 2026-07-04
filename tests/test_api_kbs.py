from pathlib import Path

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
    seed_profile(c)  # profile id 1, so POST /kbs can reference llm_profile_id=1
    return c


def test_create_and_list_kbs(client):
    r = client.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
              "llm_profile_id": 1, "min_unit_success_ratio": 1.0},
    )
    assert r.status_code == 201 and r.json()["name"] == "kb1"
    assert client.get("/kbs").json()[0]["name"] == "kb1"


def test_upload_document_text(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1})
    r = client.post("/kbs/1/documents", json={"title": "d1", "text": "hello world"})
    assert r.status_code == 201
    docs = client.get("/kbs/1/documents").json()
    assert len(docs) == 1 and docs[0]["title"] == "d1"


def test_get_kb(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1})
    kb_id = r.json()["id"]
    got = client.get(f"/kbs/{kb_id}").json()
    assert got["name"] == "kb1" and got["method"] == "standard"


def test_get_kb_missing(client):
    assert client.get("/kbs/999").status_code == 404


def test_upload_document_file(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1})
    r = client.post(
        "/kbs/1/documents",
        files={"file": ("note.txt", b"file body text", "text/plain")},
    )
    assert r.status_code == 201 and r.json()["title"] == "note.txt"
    docs = client.get("/kbs/1/documents").json()
    assert len(docs) == 1


def test_upload_document_missing_input(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1})
    assert client.post("/kbs/1/documents").status_code == 400


def test_create_kb_422_on_missing_name(client):
    r = client.post("/kbs", json={"method": "standard", "llm_profile_id": 1})  # missing name
    assert r.status_code == 422


def test_add_document_422_on_bad_body(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1})
    r = client.post("/kbs/1/documents", json={"text": 123})  # wrong type
    assert r.status_code == 422


def test_create_kb_response_shape(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1})
    body = r.json()
    assert set(body.keys()) == {"id", "name", "method"}  # response_model restricts fields


def test_llm_fallback_profile_ids_round_trip(client):
    """POST /kbs with llm_fallback_profile_ids=[2,3] is persisted and returned
    verbatim on GET /kbs/{id}; PATCH also round-trips."""
    # create two extra LLM profiles (ids 2 and 3; profile 1 already seeded)
    pid2 = seed_profile(client, name="DS2", provider="deepseek", model="deepseek-chat")
    pid3 = seed_profile(client, name="DS3", provider="deepseek", model="deepseek-chat")
    assert {pid2, pid3} == {2, 3}

    # create
    r = client.post(
        "/kbs",
        json={
            "name": "fb", "method": "standard", "settings_yaml": "{}",
            "llm_profile_id": 1, "llm_fallback_profile_ids": [pid2, pid3],
        },
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]

    # GET round-trips the ordered list + resolved ProfileRefs
    got = client.get(f"/kbs/{kb_id}").json()
    assert got["llm_fallback_profile_ids"] == [pid2, pid3]
    assert [p["id"] for p in got["llm_fallback_profiles"]] == [pid2, pid3]

    # PATCH replaces the list (full replace)
    r = client.patch(
        f"/kbs/{kb_id}",
        json={
            "name": "fb", "method": "standard", "settings_yaml": "{}",
            "llm_profile_id": 1, "llm_fallback_profile_ids": [pid3],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["llm_fallback_profile_ids"] == [pid3]

    # empty list = no fallback (persisted as NULL)
    r = client.patch(
        f"/kbs/{kb_id}",
        json={
            "name": "fb", "method": "standard", "settings_yaml": "{}",
            "llm_profile_id": 1, "llm_fallback_profile_ids": [],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["llm_fallback_profile_ids"] == []


def test_llm_fallback_excludes_primary_and_rejects_unknown(client):
    """The primary llm_profile_id is silently dropped from the fallback list;
    a non-existent id is a 400."""
    pid2 = seed_profile(client, name="DS2", provider="deepseek", model="deepseek-chat")
    # primary = 1 also listed in fallback -> should be dropped, not 400
    r = client.post(
        "/kbs",
        json={
            "name": "fb2", "method": "standard", "settings_yaml": "{}",
            "llm_profile_id": 1, "llm_fallback_profile_ids": [1, pid2],
        },
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    got = client.get(f"/kbs/{kb_id}").json()
    assert got["llm_fallback_profile_ids"] == [pid2]

    # unknown id -> 400
    r = client.post(
        "/kbs",
        json={
            "name": "fb3", "method": "standard", "settings_yaml": "{}",
            "llm_profile_id": 1, "llm_fallback_profile_ids": [9999],
        },
    )
    assert r.status_code == 400


def test_get_kb_stats_returns_snapshot(tmp_path):
    """GET /kbs/{id}/stats returns the written stats.json content."""
    import json

    from fastapi.testclient import TestClient

    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    (tmp_path / "stats.json").write_text(json.dumps({
        "updated_at": "2026-06-28T00:00:00+00:00",
        "document_count": 2, "chunk_count": 5,
        "entity_count": 9, "relationship_count": 7,
        "community_count": 3, "community_report_count": 3, "text_unit_count": 5,
    }))
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    r = client.get("/kbs/1/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_count"] == 9
    assert body["community_count"] == 3
    assert body["document_count"] == 2


def test_get_kb_stats_empty_when_no_snapshot(tmp_path):
    """No stats.json yet -> 200 with all-None body (UI shows '—'), not 404."""
    from fastapi.testclient import TestClient

    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    r = client.get("/kbs/1/stats")
    assert r.status_code == 200
    assert r.json() == {
        "updated_at": None, "document_count": None, "chunk_count": None,
        "entity_count": None, "relationship_count": None,
        "community_count": None, "community_report_count": None, "text_unit_count": None,
    }


def test_create_kb_default_data_root_is_per_kb_isolated(client, tmp_path):
    """Omitting data_root -> {global_resolve}/{kb.id} (per-KB isolation)."""
    r = client.post("/kbs", json={"name": "kb1", "method": "standard",
                                  "settings_yaml": "{}", "llm_profile_id": 1})
    assert r.status_code == 201
    kid = r.json()["id"]
    detail = client.get(f"/kbs/{kid}").json()
    expected = str(Path(str(tmp_path)).resolve() / str(kid))
    assert detail["data_root"] == expected


def test_create_kb_custom_data_root_used_verbatim(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                  "llm_profile_id": 1, "data_root": "/abs/some/kb-dir"})
    assert r.status_code == 201
    detail = client.get(f"/kbs/{r.json()['id']}").json()
    assert detail["data_root"] == "/abs/some/kb-dir"


def test_create_kb_rejects_relative_data_root(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                  "llm_profile_id": 1, "data_root": "relative/path"})
    assert r.status_code == 400
    assert "绝对路径" in r.json()["detail"]


def test_create_kb_rejects_traversal_data_root(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                  "llm_profile_id": 1, "data_root": "/abs/../etc"})
    assert r.status_code == 400
    assert ".." in r.json()["detail"]


def test_update_kb_ignores_data_root(client):
    """data_root is create-only: a PATCH body carrying data_root is ignored."""
    r = client.post("/kbs", json={"name": "kb1", "method": "standard",
                                  "settings_yaml": "{}", "llm_profile_id": 1})
    kid = r.json()["id"]
    before = client.get(f"/kbs/{kid}").json()["data_root"]
    # Mirror a valid PATCH body (see Note below); add data_root, which KbUpdate doesn't declare.
    client.patch(f"/kbs/{kid}", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                       "llm_profile_id": 1, "data_root": "/abs/other"})
    after = client.get(f"/kbs/{kid}").json()["data_root"]
    assert after == before
