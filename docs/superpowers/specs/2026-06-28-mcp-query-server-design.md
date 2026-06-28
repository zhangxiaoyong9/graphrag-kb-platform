# MCP 查询服务 — 让外部 Agent 调用知识库检索 — 设计文档

- 日期: 2026-06-28
- 状态: 已批准(按推荐方案:stdio 瘦代理)
- 上游: 总体 spec `2026-06-24-kb-platform-design.md` §查询;查询 seam `kb_platform/query/engine.py`、API `POST /kbs/{id}/query`、`GET /kbs`。
- 依赖: 现有 HTTP API server(查询入口已在生产跑通:`assemble_kb_settings` 解析 profile + 解密 key → `GraphRagQueryEngine`)。

## 1. 背景与目标

平台已具备完整的知识库检索能力(四种方法:`local` / `global` / `drift` / `basic`),但目前只通过 HTTP API + 仪表盘暴露。希望把它**再包一层**,以 [MCP(Model Context Protocol)](https://modelcontextprotocol.io) 标准对外,让其他 AI agent(Claude Code / Claude Desktop / Cursor 等)能像调用普通工具一样检索知识库,无需自己拼 HTTP、无需知道 GraphRAG 细节。

**成功标准:**
- 一个新进程 `python -m kb_platform.mcp` 以 **stdio MCP server** 形式运行,可被标准 MCP client 配置后 spawn。
- 暴露两个 tool:`list_knowledge_bases`(发现可用 KB)、`query_knowledge_base`(检索)。
- MCP 进程**不 import graphrag**,纯 HTTP 代理到已运行的 API server → 复用全部既有查询逻辑(设置解析、引擎构建、错误处理),零重复。
- 连不上 API / KB 不存在 / 检索失败 → tool 返回结构化错误,不崩溃 MCP 进程。
- 有后端单测覆盖(真实 HTTP 往返到带 `FakeQueryEngine` 的 API,无 LLM)。

## 2. 范围(YAGNI)

**做:**
- `kb_platform/mcp/` 新包:`server.py`(tool 逻辑 + `KbApiClient` + `build_mcp_server`)、`__main__.py`(stdio 入口)。
- 两个 MCP tool:list / query。
- 可选依赖 extra `[mcp]`(官方 `mcp` SDK,含 `FastMCP`)。
- agent 接入配置示例文档。

**不做(v1):**
- HTTP / Streamable HTTP 传输(远程 agent)。stdio 优先;后续要远程再增量加 transport,不影响 tool 定义。
- 独立型 MCP(直读 SQLite + 自建引擎)——会重复 settings 解析逻辑,弃。
- 索引/文档管理类 tool(写操作、文档上传)——只读检索优先,避免给 agent 意外的写权限。
- 鉴权 / API token(API server 本身目前无鉴权,MCP 与之同级;部署时由网络层隔离)。

## 3. 架构设计

### 3.1 第三个进程:stdio 瘦代理

```
agent(Claude Code/Desktop/…)  ──stdio(JSON-RPC)──▶  python -m kb_platform.mcp
                                                          │  (仅 httpx,不 import graphrag)
                                                          ▼  HTTP
                                              已运行的 API server(:8000)
                                                  GET /kbs   POST /kbs/{id}/query
                                                          │
                                                  GraphRagQueryEngine(现有)
```

与现有"API server + worker"多进程架构一致:新增一个**轻量**进程,职责单一(协议转换 + 转发)。API server 仍是唯一的查询执行点,所有 settings 解析/profile 解密/引擎构建逻辑不被复制。

**为何不独立型:** 独立读 SQLite 自建 `GraphRagQueryEngine` 要复制 `assemble_kb_settings` + provider profile 解析那套(worker 已有一份),且每次 import graphrag 启动慢。瘦代理几十行,启动快,逻辑零重复。代价是依赖 API server 在跑——但生产本就需要 API server 运行(控制面 + 仪表盘),无额外约束。

### 3.2 `KbApiClient`(HTTP 客户端 seam,可测)

```python
class KbApiClient:
    def __init__(self, base_url: str, *, timeout: float = 180.0, http: httpx.AsyncClient | None = None): ...
    async def list_kbs(self) -> list[dict]:           # GET /kbs → [{id,name,method}]
    async def query(self, kb_id: int, method: str, query: str) -> dict:  # POST /kbs/{id}/query
    async def aclose(self) -> None: ...
```

- `base_url` 去 trailing slash;`http` 可注入(测试用 `httpx.AsyncClient(transport=ASGITransport(app=…))` 直连内存中的 FastAPI app,真实路由往返,无 socket)。
- `timeout` 给足(检索走 LLM,默认 180s)。
- 网络错误 → 抛 `KbApiError`(tool 层捕获转结构化错误)。

### 3.3 Tool 逻辑(纯函数,关闭于 client)

把**业务逻辑**与 **MCP 注册**分离,便于单测:

```python
async def list_knowledge_bases(client: KbApiClient) -> list[dict]: ...
async def query_knowledge_base(
    client: KbApiClient, kb_id: int, query: str,
    method: Literal["local","global","drift","basic"] = "local",
) -> dict: ...
```

- `query_knowledge_base` 返回 agent 友好结构:`{answer, method, error, sources:[{kind,name,text}]}`(token 用量等运营字段对 agent 无用,省略)。
- `method` 用 `Literal` → tool schema 里直接暴露四个合法值 + 默认 `local`,agent 可见。
- 任何异常(API 不可达 / KB 不存在 / 检索失败)→ 返回 `{"error": "..."}`,**不抛**。
- `list_knowledge_bases` → `[{id,name,method}]`;空库 → 返回 `[]`(agent 自然知道无可查)。

### 3.4 MCP 注册 + 入口

```python
def build_mcp_server(client: KbApiClient) -> FastMCP:
    mcp = FastMCP("kb-platform")
    @mcp.tool()
    async def list_knowledge_bases() -> list[dict]: return await _list(client)
    @mcp.tool()
    async def query_knowledge_base(kb_id: int, query: str, method: Literal[...] = "local") -> dict: ...
    return mcp
```

`kb_platform/mcp/__main__.py`:
- 解析 `--api-url`(默认 `KB_API_URL` 或 `http://127.0.0.1:8000`)。
- 建 `KbApiClient` → `build_mcp_server` → `mcp.run()`(stdio,SDK 默认)。
- `mcp` 未安装 → 给清晰的 `ImportError` 提示(`uv sync --extra mcp`)。

## 4. 配置与依赖

- 新增 optional extra:`mcp = ["mcp>=1.2"]`(官方 Python SDK,提供 `FastMCP`;transitively 带 `httpx`)。
- `dev` extra 追加 `mcp>=1.2`(让 `uv run pytest` 能跑 MCP 测试)。
- **不**把 `mcp` 放主依赖:核心 API/worker 不需要,MCP 是 opt-in;`kb_platform/mcp/` 对 `mcp` 的 import 全部在模块内(lazy),主包无感。
- 配置:`KB_API_URL` 环境变量 / `--api-url` 命令行参数(命令行优先)。

agent 侧接入示例(`claude_desktop_config.json` 或 Claude Code MCP 配置):
```json
{
  "mcpServers": {
    "kb-platform": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/graphrag-kb-platform",
               "python", "-m", "kb_platform.mcp"],
      "env": { "KB_API_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

## 5. 测试策略

**后端(`tests/test_mcp_server.py`,新建,无 LLM):**
1. `KbApiClient` 用 `httpx.ASGITransport` 挂到 `create_app(repo, query_engine=FakeQueryEngine())`:
   - `list_kbs`:建 2 个 KB → 返回 `[{id,name,method}]`。
   - `query`:命中 FakeQueryEngine → 返回 `answer` 含 `[{method}]` 前缀,`method` 字段正确。
2. Tool 逻辑函数:`query_knowledge_base(fake_client, …)` 正常路径 → `{answer,method,sources}`;`method` 缺省为 `local`。
3. 错误路径:client 指向不存在的端口(真 socket 连不上)→ `KbApiError`;tool 捕获 → 返回 `{"error": …}` 不抛。KB 不存在 → API 返回 `error` 字段被透传。
4. `build_mcp_server`:注册后 `mcp` 暴露两个 tool 名(`list_knowledge_bases` / `query_knowledge_base`),可在 `_tool_manager` / `list_tools()` 中查到(验证接线,不跑完整 stdio 握手)。

**回归:** `uv run pytest` 全绿(新 extra 不影响既有测试);`uv run ruff check .` 通过。

**手验(可选,真 LLM):** 启 API server + worker,`python -m kb_platform.mcp` 跑起,用 `mcp` inspector 或直接 agent 配置后问一个已索引 KB —— 沿用既有 verify 流程记录。

## 6. 非目标 / 延后项

- 远程传输(HTTP/SSE)、写操作 tool、鉴权、多租户 —— 见 §2。
- 在仪表盘里加"MCP 服务"管理页(UI 后置;配置是文件 + 环境变量,足够 v1)。
