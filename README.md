# KB Platform

Knowledge base management platform built on top of Microsoft [GraphRAG](https://github.com/microsoft/graphrag). Provides a REST API + React dashboard for creating knowledge bases, indexing documents, tracking every chunk and pipeline step, and querying the resulting graph with local / global / drift / basic search.

## Quick start

```bash
# clone & install
git clone https://github.com/zhangxiaoyong9/graphrag-kb-platform.git kb-platform
cd kb-platform
uv sync

# create the database (once)
uv run alembic upgrade head

# start the API server
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000

# in another terminal, start the background worker
uv run python -m kb_platform.worker kb.db
```

The dashboard is served at `http://127.0.0.1:8000` (Vite SPA; API routes take priority over the catch-all).

To use real LLM indexing + query, set your provider key in the environment (e.g. `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`) and supply a `settings_yaml` when creating a KB:

```json
POST /kbs {
  "name": "my-kb",
  "settings_yaml": "{\"llm\":{\"model_provider\":\"deepseek\",\"model\":\"deepseek-chat\"}}"
}
```

The adapter resolves credentials from `llm.api_key_env` → `{PROVIDER}_API_KEY` env var → explicit `api_key` arg — no key is ever stored in the database.

## Architecture

**Control plane (SQLite)** — tracks jobs, steps, units, documents, and retries.

**Data plane (parquet + LanceDB)** — the knowledge graph output (entities, relationships, communities, community reports, text units) plus embeddings under `<data_root>/vectors/`.

**Worker** — polls SQLite for pending jobs, runs the indexing engine (chunk → extract → summarize → finalize → cluster → community reports → embed). Per-job exception isolation; crash recovery resets stale units; graceful SIGTERM/SIGINT shutdown finishes the in-flight unit then exits. The API server never runs indexing directly.

**Server** — FastAPI REST API + serves the built React SPA under `/assets` with a history fallback catch-all (API routes registered first → always win).

### Process boundaries

| Process | Entry point | What it does |
|---------|------------|--------------|
| API server | `python -m kb_platform.server` | REST endpoints + SPA hosting |
| Worker | `python -m kb_platform.worker` | Polls SQLite → runs/indexing jobs |

## Frontend

Open `http://127.0.0.1:8000` after starting the server — no extra config needed.

| Page | What you can do |
|------|----------------|
| KB list | Create / browse knowledge bases |
| KB detail | Document manager (file upload / paste / list / delete), trigger full / incremental indexing, cumulative **cost**, **export** (zip / GraphML), interactive **graph** viz, jobs, query box |
| Job detail | Step timeline + per-step progress bars + unit table + per-step **cost** bars |
| Retry | Single-unit retry + batch retry all failed units in a step |
| Query | Pick search method (local / global / drift / basic) → ask a question → see answer |

The graph view uses [react-force-graph-2d](https://github.com/vasturiano/react-force-graph-2d): nodes are entities (sized/colored by degree/community), with a search box to focus a neighborhood. Cost bars are pure CSS (no chart library).

Stack: React 18 + TypeScript + Vite + Tailwind CSS. The built SPA lives in `web/dist/`; the API server hosts it automatically (`/assets` static files + history fallback; API routes are registered first and always win).

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness: DB ping + worker heartbeat staleness |
| `POST` | `/kbs` | Create a knowledge base |
| `GET` | `/kbs` | List all KBs |
| `GET` | `/kbs/{id}` | Get a single KB |
| `POST` | `/kbs/{id}/documents` | Add a document — JSON `{title, text}` **or** multipart file upload (parsed via [markitdown](https://github.com/microsoft/markitdown): `.txt`, `.md`, `.pdf`, `.docx`, `.html`, …) |
| `GET` | `/kbs/{id}/documents` | List documents (with `bytes` + `chunk_count`) |
| `DELETE` | `/kbs/{id}/documents/{doc_id}` | Delete a document + its chunks (**graph is not shrunk** — re-run incremental to refresh) |
| `POST` | `/kbs/{id}/jobs` | Trigger a job (`type: "full"` or `"incremental"`) |
| `GET` | `/kbs/{id}/jobs` | List jobs for a KB |
| `GET` | `/kbs/{id}/cost` | Cumulative cost by step / model / job |
| `GET` | `/kbs/{id}/jobs/{jid}/cost` | One job's cost by step / model |
| `GET` | `/kbs/{id}/export?format=zip\|graphml` | Download the index (zip of parquet + GraphML, or standalone GraphML) |
| `GET` | `/kbs/{id}/graph?limit=&q=&hop=` | Graph viz data (Top-N entities by degree, or a search neighborhood, with community coloring) |
| `GET` | `/jobs/{id}` | Get job status |
| `GET` | `/jobs/{id}/steps` | Get step timeline with per-step progress |
| `GET` | `/steps/{id}/units` | Get unit table for a step |
| `POST` | `/steps/{id}/retry` | Retry all failed units in a step |
| `POST` | `/units/{id}/retry` | Retry a single failed unit |
| `POST` | `/kbs/{id}/query` | Query (`method: "local"` / `"global"` / `"drift"` / `"basic"`) |

Upload size is capped at 25 MiB by default (`KB_MAX_UPLOAD_BYTES` env). Cost is captured per LLM call via graphrag-llm's model-cost registry; unknown models contribute tokens but no USD (never fails a unit).

## Indexing pipeline

Full indexing (`type: "full"`):

```
chunk_documents → extract_graph → summarize_descriptions → finalize_graph →
create_communities → community_reports → generate_text_embeddings
```

Incremental indexing (`type: "incremental"`) re-runs only what changed: delta-filtered extract (only new chunks' LLM), merge-delta (re-merges ALL cached on-disk extractions, zero LLM), then **delta-scoped** summarize and community_reports — only entities whose descriptions changed and communities whose context changed are re-LLM'd (the rest carry over via on-disk summaries / a `reports_by_hash` sidecar, since Leiden reassigns community ids each run). Old documents are never re-parsed.

Every LLM step is tracked at the chunk/entity/community level (unit) with `pending → running → succeeded/failed` status; failed units can be retried individually or as a step.

**DeepSeek community reports:** set `community_reports.structured_output: false` in the KB settings to generate reports via a plain-text completion + lenient JSON parse (DeepSeek rejects `response_format: json_schema`; the plain path works around it). Default is `true` (graphrag's structured output, for OpenAI/GPT-4o).

## Query

All four graphrag search methods are supported:

| Method | Needs community reports | Description |
|--------|------------------------|-------------|
| `local` | no | Entity-grounded retrieval with community summaries |
| `global` | yes | Map-reduce over community reports |
| `drift` | yes | Dense retrieval focused search |
| `basic` | no | Text-unit-level vector search (simplest, fastest) |

`global` and `drift` require community reports. DeepSeek rejects `response_format: json_schema`, so for those methods either use a json-schema-capable model (e.g. GPT-4o), or set the KB's `community_reports.structured_output: false` to use the plain-text report fallback (see [Indexing pipeline](#indexing-pipeline)).

## Development

```bash
uv sync                          # install all deps (including dev)
uv run alembic upgrade head      # create/update the database schema
uv run pytest                    # run all backend tests (160)
uv run ruff check .              # lint
uv run ruff format --check .     # format check

# frontend
cd web && npm install && npm run build && npm test   # 19 vitest tests
```

Tests use `FakeGraphAdapter` (deterministic, no LLM), `FakeVectorStore` (in-memory), and `FakeQueryEngine`. Real-LLM integration tests need a provider key in the environment.

### Requirements

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) for dependency management
- Node 18+ (for the React dashboard)

## Project structure

```
kb_platform/
  api/            FastAPI app, routes (kbs/jobs/query/health/cost/export/graph), models
  db/             SQLAlchemy models, repository, engine helpers
  engine/         Indexing orchestrator, atomic steps, unit worker, strategies (incl. delta)
  graph/          GraphAdapter seam, vector_store, GraphML writer, cost capture (completion wrapper)
  input/          Document readers (markitdown)
  query/          QueryEngine seam (Fake + GraphRagQueryEngine)
  reconsolidate/  Post-incremental extraction re-merge
  worker.py       Background indexing worker (SQLite-as-queue, graceful shutdown)
  server.py       HTTP API server entry point
web/              React + TypeScript + Vite + Tailwind SPA (DocumentManager, GraphView, CostPanel, …)
tests/            Backend tests (unit + integration; pytest)
docs/             Design specs and implementation plans
alembic/          Database migrations
```
