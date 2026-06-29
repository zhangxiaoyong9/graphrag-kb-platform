from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from starlette.testclient import TestClient


def _client(tmp_path) -> TestClient:
    e = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(e)
    return TestClient(create_app(Repository(e), data_root="."))


def test_list_includes_builtins(tmp_path):
    r = _client(tmp_path).get("/query-presets")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert {"默认", "简洁要点", "详尽调研"} <= names


def test_create_then_update_then_delete_custom(tmp_path):
    c = _client(tmp_path)
    body = {"name": "我的", "method": "local", "community_level": 1, "temperature": 0.2}
    r = c.post("/query-presets", json=body)
    assert r.status_code == 201 and r.json()["is_builtin"] is False
    pid = r.json()["id"]
    assert c.patch(f"/query-presets/{pid}", json={"response_type": "single paragraph"}).status_code == 200
    assert c.delete(f"/query-presets/{pid}").status_code == 204


def test_modify_builtin_is_forbidden(tmp_path):
    c = _client(tmp_path)
    pid = next(p["id"] for p in c.get("/query-presets").json() if p["is_builtin"])
    assert c.patch(f"/query-presets/{pid}", json={"temperature": 0.9}).status_code == 403
    assert c.delete(f"/query-presets/{pid}").status_code == 403


def test_duplicate_name_conflicts(tmp_path):
    c = _client(tmp_path)
    r = c.post("/query-presets", json={"name": "默认", "method": "local"})
    assert r.status_code in (409, 422)
