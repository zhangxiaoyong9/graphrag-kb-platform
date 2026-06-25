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
