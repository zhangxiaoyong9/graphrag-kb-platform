import httpx
import pytest
from graphrag_llm.config import ModelConfig

from kb_platform.llm.embedding import NativeEmbedding


def _mc():
    # ModelConfig(extra="allow") carries kb_profiles in model_extra
    return ModelConfig(
        type="kb_native", model_provider="openai", model="m", api_key="x",
        kb_profiles=[{
            "provider": "openai", "model": "m", "api_base": None, "api_version": None,
            "keys": ["k"], "ssl_verify": True,
        }],
    )


def _client():
    offset = 0

    async def handler(request):
        nonlocal offset
        import json
        n = len(json.loads(request.content)["input"])
        start = offset
        offset += n
        return httpx.Response(200, json={
            "object": "list", "model": "m",
            "data": [{"object": "embedding", "index": start + i,
                      "embedding": [float(start + i), float(start + i)]} for i in range(n)],
            "usage": {"prompt_tokens": n, "total_tokens": n},
        })
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_embedding_returns_vectors_in_order_and_batches():
    # 100 inputs -> 2 batches (64 + 36); handler returns one row per input
    client = _client()
    emb = NativeEmbedding(model_id="openai/m", model_config=_mc(), client=client)
    resp = await emb.embed_many_async([f"t{i}" for i in range(100)])
    # Consumer (graphrag_adapter.py) reads `.embeddings` — assert the real contract
    assert len(resp.embeddings) == 100
    assert resp.embeddings[0] == [0.0, 0.0]
    assert resp.embeddings[99] == [99.0, 99.0]
    assert resp.usage.total_tokens == 100
    await client.aclose()


@pytest.mark.asyncio
async def test_embedding_retries_429_and_logs_batch_lifecycle(caplog, monkeypatch):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, text="rate limited", request=request)
        return httpx.Response(200, request=request, json={
            "data": [{"index": 0, "embedding": [1.0, 2.0]}],
            "usage": {"total_tokens": 3},
        })

    monkeypatch.setattr("kb_platform.llm.embedding.asyncio.sleep", lambda *_: _noop())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = NativeEmbedding(model_id="openai/m", model_config=_mc(), client=client)
    with caplog.at_level("INFO", logger="kb_platform.llm.embedding"):
        response = await emb.embed_many_async(["hello"])
    assert response.embeddings == [[1.0, 2.0]]
    assert calls == 2
    messages = [record.getMessage() for record in caplog.records]
    assert any("embedding.http_error" in message for message in messages)
    assert any("embedding.batch_success" in message for message in messages)
    assert any("embedding.done" in message for message in messages)
    await client.aclose()


async def _noop():
    return None


@pytest.mark.asyncio
async def test_embedding_rejects_vector_count_mismatch():
    async def handler(request):
        return httpx.Response(200, request=request, json={"data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = NativeEmbedding(model_id="openai/m", model_config=_mc(), client=client)
    with pytest.raises(RuntimeError, match="vector count mismatch"):
        await emb.embed_many_async(["hello"])
    await client.aclose()


def test_embedding_ssl_verify_false_reaches_httpx(monkeypatch):
    """I1: NativeEmbedding must pass the profile's ssl_verify to httpx.AsyncClient."""
    captured: dict = {}
    real_init = httpx.AsyncClient.__init__

    def capturing_init(self, *args, **kwargs):
        captured.update(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capturing_init)

    mc = ModelConfig(
        type="kb_native", model_provider="openai", model="m", api_key="x",
        kb_profiles=[{
            "provider": "openai", "model": "m", "api_base": None,
            "api_version": None, "keys": ["k"], "ssl_verify": False,
        }],
    )
    NativeEmbedding(model_id="openai/m", model_config=mc)
    assert captured.get("verify") is False
