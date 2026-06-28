# Live LLM smoke test — Ollama (chat + embeddings), 2026-06-28

End-to-end real-LLM run on the kb-platform using **only local Ollama models** — no
remote API keys, no network beyond localhost, zero cost. The smoke test exposed
**two production-blocking bugs** in the provider-profiles path (both the worker
indexing path and the query path), each fixed + verified below.

## Setup

- Chat LLM: Ollama `wangshenzhi/llama3-8b-chinese-chat-ollama-q8:latest` (Chinese-tuned,
  follows graphrag's extraction format; no `<think>` traces).
- Embeddings: Ollama `nomic-embed-text:latest`.
- Wired through **provider profiles** (the post-refactor flow): LLM profile with
  `structured_output=false` → `report_community_plain` path (local models reject
  `response_format: json_schema`); embedding profile with `api_key="ollama"`.
- Server + worker started with `all_proxy`/`http_proxy`/`https_proxy` **unset**
  (required for localhost Ollama — litellm otherwise routes `localhost:11434`
  through SOCKS and fails).
- Fresh DB `/tmp/smoke.db` (`alembic -x db=/tmp/smoke.db upgrade head`), data root
  `/tmp/smoke-data` (`mkdir -p` first — the server does not create it).

## Bugs found + fixed

### Bug 1 — the worker could not build an adapter for ANY profile-based KB (indexing fully broken)

`run_worker_once` loaded the KB inside a session, then handed
`build_adapter_for_kb` a lightweight `_SettingsKb` carrier carrying only
`settings_json` + `data_root`. After the provider-profiles refactor,
`assemble_kb_settings` reads `kb.llm_profile_id` / `kb.embedding_profile_id`
off the KB object → `AttributeError: '_SettingsKb' object has no attribute
'llm_profile_id'` → job FAILED before `chunk_documents` even ran:

```
File ".../kb_platform/graph/graphrag_adapter.py", line 484, in assemble_kb_settings
    if kb.llm_profile_id is None:
AttributeError: '_SettingsKb' object has no attribute 'llm_profile_id'
```

Every worker test uses `adapter_factory=lambda kb: FakeGraphAdapter()` (the carrier
is ignored) and a KB with no profiles, so the production `build_adapter_for_kb`
path was never exercised — this is exactly the gap a real-LLM smoke test closes.

**Fix** (`kb_platform/worker.py`): `_SettingsKb` now also carries
`llm_profile_id` + `embedding_profile_id` (profile ids are plain `Integer`
columns, safe to read inside the load session — no lazy load); `run_worker_once`
populates them. Regression test: `tests/test_worker.py::test_worker_carrier_carries_profile_ids`.

### Bug 2 — the query endpoint had no LLM config (querying fully broken)

`routes_query.py` passed **raw** `kb.settings_json` (content-only — `chunking`,
etc.; no `llm`/`embedding` blocks) to `GraphRagQueryEngine`. With no `llm` block,
`_resolve_config` never built `completion_models`, so graphrag raised on every
method:

```
Model ID default_completion_model not found in completion_models.
Please rerun `graphrag init` and set the completion_models configuration.
```

The provider-profiles refactor updated the **indexing** path to use
`assemble_kb_settings` but missed the **query** path.

**Fix** (`kb_platform/api/routes_query.py` + `kb_platform/query/graphrag_engine.py`):
the route now resolves `assemble_kb_settings(kb, repo)` (profiles + decrypted keys,
same seam as indexing) and passes the full settings dict to the engine; assembly
errors surface as a graceful `QueryResult.error` rather than a 500. Because
`assemble_kb_settings` emits `llm.api_keys` (a **list**), `_resolve_config` now
reads `api_keys[0]` in addition to the scalar `api_key` (LLM + embedding, symmetric).
Regression test: `tests/test_query_sources.py::test_resolve_config_reads_assembled_api_keys_list`.

## Result — job #2, all 7 steps succeeded (real Ollama LLM)

```
0 chunk_documents            succeeded
1 extract_graph              succeeded   (1 unit — Ollama chat)
2 summarize_descriptions     succeeded   (0 units — single-chunk doc, no dup descriptions)
3 finalize_graph             succeeded
4 create_communities         succeeded
5 community_reports          succeeded   (1 unit — Ollama chat, plain-text path)
6 generate_text_embeddings   succeeded   (Ollama nomic-embed-text)
```

Stats: **5 entities, 10 relationships, 1 community, 1 community report, 1 text unit.**

Extracted entities (real model output, all `ORGANIZATION`):
宁德时代 · 比亚迪 · LG新能源 · 松下 · 三星SDI — every manufacturer in the source doc.

Data plane on disk: `entities / relationships / communities / community_reports /
text_units` parquet + 3 LanceDB tables (`entity_description / text_unit_text /
community_full_content`).

## Queries — real Ollama LLM answers (after Bug 2 fix)

| method | question | answer (abridged) | LLM calls |
|---|---|---|---|
| local | 宁德时代为哪些汽车厂商供应电池？ | …clients include Tesla, XPeng, and BYD. | 1 |
| global | 全球排名前五的动力电池制造商是哪几家？ | 宁德时代、BYD、LG新能源、松下、三星SDI (map+reduce, Chinese) | 2 |
| basic | 刀片电池是哪家公司研发的？ | …BYD (Build Your Dreams), 自主研发了刀片电池技术. | 1 |

## Cost capture — works as designed

`Unit.cost_json` (DB column; not exposed on `UnitOut`):

```
extract_graph      {"model":"…llama3-8b-chinese-chat…", "prompt_tokens":2074, "completion_tokens":744, "estimated_cost_usd":null}
community_reports  {"model":"…llama3-8b-chinese-chat…", "prompt_tokens":2286, "completion_tokens":447, "estimated_cost_usd":null}
```

Tokens are captured; `estimated_cost_usd=null` because the local model has no
price entry — exactly the documented "unknown model → tokens counted, cost latched
to None, never zero" behavior. (graphrag-llm does return `.usage` for Ollama:
`CompletionUsage(prompt_tokens=…, completion_tokens=…)`.)

## Reproduce

```bash
# 1. models (one-time)
ollama pull wangshenzhi/llama3-8b-chinese-chat-ollama-q8 nomic-embed-text

# 2. fresh control plane + data root
rm -rf /tmp/smoke.db /tmp/smoke-data && mkdir -p /tmp/smoke-data
uv run alembic -x db=/tmp/smoke.db upgrade head

# 3. server + worker (proxy unset for localhost Ollama)
env -u all_proxy -u http_proxy -u https_proxy uv run python -m kb_platform.server /tmp/smoke.db /tmp/smoke-data 127.0.0.1 8000 &
env -u all_proxy -u http_proxy -u https_proxy uv run python -m kb_platform.worker /tmp/smoke.db &

# 4. profiles → KB → doc → full job → query (see commands in session)
```

## Test impact

`uv run pytest -q` → **254 passed**; `uv run ruff check .` → clean. Two regression
tests added (one per bug).

## Screenshots

`docs/screenshots/real-llm-ollama-smoke-2026-06-28/`:

- `job-green-7of7.png` — job #2 detail, 7/7 steps green (extract_graph 1 unit,
  community_reports 1 unit).
- `llm-raw-output.png` — extract_graph unit's "LLM 输出" expanded: the real
  graphrag-format entity tuples `("entity"<|>宁德时代<|>ORGANIZATION<|>…)` the
  Ollama chat model returned.
- `graph.png` — graph tab: 5 manufacturer nodes (宁德时代/比亚迪/LG新能源/松下/三星SDI)
  + 10 relationships.
