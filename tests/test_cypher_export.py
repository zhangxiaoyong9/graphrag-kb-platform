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
