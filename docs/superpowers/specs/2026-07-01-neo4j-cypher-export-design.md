# Neo4j Cypher Export — Design

**Date:** 2026-07-01
**Status:** Approved (implemented — see docs/superpowers/plans/2026-07-03-neo4j-cypher-export.md)
**Goal owner:** user wants the KB graph loadable into Neo4j as a knowledge base for an agent workflow.

## Goal

Add a `format=cypher` export to `GET /kbs/{id}/export` that emits an **idempotent**
Cypher script for loading the entity + relationship graph into Neo4j. Target use case:
an agent runtime queries the resulting graph (find entity by title/type, fetch
neighbors, pull a subgraph, find a path). The graph must carry **all** entity/relationship
columns as properties and keep list-valued columns as native Neo4j arrays.

Shape mirrors the existing GraphML export: a stateless downloadable artifact produced
by a writer isolated under `kb_platform/graph/`, registered on the existing export route.
No new runtime dependencies, no secrets, no live driver connection from the platform.

The script also emits `:TextUnit` nodes, `:FROM_CHUNK` edges, and both vector indexes
(`entity_description`, `text_unit_text`) so the resulting Neo4j database is a complete,
portable GraphRAG backend (the read side is `2026-07-03-neo4j-graph-query-design.md`).

## Scope

**In scope**

- New writer `kb_platform/graph/cypher.py`: `write_cypher(entities, relationships, text_units=None, entity_embeddings=None, text_unit_embeddings=None) -> str`.
- `format=cypher` branch on `routes_export.py::export`, returning `text/plain; charset=utf-8`.
- Include `graph.cypher` in the `format=zip` bundle (alongside `graph.graphml`).
- `TextUnit` nodes (one per `text_units.parquet` row) and `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges derived from each entity's `text_unit_ids`.
- Vector indexes + vector properties for `entity_description` and `text_unit_text` (read from LanceDB), loaded via `db.create.setNodeVectorProperty`.
- `embeddings` loading via the `lancedb` package directly (bulk read of `id` + `vector` columns).
- Unit tests for the writer + a route test.

**Out of scope (non-goals)**

- Live `neo4j`-driver push from the platform (rejected option C — needs secrets/conn mgmt).
- CSV + `neo4j-admin database import` bulk load (rejected option B — requires empty/offline DB, not re-runnable).
- Community / community_reports nodes (global/drift stay on parquet; not needed for the graph-query modes).
- Documents as nodes (model scope = entities + relationships + text units; documents are not in the parquet artifacts as a node-bearing table).
- Incremental delta export — re-running the full script MERGE-updates in place instead.

## Graph model

- **Nodes:** one `:Entity` label per entity row. `title` is the identity property.
  A uniqueness constraint + a `type` index are emitted so the target DB self-configures
  and agent lookups are indexed.
- **Relationships:** all edges are `:RELATED` (the relationships parquet in this codebase
  has no `relationship`/name column — only `source, target, description, text_unit_ids,
  weight, combined_degree`). Edge semantics ride on the `description` text, which the
  agent reads.
- **Identity / idempotency:** `MERGE` on `:Entity {title}` for nodes; `MERGE` on
  `(source)-[:RELATED]->(target)` for edges. Re-running an updated export updates
  properties in place without creating duplicates.
- **TextUnit nodes:** one `:TextUnit` label per text_units row, keyed by `id`. Each
  entity's `text_unit_ids` array becomes `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges —
  the text units that mention each entity. Orphan references (text_unit id absent from
  the text_units set) are dropped at write time.
- **Vectors:** when LanceDB embeddings are present, `CREATE VECTOR INDEX entity_description_vec`
  over `Entity.entity_description` (keyed by `title`) and `text_unit_text_vec` over
  `TextUnit.text_unit_text` (keyed by `id`). Similarity is cosine; dimensions are detected
  from the first vector. Properties are set via `db.create.setNodeVectorProperty`
  (Neo4j ≥ 5.11). When embeddings are absent, the vector section is omitted and the
  graph-only load still succeeds.

## Script structure

The writer emits, in order:

```
// Header comment: run with `cypher-shell -f load.cypher` or paste into Neo4j Browser.

// 1. Schema preamble (idempotent — IF NOT EXISTS)
CREATE CONSTRAINT entity_title_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.title IS UNIQUE;
CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type);

// 2. Entity batches (~500 rows per block)
WITH [{title: "A", type: "ORG", description: "...", text_unit_ids: ["c1"], ...}, ...] AS rows
UNWIND rows AS row
MERGE (e:Entity {title: row.title})
SET e += row;

// 3. Relationship batches (~500 rows per block)
WITH [{source: "A", target: "B", _p: {description: "...", weight: 1.0, ...}}, ...] AS rows
UNWIND rows AS row
MATCH (s:Entity {title: row.source}), (t:Entity {title: row.target})
MERGE (s)-[r:RELATED]->(t)
SET r += row._p;
```

Key behaviors:

- `SET e += row` keeps **every** column as a property (contrast with GraphML's
  whitelist of `type`/`degree`/`description`). `title` is re-set to the same value — no
  effect.
- For relationships, `source`/`target` are pulled out of the property map (used by the
  `MATCH`); the remaining columns (`description`, `weight`, `text_unit_ids`,
  `combined_degree`) go into `row._p` so they become relationship properties without
  polluting the edge with `source`/`target` props.
- **List columns stay native arrays.** `description` and `text_unit_ids` become Neo4j
  string-array properties, not flattened text. (GraphML flattens via `cell_to_text`;
  Cypher deliberately does not.)
- **Orphan edges are dropped.** A relationship whose endpoint entity is absent from the
  entities set yields no `MATCH` and is silently skipped — no dangling edges. (graphrag's
  merged parquet should not produce these, but the writer is defensive.)
- **Batch size ≈ 500 rows** per `WITH`/`UNWIND` block, keeping each statement parseable
  and memory-bounded. 378 entities → 1 block; 1030 relationships → 3 blocks (current KB).

## Serialization

Row data is emitted INLINE as Cypher-native literals —
`WITH [{title: "A", ...}, ...] AS rows UNWIND rows AS row ...` — NOT via
cypher-shell's `:param` directive. **Cypher 5 rejects `:param` for object lists**:
cypher-shell wraps `:param x => <value>` in `RETURN {x: <value>} AS result` to
evaluate it, and Cypher 5 no longer parses a JSON-style map literal (quoted
keys `{"title": ...}`) in expression position. The previously shipped
`:param rows => <JSON>` format therefore never loaded into Neo4j 5.x (caught by
the real-load test added with this fix, `tests/test_cypher_export_load.py`).

`_cypher_literal(value)` is the single serializer:

| Cell value                              | Cypher output            |
|-----------------------------------------|--------------------------|
| `None` / `pd.NA` / `NaN` / `NaT`        | `null`                   |
| numpy scalar (`int64`, `float64`, …)    | native Python int/float  |
| `ndarray` / list / tuple / `Series`     | Cypher list (recursively)|
| `str`                                   | Cypher string (JSON-escaped via `json.dumps` — a valid Cypher string escape; `ensure_ascii=False`) |
| bool                                    | `true` / `false`         |
| dict                                    | `{key: value, ...}` with **identifier (unquoted) keys** when the key is a valid Cypher identifier, else backtick-quoted |

Neo4j property arrays must be homogeneous; graphrag's list columns (`text_unit_ids`,
`description` after merge) are homogeneous string arrays, so this holds. The one
deliberate nested map is the relationship `_p` (properties split from endpoints).

## Files

- **New** `kb_platform/graph/cypher.py` — the writer `write_cypher(entities, relationships, text_units=None, entity_embeddings=None, text_unit_embeddings=None) -> str`.
- **New** `tests/test_cypher_export.py` — writer unit tests.
- **Edit** `kb_platform/api/routes_export.py` — add `format=cypher` branch; add
  `graph.cypher` to the zip bundle; add `_load_text_units` + `_load_embeddings` loaders.
- **Edit** `tests/test_api_export.py` — add a `?format=cypher` case.
- **New** this spec.

## Testing

`tests/test_cypher_export.py` (pandas-fixture style, mirroring `tests/test_graphml.py`):

- Schema preamble present (`CREATE CONSTRAINT … entity_title_unique`, `CREATE INDEX … entity_type`).
- **Every** entity column appears as a property in `SET e += row` (whitelist regression — `text_unit_ids`, `frequency`, etc. all present).
- **Every** relationship column except `source`/`target` appears in `row._p`.
- List columns serialize to JSON arrays (`"text_unit_ids":["c1","c2"]`), **not** flattened strings — assert the raw `['c1' 'c2']` repr is absent.
- Strings containing `"`, `\`, and newlines are correctly JSON-escaped (inside a Cypher string literal).
- **No `:param` / `$rows` anywhere**; each data block is `WITH <list> AS rows` + `UNWIND rows AS row` with identifier (unquoted) keys and bracket-balanced literals.
- Empty entity / relationship DataFrames do not crash; preamble still emitted.

The script must LOAD into Neo4j 5.x — `tests/test_cypher_export_load.py` boots a
testcontainer, executes every statement via the driver, and asserts the graph
(the regression the original `:param` format needed and lacked).

Orphan-edge dropping is a **runtime** consequence of the `MATCH` (it executes at load
time in Neo4j, not at write time), so the writer always emits every relationship row into
its param block; it is documented behavior, not a writer-level unit test.

`tests/test_api_export.py`:

- `GET /kbs/{id}/export?format=cypher` returns `text/plain; charset=utf-8` and the body contains `CREATE CONSTRAINT`.
- `?format=zip` now includes a `graph.cypher` entry alongside `graph.graphml`.
- Unknown `format` still returns HTTP 400.

## Risks / notes

- **Relationship dedup.** `MERGE (s)-[:RELATED]->(t)` collapses any duplicate `(source, target)` pairs in the parquet into a single edge (last row's properties win). graphrag's `merge_extractions` already merges same-pair relationships into one row with an aggregated `description` list and summed `weight`, so this should be a no-op in practice; documented for safety.
- **Inline `WITH` (not `:param`).** The script uses inline `WITH [<rows>] AS rows UNWIND rows AS row` because cypher-shell 5's `:param x => <value>` evaluates via `RETURN {x: <value>}`, which Cypher 5 rejects for object payloads (quoted-key map literals are not parseable in expression position). Both `cypher-shell -f` and the Neo4j Browser accept the inline form. Older Neo4j versions (< 4.4) lack `IF NOT EXISTS` on constraints; the script targets Neo4j ≥ 5.11 (documented in the header comment).
- **Neo4j ≥ 5.11** is now required (was 5.0) for `CREATE VECTOR INDEX` + `db.create.setNodeVectorProperty`. The header comment documents this.
- **No APOC dependency.** Everything uses built-in Cypher (`MERGE`, `UNWIND`, `SET +=`, `CREATE CONSTRAINT … IF NOT EXISTS`, `CREATE VECTOR INDEX`, `db.create.setNodeVectorProperty`).
