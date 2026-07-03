from kb_platform.api.models import QueryParamsIn, QueryResultOut
from kb_platform.query.engine import QueryParams, StreamDone, StreamMeta
from kb_platform.query.params import resolve_query_params


def test_query_params_defaults_none():
    p = QueryParams()
    assert p.hops is None and p.cypher_timeout_ms is None


def test_stream_done_truncated_defaults_false():
    d = StreamDone(method="cypher", answer="x")
    assert d.truncated is False


def test_stream_meta_carries_cypher():
    m = StreamMeta(cypher="MATCH (n) RETURN n")
    assert m.cypher == "MATCH (n) RETURN n"


def test_resolve_includes_new_fields():
    # KB defaults layer
    resolved = resolve_query_params({"query_defaults": {"hops": 3}}, QueryParams(cypher_timeout_ms=8000))
    assert resolved.hops == 3
    assert resolved.cypher_timeout_ms == 8000


def test_query_params_in_accepts_new_fields():
    p = QueryParamsIn(hops=2, cypher_timeout_ms=12000)
    assert p.hops == 2 and p.cypher_timeout_ms == 12000


def test_query_result_out_has_truncated():
    out = QueryResultOut(answer="a", method="cypher")
    assert out.truncated is False
