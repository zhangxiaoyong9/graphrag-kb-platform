"""FailoverGateway cross-profile failover + breaker gating (T14).

Two profiles (primary + fallback): primary's transport always 500s, fallback
streams a clean body. We assert the gateway advances to the fallback, the
primary breaker opens at threshold, and a subsequent call skips the open
primary entirely (its handler is not invoked again).
"""

import json

import httpx
import pytest

from kb_platform.llm.circuit_breaker import CircuitBreaker
from kb_platform.llm.events import Done, TextDelta
from kb_platform.llm.gateway import ChatRequest, FailoverGateway
from kb_platform.llm.request import ProviderConfig


def _cfg(provider: str, key: str, api_base: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider, model="m", api_base=api_base,
        api_version=None, key=key, ssl_verify=True,
    )


def _streaming_lines(text: str) -> str:
    return "\n".join([
        f'data: {{"choices":[{{"delta":{{"content":{json.dumps(text)}}}}}]}}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":2,"completion_tokens":3}}',
        "data: [DONE]",
    ])


def _build_client(primary_calls: list[int], fallback_calls: list[int]) -> httpx.AsyncClient:
    """Routes by Host header: primary -> 500, fallback -> 200 streaming body."""

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "primary.example":
            primary_calls.append(1)
            return httpx.Response(500, text="upstream boom")
        if host == "fallback.example":
            fallback_calls.append(1)
            return httpx.Response(200, text=_streaming_lines("ok"))
        return httpx.Response(404, text="no route")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gateway(client: httpx.AsyncClient, threshold: int = 1) -> FailoverGateway:
    profiles = [
        _cfg("openai", "pk", "https://primary.example/v1"),
        _cfg("openai", "fk", "https://fallback.example/v1"),
    ]
    breakers = {i: CircuitBreaker(failure_threshold=threshold, open_seconds=30)
                for i in range(len(profiles))}
    gw = FailoverGateway(
        profiles=profiles, client=client, breakers=breakers,
        failure_threshold=threshold, open_seconds=30.0,
    )
    return gw


@pytest.mark.asyncio
async def test_astream_fails_over_to_fallback_and_trips_primary_breaker():
    primary_calls: list[int] = []
    fallback_calls: list[int] = []
    client = _build_client(primary_calls, fallback_calls)
    gw = _gateway(client, threshold=1)

    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], stream=True,
                      response_format=None, params={})
    out = [e async for e in gw.astream(req)]

    # Content comes from the FALLBACK.
    texts = "".join(e.text for e in out if isinstance(e, TextDelta))
    assert texts == "ok", f"expected fallback content, got {texts!r}"
    assert isinstance(out[-1], Done)

    # Primary was attempted up to the breaker threshold (>=1); fallback at least once.
    assert primary_calls, "primary should have been attempted before failover"
    assert fallback_calls == [1], f"fallback should be hit exactly once, got {fallback_calls}"

    # Breaker states.
    assert gw._breakers[0].state == "open", \
        f"primary breaker should be open after threshold failures, got {gw._breakers[0].state}"
    assert gw._breakers[1].state == "closed", \
        f"fallback breaker should be closed on success, got {gw._breakers[1].state}"

    # Second call: primary breaker is open -> _candidates() omits profile 0,
    # so its handler must NOT be invoked. Snapshot counts before the call.
    pre = len(primary_calls)
    out2 = [e async for e in gw.astream(req)]
    assert len(primary_calls) == pre, \
        f"primary handler called again ({len(primary_calls) - pre}x) despite open breaker"
    texts2 = "".join(e.text for e in out2 if isinstance(e, TextDelta))
    assert texts2 == "ok"

    await client.aclose()


@pytest.mark.asyncio
async def test_collect_fails_over_to_fallback():
    primary_calls: list[int] = []
    fallback_calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "primary.example":
            primary_calls.append(1)
            return httpx.Response(500, text="upstream boom")
        if host == "fallback.example":
            fallback_calls.append(1)
            return httpx.Response(200, text=json.dumps({
                "choices": [{"message": {"content": "collected"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 4},
            }))
        return httpx.Response(404, text="no route")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = _gateway(client, threshold=1)

    req = ChatRequest(messages=[], stream=False, response_format=None, params={})
    res = await gw.collect(req)

    assert res.error is None
    assert res.content == "collected"
    assert res.usage == (1, 4)
    assert gw._breakers[0].state == "open"
    assert gw._breakers[1].state == "closed"
    await client.aclose()
