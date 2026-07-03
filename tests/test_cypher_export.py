"""Tests for the Cypher export writer.

The writer emits row data INLINE as Cypher-native literals
(``WITH [{title: "A", ...}, ...] AS rows UNWIND rows AS row ...``) — NOT via
cypher-shell's ``:param`` directive (Cypher 5 rejects JSON-style quoted-key
map literals in expression position, and cypher-shell wraps ``:param x => v``
in such a map, so object-list payloads never loaded). These tests pin the
emitted format; the real load-into-Neo4j gate lives in ``test_cypher_export_load.py``.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from kb_platform.graph.cypher import write_cypher

_WITH_RE = re.compile(r"^WITH (.+) AS rows\s*$")


def _with_blocks(script: str) -> list[str]:
    """Return the literal text of every ``WITH <list> AS rows`` line."""
    return [m.group(1) for line in script.splitlines() if (m := _WITH_RE.match(line))]


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


# --- format: no cypher-shell :param, inline WITH/UNWIND rows ------------------
def test_no_param_directive_or_dollar_rows():
    s = write_cypher(_ents(), _rels())
    assert ":param" not in s
    assert "$rows" not in s
    assert "UNWIND rows AS row" in s
    assert _with_blocks(s), "expected at least one WITH <list> AS rows block"


def test_with_blocks_use_identifier_keys_not_json_quoted():
    s = write_cypher(_ents(), _rels())
    for b in _with_blocks(s):
        # JSON-style quoted keys (": ") must NOT appear — Cypher 5 rejects them.
        assert '": ' not in b, f"quoted JSON key in Cypher literal: {b[:80]}"
    # identifier keys DO appear
    assert any("title: " in b for b in _with_blocks(s))


def test_preamble_present():
    s = write_cypher(_ents(), _rels())
    assert "CREATE CONSTRAINT entity_title_unique IF NOT EXISTS" in s
    assert "FOR (e:Entity) REQUIRE e.title IS UNIQUE" in s
    assert "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)" in s
    assert "cypher-shell" in s  # header usage comment


# --- entities ---------------------------------------------------------------
def test_every_entity_column_is_a_property():
    s = write_cypher(_ents(), _rels())
    # entity MERGE on title, SET += row carries every column
    assert "MERGE (e:Entity {title: row.title})" in s
    assert "SET e += row" in s
    blocks = _with_blocks(s)
    # the entity block is the one carrying title + type together
    ent_blocks = [b for b in blocks if "title: " in b and "type: " in b]
    assert ent_blocks, "no entity WITH block found"
    eb = ent_blocks[0]
    for col in ("title", "type", "description", "text_unit_ids", "frequency", "degree"):
        assert f"{col}: " in eb, f"column {col!r} missing from entity row"


def test_list_columns_are_arrays_not_flattened():
    s = write_cypher(_ents(), _rels())
    # the ndarray must NOT appear as its Python repr
    assert "['c1'" not in s
    # it appears as a Cypher list literal with identifier key
    assert 'text_unit_ids: ["c1", "c2"]' in s


def test_strings_with_quotes_and_backslashes_escaped():
    ents = pd.DataFrame([{"title": 'a"b\\c', "type": "X", "description": "d\ne"}])
    rels = pd.DataFrame([{"source": "x", "target": "y", "description": "z"}])
    s = write_cypher(ents, rels)
    # json.dumps-style escaping inside a Cypher string literal
    assert '"title":' not in s  # still identifier keys
    assert 'title: "a\\"b\\\\c"' in s
    assert 'description: "d\\ne"' in s


def test_empty_dataframes_do_not_crash():
    s = write_cypher(
        pd.DataFrame(columns=["title"]),
        pd.DataFrame(columns=["source", "target"]),
    )
    assert "CREATE CONSTRAINT entity_title_unique" in s  # preamble still emitted
    # no data rows -> no WITH blocks emitted
    assert _with_blocks(s) == []


def test_numpy_scalars_become_native():
    ents = pd.DataFrame(
        [{"title": "A", "type": "X", "frequency": np.int64(7), "degree": np.int64(3)}]
    )
    s = write_cypher(ents, pd.DataFrame(columns=["source", "target"]))
    ent_blocks = [b for b in _with_blocks(s) if "title: " in b]
    assert ent_blocks
    eb = ent_blocks[0]
    # native int rendering (no np.int64(...) repr)
    assert "frequency: 7" in eb
    assert "degree: 3" in eb
    assert "np." not in eb and "int64" not in eb


# --- relationships ----------------------------------------------------------
def test_every_relationship_column_except_endpoints_in_p():
    s = write_cypher(_ents(), _rels())
    assert "MATCH (s:Entity {title: row.source}), (t:Entity {title: row.target})" in s
    assert "MERGE (s)-[r:RELATED]->(t)" in s
    assert "SET r += row._p" in s
    rel_blocks = [b for b in _with_blocks(s) if "source: " in b and "_p: " in b]
    assert rel_blocks
    rb = rel_blocks[0]
    # endpoints at the row root; everything else under _p
    assert "source: " in rb and "target: " in rb
    assert "_p: {" in rb
    for col in ("description", "text_unit_ids", "weight", "combined_degree"):
        assert f"{col}: " in rb, f"column {col!r} missing from relationship _p"
    # float renders as 8.0 (repr), not 8
    assert "weight: 8.0" in rb


# --- text units + FROM_CHUNK ------------------------------------------------
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
    tu_blocks = [b for b in _with_blocks(s) if "id: " in b and "text: " in b]
    assert tu_blocks
    tb = tu_blocks[0]
    for col in ("id", "text", "document_ids", "n_tokens"):
        assert f"{col}: " in tb
    # ndarray -> Cypher list
    assert "document_ids: [\"d1\"]" in tb


def test_from_chunk_edges_connect_text_unit_to_entity():
    # entity A has text_unit_ids = [c1, c2]; both exist in _text_units()
    s = write_cypher(_ents(), _rels(), text_units=_text_units())
    assert "MATCH (t:TextUnit {id: row.tu_id}), (e:Entity {title: row.entity_title})" in s
    assert "MERGE (t)-[:FROM_CHUNK]->(e)" in s
    fc_blocks = [b for b in _with_blocks(s) if "tu_id: " in b]
    assert fc_blocks
    # both (c1, A) and (c2, A) pairs are present in the FROM_CHUNK data
    joined = " ".join(fc_blocks)
    assert 'tu_id: "c1"' in joined and 'entity_title: "A"' in joined
    assert 'tu_id: "c2"' in joined


def test_from_chunk_orphans_dropped():
    # entity references a text_unit id that is NOT in the text_units frame
    ents = pd.DataFrame([{"title": "A", "type": "X", "text_unit_ids": np.array(["c1", "ghost"])}])
    rels = pd.DataFrame(columns=["source", "target"])
    tus = pd.DataFrame([{"id": "c1", "text": "only", "document_ids": np.array([]), "n_tokens": 1}])
    s = write_cypher(ents, rels, text_units=tus)
    fc_blocks = [b for b in _with_blocks(s) if "tu_id: " in b]
    joined = " ".join(fc_blocks)
    assert 'tu_id: "c1"' in joined
    assert "ghost" not in joined  # orphan dropped


def test_no_text_unit_section_when_none():
    s = write_cypher(_ents(), _rels())  # text_units default None
    assert ":TextUnit" not in s
    assert "FROM_CHUNK" not in s


# --- vector indexes + vector property load ----------------------------------
def test_entity_vector_index_created_when_embeddings_present():
    emb = {"A": [0.1, 0.2, 0.3], "B": [0.4, 0.5, 0.6]}  # dim 3
    s = write_cypher(_ents(), _rels(), entity_embeddings=emb)
    assert "CREATE VECTOR INDEX entity_description_vec IF NOT EXISTS" in s
    assert "FOR (e:Entity) ON (e.entity_description)" in s
    assert "`vector.dimensions`: 3" in s
    assert '"cosine"' in s
    assert 'CALL db.create.setNodeVectorProperty(e, "entity_description", row.vec)' in s
    # the vec values land in WITH blocks as Cypher lists keyed by title
    vec_blocks = [b for b in _with_blocks(s) if "vec: " in b]
    assert vec_blocks
    joined = " ".join(vec_blocks)
    assert 'title: "A"' in joined and 'title: "B"' in joined


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


def test_each_with_block_is_balanced():
    # structural sanity: every WITH <list> AS rows block has balanced [] and {}
    s = write_cypher(_ents(), _rels(), text_units=_text_units(),
                     entity_embeddings={"A": [0.1, 0.2, 0.3]})
    for b in _with_blocks(s):
        assert b.count("[") == b.count("]"), f"unbalanced [] in: {b[:80]}"
        assert b.count("{") == b.count("}"), f"unbalanced {{}} in: {b[:80]}"
