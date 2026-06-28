# MCP 查询服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 stdio MCP server(`python -m kb_platform.mcp`),作为瘦 HTTP 代理把知识库检索暴露给外部 agent,复用既有 API 查询逻辑,不 import graphrag。

**Architecture:** 见 spec `docs/superpowers/specs/2026-06-28-mcp-query-server-design.md`。三段式:`KbApiClient`(httpx seam,可注入 transport)→ 纯 tool 逻辑函数(`list_knowledge_bases` / `query_knowledge_base`,关闭于 client)→ `build_mcp_server`(用官方 `mcp` SDK 注册 tool)+ `__main__`(stdio 入口)。

**Tech Stack:** Python 3.11 + uv + 官方 `mcp` Python SDK(v1.x)+ httpx + pytest/ruff。

## Global Constraints

- 后端 `uv run ruff check .`(line-length 100,py311)、`uv run pytest`(`asyncio_mode=auto`)。
- **版本兼容**:`mcp` SDK v1.12.4 已把 `FastMCP` 重命名为 `MCPServer`(`mcp.server.mcpserver`)。用 shim:优先 `from mcp.server.mcpserver import MCPServer`,失败回退 `from mcp.server.fastmcp import FastMCP`。`mcp.run()` 无参即 stdio(v1/v2 一致)。**实现前先 `uv sync --extra mcp` 后用 `python -c` 实地确认 import 路径与 `run`/`list_tools` 签名,再据此写代码。**
- `mcp` 是 **optional extra**,不放主依赖;`kb_platform/mcp/` 对 `mcp`/`httpx` 的 import 全部在模块内(lazy)。主包 `import kb_platform` 不触发 MCP 依赖。
- 测试用 `httpx.ASGITransport(app=create_app(repo, query_engine=FakeQueryEngine()))` 真实路由往返,**无 socket、无 LLM**。
- 不改前端、不改既有 API 路由、不改 worker;不动 alembic;不改 DB。

## File Structure

- `pyproject.toml`(改):新增 `mcp = ["mcp>=1.2"]` extra;`dev` 追加 `mcp>=1.2`。
- `kb_platform/mcp/__init__.py`(新建):空(或一行 docstring)。
- `kb_platform/mcp/server.py`(新建):`KbApiError`、`KbApiClient`、tool 逻辑函数、`build_mcp_server`。
- `kb_platform/mcp/__main__.py`(新建):解析 `--api-url`/`KB_API_URL` → 建 client → `build_mcp_server` → `mcp.run()`。
- `tests/test_mcp_server.py`(新建):client + tool 逻辑 + build_mcp_server 接线测试。
- `docs/verify/` 或 README:agent 接入配置示例(随 verify 记录写)。

---

## Task 1: 加 `mcp` extra + 安装 + 实地确认 SDK API

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1:** `pyproject.toml` 加 `mcp = ["mcp>=1.2"]`;`dev` 列表追加 `"mcp>=1.2"`。
- [ ] **Step 2:** `uv sync --extra mcp` 安装。
- [ ] **Step 3:** 实地确认:`` uv run python -c "import mcp.server.mcpserver as m; print([x for x in dir(m) if 'Server' in x or x=='Context'])" `` 与 `... ; s=m.MCPServer('t'); print(hasattr(s,'run'), hasattr(s,'list_tools'))"`。记下 `MCPServer.run` 是否接受 `transport=`、`list_tools` 是否 async。据此定型 shim 与调用。
- [ ] **Step 4:** `uv run python -c "import mcp; print(mcp.__version__)"` 记录实际版本。

## Task 2: `KbApiClient`(TDD — 先写测试)

**Files:**
- Test: `tests/test_mcp_server.py`(新建)
- Impl: `kb_platform/mcp/server.py`(新建)

- [ ] **Step 1(写失败测试):** `tests/test_mcp_server.py`:
  - 建 repo(沿用 `tests/conftest.py` 既有 fixture / helper,或直接 `Repository(create_engine("sqlite:///:memory:"))` + 建表)+ 建 2 个 KB。
  - `app = create_app(repo, query_engine=FakeQueryEngine())`;`http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")`。
  - `client = KbApiClient("http://testserver", http=http)`。
  - `await client.list_kbs()` → 断言两个 KB 的 `id/name/method`。
  - `await client.query(1, "local", "hello")` → 断言 `answer` 含 `"[local]"`、`method=="local"`。
  - client 指向 `http://127.0.0.1:1`(连不上)→ `await client.list_kbs()` 抛 `KbApiError`。
- [ ] **Step 2(实现):** `kb_platform/mcp/server.py`:定义 `KbApiError(Exception)`;`KbApiClient.__init__(base_url, *, timeout=180.0, http=None)`(无 http 则自建 `httpx.AsyncClient(base_url=..., timeout=...)`);`list_kbs`→`GET /kbs` 非 2xx 或网络错 → `KbApiError`;`query`→`POST /kbs/{id}/query` json=`{method,query}` → `.json()`。`aclose()` 关自建 client(注入的不关)。
- [ ] **Step 3(跑):** `uv run pytest tests/test_mcp_server.py` → 新测试绿;`uv run ruff check .` 过。

## Task 3: tool 逻辑函数(TDD)

**Files:**
- Test: `tests/test_mcp_server.py`(追加)
- Impl: `kb_platform/mcp/server.py`(追加)

- [ ] **Step 1(写失败测试):**
  - `list_knowledge_bases(client)` → 返回 `list[dict]`(同 `list_kbs`,空库 → `[]`)。
  - `query_knowledge_base(client, kb_id=1, query="hi")` → 默认 `method="local"`,返回含 `answer/method/sources` 的 dict。
  - `query_knowledge_base(client, kb_id=1, query="hi", method="basic")` → `method=="basic"`。
  - 错误路径:用连不上的 client → `query_knowledge_base(...)` 返回 `{"error": ...}` **不抛**;KB 不存在(API 返 `error` 字段)→ dict 含 `error`。
- [ ] **Step 2(实现):** 纯 `async def` 函数,签名带 `Literal["local","global","drift","basic"]`;try/except `KbApiError` → `{"error": str(e)}`;成功 → 精简为 `{answer, method, error, sources:[{kind,name,text}]}`(省略 token 用量等)。
- [ ] **Step 3(跑):** 绿 + ruff 过。

## Task 4: `build_mcp_server` + `__main__`(TDD)

**Files:**
- Test: `tests/test_mcp_server.py`(追加)
- Impl: `kb_platform/mcp/server.py`(追加)、`kb_platform/mcp/__main__.py`、`kb_platform/mcp/__init__.py`

- [ ] **Step 1(写失败测试):** `server = build_mcp_server(client)`;按 Task 1 实地确认的方式取已注册 tool 名集合 → 断言含 `list_knowledge_bases` 与 `query_knowledge_base`(若 `await server.list_tools()` 可用则用它,取 `[t.name for t in tools]`;否则 introspect 内部 tool 注册表)。验证接线,不跑完整 stdio 握手。
- [ ] **Step 2(实现):** shim import `_McpServer`;`build_mcp_server(client)` 实例化 `_McpServer("kb-platform")`,用 `@mcp.tool()` 注册两个 thin wrapper(关闭于 `client`,直接调 Task 3 的逻辑函数 + docstring 描述 tool),返回 `mcp`。
- [ ] **Step 3(实现 `__main__.py`):** `argparse` 解析 `--api-url`(默认 `os.environ.get("KB_API_URL","http://127.0.0.1:8000")`);建 `KbApiClient` → `build_mcp_server` → `server.run()`(stdio)。`mcp` 未装 → `ImportError` 带 "uv sync --extra mcp" 提示(把 mcp import 放进 `__main__`/`build_mcp_server`,模块顶部也 import httpx,缺依赖时报清晰)。
- [ ] **Step 4(跑):** 测试绿 + ruff 过。

## Task 5: 回归 + 烟雾验证

- [ ] **Step 1:** `uv run pytest`(全套,确认新 extra / 新模块不破坏既有)+ `uv run ruff check .`。
- [ ] **Step 2:** `uv run python -m kb_platform.mcp --help` → 正常输出(确认入口可 import、`--api-url` 可见)。
- [ ] **Step 3(手验,可选真 LLM):** 启 API server + worker(已索引一个 KB),另起 `uv run python -m kb_platform.mcp`,用 `mcp` inspector 或 agent 配置后调 `list_knowledge_bases` + `query_knowledge_base`;记录到 `docs/`(沿用 verify 流程)。无真 LLM 则跳过并注明。
- [ ] **Step 4:** 写 agent 接入配置示例(README 或 docs/),含 `claude_desktop_config.json` 片段。
