# Provider-profile SSL verification toggle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-profile `ssl_verify` toggle (default True) so self-signed HTTPS endpoints work, wired through `ModelConfig.call_args={"ssl_verify": ...}` to all three litellm call paths (index LLM, index embedding, query LLM+embedding).

**Architecture:** New boolean column on `ProviderProfile` → surfaced in API + dashboard → read by `assemble_kb_settings` into the settings dict → consumed by the two index-time ModelConfig builders and the query-time config resolver, each injecting `call_args["ssl_verify"]` which graphrag-llm spreads into every `litellm.{completion,embedding}` call.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy + Alembic (SQLite) / graphrag-llm `ModelConfig` / React + TS + Vite + vitest.

**Spec:** `docs/superpowers/specs/2026-06-30-provider-ssl-verify-design.md`

## Global Constraints

- `ssl_verify` default is **True** everywhere (model column, API field, frontend checkbox = "校验开启/checked"). Disabling is opt-in.
- Dashboard copy is Chinese — match surrounding copy. New checkbox label: `校验 SSL 证书（自签证书可取消勾选）`.
- Backend tests use `FakeGraphAdapter`/in-memory; `pytest` config is `asyncio_mode=auto`, pythonpath includes `tests`. `tests/conftest.py` autouse-fixture sets a per-test Fernet key.
- Lint: `uv run ruff check .` (line-length 100, py311). Frontend: `cd web && npm test` (vitest).
- No graphrag imports outside `graphrag_adapter.py` / `graphrag_engine.py`.

---

## File Map

- `kb_platform/db/models_profile.py` — add `ssl_verify` column (Task 1)
- `kb_platform/db/repository.py` — `create_profile` kwarg (Task 1)
- `alembic/versions/0008_provider_ssl_verify.py` — create migration (Task 2)
- `kb_platform/graph/graphrag_adapter.py` — `assemble_kb_settings` + `_build_embed_model_config` + `build_adapter_from_settings` (Tasks 3, 4)
- `kb_platform/query/graphrag_engine.py` — `_resolve_config` query entries (Task 5)
- `kb_platform/api/models.py` — `ProfileCreate/Update/Out` (Task 6)
- `kb_platform/api/routes_profiles.py` — `_out()` + warning log (Task 6)
- `web/src/api/types.ts` — `ProviderProfile` + `ProfileCreate` (Task 7)
- `web/src/pages/ProviderProfilesPage.tsx` — checkbox + payload (Task 7)

---

### Task 1: `ssl_verify` column + `create_profile` kwarg

**Files:**
- Modify: `kb_platform/db/models_profile.py:20` (after `structured_output`)
- Modify: `kb_platform/db/repository.py:595-601` (`create_profile`)
- Test: `tests/test_profiles_api.py` (append)

**Interfaces:**
- Produces: `ProviderProfile.ssl_verify: bool` (default True); `Repository.create_profile(*, ..., ssl_verify: bool = True)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles_api.py`:

```python
def test_create_profile_persists_ssl_verify(tmp_path, monkeypatch):
    client, repo = _client(tmp_path, monkeypatch)
    r = client.post("/provider-profiles", json={
        "name": "SelfSigned", "kind": "embedding", "provider": "ollama",
        "model": "nomic-embed-text", "api_base": "https://emb.internal",
        "api_keys": ["ollama"], "ssl_verify": False,
    })
    assert r.status_code == 201, r.text
    with session_scope(repo.engine) as s:
        p = s.get(ProviderProfile, r.json()["id"])
        assert p.ssl_verify is False
    # default True when omitted
    pid2 = client.post("/provider-profiles", json={
        "name": "Cloud", "kind": "llm", "provider": "openai",
        "model": "gpt-4o-mini", "api_keys": ["sk-1"],
    }).json()["id"]
    with session_scope(repo.engine) as s:
        assert s.get(ProviderProfile, pid2).ssl_verify is True
```

Add `ProviderProfile` to the test file's imports (`from kb_platform.db.models import Base, KnowledgeBase` → also import `ProviderProfile`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profiles_api.py::test_create_profile_persists_ssl_verify -v`
Expected: FAIL — either `ProfileCreate` rejects `ssl_verify` (Task 6 not done) or the column/serialization is missing. (This task makes the column + repo accept it; the API Pydantic field lands in Task 6. To keep this task independently green, the test here hits the route, so it needs Task 6's API field too. **Therefore move this test's assertion to use the repo directly:**)

Replace the test body with a repo-level test that does not depend on the API layer:

```python
def test_create_profile_persists_ssl_verify(tmp_path, monkeypatch):
    from kb_platform.db.models import ProviderProfile
    _, repo = _client(tmp_path, monkeypatch)
    p = repo.create_profile(name="SelfSigned", kind="embedding", provider="ollama",
                            model="nomic-embed-text", api_base="https://emb.internal",
                            api_keys=["ollama"], ssl_verify=False)
    assert p.ssl_verify is False
    p2 = repo.create_profile(name="Cloud", kind="llm", provider="openai",
                             model="gpt-4o-mini", api_keys=["sk-1"])
    assert p2.ssl_verify is True  # default
```

Run: `uv run pytest tests/test_profiles_api.py::test_create_profile_persists_ssl_verify -v`
Expected: FAIL — `TypeError: create_profile() got an unexpected keyword argument 'ssl_verify'`.

- [ ] **Step 3: Add the column**

In `kb_platform/db/models_profile.py`, after the `structured_output` line:

```python
    structured_output: Mapped[bool] = mapped_column(Boolean, default=True)
    ssl_verify: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [ ] **Step 4: Add the `create_profile` kwarg**

In `kb_platform/db/repository.py`, change the signature and constructor (lines ~595-601):

```python
    def create_profile(self, *, name, kind, provider, model, api_base=None,
                       api_version=None, api_keys=None, structured_output=True,
                       ssl_verify=True) -> ProviderProfile:
        with session_scope(self.engine) as s:
            p = ProviderProfile(name=name, kind=kind, provider=provider, model=model,
                                api_base=api_base, api_version=api_version,
                                api_keys_enc=encrypt_values(api_keys or []),
                                structured_output=structured_output,
                                ssl_verify=ssl_verify)
            s.add(p)
            s.flush()
```

(`update_profile` is already a generic `setattr` loop guarded by `hasattr(p, k) and v is not None`; `ssl_verify` works there with no change — verified in Task 6.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_profiles_api.py::test_create_profile_persists_ssl_verify -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/db/models_profile.py kb_platform/db/repository.py tests/test_profiles_api.py
git commit -m "feat(db): add ProviderProfile.ssl_verify column (default True)"
```

---

### Task 2: Alembic migration `0008`

**Files:**
- Create: `alembic/versions/0008_provider_ssl_verify.py`
- Test: `tests/test_migrations.py` (append, or create if absent — check first)

**Interfaces:**
- Produces: migration `revision="0008"`, `down_revision="0007"`; adds `provider_profile.ssl_verify BOOLEAN NOT NULL DEFAULT true`.

- [ ] **Step 1: Write the failing test**

Check whether `tests/test_migrations.py` exists; if it does, follow its pattern. Otherwise append to a new `tests/test_migrations.py`:

```python
import subprocess, sys
from pathlib import Path

def _run(*args):
    subprocess.run([sys.executable, "-m", "alembic", *args], check=True,
                   cwd=Path(__file__).resolve().parents[1])

def test_migration_0008_adds_ssl_verify(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("ALEMBIC_DB", str(db))  # unused if alembic.ini hardcoded; see note
    _run("upgrade", "head")
    import sqlite3
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(provider_profile)")]
    assert "ssl_verify" in cols
```

**Note:** `alembic.ini` targets `sqlite:///./kb.db`. To redirect to a temp DB in tests, either (a) monkeypatch the sqlalchemy.url via `alembic`'s `-x` if the env.py supports it, or (b) simplest: run the migration against a throwaway copy and just assert the column exists after `alembic upgrade head` on the real target. If wiring a temp URL is non-trivial, write the test to call `op.add_column` logic indirectly: instead assert the migration module imports and has correct revision ids, **and** rely on the model test (Task 1) for the column semantics. Prefer option: assert revision metadata + a round-trip upgrade on a temp engine by setting `alembic.ini`'s url at runtime via `Config`. If the existing repo has no migration test, keep this task's test to: import the migration module and assert `revision == "0008"` and `down_revision == "0007"`, plus a manual `alembic upgrade head` verification step below.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_migrations.py -v` (or the chosen test name)
Expected: FAIL — module `0008_provider_ssl_verify` not found.

- [ ] **Step 3: Create the migration**

Create `alembic/versions/0008_provider_ssl_verify.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License
"""Add provider_profile.ssl_verify (default True).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_profile",
        sa.Column("ssl_verify", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("provider_profile", "ssl_verify")
```

- [ ] **Step 4: Verify migration applies**

Run: `uv run alembic upgrade head`
Expected: succeeds; `PRAGMA table_info(provider_profile)` includes `ssl_verify` with default 1. Then run the test from Step 1.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0008_provider_ssl_verify.py tests/test_migrations.py
git commit -m "feat(db): migration 0008 — provider_profile.ssl_verify"
```

---

### Task 3: `assemble_kb_settings` propagates `ssl_verify`

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py` — `assemble_kb_settings` (llm dict ~line 508-516; embedding dict ~line 533-540)
- Test: `tests/test_assemble_settings.py` (append)

**Interfaces:**
- Consumes: `ProviderProfile.ssl_verify` (Task 1).
- Produces: `settings["llm"]["ssl_verify"]: bool` and `settings["embedding"]["ssl_verify"]: bool`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assemble_settings.py`:

```python
def test_assemble_propagates_ssl_verify(tmp_path, monkeypatch):
    engine, repo = _seed_kb(tmp_path, monkeypatch)
    # flip both profiles to insecure via repo update path
    with session_scope(engine) as s:
        for pid in (1, 2):
            s.get(... )  # see note
    # simpler: seed an insecure embedding profile directly
```

Use this concrete version (seed insecure profiles directly):

```python
def test_assemble_propagates_ssl_verify(tmp_path, monkeypatch):
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase
    engine, repo = _seed_kb(tmp_path, monkeypatch)  # profiles id=1 (llm), id=2 (embedding)
    repo.update_profile(1, ssl_verify=False)
    repo.update_profile(2, ssl_verify=False)
    with session_scope(engine) as s:
        assembled = assemble_kb_settings(s.get(KnowledgeBase, 1), repo)
    assert assembled["llm"]["ssl_verify"] is False
    assert assembled["embedding"]["ssl_verify"] is False
```

(If `repo.update_profile` isn't imported in the test, it's `repo.update_profile` from the `Repository` returned by `_seed_kb`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_assemble_settings.py::test_assemble_propagates_ssl_verify -v`
Expected: FAIL — `KeyError: 'ssl_verify'` (assemble doesn't emit it yet).

- [ ] **Step 3: Implement**

In `assemble_kb_settings`, add `"ssl_verify": lp.ssl_verify,` to the `assembled["llm"]` dict (next to `"api_version": lp.api_version,`), and `"ssl_verify": ep.ssl_verify,` to the `assembled["embedding"]` dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_assemble_settings.py -v`
Expected: PASS (all assemble tests green).

- [ ] **Step 5: Commit**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_assemble_settings.py
git commit -m "feat(graph): assemble_kb_settings propagates ssl_verify"
```

---

### Task 4: Index-time ModelConfig builders inject `call_args["ssl_verify"]`

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py` — `_build_embed_model_config` (~line 270-277) and `build_adapter_from_settings` LLM ModelConfig (~line 448-455)
- Test: `tests/test_build_adapter_settings.py` (append)

**Interfaces:**
- Consumes: `settings["embedding"]["ssl_verify"]`, `settings["llm"]["ssl_verify"]` (Task 3).
- Produces: `ModelConfig.call_args["ssl_verify"]` on both index-time configs → spread into `litellm.embedding` / `litellm.completion` by graphrag-llm.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_build_adapter_settings.py`:

```python
def test_build_embed_model_config_carries_ssl_verify():
    from kb_platform.graph.graphrag_adapter import _build_embed_model_config
    mc = _build_embed_model_config({"embedding": {
        "model_provider": "ollama", "model": "nomic-embed-text",
        "api_key": "ollama", "ssl_verify": False}})
    assert mc.call_args["ssl_verify"] is False
    # default True when absent
    mc2 = _build_embed_model_config({"embedding": {
        "model_provider": "ollama", "model": "nomic-embed-text", "api_key": "ollama"}})
    assert mc2.call_args["ssl_verify"] is True


def test_build_adapter_passes_ssl_verify_to_llm_model_config(monkeypatch):
    import kb_platform.graph.graphrag_adapter as gra
    captured = {}
    def fake_build_default_adapter(*, model_config, embed_model_config=None, **kw):
        captured["llm"] = model_config
        return object()
    monkeypatch.setattr(gra, "build_default_adapter", fake_build_default_adapter)
    settings = ('{"llm":{"model_provider":"openai","model":"gpt-4o-mini",'
                '"api_keys":["sk-x"],"ssl_verify":false}}')
    gra.build_adapter_from_settings(settings_json=settings, data_root="/tmp/_x_")
    assert captured["llm"].call_args["ssl_verify"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_build_adapter_settings.py::test_build_embed_model_config_carries_ssl_verify tests/test_build_adapter_settings.py::test_build_adapter_passes_ssl_verify_to_llm_model_config -v`
Expected: FAIL — `KeyError: 'ssl_verify'` (call_args has no such key) / `mc.call_args` is `{}`.

- [ ] **Step 3: Implement — embedding config**

In `_build_embed_model_config`, add `call_args` to the returned `ModelConfig`:

```python
    return ModelConfig(
        type=emb.get("type", "litellm"),
        model_provider=provider,
        model=emb.get("model", "text-embedding-3-small"),
        api_base=emb.get("api_base"),
        api_version=emb.get("api_version"),
        api_key=resolved,
        call_args={"ssl_verify": emb.get("ssl_verify", True)},
    )
```

- [ ] **Step 4: Implement — LLM config**

In `build_adapter_from_settings`, add `call_args` to the LLM `ModelConfig` (~line 448):

```python
    model_config = ModelConfig(
        type=llm.get("type", "litellm"),
        model_provider=provider,
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=resolved_key,
        call_args={"ssl_verify": llm.get("ssl_verify", True)},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_adapter_settings.py -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_build_adapter_settings.py
git commit -m "feat(graph): inject ssl_verify into index-time LLM + embedding ModelConfig.call_args"
```

---

### Task 5: Query-time config resolver injects `call_args["ssl_verify"]`

**Files:**
- Modify: `kb_platform/query/graphrag_engine.py` — `_resolve_config` completion_models entry (~line 567-577) and embedding_models entry (~line 600-610)
- Test: `tests/test_graphrag_engine.py` (append)

**Interfaces:**
- Consumes: `settings["llm"]["ssl_verify"]`, `settings["embedding"]["ssl_verify"]` (the model_config dict passed to `GraphRagQueryEngine`).
- Produces: `GraphRagConfig.completion_models[...].call_args["ssl_verify"]` and `.embedding_models[...].call_args["ssl_verify"]`, which graphrag-llm spreads into litellm at query time.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graphrag_engine.py`:

```python
def test_resolve_config_carries_ssl_verify(tmp_path):
    qe = GraphRagQueryEngine(
        data_root=str(tmp_path),
        model_config={
            "llm": {"model_provider": "openai", "model": "gpt-4o-mini",
                    "api_key": "sk-x", "ssl_verify": False},
            "embedding": {"model_provider": "ollama", "model": "nomic-embed-text",
                          "api_key": "ollama", "ssl_verify": False},
        },
    )
    cfg = qe._resolve_config()
    assert cfg.completion_models["default_completion_model"].call_args["ssl_verify"] is False
    assert cfg.embedding_models["default_embedding_model"].call_args["ssl_verify"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graphrag_engine.py::test_resolve_config_carries_ssl_verify -v`
Expected: FAIL — `KeyError: 'ssl_verify'` (entry has no `call_args`).

- [ ] **Step 3: Implement — completion entry**

In `_resolve_config`, add to the `entry` dict built for `completion_models` (the one with `"model": llm.get("model", "gpt-4o-mini")`):

```python
                entry = {
                    "type": llm.get("type", "litellm"),
                    "model_provider": provider,
                    "model": llm.get("model", "gpt-4o-mini"),
                    "api_base": llm.get("api_base"),
                    "api_version": llm.get("api_version"),
                    "call_args": {"ssl_verify": llm.get("ssl_verify", True)},
                }
```

- [ ] **Step 4: Implement — embedding entry**

Add the same key to the embedding `entry` dict:

```python
                entry = {
                    "type": emb.get("type", "litellm"),
                    "model_provider": provider,
                    "model": emb.get("model", "text-embedding-3-small"),
                    "api_base": emb.get("api_base"),
                    "api_version": emb.get("api_version"),
                    "call_args": {"ssl_verify": emb.get("ssl_verify", True)},
                }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_graphrag_engine.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/query/graphrag_engine.py tests/test_graphrag_engine.py
git commit -m "feat(query): inject ssl_verify into query-time completion + embedding ModelConfig"
```

---

### Task 6: API contract (`ProfileCreate/Update/Out`) + insecure warning

**Files:**
- Modify: `kb_platform/api/models.py:245,261,272` (`ProfileCreate/Update/Out`)
- Modify: `kb_platform/api/routes_profiles.py` (`_out()`, create/update handlers)
- Test: `tests/test_profiles_api.py` (append)

**Interfaces:**
- Produces: `POST/PATCH /provider-profiles` accept `ssl_verify`; `ProfileOut.ssl_verify` is returned; insecure profiles log a warning.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles_api.py`:

```python
def test_profile_out_includes_ssl_verify_and_patch_persists(tmp_path, monkeypatch):
    client, repo = _client(tmp_path, monkeypatch)
    r = client.post("/provider-profiles", json={
        "name": "SelfSigned", "kind": "embedding", "provider": "ollama",
        "model": "nomic-embed-text", "api_keys": ["ollama"], "ssl_verify": False,
    })
    assert r.status_code == 201, r.text
    assert r.json()["ssl_verify"] is False
    # default True on omit
    assert client.post("/provider-profiles", json={
        "name": "Cloud", "kind": "llm", "provider": "openai",
        "model": "gpt-4o-mini", "api_keys": ["sk-1"],
    }).json()["ssl_verify"] is True
    # patch flips it
    pid = r.json()["id"]
    patched = client.patch(f"/provider-profiles/{pid}", json={"ssl_verify": True}).json()
    assert patched["ssl_verify"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profiles_api.py::test_profile_out_includes_ssl_verify_and_patch_persists -v`
Expected: FAIL — response missing `ssl_verify` (or 422 on the field).

- [ ] **Step 3: API models**

In `kb_platform/api/models.py`:
- `ProfileCreate`: add `ssl_verify: bool = True`
- `ProfileUpdate`: add `ssl_verify: bool | None = None`
- `ProfileOut`: add `ssl_verify: bool`

- [ ] **Step 4: Serialization + warning**

In `kb_platform/api/routes_profiles.py`:

Add a module logger near the top:
```python
import logging
logger = logging.getLogger(__name__)
```

In `_out()`, add `ssl_verify=p.ssl_verify,`.

In `create_profile` and `update_profile` handlers, after obtaining `p`, add:
```python
    if not p.ssl_verify:
        logger.warning("provider profile '%s' has SSL verification disabled", p.name)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_profiles_api.py -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_profiles.py tests/test_profiles_api.py
git commit -m "feat(api): expose ssl_verify on profile CRUD + warn when disabled"
```

---

### Task 7: Frontend — types + form checkbox

**Files:**
- Modify: `web/src/api/types.ts:35,46` (`ProviderProfile`, `ProfileCreate`)
- Modify: `web/src/pages/ProviderProfilesPage.tsx` (state ~line 21-23, payload ~line 40-46, checkbox near line 132, reset ~line 47)
- Test: `web/src/pages/ProviderProfilesPage.test.tsx` (fixtures + new test)

**Interfaces:**
- Produces: dashboard can create a profile with `ssl_verify`; checkbox toggles it; list shows state.

- [ ] **Step 1: Update the test (RED)**

In `web/src/pages/ProviderProfilesPage.test.tsx`, add `ssl_verify: false` / `ssl_verify: true` to the mock profile object(s) and the created-profile handler, then append:

```tsx
test("create sends ssl_verify from the checkbox", async () => {
  let captured: any = null;
  server.use(
    http.post("/provider-profiles", async ({ request }) => {
      captured = await request.json();
      return HttpResponse.json({ id: 99, ...captured, api_keys_count: 1, ssl_verify: captured.ssl_verify });
    }),
  );
  render(<MemoryRouter><ProviderProfilesPage /></MemoryRouter>);
  await screen.findByText("DeepSeek");
  fireEvent.change(screen.getByPlaceholderText("名称，如 DeepSeek"), { target: { value: "OllamaSelf" } });
  fireEvent.change(screen.getByPlaceholderText("provider"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByPlaceholderText("deepseek-chat"), { target: { value: "nomic-embed-text" } });
  fireEvent.change(screen.getByPlaceholderText("sk-..."), { target: { value: "ollama" } });
  fireEvent.click(screen.getByLabelText(/校验 SSL/));  // uncheck -> ssl_verify false
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));
  await waitFor(() => expect(captured).not.toBeNull());
  expect(captured.ssl_verify).toBe(false);
});
```

Also add `ssl_verify: true` to the existing `profiles` fixture and the existing POST handler's `created` object so the typechecks.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- ProviderProfilesPage`
Expected: FAIL — TS error (`ssl_verify` not on type) or `getByLabelText(/校验 SSL/)` not found.

- [ ] **Step 3: Types**

In `web/src/api/types.ts`, add to `ProviderProfile` and `ProfileCreate`:
```ts
  ssl_verify: boolean;          // ProviderProfile (required)
  ssl_verify?: boolean;         // ProfileCreate (optional; backend defaults true)
```
(Place `ssl_verify: boolean` inside `ProviderProfile`, and `ssl_verify?: boolean` inside `ProfileCreate`.)

- [ ] **Step 4: Form state + checkbox + payload**

In `web/src/pages/ProviderProfilesPage.tsx`:

Add state near the other `useState`:
```tsx
  const [sslVerify, setSslVerify] = useState(true);
```

Add to the `createProfile({...})` payload:
```tsx
        structured_output: structured,
        ssl_verify: sslVerify,
```

Add the checkbox in the form (show for **both** kinds — embedding can be self-signed too), e.g. after the `structured_output` label block:
```tsx
        <label className="mt-3 flex items-center gap-2 text-[13px] text-body">
          <input type="checkbox" checked={sslVerify} onChange={(e) => setSslVerify(e.target.checked)} />
          校验 SSL 证书（自签证书可取消勾选）
        </label>
```

Add `setSslVerify(true);` to the post-submit reset line (next to `setStructured`).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && npm test -- ProviderProfilesPage`
Expected: PASS.

- [ ] **Step 6: Build + Commit**

Run: `cd web && npm run build`
Expected: tsc + vite build succeed.

```bash
git add web/src/api/types.ts web/src/pages/ProviderProfilesPage.tsx web/src/pages/ProviderProfilesPage.test.tsx
git commit -m "feat(web): ssl_verify checkbox on provider-profile form"
```

---

### Task 8: Full verification

- [ ] **Step 1: Backend full suite + lint**

Run: `uv run ruff check . && uv run pytest -q`
Expected: ruff clean; all tests pass.

- [ ] **Step 2: Frontend suite + build**

Run: `cd web && npm test && npm run build`
Expected: vitest pass; build succeed.

- [ ] **Step 3: Migration round-trip**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: both succeed; `ssl_verify` column dropped then restored.

- [ ] **Step 4: Smoke (manual)** — start server + worker, create an embedding profile with the checkbox unchecked pointing at a self-signed endpoint, run an incremental index, confirm the embed step no longer fails on TLS. (Only if a self-signed endpoint is available; otherwise skip and rely on unit coverage.)

---

## Self-Review (completed)

- **Spec coverage:** column+default (T1), migration (T2), assemble propagation (T3), index LLM+embedding call_args (T4), query LLM+embedding call_args (T5), API CRUD + warning (T6), UI (T7), verification (T8). All spec sections mapped.
- **Placeholders:** none — every code step shows actual code; the migration-test harness is called out explicitly with a fallback if `tests/test_migrations.py` infrastructure is absent.
- **Type/name consistency:** `ssl_verify` (snake_case, matching litellm + backend) used uniformly; frontend `ssl_verify` matches the API field. `create_profile(..., ssl_verify=True)` (T1) matches the test call. `call_args["ssl_verify"]` asserted identically in T4/T5.
