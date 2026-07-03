"""L1 read-only Cypher validator + L2 row-cap helper (pure functions)."""

from kb_platform.neo4j.safety import is_readonly_cypher, truncate_rows


# --- is_readonly_cypher: positives (admit) ----------------------------------
def test_admits_plain_match_return():
    assert is_readonly_cypher("MATCH (n) RETURN n LIMIT 10")


def test_admits_profile_explain_show():
    assert is_readonly_cypher("PROFILE MATCH (n) RETURN n")
    assert is_readonly_cypher("EXPLAIN MATCH (n) RETURN n")
    assert is_readonly_cypher("SHOW INDEXES")


def test_admits_leading_whitespace_and_comments():
    assert is_readonly_cypher("   // hi\n   MATCH (n) RETURN n")
    assert is_readonly_cypher("\nMATCH (n)\nRETURN n\n")


def test_admits_multi_statement_all_readonly():
    s = "MATCH (a) RETURN a; MATCH (b) RETURN b;"
    assert is_readonly_cypher(s)


# --- is_readonly_cypher: negatives (reject) ---------------------------------
def test_rejects_create_merge_delete_drop_set_remove():
    for s in [
        "CREATE (n:Entity {title:'x'})",
        "MERGE (n:Entity {title:'x'})",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "DROP INDEX x",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) REMOVE n.x",
    ]:
        assert not is_readonly_cypher(s), s


def test_rejects_load_csv():
    assert not is_readonly_cypher(
        "LOAD CSV WITH HEADERS FROM 'https://evil/x.csv' AS row CREATE (n:X)"
    )


def test_rejects_write_after_readonly_prefix():
    # the validator must scan EVERY statement, not just the first
    assert not is_readonly_cypher("MATCH (n) RETURN n; CREATE (n)")


def test_rejects_call_to_write_procedure():
    assert not is_readonly_cypher("CALL apoc.create.node(['X'], {}) YIELD node RETURN node")


def test_admits_call_to_read_procedure():
    # vector ANN is a read CALL; admit it
    assert is_readonly_cypher(
        "CALL db.index.vector.queryNodes('entity_description_vec', 10, $v) "
        "YIELD node, score RETURN node, score"
    )


# --- truncate_rows ----------------------------------------------------------
def test_truncate_under_cap_returns_all_not_truncated():
    rows = [{"a": i} for i in range(5)]
    out, truncated = truncate_rows(rows, 10)
    assert out == rows and truncated is False


def test_truncate_at_cap_returns_all_not_truncated():
    rows = [{"a": i} for i in range(10)]
    out, truncated = truncate_rows(rows, 10)
    assert len(out) == 10 and truncated is False


def test_truncate_over_cap_returns_cap_and_flag():
    rows = [{"a": i} for i in range(50)]
    out, truncated = truncate_rows(rows, 10)
    assert len(out) == 10 and truncated is True
    assert out[0] == {"a": 0} and out[-1] == {"a": 9}
