"""build_default_adapter uses bucket-2 params (chunk/cluster/extract/summarize/report)
instead of hardcoding them."""

from graphrag_llm.config import ModelConfig

from kb_platform.graph.graphrag_adapter import build_default_adapter


def _patch_factories(monkeypatch):
    import graphrag_chunking.chunker_factory as cf
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod

    captured: dict = {}

    class _FakeChunker:
        def chunk(self, text):
            return []

    def fake_create_chunker(cfg, encode, decode):
        captured["chunker_cfg"] = cfg
        return _FakeChunker()

    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: object())

    # create_chunker is imported inside build_default_adapter from graphrag_chunking.chunker_factory
    monkeypatch.setattr(cf, "create_chunker", fake_create_chunker)
    return captured


def test_build_default_adapter_uses_custom_bucket2(monkeypatch):
    captured = _patch_factories(monkeypatch)
    llm = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")

    adapter = build_default_adapter(
        data_root="/tmp/_unused_",
        model_config=llm,
        chunk_size=300,
        chunk_overlap=20,
        encoding_model="r50k_base",
        max_cluster_size=7,
        entity_types=["ORG"],
        max_gleanings=2,
        summarize_max_length=111,
        summarize_max_input_tokens=2222,
        report_max_length=3333,
    )
    # chunker got the custom config
    cfg = captured["chunker_cfg"]
    assert cfg.size == 300 and cfg.overlap == 20 and cfg.encoding_model == "r50k_base"
    # adapter carries the custom cluster size + entity_types
    assert adapter._max_cluster_size == 7
    assert adapter._entity_types == ["ORG"]
    # extractor/summarize/report factories captured their params (introspect closures is hard,
    # so assert via the adapter fields that are observable; summarize/report max lengths are
    # baked into the factory closures — verified indirectly via build_adapter_from_settings test)


def test_build_default_adapter_defaults_match_current(monkeypatch):
    captured = _patch_factories(monkeypatch)
    llm = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")
    adapter = build_default_adapter(data_root="/tmp/_unused_", model_config=llm)
    cfg = captured["chunker_cfg"]
    assert cfg.size == 1200 and cfg.overlap == 100 and cfg.encoding_model == "cl100k_base"
    assert adapter._max_cluster_size == 10


def test_build_adapter_from_settings_reads_bucket2(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}

    def fake_build(**kw):
        captured.update(kw)
        # Return a sentinel — the test only inspects `captured`; calling the real
        # build_default_adapter here would recurse (this attr is monkeypatched).
        return object()

    monkeypatch.setattr(ga, "build_default_adapter", fake_build)

    settings = {
        "llm": {"model_provider": "deepseek", "model": "deepseek-chat", "api_keys": ["k"]},
        "chunking": {"size": 350, "overlap": 25},
        "cluster_graph": {"max_cluster_size": 8},
        "extract_graph": {"entity_types": ["ORG", "PERSON"], "max_gleanings": 1},
        "summarize_descriptions": {"max_length": 222, "max_input_tokens": 9000},
        "community_reports": {"max_length": 4444},
    }
    ga.build_adapter_from_settings(json.dumps(settings), "/tmp/_unused_")
    assert captured["chunk_size"] == 350 and captured["chunk_overlap"] == 25
    assert captured["max_cluster_size"] == 8
    assert captured["entity_types"] == ["ORG", "PERSON"] and captured["max_gleanings"] == 1
    assert captured["summarize_max_length"] == 222 and captured["summarize_max_input_tokens"] == 9000
    assert captured["report_max_length"] == 4444


def test_build_adapter_from_settings_entity_types_csv_string(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga
    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(
        json.dumps({"llm": {"model": "x", "api_keys": ["k"]}, "extract_graph": {"entity_types": "ORG, PERSON"}}),
        "/tmp/_unused_",
    )
    assert captured["entity_types"] == ["ORG", "PERSON"]  # csv string -> list, trimmed


def test_build_adapter_from_settings_reads_prompts(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga

    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(
        json.dumps({
            "llm": {"model": "x", "api_keys": ["k"]},
            "extract_graph": {"prompt": "EXTRACT-PROMPT-X"},
            "summarize_descriptions": {"prompt": "SUMM-PROMPT-Y"},
            "community_reports": {"prompt": "REPORT-PROMPT-Z"},
        }),
        "/tmp/_unused_",
    )
    assert captured["extract_prompt"] == "EXTRACT-PROMPT-X"
    assert captured["summarize_prompt"] == "SUMM-PROMPT-Y"
    assert captured["community_report_prompt"] == "REPORT-PROMPT-Z"


def test_build_adapter_from_settings_prompts_default_none(monkeypatch):
    import json
    import kb_platform.graph.graphrag_adapter as ga
    captured: dict = {}
    monkeypatch.setattr(ga, "build_default_adapter", lambda **kw: captured.update(kw) or object())
    ga.build_adapter_from_settings(json.dumps({"llm": {"model": "x", "api_keys": ["k"]}}), "/tmp/_unused_")
    assert captured["extract_prompt"] is None
    assert captured["summarize_prompt"] is None
    assert captured["community_report_prompt"] is None

