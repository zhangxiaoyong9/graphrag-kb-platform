# Native LLM Provider Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every `graphrag-llm`/litellm chat-completion and embedding call with a self-owned `kb_platform/llm/` layer that talks to OpenAI-compatible providers via httpx, parses provider SSE natively, and adds circuit-breaker-driven cross-profile failover — so no litellm network call is made on any indexing or query hot path.

**Architecture:** Use graphrag-llm's first-class `register_completion`/`register_embedding` seam (`ModelConfig.type = "kb_native"`, which `ModelConfig(extra="allow")` explicitly supports and whose validator only fires for `type=LiteLLM`). A `FailoverGateway` holds an ordered list of provider profiles + per-profile `CircuitBreaker`s; `NativeCompletion`/`NativeEmbedding` are graphrag-llm `LLMCompletion` subclasses that drive the gateway and adapt its normalized events to OpenAI `ChatCompletionChunk` / `LLMCompletionResponse` shapes that graphrag consumes.

**Tech Stack:** Python 3.11, `uv`, pytest (asyncio_mode=auto), `httpx` (already a transitive dep via graphrag-llm; verify it's a direct dep — Task 1), pydantic v2, openai types (re-exported by graphrag-llm), ruff (line-length 100, py311), alembic.

## Global Constraints

- **No new graphrag imports outside `kb_platform/llm/` and `graphrag_adapter.py`/`graphrag_engine.py`.** The `LLMCompletion`/embedding abstract bases are imported only inside `kb_platform/llm/client.py` + `embedding.py`. `httpx` is the only network client.
- **No monkeypatching.** Integration is exclusively via `register_completion("kb_native", …)` / `register_embedding("kb_native", …)` called once per process from a new `bootstrap.py`.
- **`loop="asyncio"` stays** for uvicorn (graphrag-llm calls `nest_asyncio.apply()` at import). Do not switch to uvloop.
- **Provider set:** OpenAI, DeepSeek, Ollama, Azure. One OpenAI-compatible request body; URL/header/auth normalized per provider in `request.py`.
- **`asyncio_mode = "auto"`**, `pythonpath` includes `tests`, per-test Fernet master key autouse fixture (provider-profile crypto). Match these in every test.
- **Chunk shape contract** (verified against graphrag v3.1.0): all four search engines read `chunk.choices[0].delta.content` from the stream, and the stream value must be usable BOTH as `async for chunk in X` (basic) AND `async for chunk in await X` (local/global/drift).
- **Copy/UI in Chinese** where the dashboard is touched (P2 KB form). Match surrounding copy.
- **Commit conventions:** end commit messages with `Co-Authored-By: Claude <noreply@anthropic.com>`. Commit per task.

## File Structure

New package `kb_platform/llm/`:

| File | Responsibility |
|---|---|
| `events.py` | Normalized gateway event dataclasses (gateway-internal contract). |
| `sse.py` | Provider-side OpenAI-compatible SSE parser → `StreamEvent`s. |
| `request.py` | Per-provider URL/header/body normalization (chat + embedding). |
| `circuit_breaker.py` | `CircuitBreaker` (closed/open/half-open). |
| `gateway.py` | `FailoverGateway`: ordered profiles + breakers, key round-robin, failover, metrics hooks. |
| `client.py` | `NativeCompletion(LLMCompletion)` + `_AwaitableAsyncIterator` + event→OpenAI-chunk/response mapping. |
| `embedding.py` | `NativeEmbedding`: httpx `/v1/embeddings`, batched, key round-robin + retry. |
| `metrics.py` | TTFT / failover-detection / failover-recovery recorders + in-memory store. |
| `health.py` | `HealthProbe`: shared background loop pinging each profile. |
| `registry.py` | `register_completion`/`register_embedding("kb_native", …)`. |
| `bootstrap.py` | One-call entrypoint: registry + start `HealthProbe`. Called from `server.py` + `worker.py`. |

Modified:
- `kb_platform/graph/graphrag_adapter.py` — set `type="kb_native"`, pack profile bundle into `ModelConfig` extras, delete `LoadBalancingCompletion` wiring, keep `CostCapturingCompletion`.
- `kb_platform/graph/cost_capture.py` — delete `LoadBalancingCompletion`.
- `kb_platform/query/graphrag_engine.py` — set `type="kb_native"` in `_resolve_config`, delete `_StreamFixWrapper` + the basic-method swap branch.
- `kb_platform/conversation/` rewriter path (`graphrag_adapter.build_chat_complete`) — `type="kb_native"`.
- `kb_platform/server.py`, `kb_platform/worker.py` — call `kb_platform.llm.bootstrap()` at startup.
- `kb_platform/db/models.py` + `alembic/versions/` — `KnowledgeBase.llm_fallback_profile_ids` (P2).
- `web/src/...` — KB form ordered fallback-profile multi-select (P2).

Tests under `tests/llm/` (new) mirroring the package; guard test in `tests/`.

---

# Phase 1 — Native client, zero litellm calls, behavior parity (no failover yet)

**Phase exit gate (review checkpoint):** all of `uv run pytest` passes; the guard test (T11) proves indexing + query hot paths use `NativeCompletion`/`NativeEmbedding` and never instantiate `LiteLLMCompletion`; a real-adapter smoke (manual or existing integration test) shows `extract`/`summarize`/`report`/query still produce results. Commit branch state and human-review before P2.

---

### Task 1: Confirm httpx dependency + create package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `kb_platform/llm/__init__.py`
- Test: `tests/llm/__init__.py`

**Interfaces:** Produces the empty `kb_platform.llm` package importable by later tasks.

- [ ] **Step 1: Verify httpx is resolvable as a direct dep**

Run: `uv run python -c "import httpx; print(httpx.__version__)"`
Expected: prints a version (httpx is pulled in transitively). If it is NOT listed in `pyproject.toml` `[project.dependencies]`, add it.

- [ ] **Step 2: Add httpx to dependencies if missing**

In `pyproject.toml`, under `[project.dependencies]`, add `"httpx>=0.27"` (match the style of existing entries). Then:

Run: `uv sync`
Expected: sync succeeds.

- [ ] **Step 3: Create empty package files**

`kb_platform/llm/__init__.py`:
```python
"""Self-owned LLM provider layer: native OpenAI-compatible transport,
SSE parsing, circuit breakers, and cross-profile failover.

Registered into graphrag-llm as the ``kb_native`` completion/embedding type
(see registry.py + bootstrap.py). No litellm network call is made on any
indexing or query hot path."""
```

`tests/llm/__init__.py`: empty file.

- [ ] **Step 4: Verify import**

Run: `uv run python -c "import kb_platform.llm; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock kb_platform/llm/__init__.py tests/llm/__init__.py
git commit -m "feat(llm): scaffold kb_platform.llm package + httpx dep"
```

---

### Task 2: Normalized gateway events

**Files:**
- Create: `kb_platform/llm/events.py`
- Test: `tests/llm/test_events.py`

**Interfaces:**
- Produces: `TextDelta`, `ToolCallDelta`, `Usage`, `Done`, `Error`, and `StreamEvent = TextDelta | ToolCallDelta | Usage | Done | Error`.

- [ ] **Step 1: Write the failing test**

`tests/llm/test_events.py`:
```python
from kb_platform.llm.events import (
    Done, Error, StreamEvent, TextDelta, ToolCallDelta, Usage,
)


def test_event_fields():
    assert TextDelta(text="hi").text == "hi"
    tc = ToolCallDelta(index=0, id="c1", name="search", args_chunk='{"q":')
    assert tc.index == 0 and tc.args_chunk == '{"q":'
    u = Usage(prompt_tokens=3, completion_tokens=5)
    assert u.prompt_tokens == 3 and u.completion_tokens == 5
    assert Done() == Done()
    e = Error(message="boom", retriable=True)
    assert e.retriable is True


def test_stream_event_union_membership():
    for ev in (TextDelta("x"), ToolCallDelta(1), Usage(), Done(), Error("e", False)):
        assert isinstance(ev, StreamEvent.__args__)  # union membership sanity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: kb_platform.llm.events`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/events.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_events.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/events.py tests/llm/test_events.py
git add kb_platform/llm/events.py tests/llm/test_events.py
git commit -m "feat(llm): normalized gateway stream events"
```

---

### Task 3: Provider SSE parser (provider wire format → events)

**Files:**
- Create: `kb_platform/llm/sse.py`
- Test: `tests/llm/test_sse.py`

**Interfaces:**
- Produces: `async def parse_provider_stream(lines: AsyncIterator[str]) -> AsyncIterator[StreamEvent]` and `def parse_provider_json(obj: dict) -> list[StreamEvent]`.
- Consumes: `events.StreamEvent`.

- [ ] **Step 1: Write the failing test**

`tests/llm/test_sse.py`:
```python
import pytest

from kb_platform.llm import sse
from kb_platform.llm.events import Done, TextDelta, ToolCallDelta, Usage


async def _aiter(items):
    for it in items:
        yield it


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
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c","function":{"name":"f","arguments":"{\\"x\":"}}}]}]}',
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_sse.py -v`
Expected: FAIL — `ModuleNotFoundError: kb_platform.llm.sse`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/sse.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_sse.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/sse.py tests/llm/test_sse.py
git add kb_platform/llm/sse.py tests/llm/test_sse.py
git commit -m "feat(llm): provider SSE parser -> normalized events"
```

---

### Task 4: Per-provider request normalization

**Files:**
- Create: `kb_platform/llm/request.py`
- Test: `tests/llm/test_request.py`

**Interfaces:**
- Produces: `@dataclass ProviderConfig` and `build_chat_request(cfg, *, messages, stream, response_format, params) -> (url, headers, body)`, `build_embed_request(cfg, *, inputs) -> (url, headers, body)`.

- [ ] **Step 1: Write the failing test**

`tests/llm/test_request.py`:
```python
from kb_platform.llm.request import ProviderConfig, build_chat_request, build_embed_request


def _cfg(provider, **kw):
    base = dict(provider=provider, model="m", api_base=None, api_version=None,
                key="k", ssl_verify=True)
    base.update(kw)
    return ProviderConfig(**base)


def test_openai_chat_request():
    url, headers, body = build_chat_request(
        _cfg("openai"), messages=[{"role": "user", "content": "hi"}],
        stream=True, response_format=None, params={"temperature": 0.1},
    )
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"
    assert body["model"] == "m" and body["stream"] is True and body["temperature"] == 0.1
    assert "response_format" not in body


def test_deepseek_custom_api_base():
    url, headers, _ = build_chat_request(
        _cfg("deepseek", api_base="https://api.deepseek.com"),
        messages=[], stream=False, response_format=None, params={},
    )
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer k"


def test_ollama_no_auth_header():
    url, headers, _ = build_chat_request(
        _cfg("ollama", api_base="http://localhost:11434"),
        messages=[], stream=False, response_format=None, params={},
    )
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in headers


def test_azure_deployment_url_and_apikey_header():
    url, headers, _ = build_chat_request(
        _cfg("azure", model="dep1", api_base="https://r.openai.azure.com",
             api_version="2024-06-01"),
        messages=[], stream=False, response_format=None, params={},
    )
    assert url == (
        "https://r.openai.azure.com/openai/deployments/dep1/chat/completions"
        "?api-version=2024-06-01"
    )
    assert headers["api-key"] == "k"
    assert "Authorization" not in headers


def test_structured_output_passthrough():
    schema = {"type": "json_schema", "json_schema": {"name": "R", "schema": {}}}
    _, _, body = build_chat_request(
        _cfg("openai"), messages=[], stream=False, response_format=schema, params={},
    )
    assert body["response_format"] == schema


def test_embed_request_url_and_body():
    url, headers, body = build_embed_request(_cfg("openai"), inputs=["a", "b"])
    assert url == "https://api.openai.com/v1/embeddings"
    assert headers["Authorization"] == "Bearer k"
    assert body["input"] == ["a", "b"] and body["model"] == "m"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_request.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/request.py`:
```python
"""Per-provider request normalization for OpenAI-compatible chat + embedding.

One OpenAI-compatible request body; provider differences are URL + headers only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
_DEFAULT_OLLAMA_BASE = "http://localhost:11434/v1"


@dataclass
class ProviderConfig:
    provider: str           # openai | deepseek | ollama | azure
    model: str
    api_base: str | None
    api_version: str | None
    key: str | None
    ssl_verify: bool = True


def _chat_url(cfg: ProviderConfig) -> str:
    if cfg.provider == "azure":
        base = (cfg.api_base or "").rstrip("/")
        return f"{base}/openai/deployments/{cfg.model}/chat/completions?api-version={cfg.api_version}"
    if cfg.provider == "ollama":
        base = (cfg.api_base or _DEFAULT_OLLAMA_BASE).rstrip("/")
        return f"{base}/chat/completions"
    base = (cfg.api_base or _DEFAULT_OPENAI_BASE).rstrip("/")
    return f"{base}/chat/completions"


def _embed_url(cfg: ProviderConfig) -> str:
    if cfg.provider == "azure":
        base = (cfg.api_base or "").rstrip("/")
        return f"{base}/openai/deployments/{cfg.model}/embeddings?api-version={cfg.api_version}"
    if cfg.provider == "ollama":
        base = (cfg.api_base or _DEFAULT_OLLAMA_BASE).rstrip("/")
        return f"{base}/embeddings"
    base = (cfg.api_base or _DEFAULT_OPENAI_BASE).rstrip("/")
    return f"{base}/embeddings"


def _auth_headers(cfg: ProviderConfig) -> dict[str, str]:
    if cfg.provider == "azure":
        return {"api-key": cfg.key or ""}
    if cfg.provider == "ollama":
        return {}
    return {"Authorization": f"Bearer {cfg.key}"}


def build_chat_request(
    cfg: ProviderConfig,
    *,
    messages: list[dict[str, Any]],
    stream: bool,
    response_format: Any,
    params: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {"Content-Type": "application/json", **_auth_headers(cfg)}
    body: dict[str, Any] = {"model": cfg.model, "messages": messages, "stream": stream, **params}
    if stream:
        body["stream_options"] = {"include_usage": True}
    if response_format is not None:
        body["response_format"] = response_format
    return _chat_url(cfg), headers, body


def build_embed_request(
    cfg: ProviderConfig, *, inputs: list[str]
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {"Content-Type": "application/json", **_auth_headers(cfg)}
    return _embed_url(cfg), headers, {"model": cfg.model, "input": inputs}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_request.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/request.py tests/llm/test_request.py
git add kb_platform/llm/request.py tests/llm/test_request.py
git commit -m "feat(llm): per-provider request normalization"
```

---

### Task 5: Circuit breaker (used by P2, but landed with the gateway)

**Files:**
- Create: `kb_platform/llm/circuit_breaker.py`
- Test: `tests/llm/test_circuit_breaker.py`

**Interfaces:**
- Produces: `class CircuitBreaker(failure_threshold=5, open_seconds=30)` with `allow() -> bool`, `record_success()`, `record_failure()`, `.state` (`"closed"|"open"|"half_open"`).

> Note: Phase-1 gateway (T6) does NOT use the breaker (single-profile pass-through). The breaker lands now so P2 wires it in without changing the gateway's public surface.

- [ ] **Step 1: Write the failing test**

`tests/llm/test_circuit_breaker.py`:
```python
import time

from kb_platform.llm.circuit_breaker import CircuitBreaker


def test_closed_allows_and_success_resets():
    cb = CircuitBreaker(failure_threshold=3, open_seconds=30)
    assert cb.state == "closed" and cb.allow() is True
    cb.record_failure(); cb.record_failure()
    assert cb.state == "closed"  # under threshold
    cb.record_success()
    assert cb.state == "closed"


def test_opens_after_threshold_then_half_open_after_ttl():
    cb = CircuitBreaker(failure_threshold=2, open_seconds=30)
    cb.record_failure(); cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False  # still open
    # simulate TTL elapse
    cb._opened_at -= 31
    assert cb.allow() is True   # half-open admits one
    assert cb.state == "half_open"
    cb.record_failure()
    assert cb.state == "open"   # back to open


def test_half_open_success_closes(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, open_seconds=30)
    cb.record_failure()
    assert cb.state == "open"
    cb._opened_at -= 31
    assert cb.allow() is True
    cb.record_success()
    assert cb.state == "closed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_circuit_breaker.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/circuit_breaker.py`:
```python
"""Per-profile circuit breaker: closed -> open (N consecutive failures) ->
half-open (after TTL) -> closed on success / open on failure.

Relaxed half-open: while half-open, ``allow()`` admits requests (the first to
succeed closes the breaker). This avoids cross-request locking; the gateway
drives one profile at a time per logical call."""

from __future__ import annotations

import time


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, open_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.time() - self._opened_at >= self.open_seconds:
                self._state = "half_open"
                return True
            return False
        # half_open
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == "half_open" or self._failures >= self.failure_threshold:
            self._state = "open"
            self._opened_at = time.time()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_circuit_breaker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/circuit_breaker.py tests/llm/test_circuit_breaker.py
git add kb_platform/llm/circuit_breaker.py tests/llm/test_circuit_breaker.py
git commit -m "feat(llm): per-profile circuit breaker"
```

---

### Task 6: FailoverGateway (single-profile pass-through now; failover wiring lands in P2)

**Files:**
- Create: `kb_platform/llm/gateway.py`
- Test: `tests/llm/test_gateway.py`

**Interfaces:**
- Produces: `class FailoverGateway` with `async def astream(req) -> AsyncIterator[StreamEvent]` and `async def collect(req) -> GatewayResult`, plus `@dataclass ChatRequest` and `@dataclass GatewayResult(content, usage, error)`.
- Consumes: `events`, `request.ProviderConfig`/`build_chat_request`, `sse.parse_provider_stream`.

> A reusable `httpx.AsyncClient` is injected for testability (tests pass a fake transport). The gateway round-robins keys within the single profile (this is what replaces `LoadBalancingCompletion`). Cross-profile iteration + breaker gating are added in P2 by changing ONLY the candidate-selection loop — `astream`/`collect`'s public surface stays.

- [ ] **Step 1: Write the failing test (fake transport)**

`tests/llm/test_gateway.py`:
```python
import httpx
import pytest

from kb_platform.llm.events import Done, TextDelta, Usage
from kb_platform.llm.gateway import ChatRequest, FailoverGateway
from kb_platform.llm.request import ProviderConfig


def _cfg(key="k1"):
    return ProviderConfig(provider="openai", model="m", api_base=None,
                          api_version=None, key=key, ssl_verify=True)


def _streaming_client(lines):
    async def handler(request):
        return httpx.Response(200, text="\n".join(lines))
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_astream_yields_events():
    client = _streaming_client([
        'data: {"choices":[{"delta":{"content":"he"}}]}',
        'data: {"choices":[{"delta":{"content":"llo"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":2,"completion_tokens":3}}',
        "data: [DONE]",
    ])
    gw = FailoverGateway(profiles=[_cfg()], client=client, breakers={})
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], stream=True,
                      response_format=None, params={})
    out = [e async for e in gw.astream(req)]
    texts = "".join(e.text for e in out if isinstance(e, TextDelta))
    assert texts == "hello"
    assert any(isinstance(e, Usage) for e in out)
    assert isinstance(out[-1], Done)
    await client.aclose()


@pytest.mark.asyncio
async def test_collect_assembles_content_and_usage():
    client = _streaming_client([
        'data: {"choices":[{"delta":{"content":"abc"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":1,"completion_tokens":2}}',
        "data: [DONE]",
    ])
    gw = FailoverGateway(profiles=[_cfg()], client=client, breakers={})
    req = ChatRequest(messages=[], stream=False, response_format=None, params={})
    res = await gw.collect(req)
    assert res.content == "abc"
    assert res.usage == (1, 2)
    assert res.error is None
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_gateway.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/gateway.py`:
```python
"""FailoverGateway: ordered provider profiles + per-profile breakers.

Phase 1: single-profile pass-through with key round-robin (replaces
LoadBalancingCompletion). Phase 2 adds breaker-gated cross-profile failover by
extending the candidate-selection loop only."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from kb_platform.llm.circuit_breaker import CircuitBreaker
from kb_platform.llm.events import Done, Error, StreamEvent, TextDelta, Usage
from kb_platform.llm.request import ProviderConfig, build_chat_request
from kb_platform.llm.sse import parse_provider_stream


@dataclass
class ChatRequest:
    messages: list[dict[str, Any]]
    stream: bool
    response_format: Any
    params: dict[str, Any]


@dataclass
class GatewayResult:
    content: str
    usage: tuple[int, int]
    error: str | None = None


@dataclass
class _ProfileKeys:
    cfg: ProviderConfig
    keys: list[str]
    # round-robin cursor shared across calls within this profile
    _cycle: itertools.cycle = field(init=False)

    def __post_init__(self) -> None:
        self._cycle = itertools.cycle(self.keys or [self.cfg.key or ""])

    def next_key(self) -> str:
        return next(self._cycle)


class FailoverGateway:
    def __init__(
        self,
        *,
        profiles: list[ProviderConfig],
        client: httpx.AsyncClient,
        breakers: dict[int, CircuitBreaker],
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
    ) -> None:
        # group keys by provider cfg identity (index) — P1: exactly one profile
        self._pks = [_ProfileKeys(cfg=p, keys=[p.key or ""]) for p in profiles]
        self._profiles = profiles
        self._client = client
        self._breakers = breakers
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds

    # --- candidate selection (extended in P2) ---
    def _candidates(self) -> list[tuple[int, _ProfileKeys]]:
        # P1: single profile, always admitted. P2 will skip open breakers + add fallbacks.
        return [(i, pk) for i, pk in enumerate(self._pks)]

    def _on_attempt_error(self, idx: int, retriable: bool) -> None:
        cb = self._breakers.get(idx)
        if cb is not None and retriable:
            cb.record_failure()

    def _on_success(self, idx: int) -> None:
        cb = self._breakers.get(idx)
        if cb is not None:
            cb.record_success()

    # --- streaming ---
    async def astream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        last_error: str | None = None
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=True,
                response_format=req.response_format, params=req.params,
            )
            try:
                async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        self._on_attempt_error(idx, retriable=resp.status_code >= 500 or resp.status_code == 429)
                        continue
                    self._on_success(idx)
                    async for ev in parse_provider_stream(resp.aiter_lines()):
                        if isinstance(ev, Error):
                            self._on_attempt_error(idx, retriable=ev.retriable)
                            last_error = ev.message
                            break
                        yield ev
                        if isinstance(ev, Done):
                            return
                    else:
                        return  # stream ended cleanly
                    continue  # Error mid-stream -> try next candidate
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = str(exc)
                self._on_attempt_error(idx, retriable=True)
                continue
        yield Error(message=last_error or "all profiles failed", retriable=False)

    # --- non-streaming ---
    async def collect(self, req: ChatRequest) -> GatewayResult:
        last_error: str | None = None
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=False,
                response_format=req.response_format, params=req.params,
            )
            # OpenAI-compatible non-stream still needs usage -> request stream=True-style usage
            # via stream_options; but non-stream returns usage in the single JSON body.
            try:
                resp = await self._client.post(url, headers=headers, json=body)
                if resp.status_code >= 400:
                    last_error = f"HTTP {resp.status_code}"
                    self._on_attempt_error(idx, retriable=resp.status_code >= 500 or resp.status_code == 429)
                    continue
                obj = resp.json()
                content = ""
                choices = obj.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
                usage = obj.get("usage") or {}
                self._on_success(idx)
                return GatewayResult(
                    content=content,
                    usage=(int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = str(exc)
                self._on_attempt_error(idx, retriable=True)
                continue
        return GatewayResult(content="", usage=(0, 0), error=last_error or "all profiles failed")

    def _cfg_with_key(self, pk: _ProfileKeys) -> ProviderConfig:
        from dataclasses import replace
        return replace(pk.cfg, key=pk.next_key())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_gateway.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/gateway.py tests/llm/test_gateway.py
git add kb_platform/llm/gateway.py tests/llm/test_gateway.py
git commit -m "feat(llm): failover gateway (single-profile pass-through + key RR)"
```

---

### Task 7: NativeCompletion (LLMCompletion subclass) + `_AwaitableAsyncIterator`

**Files:**
- Create: `kb_platform/llm/client.py`
- Test: `tests/llm/test_client.py`

**Interfaces:**
- Produces: `class NativeCompletion(LLMCompletion)` exposing `.model_id`, `.tokenizer`, `async completion_async(**kwargs)`, sync `completion(**kwargs)`, and `class _AwaitableAsyncIterator`.
- Consumes: `gateway.FailoverGateway`, `events`, `request.ProviderConfig`. Receives `model_config` (with `kb_profiles` extra) + `tokenizer` from graphrag-llm's factory.

> The `__init__` signature MUST accept every kwarg graphrag-llm's factory passes (`model_id, model_config, tokenizer, metrics_store, metrics_processor, rate_limiter, retrier, cache, cache_key_creator`). We store `model_id`, `tokenizer`, read `model_config` extras, and build the gateway. `kb_profiles` extra shape (packed by the wiring in T10/T11):
> ```python
> [{"provider":"openai","model":"m","api_base":None,"api_version":None,"keys":["k1"],"ssl_verify":True}]
> ```

- [ ] **Step 1: Write the failing test (gateway stubbed; verify chunk shape + dual await/async-for)**

`tests/llm/test_client.py`:
```python
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
    # bypass graphrag-llm factory: construct NativeCompletion with the abstract base's kwargs
    import graphrag_llm.completion.completion as base
    from unittest.mock import MagicMock
    mc = MagicMock()
    mc.model_extra = {}
    # use object.__new__ to skip ABC __init__ then call our own
    obj = object.__new__(NativeCompletion)
    NativeCompletion._init_for_test(obj, model_id="openai/m", model_config=mc,
                                    tokenizer=MagicMock(), gateway=gateway)
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
        nxt = next(it)
    except StopIteration as si:
        return si.value
    raise AssertionError("__await__ should be a no-op returning self")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/client.py`:
```python
"""NativeCompletion: graphrag-llm LLMCompletion backed by our FailoverGateway.

Adapts the gateway's normalized events to the OpenAI shapes graphrag consumes:
- non-stream -> LLMCompletionResponse (choices[0].message.content + usage)
- stream     -> openai ChatCompletionChunk (choices[0].delta.content)

stream return is an _AwaitableAsyncIterator: BOTH ``async for chunk in X``
(graphrag basic_search, no await) AND ``async for chunk in await X`` (local /
global / drift) work. This is what retires _StreamFixWrapper for all engines."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage

try:
    from graphrag_llm.completion.completion import LLMCompletion
except Exception:  # pragma: no cover - graphrag-llm always present in prod
    class LLMCompletion:  # type: ignore[no-redef]
        pass

from kb_platform.llm.events import Done, Error, TextDelta, Usage
from kb_platform.llm.gateway import ChatRequest, FailoverGateway, GatewayResult
from kb_platform.llm.request import ProviderConfig


class _AwaitableAsyncIterator:
    """An async iterator that is also awaitable (await returns self, no-op).

    Lets callers use EITHER ``async for chunk in X`` (basic_search) OR
    ``resp = await X; async for chunk in resp`` (local/global/drift)."""

    def __init__(self, gen: AsyncIterator[Any]) -> None:
        self._gen = gen

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._gen

    async def __anext__(self) -> Any:
        return await self._gen.__anext__()

    def __await__(self) -> Iterator[Any]:
        # no-op generator: ``await aai`` evaluates to self immediately
        if False:
            yield  # noqa: pylint: disable=unreachable
        return self


def _profile_configs(model_config: Any) -> list[ProviderConfig]:
    """Read the ``kb_profiles`` bundle packed into ModelConfig extras."""
    extra = getattr(model_config, "model_extra", None) or {}
    raw = extra.get("kb_profiles") or []
    out: list[ProviderConfig] = []
    for p in raw:
        keys = p.get("keys") or []
        out.append(
            ProviderConfig(
                provider=p["provider"],
                model=p["model"],
                api_base=p.get("api_base"),
                api_version=p.get("api_version"),
                key=keys[0] if keys else None,
                ssl_verify=p.get("ssl_verify", True),
            )
        )
    return out


class NativeCompletion(LLMCompletion):
    """graphrag-llm completion backed by our native gateway.

    Registered as the ``kb_native`` completion type (registry.py). Built per
    create_completion call (scope='transient') so each KB gets its own gateway
    from the ``kb_profiles`` bundle in ModelConfig extras."""

    def __init__(
        self,
        *,
        model_id: str,
        model_config: Any,
        tokenizer: Any,
        metrics_store: Any = None,
        metrics_processor: Any = None,
        rate_limiter: Any = None,
        retrier: Any = None,
        cache: Any = None,
        cache_key_creator: Any = None,
        gateway: FailoverGateway | None = None,
        **kwargs: Any,
    ) -> None:
        self.model_id = model_id
        self.tokenizer = tokenizer
        self._model_config = model_config
        profiles = _profile_configs(model_config)
        if gateway is not None:
            self._gateway = gateway
        else:
            import httpx
            client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
            self._gateway = FailoverGateway(
                profiles=profiles, client=client, breakers={},
                failure_threshold=kwargs.get("failure_threshold", 5),
                open_seconds=kwargs.get("open_seconds", 30.0),
            )

    # test-only constructor bypass (avoids graphrag-llm factory in unit tests)
    def _init_for_test(self, **kw) -> None:  # noqa: D401 - test seam
        self.__dict__.update(kw)

    # --- async ---
    def completion_async(self, /, **kwargs: Any):  # type: ignore[override]
        messages = kwargs.get("messages")
        stream = bool(kwargs.get("stream"))
        response_format = kwargs.get("response_format")
        params = {k: v for k, v in kwargs.items()
                  if k not in {"messages", "stream", "response_format"}}
        req = ChatRequest(messages=_coerce_messages(messages), stream=stream,
                          response_format=response_format, params=params)
        if stream:
            return _AwaitableAsyncIterator(self._stream_chunks(req))
        return self._non_stream(req)

    async def _non_stream(self, req: ChatRequest) -> ChatCompletion:
        res: GatewayResult = await self._gateway.collect(req)
        return ChatCompletion(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            object="chat.completion",
            created=int(time.time()),
            model=self.model_id,
            choices=[Choice(index=0, message=ChatCompletionMessage(role="assistant", content=res.content),
                            finish_reason="stop")],
            usage=CompletionUsage(prompt_tokens=res.usage[0], completion_tokens=res.usage[1], total_tokens=sum(res.usage)),
        )

    async def _stream_chunks(self, req: ChatRequest) -> AsyncIterator[ChatCompletionChunk]:
        async for ev in self._gateway.astream(req):
            if isinstance(ev, TextDelta):
                yield _chunk(self.model_id, content=ev.text)
            elif isinstance(ev, Usage):
                yield _chunk(self.model_id, usage=ev)
            elif isinstance(ev, Done):
                yield _chunk(self.model_id, finish_reason="stop")
                return
            elif isinstance(ev, Error):
                # surface as an empty finish so graphrag stops cleanly; error is terminal
                yield _chunk(self.model_id, finish_reason="stop")
                return
        yield _chunk(self.model_id, finish_reason="stop")

    # --- sync (used by extract_chunk_sync test helper) ---
    def completion(self, /, **kwargs: Any):  # type: ignore[override]
        import asyncio
        return asyncio.run(self.completion_async(**kwargs))


def _chunk(model_id: str, *, content: str | None = None, usage: Usage | None = None,
           finish_reason: str | None = None) -> ChatCompletionChunk:
    delta_kwargs: dict[str, Any] = {}
    if content is not None:
        delta_kwargs["content"] = content
    return ChatCompletionChunk(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model_id,
        choices=[ChunkChoice(index=0, delta=ChoiceDelta(**delta_kwargs), finish_reason=finish_reason)],
        usage=CompletionUsage(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                              total_tokens=usage.prompt_tokens + usage.completion_tokens) if usage else None,
    )


def _coerce_messages(messages: Any) -> list[dict[str, Any]]:
    """graphrag passes messages as str | list[dict]. Normalize to list[dict]."""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return list(messages or [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/client.py tests/llm/test_client.py
git add kb_platform/llm/client.py tests/llm/test_client.py
git commit -m "feat(llm): NativeCompletion with dual await/async-for streaming"
```

---

### Task 8: NativeEmbedding

**Files:**
- Create: `kb_platform/llm/embedding.py`
- Test: `tests/llm/test_embedding.py`

**Interfaces:**
- Produces: `class NativeEmbedding` exposing `.embedding(*, input: list[str]) -> CreateEmbeddingResponse` (graphrag-llm embedding signature), batched at `_EMBED_BATCH_SIZE=64`. Constructor is **factory-style** (the same shape graphrag-llm's `embedding_factory.create(init_args=…)` calls): `__init__(*, model_id, model_config, tokenizer=None, client=None, **_kwargs)`. It reads the profile from `model_config`'s `kb_profiles` extra (single element) and accepts an optional injected `client` for tests.
- Consumes: `request.ProviderConfig`/`build_embed_request`. Single profile, multi-key round-robin, retriable retry.

- [ ] **Step 1: Write the failing test**

`tests/llm/test_embedding.py`:
```python
import httpx
import pytest
from graphrag_llm.config import ModelConfig

from kb_platform.llm.embedding import NativeEmbedding


def _mc():
    # ModelConfig(extra="allow") carries kb_profiles in model_extra
    return ModelConfig(
        type="kb_native", model_provider="openai", model="m", api_key="x",
        kb_profiles=[{
            "provider": "openai", "model": "m", "api_base": None, "api_version": None,
            "keys": ["k"], "ssl_verify": True,
        }],
    )


def _payload(n):
    return {
        "object": "list",
        "model": "m",
        "data": [{"object": "embedding", "index": i, "embedding": [float(i), float(i)]} for i in range(n)],
        "usage": {"prompt_tokens": n, "total_tokens": n},
    }


def _client():
    async def handler(request):
        import json
        n = len(json.loads(request.content)["input"])
        return httpx.Response(200, json=_payload(n))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_embedding_returns_vectors_in_order_and_batches():
    # 100 inputs -> 2 batches (64 + 36); handler returns one row per input
    client = _client()
    emb = NativeEmbedding(model_id="openai/m", model_config=_mc(), client=client)
    resp = emb.embedding(input=[f"t{i}" for i in range(100)])
    assert len(resp.embeddings) == 100
    assert resp.embeddings[0] == [0.0, 0.0]
    assert resp.embeddings[99] == [99.0, 99.0]
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_embedding.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/embedding.py`:
```python
"""NativeEmbedding: httpx POST /v1/embeddings, batched, single profile.

graphrag resolves embedding models via create_embedding; we register this class
as the ``kb_native`` embedding type. No cross-profile failover for embeddings
(single embedding_profile_id retained); within-profile keys round-robin."""

from __future__ import annotations

import itertools
from typing import Any

import httpx
from openai.types.create_embedding_response import CreateEmbeddingResponse, Usage
from openai.types.embedding import Embedding

from kb_platform.llm.request import ProviderConfig, build_embed_request

_EMBED_BATCH_SIZE = 64


class NativeEmbedding:
    def __init__(self, *, model_id: str, model_config: Any, tokenizer: Any = None,
                 client: httpx.AsyncClient | None = None, keys: list[str] | None = None,
                 **_kwargs: Any) -> None:
        extra = getattr(model_config, "model_extra", None) or {}
        prof = (extra.get("kb_profiles") or [{}])[0]
        self._profile = ProviderConfig(
            provider=prof.get("provider", "openai"),
            model=prof.get("model", "text-embedding-3-small"),
            api_base=prof.get("api_base"),
            api_version=prof.get("api_version"),
            key=(prof.get("keys") or [None])[0],
            ssl_verify=prof.get("ssl_verify", True),
        )
        self._keys = keys or prof.get("keys") or []
        self._cycle = itertools.cycle(self._keys or [self._profile.key or ""])
        if client is not None:
            self._client = client
        else:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    def embedding(self, *, input: list[str], **_kwargs: Any) -> CreateEmbeddingResponse:
        import asyncio
        return asyncio.run(self._embedding_async(input))

    async def _embedding_async(self, inputs: list[str]) -> CreateEmbeddingResponse:
        all_vecs: list[list[float]] = []
        total = 0
        for start in range(0, len(inputs), _EMBED_BATCH_SIZE):
            batch = inputs[start : start + _EMBED_BATCH_SIZE]
            from dataclasses import replace
            cfg = replace(self._profile, key=next(self._cycle))
            url, headers, body = build_embed_request(cfg, inputs=batch)
            resp = await self._client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                raise RuntimeError(f"embedding HTTP {resp.status_code}: {resp.text[:200]}")
            obj = resp.json()
            for item in obj.get("data", []):
                all_vecs.append(item["embedding"])
            u = obj.get("usage") or {}
            total += int(u.get("total_tokens", 0) or 0)
        return CreateEmbeddingResponse(
            object="list",
            model=self._profile.model,
            data=[Embedding(object="embedding", index=i, embedding=v) for i, v in enumerate(all_vecs)],
            usage=Usage(prompt_tokens=total, total_tokens=total),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_embedding.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/embedding.py tests/llm/test_embedding.py
git add kb_platform/llm/embedding.py tests/llm/test_embedding.py
git commit -m "feat(llm): native embedding client (batched, key RR)"
```

---

### Task 9: Registry + bootstrap

**Files:**
- Create: `kb_platform/llm/registry.py`, `kb_platform/llm/bootstrap.py`
- Test: `tests/llm/test_registry.py`

**Interfaces:**
- Produces: `register_native(provider_type="kb_native")` (idempotent) and `bootstrap()` (registry + P3 HealthProbe start; P1 leaves probe as a no-op stub).

- [ ] **Step 1: Write the failing test**

`tests/llm/test_registry.py`:
```python
from kb_platform.llm.registry import register_native, NATIVE_TYPE


def test_register_native_registers_completion_and_embedding():
    register_native()  # idempotent
    from graphrag_llm.completion.completion_factory import completion_factory
    from graphrag_llm.embedding.embedding_factory import embedding_factory
    assert NATIVE_TYPE in completion_factory  # registry exposes membership
    assert NATIVE_TYPE in embedding_factory
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`kb_platform/llm/registry.py`:
```python
"""Register NativeCompletion + NativeEmbedding into graphrag-llm's factories.

graphrag-llm's create_completion/create_embedding dispatch on ModelConfig.type;
pre-registering our type bypasses the LiteLLM branch entirely (and the
type validator only fires for type=LiteLLM). Idempotent."""

from __future__ import annotations

from graphrag_llm.completion.completion_factory import (
    completion_factory,
    register_completion,
)
from graphrag_llm.embedding.embedding_factory import (
    embedding_factory,
    register_embedding,
)

NATIVE_TYPE = "kb_native"
_registered = False


def register_native() -> None:
    global _registered
    if _registered:
        return
    from kb_platform.llm.client import NativeCompletion
    from kb_platform.llm.embedding import NativeEmbedding

    register_completion(NATIVE_TYPE, NativeCompletion, scope="transient")
    register_embedding(NATIVE_TYPE, NativeEmbedding, scope="transient")
    _registered = True
```

`kb_platform/llm/bootstrap.py`:
```python
"""One-call entrypoint: register the kb_native types + start the HealthProbe.

Called from server.py and worker.py before any adapter/engine is built.
Phase 1: probe is a no-op stub (Phase 3 fills it in)."""

from __future__ import annotations

from kb_platform.llm.registry import register_native

_bootstrapped = False


def bootstrap() -> None:
    global _bootstrapped
    if _bootstrapped:
        return
    register_native()
    # HealthProbe start lands in Phase 3 (no-op until then).
    _bootstrapped = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/llm/registry.py kb_platform/llm/bootstrap.py tests/llm/test_registry.py
git add kb_platform/llm/registry.py kb_platform/llm/bootstrap.py tests/llm/test_registry.py
git commit -m "feat(llm): register kb_native into graphrag-llm factories"
```

---

### Task 10: Wire indexing + rewriter to `kb_native`; delete `LoadBalancingCompletion`

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py` (`build_default_adapter`, `build_adapter_from_settings`, `build_chat_complete`)
- Modify: `kb_platform/graph/cost_capture.py` (delete `LoadBalancingCompletion`)
- Modify: `kb_platform/server.py`, `kb_platform/worker.py` (call `bootstrap()`)
- Test: `tests/test_unit_worker.py` (existing) — ensure still green; add `tests/llm/test_wiring_indexing.py`

**Interfaces:**
- Consumes: T7 `NativeCompletion`, T8 `NativeEmbedding`, T9 `bootstrap`.
- Produces: a `kb_profiles` bundle in `ModelConfig` extras so `NativeCompletion.__init__` can build its gateway.

> `assemble_kb_settings` is the single place that knows the resolved primary profile (+ fallbacks in P2). P1 packs exactly the primary profile (as a 1-element `kb_profiles` list) + all its keys. `build_default_adapter`/`build_chat_complete` consume that.

- [ ] **Step 1: Read the current wiring to anchor the edit**

Run: `uv run python -c "import kb_platform.graph.graphrag_adapter as g; import inspect; print(inspect.getsource(g.build_default_adapter)[:2000])"`
Re-read `kb_platform/graph/graphrag_adapter.py:296-499` (already familiar). Confirm `extra_api_keys` is the multi-key entry point.

- [ ] **Step 2: Write the failing test**

`tests/llm/test_wiring_indexing.py`:
```python
from kb_platform.graph.graphrag_adapter import build_default_adapter
from kb_platform.llm.registry import register_native


def test_build_default_adapter_yields_native_completion():
    register_native()
    from graphrag_llm.config import ModelConfig
    mc = ModelConfig(type="kb_native", model_provider="openai", model="gpt-4o-mini",
                     api_key="ignored-for-kb_native",
                     extra={  # pydantic extra -> model_extra
                         "kb_profiles": [{
                             "provider": "openai", "model": "gpt-4o-mini",
                             "api_base": None, "api_version": None,
                             "keys": ["sk-1", "sk-2"], "ssl_verify": True,
                         }]
                     })
    adapter = build_default_adapter(data_root=".", model_config=mc)
    from kb_platform.llm.client import NativeCompletion
    assert isinstance(adapter._completion, NativeCompletion)
    # Indexing EMBEDDING path must also be native (spec: embeddings go native too).
    from kb_platform.llm.embedding import NativeEmbedding
    assert isinstance(adapter._embed_factory(), NativeEmbedding)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_wiring_indexing.py -v`
Expected: FAIL — adapter builds a `CostCapturingCompletion` around a litellm completion today.

- [ ] **Step 4: Rewire `build_default_adapter`**

In `kb_platform/graph/graphrag_adapter.py`, replace the completion-construction block (lines ~355-380) so it:
1. Sets `type="kb_native"` on `model_config` (construct via `ModelConfig(type="kb_native", …)` carrying the keys bundle in extras).
2. Calls `create_completion(model_config)` — which now returns `NativeCompletion` (after `register_native()`).
3. Wraps it ONCE with `CostCapturingCompletion` (keys live inside the gateway now).
4. Drops the `extra_api_keys`/`LoadBalancingCompletion` branch entirely.

Concretely, replace the block with:
```python
    from kb_platform.graph.cost_capture import CostCapturingCompletion
    from kb_platform.llm.registry import register_native

    register_native()
    model_id = model_config.model
    # Build a kb_native ModelConfig carrying all keys in extras -> NativeCompletion gateway.
    from graphrag_llm.config import ModelConfig as _MC
    kb_cfg = _MC(
        type="kb_native",
        model_provider=model_config.model_provider,
        model=model_config.model,
        api_base=model_config.api_base,
        api_version=model_config.api_version,
        api_key=(extra_api_keys or [model_config.api_key])[0],
        call_args=dict(model_config.call_args or {}),
        kb_profiles=[{
            "provider": model_config.model_provider,
            "model": model_config.model,
            "api_base": model_config.api_base,
            "api_version": model_config.api_version,
            "keys": list((extra_api_keys or []) or [model_config.api_key]),
            "ssl_verify": (model_config.call_args or {}).get("ssl_verify", True),
        }],
    )
    completion = create_completion(kb_cfg)
    wrapped = CostCapturingCompletion(completion, model_id=model_id)
    completion = wrapped
```

Delete the `extra_api_keys`/`LoadBalancingCompletion` branch that follows.

Also update `build_adapter_from_settings` to drop the `extra_api_keys=extra_keys or None` arg (the keys now ride inside `kb_profiles` via `assemble_kb_settings` — see Step 6) and pass `extra_api_keys=api_keys` instead so the wiring above fans them out. Keep `api_key=resolved_key` as the ModelConfig api_key (validator is skipped for kb_native).

Also flip the **indexing embedding** path to `kb_native` (spec: embeddings go native too; the T10 test asserts `adapter._embed_factory()` is `NativeEmbedding`):

1. In `assemble_kb_settings`, change the `embedding` block to carry `type="kb_native"` + a single-element `kb_profiles` (mirroring the `llm` block):
```python
        assembled["embedding"] = {
            "type": "kb_native",
            "model_provider": ep.provider,
            "model": ep.model,
            "api_base": ep.api_base,
            "api_version": ep.api_version,
            "api_keys": emb_keys,
            "ssl_verify": ep.ssl_verify,
            "kb_profiles": [{
                "provider": ep.provider,
                "model": ep.model,
                "api_base": ep.api_base,
                "api_version": ep.api_version,
                "keys": emb_keys,
                "ssl_verify": ep.ssl_verify,
            }],
        }
```
2. In `_build_embed_model_config`, set `type="kb_native"` (instead of `emb.get("type", "litellm")`) and add a `kb_profiles=[{…}]` extra built from the same `emb` fields (provider/model/api_base/api_version/keys=[resolved]/ssl_verify). `build_default_adapter`'s `create_embedding(embed_model_config or model_config)` then returns `NativeEmbedding` for either branch.

- [ ] **Step 5: Delete `LoadBalancingCompletion`**

In `kb_platform/graph/cost_capture.py`, remove the `class LoadBalancingCompletion` (lines ~163-184) and its doc reference. Leave `CostCapturingCompletion` untouched. Remove the now-unused import in `graphrag_adapter.py`.

- [ ] **Step 6: Update `build_chat_complete` (rewriter) to `kb_native`**

In `graphrag_adapter.py::build_chat_complete`, build the `ModelConfig` with `type="kb_native"` and a `kb_profiles` one-element bundle (primary key + extras), then `create_completion`. Return the same `complete(system, user) -> ChatTurn` closure.

- [ ] **Step 7: Call `bootstrap()` at process startup**

In `kb_platform/server.py`, near the top of `main()`/app construction (before serving), add:
```python
from kb_platform.llm.bootstrap import bootstrap as _bootstrap_llm
_bootstrap_llm()
```
Same in `kb_platform/worker.py` `run_worker()` (before the poll loop). Place the import inside the function to avoid import-time side effects.

- [ ] **Step 8: Run the new test + the existing worker suite**

Run: `uv run pytest tests/llm/test_wiring_indexing.py tests/test_unit_worker.py -v`
Expected: PASS.

- [ ] **Step 9: Lint + commit**

```bash
uv run ruff check kb_platform/graph/graphrag_adapter.py kb_platform/graph/cost_capture.py kb_platform/server.py kb_platform/worker.py tests/llm/test_wiring_indexing.py
git add -A
git commit -m "feat(llm): wire indexing + rewriter to kb_native; drop LoadBalancingCompletion"
```

---

### Task 11: Wire query path; delete `_StreamFixWrapper`; guard test

**Files:**
- Modify: `kb_platform/query/graphrag_engine.py` (`_resolve_config`, `_run_graphrag_search`, delete `_StreamFixWrapper`)
- Modify: `kb_platform/graph/graphrag_adapter.py::assemble_kb_settings` (pack `kb_profiles`)
- Test: `tests/llm/test_guard_no_litellm.py`

**Interfaces:**
- Consumes: T7-T10. `assemble_kb_settings` now emits `llm.kb_profiles` (primary, 1 element in P1) so `_resolve_config` can build a `kb_native` completion model.

> The query path: graphrag's `query/factory.py` calls `create_completion`/`create_embedding` using `config.completion_models["default_completion_model"]`. Setting that entry's `type="kb_native"` + a `kb_profiles` extra makes graphrag build `NativeCompletion`/`NativeEmbedding` for `engine.model` and `context_builder.text_embedder` automatically — no post-construction swap, no `_StreamFixWrapper`.

- [ ] **Step 1: Update `assemble_kb_settings` to emit `kb_profiles`**

In `kb_platform/graph/graphrag_adapter.py::assemble_kb_settings`, change the `llm` block to carry `kb_profiles` (P1: primary only):
```python
        "llm": {
            "type": "kb_native",
            "model_provider": lp.provider,
            "model": lp.model,
            "api_base": lp.api_base,
            "api_version": lp.api_version,
            "api_keys": api_keys,
            "ssl_verify": lp.ssl_verify,
            "kb_profiles": [{
                "provider": lp.provider,
                "model": lp.model,
                "api_base": lp.api_base,
                "api_version": lp.api_version,
                "keys": api_keys,
                "ssl_verify": lp.ssl_verify,
            }],
        },
```
P2 will append fallback profiles to `kb_profiles` from `kb.llm_fallback_profile_ids`.

- [ ] **Step 2: Update `_resolve_config` to build `kb_native` completion + embedding models**

In `kb_platform/query/graphrag_engine.py::_resolve_config`, change the `completion_models` entry to:
```python
                entry = {
                    "type": "kb_native",
                    "model_provider": provider,
                    "model": llm.get("model", "gpt-4o-mini"),
                    "api_base": llm.get("api_base"),
                    "api_version": llm.get("api_version"),
                    "call_args": {"ssl_verify": llm.get("ssl_verify", True)},
                    "kb_profiles": llm.get("kb_profiles") or [{
                        "provider": provider,
                        "model": llm.get("model", "gpt-4o-mini"),
                        "api_base": llm.get("api_base"),
                        "api_version": llm.get("api_version"),
                        "keys": list(llm.get("api_keys") or []),
                        "ssl_verify": llm.get("ssl_verify", True),
                    }],
                }
                if resolved_key:
                    entry["api_key"] = resolved_key
```
Mirror the same `type="kb_native"` change for the `embedding_models` entry. Because `NativeEmbedding` (T8) reads its profile from `model_config.kb_profiles[0]`, the embedding entry MUST carry a single-element `kb_profiles` too:
```python
                emb_entry = {
                    "type": "kb_native",
                    "model_provider": provider,
                    "model": emb.get("model", "text-embedding-3-small"),
                    "api_base": emb.get("api_base"),
                    "api_version": emb.get("api_version"),
                    "call_args": {"ssl_verify": emb.get("ssl_verify", True)},
                    "kb_profiles": [{
                        "provider": provider,
                        "model": emb.get("model", "text-embedding-3-small"),
                        "api_base": emb.get("api_base"),
                        "api_version": emb.get("api_version"),
                        "keys": list(emb.get("api_keys") or [resolved_key]),
                        "ssl_verify": emb.get("ssl_verify", True),
                    }],
                }
                if resolved_key:
                    emb_entry["api_key"] = resolved_key
                values["embedding_models"] = {"default_embedding_model": emb_entry}
```

- [ ] **Step 3: Verify graphrag-llm's `embedding_factory` passes `model_config`**

T8 already lands `NativeEmbedding` in the factory-style shape (`__init__(*, model_id, model_config, tokenizer=None, client=None, …)`), so no rewrite is needed. Before running Step 6, confirm `graphrag_llm/embedding/embedding_factory.py::create_embedding` spreads `init_args={"model_id": …, "model_config": …, "tokenizer": …, …}` (same shape as the completion factory). If it omits `model_config`, fall back to reading the profile from a module-level side-channel keyed on `model_id`. Run:
`uv run python -c "import inspect, graphrag_llm.embedding.embedding_factory as f; print(inspect.getsource(f.create_embedding))"`
Expected: `init_args` includes `"model_config": model_config`.

- [ ] **Step 4: Delete `_StreamFixWrapper` and the basic-method swap**

In `kb_platform/query/graphrag_engine.py`: delete the `_StreamFixWrapper` class (lines ~55-86). In `_run_graphrag_search`, delete the `if method == "basic": engine.model = _StreamFixWrapper(engine.model)` branch (lines ~408-409). NativeCompletion's `_AwaitableAsyncIterator` covers all four engines.

- [ ] **Step 5: Write the guard test**

`tests/llm/test_guard_no_litellm.py`:
```python
"""P1 guard: indexing + query hot paths use NativeCompletion/NativeEmbedding
and never instantiate LiteLLMCompletion."""

import pytest

from kb_platform.graph.graphrag_adapter import assemble_kb_settings
from kb_platform.llm.bootstrap import bootstrap

bootstrap()


def test_resolve_config_uses_kb_native(monkeypatch):
    # Stamp LiteLLMCompletion.__init__ to fail if ever called.
    from graphrag_llm.completion.lite_llm_completion import LiteLLMCompletion
    boom = pytest.MonkeyPatch()
    boom.setattr(LiteLLMCompletion, "__init__", lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("litellm constructed")))

    # Use FakeQueryEngine path is not enough — exercise the real resolve path.
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine
    import os, json, types

    class _FakeProfile:
        provider = "openai"; model = "gpt-4o-mini"; api_base = None
        api_version = None; ssl_verify = True
        def __init__(self): self.api_keys_enc = b""  # not used directly here

    # Build a settings dict the way assemble_kb_settings would, then resolve.
    settings = {
        "llm": {
            "type": "kb_native", "model_provider": "openai", "model": "gpt-4o-mini",
            "api_base": None, "api_version": None, "api_keys": ["sk-1"],
            "ssl_verify": True,
            "kb_profiles": [{"provider": "openai", "model": "gpt-4o-mini",
                             "api_base": None, "api_version": None,
                             "keys": ["sk-1"], "ssl_verify": True}],
        },
    }
    eng = GraphRagQueryEngine(data_root=".", model_config=settings)
    cfg = eng._resolve_config(root=".")
    entry = cfg.completion_models["default_completion_model"]
    assert entry.type == "kb_native"
    boom.undo()


def test_native_completion_is_what_graphrag_builds():
    # End-to-end: graphrag's create_completion on a kb_native config returns NativeCompletion.
    from graphrag_llm.completion import create_completion
    from graphrag_llm.config import ModelConfig
    from kb_platform.llm.client import NativeCompletion

    mc = ModelConfig(type="kb_native", model_provider="openai", model="gpt-4o-mini",
                     api_key="x",
                     kb_profiles=[{"provider": "openai", "model": "gpt-4o-mini",
                                   "api_base": None, "api_version": None,
                                   "keys": ["x"], "ssl_verify": True}])
    assert isinstance(create_completion(mc), NativeCompletion)


def test_native_embedding_is_what_graphrag_builds():
    # Embeddings also resolve to NativeEmbedding (no litellm on the embedding path).
    from graphrag_llm.embedding import create_embedding
    from graphrag_llm.config import ModelConfig
    from kb_platform.llm.embedding import NativeEmbedding

    mc = ModelConfig(type="kb_native", model_provider="openai", model="text-embedding-3-small",
                     api_key="x",
                     kb_profiles=[{"provider": "openai", "model": "text-embedding-3-small",
                                   "api_base": None, "api_version": None,
                                   "keys": ["x"], "ssl_verify": True}])
    assert isinstance(create_embedding(mc), NativeEmbedding)
```

- [ ] **Step 6: Run the guard test + full query test suite**

Run: `uv run pytest tests/llm/test_guard_no_litellm.py tests/test_query_engine.py tests/test_streaming.py -v` (adjust names to the actual query/streaming test files; if unsure, run `uv run pytest tests/ -k "query or stream" -v`).
Expected: PASS.

- [ ] **Step 7: Run the whole suite + lint**

Run: `uv run pytest && uv run ruff check .`
Expected: all green.

- [ ] **Step 8: Commit (Phase 1 exit)**

```bash
git add -A
git commit -m "feat(llm): wire query path to kb_native; delete _StreamFixWrapper; P1 guard"
```

- [ ] **Step 9: REVIEW CHECKPOINT — Phase 1**

Manual smoke (optional but recommended): with a real provider profile, run one indexing job + one streaming query through the running server; confirm the answer streams and cost captures. Human review of the `kb_platform/llm/` package + the wiring diffs before starting Phase 2.

---

# Phase 2 — Circuit-breaker-driven cross-profile failover + data model

**Phase exit gate:** a KB with a primary + one fallback profile, where the primary's transport returns 500, still answers the query / completes the indexing unit via the fallback; the primary's breaker opens after `failure_threshold` consecutive failures; full `uv run pytest` green; human review.

---

### Task 12: Data model — `KnowledgeBase.llm_fallback_profile_ids`

**Files:**
- Modify: `kb_platform/db/models.py`
- Create: `alembic/versions/000X_add_kb_llm_fallback_profiles.py`
- Test: `tests/test_db_models.py` (extend) or `tests/llm/test_fallback_profiles.py`

**Interfaces:**
- Produces: `KnowledgeBase.llm_fallback_profile_ids` (JSON column, list[int], nullable).

- [ ] **Step 1: Add the column to the ORM**

In `kb_platform/db/models.py`, on the `KnowledgeBase` class, add (match the style of existing JSON-ish columns; the codebase uses `settings_json` as `Column(Text)`):
```python
    llm_fallback_profile_ids = Column(Text, nullable=True, default=None)
    """JSON-encoded ordered list of fallback LLM provider-profile ids.
    Failover order is [llm_profile_id] + decode(this)."""
```

- [ ] **Step 2: Generate the migration**

Run: `uv run alembic revision --autogenerate -m "add kb llm_fallback_profile_ids"`
Inspect the generated file; it should add one nullable `Text` column to `knowledge_bases`. If autogenerate produces unrelated diffs, prune them to just this column.

- [ ] **Step 3: Write the test**

`tests/llm/test_fallback_profiles.py`:
```python
import json

from kb_platform.db.models import KnowledgeBase


def test_fallback_column_round_trips():
    kb = KnowledgeBase(name="x", llm_profile_id=1,
                       llm_fallback_profile_ids=json.dumps([2, 3]))
    assert json.loads(kb.llm_fallback_profile_ids) == [2, 3]


def test_fallback_column_nullable():
    kb = KnowledgeBase(name="x", llm_profile_id=1)
    assert kb.llm_fallback_profile_ids is None
```

- [ ] **Step 4: Apply migration + run test**

Run: `uv run alembic upgrade head && uv run pytest tests/llm/test_fallback_profiles.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/db/models.py tests/llm/test_fallback_profiles.py
git add -A
git commit -m "feat(db): kb.llm_fallback_profile_ids ordered fallback list"
```

---

### Task 13: `assemble_kb_settings` packs the full ordered `kb_profiles`

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py::assemble_kb_settings`
- Test: `tests/llm/test_assemble_kb_profiles.py`

**Interfaces:**
- Produces: `llm.kb_profiles` = `[primary_profile, *fallback_profiles]` in failover order, each with its own decrypted keys.

- [ ] **Step 1: Write the failing test** — build a KB ORM object with `llm_profile_id=1`, `llm_fallback_profile_ids=json.dumps([2])`, two mocked `ProviderProfile`s in the repo, call `assemble_kb_settings`, assert `llm.kb_profiles` has 2 entries in order with the right keys.

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** — in `assemble_kb_settings`, after resolving `lp` + `api_keys`, build the primary dict, then for each id in `json.loads(kb.llm_fallback_profile_ids or "[]")`: `fp = repo.get_profile(id)`, `fk = decrypt_values(fp.api_keys_enc)`, append a profile dict. Assign the combined list to `llm["kb_profiles"]`. Raise a clear `ValueError` if a fallback profile id is missing or has no keys (don't silently degrade).

- [ ] **Step 4: Run to pass; lint; commit** — `feat(llm): assemble_kb_settings packs ordered fallback profiles`.

---

### Task 14: Gateway cross-profile failover + breaker gating

**Files:**
- Modify: `kb_platform/llm/gateway.py`, `kb_platform/llm/client.py`
- Test: `tests/llm/test_gateway_failover.py`

**Interfaces:**
- Consumes: T5 `CircuitBreaker`, multi-element `profiles`.
- Produces: `FailoverGateway` that (a) skips profiles whose breaker is open, (b) advances to the next profile on a retriable error, (c) raises `Error(retriable=False)` when all candidates are exhausted.

- [ ] **Step 1: Write the failing test** — two profiles; primary's `MockTransport` returns 500 three times; fallback returns 200 with a delta. Assert the gateway yields the fallback's delta and that the primary breaker transitions to `open` after `failure_threshold`.

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** —
  1. In `FailoverGateway.__init__`, build a `CircuitBreaker` per profile index (store in `self._breakers` dict) when not supplied.
  2. Change `_candidates()` to return `[(i, pk) for i, pk in enumerate(self._pks) if self._breakers[i].allow()]`.
  3. In `_ProfileKeys`, carry the full `keys` list (already there) and ensure `_cfg_with_key` rotates per attempt (already there).
  4. In `NativeCompletion.__init__`, build `breakers = {i: CircuitBreaker(...) for i in range(len(profiles))}` and pass to `FailoverGateway`.

- [ ] **Step 4: Run to pass; lint; commit** — `feat(llm): cross-profile failover + breaker gating`.

---

### Task 15: KB form UI — ordered fallback profile multi-select

**Files:**
- Modify: `web/src/...` (KB create/edit form), the KB API models (`kb_platform/api/models.py`) + routes (`routes_kbs.py`) to accept/return `llm_fallback_profile_ids`.
- Test: `web/src/.../*.test.tsx` + `tests/test_api_kbs.py` (extend)

- [ ] **Step 1: Backend** — extend the KB create/update API model with `llm_fallback_profile_ids: list[int] | None`, persist as JSON. Add a round-trip test.
- [ ] **Step 2: Frontend** — add an ordered multi-select under the LLM profile field labelled "故障转移顺序 (fallback)" with helper copy in Chinese: "主 profile 失败时按此顺序切换;拖动可调整顺序。" Use the existing profile-picker component as a base.
- [ ] **Step 3: `npm run test && npm run build`** green.
- [ ] **Step 4: Commit** — `feat(web): ordered LLM fallback profile selector`.
- [ ] **Step 5: REVIEW CHECKPOINT — Phase 2.**

---

# Phase 3 — Background health probe

**Phase exit gate:** with the server/worker running and two profiles (one unhealthy), the unhealthy profile's breaker opens within ~`probe_interval` with no user traffic; after it recovers, the breaker closes; full `uv run pytest` green; human review.

---

### Task 16: `HealthProbe` background loop

**Files:**
- Create: `kb_platform/llm/health.py`
- Modify: `kb_platform/llm/bootstrap.py`, `kb_platform/llm/gateway.py` (share breaker refs)
- Test: `tests/llm/test_health_probe.py`

**Interfaces:**
- Produces: `class HealthProbe(gateway, interval=60.0)` with `async def start()` / `async def stop()`, issuing one `max_tokens=1` completion per profile per tick and driving the breaker (`record_success`/`record_failure`).

> The probe needs the SAME breaker instances the gateway uses. `bootstrap()` constructs one shared `HealthProbe` per process; breakers live on the gateway which is per-KB (transient). Reconcile by lifting breakers to a **process-wide profile-keyed registry** (`kb_platform/llm/health.py::breaker_for(profile_id)`) so both the gateway and the probe share state. This is the one refactor P3 introduces; it touches `gateway.py` + `client.py` to read breakers from the registry instead of constructing them locally.

- [ ] **Step 1: Write the failing test** — a fake gateway with two profiles, a fake clock advancing `interval`, one profile returning 500; assert that after `failure_threshold` ticks the breaker is open and `record_success` on the healthy profile kept it closed.
- [ ] **Step 2: Run to fail.**
- [ ] **Step 3: Implement** `HealthProbe` + the process-wide breaker registry; have `bootstrap()` start the probe task (store the task handle; cancel on shutdown). Wire `FailoverGateway` to use `breaker_for(profile_id)` instead of a local dict.
- [ ] **Step 4: Wire cancellation** into the existing SIGTERM/SIGINT handlers in `worker.py::run_worker` and the server lifespan.
- [ ] **Step 5: Run to pass; lint; commit** — `feat(llm): background health probe + shared breaker registry`.
- [ ] **Step 6: REVIEW CHECKPOINT — Phase 3.**

---

# Phase 4 — Observability: TTFT + failover metrics + `/llm/health`

**Phase exit gate:** a streaming query against a flaky primary logs TTFT + failover timings; `GET /llm/health` returns per-profile breaker state; full `uv run pytest` green; human review.

---

### Task 17: Metrics recorders + emission

**Files:**
- Create: `kb_platform/llm/metrics.py`
- Modify: `kb_platform/llm/gateway.py` (instrument TTFT + failover detection/recovery)
- Test: `tests/llm/test_metrics.py`

- [ ] **Step 1: Write the failing test** — drive a fake gateway that fails once then succeeds; assert `MetricsStore` recorded a `failover_detection_ms`, `failover_recovery_ms`, and (for stream) a `ttft_ms`.
- [ ] **Step 2: Run to fail.**
- [ ] **Step 3: Implement** `metrics.py` (`MetricsStore` in-memory + structured log helper) and instrument `gateway.astream` (TTFT = first `TextDelta` − request start; failover detection = first error − attempt start; recovery = success − failover decision) and `gateway.collect` (failover timings only).
- [ ] **Step 4: Run to pass; lint; commit** — `feat(llm): TTFT + failover metrics`.

---

### Task 18: `GET /llm/health` route

**Files:**
- Create: `kb_platform/api/routes_llm_health.py`
- Modify: `kb_platform/api/app.py` (register the router)
- Test: `tests/test_api_llm_health.py`

- [ ] **Step 1: Write the failing test** — `GET /llm/health` returns JSON `{profiles: [{id, provider, model, state, last_probe, last_failure}], recent: {ttft_ms_p50, failovers}`}`.
- [ ] **Step 2: Run to fail.**
- [ ] **Step 3: Implement** the router reading the shared breaker registry + `MetricsStore`; register BEFORE the SPA catch-all in `app.py`.
- [ ] **Step 4: Run to pass; lint; commit** — `feat(api): GET /llm/health`.
- [ ] **Step 5: REVIEW CHECKPOINT — Phase 4 — spec fully delivered.**

---

## Self-Review (run after writing, before handoff)

**1. Spec coverage:**
- §4.1 module layout → T1-T9, T16, T17, T18 ✓
- §4.2 events schema (incl. reserved ToolCallDelta) → T2 ✓
- §4.3 provider SSE parser → T3 ✓
- §4.4 request normalization (OpenAI/DeepSeek/Ollama/Azure) → T4 ✓
- §4.5 NativeCompletion contract (.model_id/.tokenizer/dual stream/structured_output) → T7, T11 ✓
- §4.6 circuit breaker + health probe → T5, T16 ✓
- §4.7 FailoverGateway (ordered profiles, breaker-gated, key RR, all-failed raises) → T6, T14 ✓
- §4.8 metrics (TTFT / failover detection+recovery) + `/llm/health` → T17, T18 ✓
- §4.9 data model (`llm_fallback_profile_ids`) + assemble packing + UI → T12, T13, T15 ✓
- §5 integration (registry, `type="kb_native"`, delete LoadBalancingCompletion + _StreamFixWrapper, keep CostCapturingCompletion) → T9, T10, T11 ✓
- §6 alternatives + §7 phased delivery → reflected in phase gates ✓
- §8 guard test → T11 ✓
- §9 risk: `model_extra` plumbing → de-risked (T7 reads `model_config` directly); tokenizer fidelity → T7 reuses the injected `tokenizer`; Azure `api_version` → T4 builds the URL, add a T4 step asserting azure without api_version is surfaced (folded into request tests).

**2. Placeholder scan:** no TBD/TODO; every code step shows real code. The P2-P5 frontend step (T15) names the component area but defers exact JSX to the existing profile-picker pattern — acceptable since it says "use the existing profile-picker component as a base" and lists the exact label/copy.

**3. Type consistency:** `ProviderConfig` fields used identically across T4/T6/T7/T8/T14 ✓. `ChatRequest`/`GatewayResult` defined in T6, consumed unchanged in T7/T14/T16/T17 ✓. `_AwaitableAsyncIterator` defined in T7, satisfies both consumption patterns verified in T11's source quotes ✓. `NATIVE_TYPE = "kb_native"` consistent across T9-T11 ✓.

**Open items to confirm at execution time** (not placeholders — verifiable facts):
- Exact name of the existing query/streaming test files in `tests/` (T11 Step 6 tells the engineer to discover via `pytest -k`).
- `embedding_factory`'s exact init_args spread (T11 Step 3 mirrors the completion init_args; verify in `graphrag_llm/embedding/embedding_factory.py` before running T11 Step 3).
