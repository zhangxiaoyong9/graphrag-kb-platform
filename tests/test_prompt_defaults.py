"""GET /prompts/defaults returns graphrag's three built-in indexing prompts."""

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def test_prompt_defaults_endpoint(tmp_path):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/p.db"))
    Base.metadata.create_all(repo.engine)
    with TestClient(create_app(repo, data_root=".")) as c:
        r = c.get("/prompts/defaults")
    assert r.status_code == 200
    body = r.json()
    for k in ("extract_graph", "summarize_descriptions", "community_reports"):
        assert isinstance(body[k], str) and len(body[k]) > 100
