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
