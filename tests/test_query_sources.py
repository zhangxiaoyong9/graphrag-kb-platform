"""QueryResult enrichment: SourceRef + optional elapsed/tokens/sources."""
from types import SimpleNamespace

import pandas as pd

from kb_platform.query.engine import QueryResult, SourceRef
from kb_platform.query.graphrag_engine import GraphRagQueryEngine


def test_query_result_defaults():
    r = QueryResult(answer="a", method="local")
    assert r.error is None
    assert r.elapsed_ms is None
    assert r.prompt_tokens is None
    assert r.output_tokens is None
    assert r.llm_calls is None
    assert r.sources is None


def test_source_ref():
    s = SourceRef(kind="entity", name="宁德时代", text="电池厂商")
    assert s.kind == "entity"
    assert s.name == "宁德时代"


def _engine():
    return GraphRagQueryEngine(data_root=".", model_config={})


def test_extract_sources_entities_and_text():
    ctx = {
        "entities": pd.DataFrame(
            [{"name": "宁德时代", "description": "电池厂商"}, {"name": "特斯拉", "description": "车厂"}]
        ),
        "text units": pd.DataFrame([{"id": 1, "text": "供货协议片段"}]),
    }
    out = _engine()._extract_sources(ctx, "local")
    kinds = {s.kind for s in out}
    names = {s.name for s in out if s.kind == "entity"}
    assert "entity" in kinds and "宁德时代" in names
    assert any(s.kind == "text_unit" and "供货协议" in s.text for s in out)


def test_extract_sources_basic_single_key():
    ctx = {"text units": pd.DataFrame([{"id": 7, "text": "一段正文"}])}
    out = _engine()._extract_sources(ctx, "basic")
    assert out and out[0].kind == "text_unit" and "一段正文" in out[0].text


def test_extract_sources_degrades_on_none():
    assert _engine()._extract_sources(None, "local") is None
    assert _engine()._extract_sources("   ", "local") is None


def test_extract_sources_caps_text_snippet():
    long = "x" * 500
    ctx = {"sources": pd.DataFrame([{"id": 1, "text": long}])}
    out = _engine()._extract_sources(ctx, "basic")
    assert len(out[0].text) <= 200


def test_result_from_search_maps_fields():
    sr = SimpleNamespace(
        response="答案",
        context_data={"entities": pd.DataFrame([{"name": "E1", "description": "d"}])},
        completion_time=0.123,
        prompt_tokens=10,
        output_tokens=20,
        llm_calls=1,
    )
    r = _engine()._result_from_search("local", sr)
    assert r.answer == "答案" and r.method == "local"
    assert r.elapsed_ms == 123.0
    assert r.prompt_tokens == 10 and r.output_tokens == 20 and r.llm_calls == 1
    assert r.sources and r.sources[0].name == "E1"


def test_result_from_search_handles_list_response():
    sr = SimpleNamespace(
        response=[{"x": 1}], context_data=None, completion_time=0.0,
        prompt_tokens=0, output_tokens=0, llm_calls=0,
    )
    r = _engine()._result_from_search("basic", sr)
    assert r.answer == "[{'x': 1}]" and r.sources is None


def test_resolve_config_injects_default_completion_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    eng = GraphRagQueryEngine(
        data_root=".",
        model_config={
            "llm": {
                "model_provider": "deepseek",
                "model": "deepseek-chat",
                "api_key_env": "DEEPSEEK_API_KEY",
            }
        },
    )
    cfg = eng._resolve_config(root=".")
    entry = cfg.completion_models["default_completion_model"]
    assert entry.model == "deepseek-chat"
    assert entry.api_key == "sk-test"
    # vector_store also forced (existing behavior preserved)
    assert cfg.vector_store.db_uri.endswith("/vectors")


def test_resolve_config_reads_assembled_api_keys_list():
    """Regression: assemble_kb_settings produces ``llm.api_keys`` (a LIST), and
    the query route passes assembled settings (not raw content-only
    settings_json, which has no llm block at all). _resolve_config must read
    api_keys[0] — otherwise no completion_model is configured and every real-LLM
    query fails with 'default_completion_model not found in completion_models'.
    """
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    eng = GraphRagQueryEngine(
        data_root=".",
        model_config={
            "llm": {
                "type": "litellm",
                "model_provider": "ollama",
                "model": "llama3",
                "api_base": "http://localhost:11434",
                "api_keys": ["ollama"],  # list form emitted by assemble_kb_settings
            },
        },
    )
    cfg = eng._resolve_config(root=".")
    entry = cfg.completion_models["default_completion_model"]
    assert entry.model == "llama3"
    assert entry.api_key == "ollama"


def test_resolve_config_keeps_explicit_completion_models():
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    eng = GraphRagQueryEngine(
        data_root=".",
        model_config={"llm": {"model": "x"}, "completion_models": {"default_completion_model": {"model": "explicit", "model_provider": "openai", "api_key": "sk-x"}}},
    )
    cfg = eng._resolve_config()
    assert cfg.completion_models["default_completion_model"].model == "explicit"


def test_resolve_config_injects_default_embedding_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-emb")
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    eng = GraphRagQueryEngine(
        data_root=".",
        model_config={
            "llm": {"model": "deepseek-chat"},
            "embedding": {
                "model_provider": "openai",
                "model": "text-embedding-3-small",
                "api_key_env": "OPENAI_API_KEY",
            },
        },
    )
    cfg = eng._resolve_config(root=".")
    emb = cfg.embedding_models["default_embedding_model"]
    assert emb.model == "text-embedding-3-small"
    assert emb.api_key == "sk-emb"
    # completion_models injection (from llm) still happens alongside
    assert cfg.completion_models["default_completion_model"].model == "deepseek-chat"


def test_resolve_config_no_embedding_when_settings_absent():
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    eng = GraphRagQueryEngine(data_root=".", model_config={"llm": {"model": "x"}})
    cfg = eng._resolve_config()
    # no `embedding` settings -> embedding_models not synthesized (graphrag will
    # raise its own "not configured" error for vector methods, which is honest)
    assert not cfg.embedding_models
