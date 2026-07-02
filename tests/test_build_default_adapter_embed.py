"""build_default_adapter uses embed_model_config for the embedder when provided."""
from graphrag_llm.config import ModelConfig

from kb_platform.graph.graphrag_adapter import build_default_adapter


def test_embed_model_config_is_used_for_embedder(monkeypatch):
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod

    seen: dict = {}

    def fake_completion(mc):
        seen["completion"] = mc
        return object()

    class _FakeEmbedder:
        def embedding_batch(self, texts):
            return [[0.0] for _ in texts]

    def fake_embedding(mc):
        seen["embedding"] = mc
        return _FakeEmbedder()

    monkeypatch.setattr(comp_mod, "create_completion", fake_completion)
    monkeypatch.setattr(emb_mod, "create_embedding", fake_embedding)

    llm_cfg = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")
    emb_cfg = ModelConfig(
        model_provider="ollama",
        model="nomic-embed-text",
        api_key="ollama",
        api_base="http://localhost:11434",
    )
    adapter = build_default_adapter(
        data_root="/tmp/_unused_", model_config=llm_cfg, embed_model_config=emb_cfg
    )
    # T10: completion is now built from a kb_native-wrapped config derived from
    # llm_cfg (round-robin keys live inside the gateway), so it's NOT llm_cfg
    # itself — but it carries llm_cfg's provider/model/api_base.
    comp_mc = seen["completion"]
    assert comp_mc.type == "kb_native"
    assert comp_mc.model == "gpt-4o-mini"
    # embedding still uses the embed config verbatim.
    assert seen["embedding"] is emb_cfg
    # the adapter's embed_factory yields the embedder built from emb_cfg
    assert isinstance(adapter._embed_factory(), _FakeEmbedder)


def test_falls_back_to_model_config_when_no_embed_config(monkeypatch):
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod

    seen: dict = {}
    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: seen.setdefault("completion", mc) or object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: seen.setdefault("embedding", mc) or object())

    llm_cfg = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")
    build_default_adapter(data_root="/tmp/_unused_", model_config=llm_cfg)
    assert seen["embedding"] is llm_cfg  # fallback to LLM config (current behavior)
