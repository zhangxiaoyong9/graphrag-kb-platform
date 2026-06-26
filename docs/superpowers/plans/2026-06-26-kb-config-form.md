# KB 配置表单化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用结构化分区表单取代建库的手写 JSON 文本框，覆盖所有平台实际兑现的 GraphRAG 配置（含把当前硬编码的流水线参数接到 settings）。

**Architecture:** 后端 `build_default_adapter` 接收 bucket-2 参数（默认=现状），`build_adapter_from_settings` 从 settings 各段读取并透传。前端新增 `kb-settings.ts`（`DEFAULTS` + 纯函数 `buildSettings`）+ 重写 `KbForm` 为分区表单；提交时 `buildSettings` 生成 settings 对象（省略默认值）→ 序列化成 `settings_yaml` → 既有 `POST /kbs`。

**Tech Stack:** Python 3.11 + uv + graphrag 3.1 + pytest/ruff（后端）；React 18 + TypeScript + Vite + Tailwind + vitest（前端）。

## Global Constraints

- 后端 `uv run pytest` / `uv run ruff check .`；前端 `cd web && npm run build && npm test`；kb-platform **无 pyright/poe/semversioner**（ruff only）。
- settings key 路径用 graphrag 原生：`llm` / `embedding` / `chunking` / `cluster_graph` / `extract_graph` / `summarize_descriptions` / `community_reports`。
- bucket-2 默认值=现状硬编码：chunk size 1200 / overlap 100 / encoding `cl100k_base`；cluster `max_cluster_size` 10；extract `max_gleanings` 0、`entity_types` 默认 `DEFAULT_ENTITY_TYPES`；summarize `max_length` 500 / `max_input_tokens` 32000；report `max_length` 2000；`community_reports.structured_output` 默认 true。
- 不传 bucket-2 段时行为不变（默认值=现状）→ 既有 189 后端测试不回归。
- 不暴露 bucket 3（平台忽略的段）。高级覆盖框是逃生口。
- 前端 buildSettings 省略「等于 DEFAULTS」的字段；llm/embedding 按字段非空取舍。
- 异步/前端不引入新依赖。

---

## File Structure

- `kb_platform/graph/graphrag_adapter.py`（改）：`build_default_adapter` 加 bucket-2 形参并使用；`build_adapter_from_settings` 读 settings 各段透传；`GraphRagAdapter` 加 `max_cluster_size` 字段供 `cluster_relationships` 用。
- `web/src/lib/kb-settings.ts`（新建）：`DEFAULTS` 常量 + `buildSettings(state)` 纯函数。
- `web/src/lib/kb-settings.test.ts`（新建）：buildSettings 各分支单测。
- `web/src/components/KbForm.tsx`（重写）：分区表单，引用 `kb-settings.ts`。
- `tests/test_build_adapter_settings.py`（新建）：后端 bucket-2 透传测试。

---

## Task 1: 后端 — `build_default_adapter` 接收并使用 bucket-2 参数

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py`
- Test: `tests/test_build_adapter_settings.py`（新建）

**Interfaces:**
- Produces: `build_default_adapter(*, data_root, model_config, embed_model_config=None, chunk_size=1200, chunk_overlap=100, encoding_model="cl100k_base", max_cluster_size=10, entity_types=None, max_gleanings=0, summarize_max_length=500, summarize_max_input_tokens=32000, report_max_length=2000)`；`GraphRagAdapter.__init__` 增加 `max_cluster_size` 形参（存 `self._max_cluster_size`），`cluster_relationships` 用 `self._max_cluster_size`。

- [ ] **Step 1: 写失败测试**

`tests/test_build_adapter_settings.py`：
```python
"""build_default_adapter uses bucket-2 params (chunk/cluster/extract/summarize/report)
instead of hardcoding them."""
from graphrag_llm.config import ModelConfig

from kb_platform.graph.graphrag_adapter import build_default_adapter


def _patch_factories(monkeypatch):
    import graphrag_chunking.chunker_factory as cf
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}

    class _ChunkCfg:
        def __init__(self, **kw):
            captured["chunk_cfg"] = kw

    class _FakeChunker:
        def chunk(self, text):
            return []

    def fake_create_chunker(cfg, encode, decode):
        captured["chunker_cfg"] = cfg
        return _FakeChunker()

    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: object())

    # create_chunker is imported inside build_default_adapter from graphrag_chunking.chunker_factory
    monkeypatch.setattr(cf, "create_chunker", fake_create_chunker)
    return captured


def test_build_default_adapter_uses_custom_bucket2(monkeypatch):
    import graphrag_chunking.chunker_factory as cf
    captured = _patch_factories(monkeypatch)
    llm = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")

    adapter = build_default_adapter(
        data_root="/tmp/_unused_",
        model_config=llm,
        chunk_size=300,
        chunk_overlap=20,
        encoding_model="r50k_base",
        max_cluster_size=7,
        entity_types=["ORG"],
        max_gleanings=2,
        summarize_max_length=111,
        summarize_max_input_tokens=2222,
        report_max_length=3333,
    )
    # chunker got the custom config
    cfg = captured["chunker_cfg"]
    assert cfg.size == 300 and cfg.overlap == 20 and cfg.encoding_model == "r50k_base"
    # adapter carries the custom cluster size + entity_types
    assert adapter._max_cluster_size == 7
    assert adapter._entity_types == ["ORG"]
    # extractor/summarize/report factories captured their params (introspect closures is hard,
    # so assert via the adapter fields that are observable; summarize/report max lengths are
    # baked into the factory closures — verified indirectly via build_adapter_from_settings test)


def test_build_default_adapter_defaults_match_current(monkeypatch):
    captured = _patch_factories(monkeypatch)
    llm = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")
    adapter = build_default_adapter(data_root="/tmp/_unused_", model_config=llm)
    cfg = captured["chunker_cfg"]
    assert cfg.size == 1200 and cfg.overlap == 100 and cfg.encoding_model == "cl100k_base"
    assert adapter._max_cluster_size == 10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_build_adapter_settings.py -v`
Expected: FAIL — `build_default_adapter() got an unexpected keyword argument 'chunk_size'`（或 `_max_cluster_size` 缺失）。

- [ ] **Step 3: 实现**

在 `kb_platform/graph/graphrag_adapter.py`：

(a) `GraphRagAdapter.__init__` 增加 `max_cluster_size: int = 10` 形参，存 `self._max_cluster_size = max_cluster_size`。

(b) `cluster_relationships` 把 `max_cluster_size=10` 改为 `max_cluster_size=self._max_cluster_size`。

(c) `build_default_adapter` 签名改为：
```python
def build_default_adapter(
    *,
    data_root: str,
    model_config,
    embed_model_config=None,
    chunk_size: int = 1200,
    chunk_overlap: int = 100,
    encoding_model: str = "cl100k_base",
    max_cluster_size: int = 10,
    entity_types=None,
    max_gleanings: int = 0,
    summarize_max_length: int = 500,
    summarize_max_input_tokens: int = 32000,
    report_max_length: int = 2000,
) -> GraphRagAdapter:
```

(d) 函数体内：
- chunker：`ChunkingConfig(type=ChunkerType.Tokens, encoding_model=encoding_model, size=chunk_size, overlap=chunk_overlap)`。
- `GraphExtractor(model=completion, prompt=GRAPH_EXTRACTION_PROMPT, max_gleanings=max_gleanings, on_error=_raise_on_error)`（不变，max_gleanings 已接）。
- `SummarizeExtractor(model=completion, max_summary_length=summarize_max_length, max_input_tokens=summarize_max_input_tokens, summarization_prompt=SUMMARIZE_PROMPT, on_error=_raise_on_error)`。
- `CommunityReportsExtractor(model=completion, extraction_prompt=COMMUNITY_REPORT_PROMPT, max_report_length=report_max_length, on_error=_raise_on_error)`。
- `GraphRagAdapter(..., entity_types=entity_types or list(DEFAULT_ENTITY_TYPES), ..., max_cluster_size=max_cluster_size)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: PASS（2 个）。

- [ ] **Step 5: 全量回归 + ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 既有 189 + 新增 2 全绿；ruff 干净。

- [ ] **Step 6: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/graph/graphrag_adapter.py tests/test_build_adapter_settings.py
git commit -m "feat(adapter): make chunk/cluster/extract/summarize/report params configurable in build_default_adapter"
```

---

## Task 2: 后端 — `build_adapter_from_settings` 从 settings 读取 bucket-2 段

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py`（`build_adapter_from_settings`）
- Test: `tests/test_build_adapter_settings.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `build_default_adapter` 形参。
- Produces: `build_adapter_from_settings(settings_json, data_root, api_key=None)` 把 `chunking/cluster_graph/extract_graph/summarize_descriptions/community_reports` 各段映射到 `build_default_adapter` 形参。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_build_adapter_settings.py`：
```python
def test_build_adapter_from_settings_reads_bucket2(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}
    def fake_build(**kw):
        captured.update(kw)
        # return a minimal real adapter via the real constructor to keep types happy
        return ga.build_default_adapter(
            data_root=kw["data_root"], model_config=kw["model_config"], **{k: v for k, v in kw.items() if k in {"chunk_size","chunk_overlap","encoding_model","max_cluster_size","entity_types","max_gleanings","summarize_max_length","summarize_max_input_tokens","report_max_length"}}
        )
    monkeypatch.setattr(ga, "build_default_adapter", fake_build)
    # also patch the heavy internals the real build_default_adapter pulls in
    import graphrag_chunking.chunker_factory as cf
    class _C: 
        def chunk(s, t): return []
    monkeypatch.setattr(cf, "create_chunker", lambda *a, **k: _C())
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod
    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: object())

    settings = {
        "llm": {"model_provider": "deepseek", "model": "deepseek-chat", "api_key": "k"},
        "chunking": {"size": 350, "overlap": 25},
        "cluster_graph": {"max_cluster_size": 8},
        "extract_graph": {"entity_types": ["ORG", "PERSON"], "max_gleanings": 1},
        "summarize_descriptions": {"max_length": 222, "max_input_tokens": 9000},
        "community_reports": {"max_length": 4444},
    }
    ga.build_adapter_from_settings(json.dumps(settings), "/tmp/_unused_")
    assert captured["chunk_size"] == 350 and captured["chunk_overlap"] == 25
    assert captured["max_cluster_size"] == 8
    assert captured["entity_types"] == ["ORG", "PERSON"] and captured["max_gleanings"] == 1
    assert captured["summarize_max_length"] == 222 and captured["summarize_max_input_tokens"] == 9000
    assert captured["report_max_length"] == 4444


def test_build_adapter_from_settings_entity_types_csv_string(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga
    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(
        json.dumps({"llm": {"model": "x", "api_key": "k"}, "extract_graph": {"entity_types": "ORG, PERSON"}}),
        "/tmp/_unused_",
    )
    assert captured["entity_types"] == ["ORG", "PERSON"]  # csv string -> list, trimmed
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: FAIL — `captured` 缺 `chunk_size` 等（当前 `build_adapter_from_settings` 未透传 bucket-2）。

- [ ] **Step 3: 实现**

在 `build_adapter_from_settings`（构造 `model_config` 之后、`return build_default_adapter(...)` 处），改为读取各段并透传：
```python
    chunking = settings.get("chunking") or {}
    cluster_graph = settings.get("cluster_graph") or {}
    extract_graph = settings.get("extract_graph") or {}
    summarize = settings.get("summarize_descriptions") or {}
    reports = settings.get("community_reports") or {}

    et = extract_graph.get("entity_types")
    if isinstance(et, str):
        et = [t.strip() for t in et.split(",") if t.strip()]

    embed_model_config = _build_embed_model_config(settings)
    return build_default_adapter(
        data_root=data_root,
        model_config=model_config,
        embed_model_config=embed_model_config,
        chunk_size=chunking.get("size", 1200),
        chunk_overlap=chunking.get("overlap", 100),
        encoding_model=chunking.get("encoding_model", "cl100k_base"),
        max_cluster_size=cluster_graph.get("max_cluster_size", 10),
        entity_types=et,
        max_gleanings=extract_graph.get("max_gleanings", 0),
        summarize_max_length=summarize.get("max_length", 500),
        summarize_max_input_tokens=summarize.get("max_input_tokens", 32000),
        report_max_length=reports.get("max_length", 2000),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: PASS（4 个）。

- [ ] **Step 5: 全量回归 + ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全绿；ruff 干净。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_build_adapter_settings.py
git commit -m "feat(adapter): read chunk/cluster/extract/summarize/report settings in build_adapter_from_settings"
```

---

## Task 3: 前端 — `kb-settings.ts`（DEFAULTS + buildSettings 纯函数）

**Files:**
- Create: `web/src/lib/kb-settings.ts`
- Test: `web/src/lib/kb-settings.test.ts`

**Interfaces:**
- Produces: `DEFAULTS` 常量；`KbFormState` 类型；`buildSettings(state: KbFormState): Record<string, unknown>`（advancedOverride 非空时抛/返回 JSON.parse 结果）。

- [ ] **Step 1: 写失败测试**

`web/src/lib/kb-settings.test.ts`：
```typescript
import { describe, expect, it } from "vitest";
import { buildSettings, DEFAULTS, type KbFormState } from "./kb-settings";

const base: KbFormState = {
  ...DEFAULTS,
  llm: { ...DEFAULTS.llm },
  embedding: { ...DEFAULTS.embedding },
  chunking: { ...DEFAULTS.chunking },
  extractGraph: { ...DEFAULTS.extractGraph },
  summarize: { ...DEFAULTS.summarize },
  communityReports: { ...DEFAULTS.communityReports },
  cluster: { ...DEFAULTS.cluster },
  advancedOverride: "",
};

describe("buildSettings", () => {
  it("all defaults -> empty object", () => {
    expect(buildSettings(base)).toEqual({});
  });

  it("emits only non-default chunking field", () => {
    const s = { ...base, chunking: { ...DEFAULTS.chunking, size: 300 } };
    expect(buildSettings(s)).toEqual({ chunking: { size: 300 } });
  });

  it("emits llm non-empty fields only", () => {
    const s = { ...base, llm: { ...DEFAULTS.llm, provider: "deepseek", model: "deepseek-chat", apiKeyEnv: "DEEPSEEK_API_KEY" } };
    expect(buildSettings(s)).toEqual({
      llm: { model_provider: "deepseek", model: "deepseek-chat", api_key_env: "DEEPSEEK_API_KEY" },
    });
  });

  it("omits embedding when disabled", () => {
    const s = { ...base, embedding: { ...DEFAULTS.embedding, enabled: false, provider: "ollama" } };
    expect(buildSettings(s)).toEqual({});
  });

  it("emits embedding when enabled + filled", () => {
    const s = { ...base, embedding: { enabled: true, provider: "ollama", model: "nomic-embed-text", apiBase: "http://localhost:11434", apiKey: "ollama", apiKeyEnv: "", apiVersion: "" } };
    expect(buildSettings(s)).toEqual({
      embedding: { model_provider: "ollama", model: "nomic-embed-text", api_base: "http://localhost:11434", api_key: "ollama" },
    });
  });

  it("emits community_reports.structured_output when false (default true)", () => {
    const s = { ...base, communityReports: { ...DEFAULTS.communityReports, structuredOutput: false } };
    expect(buildSettings(s)).toEqual({ community_reports: { structured_output: false } });
  });

  it("advanced override replaces everything", () => {
    const s = { ...base, advancedOverride: '{"llm":{"model":"x"}}' };
    expect(buildSettings(s)).toEqual({ llm: { model: "x" } });
  });

  it("advanced override invalid JSON throws", () => {
    const s = { ...base, advancedOverride: "{not json" };
    expect(() => buildSettings(s)).toThrow();
  });

  it("entity_types csv -> list", () => {
    const s = { ...base, extractGraph: { entityTypes: "ORG, PERSON", maxGleanings: 0 } };
    expect(buildSettings(s)).toEqual({ extract_graph: { entity_types: ["ORG", "PERSON"] } });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- kb-settings`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 实现**

`web/src/lib/kb-settings.ts`：
```typescript
/** KB config form state, defaults, and settings-serializer (replaces hand-written JSON). */

export interface LlmFields { provider: string; model: string; apiBase: string; apiKeyEnv: string; apiKey: string; apiVersion: string }
export interface EmbeddingFields extends LlmFields { enabled: boolean }

export interface KbFormState {
  method: string;
  minRatio: string;
  llm: LlmFields;
  embedding: EmbeddingFields;
  chunking: { size: number; overlap: number; encodingModel: string };
  extractGraph: { entityTypes: string; maxGleanings: number };
  summarize: { maxLength: number; maxInputTokens: number };
  communityReports: { structuredOutput: boolean; maxLength: number };
  cluster: { maxClusterSize: number };
  advancedOverride: string;
}

const EMPTY_LLM: LlmFields = { provider: "", model: "", apiBase: "", apiKeyEnv: "", apiKey: "", apiVersion: "" };

export const DEFAULTS: KbFormState = {
  method: "standard",
  minRatio: "1.0",
  llm: { ...EMPTY_LLM },
  embedding: { ...EMPTY_LLM, enabled: false },
  chunking: { size: 1200, overlap: 100, encodingModel: "cl100k_base" },
  extractGraph: { entityTypes: "", maxGleanings: 0 },
  summarize: { maxLength: 500, maxInputTokens: 32000 },
  communityReports: { structuredOutput: true, maxLength: 2000 },
  cluster: { maxClusterSize: 10 },
  advancedOverride: "",
};

const LLM_MAP: [keyof LlmFields, string][] = [
  ["provider", "model_provider"], ["model", "model"], ["apiBase", "api_base"],
  ["apiKeyEnv", "api_key_env"], ["apiKey", "api_key"], ["apiVersion", "api_version"],
];

function pickLlm(f: LlmFields): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, snake] of LLM_MAP) {
    const v = (f as Record<string, string>)[k as string];
    if (v && v.trim()) out[snake] = v.trim();
  }
  return out;
}

export function buildSettings(state: KbFormState): Record<string, unknown> {
  const override = state.advancedOverride.trim();
  if (override) return JSON.parse(override) as Record<string, unknown>;

  const out: Record<string, unknown> = {};
  const llm = pickLlm(state.llm);
  if (Object.keys(llm).length) out.llm = llm;
  if (state.embedding.enabled) {
    const emb = pickLlm(state.embedding);
    if (Object.keys(emb).length) out.embedding = emb;
  }

  const addIf = <T>(key: string, val: T, def: T, bucket: string) => {
    if (val !== def) {
      const b = (out[bucket] ?? {}) as Record<string, unknown>;
      b[key] = val;
      out[bucket] = b;
    }
  };

  addIf("size", state.chunking.size, DEFAULTS.chunking.size, "chunking");
  addIf("overlap", state.chunking.overlap, DEFAULTS.chunking.overlap, "chunking");
  addIf("encoding_model", state.chunking.encodingModel, DEFAULTS.chunking.encodingModel, "chunking");

  addIf("max_cluster_size", state.cluster.maxClusterSize, DEFAULTS.cluster.maxClusterSize, "cluster_graph");

  addIf("max_gleanings", state.extractGraph.maxGleanings, DEFAULTS.extractGraph.maxGleanings, "extract_graph");
  const et = state.extractGraph.entityTypes.split(",").map((t) => t.trim()).filter(Boolean);
  if (et.length) {
    const b = (out.extract_graph ?? {}) as Record<string, unknown>;
    b.entity_types = et;
    out.extract_graph = b;
  }

  addIf("max_length", state.summarize.maxLength, DEFAULTS.summarize.maxLength, "summarize_descriptions");
  addIf("max_input_tokens", state.summarize.maxInputTokens, DEFAULTS.summarize.maxInputTokens, "summarize_descriptions");

  addIf("structured_output", state.communityReports.structuredOutput, DEFAULTS.communityReports.structuredOutput, "community_reports");
  addIf("max_length", state.communityReports.maxLength, DEFAULTS.communityReports.maxLength, "community_reports");

  return out;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npm test -- kb-settings`
Expected: PASS（9 个）。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/lib/kb-settings.ts web/src/lib/kb-settings.test.ts
git commit -m "feat(web): kb-settings DEFAULTS + buildSettings (form -> settings dict, omit defaults)"
```

---

## Task 4: 前端 — 重写 `KbForm` 为分区表单

**Files:**
- Modify: `web/src/components/KbForm.tsx`（重写）
- Test: `web/src/components/KbForm.test.tsx`（新建或复用既有 KbListPage 测试模式）

**Interfaces:**
- Consumes: Task 3 的 `DEFAULTS` / `KbFormState` / `buildSettings`；既有 `createKb` / `Field` / `input/select/textarea` / `Button`。

- [ ] **Step 1: 写失败测试**

`web/src/components/KbForm.test.tsx`：
```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import KbForm from "./KbForm";

const server = setupServer(
  http.post("/kbs", async ({ request }) => {
    const body = (await request.json()) as { settings_yaml: string };
    return HttpResponse.json({ id: 1, name: "x", method: "standard", settings: JSON.parse(body.settings_yaml || "{}") });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("form has all sections and submits LLM settings_yaml", async () => {
  render(<MemoryRouter><KbForm onCreated={() => {}} /></MemoryRouter>);
  // sections render
  for (const label of ["LLM 模型", "Embedding 模型", "分块", "图谱抽取", "社区报告", "聚类"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  // no visible raw-JSON textarea by default (advanced is collapsed) — the only textarea shown
  // initially is entity_types (text input) — assert LLM provider/model inputs exist
  expect(screen.getByPlaceholderText(/deepseek|provider/i)).toBeInTheDocument();
  await userEvent.type(screen.getByPlaceholderText(/deepseek|provider/i), "deepseek");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));
  // created -> name field cleared
  // (server echoes settings; not strictly asserting here — the request was accepted)
});
```

> 注：占位符/标签以实现时实际文案为准；先 `npm run dev` 看一眼或对齐下面的实现文案，必要时调整测试选择器。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- KbForm`
Expected: FAIL — 当前 KbForm 没有「分块/聚类/…」分区。

- [ ] **Step 3: 实现**

重写 `web/src/components/KbForm.tsx` 为分区表单。state 用 `KbFormState`（初值 `structuredClone(DEFAULTS)` 或逐字段）；每个字段一个受控输入。结构：
```tsx
import { useState } from "react";
import { createKb } from "../api/client";
import type { KbOut } from "../api/types";
import { Button, Field } from "./ui";
import { IconPlus } from "./icons";
import { DEFAULTS, buildSettings, type KbFormState } from "../lib/kb-settings";

export default function KbForm({ onCreated }: { onCreated: (kb: KbOut) => void }) {
  const [s, setS] = useState<KbFormState>(() => ({ ...DEFAULTS, llm: { ...DEFAULTS.llm }, embedding: { ...DEFAULTS.embedding }, chunking: { ...DEFAULTS.chunking }, extractGraph: { ...DEFAULTS.extractGraph }, summarize: { ...DEFAULTS.summarize }, communityReports: { ...DEFAULTS.communityReports }, cluster: { ...DEFAULTS.cluster }, advancedOverride: "" }));
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const set = <K extends keyof KbFormState>(k: K, v: KbFormState[K]) => setS((p) => ({ ...p, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const settingsObj = buildSettings(s); // throws on bad advancedOverride
      const kb = await createKb({ name, method: s.method, settings_yaml: JSON.stringify(settingsObj), min_unit_success_ratio: parseFloat(s.minRatio) });
      onCreated(kb);
      setName("");
    } catch (err) {
      setError(String((err as Error).message ?? err));
    } finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {/* 基础 */}
      <Field label="知识库名称"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="请输入知识库名称" required /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="索引方法" hint="standard / fast">
          <select className="select" value={s.method} onChange={(e) => set("method", e.target.value)}>
            <option value="standard">standard（LLM 精抽取）</option>
            <option value="fast">fast（NLP 快速）</option>
          </select>
        </Field>
        <Field label="最小成功率" hint="低于此值步骤失败">
          <input className="input" type="number" step="0.01" min="0" max="1" value={s.minRatio} onChange={(e) => set("minRatio", e.target.value)} />
        </Field>
      </div>

      {/* LLM 模型 — 字段：provider/model/api_base/api_key_env/api_key/api_version */}
      <details open><summary className="section-title">LLM 模型</summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="provider" hint="deepseek / openai / ollama"><input className="input" value={s.llm.provider} placeholder="deepseek" onChange={(e) => set("llm", { ...s.llm, provider: e.target.value })} /></Field>
          <Field label="model"><input className="input" value={s.llm.model} placeholder="deepseek-chat" onChange={(e) => set("llm", { ...s.llm, model: e.target.value })} /></Field>
          <Field label="api_base" hint="自定义端点（可选）"><input className="input" value={s.llm.apiBase} placeholder="https://api.deepseek.com" onChange={(e) => set("llm", { ...s.llm, apiBase: e.target.value })} /></Field>
          <Field label="api_key_env" hint="密钥环境变量名（推荐）"><input className="input" value={s.llm.apiKeyEnv} placeholder="DEEPSEEK_API_KEY" onChange={(e) => set("llm", { ...s.llm, apiKeyEnv: e.target.value })} /></Field>
          <Field label="api_key" hint="明文（不推荐，会入库）"><input className="input" value={s.llm.apiKey} onChange={(e) => set("llm", { ...s.llm, apiKey: e.target.value })} /></Field>
          <Field label="api_version" hint="仅 Azure"><input className="input" value={s.llm.apiVersion} onChange={(e) => set("llm", { ...s.llm, apiVersion: e.target.value })} /></Field>
        </div>
      </details>

      {/* Embedding 模型 — enabled 开关 + 同 6 字段 */}
      <details><summary className="section-title">Embedding 模型</summary>
        <div className="mt-3 space-y-3">
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={s.embedding.enabled} onChange={(e) => set("embedding", { ...s.embedding, enabled: e.target.checked })} /> 启用嵌入（local/basic/drift 需要）</label>
          {s.embedding.enabled && (
            <div className="grid grid-cols-2 gap-3">
              {/* 同 LLM 6 字段，onChange set("embedding", {...s.embedding, field: v})；provider 默认 ollama/openai */}
              <Field label="provider"><input className="input" value={s.embedding.provider} placeholder="ollama" onChange={(e) => set("embedding", { ...s.embedding, provider: e.target.value })} /></Field>
              <Field label="model"><input className="input" value={s.embedding.model} placeholder="nomic-embed-text" onChange={(e) => set("embedding", { ...s.embedding, model: e.target.value })} /></Field>
              <Field label="api_base"><input className="input" value={s.embedding.apiBase} placeholder="http://localhost:11434" onChange={(e) => set("embedding", { ...s.embedding, apiBase: e.target.value })} /></Field>
              <Field label="api_key_env"><input className="input" value={s.embedding.apiKeyEnv} onChange={(e) => set("embedding", { ...s.embedding, apiKeyEnv: e.target.value })} /></Field>
              <Field label="api_key"><input className="input" value={s.embedding.apiKey} onChange={(e) => set("embedding", { ...s.embedding, apiKey: e.target.value })} /></Field>
              <Field label="api_version"><input className="input" value={s.embedding.apiVersion} onChange={(e) => set("embedding", { ...s.embedding, apiVersion: e.target.value })} /></Field>
            </div>
          )}
        </div>
      </details>

      {/* 分块 / 图谱抽取 / 描述摘要 / 社区报告 / 聚类 — 同 <details> + Field + input[number|text|checkbox] 模式，字段对应 KbFormState */}
      <details><summary className="section-title">分块 Chunking</summary>
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Field label="size"><input className="input" type="number" value={s.chunking.size} onChange={(e) => set("chunking", { ...s.chunking, size: Number(e.target.value) })} /></Field>
          <Field label="overlap"><input className="input" type="number" value={s.chunking.overlap} onChange={(e) => set("chunking", { ...s.chunking, overlap: Number(e.target.value) })} /></Field>
          <Field label="encoding_model"><input className="input" value={s.chunking.encodingModel} onChange={(e) => set("chunking", { ...s.chunking, encodingModel: e.target.value })} /></Field>
        </div>
      </details>
      <details><summary className="section-title">图谱抽取 Extract Graph</summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="entity_types" hint="逗号分隔"><input className="input" value={s.extractGraph.entityTypes} placeholder="organization,person,geo" onChange={(e) => set("extractGraph", { ...s.extractGraph, entityTypes: e.target.value })} /></Field>
          <Field label="max_gleanings"><input className="input" type="number" value={s.extractGraph.maxGleanings} onChange={(e) => set("extractGraph", { ...s.extractGraph, maxGleanings: Number(e.target.value) })} /></Field>
        </div>
      </details>
      <details><summary className="section-title">描述摘要 Summarize</summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="max_length"><input className="input" type="number" value={s.summarize.maxLength} onChange={(e) => set("summarize", { ...s.summarize, maxLength: Number(e.target.value) })} /></Field>
          <Field label="max_input_tokens"><input className="input" type="number" value={s.summarize.maxInputTokens} onChange={(e) => set("summarize", { ...s.summarize, maxInputTokens: Number(e.target.value) })} /></Field>
        </div>
      </details>
      <details><summary className="section-title">社区报告 Community Reports</summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="structured_output" hint="DeepSeek 关闭"><label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={s.communityReports.structuredOutput} onChange={(e) => set("communityReports", { ...s.communityReports, structuredOutput: e.target.checked })} /> 结构化输出（json_schema）</label></Field>
          <Field label="max_length"><input className="input" type="number" value={s.communityReports.maxLength} onChange={(e) => set("communityReports", { ...s.communityReports, maxLength: Number(e.target.value) })} /></Field>
        </div>
      </details>
      <details><summary className="section-title">聚类 Clustering</summary>
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Field label="max_cluster_size"><input className="input" type="number" value={s.cluster.maxClusterSize} onChange={(e) => set("cluster", { ...s.cluster, maxClusterSize: Number(e.target.value) })} /></Field>
        </div>
      </details>

      {/* 高级：只读预览 + 覆盖框 */}
      <div>
        <button type="button" className="text-[13px] text-brand hover:underline" onClick={() => setShowAdvanced((v) => !v)}>{showAdvanced ? "隐藏高级" : "高级（原始 settings_yaml 覆盖）"}</button>
        {showAdvanced && (
          <div className="mt-3 space-y-2">
            <pre className="rounded-lg bg-surface-2 p-3 text-[11px] text-muted overflow-x-auto">{JSON.stringify(buildSettings(s), null, 2)}</pre>
            <Field label="原始 settings_yaml（非空则覆盖表单）"><textarea className="textarea h-24 font-mono text-[12px]" value={s.advancedOverride} onChange={(e) => set("advancedOverride", e.target.value)} placeholder='{"llm":{"model_provider":"..."}}' /></Field>
          </div>
        )}
      </div>

      {error && <p className="text-[13px] text-danger">创建失败：{error}</p>}
      <Button type="submit" variant="primary" disabled={busy} className="w-full"><IconPlus width={16} height={16} />{busy ? "创建中…" : "创建知识库"}</Button>
    </form>
  );
}
```
> `.section-title` 复用既有 `text-[13px] font-medium text-body`；<details> 内字段布局按 grid-2/3 视情况调整。

- [ ] **Step 4: 跑 build + 测试确认通过**

Run: `cd web && npm run build && npm test`
Expected: build 干净；前端测试全绿（含新 KbForm 测试 + 既有）。

- [ ] **Step 5: 全量回归（后端 + 前端）**

Run: `uv run pytest -q && (cd web && npm run build && npm test)`
Expected: 后端 189+ / 前端全绿；ruff/build 干净。

- [ ] **Step 6: 提交**

```bash
git add web/src/components/KbForm.tsx web/src/components/KbForm.test.tsx
git commit -m "feat(web): structured KB config form (LLM/Embedding/Chunking/Extract/Summarize/Reports/Cluster) — no hand-written JSON"
```

---

## Self-Review

- **Spec 覆盖**：bucket-2 后端接到 settings（Task 1 + 2）；前端表单覆盖所有可生效段（Task 3 DEFAULTS/buildSettings + Task 4 分区表单）；高级覆盖框（Task 4）；省略默认（Task 3 buildSettings，测试覆盖）；entity_types csv→list（Task 2 后端 + Task 3 前端，双保险）；bucket-3 不暴露（Global Constraints）。全覆盖。
- **占位符扫描**：无 TBD/TODO；每个 step 含完整代码（表单 JSX 用 `<details>` 块 + Field 模式，给出 LLM/Embedding/Chunking/Extract/Summarize/Reports/Cluster 各段完整结构）。
- **类型一致性**：`KbFormState`（Task 3）在 Task 4 表单 state 中使用；`buildSettings`/`DEFAULTS` 导出签名一致；后端 `build_default_adapter` 形参（Task 1）与 `build_adapter_from_settings` 透传（Task 2）一一对应（chunk_size/overlap/encoding_model/max_cluster_size/entity_types/max_gleanings/summarize_max_length/summarize_max_input_tokens/report_max_length）；settings key 路径（chunking/cluster_graph/extract_graph/summarize_descriptions/community_reports）前后端一致。
- **既有行为不变**：bucket-2 默认值=现状硬编码（Global Constraints）；Task 1 Step 5 回归断言默认值匹配；既有 189 后端测试不回归。
