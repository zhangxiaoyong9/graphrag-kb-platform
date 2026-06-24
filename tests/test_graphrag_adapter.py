"""Contract test for the real graphrag-backed adapter (zero-cost via MockLLM)."""

import pandas as pd  # noqa: F401  (kept for parity with brief / future assertions)

from kb_platform.graph.graphrag_adapter import build_default_adapter

# graphrag extraction output structured format (## delimits records, <|> delimits fields)
CANNED = (
    '("entity"<|>ACME<|>ORGANIZATION<|>A tech company)##'
    '("entity"<|>BOB<|>PERSON<|>CEO of ACME)##'
    '("relationship"<|>ACME<|>BOB<|>employs<|>0.9)##'
    "<|COMPLETE|>"
)


def _mock_model_config() -> "object":
    from graphrag_llm.config import ModelConfig

    return ModelConfig(
        type="mock",
        model_provider="mock",
        model="mock",
        mock_responses=[CANNED],
    )


def test_real_extract_chunk_parses_entities(tmp_path):
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    result = adapter.extract_chunk_sync("c1", "ACME employs Bob.")
    assert set(result.entities["title"]) == {"ACME", "BOB"}
    assert len(result.relationships) == 1


def test_real_chunk_document(tmp_path):
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    chunks = adapter.chunk_document(doc_id=1, text="one two three " * 500)
    assert len(chunks) >= 1
    assert all(c.text for c in chunks)


def test_real_merge_dedupes_entities(tmp_path):
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    r = adapter.extract_chunk_sync("c1", "ACME is big.")
    r2 = adapter.extract_chunk_sync("c2", "ACME is global.")
    entities, relationships = adapter.merge_extractions([r, r2])
    # both chunks extract ACME; merged entity table has 1 ACME row with frequency=2
    acme = entities[entities["title"] == "ACME"].iloc[0]
    assert acme["frequency"] == 2
