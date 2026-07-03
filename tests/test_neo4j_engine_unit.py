"""Pure helpers in neo4j_engine: prompt builder, hybrid Cypher template, formatter."""

from kb_platform.query.neo4j_engine import (
    build_hybrid_cypher,
    build_text2cypher_messages,
    format_rows_as_context,
)


# --- build_text2cypher_messages --------------------------------------------
def test_prompt_is_system_plus_user():
    msgs = build_text2cypher_messages("how many ORGs?")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"


def test_prompt_carries_schema_and_question():
    msgs = build_text2cypher_messages("how many ORGs?")
    sys = msgs[0]["content"]
    # canonical schema pieces the LLM needs
    assert ":Entity" in sys and "title" in sys
    assert ":RELATED" in sys
    assert ":TextUnit" in sys and ":FROM_CHUNK" in sys
    # few-shot guidance is present
    assert "MATCH" in sys
    # the question is in the user turn verbatim
    assert "how many ORGs?" in msgs[1]["content"]


def test_prompt_instructs_readonly_return():
    msgs = build_text2cypher_messages("x")
    sys = msgs[0]["content"]
    assert "RETURN" in sys
    # steer the model away from writes
    assert "read-only" in sys.lower() or "read only" in sys.lower()


# --- build_hybrid_cypher ----------------------------------------------------
def test_hybrid_cypher_uses_entity_vector_index():
    s = build_hybrid_cypher(top_k=10, hops=2)
    assert "db.index.vector.queryNodes('entity_description_vec'" in s
    assert "$vector" in s  # the embedding stays a real parameter


def test_hybrid_cypher_bakes_topk_and_hops_as_literals():
    s = build_hybrid_cypher(top_k=7, hops=3)
    # top_k as the k argument to vector ANN
    assert ", 7," in s
    # variable-length path bound is the hops literal
    assert "*1..3" in s or "*0..3" in s


def test_hybrid_cypher_returns_three_bags():
    s = build_hybrid_cypher(top_k=10, hops=2)
    assert "RETURN entities" in s
    assert "relationships" in s
    assert "chunks" in s


def test_hybrid_cypher_traverses_related_and_from_chunk():
    s = build_hybrid_cypher(top_k=10, hops=2)
    assert ":RELATED" in s
    assert ":FROM_CHUNK" in s
    assert ":TextUnit" in s


# --- format_rows_as_context -------------------------------------------------
def test_format_rows_renders_each_row_as_a_line():
    rows = [{"title": "A", "type": "ORG"}, {"title": "B", "type": "PERSON"}]
    out = format_rows_as_context(rows)
    assert "A" in out and "B" in out
    assert out.count("\n") >= 1


def test_format_rows_empty():
    assert format_rows_as_context([]) == ""
