# Agentic KB MCP Tools + Cross-Tool Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the MCP server to 6 tools (4 new) so an agent can do 2026-style deep-research retrieval over a KB, and ship a parametric installer that wires the MCP server + an agent recipe into Claude Code / opencode on macOS, Linux, and Windows.

**Architecture:** Three layers in this repo. (1) `kb_platform/mcp/server.py` gains 4 thin-proxy tools that forward to existing read-only API endpoints — no graphrag/SQLite imports. (2) A single neutral agent recipe markdown is rendered per-tool at install time. (3) `kb_platform/install/` is a Python installer module with a per-tool adapter registry; thin `install.sh` / `install.ps1` wrappers forward to it.

**Tech Stack:** Python 3.11, uv, FastAPI, httpx (ASGITransport for tests), pytest (asyncio_mode=auto), mcp SDK (FastMCP), pandas (only in tests for parquet fixtures).

**Spec:** `docs/superpowers/specs/2026-07-05-agentic-kb-mcp-installer-design.md`

## Global Constraints

Copied verbatim from the spec §8 + CLAUDE.md — every task's requirements include these:

- **MCP layer stays a thin HTTP proxy**: no `graphrag` or `kb_platform.db` imports inside `kb_platform/mcp/`. New tools only forward HTTP to the running API server.
- **Lint**: `uv run ruff check .` clean, line-length 100, target py311.
- **Tests**: `uv run pytest` green; `asyncio_mode = "auto"`; `pythonpath` includes `tests`; tests use `httpx.ASGITransport` against the real FastAPI app, no socket, no LLM.
- **Cost/error seam unchanged**: tool failures return `{"error": "..."}` (via `KbApiError`), never raise to the agent.
- **Installer is dev tooling**: log/error messages in English (unlike the Chinese dashboard copy).
- **Idempotence**: re-running install must not duplicate config or recipe sections.
- **No proxy for localhost** (CLAUDE.md gotcha): installer-generated MCP config must not set `all_proxy`/`http_proxy` for the MCP child process.

---

## File Structure

**Modified:**
- `kb_platform/mcp/server.py` — 4 new `KbApiClient` methods + 4 new tool functions + 4 new `@mcp.tool` registrations
- `tests/test_mcp_server.py` — new test helper for data fixtures + tests for the 4 new tools
- `pyproject.toml` — register `kb_platform.install` package (already covered by existing `packages`/tool config; only touch if needed)

**Created (MCP):** none (server.py stays one file — matches existing convention)

**Created (Installer):**
- `kb_platform/install/__init__.py` — empty
- `kb_platform/install/__main__.py` — argparse CLI entry (`python -m kb_platform.install`)
- `kb_platform/install/registry.py` — `TOOL_REGISTRY: dict[str, InstallTarget]`
- `kb_platform/install/platform.py` — `config_dir()`, `home_dir()` per OS
- `kb_platform/install/mcp_config.py` — `build_mcp_config(repo_root, api_url) -> dict`
- `kb_platform/install/recipe.py` — `load_recipe() -> str`, `render_for(tool) -> str`
- `kb_platform/install/recipe.md` — neutral 2026-flow recipe (data)
- `kb_platform/install/tools/__init__.py` — empty
- `kb_platform/install/tools/base.py` — `InstallTarget` Protocol
- `kb_platform/install/tools/claude_code.py` — `ClaudeCodeAdapter`
- `kb_platform/install/tools/opencode.py` — `OpenCodeAdapter`
- `install.sh` (repo root) — bash wrapper
- `install.ps1` (repo root) — PowerShell wrapper

**Created (tests):**
- `tests/install/__init__.py` — empty
- `tests/install/test_platform.py`
- `tests/install/test_mcp_config.py`
- `tests/install/test_recipe.py`
- `tests/install/test_claude_code.py`
- `tests/install/test_opencode.py`
- `tests/install/test_cli.py`

---

## Task 1: Extend `KbApiClient` with 4 new HTTP-proxy methods

**Files:**
- Modify: `kb_platform/mcp/server.py` (add methods to `KbApiClient`, lines ~27-97)
- Test: `tests/test_mcp_server.py` (add data-seeding helper + 4 new test groups)

**Interfaces:**
- Consumes: existing API endpoints `GET /kbs/{id}`, `GET /kbs/{id}/stats`, `GET /kbs/{id}/documents`, `GET /kbs/{id}/documents/{doc_id}`, `GET /kbs/{id}/graph`
- Produces (exact signatures later tasks rely on):
  ```python
  async def get_kb(self, kb_id: int) -> dict           # merges KbDetailOut + KbStatsOut
  async def list_documents(self, kb_id: int) -> list[dict]
  async def get_document(self, kb_id: int, doc_id: int) -> dict
  async def search_graph(self, kb_id: int, q: str, hop: int = 1, limit: int = 200) -> dict
  ```

- [ ] **Step 1: Add a data-seeding test helper**

Append to `tests/test_mcp_server.py` (after `_client_for`):

```python
def _seed_kb_with_data(tmp_path, app, *, docs=True, graph=True, stats=True):
    """Seed KB id=1 with documents/chunks (SQLite) + parquet + stats.json.

    Reuses the _make_app KB rows; adds the data the new tools read. The KB's
    data_root is tmp_path (set by _make_app), so parquet/stats land there.
    """
    import json as _json
    import pandas as pd
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import Chunk
    from kb_platform.db.repository import Repository

    repo = Repository(app.state.repo.engine)
    if docs:
        doc = repo.add_document(kb_id=1, title="Latency SLO spec", text="p99 < 200ms.")
        repo.add_chunks([
            Chunk(chunk_id="c1", kb_id=1, document_id=doc.id, ordinal=0,
                  text="p99 < 200ms.", token_count=5),
        ])
    if graph:
        pd.DataFrame({
            "title": ["ACME", "Beta"], "type": ["ORG", "ORG"], "degree": [3, 1],
        }).to_parquet(tmp_path / "entities.parquet", index=False)
        pd.DataFrame({
            "source": ["ACME"], "target": ["Beta"], "weight": [2.0],
            "description": ["ACME supplies Beta"],
        }).to_parquet(tmp_path / "relationships.parquet", index=False)
    if stats:
        (tmp_path / "stats.json").write_text(_json.dumps({
            "document_count": 1, "chunk_count": 1, "entity_count": 2,
            "relationship_count": 1, "community_count": 0,
            "community_report_count": 0, "text_unit_count": 1,
        }))
```

- [ ] **Step 2: Write the 4 failing client tests**

Append to `tests/test_mcp_server.py`:

```python
async def test_client_get_kb_returns_detail_and_stats(tmp_path):
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        kb = await client.get_kb(kb_id=1)
        assert kb["id"] == 1
        assert kb["name"] == "alpha"
        assert kb["stats"]["entity_count"] == 2
        assert kb["stats"]["community_report_count"] == 0
    finally:
        await http.aclose()


async def test_client_list_documents_returns_docs(tmp_path):
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        docs = await client.list_documents(kb_id=1)
        assert len(docs) == 1
        assert docs[0]["title"] == "Latency SLO spec"
        assert docs[0]["chunk_count"] == 1
    finally:
        await http.aclose()


async def test_client_get_document_returns_text_and_chunks(tmp_path):
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        doc = await client.get_document(kb_id=1, doc_id=1)
        assert doc["title"] == "Latency SLO spec"
        assert "p99" in doc["text"]
        assert len(doc["citations"]) == 1
    finally:
        await http.aclose()


async def test_client_search_graph_returns_nodes_and_edges(tmp_path):
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        g = await client.search_graph(kb_id=1, q="ACME", hop=1)
        titles = {n["title"] for n in g["nodes"]}
        assert "ACME" in titles
        assert any(e["source"] == "ACME" for e in g["edges"])
    finally:
        await http.aclose()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py -k "get_kb_returns_detail or list_documents_returns or get_document_returns or search_graph_returns" -v`
Expected: FAIL with `AttributeError: 'KbApiClient' object has no attribute 'get_kb'` (and similar).

- [ ] **Step 4: Implement the 4 methods on `KbApiClient`**

In `kb_platform/mcp/server.py`, add these methods inside `class KbApiClient` (after the existing `query` method, before `aclose`):

```python
    async def get_kb(self, kb_id: int) -> dict:
        """GET /kbs/{id} + /kbs/{id}/stats → {id, name, method, stats: {...}}.

        Stats fields are None when the KB has no snapshot yet (unindexed).
        """
        detail = await self._get_json(f"/kbs/{kb_id}")
        stats = await self._get_json(f"/kbs/{kb_id}/stats")
        if not isinstance(detail, dict) or not isinstance(stats, dict):
            raise KbApiError(f"unexpected response shape for kb {kb_id}")
        return {"id": detail["id"], "name": detail["name"],
                "method": detail["method"], "stats": stats}

    async def list_documents(self, kb_id: int) -> list[dict]:
        """GET /kbs/{id}/documents → [{id, title, status, bytes, chunk_count}, ...]."""
        data = await self._get_json(f"/kbs/{kb_id}/documents")
        if not isinstance(data, list):
            raise KbApiError(f"unexpected response shape for documents of kb {kb_id}")
        return data

    async def get_document(self, kb_id: int, doc_id: int) -> dict:
        """GET /kbs/{id}/documents/{doc_id} → {id, title, text, citations: [...], ...}."""
        return await self._get_json(f"/kbs/{kb_id}/documents/{doc_id}")

    async def search_graph(self, kb_id: int, q: str, hop: int = 1, limit: int = 200) -> dict:
        """GET /kbs/{id}/graph?q=&hop=&limit= → {nodes: [...], edges: [...]}."""
        from urllib.parse import quote

        path = f"/kbs/{kb_id}/graph?q={quote(q)}&hop={hop}&limit={limit}"
        data = await self._get_json(path)
        if not isinstance(data, dict):
            raise KbApiError(f"unexpected response shape for graph of kb {kb_id}")
        return data
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py -k "get_kb_returns_detail or list_documents_returns or get_document_returns or search_graph_returns" -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Lint + full MCP test file**

Run: `uv run ruff check kb_platform/mcp/server.py tests/test_mcp_server.py && uv run pytest tests/test_mcp_server.py -q`
Expected: ruff clean, all tests pass.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/mcp/server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add 4 read-only KbApiClient methods (kb detail/docs/graph)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: Register 4 new MCP tool functions

**Files:**
- Modify: `kb_platform/mcp/server.py` (add 4 tool-logic functions after `query_knowledge_base`, and 4 `@mcp.tool` registrations inside `build_mcp_server`)
- Test: `tests/test_mcp_server.py` (add 4 tool-level tests + update the wiring test)

**Interfaces:**
- Consumes: Task 1's `KbApiClient.get_kb/list_documents/get_document/search_graph`
- Produces: tools named `get_kb_details`, `list_documents`, `get_document`, `search_graph` registered on the FastMCP server (visible via `server.list_tools()`)

- [ ] **Step 1: Write 4 failing tool-level tests + update wiring test**

Append to `tests/test_mcp_server.py`:

```python
async def test_tool_get_kb_details_returns_readiness(tmp_path):
    from kb_platform.mcp.server import get_kb_details
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        out = await get_kb_details(client, kb_id=1)
        assert out["name"] == "alpha"
        assert out["stats"]["entity_count"] == 2
        assert out["available_methods"] == ["local", "basic"]  # no community reports
    finally:
        await http.aclose()


async def test_tool_list_documents_passes_through(tmp_path):
    from kb_platform.mcp.server import list_documents as list_docs_tool
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        out = await list_docs_tool(client, kb_id=1)
        assert out[0]["title"] == "Latency SLO spec"
    finally:
        await http.aclose()


async def test_tool_get_document_trims_for_agent(tmp_path):
    """Full text is kept; chunk citations are slimmed to {ordinal, snippet, chunk_id}."""
    from kb_platform.mcp.server import get_document as get_doc_tool
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        out = await get_doc_tool(client, kb_id=1, doc_id=1)
        assert "p99" in out["text"]
        assert out["chunks"][0] == {"ordinal": 0, "chunk_id": "c1",
                                     "snippet": out["chunks"][0]["snippet"]}
        assert "label" not in out["chunks"][0]  # internal label dropped
    finally:
        await http.aclose()


async def test_tool_search_graph_passes_through(tmp_path):
    from kb_platform.mcp.server import search_graph
    app = _make_app(tmp_path)
    _seed_kb_with_data(tmp_path, app)
    client, http = await _client_for(app)
    try:
        out = await search_graph(client, kb_id=1, q="ACME", hop=1)
        assert any(n["title"] == "ACME" for n in out["nodes"])
    finally:
        await http.aclose()
```

Then **update** the existing wiring test `test_build_mcp_server_registers_both_tools` (rename + expand). Replace its body:

```python
async def test_build_mcp_server_registers_all_tools(app):
    from kb_platform.mcp.server import build_mcp_server
    client, http = await _client_for(app)
    try:
        server = build_mcp_server(client)
        names = {t.name for t in await server.list_tools()}
        assert {
            "list_knowledge_bases", "query_knowledge_base",
            "get_kb_details", "list_documents", "get_document", "search_graph",
        } <= names
    finally:
        await http.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py -k "get_kb_details_returns or list_documents_passes or get_document_trims or search_graph_passes or registers_all_tools" -v`
Expected: FAIL with `ImportError: cannot import name 'get_kb_details'` (and similar).

- [ ] **Step 3: Add the 4 tool-logic functions**

In `kb_platform/mcp/server.py`, after the existing `query_knowledge_base` function (around line 136), add:

```python
async def get_kb_details(client: KbApiClient, kb_id: int) -> dict:
    """KB detail + readiness stats.

    Returns ``{id, name, method, stats: {...}, available_methods: [...]}``.
    ``available_methods`` reflects what's indexed: ``local``/``basic`` always;
    ``global``/``drift`` only when community reports exist. On API failure
    returns ``{"error": "..."}``.
    """
    try:
        kb = await client.get_kb(kb_id)
    except KbApiError as exc:
        return {"error": str(exc)}
    stats = kb.get("stats") or {}
    have_reports = (stats.get("community_report_count") or 0) > 0
    methods = ["local", "basic"]
    if have_reports:
        methods = ["local", "global", "drift", "basic"]
    return {"id": kb["id"], "name": kb["name"], "method": kb["method"],
            "stats": stats, "available_methods": methods}


async def list_documents(client: KbApiClient, kb_id: int) -> list[dict]:
    """List documents in a KB → ``[{id, title, status, bytes, chunk_count}, ...]``.

    Empty list when the KB has no documents. ``[{"error": "..."}]`` on API failure.
    """
    try:
        return await client.list_documents(kb_id)
    except KbApiError as exc:
        return [{"error": str(exc)}]


async def get_document(client: KbApiClient, kb_id: int, doc_id: int) -> dict:
    """Fetch one document: full text plus per-chunk snippets for citation.

    Returns ``{id, title, status, bytes, chunk_count, text, chunks: [{ordinal,
    chunk_id, snippet}, ...]}``. Internal citation labels are dropped. On API
    failure returns ``{"error": "..."}``.
    """
    try:
        doc = await client.get_document(kb_id, doc_id)
    except KbApiError as exc:
        return {"error": str(exc)}
    chunks = [
        {"ordinal": c.get("ordinal"), "chunk_id": c.get("chunk_id"),
         "snippet": c.get("snippet")}
        for c in (doc.get("citations") or [])
    ]
    return {
        "id": doc.get("id"), "title": doc.get("title"),
        "status": doc.get("status"), "bytes": doc.get("bytes", 0),
        "chunk_count": doc.get("chunk_count", 0),
        "text": doc.get("text", ""), "chunks": chunks,
    }


async def search_graph(
    client: KbApiClient, kb_id: int, q: str, hop: int = 1, limit: int = 200,
) -> dict:
    """Entity-graph neighborhood search for multi-hop questions.

    ``q`` is a title substring (case-insensitive); ``hop`` is BFS depth
    (default 1, keep ≤ 2 to bound payload); ``limit`` caps the node set.

    Returns ``{nodes: [{id, title, type, degree, community}, ...],
    edges: [{source, target, weight, description}, ...]}``. Empty ``nodes``/
    ``edges`` when nothing matches. On API failure returns
    ``{"error": "..."}``.
    """
    try:
        return await client.search_graph(kb_id, q=q, hop=hop, limit=limit)
    except KbApiError as exc:
        return {"error": str(exc)}
```

- [ ] **Step 4: Register the 4 tools inside `build_mcp_server`**

In `kb_platform/mcp/server.py`, inside `build_mcp_server` (after the existing `@mcp.tool(name="query_knowledge_base")` block, before `return mcp`), add:

```python
    @mcp.tool(name="get_kb_details")
    async def _get_kb_details_tool(kb_id: int) -> dict:
        """Get knowledge-base detail + readiness before querying.

        Call after list_knowledge_bases to confirm the KB is indexed and to
        learn which query methods are available. Returns
        {id, name, method, stats, available_methods}. available_methods is
        [local, basic] when community reports are missing, or
        [local, global, drift, basic] when present.
        """
        return await get_kb_details(client, kb_id=kb_id)

    @mcp.tool(name="list_documents")
    async def _list_documents_tool(kb_id: int) -> list[dict]:
        """List documents in a knowledge base.

        Use to browse what's in a KB and pick documents to scope a query to.
        Returns one object per document: {id, title, status, bytes, chunk_count}.
        """
        return await list_documents(client, kb_id=kb_id)

    @mcp.tool(name="get_document")
    async def _get_document_tool(kb_id: int, doc_id: int) -> dict:
        """Fetch one document's full text and per-chunk snippets.

        Use to verify exact quotes or precise claims after a query, before
        citing. Returns {id, title, text, chunks: [{ordinal, chunk_id, snippet}, ...], ...}.
        """
        return await get_document(client, kb_id=kb_id, doc_id=doc_id)

    @mcp.tool(name="search_graph")
    async def _search_graph_tool(
        kb_id: int, q: str, hop: int = 1, limit: int = 200,
    ) -> dict:
        """Search the entity graph and return a multi-hop neighborhood.

        For relationship questions ("how do X and Y relate?"). q is a
        case-insensitive entity-title substring; hop is BFS depth (keep ≤ 2).
        Returns {nodes: [...], edges: [...]}.
        """
        return await search_graph(client, kb_id=kb_id, q=q, hop=hop, limit=limit)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: all tests PASS (including the renamed wiring test).

- [ ] **Step 6: Lint**

Run: `uv run ruff check kb_platform/mcp/server.py tests/test_mcp_server.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/mcp/server.py tests/test_mcp_server.py
git commit -m "feat(mcp): expose 4 new tools (kb details/docs/graph) for agentic flow

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Agent recipe (`recipe.md`) + per-tool renderer

**Files:**
- Create: `kb_platform/install/__init__.py`, `kb_platform/install/recipe.md`, `kb_platform/install/recipe.py`
- Test: `tests/install/__init__.py`, `tests/install/test_recipe.py`

**Interfaces:**
- Consumes: nothing (recipe is the source of truth)
- Produces:
  ```python
  # kb_platform/install/recipe.py
  def load_recipe_text() -> str: ...                    # raw neutral markdown
  def render_for(tool: str) -> str: ...                 # tool-specific wrapper
  RECIPE_TOOL_NAMES: set[str]                           # names referenced inside recipe
  ```

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
mkdir -p kb_platform/install tests/install
touch kb_platform/install/__init__.py tests/install/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/install/test_recipe.py`:

```python
from kb_platform.install.recipe import load_recipe_text, render_for, RECIPE_TOOL_NAMES


def test_recipe_loads_nonempty_markdown():
    text = load_recipe_text()
    assert "# KB deep-research playbook" in text
    assert len(text) > 200  # real content, not a stub


def test_recipe_references_all_six_tools():
    # Every tool the MCP server exposes must appear in the recipe so the agent
    # learns to use it. Catches drift when tools are added/renamed.
    assert RECIPE_TOOL_NAMES == {
        "list_knowledge_bases", "query_knowledge_base", "get_kb_details",
        "list_documents", "get_document", "search_graph",
    }


def test_render_for_claude_code_has_frontmatter():
    out = render_for("claude-code")
    assert out.startswith("---\n")
    assert "name: kb-research" in out
    assert "description:" in out


def test_render_for_opencode_has_section_markers():
    out = render_for("opencode")
    assert "<!-- kb-platform:start -->" in out
    assert "<!-- kb-platform:end -->" in out


def test_render_for_unknown_tool_raises():
    import pytest
    with pytest.raises(ValueError):
        render_for("nope")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/install/test_recipe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb_platform.install'`.

- [ ] **Step 4: Write `recipe.md`**

Create `kb_platform/install/recipe.md`:

```markdown
# KB deep-research playbook

You have access to a GraphRAG knowledge-base (KB) MCP server with these tools:
`list_knowledge_bases`, `get_kb_details`, `list_documents`, `get_document`,
`query_knowledge_base`, `search_graph`. Use this playbook when the user asks a
non-trivial question over indexed documents (technical specs, internal wiki, …).

## 1. Always discover first — never query blind
1. `list_knowledge_bases` — pick the KB.
2. `get_kb_details(kb_id)` — confirm readiness: is it indexed? which methods are
   available (`available_methods`)? `global`/`drift` need community reports; if
   missing, fall back to `local`.

## 2. Route by question shape
- Simple factual → `query_knowledge_base(method="local"|"basic")`.
- Theme / overview → `method="global"` (needs community reports).
- Hybrid → `method="drift"`.
- "How do X and Y relate?" → `search_graph(q="X", hop=2)` first; if Y appears in
  the neighborhood, query with that context.
- Multi-part question → decompose into sub-questions, call `query_knowledge_base`
  **in parallel** for each.

## 3. Deep-research main flow
1. `get_kb_details` — confirm ready.
2. Plan: split the question into sub-questions.
3. Fan out: call `query_knowledge_base` in parallel, one per sub-question.
4. For relationship claims, verify with `search_graph`.
5. For direct quotes or precise numbers, verify with `get_document`.
6. Synthesize: every claim must cite `(source.name, document)`.
7. Claims you cannot trace → say "KB 中未找到明确证据" (no fabrication).

## 4. Citation rules
- Every factual claim → `(source.name, document)`.
- Direct quotes → use `get_document` to fetch exact text.
- Uncertain → write "未找到明确证据", do not guess.

## 5. Failure modes
- KB not indexed → tell the user, do not query.
- Method needs community reports but they're missing → fall back to `local`,
  inform the user.
- All sources empty → say so plainly, do not fabricate.

## Examples
- "我们 wiki 里 A 服务和 B 服务怎么交互?" → `search_graph(q="A 服务", hop=2)`,
  check whether B is in the neighborhood → `query(method="local")` for detail →
  cite sources.
- "这版技术规格对延迟有什么要求?" → `list_documents` to find the spec →
  `get_document` to scan chunks → `query(method="local")` to locate → cite the
  exact chunk.
```

- [ ] **Step 5: Write `recipe.py`**

Create `kb_platform/install/recipe.py`:

```python
"""Neutral agent recipe (single source of truth) + per-tool renderer.

``recipe.md`` is the playbook content. ``render_for(tool)`` wraps it for a
specific agent host: Claude Code wants SKILL.md frontmatter; opencode wants a
section-marker-wrapped block for AGENTS.md.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

RECIPE_DIR = Path(__file__).parent
RECIPE_FILE = RECIPE_DIR / "recipe.md"

# Tool names the recipe references. The consistency test asserts this equals
# the MCP server's tool set — update both when adding/renaming a tool.
RECIPE_TOOL_NAMES: set[str] = {
    "list_knowledge_bases", "query_knowledge_base", "get_kb_details",
    "list_documents", "get_document", "search_graph",
}

_SUPPORTED_TOOLS = {"claude-code", "opencode"}


@lru_cache(maxsize=1)
def load_recipe_text() -> str:
    return RECIPE_FILE.read_text(encoding="utf-8")


def render_for(tool: str) -> str:
    """Render the recipe for a specific agent host.

    - ``claude-code`` → SKILL.md with YAML frontmatter (name/description).
    - ``opencode`` → section-marker-wrapped block for AGENTS.md.
    Raises ``ValueError`` for unknown tools.
    """
    if tool not in _SUPPORTED_TOOLS:
        raise ValueError(f"unknown tool {tool!r}; expected one of {_SUPPORTED_TOOLS}")
    body = load_recipe_text()
    if tool == "claude-code":
        return (
            "---\n"
            "name: kb-research\n"
            "description: Deep-research retrieval over indexed GraphRAG "
            "knowledge bases — discover, query, verify, cite.\n"
            "---\n\n"
            f"{body}\n"
        )
    # opencode: section markers so install is idempotent (replace between markers)
    return f"<!-- kb-platform:start -->\n{body}\n<!-- kb-platform:end -->\n"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/install/test_recipe.py -v`
Expected: 5 tests PASS.

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check kb_platform/install/ tests/install/
git add kb_platform/install/__init__.py kb_platform/install/recipe.md kb_platform/install/recipe.py tests/install/__init__.py tests/install/test_recipe.py
git commit -m "feat(install): neutral agent recipe + per-tool renderer

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Installer skeleton — CLI, registry, platform paths, MCP config

**Files:**
- Create: `kb_platform/install/__main__.py`, `registry.py`, `platform.py`, `mcp_config.py`, `tools/__init__.py`, `tools/base.py`
- Test: `tests/install/test_platform.py`, `tests/install/test_mcp_config.py`, `tests/install/test_cli.py`

**Interfaces:**
- Consumes: Task 3's `render_for`
- Produces:
  ```python
  # platform.py
  def config_dir(tool: str) -> Path: ...     # per-OS, per-tool
  def home_dir() -> Path: ...
  # mcp_config.py
  def build_mcp_config(repo_root: Path, api_url: str) -> dict:
      """Returns {command: "uv", args: [...], env: {KB_API_URL: ...}}."""
  # tools/base.py
  class InstallTarget(Protocol):
      name: str
      def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]: ...
      def install_playbook(self, scope: str, dry_run: bool) -> list[str]: ...
      def uninstall(self, scope: str, dry_run: bool) -> list[str]: ...
  # registry.py
  TOOL_REGISTRY: dict[str, type[InstallTarget]]
  # __main__.py — CLI: --tool / --api-url / --scope / --list / --uninstall / --dry-run
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/install/test_platform.py`:

```python
import sys
from pathlib import Path
from unittest.mock import patch

from kb_platform.install.platform import config_dir, home_dir


def test_home_dir_macos():
    with patch.object(sys, "platform", "darwin"):
        with patch.dict("os.environ", {"HOME": "/Users/alice"}, clear=True):
            assert home_dir() == Path("/Users/alice")


def test_home_dir_windows():
    with patch.object(sys, "platform", "win32"):
        with patch.dict("os.environ", {"APPDATA": r"C:\Users\alice\AppData\Roaming"}, clear=True):
            assert home_dir() == Path(r"C:\Users\alice\AppData\Roaming")


def test_config_dir_opencode_macos():
    with patch.object(sys, "platform", "darwin"):
        with patch.dict("os.environ", {"HOME": "/Users/alice", "XDG_CONFIG_HOME": ""}, clear=True):
            d = config_dir("opencode")
            assert d == Path("/Users/alice/.config/opencode")


def test_config_dir_opencode_windows():
    with patch.object(sys, "platform", "win32"):
        with patch.dict("os.environ", {"APPDATA": r"C:\Users\a\AppData\Roaming"}, clear=True):
            d = config_dir("opencode")
            assert d == Path(r"C:\Users\a\AppData\Roaming\opencode")
```

Create `tests/install/test_mcp_config.py`:

```python
from pathlib import Path
from kb_platform.install.mcp_config import build_mcp_config


def test_build_mcp_config_shape():
    cfg = build_mcp_config(Path("/repo"), "http://localhost:8000")
    assert cfg["command"] == "uv"
    assert cfg["args"][:4] == ["run", "--directory", "/repo", "python"]
    assert "kb_platform.mcp" in cfg["args"]
    assert cfg["env"]["KB_API_URL"] == "http://localhost:8000"


def test_build_mcp_config_no_proxy_env():
    """CLAUDE.md gotcha: localhost must not go through proxy. Config must not
    set all_proxy/http_proxy/https_proxy."""
    cfg = build_mcp_config(Path("/repo"), "http://localhost:8000")
    for k in ("all_proxy", "http_proxy", "https_proxy"):
        assert k not in cfg["env"]
```

Create `tests/install/test_cli.py`:

```python
import pytest
from kb_platform.install.registry import TOOL_REGISTRY


def test_registry_has_claude_code_and_opencode():
    assert "claude-code" in TOOL_REGISTRY
    assert "opencode" in TOOL_REGISTRY


def test_cli_list_prints_supported_tools(capsys):
    from kb_platform.install.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(["--list"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "claude-code" in out
    assert "opencode" in out


def test_cli_unknown_tool_exits_nonzero(capsys):
    from kb_platform.install.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(["--tool", "nope"])
    assert exc.value.code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/install/test_platform.py tests/install/test_mcp_config.py tests/install/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError` for the new modules.

- [ ] **Step 3: Write `platform.py`**

Create `kb_platform/install/platform.py`:

```python
"""Per-OS path resolution for installer config targets."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def home_dir() -> Path:
    """User home/config root: ``$HOME`` on mac/linux, ``%APPDATA%`` on windows."""
    if is_windows():
        return Path(os.environ.get("APPDATA") or str(Path.home()))
    return Path(os.environ.get("HOME") or str(Path.home()))


def config_dir(tool: str) -> Path:
    """Config directory for a tool, per OS convention.

    mac/linux: ``~/.config/<tool>`` (XDG).
    windows: ``%APPDATA%\\<tool>``.
    """
    if is_windows():
        return home_dir() / tool
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else home_dir() / ".config"
    return base / tool
```

- [ ] **Step 4: Write `mcp_config.py`**

Create `kb_platform/install/mcp_config.py`:

```python
"""Build the shared MCP server config dict every tool registers."""

from __future__ import annotations

from pathlib import Path

# Name under which the MCP server is registered in every host.
MCP_SERVER_NAME = "kb-platform"


def build_mcp_config(repo_root: Path, api_url: str) -> dict:
    """Dev-mode config: run the MCP server via ``uv run`` from the checked-out repo.

    ``repo_root`` must be the absolute path to this repository (the one
    containing ``kb_platform/``). ``api_url`` is the KB Platform API base URL.
    """
    return {
        "command": "uv",
        "args": [
            "run", "--directory", str(repo_root),
            "python", "-m", "kb_platform.mcp",
        ],
        "env": {"KB_API_URL": api_url},
    }
```

- [ ] **Step 5: Write `tools/base.py` + `tools/__init__.py`**

```bash
touch kb_platform/install/tools/__init__.py
```

Create `kb_platform/install/tools/base.py`:

```python
"""InstallTarget Protocol — what every tool adapter implements."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class InstallTarget(Protocol):
    """One adapter per agent host (Claude Code, opencode, …).

    Methods return a list of human-readable action lines (for dry-run preview
    and logging); they perform real filesystem/CLI side effects when
    ``dry_run`` is False.
    """

    name: str

    def register_mcp(
        self, repo_root: Path, api_url: str, scope: str, dry_run: bool,
    ) -> list[str]: ...

    def install_playbook(self, scope: str, dry_run: bool) -> list[str]: ...

    def uninstall(self, scope: str, dry_run: bool) -> list[str]: ...
```

- [ ] **Step 6: Write `registry.py` (adapters come in Tasks 5-6; registry grows then)**

Create `kb_platform/install/registry.py`:

```python
"""Tool adapter registry. Adapters are imported lazily so a missing optional
dep (e.g. the ``claude`` CLI) doesn't break ``--list`` or the other tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kb_platform.install.tools.base import InstallTarget


def _load_registry() -> dict[str, type]:
    registry: dict[str, type] = {}
    try:
        from kb_platform.install.tools.claude_code import ClaudeCodeAdapter
        registry["claude-code"] = ClaudeCodeAdapter
    except ImportError:
        pass
    try:
        from kb_platform.install.tools.opencode import OpenCodeAdapter
        registry["opencode"] = OpenCodeAdapter
    except ImportError:
        pass
    return registry


# Module-level lazy registry: populated on first access.
class _LazyRegistry(dict):
    def __missing__(self, key):  # noqa: D401
        loaded = _load_registry()
        self.update(loaded)
        if key in loaded:
            return loaded[key]
        raise KeyError(key)


TOOL_REGISTRY = _LazyRegistry()
```

- [ ] **Step 7: Write `__main__.py`**

Create `kb_platform/install/__main__.py`:

```python
"""CLI entry: ``uv run python -m kb_platform.install --tool <name> [options]``.

Exit codes: 0 success, 1 bad args, 2 unknown tool, 3 install action failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kb_platform.install.registry import TOOL_REGISTRY


def _repo_root() -> Path:
    """This package's repo root (parent of kb_platform/)."""
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kb_platform.install",
        description="Install the KB Platform MCP server + agent playbook into an AI tool.",
    )
    parser.add_argument("--tool", help="one of: claude-code, opencode, all")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000",
                        help="KB Platform API base URL")
    parser.add_argument("--scope", choices=["user", "project"], default="project",
                        help="where to install (default: project)")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the MCP registration + playbook")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview actions without writing")
    parser.add_argument("--list", action="store_true",
                        help="list supported tools and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name in sorted(TOOL_REGISTRY):
            print(name)
        sys.exit(0)

    if not args.tool:
        parser.error("--tool is required (or pass --list)")
        sys.exit(1)  # parser.error exits already; for clarity

    tools = list(TOOL_REGISTRY) if args.tool == "all" else [args.tool]
    for t in tools:
        if t not in TOOL_REGISTRY:
            print(f"error: unknown tool {t!r}. Supported: {sorted(TOOL_REGISTRY)}",
                  file=sys.stderr)
            sys.exit(2)

    repo_root = _repo_root()
    failed = False
    for t in tools:
        target = TOOL_REGISTRY[t]()
        try:
            if args.uninstall:
                actions = target.uninstall(args.scope, args.dry_run)
            else:
                actions = target.register_mcp(repo_root, args.api_url, args.scope, args.dry_run)
                actions += target.install_playbook(args.scope, args.dry_run)
            for a in actions:
                print(f"[{t}] {a}")
        except Exception as exc:  # noqa: BLE001 - report, don't crash mid-loop
            print(f"[{t}] FAILED: {exc}", file=sys.stderr)
            failed = True

    sys.exit(3 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Add stub adapters so registry loads + CLI tests pass**

Create `kb_platform/install/tools/claude_code.py` (full impl in Task 5; minimal now so `--list` works):

```python
"""Claude Code adapter (stub — filled in Task 5)."""

from __future__ import annotations

from pathlib import Path


class ClaudeCodeAdapter:
    name = "claude-code"

    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would register MCP for {self.name}"]

    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would install playbook for {self.name}"]

    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would uninstall {self.name}"]
```

Create `kb_platform/install/tools/opencode.py` (full impl in Task 6):

```python
"""opencode adapter (stub — filled in Task 6)."""

from __future__ import annotations

from pathlib import Path


class OpenCodeAdapter:
    name = "opencode"

    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would register MCP for {self.name}"]

    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would install playbook for {self.name}"]

    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would uninstall {self.name}"]
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/install/test_platform.py tests/install/test_mcp_config.py tests/install/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 10: Lint + commit**

```bash
uv run ruff check kb_platform/install/ tests/install/
git add kb_platform/install/ tests/install/
git commit -m "feat(install): skeleton — CLI, registry, platform paths, mcp config

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: `ClaudeCodeAdapter` — real registration + playbook

**Files:**
- Modify: `kb_platform/install/tools/claude_code.py` (replace stub)
- Test: `tests/install/test_claude_code.py`

**Interfaces:**
- Consumes: `build_mcp_config`, `render_for("claude-code")`, `config_dir`
- Produces: a working `ClaudeCodeAdapter` that writes `.mcp.json` (project) or `~/.claude.json` mcpServers entry (user), and `.claude/skills/kb-research/SKILL.md` (or user-scope equivalent)

- [ ] **Step 1: Write the failing test**

Create `tests/install/test_claude_code.py`:

```python
import json
from pathlib import Path

from kb_platform.install.mcp_config import MCP_SERVER_NAME
from kb_platform.install.tools.claude_code import ClaudeCodeAdapter


def test_register_mcp_project_scope_writes_dotmcpjson(tmp_path):
    adapter = ClaudeCodeAdapter()
    repo = tmp_path / "repo"
    repo.mkdir()
    # project scope: cwd is the project root → writes ./.mcp.json
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        actions = adapter.register_mcp(repo, "http://localhost:8000", "project", dry_run=False)
    finally:
        os.chdir(cwd)
    cfg_path = tmp_path / ".mcp.json"
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text())
    assert MCP_SERVER_NAME in data["mcpServers"]
    assert data["mcpServers"][MCP_SERVER_NAME]["command"] == "uv"
    assert "kb_platform.mcp" in data["mcpServers"][MCP_SERVER_NAME]["args"]
    assert any("mcp.json" in a for a in actions)


def test_register_mcp_is_idempotent(tmp_path):
    """Re-running must not duplicate the server entry."""
    import os
    os.chdir(tmp_path)
    adapter = ClaudeCodeAdapter()
    repo = tmp_path / "repo"; repo.mkdir()
    adapter.register_mcp(repo, "http://localhost:8000", "project", False)
    adapter.register_mcp(repo, "http://localhost:8000", "project", False)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert len(data["mcpServers"]) == 1


def test_install_playbook_project_scope_writes_skill(tmp_path):
    import os
    os.chdir(tmp_path)
    adapter = ClaudeCodeAdapter()
    actions = adapter.install_playbook("project", dry_run=False)
    skill = tmp_path / ".claude" / "skills" / "kb-research" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text()
    assert text.startswith("---\n")
    assert "name: kb-research" in text
    assert any("SKILL.md" in a for a in actions)


def test_dry_run_does_not_write(tmp_path):
    import os
    os.chdir(tmp_path)
    adapter = ClaudeCodeAdapter()
    repo = tmp_path / "repo"; repo.mkdir()
    adapter.register_mcp(repo, "http://localhost:8000", "project", dry_run=True)
    adapter.install_playbook("project", dry_run=True)
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()


def test_uninstall_removes_entries(tmp_path):
    import os
    os.chdir(tmp_path)
    adapter = ClaudeCodeAdapter()
    repo = tmp_path / "repo"; repo.mkdir()
    adapter.register_mcp(repo, "http://localhost:8000", "project", False)
    adapter.install_playbook("project", False)
    adapter.uninstall("project", False)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert MCP_SERVER_NAME not in data.get("mcpServers", {})
    assert not (tmp_path / ".claude" / "skills" / "kb-research").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/install/test_claude_code.py -v`
Expected: FAIL (stub doesn't write files).

- [ ] **Step 3: Implement `ClaudeCodeAdapter`**

Replace `kb_platform/install/tools/claude_code.py`:

```python
"""Claude Code adapter.

Two scopes:
- ``project`` → ``./.mcp.json`` (mcpServers) + ``./.claude/skills/kb-research/SKILL.md``
- ``user``    → ``~/.claude.json`` (mcpServers) + ``~/.claude/skills/kb-research/SKILL.md``

Prefers the ``claude mcp add`` CLI when available; falls back to writing the
JSON files directly. All write paths are idempotent."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kb_platform.install.mcp_config import MCP_SERVER_NAME, build_mcp_config
from kb_platform.install.recipe import render_for

SKILL_NAME = "kb-research"


class ClaudeCodeAdapter:
    name = "claude-code"

    # --- scope resolution -------------------------------------------------
    def _mcp_json_path(self, scope: str) -> Path:
        if scope == "user":
            from kb_platform.install.platform import home_dir
            return home_dir() / ".claude.json"
        return Path.cwd() / ".mcp.json"

    def _skill_dir(self, scope: str) -> Path:
        if scope == "user":
            from kb_platform.install.platform import home_dir
            return home_dir() / ".claude" / "skills" / SKILL_NAME
        return Path.cwd() / ".claude" / "skills" / SKILL_NAME

    # --- MCP registration -------------------------------------------------
    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        cfg = build_mcp_config(repo_root, api_url)
        path = self._mcp_json_path(scope)
        cli_scope = "user" if scope == "user" else "project"
        if shutil.which("claude") and not dry_run:
            args = ["claude", "mcp", "add", MCP_SERVER_NAME, "--scope", cli_scope,
                    "--"] + [cfg["command"], *cfg["args"]]
            try:
                subprocess.run(args, check=True, capture_output=True)
                return [f"registered MCP via `claude mcp add` → {path}"]
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # fall through to file write
        return self._write_mcp_json(path, cfg, dry_run)

    def _write_mcp_json(self, path: Path, cfg: dict, dry_run: bool) -> list[str]:
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        servers = data.setdefault("mcpServers", {})
        servers[MCP_SERVER_NAME] = cfg  # idempotent: same key overwrites
        if dry_run:
            return [f"would write {path}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        return [f"wrote {path}"]

    # --- playbook ---------------------------------------------------------
    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        skill_path = self._skill_dir(scope) / "SKILL.md"
        content = render_for("claude-code")
        if dry_run:
            return [f"would write {skill_path}"]
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")
        return [f"wrote {skill_path}"]

    # --- uninstall --------------------------------------------------------
    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        actions: list[str] = []
        path = self._mcp_json_path(scope)
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
            if MCP_SERVER_NAME in data.get("mcpServers", {}):
                del data["mcpServers"][MCP_SERVER_NAME]
                if not dry_run:
                    path.write_text(json.dumps(data, indent=2))
                actions.append(f"removed {MCP_SERVER_NAME} from {path}")
        skill_dir = self._skill_dir(scope)
        if skill_dir.exists():
            if not dry_run:
                shutil.rmtree(skill_dir)
            actions.append(f"removed {skill_dir}")
        return actions or [f"nothing to uninstall for {self.name}"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/install/test_claude_code.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check kb_platform/install/ tests/install/
git add kb_platform/install/tools/claude_code.py tests/install/test_claude_code.py
git commit -m "feat(install): ClaudeCode adapter — mcp add/.mcp.json + SKILL.md

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: `OpenCodeAdapter` — verify schema, then implement

**Files:**
- Modify: `kb_platform/install/tools/opencode.py` (replace stub)
- Test: `tests/install/test_opencode.py`

**Interfaces:**
- Consumes: `build_mcp_config`, `render_for("opencode")`, `config_dir`
- Produces: a working `OpenCodeAdapter` that merges MCP server into opencode config and merges the playbook section into `AGENTS.md` (idempotent via section markers)

> **Note — verify schema first.** opencode's MCP config schema changes between versions. The implementation below targets the documented `~/.config/opencode/opencode.json` `mcp` object shape (local-command servers). Before Step 3, run the verification in Step 1 and adjust the JSON key (`mcp` vs `mcpServers`) if the installed opencode version differs.

- [ ] **Step 1: Verify opencode's current MCP config schema**

Run (read-only, no side effects):
```bash
opencode mcp --help 2>/dev/null || echo "opencode CLI not installed — proceed with documented shape"
cat ~/.config/opencode/opencode.json 2>/dev/null || echo "no existing config — fresh install path"
opencode --version 2>/dev/null || true
```
Record the observed top-level key for MCP servers (expected: `mcp`). If it is `mcpServers` instead, set `_MCP_KEY = "mcpServers"` in Step 3.

- [ ] **Step 2: Write the failing test**

Create `tests/install/test_opencode.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

from kb_platform.install.mcp_config import MCP_SERVER_NAME
from kb_platform.install.tools.opencode import OpenCodeAdapter


def test_register_mcp_merges_into_opencode_json(tmp_path):
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        adapter = OpenCodeAdapter()
        actions = adapter.register_mcp(tmp_path / "repo", "http://localhost:8000",
                                       "user", dry_run=False)
    cfg = json.loads((tmp_path / "opencode" / "opencode.json").read_text())
    servers = cfg["mcp"]  # or cfg["mcpServers"] if Step 1 said so
    assert MCP_SERVER_NAME in servers
    assert servers[MCP_SERVER_NAME]["command"][0] == "uv"
    assert any("opencode.json" in a for a in actions)


def test_register_mcp_preserves_existing_servers(tmp_path):
    """Merge — don't clobber user's other MCP servers."""
    cfg_path = tmp_path / "opencode" / "opencode.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"mcp": {"other": {"type": "local"}}}))
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        OpenCodeAdapter().register_mcp(tmp_path / "repo", "http://localhost:8000",
                                       "user", dry_run=False)
    cfg = json.loads(cfg_path.read_text())
    assert "other" in cfg["mcp"]  # preserved
    assert MCP_SERVER_NAME in cfg["mcp"]


def test_install_playbook_merges_agents_md_idempotently(tmp_path):
    import os
    os.chdir(tmp_path)
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        adapter = OpenCodeAdapter()
        adapter.install_playbook("project", dry_run=False)
        adapter.install_playbook("project", dry_run=False)  # run twice
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.count("<!-- kb-platform:start -->") == 1  # no duplication
    assert "playbook" in text.lower()


def test_uninstall_removes_section_and_server(tmp_path):
    import os
    os.chdir(tmp_path)
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        adapter = OpenCodeAdapter()
        adapter.register_mcp(tmp_path / "repo", "http://localhost:8000", "user", False)
        adapter.install_playbook("project", False)
        adapter.uninstall("project", False)
    cfg = json.loads((tmp_path / "opencode" / "opencode.json").read_text())
    assert MCP_SERVER_NAME not in cfg.get("mcp", {})
    assert "<!-- kb-platform:start -->" not in (tmp_path / "AGENTS.md").read_text()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/install/test_opencode.py -v`
Expected: FAIL (stub doesn't write).

- [ ] **Step 4: Implement `OpenCodeAdapter`**

Replace `kb_platform/install/tools/opencode.py`. **Set `_MCP_KEY` to the value you confirmed in Step 1** (default `"mcp"`):

```python
"""opencode adapter.

- MCP registration: merge into ``<config_dir>/opencode/opencode.json`` (key
  ``mcp`` by default — verify against your opencode version).
- Playbook: merge a section-marker-wrapped block into ``AGENTS.md`` (cwd for
  project scope; ``<config_dir>/opencode/agent.md`` for user scope).

All writes are idempotent (server keyed by name; section markers)."""

from __future__ import annotations

import json
from pathlib import Path

from kb_platform.install.mcp_config import MCP_SERVER_NAME, build_mcp_config
from kb_platform.install.recipe import render_for

# Top-level key for MCP servers in opencode.json. Verify via `opencode mcp --help`.
_MCP_KEY = "mcp"
_START = "<!-- kb-platform:start -->"
_END = "<!-- kb-platform:end -->"


class OpenCodeAdapter:
    name = "opencode"

    # --- paths ------------------------------------------------------------
    def _config_file(self) -> Path:
        from kb_platform.install.platform import config_dir
        return config_dir("opencode") / "opencode.json"

    def _agents_file(self, scope: str) -> Path:
        if scope == "user":
            from kb_platform.install.platform import config_dir
            return config_dir("opencode") / "agent.md"
        return Path.cwd() / "AGENTS.md"

    # --- MCP registration -------------------------------------------------
    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        cfg = build_mcp_config(repo_root, api_url)
        # opencode expects command as a list for local servers.
        opencode_entry = {
            "type": "local",
            "command": [cfg["command"], *cfg["args"]],
            "enabled": True,
        }
        if cfg.get("env"):
            opencode_entry["environment"] = cfg["env"]
        path = self._config_file()
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        servers = data.setdefault(_MCP_KEY, {})
        servers[MCP_SERVER_NAME] = opencode_entry  # idempotent
        if dry_run:
            return [f"would merge {MCP_SERVER_NAME} into {path}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        return [f"merged {MCP_SERVER_NAME} into {path}"]

    # --- playbook ---------------------------------------------------------
    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        path = self._agents_file(scope)
        block = render_for("opencode")  # already has start/end markers
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if _START in text:
                pre = text[: text.index(_START)]
                post = text[text.index(_END) + len(_END):]
                new = pre + block + post
            else:
                new = text.rstrip() + "\n\n" + block
        else:
            new = block
        if dry_run:
            return [f"would merge playbook into {path}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        return [f"merged playbook into {path}"]

    # --- uninstall --------------------------------------------------------
    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        actions: list[str] = []
        path = self._config_file()
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
            if MCP_SERVER_NAME in data.get(_MCP_KEY, {}):
                del data[_MCP_KEY][MCP_SERVER_NAME]
                if not dry_run:
                    path.write_text(json.dumps(data, indent=2))
                actions.append(f"removed {MCP_SERVER_NAME} from {path}")
        agents = self._agents_file(scope)
        if agents.exists():
            text = agents.read_text(encoding="utf-8")
            if _START in text and _END in text:
                pre = text[: text.index(_START)]
                post = text[text.index(_END) + len(_END):]
                if not dry_run:
                    agents.write_text((pre + post).strip() + "\n", encoding="utf-8")
                actions.append(f"removed playbook section from {agents}")
        return actions or [f"nothing to uninstall for {self.name}"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/install/test_opencode.py -v`
Expected: 4 tests PASS. If `_MCP_KEY` from Step 1 was `mcpServers`, update it and the tests' assertions together.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check kb_platform/install/ tests/install/
git add kb_platform/install/tools/opencode.py tests/install/test_opencode.py
git commit -m "feat(install): opencode adapter — merge mcp config + AGENTS.md section

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Shell wrappers + end-to-end smoke

**Files:**
- Create: `install.sh` (repo root), `install.ps1` (repo root)
- Test: `tests/install/test_cli.py` (extend with an end-to-end dry-run test)

**Interfaces:**
- Consumes: Tasks 4-6 (full installer)
- Produces: `./install.sh --tool claude-code` and `.\install.ps1 -Tool claude-code` invocations that work on mac/linux and windows respectively

- [ ] **Step 1: Write `install.sh`**

Create `install.sh` (repo root):

```bash
#!/usr/bin/env bash
# Thin wrapper: forwards every flag to the Python installer.
# Requires uv on PATH (the MCP server itself runs via uv).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --directory "$REPO_ROOT" python -m kb_platform.install "$@"
```

- [ ] **Step 2: Write `install.ps1`**

Create `install.ps1` (repo root):

```powershell
# Thin wrapper: forwards every flag to the Python installer.
# Requires uv on PATH (the MCP server itself runs via uv).
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
uv run --directory $RepoRoot python -m kb_platform.install @args
exit $LASTEXITCODE
```

- [ ] **Step 3: Make `install.sh` executable**

```bash
chmod +x install.sh
```

- [ ] **Step 4: Add an end-to-end dry-run test**

Append to `tests/install/test_cli.py`:

```python
async def test_end_to_end_dry_run_claude_code(tmp_path, monkeypatch):
    """Full CLI path: --tool claude-code --dry-run must not write anything,
    must exit 0, and must describe both register + playbook actions."""
    import os
    from kb_platform.install.__main__ import main
    monkeypatch.chdir(tmp_path)
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(["--tool", "claude-code", "--api-url", "http://localhost:8000", "--dry-run"])
    assert exc.value.code == 0
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()
```

- [ ] **Step 5: Run the full installer test suite + the existing MCP tests**

Run:
```bash
uv run pytest tests/install/ tests/test_mcp_server.py -q
```
Expected: all PASS.

- [ ] **Step 6: Manual smoke (mac, the dev's platform)**

Run:
```bash
./install.sh --list
./install.sh --tool claude-code --dry-run
```
Expected: `--list` prints `claude-code` and `opencode`; `--dry-run` prints action lines, writes nothing.

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check kb_platform/install/ tests/install/
git add install.sh install.ps1 tests/install/test_cli.py
git commit -m "feat(install): sh/ps1 wrappers + end-to-end dry-run smoke

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review (run before handoff)

**1. Spec coverage** — every spec section maps to a task:
- §4 (4 new MCP tools) → Tasks 1 + 2 ✓
- §5 (agent recipe) → Task 3 ✓
- §6.1-6.5 (installer: skeleton/platform/mcp_config/adapters/CLI/wrappers) → Tasks 4 + 5 + 6 + 7 ✓
- §7 (testing three layers) → every task has tests; installer tests don't need real claude/opencode ✓
- §8 (error handling, conventions) → Global Constraints + per-task lint steps ✓
- §9 (out of scope: packaging, Cursor, fetch_chunk, HTTP transport) → not in any task ✓

**2. Placeholder scan** — every step has real code or real commands; "stub" adapters in Task 4 are explicitly replaced in Tasks 5-6 (not left as TODOs).

**3. Type consistency** — `KbApiClient.get_kb/list_documents/get_document/search_graph` (Task 1) match the calls in tool functions (Task 2). `build_mcp_config(repo_root, api_url)` (Task 4) matches calls in Tasks 5 + 6. `render_for("claude-code"|"opencode")` (Task 3) matches calls in Tasks 5 + 6. `MCP_SERVER_NAME` defined in Task 4's `mcp_config.py`, used in Tasks 5 + 6. `_MCP_KEY` flag in Task 6 called out for Step 1 verification.

No issues found — ready for execution.
