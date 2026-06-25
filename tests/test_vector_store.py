from kb_platform.graph.vector_store import FakeVectorStore
from kb_platform.graph.adapter import FakeGraphAdapter


def test_fake_vector_store_upsert_query():
    vs = FakeVectorStore(dim=4)
    vs.connect()
    vs.upsert("entity", [{"id": "e1", "text": "ACME", "vector": [1, 0, 0, 0]}, {"id": "e2", "text": "BETA", "vector": [0, 1, 0, 0]}])
    hits = vs.query("entity", "ACME", k=1)
    assert len(hits) == 1 and hits[0]["id"] == "e1"


def test_embed_items_deterministic():
    adapter = FakeGraphAdapter()
    vecs = adapter.embed_items(["hello", "world"])
    assert len(vecs) == 2 and len(vecs[0]) > 0
