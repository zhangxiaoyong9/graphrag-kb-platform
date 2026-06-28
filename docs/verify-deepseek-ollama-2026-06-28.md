# Live LLM verify — DeepSeek (extract) + Ollama (embeddings), 2026-06-28

End-to-end real-LLM indexing run on the kb-platform, confirming the unit-level
LLM raw-output capture + the full 7-step pipeline (including embeddings) all
work with **DeepSeek** for chat steps and **Ollama `nomic-embed-text`** for
embeddings.

## Setup

- Server: `uv run python -m kb_platform.server /tmp/llm-demo.db /tmp/llm-data 127.0.0.1 8000` (proxy unset).
- Worker: `env -u all_proxy -u http_proxy -u https_proxy uv run python -m kb_platform.worker /tmp/llm-demo.db` (DEEPSEEK_API_KEY in env; proxy unset — required for localhost Ollama).
- Ollama running locally with `nomic-embed-text:latest` pulled.

## KB settings (KB "LLM 演示2", id 2)

```json
{
  "llm": {"model_provider": "deepseek", "model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY"},
  "embedding": {"model_provider": "ollama", "model": "nomic-embed-text", "api_base": "http://localhost:11434", "api_key": "ollama"},
  "community_reports": {"structured_output": false}
}
```

Doc: `battery.md` (one paragraph about CATL / Tesla / BYD / LG / 松下 / 三星 SDI). `structured_output: false` because DeepSeek rejects `response_format: json_schema` for community reports.

## Result — job #3, all 7 steps succeeded

```
0 chunk_documents            succeeded
1 extract_graph              succeeded   (1 unit — DeepSeek)
2 summarize_descriptions     succeeded
3 finalize_graph             succeeded
4 create_communities         succeeded
5 community_reports          succeeded   (2 reports — DeepSeek, plain-text path)
6 generate_text_embeddings   succeeded   (Ollama nomic-embed-text)
```

Outputs on disk: `entities / relationships / communities / community_reports / text_units` parquet + 3 LanceDB tables (`entity_description / text_unit_text / community_full_content`). Graph: **13 nodes, 15 relationships**.

Cost (cost-capture wrapper): extract_graph $0.0009, community_reports $0.0003, job total part of the ~$0.0024 cumulative.

## Unit LLM raw output is displayed

The extract_graph unit's "LLM 输出" expandable in the job-detail unit table shows the real DeepSeek return — entities with type + description, e.g.:

- `<|宁德时代|><ORGANIZATION><|…全球最大的动力电池制造商…|>`
- `<|比亚迪|><ORGANIZATION><|…刀片电池技术…|>`
- `<|LG 新能源|><ORGANIZATION><|…主要的电池供应商…|>`
- `<|福建宁德|><GEO><|…宁德时代的总部所在地|>`

Path: `CostCapturingCompletion` captures the raw response → `Unit.llm_raw_output` → `UnitOut.llm_raw_output` → `UnitTable` "LLM 输出" `<details>` (`web/src/components/UnitTable.tsx`).

## Gotchas hit during this run

1. **`data_root` must exist.** First attempt failed in `chunk_documents` with `OSError: Cannot save file into a non-existent directory: '/tmp/llm-data'` — the server does not `mkdir` the data_root. Fix: `mkdir -p /tmp/llm-data` before triggering. (Worth a server-side `mkdir -p` someday.)
2. **DeepSeek has no embedding model.** A KB with only an `llm` block fails at `generate_text_embeddings` with `LiteLLMUnknownProvider: Unmapped LLM provider … deepseek`. Fix: add the `embedding` block pointing at Ollama (or any embedder). extract_graph/summarize/community_reports are unaffected (chat-only).
3. **Proxy + localhost Ollama.** If the worker inherits `all_proxy`/`http_proxy`/`https_proxy` (Surge), litellm routes `localhost:11434` through SOCKS and fails (`socksio`). Run the worker with those unset. DeepSeek (remote) works either way.

## Screenshots

- `docs/screenshots/llm-demo-2026-06-28/llm-raw-output.png` — extract_graph unit, "LLM 输出" expanded (real DeepSeek entities).
- `docs/screenshots/llm-demo-2026-06-28/llm-job-green.png` — job #3 fully green (7/7 steps).
- `docs/screenshots/llm-demo-2026-06-28/llm-graph.png` — graph tab, 13 nodes / 15 relationships.
