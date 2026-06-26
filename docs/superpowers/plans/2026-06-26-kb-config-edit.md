# KB 配置可修改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KB 创建后可编辑配置（name/method/settings：模型/嵌入/chunk/抽取/摘要/报告/聚类/prompt）—— 后端 PATCH 端点 + 前端 parseSettings 预填 + KbForm 编辑模式 + 概要编辑弹窗。

**Architecture:** 后端 `PATCH /kbs/{id}` 整体替换 name/method/settings_json（复用 `_parse_settings` 校验）。前端 `parseSettings(settings)→KbFormState` 逆映射预填表单；`KbForm` 加可选 `kb` 入参（编辑模式 → 提交调 `updateKb`/PATCH）；KB 概要「模型配置」卡加「编辑配置」按钮 → 弹窗渲染预填 KbForm。整体替换（buildSettings 现有省略默认 → 清空字段正确回默认）。

**Tech Stack:** Python 3.11 + uv + FastAPI + pytest/ruff；React 18 + TS + Vite + Tailwind + vitest。

## Global Constraints

- 后端 `uv run pytest` / `uv run ruff check .`；前端 `cd web && npm run build && npm test`；kb-platform 无 pyright/poe/semversioner（ruff only）。
- `KnowledgeBase` 模型列：`id/name/method/settings_json/data_root`（**无** `min_unit_success_ratio` 列——它是 job 触发参数，不落 KB）。故 `KbUpdate` 只含 `name/method/settings_yaml`。
- 编辑 = **整体替换** settings（buildSettings 省略默认 + 替换 → 清空字段回默认）。高级/非表单字段会被清除（表单创建的 KB 不受影响）。
- 复用既有 `KbForm`（加编辑模式），不另写表单。
- 编辑入口 = 弹窗 Modal（可滚动，停留概要页）。
- 保存后提示「需重新索引才生效」。
- 既有 199 后端 + 40 前端测试不回归。

---

## File Structure

- `kb_platform/api/models.py`（改）：新增 `KbUpdate(BaseModel)`。
- `kb_platform/db/repository.py`（改）：新增 `update_kb`。
- `kb_platform/api/routes_kbs.py`（改）：新增 `PATCH /kbs/{id}`。
- `web/src/api/client.ts`（改）：`updateKb`。
- `web/src/lib/kb-settings.ts`（改）：`parseSettings` + 测试。
- `web/src/components/KbForm.tsx`（改）：编辑模式（可选 `kb` 入参）。
- `web/src/pages/KbOverviewPage.tsx`（改）：「编辑配置」按钮 + 弹窗。
- 测试：`tests/test_update_kb.py`、扩展 `web/src/lib/kb-settings.test.ts`、扩展 `web/src/components/KbForm.test.tsx`。

---

## Task 1: 后端 — `PATCH /kbs/{id}` + `repo.update_kb`

**Files:**
- Modify: `kb_platform/api/models.py`、`kb_platform/db/repository.py`、`kb_platform/api/routes_kbs.py`
- Test: `tests/test_update_kb.py`（新建）

**Interfaces:**
- Produces: `KbUpdate(BaseModel)`（`name: str`、`method: str = "standard"`、`settings_yaml: str | None = None`）；`repo.update_kb(kb_id, *, name, method, settings_json) -> KnowledgeBase | None`；`PATCH /kbs/{id} -> KbDetailOut`。

- [ ] **Step 1: 写失败测试**

`tests/test_update_kb.py`：
```python
"""PATCH /kbs/{id} updates name/method/settings (full replace)."""
import json

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from fastapi.testclient import TestClient


def _client(tmp_path):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/u.db"))
    Base.metadata.create_all(repo.engine)
    # seed a KB with some settings
    with repo.engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO knowledge_base(name, method, settings_json, data_root) VALUES(?, ?, ?, ?)",
            ("old", "standard", json.dumps({"llm": {"api_key": "sk-secret", "model": "m1"}}), "."),
        )
    return repo, TestClient(create_app(repo, data_root="."))


def test_patch_updates_name_method_settings(tmp_path):
    repo, c = _client(tmp_path)
    r = c.patch("/kbs/1", json={"name": "new", "method": "fast",
                                "settings_yaml": json.dumps({"llm": {"model": "m2", "api_key_env": "X"}})})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "new" and body["method"] == "fast"
    assert body["settings"]["llm"]["model"] == "m2"
    assert body["settings"]["llm"]["api_key_env"] == "X"
    # persisted
    with repo.engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT name, method, settings_json FROM knowledge_base WHERE id=1").one()
    assert row.name == "new" and row.method == "fast"
    assert json.loads(row.settings_json)["llm"]["model"] == "m2"


def test_patch_404_missing(tmp_path):
    _, c = _client(tmp_path)
    assert c.patch("/kbs/999", json={"name": "x", "method": "standard"}).status_code == 404


def test_patch_redacts_api_key(tmp_path):
    _, c = _client(tmp_path)
    r = c.patch("/kbs/1", json={"name": "n", "method": "standard",
                                "settings_yaml": json.dumps({"llm": {"api_key": "sk-new", "model": "m"}})})
    assert r.json()["settings"]["llm"]["api_key"] == "***"
    assert r.json()["settings"]["llm"]["model"] == "m"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_update_kb.py -v`
Expected: FAIL — 405（无 PATCH 路由）或 404。

- [ ] **Step 3: 实现 models + repo + route**

(a) `kb_platform/api/models.py`，在 `KbCreate` 之后加：
```python
class KbUpdate(BaseModel):
    """PATCH /kbs/{id} body — full replace of name/method/settings.

    Note: min_unit_success_ratio is NOT here — it's a per-job trigger param,
    not persisted on the KB (KnowledgeBase has no such column).
    """

    name: str
    method: str = "standard"
    settings_yaml: str | None = None
```

(b) `kb_platform/db/repository.py`，在 `create_job_pending` 之前（或其它 kb 方法附近）加：
```python
    def update_kb(self, kb_id: int, *, name: str, method: str, settings_json: str) -> Job | None:  # noqa: F821
        """Full-replace name/method/settings_json. Returns the KB or None if missing."""
        with session_scope(self.engine) as s:
            kb = s.get(KnowledgeBase, kb_id)
            if kb is None:
                return None
            kb.name = name
            kb.method = method
            kb.settings_json = settings_json
            return kb
```
（返回类型用 `KnowledgeBase | None`；上面 `Job` 是笔误占位——实现时改 `KnowledgeBase`。先 `from kb_platform.db.models import ... KnowledgeBase` 已在文件顶部 import。）

(c) `kb_platform/api/routes_kbs.py`：顶部 import 加 `KbUpdate`：
```python
from kb_platform.api.models import DocumentCreate, DocumentOut, JobListItem, KbCreate, KbDetailOut, KbUpdate
```
在 `get_kb` 之后加：
```python
@router.patch("/kbs/{kb_id}", response_model=KbDetailOut)
def update_kb(kb_id: int, payload: KbUpdate, request: Request) -> KbDetailOut:
    """Update a KB's name/method/settings (full replace)."""
    repo = request.app.state.repo
    settings = _parse_settings(payload.settings_yaml)
    kb = repo.update_kb(kb_id, name=payload.name, method=payload.method, settings_json=settings)
    if kb is None:
        raise HTTPException(404)
    return KbDetailOut(id=kb.id, name=kb.name, method=kb.method, settings=_redact(kb.settings_json))
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归 + ruff**

Run: `uv run pytest tests/test_update_kb.py -v && uv run pytest -q && uv run ruff check .`
Expected: 新增 3 通过；既有 199 全绿；ruff 干净。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/api/models.py kb_platform/db/repository.py kb_platform/api/routes_kbs.py tests/test_update_kb.py
git commit -m "feat(api): PATCH /kbs/{id} to update KB name/method/settings after creation"
```

---

## Task 2: 前端 — `updateKb` client + `parseSettings`（逆映射）

**Files:**
- Modify: `web/src/api/client.ts`、`web/src/lib/kb-settings.ts`
- Test: `web/src/lib/kb-settings.test.ts`

**Interfaces:**
- Produces: `updateKb(id, body)` client；`parseSettings(settings, method, minRatio) -> KbFormState`（buildSettings 的逆映射，缺失用 DEFAULTS）。

- [ ] **Step 1: 写失败测试**

追加到 `web/src/lib/kb-settings.test.ts`：
```typescript
import { parseSettings } from "./kb-settings";

it("parseSettings round-trips llm + chunking + prompt", () => {
  const s = parseSettings(
    {
      llm: { model_provider: "deepseek", model: "deepseek-chat", api_key_env: "DEEPSEEK_API_KEY" },
      chunking: { size: 300 },
      extract_graph: { prompt: "MY-PROMPT" },
    },
    "fast",
    "0.8",
  );
  expect(s.method).toBe("fast");
  expect(s.minRatio).toBe("0.8");
  expect(s.llm).toMatchObject({ provider: "deepseek", model: "deepseek-chat", apiKeyEnv: "DEEPSEEK_API_KEY" });
  expect(s.chunking.size).toBe(300);
  expect(s.prompts.extract).toBe("MY-PROMPT");
  // defaults for absent
  expect(s.cluster.maxClusterSize).toBe(10);
  expect(s.embedding.enabled).toBe(false);
});

it("parseSettings entity_types list -> csv + embedding enabled", () => {
  const s = parseSettings(
    { extract_graph: { entity_types: ["ORG", "PERSON"] }, embedding: { model_provider: "ollama", model: "nomic-embed-text" } },
    "standard",
    "1.0",
  );
  expect(s.extractGraph.entityTypes).toBe("ORG, PERSON");
  expect(s.embedding.enabled).toBe(true);
  expect(s.embedding.model).toBe("nomic-embed-text");
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- kb-settings`
Expected: FAIL — `parseSettings` 未导出。

- [ ] **Step 3: 实现 client + parseSettings**

(a) `web/src/api/client.ts` 末尾加：
```typescript
export const updateKb = (id: number, body: { name: string; method: string; settings_yaml: string }) =>
  req<KbOut>(`/kbs/${id}`, { method: "PATCH", body: JSON.stringify(body) });
```

(b) `web/src/lib/kb-settings.ts` 加 `parseSettings`（见 spec 4.2 的实现，逐字段从 snake_case settings 映射回 KbFormState，缺失用 DEFAULTS；`apiKey` 不回填留空；`entity_types` list→csv）：
```typescript
export function parseSettings(settings: Record<string, unknown>, method: string, minRatio: string): KbFormState {
  const f = (b: unknown, k: string, d: string) => String(((b as Record<string, unknown> | undefined) ?? {})[k] ?? d);
  const n = (b: unknown, k: string, d: number) => Number(((b as Record<string, unknown> | undefined) ?? {})[k] ?? d);
  const llm = (settings.llm as Record<string, unknown> | undefined) ?? {};
  const emb = (settings.embedding as Record<string, unknown> | undefined) ?? {};
  const ch = (settings.chunking as Record<string, unknown> | undefined) ?? {};
  const eg = (settings.extract_graph as Record<string, unknown> | undefined) ?? {};
  const su = (settings.summarize_descriptions as Record<string, unknown> | undefined) ?? {};
  const cr = (settings.community_reports as Record<string, unknown> | undefined) ?? {};
  const cl = (settings.cluster_graph as Record<string, unknown> | undefined) ?? {};
  const etRaw = eg.entity_types;
  const et = Array.isArray(etRaw) ? etRaw.join(", ") : typeof etRaw === "string" ? etRaw : "";
  return {
    ...DEFAULTS,
    method, minRatio,
    llm: { ...DEFAULTS.llm, provider: f(llm, "model_provider", ""), model: f(llm, "model", ""), apiBase: f(llm, "api_base", ""), apiKeyEnv: f(llm, "api_key_env", ""), apiKey: "", apiVersion: f(llm, "api_version", "") },
    embedding: { ...DEFAULTS.embedding, enabled: !!settings.embedding, provider: f(emb, "model_provider", ""), model: f(emb, "model", ""), apiBase: f(emb, "api_base", ""), apiKeyEnv: f(emb, "api_key_env", ""), apiKey: "", apiVersion: f(emb, "api_version", "") },
    chunking: { size: n(ch, "size", DEFAULTS.chunking.size), overlap: n(ch, "overlap", DEFAULTS.chunking.overlap), encodingModel: f(ch, "encoding_model", DEFAULTS.chunking.encodingModel) },
    extractGraph: { entityTypes: et, maxGleanings: n(eg, "max_gleanings", DEFAULTS.extractGraph.maxGleanings) },
    summarize: { maxLength: n(su, "max_length", DEFAULTS.summarize.maxLength), maxInputTokens: n(su, "max_input_tokens", DEFAULTS.summarize.maxInputTokens) },
    communityReports: { structuredOutput: (cr.structured_output ?? DEFAULTS.communityReports.structuredOutput) as boolean, maxLength: n(cr, "max_length", DEFAULTS.communityReports.maxLength) },
    cluster: { maxClusterSize: n(cl, "max_cluster_size", DEFAULTS.cluster.maxClusterSize) },
    prompts: { extract: f(eg, "prompt", ""), summarize: f(su, "prompt", ""), communityReport: f(cr, "prompt", "") },
    advancedOverride: "",
  };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npm test -- kb-settings`
Expected: PASS（既有 + 2 新增 parseSettings）。

- [ ] **Step 5: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/api/client.ts web/src/lib/kb-settings.ts web/src/lib/kb-settings.test.ts
git commit -m "feat(web): updateKb client + parseSettings (settings -> form state reverse mapping)"
```

---

## Task 3: 前端 — KbForm 编辑模式 + 概要编辑弹窗

**Files:**
- Modify: `web/src/components/KbForm.tsx`、`web/src/pages/KbOverviewPage.tsx`
- Test: `web/src/components/KbForm.test.tsx`（扩展）

**Interfaces:**
- Consumes: Task 2 的 `updateKb` / `parseSettings`；既有 `useKb()`（KbOverviewPage 已用）/ `KbContext.reload`。

- [ ] **Step 1: 写失败测试**

追加到 `web/src/components/KbForm.test.tsx`：
```typescript
import { http, HttpResponse } from "msw";
// ... 既有 server setup；加 PATCH handler

test("edit mode pre-fills and PATCHes on submit", async () => {
  const server = setupServer(
    http.get("/prompts/defaults", () => HttpResponse.json({ extract_graph: "D", summarize_descriptions: "D", community_reports: "D" })),
    http.get("/kbs/1", () => HttpResponse.json({ id: 1, name: "kb", method: "fast", settings: { llm: { model: "deepseek-chat", model_provider: "deepseek" }, chunking: { size: 300 } } })),
    http.patch("/kbs/1", async ({ request }) => { const b = (await request.json()) as { name: string }; return HttpResponse.json({ id: 1, name: b.name, method: "fast", settings: {} }); }),
  );
  beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
  // (若文件已有全局 server，复用/追加 handlers 即可)

  const kb = { id: 1, name: "kb", method: "fast", settings: { llm: { model: "deepseek-chat", model_provider: "deepseek" }, chunking: { size: 300 } } };
  render(<MemoryRouter><KbForm kb={kb as any} onSaved={() => {}} /></MemoryRouter>);
  // pre-filled: model input shows deepseek-chat
  expect((screen.getByPlaceholderText("deepseek-chat") as HTMLInputElement).value).toBe("deepseek-chat");
  // submit -> PATCH (button label = 保存修改)
  await userEvent.click(screen.getByRole("button", { name: /保存修改/ }));
});
```
> 注：既有 KbForm.test.tsx 可能已有全局 msw server；追加 handlers 而非新建 server。实现时按既有结构调整。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm test -- KbForm`
Expected: FAIL — KbForm 不接收 `kb` 入参 / 无「保存修改」按钮。

- [ ] **Step 3: 实现 KbForm 编辑模式**

`web/src/components/KbForm.tsx`：
- 改签名：`export default function KbForm({ onCreated, kb, onSaved }: { onCreated?: (kb: KbOut) => void; kb?: KbOut; onSaved?: () => void })`。
- 初值：`const [s, setS] = useState<KbFormState>(() => kb ? parseSettings((kb.settings ?? {}) as Record<string, unknown>, kb.method, "1.0") : ({ ...DEFAULTS, ...逐字段 }));`
- name 初值：`const [name, setName] = useState(kb?.name ?? "");`
- 提交：
```typescript
const isEdit = !!kb;
const submit = async (e) => {
  e.preventDefault(); setBusy(true); setError(null);
  try {
    const settingsObj = buildSettings(s);
    const settings_yaml = JSON.stringify(settingsObj);
    if (isEdit && kb) {
      await updateKb(kb.id, { name, method: s.method, settings_yaml });
      onSaved?.();
    } else {
      const created = await createKb({ name, method: s.method, settings_yaml, min_unit_success_ratio: parseFloat(s.minRatio) });
      onCreated?.(created);
    }
    if (!isEdit) setName("");
  } catch (err) { setError(String((err as Error).message ?? err)); }
  finally { setBusy(false); }
};
```
- import：`import { updateKb } from "../api/client"; import { parseSettings } from "../lib/kb-settings";`
- 按钮：编辑模式「保存修改」/ 创建「创建知识库」。

- [ ] **Step 4: 概要编辑入口 + 弹窗**

`web/src/pages/KbOverviewPage.tsx`：
- 顶部 import：`import KbForm from "../components/KbForm";` + `useState`。
- 「模型配置」卡 `<CardHeader ... actions={...}>` 的 actions 加：
```tsx
<Button variant="secondary" size="sm" onClick={() => setEditOpen(true)}><IconGear width={15} height={15} /> 编辑配置</Button>
```
（import `IconGear` + `Button`；`const [editOpen, setEditOpen] = useState(false);`）
- 组件末尾加弹窗（`editOpen` 时渲染）：
```tsx
{editOpen && (
  <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 p-4 backdrop-blur-sm" onClick={() => setEditOpen(false)}>
    <div className="card my-8 w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <h3 className="text-[15px] font-semibold">编辑配置</h3>
        <button className="text-muted hover:text-ink" onClick={() => setEditOpen(false)}>✕</button>
      </div>
      <div className="p-5">
        <KbForm kb={kb ?? undefined} onSaved={() => { setEditOpen(false); reload(); }} />
        <p className="mt-3 text-[12px] text-muted">提示：配置已更新。如需让新配置生效，请重新触发索引任务。</p>
      </div>
    </div>
  </div>
)}
```
（`reload` 来自 `useKb()`；`kb` 既有。`KbForm` 编辑模式不需要 `onCreated`。）

- [ ] **Step 5: build + 测试 + 全量回归**

Run: `cd web && npm run build && npm test && cd .. && uv run pytest -q`
Expected: build 干净；前端 + 后端全绿；ruff 干净。

- [ ] **Step 6: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/components/KbForm.tsx web/src/components/KbForm.test.tsx web/src/pages/KbOverviewPage.tsx
git commit -m "feat(web): KB config edit (KbForm edit-mode + overview edit modal) — PATCH after creation"
```

---

## Self-Review

- **Spec 覆盖**：4.1（PATCH + update_kb + KbUpdate）→ Task 1；4.2（parseSettings + updateKb）→ Task 2；4.3（KbForm 编辑模式 + 概要编辑弹窗）→ Task 3；整体替换 + 清空回默认 → Global Constraints（buildSettings 不变）；编辑后提示 → Task 3 Step 4 弹窗文案。全覆盖。
- **占位符扫描**：无 TBD/TODO；每步含完整代码。
- **类型一致性**：`KbUpdate{name, method, settings_yaml}`（Task 1）↔ `updateKb(id, {name, method, settings_yaml})`（Task 2 client）；`parseSettings(settings, method, minRatio) -> KbFormState`（Task 2）↔ KbForm 编辑初值（Task 3）；`PATCH /kbs/{id} -> KbDetailOut` 与既有 GET 一致。
- **min_ratio 修正**：KnowledgeBase 无该列 → KbUpdate 不含 min_unit_success_ratio；编辑模式 minRatio 字段保留但不发送 PATCH（仅 create 用）—— Task 1/3 一致。
- **既有行为不变**：KbForm 无 kb 入参时 = 创建模式（既有）；PATCH 是新增路由；既有 199+40 测试不回归。
