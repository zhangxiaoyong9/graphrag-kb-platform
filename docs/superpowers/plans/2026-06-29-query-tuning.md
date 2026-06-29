# A3 查询调参 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在查询时调四个旋钮(community_level / response_type / top_k / temperature)+ 用检索预设库一键应用整套配置,三层取值 `硬编码基线 ← KB 设置默认值 ← 按次覆盖`。

**Architecture:** 扩展 `QueryEngine` Protocol 加 `QueryParams` 数据类;路由层用 `resolve_query_params` 把"KB 默认 ← 按次"解析成最终参数传给引擎;`GraphRagQueryEngine._build_engine` 读 params 替代硬编码、并就地改 resolved `GraphRagConfig` 注入 top_k/temperature。预设是全局 DB 表(`query_presets`,Alembic 0007),CRUD 端点 + 前端管理页;聊天链路与 MCP 不变。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic / graphrag v3.1.0(后端);React + TS + Vite + Tailwind(前端);pytest(asyncio_mode=auto)、vitest。

## Global Constraints

- **graphrag 隔离接缝**:`graphrag` 只在 `kb_platform/graph/graphrag_adapter.py` 与 `kb_platform/query/graphrag_engine.py` 导入;`query/params.py`(本计划新增)不得 import graphrag。
- **DB 访问**一律经 `Repository` + `session_scope`;新表经 Alembic 迁移(编号 `0007`,尾随 `0006_conversations`)。
- **SSE 契约不变**:`POST /kbs/{id}/query` 仍返回 `text/event-stream`(meta/delta/done/error);`params` 只是请求体新增可选字段。
- **UI 文案中文**;侧栏分组固定(工作台/知识库/检索与问答/分析与监控/系统管理),新页归入「检索与问答」。
- **测试哲学**:后端真实 graphrag 路径用 Fake/monkeypatch(不跑真 LLM/parquet);新增的纯逻辑(`_apply_params`/`_effective_system_prompt`/`_effective_levels`/`resolve_query_params`)直接单测。
- **ruff**:line-length 100,py311;**前端**:`npm test` = `vitest run --no-file-parallelism`,`npm run build` = `tsc -b && vite build`。
- **MCP 不变**:`KbApiClient.query()` 不发 params(用 KB 默认值)—— 本计划不改 `kb_platform/mcp/`。

---

## File Structure

**后端(新建/修改)**
- `kb_platform/query/engine.py` — 加 `QueryParams` dataclass;Protocol + `FakeQueryEngine` 两方法加 `params` 形参。
- `kb_platform/query/params.py` *(新)* — `resolve_query_params(kb_settings, per_query)`;无 graphrag 依赖。
- `kb_platform/query/graphrag_engine.py` — 加模块级 `_apply_params`/`_effective_system_prompt`/`_effective_levels`;`_build_engine`/`search`/`stream_search` 接 `params`。
- `kb_platform/api/models.py` — `QueryParamsIn`;`QueryRequest.params`。
- `kb_platform/api/routes_query.py` — 解析 + 透传 params。
- `kb_platform/api/routes_conversations.py` — 聊天解析 KB 默认值(`per_query=None`)透传。
- `kb_platform/conversation/service.py` — `send_streaming`/`send` 加 `params` 形参转发给 engine。
- `kb_platform/db/models.py` — `QueryPreset` ORM。
- `kb_platform/db/repository.py` — 预设 CRUD 方法。
- `kb_platform/api/routes_presets.py` *(新)* + `kb_platform/api/app.py` 注册。
- `alembic/versions/0007_query_presets.py` *(新)* — 建表 + seed 3 条内置。

**前端(新建/修改)**
- `web/src/api/types.ts` — `QueryParams`/`QueryPreset` 类型。
- `web/src/api/client.ts` — `query(..., params?)`;预设 CRUD。
- `web/src/pages/QueryPage.tsx` — 调参面板 + 预设下拉 + 另存为。
- `web/src/pages/QueryPresetsPage.tsx` *(新)* + `App.tsx` 路由 + `lib/nav.ts` 侧栏。
- `web/src/lib/kb-settings.ts` + `web/src/components/KbForm.tsx` — 「检索默认值」段。

**测试(新建)**
- `tests/test_resolve_params.py`、`tests/test_apply_params.py`、`tests/test_query_params_route.py`、`tests/test_query_presets.py`;扩展 `tests/test_conversation_service.py`。
- `web/src/lib/kb-settings.test.ts`(扩展)、`web/src/pages/QueryPage.test.tsx`(扩展)、`web/src/pages/QueryPresetsPage.test.tsx`(新)。

---

### Task 1: `QueryParams` dataclass + Protocol / FakeQueryEngine 形参

**Files:**
- Modify: `kb_platform/query/engine.py`
- Test: `tests/test_query_params.py`

**Interfaces:**
- Produces: `QueryParams(community_level, response_type, top_k, temperature, system_prompt)`(全 `| None = None`);`QueryEngine.search`/`stream_search` 尾参 `params: QueryParams | None = None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_query_params.py
from kb_platform.query.engine import FakeQueryEngine, QueryParams


def test_query_params_all_default_none():
    p = QueryParams()
    assert p.community_level is None
    assert p.response_type is None
    assert p.top_k is None
    assert p.temperature is None
    assert p.system_prompt is None


async def test_fake_engine_stream_accepts_params():
    eng = FakeQueryEngine()
    out = [e async for e in eng.stream_search("local", "q", "/tmp", QueryParams(community_level=1))]
    assert out and out[-1].method == "local"


async def test_fake_engine_search_accepts_params():
    eng = FakeQueryEngine()
    res = await eng.search("global", "q", "/tmp", QueryParams(temperature=0.3))
    assert res.method == "global"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_params.py -v`
Expected: FAIL — `QueryParams` 未定义 / `stream_search() got unexpected keyword 'params'`。

- [ ] **Step 3: 实现**

在 `kb_platform/query/engine.py` 的 `StreamDone` 之后、`QueryEngine` 之前插入:

```python
@dataclass
class QueryParams:
    """Per-query tuning knobs (all optional; None = use the lower layer).

    Layered by the route: hardcoded baseline <- KB settings (query_defaults)
    <- per-query (this object). See resolve_query_params.
    """

    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
```

把 Protocol 改为:

```python
class QueryEngine(Protocol):
    async def search(
        self, method: str, query: str, kb_data_root: str, params: "QueryParams | None" = None
    ) -> QueryResult: ...

    async def stream_search(
        self, method: str, query: str, kb_data_root: str, params: "QueryParams | None" = None
    ) -> AsyncIterator["StreamDelta | StreamDone"]: ...
```

把 `FakeQueryEngine` 两方法签名加 `params: "QueryParams | None" = None`(实现忽略它):

```python
class FakeQueryEngine:
    async def search(self, method, query, kb_data_root, params=None) -> QueryResult:
        return QueryResult(answer=f"[{method}] You asked: {query}", method=method)

    async def stream_search(self, method, query, kb_data_root, params=None):
        answer = f"[{method}] You asked: {query}"
        parts = answer.split(" ")
        for i, w in enumerate(parts):
            yield StreamDelta(text=(w + (" " if i < len(parts) - 1 else "")))
        yield StreamDone(answer=answer, method=method)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_query_params.py -v`
Expected: PASS(3 项)。

- [ ] **Step 5: 全量回归 + ruff + 提交**

```bash
uv run pytest -q
uv run ruff check .
git add kb_platform/query/engine.py tests/test_query_params.py
git commit -m "feat(query): add QueryParams + Protocol params arg"
```

---

### Task 2: `resolve_query_params`(三层解析,无 graphrag 依赖)

**Files:**
- Create: `kb_platform/query/params.py`
- Test: `tests/test_resolve_params.py`

**Interfaces:**
- Consumes: `QueryParams`(Task 1)。
- Produces: `resolve_query_params(kb_settings: dict | None, per_query: QueryParams | None) -> QueryParams`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resolve_params.py
from kb_platform.query.engine import QueryParams
from kb_platform.query.params import resolve_query_params


def test_all_none_when_nothing_set():
    assert resolve_query_params({}, None) == QueryParams()


def test_kb_defaults_used_when_no_per_query():
    kb = {"query_defaults": {"community_level": 1, "temperature": 0.3}}
    p = resolve_query_params(kb, None)
    assert p.community_level == 1 and p.temperature == 0.3


def test_per_query_overrides_kb():
    kb = {"query_defaults": {"community_level": 1, "response_type": "single paragraph"}}
    p = resolve_query_params(kb, QueryParams(community_level=3))
    assert p.community_level == 3
    assert p.response_type == "single paragraph"


def test_per_query_partial_only_overrides_set_fields():
    p = resolve_query_params({}, QueryParams(top_k=12))
    assert p.top_k == 12 and p.community_level is None


def test_missing_query_defaults_bucket_is_ok():
    assert resolve_query_params({"chunking": {"size": 1}}, None) == QueryParams()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_resolve_params.py -v`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 实现**

```python
# kb_platform/query/params.py
"""Three-layer query-param resolution: hardcoded baseline <- KB settings <- per-query.

No graphrag import here — this is pure layering. The engine applies the
resolved QueryParams (community_level/response_type are read directly;
top_k/temperature are injected into the resolved GraphRagConfig; system_prompt
overrides the method's primary answer-prompt slot).
"""
from kb_platform.query.engine import QueryParams

_FIELDS = ("community_level", "response_type", "top_k", "temperature", "system_prompt")


def resolve_query_params(kb_settings: dict | None, per_query: QueryParams | None) -> QueryParams:
    kb_settings = kb_settings or {}
    kbq = kb_settings.get("query_defaults") if isinstance(kb_settings, dict) else None
    kbq = kbq or {}

    def pick(name: str):
        per = getattr(per_query, name) if per_query is not None else None
        return per if per is not None else kbq.get(name)

    return QueryParams(**{name: pick(name) for name in _FIELDS})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_resolve_params.py -v`
Expected: PASS(5 项)。

- [ ] **Step 5: ruff + 提交**

```bash
uv run ruff check .
git add kb_platform/query/params.py tests/test_resolve_params.py
git commit -m "feat(query): add resolve_query_params (KB-default <- per-query)"
```

---

### Task 3: `GraphRagQueryEngine` 消费 `params`(纯助手 + 接线)

**Files:**
- Modify: `kb_platform/query/graphrag_engine.py`
- Test: `tests/test_apply_params.py`

**Interfaces:**
- Consumes: `QueryParams`(Task 1)。
- Produces: 模块级 `_apply_params(config, method, params)`、`_effective_system_prompt(params, query_prompts, method)`、`_effective_levels(params)`;`GraphRagQueryEngine.search`/`stream_search`/`_build_engine` 接 `params`。

> graphrag v3.1.0 已核实(见 spec §4.5):top_k = `config.local_search.top_k_entities`/`top_k_relationships`(local)、`config.basic_search.k`(basic);temperature = `config.completion_models[<method completion_model_id>].call_args["temperature"]`(local/global/basic)、`config.drift_search.reduce_temperature`+`local_search_temperature`(drift)。

- [ ] **Step 1: 写失败测试(纯助手)**

```python
# tests/test_apply_params.py
from kb_platform.query.engine import QueryParams
from kb_platform.query.graphrag_engine import (
    GraphRagQueryEngine,
    _apply_params,
    _effective_levels,
    _effective_system_prompt,
)


def _cfg(method="local"):
    eng = GraphRagQueryEngine(
        data_root=".", model_config={"llm": {"model": "x", "model_provider": "openai"}}
    )
    return eng._resolve_config()


def test_effective_levels_default():
    assert _effective_levels(None) == (2, "multiple paragraphs")


def test_effective_levels_from_params():
    assert _effective_levels(QueryParams(community_level=1, response_type="single paragraph")) == (
        1,
        "single paragraph",
    )


def test_apply_params_top_k_local():
    cfg = _cfg()
    _apply_params(cfg, "local", QueryParams(top_k=12))
    assert cfg.local_search.top_k_entities == 12
    assert cfg.local_search.top_k_relationships == 12


def test_apply_params_top_k_basic_uses_k():
    cfg = _cfg()
    _apply_params(cfg, "basic", QueryParams(top_k=8))
    assert cfg.basic_search.k == 8


def test_apply_params_temperature_local_call_args():
    cfg = _cfg()
    _apply_params(cfg, "local", QueryParams(temperature=0.4))
    mid = cfg.local_search.completion_model_id
    assert cfg.completion_models[mid].call_args.get("temperature") == 0.4


def test_apply_params_temperature_drift_fields():
    cfg = _cfg()
    _apply_params(cfg, "drift", QueryParams(temperature=0.5))
    assert cfg.drift_search.reduce_temperature == 0.5
    assert cfg.drift_search.local_search_temperature == 0.5


def test_apply_params_none_is_noop():
    cfg = _cfg()
    before_entities = cfg.local_search.top_k_entities
    _apply_params(cfg, "local", None)
    assert cfg.local_search.top_k_entities == before_entities


def test_apply_params_global_top_k_ignored():
    cfg = _cfg()
    _apply_params(cfg, "global", QueryParams(top_k=99))
    # global has no top_k knob; nothing asserts an error — just must not raise


def test_effective_system_prompt_per_query_wins():
    qp = {"local_system": "KB-LOCAL", "global_reduce": "KB-REDUCE"}
    assert _effective_system_prompt(QueryParams(system_prompt="PQ"), qp, "local") == "PQ"


def test_effective_system_prompt_kb_slot_for_method():
    qp = {"local_system": "KB-LOCAL", "global_reduce": "KB-REDUCE", "global_map": "KB-MAP"}
    assert _effective_system_prompt(None, qp, "local") == "KB-LOCAL"
    assert _effective_system_prompt(None, qp, "global") == "KB-REDUCE"  # global -> reduce slot
    assert _effective_system_prompt(None, qp, "basic") is None  # basic_system unset
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_apply_params.py -v`
Expected: FAIL — `_apply_params` 等未定义。

- [ ] **Step 3: 实现纯助手(模块级,加在 `_SourceCapturingCallback` 之后)**

```python
# graphrag_engine.py —— 模块级助手(放在 _SourceCapturingCallback 类之后)

# Per-query system_prompt maps to each method's PRIMARY answer-shaping prompt slot.
# (global/drift reduce is the final answer step; their map/local slots stay KB-level.)
_PRIMARY_PROMPT_SLOT = {
    "local": "local_system",
    "global": "global_reduce",
    "drift": "global_reduce",
    "basic": "basic_system",
}

_BASE_COMMUNITY_LEVEL = 2
_BASE_RESPONSE_TYPE = "multiple paragraphs"


def _effective_levels(params: "QueryParams | None") -> tuple[int, str]:
    """community_level / response_type from params, else the hardcoded baseline."""
    cl = params.community_level if params and params.community_level is not None else _BASE_COMMUNITY_LEVEL
    rt = params.response_type if params and params.response_type else _BASE_RESPONSE_TYPE
    return cl, rt


def _effective_system_prompt(params, query_prompts: dict, method: str) -> str | None:
    """Per-query system_prompt overrides the method's primary KB prompt slot; else
    the KB value for that slot; else None (= graphrag default)."""
    if params is not None and params.system_prompt:
        return params.system_prompt
    slot = _PRIMARY_PROMPT_SLOT.get(method)
    return (query_prompts or {}).get(slot) if slot else None


def _apply_params(config, method: str, params: "QueryParams | None") -> None:
    """Mutate the resolved GraphRagConfig in place for top_k / temperature.

    graphrag itself mutates config objects (e.g. vector_store.db_uri), so this is
    safe; production builds a fresh config per request. No-op when params is None.
    """
    if params is None:
        return
    if params.top_k is not None:
        if method == "local":
            config.local_search.top_k_entities = params.top_k
            config.local_search.top_k_relationships = params.top_k
        elif method == "basic":
            config.basic_search.k = params.top_k
        # global / drift: top_k is not applicable -> ignored
    if params.temperature is not None:
        if method == "drift":
            config.drift_search.reduce_temperature = params.temperature
            config.drift_search.local_search_temperature = params.temperature
        else:
            mid = {
                "local": config.local_search.completion_model_id,
                "global": config.global_search.completion_model_id,
                "basic": config.basic_search.completion_model_id,
            }.get(method)
            cm = config.completion_models.get(mid) if mid else None
            if cm is not None:
                cm.call_args = {**(cm.call_args or {}), "temperature": params.temperature}
```

> `QueryParams` 已在 `engine.py` 定义;`graphrag_engine.py` 顶部 `from kb_platform.query.engine import QueryResult, SourceRef, StreamDelta, StreamDone` 一行加上 `QueryParams`(用引号注解或直接导入均可——这里直接导入最简)。

- [ ] **Step 4: 接线 `_build_engine` / `search` / `stream_search`**

改 `_build_engine` 签名与开头(替换现有 `community_level = 2` / `response_type = "multiple paragraphs"` 两行):

```python
def _build_engine(self, method: str, root: str, params=None):
    # ... existing imports inside the method unchanged ...
    config = self._resolve_config(root=root)
    _apply_params(config, method, params)
    community_level, response_type = _effective_levels(params)

    # Optional custom query prompts from KB settings (existing) ...
    ls = (self._model_config if isinstance(self._model_config, dict) else {}) if self._model_config else {}
    qp = ls.get("query_prompts") or {}
    # ... rest unchanged through the readers (they already use community_level var) ...
```

四个 method 分支里,把 prompt 变量改为用 `_effective_system_prompt`,其余入参不变:

```python
    if method == "local":
        store = self._build_embedding_store(config, _ENTITY_DESCRIPTION)
        return get_local_search_engine(
            config, reports=reports, text_units=text_units, entities=entities,
            relationships=relationships, covariates={}, response_type=response_type,
            description_embedding_store=store,
            system_prompt=_effective_system_prompt(params, qp, "local"),
        )
    if method == "global":
        return get_global_search_engine(
            config, reports=reports, entities=entities, communities=communities,
            response_type=response_type,
            map_system_prompt=qp.get("global_map"),  # map stays KB-level
            reduce_system_prompt=_effective_system_prompt(params, qp, "global"),
        )
    if method == "drift":
        store = self._build_embedding_store(config, _ENTITY_DESCRIPTION)
        report_store = self._build_embedding_store(config, _COMMUNITY_FULL_CONTENT)
        from graphrag.query.indexer_adapters import read_indexer_report_embeddings
        read_indexer_report_embeddings(reports, report_store)
        return get_drift_search_engine(
            config, reports=reports, text_units=text_units, entities=entities,
            relationships=relationships, description_embedding_store=store,
            response_type=response_type,
            local_system_prompt=qp.get("local_system"),  # drift local phase stays KB-level
            reduce_system_prompt=_effective_system_prompt(params, qp, "drift"),
        )
    if method == "basic":
        store = self._build_embedding_store(config, _TEXT_UNIT_TEXT)
        return get_basic_search_engine(
            text_units=text_units, text_unit_embeddings=store, config=config,
            response_type=response_type,
            system_prompt=_effective_system_prompt(params, qp, "basic"),
        )
    raise ValueError(f"unknown query method: {method}")
```

改 `search` / `stream_search` / `_run_graphrag_search` 透传 params:

```python
async def search(self, method, query, kb_data_root, params=None) -> QueryResult:
    root = self._data_root or kb_data_root
    if method in _REPORTS_REQUIRED and not os.path.exists(os.path.join(root, _COMMUNITY_REPORTS_FILE)):
        return QueryResult(answer="", method=method, error=_NO_REPORTS_MSG)
    try:
        return await self._run_graphrag_search(method, query, root, params)
    except Exception as e:
        logger.exception("graphrag search failed for method=%s", method)
        return QueryResult(answer="", method=method, error=str(e))

async def stream_search(self, method, query, kb_data_root, params=None):
    root = self._data_root or kb_data_root
    if method in _REPORTS_REQUIRED and not os.path.exists(os.path.join(root, _COMMUNITY_REPORTS_FILE)):
        yield StreamDone(method=method, answer="", error=_NO_REPORTS_MSG)
        return
    try:
        engine = self._build_engine(method, root, params)
    except Exception as e:
        logger.exception("stream_search build_engine failed for method=%s", method)
        yield StreamDone(method=method, answer="", error=str(e))
        return
    # ... rest of stream_search body unchanged ...

async def _run_graphrag_search(self, method, query, root, params=None) -> QueryResult:
    engine = self._build_engine(method, root, params)
    if method == "basic":
        engine.model = _StreamFixWrapper(engine.model)
    result = await engine.search(query=query)
    return self._result_from_search(method, result)
```

顶部导入加 `QueryParams`:`from kb_platform.query.engine import QueryParams, QueryResult, SourceRef, StreamDelta, StreamDone`。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_apply_params.py tests/test_graphrag_engine.py tests/test_graphrag_engine_stream.py -v`
Expected: PASS(新助手测试 + 既有 graphrag engine 测试不回归)。

- [ ] **Step 6: 全量回归 + ruff + 提交**

```bash
uv run pytest -q
uv run ruff check .
git add kb_platform/query/graphrag_engine.py tests/test_apply_params.py
git commit -m "feat(query): GraphRagQueryEngine consumes QueryParams (knobs + prompt)"
```

---

### Task 4: `QueryRequest.params` + `routes_query` 解析透传

**Files:**
- Modify: `kb_platform/api/models.py`, `kb_platform/api/routes_query.py`
- Test: `tests/test_query_params_route.py`

**Interfaces:**
- Consumes: `resolve_query_params`(Task 2)、`QueryParams`(Task 1)、engine `params`(Task 3)。
- Produces: `QueryParamsIn`、`QueryRequest.params`;`POST /kbs/{id}/query` 接受可选 `params`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_query_params_route.py
import json

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine, QueryParams


def _client():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=".", llm_profile_id=None))
    repo = Repository(engine)
    captured: list = []

    class Capturing(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    app = create_app(repo, data_root=".", query_engine=Capturing())
    from starlette.testclient import TestClient
    return TestClient(app), captured


def _read_sse(text):
    # collect event types from the raw SSE body
    return [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("event:")]


def test_query_route_forwards_per_query_params():
    client, captured = _client()
    body = {"method": "local", "query": "hi", "params": {"community_level": 1, "top_k": 9}}
    r = client.post("/kbs/1/query", json=body)
    assert r.status_code == 200
    assert "delta" in _read_sse(r.text)
    assert captured and captured[0].community_level == 1 and captured[0].top_k == 9


def test_query_route_no_params_sends_none_object():
    client, captured = _client()
    client.post("/kbs/1/query", json={"method": "local", "query": "hi"})
    # FakeQueryEngine receives params=None (no per-query); resolve still yields a QueryParams
    assert captured and captured[0].community_level is None


def test_query_route_kb_defaults_applied():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(
            name="kb1", method="standard",
            settings_json=json.dumps({"query_defaults": {"temperature": 0.2}}),
            data_root=".", llm_profile_id=None,
        ))
    repo = Repository(engine)
    captured: list = []

    class Capturing(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    app = create_app(repo, data_root=".", query_engine=Capturing())
    from starlette.testclient import TestClient
    client = TestClient(app)
    client.post("/kbs/1/query", json={"method": "local", "query": "hi"})
    assert captured and captured[0].temperature == 0.2  # KB default reached engine
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_params_route.py -v`
Expected: FAIL — `QueryRequest` 无 `params` / engine 收到的 params 不对。

- [ ] **Step 3: 改 `models.py`**

在 `QueryRequest` 上方加 `QueryParamsIn`,并给 `QueryRequest` 加 `params`:

```python
class QueryParamsIn(BaseModel):
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None


class QueryRequest(BaseModel):
    method: str
    query: str
    params: QueryParamsIn | None = None
```

- [ ] **Step 4: 改 `routes_query.py`(解析 + 透传)**

顶部 import 追加(`StreamDelta` 已在文件中导入则不重复):

```python
from kb_platform.query.engine import QueryParams, StreamDelta
from kb_platform.query.params import resolve_query_params
```

把整个 `gen()` 替换为下面这版(关键:`per_query` 从 payload 算;`resolved` 生产分支读 `kb.settings_json` 里的 `query_defaults`,注入分支用空 settings;`params=resolved` 透传给 `stream_search`;`done` 分支的 `QueryResultOut(...)` 构造与原实现完全一致,不要改):

```python
    async def gen():
        nonlocal data_root
        local_engine = engine
        import json

        per_query = (
            QueryParams(**payload.params.model_dump()) if payload.params is not None else None
        )
        resolved: QueryParams | None = None
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
                kb_settings = json.loads(kb.settings_json or "{}")
                resolved = resolve_query_params(kb_settings, per_query)
                try:
                    model_config = assemble_kb_settings(kb, repo)
                except Exception as exc:  # noqa: BLE001 - graceful, never 500
                    yield format_sse("error", {"message": f"settings resolution failed: {exc}"})
                    return
            try:
                local_engine = GraphRagQueryEngine(data_root=data_root, model_config=model_config)
            except Exception as exc:  # noqa: BLE001 - graceful, never 500
                yield format_sse("error", {"message": f"engine build failed: {exc}"})
                return
        else:
            resolved = resolve_query_params({}, per_query)

        yield format_sse("meta", {"method": payload.method})
        async for ev in local_engine.stream_search(
            payload.method, payload.query, data_root, params=resolved
        ):
            if isinstance(ev, StreamDelta):
                yield format_sse("delta", {"text": ev.text})
            else:  # StreamDone — 与原实现一致地构造 QueryResultOut
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
```

> `select`、`session_scope`、`KnowledgeBase`、`QueryResultOut`、`SourceOut`、`format_sse` 均已在文件顶部导入,无需新增。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_query_params_route.py tests/test_api_query.py -v`
Expected: PASS。

- [ ] **Step 6: 全量回归 + ruff + 提交**

```bash
uv run pytest -q && uv run ruff check .
git add kb_platform/api/models.py kb_platform/api/routes_query.py tests/test_query_params_route.py
git commit -m "feat(api): QueryRequest.params + resolve/forward in /kbs/{id}/query"
```

---

### Task 5: 聊天链路透传 KB 默认值(`routes_conversations` + `ConversationService`)

**Files:**
- Modify: `kb_platform/conversation/service.py`, `kb_platform/api/routes_conversations.py`
- Test: 扩展 `tests/test_conversation_service.py`

**Interfaces:**
- Consumes: `resolve_query_params`、engine `params`。
- Produces: `ConversationService.send`/`send_streaming` 加 `params: QueryParams | None = None` 转发给 `engine.search`/`stream_search`。

> 聊天无按次控件:`MessageSend` 不变;路由解析 `resolve_query_params(kb_settings, None)`(纯 KB 默认值)传入。

- [ ] **Step 1: 写失败测试**

在 `tests/test_conversation_service.py` 末尾追加(复用该文件既有的 `_setup` / `_drain` 与 `FakeQueryEngine`):

```python
async def test_send_streaming_forwards_params_to_engine(tmp_path):
    """Chat path forwards the resolved params object to the engine."""
    from kb_platform.query.engine import QueryParams

    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    captured: list = []

    class _RecordingEngine(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    svc = ConversationService(repo, _RecordingEngine(), None, data_root=".")
    await _drain(svc.send_streaming(cid, "hi", None, params=QueryParams(temperature=0.2)))
    assert captured and captured[0] is not None and captured[0].temperature == 0.2


async def test_send_streaming_defaults_params_none_when_omitted(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    captured: list = []

    class _RecordingEngine(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    svc = ConversationService(repo, _RecordingEngine(), None, data_root=".")
    await _drain(svc.send_streaming(cid, "hi", None))
    assert captured and captured[0] is None  # no params passed -> engine sees None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_conversation_service.py -v`
Expected: FAIL — `send_streaming` 无 `params` 形参 / engine 未收到。

- [ ] **Step 3: 改 `service.py`**

`send` 与 `send_streaming` 加 `params` 形参并转发:

```python
async def send(self, conversation_id, content, method, params=None):
    # ... 不变 ...
    result = await self._engine.search(chosen_method, standalone, self._data_root, params=params)
    # ... 不变 ...

async def send_streaming(self, conversation_id, content, method, params=None):
    # ... 不变直到 stream_search 调用 ...
    async for ev in self._engine.stream_search(chosen_method, standalone, self._data_root, params=params):
        # ... 不变 ...
```

- [ ] **Step 4: 改 `routes_conversations.py`**

顶部 import 追加(`json` 已在文件顶部导入则不重复):

```python
from kb_platform.query.params import resolve_query_params
```

在 `gen()` 的两个分支各自算出 `resolved`,然后透传给 `send_streaming`。生产分支(`engine is None`)里 `kb` 已经由 `repo.get_kb(conv.kb_id)` 取到;注入分支没有 kb,用空 settings(即纯 `per_query=None` → 全 None QueryParams)。

在 `engine is None` 分支内、`try: local_engine = GraphRagQueryEngine(...)` 之前加:

```python
            kb_settings = json.loads(kb.settings_json or "{}")
            resolved = resolve_query_params(kb_settings, None)
```

在 `else:` 分支(`local_engine = engine` / `local_rewriter = rewriter`)之后加:

```python
        else:
            local_engine = engine
            local_rewriter = rewriter
            resolved = resolve_query_params({}, None)
```

把 `send_streaming` 调用改为带 `params=resolved`:

```python
        service = ConversationService(repo, local_engine, local_rewriter, data_root)
        async for ev in service.send_streaming(
            conv_id, payload.content, payload.method, params=resolved
        ):
            if ev.type == "done" and ev.message is not None:
                yield format_sse("done", {"message": _message_out(ev.message).model_dump(mode="json")})
            else:
                yield format_sse(ev.type, ev.data)
```

(其余 gen() 内容不变。)`resolved` 形参在 `ConversationService.send_streaming` 已由 Task 5 Step 3 加好。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_conversation_service.py tests/test_api_conversations.py -v`
Expected: PASS。

- [ ] **Step 6: 全量回归 + ruff + 提交**

```bash
uv run pytest -q && uv run ruff check .
git add kb_platform/conversation/service.py kb_platform/api/routes_conversations.py tests/test_conversation_service.py
git commit -m "feat(chat): forward resolved KB-default params through ConversationService"
```

---

### Task 6: `QueryPreset` ORM + Alembic 0007(seed 3 内置)+ Repository CRUD

**Files:**
- Modify: `kb_platform/db/models.py`, `kb_platform/db/repository.py`
- Create: `alembic/versions/0007_query_presets.py`
- Test: `tests/test_query_presets_repo.py`

**Interfaces:**
- Produces: `QueryPreset` ORM;`Repository.list_query_presets/get/create/update/delete_query_preset`;迁移建表 + seed。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_query_presets_repo.py
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def _repo():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    return Repository(e), e


def test_seed_builtin_presets_present_after_create_all():
    # Alembic seeds via op.bulk_insert; for in-memory Base.metadata.create_all we
    # also seed in code (Repository.__init__ or a seed helper) so tests get them.
    repo, _ = _repo()
    names = {p.name for p in repo.list_query_presets()}
    assert {"默认", "简洁要点", "详尽调研"} <= names


def test_create_and_list_custom_preset():
    repo, _ = _repo()
    p = repo.create_query_preset(name="我的预设", method="local", community_level=1, temperature=0.2)
    assert p.id is not None and p.is_builtin is False
    assert any(x.name == "我的预设" for x in repo.list_query_presets())


def test_update_query_preset():
    repo, _ = _repo()
    p = repo.create_query_preset(name="p2", method="basic")
    updated = repo.update_query_preset(p.id, response_type="single paragraph")
    assert updated.response_type == "single paragraph"


def test_delete_query_preset():
    repo, _ = _repo()
    p = repo.create_query_preset(name="p3", method="local")
    assert repo.delete_query_preset(p.id) is True
    assert repo.get_query_preset(p.id) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_presets_repo.py -v`
Expected: FAIL — `QueryPreset` / CRUD 不存在。

- [ ] **Step 3: 加 ORM(`models.py`)**

在文件末尾 `models_conversation` 导入之后加:

```python
from sqlalchemy import Float  # 顶部导入已有 Boolean,DateTime,ForeignKey,Index,Integer,String,Text —— 补 Float


class QueryPreset(Base):
    __tablename__ = "query_preset"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(String, default="")
    method: Mapped[str] = mapped_column(String)  # local|global|drift|basic
    community_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_type: Mapped[str | None] = mapped_column(String, nullable=True)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

> 顶部 `from sqlalchemy import ...` 行追加 `Float`。

- [ ] **Step 4: 加迁移 `0007_query_presets.py`**

```python
# alembic/versions/0007_query_presets.py
"""query_presets: global, cross-KB retrieval preset library (A3).

NOTE: the built-in seed list is DUPLICATED in Repository._seed_builtin_presets
(in-memory test DBs created via Base.metadata.create_all bypass Alembic). Keep
the two lists in sync when editing.
"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_preset",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("method", sa.String, nullable=False),
        sa.Column("community_level", sa.Integer, nullable=True),
        sa.Column("response_type", sa.String, nullable=True),
        sa.Column("top_k", sa.Integer, nullable=True),
        sa.Column("temperature", sa.Float, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    now = datetime.now()
    op.bulk_insert(
        sa.table(
            "query_preset",
            sa.column("name", sa.String),
            sa.column("description", sa.String),
            sa.column("method", sa.String),
            sa.column("community_level", sa.Integer),
            sa.column("response_type", sa.String),
            sa.column("temperature", sa.Float),
            sa.column("is_builtin", sa.Boolean),
            sa.column("created_at", sa.DateTime),
        ),
        [
            {"name": "默认", "description": "graphrag 默认行为", "method": "local",
             "is_builtin": True, "created_at": now},
            {"name": "简洁要点", "description": "单段、低温、更确定", "method": "local",
             "response_type": "single paragraph", "temperature": 0.2,
             "is_builtin": True, "created_at": now},
            {"name": "详尽调研", "description": "global、粗社区、多段", "method": "global",
             "community_level": 1, "response_type": "multiple paragraphs", "temperature": 0.3,
             "is_builtin": True, "created_at": now},
        ],
    )


def downgrade() -> None:
    op.drop_table("query_preset")
```

- [ ] **Step 5: Repository CRUD + seed 辅助(`repository.py`)**

```python
# repository.py 顶部导入补 QueryPreset(与 Conversation 一起):
from kb_platform.db.models_conversation import Conversation, Message
from kb_platform.db.models import QueryPreset  # 或就近导入

# seed 内置(幂等):在 Repository.__init__ 末尾或单独方法
_BUILTIN_PRESETS = [
    {"name": "默认", "description": "graphrag 默认行为", "method": "local", "is_builtin": True},
    {"name": "简洁要点", "description": "单段、低温、更确定", "method": "local",
     "response_type": "single paragraph", "temperature": 0.2, "is_builtin": True},
    {"name": "详尽调研", "description": "global、粗社区、多段", "method": "global",
     "community_level": 1, "response_type": "multiple paragraphs", "temperature": 0.3, "is_builtin": True},
]


class Repository:
    def __init__(self, engine):
        self.engine = engine
        self._seed_builtin_presets()

    def _seed_builtin_presets(self):
        with session_scope(self.engine) as s:
            existing = {row[0] for row in s.execute(select(QueryPreset.name)).all()}
            for p in _BUILTIN_PRESETS:
                if p["name"] not in existing:
                    s.add(QueryPreset(**p))

    def list_query_presets(self) -> list[QueryPreset]:
        with session_scope(self.engine) as s:
            rows = s.execute(
                select(QueryPreset).order_by(QueryPreset.is_builtin.desc(), QueryPreset.name)
            ).scalars().all()
            return rows

    def get_query_preset(self, preset_id: int) -> QueryPreset | None:
        with session_scope(self.engine) as s:
            return s.get(QueryPreset, preset_id)

    def create_query_preset(self, **fields) -> QueryPreset:
        with session_scope(self.engine) as s:
            p = QueryPreset(**fields)
            s.add(p)
            s.flush()
            return p

    def update_query_preset(self, preset_id: int, **fields) -> QueryPreset | None:
        with session_scope(self.engine) as s:
            p = s.get(QueryPreset, preset_id)
            if p is None:
                return None
            for k, v in fields.items():
                setattr(p, k, v)
            s.flush()
            return p

    def delete_query_preset(self, preset_id: int) -> bool:
        with session_scope(self.engine) as s:
            p = s.get(QueryPreset, preset_id)
            if p is None:
                return False
            s.delete(p)
            return True
```

> `select` 已在 repository.py 顶部导入(既有 list 方法用)。`__init__` 现有签名若不同,保留其原内容、仅追加 `self._seed_builtin_presets()`。

- [ ] **Step 6: 跑迁移 + 测试**

```bash
uv run alembic upgrade head   # 应用 0007(本地 kb.db)
uv run pytest tests/test_query_presets_repo.py -v
```
Expected: PASS(4 项)。

- [ ] **Step 7: 全量回归 + ruff + 提交**

```bash
uv run pytest -q && uv run ruff check .
git add kb_platform/db/models.py kb_platform/db/repository.py alembic/versions/0007_query_presets.py tests/test_query_presets_repo.py
git commit -m "feat(db): QueryPreset + Alembic 0007 (seed 3 built-ins) + repo CRUD"
```

---

### Task 7: 预设 CRUD API(`routes_presets.py` + 注册)

**Files:**
- Create: `kb_platform/api/routes_presets.py`
- Modify: `kb_platform/api/models.py`(`QueryPresetIn`/`QueryPresetOut`),`kb_platform/api/app.py`(注册)
- Test: `tests/test_query_presets_api.py`

**Interfaces:**
- Produces: `GET /query-presets`、`POST /query-presets`、`PATCH /query-presets/{id}`、`DELETE /query-presets/{id}`(内置 403)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_query_presets_api.py
from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from starlette.testclient import TestClient


def _client():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    return TestClient(create_app(Repository(e), data_root="."))


def test_list_includes_builtins():
    r = _client().get("/query-presets")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert {"默认", "简洁要点", "详尽调研"} <= names


def test_create_then_update_then_delete_custom():
    c = _client()
    body = {"name": "我的", "method": "local", "community_level": 1, "temperature": 0.2}
    r = c.post("/query-presets", json=body)
    assert r.status_code == 201 and r.json()["is_builtin"] is False
    pid = r.json()["id"]
    assert c.patch(f"/query-presets/{pid}", json={"response_type": "single paragraph"}).status_code == 200
    assert c.delete(f"/query-presets/{pid}").status_code == 204


def test_modify_builtin_is_forbidden():
    c = _client()
    pid = next(p["id"] for p in c.get("/query-presets").json() if p["is_builtin"])
    assert c.patch(f"/query-presets/{pid}", json={"temperature": 0.9}).status_code == 403
    assert c.delete(f"/query-presets/{pid}").status_code == 403


def test_duplicate_name_conflicts():
    c = _client()
    r = c.post("/query-presets", json={"name": "默认", "method": "local"})
    assert r.status_code in (409, 422)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_query_presets_api.py -v`
Expected: FAIL — 路由不存在(404)。

- [ ] **Step 3: 加 `models.py` 的 In/Out**

```python
class QueryPresetIn(BaseModel):
    name: str
    description: str = ""
    method: str
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None


class QueryPresetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    method: str | None = None
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None


class QueryPresetOut(BaseModel):
    id: int
    name: str
    description: str
    method: str
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    is_builtin: bool
```

- [ ] **Step 4: 写 `routes_presets.py`**

```python
# kb_platform/api/routes_presets.py
"""Query-preset CRUD: global, cross-KB retrieval presets (A3)."""
from fastapi import APIRouter, HTTPException, Request

from kb_platform.api.models import QueryPresetIn, QueryPresetOut, QueryPresetUpdate

router = APIRouter()


def _out(p) -> QueryPresetOut:
    return QueryPresetOut(
        id=p.id, name=p.name, description=p.description, method=p.method,
        community_level=p.community_level, response_type=p.response_type, top_k=p.top_k,
        temperature=p.temperature, system_prompt=p.system_prompt, is_builtin=p.is_builtin,
    )


def _require_custom(p):
    if p is None:
        raise HTTPException(404)
    if p.is_builtin:
        raise HTTPException(403, "built-in presets are read-only")


@router.get("/query-presets", response_model=list[QueryPresetOut])
def list_presets(request: Request):
    return [_out(p) for p in request.app.state.repo.list_query_presets()]


@router.post("/query-presets", response_model=QueryPresetOut, status_code=201)
def create_preset(payload: QueryPresetIn, request: Request):
    repo = request.app.state.repo
    try:
        p = repo.create_query_preset(is_builtin=False, **payload.model_dump())
    except Exception as exc:  # noqa: BLE001 - IntegrityError on duplicate name
        raise HTTPException(409, f"preset name already exists: {exc}") from exc
    return _out(p)


@router.patch("/query-presets/{pid}", response_model=QueryPresetOut)
def update_preset(pid: int, payload: QueryPresetUpdate, request: Request):
    repo = request.app.state.repo
    p = repo.get_query_preset(pid)
    _require_custom(p)
    updated = repo.update_query_preset(pid, **payload.model_dump(exclude_unset=True))
    return _out(updated)


@router.delete("/query-presets/{pid}", status_code=204)
def delete_preset(pid: int, request: Request):
    repo = request.app.state.repo
    p = repo.get_query_preset(pid)
    _require_custom(p)
    repo.delete_query_preset(pid)
```

- [ ] **Step 5: 注册路由(`app.py`)**

```python
# 顶部导入:
from kb_platform.api.routes_presets import router as presets_router

# include_router 区块加(任意位置,catch-all 之前):
app.include_router(presets_router)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_query_presets_api.py -v`
Expected: PASS(4 项)。

- [ ] **Step 7: 全量回归 + ruff + 提交**

```bash
uv run pytest -q && uv run ruff check .
git add kb_platform/api/routes_presets.py kb_platform/api/models.py kb_platform/api/app.py tests/test_query_presets_api.py
git commit -m "feat(api): query-preset CRUD endpoints (built-ins read-only)"
```

---

### Task 8: KB 设置「检索默认值」段(`kb-settings.ts` + `KbForm.tsx`)

**Files:**
- Modify: `web/src/lib/kb-settings.ts`, `web/src/components/KbForm.tsx`, `web/src/lib/kb-settings.test.ts`
- Test: 扩展 `kb-settings.test.ts`

**Interfaces:**
- Produces: `KbFormState.queryDefaults`;`buildSettings` 写 `query_defaults`;`parseSettings` 回填。

- [ ] **Step 1: 写失败测试(扩 `kb-settings.test.ts`)**

```ts
// 追加到 web/src/lib/kb-settings.test.ts
import { buildSettings, parseSettings, DEFAULTS } from "./kb-settings";

describe("query defaults", () => {
  it("emits query_defaults only when non-default", () => {
    const s = { ...DEFAULTS, queryDefaults: { communityLevel: "1", temperature: "0.2" } };
    const out = buildSettings(s);
    expect(out.query_defaults).toEqual({ community_level: 1, temperature: 0.2 });
  });

  it("omits query_defaults when all empty", () => {
    const out = buildSettings({ ...DEFAULTS });
    expect(out.query_defaults).toBeUndefined();
  });

  it("parseSettings reads query_defaults back", () => {
    const s = parseSettings(
      { query_defaults: { community_level: 1, temperature: 0.2 } },
      "standard",
      "1.0",
    );
    expect(s.queryDefaults.communityLevel).toBe("1");
    expect(s.queryDefaults.temperature).toBe("0.2");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/lib/kb-settings.test.ts`
Expected: FAIL — `queryDefaults` 未定义。

- [ ] **Step 3: 改 `kb-settings.ts`**

`KbFormState` 加字段(在 `queryPrompts` 之后):

```ts
  queryDefaults: {
    communityLevel: string;
    responseType: string;
    topK: string;
    temperature: string;
  };
```

`DEFAULTS` 加:

```ts
  queryDefaults: { communityLevel: "", responseType: "", topK: "", temperature: "" },
```

`buildSettings` 在 `query_prompts` 段之后加:

```ts
  // query defaults (KB-level; omit = hardcoded baseline)
  const qd = state.queryDefaults;
  const qdBuiltin: Record<string, number> = {};
  if (qd.communityLevel.trim()) qdBuiltin.community_level = Number(qd.communityLevel);
  if (qd.responseType.trim()) qdBuiltin.response_type = qd.responseType.trim();
  if (qd.topK.trim()) qdBuiltin.top_k = Number(qd.topK);
  if (qd.temperature.trim()) qdBuiltin.temperature = Number(qd.temperature);
  if (Object.keys(qdBuiltin).length) out.query_defaults = qdBuiltin;
```

`parseSettings` 加(在 `queryPrompts` 之后):

```ts
    queryDefaults: {
      communityLevel: qd?.community_level != null ? String(qd.community_level) : "",
      responseType: f(qd, "response_type", ""),
      topK: qd?.top_k != null ? String(qd.top_k) : "",
      temperature: qd?.temperature != null ? String(qd.temperature) : "",
    },
```

并在 `parseSettings` 顶部加 `const qd = (settings.query_defaults as Record<string, unknown> | undefined) ?? {};`。

- [ ] **Step 4: 改 `KbForm.tsx`(加「检索默认值」段)**

`useState` 初始对象里 `queryPrompts` 之后加 `queryDefaults: { ...DEFAULTS.queryDefaults }`(edit 分支 `parseSettings` 已含)。在「提示词 Prompts」`</details>` 之后、「高级」之前插入:

```tsx
      {/* 检索默认值 */}
      <details>
        <summary className="text-[13px] font-medium text-body cursor-pointer select-none">
          检索默认值 Query Defaults（留空=默认；查询时可按次覆盖）
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="community_level" hint="0–4；越大越细">
            <input className="input" type="number" min={0} max={4}
              value={s.queryDefaults.communityLevel}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, communityLevel: e.target.value })}
              placeholder="留空=2" />
          </Field>
          <Field label="response_type" hint="多段/单段/要点">
            <select className="select"
              value={s.queryDefaults.responseType}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, responseType: e.target.value })}>
              <option value="">留空=默认</option>
              <option value="multiple paragraphs">多段</option>
              <option value="single paragraph">单段</option>
              <option value="bullet points">要点</option>
            </select>
          </Field>
          <Field label="top_k" hint="local/basic 结果数">
            <input className="input" type="number" min={1}
              value={s.queryDefaults.topK}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, topK: e.target.value })}
              placeholder="留空=默认" />
          </Field>
          <Field label="temperature" hint="0–1">
            <input className="input" type="number" step="0.05" min={0} max={1}
              value={s.queryDefaults.temperature}
              onChange={(e) => set("queryDefaults", { ...s.queryDefaults, temperature: e.target.value })}
              placeholder="留空=默认" />
          </Field>
        </div>
      </details>
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd web && npx vitest run src/lib/kb-settings.test.ts`
Expected: PASS。

- [ ] **Step 6: build + 全量前端测试 + 提交**

```bash
cd web && npm run build && npm test
cd ..
git add web/src/lib/kb-settings.ts web/src/lib/kb-settings.test.ts web/src/components/KbForm.tsx
git commit -m "feat(web): KB 'query defaults' section (query_defaults settings)"
```

---

### Task 9: 前端 types + client(`query(..., params?)` + 预设 CRUD)

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`
- Test: 扩展 `web/src/api/client.test.ts`

**Interfaces:**
- Produces: `QueryParams`、`QueryPreset` 类型;`query(kbId, method, q, params?)`;`listQueryPresets/createQueryPreset/updateQueryPreset/deleteQueryPreset`。

- [ ] **Step 1: 写失败测试**

在 `web/src/api/client.test.ts` 顶部 import 加 `query`,并追加两个用 msw `server.use` 捕获 body 的测试:

```ts
import { http, HttpResponse } from "msw";
// 顶部那行 import { ... } from "./client" 加入 query
import { createKb, deleteConversation, deleteDocument, getDocumentDetail, getDocumentEvidence, listKbs, retryUnit, createConversation, sendMessage, query } from "./client";

test("query sends params in body when provided", async () => {
  let captured: unknown;
  server.use(
    http.post("/kbs/1/query", async ({ request }) => {
      captured = await request.json();
      return new HttpResponse("event: done\n", { headers: { "content-type": "text/event-stream" } });
    }),
  );
  await query(1, "local", "q", { community_level: 1 });
  expect((captured as { params: unknown }).params).toEqual({ community_level: 1 });
});

test("query omits params when undefined", async () => {
  let captured: unknown;
  server.use(
    http.post("/kbs/1/query", async ({ request }) => {
      captured = await request.json();
      return new HttpResponse("event: done\n", { headers: { "content-type": "text/event-stream" } });
    }),
  );
  await query(1, "local", "q");
  expect((captured as { params?: unknown }).params).toBeUndefined();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/api/client.test.ts`
Expected: FAIL — `query` 第 4 参未支持。

- [ ] **Step 3: 改 `types.ts`**

```ts
export interface QueryParams {
  community_level?: number;
  response_type?: string;
  top_k?: number;
  temperature?: number;
  system_prompt?: string;
}

export interface QueryPreset {
  id: number;
  name: string;
  description: string;
  method: string;
  community_level?: number | null;
  response_type?: string | null;
  top_k?: number | null;
  temperature?: number | null;
  system_prompt?: string | null;
  is_builtin: boolean;
}
```

- [ ] **Step 4: 改 `client.ts`**

替换 `query` 导出:

```ts
export const query = (kbId: number, method: string, q: string, params?: QueryParams) =>
  fetch(`/kbs/${kbId}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method, query: q, ...(params ? { params } : {}) }),
  });
```

并在文件末尾(`getPromptDefaults` 之后)加:

```ts
export const listQueryPresets = () => req<QueryPreset[]>("/query-presets");
export const createQueryPreset = (b: Omit<QueryPreset, "id" | "is_builtin">) =>
  req<QueryPreset>("/query-presets", { method: "POST", body: JSON.stringify(b) });
export const updateQueryPreset = (id: number, b: Partial<Omit<QueryPreset, "id" | "is_builtin">>) =>
  req<QueryPreset>(`/query-presets/${id}`, { method: "PATCH", body: JSON.stringify(b) });
export const deleteQueryPreset = (id: number) => req<void>(`/query-presets/${id}`, { method: "DELETE" });
```

顶部 `import type { ... } from "./types"` 加入 `QueryParams, QueryPreset`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd web && npx vitest run src/api/client.test.ts`
Expected: PASS。

- [ ] **Step 6: build + 全量前端测试 + 提交**

```bash
cd web && npm run build && npm test
cd ..
git add web/src/api/types.ts web/src/api/client.ts web/src/api/client.test.ts
git commit -m "feat(web): QueryParams/QueryPreset types + query(params) + preset CRUD client"
```

---

### Task 10: QueryPage 调参面板 + 预设下拉 + 另存为

**Files:**
- Modify: `web/src/pages/QueryPage.tsx`, `web/src/pages/QueryPage.test.tsx`
- Test: 扩展 `QueryPage.test.tsx`

**Interfaces:**
- Consumes: `query(..., params?)`、`listQueryPresets/createQueryPreset`(Task 9)。

- [ ] **Step 1: 写失败测试**

在 `web/src/pages/QueryPage.test.tsx` 追加(沿用该文件既有的 msw `server` + `KbContext.Provider` + `fireEvent` 模式;`parseSse` 已被 vi.mock):

```tsx
test("tuning panel is collapsed by default and opens on click", async () => {
  render(<KbContext.Provider value={kbCtx}><QueryPage /></KbContext.Provider>);
  expect(screen.queryByLabelText("community_level")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /调参/ }));
  expect(await screen.findByLabelText("community_level")).toBeInTheDocument();
});

test("top_k is hidden for global method", async () => {
  render(<KbContext.Provider value={kbCtx}><QueryPage /></KbContext.Provider>);
  fireEvent.click(screen.getByRole("button", { name: /调参/ }));
  await screen.findByLabelText("community_level");
  fireEvent.click(screen.getByText("global").closest("button")!);
  expect(screen.queryByLabelText("top_k")).not.toBeInTheDocument();
});

test("selecting a preset fills the knobs", async () => {
  server.use(
    http.get("/query-presets", () =>
      HttpResponse.json([
        { id: 9, name: "详尽调研", description: "", method: "global",
          community_level: 1, response_type: "multiple paragraphs",
          temperature: 0.3, is_builtin: true },
      ]),
    ),
  );
  render(<KbContext.Provider value={kbCtx}><QueryPage /></KbContext.Provider>);
  fireEvent.click(screen.getByRole("button", { name: /调参/ }));
  const select = await screen.findByLabelText("预设");
  fireEvent.change(select, { target: { value: "详尽调研" } });
  await waitFor(() =>
    expect((screen.getByLabelText("community_level") as HTMLInputElement).value).toBe("1"),
  );
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/QueryPage.test.tsx`
Expected: FAIL — 调参面板不存在。

- [ ] **Step 3: 改 `QueryPage.tsx`**

顶部 import 加:

```tsx
import { useEffect, useMemo, useState } from "react";
import { query as apiQuery, listQueryPresets, createQueryPreset } from "../api/client";
import type { QueryParams, QueryPreset } from "../api/types";
```

在组件内(`result` state 附近)加调参状态 + 预设加载:

```tsx
  const [showTune, setShowTune] = useState(false);
  const [presets, setPresets] = useState<QueryPreset[]>([]);
  const [cl, setCl] = useState("");
  const [rt, setRt] = useState("");
  const [topK, setTopK] = useState("");
  const [temp, setTemp] = useState("");
  const [sysPrompt, setSysPrompt] = useState("");

  useEffect(() => {
    listQueryPresets().then(setPresets).catch(() => {});
  }, []);

  const params: QueryParams | undefined = useMemo(() => {
    const p: QueryParams = {};
    if (cl.trim()) p.community_level = Number(cl);
    if (rt.trim()) p.response_type = rt;
    if (topK.trim()) p.top_k = Number(topK);
    if (temp.trim()) p.temperature = Number(temp);
    if (sysPrompt.trim()) p.system_prompt = sysPrompt;
    return Object.keys(p).length ? p : undefined;
  }, [cl, rt, topK, temp, sysPrompt]);

  const applyPreset = (p: QueryPreset | undefined) => {
    if (!p) return;
    setMethod(p.method);
    setCl(p.community_level != null ? String(p.community_level) : "");
    setRt(p.response_type ?? "");
    setTopK(p.top_k != null ? String(p.top_k) : "");
    setTemp(p.temperature != null ? String(p.temperature) : "");
    setSysPrompt(p.system_prompt ?? "");
  };

  const savePreset = async () => {
    const name = window.prompt("预设名称");
    if (!name) return;
    await createQueryPreset({
      name, description: "", method,
      community_level: cl ? Number(cl) : null,
      response_type: rt || null,
      top_k: topK ? Number(topK) : null,
      temperature: temp ? Number(temp) : null,
      system_prompt: sysPrompt || null,
    });
    setPresets(await listQueryPresets());
  };
```

`ask` 里把 `apiQuery(kbId, method, q)` 改为 `apiQuery(kbId, method, q, params)`。

在 method 四宫格 `<div className="grid ...">` 与 `<textarea>` 之间插入调参面板:

```tsx
          <div>
            <button type="button" className="text-[13px] text-brand hover:underline"
              onClick={() => setShowTune((v) => !v)}>
              {showTune ? "隐藏调参" : "调参 / 预设"}
            </button>
            {showTune && (
              <div className="mt-3 space-y-3 rounded-xl border border-line bg-surface-2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <label className="text-[12px] text-muted">预设</label>
                  <select className="select max-w-[220px]" defaultValue="" aria-label="预设"
                    onChange={(e) => applyPreset(presets.find((p) => p.name === e.target.value))}>
                    <option value="" disabled>选择预设…</option>
                    {presets.map((p) => (
                      <option key={p.id} value={p.name}>{p.name}{p.is_builtin ? "（内置）" : ""}</option>
                    ))}
                  </select>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={savePreset}>另存为预设</button>
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <label className="text-[12px] text-muted">community_level
                    <input className="input mt-1" type="number" min={0} max={4} value={cl}
                      aria-label="community_level"
                      onChange={(e) => setCl(e.target.value)} placeholder="留空=2" />
                  </label>
                  <label className="text-[12px] text-muted">response_type
                    <select className="select mt-1" aria-label="response_type" value={rt}
                      onChange={(e) => setRt(e.target.value)}>
                      <option value="">留空=默认</option>
                      <option value="multiple paragraphs">多段</option>
                      <option value="single paragraph">单段</option>
                      <option value="bullet points">要点</option>
                    </select>
                  </label>
                  {(method === "local" || method === "basic") && (
                    <label className="text-[12px] text-muted">top_k
                      <input className="input mt-1" type="number" min={1} value={topK}
                        aria-label="top_k" onChange={(e) => setTopK(e.target.value)} placeholder="留空=默认" />
                    </label>
                  )}
                  <label className="text-[12px] text-muted">temperature
                    <input className="input mt-1" type="number" step="0.05" min={0} max={1} value={temp}
                      aria-label="temperature" onChange={(e) => setTemp(e.target.value)} placeholder="留空=默认" />
                  </label>
                </div>
                <label className="block text-[12px] text-muted">system_prompt（覆盖当前 method 主回答 prompt）
                  <textarea className="textarea mt-1 h-20 font-mono text-[12px]" value={sysPrompt}
                    aria-label="system_prompt" onChange={(e) => setSysPrompt(e.target.value)}
                    placeholder="留空=用 KB / graphrag 默认" />
                </label>
              </div>
            )}
          </div>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/QueryPage.test.tsx`
Expected: PASS。

- [ ] **Step 5: build + 全量前端测试 + 提交**

```bash
cd web && npm run build && npm test
cd ..
git add web/src/pages/QueryPage.tsx web/src/pages/QueryPage.test.tsx
git commit -m "feat(web): QueryPage tuning panel + preset picker + save-as"
```

---

### Task 11: 检索预设管理页 + 路由 + 侧栏

**Files:**
- Create: `web/src/pages/QueryPresetsPage.tsx`, `web/src/pages/QueryPresetsPage.test.tsx`
- Modify: `web/src/App.tsx`, `web/src/lib/nav.ts`

**Interfaces:**
- Consumes: 预设 CRUD client(Task 9)。

- [ ] **Step 1: 写失败测试**

```tsx
// web/src/pages/QueryPresetsPage.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import QueryPresetsPage from "./QueryPresetsPage";

const server = setupServer(
  http.get("/query-presets", () =>
    HttpResponse.json([
      { id: 1, name: "默认", description: "", method: "local", is_builtin: true },
    ]),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists built-in presets with no delete control", async () => {
  render(<QueryPresetsPage />);
  expect(await screen.findByText("默认")).toBeInTheDocument();
  // only builtin row present -> no 删除 button (title="删除") rendered
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument(),
  );
});

test("renders the create form", async () => {
  render(<QueryPresetsPage />);
  expect(await screen.findByPlaceholderText("名称")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /新建/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/QueryPresetsPage.test.tsx`
Expected: FAIL — 文件不存在。

- [ ] **Step 3: 写 `QueryPresetsPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { listQueryPresets, createQueryPreset, updateQueryPreset, deleteQueryPreset } from "../api/client";
import type { QueryPreset } from "../api/types";
import { Card, CardHeader, Button, Spinner, EmptyState } from "../components/ui";
import { IconSearch, IconPlus, IconTrash } from "../components/icons";

/** 检索预设:全局跨 KB 的查询配置库;内置只读。 */
export default function QueryPresetsPage() {
  const [items, setItems] = useState<QueryPreset[] | null>(null);
  const reload = () => listQueryPresets().then(setItems).catch(() => setItems([]));
  useEffect(() => { reload(); }, []);

  if (items === null) return <Spinner />;
  const blank: Omit<QueryPreset, "id" | "is_builtin"> = {
    name: "", description: "", method: "local",
    community_level: null, response_type: null, top_k: null, temperature: null, system_prompt: null,
  };
  const [draft, setDraft] = useState(blank);
  const create = async () => {
    if (!draft.name.trim()) return;
    await createQueryPreset(draft);
    setDraft(blank);
    reload();
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title="检索预设" subtitle="查询配置库 · 内置只读 · 跨知识库复用" icon={<IconSearch width={18} height={18} />} />
        <div className="mt-4 overflow-x-auto">
          {items.length === 0 ? (
            <EmptyState icon={<IconSearch />} title="还没有预设" hint="在下方新建,或在检索页「另存为预设」。" />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-[12px] text-muted">
                <tr><th className="py-2">名称</th><th>method</th><th>community_level</th><th>response_type</th><th>top_k</th><th>temperature</th><th></th></tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id} className="border-t border-line">
                    <td className="py-2 font-medium text-ink">{p.name}{p.is_builtin && <span className="ml-1 text-[10px] text-muted">内置</span>}</td>
                    <td className="font-mono text-[12px]">{p.method}</td>
                    <td>{p.community_level ?? "—"}</td>
                    <td>{p.response_type ?? "—"}</td>
                    <td>{p.top_k ?? "—"}</td>
                    <td>{p.temperature ?? "—"}</td>
                    <td>{!p.is_builtin && (
                      <button className="text-muted hover:text-danger" title="删除"
                        onClick={async () => { await deleteQueryPreset(p.id); reload(); }}>
                        <IconTrash width={14} height={14} />
                      </button>
                    )}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="新建预设" icon={<IconPlus width={18} height={18} />} />
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
          <input className="input" placeholder="名称" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <select className="select" value={draft.method} onChange={(e) => setDraft({ ...draft, method: e.target.value })}>
            <option value="local">local</option><option value="global">global</option>
            <option value="drift">drift</option><option value="basic">basic</option>
          </select>
          <input className="input" type="number" placeholder="community_level（可空）" onChange={(e) => setDraft({ ...draft, community_level: e.target.value ? Number(e.target.value) : null })} />
          <select className="select" onChange={(e) => setDraft({ ...draft, response_type: e.target.value || null })}>
            <option value="">response_type：默认</option>
            <option value="multiple paragraphs">多段</option>
            <option value="single paragraph">单段</option>
            <option value="bullet points">要点</option>
          </select>
          <input className="input" type="number" placeholder="top_k（可空）" onChange={(e) => setDraft({ ...draft, top_k: e.target.value ? Number(e.target.value) : null })} />
          <input className="input" type="number" step="0.05" placeholder="temperature（可空）" onChange={(e) => setDraft({ ...draft, temperature: e.target.value ? Number(e.target.value) : null })} />
        </div>
        <div className="mt-3"><Button variant="primary" onClick={create}><IconPlus width={16} height={16} />新建</Button></div>
      </Card>
    </div>
  );
}
```

> 若 `EmptyState`/`IconSearch` 的 props 与现有签名不符,按 `components/ui.tsx`、`components/icons.tsx` 的实际导出微调(`EmptyState` 需要 `icon/title/hint`;`IconSearch` 默认可无 props)。

- [ ] **Step 4: 路由 + 侧栏**

`App.tsx`:顶部 import 加 `import QueryPresetsPage from "./pages/QueryPresetsPage";`,在「检索与问答」相关路由(`/chat` 那行)附近加:

```tsx
<Route path="/query-presets" element={<QueryPresetsPage />} />
```

`lib/nav.ts`:「检索与问答」组 items 加(用已有 `IconSearch` 或新图标):

```ts
{ to: "/query-presets", label: "检索预设", icon: IconSearch },
```

- [ ] **Step 5: 跑测试 + build + 全量前端测试 + 提交**

```bash
cd web && npx vitest run src/pages/QueryPresetsPage.test.tsx && npm run build && npm test
cd ..
git add web/src/pages/QueryPresetsPage.tsx web/src/pages/QueryPresetsPage.test.tsx web/src/App.tsx web/src/lib/nav.ts
git commit -m "feat(web): QueryPresetsPage (CRUD, built-ins read-only) + nav"
```

---

### Task 12: 全量验证

**Files:** 无(只跑命令)。

- [ ] **Step 1: 后端全量 + ruff**

```bash
uv run pytest -q
uv run ruff check .
```
Expected: 全绿;新增测试(`test_query_params`/`test_resolve_params`/`test_apply_params`/`test_query_params_route`/`test_query_presets_repo`/`test_query_presets_api` + 扩展的 conversation 测试)通过;既有不回归。

- [ ] **Step 2: 前端全量 + build**

```bash
cd web && npm test && npm run build
```
Expected: 全绿;`dist/` 生成。

- [ ] **Step 3: 迁移幂等**

```bash
uv run alembic upgrade head
```
Expected: 无错(表已存在则 no-op;seed 幂等)。

- [ ] **Step 4(可选,手动冒烟):** 真实 LLM 下,QueryPage 切 `community_level` 0↔3、切 response_type 多段↔要点、应用「详尽调研」预设,确认答案粗细/格式变化;聊天页用设了 `query_defaults.temperature` 的 KB 提问,确认走默认值、不回归。

- [ ] **Step 5: 更新路线图记忆 + 提交收尾**

完成后在 memory `qa-experience-roadmap.md` 把 A3 标 DONE(留 commit/验证记录占位),提交。

---

## Self-Review(plan 自检,已在撰写时完成)

1. **Spec 覆盖**:四旋钮(Task 1/3 + 路由 4/5)✓;三层解析(Task 2)✓;预设表+迁移+CRUD(Task 6/7)✓;KB 默认值段(Task 8)✓;QueryPage 调参+预设(Task 10)✓;预设管理页(Task 11)✓;聊天走 KB 默认(Task 5)✓;MCP 不变(Global Constraints + 无 mcp/ 改动)✓;方法适用性(top_k 仅 local/basic 显隐 — Task 10;global/drift top_k 在 `_apply_params` 忽略 — Task 3)✓;prompt 映射(global→reduce — Task 3 `_PRIMARY_PROMPT_SLOT`)✓。
2. **占位扫描**:Task 5/9/10/11 的测试含「沿用既有 mock 模式」指引——因这些测试文件已有既有 mock 套路,实施时打开文件照写即可(非占空,是引用既有 helper);其余每步均有完整代码。
3. **类型一致性**:`QueryParams`(engine.py)↔ `QueryParamsIn`(models.py,字段同名)↔ `resolve_query_params` ↔ 前端 `QueryParams`(types.ts,snake_case 对齐后端)`_FIELDS` 五字段一致;`QueryPreset` ORM ↔ `QueryPresetOut` ↔ 前端 `QueryPreset` 字段一致;`_PRIMARY_PROMPT_SLOT` 与 spec §4.9 一致。
