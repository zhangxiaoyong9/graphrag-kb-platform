"""Pure-helper tests for the QueryParams wiring in graphrag_engine.

No parquet, no real LLM: builds a config via ``GraphRagQueryEngine._resolve_config``
and exercises the three module-level helpers added in Task 3.
"""

import pytest

from kb_platform.query.engine import QueryParams
from kb_platform.query.graphrag_engine import (
    GraphRagQueryEngine,
    _apply_params,
    _effective_levels,
    _effective_system_prompt,
)


@pytest.fixture
def _cfg(monkeypatch):
    """Return a callable that builds a resolved GraphRagConfig.

    ``OPENAI_API_KEY`` is set so ``_resolve_config`` injects a real
    ``default_completion_model`` entry into ``completion_models`` (needed for
    the temperature assertions).
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def _make(method: str = "local"):
        eng = GraphRagQueryEngine(
            data_root=".",
            model_config={"llm": {"model": "x", "model_provider": "openai"}},
        )
        return eng._resolve_config()

    return _make


def test_effective_levels_default():
    assert _effective_levels(None) == (2, "multiple paragraphs")


def test_effective_levels_from_params():
    assert _effective_levels(
        QueryParams(community_level=1, response_type="single paragraph")
    ) == (1, "single paragraph")


def test_apply_params_top_k_local(_cfg):
    cfg = _cfg()
    _apply_params(cfg, "local", QueryParams(top_k=12))
    assert cfg.local_search.top_k_entities == 12
    assert cfg.local_search.top_k_relationships == 12


def test_apply_params_top_k_basic_uses_k(_cfg):
    cfg = _cfg()
    _apply_params(cfg, "basic", QueryParams(top_k=8))
    assert cfg.basic_search.k == 8


def test_apply_params_temperature_local_call_args(_cfg):
    cfg = _cfg()
    _apply_params(cfg, "local", QueryParams(temperature=0.4))
    mid = cfg.local_search.completion_model_id
    assert cfg.completion_models[mid].call_args.get("temperature") == 0.4


def test_apply_params_temperature_drift_fields(_cfg):
    cfg = _cfg()
    _apply_params(cfg, "drift", QueryParams(temperature=0.5))
    assert cfg.drift_search.reduce_temperature == 0.5
    assert cfg.drift_search.local_search_temperature == 0.5


def test_apply_params_none_is_noop(_cfg):
    cfg = _cfg()
    before_entities = cfg.local_search.top_k_entities
    _apply_params(cfg, "local", None)
    assert cfg.local_search.top_k_entities == before_entities


def test_apply_params_global_top_k_ignored(_cfg):
    cfg = _cfg()
    _apply_params(cfg, "global", QueryParams(top_k=99))
    # global has no top_k knob; nothing asserts an error — just must not raise


def test_effective_system_prompt_per_query_wins():
    qp = {"local_system": "KB-LOCAL", "global_reduce": "KB-REDUCE"}
    assert _effective_system_prompt(QueryParams(system_prompt="PQ"), qp, "local") == "PQ"


def test_effective_system_prompt_kb_slot_for_method():
    qp = {
        "local_system": "KB-LOCAL",
        "global_reduce": "KB-REDUCE",
        "global_map": "KB-MAP",
    }
    assert _effective_system_prompt(None, qp, "local") == "KB-LOCAL"
    assert (
        _effective_system_prompt(None, qp, "global") == "KB-REDUCE"
    )  # global -> reduce slot
    assert _effective_system_prompt(None, qp, "basic") is None  # basic_system unset
