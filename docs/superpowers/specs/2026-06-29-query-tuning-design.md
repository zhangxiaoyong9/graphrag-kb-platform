# A3 查询调参 UI — 查询时旋钮(社区层级 / 结果数 / 温度 / 响应风格)+ 检索预设库

> 状态:已 brainstorm、设计已确认;待写实现计划。
> 日期:2026-06-29。
> 范围:后端(`QueryParams` 透传 + 预设表 + CRUD 端点)+ 前端(QueryPage 调参面板 + 预设管理页 + KB 默认值段)。聊天链路与 MCP 不在本次范围。

## 1. 背景

Q&A 体验路线图 A1(多轮对话)、A2(流式回答)已合并。A3 的价值是把检索质量交到用户手里:今天 `POST /kbs/{id}/query` 的请求体只有 `{method, query}`(**零调参**),而 graphrag 引擎里两个高价值旋钮被**硬编码**——

- `community_level = 2`(`graphrag_engine.py:350`)→ 喂给 `read_indexer_reports/entities`,决定答案的粗细粒度;
- `response_type = "multiple paragraphs"`(`graphrag_engine.py:351`)→ 原文进 graphrag 回答 prompt。

此外 graphrag 还暴露 `top_k_entities`/`top_k_relationships`(local)、`top_k`(basic)、模型 `temperature` 等,目前都走 config 默认值,无法调。查询侧系统提示词(`query_prompts.{local_system,global_map,global_reduce,basic_system}`)虽已端到端打通(KB 表单写入 → `graphrag_engine.py:353-359` 读取),但只能整库一套、无法按次切换,也没有"保存我喜欢的配置"的机制。

## 2. 目标 / 非目标

**目标**

- 四个查询时旋钮可调:`community_level`、`response_type`、`top_k`、`temperature`。
- 三层取值:`硬编码基线 ← KB 设置默认值 ← 按次覆盖`。任一层缺省=用下层,完全不配=今天行为不变。
- **检索预设库(preset)**:每条 = 名字 + 整套 `{method, community_level, response_type, top_k, temperature, system_prompt}`;全局(跨 KB)存储,内置 2-3 条只读预设;可一键应用、可"另存为"。
- QueryPage 提供按次调参面板 + 预设下拉;KB 设置提供"检索默认值"段。

**非目标**

- 不改索引侧 prompt(另一 spec `2026-06-26-kb-prompt-config-design.md`)。
- 聊天(ChatPage)**不加**按次控件 —— 对话继承 KB 默认值;"每段对话一套预设"留后续。
- MCP 不变:`KbApiClient.query()` 不发 `params`,用 KB 默认值;SSE 聚合契约不变。
- 不接 `prompt-tune`、不改 settings 解析/存储格式(仍是 `settings_json`,新增字段而已)。

## 3. 决策(brainstorm 已定)

1. **范围 = 四旋钮 + 预设库**(用户选 C)。
2. **分层 = KB 默认值 + 按次覆盖**(用户选 C):`硬编码基线 ← KB 设置 ← 按次 QueryParams`。
3. **预设库 = 完整 preset + 全局 DB 表 + 内置预设**(用户按推荐选 B)。
4. **实现路线 = 扩展 `QueryEngine` Protocol 加 `QueryParams`**;否决"把旋钮烤进 `model_config`"——它混淆"模型配置"与"查询调参"、`FakeQueryEngine`/聊天链路看不到、不好测。

## 4. 设计

### 4.1 `QueryParams` 数据类(`kb_platform/query/engine.py`)

```python
@dataclass
class QueryParams:
    community_level: int | None = None
    response_type:   str | None = None
    top_k:           int | None = None
    temperature:     float | None = None
    system_prompt:   str | None = None   # 按次覆盖"当前 method 的主回答 prompt"
```

全可选,`None` = 用下层。`FakeQueryEngine.search`/`stream_search` 接收 `params` 形参但忽略其内容(测试可断言"收到了")。

`QueryEngine` Protocol 两个方法都加 `params: QueryParams | None = None` 尾参;`FakeQueryEngine` 与 `GraphRagQueryEngine` 同步。

### 4.2 方法适用性(不适用的 method 静默忽略,不报错)

| 旋钮 | local | global | drift | basic |
|---|---|---|---|---|
| community_level | ✅ | ✅ | ✅ | ✖ |
| response_type | ✅ | ✅ | ✅ | ✅ |
| top_k | ✅(entities/relationships) | ✖ | ✖ | ✅(text_unit) |
| temperature | ✅ | ✅ | ✅ | ✅ |

### 4.3 三层解析(`resolve_query_params`)

新增 `kb_platform/query/params.py`(或并入 `engine.py`):

```python
def resolve_query_params(kb_settings: dict, per_query: QueryParams | None) -> QueryParams:
    """硬编码基线 ← KB 设置默认值 ← 按次覆盖。"""
    kbq = (kb_settings.get("query_defaults") or {}) if kb_settings else {}
    return QueryParams(
        community_level=_(per_query, kbq, "community_level"),
        response_type=  _(per_query, kbq, "response_type"),
        top_k=          _(per_query, kbq, "top_k"),
        temperature=    _(per_query, kbq, "temperature"),
        system_prompt=  _(per_query, kbq, "system_prompt"),
    )  # _ = per_query 字段 if not None else kbq 字段(均缺省→None→引擎用硬编码/默认)
```

`system_prompt` 的三层:按次 ← KB `query_prompts.<method>_system`(已有)← graphrag 默认。即"按次 system_prompt"覆盖既有 KB 提示词层。

### 4.4 `GraphRagQueryEngine` 改动

- `_build_engine(self, method, root, params: QueryParams | None)`:
  - `community_level = params.community_level if params and params.community_level is not None else 2`
  - `response_type = params.response_type if params and params.response_type else "multiple paragraphs"`
  - `top_k` / `temperature`:见 §4.5(在 resolved config 上注入)。
  - prompt 覆盖:在既有 `query_prompts` 读取处再叠一层 `params.system_prompt`(对当前 method 的主回答 prompt 槽生效)。
- `search` / `stream_search` 接收 `params` 并透传给 `_build_engine`。

### 4.5 `temperature` / `top_k` 的 config 注入(已对 graphrag v3.1.0 源码核实)

这两个在 graphrag 里**不是工厂直参**,而是 config 字段。核实结论:

- **top_k**:`config.local_search.top_k_entities` + `config.local_search.top_k_relationships`(local,两个都设);`config.basic_search.k`(basic —— 字段名是 `k` 不是 `top_k`)。global/drift 不适用。
- **temperature**:local/global/basic 经模型 `call_args` 生效 —— 工厂把 `model_settings.call_args` 作为 `model_params` 传给 engine,engine 在补全时 `**self.model_params`。故注入 `config.completion_models[<method 的 completion_model_id>].call_args["temperature"]`。drift 走自己的字段:`config.drift_search.reduce_temperature` 与 `config.drift_search.local_search_temperature`(两个都设)。
- `ModelConfig.call_args` 默认 `{}`(`default_factory`);graphrag 自身就在改 config 对象(`vector_store.db_uri`),pydantic v2 BaseModel 默认可变,故**就地改字段安全**。

做法:在 `_build_engine` 里 `config = self._resolve_config(root=root)` 之后,**按 `params` 就地改上述字段**再建 engine。生产路径每次请求新建 config、无共享状态。

### 4.6 请求模型(`kb_platform/api/models.py`)

```python
class QueryParamsIn(BaseModel):
    community_level: int | None = None
    response_type:   str | None = None
    top_k:           int | None = None
    temperature:     float | None = None
    system_prompt:   str | None = None

class QueryRequest(BaseModel):
    method: str
    query: str
    params: QueryParamsIn | None = None   # 新增
```

`MessageSend` **A3 不动**(聊天走 KB 默认值)。

### 4.7 路由(`routes_query.py`、`routes_conversations.py`)

`routes_query.py`:拿到 KB settings 后构造按次参数 `qp = QueryParams(**payload.params.model_dump()) if payload.params else None`,再 `resolved = resolve_query_params(settings, qp)`,把 `resolved` 传 `local_engine.stream_search(..., params=resolved)`。`routes_conversations.py` 聊天路径 `resolve_query_params(settings, None)`(纯 KB 默认值),透传给 `ConversationService.send_streaming` → engine。

### 4.8 预设库(全局 DB 表 + Alembic 0007)

```python
# kb_platform/db/models.py
class QueryPreset(Base):
    id            = Column(Integer, primary_key=True)
    name          = Column(Text, unique=True, nullable=False)
    description   = Column(Text, default="")
    method        = Column(Text, nullable=False)          # local/global/drift/basic
    community_level = Column(Integer, nullable=True)
    response_type   = Column(Text, nullable=True)
    top_k           = Column(Integer, nullable=True)
    temperature     = Column(Float, nullable=True)
    system_prompt   = Column(Text, nullable=True)
    is_builtin      = Column(Boolean, default=False, nullable=False)
    created_at      = Column(DateTime, server_default=func.now())
    updated_at      = Column(DateTime, onupdate=func.now())
```

迁移 `alembic/versions/0007_query_presets.py`:建表 + seed 内置预设(只读,`is_builtin=True`)。

**内置 3 条**:

| name | method | community_level | response_type | temperature | 说明 |
|---|---|---|---|---|---|
| 默认 | local | NULL | NULL | NULL | 全 NULL=今天行为,命名基线 |
| 简洁要点 | local | NULL | 单段 | ≈0.2 | 短答、低温 |
| 详尽调研 | global | 1 | 多段 | ≈0.3 | 粗社区、全量 map-reduce |

Repository 加 `list_query_presets / get_query_preset / create_query_preset / update_query_preset / delete_query_preset`。

### 4.9 预设 CRUD API(新 `routes_presets.py`)

- `GET /query-presets` → 全部(内置 + 自定义),按 `is_builtin`、`name` 排序。
- `POST /query-presets`(body = 除 id/is_builtin 外的字段)→ 新建自定义。
- `PATCH /query-presets/{id}` → 改自定义;对 `is_builtin` 返回 403。
- `DELETE /query-presets/{id}` → 删自定义;对 `is_builtin` 返回 403。

**应用语义**(纯前端行为,无专门端点):点预设 = 把 `{method + 全部旋钮 + system_prompt}` 一次性灌进 QueryPage 表单状态;灌完用户仍可逐项微调;「另存为预设」= POST 当前表单值。

**prompt 映射**:preset 的单个 `system_prompt` 按 **preset 自己的 `method`** 映射到对应 prompt 槽;global 的单 prompt 填 **reduce**(最终回答塑形那步),map 维持 KB 级。这与 §4.4 的"按次 system_prompt 覆盖当前 method 主回答 prompt"一致。

### 4.10 前端

- **`web/src/api/types.ts` + `client.ts`**:加 `QueryParams`/`QueryPreset` 类型;`query(kbId, method, q, params?)` 带可选 params;`listQueryPresets/createQueryPreset/updateQueryPreset/deleteQueryPreset`。
- **`web/src/pages/QueryPage.tsx`**:加可折叠「调参」面板(默认收起)——预设下拉(选中→灌值)、community_level(数字/下拉)、response_type(下拉:多段/单段/要点 —— 值取 graphrag 字符串 `"multiple paragraphs"`/`"single paragraph"`/`"bullet points"`,下拉仅作中文显示)、top_k(数字,按 method 显隐)、temperature(滑块或数字,0–1)、`system_prompt`(textarea)、「另存为预设」按钮。method 四宫格选择器保留在最上不变。发送时把当前调参状态作为 `params` 入请求体。
- **`web/src/pages/QueryPresetsPage.tsx`(新)**:侧栏「检索与问答」下新增「检索预设」——列表 + 增删改表单;内置条目标只读锁。
- **`web/src/lib/kb-settings.ts` + `KbForm.tsx`**:新增「检索默认值」段(4 个可选旋钮,留空=硬编码);`buildSettings` 仅在非默认时写 `query_defaults`;`parseSettings` 回填。既有 `queryPrompts` 段不动。
- **`web/src/pages/ChatPage.tsx`**:**不改**,继承 KB 默认值。

### 4.11 数据流(端到端)

```
QueryPage 调参面板(或预设灌值)→ QueryRequest.params
  → routes_query.resolve_query_params(kb_settings, params)   # KB默认 ← 按次
  → GraphRagQueryEngine.stream_search(..., params=resolved)
  → _build_engine:community_level/response_type 直读;
    top_k/temperature 注入 resolved config;system_prompt 覆盖 prompt 槽
  → graphrag 工厂 → SSE(meta/delta/done)
```

预设:DB 表 ↔ CRUD 端点 ↔ 前端;「应用」只在前端把字段灌进表单状态。

## 5. 测试

**后端**
- `resolve_query_params` 三层顺序:全 None→各字段 None(引擎走默认);KB 有值、per_query 无→用 KB;两者都有→用 per_query。
- `GraphRagQueryEngine._build_engine`:monkeypatch 四个工厂,捕获入参,断言 `community_level`/`response_type` 用 `params` 覆盖硬编码;`params=None` 时仍是 `2`/`"multiple paragraphs"`(回归)。
- top_k/temperature 注入:`params.top_k` → 断言 `config.local_search.top_k_entities`/`top_k_relationships`(local)或 `config.basic_search.k`(basic)被改;`params.temperature` → 断言 `config.completion_models[<id>].call_args["temperature"]`(local/global/basic)或 `config.drift_search.reduce_temperature`+`local_search_temperature`(drift)被改。
- `QueryParams` 透传到 `FakeQueryEngine`(断言收到对象)。
- 预设 CRUD:`GET` 含内置;`POST` 建自定义;`PATCH`/`DELETE` 自定义 OK、对内置 403;`name` 唯一冲突 422/409。
- 路由:`QueryRequest.params` 到达 engine(SSE 路径用 Fake engine 断言)。
- 既有 ~301 后端测试不回归;ruff 干净。

**前端**
- `buildSettings`:`query_defaults` 仅在非默认时 emit;`parseSettings` 回填。
- `query(kbId, method, q, params)` 在有参数时带 body;无参数时不带(回归)。
- QueryPage:预设下拉选中→表单字段灌入;折叠面板默认收起;按 method 显隐 top_k。
- QueryPresetsPage:列表渲染;内置只读;新建/编辑/删除调对应 client。
- 既有 ~80 前端测试不回归;`npm run build` 干净。

## 6. 风险与对策

1. **temperature/top_k 注入点** → 已对 graphrag v3.1.0 源码核实(§4.5):`local_search.top_k_entities/relationships`、`basic_search.k`、`completion_models[<id>].call_args["temperature"]`、`drift_search.reduce_temperature`/`local_search_temperature`。graphrag 自身就地改 config,故安全。
2. **community_level 超出索引实际层级** → graphrag 返回空上下文。对策:前端下拉只给 0–4 合理档;后端不强制(保持 graphrag 原生行为),文档提示"层级过高可能无结果"。
3. **per-query 改 config 的线程/并发安全** → 生产每次请求 `_resolve_config` 新建 config,无共享;测试里也按此构造,不复用。
4. **预设 system_prompt 单值 vs global 双 prompt(map+reduce)** → 已定:单值填 reduce,map 维持 KB 级;spec 与 UI 文案均说明。
5. **内置预设随 graphrag 默认漂移** → 内置条目字段写死在迁移 seed,不读 graphrag;「默认」预设全 NULL 永远跟随后端硬编码基线。
6. **范围蔓延(聊天按次调参)** → 非目标已明确排除;ChatPage 不改。

## 7. 验收(Done)

- QueryPage「调参」面板:四旋钮可调 + 预设下拉(含 3 条内置)+「另存为预设」;按 method 显隐 top_k;留空=默认。
- KB 设置「检索默认值」段:四旋钮可选,留空=不变。
- 检索预设页:列表 + 增删改,内置只读。
- 四旋钮经 `params` → resolved config → graphrag,实际改变检索行为(community_level/response_type/top_k/temperature 单测必过;可选手验:切 community_level 答案粗细变化)。
- 预设 CRUD 端点 + 内置 seed + `is_builtin` 保护。
- 聊天仍走 KB 默认值、行为不回归;MCP 契约不变。
- 既有测试全绿;ruff / `npm run build` 干净。

## 8. 改动清单

**后端**
- `kb_platform/query/engine.py`:`QueryParams` dataclass;Protocol + `FakeQueryEngine` 加 `params` 形参。
- `kb_platform/query/params.py`(新):`resolve_query_params`。
- `kb_platform/query/graphrag_engine.py`:`_build_engine`/`search`/`stream_search` 接 `params`;community_level/response_type 读 params;top_k/temperature 注入 config;system_prompt 叠层覆盖。
- `kb_platform/api/models.py`:`QueryParamsIn`;`QueryRequest.params`。
- `kb_platform/api/routes_query.py`、`routes_conversations.py`:解析 + 透传。
- `kb_platform/api/routes_presets.py`(新)+ `app.py` 注册。
- `kb_platform/db/models.py`:`QueryPreset`;`repository.py`:CRUD 方法。
- `alembic/versions/0007_query_presets.py`(新):建表 + seed 内置。

**前端**
- `web/src/api/types.ts`、`client.ts`:`QueryParams`/`QueryPreset` 类型 + 预设 CRUD + `query(..., params?)`。
- `web/src/pages/QueryPage.tsx`:调参面板 + 预设下拉 + 另存为。
- `web/src/pages/QueryPresetsPage.tsx`(新)+ `App.tsx`/`lib/nav.ts` 路由 + 侧栏项。
- `web/src/lib/kb-settings.ts`、`components/KbForm.tsx`:「检索默认值」段。
- `web/src/pages/ChatPage.tsx`:不改。

**测试**
- 后端:`tests/test_query_params.py`(解析 + 透传 + 注入)、`tests/test_query_presets.py`(CRUD + 内置)、扩展 query/engine 与 routes 既有测试。
- 前端:`kb-settings.test.ts`(query_defaults emit)、`QueryPage.test.tsx`(面板 + 预设灌值)、`QueryPresetsPage.test.tsx`(新)。
