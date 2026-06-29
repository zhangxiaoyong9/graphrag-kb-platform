import pytest

from kb_platform.api.sse import format_sse, iter_sse_events, parse_sse


def test_format_sse_round_trips():
    s = format_sse("delta", {"text": "你好"})
    assert s == 'event: delta\ndata: {"text": "你好"}\n\n'


def test_format_sse_preserves_chinese_readably():
    # ensure_ascii=False so Chinese streams as-is (not \uXXXX) over the wire
    s = format_sse("delta", {"text": "你好"})
    assert "你好" in s


def test_parse_sse_multiple_events():
    blob = (
        format_sse("meta", {"method": "local"})
        + format_sse("delta", {"text": "Hello "})
        + format_sse("delta", {"text": "world"})
        + format_sse("done", {"result": {"answer": "Hello world"}})
    )
    events = parse_sse(blob)
    assert [e for e, _ in events] == ["meta", "delta", "delta", "done"]
    assert events[1][1] == {"text": "Hello "}
    assert events[3][1]["result"]["answer"] == "Hello world"


async def _aiter(lines):
    for ln in lines:
        yield ln


@pytest.mark.asyncio
async def test_iter_sse_events_from_async_lines():
    blob = format_sse("delta", {"text": "x"}) + format_sse("done", {"ok": True})
    # simulate an async line stream (no trailing newline on the last line)
    lines = blob.split("\n")
    out = [ev async for ev in iter_sse_events(_aiter(lines))]
    assert out == [("delta", {"text": "x"}), ("done", {"ok": True})]


@pytest.mark.asyncio
async def test_iter_sse_events_flushes_event_without_terminating_blank_line():
    # No trailing "" — the last event has no terminating blank line, as can
    # happen on the final chunk of a stream. iter_sse_events must still emit it.
    lines = ["event: delta", 'data: {"text": "x"}', "event: done", 'data: {"ok": true}']
    out = [ev async for ev in iter_sse_events(_aiter(lines))]
    assert out == [("delta", {"text": "x"}), ("done", {"ok": True})]
