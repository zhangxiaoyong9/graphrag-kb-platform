# Provider Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-KB provider/key config with global named provider profiles (selected at KB creation) and frontend-entered, encrypted API keys; auto-migrate existing KBs.

**Architecture:** New `provider_profile` table (llm/embedding connection + Fernet-encrypted key list). KBs reference `llm_profile_id` + optional `embedding_profile_id` and keep only content params in `settings_json`. A new `assemble_kb_settings(kb, repo)` resolves profiles → full settings for graphrag (graphrag coupling unchanged). Master key auto-generated to a file next to the DB. Existing KBs migrated by an alembic data backfill.

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, Alembic, `cryptography.fernet.Fernet` (already installed), Pydantic, React 18 + TypeScript + Vite + Tailwind, Vitest, pytest.

## Global Constraints

- `cryptography` 49.0.0 is already a dependency — do NOT add it.
- API keys are **frontend-entered only**, encrypted with Fernet, stored in `provider_profile.api_keys_enc` (JSON array of tokens). The env-var key path (`api_key_env`, `api_key_envs`, `{PROVIDER}_API_KEY` fallback) is **removed**.
- Master key: env `KB_SECRET_KEY` if set, else auto-generated at `<dirname(db_path)>/.kb_secret_key` (chmod 600). Never committed.
- `build_adapter_from_settings` keeps its signature `(settings_json, data_root, api_key=None)` but reads decrypted literals from `llm.api_keys` instead of env names.
- Graphrag coupling (`build_default_adapter`, `_resolve_config`, ModelConfig) is unchanged.
- KB `settings_json` after the split holds ONLY: chunking, extract_graph, summarize_descriptions, community_reports (max_length only — structured_output moves to the llm profile), cluster_graph, prompts, query_prompts.
- Python files start with the copyright/license header used elsewhere in the repo.
- README EN+ZH must be updated; all backend (pytest, ruff) + frontend (vitest, tsc, build) + E2E (`npm run e2e`) must be green before merge.

---

## File Structure

### Backend
- Create: `kb_platform/db/crypto.py` — Fernet wrapper + master-key bootstrap.
- Create: `kb_platform/db/models_profile.py` — `ProviderProfile` model (kept out of the large `models.py`).
- Modify: `kb_platform/db/models.py` — add `llm_profile_id` / `embedding_profile_id` FKs on `KnowledgeBase`; import the new model.
- Modify: `kb_platform/db/repository.py` — profile CRUD + referencing-KB lookup; KB create/update accept profile ids.
- Modify: `kb_platform/graph/graphrag_adapter.py` — read `llm.api_keys` literals; new `build_adapter_for_kb(kb, repo)`.
- Modify: `kb_platform/worker.py` — `adapter_factory = build_adapter_for_kb`.
- Modify: `kb_platform/api/models.py` — profile Pydantic models; KB create/update/detail models.
- Create: `kb_platform/api/routes_profiles.py` — `/provider-profiles` CRUD.
- Modify: `kb_platform/api/app.py` — register the profiles router.
- Modify: `kb_platform/api/routes_kbs.py` — KB create/update store profile ids + content settings; detail returns resolved profiles.
- Create: `alembic/versions/0005_provider_profiles.py` — schema + idempotent data backfill.
- Create: `tests/test_crypto.py`, `tests/test_profiles_api.py`, `tests/test_assemble_settings.py`, `tests/test_migration_provider_profiles.py`.

### Frontend
- Modify: `web/src/api/types.ts` — `ProviderProfile`, update `KbCreate`/`KbDetail`.
- Modify: `web/src/api/client.ts` — profile CRUD; update KB create/update payloads.
- Create: `web/src/pages/ProviderProfilesPage.tsx` (+ `.test.tsx`) — list/CRUD + dynamic key list.
- Modify: `web/src/components/KbForm.tsx` (+ `.test.tsx`) — replace llm/embedding sections with profile dropdowns.
- Modify: `web/src/lib/nav.ts` + `web/src/App.tsx` — add the 系统管理 → Provider 配置 route.
- Modify: `web/src/lib/kb-settings.ts` — drop llm/embedding fields from `KbFormState`/`DEFAULTS`/`buildSettings`.

### E2E + docs
- Modify: `scripts/e2e_server.py` — seed a profile; baseline KB references it.
- Modify: `web/e2e/fixtures.ts` — `createKbViaApi` passes `llm_profile_id`; add `createProfileViaApi`.
- Modify: `web/e2e/create-kb.spec.ts` (and any state-changer) — use the seeded profile.
- Modify: `README.md` + `README.zh.md` — config model + key handling.

---

## Task 1: Crypto helper

**Files:**
- Create: `kb_platform/db/crypto.py`
- Test: `tests/test_crypto.py`

**Interfaces:**
- Produces: `encrypt_values(values: list[str]) -> str` (JSON array of tokens), `decrypt_values(token_json: str) -> list[str]`, `master_key_source() -> str` (for diagnostics).

- [ ] **Step 1: Write failing test**

Create `tests/test_crypto.py`:
```python
import json

from kb_platform.db.crypto import encrypt_values, decrypt_values


def test_round_trip_uses_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_SECRET_KEY", raising=False)  # force file path
    key_file = tmp_path / ".kb_secret_key"
    monkeypatch.setattr("kb_platform.db.crypto._key_file_path", lambda: str(key_file))
    token_json = encrypt_values(["sk-aaa", "sk-bbb"])
    tokens = json.loads(token_json)
    assert len(tokens) == 2
    assert all("." in t for t in tokens)
    assert "sk-aaa" not in token_json        # tokens are not the plaintext
    assert key_file.exists()                 # auto-generated file created
    assert decrypt_values(token_json) == ["sk-aaa", "sk-bbb"]


def test_env_key_used_and_no_file(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("KB_SECRET_KEY", key)
    key_file = tmp_path / ".kb_secret_key"
    monkeypatch.setattr("kb_platform.db.crypto._key_file_path", lambda: str(key_file))
    token_json = encrypt_values(["sk-x"])
    assert decrypt_values(token_json) == ["sk-x"]
    assert not key_file.exists()  # env key means no file is created
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crypto.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kb_platform.db.crypto'`.

- [ ] **Step 3: Implement**

Create `kb_platform/db/crypto.py`:
```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Fernet encryption for provider-profile API keys.

Master key source: env ``KB_SECRET_KEY`` if set; otherwise an auto-generated
key persisted to a file next to the DB (``<dirname(db_path)>/.kb_secret_key``,
chmod 600). The file path is resolved lazily from the configured DB url.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet

_KEY_FILE_NAME = ".kb_secret_key"


def _key_file_path() -> str:
    """Resolve the master-key file path from the configured DB url.

    Defaults to ``./.kb_secret_key`` when no DB url is discoverable. Tests
    monkeypatch this function.
    """
    db_url = os.environ.get("KB_DB_URL") or "kb.db"
    # strip sqlite:/// prefix
    path = db_url.replace("sqlite:///", "")
    import os as _os

    return _os.path.join(_os.path.dirname(_os.path.abspath(path)) or ".", _KEY_FILE_NAME)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env_key = os.environ.get("KB_SECRET_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode())
    path = _key_file_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Fernet(f.read().strip())
    key = Fernet.generate_key()
    with open(path, "wb") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return Fernet(key)


def encrypt_values(values: list[str]) -> str:
    """Encrypt a list of plaintext strings → JSON array of Fernet tokens."""
    if not values:
        return "[]"
    f = _fernet()
    return json.dumps([f.encrypt(v.encode()).decode() for v in values])


def decrypt_values(token_json: str) -> list[str]:
    """Decrypt a JSON array of Fernet tokens → list of plaintext strings."""
    tokens = json.loads(token_json or "[]")
    if not tokens:
        return []
    f = _fernet()
    return [f.decrypt(t.encode()).decode() for t in tokens]


def master_key_source() -> str:
    return "env:KB_SECRET_KEY" if os.environ.get("KB_SECRET_KEY", "").strip() else _key_file_path()
```

Wire the DB url so `_key_file_path` sees it: in `kb_platform/server.py` and `kb_platform/worker.py`, before starting, set `os.environ.setdefault("KB_DB_URL", f"sqlite:///{db}")` next to the existing `db = sys.argv[1] ...` line. (Both files already parse `db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crypto.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add kb_platform/db/crypto.py kb_platform/server.py kb_platform/worker.py tests/test_crypto.py
git commit -m "feat(db): Fernet crypto helper for provider-profile keys"
```

---

## Task 2: ProviderProfile model + repository + CRUD API

**Files:**
- Create: `kb_platform/db/models_profile.py`
- Modify: `kb_platform/db/models.py` (import the new model so `Base.metadata` includes it)
- Modify: `kb_platform/db/repository.py`
- Modify: `kb_platform/api/models.py`
- Create: `kb_platform/api/routes_profiles.py`
- Modify: `kb_platform/api/app.py`
- Test: `tests/test_profiles_api.py`

**Interfaces:**
- Consumes: `encrypt_values` / `decrypt_values` from Task 1.
- Produces:
  - `ProviderProfile` ORM model (columns: id, name, kind, provider, model, api_base, api_version, api_keys_enc, structured_output).
  - Repo: `create_profile(...)`, `get_profile(id)`, `list_profiles(kind=None)`, `update_profile(...)`, `delete_profile(id) -> bool`, `profiles_referencing_kbs(profile_id) -> list[int]`.
  - API: `GET/POST/PATCH/DELETE /provider-profiles`.

- [ ] **Step 1: Write failing test**

Create `tests/test_profiles_api.py`:
```python
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def _client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    return TestClient(create_app(repo, data_root=str(tmp_path))), repo


def test_create_list_profile_masks_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_SECRET_KEY", "z")  # invalid fernet -> set real below
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    client, _ = _client(tmp_path)
    r = client.post("/provider-profiles", json={
        "name": "DeepSeek", "kind": "llm", "provider": "deepseek",
        "model": "deepseek-chat", "api_keys": ["sk-aaa", "sk-bbb"],
        "structured_output": False,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["api_keys_count"] == 2
    assert "api_keys" not in body  # never plaintext
    assert "api_keys_enc" not in body

    lst = client.get("/provider-profiles?kind=llm").json()
    assert len(lst) == 1 and lst[0]["name"] == "DeepSeek"


def test_delete_referenced_profile_is_409(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    client, repo = _client(tmp_path)
    pid = client.post("/provider-profiles", json={
        "name": "DeepSeek", "kind": "llm", "provider": "deepseek",
        "model": "deepseek-chat", "api_keys": ["sk-a"], "structured_output": False,
    }).json()["id"]
    # attach a KB to it directly
    from kb_platform.db.models import KnowledgeBase
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(name="k1", method="standard", settings_json="{}",
                            data_root=str(tmp_path), llm_profile_id=pid))
    r = client.delete(f"/provider-profiles/{pid}")
    assert r.status_code == 409
    assert 1 in r.json()["referencing_kbs"]


def test_patch_replaces_keys_only_when_sent(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    client, _ = _client(tmp_path)
    pid = client.post("/provider-profiles", json={
        "name": "P", "kind": "llm", "provider": "openai", "model": "gpt-4o-mini",
        "api_keys": ["sk-1"], "structured_output": True,
    }).json()["id"]
    # patch without api_keys -> count unchanged
    assert client.patch(f"/provider-profiles/{pid}", json={"model": "gpt-4o"}).json()["api_keys_count"] == 1
    # patch with [] -> cleared
    assert client.patch(f"/provider-profiles/{pid}", json={"api_keys": []}).json()["api_keys_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profiles_api.py -q`
Expected: FAIL — no `/provider-profiles` route / no `llm_profile_id` column.

- [ ] **Step 3: Implement the ORM model**

Create `kb_platform/db/models_profile.py`:
```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""ProviderProfile: reusable LLM/embedding connection + encrypted keys."""
from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from kb_platform.db.models import Base


class ProviderProfile(Base):
    __tablename__ = "provider_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)  # "llm" | "embedding"
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    api_base: Mapped[str | None] = mapped_column(String, nullable=True)
    api_version: Mapped[str | None] = mapped_column(String, nullable=True)
    api_keys_enc: Mapped[str] = mapped_column(Text, default="[]")
    structured_output: Mapped[bool] = mapped_column(Boolean, default=True)
```

In `kb_platform/db/models.py`:
- Add to the `KnowledgeBase` class (after `data_root`):
```python
    llm_profile_id: Mapped[int | None] = mapped_column(ForeignKey("provider_profile.id"), nullable=True)
    embedding_profile_id: Mapped[int | None] = mapped_column(ForeignKey("provider_profile.id"), nullable=True)
```
- At the bottom of `models.py`, add `from kb_platform.db.models_profile import ProviderProfile  # noqa: E402,F401  (register on Base.metadata)`.

- [ ] **Step 4: Implement repository methods**

In `kb_platform/db/repository.py`, add:
```python
from kb_platform.db.models_profile import ProviderProfile
from kb_platform.db.crypto import encrypt_values, decrypt_values

# ... inside class Repository:

def create_profile(self, *, name, kind, provider, model, api_base=None,
                   api_version=None, api_keys=None, structured_output=True) -> ProviderProfile:
    with session_scope(self.engine) as s:
        p = ProviderProfile(name=name, kind=kind, provider=provider, model=model,
                            api_base=api_base, api_version=api_version,
                            api_keys_enc=encrypt_values(api_keys or []),
                            structured_output=structured_output)
        s.add(p); s.flush()
        return p

def get_profile(self, profile_id: int) -> ProviderProfile | None:
    with session_scope(self.engine) as s:
        return s.get(ProviderProfile, profile_id)

def list_profiles(self, kind: str | None = None) -> list[ProviderProfile]:
    with session_scope(self.engine) as s:
        q = select(ProviderProfile)
        if kind:
            q = q.where(ProviderProfile.kind == kind)
        return list(s.scalars(q))

def update_profile(self, profile_id: int, **fields) -> ProviderProfile | None:
    api_keys = fields.pop("api_keys", None)  # None = unchanged
    with session_scope(self.engine) as s:
        p = s.get(ProviderProfile, profile_id)
        if p is None:
            return None
        for k, v in fields.items():
            if hasattr(p, k) and v is not None:
                setattr(p, k, v)
        if api_keys is not None:  # [] clears, [...] replaces
            p.api_keys_enc = encrypt_values(api_keys)
        s.flush()
        return p

def delete_profile(self, profile_id: int) -> bool:
    with session_scope(self.engine) as s:
        p = s.get(ProviderProfile, profile_id)
        if p is None:
            return False
        s.delete(p)
        return True

def referencing_kbs(self, profile_id: int) -> list[int]:
    with session_scope(self.engine) as s:
        rows = s.scalars(select(KnowledgeBase).where(
            (KnowledgeBase.llm_profile_id == profile_id)
            | (KnowledgeBase.embedding_profile_id == profile_id)
        ))
        return [k.id for k in rows]

def profile_key_count(self, profile_id: int) -> int:
    p = self.get_profile(profile_id)
    return len(decrypt_values(p.api_keys_enc)) if p else 0
```

- [ ] **Step 5: Implement Pydantic models + routes**

In `kb_platform/api/models.py` add:
```python
class ProfileCreate(BaseModel):
    name: str
    kind: Literal["llm", "embedding"]
    provider: str
    model: str
    api_base: str | None = None
    api_version: str | None = None
    api_keys: list[str] = []
    structured_output: bool = True

class ProfileUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    api_keys: list[str] | None = None  # None=unchanged, []=clear
    structured_output: bool | None = None

class ProfileOut(BaseModel):
    id: int
    name: str
    kind: str
    provider: str
    model: str
    api_base: str | None = None
    api_version: str | None = None
    structured_output: bool
    api_keys_count: int
```

Create `kb_platform/api/routes_profiles.py`:
```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
from fastapi import APIRouter, HTTPException, Query, Request

from kb_platform.api.models import ProfileCreate, ProfileOut, ProfileUpdate

router = APIRouter()


def _out(repo, p) -> ProfileOut:
    return ProfileOut(id=p.id, name=p.name, kind=p.kind, provider=p.provider,
                      model=p.model, api_base=p.api_base, api_version=p.api_version,
                      structured_output=p.structured_output, api_keys_count=repo.profile_key_count(p.id))


@router.get("/provider-profiles", response_model=list[ProfileOut])
def list_profiles(request: Request, kind: str | None = Query(default=None)):
    repo = request.app.state.repo
    return [_out(repo, p) for p in repo.list_profiles(kind=kind)]


@router.post("/provider-profiles", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileCreate, request: Request):
    repo = request.app.state.repo
    try:
        p = repo.create_profile(**payload.model_dump())
    except Exception:
        raise HTTPException(409, "profile name already exists")
    return _out(repo, p)


@router.patch("/provider-profiles/{pid}", response_model=ProfileOut)
def update_profile(pid: int, payload: ProfileUpdate, request: Request):
    repo = request.app.state.repo
    p = repo.update_profile(pid, **payload.model_dump(exclude_unset=True))
    if p is None:
        raise HTTPException(404)
    return _out(repo, p)


@router.delete("/provider-profiles/{pid}", status_code=204)
def delete_profile(pid: int, request: Request):
    repo = request.app.state.repo
    refs = repo.referencing_kbs(pid)
    if refs:
        raise HTTPException(409, detail={"referencing_kbs": refs})
    if not repo.delete_profile(pid):
        raise HTTPException(404)
```

In `kb_platform/api/app.py`, register it: add `from kb_platform.api.routes_profiles import router as profiles_router` and `app.include_router(profiles_router)` next to the other `include_router` calls.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_profiles_api.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add kb_platform/db/models_profile.py kb_platform/db/models.py kb_platform/db/repository.py kb_platform/api/models.py kb_platform/api/routes_profiles.py kb_platform/api/app.py tests/test_profiles_api.py
git commit -m "feat(api): provider-profile CRUD with encrypted keys"
```

---

## Task 3: KB profile refs + assemble_kb_settings + adapter seam

**Files:**
- Modify: `kb_platform/db/repository.py` (KB create/update accept profile ids)
- Modify: `kb_platform/graph/graphrag_adapter.py` (read `llm.api_keys`; add `build_adapter_for_kb`)
- Modify: `kb_platform/worker.py`
- Test: `tests/test_assemble_settings.py`

**Interfaces:**
- Consumes: `ProviderProfile` + `decrypt_values`.
- Produces: `assemble_kb_settings(kb, repo) -> dict`, `build_adapter_for_kb(kb, repo) -> GraphRagAdapter`.

- [ ] **Step 1: Write failing test**

Create `tests/test_assemble_settings.py`:
```python
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.graph.graphrag_adapter import assemble_kb_settings


def _setup(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    llm = repo.create_profile(name="DS", kind="llm", provider="deepseek",
                              model="deepseek-chat", api_keys=["sk-a"], structured_output=False)
    emb = repo.create_profile(name="Ollama", kind="embedding", provider="ollama",
                              model="nomic-embed-text", api_base="http://localhost:11434",
                              api_keys=["ollama"])
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard",
                            settings_json='{"chunking":{"size":800},"community_reports":{"max_length":1500}}',
                            data_root=str(tmp_path), llm_profile_id=llm.id, embedding_profile_id=emb.id))
        s.flush()
        kb_id = s.get(KnowledgeBase, 1).id
    return repo, kb_id


def test_assemble_merges_profile_and_content(tmp_path, monkeypatch):
    repo, kb_id = _setup(tmp_path, monkeypatch)
    kb = repo.get_kb(kb_id) if hasattr(repo, "get_kb") else None
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        assembled = assemble_kb_settings(kb, repo)
    assert assembled["llm"]["model"] == "deepseek-chat"
    assert assembled["llm"]["api_keys"] == ["sk-a"]
    assert assembled["embedding"]["model"] == "nomic-embed-text"
    assert assembled["community_reports"]["structured_output"] is False  # from llm profile
    assert assembled["community_reports"]["max_length"] == 1500  # from KB
    assert assembled["chunking"]["size"] == 800


def test_assemble_omits_embedding_when_null(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db"); Base.metadata.create_all(engine)
    repo = Repository(engine)
    llm = repo.create_profile(name="DS", kind="llm", provider="deepseek", model="deepseek-chat", api_keys=["sk"])
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}",
                            data_root=str(tmp_path), llm_profile_id=llm.id))
    with session_scope(engine) as s:
        assembled = assemble_kb_settings(s.get(KnowledgeBase, 1), repo)
    assert "embedding" not in assembled


def test_assemble_raises_without_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    import pytest
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db"); Base.metadata.create_all(engine)
    repo = Repository(engine)
    llm = repo.create_profile(name="DS", kind="llm", provider="deepseek", model="deepseek-chat", api_keys=[])
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}",
                            data_root=str(tmp_path), llm_profile_id=llm.id))
    with pytest.raises(ValueError):
        with session_scope(engine) as s:
            assemble_kb_settings(s.get(KnowledgeBase, 1), repo)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_assemble_settings.py -q`
Expected: FAIL — `assemble_kb_settings` not defined.

- [ ] **Step 3: Implement assemble + adapter seam**

In `kb_platform/graph/graphrag_adapter.py`, replace the key-resolution block inside `build_adapter_from_settings` (the `resolved_key = ...` and `api_key_envs = ...` lines) with:
```python
    api_keys = list(llm.get("api_keys") or [])
    if not api_keys:
        raise ValueError(
            f"KB has no API keys for provider '{provider}'. "
            "Add keys to its LLM provider profile."
        )
    resolved_key = api_keys[0]
    extra_keys = api_keys[1:]
```
(removes the env-var fallbacks). Then add a new function at the bottom of the file:
```python
def assemble_kb_settings(kb, repo) -> dict:
    """Resolve a KB's provider profiles + content settings -> full settings dict."""
    import json
    from kb_platform.db.crypto import decrypt_values

    content = json.loads(kb.settings_json or "{}")
    if kb.llm_profile_id is None:
        raise ValueError("KB has no LLM provider profile.")
    lp = repo.get_profile(kb.llm_profile_id)
    api_keys = decrypt_values(lp.api_keys_enc)
    if not api_keys:
        raise ValueError(f"LLM profile '{lp.name}' has no API keys.")
    assembled = {
        "llm": {
            "type": "litellm", "model_provider": lp.provider, "model": lp.model,
            "api_base": lp.api_base, "api_version": lp.api_version, "api_keys": api_keys,
        },
        "chunking": content.get("chunking", {}),
        "extract_graph": content.get("extract_graph", {}),
        "summarize_descriptions": content.get("summarize_descriptions", {}),
        "cluster_graph": content.get("cluster_graph", {}),
        "community_reports": {
            "structured_output": lp.structured_output,
            "max_length": (content.get("community_reports") or {}).get("max_length", 2000),
        },
    }
    if content.get("prompts"):
        assembled["prompts"] = content["prompts"]
    if kb.embedding_profile_id is not None:
        ep = repo.get_profile(kb.embedding_profile_id)
        assembled["embedding"] = {
            "type": "litellm", "model_provider": ep.provider, "model": ep.model,
            "api_base": ep.api_base, "api_version": ep.api_version,
            "api_key": (decrypt_values(ep.api_keys_enc) or [None])[0],
        }
    return assembled


def build_adapter_for_kb(kb, repo):
    import json
    return build_adapter_from_settings(json.dumps(assemble_kb_settings(kb, repo)), kb.data_root)
```

In `kb_platform/worker.py`, change the production `adapter_factory`:
```python
    from kb_platform.graph.graphrag_adapter import build_adapter_for_kb
    ...
    run_worker(
        repo=repo,
        adapter_factory=lambda kb: build_adapter_for_kb(kb, repo),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_assemble_settings.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add kb_platform/graph/graphrag_adapter.py kb_platform/worker.py tests/test_assemble_settings.py
git commit -m "feat(graph): assemble_kb_settings + build_adapter_for_kb (profile-resolved)"
```

---

## Task 4: KB API stores profile ids + content settings

**Files:**
- Modify: `kb_platform/api/models.py` (KbCreate/KbUpdate/KbDetail)
- Modify: `kb_platform/api/routes_kbs.py`
- Modify: `kb_platform/db/repository.py` (`update_kb` accepts profile ids; KB create passes them)
- Test: extend `tests/test_api_documents.py` or new `tests/test_kb_profiles.py`

**Interfaces:**
- Produces: `POST/PATCH /kbs` accept `llm_profile_id` + `embedding_profile_id`; `GET /kbs/{id}` returns `llm_profile` / `embedding_profile` resolved.

- [ ] **Step 1: Write failing test**

Create `tests/test_kb_profiles.py`:
```python
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db"); Base.metadata.create_all(engine)
    repo = Repository(engine)
    c = TestClient(create_app(repo, data_root=str(tmp_path)))
    pid = c.post("/provider-profiles", json={"name": "DS", "kind": "llm", "provider": "deepseek",
                   "model": "deepseek-chat", "api_keys": ["sk"], "structured_output": False}).json()["id"]
    return c, pid


def test_create_kb_with_profile_and_detail(tmp_path, monkeypatch):
    c, pid = _client(tmp_path, monkeypatch)
    r = c.post("/kbs", json={"name": "k1", "method": "standard",
                             "llm_profile_id": pid, "settings_yaml": '{"chunking":{"size":500}}'})
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    d = c.get(f"/kbs/{kb_id}").json()
    assert d["llm_profile"]["model"] == "deepseek-chat"
    assert d["embedding_profile"] is None


def test_create_kb_rejects_unknown_profile(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    assert c.post("/kbs", json={"name": "x", "method": "standard",
                                "llm_profile_id": 999, "settings_yaml": "{}"}).status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_kb_profiles.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `kb_platform/api/models.py`, extend the KB models:
```python
class KbCreate(BaseModel):
    name: str
    method: str = "standard"
    settings_yaml: str | None = None
    llm_profile_id: int
    embedding_profile_id: int | None = None

class KbUpdate(BaseModel):
    name: str
    method: str = "standard"
    settings_yaml: str | None = None
    llm_profile_id: int
    embedding_profile_id: int | None = None

class ProfileRef(BaseModel):
    id: int; name: str; provider: str; model: str

class KbDetailOut(BaseModel):
    id: int; name: str; method: str
    settings: dict
    llm_profile: ProfileRef | None = None
    embedding_profile: ProfileRef | None = None
```
Remove the old `_redact`-based `settings` field shape on `KbDetailOut` (it is now a content-only dict; nothing sensitive remains, but keep `_redact` applied for defense-in-depth on prompt text).

In `kb_platform/api/routes_kbs.py`:
- `create_kb`: validate both profile ids exist (400 if not); pass them to the `KnowledgeBase(...)` constructor.
- `update_kb`: pass `llm_profile_id`/`embedding_profile_id` to `repo.update_kb`.
- `get_kb`: resolve `repo.get_profile(...)` → `ProfileRef` for each; return content `settings` (json-parsed, redacted).

```python
@router.post("/kbs", response_model=KbOut, status_code=201)
def create_kb(payload: KbCreate, request: Request):
    repo = request.app.state.repo
    _require_profile(repo, payload.llm_profile_id, "llm")
    if payload.embedding_profile_id is not None:
        _require_profile(repo, payload.embedding_profile_id, "embedding")
    settings = _parse_settings(payload.settings_yaml)
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(name=payload.name, method=payload.method, settings_json=settings,
                           data_root=request.app.state.data_root,
                           llm_profile_id=payload.llm_profile_id,
                           embedding_profile_id=payload.embedding_profile_id)
        s.add(kb); s.flush()
        return KbOut(id=kb.id, name=kb.name, method=kb.method)


def _require_profile(repo, pid, kind):
    p = repo.get_profile(pid)
    if p is None:
        raise HTTPException(400, f"unknown {kind} profile id {pid}")
    return p


@router.get("/kbs/{kb_id}", response_model=KbDetailOut)
def get_kb(kb_id: int, request: Request):
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        llm = _profileref(repo, kb.llm_profile_id)
        emb = _profileref(repo, kb.embedding_profile_id)
        return KbDetailOut(id=kb.id, name=kb.name, method=kb.method,
                           settings=_redact(kb.settings_json),
                           llm_profile=llm, embedding_profile=emb)


def _profileref(repo, pid):
    if pid is None:
        return None
    p = repo.get_profile(pid)
    return ProfileRef(id=p.id, name=p.name, provider=p.provider, model=p.model) if p else None
```
Update `repo.update_kb` signature to accept `llm_profile_id` / `embedding_profile_id` and set them.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_kb_profiles.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_kbs.py kb_platform/db/repository.py tests/test_kb_profiles.py
git commit -m "feat(api): KB create/update store profile refs; detail resolves profiles"
```

---

## Task 5: Alembic migration (schema + idempotent backfill)

**Files:**
- Create: `alembic/versions/0005_provider_profiles.py`
- Test: `tests/test_migration_provider_profiles.py`

**Interfaces:**
- Consumes: `encrypt_values` (for storing nothing — migrated profiles have empty keys), the models.
- Produces: a migration that creates the table + FKs and backfills every legacy KB.

- [ ] **Step 1: Write failing test**

Create `tests/test_migration_provider_profiles.py`:
```python
import json
import os
import subprocess
import sys

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.models_profile import ProviderProfile


def test_migration_backfills_legacy_kb(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    db = tmp_path / "kb.db"
    engine = create_engine(f"sqlite:///{db}"); Base.metadata.create_all(engine)
    # legacy KB with full settings (no profile ids yet)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="legacy", method="standard", data_root=str(tmp_path),
                            settings_json=json.dumps({
                                "llm": {"model_provider": "deepseek", "model": "deepseek-chat",
                                        "api_base": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
                                "embedding": {"model_provider": "ollama", "model": "nomic-embed-text",
                                              "api_base": "http://localhost:11434", "enabled": True},
                                "community_reports": {"structured_output": False, "max_length": 1500},
                                "chunking": {"size": 900},
                            })))
    # run migration (down_revision 0004 -> 0005)
    env = {**os.environ, "KB_DB_URL": f"sqlite:///{db}"}
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"],
                   cwd=os.getcwd(), check=True, env=env)
    # postconditions
    with session_scope(engine) as s:
        kb = s.get(KnowledgeBase, 1)
        profiles = list(s.query(ProviderProfile))
        assert kb.llm_profile_id is not None
        assert kb.embedding_profile_id is not None
        # llm/embedding blocks stripped from settings_json
        content = json.loads(kb.settings_json)
        assert "llm" not in content and "embedding" not in content
        assert content["community_reports"]["max_length"] == 1500  # content stays
        assert "structured_output" not in content["community_reports"]  # moved to profile
        # dedup: one llm profile, one embedding profile
        assert len([p for p in profiles if p.kind == "llm"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_migration_provider_profiles.py -q`
Expected: FAIL — migration 0005 doesn't exist.

- [ ] **Step 3: Implement the migration**

Create `alembic/versions/0005_provider_profiles.py`:
```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""provider profiles + KB profile refs; backfill legacy KB settings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "provider_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, unique=True),
        sa.Column("kind", sa.String),
        sa.Column("provider", sa.String),
        sa.Column("model", sa.String),
        sa.Column("api_base", sa.String, nullable=True),
        sa.Column("api_version", sa.String, nullable=True),
        sa.Column("api_keys_enc", sa.Text, nullable=False, server_default="[]"),
        sa.Column("structured_output", sa.Boolean, nullable=False, server_default=sa.text("1")),
    )
    op.add_column("knowledge_base", sa.Column("llm_profile_id", sa.Integer, nullable=True))
    op.add_column("knowledge_base", sa.Column("embedding_profile_id", sa.Integer, nullable=True))

    rows = bind.execute(sa.text("SELECT id, settings_json FROM knowledge_base WHERE llm_profile_id IS NULL")).fetchall()
    seen = {}  # dedup key -> profile id
    name_counts = {}
    for kb_id, settings_json in rows:
        s = json.loads(settings_json or "{}")
        llm = (s.get("llm") or {})
        emb = (s.get("embedding") or {})
        reports = (s.get("community_reports") or {})
        llm_pid = _profile(bind, seen, name_counts, kind="llm", provider=llm.get("model_provider", "openai"),
                           model=llm.get("model", "gpt-4o-mini"), api_base=llm.get("api_base"),
                           api_version=llm.get("api_version"),
                           structured_output=bool(reports.get("structured_output", True)))
        emb_pid = None
        if emb and emb.get("enabled", True):
            emb_pid = _profile(bind, seen, name_counts, kind="embedding", provider=emb.get("model_provider", "openai"),
                               model=emb.get("model", "text-embedding-3-small"), api_base=emb.get("api_base"),
                               api_version=emb.get("api_version"), structured_output=True)
        # strip llm/embedding + structured_output from settings
        for k in ("llm", "embedding"):
            s.pop(k, None)
        if "community_reports" in s:
            s["community_reports"].pop("structured_output", None)
        bind.execute(sa.text("UPDATE knowledge_base SET llm_profile_id=:p, embedding_profile_id=:e, settings_json=:sj WHERE id=:i"),
                     {"p": llm_pid, "e": emb_pid, "sj": json.dumps(s), "i": kb_id})


def _profile(bind, seen, name_counts, *, kind, provider, model, api_base, api_version, structured_output):
    key = (kind, provider, model, api_base, api_version, structured_output)
    if key in seen:
        return seen[key]
    base = f"{provider}-{model}"
    name = base
    n = name_counts.get(base, 0)
    if n:
        name = f"{base}-{n}"
    name_counts[base] = n + 1
    res = bind.execute(sa.text(
        "INSERT INTO provider_profile (name,kind,provider,model,api_base,api_version,api_keys_enc,structured_output) "
        "VALUES (:n,:k,:p,:m,:ab,:av,'[]',:so)"
    ), {"n": name, "k": kind, "p": provider, "m": model, "ab": api_base, "av": api_version, "so": 1 if structured_output else 0})
    pid = res.lastrowid
    seen[key] = pid
    return pid


def downgrade() -> None:
    op.drop_column("knowledge_base", "embedding_profile_id")
    op.drop_column("knowledge_base", "llm_profile_id")
    op.drop_table("provider_profile")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_migration_provider_profiles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0005_provider_profiles.py tests/test_migration_provider_profiles.py
git commit -m "feat(db): alembic 0005 — provider profiles + backfill legacy KBs"
```

---

## Task 6: Frontend — Provider 配置 page + client + types

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`
- Create: `web/src/pages/ProviderProfilesPage.tsx`, `web/src/pages/ProviderProfilesPage.test.tsx`
- Modify: `web/src/lib/nav.ts`, `web/src/App.tsx`

**Interfaces:**
- Produces: `ProviderProfile` type; `listProfiles/createProfile/updateProfile/deleteProfile` client fns; the page at `/provider-profiles`.

- [ ] **Step 1: Write failing test**

Create `web/src/pages/ProviderProfilesPage.test.tsx`:
```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import { ProviderProfilesPage } from "./ProviderProfilesPage";

const server = setupServer(
  http.get("/provider-profiles", () => HttpResponse.json([
    { id: 1, name: "DeepSeek", kind: "llm", provider: "deepseek", model: "deepseek-chat",
      api_base: null, api_version: null, structured_output: false, api_keys_count: 2 },
  ])),
  http.post("/provider-profiles", async ({ request }) => {
    const b = await request.json() as { name: string };
    return HttpResponse.json({ id: 2, name: b.name, kind: "llm", provider: "openai",
      model: "gpt-4o-mini", api_base: null, api_version: null, structured_output: true, api_keys_count: 1 });
  }),
  http.delete("/provider-profiles/1", () => new HttpResponse(null, { status: 409 })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("lists profiles and adds one with a key", async () => {
  render(<MemoryRouter><ProviderProfilesPage /></MemoryRouter>);
  expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText(/名称/), { target: { value: "OpenAI" } });
  fireEvent.change(screen.getByPlaceholderText(/provider/), { target: { value: "openai" } });
  fireEvent.change(screen.getAllByPlaceholderText(/sk-/)[0], { target: { value: "sk-xxx" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));
  await waitFor(() => expect(screen.getByText("OpenAI")).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/pages/ProviderProfilesPage.test.tsx`
Expected: FAIL — page doesn't exist.

- [ ] **Step 3: Implement types + client + page**

In `web/src/api/types.ts` add:
```ts
export interface ProviderProfile {
  id: number; name: string; kind: "llm" | "embedding";
  provider: string; model: string; api_base: string | null; api_version: string | null;
  structured_output: boolean; api_keys_count: number;
}
export interface ProfileCreate {
  name: string; kind: "llm" | "embedding"; provider: string; model: string;
  api_base?: string; api_version?: string; api_keys: string[]; structured_output: boolean;
}
```
Update `KbCreate`/`KbDetail`:
```ts
export interface KbCreate { name: string; method: string; settings_yaml?: string; llm_profile_id: number; embedding_profile_id?: number | null }
export interface KbDetail extends KbOut { settings: Record<string, unknown>; llm_profile: { id: number; name: string; provider: string; model: string } | null; embedding_profile: { id: number; name: string; provider: string; model: string } | null }
```

In `web/src/api/client.ts` add:
```ts
export const listProfiles = (kind?: string) => req<ProviderProfile[]>(`/provider-profiles${kind ? `?kind=${kind}` : ""}`);
export const createProfile = (b: ProfileCreate) => req<ProviderProfile>("/provider-profiles", { method: "POST", body: JSON.stringify(b) });
export const updateProfile = (id: number, b: Partial<ProfileCreate>) => req<ProviderProfile>(`/provider-profiles/${id}`, { method: "PATCH", body: JSON.stringify(b) });
export const deleteProfile = (id: number) => req<void>(`/provider-profiles/${id}`, { method: "DELETE" });
```
Also update `createKb`/`updateKb` to send `llm_profile_id`/`embedding_profile_id`.

Create `web/src/pages/ProviderProfilesPage.tsx` — a list + a create form with the **dynamic API Keys list** (default 1 input, `+ 新增` adds a row, `✕` removes; `api_keys` sent only when any non-empty). Use the existing `Card`/`Button`/`Field` primitives and `useState` for the dynamic rows. On delete conflict (409), surface the error message.

In `web/src/lib/nav.ts`, add under the 系统管理 group: `{ to: "/provider-profiles", label: "Provider 配置", icon: IconKey }`. In `web/src/App.tsx`, add `<Route path="/provider-profiles" element={<ProviderProfilesPage />} />` in the top-level routes block.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/pages/ProviderProfilesPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/pages/ProviderProfilesPage.tsx web/src/pages/ProviderProfilesPage.test.tsx web/src/lib/nav.ts web/src/App.tsx
git commit -m "feat(web): Provider 配置 page + dynamic key list"
```

---

## Task 7: KbForm rework — profile dropdowns

**Files:**
- Modify: `web/src/lib/kb-settings.ts` (drop llm/embedding from state)
- Modify: `web/src/components/KbForm.tsx`, `web/src/components/KbForm.test.tsx`

**Interfaces:**
- Consumes: `listProfiles` from Task 6.

- [ ] **Step 1: Write failing test**

In `web/src/components/KbForm.test.tsx`, replace the provider/model sections. Add MSW handlers returning two llm profiles and one embedding profile; assert the form has `<select>`s and that submitting sends `llm_profile_id`. Minimal new test:
```tsx
test("submit requires an LLM profile and sends its id", async () => {
  server.use(
    http.get("/provider-profiles", (req) => {
      const kind = new URL(req.request.url).searchParams.get("kind");
      if (kind === "embedding") return HttpResponse.json([{ id: 3, name: "Ollama", kind: "embedding", provider: "ollama", model: "nomic-embed-text", api_base: "http://localhost:11434", api_version: null, structured_output: true, api_keys_count: 1 }]);
      return HttpResponse.json([{ id: 1, name: "DS", kind: "llm", provider: "deepseek", model: "deepseek-chat", api_base: null, api_version: null, structured_output: false, api_keys_count: 1 }]);
    }),
  );
  const onCreated = renderForm();
  // submit without selecting -> blocked
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));
  expect(onCreated).not.toHaveBeenCalled();
  // select LLM profile then submit
  await userEvent.selectOptions(screen.getByLabelText(/LLM 配置/), "1");
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "my-kb");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(captured[captured.length - 1]?.body).toMatchObject({ llm_profile_id: 1 });
});
```
Remove/adjust the old tests that asserted provider/model/api_key_env fields (they no longer exist).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/KbForm.test.tsx`
Expected: FAIL (old fields removed; new select missing).

- [ ] **Step 3: Implement**

In `web/src/lib/kb-settings.ts`: remove `LlmFields`, `embedding`, and `llm` from `KbFormState`/`DEFAULTS`/`buildSettings`. `buildSettings` now returns only the content keys (chunking, extract_graph, summarize_descriptions, cluster_graph, community_reports {max_length}, prompts, query_prompts).

In `web/src/components/KbForm.tsx`: fetch `listProfiles("llm")` + `listProfiles("embedding")` on mount; replace the provider/model/api_base/api_key section with two `<select>`s bound to `llmProfileId`/`embeddingProfileId` state (embedding select has a "无" option → null). The submit body becomes `{ name, method, llm_profile_id, embedding_profile_id, settings_yaml: JSON.stringify(buildSettings(s)) }`. Disable submit when `llmProfileId` is null.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/KbForm.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/kb-settings.ts web/src/components/KbForm.tsx web/src/components/KbForm.test.tsx
git commit -m "feat(web): KbForm picks LLM/embedding profiles via dropdowns"
```

---

## Task 8: E2E fix-up + README + full green run

**Files:**
- Modify: `scripts/e2e_server.py`, `web/e2e/fixtures.ts`, `web/e2e/create-kb.spec.ts`
- Modify: `README.md`, `README.zh.md`

- [ ] **Step 1: Update E2E harness to seed a profile**

In `scripts/e2e_server.py`, after `Base.metadata.create_all(engine)`, before seeding the baseline KB, create an llm profile and point the baseline KB at it:
```python
    llm_profile = repo.create_profile(name="fake-llm", kind="llm", provider="openai",
                                       model="gpt-4o-mini", api_keys=["fake-key"], structured_output=True)
```
and pass `llm_profile_id=llm_profile.id` to the baseline `KnowledgeBase(...)`.

- [ ] **Step 2: Update E2E fixtures + create-KB spec**

In `web/e2e/fixtures.ts`, add:
```ts
export async function createProfileViaApi(page: import("@playwright/test").Page, body: { name: string; kind: "llm"; provider: string; model: string; api_keys: string[]; structured_output: boolean }): Promise<number> {
  const r = await page.request.post("/provider-profiles", { data: body });
  return (await r.json()).id as number;
}
```
Change `createKbViaApi(page, name)` to first create a profile, then POST `/kbs` with `llm_profile_id`. Update `web/e2e/create-kb.spec.ts` to use the profile-based flow (the form now picks a profile via the LLM 配置 select).

- [ ] **Step 3: Update README EN+ZH**

In both `README.md` and `README.zh.md`:
- Replace the "Configuration (model / api_base / key)" section's env-key guidance with the new model: define provider profiles on the Provider 配置 page (keys encrypted in DB), pick profiles at KB creation. Note the master-key file (`.kb_secret_key`) and that migrated KBs need keys re-entered.
- Update the API table: add the `/provider-profiles` rows; change the `POST /kbs` description to "create KB referencing llm/embedding profile ids".

- [ ] **Step 4: Run full backend + frontend + E2E green**

```bash
uv run pytest -q && uv run ruff check .
cd web && npm test && npm run build && npm run e2e
```
Expected: pytest all pass, ruff clean, vitest all pass, build succeeds, all 10 E2E specs pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/e2e_server.py web/e2e/fixtures.ts web/e2e/create-kb.spec.ts README.md README.zh.md
git commit -m "feat(e2e,docs): profile-based E2E + README for provider profiles"
```

---

## Self-Review Notes

- Spec coverage: profiles model+CRUD (Task 2); `assemble_kb_settings` seam (Task 3); KB profile refs + API (Task 4); migration (Task 5); Provider 配置 page + dynamic keys (Task 6); KbForm dropdowns (Task 7); E2E + README (Task 8); crypto (Task 1). All spec sections map to a task.
- The graphrag side is untouched except `build_adapter_from_settings`'s key-resolution block (now reads `llm.api_keys` literals) — verified the function already supported `extra_api_keys` via `LoadBalancingCompletion`.
- Type consistency: `ProviderProfile`/`ProfileCreate`/`ProfileOut`/`ProfileRef` names are used consistently across tasks; `assemble_kb_settings(kb, repo)` signature matches between Task 3 (defined) and Task 8 (worker uses `build_adapter_for_kb`).
- E2E downstream (spec flagged) is Task 8 Step 1–2.
- Migration is idempotent (`WHERE llm_profile_id IS NULL`) and dedups profiles by `(kind, provider, model, api_base, api_version, structured_output)`; migrated profiles get empty `api_keys_enc` → re-keying noted in README.
```
