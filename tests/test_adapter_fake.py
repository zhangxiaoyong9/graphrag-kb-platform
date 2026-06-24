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


# --- Task 4: summarize / report / cluster / finalize ---

from kb_platform.graph.adapter import CommunityReport  # noqa: E402


def test_summarize_entity_joins_descriptions():
    a = FakeGraphAdapter()
    merged = a.summarize_entity_sync("ACME", ["desc one", "desc two"])
    assert "desc one" in merged and "desc two" in merged


def test_cluster_relationships_returns_communities():
    a = FakeGraphAdapter()
    rels = pd.DataFrame([
        {"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
        {"source": "B", "target": "C", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
        {"source": "X", "target": "Y", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c2"]},
    ])
    comms = a.cluster_relationships(rels)
    assert {"level", "community_id", "parent", "entity_ids"} <= set(comms.columns)
    assert len(comms) >= 1
    # all sources/targets appear in some community
    members = {e for ids in comms["entity_ids"] for e in ids}
    assert {"A", "B", "C", "X", "Y"} <= members


def test_finalize_entities_relationships_adds_degree():
    a = FakeGraphAdapter()
    ents = pd.DataFrame([{"title": "A", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1}])
    rels = pd.DataFrame([{"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]}])
    e2, r2 = a.finalize_entities_relationships(ents, rels)
    assert "degree" in e2.columns or "degree" in r2.columns


def test_report_community_returns_report():
    a = FakeGraphAdapter()
    ctx = {"community": "C0", "level": 0, "entities": [{"title": "A", "description": "d"}], "relationships": [], "sub_reports": []}
    rep = a.report_community_sync(ctx)
    assert isinstance(rep, CommunityReport)
    assert rep.community == "C0" and rep.level == 0
