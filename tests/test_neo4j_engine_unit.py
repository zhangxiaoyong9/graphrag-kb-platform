"""Pure helpers in neo4j_engine: prompt builder, hybrid Cypher template, formatter."""

from types import SimpleNamespace

from kb_platform.neo4j import driver_pool
from kb_platform.query.engine import StreamDelta, StreamDone, StreamMeta
from kb_platform.query.neo4j_engine import (
    Neo4jQueryEngine,
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


# --- Neo4jQueryEngine (fake-driven) -----------------------------------------


class _FakeUsage(SimpleNamespace):
    pass


class _FakeCompletion:
    """Mimics NativeCompletion's completion_async for both stream modes."""

    def __init__(self, cypher_text: str, answer_words: list[str]):
        self._cypher = cypher_text
        self._words = answer_words

    async def completion_async(self, /, *, messages, stream=False, **_kw):
        if not stream:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self._cypher))],
                usage=_FakeUsage(prompt_tokens=12, completion_tokens=3),
            )

        async def _gen():
            for i, w in enumerate(self._words):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=w + " "))],
                    usage=None,
                )
            # final chunk carries usage
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=None))],
                usage=_FakeUsage(prompt_tokens=50, completion_tokens=20),
            )

        return _gen()


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def __aiter__(self):
        rows = list(self._rows)
        async def _g():
            for r in rows:
                yield SimpleNamespace(data=lambda r=r: r)
        return _g()


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def run(self, cypher, parameters=None, timeout=None):
        return _FakeResult(self._rows)

    async def close(self):
        pass


class _FakeDriver:
    def __init__(self, rows):
        self._rows = rows

    def session(self, database=None):
        return _FakeSession(self._rows)


async def _events(engine, method, query, rows, words=None, cypher_text=None):
    completion = _FakeCompletion(cypher_text or "MATCH (n:Entity) RETURN n LIMIT 5", words or ["A", "B"])
    engine._completion = completion
    # patch the pool to return a fake driver with the given rows
    driver_pool._reset_for_test()
    engine._pool = SimpleNamespace(get_driver=lambda *a, **kw: _FakeDriver(rows))
    out = []
    async for ev in engine.stream_search(method, query, "/tmp/none"):
        out.append(ev)
    return out


def _engine():
    # Resolution #2: read-only cypher that passes the L1 gate, and words whose
    # streamed concatenation is "A B " (matches test_search_accumulates_stream).
    return Neo4jQueryEngine(
        uri="bolt://x", username="u", password="p",
        driver_pool=driver_pool, completion=_FakeCompletion("MATCH (n:Entity) RETURN n LIMIT 5", ["A", "B"]),
        embed=None, model_id="gpt-4o-mini",
    )


async def test_cypher_emits_meta_then_deltas_then_done():
    eng = _engine()
    evs = await _events(eng, "cypher", "how many orgs?",
                        rows=[{"title": "A", "type": "ORG"}, {"title": "B", "type": "ORG"}],
                        words=["two", "orgs"])
    assert isinstance(evs[0], StreamMeta)
    assert "MATCH" in evs[0].cypher
    deltas = [e for e in evs if isinstance(e, StreamDelta)]
    assert [d.text for d in deltas] == ["two ", "orgs "]
    done = evs[-1]
    assert isinstance(done, StreamDone)
    assert done.answer == "two orgs "
    assert done.method == "cypher"
    assert done.truncated is False
    assert done.prompt_tokens and done.output_tokens


async def test_cypher_l1_rejection_yields_error():
    eng = _engine()
    evs = await _events(eng, "cypher", "delete everything",
                        rows=[], cypher_text="MATCH (n) DETACH DELETE n")
    assert isinstance(evs[0], StreamMeta)
    done = evs[-1]
    assert isinstance(done, StreamDone)
    assert done.error and "read-only" in done.error


async def test_cypher_row_cap_truncates():
    eng = _engine()
    rows = [{"title": f"e{i}"} for i in range(50)]
    # force a tiny cap via monkeypatching the module ROW_CAP
    import kb_platform.query.neo4j_engine as mod

    orig = mod.ROW_CAP
    mod.ROW_CAP = 10
    try:
        evs = await _events(eng, "cypher", "list", rows=rows, words=["x"])
    finally:
        mod.ROW_CAP = orig
    done = evs[-1]
    assert isinstance(done, StreamDone) and done.truncated is True


async def test_hybrid_emits_templated_cypher_and_answer():
    async def embed(text):
        return [0.1, 0.2, 0.3]

    eng = Neo4jQueryEngine(
        uri="bolt://x", username="u", password="p",
        driver_pool=driver_pool, completion=_FakeCompletion("ignored", ["ans"]),
        embed=embed, model_id="gpt-4o-mini",
    )
    # one record with the three bags
    rows = [{"entities": [{"title": "A"}], "relationships": [], "chunks": [{"id": "c1", "text": "hi"}]}]
    evs = await _events(eng, "hybrid", "who is A?", rows=rows, words=["A", "rocks"])
    assert isinstance(evs[0], StreamMeta)
    assert "entity_description_vec" in evs[0].cypher
    done = evs[-1]
    assert isinstance(done, StreamDone)
    assert done.answer == "A rocks "
    assert done.method == "hybrid"


async def test_unsupported_method_errors():
    eng = _engine()
    evs = [e async for e in eng.stream_search("local", "q", "/tmp/none")]
    assert isinstance(evs[-1], StreamDone)
    assert evs[-1].error and "local" in evs[-1].error


async def test_search_accumulates_stream():
    eng = _engine()
    eng._pool = SimpleNamespace(get_driver=lambda *a, **kw: _FakeDriver([{"title": "A"}]))
    res = await eng.search("cypher", "how many", "/tmp/none")
    assert res.method == "cypher"
    assert res.answer == "A B "
