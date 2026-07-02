"""Registry + bootstrap tests."""

from kb_platform.llm.registry import register_native, NATIVE_TYPE


def test_register_native_registers_completion_and_embedding():
    register_native()  # idempotent
    from graphrag_llm.completion.completion_factory import completion_factory
    from graphrag_llm.embedding.embedding_factory import embedding_factory

    assert NATIVE_TYPE in completion_factory  # registry exposes membership
    assert NATIVE_TYPE in embedding_factory


def test_register_native_is_idempotent():
    register_native()
    register_native()  # second call must be a no-op (no error, still registered)


def test_bootstrap_registers_native():
    from kb_platform.llm.bootstrap import bootstrap

    bootstrap()
    register_native()  # ensure registered
    from graphrag_llm.completion.completion_factory import completion_factory

    assert NATIVE_TYPE in completion_factory
