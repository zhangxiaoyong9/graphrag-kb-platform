"""Dispatch a query method to the right QueryEngine.

This is the wiring layer (like the routes): it MAY import ``assemble_kb_settings``
(graphrag-importing), exactly as today's routes do. The engines themselves stay
graphrag-free (Neo4jQueryEngine) or graphrag-only (graphrag_engine.py).

For method ∈ {cypher, hybrid} AND kb.neo4j_profile_id set -> Neo4jQueryEngine
(driver pool + injected kb_native completion/embed clients). Otherwise ->
GraphRagQueryEngine, mirroring what routes_query.py does today.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _ensure_neo4j_available() -> None:
    """Import neo4j eagerly so a missing [neo4j] extra fails at engine-build
    time (surfaced as SSE error), not deep inside the first query."""
    import neo4j  # noqa: F401, PLC0415


def _assemble_kb_settings(kb, repo):
    """Thin indirection so tests can short-circuit the graphrag config build."""
    from kb_platform.graph.graphrag_adapter import assemble_kb_settings  # noqa: PLC0415

    return assemble_kb_settings(kb, repo)


def build_query_engine(method, kb, repo, app_state):
    """Return the QueryEngine for ``method`` on ``kb``."""
    data_root = getattr(app_state, "data_root", None) or kb.data_root

    if method in ("cypher", "hybrid"):
        return _build_neo4j_engine(method, kb, repo, data_root)

    from kb_platform.query.graphrag_engine import GraphRagQueryEngine  # noqa: PLC0415

    model_config = _assemble_kb_settings(kb, repo)
    return GraphRagQueryEngine(data_root=data_root, model_config=model_config)


def _build_neo4j_engine(method, kb, repo, data_root):
    # Check the profile link first so a misconfigured KB surfaces a clear error
    # even on installs without the [neo4j] extra (the extra check below only
    # fires once we know we are actually going to build the engine).
    if not kb.neo4j_profile_id:
        raise RuntimeError(
            f"KB has no Neo4j profile; configure one to use method='{method}'"
        )
    neo = repo.get_profile(kb.neo4j_profile_id)
    if neo is None:
        raise RuntimeError(f"Neo4j profile {kb.neo4j_profile_id} not found")

    try:
        _ensure_neo4j_available()
    except ModuleNotFoundError as e:
        raise RuntimeError(
            f"the [neo4j] extra is not installed (method={method}): "
            "install with `uv sync --extra neo4j`"
        ) from e

    from kb_platform.db.crypto import decrypt_values  # noqa: PLC0415
    from kb_platform.llm.native_builders import (  # noqa: PLC0415
        build_native_completion,
        build_native_embedding,
    )
    from kb_platform.neo4j import driver_pool  # noqa: PLC0415
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine  # noqa: PLC0415

    passwords = decrypt_values(neo.api_keys_enc)
    if not passwords:
        raise RuntimeError(f"Neo4j profile '{neo.name}' has no password")
    uri = neo.api_base
    username = neo.username or "neo4j"
    password = passwords[0]

    settings = _assemble_kb_settings(kb, repo)
    llm = settings.get("llm") or {}
    kb_profiles = llm.get("kb_profiles") or []
    if not kb_profiles:
        raise RuntimeError("KB has no resolved LLM profile for the query LLM")
    model_id = llm.get("model", "gpt-4o-mini")
    completion = build_native_completion(model_id, kb_profiles)

    embed = None
    emb = settings.get("embedding") or {}
    if emb.get("kb_profiles"):
        emb_profile = emb["kb_profiles"][0]
        emb_model = emb.get("model", "text-embedding-3-small")
        native_embed = build_native_embedding(emb_model, emb_profile)

        async def embed(text: str) -> list[float]:
            # Await the async entry directly (NOT asyncio.to_thread(native_embed.embedding)):
            # .embedding() is sync and calls asyncio.run, which spins up a throwaway
            # loop that binds the shared httpx.AsyncClient to it, breaking the
            # subsequent streaming synthesis ("bound to a different event loop").
            return await native_embed.embed_async(text)

    return Neo4jQueryEngine(
        uri=uri, username=username, password=password,
        driver_pool=driver_pool, completion=completion, embed=embed,
        model_id=model_id,
    )
