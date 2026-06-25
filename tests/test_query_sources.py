"""QueryResult enrichment: SourceRef + optional elapsed/tokens/sources."""
import pandas as pd

from kb_platform.query.engine import QueryResult, SourceRef
from kb_platform.query.graphrag_engine import GraphRagQueryEngine


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


def _engine():
    return GraphRagQueryEngine(data_root=".", model_config={})


def test_extract_sources_entities_and_text():
    ctx = {
        "entities": pd.DataFrame(
            [{"name": "宁德时代", "description": "电池厂商"}, {"name": "特斯拉", "description": "车厂"}]
        ),
        "text units": pd.DataFrame([{"id": 1, "text": "供货协议片段"}]),
    }
    out = _engine()._extract_sources(ctx, "local")
    kinds = {s.kind for s in out}
    names = {s.name for s in out if s.kind == "entity"}
    assert "entity" in kinds and "宁德时代" in names
    assert any(s.kind == "text_unit" and "供货协议" in s.text for s in out)


def test_extract_sources_basic_single_key():
    ctx = {"text units": pd.DataFrame([{"id": 7, "text": "一段正文"}])}
    out = _engine()._extract_sources(ctx, "basic")
    assert out and out[0].kind == "text_unit" and "一段正文" in out[0].text


def test_extract_sources_degrades_on_none():
    assert _engine()._extract_sources(None, "local") is None
    assert _engine()._extract_sources("   ", "local") is None


def test_extract_sources_caps_text_snippet():
    long = "x" * 500
    ctx = {"sources": pd.DataFrame([{"id": 1, "text": long}])}
    out = _engine()._extract_sources(ctx, "basic")
    assert len(out[0].text) <= 200
