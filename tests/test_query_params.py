from kb_platform.query.engine import FakeQueryEngine, QueryParams


def test_query_params_all_default_none():
    p = QueryParams()
    assert p.community_level is None
    assert p.response_type is None
    assert p.top_k is None
    assert p.temperature is None
    assert p.system_prompt is None


async def test_fake_engine_stream_accepts_params():
    eng = FakeQueryEngine()
    out = [e async for e in eng.stream_search("local", "q", "/tmp", QueryParams(community_level=1))]
    assert out and out[-1].method == "local"


async def test_fake_engine_search_accepts_params():
    eng = FakeQueryEngine()
    res = await eng.search("global", "q", "/tmp", QueryParams(temperature=0.3))
    assert res.method == "global"
