"""TTFT + failover metrics recording (T17).

Drives a 2-profile gateway where the primary 500s (one retriable error)
then the fallback succeeds. Asserts:
  - streaming: TTFT + failover detect/recover recorded, failovers==1
  - non-streaming: NO TTFT (streaming-only) but failover metrics recorded

Isolation: monkeypatch `kb_platform.llm.gateway.METRICS` to a fresh store.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kb_platform.llm import gateway as gateway_module
from kb_platform.llm.circuit_breaker import CircuitBreaker
from kb_platform.llm.events import Done, TextDelta
from kb_platform.llm.gateway import ChatRequest, FailoverGateway
from kb_platform.llm.metrics import MetricsStore
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


def _build_streaming_client() -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "primary.example":
            return httpx.Response(500, text="upstream boom")
        if host == "fallback.example":
            return httpx.Response(200, text=_streaming_lines("ok"))
        return httpx.Response(404, text="no route")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _build_collect_client() -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "primary.example":
            return httpx.Response(500, text="upstream boom")
        if host == "fallback.example":
            return httpx.Response(200, text=json.dumps({
                "choices": [{"message": {"content": "collected"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 4},
            }))
        return httpx.Response(404, text="no route")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gateway(client: httpx.AsyncClient) -> FailoverGateway:
    profiles = [
        _cfg("openai", "pk", "https://primary.example/v1"),
        _cfg("openai", "fk", "https://fallback.example/v1"),
    ]
    breakers = {i: CircuitBreaker(failure_threshold=1, open_seconds=30)
                for i in range(len(profiles))}
    return FailoverGateway(
        profiles=profiles, client=client, breakers=breakers,
        failure_threshold=1, open_seconds=30.0,
    )


@pytest.fixture()
def fresh_metrics(monkeypatch: pytest.MonkeyPatch) -> MetricsStore:
    """Replace the gateway's module-level METRICS with a fresh store."""
    store = MetricsStore()
    monkeypatch.setattr(gateway_module, "METRICS", store)
    return store


@pytest.mark.asyncio
async def test_astream_records_ttft_and_failover(fresh_metrics: MetricsStore):
    client = _build_streaming_client()
    gw = _gateway(client)
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], stream=True,
                      response_format=None, params={})

    out = [e async for e in gw.astream(req)]
    texts = "".join(e.text for e in out if isinstance(e, TextDelta))
    assert texts == "ok"
    assert isinstance(out[-1], Done)

    snap = fresh_metrics.snapshot()
    assert snap["ttft_ms_p50"] is not None, "TTFT should be recorded on stream"
    assert snap["failover_detect_ms_p50"] is not None, "failover detect timing recorded"
    assert snap["failover_recover_ms_p50"] is not None, "failover recover timing recorded"
    assert snap["failovers"] == 1, f"expected 1 failover, got {snap['failovers']}"
    assert snap["successes"] >= 1
    await client.aclose()


@pytest.mark.asyncio
async def test_collect_records_failover_but_no_ttft(fresh_metrics: MetricsStore):
    client = _build_collect_client()
    gw = _gateway(client)
    req = ChatRequest(messages=[], stream=False, response_format=None, params={})

    res = await gw.collect(req)
    assert res.error is None
    assert res.content == "collected"

    snap = fresh_metrics.snapshot()
    assert snap["ttft_ms_p50"] is None, "TTFT must be streaming-only"
    assert snap["failover_detect_ms_p50"] is not None
    assert snap["failover_recover_ms_p50"] is not None
    assert snap["failovers"] == 1
    await client.aclose()
