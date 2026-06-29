from kb_platform.query.engine import QueryParams
from kb_platform.query.params import resolve_query_params


def test_all_none_when_nothing_set():
    assert resolve_query_params({}, None) == QueryParams()


def test_kb_defaults_used_when_no_per_query():
    kb = {"query_defaults": {"community_level": 1, "temperature": 0.3}}
    p = resolve_query_params(kb, None)
    assert p.community_level == 1 and p.temperature == 0.3


def test_per_query_overrides_kb():
    kb = {"query_defaults": {"community_level": 1, "response_type": "single paragraph"}}
    p = resolve_query_params(kb, QueryParams(community_level=3))
    assert p.community_level == 3
    assert p.response_type == "single paragraph"


def test_per_query_partial_only_overrides_set_fields():
    p = resolve_query_params({}, QueryParams(top_k=12))
    assert p.top_k == 12 and p.community_level is None


def test_missing_query_defaults_bucket_is_ok():
    assert resolve_query_params({"chunking": {"size": 1}}, None) == QueryParams()
