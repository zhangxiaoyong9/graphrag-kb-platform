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
