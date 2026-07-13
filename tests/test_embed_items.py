"""embed_items must use graphrag-llm's real embedding API: embedding(input=[...])
returns an LLMEmbeddingResponse with .embeddings (list[list[float]]).

embed_items must BATCH a large input: a single embedding(input=[thousands]) call
overwhelms providers (Ollama /api/embed returns 400 / times out past ~hundreds),
so it chunks to _EMBED_BATCH_SIZE and concatenates the vectors in input order.
"""
import pytest

from kb_platform.graph import graphrag_adapter
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


@pytest.mark.asyncio
async def test_embed_items_uses_embedding_api():
    embedder = _FakeEmbedder()
    adapter = _make_adapter(embedder)
    out = await adapter.embed_items(["a", "b"])
    assert embedder.calls == [["a", "b"]]
    assert out == [[0.0, 0.1], [0.0, 0.1]]


@pytest.mark.asyncio
async def test_embed_items_empty_returns_empty():
    embedder = _FakeEmbedder()
    adapter = _make_adapter(embedder)
    assert await adapter.embed_items([]) == []
    assert embedder.calls == []  # not called for empty input


@pytest.mark.asyncio
async def test_embed_items_batches_large_input(monkeypatch):
    """Large inputs are split into _EMBED_BATCH_SIZE-sized calls, vectors
    concatenated in order. Guards against the timeout/400 from sending the
    whole collection in one embedding() call."""
    monkeypatch.setattr(graphrag_adapter, "_EMBED_BATCH_SIZE", 3)

    class _OrderEmbedder:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def embedding(self, *, input):  # noqa: A002
            self.calls.append(list(input))
            # distinct vector per text so order is observable end-to-end
            return _FakeResponse([[float(ord(s[-1])), 0.0] for s in input])

    embedder = _OrderEmbedder()
    adapter = _make_adapter(embedder)
    items = [f"t{i}" for i in range(8)]  # 8 / batch=3 -> [3, 3, 2]

    out = await adapter.embed_items(items)

    assert [len(c) for c in embedder.calls] == [3, 3, 2]
    assert embedder.calls[0] == ["t0", "t1", "t2"]
    assert embedder.calls[-1] == ["t6", "t7"]
    assert len(out) == 8
    # order preserved: out[i] derived from items[i]
    assert out[0] == [float(ord("0")), 0.0]
    assert out[7] == [float(ord("7")), 0.0]
