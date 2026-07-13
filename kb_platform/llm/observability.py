"""Safe, consistent metadata helpers for LLM and embedding logs."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from kb_platform.logging_config import redact_text


def new_call_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def safe_endpoint(url: str) -> str:
    """Remove credentials and query parameters (including Azure API versions)."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:  # pragma: no cover
        return "<invalid-url>"


def response_excerpt(text: object, limit: int = 400) -> str:
    return redact_text(text, limit=limit)


def input_stats(values: list[str]) -> tuple[int, str]:
    chars = sum(len(value) for value in values)
    joined = "\x1f".join(values).encode("utf-8", errors="replace")
    return chars, hashlib.sha256(joined).hexdigest()[:12]


def message_stats(messages: list[dict]) -> tuple[int, int, str]:
    texts = [str(message.get("content") or "") for message in messages]
    chars, digest = input_stats(texts)
    return len(messages), chars, digest
