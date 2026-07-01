"""Provider-side OpenAI-compatible SSE stream parser.

Distinct from kb_platform.api.sse, which serializes OUR event-stream to the
browser. This module reads the provider wire format (``data: {json}`` lines)
and emits normalized gateway events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from kb_platform.llm.events import Done, Error, StreamEvent, TextDelta, ToolCallDelta, Usage


def parse_provider_json(obj: dict[str, Any]) -> list[StreamEvent]:
    """Project one provider chunk dict onto gateway events."""
    out: list[StreamEvent] = []
    usage = obj.get("usage")
    if usage:
        out.append(
            Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            )
        )
    choices = obj.get("choices") or []
    if choices:
        delta = choices[0].get("delta") or {}
        if delta.get("content"):
            out.append(TextDelta(text=delta["content"]))
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            out.append(
                ToolCallDelta(
                    index=int(tc.get("index", 0) or 0),
                    id=tc.get("id"),
                    name=fn.get("name"),
                    args_chunk=fn.get("arguments", "") or "",
                )
            )
    return out


async def parse_provider_stream(lines: AsyncIterator[str]) -> AsyncIterator[StreamEvent]:
    """Parse provider SSE lines into gateway events. Emits a terminal ``Done``.

    On a malformed ``data:`` payload, yields one ``Error(retriable=False)`` and stops.
    """
    saw_done = False
    async for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            yield Done()
            saw_done = True
            return
        try:
            obj = json.loads(payload)
        except Exception as exc:  # noqa: BLE001
            yield Error(message=f"sse parse error: {exc}", retriable=False)
            return
        for ev in parse_provider_json(obj):
            yield ev
    if not saw_done:
        yield Done()
