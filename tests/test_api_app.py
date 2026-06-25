"""Tests for FastAPI app factory including SPA static hosting + history fallback."""

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def test_api_routes_work_without_spa(tmp_path):
    """API works even when web/dist does not exist (pre-Task 4)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    assert c.get("/kbs").status_code == 200  # API 可用,即使无 web/dist


def test_spa_served_when_dist_exists(tmp_path, monkeypatch):
    """When web/dist exists: '/' and SPA history routes serve index.html."""
    web = tmp_path / "web" / "dist"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr("kb_platform.api.app.WEB_DIST", str(web))
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    r = c.get("/")
    assert r.status_code == 200 and "SPA" in r.text
    # history fallback: 未知非 API 路径回 index.html
    assert c.get("/kbs/1/jobs/5").status_code == 200
    assert "SPA" in c.get("/kbs/1/jobs/5").text


def test_api_wins_over_catch_all_when_dist_exists(tmp_path, monkeypatch):
    """CRITICAL: API routes must NOT be swallowed by the SPA catch-all.

    With web/dist present, GET /kbs still returns the JSON list (not index.html).
    """
    web = tmp_path / "web" / "dist"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr("kb_platform.api.app.WEB_DIST", str(web))
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    r = c.get("/kbs")
    assert r.status_code == 200
    # JSON list, not the SPA html
    assert r.headers["content-type"].startswith("application/json")
    assert isinstance(r.json(), list)
    assert "SPA" not in r.text
