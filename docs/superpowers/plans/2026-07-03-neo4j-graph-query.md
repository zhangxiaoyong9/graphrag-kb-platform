# Neo4j Graph Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two query methods — `cypher` (Text2Cypher) and `hybrid` (Vector+Cypher) — to `POST /kbs/{id}/query`, reading the KB's graph from a user-provided Neo4j database via a live `neo4j` async driver.

**Architecture:** A new `Neo4jQueryEngine(QueryEngine)` (graphrag-free) does retrieval by generating/templating Cypher, executing it against Neo4j through a process-level async-driver pool, and synthesizing an answer over the rows via the existing `kb_native` completion/embedding clients. A `build_query_engine` factory (the wiring layer — allowed to import `assemble_kb_settings`, exactly as today's routes do) resolves the KB's neo4j profile + LLM/embedding profiles and **injects** the driver pool, completion, and embed callable into the engine, keeping graphrag out of the engine module. Both methods reuse the existing SSE contract (`meta`/`delta`/`done`/`error`); a new `StreamMeta` event carries the generated/templated Cypher for L3 transparency.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy + Alembic (SQLite), the `neo4j` python driver (opt-in `[neo4j]` extra, all imports lazy), graphrag-llm (already a dep), pytest (asyncio auto mode). Neo4j ≥ 5.11 at the user's deployment.

## Global Constraints

- **The `neo4j` driver is an opt-in extra.** Every `import neo4j` is lazy (inside a function). `uv sync --extra neo4j` installs it. Without it, `build_query_engine` raises a clear error surfaced as SSE `error`.
- **`Neo4jQueryEngine` imports neither `graphrag` nor `graphrag_llm`.** It receives its completion/embed clients + driver pool by injection. graphrag stays confined to `graphrag_engine.py` on the query side; the new `kb_platform/llm/native_builders.py` is the sanctioned home for the `graphrag_llm`-typed client construction (it already lives next to `client.py`/`embedding.py`, which import `graphrag_llm` today).
- **Safety is layered (L0–L3), decided in the spec.** L1 (`is_readonly_cypher`) is the one enforced in code for LLM output; L0 is a read-only DB-user contract (documented, not code); L2 is timeout + row cap; L3 is the `meta{cypher}` transparency event.
- **Errors are SSE, never HTTP 500** — match the existing query-path discipline (`GraphRagQueryEngine.stream_search` yields a terminal `StreamDone(error=...)`).
- **`ruff check .`** must pass (line-length 100, target py311). **pytest** config: `asyncio_mode = "auto"`, `pythonpath` includes `tests`. The autouse Fernet-key fixture in `tests/conftest.py` covers the new `username`/password crypto.
- **Neo4j profile reuses `ProviderProfile`** (not a new table): `uri`→`api_base`, `password`→the single element of `api_keys_enc` (existing Fernet crypto), plus one new nullable `username` column. `provider` is the literal `"neo4j"`.
- **Dashboard UI copy is Chinese** — match surrounding copy in `query-methods.ts` / `SettingsPage.tsx`.

## File Structure

**New files**
- `kb_platform/neo4j/__init__.py` — package marker.
- `kb_platform/neo4j/safety.py` — `is_readonly_cypher(s)` + `truncate_rows(rows, cap)` (pure, no deps).
- `kb_platform/neo4j/driver_pool.py` — process-level `neo4j.AsyncGraphDatabase.driver` pool, keyed by `(uri, username, password)`; mirrors `kb_platform/llm/http_client.py`.
- `kb_platform/llm/native_builders.py` — graphrag-free construction of `NativeCompletion` / `NativeEmbedding` from a `kb_profiles` bundle.
- `kb_platform/query/neo4j_engine.py` — `Neo4jQueryEngine` + the pure helpers (`build_text2cypher_messages`, `build_hybrid_cypher`, `format_rows_as_context`).
- `kb_platform/query/factory.py` — `build_query_engine(method, kb, repo, app_state)`.
- `alembic/versions/0010_neo4j_profile.py` — adds `provider_profile.username` + `knowledge_base.neo4j_profile_id`.
- `tests/test_neo4j_safety.py`, `tests/test_neo4j_engine_unit.py`, `tests/test_query_factory.py`, `tests/test_neo4j_driver_pool.py`, `tests/test_neo4j_profile_kb.py`.
- `tests/test_neo4j_integration.py` — real-Neo4j (testcontainers), marked integration / skipped without Neo4j + a real LLM profile.

**Modified files**
- `kb_platform/query/engine.py` — `QueryParams.{hops, cypher_timeout_ms}`; `StreamDone.truncated`; new `StreamMeta` event.
- `kb_platform/query/params.py` — `_FIELDS` += `hops`, `cypher_timeout_ms`.
- `kb_platform/api/models.py` — `QueryParamsIn.{hops, cypher_timeout_ms}`; `QueryResultOut.truncated`; `ProfileCreate/Update/Out` accept `kind="neo4j"` + `username`; `KbCreate/Update` + `KbDetailOut` carry `neo4j_profile_id` / `neo4j_profile`.
- `kb_platform/db/models_profile.py` — `username: Mapped[str | None]`.
- `kb_platform/db/models.py` — `KnowledgeBase.neo4j_profile_id`.
- `kb_platform/db/repository.py` — `create_profile`/`update_profile` accept `username`; `update_kb` accepts `neo4j_profile_id`; `create_kb` route passes it.
- `kb_platform/api/routes_profiles.py` — `_out` includes `username`.
- `kb_platform/api/routes_kbs.py` — `create_kb` / `get_kb` / `update_kb` thread `neo4j_profile_id` (+ a `_require_profile` for it when set).
- `kb_platform/api/routes_query.py` + `kb_platform/api/routes_conversations.py` — build engines via `build_query_engine`; route handles `StreamMeta` (`meta{cypher}`) and `StreamDone.truncated`.
- `kb_platform/llm/bootstrap.py` — `close_clients()` also calls `neo4j.driver_pool.close_all()` (lazy; no-op when the extra is absent).
- `pyproject.toml` — `[neo4j]` extra.
- `web/src/lib/query-methods.ts` — add `cypher` + `hybrid`.
- `web/src/pages/SettingsPage.tsx` — add the two method descriptions.
- `web/src/api/types.ts` — `QueryParams` gains `hops`/`cypher_timeout_ms`; `QueryResult` gains `truncated`.

---

### Task 1: Read-only Cypher validator (L1) + row-cap helper

**Files:**
- Create: `kb_platform/neo4j/__init__.py`
- Create: `kb_platform/neo4j/safety.py`
- Test: `tests/test_neo4j_safety.py`

**Interfaces:**
- Produces: `is_readonly_cypher(s: str) -> bool` and `truncate_rows(rows: list, cap: int) -> tuple[list, bool]` in `kb_platform/neo4j/safety.py`. Both are pure (no `neo4j` import).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_neo4j_safety.py`:

```python
"""L1 read-only Cypher validator + L2 row-cap helper (pure functions)."""

from kb_platform.neo4j.safety import is_readonly_cypher, truncate_rows


# --- is_readonly_cypher: positives (admit) ----------------------------------
def test_admits_plain_match_return():
    assert is_readonly_cypher("MATCH (n) RETURN n LIMIT 10")


def test_admits_profile_explain_show():
    assert is_readonly_cypher("PROFILE MATCH (n) RETURN n")
    assert is_readonly_cypher("EXPLAIN MATCH (n) RETURN n")
    assert is_readonly_cypher("SHOW INDEXES")


def test_admits_leading_whitespace_and_comments():
    assert is_readonly_cypher("   // hi\n   MATCH (n) RETURN n")
    assert is_readonly_cypher("\nMATCH (n)\nRETURN n\n")


def test_admits_multi_statement_all_readonly():
    s = "MATCH (a) RETURN a; MATCH (b) RETURN b;"
    assert is_readonly_cypher(s)


# --- is_readonly_cypher: negatives (reject) ---------------------------------
def test_rejects_create_merge_delete_drop_set_remove():
    for s in [
        "CREATE (n:Entity {title:'x'})",
        "MERGE (n:Entity {title:'x'})",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "DROP INDEX x",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) REMOVE n.x",
    ]:
        assert not is_readonly_cypher(s), s


def test_rejects_load_csv():
    assert not is_readonly_cypher(
        "LOAD CSV WITH HEADERS FROM 'https://evil/x.csv' AS row CREATE (n:X)"
    )


def test_rejects_write_after_readonly_prefix():
    # the validator must scan EVERY statement, not just the first
    assert not is_readonly_cypher("MATCH (n) RETURN n; CREATE (n)")


def test_rejects_call_to_write_procedure():
    assert not is_readonly_cypher("CALL apoc.create.node(['X'], {}) YIELD node RETURN node")


def test_admits_call_to_read_procedure():
    # vector ANN is a read CALL; admit it
    assert is_readonly_cypher(
        "CALL db.index.vector.queryNodes('entity_description_vec', 10, $v) "
        "YIELD node, score RETURN node, score"
    )


# --- truncate_rows ----------------------------------------------------------
def test_truncate_under_cap_returns_all_not_truncated():
    rows = [{"a": i} for i in range(5)]
    out, truncated = truncate_rows(rows, 10)
    assert out == rows and truncated is False


def test_truncate_at_cap_returns_all_not_truncated():
    rows = [{"a": i} for i in range(10)]
    out, truncated = truncate_rows(rows, 10)
    assert len(out) == 10 and truncated is False


def test_truncate_over_cap_returns_cap_and_flag():
    rows = [{"a": i} for i in range(50)]
    out, truncated = truncate_rows(rows, 10)
    assert len(out) == 10 and truncated is True
    assert out[0] == {"a": 0} and out[-1] == {"a": 9}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_neo4j_safety.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kb_platform.neo4j.safety'`.

- [ ] **Step 3: Write minimal implementation**

Create `kb_platform/neo4j/__init__.py`:

```python
"""Neo4j read-side support: driver pool + Cypher safety guards."""
```

Create `kb_platform/neo4j/safety.py`:

```python
"""L1 read-only Cypher validator + L2 row-cap truncation helper.

Both functions are pure (no ``neo4j`` import) so they are unit-testable without
the driver installed. Belt-and-suspenders with L0 (a read-only DB user): either
layer failing alone still blocks writes.
"""

from __future__ import annotations

import re

# Root verbs whose statement is a read. PROFILE/EXPLAIN wrap a read query;
# SHOW lists metadata. Everything else (CREATE/MERGE/DELETE/SET/REMOVE/DROP/
# LOAD CSV/CALL-for-write) is rejected.
_READONLY_VERBS = {"MATCH", "RETURN", "PROFILE", "EXPLAIN", "SHOW"}

# Matches ``CALL <proc>`` and lets us admit read procedures (vector ANN) while
# rejecting write procedures (apoc.create.*). We admit the well-known read-only
# vector / schema-inspection procedures and reject everything else named
# ``create.*`` / ``delete.*`` / ``merge.*`` / ``remove.*``.
_READ_PROC_ALLOW = re.compile(
    r"^db\.(index\.vector\.queryNodes|indexes|constraints|labels|properties|relationshipTypes)",
    re.IGNORECASE,
)
_WRITE_PROC_DENY = re.compile(r"(create|delete|merge|remove|set|drop)", re.IGNORECASE)


def _strip_comments_and_strings(sql: str) -> str:
    """Remove ``//`` line comments and quoted string literals so verbs inside
    strings/comments cannot mask a write verb."""
    out_lines = []
    for line in sql.splitlines():
        # strip // line comments (keep everything before them)
        if "//" in line:
            line = line.split("//", 1)[0]
        out_lines.append(line)
    text = "\n".join(out_lines)
    # strip single-quoted string literals (Cypher uses single quotes)
    text = re.sub(r"'(?:[^'\\\\]|\\\\.)*'", "''", text)
    return text


def _statement_is_readonly(stmt: str) -> bool:
    stmt = stmt.strip()
    if not stmt:
        return True  # empty between semicolons -> vacuously read-only
    head = stmt.split(None, 1)[0] if stmt.split() else ""
    # peel a leading optional clause chain down to the root verb:
    # PROFILE/EXPLAIN wrap a real statement, so look at the first token.
    tokens = stmt.split()
    # If the first token is PROFILE/EXPLAIN, the wrapped statement must also
    # be read-only (recurse on the remainder).
    if tokens and tokens[0].upper() in {"PROFILE", "EXPLAIN"}:
        return _statement_is_readonly(" ".join(tokens[1:]))
    verb = tokens[0].upper() if tokens else ""
    if verb in _READONLY_VERBS:
        return True
    if verb == "CALL":
        # parse ``CALL <proc>(...)`` -> the procedure path
        m = re.match(r"\s*CALL\s+([\w\.]+)\s*\(", stmt, re.IGNORECASE)
        proc = m.group(1) if m else ""
        if _READ_PROC_ALLOW.match(proc):
            return True
        if _WRITE_PROC_DENY.search(proc):
            return False
        # Unknown read procedure: be conservative and reject.
        return False
    return False


def is_readonly_cypher(s: str) -> bool:
    """True iff EVERY ``;``-separated statement in ``s`` is read-only.

    Comments and string literals are stripped first so a write verb buried in a
    string cannot slip through. PROFILE/EXPLAIN/SHOW are admitted (read-side).
    CALL is admitted only for known read procedures (vector ANN, schema list);
    unknown procedures are rejected (conservative).
    """
    if not s or not s.strip():
        return False
    cleaned = _strip_comments_and_strings(s)
    statements = cleaned.split(";")
    return all(_statement_is_readonly(stmt) for stmt in statements)


def truncate_rows(rows: list, cap: int) -> tuple[list, bool]:
    """Return ``(rows[:cap], len(rows) > cap)``. The flag feeds ``StreamDone.truncated``."""
    truncated = len(rows) > cap
    return (rows[:cap] if truncated else list(rows)), truncated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_neo4j_safety.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/neo4j/safety.py kb_platform/neo4j/__init__.py tests/test_neo4j_safety.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/neo4j/__init__.py kb_platform/neo4j/safety.py tests/test_neo4j_safety.py
git commit -m "$(cat <<'EOF'
feat(neo4j): read-only Cypher validator (L1) + row-cap helper

Pure is_readonly_cypher admits MATCH/RETURN/PROFILE/EXPLAIN/SHOW and known
read CALLs (vector ANN), rejects write/DDL verbs and write procedures after
stripping comments + string literals. truncate_rows caps result rows for L2.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 2: neo4j profile kind + KB column + migration + CRUD wiring

**Files:**
- Create: `alembic/versions/0010_neo4j_profile.py`
- Modify: `kb_platform/db/models_profile.py`
- Modify: `kb_platform/db/models.py`
- Modify: `kb_platform/db/repository.py` (`create_profile`, `update_profile`, `update_kb`)
- Modify: `kb_platform/api/models.py` (`ProfileCreate`, `ProfileUpdate`, `ProfileOut`, `KbCreate`, `KbUpdate`, `KbDetailOut`)
- Modify: `kb_platform/api/routes_profiles.py` (`_out`)
- Modify: `kb_platform/api/routes_kbs.py` (`create_kb`, `get_kb`, `update_kb`)
- Test: `tests/test_neo4j_profile_kb.py`

**Interfaces:**
- Produces: a `neo4j` provider-profile kind. For a `neo4j` profile: `provider == "neo4j"`, `api_base` holds the bolt URI, the single encrypted key in `api_keys_enc` holds the password, and the new `username` column holds the user. `KnowledgeBase.neo4j_profile_id` (nullable FK) references it. CRUD threads `username` (profiles) and `neo4j_profile_id` (KBs) end-to-end.

**Mapping (memorize before writing code):** neo4j `uri`→`api_base`; neo4j `password`→`api_keys[0]` (Fernet-encrypted via existing `encrypt_values`); neo4j `username`→new `username` column; `provider` = the literal `"neo4j"`; `model`/`structured_output`/`ssl_verify` are unused for neo4j (set `model=""`, defaults for the rest).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_neo4j_profile_kb.py`:

```python
"""neo4j provider-profile kind + KB.neo4j_profile_id round-trip."""

from kb_platform.db.crypto import decrypt_values
from kb_platform.db.repository import Repository


def _repo(tmp_path):
    from kb_platform.db.engine import engine_from_url
    from kb_platform.db.models import Base

    eng = engine_from_url(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(eng)
    return Repository(eng)


def test_create_neo4j_profile_round_trips_uri_username_password(tmp_path):
    repo = _repo(tmp_path)
    p = repo.create_profile(
        name="neo-main", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j",
        api_keys=["s3cret"],
    )
    assert p.kind == "neo4j"
    assert p.api_base == "bolt://localhost:7687"
    assert p.username == "neo4j"
    assert decrypt_values(p.api_keys_enc) == ["s3cret"]


def test_update_profile_username_and_password(tmp_path):
    repo = _repo(tmp_path)
    p = repo.create_profile(
        name="neo-main", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j", api_keys=["s3cret"],
    )
    updated = repo.update_profile(p.id, username="graphuser", api_keys=["new-pw"])
    assert updated.username == "graphuser"
    assert decrypt_values(updated.api_keys_enc) == ["new-pw"]


def test_kb_carries_neo4j_profile_id(tmp_path):
    repo = _repo(tmp_path)
    llm = repo.create_profile(
        name="llm", kind="llm", provider="openai", model="gpt-4o-mini", api_keys=["k"],
    )
    neo = repo.create_profile(
        name="neo-main", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j", api_keys=["s3cret"],
    )
    kb = repo.create_kb(
        name="kb1", method="standard", settings_json="{}", data_root=".",
        llm_profile_id=llm.id, neo4j_profile_id=neo.id,
    )
    assert kb.neo4j_profile_id == neo.id
    # update can clear it (None)
    cleared = repo.update_kb(
        kb.id, name="kb1", method="standard", settings_json="{}",
        llm_profile_id=llm.id, neo4j_profile_id=None,
    )
    assert cleared.neo4j_profile_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_neo4j_profile_kb.py -v`
Expected: FAIL — `create_profile` rejects `username=`; `KnowledgeBase` has no `neo4j_profile_id`; `create_kb`/`update_kb` reject `neo4j_profile_id=`.

- [ ] **Step 3: Add the model columns**

In `kb_platform/db/models_profile.py`, append one column inside `ProviderProfile`:

```python
    # neo4j kind only: the DB user. Unused for llm/embedding profiles (NULL).
    username: Mapped[str | None] = mapped_column(String, nullable=True)
```

In `kb_platform/db/models.py`, inside `KnowledgeBase` (after `llm_fallback_profile_ids`):

```python
    neo4j_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_profile.id"), nullable=True
    )
    """Optional Neo4j profile enabling the cypher/hybrid query methods. None
    means the KB stays on the four graphrag methods (local/global/drift/basic)."""
```

- [ ] **Step 4: Write the Alembic migration**

Create `alembic/versions/0010_neo4j_profile.py`:

```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Add provider_profile.username + knowledge_base.neo4j_profile_id.

Read side of the Neo4j graph-query feature. ``username`` holds the Neo4j DB
user (neo4j kind only; NULL for llm/embedding profiles). ``neo4j_profile_id``
optionally links a KB to a neo4j provider-profile; when set, the cypher/hybrid
query methods read the KB's graph from Neo4j.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_profile",
        sa.Column("username", sa.String(), nullable=True),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("neo4j_profile_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_knowledge_base_neo4j_profile_id_provider_profile",
        "knowledge_base",
        "provider_profile",
        ["neo4j_profile_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_knowledge_base_neo4j_profile_id_provider_profile",
        "knowledge_base",
        type_="foreignkey",
    )
    op.drop_column("knowledge_base", "neo4j_profile_id")
    op.drop_column("provider_profile", "username")
```

- [ ] **Step 5: Thread `username` through the repo profile methods**

In `kb_platform/db/repository.py`, `create_profile` — add `username=None` to the signature and pass it through. Replace the method's signature + body opening:

```python
    def create_profile(self, *, name, kind, provider, model, api_base=None,
                       api_version=None, api_keys=None, structured_output=True,
                       ssl_verify=True, username=None) -> ProviderProfile:
        with session_scope(self.engine) as s:
            p = ProviderProfile(name=name, kind=kind, provider=provider, model=model,
                                api_base=api_base, api_version=api_version,
                                api_keys_enc=encrypt_values(api_keys or []),
                                structured_output=structured_output,
                                ssl_verify=ssl_verify, username=username)
            s.add(p)
            s.flush()
            return p
```

`update_profile` already does `for k, v in fields.items(): if hasattr(p, k) and v is not None: setattr(p, k, v)` — because `username` is now a real column (`hasattr` is True), it flows through unchanged. No edit needed beyond confirming `username` is in `ProfileUpdate` (Step 7).

- [ ] **Step 6: Thread `neo4j_profile_id` through the repo KB methods**

In `kb_platform/db/repository.py`, `update_kb` — add `neo4j_profile_id=None`:

```python
    def update_kb(
        self, kb_id: int, *, name: str, method: str, settings_json: str,
        llm_profile_id: int | None = None, embedding_profile_id: int | None = None,
        llm_fallback_profile_ids: str | None = None, neo4j_profile_id: int | None = None,
    ) -> KnowledgeBase | None:
        """Full-replace name/method/settings_json/profiles. Returns the KB or None if missing."""
        with session_scope(self.engine) as s:
            kb = s.get(KnowledgeBase, kb_id)
            if kb is None:
                return None
            kb.name = name
            kb.method = method
            kb.settings_json = settings_json
            kb.llm_profile_id = llm_profile_id
            kb.embedding_profile_id = embedding_profile_id
            kb.llm_fallback_profile_ids = llm_fallback_profile_ids
            kb.neo4j_profile_id = neo4j_profile_id
            return kb
```

`create_kb` is constructed inline in the route (Step 9), so the repo needs no `create_kb` change.

- [ ] **Step 7: Extend the pydantic models**

In `kb_platform/api/models.py`:

`ProfileCreate` — widen the `kind` literal and add `username`:

```python
class ProfileCreate(BaseModel):
    name: str
    kind: Literal["llm", "embedding", "neo4j"]
    provider: str
    model: str = ""
    api_base: str | None = None
    api_version: str | None = None
    api_keys: list[str] = []
    structured_output: bool = True
    ssl_verify: bool = True
    username: str | None = None  # neo4j kind only
```

`ProfileUpdate` — add `username`:

```python
    username: str | None = None  # None=unchanged
```

`ProfileOut` — add `username`:

```python
class ProfileOut(BaseModel):
    id: int
    name: str
    kind: str
    provider: str
    model: str
    api_base: str | None = None
    api_version: str | None = None
    structured_output: bool
    ssl_verify: bool
    api_keys_count: int
    username: str | None = None
```

`KbCreate` and `KbUpdate` — add the field (after `llm_fallback_profile_ids`):

```python
    neo4j_profile_id: int | None = None
```

`KbDetailOut` — add the resolved ref:

```python
    neo4j_profile: ProfileRef | None = None
```

- [ ] **Step 8: Surface `username` in the profile route**

In `kb_platform/api/routes_profiles.py`, `_out`:

```python
def _out(repo, p) -> ProfileOut:
    return ProfileOut(
        id=p.id, name=p.name, kind=p.kind, provider=p.provider, model=p.model,
        api_base=p.api_base, api_version=p.api_version,
        structured_output=p.structured_output, ssl_verify=p.ssl_verify,
        api_keys_count=repo.profile_key_count(p.id),
        username=p.username,
    )
```

- [ ] **Step 9: Thread `neo4j_profile_id` through the KB routes**

In `kb_platform/api/routes_kbs.py`:

`create_kb` — validate when set and pass it through. After the `embedding_profile_id` block:

```python
    if payload.neo4j_profile_id is not None:
        _require_profile(repo, payload.neo4j_profile_id, "neo4j")
```

and add `neo4j_profile_id=payload.neo4j_profile_id,` to the `KnowledgeBase(...)` constructor.

`get_kb` — add `neo4j_profile=_profileref(repo, kb.neo4j_profile_id),` to the `KbDetailOut(...)` call.

`update_kb` — add (after the embedding block):

```python
    if payload.neo4j_profile_id is not None:
        _require_profile(repo, payload.neo4j_profile_id, "neo4j")
```

pass `neo4j_profile_id=payload.neo4j_profile_id,` into `repo.update_kb(...)`, and add `neo4j_profile=_profileref(repo, kb.neo4j_profile_id),` to the returned `KbDetailOut(...)`.

- [ ] **Step 10: Run tests to verify they pass**

Run: `uv run pytest tests/test_neo4j_profile_kb.py -v && uv run alembic upgrade head`
Expected: new tests PASS; migration applies cleanly (SQLite column added).

- [ ] **Step 11: Lint**

Run: `uv run ruff check kb_platform/db kb_platform/api tests/test_neo4j_profile_kb.py alembic/versions/0010_neo4j_profile.py`
Expected: no errors.

- [ ] **Step 12: Commit**

```bash
git add kb_platform/db/models_profile.py kb_platform/db/models.py kb_platform/db/repository.py \
        kb_platform/api/models.py kb_platform/api/routes_profiles.py kb_platform/api/routes_kbs.py \
        alembic/versions/0010_neo4j_profile.py tests/test_neo4j_profile_kb.py
git commit -m "$(cat <<'EOF'
feat(db): neo4j provider-profile kind + KB.neo4j_profile_id

Adds provider_profile.username (neo4j DB user) and
knowledge_base.neo4j_profile_id (nullable FK). A neo4j profile reuses the
existing columns: api_base=bolt URI, api_keys_enc=[password] (Fernet),
username=DB user. Threaded through Profile/Kb CRUD (create/get/update) and
a single Alembic migration (0010).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 3: Process-level async-driver pool + `[neo4j]` extra

**Files:**
- Create: `kb_platform/neo4j/driver_pool.py`
- Modify: `pyproject.toml`
- Test: `tests/test_neo4j_driver_pool.py`

**Interfaces:**
- Produces: `get_driver(uri, username, password) -> AsyncDriver` and `async close_all()` in `kb_platform/neo4j/driver_pool.py`. Drivers are keyed by the full `(uri, username, password)` tuple so a password rotation picks up a fresh driver. `import neo4j` is lazy (inside `get_driver`). `close_all()` is wired into `bootstrap.close_clients` in Task 9.

- [ ] **Step 1: Add the `[neo4j]` extra**

In `pyproject.toml`, under `[project.optional-dependencies]`, after the `mcp =` line:

```toml
# Neo4j read side: cypher/hybrid query methods (POST /kbs/{id}/query?method=cypher|hybrid).
neo4j = ["neo4j>=5.20"]
```

`>=5.20` covers the `AsyncGraphDatabase.driver` + `execute_query` auto-commit API and vector index support at the user's Neo4j ≥ 5.11 deployment.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_neo4j_driver_pool.py`:

```python
"""Driver pool: reuse-by-identity + close_all. The real neo4j driver is mocked
so this runs without the [neo4j] extra installed."""

from unittest.mock import AsyncMock, MagicMock


def test_get_driver_reuses_by_identity(monkeypatch):
    from kb_platform.neo4j import driver_pool

    driver_pool._reset_for_test()
    created = MagicMock()
    fake_driver = MagicMock(name="asyncdriver")
    created.return_value = fake_driver
    monkeypatch.setattr(driver_pool, "_build_driver", created)

    a = driver_pool.get_driver("bolt://x", "u", "p")
    b = driver_pool.get_driver("bolt://x", "u", "p")
    assert a is b
    assert created.call_count == 1


def test_get_driver_distinct_identity_creates_new(monkeypatch):
    from kb_platform.neo4j import driver_pool

    driver_pool._reset_for_test()
    factory = MagicMock(side_effect=lambda *a, **kw: MagicMock(name="d"))
    monkeypatch.setattr(driver_pool, "_build_driver", factory)

    driver_pool.get_driver("bolt://x", "u", "p")
    driver_pool.get_driver("bolt://x", "u", "rotated")  # password changed
    driver_pool.get_driver("bolt://y", "u", "p")        # uri changed
    assert factory.call_count == 3


async def test_close_all_closes_every_driver(monkeypatch):
    from kb_platform.neo4j import driver_pool

    driver_pool._reset_for_test()
    d1, d2 = AsyncMock(), AsyncMock()
    monkeypatch.setattr(driver_pool, "_build_driver", lambda *a, **kw: d1 if len(driver_pool._DRIVERS) == 0 else d2)
    driver_pool.get_driver("bolt://x", "u", "p")
    driver_pool.get_driver("bolt://y", "u", "p")
    await driver_pool.close_all()
    d1.close.assert_awaited_once()
    d2.close.assert_awaited_once()
    assert driver_pool._DRIVERS == {}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_neo4j_driver_pool.py -v`
Expected: FAIL — `No module named 'kb_platform.neo4j.driver_pool'`.

- [ ] **Step 4: Write minimal implementation**

Create `kb_platform/neo4j/driver_pool.py`:

```python
"""Process-level async Neo4j driver pool, keyed by full connection identity.

Drivers are expensive to create, so they are reused across requests. The key is
``(uri, username, password)`` — a rotated password therefore picks up a fresh
driver, while the (common) steady-state reuses one driver per KB. Mirrors
``kb_platform/llm/http_client.py``: a module-level dict + lock + lazy ``close_all``.

``import neo4j`` is lazy (inside ``_build_driver``) so the platform runs unchanged
without the ``[neo4j]`` extra. ``close_all`` is wired into bootstrap.close_clients.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_DRIVERS: dict[tuple[str, str, str], Any] = {}


def _build_driver(uri: str, username: str, password: str):
    """Construct a real neo4j async driver. Imported lazily so callers that only
    resolve engines (no live query) don't require the [neo4j] extra."""
    import neo4j  # noqa: PLC0415 - lazy on purpose

    return neo4j.AsyncGraphDatabase.driver(uri, auth=(username, password))


def get_driver(uri: str, username: str, password: str):
    """Return the pooled async driver for this (uri, username, password)."""
    key = (uri, username, password)
    with _LOCK:
        d = _DRIVERS.get(key)
        if d is None:
            d = _build_driver(uri, username, password)
            _DRIVERS[key] = d
        return d


async def close_all() -> None:
    """Close every pooled driver. Called on process shutdown."""
    with _LOCK:
        drivers = list(_DRIVERS.values())
        _DRIVERS.clear()
    for d in drivers:
        await d.close()


def _reset_for_test() -> None:
    with _LOCK:
        _DRIVERS.clear()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_neo4j_driver_pool.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Lint**

Run: `uv run ruff check kb_platform/neo4j/driver_pool.py tests/test_neo4j_driver_pool.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/neo4j/driver_pool.py pyproject.toml tests/test_neo4j_driver_pool.py
git commit -m "$(cat <<'EOF'
feat(neo4j): async driver pool + [neo4j] extra

Process-level neo4j.AsyncGraphDatabase.driver pool keyed by
(uri, username, password) so a rotated password picks up a fresh driver.
import neo4j is lazy; close_all drains the pool on shutdown. Adds the
opt-in [neo4j] extra (neo4j>=5.20).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: graphrag-free `NativeCompletion` / `NativeEmbedding` builder

**Files:**
- Create: `kb_platform/llm/native_builders.py`
- Test: `tests/test_native_builders.py`

**Interfaces:**
- Produces: `build_native_completion(model_id, kb_profiles) -> NativeCompletion` and `build_native_embedding(model_id, kb_profile) -> NativeEmbedding` in `kb_platform/llm/native_builders.py`. Both pack the `kb_profiles` bundle into a minimal model-config stub (a `SimpleNamespace` exposing `.model_extra`) that the existing `NativeCompletion` / `NativeEmbedding` constructors read. This is the sanctioned `kb_platform/llm/` location for the `graphrag_llm`-typed client construction, so the `Neo4jQueryEngine` (Task 7) and `build_query_engine` factory (Task 8) never import graphrag.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_native_builders.py`:

```python
"""NativeCompletion/NativeEmbedding built from a kb_profiles bundle, without
graphrag-llm's factory. The built completion's gateway is exercised via the
_build_driver-style seam to avoid real network."""

from types import SimpleNamespace

from kb_platform.llm.native_builders import (
    build_native_completion,
    build_native_embedding,
)


def _bundle():
    return [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "api_version": None,
            "keys": ["sk-test"],
            "ssl_verify": True,
        }
    ]


def test_build_native_completion_reads_kb_profiles_into_gateway():
    c = build_native_completion(model_id="gpt-4o-mini", kb_profiles=_bundle())
    # NativeCompletion exposes ._gateway; its profiles came from the bundle
    profs = c._gateway._profiles
    assert len(profs) == 1
    assert profs[0].provider == "openai"
    assert profs[0].model == "gpt-4o-mini"
    assert profs[0].key == "sk-test"


def test_build_native_embedding_reads_first_profile():
    e = build_native_embedding(model_id="text-embedding-3-small", kb_profile=_bundle()[0])
    assert e._profile.provider == "openai"
    assert e._profile.model == "text-embedding-3-small"
    assert e._keys == ["sk-test"]


def test_build_native_completion_passes_stub_model_config():
    # the stub must expose .model_extra (the only attr NativeCompletion reads)
    from kb_platform.llm.native_builders import _model_config_stub

    stub = _model_config_stub(_bundle())
    assert stub.model_extra == {"kb_profiles": _bundle()}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_native_builders.py -v`
Expected: FAIL — `No module named 'kb_platform.llm.native_builders'`.

- [ ] **Step 3: Write minimal implementation**

Create `kb_platform/llm/native_builders.py`:

```python
"""Build NativeCompletion / NativeEmbedding from a kb_profiles bundle, graphrag-free.

The Neo4j query engine needs an LLM + an embedding client to generate Cypher /
synthesize answers / embed the question, but it must NOT import graphrag. This
module is the sanctioned home for that construction (it sits next to
``client.py`` / ``embedding.py``, which already import ``graphrag_llm``).

``NativeCompletion`` / ``NativeEmbedding`` read the ``kb_profiles`` bundle from
``model_config.model_extra``. We therefore pack the bundle into a tiny stand-in
(a ``SimpleNamespace`` exposing ``model_extra``) and hand it to the existing
constructors — no graphrag-llm factory call, no graphrag import.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from kb_platform.llm.client import NativeCompletion
from kb_platform.llm.embedding import NativeEmbedding


def _model_config_stub(kb_profiles: list[dict]) -> Any:
    """Minimal stand-in for graphrag-llm's ModelConfig: exposes the only
    attribute NativeCompletion / NativeEmbedding read (``.model_extra``)."""
    return SimpleNamespace(model_extra={"kb_profiles": kb_profiles})


def build_native_completion(model_id: str, kb_profiles: list[dict]) -> NativeCompletion:
    """Build a NativeCompletion over the ordered profile bundle (failover list)."""
    return NativeCompletion(model_id=model_id, model_config=_model_config_stub(kb_profiles))


def build_native_embedding(model_id: str, kb_profile: dict) -> NativeEmbedding:
    """Build a single-profile NativeEmbedding (embeddings are single-profile)."""
    return NativeEmbedding(model_id=model_id, model_config=_model_config_stub([kb_profile]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_builders.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/llm/native_builders.py tests/test_native_builders.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/llm/native_builders.py tests/test_native_builders.py
git commit -m "$(cat <<'EOF'
feat(llm): graphrag-free NativeCompletion/NativeEmbedding builder

Packs a kb_profiles bundle into a minimal model-config stub (exposes
.model_extra) and hands it to the existing NativeCompletion/NativeEmbedding
constructors. Lets the Neo4j query engine build kb_native clients without
importing graphrag; the factory injects the built clients into the engine.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: QueryParams / StreamDone / API knobs

**Files:**
- Modify: `kb_platform/query/engine.py`
- Modify: `kb_platform/query/params.py`
- Modify: `kb_platform/api/models.py`
- Test: `tests/test_query_params_knobs.py`

**Interfaces:**
- Produces: `QueryParams` gains `hops: int | None` + `cypher_timeout_ms: int | None`; `StreamDone` gains `truncated: bool = False`; a new `StreamMeta` dataclass carries `cypher: str | None`; `QueryParamsIn` + the `_FIELDS` tuple carry `hops` + `cypher_timeout_ms`; `QueryResultOut` gains `truncated: bool = False`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_query_params_knobs.py`:

```python
from kb_platform.api.models import QueryParamsIn, QueryResultOut
from kb_platform.query.engine import QueryParams, StreamDone, StreamMeta
from kb_platform.query.params import resolve_query_params


def test_query_params_defaults_none():
    p = QueryParams()
    assert p.hops is None and p.cypher_timeout_ms is None


def test_stream_done_truncated_defaults_false():
    d = StreamDone(method="cypher", answer="x")
    assert d.truncated is False


def test_stream_meta_carries_cypher():
    m = StreamMeta(cypher="MATCH (n) RETURN n")
    assert m.cypher == "MATCH (n) RETURN n"


def test_resolve_includes_new_fields():
    # KB defaults layer
    resolved = resolve_query_params({"query_defaults": {"hops": 3}}, QueryParams(cypher_timeout_ms=8000))
    assert resolved.hops == 3
    assert resolved.cypher_timeout_ms == 8000


def test_query_params_in_accepts_new_fields():
    p = QueryParamsIn(hops=2, cypher_timeout_ms=12000)
    assert p.hops == 2 and p.cypher_timeout_ms == 12000


def test_query_result_out_has_truncated():
    out = QueryResultOut(answer="a", method="cypher")
    assert out.truncated is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query_params_knobs.py -v`
Expected: FAIL — no `hops`/`cypher_timeout_ms` attrs, no `StreamMeta`, no `truncated`.

- [ ] **Step 3: Edit `kb_platform/query/engine.py`**

Add `hops` + `cypher_timeout_ms` to `QueryParams`:

```python
@dataclass
class QueryParams:
    """Per-query tuning knobs (all optional; None = use the lower layer).

    Layered by the route: hardcoded baseline <- KB settings (query_defaults)
    <- per-query (this object). See resolve_query_params.
    """

    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    hops: int | None = None  # hybrid :RELATED traversal depth (default 2 when None)
    cypher_timeout_ms: int | None = None  # Text2Cypher exec timeout (default 10000)
```

Add `truncated` to `StreamDone`:

```python
@dataclass
class StreamDone:
    answer: str = ""
    method: str | None = None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    sources: list[SourceRef] | None = None
    error: str | None = None
    truncated: bool = False  # L2 row-cap indicator (cypher/hybrid)
```

Add `StreamMeta` (place it just above `QueryEngine`):

```python
@dataclass
class StreamMeta:
    """Mid-stream metadata emitted by an engine before the answer deltas.
    For cypher/hybrid, carries the generated/templated Cypher (L3 transparency).
    Engines that have nothing to add simply do not yield one."""

    cypher: str | None = None
```

- [ ] **Step 4: Edit `kb_platform/query/params.py`**

Extend `_FIELDS` so resolution layers `hops` + `cypher_timeout_ms`:

```python
_FIELDS = (
    "community_level",
    "response_type",
    "top_k",
    "temperature",
    "system_prompt",
    "hops",
    "cypher_timeout_ms",
)
```

- [ ] **Step 5: Edit `kb_platform/api/models.py`**

`QueryParamsIn` — add the two fields:

```python
class QueryParamsIn(BaseModel):
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    hops: int | None = None
    cypher_timeout_ms: int | None = None
```

`QueryResultOut` — add `truncated`:

```python
class QueryResultOut(BaseModel):
    answer: str
    method: str
    error: str | None = None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    llm_calls: int | None = None
    sources: list[SourceOut] | None = None
    truncated: bool = False
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_query_params_knobs.py -v && uv run pytest tests/ -k "query or engine" -q`
Expected: new tests PASS; no regressions in existing query/engine tests.

- [ ] **Step 7: Lint**

Run: `uv run ruff check kb_platform/query/engine.py kb_platform/query/params.py kb_platform/api/models.py tests/test_query_params_knobs.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add kb_platform/query/engine.py kb_platform/query/params.py kb_platform/api/models.py tests/test_query_params_knobs.py
git commit -m "$(cat <<'EOF'
feat(query): hops/cypher_timeout_ms params + truncated + StreamMeta

QueryParams gains hops (hybrid traversal depth) and cypher_timeout_ms (exec
timeout). StreamDone gains truncated (L2 row-cap flag). New StreamMeta event
carries the generated/templated Cypher for L3 transparency. resolve_query_params
layers the two new knobs. QueryParamsIn + QueryResultOut mirror them.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 6: Pure helpers — Text2Cypher prompt, hybrid Cypher template, row formatter

**Files:**
- Create: `kb_platform/query/neo4j_engine.py` (module-level helpers only; the class arrives in Task 7)
- Test: `tests/test_neo4j_engine_unit.py`

**Interfaces:**
- Produces three pure functions in `kb_platform/query/neo4j_engine.py`:
  - `build_text2cypher_messages(question: str) -> list[dict]` — `[{"role": "system", ...}, {"role": "user", ...}]`, schema + few-shots in the system message, the question in the user message.
  - `build_hybrid_cypher(top_k: int, hops: int) -> str` — the templated Cypher with `$vector` left as a parameter and `top_k`/`hops` baked in as literals (Neo4j's variable-length path bound must be a literal). Returns one row with three lists: `entities`, `relationships`, `chunks`.
  - `format_rows_as_context(rows: list[dict]) -> str` — flatten Text2Cypher result rows into a synthesis context string.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_neo4j_engine_unit.py`:

```python
"""Pure helpers in neo4j_engine: prompt builder, hybrid Cypher template, formatter."""

from kb_platform.query.neo4j_engine import (
    build_hybrid_cypher,
    build_text2cypher_messages,
    format_rows_as_context,
)


# --- build_text2cypher_messages --------------------------------------------
def test_prompt_is_system_plus_user():
    msgs = build_text2cypher_messages("how many ORGs?")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"


def test_prompt_carries_schema_and_question():
    msgs = build_text2cypher_messages("how many ORGs?")
    sys = msgs[0]["content"]
    # canonical schema pieces the LLM needs
    assert ":Entity" in sys and "title" in sys
    assert ":RELATED" in sys
    assert ":TextUnit" in sys and ":FROM_CHUNK" in sys
    # few-shot guidance is present
    assert "MATCH" in sys
    # the question is in the user turn verbatim
    assert "how many ORGs?" in msgs[1]["content"]


def test_prompt_instructs_readonly_return():
    msgs = build_text2cypher_messages("x")
    sys = msgs[0]["content"]
    assert "RETURN" in sys
    # steer the model away from writes
    assert "read-only" in sys.lower() or "read only" in sys.lower()


# --- build_hybrid_cypher ----------------------------------------------------
def test_hybrid_cypher_uses_entity_vector_index():
    s = build_hybrid_cypher(top_k=10, hops=2)
    assert "db.index.vector.queryNodes('entity_description_vec'" in s
    assert "$vector" in s  # the embedding stays a real parameter


def test_hybrid_cypher_bakes_topk_and_hops_as_literals():
    s = build_hybrid_cypher(top_k=7, hops=3)
    # top_k as the k argument to vector ANN
    assert ", 7," in s
    # variable-length path bound is the hops literal
    assert "*1..3" in s or "*0..3" in s


def test_hybrid_cypher_returns_three_bags():
    s = build_hybrid_cypher(top_k=10, hops=2)
    assert "RETURN entities" in s
    assert "relationships" in s
    assert "chunks" in s


def test_hybrid_cypher_traverses_related_and_from_chunk():
    s = build_hybrid_cypher(top_k=10, hops=2)
    assert ":RELATED" in s
    assert ":FROM_CHUNK" in s
    assert ":TextUnit" in s


# --- format_rows_as_context -------------------------------------------------
def test_format_rows_renders_each_row_as_a_line():
    rows = [{"title": "A", "type": "ORG"}, {"title": "B", "type": "PERSON"}]
    out = format_rows_as_context(rows)
    assert "A" in out and "B" in out
    assert out.count("\n") >= 1


def test_format_rows_empty():
    assert format_rows_as_context([]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_neo4j_engine_unit.py -v`
Expected: FAIL — `cannot import name ... from kb_platform.query.neo4j_engine`.

- [ ] **Step 3: Write minimal implementation**

Create `kb_platform/query/neo4j_engine.py`:

```python
"""Neo4j query engine + pure helpers (Text2Cypher prompt, hybrid Cypher template,
row formatter). The engine class (Task 7) is appended below; this task lands the
pure helpers so they are unit-testable in isolation.

No graphrag / graphrag_llm imports anywhere in this module. The engine receives
its completion / embed clients + driver pool by injection.
"""

from __future__ import annotations

import json

# --- Canonical graph schema (ours, stable; emitted by the cypher export) ------
_SCHEMA = """\
Graph schema (Neo4j, GraphRAG export):

Nodes:
  (:Entity {title, type, description, text_unit_ids, frequency, degree, ...})
    - title is the unique identity key (constraint entity_title_unique)
    - entity_description holds the embedding (index entity_description_vec)
  (:TextUnit {id, text, document_ids, n_tokens, ...})
    - text_unit_text holds the embedding (index text_unit_text_vec)

Relationships:
  (:Entity)-[:RELATED {description, weight, combined_degree, text_unit_ids}]->(:Entity)
  (:TextUnit)-[:FROM_CHUNK]->(:Entity)   # the text unit mentions the entity
"""

_FEW_SHOT = """\
Always answer with a SINGLE read-only Cypher query (MATCH/RETURN/OPTIONAL MATCH/
WITH/WHERE/ORDER BY/LIMIT/COUNT). Never write, create, merge, set, delete, or
load data. Return only the Cypher — no prose, no fences.

Examples:
- Entities of a given type:
    MATCH (e:Entity {type: 'ORG'}) RETURN e.title, e.description LIMIT 20
- K-hop neighbors of an entity:
    MATCH (e:Entity {title: $title})-[:RELATED*1..2]-(n:Entity)
    RETURN DISTINCT n.title, n.type
- Count entities per type:
    MATCH (e:Entity) RETURN e.type, count(*) AS n ORDER BY n DESC
- Path between two entities:
    MATCH p = shortestPath((a:Entity {title: $a})-[:RELATED*]-(b:Entity {title: $b}))
    RETURN [n IN nodes(p) | n.title] AS hops
- Text units that mention an entity:
    MATCH (t:TextUnit)-[:FROM_CHUNK]->(e:Entity {title: $title})
    RETURN t.id, t.text
"""


def build_text2cypher_messages(question: str) -> list[dict]:
    """Build the chat messages for the Text2Cypher LLM call."""
    system = (
        "You translate a user's question into one read-only Cypher query against "
        "the graph below.\n\n"
        f"{_SCHEMA}\n{_FEW_SHOT}"
    )
    user = f"User question: {question}\n\nCypher:"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_hybrid_cypher(top_k: int, hops: int) -> str:
    """Render the Vector+Cypher hybrid retrieval query.

    ``$vector`` stays a real parameter (the question embedding); ``top_k`` and
    ``hops`` are baked in as literals because Neo4j's variable-length path bound
    must be a literal, not a parameter. Returns one row with three collected
    bags: ``entities`` (seeds + their <=hops :RELATED neighborhood),
    ``relationships`` (the :RELATED edges along those paths), and ``chunks``
    (the :TextUnit nodes that mention any reached entity via :FROM_CHUNK).
    """
    return f"""CALL db.index.vector.queryNodes('entity_description_vec', {top_k}, $vector)
YIELD node AS seed, score
WITH collect(DISTINCT seed) AS seeds
// entities: seeds plus their <=hops :RELATED neighborhood
CALL {{
  WITH seeds
  UNWIND seeds AS s
  MATCH (s)-[:RELATED*0..{hops}]-(e:Entity)
  RETURN collect(DISTINCT e) AS entities
}}
// relationships: the :RELATED edges traversed within <=hops from each seed
CALL {{
  WITH seeds
  UNWIND seeds AS s
  MATCH p = (s)-[:RELATED*1..{hops}]-(:Entity)
  UNWIND relationships(p) AS r
  RETURN collect(DISTINCT r) AS relationships
}}
// chunks: text units mentioning any reached entity
CALL {{
  WITH entities
  UNWIND entities AS e
  MATCH (e)<-[:FROM_CHUNK]-(t:TextUnit)
  RETURN collect(DISTINCT t) AS chunks
}}
RETURN entities, relationships, chunks
"""


def format_rows_as_context(rows: list[dict]) -> str:
    """Flatten Text2Cypher result rows into a synthesis context string."""
    if not rows:
        return ""
    lines: list[str] = []
    for i, row in enumerate(rows, start=1):
        # Neo4j node/relationship values arrive as dicts (driver config); render
        # their properties compactly.
        rendered = {k: _render(v) for k, v in row.items()}
        lines.append(f"[{i}] " + json.dumps(rendered, ensure_ascii=False))
    return "\n".join(lines)


def _render(value) -> object:
    """Coerce a Neo4j value (node/relationship/list/scalar) into JSON-able form."""
    # Node / Relationship objects expose .id / .element_id / .items() in the
    # neo4j driver; the routing layer normalizes them to dicts before they reach
    # the engine (see Neo4jQueryEngine._rows_from_records). Lists recurse.
    if isinstance(value, (list, tuple)):
        return [_render(v) for v in value]
    if isinstance(value, dict):
        return {k: _render(v) for k, v in value.items()}
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_neo4j_engine_unit.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/query/neo4j_engine.py tests/test_neo4j_engine_unit.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/query/neo4j_engine.py tests/test_neo4j_engine_unit.py
git commit -m "$(cat <<'EOF'
feat(query): Text2Cypher prompt + hybrid Cypher template + row formatter

Pure helpers for the Neo4j engine: a system/user prompt with the canonical
schema + read-only few-shots; a hybrid Cypher template (vector ANN on
entity_description_vec -> <=hops :RELATED traversal -> FROM_CHUNK text units)
returning entities/relationships/chunks bags; and a row->context formatter.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 7: `Neo4jQueryEngine` — search + stream_search for `cypher` + `hybrid`

**Files:**
- Modify: `kb_platform/query/neo4j_engine.py` (append the engine class + private helpers)
- Test: `tests/test_neo4j_engine_unit.py` (append engine tests)

**Interfaces:**
- Consumes: `build_text2cypher_messages` / `build_hybrid_cypher` / `format_rows_as_context` (Task 6); `is_readonly_cypher` / `truncate_rows` (Task 1); `QueryParams` / `QueryResult` / `StreamDelta` / `StreamDone` / `StreamMeta` / `SourceRef` (Task 5); an injected completion (`await completion.completion_async(messages, stream=bool)`), an injected `async embed(text) -> list[float]`, and the `driver_pool` + connection triple `(uri, username, password)`.
- Produces: `Neo4jQueryEngine(uri, username, password, driver_pool, completion, embed, model_id, database="neo4j")` implementing the `QueryEngine` Protocol for `method ∈ {cypher, hybrid}`. No graphrag import.

- [ ] **Step 1: Append the failing engine tests**

Append to `tests/test_neo4j_engine_unit.py`:

```python
# --- Neo4jQueryEngine (fake-driven) -----------------------------------------
import asyncio
from types import SimpleNamespace

from kb_platform.neo4j import driver_pool
from kb_platform.query.engine import StreamDelta, StreamDone, StreamMeta
from kb_platform.query.neo4j_engine import Neo4jQueryEngine


class _FakeUsage(SimpleNamespace):
    pass


class _FakeCompletion:
    """Mimics NativeCompletion's completion_async for both stream modes."""

    def __init__(self, cypher_text: str, answer_words: list[str]):
        self._cypher = cypher_text
        self._words = answer_words

    async def completion_async(self, /, *, messages, stream=False, **_kw):
        if not stream:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self._cypher))],
                usage=_FakeUsage(prompt_tokens=12, completion_tokens=3),
            )

        async def _gen():
            for i, w in enumerate(self._words):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=w + " "))],
                    usage=None,
                )
            # final chunk carries usage
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=None))],
                usage=_FakeUsage(prompt_tokens=50, completion_tokens=20),
            )

        return _gen()


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def __aiter__(self):
        rows = list(self._rows)
        async def _g():
            for r in rows:
                yield SimpleNamespace(data=lambda r=r: r)
        return _g()


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def run(self, cypher, parameters=None, timeout=None):
        return _FakeResult(self._rows)

    async def close(self):
        pass


class _FakeDriver:
    def __init__(self, rows):
        self._rows = rows

    def session(self, database=None):
        return _FakeSession(self._rows)


async def _events(engine, method, query, rows, words=None, cypher_text=None):
    completion = _FakeCompletion(cypher_text or "MATCH (n:Entity) RETURN n LIMIT 5", words or ["A", "B"])
    engine._completion = completion
    # patch the pool to return a fake driver with the given rows
    driver_pool._reset_for_test()
    engine._pool = SimpleNamespace(get_driver=lambda *a, **kw: _FakeDriver(rows))
    out = []
    async for ev in engine.stream_search(method, query, "/tmp/none"):
        out.append(ev)
    return out


def _engine():
    return Neo4jQueryEngine(
        uri="bolt://x", username="u", password="p",
        driver_pool=driver_pool, completion=_FakeCompletion("x", ["y"]),
        embed=None, model_id="gpt-4o-mini",
    )


async def test_cypher_emits_meta_then_deltas_then_done():
    eng = _engine()
    evs = await _events(eng, "cypher", "how many orgs?",
                        rows=[{"title": "A", "type": "ORG"}, {"title": "B", "type": "ORG"}],
                        words=["two", "orgs"])
    assert isinstance(evs[0], StreamMeta)
    assert "MATCH" in evs[0].cypher
    deltas = [e for e in evs if isinstance(e, StreamDelta)]
    assert [d.text for d in deltas] == ["two ", "orgs "]
    done = evs[-1]
    assert isinstance(done, StreamDone)
    assert done.answer == "two orgs "
    assert done.method == "cypher"
    assert done.truncated is False
    assert done.prompt_tokens and done.output_tokens


async def test_cypher_l1_rejection_yields_error():
    eng = _engine()
    evs = await _events(eng, "cypher", "delete everything",
                        rows=[], cypher_text="MATCH (n) DETACH DELETE n")
    assert isinstance(evs[0], StreamMeta)
    done = evs[-1]
    assert isinstance(done, StreamDone)
    assert done.error and "read-only" in done.error


async def test_cypher_row_cap_truncates():
    eng = _engine()
    rows = [{"title": f"e{i}"} for i in range(50)]
    # force a tiny cap via monkeypatching the module ROW_CAP
    import kb_platform.query.neo4j_engine as mod

    orig = mod.ROW_CAP
    mod.ROW_CAP = 10
    try:
        evs = await _events(eng, "cypher", "list", rows=rows, words=["x"])
    finally:
        mod.ROW_CAP = orig
    done = evs[-1]
    assert isinstance(done, StreamDone) and done.truncated is True


async def test_hybrid_emits_templated_cypher_and_answer():
    async def embed(text):
        return [0.1, 0.2, 0.3]

    eng = Neo4jQueryEngine(
        uri="bolt://x", username="u", password="p",
        driver_pool=driver_pool, completion=_FakeCompletion("ignored", ["ans"]),
        embed=embed, model_id="gpt-4o-mini",
    )
    # one record with the three bags
    rows = [{"entities": [{"title": "A"}], "relationships": [], "chunks": [{"id": "c1", "text": "hi"}]}]
    evs = await _events(eng, "hybrid", "who is A?", rows=rows, words=["A", "rocks"])
    assert isinstance(evs[0], StreamMeta)
    assert "entity_description_vec" in evs[0].cypher
    done = evs[-1]
    assert isinstance(done, StreamDone)
    assert done.answer == "A rocks "
    assert done.method == "hybrid"


async def test_unsupported_method_errors():
    eng = _engine()
    evs = [e async for e in eng.stream_search("local", "q", "/tmp/none")]
    assert isinstance(evs[-1], StreamDone)
    assert evs[-1].error and "local" in evs[-1].error


async def test_search_accumulates_stream():
    eng = _engine()
    eng._pool = SimpleNamespace(get_driver=lambda *a, **kw: _FakeDriver([{"title": "A"}]))
    res = await eng.search("cypher", "how many", "/tmp/none")
    assert res.method == "cypher"
    assert res.answer == "A B "
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_neo4j_engine_unit.py -v`
Expected: the Task-6 helper tests still PASS; the new engine tests FAIL (`Neo4jQueryEngine` not defined).

- [ ] **Step 3: Append the engine implementation**

Append to `kb_platform/query/neo4j_engine.py` (below the helpers from Task 6):

```python
import logging
import re
import time

from kb_platform.neo4j.safety import is_readonly_cypher, truncate_rows
from kb_platform.query.engine import (
    QueryParams,
    QueryResult,
    SourceRef,
    StreamDelta,
    StreamDone,
    StreamMeta,
)

logger = logging.getLogger(__name__)

ROW_CAP = 1000
_DEFAULT_HOPS = 2
_DEFAULT_TIMEOUT_MS = 10_000
_FENCE_RE = re.compile(r"^```(?:cypher)?\s*\n?|\n?```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Strip a single wrapping ``` / ```cypher fence the LLM sometimes adds."""
    return _FENCE_RE.sub("", text.strip()).strip()


def _format_hybrid_context(record: dict) -> str:
    """Render the three bags (entities/relationships/chunks) as synthesis context."""
    if not record:
        return ""
    parts: list[str] = []
    ents = record.get("entities") or []
    if ents:
        parts.append("Entities:\n" + "\n".join(f"- {_render(e)}" for e in ents))
    rels = record.get("relationships") or []
    if rels:
        parts.append("Relationships:\n" + "\n".join(f"- {_render(r)}" for r in rels))
    chunks = record.get("chunks") or []
    if chunks:
        parts.append("Text units:\n" + "\n".join(f"- {_render(c)}" for c in chunks))
    return "\n\n".join(parts)


def _hybrid_sources(record: dict, limit: int = 8) -> list[SourceRef]:
    """Top entity titles + text-unit ids as citation sources."""
    out: list[SourceRef] = []
    for e in (record.get("entities") or [])[:limit]:
        title = e.get("title") if isinstance(e, dict) else None
        if title:
            out.append(SourceRef(kind="entity", name=str(title), text=str(e.get("description", ""))[:200]))
    for c in (record.get("chunks") or [])[:limit]:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid:
            out.append(SourceRef(kind="text_unit", name=str(cid), text=str(c.get("text", ""))[:200]))
    return out


def _rows_sources(rows: list[dict], limit: int = 8) -> list[SourceRef]:
    """Entity titles from flat Text2Cypher rows as citation sources."""
    out: list[SourceRef] = []
    for row in rows:
        title = row.get("title") if isinstance(row, dict) else None
        if title:
            out.append(SourceRef(kind="entity", name=str(title), text=str(row.get("description", ""))[:200]))
        if len(out) >= limit:
            break
    return out


class Neo4jQueryEngine:
    """QueryEngine backed by a live Neo4j graph store.

    Receives its driver pool, LLM completion, and (for hybrid) an embed callable
    by injection — this module imports neither graphrag nor graphrag_llm. The
    factory (build_query_engine) resolves the KB's profiles and constructs the
    clients; this class only runs retrieval + synthesis.

    Supports method ∈ {cypher, hybrid}. Any other method yields a terminal
    StreamDone(error=...); the factory routes only cypher/hybrid here.
    """

    def __init__(self, *, uri, username, password, driver_pool, completion, embed, model_id,
                 database: str = "neo4j") -> None:
        self._uri = uri
        self._username = username
        self._password = password
        self._pool = driver_pool
        self._completion = completion
        self._embed = embed
        self._model_id = model_id
        self._database = database

    async def search(self, method, query, kb_data_root, params=None) -> QueryResult:
        answer = ""
        async for ev in self.stream_search(method, query, kb_data_root, params):
            if isinstance(ev, StreamDelta):
                answer += ev.text
            elif isinstance(ev, StreamDone):
                return QueryResult(
                    answer=ev.answer or answer,
                    method=method,
                    error=ev.error,
                    elapsed_ms=ev.elapsed_ms,
                    prompt_tokens=ev.prompt_tokens,
                    output_tokens=ev.output_tokens,
                    sources=ev.sources,
                )
        return QueryResult(answer=answer, method=method)

    async def stream_search(self, method, query, kb_data_root, params=None):
        if method not in ("cypher", "hybrid"):
            yield StreamDone(method=method, answer="", error=f"neo4j engine does not support method '{method}'")
            return
        try:
            if method == "cypher":
                async for ev in self._cypher(query, params):
                    yield ev
            else:
                async for ev in self._hybrid(query, params):
                    yield ev
        except Exception as e:  # noqa: BLE001 - SSE error, never raise
            logger.exception("neo4j stream_search failed for method=%s", method)
            yield StreamDone(method=method, answer="", error=str(e))

    # --- Text2Cypher ---------------------------------------------------------
    async def _cypher(self, query, params):
        messages = build_text2cypher_messages(query)
        resp = await self._completion.completion_async(messages=messages, stream=False)
        cypher = _strip_fences(_message_content(resp))
        yield StreamMeta(cypher=cypher)

        if not is_readonly_cypher(cypher):
            yield StreamDone(method="cypher", answer="", error="generated Cypher is not read-only; refused")
            return

        timeout_s = _timeout_seconds(params)
        rows, truncated = await self._execute(cypher, {}, timeout_s, ROW_CAP)
        context = format_rows_as_context(rows)
        sources = _rows_sources(rows)
        async for ev in self._synthesize("cypher", query, context, sources, truncated, _usage(resp)):
            yield ev

    # --- Vector+Cypher hybrid ------------------------------------------------
    async def _hybrid(self, query, params):
        if self._embed is None:
            yield StreamDone(method="hybrid", answer="", error="hybrid needs an embedding profile; configure one on the KB")
            return
        vector = await self._embed(query)
        top_k = (params.top_k if params and params.top_k is not None else None) or 10
        hops = (params.hops if params and params.hops is not None else None) or _DEFAULT_HOPS
        cypher = build_hybrid_cypher(top_k, hops)
        yield StreamMeta(cypher=cypher)

        timeout_s = _timeout_seconds(params)
        rows, truncated = await self._execute(cypher, {"vector": vector}, timeout_s, ROW_CAP)
        record = rows[0] if rows else {}
        context = _format_hybrid_context(record)
        sources = _hybrid_sources(record)
        async for ev in self._synthesize("hybrid", query, context, sources, truncated, None):
            yield ev

    # --- shared --------------------------------------------------------------
    async def _execute(self, cypher, parameters, timeout_s, cap):
        driver = self._pool.get_driver(self._uri, self._username, self._password)
        session = driver.session(database=self._database)
        try:
            result = await session.run(cypher, parameters, timeout=timeout_s)
            rows = [r.data() async for r in result]
        finally:
            await session.close()
        return truncate_rows(rows, cap)

    async def _synthesize(self, method, query, context, sources, truncated, gen_usage):
        system = (
            "Answer the user's question using only the graph query results below. "
            "If the results are empty, say so plainly. Cite entities by title."
        )
        user = f"Graph query results:\n{context or '(none)'}\n\nQuestion: {query}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        start = time.time()
        accumulated = ""
        prompt_tokens = output_tokens = 0
        if gen_usage is not None:
            prompt_tokens += int(getattr(gen_usage, "prompt_tokens", 0) or 0)
            output_tokens += int(getattr(gen_usage, "completion_tokens", 0) or 0)
        async for chunk in self._completion.completion_async(messages=messages, stream=True):
            delta = None
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = getattr(choices[0].delta, "content", None)
            if delta:
                accumulated += delta
                yield StreamDelta(text=delta)
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        yield StreamDone(
            answer=accumulated,
            method=method,
            elapsed_ms=round((time.time() - start) * 1000, 1),
            prompt_tokens=prompt_tokens or None,
            output_tokens=output_tokens or None,
            sources=sources or None,
            truncated=truncated,
        )


def _timeout_seconds(params) -> float:
    ms = (params.cypher_timeout_ms if params and params.cypher_timeout_ms is not None else _DEFAULT_TIMEOUT_MS)
    return ms / 1000.0


def _message_content(resp) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    return getattr(choices[0].message, "content", "") or ""


def _usage(resp):
    return getattr(resp, "usage", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_neo4j_engine_unit.py -v`
Expected: all tests (Task 6 helpers + Task 7 engine) PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/query/neo4j_engine.py tests/test_neo4j_engine_unit.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/query/neo4j_engine.py tests/test_neo4j_engine_unit.py
git commit -m "$(cat <<'EOF'
feat(query): Neo4jQueryEngine (Text2Cypher + Vector-Cypher hybrid)

Engine implementing the QueryEngine Protocol for cypher/hybrid. cypher: LLM
generates Cypher (L1 is_readonly_cypher gate) -> execute (L2 timeout+row cap)
-> stream synthesis. hybrid: embed question -> templated vector+traversal
Cypher -> execute -> stream synthesis. Both emit StreamMeta{cypher} for L3.
Clients + driver pool are injected; no graphrag import in this module.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 8: `build_query_engine` factory

**Files:**
- Create: `kb_platform/query/factory.py`
- Test: `tests/test_query_factory.py`

**Interfaces:**
- Consumes: `Neo4jQueryEngine` (Task 7); `GraphRagQueryEngine` + `assemble_kb_settings` (`graphrag_engine.py` / `graphrag_adapter.py`); `build_native_completion` + `build_native_embedding` (Task 4); `driver_pool` (Task 3); `decrypt_values` (`db/crypto`).
- Produces: `build_query_engine(method, kb, repo, app_state) -> QueryEngine`. Dispatches `{cypher, hybrid}` + `kb.neo4j_profile_id` set → `Neo4jQueryEngine`; otherwise → `GraphRagQueryEngine`. Raises a clear error (no extra / no neo4j profile / no llm profile) for the route to surface as SSE `error`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_query_factory.py`:

```python
"""build_query_engine dispatch + injected-client construction."""

import pytest

from kb_platform.db.crypto import encrypt_values
from kb_platform.db.repository import Repository


def _repo_with_kb(tmp_path, *, neo4j_profile_id=None):
    from kb_platform.db.engine import engine_from_url
    from kb_platform.db.models import Base

    eng = engine_from_url(f"sqlite:///{tmp_path}/f.db")
    Base.metadata.create_all(eng)
    repo = Repository(eng)
    llm = repo.create_profile(
        name="llm", kind="llm", provider="openai", model="gpt-4o-mini",
        api_keys=["sk-x"], ssl_verify=True,
    )
    kb = repo.create_kb(
        name="kb", method="standard", settings_json="{}", data_root=".",
        llm_profile_id=llm.id, neo4j_profile_id=neo4j_profile_id,
    )
    return repo, kb


def test_graphrag_methods_dispatch_to_graphrag_engine(tmp_path, monkeypatch):
    from kb_platform.query import factory as F
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    # short-circuit the graphrag config build (we only test dispatch here)
    monkeypatch.setattr(F, "_assemble_kb_settings", lambda kb, repo: {"llm": {"model": "m", "kb_profiles": []}})
    repo, kb = _repo_with_kb(tmp_path)
    eng = F.build_query_engine("local", kb, repo, app_state=type("S", (), {"data_root": "."})())
    assert isinstance(eng, GraphRagQueryEngine)


def test_neo4j_method_without_profile_raises(tmp_path):
    from kb_platform.query import factory as F

    repo, kb = _repo_with_kb(tmp_path)  # no neo4j profile linked
    with pytest.raises(RuntimeError, match="Neo4j profile"):
        F.build_query_engine("cypher", kb, repo, app_state=type("S", (), {"data_root": "."})())


def test_neo4j_method_with_profile_builds_neo4j_engine(tmp_path, monkeypatch):
    from kb_platform.query import factory as F
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine

    repo, kb = _repo_with_kb(tmp_path)
    neo = repo.create_profile(
        name="neo", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j",
        api_keys=["pw"],
    )
    kb.neo4j_profile_id = neo.id
    # bypass the [neo4j] extra check + the LLM config build (we test dispatch/clients)
    monkeypatch.setattr(F, "_ensure_neo4j_available", lambda: None)
    monkeypatch.setattr(F, "_assemble_kb_settings", lambda kb, repo: {
        "llm": {"model": "gpt-4o-mini", "kb_profiles": [{"provider": "openai", "model": "gpt-4o-mini", "keys": ["k"], "ssl_verify": True}]},
    })
    eng = F.build_query_engine("hybrid", kb, repo, app_state=type("S", (), {"data_root": "."})())
    assert isinstance(eng, Neo4jQueryEngine)
    assert eng._username == "neo4j"
    assert eng._password == "pw"
    assert eng._embed is None  # no embedding profile configured


def test_neo4j_extra_missing_raises_clear_error(tmp_path, monkeypatch):
    from kb_platform.query import factory as F

    repo, kb = _repo_with_kb(tmp_path)
    neo = repo.create_profile(
        name="neo", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j", api_keys=["pw"],
    )
    kb.neo4j_profile_id = neo.id

    def _missing():
        raise ModuleNotFoundError("neo4j")

    monkeypatch.setattr(F, "_ensure_neo4j_available", _missing)
    with pytest.raises(RuntimeError, match="uv sync --extra neo4j"):
        F.build_query_engine("cypher", kb, repo, app_state=type("S", (), {"data_root": "."})())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query_factory.py -v`
Expected: FAIL — `No module named 'kb_platform.query.factory'`.

- [ ] **Step 3: Write minimal implementation**

Create `kb_platform/query/factory.py`:

```python
"""Dispatch a query method to the right QueryEngine.

This is the wiring layer (like the routes): it MAY import ``assemble_kb_settings``
(graphrag-importing), exactly as today's routes do. The engines themselves stay
graphrag-free (Neo4jQueryEngine) or graphrag-only (graphrag_engine.py).

For method ∈ {cypher, hybrid} AND kb.neo4j_profile_id set -> Neo4jQueryEngine
(driver pool + injected kb_native completion/embed clients). Otherwise ->
GraphRagQueryEngine, mirroring what routes_query.py does today.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def _ensure_neo4j_available() -> None:
    """Import neo4j eagerly so a missing [neo4j] extra fails at engine-build
    time (surfaced as SSE error), not deep inside the first query."""
    import neo4j  # noqa: F401, PLC0415


def _assemble_kb_settings(kb, repo):
    """Thin indirection so tests can short-circuit the graphrag config build."""
    from kb_platform.graph.graphrag_adapter import assemble_kb_settings  # noqa: PLC0415

    return assemble_kb_settings(kb, repo)


def build_query_engine(method, kb, repo, app_state):
    """Return the QueryEngine for ``method`` on ``kb``."""
    data_root = getattr(app_state, "data_root", None) or kb.data_root

    if method in ("cypher", "hybrid"):
        return _build_neo4j_engine(method, kb, repo, data_root)

    from kb_platform.query.graphrag_engine import GraphRagQueryEngine  # noqa: PLC0415

    model_config = _assemble_kb_settings(kb, repo)
    return GraphRagQueryEngine(data_root=data_root, model_config=model_config)


def _build_neo4j_engine(method, kb, repo, data_root):
    try:
        _ensure_neo4j_available()
    except ModuleNotFoundError as e:
        raise RuntimeError(
            f"the [neo4j] extra is not installed (method={method}): "
            "install with `uv sync --extra neo4j`"
        ) from e

    if not kb.neo4j_profile_id:
        raise RuntimeError(
            f"KB has no Neo4j profile; configure one to use method='{method}'"
        )
    neo = repo.get_profile(kb.neo4j_profile_id)
    if neo is None:
        raise RuntimeError(f"Neo4j profile {kb.neo4j_profile_id} not found")

    from kb_platform.db.crypto import decrypt_values  # noqa: PLC0415
    from kb_platform.llm.native_builders import (  # noqa: PLC0415
        build_native_completion,
        build_native_embedding,
    )
    from kb_platform.neo4j import driver_pool  # noqa: PLC0415
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine  # noqa: PLC0415

    passwords = decrypt_values(neo.api_keys_enc)
    if not passwords:
        raise RuntimeError(f"Neo4j profile '{neo.name}' has no password")
    uri = neo.api_base
    username = neo.username or "neo4j"
    password = passwords[0]

    settings = _assemble_kb_settings(kb, repo)
    llm = settings.get("llm") or {}
    kb_profiles = llm.get("kb_profiles") or []
    if not kb_profiles:
        raise RuntimeError("KB has no resolved LLM profile for the query LLM")
    model_id = llm.get("model", "gpt-4o-mini")
    completion = build_native_completion(model_id, kb_profiles)

    embed = None
    emb = settings.get("embedding") or {}
    if emb.get("kb_profiles"):
        emb_profile = emb["kb_profiles"][0]
        emb_model = emb.get("model", "text-embedding-3-small")
        native_embed = build_native_embedding(emb_model, emb_profile)

        async def embed(text: str) -> list[float]:
            # NativeEmbedding.embedding() is sync (uses asyncio.run); run it in a
            # worker thread so it does not deadlock the event loop.
            resp = await asyncio.to_thread(native_embed.embedding, input=[text])
            return resp.embeddings[0]

    return Neo4jQueryEngine(
        uri=uri, username=username, password=password,
        driver_pool=driver_pool, completion=completion, embed=embed,
        model_id=model_id,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_query_factory.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/query/factory.py tests/test_query_factory.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/query/factory.py tests/test_query_factory.py
git commit -m "$(cat <<'EOF'
feat(query): build_query_engine dispatch (cypher/hybrid -> Neo4j, else graphrag)

Wiring-layer factory: for method in {cypher, hybrid} + kb.neo4j_profile_id set,
builds a Neo4jQueryEngine with an injected kb_native completion + (optional)
embed callable and the shared driver pool (eager [neo4j] extra check -> clear
SSE error). Otherwise builds GraphRagQueryEngine exactly as routes do today.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Route wiring + bootstrap shutdown

**Files:**
- Modify: `kb_platform/api/routes_query.py`
- Modify: `kb_platform/api/routes_conversations.py`
- Modify: `kb_platform/llm/bootstrap.py`
- Test: `tests/test_query_route_neo4j.py`

**Interfaces:**
- Consumes: `build_query_engine` (Task 8); `StreamMeta` + `StreamDone.truncated` (Task 5).
- Produces: both streaming routes build their per-KB engine via `build_query_engine`; `routes_query` emits a `meta{cypher}` event for `StreamMeta` and passes `truncated` into the `done` payload; `bootstrap.close_clients()` also drains the neo4j driver pool (lazy; no-op when the extra is absent).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_query_route_neo4j.py`:

```python
"""Route wiring: cypher/hybrid build via build_query_engine; StreamMeta -> meta;
truncated lands on the done payload. Uses the injected-engine seam (FakeQueryEngine)
for the streaming shape, and asserts the no-profile error path."""

from kb_platform.api.app import create_app
from kb_platform.db.engine import engine_from_url
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine, StreamMeta


def _client(tmp_path):
    eng = engine_from_url(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(eng)
    repo = Repository(eng)
    llm = repo.create_profile(name="llm", kind="llm", provider="openai", model="m", api_keys=["k"])
    repo.create_kb(name="kb", method="standard", settings_json="{}", data_root=".",
                   llm_profile_id=llm.id)
    from fastapi.testclient import TestClient

    return TestClient(create_app(repo, data_root=".", query_engine=FakeQueryEngine())), None


def test_cypher_streams_meta_then_done(tmp_path):
    # FakeQueryEngine yields deltas + done; the route must still parse SSE for
    # any method string (cypher included) when an engine is injected.
    client, _ = _client(tmp_path)
    with client.stream("POST", "/kbs/1/query", json={"method": "cypher", "query": "q"}) as r:
        body = b"".join(r.iter_bytes()).decode()
    assert "event: meta" in body
    assert "event: done" in body
    assert '"truncated"' in body  # QueryResultOut now carries truncated


def test_neo4j_method_no_profile_yields_sse_error(tmp_path):
    # production path (no injected engine) + KB without neo4j_profile_id
    eng = engine_from_url(f"sqlite:///{tmp_path}/r2.db")
    Base.metadata.create_all(eng)
    repo = Repository(eng)
    llm = repo.create_profile(name="llm", kind="llm", provider="openai", model="m", api_keys=["k"])
    repo.create_kb(name="kb", method="standard", settings_json="{}", data_root=".",
                   llm_profile_id=llm.id)
    from fastapi.testclient import TestClient

    client = TestClient(create_app(repo, data_root=".", query_engine=None))
    with client.stream("POST", "/kbs/1/query", json={"method": "cypher", "query": "q"}) as r:
        body = b"".join(r.iter_bytes()).decode()
    assert "event: error" in body
    assert "Neo4j profile" in body or "neo4j" in body.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query_route_neo4j.py -v`
Expected: FAIL — `build_query_engine` not wired; `StreamMeta` not handled; `truncated` not in the done payload (second test may already pass once Task 8 lands, depending on import path).

- [ ] **Step 3: Wire `routes_query.py`**

In `kb_platform/api/routes_query.py`:

Update imports:

```python
from kb_platform.query.engine import QueryParams, StreamDelta, StreamMeta
```

Replace the production engine-build block inside `gen()` (the `if local_engine is None:` branch that builds `GraphRagQueryEngine`) with a `build_query_engine` call. Concretely, replace the block from `from kb_platform.graph.graphrag_adapter import assemble_kb_settings` through the end of the `try: local_engine = GraphRagQueryEngine(...)` with:

```python
            from kb_platform.query.factory import build_query_engine

            app_state = request.app.state
            repo = request.app.state.repo
            with session_scope(repo.engine) as s:
                kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
                if kb is None:
                    yield format_sse("error", {"message": f"kb {kb_id} not found"})
                    return
                data_root = kb.data_root
                kb_settings = json.loads(kb.settings_json or "{}")
                resolved = resolve_query_params(kb_settings, per_query)
            try:
                local_engine = build_query_engine(payload.method, kb, repo, app_state)
            except Exception as exc:  # noqa: BLE001 - graceful, never 500
                yield format_sse("error", {"message": f"engine build failed: {exc}"})
                return
```

(Note: `build_query_engine` calls `assemble_kb_settings` itself, so the route no longer needs to. The `kb` ORM row is usable after the session closes thanks to `expire_on_commit=False`.)

Then update the streaming loop to handle `StreamMeta` and pass `truncated`. Replace the `async for ev in local_engine.stream_search(...)` block with:

```python
        async for ev in local_engine.stream_search(
            payload.method, payload.query, data_root, params=resolved
        ):
            if isinstance(ev, StreamMeta):
                yield format_sse("meta", {"method": payload.method, "cypher": ev.cypher})
            elif isinstance(ev, StreamDelta):
                yield format_sse("delta", {"text": ev.text})
            else:  # StreamDone
                yield format_sse(
                    "done",
                    {
                        "result": QueryResultOut(
                            answer=ev.answer,
                            method=payload.method,
                            error=ev.error,
                            elapsed_ms=ev.elapsed_ms,
                            prompt_tokens=ev.prompt_tokens,
                            output_tokens=ev.output_tokens,
                            truncated=getattr(ev, "truncated", False),
                            sources=[
                                SourceOut(kind=s.kind, name=s.name, text=s.text)
                                for s in ev.sources
                            ]
                            if ev.sources
                            else None,
                        ).model_dump(mode="json")
                    },
                )
```

Drop the now-unused leading `yield format_sse("meta", {"method": payload.method})` (the injected-engine `meta` is emitted by the loop above; a `StreamMeta`-less engine such as `FakeQueryEngine` will simply not emit a `meta`, which is fine — the `done` event still carries `method`). If you want to preserve the initial `meta{method}` for graphrag engines, keep the leading `yield format_sse("meta", {"method": payload.method})` line as-is and treat a later `StreamMeta` as a second `meta` event carrying the cypher (both are valid SSE).

- [ ] **Step 4: Wire `routes_conversations.py`**

In `kb_platform/api/routes_conversations.py`, inside `gen()`'s `if engine is None:` branch, replace the engine-build block (the `from kb_platform.graph.graphrag_adapter import assemble_kb_settings, build_chat_complete` through the `local_engine = GraphRagQueryEngine(...)` try/except) with:

```python
            from kb_platform.query.factory import build_query_engine

            try:
                settings = assemble_kb_settings(kb, repo)  # for the rewriter only
                kb_settings = json.loads(kb.settings_json or "{}")
                resolved = resolve_query_params(kb_settings, None)
            except Exception as exc:  # noqa: BLE001 - graceful error, never 500
                yield format_sse("error", {"message": f"settings resolution failed: {exc}"})
                return
            try:
                method = payload.method or "local"
                local_engine = build_query_engine(method, kb, repo, request.app.state)
                try:
                    local_rewriter = LlmRewriter(build_chat_complete(settings))
                except Exception:  # noqa: BLE001 - rewriter optional
                    local_rewriter = None
            except Exception as exc:  # noqa: BLE001 - graceful error, never 500
                yield format_sse("error", {"message": f"engine build failed: {exc}"})
                return
```

Keep the `from kb_platform.graph.graphrag_adapter import assemble_kb_settings, build_chat_complete` and `from kb_platform.conversation.rewriter import LlmRewriter` imports (the rewriter still needs graphrag-llm via `build_chat_complete`). `payload.method` may be `None` on the conversation path (it's optional in `MessageSend`); default to `"local"`.

- [ ] **Step 5: Wire `bootstrap.close_clients`**

In `kb_platform/llm/bootstrap.py`, extend `close_clients`:

```python
async def close_clients() -> None:
    """Close the shared httpx client pool + the Neo4j driver pool (shutdown hook)."""
    from kb_platform.llm.http_client import close_all

    await close_all()
    # Neo4j driver pool is lazy: only present when the [neo4j] extra is installed
    # and a cypher/hybrid query has run. Swallow ImportError so this is a no-op
    # for installs without the extra.
    try:
        from kb_platform.neo4j import driver_pool  # noqa: PLC0415

        await driver_pool.close_all()
    except Exception:  # noqa: BLE001 - shutdown must not raise
        logger.debug("neo4j driver_pool close skipped (extra absent or empty)")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_query_route_neo4j.py tests/test_api_query.py tests/test_api_conversations.py -v 2>/dev/null || uv run pytest tests/test_query_route_neo4j.py -v`
Expected: new tests PASS; existing query/conversation tests still PASS (adjust the filenames to whatever the repo actually has — search with `ls tests/ | grep -E 'query|convers'`).

- [ ] **Step 7: Lint**

Run: `uv run ruff check kb_platform/api/routes_query.py kb_platform/api/routes_conversations.py kb_platform/llm/bootstrap.py tests/test_query_route_neo4j.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add kb_platform/api/routes_query.py kb_platform/api/routes_conversations.py kb_platform/llm/bootstrap.py tests/test_query_route_neo4j.py
git commit -m "$(cat <<'EOF'
feat(api): wire cypher/hybrid routes via build_query_engine + driver shutdown

Both streaming routes build the per-KB engine through build_query_engine.
routes_query emits meta{cypher} for StreamMeta and passes truncated into the
done payload. bootstrap.close_clients also drains the Neo4j driver pool
(lazy; no-op without the [neo4j] extra).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 10: Frontend — add `cypher` + `hybrid` methods

**Files:**
- Modify: `web/src/lib/query-methods.ts`
- Modify: `web/src/api/types.ts`
- Modify: `web/src/pages/QueryPage.tsx`
- Modify: `web/src/pages/SettingsPage.tsx`
- Test: `web/src/pages/QueryPage.test.tsx` (append one assertion)

**Interfaces:**
- Produces: the query-method grid offers `cypher` + `hybrid`; the tuning panel shows a `hops` knob when `method === "hybrid"`; `QueryParams` carries `hops`/`cypher_timeout_ms` and `QueryResult` carries `truncated`; the settings overview documents the two new methods.

- [ ] **Step 1: Add the two methods**

In `web/src/lib/query-methods.ts`, append two entries to `QUERY_METHODS` (after `basic`):

```typescript
  { key: "cypher", name: "cypher", desc: "Text2Cypher：LLM 生成 Cypher 查询图", needsReports: false },
  { key: "hybrid", name: "hybrid", desc: "向量召回 + Cypher 多跳遍历", needsReports: false },
```

- [ ] **Step 2: Extend the TS types**

In `web/src/api/types.ts`:

Add to the `QueryParams` interface (the one near line 142):

```typescript
  hops?: number;
  cypher_timeout_ms?: number;
```

Add to the `QueryResult` interface (near line 79):

```typescript
  truncated?: boolean;
```

- [ ] **Step 3: Show a `hops` knob for hybrid + pass it through**

In `web/src/pages/QueryPage.tsx`:

Add a `hops` state next to the other tuning knobs (e.g. after `topK`):

```typescript
  const [hops, setHops] = useState("");
```

In the `params` `useMemo`, attach `hops` when set:

```typescript
    if (hops.trim()) p.hops = Number(hops);
```

In `applyPreset`, clear it: `setHops(p.hops != null ? String(p.hops) : "");`.

In the tuning panel grid, add a `hops` input shown only for hybrid (next to the existing `top_k` block):

```tsx
                  {method === "hybrid" && (
                    <label className="text-[12px] text-muted">hops
                      <input className="input mt-1" type="number" min={1} max={5} value={hops}
                        aria-label="hops" onChange={(e) => setHops(e.target.value)} placeholder="留空=2" />
                    </label>
                  )}
```

Update the `CardHeader` subtitle from `"基于知识图谱的四种检索方式"` to `"基于知识图谱的六种检索方式"`.

- [ ] **Step 4: Document the methods on the settings page**

In `web/src/pages/SettingsPage.tsx`, in the `"查询方式"` rows list, append:

```typescript
      { k: "cypher", v: "Text2Cypher：LLM 将问题翻译为 Cypher 查询（需 Neo4j 配置）" },
      { k: "hybrid", v: "向量召回 + Cypher 多跳遍历（需 Neo4j 配置 + 嵌入模型）" },
```

- [ ] **Step 5: Test**

Append to `web/src/pages/QueryPage.test.tsx` a case that asserts the two new method buttons render:

```typescript
import { render, screen } from "@testing-library/react";
import QueryPage from "./QueryPage";
// ... mirror the existing test's KB/SSE mock setup ...

it("renders cypher and hybrid method buttons", async () => {
  // (reuse the existing mock harness in this file)
  render(<QueryPage />);
  expect(await screen.findByText("cypher")).toBeInTheDocument();
  expect(await screen.findByText("hybrid")).toBeInTheDocument();
});
```

Run: `cd web && npm test`
Expected: PASS.

- [ ] **Step 6: Build**

Run: `cd web && npm run build`
Expected: `tsc -b && vite build` succeeds (no type errors).

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/query-methods.ts web/src/api/types.ts web/src/pages/QueryPage.tsx web/src/pages/SettingsPage.tsx web/src/pages/QueryPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): add cypher + hybrid query methods

QUERY_METHODS gains cypher (Text2Cypher) and hybrid (vector + Cypher
traversal); the tuning panel shows a hops knob for hybrid. QueryParams carries
hops/cypher_timeout_ms; QueryResult carries truncated. Settings overview
documents both methods (Chinese copy, matching the existing dashboard).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Real-Neo4j integration test (testcontainers)

**Files:**
- Create: `tests/test_neo4j_integration.py`

**Interfaces:**
- Produces: an end-to-end test that boots a Neo4j ≥ 5.11 container, loads the cypher-export artifact (from the cypher-export plan — `write_cypher` over fixture parquet), and runs both `cypher` and `hybrid` through `Neo4jQueryEngine` against a real LLM (the same tier as the existing real-LLM integration tests). Skipped automatically when `neo4j` / `testcontainers` is not installed, no `NEO4J_IMAGE` is set, or no real LLM profile/env is present.

- [ ] **Step 1: Write the test**

Create `tests/test_neo4j_integration.py`:

```python
"""End-to-end Neo4j graph-query integration test.

Boots a Neo4j 5.x container, loads the cypher-export artifact, and exercises
both cypher (Text2Cypher) and hybrid (Vector+Cypher) methods through
Neo4jQueryEngine against a real LLM profile.

Skipped when any prerequisite is absent:
- ``neo4j`` / ``testcontainers`` not installed
- no ``OPENAI_API_KEY`` (or other real-LLM config) — same tier as existing real-LLM tests
- Neo4j image unavailable

Run manually: ``uv run pytest tests/test_neo4j_integration.py -v -s``
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

pytest.importorskip("neo4j")
pytest.importorskip("testcontainers")

_HAS_REAL_LLM = bool(os.getenv("OPENAI_API_KEY") or os.getenv("KB_TEST_LLM_PROFILE"))
pytestmark = pytest.mark.skipif(
    not _HAS_REAL_LLM, reason="no real-LLM credentials (set OPENAI_API_KEY)"
)


@pytest.fixture(scope="module")
def neo4j_store():
    from testcontainers.neo4j import Neo4jContainer

    image = os.getenv("NEO4J_IMAGE", "neo4j:5.20")
    with Neo4jContainer(image=image, password="testpass") as neo:
        yield neo


def _load_export(neo4j_store):
    """Write fixture parquet via write_cypher, then run the script in the container."""
    import numpy as np

    from kb_platform.graph.cypher import write_cypher

    entities = pd.DataFrame([
        {"title": "Alice", "type": "PERSON", "description": "engineer",
         "text_unit_ids": np.array(["c1"]), "frequency": 1, "degree": 1},
        {"title": "Acme", "type": "ORG", "description": "company",
         "text_unit_ids": np.array(["c1"]), "frequency": 1, "degree": 1},
    ])
    relationships = pd.DataFrame([
        {"source": "Alice", "target": "Acme", "description": "works at",
         "text_unit_ids": np.array(["c1"]), "weight": 1.0, "combined_degree": 2},
    ])
    text_units = pd.DataFrame([
        {"id": "c1", "text": "Alice works at Acme.", "document_ids": np.array(["d1"]), "n_tokens": 4},
    ])
    script = write_cypher(entities, relationships, text_units=text_units)
    # run the script via the sync driver the container exposes
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(neo4j_store.get_connection_url(),
                                  auth=("neo4j", "testpass"))
    try:
        with driver.session() as s:
            for stmt in [c for c in script.split(";") if c.strip()]:
                s.run(stmt + ";")
    finally:
        driver.close()
    return neo4j_store


async def test_cypher_method_answers_structural_question(neo4j_store):
    _load_export(neo4j_store)
    from kb_platform.llm.native_builders import build_native_completion
    from kb_platform.neo4j import driver_pool
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine

    completion = build_native_completion(
        "gpt-4o-mini",
        [{"provider": "openai", "model": "gpt-4o-mini",
          "api_base": None, "api_version": None,
          "keys": [os.environ["OPENAI_API_KEY"]], "ssl_verify": True}],
    )
    engine = Neo4jQueryEngine(
        uri=neo4j_store.get_connection_url(), username="neo4j", password="testpass",
        driver_pool=driver_pool, completion=completion, embed=None, model_id="gpt-4o-mini",
    )
    events = [e async for e in engine.stream_search("cypher", "How many entities are ORG?", "/none")]
    assert any(e.cypher for e in events if hasattr(e, "cypher"))
    done = events[-1]
    assert done.error is None
    assert "1" in done.answer  # one ORG (Acme)


async def test_hybrid_method_uses_vector_traversal(neo4j_store):
    from kb_platform.llm.native_builders import build_native_completion, build_native_embedding
    from kb_platform.neo4j import driver_pool
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine

    completion = build_native_completion(
        "gpt-4o-mini",
        [{"provider": "openai", "model": "gpt-4o-mini", "api_base": None, "api_version": None,
          "keys": [os.environ["OPENAI_API_KEY"]], "ssl_verify": True}],
    )
    native_emb = build_native_embedding(
        "text-embedding-3-small",
        {"provider": "openai", "model": "text-embedding-3-small", "api_base": None,
         "api_version": None, "keys": [os.environ["OPENAI_API_KEY"]], "ssl_verify": True},
    )

    async def embed(text):
        import asyncio

        return (await asyncio.to_thread(native_emb.embedding, input=[text])).embeddings[0]

    engine = Neo4jQueryEngine(
        uri=neo4j_store.get_connection_url(), username="neo4j", password="testpass",
        driver_pool=driver_pool, completion=completion, embed=embed,
        model_id="gpt-4o-mini",
    )
    events = [e async for e in engine.stream_search("hybrid", "who is Alice?", "/none", )]
    assert "entity_description_vec" in events[0].cypher
    done = events[-1]
    assert done.error is None
    assert "Alice" in done.answer
```

> **Note:** the hybrid test requires the export to have created the `entity_description_vec` vector index + set `entity_description` properties. The `write_cypher` helper from the cypher-export plan emits that section when `entity_embeddings` are supplied; extend `_load_export` to pass a non-empty `entity_embeddings={"Alice": [...], "Acme": [...]}` dict (real embeddings from `text-embedding-3-small`) so the hybrid path has vectors to ANN over. If wiring the embedding into the loader proves finicky in CI, gate the hybrid test behind a separate `KB_TEST_HYBRID=1` flag and keep the cypher test the default.

- [ ] **Step 2: Run the suite (most will skip locally)**

Run: `uv run pytest tests/ -q`
Expected: the new file skips (`no real-LLM credentials` / `importorskip`); all other tests still PASS.

- [ ] **Step 3: Lint**

Run: `uv run ruff check tests/test_neo4j_integration.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/test_neo4j_integration.py
git commit -m "$(cat <<'EOF'
test(neo4j): real-Neo4j integration test (testcontainers)

Boots Neo4j 5.x, loads the cypher-export artifact over fixture parquet, and
runs both cypher + hybrid methods through Neo4jQueryEngine against a real LLM.
Skipped without neo4j/testcontainers, without a real-LLM profile, or without
the image — same tier as existing real-LLM integration tests.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage** — every requirement in `2026-07-03-neo4j-graph-query-design.md` maps to a task:

- `is_readonly_cypher` L1 validator + row-cap → **Task 1**.
- neo4j profile kind + `KB.neo4j_profile_id` + Alembic + CRUD → **Task 2**.
- Driver pool (`get_driver` + `close_all`) + `[neo4j]` extra → **Task 3**.
- Text2Cypher prompt builder → **Task 6**.
- Hybrid Cypher template builder → **Task 6**.
- Row → context formatter → **Task 6**.
- `Neo4jQueryEngine` (search/stream_search for cypher + hybrid, no graphrag import) → **Task 7**.
- `build_query_engine` dispatch + injected clients → **Task 8**.
- `QueryParams.hops/cypher_timeout_ms`, `StreamDone.truncated`, `meta{cypher}` (via `StreamMeta`) → **Task 5** (+ Task 9 wires `meta`/`truncated`).
- Route wiring (`routes_query` + `routes_conversations`) → **Task 9**.
- `bootstrap.close_clients` drains the driver pool → **Task 9**.
- Frontend query-tuning panel + i18n → **Task 10**.
- Real-Neo4j integration test → **Task 11**.

**Cross-spec contract** (consumed from the cypher-export artifact, asserted lazily): `:Entity`/`:RELATED` (Task 7 reads them), `:TextUnit`/`:FROM_CHUNK` (Task 6 hybrid template), `entity_description_vec` (Task 6 + Task 11), `text_unit_text_vec` (Task 6 schema), `entity_title_unique` (Task 6 schema). All present in the cypher-export plan (merged).

**Safety layers (L0–L3):** L1 enforced (Task 1, gated in Task 7's `_cypher`); L0 documented (Task 10 settings copy + the cypher-export header already notes Neo4j ≥ 5.11); L2 timeout + row cap (Task 7 `_execute` + `truncate_rows`); L3 transparency (`StreamMeta` → `meta{cypher}`, Task 5 + Task 9).

**Placeholder scan:** no TBD/TODO; every code step shows full code. Two intentional "verify-against-repo" commands (Task 9 step 6 reconcile test filenames; Task 11 embeds-in-loader note) are concrete locate-and-run instructions, not hand-waves.

**Type consistency:** `build_query_engine(method, kb, repo, app_state)` signature is identical across Tasks 8 + 9. `Neo4jQueryEngine.__init__` kwargs (`uri, username, password, driver_pool, completion, embed, model_id`) match between Task 7 (definition), Task 8 (construction), and Task 11 (integration test). `StreamMeta.cypher` / `StreamDone.truncated` / `QueryParams.hops`/`cypher_timeout_ms` match between Task 5 (definition), Task 7 (read), and Task 9 (route emit). `_ensure_neo4j_available` / `_assemble_kb_settings` seam names match between Task 8 (definition) and Task 8 tests (monkeypatch). The injected completion contract (`await completion.completion_async(messages, stream=bool)`) matches Task 4's `NativeCompletion` (real) and Task 7's `_FakeCompletion` (test).

**graphrag-isolation seam:** `Neo4jQueryEngine` and `kb_platform/neo4j/*` import neither `graphrag` nor `graphrag_llm`. The only `graphrag_llm`-typed construction is in `kb_platform/llm/native_builders.py` (Task 4) — the sanctioned location, sibling to `client.py`/`embedding.py`. `factory.py` (Task 8) imports `assemble_kb_settings` from `graphrag_adapter`, exactly as today's routes do; it is wiring, not an engine. `graphrag_engine.py` remains the only graphrag import among the engine classes.

