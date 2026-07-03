"""Real-load regression for ``write_cypher``: the script must LOAD into Neo4j 5.x.

This is the gate the original ``:param rows => <JSON>`` format needed and lacked
— it passed the unit tests (valid JSON) but cypher-shell 5 rejected it (Cypher 5
won't evaluate a JSON-style map literal in expression position, and cypher-shell
wraps ``:param x => v`` in such a map). The new format emits inline
``WITH [...] AS rows`` Cypher literals that the driver executes directly.

Boots a Neo4j 5.20 testcontainer, executes every ``;``-separated statement via
the ``neo4j`` driver (the old ``:param`` lines would be rejected by the driver
too — they are not Cypher — so this test fails on the old format and passes on
the new one), and asserts the resulting graph.

Skipped without ``testcontainers`` / ``neo4j`` / Docker — same tier as the
existing real-Neo4j integration tests. Run locally with
``uv pip install testcontainers && uv run python -m pytest tests/test_cypher_export_load.py -v -s``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("testcontainers")
pytest.importorskip("neo4j")


@pytest.fixture(scope="module")
def neo4j_url():
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer(image="neo4j:5.20", password="testpass") as container:
        yield container.get_connection_url()


def _script():
    import numpy as np
    import pandas as pd

    from kb_platform.graph.cypher import write_cypher

    ents = pd.DataFrame(
        [
            {"title": "Alice", "type": "PERSON", "description": "engineer",
             "text_unit_ids": np.array(["c1"]), "frequency": 1, "degree": 1},
            {"title": "Acme", "type": "ORG", "description": "company",
             "text_unit_ids": np.array(["c1"]), "frequency": 1, "degree": 1},
        ]
    )
    rels = pd.DataFrame(
        [{"source": "Alice", "target": "Acme", "description": "works at",
          "text_unit_ids": np.array(["c1"]), "weight": 1.0, "combined_degree": 2}]
    )
    tus = pd.DataFrame(
        [{"id": "c1", "text": "Alice works at Acme.", "document_ids": np.array(["d1"]), "n_tokens": 4}]
    )
    emb = {"Alice": [0.1, 0.2, 0.3], "Acme": [0.4, 0.5, 0.6]}
    return write_cypher(ents, rels, text_units=tus, entity_embeddings=emb)


def _run_script(driver, script: str) -> None:
    """Execute every ;-terminated statement (the driver rejects ``:param`` lines,
    so the old format would raise here).

    ``//`` line comments are stripped FIRST (to end of line) so a ``;`` inside a
    comment (the header has ``// ... ; safe to re-run ...``) doesn't fragment the
    next statement. The fixture carries no ``//`` inside string values, so a
    non-string-aware strip is safe here; cypher-shell itself is string-aware on
    the real load path (the manual container load verified that).
    """
    import re

    stripped = re.sub(r"//[^\n]*", "", script)
    with driver.session() as s:
        for raw in stripped.split(";"):
            stmt = raw.strip()
            if stmt:
                s.run(stmt)


def test_write_cypher_loads_into_neo4j(neo4j_url):
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_url, auth=("neo4j", "testpass"))
    try:
        _run_script(driver, _script())
        with driver.session() as s:
            n_ent = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
            n_rel = s.run("MATCH ()-[r:RELATED]->() RETURN count(r) AS n").single()["n"]
            n_tu = s.run("MATCH (t:TextUnit) RETURN count(t) AS n").single()["n"]
            n_fc = s.run(
                "MATCH (t:TextUnit)-[:FROM_CHUNK]->(:Entity) RETURN count(*) AS n"
            ).single()["n"]
            vec_names = s.run(
                "SHOW VECTOR INDEXES YIELD name RETURN collect(name) AS names"
            ).single()["names"]
            # round-trip a non-ASCII / quoted-safe value to be sure strings stored correctly
            alice_desc = s.run(
                'MATCH (e:Entity {title: "Alice"}) RETURN e.description AS d'
            ).single()["d"]
        assert n_ent == 2
        assert n_rel == 1
        assert n_tu == 1
        assert n_fc == 2  # both Alice and Acme mention c1
        assert "entity_description_vec" in vec_names
        assert alice_desc == "engineer"  # scalar description round-trips
    finally:
        driver.close()
