# 流式回答(SSE)验证记录 — 2026-06-29

> 自动化测试已覆盖协议/聚合/渲染。本文档为真实 LLM + 浏览器的手动冒烟清单(需要:运行中的 API server + worker、一个已索引的 KB、带有效 key 的 provider profile)。

## 前置
- [x] `uv run python -m kb_platform.server` 与 `uv run python -m kb_platform.worker` 正常启动。
- [x] 至少一个 KB 已完成全量索引(含 community reports,以验证 global/drift)。
- [x] 该 KB 绑定有效 LLM provider profile(key 可用)。

## Chat 页流式
- [x] 进入 Chat 页,选 KB → 新建对话 → 提问:答案**逐字**出现(不是整段一次性出现)。
- [x] 追问(如"再说详细点"):顶部出现"理解为 {rewritten_query}"提示(meta 事件),答案随后逐字流出。
- [ ] 切到 `global` 方式提问:前若干秒在准备(map 阶段),随后答案逐字流出;`done` 后 sources 正常展示。*(2026-06-30 未在 Chat 单独跑 global;local/global 的 SSE 协议在 API 层已验证,global 端到端待补)*
- [x] 刷新页面:对话与消息恢复正常(A1 持久化不受影响)。
- [ ] 断网/停止:已流出文本保留,不假装完成。*(未测)*

## 检索测试页流式
- [x] QueryTestPage 提问:答案逐字出现,`done` 后耗时/sources 正常。*(在 `/kbs/1/query`(QueryPage)实测 SSE 逐 token + 耗时/sources;全局 `/query`(QueryTestPage)未单独发查询,但走同一 SSE 端点)*

## MCP 聚合
- [x] `uv run python -m kb_platform.mcp` 启动;agent 调 `query_knowledge_base` 返回**单个**完整答案(非增量),sources 正常 —— 即 MCP 内部聚合了 SSE。*(2026-06-30 用 `KbApiClient.query()` 直测:返回单个 dict,answer=1529 字 + 3 sources)*

## 降级
- [ ] 对一个无 community reports 的 KB 用 `global` 提问:返回 `error` 事件("no community reports…"),前端气泡/检索页显示错误,无 500。*(2026-06-30 **未干净复现**:用空 KB2 测,但因 data_root 多 KB 共用——见下「发现」——KB2 读到了 KB1 的数据,没进入"无 reports"状态;本地 llama3 对任意语料都产 reports,难稳定造该状态。该路径有单测覆盖,仅手动冒烟未亲验)*

---

## 执行记录 — 2026-06-30(真 LLM + 浏览器冒烟)

**环境**:冷启 server+worker(清代理)→ 建 2 个 Ollama provider profile(`llama3` chat + `nomic-embed-text` embed,无 remote key)→ 建 KB「冒烟KB」+ 灌 1 段电池行业语料 → 全量索引 90s 全绿(5 实体 / 1 关系 / 1 community / 1 community report / 3 LanceDB 表)→ Playwright 驱动 SPA + curl/python 探 API 契约。

**核心结论**:流式链路工作。API 原始 SSE `event: delta`/`{"text":"..."}` 逐 token;UI 渲染时抓到**中间帧**(答案停在半句 `...power batteries and has`,按钮显示「回答中…」)→ 证明真·逐字流式而非整段。

**证据**:`docs/screenshots` 无;冒烟截图存于仓库根 `smoke-02-chat-q1-done.png`(Q1 完成+sources)、`smoke-03-chat-q2-streaming.png`(Q2 流式中 + 改写提示「理解为:What is the location of CATL's headquarters?」)。

**发现(冒烟期)**:
1. ⚠️ **路由碰撞(已修)**:`/query-presets`、`/kbs` 等 SPA 路由与同名 GET API 端点碰撞 → 直接访问/刷新/深链返回原始 JSON。修复见 commit `5fa931d`(中间件:浏览器导航 `Sec-Fetch-Mode: navigate` 且响应为 JSON 时换发 SPA,带 `Cache-Control: no-store` + `Vary: Sec-Fetch-Mode` 防缓存串用)。新增测试 `tests/test_spa_served.py`。
2. ⚠️ **data_root 多 KB 共用(未修,既有平台问题)**:`routes_kbs.py:130` 每个 KB 存 `app.state.data_root`,无 per-KB 子目录;空 KB2 的查询读到了 KB1 的图谱。与 CLAUDE.md「data_root is per-KB」矛盾,建议团队确认。
3. 流式 token 统计 best-effort(看到不准的 output 数),符合「流式无 usage」已知限制。
4. 本地 llama3 答案默认英文(语言偏好,非平台问题)。
