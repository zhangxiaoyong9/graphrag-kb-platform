"""GET /llm/health: per-endpoint breaker state + LLM call metrics."""

import json


def _app(tmp_path):
    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine
    from kb_platform.db.models import Base
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    return repo, create_app(repo, data_root=str(tmp_path))


METRIC_KEYS = {
    "ttft_ms_p50",
    "failover_detect_ms_p50",
    "failover_recover_ms_p50",
    "failovers",
    "successes",
}


def test_llm_health_empty(tmp_path):
    """With no registered breakers, profiles is an empty list + metrics shape."""
    from fastapi.testclient import TestClient

    from kb_platform.llm.breaker_registry import _reset_for_test
    from kb_platform.llm.metrics import METRICS

    _reset_for_test()
    METRICS._reset_for_test()

    _repo, app = _app(tmp_path)
    r = TestClient(app).get("/llm/health")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["profiles"], list)
    assert len(body["profiles"]) == 0
    assert set(body["metrics"].keys()) == METRIC_KEYS


def test_llm_health_lists_registered_breaker(tmp_path):
    """A breaker registered via breaker_for() surfaces as a profile entry."""
    from fastapi.testclient import TestClient

    from kb_platform.llm.breaker_registry import _reset_for_test, breaker_for
    from kb_platform.llm.metrics import METRICS
    from kb_platform.llm.request import ProviderConfig

    _reset_for_test()
    METRICS._reset_for_test()

    cfg = ProviderConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_base="https://api.openai.com/v1",
        api_version=None,
        key="sk-test-secret-DO-NOT-LEAK",
    )
    breaker_for(cfg)

    _repo, app = _app(tmp_path)
    r = TestClient(app).get("/llm/health")
    assert r.status_code == 200
    body = r.json()
    profiles = body["profiles"]
    assert len(profiles) == 1
    p = profiles[0]
    assert p["provider"] == "openai"
    assert p["model"] == "gpt-4o-mini"
    assert p["api_base"] == "https://api.openai.com/v1"
    assert p["state"] in {"closed", "open", "half_open"}
    assert set(body["metrics"].keys()) == METRIC_KEYS


def test_llm_health_does_not_leak_api_key(tmp_path):
    """No key material must appear anywhere in the response body."""
    from fastapi.testclient import TestClient

    from kb_platform.llm.breaker_registry import _reset_for_test, breaker_for
    from kb_platform.llm.metrics import METRICS
    from kb_platform.llm.request import ProviderConfig

    _reset_for_test()
    METRICS._reset_for_test()

    secret = "sk-test-secret-DO-NOT-LEAK"
    cfg = ProviderConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_base="https://api.openai.com/v1",
        api_version=None,
        key=secret,
    )
    breaker_for(cfg)

    _repo, app = _app(tmp_path)
    r = TestClient(app).get("/llm/health")
    assert r.status_code == 200
    text = json.dumps(r.json())
    assert secret not in text
    for p in r.json()["profiles"]:
        assert "key" not in p
        assert "api_key" not in p


def test_llm_health_route_is_json_not_spa_html(tmp_path):
    """Route must be a registered API route, not shadowed by the SPA catch-all."""
    from fastapi.testclient import TestClient

    from kb_platform.llm.breaker_registry import _reset_for_test
    from kb_platform.llm.metrics import METRICS

    _reset_for_test()
    METRICS._reset_for_test()

    _repo, app = _app(tmp_path)
    r = TestClient(app).get("/llm/health")
    assert r.status_code == 200
    assert "application/json" in r.headers.get("content-type", "")
