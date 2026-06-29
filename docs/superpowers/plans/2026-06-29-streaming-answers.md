# 流式回答(SSE)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 KB 平台的回答改为 token 级 SSE 流式:答案一边生成一边推给前端,聊天路径与单次查询路径都流式。

**Architecture:** 给 `QueryEngine` Protocol 加 `stream_search`(先吐 `StreamDelta`*、再恰好一个 `StreamDone` 收尾);`GraphRagQueryEngine` 经 graphrag 原生 `engine.stream_search()` 驱动,用 `QueryCallbacks.on_context` 钩子取回 sources。既有端点 `POST /kbs/{id}/query` 与 `POST /conversations/{id}/messages` 改吐 `text/event-stream`(SSE-only),事件 `meta`/`delta`/`done`/`error`。MCP 代理内部聚合 SSE,工具契约不变。前端用 `fetch`+`ReadableStream` 手解 SSE(`EventSource` 不支持 POST body)。

**Tech Stack:** Python 3.11 / FastAPI / Starlette `StreamingResponse` / graphrag `stream_search` / httpx;React + TS / 原生 `fetch` `ReadableStream` / vitest。

## Global Constraints

- `loop="asyncio"` 必须保持(uvicorn 与 e2e server 均如此);localhost 代理 gotcha 不变。
- 无新增 Python/npm 依赖(SSE 用 Starlette `StreamingResponse`;前端用原生 `fetch`+`ReadableStream`)。
- 无新增 DB 表、无 Alembic 迁移;worker / 索引路径**零改动**。
- Python ≥ 3.11;`uv run ruff check .` 通过(line-length 100);`uv run pytest` 全绿。
- 前端 `npm test` 与 `npm run build` 通过;新增 UI 文案中文,与 ChatPage 现有风格一致。
- 只有 `kb_platform/query/graphrag_engine.py` import graphrag(接缝纪律);`conversation/` 不 import graphrag/api。
- `chunk_id`/`content_hash` 等既有约定不动;失败绝不 500、绝不静默吞错。
- 经源码核实:四种方法的 `stream_search` 都正确 `await` `completion_async(stream=True)`,**流式路径不套 `_StreamFixWrapper`**;仅 basic 阻塞 `search()` 仍套(保持现状)。

## File Structure

**Create:**
- `kb_platform/api/sse.py` — SSE 序列化/解析:`format_sse(event, data)`、`iter_sse_events(line_aiter)`、`parse_sse(text)`。纯函数,前后端/MCP/测试共享事件语义。
- `web/src/lib/sse.ts` — 前端 SSE 解析:`parseSse(resp)` 异步生成器 → `{event, data}`。
- `tests/test_sse.py` — `sse.py` 单测。
- `tests/test_graphrag_engine_stream.py` — `GraphRagQueryEngine.stream_search` 接线 + `_SourceCapturingCallback` 单测(monkey-patch `_build_engine`,不读 parquet/不跑 LLM)。
- `web/src/lib/sse.test.ts` — `parseSse` 单测。
- `docs/verify-streaming-2026-06-29.md` — 手动冒烟验证清单。

**Modify:**
- `kb_platform/query/engine.py` — 加 `StreamDelta`/`StreamDone` 数据类;`QueryEngine` Protocol + `FakeQueryEngine` 加 `stream_search`。
- `kb_platform/query/graphrag_engine.py` — 抽 `_build_engine(method, root)` 公用;加 `_SourceCapturingCallback`;加 `stream_search`。
- `kb_platform/conversation/service.py` — 加 `StreamEvent` + `send_streaming`;抽出共享小函数(`_rewrite_once`)供 `send`/`send_streaming` 复用。
- `kb_platform/api/routes_conversations.py` — `send_message` 返回 `StreamingResponse`(SSE);`done` 事件经 `_message_out` 序列化。
- `kb_platform/api/routes_query.py` — `query_kb` 返回 `StreamingResponse`(SSE)。
- `kb_platform/mcp/server.py` — `KbApiClient.query()` 消费 SSE 流并聚合成单 dict。
- `web/src/api/client.ts` — `sendMessage`/`query` 改为返回 `fetch` `Response`(供流式迭代)。
- `web/src/pages/ChatPage.tsx` — `send` 迭代 SSE 增量渲染。
- `web/src/pages/QueryTestPage.tsx` — `ask` 迭代 SSE 增量渲染。
- `tests/test_api_conversations.py`、`tests/test_api_query.py`、`tests/test_query_route_enriched.py`、`tests/test_conversation_service.py`、`tests/test_mcp_server.py` — 适配 SSE/`stream_search`。
- `CLAUDE.md` — 在架构/convention 处补一句"查询端点为 SSE 流式"。

---

### Task 1: 引擎 Protocol — `StreamDelta`/`StreamDone` + `FakeQueryEngine.stream_search`

**Files:**
- Modify: `kb_platform/query/engine.py`
- Test: `tests/test_query_engine.py`

**Interfaces:**
- Produces: `StreamDelta(text: str)`、`StreamDone(answer, method, elapsed_ms, prompt_tokens, output_tokens, sources, error)`;`QueryEngine.stream_search(method, query, kb_data_root) -> AsyncIterator[StreamDelta | StreamDone]`;`FakeQueryEngine.stream_search`。

- [ ] **Step 1: Write the failing test** — append to `tests/test_query_engine.py`:

```python
from kb_platform.query.engine import FakeQueryEngine, StreamDelta, StreamDone


@pytest.mark.asyncio
async def test_fake_stream_search_yields_deltas_then_done():
    engine = FakeQueryEngine()
    out = [e async for e in engine.stream_search("local", "what is ACME?", "/tmp")]
    # contract: 0+ deltas then exactly one StreamDone
    assert isinstance(out[-1], StreamDone)
    deltas = out[:-1]
    assert deltas and all(isinstance(d, StreamDelta) for d in deltas)
    # the concatenated delta text equals the same answer search() returns
    blocking = await engine.search("local", "what is ACME?", "/tmp")
    assert "".join(d.text for d in deltas) == blocking.answer
    done = out[-1]
    assert done.method == "local"
    assert done.answer == blocking.answer
    assert done.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_query_engine.py::test_fake_stream_search_yields_deltas_then_done -v`
Expected: FAIL with `ImportError: cannot import name 'StreamDelta'` (or AttributeError on `stream_search`).

- [ ] **Step 3: Write minimal implementation** — edit `kb_platform/query/engine.py`. Add the two dataclasses after `QueryResult`, add `stream_search` to the `QueryEngine` Protocol, and implement it on `FakeQueryEngine`:

```python
from collections.abc import AsyncIterator
```
(add to the existing imports at the top of the file)

```python
@dataclass
class StreamDelta:
    """One incremental answer chunk (token run) from a streaming search."""

    text: str


@dataclass
class StreamDone:
    """Terminal event of a streaming search. Carries the full accumulated answer
    plus the same metadata `QueryResult` carries. ``error`` non-empty => failure
    (``answer`` then holds whatever streamed before the failure)."""

    answer: str = ""
    method: str | None = None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    sources: list[SourceRef] | None = None
    error: str | None = None
```

Add to the `QueryEngine` Protocol body (after `search`):

```python
    async def stream_search(
        self, method: str, query: str, kb_data_root: str
    ) -> AsyncIterator["StreamDelta | StreamDone"]: ...
```

Add to `FakeQueryEngine` (after its `search`):

```python
    async def stream_search(self, method: str, query: str, kb_data_root: str):
        answer = f"[{method}] You asked: {query}"
        # stream word-by-word so tests see multiple deltas
        parts = answer.split(" ")
        for i, w in enumerate(parts):
            yield StreamDelta(text=(w + (" " if i < len(parts) - 1 else "")))
        yield StreamDone(answer=answer, method=method)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_query_engine.py -v`
Expected: PASS (both the existing `test_fake_query_engine` and the new streaming test).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/query/engine.py tests/test_query_engine.py
git add kb_platform/query/engine.py tests/test_query_engine.py
git commit -m "feat(query): StreamDelta/StreamDone + FakeQueryEngine.stream_search"
```

---

### Task 2: SSE 序列化/解析 helper `kb_platform/api/sse.py`

**Files:**
- Create: `kb_platform/api/sse.py`
- Test: `tests/test_sse.py`

**Interfaces:**
- Produces: `format_sse(event: str, data) -> str`(序列化成 `event: …\ndata: {json}\n\n`);`parse_sse(text: str) -> list[tuple[str, dict]]`(整段文本解析,测试用);`async iter_sse_events(line_aiter) -> AsyncIterator[tuple[str, dict]]`(按行异步解析,MCP 用)。

- [ ] **Step 1: Write the failing test** — create `tests/test_sse.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sse.py -v`
Expected: FAIL with `ModuleNotFoundError: kb_platform.api.sse`.

- [ ] **Step 3: Write minimal implementation** — create `kb_platform/api/sse.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sse.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/api/sse.py tests/test_sse.py
git add kb_platform/api/sse.py tests/test_sse.py
git commit -m "feat(api): SSE framing helper (format/parse/iter)"
```

---

### Task 3: `ConversationService.send_streaming` + 共享改写小函数

**Files:**
- Modify: `kb_platform/conversation/service.py`
- Test: `tests/test_conversation_service.py`

**Interfaces:**
- Consumes: `kb_platform.query.engine.StreamDelta`/`StreamDone` (Task 1).
- Produces: `StreamEvent(type, data, message)` 数据类;`ConversationService.send_streaming(conversation_id, content, method) -> AsyncIterator[StreamEvent]`。契约:`meta` → 0..n `delta`(`data={"text":...}`) → 恰好一个终止事件(`done` 带 `message`=持久化 ORM Message,或 `error` 带 `{"message":...}`)。落 assistant 消息时机同 `send`(流结束一次性落库)。

- [ ] **Step 1: Write the failing test** — append to `tests/test_conversation_service.py`:

```python
from kb_platform.conversation.service import ConversationService, StreamEvent


async def _drain(gen):
    return [e async for e in gen]


async def test_send_streaming_first_turn_meta_delta_done(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), None, data_root=".")
    events = await _drain(svc.send_streaming(cid, "What does Acme do?", None))
    assert [e.type for e in events[:1]] == ["meta"]
    assert events[0].data["method"] == "local"
    assert "rewritten_query" not in events[0].data  # first turn: no rewrite
    deltas = [e for e in events if e.type == "delta"]
    assert deltas  # at least one streamed chunk
    terminals = [e for e in events if e.type in ("done", "error")]
    assert len(terminals) == 1 and terminals[0].type == "done"
    done = terminals[0]
    # done carries the persisted assistant message
    assert done.message.role == "assistant"
    assert "What does Acme do?" in done.message.content
    # persisted to DB exactly once (user + assistant)
    rows = repo.get_messages(cid)
    assert [r.role for r in rows] == ["user", "assistant"]


async def test_send_streaming_followup_rewrites(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    rw = _RecordingRewriter()
    svc = ConversationService(repo, FakeQueryEngine(), rw, data_root=".")
    await svc.send(cid, "What does Acme do?", "global")  # seed a turn
    events = await _drain(svc.send_streaming(cid, "who is the CEO?", None))
    meta = next(e for e in events if e.type == "meta")
    assert meta.data["rewritten_query"] == "REWRITTEN::who is the CEO?"
    assert meta.data["method"] == "global"  # defaulted from prior assistant
    done = next(e for e in events if e.type == "done")
    assert "REWRITTEN::who is the CEO?" in done.message.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_conversation_service.py::test_send_streaming_first_turn_meta_delta_done -v`
Expected: FAIL with `ImportError: cannot import name 'StreamEvent'`.

- [ ] **Step 3: Write minimal implementation** — edit `kb_platform/conversation/service.py`.

Add imports (top, after existing imports):

```python
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from kb_platform.query.engine import StreamDelta, StreamDone
```

Add the `StreamEvent` dataclass after the imports (before `class ConversationService`):

```python
@dataclass
class StreamEvent:
    """One SSE-bound event from ``send_streaming``.

    ``data`` is the JSON-serializable payload for meta/delta/error. For ``done``
    the persisted ORM ``Message`` is carried on ``message`` and serialized by the
    route (the service stays free of ``api.models``).
    """

    type: str  # "meta" | "delta" | "done" | "error"
    data: dict = field(default_factory=dict)
    message: Any = None  # ORM Message, set only for type == "done"
```

Refactor the rewrite block out of `send` into a helper, then add `send_streaming`. Replace the existing `send` method body's rewrite section. The new `send` and `send_streaming` share `_rewrite_once`. Final `ConversationService` body:

```python
    async def _rewrite_once(self, content, history):
        """Run the rewriter if there is history. Returns
        (rewrote, rewrite_fell_back, prompt_tokens, output_tokens, standalone)."""
        rewrite_fell_back = False
        rw_pt = 0
        rw_ot = 0
        standalone = content
        if not history or self._rewriter is None:
            return False, False, 0, 0, content
        try:
            rr = await self._rewriter.rewrite(content, history)
            return True, False, rr.prompt_tokens, rr.output_tokens, rr.standalone
        except Exception:  # noqa: BLE001 - fall back to raw message, never block
            logger.exception("query rewrite failed; falling back to raw message")
            return False, True, 0, 0, content
```

Replace the rewrite block inside the existing `send` (the lines from `rewrote = False` through the `rewrite_fell_back = True` except block) with:

```python
        rewrote, rewrite_fell_back, rw_pt, rw_ot, standalone = await self._rewrite_once(
            content, history
        )
```

(The rest of `send` — persist user, `await self._engine.search(...)`, persist assistant, touch/title — stays unchanged.)

Add `send_streaming` to `ConversationService` (after `send`):

```python
    async def send_streaming(
        self, conversation_id: int, content: str, method: str | None
    ) -> AsyncIterator[StreamEvent]:
        """Streaming variant of ``send``: rewrite -> meta -> persist user ->
        stream answer deltas -> persist assistant -> done|error."""
        conv = self._repo.get_conversation(conversation_id)
        if conv is None:
            return

        history_rows = self._repo.recent_messages(conversation_id)
        history = [HistoryTurn(r.role, r.content) for r in history_rows]
        chosen_method = method or _last_method(history_rows) or "local"

        rewrote, rewrite_fell_back, rw_pt, rw_ot, standalone = await self._rewrite_once(
            content, history
        )
        meta = {"method": chosen_method, "rewrite_fell_back": rewrite_fell_back}
        if rewrote:
            meta["rewritten_query"] = standalone
        yield StreamEvent("meta", meta)

        self._repo.add_message(conversation_id, role="user", content=content)

        accumulated = ""
        done: StreamDone | None = None
        async for ev in self._engine.stream_search(chosen_method, standalone, self._data_root):
            if isinstance(ev, StreamDelta):
                accumulated += ev.text
                yield StreamEvent("delta", {"text": ev.text})
            else:  # StreamDone
                done = ev
        if done is None:  # engine misbehaved; synthesize an error terminal
            done = StreamDone(method=chosen_method, error="stream ended without a done event")
        if done.answer:
            accumulated = done.answer  # prefer the engine's authoritative full text

        assistant = self._repo.add_message(
            conversation_id,
            role="assistant",
            content=accumulated or (done.error or ""),
            method=chosen_method,
            rewritten_query=standalone if rewrote else None,
            rewrite_fell_back=rewrite_fell_back,
            sources_json=_serialize_sources(done.sources),
            prompt_tokens=_merge_tokens(rw_pt, done.prompt_tokens),
            output_tokens=_merge_tokens(rw_ot, done.output_tokens),
            elapsed_ms=done.elapsed_ms,
            error=done.error,
        )
        self._repo.touch_conversation(conversation_id)
        if not conv.title:
            self._repo.update_conversation(conversation_id, _title_from(content))

        if done.error:
            yield StreamEvent("error", {"message": done.error})
        else:
            yield StreamEvent("done", {}, message=assistant)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_conversation_service.py -v`
Expected: PASS (all existing + 2 new streaming tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/conversation/service.py tests/test_conversation_service.py
git add kb_platform/conversation/service.py tests/test_conversation_service.py
git commit -m "feat(conversation): ConversationService.send_streaming (meta/delta/done)"
```

---

### Task 4: `GraphRagQueryEngine.stream_search` + `_build_engine` 抽取 + `_SourceCapturingCallback`

**Files:**
- Modify: `kb_platform/query/graphrag_engine.py`
- Test: `tests/test_graphrag_engine_stream.py`

**Interfaces:**
- Consumes: `kb_platform.query.engine.StreamDelta`/`StreamDone` (Task 1).
- Produces: `GraphRagQueryEngine.stream_search(method, query, kb_data_root) -> AsyncIterator[StreamDelta | StreamDone]`;`_build_engine(method, root)`(从 `_run_graphrag_search` 抽出的公用构造器,`search` 与 `stream_search` 共用);`_SourceCapturingCallback`(捕 `on_context`→`context_data`)。

- [ ] **Step 1: Write the failing test** — create `tests/test_graphrag_engine_stream.py`:

```python
"""GraphRagQueryEngine.stream_search wiring (no real LLM, no parquet).

We monkey-patch ``_build_engine`` to return a fake graphrag engine whose
``stream_search`` yields known chunks and fires ``on_context`` — exercising the
delta→StreamDelta, done→StreamDone, and sources-via-callback wiring without
graphrag's index/LLM machinery.
"""

import types
from unittest.mock import patch

import pandas as pd
import pytest

from kb_platform.query.engine import StreamDelta, StreamDone
from kb_platform.query.graphrag_engine import GraphRagQueryEngine, _SourceCapturingCallback


def test_source_capturing_callback_records_context():
    cb = _SourceCapturingCallback()
    assert cb.context_data is None
    cb.on_context({"entities": "x"})
    assert cb.context_data == {"entities": "x"}
    # any other callback hook is a no-op (must not raise)
    cb.on_llm_new_token("t")
    cb.on_map_response_end([])
    cb.on_reduce_response_start("ctx")


class _FakeGraphragEngine:
    """Stand-in for a graphrag search engine: yields chunks, fires on_context."""

    def __init__(self, chunks, context_data):
        self._chunks = chunks
        self._context_data = context_data
        self.callbacks: list = []
        self.model = types.SimpleNamespace()  # present so attribute access works

    async def stream_search(self, query):  # noqa: ARG002 (query unused)
        for cb in self.callbacks:
            cb.on_context(self._context_data)
        for c in self._chunks:
            yield c


@pytest.mark.asyncio
async def test_stream_search_yields_deltas_then_done_with_sources():
    ents = pd.DataFrame([{"name": "ACME", "description": "a company"}])
    engine = GraphRagQueryEngine(data_root=".", model_config=None)
    fake = _FakeGraphragEngine(["Hello ", "world"], {"entities": ents})
    with patch.object(engine, "_build_engine", return_value=fake):
        out = [e async for e in engine.stream_search("local", "q", ".")]
    assert [type(e).__name__ for e in out] == ["StreamDelta", "StreamDelta", "StreamDone"]
    assert out[0].text == "Hello " and out[1].text == "world"
    done = out[2]
    assert done.answer == "Hello world"
    assert done.method == "local"
    assert done.elapsed_ms is not None and done.elapsed_ms >= 0
    assert done.error is None
    assert done.sources and done.sources[0].kind == "entity" and done.sources[0].name == "ACME"


@pytest.mark.asyncio
async def test_stream_search_reports_missing_reports_guard():
    engine = GraphRagQueryEngine(data_root=".", model_config=None)
    # global needs community_reports.parquet; with no data_root files, guard fires
    out = [e async for e in engine.stream_search("global", "q", "/nonexistent-root")]
    assert len(out) == 1 and isinstance(out[0], StreamDone)
    assert out[0].error and "community reports" in out[0].error


@pytest.mark.asyncio
async def test_stream_search_wraps_build_engine_failure():
    engine = GraphRagQueryEngine(data_root=".", model_config=None)
    with patch.object(engine, "_build_engine", side_effect=FileNotFoundError("missing parquet")):
        out = [e async for e in engine.stream_search("local", "q", ".")]
    assert len(out) == 1 and isinstance(out[0], StreamDone)
    assert "missing parquet" in (out[0].error or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graphrag_engine_stream.py -v`
Expected: FAIL with `ImportError: cannot import name '_SourceCapturingCallback'` / AttributeError `stream_search`.

- [ ] **Step 3: Write minimal implementation** — edit `kb_platform/query/graphrag_engine.py`.

Add `time` import at top (alongside `import logging`, `import os`):

```python
import time
```

Add `StreamDelta`/`StreamDone` to the existing `from kb_platform.query.engine import ...` line:

```python
from kb_platform.query.engine import QueryResult, SourceRef, StreamDelta, StreamDone
```

Add the `_SourceCapturingCallback` class (near `_StreamFixWrapper`, above `GraphRagQueryEngine`):

```python
class _SourceCapturingCallback:
    """Duck-typed ``QueryCallbacks``: captures ``on_context`` so ``stream_search``
    can extract sources; every other callback hook is a no-op."""

    def __init__(self) -> None:
        self.context_data = None

    def on_context(self, context) -> None:
        self.context_data = context

    def __getattr__(self, name):
        # on_llm_new_token / on_map_response_* / on_reduce_response_* ... → no-op
        return lambda *args, **kwargs: None
```

Refactor `_run_graphrag_search` to extract `_build_engine`. The new structure: `_build_engine` owns everything from the imports through constructing the engine (returning the raw engine, **no** model wrapping). `_run_graphrag_search` calls it, applies the basic `_StreamFixWrapper`, runs `search()`, maps the result. Concretely, replace the body of `_run_graphrag_search` (everything after the docstring) so it becomes:

```python
        engine = self._build_engine(method, query, root)
        # BasicSearch.search() calls completion_async(stream=True) WITHOUT await
        # → wrap so streaming returns an async gen (graphrag-llm returns a coroutine).
        if method == "basic":
            engine.model = _StreamFixWrapper(engine.model)
        result = await engine.search(query=query)
        return self._result_from_search(method, result)
```

And add `_build_engine` (it holds the body that used to be inside `_run_graphrag_search` — imports, `_resolve_config`, parquet reads, indexer adapters, per-method factory selection — and returns the engine). Note it now takes `query` only because the original code referenced `query` for nothing in construction (it's passed to `engine.search` by the caller), so the signature is `_build_engine(self, method, root)` — `query` is NOT needed for construction; remove the `query` arg. Verify the original construction body does not use `query` (it does not; `query` is only passed to `engine.search`/`stream_search`). Final:

```python
    def _build_engine(self, method: str, root: str):
        """Construct the graphrag search engine for ``method`` from on-disk index
        parquet + resolved config. Shared by ``search`` and ``stream_search``.

        Returns the raw engine (no model wrapping). Raises FileNotFoundError if
        a required parquet artifact is missing, or any graphrag config error.
        """
        import pandas as pd

        from graphrag.query.factory import (
            get_basic_search_engine,
            get_drift_search_engine,
            get_global_search_engine,
            get_local_search_engine,
        )
        from graphrag.query.indexer_adapters import (
            read_indexer_communities,
            read_indexer_entities,
            read_indexer_relationships,
            read_indexer_reports,
            read_indexer_text_units,
        )

        config = self._resolve_config(root=root)
        community_level = 2
        response_type = "multiple paragraphs"

        ls = (self._model_config if isinstance(self._model_config, dict) else {}) if self._model_config else {}
        qp = ls.get("query_prompts") or {}
        local_prompt = qp.get("local_system")
        global_map_prompt = qp.get("global_map")
        global_reduce_prompt = qp.get("global_reduce")
        basic_prompt = qp.get("basic_system")

        def _read(name: str) -> pd.DataFrame:
            path = os.path.join(root, name)
            if not os.path.exists(path):
                raise FileNotFoundError(f"missing index artifact: {name} under {root}")
            return pd.read_parquet(path)

        entities_df = _norm_entities(_read("entities.parquet"))
        communities_df = _norm_communities(_read("communities.parquet"))
        reports_df = _norm_reports(_read("community_reports.parquet"))
        text_units_df = _norm_text_units(
            _read("text_unit_ids.parquet")
            if os.path.exists(os.path.join(root, "text_unit_ids.parquet"))
            else _read("text_units.parquet")
        )
        relationships_df = _norm_relationships(_read("relationships.parquet"))

        communities = read_indexer_communities(communities_df, reports_df)
        reports = read_indexer_reports(reports_df, communities_df, community_level=community_level)
        entities = read_indexer_entities(entities_df, communities_df, community_level=community_level)
        relationships = read_indexer_relationships(relationships_df)
        text_units = read_indexer_text_units(text_units_df)

        if method == "local":
            store = self._build_embedding_store(config, _ENTITY_DESCRIPTION)
            return get_local_search_engine(
                config,
                reports=reports,
                text_units=text_units,
                entities=entities,
                relationships=relationships,
                covariates={},
                response_type=response_type,
                description_embedding_store=store,
                system_prompt=local_prompt,
            )
        if method == "global":
            return get_global_search_engine(
                config,
                reports=reports,
                entities=entities,
                communities=communities,
                response_type=response_type,
                map_system_prompt=global_map_prompt,
                reduce_system_prompt=global_reduce_prompt,
            )
        if method == "drift":
            store = self._build_embedding_store(config, _ENTITY_DESCRIPTION)
            report_store = self._build_embedding_store(config, _COMMUNITY_FULL_CONTENT)
            from graphrag.query.indexer_adapters import read_indexer_report_embeddings

            read_indexer_report_embeddings(reports, report_store)
            return get_drift_search_engine(
                config,
                reports=reports,
                text_units=text_units,
                entities=entities,
                relationships=relationships,
                description_embedding_store=store,
                response_type=response_type,
                local_system_prompt=local_prompt,
                reduce_system_prompt=global_reduce_prompt,
            )
        if method == "basic":
            store = self._build_embedding_store(config, _TEXT_UNIT_TEXT)
            return get_basic_search_engine(
                text_units=text_units,
                text_unit_embeddings=store,
                config=config,
                response_type=response_type,
                system_prompt=basic_prompt,
            )
        raise ValueError(f"unknown query method: {method}")
```

Add `stream_search` to `GraphRagQueryEngine` (after `search`):

```python
    async def stream_search(self, method: str, query: str, kb_data_root: str):
        """Stream the final answer as ``StreamDelta`` chunks then one ``StreamDone``.

        graphrag's four engines expose ``async stream_search(query)`` that yield
        answer ``str`` deltas after doing retrieval/map-reduce internally. We
        register a ``_SourceCapturingCallback`` to recover sources via
        ``on_context`` (best-effort; drift never fires it → sources None).
        Verified: all four ``stream_search`` AWAITS ``completion_async(stream=True)``
        correctly, so no ``_StreamFixWrapper`` is needed here.
        """
        root = self._data_root or kb_data_root
        if method in _REPORTS_REQUIRED and not os.path.exists(
            os.path.join(root, _COMMUNITY_REPORTS_FILE)
        ):
            yield StreamDone(method=method, answer="", error=_NO_REPORTS_MSG)
            return
        try:
            engine = self._build_engine(method, root)
        except Exception as e:  # noqa: BLE001 - missing parquet / config error
            logger.exception("stream_search build_engine failed for method=%s", method)
            yield StreamDone(method=method, answer="", error=str(e))
            return

        capturer = _SourceCapturingCallback()
        engine.callbacks.append(capturer)
        start = time.time()
        accumulated = ""
        try:
            async for chunk in engine.stream_search(query=query):
                accumulated += chunk or ""
                yield StreamDelta(text=chunk or "")
            yield StreamDone(
                answer=accumulated,
                method=method,
                elapsed_ms=round((time.time() - start) * 1000, 1),
                sources=self._extract_sources(capturer.context_data, method),
            )
        except Exception as e:  # noqa: BLE001 - surface as a terminal error event
            logger.exception("graphrag stream_search failed for method=%s", method)
            yield StreamDone(
                method=method,
                answer=accumulated,
                error=str(e),
                elapsed_ms=round((time.time() - start) * 1000, 1),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_graphrag_engine_stream.py tests/test_query_sources.py -v`
Expected: PASS (4 new stream tests; `test_query_sources.py` still green — `_extract_sources`/`_norm_*` untouched).

- [ ] **Step 5: Run the full query test surface + lint**

Run: `uv run pytest tests/test_query_engine.py tests/test_query_schema_bridge.py tests/test_query_sources.py tests/test_graphrag_engine_stream.py -v && uv run ruff check kb_platform/query/graphrag_engine.py`
Expected: PASS (the `_build_engine` refactor preserves blocking `search()` behavior; lint clean).

- [ ] **Step 6: Commit**

```bash
git add kb_platform/query/graphrag_engine.py tests/test_graphrag_engine_stream.py
git commit -m "feat(query): GraphRagQueryEngine.stream_search + _build_engine refactor"
```

---

### Task 5: `POST /conversations/{id}/messages` → SSE

**Files:**
- Modify: `kb_platform/api/routes_conversations.py`
- Test: `tests/test_api_conversations.py`

**Interfaces:**
- Consumes: `kb_platform.api.sse.format_sse` (Task 2);`ConversationService.send_streaming` / `StreamEvent` (Task 3).
- Produces: `send_message` returns `StreamingResponse(media_type="text/event-stream")`;事件经 `_message_out` 序列化 `MessageOut`。

- [ ] **Step 1: Write the failing test** — in `tests/test_api_conversations.py`, replace the bodies of `test_send_first_turn_then_followup`, `test_send_missing_conversation_404`, and `test_production_settings_error_is_graceful` to parse SSE. Add a helper at the top (after imports):

```python
from kb_platform.api.sse import parse_sse


async def _post_sse(client, path, body):
    """POST and parse the SSE response into a list of (event, data)."""
    r = await client.post(path, json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)
```

Replace `test_send_first_turn_then_followup` with:

```python
async def test_send_first_turn_then_followup(client):
    await client.__aenter__()
    try:
        cid = (await client.post("/kbs/1/conversations", json={})).json()["id"]
        ev1 = await _post_sse(client, f"/conversations/{cid}/messages", {"content": "hi", "method": "local"})
        types1 = [e for e, _ in ev1]
        assert types1[0] == "meta" and types1[-1] == "done"
        assert "delta" in types1
        done1 = next(d for e, d in ev1 if e == "done")
        msg1 = done1["message"]
        assert msg1["role"] == "assistant" and "hi" in msg1["content"]
        assert msg1["rewritten_query"] is None and msg1["rewrite_fell_back"] is False
        # second turn rewrites
        ev2 = await _post_sse(client, f"/conversations/{cid}/messages", {"content": "more"})
        meta2 = next(d for e, d in ev2 if e == "meta")
        assert meta2["rewritten_query"] is not None  # follow-up was rewritten
        assert meta2["method"] == "local"  # defaulted from prior assistant
        # detail has 4 rows
        det = await client.get(f"/conversations/{cid}")
        assert len(det.json()["messages"]) == 4
    finally:
        await client.__aexit__(None, None, None)
```

Replace `test_production_settings_error_is_graceful` with:

```python
async def test_production_settings_error_is_graceful(tmp_path):
    app = _make_app(tmp_path, inject=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/kbs/1/conversations", json={})).json()["id"]
        r = await ac.post(f"/conversations/{cid}/messages", json={"content": "hi"})
        assert r.status_code == 200
        events = parse_sse(r.text)
        err = next(d for e, d in events if e == "error")
        assert err["message"].startswith("settings resolution failed")
```

(`test_send_missing_conversation_404` stays as-is — it asserts `r.status_code == 404`, which the route still raises before streaming.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_conversations.py -v`
Expected: FAIL — `send_message` still returns JSON, so `parse_sse(r.text)` sees no SSE events (no `done`/`error` found → StopIteration).

- [ ] **Step 3: Write minimal implementation** — edit `kb_platform/api/routes_conversations.py`.

Add imports:

```python
from kb_platform.api.sse import format_sse
from kb_platform.conversation.service import ConversationService, StreamEvent
from starlette.responses import StreamingResponse
```

Replace the `send_message` handler with a streaming version:

```python
@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: int, payload: MessageSend, request: Request):
    repo = request.app.state.repo
    if repo.get_conversation(conv_id) is None:
        raise HTTPException(404)
    engine = request.app.state.query_engine
    rewriter = request.app.state.rewriter
    data_root = request.app.state.data_root

    async def gen():
        # Production: resolve KB settings, build a real engine + rewriter.
        if engine is None:
            try:
                conv = repo.get_conversation(conv_id)
                kb = repo.get_kb(conv.kb_id) if conv else None
                if kb is None:
                    yield format_sse("error", {"message": f"conversation {conv_id} has no kb"})
                    return
                from kb_platform.conversation.rewriter import LlmRewriter
                from kb_platform.graph.graphrag_adapter import assemble_kb_settings, build_chat_complete
                from kb_platform.query.graphrag_engine import GraphRagQueryEngine

                settings = assemble_kb_settings(kb, repo)
                local_engine = GraphRagQueryEngine(data_root=kb.data_root, model_config=settings)
                try:
                    local_rewriter = LlmRewriter(build_chat_complete(settings))
                except Exception:  # noqa: BLE001 - rewriter optional
                    local_rewriter = None
            except Exception as exc:  # noqa: BLE001 - graceful error, never 500
                yield format_sse("error", {"message": f"settings resolution failed: {exc}"})
                return
        else:
            local_engine = engine
            local_rewriter = rewriter

        service = ConversationService(repo, local_engine, local_rewriter, data_root)
        async for ev in service.send_streaming(conv_id, payload.content, payload.method):
            if ev.type == "done" and ev.message is not None:
                yield format_sse("done", {"message": _message_out(ev.message).model_dump(mode="json")})
            else:
                yield format_sse(ev.type, ev.data)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_conversations.py -v`
Expected: PASS (all conversation route tests, SSE-parsed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/api/routes_conversations.py tests/test_api_conversations.py
git add kb_platform/api/routes_conversations.py tests/test_api_conversations.py
git commit -m "feat(api): POST /conversations/{id}/messages streams SSE"
```

---

### Task 6: `POST /kbs/{id}/query` → SSE

**Files:**
- Modify: `kb_platform/api/routes_query.py`
- Modify: `tests/test_api_query.py`, `tests/test_query_route_enriched.py`
- Test: `tests/test_api_query.py`

**Interfaces:**
- Consumes: `kb_platform.api.sse.format_sse`;`QueryEngine.stream_search` / `StreamDelta` / `StreamDone` (Tasks 1, 4);`QueryResultOut`/`SourceOut`.
- Produces: `query_kb` returns `StreamingResponse`;SSE `meta`/`delta`/`done`(done 带 `QueryResultOut`)/`error`。

- [ ] **Step 1: Write the failing test** — in `tests/test_api_query.py`, add a helper and convert the JSON assertions to SSE. Add after imports:

```python
from kb_platform.api.sse import parse_sse


def _post_sse(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)
```

Replace `test_query_returns_answer` with:

```python
def test_query_returns_answer(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    events = _post_sse(client, "/kbs/1/query", {"method": "local", "query": "what is ACME?"})
    types = [e for e, _ in events]
    assert types[0] == "meta" and types[-1] == "done" and "delta" in types
    done = next(d for e, d in events if e == "done")["result"]
    assert done["method"] == "local"
    assert "ACME" in done["answer"]
```

Replace `test_query_builds_real_engine_per_kb_when_not_injected` with:

```python
def test_query_builds_real_engine_per_kb_when_not_injected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    client = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post("/kbs/1/query", json={"method": "global", "query": "hello"})
    assert r.status_code == 200  # graceful: SSE error event, not 500
    events = parse_sse(r.text)
    err = next((d for e, d in events if e == "error"), None)
    assert err is not None  # no community reports / no LLM → error event
```

(`test_query_positional_args_still_work` is unchanged — it only checks `app.state.query_engine is None`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_query.py -v`
Expected: FAIL — route returns JSON, `parse_sse` finds no `done`/`error`.

- [ ] **Step 3: Write minimal implementation** — edit `kb_platform/api/routes_query.py`. Replace the whole file content with:

```python
"""Query endpoint: POST /kbs/{id}/query (SSE streaming)."""

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import StreamingResponse

from kb_platform.api.models import QueryRequest, QueryResultOut, SourceOut
from kb_platform.api.sse import format_sse
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.query.engine import StreamDelta

router = APIRouter()


@router.post("/kbs/{kb_id}/query")
async def query_kb(kb_id: int, payload: QueryRequest, request: Request):
    engine = request.app.state.query_engine
    data_root = request.app.state.data_root

    async def gen():
        # Injected engine (tests) takes priority; otherwise build a real one per-KB.
        local_engine = engine
        if local_engine is None:
            from kb_platform.graph.graphrag_adapter import assemble_kb_settings
            from kb_platform.query.graphrag_engine import GraphRagQueryEngine

            repo = request.app.state.repo
            with session_scope(repo.engine) as s:
                kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
                if kb is None:
                    yield format_sse("error", {"message": f"kb {kb_id} not found"})
                    return
                data_root = kb.data_root
                try:
                    model_config = assemble_kb_settings(kb, repo)
                except Exception as exc:  # noqa: BLE001 - graceful, never 500
                    yield format_sse("error", {"message": f"settings resolution failed: {exc}"})
                    return
            local_engine = GraphRagQueryEngine(data_root=data_root, model_config=model_config)

        yield format_sse("meta", {"method": payload.method})
        async for ev in local_engine.stream_search(payload.method, payload.query, data_root):
            if isinstance(ev, StreamDelta):
                yield format_sse("delta", {"text": ev.text})
            else:  # StreamDone
                yield format_sse(
                    "done",
                    {
                        "result": QueryResultOut(
                            answer=ev.answer,
                            method=payload.method,
                            error=ev.error,
                            elapsed_ms=ev.elapsed_ms,
                            prompt_tokens=ev.prompt_tokens,
                            output_tokens=ev.output_tokens,
                            sources=[
                                SourceOut(kind=s.kind, name=s.name, text=s.text)
                                for s in ev.sources
                            ]
                            if ev.sources
                            else None,
                        ).model_dump(mode="json")
                    },
                )

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 4: Update `tests/test_query_route_enriched.py`** — the `_Stub` engine must now implement `stream_search` and the test parses SSE. Replace the file content with:

```python
"""Query route streams enriched fields (elapsed/tokens/sources) over SSE."""
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.api.sse import parse_sse
from kb_platform.db.engine import create_engine
from kb_platform.db.repository import Repository
from kb_platform.query.engine import QueryResult, SourceRef, StreamDelta, StreamDone


class _Stub:
    async def search(self, method, query, kb_data_root):
        return QueryResult(
            answer="A", method=method,
            elapsed_ms=42.0, prompt_tokens=5, output_tokens=9, llm_calls=1,
            sources=[SourceRef("entity", "宁德时代", "电池厂商")],
        )

    async def stream_search(self, method, query, kb_data_root):
        yield StreamDelta(text="A")
        yield StreamDone(
            answer="A", method=method,
            elapsed_ms=42.0, prompt_tokens=5, output_tokens=9,
            sources=[SourceRef("entity", "宁德时代", "电池厂商")],
        )


def _client():
    repo = Repository(create_engine("sqlite:///:memory:"))
    return TestClient(create_app(repo, data_root=".", query_engine=_Stub()))


def test_query_returns_sources_and_tokens():
    with _client() as c:
        r = c.post("/kbs/1/query", json={"method": "local", "query": "x"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    done = next(d for e, d in events if e == "done")["result"]
    assert done["answer"] == "A"
    assert done["elapsed_ms"] == 42.0
    assert done["prompt_tokens"] == 5
    assert done["sources"][0]["name"] == "宁德时代"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_query.py tests/test_query_route_enriched.py tests/test_query_schema_bridge.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check kb_platform/api/routes_query.py tests/test_api_query.py tests/test_query_route_enriched.py
git add kb_platform/api/routes_query.py tests/test_api_query.py tests/test_query_route_enriched.py
git commit -m "feat(api): POST /kbs/{id}/query streams SSE"
```

---

### Task 7: MCP `KbApiClient.query()` 聚合 SSE

**Files:**
- Modify: `kb_platform/mcp/server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `kb_platform.api.sse.iter_sse_events` (Task 2);SSE 事件语义(meta/delta/done/error)。
- Produces: `KbApiClient.query(kb_id, method, query) -> dict`(聚合 `delta.text`→`answer`,`done.result`→sources/元数据,保持与原 `{answer, method, error, sources, ...}` 同形状)。

- [ ] **Step 1: Write the failing test** — append to `tests/test_mcp_server.py`:

```python
async def test_client_query_aggregates_sse_stream(app):
    """POST /kbs/{id}/query now returns SSE; the client must aggregate it into a
    single result dict (same shape the tool returns)."""
    client, http = await _client_for(app)
    try:
        res = await client.query(kb_id=1, method="local", query="what is ACME?")
        assert res["method"] == "local"
        assert "ACME" in res["answer"]  # deltas were concatenated
        # graceful when the (fake) stream carries no error
        assert "error" not in res or res["error"] is None
    finally:
        await http.aclose()
```

(The existing `test_client_query_round_trips_through_api` already asserts `res["method"]` and `"ACME" in res["answer"]` and will keep passing once aggregation lands.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py::test_client_query_aggregates_sse_stream -v`
Expected: FAIL — `query()` still uses `_post_json` which calls `.json()` on an SSE body (raises / wrong shape).

- [ ] **Step 3: Write minimal implementation** — edit `kb_platform/mcp/server.py`.

Replace the `query` method on `KbApiClient`:

```python
    async def query(self, kb_id: int, method: str, query: str) -> dict:
        """POST /kbs/{id}/query → aggregate the SSE stream into a single result dict.

        The endpoint streams ``meta``/``delta``/``done``/``error`` events; we
        concatenate the ``delta`` text and lift sources/metadata off ``done`` so
        MCP tool callers still get one ``{answer, method, sources, ...}`` object.
        """
        from kb_platform.api.sse import iter_sse_events

        path = f"/kbs/{kb_id}/query"
        try:
            async with self._http.stream("POST", path, json={"method": method, "query": query}) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:200]
                    raise KbApiError(f"POST {path} -> {resp.status_code}: {body}")
                answer_parts: list[str] = []
                result: dict = {"answer": "", "method": method}
                async for event, data in iter_sse_events(resp.aiter_lines()):
                    if event == "delta":
                        answer_parts.append(data.get("text", ""))
                    elif event == "done":
                        result = data.get("result") or result
                    elif event == "error":
                        result["error"] = data.get("message", "stream error")
                result["answer"] = result.get("answer") or "".join(answer_parts)
                if not result.get("method"):
                    result["method"] = method
                return result
        except httpx.HTTPError as exc:
            raise KbApiError(f"POST {path} failed: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS (round-trip + aggregation + tool logic + unreachable).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/mcp/server.py tests/test_mcp_server.py
git add kb_platform/mcp/server.py tests/test_mcp_server.py
git commit -m "feat(mcp): aggregate SSE query stream into a single result"
```

---

### Task 8: 前端 SSE 解析 `web/src/lib/sse.ts`

**Files:**
- Create: `web/src/lib/sse.ts`
- Test: `web/src/lib/sse.test.ts`

**Interfaces:**
- Produces: `parseSse(resp: Response) -> AsyncGenerator<{event: string; data: any}>`。从 `resp.body.getReader()` 增量解析 `event:`/`data:`/空行。

- [ ] **Step 1: Write the failing test** — create `web/src/lib/sse.test.ts`:

```typescript
import { parseSse } from "./sse";

function sseResponse(body: string): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(enc.encode(body));
      controller.close();
    },
  });
  return new Response(stream, { headers: { "content-type": "text/event-stream" } });
}

test("parses meta/delta/done events", async () => {
  const body =
    'event: meta\ndata: {"method":"local"}\n\n' +
    'event: delta\ndata: {"text":"Hello "}\n\n' +
    'event: delta\ndata: {"text":"world"}\n\n' +
    'event: done\ndata: {"result":{"answer":"Hello world"}}\n\n';
  const events = [];
  for await (const ev of parseSse(sseResponse(body))) events.push(ev);
  expect(events.map((e) => e.event)).toEqual(["meta", "delta", "delta", "done"]);
  expect(events[1].data).toEqual({ text: "Hello " });
  expect(events[3].data.result.answer).toBe("Hello world");
});

test("handles chunk boundaries splitting a line", async () => {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(enc.encode('event: delta\ndata: {"text":"abc'));
      controller.enqueue(enc.encode('def"}\n\n'));
      controller.close();
    },
  });
  const events = [];
  for await (const ev of parseSse(new Response(stream))) events.push(ev);
  expect(events).toEqual([{ event: "delta", data: { text: "abcdef" } }]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- src/lib/sse.test.ts`
Expected: FAIL — `Cannot find module './sse'`.

- [ ] **Step 3: Write minimal implementation** — create `web/src/lib/sse.ts`:

```typescript
/** Parse a `text/event-stream` fetch Response into SSE events.
 *
 * `EventSource` only supports GET; our endpoints are POST with a JSON body, so
 * we read `response.body` and frame `event:` / `data:` / blank-line ourselves.
 */
export type SseEvent = { event: string; data: any };

export async function* parseSse(resp: Response): AsyncGenerator<SseEvent> {
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let dataLines: string[] = [];
  const flush = function* (): Generator<SseEvent> {
    if (event) {
      yield { event, data: dataLines.length ? JSON.parse(dataLines.join("")) : {} };
    }
    event = "";
    dataLines = [];
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // keep the last (possibly partial) line
    for (const line of lines) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      else if (line === "") yield* flush();
    }
  }
  // flush a trailing event without a terminating blank line
  buffer += decoder.decode();
  if (buffer.includes("\n")) {
    for (const line of buffer.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
  }
  yield* flush();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- src/lib/sse.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd web && git add src/lib/sse.ts src/lib/sse.test.ts && git commit -m "feat(web): SSE stream parser (parseSse)"
```

---

### Task 9: ChatPage 流式渲染 + `sendMessage` 改返回 Response

**Files:**
- Modify: `web/src/api/client.ts`
- Modify: `web/src/pages/ChatPage.tsx`
- Test: `web/src/pages/ChatPage.test.tsx`

**Interfaces:**
- Consumes: `parseSse` (Task 8);`SseEvent`。
- Produces: `sendMessage(convId, content, method) -> Promise<Response>`(原始 fetch 响应,交调用方迭代)。ChatPage 增量渲染:`meta`→可选拿 rewritten_query 提示;`delta`→追加 assistant 气泡;`done`→用持久化 `MessageOut` 替换 optimistic 气泡;`error`→气泡显示错误。

- [ ] **Step 1: Write the failing test** — edit `web/src/pages/ChatPage.test.tsx`. Replace the `http.post("/conversations/8/messages", …)` handler with an SSE response and assert incremental render:

```typescript
  http.post("/conversations/8/messages", () => {
    const body =
      'event: meta\ndata: {"method":"local","rewrite_fell_back":false}\n\n' +
      'event: delta\ndata: {"text":"A:hello"}\n\n' +
      'event: done\ndata: {"message":{"id":11,"role":"assistant","content":"A:hello","method":"local","rewrite_fell_back":false,"sources":[]}}\n\n';
    return new HttpResponse(body, { headers: { "content-type": "text/event-stream" } });
  }),
```

The existing assertion `expect(await screen.findByText("A:hello")).toBeInTheDocument();` stays valid (the delta renders "A:hello" before `done`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- src/pages/ChatPage.test.tsx`
Expected: FAIL — `sendMessage` still does `.json()` on the SSE body; ChatPage reads `r.content` from JSON (undefined), so "A:hello" never appears.

- [ ] **Step 3: Write minimal implementation**

Edit `web/src/api/client.ts` — change `sendMessage` to return the raw `Response`:

```typescript
export const sendMessage = (convId: number, content: string, method?: string) =>
  fetch(`/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, method: method ?? null }),
  });
```

Edit `web/src/pages/ChatPage.tsx` — add the import and rewrite the `send` body. Add to the imports at the top (the existing `import type { … ChatMessage … } from "../api/types";` already covers the type):

```typescript
import { parseSse } from "../lib/sse";
```

Replace the `try { … } catch { … }` block inside `send` (the `const r = await sendMessage(...)` through the catch) with:

```typescript
    try {
      const resp = await sendMessage(convId, q, method);
      if (!resp.ok) throw new Error(`${resp.status}`);
      let rewritten: string | undefined;
      for await (const ev of parseSse(resp)) {
        if (ev.event === "meta") {
          rewritten = ev.data.rewritten_query;
          if (rewritten) {
            setMessages((m) =>
              m.map((msg) =>
                msg.id === pendingId ? { ...msg, rewritten_query: rewritten } : msg,
              ),
            );
          }
        } else if (ev.event === "delta") {
          setMessages((m) =>
            m.map((msg) =>
              msg.id === pendingId ? { ...msg, content: msg.content + ev.data.text } : msg,
            ),
          );
        } else if (ev.event === "done") {
          const persisted: ChatMessage = ev.data.message;
          setMessages((m) =>
            m.map((msg) => (msg.id === pendingId ? { ...persisted, elapsed_ms: persisted.elapsed_ms ?? fallbackElapsed } : msg)),
          );
        } else if (ev.event === "error") {
          throw new Error(ev.data.message ?? "stream error");
        }
      }
      void reloadList();
    } catch (e) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId ? { ...msg, error: (e as Error).message ?? String(e) } : msg,
        ),
      );
    }
```

(`const fallbackElapsed = performance.now() - t0;` stays where it is, just above this block.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- src/pages/ChatPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Build + commit**

```bash
cd web && npm run build
cd .. && git add web/src/api/client.ts web/src/pages/ChatPage.tsx web/src/pages/ChatPage.test.tsx
git commit -m "feat(web): ChatPage streams the answer over SSE"
```

---

### Task 10: QueryTestPage 流式渲染 + `query` 改返回 Response

**Files:**
- Modify: `web/src/api/client.ts`
- Modify: `web/src/pages/QueryTestPage.tsx`

**Interfaces:**
- Consumes: `parseSse` (Task 8);`QueryResult` 类型。
- Produces: `query(kbId, method, q) -> Promise<Response>`。QueryTestPage 增量渲染:`delta`→追加答案;`done`→用完整 `QueryResultOut` + sources/耗时定稿;`error`→设错误。

- [ ] **Step 1: Write the failing test** — there is no existing QueryTestPage test; create `web/src/pages/QueryTestPage.test.tsx`:

```typescript
import { render, screen, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import QueryTestPage from "./QueryTestPage";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.post("/kbs/1/query", () => {
    const body =
      'event: meta\ndata: {"method":"local"}\n\n' +
      'event: delta\ndata: {"text":"Hel"}\n\n' +
      'event: delta\ndata: {"text":"lo"}\n\n' +
      'event: done\ndata: {"result":{"answer":"Hello","method":"local","error":null,"sources":[]}}\n\n';
    return new HttpResponse(body, { headers: { "content-type": "text/event-stream" } });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("streams the answer incrementally", async () => {
  render(
    <MemoryRouter>
      <QueryTestPage />
    </MemoryRouter>,
  );
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "hi" } });
  fireEvent.click(screen.getByRole("button", { name: /检索|提问|查询/ }));
  expect(await screen.findByText("Hello")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fail**

Run: `cd web && npm test -- src/pages/QueryTestPage.test.tsx`
Expected: FAIL — `apiQuery` does `.json()`; "Hello" never renders.

- [ ] **Step 3: Write minimal implementation**

Edit `web/src/api/client.ts` — change `query` to return the raw `Response`:

```typescript
export const query = (kbId: number, method: string, q: string) =>
  fetch(`/kbs/${kbId}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method, query: q }),
  });
```

Edit `web/src/pages/QueryTestPage.tsx` — add import and rewrite `ask`. Add import (the existing `import type { QueryResult } from "../api/types";` already covers the type):

```typescript
import { parseSse } from "../lib/sse";
```

Replace the `try { … } catch { … }` block inside `ask` (the `const r = await apiQuery(...)` through the catch) with:

```typescript
    try {
      const resp = await apiQuery(kbId, method, q);
      if (!resp.ok) throw new Error(`${resp.status}`);
      let partial = "";
      let methodUsed = method;
      for await (const ev of parseSse(resp)) {
        if (ev.event === "meta") {
          methodUsed = ev.data.method ?? method;
        } else if (ev.event === "delta") {
          partial += ev.data.text ?? "";
          setResult({ data: { answer: partial, method: methodUsed, error: null }, elapsedMs: performance.now() - t0, method: methodUsed });
        } else if (ev.event === "done") {
          const data = ev.data.result as QueryResult;
          setResult({ data, elapsedMs: data.elapsed_ms ?? performance.now() - t0, method: methodUsed });
          if (data.error) setError(data.error);
        } else if (ev.event === "error") {
          throw new Error(ev.data.message ?? "stream error");
        }
      }
    } catch (e) {
      setError((e as Error).message ?? String(e));
      setResult(null);
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- src/pages/QueryTestPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Build + commit**

```bash
cd web && npm run build
cd .. && git add web/src/api/client.ts web/src/pages/QueryTestPage.tsx web/src/pages/QueryTestPage.test.tsx
git commit -m "feat(web): QueryTestPage streams the answer over SSE"
```

---

### Task 11: 全量校验 + 验证文档 + CLAUDE.md

**Files:**
- Create: `docs/verify-streaming-2026-06-29.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run the entire backend test + lint suite**

Run: `uv run ruff check . && uv run pytest`
Expected: PASS, 0 failures. (All earlier per-task suites plus any test that touches `/query` or `/messages` now pass under SSE.)

- [ ] **Step 2: Run the entire frontend test + build suite**

Run: `cd web && npm test && npm run build`
Expected: PASS, build succeeds.

- [ ] **Step 3: Write the manual verify doc** — create `docs/verify-streaming-2026-06-29.md`:

```markdown
# 流式回答(SSE)验证记录 — 2026-06-29

> 自动化测试已覆盖协议/聚合/渲染。本文档为真实 LLM + 浏览器的手动冒烟清单(需要:运行中的 API server + worker、一个已索引的 KB、带有效 key 的 provider profile)。

## 前置
- [ ] `uv run python -m kb_platform.server` 与 `uv run python -m kb_platform.worker` 正常启动。
- [ ] 至少一个 KB 已完成全量索引(含 community reports,以验证 global/drift)。
- [ ] 该 KB 绑定有效 LLM provider profile(key 可用)。

## Chat 页流式
- [ ] 进入 Chat 页,选 KB → 新建对话 → 提问:答案**逐字**出现(不是整段一次性出现)。
- [ ] 追问(如"再说详细点"):顶部出现"理解为 {rewritten_query}"提示(meta 事件),答案随后逐字流出。
- [ ] 切到 `global` 方式提问:前若干秒在准备(map 阶段),随后答案逐字流出;`done` 后 sources 正常展示。
- [ ] 刷新页面:对话与消息恢复正常(A1 持久化不受影响)。
- [ ] 断网/停止:已流出文本保留,不假装完成。

## 检索测试页流式
- [ ] QueryTestPage 提问:答案逐字出现,`done` 后耗时/sources 正常。

## MCP 聚合
- [ ] `uv run python -m kb_platform.mcp` 启动;agent 调 `query_knowledge_base` 返回**单个**完整答案(非增量),sources 正常 —— 即 MCP 内部聚合了 SSE。

## 降级
- [ ] 对一个无 community reports 的 KB 用 `global` 提问:返回 `error` 事件("no community reports…"),前端气泡/检索页显示错误,无 500。
```

- [ ] **Step 4: Note SSE in CLAUDE.md** — in `CLAUDE.md`, under the "### Frontend SPA hosting" or "### Two graphrag isolation seams" section, append one bullet so future sessions know the query endpoints stream:

```markdown
- **查询端点是 SSE 流式**:`POST /kbs/{id}/query` 与 `POST /conversations/{id}/messages` 返回 `text/event-stream`(事件 `meta`/`delta`/`done`/`error`),由 `QueryEngine.stream_search` 驱动;MCP 代理在 `KbApiClient.query()` 内部聚合 SSE 成单结果(工具契约不变)。单发 JSON 不再存在 —— 测试与客户端都按 SSE 解析。
```

- [ ] **Step 5: Commit**

```bash
git add docs/verify-streaming-2026-06-29.md CLAUDE.md
git commit -m "docs(verify): A2 streaming answers manual smoke + CLAUDE.md note"
```

- [ ] **Step 6: Update memory** — update `qa-experience-roadmap.md`: mark A2 (streaming) DONE, A3 (query-tuning UI) NEXT. (One-line edit to the roadmap memory + MEMORY.md stays as-is.)
