# KB 提示词可配置 — 让 graphrag 的三大索引 prompt 可在前端覆盖（含查看默认值）

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：后端（新端点 + prompt 参数透传）+ 前端（Prompts 表单段 + 查看默认值）。

## 1. 背景

现状（已核实）：平台确实跑 graphrag 的真实抽取器（`GraphExtractor`/`SummarizeExtractor`/`CommunityReportsExtractor`）+ graphrag 默认英文 prompt（硬编码 import：`GRAPH_EXTRACTION_PROMPT`/`SUMMARIZE_PROMPT`/`COMMUNITY_REPORT_PROMPT`）。**prompt 不可配置** → 中文语料用英文默认 prompt，抽取/报告质量差（这解释了部分 "unable to answer" / 实体稀疏）。`graphrag prompt-tune` 正是 graphrag 官方适配语言/领域的途径，平台完全没接。

## 2. 目标 / 非目标

**目标**
- 三大索引 prompt（extract_graph / summarize_descriptions / community_reports）可在 KB settings 里覆盖；缺省=graphrag 默认（行为不变）。
- 前端表单加「提示词 Prompts」段：每个 prompt 一个可编辑 textarea（留空=用默认）+ 一个「查看 graphrag 默认」折叠，展示默认 prompt 全文（只读），让用户看到要覆盖的是什么。
- 新增后端端点返回 graphrag 默认 prompt，供前端展示。

**非目标**
- 不做「运行 prompt-tune」按钮（graphrag 在样本语料上调 prompt，是单独的更大特性，作为后续）。
- 不改查询侧 prompt（local/global/drift 的 map-reduce prompt 仍用 graphrag 默认）。
- 不改既有 settings 解析/存储（仍是 settings_json）。

## 3. 决策（brainstorm 已定）

- 可编辑 textarea **留空=用 graphrag 默认**（不预填，避免几大段英文淹没表单）；非空才写入 settings。
- 「查看 graphrag 默认」折叠：前端调新端点 `GET /prompts/defaults` 拿 graphrag 三大默认 prompt，只读 `<pre>` 展示。
- 后端 `build_default_adapter` 加 `extract_prompt`/`summarize_prompt`/`community_report_prompt`（None→graphrag 默认 import；非 None→覆盖）；`build_adapter_from_settings` 读 `extract_graph.prompt` 等。

## 4. 设计

### 4.1 后端：默认 prompt 端点

新增 `GET /prompts/defaults`（`kb_platform/api/routes_kbs.py` 或新 `routes_prompts.py`）：
```python
@router.get("/prompts/defaults")
def prompt_defaults() -> dict:
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT
    from graphrag.prompts.index.summarize_descriptions import SUMMARIZE_PROMPT
    from graphrag.prompts.index.community_report import COMMUNITY_REPORT_PROMPT
    return {
        "extract_graph": GRAPH_EXTRACTION_PROMPT,
        "summarize_descriptions": SUMMARIZE_PROMPT,
        "community_reports": COMMUNITY_REPORT_PROMPT,
    }
```
注册到 app。

### 4.2 后端：prompt 参数透传

`kb_platform/graph/graphrag_adapter.py`：
- `build_default_adapter` 加 keyword 形参 `extract_prompt: str | None = None`、`summarize_prompt: str | None = None`、`community_report_prompt: str | None = None`。
  - `GraphExtractor(model=completion, prompt=extract_prompt or GRAPH_EXTRACTION_PROMPT, max_gleanings=..., on_error=_raise_on_error)`。
  - `SummarizeExtractor(model=completion, max_summary_length=..., max_input_tokens=..., summarization_prompt=summarize_prompt or SUMMARIZE_PROMPT, on_error=_raise_on_error)`。
  - `CommunityReportsExtractor(model=completion, extraction_prompt=community_report_prompt or COMMUNITY_REPORT_PROMPT, max_report_length=..., on_error=_raise_on_error)`。
  - None → graphrag 默认（既有行为）。
- `build_adapter_from_settings` 读取：
  - `extract_graph.get("prompt")`、`summarize_descriptions.get("prompt")`、`community_reports.get("prompt")`（缺失/空→None→graphrag 默认）。
  - 透传给 build_default_adapter。

### 4.3 前端：types + client + buildSettings

- `web/src/api/client.ts`：`export const getPromptDefaults = () => req<{extract_graph: string; summarize_descriptions: string; community_reports: string}>("/prompts/defaults");`
- `web/src/lib/kb-settings.ts`：`KbFormState` 加 `prompts: { extract: string; summarize: string; communityReport: string }`（默认全空）。`buildSettings` 仅在非空时写 `extract_graph.prompt` / `summarize_descriptions.prompt` / `community_reports.prompt`。
- `web/src/components/KbForm.tsx`：加「提示词 Prompts」段——3 个 `<details>`（或 3 个 Field），每个：可编辑 textarea（留空=默认）+ 一个「查看 graphrag 默认」按钮/折叠，点击展开只读 `<pre>` 显示该 prompt 默认全文（通过 `getPromptDefaults()` 一次性拉取并缓存；加载中显示 spinner，失败显示「加载默认失败」）。

### 4.4 数据流

表单 prompts（非空）→ buildSettings → settings 的 `extract_graph.prompt` 等 → settings_yaml → POST /kbs → build_adapter_from_settings 读取 → build_default_adapter 用自定义 prompt（缺省用 graphrag import 的默认）。`GET /prompts/defaults` 仅给前端「查看默认」用。

## 5. 测试

- 后端：
  - `GET /prompts/defaults` 返回三键且为非空字符串。
  - `build_default_adapter` 自定义 prompt 透传到 extractor（monkeypatch `GraphExtractor`/`SummarizeExtractor`/`CommunityReportsExtractor` 构造，捕获 prompt 入参；断言自定义值；None→graphrag 默认）。
  - `build_adapter_from_settings` 读 `extract_graph.prompt` 等并透传。
- 前端：
  - `buildSettings`：prompts 非空 → emit 对应 key；全空 → 不 emit。
  - （可选）`getPromptDefaults` msw 测试。
- 既有 196 后端 + 37 前端测试全绿；ruff/build 干净。

## 6. 风险与对策

1. **prompt 很长**：textarea 留空=默认，避免淹没表单；「查看默认」折叠只读展示全文。
2. **默认值漂移**：`GET /prompts/defaults` 直接读 graphrag 当前版本 import，永远与后端实际用的默认一致。
3. **None→默认**：保证不配 prompt 时行为不变（既有测试不回归）。
4. **prompt-tune 未接**：用户需自己跑 prompt-tune（外部）产出中文 prompt 再粘贴；本阶段不做内置按钮。

## 7. 验收（Done）

- 表单「提示词」段：3 个可编辑 textarea（留空=默认）+ 每个有「查看 graphrag 默认」展示全文。
- 自定义 prompt 写入 settings → 索引时 extractor 用自定义 prompt（单测 + 可选手验：自定义 entity_types/prompt 改变抽取结果）。
- `GET /prompts/defaults` 返回三键全文。
- 既有测试全绿；ruff/build 干净。

## 8. 改动清单

- 后端：`kb_platform/graph/graphrag_adapter.py`（build_default_adapter 加 3 prompt 形参；build_adapter_from_settings 读取）、`kb_platform/api/routes_kbs.py` 或新 `routes_prompts.py`（`GET /prompts/defaults`）、`kb_platform/api/app.py`（注册路由，若新文件）。
- 前端：`web/src/api/client.ts`（getPromptDefaults）、`web/src/lib/kb-settings.ts`（prompts 字段 + buildSettings）、`web/src/components/KbForm.tsx`（提示词段 + 查看默认）。
- 测试：`tests/test_prompt_defaults.py`（端点）、扩展 `tests/test_build_adapter_settings.py`（prompt 透传）、扩展 `web/src/lib/kb-settings.test.ts`（prompts emit）。
