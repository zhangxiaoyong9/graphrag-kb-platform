import json

import pytest

from kb_platform.llm import sse
from kb_platform.llm.events import Done, TextDelta, ToolCallDelta, Usage


async def _aiter(items):
    for it in items:
        yield it


def _data(obj: dict) -> str:
    return "data: " + json.dumps(obj)


@pytest.mark.asyncio
async def test_parse_stream_text_delta_and_done():
    lines = [
        'data: {"choices":[{"delta":{"content":"hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        "data: [DONE]",
        "",
    ]
    out = [e async for e in sse.parse_provider_stream(_aiter(lines))]
    assert isinstance(out[0], TextDelta) and out[0].text == "hel"
    assert isinstance(out[1], TextDelta) and out[1].text == "lo"
    assert isinstance(out[-1], Done)


@pytest.mark.asyncio
async def test_parse_stream_usage_and_tool_call():
    tool_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c", "function": {"name": "f", "arguments": '{"x":'}}
                    ]
                }
            }
        ]
    }
    lines = [
        _data(tool_chunk),
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":4,"completion_tokens":7}}',
        "data: [DONE]",
    ]
    out = [e async for e in sse.parse_provider_stream(_aiter(lines))]
    tc = next(e for e in out if isinstance(e, ToolCallDelta))
    assert tc.index == 0 and tc.name == "f" and tc.args_chunk == '{"x":'
    u = next(e for e in out if isinstance(e, Usage))
    assert u.prompt_tokens == 4 and u.completion_tokens == 7
    assert isinstance(out[-1], Done)


@pytest.mark.asyncio
async def test_parse_stream_skips_heartbeats_and_blank():
    lines = [": keep-alive", "", 'data: {"choices":[{"delta":{"content":"x"}}]}', "data: [DONE]"]
    out = [e async for e in sse.parse_provider_stream(_aiter(lines))]
    assert [type(e).__name__ for e in out] == ["TextDelta", "Done"]


@pytest.mark.asyncio
async def test_parse_stream_done_missing_yields_done():
    # stream ends without [DONE] -> terminal Done synthesized
    lines = ['data: {"choices":[{"delta":{"content":"x"}}]}']
    out = [e async for e in sse.parse_provider_stream(_aiter(lines))]
    assert isinstance(out[-1], Done)
