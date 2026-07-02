"""P1 guard: indexing + query hot paths use NativeCompletion/NativeEmbedding
and never instantiate LiteLLMCompletion.

This is the Phase-1 exit gate. After this lands, no litellm completion/embedding
call is made on any indexing or query hot path; ``LiteLLMCompletion.__init__``
is stamped to raise, so any regression that re-introduces a litellm construction
on the resolve + factory path fails this test loudly.
"""

from kb_platform.llm.bootstrap import bootstrap

bootstrap()


def test_resolve_config_uses_kb_native(monkeypatch):
    # Stamp LiteLLMCompletion.__init__ to raise if a litellm completion is ever
    # constructed during config resolution. _resolve_config only builds the
    # ModelConfig dict (no completion is instantiated), but a future regression
    # that swapped the type back to "litellm" would be caught at the moment
    # graphrag-llm's factory builds the model (which the second assertion in
    # test_native_completion_is_what_graphrag_builds exercises).
    from graphrag_llm.completion.lite_llm_completion import LiteLLMCompletion

    def _boom(self, *args, **kwargs):
        raise AssertionError("LiteLLMCompletion was constructed on the query path")

    monkeypatch.setattr(LiteLLMCompletion, "__init__", _boom)

    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    settings = {
        "llm": {
            "type": "kb_native",
            "model_provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": None,
            "api_version": None,
            "api_keys": ["sk-1"],
            "ssl_verify": True,
            "kb_profiles": [
                {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_base": None,
                    "api_version": None,
                    "keys": ["sk-1"],
                    "ssl_verify": True,
                }
            ],
        },
    }
    eng = GraphRagQueryEngine(data_root=".", model_config=settings)
    cfg = eng._resolve_config(root=".")
    entry = cfg.completion_models["default_completion_model"]
    assert entry.type == "kb_native", entry.type


def test_resolve_config_uses_kb_native_embedding(monkeypatch):
    # The embedding entry also resolves to kb_native with a kb_profiles bundle.
    settings = {
        "llm": {
            "type": "kb_native",
            "model_provider": "openai",
            "model": "gpt-4o-mini",
            "api_keys": ["sk-1"],
            "ssl_verify": True,
            "kb_profiles": [
                {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_base": None,
                    "api_version": None,
                    "keys": ["sk-1"],
                    "ssl_verify": True,
                }
            ],
        },
        "embedding": {
            "type": "kb_native",
            "model_provider": "openai",
            "model": "text-embedding-3-small",
            "api_key": "sk-emb",
            "api_keys": ["sk-emb"],
            "ssl_verify": True,
        },
    }
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    eng = GraphRagQueryEngine(data_root=".", model_config=settings)
    cfg = eng._resolve_config(root=".")
    emb = cfg.embedding_models["default_embedding_model"]
    assert emb.type == "kb_native", emb.type


def test_native_completion_is_what_graphrag_builds(monkeypatch):
    # End-to-end: graphrag's create_completion on a kb_native config returns
    # NativeCompletion, never LiteLLMCompletion. Stamp LiteLLMCompletion.__init__
    # so a regression that re-routes to litellm raises here.
    from graphrag_llm.completion import create_completion
    from graphrag_llm.completion.lite_llm_completion import LiteLLMCompletion
    from graphrag_llm.config import ModelConfig

    from kb_platform.llm.client import NativeCompletion

    def _boom(self, *args, **kwargs):
        raise AssertionError("LiteLLMCompletion was constructed by create_completion")

    monkeypatch.setattr(LiteLLMCompletion, "__init__", _boom)

    mc = ModelConfig(
        type="kb_native",
        model_provider="openai",
        model="gpt-4o-mini",
        api_key="x",
        kb_profiles=[
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_base": None,
                "api_version": None,
                "keys": ["x"],
                "ssl_verify": True,
            }
        ],
    )
    assert isinstance(create_completion(mc), NativeCompletion)


def test_native_embedding_is_what_graphrag_builds(monkeypatch):
    # Embeddings also resolve to NativeEmbedding (no litellm on the embedding path).
    from graphrag_llm.embedding import create_embedding
    from graphrag_llm.embedding.lite_llm_embedding import LiteLLMEmbedding
    from graphrag_llm.config import ModelConfig

    from kb_platform.llm.embedding import NativeEmbedding

    def _boom(self, *args, **kwargs):
        raise AssertionError("LiteLLMEmbedding was constructed by create_embedding")

    monkeypatch.setattr(LiteLLMEmbedding, "__init__", _boom)

    mc = ModelConfig(
        type="kb_native",
        model_provider="openai",
        model="text-embedding-3-small",
        api_key="x",
        kb_profiles=[
            {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "api_base": None,
                "api_version": None,
                "keys": ["x"],
                "ssl_verify": True,
            }
        ],
    )
    assert isinstance(create_embedding(mc), NativeEmbedding)
