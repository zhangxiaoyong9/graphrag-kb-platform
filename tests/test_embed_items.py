"""embed_items must use the batch embedding API (embedding_batch), not the
single-text embedding() method — see graphrag_llm LiteLLMEmbedding."""
from kb_platform.graph.graphrag_adapter import GraphRagAdapter


class _FakeEmbedder:
    def __init__(self) -> None:
        self.batch_calls: list[list[str]] = []

    def embedding_batch(self, texts):
        self.batch_calls.append(list(texts))
        return [[0.0, 0.1] for _ in texts]


def test_embed_items_uses_batch_api():
    embedder = _FakeEmbedder()
    # __init__ requires chunker / extractor_factory / entity_types; supply
    # minimal stubs — only embed_factory is exercised by embed_items.
    adapter = GraphRagAdapter(
        chunker=None,
        extractor_factory=lambda: None,
        entity_types=[],
        embed_factory=lambda: embedder,
    )
    out = adapter.embed_items(["a", "b"])
    assert embedder.batch_calls == [["a", "b"]]
    assert out == [[0.0, 0.1], [0.0, 0.1]]
