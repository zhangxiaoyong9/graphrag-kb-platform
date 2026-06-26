# KB 配置可修改 — 创建后能编辑模型/Prompt/流水线参数

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：后端（PATCH 端点）+ 前端（parseSettings + KbForm 编辑模式 + 概要编辑入口弹窗）。

## 1. 背景

现状：KB 配置只在**创建时**设定（POST /kbs），**无更新接口**。改模型/api_base/prompt/chunk 等只能新建 KB（重新加文档+重新索引）。KB 概要的「模型配置」卡是只读的。

## 2. 目标 / 非目标

**目标**
- KB 创建后可编辑：name / method / settings（模型/嵌入/chunk/抽取/摘要/报告/聚类/prompt）/ min_success_ratio。
- 复用既有结构化 `KbForm`（加编辑模式），不另写表单。
- KB 概要「模型配置」卡加「编辑配置」按钮 → 弹窗打开预填好的表单 → 保存调 PATCH。

**非目标**
- 不改文档/任务/索引产物（编辑配置不影响已索引的图谱；用户改完需重新索引才生效——UI 提示）。
- 不做字段级 partial update（整体替换 settings）。
- 不改查询侧。

## 3. 决策（brainstorm 已定）

- 编辑入口：**弹窗 Modal**（可滚动，停留概要页）。
- 保存语义：**整体替换** settings（buildSettings 现有「省略默认」+ 整体替换 → 清空字段能正确回到默认；不需要 forUpdate 开关）。
- 高级/非表单字段：编辑会整体替换 → 这类字段会被清除；「高级」覆盖框在编辑模式**留空**（表单为编辑器），表单创建的 KB 不受影响。
- 编辑后提示「配置已更新，需重新索引才生效」。

## 4. 设计

### 4.1 后端：PATCH /kbs/{id}

`kb_platform/api/models.py`：新增 `KbUpdate(BaseModel)`（与 KbCreate 同形：`name: str`、`method: str = "standard"`、`settings_yaml: str | None = None`、`min_unit_success_ratio: float | None = None`）。

`kb_platform/db/repository.py`：新增
```python
def update_kb(self, kb_id: int, *, name: str, method: str, settings_json: str,
              min_unit_success_ratio: float | None) -> KnowledgeBase | None:
    with session_scope(self.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if kb is None:
            return None
        kb.name = name
        kb.method = method
        kb.settings_json = settings_json
        # min_unit_success_ratio 不在 KnowledgeBase 模型上（创建时也只是请求字段，
        # 实际作用于 job 触发）—— 若模型无此列，这里只更新 name/method/settings_json。
        return kb
```
> 注：先 `inspect` 确认 `KnowledgeBase` 列；若 `min_unit_success_ratio` 不落库（创建时仅入 job），则 KbUpdate 仍接收但 update_kb 不写该列（与 create 行为一致）。实现时以模型实际列为准。

`kb_platform/api/routes_kbs.py`：新增
```python
@router.patch("/kbs/{kb_id}", response_model=KbDetailOut)
def update_kb(kb_id: int, payload: KbUpdate, request: Request) -> KbDetailOut:
    repo = request.app.state.repo
    settings = _parse_settings(payload.settings_yaml)  # 复用既有 JSON 校验
    kb = repo.update_kb(kb_id, name=payload.name, method=payload.method,
                        settings_json=settings)
    if kb is None:
        raise HTTPException(404)
    return KbDetailOut(id=kb.id, name=kb.name, method=kb.method, settings=_redact(kb.settings_json))
```

### 4.2 前端：parseSettings（逆映射）+ updateKb client

`web/src/api/client.ts`：
```typescript
export const updateKb = (id: number, body: { name: string; method: string; settings_yaml: string; min_unit_success_ratio?: number }) =>
  req<KbOut>(`/kbs/${id}`, { method: "PATCH", body: JSON.stringify(body) });
```

`web/src/lib/kb-settings.ts`：新增
```typescript
export function parseSettings(settings: Record<string, unknown>, method: string, minRatio: string): KbFormState {
  const llm = (settings.llm as Record<string, any> | undefined) ?? {};
  const emb = (settings.embedding as Record<string, any> | undefined) ?? {};
  const chunking = (settings.chunking as Record<string, any> | undefined) ?? {};
  const eg = (settings.extract_graph as Record<string, any> | undefined) ?? {};
  const summ = (settings.summarize_descriptions as Record<string, any> | undefined) ?? {};
  const cr = (settings.community_reports as Record<string, any> | undefined) ?? {};
  const cluster = (settings.cluster_graph as Record<string, any> | undefined) ?? {};
  const et = Array.isArray(eg.entity_types) ? eg.entity_types.join(", ") : (typeof eg.entity_types === "string" ? eg.entity_types : "");
  return {
    ...DEFAULTS,
    method, minRatio,
    llm: { ...DEFAULTS.llm, provider: llm.model_provider ?? "", model: llm.model ?? "", apiBase: llm.api_base ?? "", apiKeyEnv: llm.api_key_env ?? "", apiKey: "", apiVersion: llm.api_version ?? "" },
    embedding: { ...DEFAULTS.embedding, enabled: !!settings.embedding, provider: emb.model_provider ?? "", model: emb.model ?? "", apiBase: emb.api_base ?? "", apiKeyEnv: emb.api_key_env ?? "", apiKey: "", apiVersion: emb.api_version ?? "" },
    chunking: { ...DEFAULTS.chunking, size: Number(chunking.size ?? DEFAULTS.chunking.size), overlap: Number(chunking.overlap ?? DEFAULTS.chunking.overlap), encodingModel: chunking.encoding_model ?? DEFAULTS.chunking.encodingModel },
    extractGraph: { entityTypes: et, maxGleanings: Number(eg.max_gleanings ?? DEFAULTS.extractGraph.maxGleanings) },
    summarize: { maxLength: Number(summ.max_length ?? DEFAULTS.summarize.maxLength), maxInputTokens: Number(summ.max_input_tokens ?? DEFAULTS.summarize.maxInputTokens) },
    communityReports: { structuredOutput: cr.structured_output ?? DEFAULTS.communityReports.structuredOutput, maxLength: Number(cr.max_length ?? DEFAULTS.communityReports.maxLength) },
    cluster: { maxClusterSize: Number(cluster.max_cluster_size ?? DEFAULTS.cluster.maxClusterSize) },
    prompts: { extract: (eg.prompt as string) ?? "", summarize: (summ.prompt as string) ?? "", communityReport: (cr.prompt as string) ?? "" },
    advancedOverride: "",
  };
}
```
（apiKey 不回填——脱敏 GET 拿不到明文，且鼓励用 api_key_env；留空=不覆盖。）

`buildSettings` 不变（省略默认 + 整体替换）。

### 4.3 前端：KbForm 编辑模式 + 概要编辑入口

`web/src/components/KbForm.tsx`：加可选 `kb?: KbOut` 入参（编辑模式）。
- 初值：`kb ? parseSettings(kb.settings ?? {}, kb.method, /* minRatio 需从 kb 取——见下 */) : DEFAULTS`。
  > `minRatio`：KbOut 当前无 min_unit_success_ratio 字段（创建时入 job，不落 KB）。编辑模式默认填 "1.0"（或从最近 job 推断——本阶段用默认 1.0）。
- 提交：`kb ? updateKb(kb.id, {...}) : createKb({...})`；按钮文案编辑模式「保存修改」/ 创建模式「创建知识库」。
- `onCreated`/`onSaved` 回调区分。

`web/src/pages/KbOverviewPage.tsx`：「模型配置」卡 CardHeader actions 加「编辑配置」按钮 → 打开弹窗（`useState(false)`）→ 弹窗内渲染 `<KbForm kb={kb} onSaved={() => { setOpen(false); reload(); }} />`。弹窗用既有设计风格（fixed overlay + 卡片 + 可滚动）。保存成功后关闭 + reload（KbContext.reload）刷新概要。

保存后提示：「配置已更新。如需让新配置生效，请重新触发索引任务。」

### 4.4 数据流

编辑：parseSettings(kb.settings) 预填表单 → 用户改字段 → buildSettings(state)（省略默认）→ JSON.stringify → PATCH /kbs/{id} → repo.update_kb 整体替换 settings_json → reload 概要。查询侧 `_resolve_config` 下次查询读新 settings。

## 5. 测试

- 后端：`PATCH /kbs/{id}` 更新 name/method/settings（TestClient，tmp_path DB）；404 不存在；返回脱敏 settings。
- 前端：`parseSettings` 单测（settings → form state，缺失用默认；entity_types list→csv；embedding enabled 推断）；`updateKb` 调用（msw）。KbForm 编辑模式渲染预填 + 提交调 updateKb（RTL）。
- 既有 199 后端 + 40 前端测试全绿；ruff/build 干净。

## 6. 风险与对策

1. **整体替换丢高级字段**：表单创建的 KB 无此问题；高级覆盖框是逃生口（编辑模式留空，表单为编辑器）。UI 提示。
2. **apiKey 不回填**：GET 脱敏拿不到明文；编辑模式 apiKey 留空=不覆盖（鼓励 api_key_env）。若用户原用 api_key 明文，编辑后需重填（提示）。
3. **minRatio**：KbOut 无此字段；编辑模式默认 1.0（与创建默认一致）。
4. **改配置不自动重索引**：UI 明确提示需重新索引才生效。

## 7. 验收（Done）

- KB 概要「编辑配置」→ 弹窗预填当前配置 → 改字段（如 LLM model / prompt / chunk size）→ 保存 → GET /kbs/{id} 返回新 settings；概要刷新。
- 后端 PATCH 单测 + 前端 parseSettings/updateKb/编辑模式单测全绿。
- 既有测试不回归；ruff/build 干净。

## 8. 改动清单

- 后端：`kb_platform/api/models.py`（KbUpdate）、`kb_platform/db/repository.py`（update_kb）、`kb_platform/api/routes_kbs.py`（PATCH）。
- 前端：`web/src/api/client.ts`（updateKb）、`web/src/lib/kb-settings.ts`（parseSettings + 测试）、`web/src/components/KbForm.tsx`（编辑模式）、`web/src/pages/KbOverviewPage.tsx`（编辑按钮 + 弹窗）。
- 测试：`tests/test_update_kb.py`、扩展 `web/src/lib/kb-settings.test.ts`、扩展 `web/src/components/KbForm.test.tsx`。
