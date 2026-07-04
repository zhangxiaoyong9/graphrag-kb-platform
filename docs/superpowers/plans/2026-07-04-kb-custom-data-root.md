# KB 创建自定义 data_root + 默认按 KB 隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users specify a per-KB `data_root` at creation, and make the default `{global}/{kb.id}` so each new KB is isolated in its own directory (fixing the latent shared-directory collision).

**Architecture:** `KbCreate` gains an optional `data_root`; `create_kb` validates it (absolute, no `..`) or defaults to `{resolve(global)}/{kb.id}` via a two-step flush (NOT NULL placeholder → flush for id → overwrite). `KbDetailOut` exposes it; `KbUpdate` does not (create-only). Frontend `KbForm` adds an optional create-only input; the overview page shows the path read-only. No Alembic migration (the column already exists).

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy 2 (backend); React + TS + Vite + Vitest + msw (frontend).

**Spec:** `docs/superpowers/specs/2026-07-04-kb-custom-data-root-design.md`

## Global Constraints

- **Default `data_root` for a new KB (when the field is omitted) = `str(Path(app.state.data_root).resolve() / str(kb.id))`** — per-KB isolation.
- A user-supplied `data_root` is used **verbatim** (no normalize/resolve, no per-KB suffix).
- Validation: `os.path.isabs(path)` AND `".." not in Path(path).parts`, else HTTP 400 with a Chinese message (`data_root 必须为绝对路径` / `data_root 不得含 .. `).
- `data_root` is **create-only** — `KbUpdate` does not declare it; pydantic ignores it on PATCH (the KB's data_root is unchanged).
- `KbDetailOut` carries `data_root: str`; `KbOut` (list) does NOT (keep the list lean).
- No Alembic migration (`KnowledgeBase.data_root` column already exists).
- Backend: ruff line-length 100, py311. Dashboard copy is Chinese. Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Each task runs its own test command and commits only after green.

---

## File Structure

- Modify `kb_platform/api/models.py` — `KbCreate.data_root` + `KbDetailOut.data_root`.
- Modify `kb_platform/api/routes_kbs.py` — `validate_data_root` helper + `create_kb` two-step flush + the GET route populates `data_root` on `KbDetailOut`.
- Modify `web/src/api/types.ts` — `KbCreate.data_root?`, `KbDetail.data_root`.
- Modify `web/src/components/KbForm.tsx` — create-only `data_root` input + state + body.
- Modify `web/src/pages/KbOverviewPage.tsx` — read-only path display.
- Tests: `tests/test_api_kbs.py` (extend), `web/src/components/KbForm.test.tsx` (extend).

---

## Task 1: Backend — `data_root` on `KbCreate`/`KbDetailOut` + per-KB default + validation

**Files:**
- Modify: `kb_platform/api/models.py` (`KbCreate`, `KbDetailOut`)
- Modify: `kb_platform/api/routes_kbs.py` (`create_kb` at ~line 149; add `validate_data_root`; the GET `/kbs/{id}` route that builds `KbDetailOut`)
- Test: `tests/test_api_kbs.py` (extend with the cases below)

**Interfaces:**
- Consumes: `request.app.state.data_root` (the server's global root); the existing `session_scope` / `KnowledgeBase` model.
- Produces: `KbCreate.data_root: str | None`; `KbDetailOut.data_root: str`; `create_kb` now stores `{global}/{id}` by default or the validated custom path; `validate_data_root(path: str) -> None` (raises `HTTPException(400)`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_kbs.py` (add `from pathlib import Path` to the imports at the top if not already present):

```python
from pathlib import Path  # add to top-of-file imports if missing


def test_create_kb_default_data_root_is_per_kb_isolated(client, tmp_path):
    """Omitting data_root -> {global_resolve}/{kb.id} (per-KB isolation)."""
    r = client.post("/kbs", json={"name": "kb1", "method": "standard",
                                  "settings_yaml": "{}", "llm_profile_id": 1})
    assert r.status_code == 201
    kid = r.json()["id"]
    detail = client.get(f"/kbs/{kid}").json()
    expected = str(Path(str(tmp_path)).resolve() / str(kid))
    assert detail["data_root"] == expected


def test_create_kb_custom_data_root_used_verbatim(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                  "llm_profile_id": 1, "data_root": "/abs/some/kb-dir"})
    assert r.status_code == 201
    detail = client.get(f"/kbs/{r.json()['id']}").json()
    assert detail["data_root"] == "/abs/some/kb-dir"


def test_create_kb_rejects_relative_data_root(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                  "llm_profile_id": 1, "data_root": "relative/path"})
    assert r.status_code == 400
    assert "绝对路径" in r.json()["detail"]


def test_create_kb_rejects_traversal_data_root(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                  "llm_profile_id": 1, "data_root": "/abs/../etc"})
    assert r.status_code == 400
    assert ".." in r.json()["detail"]


def test_update_kb_ignores_data_root(client):
    """data_root is create-only: a PATCH body carrying data_root is ignored."""
    r = client.post("/kbs", json={"name": "kb1", "method": "standard",
                                  "settings_yaml": "{}", "llm_profile_id": 1})
    kid = r.json()["id"]
    before = client.get(f"/kbs/{kid}").json()["data_root"]
    # Mirror a valid PATCH body (see Note below); add data_root, which KbUpdate doesn't declare.
    client.patch(f"/kbs/{kid}", json={"name": "kb1", "method": "standard", "settings_yaml": "{}",
                                       "llm_profile_id": 1, "data_root": "/abs/other"})
    after = client.get(f"/kbs/{kid}").json()["data_root"]
    assert after == before
```

> **Note for the implementer (PATCH test body):** open `tests/test_api_kbs.py` and `kb_platform/api/models.py:KbUpdate` first. If an existing PATCH test shows a valid full body, mirror it and just add `"data_root": "/abs/other"`. The point is: the PATCH is accepted (not 422) and the KB's `data_root` is unchanged afterward. If `KbUpdate` makes any of `name/method/settings_yaml/llm_profile_id` required, the body above already supplies them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_kbs.py -k "data_root or update_kb_ignores" -q`
Expected: FAIL — `KbCreate` has no `data_root` (the relative/`..` cases may 422 instead of 400; the default case returns the global root, not `{global}/{id}`; `KbDetailOut` has no `data_root` so `detail["data_root"]` KeyErrors).

- [ ] **Step 3: Add the model fields**

In `kb_platform/api/models.py`:

Add to `KbCreate` (after `neo4j_profile_id`):
```python
    neo4j_profile_id: int | None = None
    data_root: str | None = None
```

Add to `KbDetailOut` (after `settings: dict`):
```python
class KbDetailOut(KbOut):
    """GET /kbs/{id}: adds the (redacted) parsed settings + resolved profiles."""

    settings: dict
    data_root: str
    llm_profile: ProfileRef | None = None
```

(Do **not** add `data_root` to `KbUpdate` or `KbOut`.)

- [ ] **Step 4: Add `validate_data_root` + rewrite `create_kb`**

In `kb_platform/api/routes_kbs.py`:

Ensure the file imports `os`, `Path` (add `import os` and `from pathlib import Path` to the top import block if missing; `HTTPException` is already imported).

Add the helper near the other small helpers in the file (e.g. just above `create_kb`):
```python
def validate_data_root(path: str) -> None:
    """A user-supplied data_root must be absolute and free of `..` traversal.
    Raises HTTPException(400) otherwise. The path is used verbatim (no resolve)."""
    if not os.path.isabs(path):
        raise HTTPException(status_code=400, detail="data_root 必须为绝对路径")
    if ".." in Path(path).parts:
        raise HTTPException(status_code=400, detail="data_root 不得含 .. ")
```

Replace the body of `create_kb` (currently `routes_kbs.py:149`) with:
```python
def create_kb(payload: KbCreate, request: Request) -> KbOut:
    repo = request.app.state.repo
    _require_profile(repo, payload.llm_profile_id, "llm")
    if payload.embedding_profile_id is not None:
        _require_profile(repo, payload.embedding_profile_id, "embedding")
    if payload.neo4j_profile_id is not None:
        _require_profile(repo, payload.neo4j_profile_id, "neo4j")
    if payload.data_root is not None:
        validate_data_root(payload.data_root)
    fallback_ids = _validate_fallback_ids(repo, payload.llm_fallback_profile_ids, payload.llm_profile_id)
    settings = _parse_settings(payload.settings_yaml)
    global_root = str(Path(request.app.state.data_root).resolve())
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(
            name=payload.name,
            method=payload.method,
            settings_json=settings,
            data_root=request.app.state.data_root,  # NOT NULL placeholder; overwritten once id is known
            llm_profile_id=payload.llm_profile_id,
            embedding_profile_id=payload.embedding_profile_id,
            llm_fallback_profile_ids=json.dumps(fallback_ids) if fallback_ids else None,
            neo4j_profile_id=payload.neo4j_profile_id,
        )
        s.add(kb)
        s.flush()  # assigns kb.id
        kb.data_root = payload.data_root if payload.data_root is not None else f"{global_root}/{kb.id}"
        s.flush()
        return KbOut(id=kb.id, name=kb.name, method=kb.method)
```

- [ ] **Step 5: Populate `data_root` on the GET `/kbs/{id}` response**

Find the GET `/kbs/{id}` route handler (the one returning `KbDetailOut`; `grep -n "KbDetailOut" kb_platform/api/routes_kbs.py`). Add `data_root=kb.data_root,` to its `KbDetailOut(...)` construction.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_kbs.py -q`
Expected: PASS (all 5 new tests + the existing KB tests).

- [ ] **Step 7: Lint**

Run: `uv run ruff check kb_platform/api/models.py kb_platform/api/routes_kbs.py tests/test_api_kbs.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_kbs.py tests/test_api_kbs.py
git commit -m "$(cat <<'EOF'
feat(kbs): per-KB data_root default + create-time custom path

KbCreate.data_root (optional): omit -> {global_resolve}/{kb.id} so each new
KB is isolated in its own directory (was: every KB shared the global root,
colliding parquet/vectors). Provide an absolute path -> used verbatim.
validate_data_root rejects relative / '..' paths (400). KbDetailOut exposes
it; KbUpdate does not (create-only). No migration (column already exists).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Frontend — `KbForm` create-only input + detail display

**Files:**
- Modify: `web/src/api/types.ts` (`KbCreate`, `KbDetail`)
- Modify: `web/src/components/KbForm.tsx` (state + create-only input + body)
- Modify: `web/src/pages/KbOverviewPage.tsx` (read-only path line)
- Test: `web/src/components/KbForm.test.tsx` (extend)

**Interfaces:**
- Consumes: backend `KbCreate.data_root?` + `KbDetailOut.data_root` (Task 1).
- Produces: a `KbForm` that sends `data_root` only on create (when filled) and a read-only path display on the KB overview.

- [ ] **Step 1: Write the failing test**

Append to `web/src/components/KbForm.test.tsx`. (First read the file to copy its existing render/submit pattern — provider-profile mocking, `createKb` interception, etc. The new test varies only the data_root input visibility + body inclusion.)

```tsx
test("create mode shows data_root input and sends it when filled", async () => {
  const captured: any[] = [];
  // Reuse the existing happy-path test's mocks (profiles, prompt defaults, etc.).
  // Add a capture on POST /kbs:
  server.use(
    http.post("/kbs", async ({ request }) => {
      captured.push(await request.json());
      return HttpResponse.json({ id: 1, name: "kb1", method: "standard" });
    }),
  );
  render(<KbForm onCreated={() => {}} />);
  // ...wait for profiles to load + fill name + pick llm profile, mirroring the existing test
  const dr = await screen.findByPlaceholderText(/留空 = 自动按 KB 隔离/);
  fireEvent.change(dr, { target: { value: "/abs/custom/dir" } });
  // ...submit the form the way the existing test does
  await waitFor(() => expect(captured.length).toBe(1));
  expect(captured[0].data_root).toBe("/abs/custom/dir");
});

test("edit mode does not show the data_root input", async () => {
  // Render KbForm with a kb prop (edit mode), using the existing edit-mode test's fixture.
  render(<KbForm kb={EDIT_KB_FIXTURE} onSaved={() => {}} />);
  await screen.findByDisplayValue(EDIT_KB_FIXTURE.name);
  expect(screen.queryByPlaceholderText(/留空 = 自动按 KB 隔离/)).not.toBeInTheDocument();
});
```

> **Note for the implementer:** the snippet is a shape reference. Open `KbForm.test.tsx` and copy the existing create-mode + edit-mode tests' mock plumbing (provider-profile list, prompt defaults, `createKb`/`updateKb` interception, the field interactions that select a profile + type a name). Produce two new tests that differ only in: (a) asserting the data_root placeholder input appears in create mode and the filled value is sent in the POST body; (b) asserting the data_root input does NOT appear in edit mode. Do not invent new mock plumbing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/components/KbForm.test.tsx`
Expected: FAIL — no data_root input rendered.

- [ ] **Step 3: Extend the TS types**

In `web/src/api/types.ts`:

`KbCreate` (line ~59) — add after `llm_fallback_profile_ids`:
```ts
export interface KbCreate {
  name: string;
  method?: string;
  settings_yaml?: string;
  llm_profile_id: number;
  embedding_profile_id?: number | null;
  min_unit_success_ratio?: number;
  llm_fallback_profile_ids?: number[];
  data_root?: string | null;
}
```

`KbDetail` (line ~68) — add `data_root: string`:
```ts
export interface KbDetail extends KbOut {
  settings: Record<string, unknown>;
  data_root: string;
  llm_profile: ProfileRef | null;
  embedding_profile: ProfileRef | null;
  llm_fallback_profile_ids?: number[];
  llm_fallback_profiles?: ProfileRef[];
}
```

- [ ] **Step 4: Add the `data_root` state + create-only input + body in `KbForm.tsx`**

In `web/src/components/KbForm.tsx`:

Add state next to the other `useState` calls (near `name`):
```tsx
  const [name, setName] = useState(kb?.name ?? "");
  const [dataRoot, setDataRoot] = useState("");
```

In the `createKb({...})` body (inside the `else` branch of submit, ~line 126), spread `data_root` only when filled:
```tsx
        const created = await createKb({
          name,
          method: s.method,
          settings_yaml,
          llm_profile_id: llmProfileId,
          embedding_profile_id: embeddingProfileId,
          min_unit_success_ratio: parseFloat(s.minRatio),
          llm_fallback_profile_ids: llmFallbackIds,
          ...(dataRoot.trim() ? { data_root: dataRoot.trim() } : {}),
        });
```

(Do **not** change the `updateKb` body — `data_root` is create-only.)

Add the input to the form JSX, gated on `!isEdit` (place it near the name field, inside an existing `<Field>` / form grid section; match the surrounding input idiom — use `className="input"` and a `<Field label=...>` wrapper if that's the house style):
```tsx
          {!isEdit && (
            <Field label="数据目录（可选）">
              <input
                className="input"
                placeholder="留空 = 自动按 KB 隔离"
                value={dataRoot}
                onChange={(e) => setDataRoot(e.target.value)}
                aria-label="data_root"
              />
            </Field>
          )}
```

> **Note for the implementer:** confirm the `Field` import is present (`import { Button, Field } from "./ui";` — it is, per the file head). Place the input in the form grid where the other top-level fields (name / method) live; if the layout uses a single column for those, follow the same single-column placement. The exact grid cell is not load-bearing — visibility-gating and the `aria-label="data_root"` are.

- [ ] **Step 5: Add the read-only path display in `KbOverviewPage.tsx`**

In `web/src/pages/KbOverviewPage.tsx`, immediately after the top stat-grid `<div className="grid grid-cols-2 ... lg:grid-cols-4">...</div>` block (line ~33), add:
```tsx
      <div className="text-[12px] text-muted">
        数据目录：<span className="font-mono text-ink/70">{kb?.data_root ?? "—"}</span>
      </div>
```

(`kb` is the `KbDetail` loaded by the page; `data_root` is now on the type after Step 3.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/components/KbForm.test.tsx`
Expected: PASS (new tests + existing ones).

- [ ] **Step 7: Type-check + build**

Run: `cd web && npm run build`
Expected: `tsc -b && vite build` succeeds.

- [ ] **Step 8: Commit**

```bash
git add web/src/api/types.ts web/src/components/KbForm.tsx web/src/components/KbForm.test.tsx web/src/pages/KbOverviewPage.tsx
git commit -m "$(cat <<'EOF'
feat(web): optional data_root input on KB create + read-only display

KbForm gains a create-only 数据目录 input (omitted on edit; data_root is
create-only). KbOverviewPage shows the resolved path read-only. KbCreate/
KbDetail types carry data_root.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Integration gate

**Files:** none (verification only).

- [ ] **Step 1: Full backend suite + lint**

Run: `uv run ruff check . && uv run python -m pytest -q`
Expected: ruff clean; all tests PASS (including the 5 new `test_api_kbs.py` cases).

- [ ] **Step 2: Full frontend suite + build**

Run: `cd web && npm test && npm run build`
Expected: vitest all green; `tsc -b && vite build` succeeds.

- [ ] **Step 3: Migration sanity**

Run: `uv run alembic current`
Expected: still `0011 (head)` (this feature adds no migration).

---

## Self-Review

**Spec coverage** — every spec requirement maps to a task:

- `KbCreate.data_root` (optional) → **Task 1** Step 3.
- Default `{global_resolve}/{kb.id}` (two-step flush) → **Task 1** Step 4 (`global_root` + `kb.data_root = ... f"{global_root}/{kb.id}"`).
- Custom path used verbatim + `validate_data_root` (absolute, no `..`, 400) → **Task 1** Step 4 (+ tests Step 1).
- `KbDetailOut.data_root` (GET exposes it); `KbOut`/`KbUpdate` do NOT → **Task 1** Steps 3 + 5; create-only enforced by `KbUpdate` not declaring it (test `test_update_kb_ignores_data_root`).
- Frontend `KbCreate.data_root?` + `KbDetail.data_root` → **Task 2** Step 3.
- `KbForm` create-only input + body inclusion → **Task 2** Step 4 (+ tests Step 1).
- Read-only display on the overview → **Task 2** Step 5.
- No Alembic migration → **Task 3** Step 3 (verifies head unchanged).

**Placeholder scan:** every code step shows complete code. The two "Note for the implementer" callouts (Task 1 Step 1 PATCH body mirroring; Task 2 Step 1 test-fixture mirroring; Task 2 Step 4 Field placement) are concrete locate-and-reuse instructions for plumbing the plan cannot fully see, not hand-waves.

**Type consistency:** `data_root: str | None = None` (pydantic `KbCreate`) ↔ `data_root?: string | null` (TS `KbCreate`) ↔ `data_root: str` (pydantic `KbDetailOut`) ↔ `data_root: string` (TS `KbDetail`). `validate_data_root(path: str) -> None` is identical between Task 1 Step 4 (definition) and Step 1 (the relative/`..` tests assert its 400 messages). The default `{global_root}/{kb.id}` format is identical between Task 1 Step 4 (impl) and Step 1 (`test_create_kb_default_data_root_is_per_kb_isolated` computes `str(Path(str(tmp_path)).resolve() / str(kid))`).

**Scope:** two implementation tasks + one gate; each independently testable. Task 2 depends on Task 1 (the types/body shape). Linear ordering respects that. No graphrag/graphrag-llm seam change; no migration.
