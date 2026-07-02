# Neo4j Cypher Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `format=cypher` export on `GET /kbs/{id}/export` that emits an idempotent Cypher load script for Neo4j, covering the full graph + text units + both vector indexes — the complete, portable backend the graph-query spec depends on.

**Architecture:** A pure writer `kb_platform/graph/cypher.py::write_cypher(...)` takes pandas DataFrames + embedding dicts and returns a Cypher string. The route loads parquet + LanceDB vectors and calls the writer. Graphrag stays unimported in the writer (it only takes DataFrames); LanceDB is read directly via the `lancedb` package (transitive dep, verified 0.24.3) for full-table reads. Tests are pandas-fixture style, mirroring `tests/test_graphml.py`.

**Tech Stack:** Python 3.11, pandas, lancedb (transitive), pytest (asyncio auto mode), FastAPI, Neo4j ≥ 5.11 (target runtime, not a test dep).

## Global Constraints

- **Neo4j ≥ 5.11** at the user's deployment (vector index + `db.create.setNodeVectorProperty`). Stated in the script header comment.
- **Ruff line-length 100, target py311.** All new code must pass `uv run ruff check .`.
- **pytest config:** `asyncio_mode = "auto"`, `pythonpath` includes `tests`. The autouse Fernet-key fixture in `tests/conftest.py` is active (not strictly needed here — no DB crypto in the writer — but harmless).
- **Parquet column names are fixed** (verified against the current KB): entities `{title, type, description, text_unit_ids(ndarray), frequency, degree}`; relationships `{source, target, description, text_unit_ids(ndarray), weight, combined_degree}`; text_units `{id, text, document_ids(ndarray), n_tokens}`. The writer is schema-agnostic (`SET e += row` emits every column), but tests use these exact columns.
- **LanceDB vector tables** at `<data_root>/vectors/`, named `entity_description` + `text_unit_text`, columns `id` + `vector`. `id` = entity title (entity_description) / text_unit id (text_unit_text).
- **`description` / `text_unit_ids` / `document_ids` are numpy arrays** post-parquet-round-trip — the writer's coercion helper must handle `ndarray` → list, never the raw `['..' '..']` repr.
- **No graphrag imports in the writer.** The writer takes DataFrames + dicts only.

---

### Task 1: Core writer — schema preamble + entity + relationship batching

**Files:**
- Create: `kb_platform/graph/cypher.py`
- Test: `tests/test_cypher_export.py`

**Interfaces:**
- Produces: `write_cypher(entities, relationships, text_units=None, entity_embeddings=None, text_unit_embeddings=None) -> str` in `kb_platform/graph/cypher.py`. Tasks 2 and 3 fill in the `text_units` / embeddings branches; this task implements the signature, the `_coerce` helper, and the entity/relationship/preamble sections. When `text_units` / embeddings are `None` (the default), only the entity/relationship graph is emitted.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cypher_export.py`:

```python
"""Tests for the Cypher export writer."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from kb_platform.graph.cypher import write_cypher


def _ents():
    return pd.DataFrame(
        [
            {
                "title": "A",
                "type": "ORG",
                "description": "alpha",
                "text_unit_ids": np.array(["c1", "c2"]),
                "frequency": 2,
                "degree": 5,
            }
        ]
    )


def _rels():
    return pd.DataFrame(
        [
            {
                "source": "A",
                "target": "B",
                "description": "rel-desc",
                "text_unit_ids": np.array(["c1"]),
                "weight": 8.0,
                "combined_degree": 13,
            }
        ]
    )


def _param_blocks(script: str) -> list[str]:
    """Return the JSON RHS of every `:param rows =>` line."""
    out = []
    for line in script.splitlines():
        s = line.strip()
        if s.startswith(":param rows =>"):
            out.append(s[len(":param rows =>"):].rstrip(";"))
    return out


def test_preamble_present():
    s = write_cypher(_ents(), _rels())
    assert "CREATE CONSTRAINT entity_title_unique IF NOT EXISTS" in s
    assert "FOR (e:Entity) REQUIRE e.title IS UNIQUE" in s
    assert "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)" in s
    assert "cypher-shell" in s  # header usage comment


def test_every_entity_column_is_a_property():
    s = write_cypher(_ents(), _rels())
    # entity MERGE on title, SET += row carries every column
    assert "MERGE (e:Entity {title: row.title})" in s
    assert "SET e += row" in s
    # every param block's row dicts carry the extra columns
    blocks = _param_blocks(s)
    entity_blocks = [b for b in blocks if '"title"' in b and '"type"' in b]
    assert entity_blocks, "no entity param block found"
    row = json.loads(entity_blocks[0])[0]
    for col in ("title", "type", "description", "text_unit_ids", "frequency", "degree"):
        assert col in row, f"column {col!r} missing from entity row"
    # text_unit_ids is a JSON array, not a flattened string
    assert row["text_unit_ids"] == ["c1", "c2"]


def test_every_relationship_column_except_endpoints_in_p():
    s = write_cypher(_ents(), _rels())
    assert "MATCH (s:Entity {title: row.source}), (t:Entity {title: row.target})" in s
    assert "MERGE (s)-[r:RELATED]->(t)" in s
    assert "SET r += row._p" in s
    blocks = _param_blocks(s)
    rel_blocks = [b for b in blocks if '"source"' in b and '"_p"' in b]
    assert rel_blocks
    row = json.loads(rel_blocks[0])[0]
    assert set(row.keys()) == {"source", "target", "_p"}
    for col in ("description", "text_unit_ids", "weight", "combined_degree"):
        assert col in row["_p"], f"column {col!r} missing from relationship _p"
    assert "source" not in row["_p"] and "target" not in row["_p"]


def test_each_param_rhs_is_valid_json():
    s = write_cypher(_ents(), _rels())
    for rhs in _param_blocks(s):
        json.loads(rhs)  # must not raise


def test_list_columns_are_arrays_not_flattened():
    s = write_cypher(_ents(), _rels())
    # the ndarray must NOT appear as its Python repr
    assert "['c1'" not in s
    # it appears as a JSON array
    assert '"text_unit_ids": ["c1", "c2"]' in s or '"text_unit_ids":["c1","c2"]' in s


def test_strings_with_quotes_and_backslashes_escaped():
    ents = pd.DataFrame([{"title": 'a"b\\c', "type": "X", "description": "d\ne"}])
    rels = pd.DataFrame([{"source": "x", "target": "y", "description": "z"}])
    s = write_cypher(ents, rels)
    for rhs in _param_blocks(s):
        json.loads(rhs)  # escaping must keep every :param RHS parseable


def test_empty_dataframes_do_not_crash():
    s = write_cypher(
        pd.DataFrame(columns=["title"]),
        pd.DataFrame(columns=["source", "target"]),
    )
    assert "CREATE CONSTRAINT entity_title_unique" in s  # preamble still emitted
    for rhs in _param_blocks(s):
        json.loads(rhs)


def test_numpy_scalars_become_native():
    ents = pd.DataFrame(
        [{"title": "A", "type": "X", "frequency": np.int64(7), "degree": np.int64(3)}]
    )
    s = write_cypher(ents, pd.DataFrame(columns=["source", "target"]))
    blocks = [b for b in _param_blocks(s) if '"title"' in b]
    row = json.loads(blocks[0])[0]
    assert row["frequency"] == 7 and isinstance(row["frequency"], int)
    assert row["degree"] == 3 and isinstance(row["degree"], int)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cypher_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kb_platform.graph.cypher'` (or `ImportError`).

- [ ] **Step 3: Write minimal implementation**

Create `kb_platform/graph/cypher.py`:

```python
"""Cypher export writer: parquet DataFrames + embedding dicts -> Cypher script.

The writer is pure (no graphrag imports, no I/O). The route loads parquet +
LanceDB vectors and hands them in. Output is an idempotent Cypher script that
MERGE-upserts entities, relationships, text units, and (when supplied) vector
indexes + vector properties into Neo4j >= 5.11.

Every `:param rows => <JSON>` line's right-hand side is valid JSON
(``json.loads`` must not raise) — the writer therefore serializes with
``json.dumps(rows, ensure_ascii=False)`` and never hand-rolls Cypher escaping.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

_BATCH = 500


def _coerce(value: Any) -> Any:
    """Coerce a parquet cell into a JSON-serializable Python value.

    - None / NaN / NA / NaT -> None
    - numpy scalar -> native Python int/float/bool
    - ndarray / list / tuple / Series -> list (recursively coerced)
    - everything else passes through (str, bool, int, float)
    """
    import numpy as np

    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        v = value.item()
        if isinstance(v, float) and math.isnan(v):
            return None
        return v
    if isinstance(value, np.ndarray):
        return [_coerce(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    if isinstance(value, pd.Series):
        return [_coerce(v) for v in value.tolist()]
    return value


def _row_dict(row: pd.Series) -> dict:
    return {col: _coerce(val) for col, val in row.items()}


def _batches(rows: list[dict], size: int = _BATCH):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _emit_param_block(lines: list[str], rows: list[dict]) -> None:
    lines.append(f":param rows => {json.dumps(rows, ensure_ascii=False)};")
    lines.append("UNWIND $rows AS row")


def write_cypher(
    entities: pd.DataFrame,
    relationships: pd.DataFrame,
    text_units: pd.DataFrame | None = None,
    entity_embeddings: dict[str, list[float]] | None = None,
    text_unit_embeddings: dict[str, list[float]] | None = None,
) -> str:
    """Render an idempotent Cypher load script.

    Only entities + relationships are required. TextUnit nodes + FROM_CHUNK
    edges are emitted when ``text_units`` is supplied (Task 2). Vector indexes
    + vector properties are emitted when the corresponding embedding dict is
    supplied and non-empty (Task 3).
    """
    lines: list[str] = []
    lines.append("// Cypher load script generated by KB Platform.")
    lines.append("// Load with: cypher-shell -a <uri> -u <user> -p <pw> -f load.cypher")
    lines.append("// Requires Neo4j >= 5.11 (vector index + db.create.setNodeVectorProperty).")
    lines.append("// Idempotent: MERGE / IF NOT EXISTS; safe to re-run after a reindex.")
    lines.append("")
    _emit_schema_preamble(lines)
    _emit_entities(lines, entities)
    _emit_relationships(lines, relationships)
    # Task 2 fills these in; no-op stubs keep the signature stable across tasks.
    if text_units is not None:
        _emit_text_units(lines, text_units, entities)
    if entity_embeddings:
        _emit_vector_index(lines, "entity_description_vec", "Entity", "entity_description", "title", entity_embeddings)
    if text_unit_embeddings:
        _emit_vector_index(lines, "text_unit_text_vec", "TextUnit", "text_unit_text", "id", text_unit_embeddings)
    return "\n".join(lines) + "\n"


def _emit_schema_preamble(lines: list[str]) -> None:
    lines.append("// 1. Schema preamble (idempotent)")
    lines.append("CREATE CONSTRAINT entity_title_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.title IS UNIQUE;")
    lines.append("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type);")
    lines.append("CREATE CONSTRAINT text_unit_id_unique IF NOT EXISTS FOR (t:TextUnit) REQUIRE t.id IS UNIQUE;")
    lines.append("")


def _emit_entities(lines: list[str], entities: pd.DataFrame) -> None:
    lines.append(f"// 2. Entities ({len(entities)} rows)")
    rows = [_row_dict(row) for _, row in entities.iterrows()] if len(entities) else []
    for batch in _batches(rows):
        _emit_param_block(lines, batch)
        lines.append("MERGE (e:Entity {title: row.title})")
        lines.append("SET e += row;")
    lines.append("")


def _emit_relationships(lines: list[str], relationships: pd.DataFrame) -> None:
    lines.append(f"// 3. Relationships ({len(relationships)} rows)")
    rows: list[dict] = []
    for _, row in relationships.iterrows():
        d = _row_dict(row)
        source = d.pop("source", None)
        target = d.pop("target", None)
        rows.append({"source": source, "target": target, "_p": d})
    for batch in _batches(rows):
        _emit_param_block(lines, batch)
        lines.append("MATCH (s:Entity {title: row.source}), (t:Entity {title: row.target})")
        lines.append("MERGE (s)-[r:RELATED]->(t)")
        lines.append("SET r += row._p;")
    lines.append("")


# --- Stubs filled in by Tasks 2 and 3. ---------------------------------------
def _emit_text_units(lines, text_units, entities):  # pragma: no cover - Task 2
    raise NotImplementedError


def _emit_vector_index(lines, index_name, label, prop, key_col, embeddings):  # pragma: no cover - Task 3
    raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cypher_export.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/graph/cypher.py tests/test_cypher_export.py`
Expected: no errors (fix any ruff findings before committing).

- [ ] **Step 6: Commit**

```bash
git add kb_platform/graph/cypher.py tests/test_cypher_export.py
git commit -m "$(cat <<'EOF'
feat(graph): write_cypher core — schema, entities, relationships

Pure writer that emits an idempotent Cypher load script (MERGE / IF NOT
EXISTS). Every :param rows => RHS is valid JSON via json.dumps; numpy
scalars/ndarrays coerce to native int/float/list. Signature already
accepts text_units + embeddings, filled in by the next two tasks.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: TextUnit nodes + FROM_CHUNK edges

**Files:**
- Modify: `kb_platform/graph/cypher.py` (replace the `_emit_text_units` stub)
- Test: `tests/test_cypher_export.py`

**Interfaces:**
- Consumes: `write_cypher(...)` signature from Task 1 (unchanged).
- Produces: when `text_units` is non-None, the script emits `:TextUnit` nodes (`MERGE (t:TextUnit {id: row.id})`) and `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges derived from each entity's `text_unit_ids`. Orphan FROM_CHUNK rows (text_unit id not in the text_units set) are dropped at write time.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cypher_export.py`:

```python
def _text_units():
    return pd.DataFrame(
        [
            {"id": "c1", "text": "hello world", "document_ids": np.array(["d1"]), "n_tokens": 5},
            {"id": "c2", "text": "second chunk", "document_ids": np.array(["d1"]), "n_tokens": 2},
        ]
    )


def test_text_unit_nodes_emitted_when_supplied():
    s = write_cypher(_ents(), _rels(), text_units=_text_units())
    assert "MERGE (t:TextUnit {id: row.id})" in s
    assert "SET t += row" in s
    blocks = _param_blocks(s)
    tu_blocks = [b for b in blocks if '"id"' in b and '"text"' in b]
    assert tu_blocks
    row = json.loads(tu_blocks[0])[0]
    for col in ("id", "text", "document_ids", "n_tokens"):
        assert col in row
    assert row["document_ids"] == ["d1"]  # ndarray -> JSON array


def test_from_chunk_edges_connect_text_unit_to_entity():
    # entity A has text_unit_ids = [c1, c2]; both exist in _text_units()
    s = write_cypher(_ents(), _rels(), text_units=_text_units())
    assert "MATCH (t:TextUnit {id: row.tu_id}), (e:Entity {title: row.entity_title})" in s
    assert "MERGE (t)-[:FROM_CHUNK]->(e)" in s
    # find the FROM_CHUNK param blocks and confirm both c1, c2 are wired to A
    fc_blocks = [b for b in _param_blocks(s) if '"tu_id"' in b]
    assert fc_blocks
    pairs = [(r["tu_id"], r["entity_title"]) for b in fc_blocks for r in json.loads(b)]
    assert ("c1", "A") in pairs
    assert ("c2", "A") in pairs


def test_from_chunk_orphans_dropped():
    # entity references a text_unit id that is NOT in the text_units frame
    ents = pd.DataFrame([{"title": "A", "type": "X", "text_unit_ids": np.array(["c1", "ghost"])}])
    rels = pd.DataFrame(columns=["source", "target"])
    tus = pd.DataFrame([{"id": "c1", "text": "only", "document_ids": np.array([]), "n_tokens": 1}])
    s = write_cypher(ents, rels, text_units=tus)
    fc_blocks = [b for b in _param_blocks(s) if '"tu_id"' in b]
    pairs = [(r["tu_id"], r["entity_title"]) for b in fc_blocks for r in json.loads(b)]
    assert ("c1", "A") in pairs
    assert ("ghost", "A") not in pairs  # orphan dropped


def test_no_text_unit_section_when_none():
    s = write_cypher(_ents(), _rels())  # text_units default None
    assert ":TextUnit" not in s
    assert "FROM_CHUNK" not in s
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cypher_export.py -v`
Expected: the 4 new tests FAIL (`NotImplementedError` from the stub); the Task-1 tests still PASS.

- [ ] **Step 3: Replace the `_emit_text_units` stub**

In `kb_platform/graph/cypher.py`, replace:

```python
def _emit_text_units(lines, text_units, entities):  # pragma: no cover - Task 2
    raise NotImplementedError
```

with:

```python
def _emit_text_units(lines: list[str], text_units: pd.DataFrame, entities: pd.DataFrame) -> None:
    lines.append(f"// 4. Text units ({len(text_units)} rows)")
    valid_ids = set(text_units["id"].astype(str)) if "id" in text_units.columns else set()
    rows = [_row_dict(row) for _, row in text_units.iterrows()] if len(text_units) else []
    for batch in _batches(rows):
        _emit_param_block(lines, batch)
        lines.append("MERGE (t:TextUnit {id: row.id})")
        lines.append("SET t += row;")
    # FROM_CHUNK edges: (:TextUnit)-[:FROM_CHUNK]->(:Entity), derived from
    # each entity's text_unit_ids. Orphans (text_unit id absent from the
    # text_units set) are dropped at write time.
    from_chunk: list[dict] = []
    if "text_unit_ids" in entities.columns and "title" in entities.columns:
        for _, ent in entities.iterrows():
            title = _coerce(ent["title"])
            ids = ent["text_unit_ids"]
            if ids is None:
                continue
            id_list = ids.tolist() if hasattr(ids, "tolist") else list(ids)
            for tu_id in id_list:
                tu_id = str(tu_id)
                if tu_id in valid_ids:
                    from_chunk.append({"tu_id": tu_id, "entity_title": title})
    lines.append(f"// 4b. FROM_CHUNK edges ({len(from_chunk)} rows)")
    for batch in _batches(from_chunk):
        _emit_param_block(lines, batch)
        lines.append("MATCH (t:TextUnit {id: row.tu_id}), (e:Entity {title: row.entity_title})")
        lines.append("MERGE (t)-[:FROM_CHUNK]->(e);")
    lines.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cypher_export.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/graph/cypher.py tests/test_cypher_export.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/graph/cypher.py tests/test_cypher_export.py
git commit -m "$(cat <<'EOF'
feat(graph): cypher export — TextUnit nodes + FROM_CHUNK edges

Emits :TextUnit nodes (all columns as properties) and derives
(:TextUnit)-[:FROM_CHUNK]->(:Entity) edges from each entity's
text_unit_ids. Orphan references (text_unit id absent from the supplied
text_units frame) are dropped at write time.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Vector indexes + vector property load

**Files:**
- Modify: `kb_platform/graph/cypher.py` (replace the `_emit_vector_index` stub)
- Test: `tests/test_cypher_export.py`

**Interfaces:**
- Consumes: `write_cypher(...)` signature from Task 1 (unchanged).
- Produces: when an embedding dict is non-empty, the script emits `CREATE VECTOR INDEX <name> IF NOT EXISTS ... OPTIONS {indexConfig: {vector.dimensions: <detected>, vector.similarity_function: "cosine"}}` then batched `:param`/`UNWIND`/`MATCH`/`CALL db.create.setNodeVectorProperty(...)`. Dimensions are detected from the first vector. Empty/None embeddings → section skipped.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cypher_export.py`:

```python
def test_entity_vector_index_created_when_embeddings_present():
    emb = {"A": [0.1, 0.2, 0.3], "B": [0.4, 0.5, 0.6]}  # dim 3
    s = write_cypher(_ents(), _rels(), entity_embeddings=emb)
    assert "CREATE VECTOR INDEX entity_description_vec IF NOT EXISTS" in s
    assert "FOR (e:Entity) ON (e.entity_description)" in s
    assert "`vector.dimensions`: 3" in s
    assert '"cosine"' in s
    assert 'CALL db.create.setNodeVectorProperty(e, "entity_description", row.vec)' in s
    # the vec values land in param blocks as JSON arrays
    blocks = [b for b in _param_blocks(s) if '"vec"' in b]
    assert blocks
    parsed = json.loads(blocks[0])
    titles = {row["title"] for row in parsed}
    assert titles == {"A", "B"}


def test_text_unit_vector_index_uses_id_key():
    emb = {"c1": [0.1, 0.2]}
    s = write_cypher(_ents(), _rels(), text_unit_embeddings=emb)
    assert "CREATE VECTOR INDEX text_unit_text_vec IF NOT EXISTS" in s
    assert "FOR (t:TextUnit) ON (t.text_unit_text)" in s
    assert "`vector.dimensions`: 2" in s
    assert 'CALL db.create.setNodeVectorProperty(t, "text_unit_text", row.vec)' in s
    assert "MATCH (t:TextUnit {id: row.tu_id})" in s


def test_vector_section_skipped_when_embeddings_empty():
    s = write_cypher(_ents(), _rels(), entity_embeddings={}, text_unit_embeddings={})
    assert "CREATE VECTOR INDEX" not in s
    assert "setNodeVectorProperty" not in s


def test_vector_section_skipped_when_none():
    s = write_cypher(_ents(), _rels())  # both default None
    assert "CREATE VECTOR INDEX" not in s


def test_each_vector_param_rhs_is_valid_json():
    emb = {"A": [0.1, 0.2, 0.3]}
    s = write_cypher(_ents(), _rels(), entity_embeddings=emb)
    for rhs in _param_blocks(s):
        json.loads(rhs)  # floats must serialize cleanly
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cypher_export.py -v`
Expected: the 5 new tests FAIL (`NotImplementedError`); prior tests PASS.

- [ ] **Step 3: Replace the `_emit_vector_index` stub**

In `kb_platform/graph/cypher.py`, replace:

```python
def _emit_vector_index(lines, index_name, label, prop, key_col, embeddings):  # pragma: no cover - Task 3
    raise NotImplementedError
```

with:

```python
def _emit_vector_index(
    lines: list[str],
    index_name: str,
    label: str,
    prop: str,
    key_col: str,
    embeddings: dict[str, list[float]],
) -> None:
    """Emit CREATE VECTOR INDEX + batched setNodeVectorProperty.

    ``key_col`` is the node's identity property name used in the MATCH
    ("title" for Entity, "id" for TextUnit); the param-row key is always
    renamed to the Cypher bind variable expected by the MATCH. Dimensions
    are detected from the first vector; similarity is cosine.
    """
    sample = next(iter(embeddings.values()), None)
    if not sample:
        return
    dim = len(sample)
    var = "e" if label == "Entity" else "t"
    key_in_row = "title" if key_col == "title" else "tu_id"
    lines.append(f"// Vector index: {label}.{prop}")
    lines.append(f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS")
    lines.append(f"  FOR ({var}:{label}) ON ({var}.{prop})")
    lines.append(
        f"  OPTIONS {{ indexConfig: {{ `vector.dimensions`: {dim}, "
        f'`vector.similarity_function`: "cosine" }} }};'
    )
    rows = [{key_in_row: k, "vec": [float(x) for x in v]} for k, v in embeddings.items()]
    for batch in _batches(rows):
        _emit_param_block(lines, batch)
        match_key = "row.title" if key_col == "title" else "row.tu_id"
        lines.append(f"MATCH ({var}:{label} {{{key_col}: {match_key}}})")
        lines.append(f'CALL db.create.setNodeVectorProperty({var}, "{prop}", row.vec);')
    lines.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cypher_export.py -v`
Expected: all 17 tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check kb_platform/graph/cypher.py tests/test_cypher_export.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/graph/cypher.py tests/test_cypher_export.py
git commit -m "$(cat <<'EOF'
feat(graph): cypher export — vector indexes + vector property load

When embedding dicts are supplied, emits CREATE VECTOR INDEX (cosine,
dimensions detected from the first vector) and batched
db.create.setNodeVectorProperty calls for entity_description and
text_unit_text. Section is skipped entirely when embeddings are
empty/None, so the graph-only export still works.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Route — `format=cypher` + zip bundle + loaders

**Files:**
- Modify: `kb_platform/api/routes_export.py`
- Modify: `tests/test_api_export.py`

**Interfaces:**
- Consumes: `write_cypher(...)` from Task 1.
- Produces: `GET /kbs/{id}/export?format=cypher` returns `text/plain; charset=utf-8`; `?format=zip` now includes a `graph.cypher` entry alongside `graph.graphml`. New private loaders `_load_text_units(root)` and `_load_embeddings(root, index_name)`.

- [ ] **Step 1: Inspect the existing export test file**

Run: `sed -n '1,60p' tests/test_api_export.py`
Confirm the existing test style (how the route is invoked, how a KB/data_root is staged). Mirror that style in the new tests below.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api_export.py` (adjust the staging fixture to match what step 1 revealed — the snippets below assume the same helper the existing graphml/zip tests use to create a KB with parquet on disk; if the file uses a different helper, swap it in):

```python
def test_export_cypher_returns_text_plain_with_preamble(tmp_path):
    client, kb_id, root = _stage_kb_with_parquet(tmp_path)  # see note below
    resp = client.get(f"/kbs/{kb_id}/export?format=cypher")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "CREATE CONSTRAINT entity_title_unique" in resp.text


def test_export_zip_includes_graph_cypher(tmp_path):
    client, kb_id, root = _stage_kb_with_parquet(tmp_path)
    import io, zipfile

    resp = client.get(f"/kbs/{kb_id}/export?format=zip")
    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    names = archive.namelist()
    assert "graph.graphml" in names
    assert "graph.cypher" in names
    assert "CREATE CONSTRAINT entity_title_unique" in archive.read("graph.cypher").decode()


def test_export_cypher_missing_artifacts_still_returns_preamble(tmp_path):
    # a KB with no parquet at all: writer handles empty frames, route must not 500
    client, kb_id, root = _stage_empty_kb(tmp_path)
    resp = client.get(f"/kbs/{kb_id}/export?format=cypher")
    assert resp.status_code == 200
    assert "CREATE CONSTRAINT entity_title_unique" in resp.text


def test_export_unknown_format_returns_400(tmp_path):
    client, kb_id, root = _stage_kb_with_parquet(tmp_path)
    resp = client.get(f"/kbs/{kb_id}/export?format=bogus")
    assert resp.status_code == 400
```

If `_stage_kb_with_parquet` / `_stage_empty_kb` do not already exist, add them at module scope in `tests/test_api_export.py`, following the pattern the existing graphml/zip tests use. They must: create a KB row, write `entities.parquet` + `relationships.parquet` + `text_units.parquet` under its `data_root`, and return `(client, kb_id, root)`. Use the same `_ents()`/`_rels()` shapes the existing tests use, or the parquet fixtures already in the file.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_export.py -v`
Expected: the cypher/zip-cypher tests FAIL (no `format=cypher` branch yet; zip lacks `graph.cypher`).

- [ ] **Step 4: Implement loaders + route branches**

In `kb_platform/api/routes_export.py`, add the loaders near the existing `_load_*` helpers:

```python
def _load_text_units(root: Path) -> pd.DataFrame:
    path = root / "text_units.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["id", "text"])


def _load_embeddings(root: Path, index_name: str) -> dict[str, list[float]]:
    """Read all (id, vector) pairs from a LanceDB vector table.

    Returns {} if the table is absent (e.g., embeddings never generated).
    Uses the `lancedb` package directly (a transitive graphrag dep) because
    graphrag's vector-store API exposes similarity search, not bulk reads.
    """
    import lancedb

    db = lancedb.connect(str(root / "vectors"))
    if index_name not in db.table_names():
        return {}
    df = db.open_table(index_name).to_pandas()
    out: dict[str, list[float]] = {}
    for _, row in df.iterrows():
        rid = row.get("id")
        vec = row.get("vector")
        if rid is None or vec is None:
            continue
        out[str(rid)] = [float(x) for x in vec]
    return out
```

Update the `format` error message and add the `cypher` branch + zip entry. Replace the body of `export(...)` starting from the `format == "graphml"` check:

```python
    if format == "graphml":
        from kb_platform.graph.graphml import write_graphml

        xml = write_graphml(_load_entities(root), _load_relationships(root))
        return Response(content=xml, media_type="application/graphml+xml")

    if format == "cypher":
        from kb_platform.graph.cypher import write_cypher

        script = write_cypher(
            _load_entities(root),
            _load_relationships(root),
            text_units=_load_text_units(root),
            entity_embeddings=_load_embeddings(root, "entity_description"),
            text_unit_embeddings=_load_embeddings(root, "text_unit_text"),
        )
        return Response(content=script, media_type="text/plain; charset=utf-8")

    if format == "zip":
        from kb_platform.graph.cypher import write_cypher
        from kb_platform.graph.graphml import write_graphml

        entities = _load_entities(root)
        relationships = _load_relationships(root)
        text_units = _load_text_units(root)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in _PARQUET_ARTIFACTS:
                path = root / name
                if path.exists():
                    archive.write(path, name)
            archive.writestr("graph.graphml", write_graphml(entities, relationships))
            archive.writestr(
                "graph.cypher",
                write_cypher(
                    entities,
                    relationships,
                    text_units=text_units,
                    entity_embeddings=_load_embeddings(root, "entity_description"),
                    text_unit_embeddings=_load_embeddings(root, "text_unit_text"),
                ),
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=kb-{kb_id}.zip"},
        )

    raise HTTPException(status_code=400, detail="format must be one of: zip, graphml, cypher")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_export.py -v`
Expected: all tests PASS (new + existing graphml/zip).

- [ ] **Step 6: Lint**

Run: `uv run ruff check kb_platform/api/routes_export.py tests/test_api_export.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/api/routes_export.py tests/test_api_export.py
git commit -m "$(cat <<'EOF'
feat(api): GET /kbs/{id}/export?format=cypher + graph.cypher in zip

Adds the cypher export route: text/plain script for format=cypher, and a
graph.cypher entry (alongside graph.graphml) in the zip bundle. Loaders
read text_units parquet + both LanceDB vector tables (entity_description,
text_unit_text) directly via lancedb. Unknown format still 400.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Revise the cypher-export spec to reflect the extension

**Files:**
- Modify: `docs/superpowers/specs/2026-07-01-neo4j-cypher-export-design.md`

The graph-query spec declares this spec as its write-side dependency and lists 5 extension items. Fold them into the cypher-export spec so it is self-contained and authoritative.

- [ ] **Step 1: Open the spec**

Run: `sed -n '1,200p' docs/superpowers/specs/2026-07-01-neo4j-cypher-export-design.md`
(Or open in the editor.) Re-read the Scope, Graph model, Script structure, and Out-of-scope sections.

- [ ] **Step 2: Apply the edits**

Make these specific changes (each is a locate-and-replace in the editor):

1. **Status line** — change from `Approved (pending implementation plan)` to `Approved (implemented — see docs/superpowers/plans/2026-07-03-neo4j-cypher-export.md)`.

2. **Goal** — append one sentence:
   > The script also emits `:TextUnit` nodes, `:FROM_CHUNK` edges, and both vector indexes (`entity_description`, `text_unit_text`) so the resulting Neo4j database is a complete, portable GraphRAG backend (the read side is `2026-07-03-neo4j-graph-query-design.md`).

3. **Scope → In scope** — add three bullets:
   - `TextUnit` nodes (one per `text_units.parquet` row) and `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges derived from each entity's `text_unit_ids`.
   - Vector indexes + vector properties for `entity_description` and `text_unit_text` (read from LanceDB), loaded via `db.create.setNodeVectorProperty`.
   - `embeddings` loading via the `lancedb` package directly (bulk read of `id` + `vector` columns).

4. **Scope → Out of scope** — delete the line `- Vector embeddings (live in LanceDB, not in the parquet artifacts; separate concern).` Replace it with:
   - `Community / community_reports nodes (global/drift stay on parquet; not needed for the graph-query modes).`

5. **Graph model** — add a `:TextUnit` subsection:
   > **TextUnit nodes:** one `:TextUnit` label per text_units row, keyed by `id`. Each entity's `text_unit_ids` array becomes `(:TextUnit)-[:FROM_CHUNK]->(:Entity)` edges — the text units that mention each entity. Orphan references (text_unit id absent from the text_units set) are dropped at write time.

6. **Graph model** — add a Vectors subsection:
   > **Vectors:** when LanceDB embeddings are present, `CREATE VECTOR INDEX entity_description_vec` over `Entity.entity_description` (keyed by `title`) and `text_unit_text_vec` over `TextUnit.text_unit_text` (keyed by `id`). Similarity is cosine; dimensions are detected from the first vector. Properties are set via `db.create.setNodeVectorProperty` (Neo4j ≥ 5.11). When embeddings are absent, the vector section is omitted and the graph-only load still succeeds.

7. **Files** — update the writer entry to reflect the extended signature:
   - **New** `kb_platform/graph/cypher.py` — the writer `write_cypher(entities, relationships, text_units=None, entity_embeddings=None, text_unit_embeddings=None) -> str`.
   - **Edit** `kb_platform/api/routes_export.py` — add `format=cypher` branch; add `graph.cypher` to the zip bundle; add `_load_text_units` + `_load_embeddings` loaders.

8. **Risks / notes** — add:
   > **Neo4j ≥ 5.11** is now required (was 5.0) for `CREATE VECTOR INDEX` + `db.create.setNodeVectorProperty`. The header comment documents this.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-01-neo4j-cypher-export-design.md
git commit -m "$(cat <<'EOF'
docs(neo4j): revise cypher-export spec — TextUnit + vectors

Folds the graph-query spec's cross-spec dependency into the cypher-export
spec so it is self-contained: TextUnit nodes, FROM_CHUNK edges, and both
vector indexes (entity_description, text_unit_text). Neo4j floor moves
to 5.11. Status -> implemented.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage** — every requirement in the revised cypher-export spec maps to a task:
- Schema preamble + entity MERGE + relationship MERGE → Task 1
- TextUnit nodes + FROM_CHUNK edges → Task 2
- Vector indexes + setNodeVectorProperty → Task 3
- `format=cypher` route + zip bundle + loaders → Task 4
- Spec doc revision → Task 5

**Graph-query spec contract** (the read side's preconditions) is fully satisfied: `:Entity` (Task 1), `:RELATED` (Task 1), `:TextUnit` (Task 2), `:FROM_CHUNK` (Task 2), `entity_description_vec` (Task 3), `text_unit_text_vec` (Task 3), `entity_title_unique` + `entity_type` (Task 1 preamble).

**Placeholder scan** — no TBD/TODO; every code step shows full code. The one "see note below" in Task 4 step 2 (`_stage_kb_with_parquet`) instructs the implementer to mirror the existing test staging helper rather than duplicate it — this is intentional (the helper already exists in `test_api_export.py`) and is grounded by the step-1 inspection command.

**Type consistency** — `write_cypher` signature is identical across Tasks 1–4. `_emit_vector_index`'s `key_col` / `key_in_row` / `match_key` mapping (title→title→row.title, id→tu_id→row.tu_id) is consistent between Task 3 implementation and Task 3 tests. LanceDB column names (`id`, `vector`) match graphrag_vectors' defaults (verified: `id_field="id"`, `vector_field="vector"`).
