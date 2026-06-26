# 前后端 API 真实逻辑匹配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `POST /kbs/{id}/query` 端到端返回真实答案，并把后端真实能力（服务端耗时、token 用量、来源实体/文本片段）透出给前端检索/对话页；同时让 KB 配置（模型 provider/model）在 KB 概要可见。

**Architecture:** 后端三处缝：`server.py` 强制 asyncio loop（修 `graphrag_llm` 的 nest_asyncio 无法 patch uvloop 的崩溃）；`query/engine.py` 的 `GraphRagQueryEngine` 从 graphrag `SearchResult` 读取 `completion_time`/tokens/`context_data` 并提取来源；`api/{models,routes_query,routes_kbs}.py` 扩展响应模型与映射。前端：`types.ts` 加可选字段，新增共享 `QueryResultView` 组件，检索/对话/KB 检索 tab 复用，KB 概要加模型配置卡。

**Tech Stack:** Python 3.11 + uv + FastAPI + graphrag 3.1 + pytest + ruff；React 18 + TypeScript + Vite + Tailwind + vitest。

## Global Constraints

- 后端用 `uv run pytest` / `uv run ruff check .`；kb-platform **无 pyright/poe/semversioner**（ruff only）。
- 前端在 `web/` 下：`npm run build`（`tsc -b && vite build`）、`npm test`（vitest）。
- 中文 UI 文案；查询耗时不展示原始秒数浮点，展示整数 ms。
- 密钥绝不入库；`settings` 透出前必须脱敏（key/token/secret → `***`）。
- 不改 graphrag/uvicorn 源码，只配置 `loop="asyncio"`。
- 每个 Python 文件沿用其现有头部约定（有 copyright 头的保留，没有的不加）。
- `SearchResult.completion_time` 单位为秒，转 ms 乘 1000。

---

## File Structure

**后端（改动）：**
- `kb_platform/server.py` — uvicorn loop 配置。
- `kb_platform/query/engine.py` — `QueryResult`/`SourceRef` 扩展 + `_extract_sources` + 结果映射。
- `kb_platform/api/models.py` — `SourceOut`、`QueryResultOut` 扩展、`KbDetailOut`。
- `kb_platform/api/routes_query.py` — 映射 `QueryResult`→`QueryResultOut`（含新字段）。
- `kb_platform/api/routes_kbs.py` — `_redact` + `GET /kbs/{id}` 返回 `KbDetailOut`。

**前端（改动/新增）：**
- `web/src/api/types.ts` — `QueryResult`/`SourceRef`/`KbOut` 扩展。
- `web/src/components/QueryResultView.tsx`（**新建**）— 共享：来源 + token + 耗时 + 错误渲染。
- `web/src/pages/QueryTestPage.tsx`、`web/src/pages/QueryPage.tsx`、`web/src/pages/ChatPage.tsx` — 复用 `QueryResultView` + 服务端字段。
- `web/src/pages/KbOverviewPage.tsx` — 模型配置卡。
- `web/src/pages/new-pages.test.tsx`（扩展）/ 新增组件测试。

**测试（新增）：**
- `tests/test_server_loop.py`、`tests/test_query_sources.py`、`tests/test_redact.py`、`tests/test_query_route_enriched.py`、`web/src/components/QueryResultView.test.tsx`。

---

## Task 1: 修复查询崩溃 — server 强制 asyncio loop

**Files:**
- Modify: `kb_platform/server.py`（`main()` 内 `uvicorn.run` 一行）
- Test: `tests/test_server_loop.py`

**Interfaces:**
- Produces: `server.main()` 调用 `uvicorn.run(app, host=host, port=port, loop="asyncio")`。

- [ ] **Step 1: 写失败测试**

`tests/test_server_loop.py`:
```python
"""Regression: server must run uvicorn on the asyncio loop, not uvloop.

graphrag_llm calls nest_asyncio.apply() at import, which cannot patch a
uvloop. uvicorn auto-selects uvloop when installed, so we force loop="asyncio".
"""
import sys


def test_server_forces_asyncio_loop(monkeypatch):
    import uvicorn

    captured: dict = {}

    def fake_run(app, **kwargs):  # noqa: ANN001
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["server", ":memory:", "/tmp", "127.0.0.1", "8000"])

    from kb_platform import server

    server.main()
    assert captured.get("loop") == "asyncio"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_server_loop.py -v`
Expected: FAIL — `assert None == "asyncio"`（当前未传 loop）。

- [ ] **Step 3: 最小实现**

`kb_platform/server.py`，把 `main()` 里的：
```python
    uvicorn.run(app, host=host, port=port)
```
改为：
```python
    # Force the native asyncio loop: graphrag_llm runs nest_asyncio.apply()
    # at import, which cannot patch uvloop (uvicorn auto-selects uvloop when
    # installed -> "Can't patch loop of type <class 'uvloop.Loop'>").
    uvicorn.run(app, host=host, port=port, loop="asyncio")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_server_loop.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/server.py tests/test_server_loop.py
git commit -m "fix(server): force asyncio loop so graphrag query stops crashing on uvloop"
```

---

## Task 2: 扩展 QueryResult + 新增 SourceRef 数据类

**Files:**
- Modify: `kb_platform/query/engine.py`（`QueryResult` + 新 `SourceRef`）
- Test: `tests/test_query_sources.py`

**Interfaces:**
- Produces: `SourceRef(kind: str, name: str, text: str)`；`QueryResult(..., elapsed_ms=None, prompt_tokens=None, output_tokens=None, llm_calls=None, sources=None)`。

- [ ] **Step 1: 写失败测试**

`tests/test_query_sources.py`（本文件后续 Task 3/4 续用）：
```python
"""QueryResult enrichment: SourceRef + optional elapsed/tokens/sources."""
from kb_platform.query.engine import QueryResult, SourceRef


def test_query_result_defaults():
    r = QueryResult(answer="a", method="local")
    assert r.error is None
    assert r.elapsed_ms is None
    assert r.prompt_tokens is None
    assert r.output_tokens is None
    assert r.llm_calls is None
    assert r.sources is None


def test_source_ref():
    s = SourceRef(kind="entity", name="宁德时代", text="电池厂商")
    assert s.kind == "entity"
    assert s.name == "宁德时代"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_sources.py -v`
Expected: FAIL — `cannot import name 'SourceRef'`。

- [ ] **Step 3: 实现**

`kb_platform/query/engine.py`，把：
```python
from dataclasses import dataclass
from typing import Protocol


@dataclass
class QueryResult:
    answer: str
    method: str
    error: str | None = None
```
改为：
```python
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SourceRef:
    """A single cited source from a graphrag search context.

    kind is one of: entity | text_unit | relationship.
    """

    kind: str
    name: str
    text: str


@dataclass
class QueryResult:
    answer: str
    method: str
    error: str | None = None
    # Real server-side latency (SearchResult.completion_time * 1000), ms.
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    llm_calls: int | None = None
    sources: list[SourceRef] | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_query_sources.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/query/engine.py tests/test_query_sources.py
git commit -m "feat(query): add SourceRef + extend QueryResult with elapsed/tokens/sources"
```

---

## Task 3: `_extract_sources` — 从 context_data 提取真实来源

**Files:**
- Modify: `kb_platform/query/graphrag_engine.py`（新增 `_extract_sources` + 模块级 helper）
- Test: `tests/test_query_sources.py`（追加）

**Interfaces:**
- Consumes: graphrag `SearchResult.context_data`（dict[str, DataFrame] / list / str）。
- Produces: `GraphRagQueryEngine._extract_sources(context_data, method, limit=4) -> list[SourceRef] | None`。

**事实（已核实 graphrag 源）：** local/drift 的 `context_data` 是 `dict[str, DataFrame]`，含 `"entities"` 键（实体 df，name 在 `name` 列）；basic 的 `context_data` 是单键 dict，其 df 含 `text` 列（文本单元）。任何形态失败 → 返回 None，绝不抛。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_query_sources.py`：
```python
import pandas as pd

from kb_platform.query.graphrag_engine import GraphRagQueryEngine


def _engine():
    return GraphRagQueryEngine(data_root=".", model_config={})


def test_extract_sources_entities_and_text():
    ctx = {
        "entities": pd.DataFrame(
            [{"name": "宁德时代", "description": "电池厂商"}, {"name": "特斯拉", "description": "车厂"}]
        ),
        "text units": pd.DataFrame([{"id": 1, "text": "供货协议片段"}]),
    }
    out = _engine()._extract_sources(ctx, "local")
    kinds = {s.kind for s in out}
    names = {s.name for s in out if s.kind == "entity"}
    assert "entity" in kinds and "宁德时代" in names
    assert any(s.kind == "text_unit" and "供货协议" in s.text for s in out)


def test_extract_sources_basic_single_key():
    ctx = {"text units": pd.DataFrame([{"id": 7, "text": "一段正文"}])}
    out = _engine()._extract_sources(ctx, "basic")
    assert out and out[0].kind == "text_unit" and "一段正文" in out[0].text


def test_extract_sources_degrades_on_none():
    assert _engine()._extract_sources(None, "local") is None
    assert _engine()._extract_sources("   ", "local") is None


def test_extract_sources_caps_text_snippet():
    long = "x" * 500
    ctx = {"sources": pd.DataFrame([{"id": 1, "text": long}])}
    out = _engine()._extract_sources(ctx, "basic")
    assert len(out[0].text) <= 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_sources.py -v`
Expected: FAIL — `_extract_sources` 不存在（AttributeError）。

- [ ] **Step 3: 实现**

在 `kb_platform/query/graphrag_engine.py` 模块级（`_norm_*` 之后、`class GraphRagQueryEngine` 之前）加：
```python
def _is_df(value) -> bool:
    try:
        import pandas as pd  # noqa: PLC0415

        return isinstance(value, pd.DataFrame)
    except Exception:  # noqa: BLE001
        return False


def _first(row, cols) -> str:
    for c in cols:
        if c in row.index and row[c] not in (None, ""):
            return str(row[c])
    return ""
```

在 `class GraphRagQueryEngine` 内（`_build_embedding_store` 之后）加：
```python
    def _extract_sources(self, context_data, method: str, limit: int = 4):
        """Best-effort extraction of source entities + text snippets from a
        graphrag SearchResult.context_data.

        - dict[str, DataFrame]: "entities" -> entity name+description; the
          first text-bearing frame -> text_unit snippets.
        - str: wrapped as a single text_unit source.
        Anything else / any failure -> None (never raises; never blocks the
        answer).
        """
        try:
            sources: list[SourceRef] = []
            if isinstance(context_data, dict):
                ents = context_data.get("entities")
                if _is_df(ents):
                    for _, row in ents.head(limit).iterrows():
                        name = _first(row, ("name", "title", "id"))
                        if not name:
                            continue
                        desc = str(row.get("description", "") or "")[:200]
                        sources.append(SourceRef("entity", name, desc))
                for _key, df in context_data.items():
                    if not _is_df(df) or "text" not in df.columns:
                        continue
                    for _, row in df.head(limit).iterrows():
                        txt = str(row.get("text", "") or "")
                        if not txt.strip():
                            continue
                        sources.append(SourceRef("text_unit", str(row.get("id", _key)), txt[:200]))
                    break
            elif isinstance(context_data, str) and context_data.strip():
                sources.append(SourceRef("text_unit", "context", context_data.strip()[:200]))
            return sources or None
        except Exception:  # noqa: BLE001 - sources are a nice-to-have
            logger.exception("source extraction failed")
            return None
```

（`SourceRef` 已在 Task 2 由 `from kb_platform.query.engine import QueryResult` 同处导出；在 graphrag_engine.py 顶部把 import 改为 `from kb_platform.query.engine import QueryResult, SourceRef`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_query_sources.py -v`
Expected: PASS（全部 5 个）。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/query/graphrag_engine.py tests/test_query_sources.py
git commit -m "feat(query): extract real source entities + text snippets from context_data"
```

---

## Task 4: 在真实搜索路径填充 elapsed/tokens/sources

**Files:**
- Modify: `kb_platform/query/graphrag_engine.py`（`_run_graphrag_search` 末尾 + 新 `_result_from_search`）
- Test: `tests/test_query_sources.py`（追加）

**Interfaces:**
- Produces: `GraphRagQueryEngine._result_from_search(method, search_result) -> QueryResult`（读 `completion_time`/`prompt_tokens`/`output_tokens`/`llm_calls`/`context_data`）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_query_sources.py`：
```python
from types import SimpleNamespace


def test_result_from_search_maps_fields():
    sr = SimpleNamespace(
        response="答案",
        context_data={"entities": pd.DataFrame([{"name": "E1", "description": "d"}])},
        completion_time=0.123,
        prompt_tokens=10,
        output_tokens=20,
        llm_calls=1,
    )
    r = _engine()._result_from_search("local", sr)
    assert r.answer == "答案" and r.method == "local"
    assert r.elapsed_ms == 123.0
    assert r.prompt_tokens == 10 and r.output_tokens == 20 and r.llm_calls == 1
    assert r.sources and r.sources[0].name == "E1"


def test_result_from_search_handles_list_response():
    sr = SimpleNamespace(
        response=[{"x": 1}], context_data=None, completion_time=0.0,
        prompt_tokens=0, output_tokens=0, llm_calls=0,
    )
    r = _engine()._result_from_search("basic", sr)
    assert r.answer == "[{'x': 1}]" and r.sources is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_sources.py::test_result_from_search_maps_fields -v`
Expected: FAIL — `_result_from_search` 不存在。

- [ ] **Step 3: 实现**

在 `class GraphRagQueryEngine` 内加：
```python
    def _result_from_search(self, method: str, search_result) -> QueryResult:
        """Map a graphrag SearchResult into an enriched QueryResult."""
        answer = getattr(search_result, "response", "") or ""
        if isinstance(answer, (list, dict)):
            answer = str(answer)
        return QueryResult(
            answer=answer,
            method=method,
            elapsed_ms=round(float(getattr(search_result, "completion_time", 0.0) or 0.0) * 1000, 1),
            prompt_tokens=int(getattr(search_result, "prompt_tokens", 0) or 0) or None,
            output_tokens=int(getattr(search_result, "output_tokens", 0) or 0) or None,
            llm_calls=int(getattr(search_result, "llm_calls", 0) or 0) or None,
            sources=self._extract_sources(getattr(search_result, "context_data", None), method),
        )
```

把 `_run_graphrag_search` 末尾的：
```python
        result = await engine.search(query=query)
        answer = getattr(result, "response", "") or ""
        if isinstance(answer, (list, dict)):
            answer = str(answer)
        return QueryResult(answer=answer, method=method)
```
改为：
```python
        result = await engine.search(query=query)
        return self._result_from_search(method, result)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_query_sources.py -v`
Expected: PASS（全部）。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/query/graphrag_engine.py tests/test_query_sources.py
git commit -m "feat(query): populate elapsed_ms/tokens/sources from graphrag SearchResult"
```

---

## Task 5: API 响应模型扩展 + routes_query 映射

**Files:**
- Modify: `kb_platform/api/models.py`（`SourceOut` + `QueryResultOut` 扩展）
- Modify: `kb_platform/api/routes_query.py`（映射）
- Test: `tests/test_query_route_enriched.py`

**Interfaces:**
- Produces: `SourceOut`、`QueryResultOut(answer, method, error?, elapsed_ms?, prompt_tokens?, output_tokens?, llm_calls?, sources?: list[SourceOut])`。

- [ ] **Step 1: 写失败测试**

`tests/test_query_route_enriched.py`：
```python
"""Query route returns enriched fields (elapsed/tokens/sources)."""
from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.repository import Repository
from kb_platform.query.engine import QueryResult, SourceRef
from fastapi.testclient import TestClient


class _Stub:
    async def search(self, method, query, kb_data_root):
        return QueryResult(
            answer="A", method=method,
            elapsed_ms=42.0, prompt_tokens=5, output_tokens=9, llm_calls=1,
            sources=[SourceRef("entity", "宁德时代", "电池厂商")],
        )


def _client():
    repo = Repository(create_engine("sqlite:///:memory:"))
    return TestClient(create_app(repo, data_root=".", query_engine=_Stub()))


def test_query_returns_sources_and_tokens():
    with _client() as c:
        r = c.post("/kbs/1/query", json={"method": "local", "query": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "A"
    assert body["elapsed_ms"] == 42.0
    assert body["prompt_tokens"] == 5 and body["llm_calls"] == 1
    assert body["sources"][0]["name"] == "宁德时代"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_route_enriched.py -v`
Expected: FAIL — 响应无 `elapsed_ms`/`sources` 字段（key 不存在）。

- [ ] **Step 3: 实现 models**

`kb_platform/api/models.py`，在 Query 段：
```python
class QueryRequest(BaseModel):
    method: str
    query: str


class QueryResultOut(BaseModel):
    answer: str
    method: str
    error: str | None = None
```
改为：
```python
class QueryRequest(BaseModel):
    method: str
    query: str


class SourceOut(BaseModel):
    kind: str
    name: str
    text: str


class QueryResultOut(BaseModel):
    answer: str
    method: str
    error: str | None = None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    llm_calls: int | None = None
    sources: list[SourceOut] | None = None
```

- [ ] **Step 4: 实现 route 映射**

`kb_platform/api/routes_query.py`，把：
```python
    result = await engine.search(payload.method, payload.query, request.app.state.data_root)
    return QueryResultOut(answer=result.answer, method=result.method, error=result.error)
```
改为：
```python
    from kb_platform.api.models import SourceOut

    result = await engine.search(payload.method, payload.query, request.app.state.data_root)
    return QueryResultOut(
        answer=result.answer,
        method=result.method,
        error=result.error,
        elapsed_ms=result.elapsed_ms,
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        llm_calls=result.llm_calls,
        sources=[SourceOut(kind=s.kind, name=s.name, text=s.text) for s in result.sources]
        if result.sources
        else None,
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_query_route_enriched.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_query.py tests/test_query_route_enriched.py
git commit -m "feat(api): expose elapsed_ms/tokens/sources on QueryResultOut"
```

---

## Task 6: KB 配置可见 — `_redact` + `KbDetailOut` + GET /kbs/{id}

**Files:**
- Modify: `kb_platform/api/models.py`（`KbDetailOut`）
- Modify: `kb_platform/api/routes_kbs.py`（`_redact` + `get_kb`）
- Test: `tests/test_redact.py`

**Interfaces:**
- Produces: `KbDetailOut(id, name, method, settings: dict)`；`_redact(settings_json_str) -> dict`。

- [ ] **Step 1: 写失败测试**

`tests/test_redact.py`：
```python
"""Settings redaction: key/token/secret values are masked before exposure."""
import json

from kb_platform.api.routes_kbs import _redact


def test_redact_masks_keys():
    s = json.dumps(
        {"llm": {"model": "deepseek-chat", "api_key": "sk-secret"}, "token": "abc"}
    )
    out = _redact(s)
    assert out["llm"]["model"] == "deepseek-chat"
    assert out["llm"]["api_key"] == "***"
    assert out["token"] == "***"


def test_redact_invalid_json_returns_empty():
    assert _redact(None) == {}
    assert _redact("not json") == {}
```

并追加一个路由测试（同文件）：
```python
from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.repository import Repository
from fastapi.testclient import TestClient


def test_get_kb_returns_redacted_settings():
    repo = Repository(create_engine("sqlite:///:memory:"))
    with repo.engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO knowledge_base(name, method, settings_json, data_root) "
            "VALUES('k','standard', ?,'.')",
            (json.dumps({"llm": {"api_key": "sk-x", "model": "m"}}),),
        )
    with TestClient(create_app(repo, data_root=".")) as c:
        body = c.get("/kbs/1").json()
    assert body["name"] == "k"
    assert body["settings"]["llm"]["model"] == "m"
    assert body["settings"]["llm"]["api_key"] == "***"
```

> 注：表名/列名以现有 `kb_platform/db/models.py` 的 `KnowledgeBase` 为准；若 ORM 列名不同，改用 `repo.add` 风格插入。先 `uv run python -c "from kb_platform.db.models import KnowledgeBase; print(KnowledgeBase.__tablename__, [c.name for c in KnowledgeBase.__table__.columns])"` 确认。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_redact.py -v`
Expected: FAIL — `cannot import name '_redact'`。

- [ ] **Step 3: 实现 models**

`kb_platform/api/models.py`，在 `KbOut` 之后加：
```python
class KbDetailOut(KbOut):
    """GET /kbs/{id}: adds the (redacted) parsed settings."""

    settings: dict
```

- [ ] **Step 4: 实现 _redact + route**

`kb_platform/api/routes_kbs.py`，顶部 `import json` 已有；在 `_parse_settings` 之后加：
```python
_SENSITIVE = ("key", "token", "secret", "password")


def _redact(settings_json: str | None) -> dict:
    """Parse stored settings JSON and mask any sensitive values.

    Keys are never stored in the DB (resolved from env at runtime), but this
    is defense-in-depth for anything a user may have pasted into settings_yaml.
    """
    try:
        data = json.loads(settings_json or "{}")
    except (TypeError, ValueError):
        return {}

    def _walk(node):
        if isinstance(node, dict):
            return {
                k: ("***" if any(s in k.lower() for s in _SENSITIVE) else _walk(v))
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(data)
```

把 `get_kb` 改为：
```python
@router.get("/kbs/{kb_id}", response_model=KbDetailOut)
def get_kb(kb_id: int, request: Request) -> KbDetailOut:
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        return KbDetailOut(
            id=kb.id, name=kb.name, method=kb.method, settings=_redact(kb.settings_json)
        )
```

并把该文件顶部 import 行：
```python
from kb_platform.api.models import DocumentCreate, DocumentOut, JobListItem, KbCreate, KbOut
```
改为：
```python
from kb_platform.api.models import DocumentCreate, DocumentOut, JobListItem, KbCreate, KbDetailOut, KbOut
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_redact.py -v`
Expected: PASS。

- [ ] **Step 6: 跑全量后端测试确认无回归**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全绿、ruff 干净。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_kbs.py tests/test_redact.py
git commit -m "feat(api): expose redacted KB settings on GET /kbs/{id}"
```

---

## Task 7: 前端 types.ts 扩展

**Files:**
- Modify: `web/src/api/types.ts`

**Interfaces:**
- Produces: `SourceRef`、`QueryResult` 新可选字段、`KbOut.settings?`。

- [ ] **Step 1: 改 types.ts**

把：
```ts
export interface KbOut { id: number; name: string; method: string }
```
改为：
```ts
export interface KbOut { id: number; name: string; method: string; settings?: Record<string, unknown> }
```

把：
```ts
export interface QueryResult { answer: string; method: string; error: string | null }
```
改为：
```ts
export interface SourceRef { kind: string; name: string; text: string }

export interface QueryResult {
  answer: string;
  method: string;
  error: string | null;
  elapsedMs?: number;
  promptTokens?: number;
  outputTokens?: number;
  llmCalls?: number;
  sources?: SourceRef[];
}
```

- [ ] **Step 2: 跑 build 确认类型不破**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web && npm run build`
Expected: 成功（无 TS 错误）。

- [ ] **Step 3: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/api/types.ts
git commit -m "feat(web): add query sources/tokens/elapsed + KB settings to API types"
```

---

## Task 8: 共享组件 QueryResultView（来源 + token + 耗时 + 错误）

**Files:**
- Create: `web/src/components/QueryResultView.tsx`
- Test: `web/src/components/QueryResultView.test.tsx`

**Interfaces:**
- Produces: `QueryResultView({ result, clientElapsedMs? }: { result: QueryResult; clientElapsedMs?: number })` — 渲染错误条、meta（方法/耗时/token）、来源列表（实体 chips + 文本片段）。不渲染答案正文（由调用方控制）。

- [ ] **Step 1: 写失败测试**

`web/src/components/QueryResultView.test.tsx`：
```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryResultView } from "./QueryResultView";
import type { QueryResult } from "../api/types";

const r: QueryResult = {
  answer: "A",
  method: "local",
  error: null,
  elapsedMs: 42,
  promptTokens: 5,
  outputTokens: 9,
  llmCalls: 1,
  sources: [
    { kind: "entity", name: "宁德时代", text: "电池厂商" },
    { kind: "text_unit", name: "1", text: "一段来源片段" },
  ],
};

test("renders sources, tokens and server elapsed", () => {
  render(<MemoryRouter><QueryResultView result={r} /></MemoryRouter>);
  expect(screen.getByText("宁德时代")).toBeInTheDocument();
  expect(screen.getByText(/一段来源片段/)).toBeInTheDocument();
  expect(screen.getByText(/42/)).toBeInTheDocument(); // elapsed
  expect(screen.getByText(/5.*9/)).toBeInTheDocument(); // tokens
});

test("hides sources section when none", () => {
  render(<MemoryRouter><QueryResultView result={{ ...r, sources: undefined }} /></MemoryRouter>);
  expect(screen.queryByText("引用与来源")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- QueryResultView`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 实现组件**

`web/src/components/QueryResultView.tsx`：
```tsx
import type { QueryResult, SourceRef } from "../api/types";
import { Badge } from "./ui";
import { IconClock, IconWarn } from "./icons";

/** Render query metadata (method/elapsed/tokens), real sources, and errors.
 * Does NOT render the answer body — callers present that in their own layout. */
export function QueryResultView({
  result,
  clientElapsedMs,
}: {
  result: QueryResult;
  clientElapsedMs?: number;
}) {
  const elapsed = result.elapsedMs ?? clientElapsedMs;
  const entities = result.sources?.filter((s) => s.kind === "entity") ?? [];
  const texts = result.sources?.filter((s) => s.kind !== "entity") ?? [];
  const hasTokens = result.promptTokens || result.outputTokens || result.llmCalls;

  return (
    <div className="space-y-3">
      {result.error && (
        <div className="flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          <IconWarn width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{result.error}</span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
        <Badge tone="brand">{result.method}</Badge>
        {elapsed != null && (
          <span className="flex items-center gap-1 nums">
            <IconClock width={13} height={13} /> {Math.round(elapsed)} ms
          </span>
        )}
        {hasTokens ? (
          <span className="nums">
            {result.promptTokens ?? 0} prompt · {result.outputTokens ?? 0} output
            {result.llmCalls ? ` · ${result.llmCalls} 次调用` : ""}
          </span>
        ) : null}
      </div>

      {result.sources && result.sources.length > 0 && (
        <div>
          <p className="mb-1.5 text-[12px] font-medium text-body">引用与来源</p>
          {entities.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {entities.map((e) => (
                <SourceChip key={`e-${e.name}`} s={e} />
              ))}
            </div>
          )}
          {texts.length > 0 && (
            <ul className="space-y-1.5">
              {texts.map((t, i) => (
                <li
                  key={`t-${i}`}
                  className="rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[12px] leading-relaxed text-body"
                >
                  {t.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function SourceChip({ s }: { s: SourceRef }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-1 text-[12px]">
      <span className="font-medium text-ink">{s.name}</span>
      {s.text && <span className="text-muted">· {s.text.slice(0, 40)}</span>}
    </span>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npm test -- QueryResultView`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/components/QueryResultView.tsx web/src/components/QueryResultView.test.tsx
git commit -m "feat(web): QueryResultView shows real sources/tokens/elapsed"
```

---

## Task 9: 检索测试 / 问答对话 / KB 检索 tab 接入真实来源

**Files:**
- Modify: `web/src/pages/QueryTestPage.tsx`、`web/src/pages/QueryPage.tsx`、`web/src/pages/ChatPage.tsx`

**Interfaces:**
- Consumes: `QueryResultView`（Task 8）、`QueryResult` 新字段（Task 7）。

- [ ] **Step 1: QueryTestPage — 用 QueryResultView 替换内联回答 + 去掉「无引用」占位**

`web/src/pages/QueryTestPage.tsx`：把 import 行加上 `QueryResultView`：
```ts
import { Card, CardHeader, Button, Spinner, EmptyState } from "../components/ui";
import { QueryResultView } from "../components/QueryResultView";
```
把 `{(result || error) && ( ... )}` 整块「回答」`<Card>` 的内容体替换为：
```tsx
          <div className="mt-4 space-y-3">
            <div className="whitespace-pre-wrap rounded-xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink">
              {result?.data.answer}
            </div>
            <QueryResultView result={result?.data ?? { answer: "", method: method, error: error ?? null }} clientElapsedMs={result?.elapsedMs} />
          </div>
```
并删掉旧的 error 块与「引用片段：…不展示」整段（`QueryResultView` 已接管错误与来源）。注意：保留外层 `CardHeader`（标题「回答」）。

> 即：把 `<div className="mt-4 space-y-3"> {error ? (...) : (<div ...answer>) } <div ...引用片段说明> </div></div>` 简化为上面的「答案 + `<QueryResultView>`」。

- [ ] **Step 2: QueryPage（KB 检索问答 tab）同样替换**

`web/src/pages/QueryPage.tsx`：顶部加：
```ts
import { QueryResultView } from "../components/QueryResultView";
```
把 `{(result || error) && ( <Card>... )}` 块的正文（error 块 + answer 块）替换为：
```tsx
          <div className="mt-4 space-y-3">
            <div className="whitespace-pre-wrap rounded-xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink">
              {result?.answer}
            </div>
            <QueryResultView result={result ?? { answer: "", method: method, error: error ?? null }} />
          </div>
```

- [ ] **Step 3: ChatPage — 每条助手气泡下挂来源/token**

`web/src/pages/ChatPage.tsx`：顶部加：
```ts
import { QueryResultView } from "../components/QueryResultView";
```
在 `ChatBubble` 的助手分支里，answer 文本块之后、闭合前插入：
```tsx
          {m.error ? null : (
            <QueryResultView
              result={{
                answer: m.text,
                method: m.method ?? "local",
                error: m.error ?? null,
                elapsedMs: m.elapsedMs,
              }}
            />
          )}
```
并删掉底部「查询接口仅返回综合答案…」的固定提示行（现在来源是真实的；若无来源 `QueryResultView` 自然不渲染该区）。

- [ ] **Step 4: 跑 build + 全量前端测试**

Run: `cd web && npm run build && npm test`
Expected: build 成功、测试全绿。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/pages/QueryTestPage.tsx web/src/pages/QueryPage.tsx web/src/pages/ChatPage.tsx
git commit -m "feat(web): query/chat surfaces show real sources, tokens, server elapsed"
```

---

## Task 10: KB 概要显示模型配置

**Files:**
- Modify: `web/src/pages/KbOverviewPage.tsx`

**Interfaces:**
- Consumes: `useKb()`（已有）→ `kb.settings`（Task 6 + 7）。

- [ ] **Step 1: 实现**

`web/src/pages/KbOverviewPage.tsx`：顶部加：
```ts
import { Badge } from "../components/ui";
```
（若已 import `Badge` 则跳过。）

在「快捷操作」`<Card>` 之后、「累计成本/最近任务」grid 之前，插入一张配置卡：
```tsx
      <Card>
        <CardHeader title="模型配置" subtitle="创建知识库时通过 settings_yaml 设定（密钥不入库）" icon={<IconLayers width={18} height={18} />} />
        <div className="mt-4">
          <ModelConfig settings={kb?.settings} />
        </div>
      </Card>
```

并在文件末尾（`export default function` 之外）加：
```tsx
function ModelConfig({ settings }: { settings: Record<string, unknown> | undefined }) {
  if (!settings) return <p className="text-[13px] text-muted">未读取到配置。</p>;
  const llm = (settings.llm as Record<string, unknown> | undefined) ?? {};
  const emb = (settings.embedding as Record<string, unknown> | undefined) ?? {};
  const cr = (settings.community_reports as Record<string, unknown> | undefined) ?? {};
  const rows: { k: string; v: string }[] = [
    { k: "LLM provider", v: String(llm.model_provider ?? "—") },
    { k: "LLM model", v: String(llm.model ?? "—") },
    { k: "Embedding model", v: String(emb.model ?? "—") },
  ];
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-3">
        {rows.map((r) => (
          <div key={r.k} className="rounded-lg border border-line bg-surface-2/40 px-3 py-2">
            <p className="text-[11px] text-muted">{r.k}</p>
            <p className="mt-0.5 truncate font-mono text-[13px] text-ink">{r.v}</p>
          </div>
        ))}
      </div>
      <p className="text-[12px] text-muted">
        社区报告结构化输出：
        <Badge tone={cr.structured_output === false ? "warning" : "info"} className="ml-1">
          {cr.structured_output === false ? "关闭（纯文本回退）" : "开启（json_schema）"}
        </Badge>
      </p>
    </div>
  );
}
```

- [ ] **Step 2: 跑 build + 测试**

Run: `cd web && npm run build && npm test`
Expected: build 成功、测试全绿。

- [ ] **Step 3: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/pages/KbOverviewPage.tsx
git commit -m "feat(web): show KB model config (provider/model/structured_output) on overview"
```

---

## Task 11: DeepSeek 真实端到端验证（手动 runbook）

**Files:** 无代码；产出 `docs/verify-deepseek-2026-06-26.md`（验证记录）。

**约束：** 密钥只用环境变量 `$DEEPSEEK_API_KEY`，绝不写进任何文件。**embedding 注意：DeepSeek 无 embedding 接口** —— `local`/`basic`/`drift` 需要向量，若仅有 DeepSeek 则只能完整验证 `global`（纯社区报告，不需 embedding）；`local`/`basic`/`drift` 验证到 embedding-store 步骤会因无 embedding provider 而失败，属预期，记录即可。如需全方法验证，额外配置一个 embedding provider（如 `OPENAI_API_KEY` + `embedding.model=text-embedding-3-small`）。

- [ ] **Step 1: 起临时后端（仓库外临时 DB/数据）**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
rm -f /tmp/verify.db && rm -rf /tmp/verify_data && mkdir -p /tmp/verify_data
uv run alembic -x db=/tmp/verify.db upgrade head
# 终端 A（worker）：
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY uv run python -m kb_platform.worker /tmp/verify.db
# 终端 B（server）：
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY uv run python -m kb_platform.server /tmp/verify.db /tmp/verify_data 127.0.0.1 8000
```

- [ ] **Step 2: 建 KB（DeepSeek + 关 structured_output）+ 传文档 + 触发全量**

```bash
# 建库（curl 经本机 Surge 代理时加 --noproxy '*'）
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs -H 'Content-Type: application/json' \
  -d '{"name":"verify","method":"standard","settings_yaml":"{\"llm\":{\"model_provider\":\"deepseek\",\"model\":\"deepseek-chat\",\"api_key_env\":\"DEEPSEEK_API_KEY\"},\"community_reports\":{\"structured_output\":false}}"}'
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs/1/documents -H 'Content-Type: application/json' \
  -d '{"title":"电池行业","text":"宁德时代是全球动力电池龙头，与特斯拉签订长期供货协议，专注磷酸铁锂电池。特斯拉的电池供应商还包括 LG 新能源和松下。"}'
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs/1/jobs -H 'Content-Type: application/json' -d '{"type":"full"}'
# 轮询至 SUCCEEDED：
curl -s --noproxy '*' http://127.0.0.1:8000/jobs/1
```

- [ ] **Step 3: 查询 global（DeepSeek-only 可完整验证）并断言新字段**

```bash
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs/1/query \
  -H 'Content-Type: application/json' -d '{"method":"global","query":"宁德时代和特斯拉的关系？"}' | python3 -m json.tool
```
断言：`error == null`；`answer` 非空；`elapsed_ms > 0`；`prompt_tokens > 0`；`sources` 非空（社区报告片段）。

- [ ] **Step 4:（可选，需 embedding provider）验证 local/basic/drift**

若另设了 embedding provider，重复 Step 3 用 `local`/`basic`/`drift`，断言 `sources` 含实体 chips（local）或文本片段（basic）。若仅有 DeepSeek，记录「embedding 缺失导致这三方法在 embedding-store 步失败，属环境限制」。

- [ ] **Step 5: 截图复核（Playwright MCP）**

按 Task 1–10 起好 server（dist 需 `cd web && npm run build`），访问 `/query`、`/chat`、`/kbs/1`，截图确认：来源 chips/片段、token、服务端耗时、KB 模型配置卡均渲染。

- [ ] **Step 6: 记录 + 清理**

把结果写入 `docs/verify-deepseek-2026-06-26.md`（成功方法 / 各方法 sources 是否非空 / embedding 限制说明）。停止 worker/server，`rm -f /tmp/verify.db* && rm -rf /tmp/verify_data`。

- [ ] **Step 7: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add docs/verify-deepseek-2026-06-26.md
git commit -m "docs: DeepSeek end-to-end query verification record"
```

---

## Self-Review（写完后自检结果）

- **Spec 覆盖**：Part 1→Task 1；Part 2（dataclass/extract/wire/models+route）→Task 2–5；Part 3（types/shared/pages）→Task 7–9；Part 4→Task 6（后端）+ Task 10（前端）；Part 5→Task 11。审计中「字段全对齐、仅 query 与 settings 两处缺口」均覆盖。
- **占位符扫描**：无 TBD/TODO；每个代码步含完整代码。
- **类型一致性**：`SourceRef`/`SourceOut`（kind/name/text）、`QueryResult.elapsed_ms/prompt_tokens/output_tokens/llm_calls/sources`、`QueryResultOut` 同名字段、前端 `elapsedMs/promptTokens/...`、`KbDetailOut.settings`↔`KbOut.settings?` —— 前后端命名一致（snake_case ↔ camelCase 由 route 显式映射，前端 types 用 camelCase）。
- **已知限制**：DeepSeek 无 embedding → `global` 可全验证；`local`/`basic`/`drift` 需额外 embedding provider（Task 11 Step 4 已说明，不阻塞实现）。
