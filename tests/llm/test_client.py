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
