# MCP 查询服务（供外部 Agent 调用）— 验证记录

- 日期: 2026-06-28
- 分支: main（工作树未提交）
- 规格: `docs/superpowers/specs/2026-06-28-mcp-query-server-design.md`
- 计划: `docs/superpowers/plans/2026-06-28-mcp-query-server.md`

## 功能

新增一个**可选的第三个进程** `python -m kb_platform.mcp`,以 **stdio MCP server** 形式把知识库
检索暴露给外部 AI agent(Claude Code / Claude Desktop / Cursor 等)。它是**瘦 HTTP 代理**:
只持 `httpx` client,转发到正在运行的 API server(`GET /kbs`、`POST /kbs/{id}/query`),
**不 import graphrag、不重复任何查询逻辑**。

- `kb_platform/mcp/server.py`:`KbApiError`、`KbApiClient`(可注入 httpx transport 的 seam)、
  tool 逻辑函数(`list_knowledge_bases` / `query_knowledge_base`)、`build_mcp_server`(注册 tool)。
- `kb_platform/mcp/__main__.py`:解析 `--api-url` / `KB_API_URL` → 建 client → `server.run(transport="stdio")`。
- 依赖:新增 optional extra `[mcp]`(`mcp` 官方 SDK,transitively 带 `httpx`);`dev` extra 同步加入。
- 文档:`README.md` / `README.zh.md` 新增"MCP 查询服务"章节(含 Claude Desktop/Code 接入配置);
  `CLAUDE.md` 记录第三个进程与"保持纯代理"约束。

**暴露的 tool:** `list_knowledge_bases`(发现 KB)、`query_knowledge_base(kb_id, query, method?)`
(`method` 默认 `local`,可选 `global`/`drift`/`basic`;返回 `{answer, method, sources}`,出错带 `error`)。

## 自动化验证

`tests/test_mcp_server.py`(12 例),client 经 `httpx.ASGITransport` 挂到带 `FakeQueryEngine` 的
真实 FastAPI app —— 真实路由往返,无 socket、无 LLM:

- `KbApiClient`:`list_kbs` 返回 KB、`query` 命中 FakeEngine;指向拒绝端口 → 抛 `KbApiError`。
- tool 逻辑:list 正常/空库/不可达错误形态;query 默认 `local`、显式 `basic`;不可达 → `{"error":…}` 不抛;
  stub client 验证 `error` 透传 + `sources` 规范化(None→[]) + 运营字段(`elapsed_ms`/`prompt_tokens`)裁剪。
- `build_mcp_server`:`await server.list_tools()` 含两个 tool 名。

全量:`uv run pytest -q` → **266 passed**(1 条 starlette/httpx 弃用警告,预先存在,非本次引入)。
Lint:`uv run ruff check .` → **All checks passed!**

> 注:`POST /kbs` 必填 `llm_profile_id`,且注入 `FakeQueryEngine` 时查询路由**跳过 KB 存在性检查**
> (忽略 `kb_id`)。故测试中 KB 经 Repository 直接种入;`error` 透传 / 字段裁剪用 stub client 单测。

## 端到端 stdio 烟雾验证

跑一次性脚本(`/tmp/mcp_smoke.py`,验证后删除):起真实 API server(FakeQueryEngine + 一个 KB,
uvicorn :18999),用**官方 `mcp` 客户端 SDK** spawn `python -m kb_platform.mcp --api-url …`
子进程,驱动完整 JSON-RPC 握手 + 调用。结果:

```
TOOLS: ['list_knowledge_bases', 'query_knowledge_base']
LIST : structuredContent.result = [{'id': 1, 'name': 'smoke-kb', 'method': 'standard'}]  (200 OK ← GET /kbs)
QUERY: content.text = {"answer":"[local] You asked: what is smoke?","method":"local","sources":[]}  (200 OK ← POST /kbs/1/query)
SMOKE OK
```

证明全链路:**mcp client → stdio JSON-RPC → MCP server → HTTP 代理 → API server → FakeQueryEngine**。
入口 `uv run python -m kb_platform.mcp --help` 正常输出 `--api-url`。

> 未做真 LLM 冒烟:MCP 是无逻辑代理,真 LLM 行为已在既有查询验证(`docs/verify-real-llm-ollama-smoke-2026-06-28.md`
> 等)覆盖;此处用 FakeEngine 即可证明接线正确。

## 非目标 / 延后

- HTTP / Streamable HTTP 传输(远程 agent)—— stdio 优先,后续增量加 transport,不影响 tool 定义。
- 独立型 MCP(直读 SQLite + 自建引擎)、写操作 tool、鉴权 —— 见 spec §2。
