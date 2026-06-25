"""QueryResult enrichment: SourceRef + optional elapsed/tokens/sources."""
from kb_platform.query.engine import QueryResult, SourceRef


def test_query_result_defaults():
    r = QueryResult(answer="a", method="local")
    assert r.error is None
    assert r.elapsed_ms is None
    assert r.prompt_tokens is None
    assert r.output_tokens is None
    assert r.llm_calls is None
    assert r.sources is None


def test_source_ref():
    s = SourceRef(kind="entity", name="宁德时代", text="电池厂商")
    assert s.kind == "entity"
    assert s.name == "宁德时代"
