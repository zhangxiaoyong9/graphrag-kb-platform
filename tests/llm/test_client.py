import pytest

from kb_platform.llm.client import NativeCompletion, _AwaitableAsyncIterator
from kb_platform.llm.events import Done, TextDelta, Usage


class _FakeGateway:
    def __init__(self, events):
        self._events = events
    async def astream(self, req):
        for e in self._events:
            yield e
    async def collect(self, req):
        text = "".join(e.text for e in self._events if isinstance(e, TextDelta))
        u = next((e for e in self._events if isinstance(e, Usage)), Usage(0, 0))
        from kb_platform.llm.gateway import GatewayResult
        return GatewayResult(content=text, usage=(u.prompt_tokens, u.completion_tokens))


def _make_completion(gateway):
    # bypass graphrag-llm factory: skip ABC __init__ (clear __abstractmethods__ so
    # object.__new__ succeeds even though the base declares abstract attrs we won't
    # exercise in unit tests), then seed our own attrs via _init_for_test.
    from unittest.mock import MagicMock
    mc = MagicMock()
    mc.model_extra = {}
    saved = NativeCompletion.__abstractmethods__
    NativeCompletion.__abstractmethods__ = frozenset()
    try:
        obj = object.__new__(NativeCompletion)
    finally:
        NativeCompletion.__abstractmethods__ = saved
    NativeCompletion._init_for_test(
        obj,
        model_id="openai/m",
        model_config=mc,
        tokenizer=MagicMock(),
        metrics_store=MagicMock(),
        gateway=gateway,
    )
    return obj


@pytest.mark.asyncio
async def test_non_stream_returns_completion_response():
    gw = _FakeGateway([TextDelta("he"), TextDelta("llo"), Usage(2, 3), Done()])
    c = _make_completion(gw)
    resp = await c.completion_async(messages=[{"role": "user", "content": "hi"}], stream=False)
    assert resp.content == "hello"
    assert resp.usage.prompt_tokens == 2 and resp.usage.completion_tokens == 3


@pytest.mark.asyncio
async def test_stream_async_for_without_await():
    gw = _FakeGateway([TextDelta("a"), TextDelta("b"), Usage(1, 2), Done()])
    c = _make_completion(gw)
    # basic-search style: NO await
    chunks = []
    async for chunk in c.completion_async(messages=[], stream=True):
        chunks.append(chunk.choices[0].delta.content or "")
    assert "".join(chunks) == "ab"


@pytest.mark.asyncio
async def test_stream_await_then_async_for():
    gw = _FakeGateway([TextDelta("x"), Done()])
    c = _make_completion(gw)
    # local/global/drift style: await first
    it = await c.completion_async(messages=[], stream=True)
    out = []
    async for chunk in it:
        out.append(chunk.choices[0].delta.content or "")
    assert out == ["x"]


def test_awaitable_async_iterator_supports_both_protocols():
    async def gen():
        yield 1
        yield 2
    aai = _AwaitableAsyncIterator(gen())
    assert await_via_await(aai) is aai  # await returns self
    # cannot easily run async here; the two async tests above cover runtime behavior


def await_via_await(obj):
    # __await__ must be a sync iterator returning obj
    it = obj.__await__()
    try:
        next(it)
    except StopIteration as si:
        return si.value
    raise AssertionError("__await__ should be a no-op returning self")


@pytest.mark.asyncio
async def test_non_stream_populates_formatted_response_for_pydantic_model():
    """C1: when graphrag passes response_format=SomePydanticModel, the native
    completion path must parse the JSON content and set .formatted_response."""
    from pydantic import BaseModel

    class ReportModel(BaseModel):
        title: str
        summary: str

    payload = '{"title": "T", "summary": "S"}'
    gw = _FakeGateway([TextDelta(payload), Usage(1, 2), Done()])
    c = _make_completion(gw)
    resp = await c.completion_async(
        messages=[{"role": "user", "content": "hi"}],
        response_format=ReportModel,
        stream=False,
    )
    assert resp.content == payload
    assert isinstance(resp.formatted_response, ReportModel)
    assert resp.formatted_response.title == "T"
    assert resp.formatted_response.summary == "S"


@pytest.mark.asyncio
async def test_non_stream_formatted_response_none_on_bad_json():
    """C1: malformed JSON content -> formatted_response is None (graphrag tolerates None)."""
    from pydantic import BaseModel

    class ReportModel(BaseModel):
        title: str

    gw = _FakeGateway([TextDelta("not json"), Usage(1, 2), Done()])
    c = _make_completion(gw)
    resp = await c.completion_async(
        messages=[{"role": "user", "content": "hi"}],
        response_format=ReportModel,
        stream=False,
    )
    assert resp.formatted_response is None


def test_ssl_verify_false_reaches_httpx(monkeypatch):
    """I1: ProviderConfig.ssl_verify=False must be passed as verify=False to httpx."""
    import httpx

    captured: dict = {}
    real_init = httpx.AsyncClient.__init__

    def capturing_init(self, *args, **kwargs):
        captured.update(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capturing_init)

    from graphrag_llm.config import ModelConfig

    mc = ModelConfig(
        type="kb_native", model_provider="openai", model="m", api_key="x",
        kb_profiles=[{
            "provider": "openai", "model": "m", "api_base": None,
            "api_version": None, "keys": ["k"], "ssl_verify": False,
        }],
    )
    NativeCompletion(
        model_id="openai/m", model_config=mc, tokenizer=None,
        metrics_store=None,
    )
    assert captured.get("verify") is False
