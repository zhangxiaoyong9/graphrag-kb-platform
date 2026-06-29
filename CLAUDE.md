# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

KB Platform is a control plane on top of Microsoft GraphRAG: a FastAPI service + React dashboard for creating knowledge bases, indexing documents with per-chunk/per-community tracking, and querying via local/global/drift/basic search.

## Commands

Backend (Python, `uv`):

```bash
uv sync                            # install deps (add [dev] extra for pytest/ruff/httpx)
uv run alembic upgrade head        # create/apply SQLite schema (run once, after model changes)
uv run ruff check .                # lint (line-length 100, target py311)
uv run pytest                      # all backend tests
uv run pytest tests/test_unit_worker.py               # one file
uv run pytest tests/test_unit_worker.py::test_name    # one test
```

Run the **two** processes in separate terminals (there is no single entry point):

```bash
uv run python -m kb_platform.server [db_path] [data_root] [host] [port]   # defaults: kb.db . 127.0.0.1 8000
uv run python -m kb_platform.worker [db_path]                             # polls SQLite → runs indexing jobs
```

MCP query server (optional, opt-in extra; exposes search to external agents):

```bash
uv sync --extra mcp                                                        # install the `mcp` SDK
uv run python -m kb_platform.mcp [--api-url URL]                           # stdio MCP server, thin HTTP proxy to the API server
```

Frontend (React + TS + Vite + Tailwind, in `web/`):

```bash
cd web && npm install
npm run build          # tsc -b && vite build → web/dist (served by the API server in production)
npm run dev            # Vite dev server on :5173, proxies /kbs /jobs /steps /units /health to :8000
npm test               # vitest run (component + lib tests)
npm run test:watch
```

E2E (Playwright, optional, no LLM/key needed):

```bash
cd web
npm run e2e:install    # one-time: playwright install chromium
npm run e2e            # builds SPA + runs suite against a FakeGraphAdapter fake server on :18000
npm run e2e:server     # standalone fake server (scripts/e2e_server.py) for debugging
```

## Architecture

### Two processes, SQLite as the queue
The **API server** (`server.py` → `api/app.py`) only serves HTTP + hosts the built SPA; it never runs indexing. The **worker** (`worker.py`) only runs indexing; it never serves HTTP. They communicate exclusively through the SQLite control plane (`claim_one_pending_job`, unit status rows). `run_worker` installs SIGTERM/SIGINT handlers, finishes the in-flight job, then exits; hard kills are recovered on next start (`recover_stale_jobs` / `recover_stale_units` reset RUNNING→PENDING, and the orchestrator skips already-SUCCEEDED steps on resume so non-idempotent work like `chunk_documents` isn't re-run).

The **MCP query server** (`kb_platform/mcp/`, opt-in via the `[mcp]` extra) is an optional third process: a **stdio** server that is a **thin HTTP proxy** to the running API server (`KbApiClient` → `GET /kbs`, `POST /kbs/{id}/query`). It never imports graphrag and reimplements no query logic. The testable seam is `KbApiClient` (inject an `httpx` transport); tool logic is plain `async` functions (`list_knowledge_bases` / `query_knowledge_base`) and `build_mcp_server` registers them on a `FastMCP`. Keep it a proxy — do not reach into SQLite or graphrag from the MCP layer.

### Two graphrag isolation "seams" — keep them clean
- `kb_platform/graph/adapter.py` defines the `GraphAdapter` Protocol (chunk / extract / summarize / report / cluster / finalize / embed). **`graphrag_adapter.py` is the ONLY module that imports graphrag internals** — do not add graphrag imports elsewhere in the engine/api layers. `FakeGraphAdapter` (same file) is a deterministic, no-LLM implementation used by every engine test.
- `kb_platform/query/engine.py` defines the `QueryEngine` Protocol; `FakeQueryEngine` is the test/double impl, `graphrag_engine.py` is the real one.
- Multi-turn chat (`kb_platform/conversation/`) is a layer **above** the `QueryEngine` Protocol: `ConversationService` rewrites follow-ups (injected `complete` callable, graphrag stays in `graph/`) → calls the unchanged single-shot engine → persists `conversation`/`message` rows. The single-shot `POST /kbs/{id}/query` (MCP, query-test) is unchanged.

When adding an indexing capability, extend the `GraphAdapter` Protocol and both impls; do not call graphrag directly from a step or strategy.

### Control plane (SQLite) vs data plane (parquet + LanceDB)
SQLite holds the control plane: KBs, documents, chunks, jobs, steps, units, provider profiles. The engine writes the **data plane** as parquet to each KB's `data_root` (entities/relationships/communities/reports/text_units `.parquet`) plus LanceDB vectors to `<data_root>/vectors/`. `data_root` is per-KB. DB access is always via `Repository` (a thin DAO) inside `session_scope`; `expire_on_commit=False` keeps ORM objects usable after the session closes.

### Jobs → steps → units, driven by strategies
A job is a list of `Step`s (`plan_full` / `plan_incremental` in `orchestrator.py`), created up front by `create_job_pending`. Steps are `ATOMIC` (run once in `atomic_steps.py`) or `UNIT_FANOUT` (fan out over per-chunk/entity/community **units**). The `UnitWorker` drives a fanout step by calling its registered `UnitStepStrategy` (`strategy.py` Protocol): `next_units_batch` → `run_unit` → `persist` → `finalize`. Strategies live in `engine/strategies/`. Units carry `pending→running→succeeded/failed`; the orchestrator stops a job when a step ends not-SUCCEEDED. Failed units can be retried individually (`/units/{id}/retry`) or per-step; retry resets the unit to PENDING and reactivates a terminal-FAILED job via `reactivate_job_for_*`. The valid state transitions are centralized in `db/enums.py`.

### Cost capture (contextvar-per-unit)
`CostCapturingCompletion` wraps graphrag-llm's `LLMCompletion`. The `UnitWorker` opens a `use_recorder()` contextvar per unit; concurrent asyncio tasks are isolated because each copies the context. Each `completion_async` records `response.usage` into the unit's `CostRecorder`; `to_json()` lands in `Unit.cost_json`, which `Repository._sum_cost` aggregates by step/model/job. Unknown models contribute tokens with `estimated_cost_usd=None` — cost **never** raises and a missing price latches the total to `None` ("unknown"), never zero. Multiple API keys round-robin through `LoadBalancingCompletion`.

### Provider profiles + KB settings resolution
Connection + keys live in **named provider profiles** (`db/models_profile.py`, `POST /provider-profiles`); a KB references `llm_profile_id` (+ optional `embedding_profile_id`) and keeps only content knobs in `settings_json`. Keys are **Fernet-encrypted at rest** (`db/crypto.py`; master key from `KB_SECRET_KEY` or auto-generated `.kb_secret_key`). The resolution chain when a KB needs a real adapter:

```
assemble_kb_settings(kb, repo)   # profiles + decrypted keys + structured_output + content knobs → settings dict
  → build_adapter_from_settings  # parses settings → ModelConfig + chunking/length/prompt params
    → build_default_adapter      # wires real graphrag chunker + extractors into a GraphRagAdapter
```
`structured_output` (json_schema for community reports) follows the **LLM provider profile**, not the KB; DeepSeek needs `false` → `report_community_plain` path with lenient JSON parsing.

### Incremental indexing
`plan_incremental` chunks only new documents (delta manifest), runs delta extract only on new chunks, then **delta-scoped** summarize / community_reports (unchanged entities carry over via on-disk caches + a `reports_by_hash` sidecar; Leiden reassigns community ids each run). Delta strategies are swapped in by `incremental_strategies()` and the orchestrator. After an incremental job, `reconsolidate` re-merges any late-succeeded retried units into the final parquet (no LLM). Deleting a document removes rows + chunks but **does not shrink the graph** — re-run incremental.

### Frontend SPA hosting
In production the API server serves `web/dist` (path via `KB_WEB_DIST`): `/assets/*` static + a catch-all `/{full_path}` → `index.html` for history routing. API routers are registered **before** the catch-all so explicit API routes always win. Route handlers get `repo` / `data_root` / `query_engine` from `app.state`; `query_engine=None` means production (build a real per-KB engine), a non-None value means tests injected it.

- **查询端点是 SSE 流式**:`POST /kbs/{id}/query` 与 `POST /conversations/{id}/messages` 返回 `text/event-stream`(事件 `meta`/`delta`/`done`/`error`),由 `QueryEngine.stream_search` 驱动;MCP 代理在 `KbApiClient.query()` 内部聚合 SSE 成单结果(工具契约不变)。单发 JSON 不再存在 —— 测试与客户端都按 SSE 解析。

## Conventions & gotchas

- **`loop="asyncio"` is required** in `uvicorn.run` (both production and the e2e server): graphrag-llm calls `nest_asyncio.apply()` at import, which cannot patch uvloop (uvicorn auto-selects it). Don't switch to uvloop.
- **Proxy breaks localhost Ollama**: if `all_proxy`/`http_proxy`/`https_proxy` is set, litellm routes `localhost` through it and fails. Unset proxies or set `NO_PROXY=localhost,127.0.0.1`.
- `chunk_id` is `sha512(text)`; document `content_hash` likewise. This makes chunking idempotent and is how delta detection works.
- `pytest` config: `asyncio_mode = "auto"`, `pythonpath` includes `tests`. `tests/conftest.py` autouse-fixture sets a per-test Fernet master key (clearing the `_fernet` cache) so provider-profile crypto works in every test.
- Backend tests use `FakeGraphAdapter` / `FakeVectorStore` (in-memory) / `FakeQueryEngine`; real-LLM integration tests need a profile with a real key entered via the API.
- The dashboard UI is in Chinese (sidebar groups 工作台 / 知识库 / 检索与问答 / 分析与监控 / 系统管理); match surrounding copy when editing it.
- Design specs and per-phase plans live in `docs/superpowers/{specs,plans}/`; verification records in `docs/`. New non-trivial work follows this spec → plan → implement → verify flow.
- Alembic migrations are numbered `0001…` in `alembic/versions/`; `alembic.ini` targets `sqlite:///./kb.db`. `Base.metadata` is the autogenerate target.
