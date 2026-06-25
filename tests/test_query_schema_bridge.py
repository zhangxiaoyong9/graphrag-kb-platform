"""Tests for the parquet → graphrag-query schema bridge in graphrag_engine.

These exercise the REAL graphrag ``read_indexer_*`` adapters against
DataFrames shaped exactly like the platform's indexing output (FakeGraphAdapter
merge/cluster/report schema). They fail with KeyError/ValueError if the
normalization helpers drop a column graphrag requires.
"""

import pandas as pd

from kb_platform.query.graphrag_engine import (
    _norm_communities,
    _norm_entities,
    _norm_reports,
    _norm_relationships,
    _norm_text_units,
)


def _platform_entities():
    """Shape produced by FakeGraphAdapter.merge_extractions + finalize."""
    return pd.DataFrame(
        [
            {
                "title": "ACME",
                "type": "CONCEPT",
                "description": ["runs the factory", "makes widgets"],
                "text_unit_ids": ["c1", "c2"],
                "frequency": 2,
                "degree": 1,
            },
            {
                "title": "GLOBEX",
                "type": "CONCEPT",
                "description": ["a rival"],
                "text_unit_ids": ["c1"],
                "frequency": 1,
                "degree": 1,
            },
        ]
    )


def _platform_communities():
    return pd.DataFrame(
        [
            {
                "level": 0,
                "community_id": "0",
                "parent": "0",
                "entity_ids": ["ACME", "GLOBEX"],
            }
        ]
    )


def _platform_relationships():
    return pd.DataFrame(
        [
            {
                "source": "ACME",
                "target": "GLOBEX",
                "description": ["competes with"],
                "text_unit_ids": ["c1"],
                "weight": 1.0,
                "combined_degree": 2,
            }
        ]
    )


def _platform_reports():
    return pd.DataFrame(
        [
            {
                "title": "R",
                "summary": "ACME and GLOBEX compete",
                "findings": ["f1"],
                "rank": 0.5,
                "full_content": "ACME and GLOBEX compete",
                "level": 0,
                "community": "0",
            }
        ]
    )


def _platform_text_units():
    return pd.DataFrame([{"id": "c1", "text": "chunk one", "document_ids": ["7"], "n_tokens": 10}])


def _all_normalized():
    return {
        "entities": _norm_entities(_platform_entities()),
        "communities": _norm_communities(_platform_communities()),
        "reports": _norm_reports(_platform_reports()),
        "relationships": _norm_relationships(_platform_relationships()),
        "text_units": _norm_text_units(_platform_text_units()),
    }


def test_norm_adds_required_columns():
    n = _all_normalized()
    assert list(n["entities"]["id"]) == ["ACME", "GLOBEX"]
    assert "human_readable_id" in n["entities"].columns
    # list description flattened to a plain string (not the list repr)
    assert n["entities"]["description"].iloc[0] == "runs the factory makes widgets"
    assert "community" in n["communities"].columns
    assert "children" in n["communities"].columns
    assert isinstance(n["communities"]["children"].iloc[0], list)
    assert "id" in n["reports"].columns
    assert n["reports"]["id"].iloc[0] == "0"
    assert "document_id" in n["text_units"].columns
    assert n["text_units"]["document_id"].iloc[0] == "7"


def test_graphrag_readers_accept_normalized_platform_frames():
    """The real graphrag read_indexer_* adapters must not raise on the
    normalized platform DataFrames — this is the exact schema contract."""
    from graphrag.query.indexer_adapters import (
        read_indexer_communities,
        read_indexer_entities,
        read_indexer_relationships,
        read_indexer_reports,
        read_indexer_text_units,
    )

    n = _all_normalized()
    communities = read_indexer_communities(n["communities"], n["reports"])
    assert len(communities) == 1
    reports = read_indexer_reports(n["reports"], n["communities"], community_level=2)
    assert len(reports) == 1
    entities = read_indexer_entities(n["entities"], n["communities"], community_level=2)
    assert len(entities) == 2
    # entity community join resolves (ACME/GLOBEX both in community 0)
    acme = next(e for e in entities if e.title == "ACME")
    assert acme.community_ids == ["0"]
    relationships = read_indexer_relationships(n["relationships"])
    assert len(relationships) == 1
    text_units = read_indexer_text_units(n["text_units"])
    assert len(text_units) == 1


def test_norm_is_idempotent_on_graphrag_shaped_frames():
    """If an upstream change ever writes graphrag-native columns, normalization
    must not clobber them."""
    native = pd.DataFrame(
        [
            {
                "id": "X",
                "title": "X",
                "type": "ORG",
                "human_readable_id": 9,
                "description": "d",
                "degree": 3,
            }
        ]
    )
    out = _norm_entities(native)
    assert list(out["id"]) == ["X"]
    assert list(out["human_readable_id"]) == [9]
    assert out["description"].iloc[0] == "d"


def test_norm_handles_empty_frames():
    """Empty parquets (e.g. community_reports on DeepSeek) must not crash."""
    assert (
        len(
            _norm_communities(
                pd.DataFrame(columns=["level", "community_id", "parent", "entity_ids"])
            )
        )
        == 0
    )
    assert (
        len(
            _norm_reports(
                pd.DataFrame(
                    columns=[
                        "title",
                        "summary",
                        "findings",
                        "rank",
                        "full_content",
                        "level",
                        "community",
                    ]
                )
            )
        )
        == 0
    )
