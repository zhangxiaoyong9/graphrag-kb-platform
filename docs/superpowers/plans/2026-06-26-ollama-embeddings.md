# Ollama 嵌入支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 KB 配 Ollama 嵌入（`nomic-embed-text`）后，索引全流程成功、`local`/`basic`/`drift` 查询返回真实答案。

**Architecture:** 索引侧 `build_default_adapter` 加可选 `embed_model_config`，由 `build_adapter_from_settings` 从 KB `embedding` settings 派生（新 helper `_build_embed_model_config`）；缺省回退现行 `model_config`。查询侧不改（Task 13 已注入 `embedding_models`）。

**Tech Stack:** Python 3.11 + uv + graphrag 3.1 + graphrag-llm + litellm（ollama provider）+ Ollama 0.30.10（`nomic-embed-text`）+ pytest/ruff。

## Global Constraints

- 后端 `uv run pytest` / `uv run ruff check .`；kb-platform **无 pyright/poe/semversioner**（ruff only）。
- Ollama 本机 `http://localhost:11434`，模型 `nomic-embed-text`（768 维，已 `ollama pull`）。
- graphrag-llm `ModelConfig` 在 `auth_method=ApiKey`（默认）时**强制 `api_key`**；Ollama 无 key → 占位 `"ollama"`（litellm 忽略）。
- 向后兼容：`embed_model_config` 缺省时 `build_default_adapter` 回退 `model_config`（现行行为不变）。
- 不改查询侧代码、不改前端。
- 每个 Python 文件沿用其现有头部约定。

---

## File Structure

- `kb_platform/graph/graphrag_adapter.py`（改）：新增模块级 `_build_embed_model_config`；`build_default_adapter` 加 `embed_model_config` 形参；`build_adapter_from_settings` 派生并透传。
- `tests/test_embed_model_config.py`（新建）：helper 行为单测。
- `tests/test_build_default_adapter_embed.py`（新建）：`build_default_adapter` 优先用 `embed_model_config` 的接线单测。
- `docs/verify-ollama-2026-06-26.md`（新建，验证产物）。

---

## Task 1: 嵌入 ModelConfig 派生 + 接入索引路径

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py`
- Test: `tests/test_embed_model_config.py`（新建）、`tests/test_build_default_adapter_embed.py`（新建）

**Interfaces:**
- Produces: 模块级 `_build_embed_model_config(settings: dict) -> ModelConfig | None`；`build_default_adapter(*, data_root, model_config, embed_model_config=None, max_gleanings=0)`。

- [ ] **Step 1: 写 helper 的失败测试**

`tests/test_embed_model_config.py`：
```python
"""_build_embed_model_config: derive an embedding ModelConfig from KB settings.

Ollama has no api key -> placeholder 'ollama' (graphrag-llm requires one for
ApiKey auth; litellm ignores it for ollama). Keyed providers without a key
return None (leave embedding unconfigured rather than crash indexing).
"""
from kb_platform.graph.graphrag_adapter import _build_embed_model_config


def test_no_embedding_settings_returns_none():
    assert _build_embed_model_config({}) is None
    assert _build_embed_model_config({"llm": {"model": "x"}}) is None


def test_ollama_gets_placeholder_key_and_api_base():
    cfg = _build_embed_model_config(
        {
            "embedding": {
                "model_provider": "ollama",
                "model": "nomic-embed-text",
                "api_base": "http://localhost:11434",
            }
        }
    )
    assert cfg is not None
    assert cfg.model_provider == "ollama"
    assert cfg.model == "nomic-embed-text"
    assert cfg.api_base == "http://localhost:11434"
    assert cfg.api_key == "ollama"  # placeholder


def test_explicit_api_key_used():
    cfg = _build_embed_model_config(
        {"embedding": {"model_provider": "openai", "model": "text-embedding-3-small", "api_key": "sk-real"}}
    )
    assert cfg.api_key == "sk-real"


def test_env_api_key_used(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    cfg = _build_embed_model_config(
        {"embedding": {"model_provider": "openai", "model": "text-embedding-3-small"}}
    )
    assert cfg.api_key == "sk-env"


def test_keyed_provider_without_key_returns_none():
    cfg = _build_embed_model_config(
        {"embedding": {"model_provider": "openai", "model": "text-embedding-3-small"}}
    )
    assert cfg is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv run pytest tests/test_embed_model_config.py -v`
Expected: FAIL — `cannot import name '_build_embed_model_config'`。

- [ ] **Step 3: 实现 helper**

在 `kb_platform/graph/graphrag_adapter.py`，`build_default_adapter` 定义**之前**加模块级函数：
```python
def _build_embed_model_config(settings: dict):
    """Build an embedding ModelConfig from KB `embedding` settings, or None.

    Mirrors the LLM credential resolution in ``build_adapter_from_settings``.
    Providers without a key (ollama) get a placeholder api_key so graphrag-llm's
    ApiKey validator passes (litellm ignores it for ollama). A keyed provider
    whose key can't be resolved returns None -> embedding left unconfigured
    (build_default_adapter then falls back to the LLM model_config, whose own
    embedding creation is best-effort / optional).
    """
    emb = settings.get("embedding") or {}
    if not emb:
        return None
    import os

    from graphrag_llm.config import ModelConfig

    provider = emb.get("model_provider", "openai")
    resolved = (
        emb.get("api_key")
        or (os.getenv(emb["api_key_env"]) if emb.get("api_key_env") else None)
        or os.getenv(f"{provider.upper()}_API_KEY")
    )
    if not resolved:
        if provider == "ollama":
            resolved = "ollama"
        else:
            return None
    return ModelConfig(
        type=emb.get("type", "litellm"),
        model_provider=provider,
        model=emb.get("model", "text-embedding-3-small"),
        api_base=emb.get("api_base"),
        api_version=emb.get("api_version"),
        api_key=resolved,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_embed_model_config.py -v`
Expected: PASS（5 个）。

- [ ] **Step 5: 写接线失败测试**

`tests/test_build_default_adapter_embed.py`：
```python
"""build_default_adapter uses embed_model_config for the embedder when provided."""
from graphrag_llm.config import ModelConfig

from kb_platform.graph.graphrag_adapter import build_default_adapter


def test_embed_model_config_is_used_for_embedder(monkeypatch):
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod

    seen: dict = {}

    def fake_completion(mc):
        seen["completion"] = mc
        return object()

    class _FakeEmbedder:
        def embedding_batch(self, texts):
            return [[0.0] for _ in texts]

    def fake_embedding(mc):
        seen["embedding"] = mc
        return _FakeEmbedder()

    monkeypatch.setattr(comp_mod, "create_completion", fake_completion)
    monkeypatch.setattr(emb_mod, "create_embedding", fake_embedding)

    llm_cfg = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")
    emb_cfg = ModelConfig(
        model_provider="ollama",
        model="nomic-embed-text",
        api_key="ollama",
        api_base="http://localhost:11434",
    )
    adapter = build_default_adapter(
        data_root="/tmp/_unused_", model_config=llm_cfg, embed_model_config=emb_cfg
    )
    # completion still uses the LLM config; embedding uses the embed config
    assert seen["completion"] is llm_cfg
    assert seen["embedding"] is emb_cfg
    # the adapter's embed_factory yields the embedder built from emb_cfg
    assert isinstance(adapter._embed_factory(), _FakeEmbedder)


def test_falls_back_to_model_config_when_no_embed_config(monkeypatch):
    import graphrag_llm.completion as comp_mod
    import graphrag_llm.embedding as emb_mod

    seen: dict = {}
    monkeypatch.setattr(comp_mod, "create_completion", lambda mc: seen.setdefault("completion", mc) or object())
    monkeypatch.setattr(emb_mod, "create_embedding", lambda mc: seen.setdefault("embedding", mc) or object())

    llm_cfg = ModelConfig(model_provider="openai", model="gpt-4o-mini", api_key="sk-x")
    build_default_adapter(data_root="/tmp/_unused_", model_config=llm_cfg)
    assert seen["embedding"] is llm_cfg  # fallback to LLM config (current behavior)
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest tests/test_build_default_adapter_embed.py -v`
Expected: FAIL — `build_default_adapter() got an unexpected keyword argument 'embed_model_config'`（第一支）；第二支可能先过。

- [ ] **Step 7: 接线实现**

在 `kb_platform/graph/graphrag_adapter.py`：

(a) `build_default_adapter` 签名加 `embed_model_config=None`：
```python
def build_default_adapter(
    *,
    data_root: str,
    model_config,
    embed_model_config=None,
    max_gleanings: int = 0,
) -> GraphRagAdapter:
```

(b) 把创建 embedder 的 `embedder = create_embedding(model_config)` 一行改为：
```python
        embedder = create_embedding(embed_model_config or model_config)
```

(c) `build_adapter_from_settings` 在 `return build_default_adapter(...)` 之前派生并透传。把函数末尾：
```python
    model_config = ModelConfig(
        type=llm.get("type", "litellm"),
        model_provider=provider,
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=resolved_key,
    )
    return build_default_adapter(data_root=data_root, model_config=model_config)
```
改为：
```python
    model_config = ModelConfig(
        type=llm.get("type", "litellm"),
        model_provider=provider,
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=resolved_key,
    )
    embed_model_config = _build_embed_model_config(settings)
    return build_default_adapter(
        data_root=data_root,
        model_config=model_config,
        embed_model_config=embed_model_config,
    )
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run pytest tests/test_build_default_adapter_embed.py tests/test_embed_model_config.py -v`
Expected: PASS（全部）。

- [ ] **Step 9: 全量回归 + ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全绿（既有 178 + 新增测试）、ruff 干净。

- [ ] **Step 10: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/graph/graphrag_adapter.py tests/test_embed_model_config.py tests/test_build_default_adapter_embed.py
git commit -m "feat(embed): support separate embedding ModelConfig (Ollama nomic-embed-text) in indexing path"
```

---

## Task 2: Ollama + DeepSeek 端到端验证（手动 runbook）

**Files:** 无代码；产出 `docs/verify-ollama-2026-06-26.md`。

**前置：** `ollama list` 含 `nomic-embed-text`；`curl -s http://localhost:11434/api/tags` 200。密钥只用环境变量 `$DEEPSEEK_API_KEY`，不入库/不写文件。`web/dist` 需为最新（Task 1 不改前端，dist 维持；若改过则 `cd web && npm run build`）。

- [ ] **Step 1: 起临时后端**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
rm -f /tmp/vo.db /tmp/vo.db-shm /tmp/vo.db-wal && rm -rf /tmp/vo_data && mkdir -p /tmp/vo_data
uv run alembic -x db=/tmp/vo.db upgrade head
# 终端 A（worker）：
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY uv run python -m kb_platform.worker /tmp/vo.db
# 终端 B（server，loop=asyncio，Task 1 代码）：
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY uv run python -m kb_platform.server /tmp/vo.db /tmp/vo_data 127.0.0.1 8000
```

- [ ] **Step 2: 建 KB（DeepSeek LLM + Ollama 嵌入）+ 传文档 + 全量索引**

```bash
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs -H 'Content-Type: application/json' \
  -d '{"name":"vo","method":"standard","settings_yaml":"{\"llm\":{\"model_provider\":\"deepseek\",\"model\":\"deepseek-chat\",\"api_key_env\":\"DEEPSEEK_API_KEY\"},\"embedding\":{\"model_provider\":\"ollama\",\"model\":\"nomic-embed-text\",\"api_base\":\"http://localhost:11434\",\"api_key\":\"ollama\"},\"community_reports\":{\"structured_output\":false}}"}'
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs/1/documents -H 'Content-Type: application/json' \
  -d '{"title":"电池产业链","text":"宁德时代(CATL)是全球最大的动力电池制造商，与特斯拉(Tesla)签订长期供货协议，为其Model 3/Y提供磷酸铁锂电池。特斯拉其他电池供应商还有LG新能源和松下。比亚迪(BYD)是宁德时代国内主要竞争对手。宁德时代还向宝马、奔驰供货。"}'
curl -s --noproxy '*' -X POST http://127.0.0.1:8000/kbs/1/jobs -H 'Content-Type: application/json' -d '{"type":"full"}'
```

- [ ] **Step 3: 断言索引全 7 步成功 + 向量落盘**

轮询 `curl -s --noproxy '*' http://127.0.0.1:8000/jobs/1` 至 `status` 终态；断言 7 步全 `succeeded`（含 `generate_text_embeddings`）。断言 `<data_root>/vectors/` 下出现 LanceDB 表（`entity_description`/`text_unit_text`/`community_full_content`）。

- [ ] **Step 4: 查询 local/basic/drift 断言真实答案 + 来源 + token**

```bash
for M in local basic drift; do
  echo "=== $M ==="
  curl -s --noproxy '*' -m 120 -X POST http://127.0.0.1:8000/kbs/1/query \
    -H 'Content-Type: application/json' -d "{\"method\":\"$M\",\"query\":\"宁德时代和特斯拉的关系？\"}" | python3 -m json.tool
done
```
断言（每个）：`error == null`、`elapsed_ms > 0`、`prompt_tokens > 0`；`local`/`drift` 的 `sources` 含实体；`basic` 的 `sources` 含文本片段。`global` 单独验证（受 DeepSeek 占位报告限制，可能 "unable to answer"——记录即可，非本任务阻断项）。

- [ ] **Step 5: 记录 + 清理**

把结果（索引步骤状态、向量表清单、各查询方法的 answer/error/elapsed/tokens/sources 摘要）写入 `docs/verify-ollama-2026-06-26.md`。停止 worker/server，`rm -f /tmp/vo.db* && rm -rf /tmp/vo_data`。

- [ ] **Step 6: 提交**

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add docs/verify-ollama-2026-06-26.md
git commit -m "docs: Ollama embedding end-to-end verification record"
```

---

## Self-Review

- **Spec coverage**：spec 4.1（build_default_adapter 加参 + 1 行）→ Task 1 Step 7(a)(b)；4.2（helper + build_adapter_from_settings 派生）→ Task 1 Step 3 + 7(c)；4.3（KB settings）→ Task 2 Step 2；4.4（查询侧无代码改动，仅验证）→ Task 2 Step 4；5（验证）→ Task 2；6（风险）→ 占位 key/维度由 helper 单测与实测覆盖；7（验收）→ Task 1 Step 9 + Task 2。全覆盖。
- **占位符扫描**：无 TBD/TODO；每步含完整代码。
- **类型一致性**：`_build_embed_model_config(settings: dict) -> ModelConfig | None`、`build_default_adapter(*, data_root, model_config, embed_model_config=None, max_gleanings=0)` —— Task 1 Step 3/7 与测试一致；`embed_model_config or model_config` 回退语义一致。
- **已知边界**：`build_default_adapter` 既有 `try/except` 包 `create_embedding`（embed 可选）——`embed_model_config or model_config` 在 `embed_model_config=None` 时回退 `model_config`，DeepSeek 这类无嵌入能力的 LLM 仍走 try/except 降级，行为与今天一致（Task 1 Step 9 回归测试 + 既有套件覆盖）。
