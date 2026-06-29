"""SSE (Server-Sent Events) framing shared by the streaming query/chat routes,
the MCP aggregator, and tests.

Wire format per event (blank line terminates)::

    event: <name>
    data: <one JSON line>

``data`` is a single JSON object on one line (no multi-line data), which keeps
parsing trivial for the MCP proxy and the browser reader.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator


def format_sse(event: str, data) -> str:
    """Serialize one SSE event. ``data`` is JSON-encoded (UTF-8, non-ASCII kept)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse a full SSE text blob into ``[(event, data), ...]``."""
    events: list[tuple[str, dict]] = []
    event: str | None = None
    data_lines: list[str] = []
    for line in text.split("\n"):
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            data_lines.append(line[len("data: ") :])
        elif line == "":
            if event is not None:
                payload = json.loads("".join(data_lines)) if data_lines else {}
                events.append((event, payload))
            event = None
            data_lines = []
    return events


async def iter_sse_events(line_aiter: AsyncIterator[str]) -> AsyncIterator[tuple[str, dict]]:
    """Parse SSE from an async line iterator (e.g. httpx ``aiter_lines()``).

    Handles a final event whose terminating blank line may be missing (the last
    chunk of a stream).
    """
    event: str | None = None
    data_lines: list[str] = []
    async for line in line_aiter:
        if line.startswith("event: "):
            # a new event implicitly terminates the previous one if no blank
            # line separated them (missing terminator on an inner event)
            if event is not None:
                payload = json.loads("".join(data_lines)) if data_lines else {}
                yield event, payload
                data_lines = []
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            data_lines.append(line[len("data: ") :])
        elif line == "":
            if event is not None:
                payload = json.loads("".join(data_lines)) if data_lines else {}
                yield event, payload
            event = None
            data_lines = []
    # flush a trailing event without a terminating blank line
    if event is not None:
        payload = json.loads("".join(data_lines)) if data_lines else {}
        yield event, payload
