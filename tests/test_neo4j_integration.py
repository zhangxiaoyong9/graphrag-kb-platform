"""End-to-end Neo4j graph-query integration test.

Boots a Neo4j 5.x container, loads the cypher-export artifact, and exercises
both cypher (Text2Cypher) and hybrid (Vector+Cypher) methods through
``Neo4jQueryEngine`` against a real LLM profile.

Skipped automatically when any prerequisite is absent:

- ``neo4j`` / ``testcontainers`` not installed -> ``pytest.importorskip``
- no ``OPENAI_API_KEY`` (or ``KB_TEST_LLM_PROFILE``) -> module-level skipif
  (same tier as the existing real-LLM integration tests)
- the hybrid test additionally needs ``KB_TEST_HYBRID=1`` because it must
  embed the fixture entities with the real embedding model so the vector
  ANN has something to retrieve over (the cypher test is the default).

Run manually::

    OPENAI_API_KEY=sk-... uv run python -m pytest tests/test_neo4j_integration.py -v -s

    # to also exercise the hybrid path:
    OPENAI_API_KEY=sk-... KB_TEST_HYBRID=1 \\
        uv run python -m pytest tests/test_neo4j_integration.py -v -s
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

# Collection must succeed even when the extras are absent: importorskip turns
# the whole module into a single "skipped" item rather than a collection error.
pytest.importorskip("neo4j")
pytest.importorskip("testcontainers")

# Same gating tier as the existing real-LLM integration tests: a profile/env
# must be present, otherwise there is nothing to call. ``KB_TEST_LLM_PROFILE``
# lets CI point at a non-OpenAI provider profile json if needed.
_HAS_REAL_LLM = bool(os.getenv("OPENAI_API_KEY") or os.getenv("KB_TEST_LLM_PROFILE"))
pytestmark = pytest.mark.skipif(
    not _HAS_REAL_LLM, reason="no real-LLM credentials (set OPENAI_API_KEY)"
)


@pytest.fixture(scope="module")
def neo4j_store():
    """Boot a Neo4j 5.x container for the duration of the module.

    The image is configurable via ``NEO4J_IMAGE`` so CI can pin a version that
    has the vector index features (>= 5.11). Default ``neo4j:5.20``.
    """
    from testcontainers.neo4j import Neo4jContainer

    image = os.getenv("NEO4J_IMAGE", "neo4j:5.20")
    with Neo4jContainer(image=image, password="testpass") as neo:
        yield neo


def _profile_dicts() -> list[dict]:
    """Build the kb_profiles bundle passed to ``build_native_completion``.

    Mirrors the shape ``assemble_kb_settings`` produces: one entry per profile
    with provider/model/api_base/api_version/keys/ssl_verify. We read the key
    straight from the env (the only secret the test needs).
    """
    key = os.environ["OPENAI_API_KEY"]
    return [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": None,
            "api_version": None,
            "keys": [key],
            "ssl_verify": True,
        }
    ]


def _embedding_profile_dict() -> dict:
    """Single-profile bundle for the embedding model (text-embedding-3-small)."""
    key = os.environ["OPENAI_API_KEY"]
    return {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "api_base": None,
        "api_version": None,
        "keys": [key],
        "ssl_verify": True,
    }


def _fixture_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Tiny fixture graph: Alice (PERSON) works at Acme (ORG), one text unit."""
    import numpy as np

    entities = pd.DataFrame(
        [
            {
                "title": "Alice",
                "type": "PERSON",
                "description": "an engineer",
                "text_unit_ids": np.array(["c1"]),
                "frequency": 1,
                "degree": 1,
            },
            {
                "title": "Acme",
                "type": "ORG",
                "description": "a company",
                "text_unit_ids": np.array(["c1"]),
                "frequency": 1,
                "degree": 1,
            },
        ]
    )
    relationships = pd.DataFrame(
        [
            {
                "source": "Alice",
                "target": "Acme",
                "description": "works at",
                "text_unit_ids": np.array(["c1"]),
                "weight": 1.0,
                "combined_degree": 2,
            }
        ]
    )
    text_units = pd.DataFrame(
        [{"id": "c1", "text": "Alice works at Acme.", "document_ids": np.array(["d1"]), "n_tokens": 4}]
    )
    return entities, relationships, text_units


def _run_cypher_script(neo4j_store, script: str) -> None:
    """Execute a multi-statement Cypher script via the sync driver.

    ``write_cypher`` already emits one statement per line; we split on ``;``
    conservatively and skip blanks / pure comments so the driver is happy.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        neo4j_store.get_connection_url(), auth=("neo4j", "testpass")
    )
    try:
        with driver.session() as s:
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if not stmt or stmt.startswith("//"):
                    continue
                s.run(stmt + ";")
    finally:
        driver.close()


def _load_export(neo4j_store, entity_embeddings: dict[str, list[float]] | None = None):
    """Render the cypher-export artifact from fixture parquet and load it.

    When ``entity_embeddings`` is supplied, the export also creates the
    ``entity_description_vec`` vector index + writes ``entity_description``
    vector properties — required for the hybrid Vector+Cypher path.
    """
    from kb_platform.graph.cypher import write_cypher

    entities, relationships, text_units = _fixture_frames()
    script = write_cypher(
        entities,
        relationships,
        text_units=text_units,
        entity_embeddings=entity_embeddings,
    )
    _run_cypher_script(neo4j_store, script)
    return neo4j_store


def _build_engine(neo4j_store, *, embed):
    """Construct a ``Neo4jQueryEngine`` against the live container."""
    from kb_platform.llm.native_builders import build_native_completion
    from kb_platform.neo4j import driver_pool
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine

    completion = build_native_completion("gpt-4o-mini", _profile_dicts())
    return Neo4jQueryEngine(
        uri=neo4j_store.get_connection_url(),
        username="neo4j",
        password="testpass",
        driver_pool=driver_pool,
        completion=completion,
        embed=embed,
        model_id="gpt-4o-mini",
    )


async def test_cypher_method_answers_structural_question(neo4j_store):
    """Text2Cypher: ask a structural question, assert the answer cites the ORG."""
    _load_export(neo4j_store)
    engine = _build_engine(neo4j_store, embed=None)

    events = [e async for e in engine.stream_search("cypher", "How many entities are ORG?", "/none")]
    # a StreamMeta carrying the generated Cypher is emitted before the answer
    assert any(getattr(e, "cypher", None) for e in events)
    done = events[-1]
    assert done.error is None
    # one ORG (Acme) — the LLM should produce a "1" somewhere in the answer
    assert "1" in done.answer


@pytest.mark.skipif(
    not os.getenv("KB_TEST_HYBRID"),
    reason="hybrid path needs entity embeddings; set KB_TEST_HYBRID=1 to enable",
)
async def test_hybrid_method_uses_vector_traversal(neo4j_store):
    """Vector+Cypher hybrid: embed fixture entities, ask about Alice, assert
    the hybrid Cypher template references ``entity_description_vec`` and the
    synthesized answer mentions Alice.
    """
    import asyncio

    from kb_platform.llm.native_builders import build_native_embedding

    native_emb = build_native_embedding("text-embedding-3-small", _embedding_profile_dict())

    # Pre-compute real embeddings for the two fixture entities so the export
    # emits the vector index + vector properties; the hybrid ANN has vectors
    # to retrieve over. ``NativeEmbedding.embedding`` is sync (it asyncio.run's
    # internally) — run it in a thread so we don't nest loops.
    async def _embed_vec(text: str) -> list[float]:
        resp = await asyncio.to_thread(native_emb.embedding, input=[text])
        return resp.data[0].embedding

    entity_embeddings = {
        "Alice": await _embed_vec("Alice is an engineer."),
        "Acme": await _embed_vec("Acme is a company."),
    }
    _load_export(neo4j_store, entity_embeddings=entity_embeddings)

    async def embed(text: str) -> list[float]:
        return await _embed_vec(text)

    engine = _build_engine(neo4j_store, embed=embed)

    events = [e async for e in engine.stream_search("hybrid", "who is Alice?", "/none")]
    # the meta event carries the hybrid Cypher template -> vector index name
    assert any("entity_description_vec" in getattr(e, "cypher", "") for e in events)
    done = events[-1]
    assert done.error is None
    assert "Alice" in done.answer
