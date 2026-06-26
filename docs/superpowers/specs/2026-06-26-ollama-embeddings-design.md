# Ollama 嵌入支持 — 让 local/basic/drift 查询可用

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：`graphrag-kb-platform` 后端索引路径（最小改动）+ 端到端验证。
> 前置：查询侧（`embedding_models` 注入）已在 2026-06-26 query real-logic matching 阶段（Task 13）完成。

## 1. 背景

DeepSeek 无嵌入模型 → `generate_text_embeddings` 步骤失败 → 无向量落盘 → `local`/`basic`/`drift` 查询无法检索。要让这三种方法可用，需要一个嵌入提供方。用户本机已装 Ollama 0.30.10，且已拉取 `nomic-embed-text`（768 维）。

根因（已核实）：索引侧 `build_default_adapter`（`kb_platform/graph/graphrag_adapter.py`）用**同一个** `model_config` 同时做 completion 和 embedding（第 280 行 `embedder = create_embedding(model_config)`）。因此即便 KB settings 里写了 `embedding`，索引也仍用 LLM 的 `model_config`（deepseek-chat）去 embed → 失败。查询侧（Task 13）已能从 `embedding` settings 注入 `embedding_models`，故**只需修索引侧**。

约束（已核实 graphrag-llm 源码）：
- `ModelConfig`（`graphrag_llm/config/model_config.py`）在 `auth_method=ApiKey`（默认）时**强制要求 `api_key`**（第 102-103 行），且 `AuthMethod` 枚举只有 `ApiKey`/`AzureManagedIdentity`，无「无鉴权」选项。
- Ollama 不需要 key，但 litellm 的 ollama provider 忽略 `api_key`。故对无 key 的 provider（如 ollama），用**占位 api_key**（"ollama"）满足校验。
- `create_embedding`（`graphrag_llm/embedding/embedding_factory.py:55`）按 `model_provider/model` 构造 `LiteLLMEmbedding`，`model_id="ollama/nomic-embed-text"`，litellm 经 `api_base=http://localhost:11434` 路由到本地 Ollama。

## 2. 目标 / 非目标

**目标**
- KB settings 配 `embedding`（Ollama nomic-embed-text）后，索引全流程成功（含 `generate_text_embeddings`），3 个向量集合（entity_description/text_unit_text/community_full_content）写入 LanceDB。
- `local`/`basic`/`drift` 查询返回真实答案 + 真实来源 + token；`global` 不受影响。
- 向后兼容：未配 `embedding` 时维持现行行为（用 LLM `model_config` embed，或无 embedder）。

**非目标**
- 不改 LLM provider（继续 DeepSeek 做 completion）。
- 不改前端（KB 模型配置卡已能显示 embedding.model）。
- 不做多 provider 路由 / 嵌入模型热切换。
- 不改查询侧代码（Task 13 已覆盖）。

## 3. 决策（brainstorm 已定）

- 索引侧给 `build_default_adapter` 加可选 `embed_model_config`；`build_adapter_from_settings` 从 `embedding` settings 派生。通用，不硬编码 Ollama。
- 向后兼容：`embed_model_config` 缺省时回退现行 `model_config`。
- 无 key 的 provider（ollama 等）用占位 api_key。

## 4. 设计

### 4.1 `build_default_adapter` 加 embed_model_config

`kb_platform/graph/graphrag_adapter.py`：
- 签名加 `embed_model_config=None`（keyword-only）。
- 第 280 行 `embedder = create_embedding(model_config)` 改为 `embedder = create_embedding(embed_model_config or model_config)`。
- 其余不变（completion 仍用 `model_config`）。

### 4.2 `build_adapter_from_settings` 派生 embed_model_config

新增模块级 helper：
```python
def _build_embed_model_config(settings: dict) -> "ModelConfig | None":
    """Build an embedding ModelConfig from KB `embedding` settings, or None.

    Credential resolution mirrors the LLM path; providers without a key
    (e.g. ollama) get a placeholder api_key so graphrag-llm's ApiKey validator
    passes (litellm ignores it for those providers).
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
    # ollama / local providers have no key; placeholder satisfies the validator.
    if not resolved and provider in {"ollama"}:
        resolved = "ollama"
    return ModelConfig(
        type=emb.get("type", "litellm"),
        model_provider=provider,
        model=emb.get("model", "text-embedding-3-small"),
        api_base=emb.get("api_base"),
        api_version=emb.get("api_version"),
        api_key=resolved,
    )
```
`build_adapter_from_settings` 在构造 `model_config`（LLM）后，调用 `_build_embed_model_config(settings)`，传给 `build_default_adapter(..., embed_model_config=embed_cfg)`。

> 注意：`_build_embed_model_config` 返回 None 时，`build_default_adapter` 回退到 `model_config`（现行行为）。若 `model_config`（LLM）本身不能 embed（如 DeepSeek），`create_embedding` 会在 `embed_factory` 构造时抛异常，被 `build_default_adapter` 既有的 `try/except` 捕获 → `embed_factory=None`（现行「embedding optional」降级）。即：不配 embedding → 行为与今天一致（embed 步骤失败，但不阻断其它）。

### 4.3 KB settings（验证用）

```json
{
  "llm": {"model_provider":"deepseek","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"},
  "embedding": {"model_provider":"ollama","model":"nomic-embed-text","api_base":"http://localhost:11434","api_key":"ollama"},
  "community_reports": {"structured_output": false}
}
```

### 4.4 查询侧（无代码改动，仅验证）

Task 13 的 `_resolve_config` 已注入 `embedding_models["default_embedding_model"]`（含 `api_base` + `api_key`）。验证：`local`/`basic`/`drift` 查询时，graphrag 用该 entry 构造 embedder 把 query 向量化 → 命中 LanceDB 向量。若 Ollama 占位 key 在查询侧也需生效——Task 13 的 resolved_key 优先读 `embedding.api_key`，settings 里写了 `"api_key":"ollama"` 即命中，无需改代码。

## 5. 验证（DeepSeek LLM + Ollama 嵌入，本机）

1. 起 worker + server（`DEEPSEEK_API_KEY` 入参；Ollama 已在 localhost:11434）。
2. 建 KB（4.3 settings）+ 传 1 文档 + 触发 full。
3. 断言：job 全 7 步**成功**（含 `generate_text_embeddings`）；`<data_root>/vectors/*.lance` 三张表存在。
4. 依次查 `local`/`basic`/`drift`/`global`：断言 `error==null`、`answer` 非空（`global` 仍受 DeepSeek 占位报告限制，可能 "unable to answer"，单独说明）、`elapsed_ms>0`、`local`/`drift` 的 `sources` 含实体 chips、`basic` 的 `sources` 含文本片段。
5. 记录到 `docs/verify-ollama-2026-06-26.md`。

## 6. 风险与对策

1. **向量维度不一致**：nomic-embed-text=768；LanceDB wrapper 在 `create_index` 前设 `vector_size`（既有测试覆盖）。若三张表维度不一致会报错——单测/实测会暴露。
2. **Ollama 未运行 / 模型未拉取**：验证步骤 1 前确认 `ollama list` 含 `nomic-embed-text`、`curl localhost:11434/api/tags` 200。
3. **graphrag-llm 对 ollama provider 的 api_key 校验**：占位 "ollama" 已满足 `ApiKey` validator；litellm 忽略。单测覆盖。
4. **embed 调用慢（本地 CPU）**：可接受（内部工具）；不阻塞。
5. **查询侧 embedding_models 缺 api_base**：Task 13 已含 `api_base`；若 graphrag 查询侧仍报错，作为 bug 单独修（本设计假设 Task 13 已覆盖，验证确认）。

## 7. 验收（Done）

- 索引全流程成功；3 张向量表落盘。
- `local`/`basic`/`drift` 真实可用（真实答案 + 来源 + token）。
- 现有 178 后端 + 24 前端测试全绿；新增 `_build_embed_model_config` 单测 + `build_default_adapter` 用 embed_model_config 的单测；ruff 干净。
- 未配 `embedding` 时行为不变（回归测试）。
- 验证记录归档。

## 8. 改动清单

- Modify: `kb_platform/graph/graphrag_adapter.py`（`build_default_adapter` 加参 + 1 行；`build_adapter_from_settings` 派生；新 `_build_embed_model_config`）。
- Test: `tests/test_embed_model_config.py`（新建）；`tests/test_build_adapter.py` 或既有（`build_default_adapter` 用 embed_model_config）。
- 验证产物：`docs/verify-ollama-2026-06-26.md`。
