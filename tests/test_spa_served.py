from pathlib import Path

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def test_api_and_spa_coexist(tmp_path, monkeypatch):
    if not DIST.exists():
        import pytest

        pytest.skip("web/dist not built; run `npm run build` in web/")
    monkeypatch.setattr("kb_platform.api.app.WEB_DIST", str(DIST))
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    assert c.get("/kbs").status_code == 200  # API 仍是 JSON
    assert isinstance(c.get("/kbs").json(), list)
    root = c.get("/")
    assert root.status_code == 200 and '<div id="root">' in root.text  # SPA
    assert c.get("/kbs/1/jobs/5").status_code == 200  # history fallback → index.html


def _spa_client(tmp_path, monkeypatch):
    import pytest

    if not DIST.exists():
        pytest.skip("web/dist not built; run `npm run build` in web/")
    monkeypatch.setattr("kb_platform.api.app.WEB_DIST", str(DIST))
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return TestClient(create_app(Repository(engine), data_root=str(tmp_path)))


def test_browser_nav_to_api_colliding_route_serves_spa(tmp_path, monkeypatch):
    """A browser navigation (Sec-Fetch-Mode: navigate — address bar / link click /
    refresh) to a SPA route whose path is *also* a GET API endpoint (/query-presets,
    /kbs) must serve the SPA, not the API JSON.

    Otherwise refreshing or deep-linking those pages dumps raw JSON to the user,
    because the API route is registered before the catch-all and wins the path
    match. Sec-Fetch-Mode (not Accept) is the reliable browser-vs-XHR signal: real
    fetch() requests send `cors`/`same-origin`, only navigations send `navigate`.
    """
    c = _spa_client(tmp_path, monkeypatch)
    for path in ("/query-presets", "/kbs"):
        r = c.get(path, headers={"Sec-Fetch-Mode": "navigate"})
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], path
        assert '<div id="root">' in r.text, f"{path}: got {r.headers['content-type']}"
        # The SPA response shares its URL with a JSON API endpoint, so it must not
        # be cached (no-store) and must vary by Sec-Fetch-Mode — otherwise the
        # browser serves the cached navigation HTML to the SPA's own JSON fetch.
        assert "no-store" in r.headers.get("cache-control", ""), f"{path}: cache-control"
        assert "sec-fetch-mode" in r.headers.get("vary", "").lower(), f"{path}: vary"


def test_xhr_to_colliding_route_still_returns_api_json(tmp_path, monkeypatch):
    """The SPA's own data fetches (Sec-Fetch-Mode: cors, no navigate) to /kbs and
    /query-presets must keep hitting the API after the SPA-navigation fix."""
    c = _spa_client(tmp_path, monkeypatch)
    assert isinstance(c.get("/kbs", headers={"Sec-Fetch-Mode": "cors"}).json(), list)
    assert isinstance(c.get("/query-presets", headers={"Sec-Fetch-Mode": "cors"}).json(), list)
