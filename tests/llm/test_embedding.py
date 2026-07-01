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
    resp = emb.embedding(input=[f"t{i}" for i in range(100)])
    # Consumer (graphrag_adapter.py) reads `.embeddings` — assert the real contract
    assert len(resp.embeddings) == 100
    assert resp.embeddings[0] == [0.0, 0.0]
    assert resp.embeddings[99] == [99.0, 99.0]
    assert resp.usage.total_tokens == 100
    await client.aclose()
