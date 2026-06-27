# KB Platform

Knowledge base management platform built on top of Microsoft [GraphRAG](https://github.com/microsoft/graphrag). Provides a REST API + a React dashboard for creating knowledge bases, indexing documents, tracking every chunk and pipeline step, and querying the graph with local / global / drift / basic search.

- **Control plane:** SQLite (jobs / steps / units / documents / retries).
- **Data plane:** parquet (entities / relationships / communities / reports / text units) + LanceDB vectors.
- **Two processes:** an HTTP API server (also hosts the built SPA) + an independent background worker that runs indexing. The server never runs indexing; the worker never serves HTTP.

---

## Requirements

- Python 3.11–3.13 + [`uv`](https://docs.astral.sh/uv/)
- Node 18+ (only to build the dashboard; not needed at runtime if you use a prebuilt `web/dist/`)
- An LLM provider key in the environment (e.g. `DEEPSEEK_API_KEY` or `OPENAI_API_KEY`). Keys are **never stored** — resolved from `llm.api_key_env` → `{PROVIDER}_API_KEY` env → explicit `api_key`.
- _(Optional)_ [Ollama](https://ollama.com) for local embeddings — needed if your LLM provider has no embedding model (e.g. DeepSeek).

---

## Deployment

### 1. Backend (API server + worker)

```bash
# clone & install
git clone https://github.com/zhangxiaoyong9/graphrag-kb-platform.git kb-platform
cd kb-platform
uv sync                              # install Python dependencies

# create the SQLite database (once)
uv run alembic upgrade head
```

Run **two** processes (separate terminals):

```bash
# Terminal 1 — API server: REST endpoints + hosts the built SPA
export DEEPSEEK_API_KEY=sk-...       # your LLM provider key
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000

# Terminal 2 — background worker: polls SQLite → runs indexing jobs
export DEEPSEEK_API_KEY=sk-...       # same key (worker makes the LLM calls)
uv run python -m kb_platform.worker kb.db
```

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

Then include an `embedding` block when creating the KB (the placeholder `api_key` satisfies graphrag-llm's validator; litellm ignores it for Ollama):

```json
POST /kbs
{
  "name": "my-kb",
  "settings_yaml": "{
    \"llm\": {\"model_provider\":\"deepseek\",\"model\":\"deepseek-chat\",\"api_key_env\":\"DEEPSEEK_API_KEY\"},
    \"embedding\": {\"model_provider\":\"ollama\",\"model\":\"nomic-embed-text\",\"api_base\":\"http://localhost:11434\",\"api_key\":\"ollama\"},
    \"community_reports\": {\"structured_output\": false}
  }"
}
```

---

## Configuration (model / api_base / key)

All LLM/embedding settings live in the KB's `settings_yaml` (passed at creation). The **key** is special: it's resolved from the environment at call time and **never stored**, so you change it by changing the env var (restart the worker/server) — no KB change needed. `model` and `api_base`, however, are persisted in the KB's settings, and there is **no settings-update endpoint yet**, so changing them for an existing KB means creating a new KB (re-add docs + re-index).

### `llm` fields

| Field | Meaning | Example |
|-------|---------|---------|
| `model_provider` | Provider id | `deepseek`, `openai`, `azure`, `ollama` |
| `model` | Model id | `deepseek-chat`, `gpt-4o-mini` |
| `api_base` | Custom endpoint (relay / Azure / self-host) | `https://api.deepseek.com`, `http://localhost:11434` |
| `api_key_env` | Name of the env var holding the key (**recommended**) | `DEEPSEEK_API_KEY` |
| `api_key` | Literal key (**not recommended** — stored in DB) | `sk-...` |
| `api_version` | Azure API version (Azure only) | `2024-06-01` |

Credential resolution at call time: `llm.api_key` → env var named by `llm.api_key_env` → `{PROVIDER}_API_KEY` env. Prefer `api_key_env` so secrets stay out of the DB.

### How to change each

- **Key** — `export DEEPSEEK_API_KEY=sk-new` (or the matching `{PROVIDER}_API_KEY`), then restart worker + server. Takes effect on the next job; no KB change.
- **Model / api_base** — pass new values in `settings_yaml` when creating a KB. Changing an existing KB requires creating a new one (re-add documents + re-index). `embedding.model` / `embedding.api_base` work the same way under the `embedding` block.
- Per-KB key override — point `llm.api_key_env` at a different env var name if a KB should use a different key than the provider default.

### Examples (the object goes stringified into `settings_yaml`)

DeepSeek, official endpoint:
```json
{"llm":{"model_provider":"deepseek","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"}}
```

OpenAI via a relay / custom base:
```json
{"llm":{"model_provider":"openai","model":"gpt-4o-mini","api_base":"https://your-relay.example.com/v1","api_key_env":"OPENAI_API_KEY"}}
```

Azure OpenAI (needs `api_base` + `api_version`):
```json
{"llm":{"model_provider":"azure","model":"my-deployment","api_base":"https://my-resource.openai.azure.com","api_version":"2024-06-01","api_key_env":"AZURE_OPENAI_API_KEY"}}
```

DeepSeek LLM + Ollama embeddings + plain-text reports (full combo):
```json
{"llm":{"model_provider":"deepseek","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"},"embedding":{"model_provider":"ollama","model":"nomic-embed-text","api_base":"http://localhost:11434","api_key":"ollama"},"community_reports":{"structured_output":false}}
```

---

## Creating a KB + indexing

```bash
curl -X POST http://127.0.0.1:8000/kbs -H 'Content-Type: application/json' \
  -d '{"name":"my-kb","method":"standard","settings_yaml":"{...see above...}"}'
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
| System status / Settings / API Keys | Health + API reference; read-only config guidance; API-key placeholder |

Graph viz uses [react-force-graph-2d](https://github.com/vasturiano/react-force-graph-2d); cost bars are pure CSS.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness: DB ping + worker heartbeat staleness |
| `POST` | `/kbs` | Create a KB |
| `GET` | `/kbs` | List KBs |
| `GET` | `/kbs/{id}` | KB detail (incl. redacted `settings`) |
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

Upload cap 25 MiB (`KB_MAX_UPLOAD_BYTES`). Cost is captured per LLM call via graphrag-llm's model-cost registry; unknown models contribute tokens but no USD (never fails a unit).

## Indexing pipeline

Full (`type: "full"`):

```
chunk_documents → extract_graph → summarize_descriptions → finalize_graph →
create_communities → community_reports → generate_text_embeddings
```

Incremental (`type: "incremental"`) re-runs only what changed: delta-filtered extract (only new chunks' LLM), merge-delta (re-merges ALL cached on-disk extractions, zero LLM), then **delta-scoped** summarize and community_reports — only changed entities/communities are re-LLM'd (the rest carry over via on-disk summaries / a `reports_by_hash` sidecar; Leiden reassigns community ids each run). Old documents are never re-parsed.

Every LLM step is tracked at the chunk/entity/community level (unit): `pending → running → succeeded/failed`; failed units can be retried individually or per-step.

**DeepSeek community reports:** set `community_reports.structured_output: false` in KB settings → plain-text completion + lenient JSON parse (DeepSeek rejects `response_format: json_schema`). Default `true` (graphrag structured output, for OpenAI/GPT-4o).

## Query

| Method | Needs community reports | Needs embeddings | Description |
|--------|------------------------|------------------|-------------|
| `local` | no | yes (entity) | Entity-grounded retrieval + community summaries |
| `global` | yes | no | Map-reduce over community reports |
| `drift` | yes | yes | Dense retrieval focused search |
| `basic` | no | yes (text-unit) | Text-unit vector search (simplest, fastest) |

The query endpoint resolves the LLM from the KB `llm` settings and the embedder from `embedding` (so Ollama works for the vector methods). The response carries real server-side `elapsed_ms`, token usage, and extracted source entities / text snippets.

## Development

```bash
uv sync                          # install all deps (incl. dev)
uv run alembic upgrade head      # create/update DB schema
uv run pytest                    # backend tests
uv run ruff check .              # lint
cd web && npm install && npm run build && npm test   # frontend build + vitest
```

Tests use `FakeGraphAdapter` (deterministic, no LLM), `FakeVectorStore` (in-memory), `FakeQueryEngine`. Real-LLM integration tests need a provider key in the environment.

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
