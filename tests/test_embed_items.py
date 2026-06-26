"""embed_items must use graphrag-llm's real embedding API: embedding(input=[...])
returns an LLMEmbeddingResponse with .embeddings (list[list[float]])."""
from kb_platform.graph.graphrag_adapter import GraphRagAdapter


class _FakeResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    @property
    def embeddings(self) -> list[list[float]]:
        return self._vectors


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embedding(self, *, input):  # noqa: A002 — mirrors LLMEmbeddingArgs
        self.calls.append(list(input))
        return _FakeResponse([[0.0, 0.1] for _ in input])


def _make_adapter(embedder: _FakeEmbedder) -> GraphRagAdapter:
    # __init__ requires chunker / extractor_factory / entity_types; supply
    # minimal stubs — only embed_factory is exercised by embed_items.
    return GraphRagAdapter(
        chunker=None,
        extractor_factory=lambda: None,
        entity_types=[],
        embed_factory=lambda: embedder,
    )


def test_embed_items_uses_embedding_api():
    embedder = _FakeEmbedder()
    adapter = _make_adapter(embedder)
    out = adapter.embed_items(["a", "b"])
    assert embedder.calls == [["a", "b"]]
    assert out == [[0.0, 0.1], [0.0, 0.1]]


def test_embed_items_empty_returns_empty():
    embedder = _FakeEmbedder()
    adapter = _make_adapter(embedder)
    assert adapter.embed_items([]) == []
    assert embedder.calls == []  # not called for empty input
