# KB Platform

[English](README.md) | [简体中文](README.zh-CN.md)

A production-oriented knowledge base management platform built on Microsoft [GraphRAG](https://github.com/microsoft/graphrag). It combines a REST API, a React dashboard, an observable indexing pipeline, and an optional MCP server for AI agents.

Create and manage knowledge bases, ingest documents, inspect every chunk and pipeline step, retry failed work, track token cost, explore the entity graph, and query with six strategies: **local, global, drift, basic, cypher, and hybrid**.

- **Control plane:** SQLite for knowledge bases, documents, jobs, steps, units, retries, conversations, and provider profiles.
- **Data plane:** Parquet for GraphRAG artifacts, LanceDB for vectors, and optional Neo4j for Cypher/hybrid retrieval.
- **Process isolation:** the API server hosts REST endpoints and the built SPA; an independent worker runs indexing; the optional MCP server proxies agent requests to the API.
- **Observable by design:** unit-level progress, targeted retries, real citations, token usage, elapsed time, and cost breakdowns.

> **Project status:** active early-stage software (`0.1.0`). It is best suited to local or trusted-network deployments. Add authentication, authorization, and network isolation before exposing it publicly.

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration-provider-profiles--kb-content)
- [Creating and indexing a KB](#creating-a-kb--indexing)
- [Dashboard](#dashboard)
- [API](#api)
- [Indexing pipeline](#indexing-pipeline)
- [Query strategies](#query-strategies)
- [MCP server](#mcp-query-server-for-external-agents)
- [Development](#development)
- [Project structure](#project-structure)

---

## Requirements

- Python 3.11–3.13 + [`uv`](https://docs.astral.sh/uv/)
- Node 18+ (only to build the dashboard; not needed at runtime if you use a prebuilt `web/dist/`)
- An LLM provider key, entered in the dashboard (**Provider 配置** page) and stored **Fernet-encrypted in the DB**. No env-var keys. The encryption master key is auto-generated next to the DB (`.kb_secret_key`, chmod 600), or set via `KB_SECRET_KEY`.
- _(Optional)_ [Ollama](https://ollama.com) for local embeddings — needed if your LLM provider has no embedding model (e.g. DeepSeek).
- _(Optional)_ Neo4j 5.20+ for `cypher` and `hybrid` query methods; install backend support with `uv sync --extra neo4j`.

---

## Quick start

### 1. Backend (API server + worker)

```bash
# clone & install
git clone https://github.com/zhangxiaoyong9/graphrag-kb-platform.git kb-platform
cd kb-platform
uv sync                              # install Python dependencies

# create the SQLite database (once)
uv run alembic upgrade head
```

**One-command startup (Linux / macOS):**

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

**One-command startup (Windows PowerShell / CMD):**

```powershell
.\scripts\start.ps1
# or from Command Prompt:
.\scripts\start.cmd
```

The scripts install missing dependencies, build `web/dist` when needed, apply database migrations, and supervise both the API server and worker. Press `Ctrl+C` to stop both processes.

Common options:

```bash
./scripts/start.sh --db kb.db --data-root ./data --host 127.0.0.1 --port 8000
./scripts/start.sh --skip-install --skip-build
```

```powershell
.\scripts\start.ps1 -DbPath kb.db -DataRoot .\data -HostAddress 127.0.0.1 -Port 8000
.\scripts\start.ps1 -SkipInstall -SkipBuild
```

Equivalent environment variables are `KB_DB_PATH`, `KB_DATA_ROOT`, `KB_HOST`, and `KB_PORT`.

**Manual startup:** run **two** processes in separate terminals:

```bash
# Terminal 1 — API server: REST endpoints + hosts the built SPA
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000

# Terminal 2 — background worker: polls SQLite → runs indexing jobs
uv run python -m kb_platform.worker kb.db
```

Provider keys are entered in the dashboard (Provider 配置 page) and read from the DB at run time — no key env vars are needed for the server or worker.

Server CLI: `python -m kb_platform.server [db_path] [data_root] [host] [port]` (defaults `kb.db . 127.0.0.1 8000`). The `data_root` holds the parquet index output + `<data_root>/vectors/` LanceDB tables.

> **Proxy gotcha:** if your machine sets `all_proxy`/`http_proxy`/`https_proxy` (e.g. Surge, Clash), litellm will route **localhost** calls (Ollama embeddings) through the proxy and fail. Run the server/worker with the proxy unset for localhost — `env -u all_proxy -u http_proxy -u https_proxy ... python -m kb_platform.server ...` — or set `NO_PROXY=localhost,127.0.0.1`.

### 2. Frontend (React dashboard)

**Production (recommended):** the API server hosts the prebuilt SPA from `web/dist/`. Build it once, then just use the server — no separate frontend process.

```bash
cd web
npm install
npm run build        # outputs web/dist/ (tsc -b && vite build)
```

Open **`http://127.0.0.1:8000`** — that's it. Re-run `npm run build` after frontend upgrades; the server picks up the new `dist/` on next start.

**Development (hot reload):** run the Vite dev server with the backend running on :8000 (it proxies `/kbs`, `/jobs`, `/steps`, `/units`, `/health` to the backend):

```bash
cd web && npm install && npm run dev      # http://localhost:5173
```

### 3. (Optional) Local embeddings via Ollama

DeepSeek (and other chat-only providers) have no embedding model, so `local`/`basic`/`drift` queries need a separate embedder. Run one locally with Ollama:

```bash
ollama pull nomic-embed-text
ollama serve                                # http://localhost:11434
```

Then create an **embedding provider profile** on the Provider 配置 page (provider `ollama`, model `nomic-embed-text`, api_base `http://localhost:11434`, any placeholder key — litellm ignores it for Ollama) and select it when creating the KB. Example via API:

```json
POST /provider-profiles   →  { "id": 2, ... }
{
  "name": "Ollama", "kind": "embedding", "provider": "ollama",
  "model": "nomic-embed-text", "api_base": "http://localhost:11434",
  "api_keys": ["ollama"]
}

POST /kbs
{
  "name": "my-kb",
  "llm_profile_id": 1,
  "embedding_profile_id": 2
}
```

---

## Configuration (provider profiles + KB content)

Connection + key info lives in **named provider profiles** (global, reusable); a KB references one LLM profile (+ optional embedding profile) and keeps only content/quality knobs. This stops re-typing provider/model/api_base/key on every KB.

- **Provider profile** (Provider 配置 page, or `POST /provider-profiles`): `kind` (`llm` \| `embedding`), `provider`, `model`, `api_base`, `api_version` (Azure), `structured_output` (llm only — whether `community_reports` use json_schema), and a write-only `api_keys` list (Fernet-encrypted at rest; the list endpoint returns only `api_keys_count`, never plaintext). Multiple keys are round-robin load-balanced.
- **KB** (`POST /kbs` / `PATCH /kbs/{id}`): `llm_profile_id` (required), `embedding_profile_id` (optional — omit for `global`-only KBs), and a content-only `settings_yaml` (chunking / extract_graph / summarize_descriptions / cluster_graph / `community_reports.max_length` / prompts / query_prompts / concurrency). `structured_output` follows the selected LLM profile, not the KB.

### Provider profile fields

| Field | Meaning | Example |
|-------|---------|---------|
| `kind` | `llm` or `embedding` | `llm` |
| `provider` | Provider id | `deepseek`, `openai`, `azure`, `ollama` |
| `model` | Model id | `deepseek-chat`, `gpt-4o-mini`, `nomic-embed-text` |
| `api_base` | Custom endpoint (relay / Azure / self-host) | `https://api.deepseek.com`, `http://localhost:11434` |
| `api_version` | Azure API version (Azure only) | `2024-06-01` |
| `structured_output` | Use json_schema for community reports (llm only) | `true` (`false` for DeepSeek) |
| `api_keys` | One or more keys (write-only; round-robin) | `["sk-..."]` |

### Key handling & security

- Keys are **always in the DB**, Fernet-encrypted. The env-var key path (`api_key_env` / `{PROVIDER}_API_KEY`) is removed.
- Master key: env `KB_SECRET_KEY` if set, else auto-generated at `<dirname(db)>/.kb_secret_key` (chmod 600). Harden by pointing `KB_SECRET_KEY` at a value stored off-disk.
- `GET /provider-profiles` returns `api_keys_count` only — plaintext never leaves the write path.

### Examples

Create an LLM profile (DeepSeek; plain-text reports because DeepSeek lacks json_schema):
```json
POST /provider-profiles   →  { "id": 1, ... }
{
  "name": "DeepSeek", "kind": "llm", "provider": "deepseek",
  "model": "deepseek-chat", "api_base": "https://api.deepseek.com",
  "api_keys": ["sk-..."], "structured_output": false
}
```

Azure OpenAI (needs `api_base` + `api_version`):
```json
POST /provider-profiles
{
  "name": "Azure", "kind": "llm", "provider": "azure",
  "model": "my-deployment", "api_base": "https://my-resource.openai.azure.com",
  "api_version": "2024-06-01", "api_keys": ["..."], "structured_output": true
}
```

Then create a KB referencing it (content knobs are optional; defaults apply):
```json
POST /kbs
{ "name": "my-kb", "method": "standard", "llm_profile_id": 1,
  "settings_yaml": "{\"chunking\":{\"size\":1200},\"community_reports\":{\"max_length\":2000}}" }
```

### Migration of existing KBs

Alembic `0005` auto-migrates legacy KBs: each KB's old `llm`/`embedding` block becomes a (deduped) provider profile, the KB is repointed at it, and connection/`structured_output` is stripped from `settings_json`. **Migrated profiles start with empty keys** — re-enter keys on the Provider 配置 page before that KB can index or query.

---

## Creating a KB + indexing

```bash
# 1. create an LLM provider profile (keys encrypted in DB) — once per provider
curl -X POST http://127.0.0.1:8000/provider-profiles -H 'Content-Type: application/json' \
  -d '{"name":"DeepSeek","kind":"llm","provider":"deepseek","model":"deepseek-chat","api_keys":["sk-..."],"structured_output":false}'
# 2. create a KB referencing that profile (+ optional embedding_profile_id)
curl -X POST http://127.0.0.1:8000/kbs -H 'Content-Type: application/json' \
  -d '{"name":"my-kb","method":"standard","llm_profile_id":1,"settings_yaml":"{...content only...}"}'
curl -X POST http://127.0.0.1:8000/kbs/1/documents -H 'Content-Type: application/json' \
  -d '{"title":"intro","text":"..."}'           # or multipart file upload
curl -X POST http://127.0.0.1:8000/kbs/1/jobs -H 'Content-Type: application/json' \
  -d '{"type":"full"}'                          # "full" or "incremental"
```

Or just use the dashboard at `http://127.0.0.1:8000`.

## Dashboard

Grouped SaaS-style sidebar (工作台 / 知识库 / 检索与问答 / 分析与监控 / 系统管理). Key surfaces:

| Page | What you can do |
|------|-----------------|
| Overview | KB count, recent jobs, system health |
| KB management / Documents / Graph | Create KBs; cross-KB document center; cross-KB graph explorer |
| Retrieval test / Chat | Pick KB + method (local/global/drift/basic), see **answer + real sources + token usage + server elapsed** |
| Analytics / Jobs / Cost | Aggregated stats; every job across KBs; cost by step/model/job |
| KB detail | Document manager (upload/paste/list/delete), document detail browsing with source evidence drawer, trigger full/incremental, cumulative **cost**, **export** (zip/GraphML), interactive **graph**, entity/relation browser, jobs, query; **model-config card** shows the KB's LLM/embedding settings |
| Job detail | Step timeline + per-step progress + unit table + per-unit/step **retry** + per-step cost |
| System status / Settings / API Keys / Provider 配置 | Health + API reference; read-only config guidance; API-key placeholder; **provider profiles** (create/edit/delete LLM + embedding profiles, encrypted API keys) |

Graph viz uses [react-force-graph-2d](https://github.com/vasturiano/react-force-graph-2d); cost bars are pure CSS.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness: DB ping + worker heartbeat staleness |
| `GET` | `/provider-profiles?kind=llm\|embedding` | List profiles (`api_keys_count` only — never plaintext) |
| `POST` | `/provider-profiles` | Create profile (encrypts `api_keys`) |
| `PATCH` | `/provider-profiles/{id}` | Update profile (`api_keys` write-only: omit=keep, `[]`=clear) |
| `DELETE` | `/provider-profiles/{id}` | Delete profile — **409** with referencing-KB list if in use |
| `POST` | `/kbs` | Create a KB referencing `llm_profile_id` (+ optional `embedding_profile_id`) |
| `GET` | `/kbs` | List KBs |
| `GET` | `/kbs/{id}` | KB detail (content `settings` + resolved `llm_profile` / `embedding_profile`) |
| `POST` | `/kbs/{id}/documents` | Add doc — JSON `{title,text}` **or** multipart file (via [markitdown](https://github.com/microsoft/markitdown): `.txt/.md/.pdf/.docx/.html`…) |
| `GET` | `/kbs/{id}/documents` | List docs (`bytes` + `chunk_count`) |
| `GET` | `/kbs/{id}/documents/{doc_id}` | Document detail with stored text and chunk-backed citations |
| `GET` | `/kbs/{id}/documents/{doc_id}/citations/{citation_id}/evidence` | Evidence detail for one citation: matched chunk plus before/after context |
| `DELETE` | `/kbs/{id}/documents/{doc_id}` | Delete doc + chunks (**graph not shrunk** — re-run incremental) |
| `POST` | `/kbs/{id}/jobs` | Trigger job (`type: "full"` / `"incremental"`) |
| `GET` | `/kbs/{id}/jobs` | List jobs |
| `GET` | `/kbs/{id}/cost` | Cumulative cost by step/model/job |
| `GET` | `/kbs/{id}/jobs/{jid}/cost` | One job's cost by step/model |
| `GET` | `/kbs/{id}/export?format=zip\|graphml` | Download index (zip of parquet+GraphML, or GraphML) |
| `GET` | `/kbs/{id}/graph?limit=&q=&hop=` | Graph viz data (Top-N by degree, or search neighborhood, community coloring) |
| `GET` | `/jobs/{id}` | Job status |
| `GET` | `/jobs/{id}/steps` | Step timeline + per-step progress |
| `GET` | `/steps/{id}/units` | Unit table for a step |
| `POST` | `/steps/{id}/retry` | Retry all failed units in a step |
| `POST` | `/units/{id}/retry` | Retry a single failed unit |
| `POST` | `/kbs/{id}/query` | Query → `{answer, method, error, elapsed_ms, prompt_tokens, output_tokens, llm_calls, sources}` |
| `POST` | `/kbs/{kb_id}/conversations` | Create a conversation bound to a KB |
| `GET` | `/kbs/{kb_id}/conversations` | List conversations (id, title, snippet) |
| `GET` | `/conversations/{id}` | Conversation + ordered messages |
| `PATCH` | `/conversations/{id}` | Rename |
| `DELETE` | `/conversations/{id}` | Delete + cascade messages |
| `POST` | `/conversations/{id}/messages` | Multi-turn send: rewrite follow-up → search → persist; returns the assistant message |

Upload cap 25 MiB (`KB_MAX_UPLOAD_BYTES`). Cost is captured per LLM call via graphrag-llm's model-cost registry; unknown models contribute tokens but no USD (never fails a unit).

## Indexing pipeline

Full (`type: "full"`):

```
chunk_documents → extract_graph → summarize_descriptions → finalize_graph →
create_communities → community_reports → generate_text_embeddings
```

Incremental (`type: "incremental"`) re-runs only what changed: delta-filtered extract (only new chunks' LLM), merge-delta (re-merges ALL cached on-disk extractions, zero LLM), then **delta-scoped** summarize and community_reports — only changed entities/communities are re-LLM'd (the rest carry over via on-disk summaries / a `reports_by_hash` sidecar; Leiden reassigns community ids each run). Old documents are never re-parsed.

Every LLM step is tracked at the chunk/entity/community level (unit): `pending → running → succeeded/failed`; failed units can be retried individually or per-step.

**DeepSeek community reports:** set `structured_output: false` on the KB's **LLM provider profile** → plain-text completion + lenient JSON parse (DeepSeek rejects `response_format: json_schema`). Default `true` (graphrag structured output, for OpenAI/GPT-4o). `structured_output` follows the LLM profile, not the KB.

## Query strategies

| Method | Community reports | Embeddings | Neo4j | Best for |
|--------|-------------------|------------|--------|----------|
| `local` | No | Entity | No | Entity-grounded questions with nearby graph context |
| `global` | Yes | No | No | Corpus-wide themes and summaries |
| `drift` | Yes | Yes | No | Focused exploration that expands from dense retrieval |
| `basic` | No | Text unit | No | Fast, straightforward semantic search |
| `cypher` | No | No | Yes | Explicit graph traversal and auditable generated Cypher |
| `hybrid` | No | Yes | Yes | Vector retrieval combined with multi-hop graph context |

The query endpoint resolves the LLM from the KB's **LLM provider profile** and the embedder from its **embedding provider profile**. Ollama therefore works for vector-based methods even when the chat provider has no embedding model. Responses include real server-side elapsed time, token usage, citations, and—where applicable—generated Cypher and truncation metadata.

> `global` and `drift` require community reports. `cypher` and `hybrid` require the optional Neo4j integration and a synchronized graph snapshot.

### Multi-turn chat

Conversations (`/kbs/{kb_id}/conversations` + `/conversations/{id}/messages`) are a layer **above** the single-shot `POST /kbs/{id}/query`: each follow-up is rewritten into a standalone query against the last ~6 messages by an injected `complete` callable (the same provider-profile resolution as indexing), then answered by the unchanged `QueryEngine.search`, and the user + assistant messages (merged tokens + sources) are persisted in SQLite. The first turn passes through unmodified. `POST /kbs/{id}/query`, the MCP `query_knowledge_base` tool, and the query-test flow are unchanged.

## MCP query server (for external agents)

The platform ships a **[MCP](https://modelcontextprotocol.io) (Model Context Protocol) server** that exposes knowledge-base search as standard MCP tools, so AI agents like Claude Code / Claude Desktop / Cursor can query directly without crafting HTTP.

It is a **third (optional) process**: `python -m kb_platform.mcp`, running over **stdio** as a **thin HTTP proxy** to the running API server — it reimplements no query logic, reusing the same provider-profile resolution / engine building.

**Install (optional extra):**

```bash
uv sync --extra mcp
```

**Start (the API server must already be running):**

```bash
uv run python -m kb_platform.mcp --api-url http://127.0.0.1:8000
# or via env: KB_API_URL=http://127.0.0.1:8000 uv run python -m kb_platform.mcp
```

**Exposed tools:**

| Tool | Purpose |
|------|---------|
| `list_knowledge_bases` | Discover available KBs and their IDs |
| `get_kb_details(kb_id)` | Check index statistics and available query methods |
| `query_knowledge_base(kb_id, query, method?)` | Query a KB and return an answer with cited sources |
| `list_documents(kb_id)` | Browse the documents contained in a KB |
| `get_document(kb_id, doc_id)` | Retrieve full document text and chunk snippets for verification |
| `search_graph(kb_id, q, hop?, limit?)` | Inspect an entity's multi-hop graph neighborhood |

The MCP query tool currently exposes the four GraphRAG methods (`local`, `global`, `drift`, and `basic`). Use the REST API or dashboard for Neo4j-backed `cypher` and `hybrid` queries.

**Wire into Claude Desktop / Claude Code** (edit your MCP client config):

```json
{
  "mcpServers": {
    "kb-platform": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/graphrag-kb-platform",
               "python", "-m", "kb_platform.mcp"],
      "env": { "KB_API_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

> The MCP server is a peer of the API server and carries no auth of its own; isolate it at the network layer (same as a local Ollama). HTTP transport for remote agents can be added later. Implementation: `kb_platform/mcp/`.

## Logging and troubleshooting

Server, worker, and MCP logs are written to stderr and `logs/<process>.log`.
Every line includes `service=...`; request/query/job/step/unit/LLM-call IDs are
added when available. LLM and embedding logs record safe metadata, latency,
tokens, retry/failover decisions, HTTP error excerpts, embedding batch progress,
and vector dimensions—never prompts, document text, vectors, or API keys.

```bash
# Inspect one embedding call
rg 'llm_call=emb-|embedding\.' logs/worker.log

# Temporarily increase LLM detail
KB_LOG_LEVELS=kb_platform.llm=DEBUG uv run python -m kb_platform.worker kb.db
```

Embedding batches retry 429, 5xx, timeout, and transport errors up to three
times by default; configure this with `KB_EMBED_MAX_ATTEMPTS`. See
[Logging and observability](docs/logging-observability.md) for the event catalog,
correlation model, module audit, redaction behavior, and all environment variables.

For `unit_fanout` steps, the worker re-emits up to ten persisted
`unit.failure_summary` events even when a recovered run does not raise those old
exceptions again. Each event includes the unit ID, subject hash, attempt, error
type, and redacted reason; terminal step/job logs identify the failing step and
aggregate reason.

## Development

```bash
uv sync                          # install all deps (incl. dev)
uv run alembic upgrade head      # create/update DB schema
uv run pytest                    # backend tests
uv run ruff check .              # lint
cd web && npm install && npm run build && npm test   # frontend build + vitest
```

**E2E (Playwright, optional):** first install Chromium once — `cd web && npm run e2e:install`. Then `npm run e2e` builds the SPA and runs the suite against a no-LLM fake server (`FakeGraphAdapter` worker + injected `FakeQueryEngine`); no provider key required. The fake server can also be run standalone for debugging: `npm run e2e:server` (serves `http://127.0.0.1:18000`).

Tests use `FakeGraphAdapter` (deterministic, no LLM), `FakeVectorStore` (in-memory), `FakeQueryEngine`. Real-LLM integration tests need a provider profile with a real key entered on the Provider 配置 page.

## Project structure

```
kb_platform/
  api/            FastAPI app, routes (kbs/jobs/query/health/cost/export/graph), models
  db/             SQLAlchemy models, repository, engine helpers
  engine/         Indexing orchestrator, atomic steps, unit worker, strategies (incl. delta)
  graph/          GraphAdapter seam, vector store, GraphML writer, cost capture, embed_items
  input/          Document readers (markitdown)
  query/          QueryEngine seam (Fake + GraphRagQueryEngine + context source extraction)
  reconsolidate/  Post-incremental extraction re-merge
  worker.py       Background indexing worker (SQLite-as-queue, graceful shutdown)
  server.py       HTTP API server entry point (loop="asyncio")
web/              React + TypeScript + Vite + Tailwind SPA
tests/            Backend tests (unit + integration; pytest)
docs/             Design specs, plans, verification records, screenshots
alembic/          Database migrations
```
