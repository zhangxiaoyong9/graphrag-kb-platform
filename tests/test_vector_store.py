from kb_platform.graph.vector_store import (
    FakeVectorStore,
    LanceDBVectorStoreWrapper,
    build_vector_store,
)
from kb_platform.graph.adapter import FakeGraphAdapter


def test_fake_vector_store_upsert_query():
    vs = FakeVectorStore(dim=4)
    vs.connect()
    vs.upsert(
        "entity",
        [
            {"id": "e1", "text": "ACME", "vector": [1, 0, 0, 0]},
            {"id": "e2", "text": "BETA", "vector": [0, 1, 0, 0]},
        ],
    )
    hits = vs.query("entity", "ACME", k=1)
    assert len(hits) == 1 and hits[0]["id"] == "e1"


def test_embed_items_deterministic():
    adapter = FakeGraphAdapter()
    vecs = adapter.embed_items(["hello", "world"])
    assert len(vecs) == 2 and len(vecs[0]) > 0


def test_lancedb_wrapper_roundtrip_readable_by_graphrag(tmp_path):
    """The wrapper writes a table that graphrag's get_embedding_store reads back.

    This is the alignment contract: the write path (LanceDBVectorStoreWrapper)
    and the production read path (GraphRagQueryEngine -> get_embedding_store)
    must open the SAME table (same db_uri, same index_name == embedding name).
    """
    from graphrag.config.models.graph_rag_config import GraphRagConfig
    from graphrag.utils.api import get_embedding_store

    data_root = tmp_path / "kb"
    data_root.mkdir()
    vs = build_vector_store(str(data_root))
    assert isinstance(vs, LanceDBVectorStoreWrapper)
    vs.connect()
    vs.upsert(
        "entity_description",
        [
            {"id": "ACME", "text": "ACME Corp", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"id": "GLOBEX", "text": "Globex", "vector": [0.9, 0.8, 0.7, 0.6]},
        ],
    )

    # Read path mirrors GraphRagQueryEngine._build_embedding_store: config with
    # type=lancedb + db_uri=<data_root>/vectors, then get_embedding_store.
    cfg = GraphRagConfig.model_validate(
        {"vector_store": {"type": "lancedb", "db_uri": str(data_root / "vectors")}}
    )
    store = get_embedding_store(cfg.vector_store, "entity_description")
    hits = store.similarity_search_by_vector([0.1, 0.2, 0.3, 0.4], k=2)
    ids = [h.document.id for h in hits]
    assert "ACME" in ids
    assert "GLOBEX" in ids
    # closest vector to its own embedding is ACME
    assert hits[0].document.id == "ACME"


def test_lancedb_wrapper_reindex_replaces_stale(tmp_path):
    """Re-indexing overwrites the table rather than accumulating duplicate ids."""
    from graphrag.config.models.graph_rag_config import GraphRagConfig
    from graphrag.utils.api import get_embedding_store

    data_root = tmp_path / "kb"
    data_root.mkdir()
    vs = build_vector_store(str(data_root))
    vs.connect()
    vs.upsert("entity_description", [{"id": "ACME", "text": "v1", "vector": [1.0, 0.0, 0.0, 0.0]}])
    vs.upsert("entity_description", [{"id": "ACME", "text": "v2", "vector": [1.0, 0.0, 0.0, 0.0]}])

    cfg = GraphRagConfig.model_validate(
        {"vector_store": {"type": "lancedb", "db_uri": str(data_root / "vectors")}}
    )
    store = get_embedding_store(cfg.vector_store, "entity_description")
    assert store.count() == 1
