"""NativeCompletion/NativeEmbedding built from a kb_profiles bundle, without
graphrag-llm's factory. The built completion's gateway is exercised via the
_build_driver-style seam to avoid real network."""

from kb_platform.llm.native_builders import (
    build_native_completion,
    build_native_embedding,
)


def _bundle():
    return [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "api_version": None,
            "keys": ["sk-test"],
            "ssl_verify": True,
        }
    ]


def test_build_native_completion_reads_kb_profiles_into_gateway():
    c = build_native_completion(model_id="gpt-4o-mini", kb_profiles=_bundle())
    # NativeCompletion exposes ._gateway; its profiles came from the bundle
    profs = c._gateway._profiles
    assert len(profs) == 1
    assert profs[0].provider == "openai"
    assert profs[0].model == "gpt-4o-mini"
    assert profs[0].key == "sk-test"


def test_build_native_embedding_reads_first_profile():
    e = build_native_embedding(model_id="text-embedding-3-small", kb_profile=_bundle()[0])
    assert e._profile.provider == "openai"
    assert e._profile.model == "text-embedding-3-small"
    assert e._keys == ["sk-test"]


def test_build_native_completion_passes_stub_model_config():
    # the stub must expose .model_extra (the only attr NativeCompletion reads)
    from kb_platform.llm.native_builders import _model_config_stub

    stub = _model_config_stub(_bundle())
    assert stub.model_extra == {"kb_profiles": _bundle()}


async def test_native_embed_async_uses_callers_loop():
    """embed_async must run on the caller's loop, not a throwaway one.

    Regression: the sync ``embedding()`` uses ``asyncio.run``, which spins up a
    throwaway loop; if the shared ``httpx.AsyncClient`` is first exercised there
    it binds to that loop and any later async use raises
    ``... bound to a different event loop``. ``embed_async`` awaits the async
    entry directly, so the client stays on the caller's loop (verified by a
    second call in the same loop succeeding).
    """
    import httpx
    from types import SimpleNamespace

    from kb_platform.llm.embedding import NativeEmbedding

    def handler(request):
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3]}], "usage": {"total_tokens": 5}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = NativeEmbedding(
        model_id="m",
        model_config=SimpleNamespace(
            model_extra={"kb_profiles": [{"provider": "openai", "model": "m", "keys": ["k"]}]}
        ),
        client=client,
    )
    try:
        assert await emb.embed_async("hello") == [0.1, 0.2, 0.3]
        # reusable in the same loop — the sync .embedding()+asyncio.run path would
        # bind the client to a throwaway loop and break this second call.
        assert await emb.embed_async("again") == [0.1, 0.2, 0.3]
    finally:
        await client.aclose()

