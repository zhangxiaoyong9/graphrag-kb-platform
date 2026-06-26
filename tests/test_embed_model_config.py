"""_build_embed_model_config: derive an embedding ModelConfig from KB settings.

Ollama has no api key -> placeholder 'ollama' (graphrag-llm requires one for
ApiKey auth; litellm ignores it for ollama). Keyed providers without a key
return None (leave embedding unconfigured rather than crash indexing).
"""
from kb_platform.graph.graphrag_adapter import _build_embed_model_config


def test_no_embedding_settings_returns_none():
    assert _build_embed_model_config({}) is None
    assert _build_embed_model_config({"llm": {"model": "x"}}) is None


def test_ollama_gets_placeholder_key_and_api_base():
    cfg = _build_embed_model_config(
        {
            "embedding": {
                "model_provider": "ollama",
                "model": "nomic-embed-text",
                "api_base": "http://localhost:11434",
            }
        }
    )
    assert cfg is not None
    assert cfg.model_provider == "ollama"
    assert cfg.model == "nomic-embed-text"
    assert cfg.api_base == "http://localhost:11434"
    assert cfg.api_key == "ollama"  # placeholder


def test_explicit_api_key_used():
    cfg = _build_embed_model_config(
        {"embedding": {"model_provider": "openai", "model": "text-embedding-3-small", "api_key": "sk-real"}}
    )
    assert cfg.api_key == "sk-real"


def test_env_api_key_used(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    cfg = _build_embed_model_config(
        {"embedding": {"model_provider": "openai", "model": "text-embedding-3-small"}}
    )
    assert cfg.api_key == "sk-env"


def test_keyed_provider_without_key_returns_none():
    cfg = _build_embed_model_config(
        {"embedding": {"model_provider": "openai", "model": "text-embedding-3-small"}}
    )
    assert cfg is None
