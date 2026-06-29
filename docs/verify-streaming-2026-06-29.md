# 流式回答(SSE)验证记录 — 2026-06-29

> 自动化测试已覆盖协议/聚合/渲染。本文档为真实 LLM + 浏览器的手动冒烟清单(需要:运行中的 API server + worker、一个已索引的 KB、带有效 key 的 provider profile)。

## 前置
- [ ] `uv run python -m kb_platform.server` 与 `uv run python -m kb_platform.worker` 正常启动。
- [ ] 至少一个 KB 已完成全量索引(含 community reports,以验证 global/drift)。
- [ ] 该 KB 绑定有效 LLM provider profile(key 可用)。

## Chat 页流式
- [ ] 进入 Chat 页,选 KB → 新建对话 → 提问:答案**逐字**出现(不是整段一次性出现)。
- [ ] 追问(如"再说详细点"):顶部出现"理解为 {rewritten_query}"提示(meta 事件),答案随后逐字流出。
- [ ] 切到 `global` 方式提问:前若干秒在准备(map 阶段),随后答案逐字流出;`done` 后 sources 正常展示。
- [ ] 刷新页面:对话与消息恢复正常(A1 持久化不受影响)。
- [ ] 断网/停止:已流出文本保留,不假装完成。

## 检索测试页流式
- [ ] QueryTestPage 提问:答案逐字出现,`done` 后耗时/sources 正常。

## MCP 聚合
- [ ] `uv run python -m kb_platform.mcp` 启动;agent 调 `query_knowledge_base` 返回**单个**完整答案(非增量),sources 正常 —— 即 MCP 内部聚合了 SSE。

## 降级
- [ ] 对一个无 community reports 的 KB 用 `global` 提问:返回 `error` 事件("no community reports…"),前端气泡/检索页显示错误,无 500。
