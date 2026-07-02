"""Register NativeCompletion + NativeEmbedding into graphrag-llm's factories.

graphrag-llm's create_completion/create_embedding dispatch on ModelConfig.type;
pre-registering our type bypasses the LiteLLM branch entirely (and the
type validator only fires for type=LiteLLM). Idempotent.
"""

from __future__ import annotations

from graphrag_llm.completion.completion_factory import (
    completion_factory,
    register_completion,
)
from graphrag_llm.embedding.embedding_factory import (
    embedding_factory,
    register_embedding,
)

NATIVE_TYPE = "kb_native"
_registered = False


def register_native() -> None:
    """Register NativeCompletion + NativeEmbedding under the kb_native type.

    Idempotent: a process-scoped flag guards the registration so repeated
    calls (e.g. server + worker both bootstrap) never re-register.
    """
    global _registered
    if _registered:
        return
    from kb_platform.llm.client import NativeCompletion
    from kb_platform.llm.embedding import NativeEmbedding

    register_completion(NATIVE_TYPE, NativeCompletion, scope="transient")
    register_embedding(NATIVE_TYPE, NativeEmbedding, scope="transient")
    _registered = True


__all__ = ["NATIVE_TYPE", "register_native", "completion_factory", "embedding_factory"]
