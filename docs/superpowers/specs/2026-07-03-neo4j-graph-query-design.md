# Neo4j Graph Query — Design

**Date:** 2026-07-03
**Status:** Approved (implementation plan: `docs/superpowers/plans/2026-07-03-neo4j-graph-query.md`)
**Goal owner:** user wants the KB graph queryable in Neo4j as a complete, portable
GraphRAG backend — answering structural / multi-hop questions the existing
local/global/drift/basic modes handle poorly.

Companion to `2026-07-01-neo4j-cypher-export-design.md` (the write side). This spec
is the read side.

## Goal

Add two new query methods to `POST /kbs/{id}/query` — `cypher` (Text2Cypher) and
`hybrid` (Vector+Cypher) — that query the KB's graph in Neo4j via a live `neo4j`
driver connection. Together they cover the retriever patterns from Neo4j's "What is
GraphRAG?" article that the platform does not currently offer:

- **Text2Cypher** — an LLM generates Cypher from the user question + graph schema,
  runs it, and synthesizes a natural-language answer over the rows. Answers
  structural / counting / grouping / path questions ("list all entities of type X
  connected to Y", "how many Z relate to W").
- **Vector+Cypher hybrid** — vector ANN on entity embeddings finds seed entities,
  then a single Cypher traverses 1..N hops over `:RELATED` and `:FROM_CHUNK` edges,
  collecting entity + relationship + text-unit context for synthesis.

Both reuse the existing SSE streaming contract (`meta` / `delta` / `done` / `error`)
and the existing `QueryEngine` Protocol. The platform treats Neo4j as an **external,
user-provided, fully-loaded graph store**: the cypher-export artifact (extended — see
cross-spec dependency) produces a Neo4j database that is *complete* (graph + vectors +
text units), and this spec reads from it.

## Scope

**In scope**

- New `kb_platform/query/neo4j_engine.py` — `Neo4jQueryEngine(QueryEngine)`
  implementing `search` / `stream_search` for `method ∈ {cypher, hybrid}`. No graphrag
  imports.
- New `kb_platform/query/factory.py` — `build_query_engine(method, kb, repo, app_state)`
  dispatching `{cypher, hybrid}` → `Neo4jQueryEngine`, else → `GraphRagQueryEngine`.
- New `kb_platform/neo4j/driver_pool.py` — process-level async-driver pool keyed by
  profile id (mirrors `llm/http_client.py`); `close_all()` wired into
  `bootstrap.close_clients`.
- New `kb_platform/neo4j/safety.py` — `is_readonly_cypher(s) -> bool` (the L1
  validator) + row-cap truncation helper.
- New profile kind `neo4j` in `db/models_profile.py` (`uri`, `username`, encrypted
  `password`); KB column `neo4j_profile_id`; Alembic migration.
- `QueryParams` additions: `hops`, `cypher_timeout_ms`.
- Route wiring: `routes_query.py` + `routes_conversations.py` build engines via
  `build_query_engine` instead of constructing `GraphRagQueryEngine` directly.
- Frontend: query-tuning panel adds the two new methods (Chinese copy, matching the
  existing UI).
- Tests per the Testing section.

**Out of scope (non-goals)**

- Owning the Neo4j *load* — that lives in the (extended) cypher-export spec. This spec
  treats the loaded graph as a precondition and declares the contract below.
- Agentic / multi-step traversal (an LLM picks retrievers iteratively) — future spec.
- Graph algorithms (GDS: PageRank, FastRP, PPR) — future spec.
- A `HealthProbe` / circuit-breaker for Neo4j (the LLM-layer breaker machinery is for
  multi-profile failover; Neo4j is one endpoint per KB). Driver-pool connection
  failures surface as SSE errors; no retry/failover.
- Communities / community_reports in Neo4j — global/drift stay on parquet.

## Architecture

### Engine layer

Three new modules, single-responsibility, graphrag-isolation seam kept clean
(`graphrag_engine.py` remains the only graphrag import on the query side):

| File | Responsibility |
|---|---|
| `kb_platform/neo4j/driver_pool.py` | Process-level `neo4j.AsyncGraphDatabase.driver` pool, keyed by profile id (mirrors `llm/http_client.py`); `get_driver(profile)` + `close_all()`. Drivers are expensive to create, so they are reused across requests. |
| `kb_platform/query/neo4j_engine.py` | `Neo4jQueryEngine(QueryEngine)`. Holds a resolved profile + the pool. Implements `search` / `stream_search` for `cypher` and `hybrid` only. **Does not import graphrag.** The `neo4j` python driver is the only new runtime dependency. |
| `kb_platform/query/factory.py` | `build_query_engine(method, kb, repo, app_state) -> QueryEngine`. If `method ∈ {cypher, hybrid}` AND `kb.neo4j_profile_id` is set → `Neo4jQueryEngine`; otherwise → `GraphRagQueryEngine`. Encapsulates the choice so routes stay thin. |

`routes_query.py` and `routes_conversations.py` replace their direct
`GraphRagQueryEngine(data_root=..., model_config=...)` construction with a call to
`build_query_engine(method=payload.method, kb=kb, repo=repo, app_state=request.app.state)`.
The `QueryEngine` Protocol is unchanged; `FakeQueryEngine` is unchanged (it already
handles any method string, so injected-engine tests are unaffected).

The `neo4j` driver is an **opt-in extra** (`[neo4j]`, mirroring `[mcp]`). All neo4j
imports are lazy. If a KB has a `neo4j_profile_id` but the extra is not installed,
`build_query_engine` raises a clear error surfaced as SSE `error`
("install with `uv sync --extra neo4j`").

### Why a separate engine class, not a new branch in `_build_engine`

`GraphRagQueryEngine._build_engine` is the single seam where graphrag's query factory
is imported. A Neo4j code path has no graphrag dependency, so folding it into
`_build_engine` would either pollute the graphrag seam with a `neo4j` import or require
defensive try/except imports. A parallel `Neo4jQueryEngine` keeps each engine's
dependency surface honest and independently testable.

## Cross-spec dependency (the cypher-export artifact must be extended)

This spec is the **read side**. It requires the Neo4j database to already contain a
complete graph + vector store, produced by `2026-07-01-neo4j-cypher-export-design.md`.
That spec must be **extended** before this one can run:

1. **TextUnit nodes.** Export `:TextUnit` nodes (one per `text_units.parquet` row) with
   at least `{id, text, document_ids, chunk_ids}`.
2. **FROM_CHUNK edges.** Export `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges, derived
   from each entity's `text_unit_ids` (the inverse of the entity→chunk mapping graphrag
   already produces).
3. **Entity vector index.** Export `embeddings.jsonl` (`{title, vector}` read from the
   LanceDB `entity_description` table at `<data_root>/vectors/`) and a load section that
   runs `CREATE VECTOR INDEX entity_description_vec IF NOT EXISTS` + batched
   `UNWIND` + `db.create.setNodeVectorProperty` to set `e.entity_description`.
4. **TextUnit vector index.** Same, for `text_unit_text` embeddings →
   `text_unit_text_vec` index + `t.text_unit_text` property.
5. Remove the current "Out of scope: vector embeddings" line from the export spec.

**Contract this spec consumes** (asserted lazily on first query, surfaced as a clear
SSE error if violated):

- `:Entity` nodes with properties `title` (unique), `type`, `degree`, `description`,
  `text_unit_ids`, `frequency`, … (all entity columns; `title` is the identity key).
- `(:Entity)-[:RELATED]->(:Entity)` edges with `description`, `weight`,
  `combined_degree`, `text_unit_ids`.
- `:TextUnit` nodes with `id`, `text`, `document_ids`.
- `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges.
- Vector index `entity_description_vec` over `Entity.entity_description`.
- Vector index `text_unit_text_vec` over `TextUnit.text_unit_text`.
- Uniqueness constraint `entity_title_unique` on `Entity.title`; index on `Entity.type`.

The implementation of (1)–(5) is **owned by a revision of the cypher-export spec**, not
this spec. This spec only declares the dependency and the contract.

## Query mode 1 — Text2Cypher (`method="cypher"`)

```
1. Resolve KB's neo4j profile (factory already did this to construct the engine);
   get a driver from the pool.
2. Build the Text2Cypher prompt:
     - Canonical schema (hardcoded — it is ours and stable): :Entity fields,
       :RELATED edge fields, :TextUnit fields, :FROM_CHUNK edge.
     - A handful of few-shot examples covering the canonical patterns:
       find entity by title/type, k-hop neighbors, path between two entities,
       count, group/aggregate.
     - The user question.
3. kb_native completion (LLM, the KB's llm_profile) → generated Cypher string.
4. SSE: meta { method, cypher }                          # L3 transparency
5. L1 validate: is_readonly_cypher(cypher) must be True; else SSE error.
6. driver.execute_query(cypher, timeout=cypher_timeout_ms)   # L2 timeout
     Pull rows; truncate to ROW_CAP (1000).                  # L2 row cap
7. kb_native completion (stream=True): question + rows → synthesis.
     async for token → yield StreamDelta(text=token)         # consistent streaming UX
8. SSE: done { result: { answer, method, sources: [matched entities/relationships],
                          truncated: bool } }
```

`sources` reuses `SourceRef` (`kind ∈ {entity, relationship, text_unit}`): top-N matched
entities become `entity` sources; the relationships along the result become
`relationship` sources.

Failure paths (LLM generation failure, L1 rejection, execution timeout, driver error)
yield a terminal `StreamDone(error=...)` or SSE `error` event — never HTTP 500,
matching the existing query-path discipline.

## Query mode 2 — Vector+Cypher hybrid (`method="hybrid"`)

```
1. Resolve profile → driver.
2. kb_native embedding (the KB's embedding_profile) → embed the user question.
3. One Cypher (vector ANN → multi-hop traversal → collect context), template-built
   from $top_k and $hops:

     CALL db.index.vector.queryNodes('entity_description_vec', $top_k, $vector)
       YIELD node AS seed, score
     // then, in the same query, walk 1..$hops :RELATED hops out from each seed,
     // optionally hop to :TextUnit nodes via :FROM_CHUNK for both the reached
     // entities and the seeds, and RETURN three collected bags:
     //   entities    — distinct entity property maps in the subgraph
     //   relationships — distinct :RELATED edge property maps along the paths
     //   chunks      — distinct :TextUnit property maps (text + document_ids)
     // for the synthesis context.

   (Exact Cypher is finalized in the plan — variable-length path bounds, relationship
   binding, and DISTINCT/dedup need care. The intent is the article's chunk→entity→
   neighbor context: seed entities from vector ANN → their 1..$hops :RELATED
   neighborhood → the text units that mention any entity in that subgraph.)
4. Format the returned entity/relationship/text-unit triples into a synthesis context.
5. kb_native completion (stream=True): question + context → answer.
     async for token → yield StreamDelta(text=token)
6. SSE: meta { method, cypher (the templated Cypher, for debug) }
   SSE: done { result: { answer, method, sources } }
```

`top_k` reuses `QueryParams.top_k` (default falls through to a sensible hybrid
baseline, e.g. 10). `hops` defaults to 2.

## Data model — neo4j profile

Reuse the existing provider-profile mechanism with minimal addition:

- `db/models_profile.py`: a new `kind="neo4j"`. Profile payload fields: `uri`
  (e.g. `bolt://localhost:7687`), `username`, `password`. `password` is Fernet-encrypted
  at rest via the existing `db/crypto.py` (same path as LLM keys; the autouse test
  fixture's per-test master key covers it).
- `db/models.py` (`KnowledgeBase`): new nullable column `neo4j_profile_id`.
- New Alembic migration `00XX_add_neo4j_profile_id.py`.
- Resolution: **not** folded into `assemble_kb_settings` (that resolves LLM/embedding
  config for indexing). The factory resolves the neo4j profile independently:
  `repo.get_profile(kb.neo4j_profile_id)` → decrypt → hand to `Neo4jQueryEngine`.

A KB without `neo4j_profile_id` simply does not support `cypher` / `hybrid` — those
methods return a clear SSE error.

## API surface

- Two new `method` values: `cypher`, `hybrid`. `QueryRequest.method` is already a
  free `str`, so no request-model change.
- `QueryParams` (in `query/engine.py`) gains two optional fields:
  - `hops: int | None = None` (hybrid traversal depth; default 2 when None).
  - `cypher_timeout_ms: int | None = None` (default 10000 when None).
- SSE event shape is unchanged (`meta` / `delta` / `done` / `error`). The `meta`
  payload gains an optional `cypher` field carrying the generated/templated Cypher
  (L3 transparency, both methods).
- `StreamDone` gains a `truncated: bool` field (L2 row-cap indicator); default False.
- Frontend query-tuning panel adds the two methods; copy in Chinese to match the
  existing dashboard.

## Safety (decided: L0 contract + L1 enforced + L2 + L3)

The risk: an LLM generates Cypher from a free-text question. Without guards it could
emit `DROP`, `DETACH DELETE`, `MERGE`, or `LOAD CSV FROM 'http://…'`. Defense in
depth:

- **L0 — read-only Neo4j user (deployment contract).** The neo4j profile MUST connect
  a user with only `READ` privileges. This is documented as a hard prerequisite (README
  + profile editor warning). It is not enforced in code (it is a DB privilege, which
  cannot be bypassed by definition). L0 is the guarantee of last resort: anything L1
  misses is blocked at the DB.
- **L1 — read-only Cypher validator (enforced in code).** Pure function
  `is_readonly_cypher(s) -> bool` in `kb_platform/neo4j/safety.py`. Parses the generated
  Cypher and admits only statements whose root verb is `MATCH`, `RETURN`, `PROFILE`,
  `EXPLAIN`, or `SHOW`. Rejects write/DDL (`CREATE`, `MERGE`, `DELETE`, `DETACH`,
  `DROP`, `SET`, `REMOVE`, `LOAD CSV`, `CALL` for write procedures, …). This is the
  actual enforced layer for LLM output. Belt-and-suspenders with L0: either layer
  failing alone still leaves the other blocking writes.
- **L2 — timeout + row cap.** `driver.execute_query(..., timeout=cypher_timeout_ms)`.
  Result rows truncated to `ROW_CAP = 1000`; `StreamDone.truncated=True` when hit.
- **L3 — transparency.** The generated (cypher) / templated (hybrid) Cypher is emitted
  in the `meta` event so abuse and bugs are visible.

## Error handling (all SSE, never HTTP 500)

| Situation | Behavior |
|---|---|
| KB has no `neo4j_profile_id` | SSE `error`: "KB has no Neo4j profile; configure one" |
| Neo4j unreachable (driver connect / execute failure) | SSE `error`: "Neo4j connection failed: {detail}" |
| `[neo4j]` extra not installed | SSE `error`: "install with `uv sync --extra neo4j`" |
| LLM fails to generate Cypher | SSE `error` |
| L1 rejects the generated Cypher | SSE `error`: "generated Cypher is not read-only; refused" |
| Execution timeout (L2) | SSE `error`: "Cypher execution timed out (>{ms}ms)" |
| Result rows truncated (L2) | Not an error; `done.truncated = True` |

## Dependencies

- `neo4j` python driver — opt-in extra `[neo4j]` in `pyproject.toml` (mirrors `[mcp]`).
  All neo4j imports are lazy so the platform runs unchanged without it.
- Driver pool `close_all()` is wired into `bootstrap.close_clients` alongside the httpx
  pool, so server/worker shutdown drains Neo4j connections too.
- Requires **Neo4j ≥ 5.11** at the user's deployment (vector index +
  `db.create.setNodeVectorProperty`). The export's load-script header already notes
  this; this spec assumes it on read.

## Cost / usage capture

Text2Cypher issues 2 LLM calls (generate Cypher + synthesize); hybrid issues 1 embedding
+ 1 LLM call. All route through `kb_native`. These do **not** flow through the
per-unit `CostRecorder` contextvar (that is an indexing mechanism). Instead,
`prompt_tokens` / `output_tokens` are read directly from each completion response's
`usage` and accumulated onto `StreamDone`, matching how the existing graphrag query
path reports tokens on `QueryResult`.

## Testing

| Layer | How |
|---|---|
| `is_readonly_cypher` | Pure-function unit tests: positive (`MATCH`, `RETURN`, `PROFILE`, `SHOW`) and negative (`CREATE`, `MERGE`, `DELETE`, `DROP`, `LOAD CSV`, `CALL apoc.create.*`). |
| Text2Cypher prompt builder | Pure function; assert schema + few-shot + question are present. |
| Hybrid Cypher template builder | Pure function given `top_k` / `hops`; assert the `*1..N` and vector-index name render correctly. |
| Row → context formatter | Pure function over fixture rows. |
| `build_query_engine` dispatch | Mock profiles; assert `{cypher, hybrid}` + profile-set → `Neo4jQueryEngine`, else → `GraphRagQueryEngine`. |
| Driver pool | Mock the driver factory; assert `get_driver` reuses by profile id and `close_all` closes all. |
| Route | `POST /kbs/{id}/query?method=cypher` with no profile → SSE `error`; with `FakeQueryEngine` injected → normal streaming flow. |
| **Real-Neo4j integration** | `testcontainers-neo4j` (Neo4j ≥ 5.11); load the export artifact, run both methods end-to-end. Marked integration; skipped when no Neo4j / no real LLM profile (same tier as existing real-LLM integration tests). |

## Files

- **New** `kb_platform/neo4j/__init__.py`, `kb_platform/neo4j/driver_pool.py`,
  `kb_platform/neo4j/safety.py`.
- **New** `kb_platform/query/neo4j_engine.py`, `kb_platform/query/factory.py`.
- **Edit** `kb_platform/query/engine.py` — add `hops`, `cypher_timeout_ms` to
  `QueryParams`; add `truncated` to `StreamDone`.
- **Edit** `kb_platform/api/routes_query.py`, `kb_platform/api/routes_conversations.py`
  — build engines via `build_query_engine`.
- **Edit** `kb_platform/db/models_profile.py` — `kind="neo4j"` payload.
- **Edit** `kb_platform/db/models.py` — `KnowledgeBase.neo4j_profile_id`.
- **New** Alembic migration `00XX_add_neo4j_profile_id.py`.
- **Edit** `kb_platform/llm/bootstrap.py` (or wherever `close_clients` lives) — wire
  `neo4j.driver_pool.close_all()` (lazy; no-op when extra absent).
- **Edit** `pyproject.toml` — `[neo4j]` extra.
- **New** `tests/test_neo4j_safety.py`, `tests/test_neo4j_engine_unit.py`,
  `tests/test_query_factory.py`, `tests/test_neo4j_integration.py` (integration).
- **Edit** frontend query-tuning panel + i18n strings.
- **New** this spec.
- **Revise** `2026-07-01-neo4j-cypher-export-design.md` — extend with TextUnit nodes,
  FROM_CHUNK edges, entity + text-unit vector exports (cross-spec dependency above).

## Risks / notes

- **Cypher-injection via the LLM.** The user question is natural text fed to the LLM,
  not interpolated into Cypher directly, so classical injection is not the vector — a
  misbehaving LLM is. L0 + L1 together mitigate; documented above.
- **Hybrid Cypher shape is non-trivial.** Collecting entities + relationships + text
  units across a `*1..N` traversal without exploding the result set needs care
  (`collect(DISTINCT …)`, bounds on N). The plan should pin down the exact Cypher and
  add a unit test against fixture graph data.
- **Vector-recall parity with LanceDB.** Hybrid now uses Neo4j's vector index, not
  LanceDB, so recall/speed characteristics differ from local search. Acceptable —
  hybrid is a different mode, not a replacement for local. If recall is materially
  worse, a follow-on can switch hybrid's ANN back to LanceDB and bridge by `title`
  (the original option A).
- **Staleness.** The Neo4j graph is a snapshot loaded by the user via the export
  script; it is not auto-refreshed on reindex. v1 treats this as a manual operator
  step (re-run `cypher-shell -f load.cypher` after a reindex). A staleness indicator
  (last-loaded vs last-indexed) is a possible follow-on, not in scope here.
- **One Neo4j per KB.** The profile is per-KB; there is no shared/multi-tenant Neo4j
  story. Sharing one Neo4j across KBs (separate databases or label namespaces) is a
  future concern.
