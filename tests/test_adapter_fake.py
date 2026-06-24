import pandas as pd

from kb_platform.graph.adapter import FakeGraphAdapter


def test_fake_chunk_document():
    adapter = FakeGraphAdapter()
    chunks = adapter.chunk_document(doc_id=1, text="hello world " * 500)
    assert len(chunks) >= 1
    assert all(c.chunk_id for c in chunks)


def test_fake_extract_chunk_returns_entities():
    adapter = FakeGraphAdapter()
    result = adapter.extract_chunk_sync("c1", "some text")  # 同步包装便于测试
    assert isinstance(result.entities, pd.DataFrame)
    assert "title" in result.entities.columns


def test_fake_merge():
    adapter = FakeGraphAdapter()
    r = adapter.extract_chunk_sync("c1", "x")
    entities, relationships = adapter.merge_extractions([r, r])
    assert not entities.empty
