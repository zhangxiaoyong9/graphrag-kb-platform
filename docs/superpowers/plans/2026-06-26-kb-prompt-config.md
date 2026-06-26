# KB 提示词可配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 graphrag 三大索引 prompt（extract_graph / summarize_descriptions / community_reports）可在前端表单覆盖，并提供「查看 graphrag 默认」展示默认全文。

**Architecture:** 后端新增 `GET /prompts/defaults` 返回 graphrag 三个默认 prompt；`build_default_adapter` 加 3 个可选 prompt 形参（None→graphrag 默认），`build_adapter_from_settings` 从 settings 读取。前端 `kb-settings.ts` 加 prompts 字段（buildSettings 非空才 emit）；`KbForm` 加「提示词 Prompts」段：可编辑 textarea（留空=默认）+「查看 graphrag 默认」折叠（调 `getPromptDefaults()` 展示）。

**Tech Stack:** Python 3.11 + uv + graphrag 3.1 + pytest/ruff；React 18 + TS + Vite + Tailwind + vitest。

## Global Constraints

- 后端 `uv run pytest` / `uv run ruff check .`；前端 `cd web && npm run build && npm test`；kb-platform 无 pyright/poe/semversioner（ruff only）。
- 三大 prompt：`GRAPH_EXTRACTION_PROMPT` / `SUMMARIZE_PROMPT` / `COMMUNITY_REPORT_PROMPT`（`graphrag.prompts.index.*`）。
- settings key 路径：`extract_graph.prompt` / `summarize_descriptions.prompt` / `community_reports.prompt`。
- None/空 → graphrag 默认（既有行为不变，196 后端 + 37 前端测试不回归）。
- 表单 textarea 留空=默认（不预填长文）；非空才写入 settings。
- 不做「运行 prompt-tune」按钮（非目标）。

---

## File Structure

- `kb_platform/graph/graphrag_adapter.py`（改）：`build_default_adapter` 加 3 prompt 形参；`build_adapter_from_settings` 读取。
- `kb_platform/api/routes_kbs.py`（改）：新增 `GET /prompts/defaults`。
- `web/src/api/client.ts`（改）：`getPromptDefaults`。
- `web/src/lib/kb-settings.ts`（改）：`KbFormState.prompts` + `buildSettings` emit。
- `web/src/components/KbForm.tsx`（改）：提示词 Prompts 段 + 查看默认。
- 测试：`tests/test_prompt_defaults.py`（端点）、扩展 `tests/test_build_adapter_settings.py`（prompt 透传）、扩展 `web/src/lib/kb-settings.test.ts`（prompts emit）。

---

## Task 1: 后端 — `GET /prompts/defaults` + prompt 参数透传

**Files:**
- Modify: `kb_platform/api/routes_kbs.py`、`kb_platform/graph/graphrag_adapter.py`
- Test: `tests/test_prompt_defaults.py`（新建）、`tests/test_build_adapter_settings.py`（追加）

**Interfaces:**
- Produces: `GET /prompts/defaults → {extract_graph, summarize_descriptions, community_reports}`；`build_default_adapter(*, ..., extract_prompt=None, summarize_prompt=None, community_report_prompt=None)`。

- [ ] **Step 1: 写失败测试（端点）**

`tests/test_prompt_defaults.py`：
```python
"""GET /prompts/defaults returns graphrag's three built-in indexing prompts."""
from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.repository import Repository
from fastapi.testclient import TestClient


def test_prompt_defaults_endpoint():
    repo = Repository(create_engine("sqlite:///:memory:"))
    # in-memory engine w/ NullPool default re-creates per connection; use a tmp
    # file DB like test_redact.py to keep the table across connections.
    import os, tempfile
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    repo = Repository(create_engine(f"sqlite:///{path}"))
    from kb_platform.db.models import Base
    Base.metadata.create_all(repo.engine)
    with TestClient(create_app(repo, data_root=".")) as c:
        r = c.get("/prompts/defaults")
    assert r.status_code == 200
    body = r.json()
    for k in ("extract_graph", "summarize_descriptions", "community_reports"):
        assert isinstance(body[k], str) and len(body[k]) > 100
```

> 简化：用 tmp_path fixture 更干净。实现时若 mkstemp 临时表路径不便，改 `def test(tmp_path): repo = Repository(create_engine(f"sqlite:///{tmp_path}/p.db")); Base.metadata.create_all(repo.engine); ...`（与 `test_redact.py` 同模式）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_prompt_defaults.py -v`
Expected: FAIL — 404（端点不存在）。

- [ ] **Step 3: 实现端点**

在 `kb_platform/api/routes_kbs.py` 末尾加：
```python
@router.get("/prompts/defaults")
def prompt_defaults() -> dict:
    """Return graphrag's built-in indexing prompts (for the form's 'view default')."""
    from graphrag.prompts.index.community_report import COMMUNITY_REPORT_PROMPT
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT
    from graphrag.prompts.index.summarize_descriptions import SUMMARIZE_PROMPT

    return {
        "extract_graph": GRAPH_EXTRACTION_PROMPT,
        "summarize_descriptions": SUMMARIZE_PROMPT,
        "community_reports": COMMUNITY_REPORT_PROMPT,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_prompt_defaults.py -v`
Expected: PASS。

- [ ] **Step 5: 写失败测试（prompt 透传）**

追加到 `tests/test_build_adapter_settings.py`：
```python
def test_build_adapter_from_settings_reads_prompts(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(
        json.dumps({
            "llm": {"model": "x", "api_key": "k"},
            "extract_graph": {"prompt": "EXTRACT-PROMPT-X"},
            "summarize_descriptions": {"prompt": "SUMM-PROMPT-Y"},
            "community_reports": {"prompt": "REPORT-PROMPT-Z"},
        }),
        "/tmp/_unused_",
    )
    assert captured["extract_prompt"] == "EXTRACT-PROMPT-X"
    assert captured["summarize_prompt"] == "SUMM-PROMPT-Y"
    assert captured["community_report_prompt"] == "REPORT-PROMPT-Z"


def test_build_adapter_from_settings_prompts_default_none(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga
    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(json.dumps({"llm": {"model": "x", "api_key": "k"}}), "/tmp/_unused_")
    assert captured["extract_prompt"] is None
    assert captured["summarize_prompt"] is None
    assert captured["community_report_prompt"] is None
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: FAIL — `KeyError: 'extract_prompt'`（build_adapter_from_settings 未透传）。

- [ ] **Step 7: 实现透传**

在 `kb_platform/graph/graphrag_adapter.py`：

(a) `build_default_adapter` 签名加 3 形参（在现有 keyword 形参之后）：
```python
    extract_prompt: str | None = None,
    summarize_prompt: str | None = None,
    community_report_prompt: str | None = None,
```

(b) 三个 factory 用 `xxx or GRAPH_EXTRACTION_PROMPT`（其余两个同理）：
```python
        return GraphExtractor(
            model=completion, prompt=extract_prompt or GRAPH_EXTRACTION_PROMPT,
            max_gleanings=max_gleanings, on_error=_raise_on_error,
        )
        # SummarizeExtractor(..., summarization_prompt=summarize_prompt or SUMMARIZE_PROMPT, ...)
        # CommunityReportsExtractor(..., extraction_prompt=community_report_prompt or COMMUNITY_REPORT_PROMPT, ...)
```

(c) `build_adapter_from_settings` 在调用 `build_default_adapter(...)` 时读取并透传：
```python
        extract_prompt=extract_graph.get("prompt"),
        summarize_prompt=summarize.get("prompt"),
        community_report_prompt=reports.get("prompt"),
```
（`extract_graph` / `summarize` / `reports` 是既有 `settings.get(...) or {}`；`.get("prompt")` 缺失→None→graphrag 默认。）

- [ ] **Step 8: 跑测试确认通过 + 全量回归 + ruff**

Run: `uv run pytest tests/test_build_adapter_settings.py tests/test_prompt_defaults.py -v && uv run pytest -q && uv run ruff check .`
Expected: 全绿（既有 196 + 新增）；ruff 干净。

- [ ] **Step 9: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/api/routes_kbs.py kb_platform/graph/graphrag_adapter.py tests/test_prompt_defaults.py tests/test_build_adapter_settings.py
git commit -m "feat(prompts): GET /prompts/defaults + configurable indexing prompts (default=graphrag built-ins)"
```

---

## Task 2: 前端 — client + kb-settings（prompts 字段 + buildSettings emit）

**Files:**
- Modify: `web/src/api/client.ts`、`web/src/lib/kb-settings.ts`
- Test: `web/src/lib/kb-settings.test.ts`

**Interfaces:**
- Produces: `getPromptDefaults()` client；`KbFormState.prompts: { extract: string; summarize: string; communityReport: string }`；`buildSettings` 在非空时 emit `extract_graph.prompt` / `summarize_descriptions.prompt` / `community_reports.prompt`。

- [ ] **Step 1: 写失败测试**

追加到 `web/src/lib/kb-settings.test.ts`：
```typescript
it("emits prompts only when non-empty", () => {
  const s = {
    ...base,
    prompts: { extract: "MY-EXTRACT", summarize: "", communityReport: "MY-REPORT" },
  };
  expect(buildSettings(s)).toEqual({
    extract_graph: { prompt: "MY-EXTRACT" },
    community_reports: { prompt: "MY-REPORT" },
  });
});

it("omits prompts when all empty", () => {
  expect(buildSettings({ ...base, prompts: { extract: "", summarize: "", communityReport: "" } })).toEqual({});
});
```
（`base` 已在既有测试里定义；需把 `prompts` 加入 `base`/`DEFAULTS`——见实现。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- kb-settings`
Expected: FAIL — `prompts` 不在 KbFormState / buildSettings 不处理。

- [ ] **Step 3: 实现 client + kb-settings**

(a) `web/src/api/client.ts` 末尾加：
```typescript
export interface PromptDefaults {
  extract_graph: string;
  summarize_descriptions: string;
  community_reports: string;
}
export const getPromptDefaults = () => req<PromptDefaults>("/prompts/defaults");
```

(b) `web/src/lib/kb-settings.ts`：
- `KbFormState` 加 `prompts: { extract: string; summarize: string; communityReport: string }`。
- `DEFAULTS` 加 `prompts: { extract: "", summarize: "", communityReport: "" }`。
- `buildSettings` 在 advanced-override 之后、其余 bucket 逻辑里加：
```typescript
  if (state.prompts.extract.trim()) {
    const b = (out.extract_graph ?? {}) as Record<string, unknown>;
    b.prompt = state.prompts.extract.trim();
    out.extract_graph = b;
  }
  if (state.prompts.summarize.trim()) {
    const b = (out.summarize_descriptions ?? {}) as Record<string, unknown>;
    b.prompt = state.prompts.summarize.trim();
    out.summarize_descriptions = b;
  }
  if (state.prompts.communityReport.trim()) {
    const b = (out.community_reports ?? {}) as Record<string, unknown>;
    b.prompt = state.prompts.communityReport.trim();
    out.community_reports = b;
  }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npm test -- kb-settings`
Expected: PASS（既有 + 2 新增）。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/api/client.ts web/src/lib/kb-settings.ts web/src/lib/kb-settings.test.ts
git commit -m "feat(web): getPromptDefaults client + prompts field/buildSettings (empty=default)"
```

---

## Task 3: 前端 — KbForm 提示词段 + 查看默认

**Files:**
- Modify: `web/src/components/KbForm.tsx`

**Interfaces:**
- Consumes: Task 2 的 `getPromptDefaults` / `KbFormState.prompts` / `buildSettings`。

- [ ] **Step 1: 实现**

在 `KbForm.tsx`：
- 顶部 import `getPromptDefaults` + `useEffect/useState`（拉取默认 prompt，缓存）：
```typescript
import { useEffect, useState } from "react";
import { getPromptDefaults, type PromptDefaults } from "../api/client";
// ...
const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
const [showDef, setShowDef] = useState<Record<"extract" | "summarize" | "report", boolean>>({ extract: false, summarize: false, report: false });
useEffect(() => { getPromptDefaults().then(setDefaults).catch(() => setDefaults(null)); }, []);
```
- state 初值的 `prompts` 用 `DEFAULTS.prompts`（已在 Task 2 加入 DEFAULTS）；set helper：`set("prompts", { ...s.prompts, extract: v })` 等。
- 在表单（聚类段之后、高级段之前）加「提示词 Prompts」段：
```tsx
<details>
  <summary className="section-title">提示词 Prompts（留空=用 graphrag 默认）</summary>
  <div className="mt-3 space-y-4">
    {/* 抽取 prompt */}
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[13px] font-medium text-body">图谱抽取 extract_graph prompt</span>
        <button type="button" className="text-[12px] text-brand hover:underline" onClick={() => setShowDef((d) => ({ ...d, extract: !d.extract }))}>{showDef.extract ? "隐藏默认" : "查看 graphrag 默认"}</button>
      </div>
      <textarea className="textarea h-28 font-mono text-[12px]" value={s.prompts.extract} onChange={(e) => set("prompts", { ...s.prompts, extract: e.target.value })} placeholder="留空使用 graphrag 默认；可粘贴 prompt-tune 产出或自定义中文 prompt" />
      {showDef.extract && <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">{defaults?.extract_graph ?? "加载默认中…"}</pre>}
    </div>
    {/* 摘要 summarize_descriptions prompt —— 同上结构，value=s.prompts.summarize，默认=defaults?.summarize_descriptions，showDef.summarize */}
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[13px] font-medium text-body">描述摘要 summarize_descriptions prompt</span>
        <button type="button" className="text-[12px] text-brand hover:underline" onClick={() => setShowDef((d) => ({ ...d, summarize: !d.summarize }))}>{showDef.summarize ? "隐藏默认" : "查看 graphrag 默认"}</button>
      </div>
      <textarea className="textarea h-28 font-mono text-[12px]" value={s.prompts.summarize} onChange={(e) => set("prompts", { ...s.prompts, summarize: e.target.value })} placeholder="留空使用 graphrag 默认" />
      {showDef.summarize && <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">{defaults?.summarize_descriptions ?? "加载默认中…"}</pre>}
    </div>
    {/* 社区报告 community_reports prompt —— 同上，value=s.prompts.communityReport，默认=defaults?.community_reports，showDef.report */}
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[13px] font-medium text-body">社区报告 community_reports prompt</span>
        <button type="button" className="text-[12px] text-brand hover:underline" onClick={() => setShowDef((d) => ({ ...d, report: !d.report }))}>{showDef.report ? "隐藏默认" : "查看 graphrag 默认"}</button>
      </div>
      <textarea className="textarea h-28 font-mono text-[12px]" value={s.prompts.communityReport} onChange={(e) => set("prompts", { ...s.prompts, communityReport: e.target.value })} placeholder="留空使用 graphrag 默认" />
      {showDef.report && <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-muted whitespace-pre-wrap">{defaults?.community_reports ?? "加载默认中…"}</pre>}
    </div>
  </div>
</details>
```

- [ ] **Step 2: build + 测试**

Run: `cd web && npm run build && npm test`
Expected: build 干净；前端测试全绿。

- [ ] **Step 3: 全量回归（后端 + 前端）**

Run: `uv run pytest -q && (cd web && npm run build && npm test)`
Expected: 后端 + 前端全绿；ruff/build 干净。

- [ ] **Step 4: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/components/KbForm.tsx
git commit -m "feat(web): KB form Prompts section (editable + view-graphrag-default collapsible)"
```

---

## Self-Review

- **Spec 覆盖**：4.1（GET /prompts/defaults）→ Task 1 Step 3；4.2（build_default_adapter 形参 + build_adapter_from_settings 读取）→ Task 1 Step 7；4.3（client + kb-settings + KbForm 段）→ Task 2 + Task 3；查看默认折叠 → Task 3（getPromptDefaults + showDef + `<pre>`）；省略默认（空=默认）→ Task 2 buildSettings；既有行为不变 → Task 1 Step 8 回归。全覆盖。
- **占位符扫描**：无 TBD/TODO；每步含完整代码（Task 3 给出三个 prompt 块的完整 JSX 结构）。
- **类型一致性**：`getPromptDefaults() → PromptDefaults {extract_graph, summarize_descriptions, community_reports}`（client）；`KbFormState.prompts {extract, summarize, communityReport}`（kb-settings）；后端 key 路径 `extract_graph.prompt` / `summarize_descriptions.prompt` / `community_reports.prompt` 前后端一致；`build_default_adapter` 形参 `extract_prompt/summarize_prompt/community_report_prompt`（None→graphrag 默认）与 build_adapter_from_settings 透传一致。
- **既有行为不变**：prompt 形参默认 None → `xxx or GRAPH_EXTRACTION_PROMPT` → graphrag 默认（既有测试不回归）；Task 1 Step 8 + Task 3 Step 3 回归断言。
