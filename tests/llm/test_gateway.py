import httpx
import pytest

from kb_platform.llm.events import Done, TextDelta, Usage
from kb_platform.llm.gateway import ChatRequest, FailoverGateway
from kb_platform.llm.request import ProviderConfig


def _cfg(key="k1"):
    return ProviderConfig(provider="openai", model="m", api_base=None,
                          api_version=None, key=key, ssl_verify=True)


def _streaming_client(lines):
    async def handler(request):
        return httpx.Response(200, text="\n".join(lines))
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _json_client(obj):
    """Non-streaming fake: returns a single JSON body (OpenAI non-stream shape)."""
    import json

    async def handler(request):
        return httpx.Response(200, text=json.dumps(obj))
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_astream_yields_events():
    client = _streaming_client([
        'data: {"choices":[{"delta":{"content":"he"}}]}',
        'data: {"choices":[{"delta":{"content":"llo"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":2,"completion_tokens":3}}',
        "data: [DONE]",
    ])
    gw = FailoverGateway(profiles=[_cfg()], client=client, breakers={})
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], stream=True,
                      response_format=None, params={})
    out = [e async for e in gw.astream(req)]
    texts = "".join(e.text for e in out if isinstance(e, TextDelta))
    assert texts == "hello"
    assert any(isinstance(e, Usage) for e in out)
    assert isinstance(out[-1], Done)
    await client.aclose()


@pytest.mark.asyncio
async def test_collect_assembles_content_and_usage():
    client = _json_client({
        "choices": [{"message": {"content": "abc"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    })
    gw = FailoverGateway(profiles=[_cfg()], client=client, breakers={})
    req = ChatRequest(messages=[], stream=False, response_format=None, params={})
    res = await gw.collect(req)
    assert res.content == "abc"
    assert res.usage == (1, 2)
    assert res.error is None
    await client.aclose()
