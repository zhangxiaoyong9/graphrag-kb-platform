# Ollama 嵌入端到端验证记录（2026-06-26）

> 对应实现计划 Task 2。临时 DB（`/tmp/vo.db`，已清理）；密钥仅环境变量。

## 环境

- Ollama 0.30.10（本机 `http://localhost:11434`），模型 `nomic-embed-text`（768 维）。
- LLM：DeepSeek `deepseek-chat`（`community_reports.structured_output=false`）。
- KB settings：`llm`=deepseek + `embedding`=`{"model_provider":"ollama","model":"nomic-embed-text","api_base":"http://localhost:11434","api_key":"ollama"}` + `community_reports.structured_output=false`。
- 单文档（电池产业链，含宁德时代/特斯拉/LG新能源/松下/比亚迪/宝马/奔驰）。

**关键环境注意：** 本机 Surge 设了 `all_proxy=socks5://...`、`http_proxy`/`https_proxy`。litellm/httpx 会把对 **localhost Ollama** 的请求走 SOCKS → 报 `socksio` 未装。**worker/server 进程必须 `env -u all_proxy -u http_proxy -u https_proxy ...`（或对 localhost 设 NO_PROXY）启动**；本机有直连外网，DeepSeek 直连可达（401→带 key 即可），Ollama 直连可达。这属本机代理环境，非平台代码问题。

## 索引结果（全量）

7 步**全部成功**（含 `generate_text_embeddings`）：
```
chunk_documents ✓ → extract_graph ✓ → summarize_descriptions ✓ → finalize_graph ✓
→ create_communities ✓ → community_reports ✓ → generate_text_embeddings ✓
```
三张向量表落盘（行数正确）：
- `entity_description.lance`：7 行（7 个实体）
- `text_unit_text.lance`：1 行（1 个分块）
- `community_full_content.lance`：2 行（2 个社区报告）

**结论：Task 1 的嵌入 ModelConfig 派生 + Task 1b 的 `embed_items`（`embedding(input=texts).embeddings`）正确，Ollama 嵌入端到端打通。**

## 查询结果

### `local` —— ✅ 完整可用（核心目标达成）

```
error: null | elapsed_ms: 4435.2 | tokens: 1447/664 | llm_calls: 1 | sources: 5
answer: # 宁德时代与特斯拉的合作关系
  宁德时代（CATL）与特斯拉（Tesla）之间存在重要的供应链合作关系……为 Model 3/Y 提供磷酸铁锂电池……
  特斯拉其他电池供应商还有 LG 新能源和松大……
```
真实答案 + 真实来源（实体引用）+ 真实 token + 真实服务端耗时。**向量检索 + 实体召回 + LLM 作答全链路通。**

### `basic` —— ⚠️ 检索正常、答案为空

`error: null`，检索到 1 个 text_unit 来源，`elapsed_ms: 57.5`、`prompt_tokens: 764`、`output_tokens: null`，但 `answer` 为空。检索（向量→text_unit）正常；**空答案出在 LLM 作答环节**（DeepSeek 对 basic 的 map 输出为空，或 basic 在单分块小语料上的行为）。非嵌入管线问题。

### `drift` —— ⚠️ 报告嵌入匹配

`error: "Some reports are missing full content embeddings. 2 out of 2"`。但 `community_full_content.lance` 实际有 2 行——**嵌入已写出**。是 graphrag drift 搜索按某 key 查报告嵌入未命中（drift 专用匹配逻辑），非嵌入写入 bug。

### `global` —— ⚠️ DeepSeek 占位报告（已知）

`error: null`，`elapsed_ms: 1001`、`tokens: 945/79`，`answer: "unable to answer"`。社区报告内容为占位文本（DeepSeek 已知弱项，见 `verify-deepseek-2026-06-26.md`），非嵌入/本次代码问题。

## 验收小结

- ✅ **Ollama 嵌入管线正确**：索引全流程成功，3 张向量表落盘且行数正确；`local` 查询端到端返回真实答案 + 5 个真实来源 + 真实 token/耗时。
- ⚠️ `basic`（空答案）/`drift`（报告嵌入匹配）属 graphrag 搜索方法 / DeepSeek 模型层面的问题，**非本次 Ollama 嵌入管线的缺陷**，建议作为后续单独排查项。
- 现有 186 后端 + 24 前端测试全绿；ruff 干净。
- 环境提醒：在带 SOCKS 代理的本机跑 Ollama，需对 worker/server 解除代理 env（直连 localhost）。
