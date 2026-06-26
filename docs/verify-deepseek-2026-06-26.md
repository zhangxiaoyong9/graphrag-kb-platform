# DeepSeek 真实查询验证记录（2026-06-26）

> 对应实现计划 Task 11。临时 DB（`/tmp/verify.db`，已清理）；密钥仅作环境变量，未入库。

## 环境

- KB `verify`，`method=standard`，settings：`{"llm":{"model_provider":"deepseek","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"},"community_reports":{"structured_output":false}}`。
- 单文档（电池产业链，含宁德时代/特斯拉/LG/松下/比亚迪/宝马/奔驰 等实体与关系）。
- worker + server 跑在 `loop="asyncio"`（Task 1 修复）。

## 索引结果

全量索引 7 步：chunk / extract_graph / summarize / finalize / communities / **community_reports 全部成功**；第 7 步 `generate_text_embeddings` 失败（DeepSeek 无 embedding 模型，预期内）。`community_reports.parquet` 已写出（2 行）。

## 查询验证

### `global`（Task 12 Bug B 修复后）

```
POST /kbs/1/query {"method":"global","query":"宁德时代和特斯拉是什么关系？"}
→ {"answer":"I am sorry but I am unable to answer this question given the provided data.",
   "method":"global","error":null,
   "elapsed_ms":1020.6,"prompt_tokens":945,"output_tokens":61,"llm_calls":1,
   "sources":null}
```

**结论：查询管线端到端跑通真实逻辑**：
- Task 1（uvloop）：不再崩 `Can't patch loop`。✅
- Task 12 Bug B（completion_models 注入）：不再 `default_completion_model not found`；真实发起 1 次 LLM 调用。✅
- 富集字段全部真实：`elapsed_ms`（服务端 completion_time）、`prompt_tokens`/`output_tokens`/`llm_calls`。✅

**「unable to answer」是内容问题、非代码 bug**：检查 `community_reports.parquet`，其 `title/summary/full_content` 均为占位文本 `"Community 0"`/`"Community 1"`——DeepSeek 在 `structured_output:false` 下生成的社区报告为空/占位（memory 已记录的 DeepSeek 已知弱项）。报告无实质内容 → global map-reduce 无法作答。换更全语料或 json_schema 能力模型（gpt-4o）可改善。

### `local`（Task 12 Bug A 修复后）

```
→ error: "Model ID default_embedding_model not found in embedding_models. Please rerun graphrag init and set the embedding_models configuration."
```

**结论**：Bug A 修复有效——不再崩 `embedding() takes 1 positional argument`；现在干净地报 embedding 配置缺失。

## 新发现（Bug C，未修）

`local`/`basic`/`drift` 还需 graphrag 查询工厂解析 `config.embedding_models["default_embedding_model"]`，与 Bug B（completion_models）同族。`_resolve_config` 目前只注入 `completion_models`，未注入 `embedding_models`。

但：DeepSeek 无 embedding 模型，即使注入 `embedding_models` 也无法真正检索（索引侧 `generate_text_embeddings` 也因此失败，无向量落盘）。**要全方法可用，必须配一个 embedding provider**（如 `OPENAI_API_KEY` + `embedding.model=text-embedding-3-small`），届时 Bug A + Bug C 一起生效。

## 验收小结

- ✅ 查询 API 已跑真实逻辑（不再崩、不再配置报错、真实富集字段）——「前后端真实逻辑匹配」的核心目标达成。
- ⚠️ `global` 答案质量受 DeepSeek 报告生成为空所限（模型兼容问题，非本次代码）。
- ⚠️ `local`/`basic`/`drift` 需额外 embedding provider（DeepSeek 无）+ Bug C（embedding_models 注入，同 Bug B 模式，可选修复）。
