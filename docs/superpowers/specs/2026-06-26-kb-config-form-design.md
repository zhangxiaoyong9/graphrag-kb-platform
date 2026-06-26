# KB 配置表单化 — 所有可生效的 GraphRAG 配置前端可配（不再手写 JSON）

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：前端 `KbForm` 重写 + 后端把当前硬编码的流水线参数接到 settings。

## 1. 背景

现状：建 KB 用 `KbForm`，配置靠一个 `settings_yaml` 手写 JSON 文本框。用户要求「所有配置前端表单可配，不要自己写 JSON」。

**核查结论（已确认 graphrag 源码 + 平台代码）：** GraphRagConfig 有 ~25 个段、数百字段，但平台**只兑现一小部分**：
- **已生效（bucket 1）**：`llm.*`、`embedding.*`、`community_reports.structured_output`。
- **平台在用但当前硬编码（bucket 2）**：chunk size=1200 / overlap=100 / encoding=`cl100k_base`、cluster `max_cluster_size=10`、`entity_types=DEFAULT_ENTITY_TYPES`、`max_gleanings=0`、summarize `max_summary_length=500` / `max_input_tokens=32000`、report `max_report_length=2000`。
- **平台忽略（bucket 3）**：`input` / `*_storage` / `table_provider` / `cache` / `reporting` / `snapshots` / `workflows` 等（平台自管 SQLite/parquet/LanceDB + 独立 orchestrator）——表单暴露这些会是「假配置」，不做。

本设计覆盖 **bucket 1 + bucket 2**：表单覆盖所有平台**实际兑现**的配置；bucket 2 的硬编码参数先接到 settings（后端改造），再上表单。bucket 3 不暴露（高级覆盖框是逃生口）。

## 2. 目标 / 非目标

**目标**
- 建库全部用结构化表单，字段化覆盖：LLM、Embedding、Chunking、Extract Graph、Summarize、Community Reports、Clustering。
- 当前硬编码的流水线参数（chunk/cluster/extract/summarize/report）改为从 settings 读取，默认值=现状。
- 保留一个折叠的「高级：原始 settings_yaml 覆盖」框作为逃生口（默认空；填了就覆盖表单）。
- 表单默认值预填；提交时**等于默认值的字段省略**（payload 干净，graphrag 默认值生效）。

**非目标**
- 不暴露 bucket 3（平台忽略的段）。
- 不改 settings 的存储/解析（仍是 settings_json 字符串；`_parse_settings` 不动）。
- 不改查询侧（`_resolve_config` 直接 `GraphRagConfig.model_validate(values)`，原生 key 自动透传）。
- 不做表单与原始 JSON 的双向实时同步（高级框是「覆盖」语义，非双向）。

## 3. 决策（brainstorm 已定）

- 范围：bucket 1 + 2，drop bucket 3。
- 默认值集中在一个 `DEFAULTS` 常量里（同时作表单初值 + buildSettings 的「省略默认」比对基准），DRY。
- 提交时省略等于默认的字段；llm/embedding 按字段非空取舍。
- 高级覆盖框：非空则 `JSON.parse` 后整体替换表单结果。

## 4. 设计

### 4.1 后端：把硬编码参数接到 settings

`kb_platform/graph/graphrag_adapter.py`：

- `build_default_adapter` 增加关键字参数（默认=现状硬编码）：
  ```python
  def build_default_adapter(
      *, data_root, model_config, embed_model_config=None,
      chunk_size=1200, chunk_overlap=100, encoding_model="cl100k_base",
      max_cluster_size=10, entity_types=None, max_gleanings=0,
      summarize_max_length=500, summarize_max_input_tokens=32000,
      report_max_length=2000,
  ) -> GraphRagAdapter:
  ```
  - chunker 用 `ChunkingConfig(type=Tokens, encoding_model=encoding_model, size=chunk_size, overlap=chunk_overlap)`。
  - extractor 调用保留 `entity_types=entity_types or DEFAULT_ENTITY_TYPES`（已是 adapter 字段，call 时传），`max_gleanings` 透传 GraphExtractor。
  - `cluster_relationships` 用 `max_cluster_size`。
  - summarize factory 用 `max_summary_length=summarize_max_length, max_input_tokens=summarize_max_input_tokens`。
  - report factory 用 `max_report_length=report_max_length`。
  - 既有 `on_error=_raise_on_error`（上一阶段已加）不动。

- `build_adapter_from_settings` 从 settings 读取（graphrag 原生 key 路径，`.get` 容错）并透传：
  - `chunking` → `size/overlap/encoding_model`
  - `cluster_graph` → `max_cluster_size`
  - `extract_graph` → `entity_types`（list；若为 str 按逗号切）、`max_gleanings`
  - `summarize_descriptions` → `max_length→summarize_max_length`、`max_input_tokens`
  - `community_reports` → `max_length→report_max_length`（`structured_output` 已由 community_reports 策略读取，不改）

### 4.2 前端：表单 + DEFAULTS + buildSettings

新建 `web/src/components/kb-config-form.tsx`（KbForm 重写）+ `web/src/lib/kb-settings.ts`（DEFAULTS + buildSettings 纯函数）。

**`DEFAULTS` 常量**（`web/src/lib/kb-settings.ts`）：所有字段的默认值（llm/embedding 字段默认空串；chunk/extract/summarize/report/cluster 数值=后端现状默认）。

**`buildSettings(state)` → `object`**（纯函数，单测）：
- `llm`：收集非空字段（model_provider/model/api_base/api_key_env/api_key/api_version）→ 对象；全空则省略 `llm` 段。
- `embedding`：同上（表单有 enabled 开关；关闭则省略整段）。
- `chunking`/`cluster_graph`/`extract_graph`/`summarize_descriptions`/`community_reports`：每个字段「!== DEFAULTS 中的对应默认值」才写入；整段全默认则省略该段。
- `community_reports.structured_output` 是布尔，作为 community_reports 段的一个字段参与「省略默认」。
- 高级覆盖：若 `state.advancedOverride` 非空 → `JSON.parse`（失败抛错，表单显示）→ 返回该对象（整体覆盖）。

**表单 state**：扁平化（`name/method/minRatio/llm.*/embedding.*/chunking.*/.../advancedOverride`），每字段一个受控输入。

**表单分区**（Field + 输入，复用 `ui.tsx` 的 `Field/input/select`）：
1. 基础：name、method（standard/fast）、min success ratio
2. LLM 模型：provider、model、api_base、api_key_env、api_key、api_version
3. Embedding 模型：enabled 开关 + provider、model、api_base、api_key_env、api_key、api_version
4. 分块：size、overlap、encoding_model
5. 图谱抽取：entity_types（逗号文本）、max_gleanings
6. 描述摘要：max_length、max_input_tokens
7. 社区报告：structured_output（开关）、max_length
8. 聚类：max_cluster_size
9. 高级（折叠）：只读「生成预览」`<pre>{JSON.stringify(buildSettings(state), null, 2)}</pre>` + 可选 raw `settings_yaml` 覆盖文本框

**提交**：`settings_yaml = JSON.stringify(buildSettings(state))` → `createKb({name, method, settings_yaml, min_unit_success_ratio})`。高级覆盖非空时 advancedOverride 即 settings_yaml。

**KB 概要「模型配置」卡**（已有）：它读 `kb.settings.llm/embedding/community_reports`——表单产出的 key 路径与之兼容，无需改；如要展示更多（chunk/cluster 等），可作为后续小增强，本阶段不做。

### 4.3 数据流

表单 state → `buildSettings`（省略默认）→ JSON 对象 → `JSON.stringify` → `settings_yaml` → `POST /kbs` → 存 settings_json → worker `build_adapter_from_settings` 读各段 → 透传 `build_default_adapter`。查询侧 `_resolve_config` 把 settings 原样喂给 `GraphRagConfig.model_validate`，原生 key 自动生效。

## 5. 测试

- **前端**（`web/src/lib/kb-settings.test.ts` + 组件测试）：
  - `buildSettings`：全默认 → `{}`（或仅含必填）；改 chunk size → 出现 `chunking.size`；embedding 关闭 → 无 `embedding`；structured_output=false 且默认 true → 出现 `community_reports.structured_output=false`；advancedOverride 非空 → 返回解析结果（覆盖）；advancedOverride 非法 JSON → 抛错。
  - `KbForm`：渲染所有分区；填 LLM + 提交 → `createKb` 收到含 `llm` 的 settings_yaml（RTL，mock createKb）。
- **后端**：
  - `build_adapter_from_settings` 透传自定义 chunk/cluster/extract/summarize/report（monkeypatch `create_chunker`/extractor 构造，捕获 kwargs，断言自定义值到位）。
  - 现有行为不变：不传这些段时，`build_default_adapter` 用默认值（=现状）→ 既有 189 测试全绿。
- 既有 189 后端 + 24 前端测试全绿；ruff 干净；`npm run build` 干净。

## 6. 风险与对策

1. **省略默认的比对**：`DEFAULTS` 是唯一基准；buildSettings 与表单初值都用它，避免漂移。单测覆盖「默认省略 / 非默认保留」。
2. **entity_types 类型**：后端 list / 前端逗号文本；前端 buildSettings 切成 list，后端 `build_adapter_from_settings` 也容忍 str（按逗号切）双保险。
3. **高级覆盖非法 JSON**：前端解析失败 → 表单内联报错，不提交。
4. **bucket 2 既有默认=现状**：保证不传时行为不变（回归）。
5. **bucket 3 不暴露**：避免假配置；高级覆盖框是逃生口。

## 7. 验收（Done）

- 建库全程表单，无 JSON 文本框（默认隐藏在「高级」折叠里）。
- 改 chunk size / cluster size / entity_types / structured_output 等任一字段 → 生成的 settings_yaml 含该字段 → 索引/查询按新值行为生效（如 chunk_size 影响分块数）。
- 后端单测：自定义 bucket-2 参数透传到 adapter。
- 前端单测：buildSettings 各分支 + KbForm 提交 payload。
- 既有测试全绿；ruff/build 干净。

## 8. 改动清单

- 后端：`kb_platform/graph/graphrag_adapter.py`（`build_default_adapter` 加参；`build_adapter_from_settings` 读各段）。
- 前端：`web/src/components/KbForm.tsx`（重写为分区表单，或新建 `kb-config-form.tsx` 由 KbForm 引用）、`web/src/lib/kb-settings.ts`（DEFAULTS + buildSettings）、`web/src/lib/kb-settings.test.ts`。
- 测试：后端 `tests/test_build_adapter_settings.py`（或既有 `test_build_default_adapter_embed.py` 扩展）。
