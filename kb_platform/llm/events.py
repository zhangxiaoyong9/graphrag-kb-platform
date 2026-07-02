"""Normalized gateway-internal stream events.

These are the FailoverGateway's stream contract (see gateway.py). They are
NOT the browser-facing SSE events (api/sse.py) and NOT the OpenAI chunk
shape graphrag consumes — NativeCompletion (client.py) adapts these events
into openai ``ChatCompletionChunk`` / ``LLMCompletionResponse`` objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallDelta:
    # Reserved for future tool-use; graphrag query engines do not emit these.
    index: int
    id: str | None = None
    name: str | None = None
    args_chunk: str = ""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Done:
    pass


@dataclass
class Error:
    message: str
    retriable: bool


StreamEvent = TextDelta | ToolCallDelta | Usage | Done | Error
