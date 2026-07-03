"""Build NativeCompletion / NativeEmbedding from a kb_profiles bundle, graphrag-free.

The Neo4j query engine needs an LLM + an embedding client to generate Cypher /
synthesize answers / embed the question, but it must NOT import graphrag. This
module is the sanctioned home for that construction (it sits next to
``client.py`` / ``embedding.py``, which already import ``graphrag_llm``).

``NativeCompletion`` / ``NativeEmbedding`` read the ``kb_profiles`` bundle from
``model_config.model_extra``. We therefore pack the bundle into a tiny stand-in
(a ``SimpleNamespace`` exposing ``model_extra``) and hand it to the existing
constructors — no graphrag-llm factory call, no graphrag import.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from kb_platform.llm.client import NativeCompletion
from kb_platform.llm.embedding import NativeEmbedding


def _model_config_stub(kb_profiles: list[dict]) -> Any:
    """Minimal stand-in for graphrag-llm's ModelConfig: exposes the only
    attribute NativeCompletion / NativeEmbedding read (``.model_extra``)."""
    return SimpleNamespace(model_extra={"kb_profiles": kb_profiles})


def build_native_completion(model_id: str, kb_profiles: list[dict]) -> NativeCompletion:
    """Build a NativeCompletion over the ordered profile bundle (failover list)."""
    return NativeCompletion(model_id=model_id, model_config=_model_config_stub(kb_profiles))


def build_native_embedding(model_id: str, kb_profile: dict) -> NativeEmbedding:
    """Build a single-profile NativeEmbedding (embeddings are single-profile).

    The embedding model id may differ from the chat model id on the same
    profile (a KB references one embedding_profile_id but the profile itself
    carries the chat model name); honor ``model_id`` here by overriding the
    profile's ``model`` field before construction.
    """
    prof = {**kb_profile, "model": model_id}
    return NativeEmbedding(model_id=model_id, model_config=_model_config_stub([prof]))
