# Agentic KB 检索 + 跨工具安装器 — 设计文档

- 日期: 2026-07-05
- 状态: 待批准
- 上游: `2026-06-28-mcp-query-server-design.md`(MCP 查询服务已落地:`list_knowledge_bases` / `query_knowledge_base` 两个工具,stdio 瘦代理)
- 依赖: 现有 HTTP API server(`/kbs` `/kbs/{id}/documents` `/kbs/{id}/graph` `/kbs/{id}/stats` `/kbs/{id}/query` 等只读端点均已存在);现有 `kb_platform/mcp/` 包。

## 1. 背景与目标

现有 MCP server 只暴露 2 个工具(发现 KB + 单发检索),**只能"问一句拿一个答案"**,无法支撑 2026 主流的 agentic 检索流程(并行 fan-out、多跳图遍历、引用核对、子问题分解)。同时,目前要把 MCP server 接入 Claude Code / opencode 等宿主,需要手动改各自的配置文件并自行写 prompt 引导 agent,门槛高、易出错。

本设计两件事一起做:

1. **拓宽 MCP 工具面**:加 4 个工具,把后端已有但未暴露的只读能力(KB 就绪度、文档浏览、图遍历)开放给 agent
2. **跨工具/跨平台安装器**:一个参数化安装器,把 MCP server 注册 + agent 剧本落位自动化,支持 macOS / Linux / Windows 上的 Claude Code 与 opencode

**目标场景**:深度调研 / 调研类任务 —— 用户把技术文档、内部 wiki 等索引进 KB,通过 Claude Code 或 opencode 问复杂、多部分的问题,得到带精确引用、可溯源、经验证的答案。

**成功标准:**
- MCP server 暴露 6 个工具(原 2 + 新 4),全部为 thin HTTP proxy,不引入 graphrag/SQLite 依赖
- 一份中立的 agent 剧本 markdown,被安装器按工具渲染到正确位置(Claude Code → `SKILL.md`;opencode → `AGENTS.md`)
- `uv run python -m kb_platform.install --tool {claude-code|opencode|all}` 在 mac/linux/windows 上均能把 MCP 注册 + 剧本落位一键完成,幂等可重入,可卸载
- 既有测试不破;新加工具有单测覆盖;安装器在 tmpdir 内单测,不需要真装 claude/opencode

## 2. 范围(YAGNI)

**做:**
- 4 个新 MCP 工具:`get_kb_details` / `list_documents` / `get_document` / `search_graph`
- `kb_platform/install/` 新包:Python 安装器模块 + 工具适配注册表 + 中立剧本
- 仓库根 `install.sh`(mac/linux)与 `install.ps1`(windows)薄壳,转发给 Python 模块
- 首轮 2 个工具适配:`claude-code`、`opencode`
- 安装器单测、MCP 新工具单测、剧本一致性单测

**不做(v1):**
- `multi_query`(并行 fan-out 工具)—— Claude Code / opencode 本身能在单轮并行调多工具,让 agent 自己 fan-out 更灵活,不过度封装
- `fetch_chunk(chunk_id)` —— 先看 `get_document` 返回的 chunk 是否够用,不够再加
- 写操作工具(create/delete KB/document)—— agent 不该管,走 dashboard
- 生产打包(发布到 PyPI 用 `uvx kb-platform-mcp`)—— v1 是 dev 模式,MCP server 跑在 check-out 的仓库里(`uv run --directory`)
- HTTP / Streamable HTTP MCP transport —— 仍只 stdio
- 鉴权 / API token —— 沿用现状,API server 本身无鉴权,部署由网络层隔离
- Cursor / Claude Desktop 适配 —— 架构留口子(TOOL_REGISTRY 加一项即可),v1 不做

## 3. 架构设计

三层,都在本仓库内:

```
┌──────────────────────────────────────────────────────────┐
│ Agent 宿主(Claude Code / opencode)                      │
│   ┌────────────┐    ┌──────────────────────────────┐     │
│   │ MCP client │    │ agent 剧本(SKILL.md /       │     │
│   │  (stdio)   │    │ AGENTS.md)—— 2026 流程      │     │
│   └─────┬──────┘    └──────────────────────────────┘     │
└─────────┼────────────────────────────────────────────────┘
          │ JSON-RPC over stdio
┌─────────┴────────────────────────────────────────────────┐
│ kb_platform.mcp(已有,thin proxy)+ 4 个新工具           │
└─────────┬────────────────────────────────────────────────┘
          │ HTTP
┌─────────┴────────────────────────────────────────────────┐
│ KB Platform API server(已有)                           │
│  /kbs /documents /graph /stats /query ...(只读端点)     │
└──────────────────────────────────────────────────────────┘

+ kb_platform.install —— 跨工具/跨平台安装器(新增)
```

**关键边界(沿用 `2026-06-28-mcp-query-server-design.md` 的红线):**
- MCP 层仍是**纯 HTTP proxy** —— 新工具也只把请求转发给已有 API 端点,**不引入 graphrag / SQLite 依赖**
- 剧本写**一份**中立 markdown(`kb_platform/install/recipe.md`),安装器按工具渲染 —— 单一真相源,避免双份漂移
- 安装器薄壳 + Python 模块,跨 mac/linux/win

## 4. 新增 MCP 工具面

现有 2 个(`list_knowledge_bases` / `query_knowledge_base`)保留。新增 4 个,**全部接到已有 API 端点,无新后端逻辑**:

| 新工具 | 签名 | 接到 | 解决深度调研的什么 |
|---|---|---|---|
| `get_kb_details` | `(kb_id: int) -> dict` | `GET /kbs/{id}` + `GET /kbs/{id}/stats` | 查询前确认就绪:索引完了没、社区报告有没有、哪些 method 可用、实体/文档数量 |
| `list_documents` | `(kb_id: int) -> list[dict]` | `GET /kbs/{id}/documents` | 浏览 KB 内容,选文档作用域 |
| `get_document` | `(kb_id: int, doc_id: int) -> dict` | `GET /kbs/{id}/documents/{doc_id}`(含 chunks) | 读原文/段,**精确引用核对** |
| `search_graph` | `(kb_id: int, q: str, hop: int = 1, limit: int = 200) -> dict` | `GET /kbs/{id}/graph?q=&hop=&limit=` | **多跳实体遍历** —— 2026 流程的核心,答"X 和 Y 什么关系"类问题 |

总计 **6 个工具**。

**返回形状沿用现有约定:**
- 失败 → `{"error": "..."}`(不抛异常)
- 成功 → 结构化 dict / list,字段裁干净(去掉 `elapsed_ms` / `prompt_tokens` 等内部指标)
- `KbApiClient` 扩展 4 个对应方法,工具逻辑函数(`get_kb_details` / `list_documents` / ...)是薄包装,纯 async、不持有状态

**新增的 `KbApiClient` 方法签名:**
```python
async def get_kb(self, kb_id: int) -> dict:           # GET /kbs/{id} + stats 合并
async def list_documents(self, kb_id: int) -> list[dict]:
async def get_document(self, kb_id: int, doc_id: int) -> dict:
async def search_graph(self, kb_id: int, q: str, hop: int = 1, limit: int = 200) -> dict:
```

`build_mcp_server` 增加 4 个 `@mcp.tool(...)` 注册,工具描述写给 agent 看(英文,描述清楚何时该用)。

## 5. Agent 剧本(2026 流程)

**单一真相源**:`kb_platform/install/recipe.md`(中立 markdown)。

**核心 —— 深度调研标准流程:**

```
1. 先发现(永远不要盲查)
   list_knowledge_bases → 选 KB → get_kb_details 确认就绪
   (索引完了没?社区报告有没有?哪些 method 可用?)

2. 按问题类型路由
   简单事实      → query(method=local/basic)
   主题/全局概览 → query(method=global)
   混合型        → query(method=drift)
   "X 和 Y 什么关系" → 先 search_graph(X),再带着图上下文 query
   多部分问题    → 拆成子问题,并行 query

3. 深度调研主流程(本剧本的核心)
   ① get_kb_details — 确认 KB 就绪
   ② planner:把大问题拆成子问题
   ③ 并行 fan-out:每个子问题各调一次 query_knowledge_base
   ④ 涉及实体关系的 claim → search_graph 验证
   ⑤ 直接引用/精确数字 → get_document 取原文核对
   ⑥ 综合:每个 claim 必须挂 (source.name, 文档)
   ⑦ 找不到证据的 claim → 明说"KB 未找到证据",不许编

4. 引用规则
   每个 claim → (source.name, document)
   直接引用 → 用 get_document 取精确文本
   不确定 → 写"KB 中未找到明确证据",不要猜

5. 失败兜底
   KB 没索引好 → 告诉用户,别查
   method 需要社区报告但缺失 → 降级到 local,告知用户
   所有 sources 空 → 直说,别造
```

**两个具体例子**(技术文档 / internal wiki 风味,装进剧本给 agent 看):

- "我们 wiki 里 A 服务和 B 服务怎么交互?" → `search_graph(q="A 服务", hop=2)` → 看 B 是否在邻居里 → `query(method=local)` 取细节 → 综合引用
- "这版技术规格对延迟有什么要求?" → `list_documents` 找规格文档 → `get_document` 看目录 → `query(method=local)` 定位段落 → 引用精确到段

**渲染:**
- **Claude Code**:包成 `.claude/skills/kb-research/SKILL.md`,加 frontmatter(`name` / `description` / 触发条件);user scope 落 `~/.claude/skills/`,project scope 落 `<repo>/.claude/skills/`
- **opencode**:作为带标记的段合并进 `AGENTS.md`(项目根)或 `~/.config/opencode/agent.md`(user);用 `<!-- kb-platform:start -->` / `<!-- kb-platform:end -->` 标记便于幂等更新

## 6. 安装器设计

### 6.1 目录结构

```
kb_platform/install/
├── __init__.py
├── __main__.py        ← argparse 入口
├── registry.py        ← TOOL_REGISTRY = {"claude-code": ClaudeCodeAdapter, "opencode": OpenCodeAdapter}
├── platform.py        ← mac/linux/win 路径解析(config_dir / home)
├── recipe.py          ← 读 recipe.md,按工具渲染(SKILL.md vs AGENTS.md 段)
├── mcp_config.py      ← 构造共享 MCP server config(command/args/env),所有工具共用
└── tools/
    ├── base.py        ← InstallTarget Protocol:register_mcp() / install_playbook() / uninstall()
    ├── claude_code.py ← 优先 `claude mcp add`,无 CLI 则写 .mcp.json;落 SKILL.md
    └── opencode.py    ← 合并 ~/.config/opencode/opencode.json 的 mcp 段;合并 AGENTS.md

install.sh / install.ps1  ← 仓库根,~10 行,转发给 uv run python -m kb_platform.install
```

### 6.2 共享 MCP server config(每个工具注册的都是这个)

```json
{
  "command": "uv",
  "args": ["run", "--directory", "<repo_root>", "python", "-m", "kb_platform.mcp"],
  "env": {"KB_API_URL": "<api-url>"}
}
```

`<repo_root>` 在 dev 模式取安装器自身所在仓库根(运行时解析)。

### 6.3 CLI

```bash
uv run python -m kb_platform.install --tool claude-code --api-url http://localhost:8000
uv run python -m kb_platform.install --tool opencode
uv run python -m kb_platform.install --tool all
uv run python -m kb_platform.install --list                    # 列出支持的工具
uv run python -m kb_platform.install --tool claude-code --uninstall
uv run python -m kb_platform.install --tool claude-code --dry-run
uv run python -m kb_platform.install --tool claude-code --scope user   # 默认 project
```

### 6.4 工具适配行为

**`ClaudeCodeAdapter`:**
- 探测 `claude` CLI 是否在 PATH
- 在 → `claude mcp add kb-platform --scope <scope> -- uv run ...`(让 CLI 自己处理配置文件)
- 不在 → fallback:project scope 写 `.mcp.json`;user scope 写 `~/.claude.json` 的 `mcpServers` 段
- 剧本:写 `.claude/skills/kb-research/SKILL.md`(或 user 目录),含 frontmatter

**`OpenCodeAdapter`:**
- 写 `~/.config/opencode/opencode.json`(mac/linux)或 `%APPDATA%\opencode\opencode.json`(windows)的 `mcp` 段,**merge 不覆盖**已有 server
- 剧本:合并 `AGENTS.md`(项目根,段标记包裹,幂等)

### 6.5 平台与安全约束

- **平台差异在 `platform.py` 收口**:mac/linux 走 `~/.config/...`(XDG 风格);windows 走 `%APPDATA%\...`;`sys.platform` 检测
- **幂等**:重跑不重复(`AGENTS.md` 段标记;mcp 注册前查重)
- **不破坏用户既有配置**:写前备份(原文件 `.bak`);mcp 段是 merge
- **缺工具给清晰提示**:如 `claude` CLI 不在 PATH → 走 fallback 并告知用户
- **退出码**:0 成功 / 1 参数错 / 2 工具未识别 / 3 配置写入失败

## 7. 测试策略

| 层 | 文件 | 方法 |
|---|---|---|
| MCP 新工具 | 扩 `tests/test_mcp_server.py` | `httpx.ASGITransport` + 真实 FastAPI app。graph/documents 工具需 SQLite 塞 KB + tmp 目录造 parquet(这俩端点直读 parquet,不走 FakeQueryEngine) |
| 安装器 adapter | 新 `tests/install/test_*.py` | tmpdir 测每个 adapter 的 config 生成、SKILL.md / AGENTS.md 渲染、幂等(跑两遍无 drift)、`--dry-run` 预览、`--uninstall` 回滚。**不需要装真 claude/opencode** |
| 剧本一致性 | `tests/install/test_recipe.py` | recipe.md 里引用的工具名都存在于 MCP 注册表;Claude Code 渲染含 frontmatter,opencode 渲染含段标记 |

**每个新 MCP 工具的最小测试矩阵**(沿用现有模式):
- 成功往返(数据齐全)
- API 不可达 → `{"error": "..."}` 不抛异常
- 边界:KB 为空、文档不存在、图无匹配

## 8. 错误处理与约定

- **MCP 工具**:沿用 `KbApiError` → `{"error": ...}` 模式,绝不抛给 agent
- **安装器**:缺 CLI → fallback + 提示;写配置失败 → 备份后报错;未知工具名 → 列出支持的 + 非零退出
- **遵守的项目约定(CLAUDE.md):**
  - MCP 层仍是 thin HTTP proxy —— 新工具不引入 graphrag/SQLite 依赖
  - `ruff check .` line-length 100,py311;`pytest` asyncio_mode=auto
  - 测试用 ASGITransport 模式 + autouse Fernet fixture(如涉及 profile)
  - 安装器是 dev 工具,日志/错误信息可用英文(不像 dashboard 要中文)

## 9. 未来工作(显式 out-of-scope)

- 生产打包:发布到 PyPI,安装器支持 `uvx kb-platform-mcp` 形式(无需 check-out 仓库)
- Cursor / Claude Desktop / Continue 适配:TOOL_REGISTRY 加一项 + 一个 `tools/<name>.py`
- `fetch_chunk(chunk_id)`:若 `get_document` 的 chunk 粒度不够精细
- HTTP / Streamable HTTP MCP transport(远程 agent)
- 部署侧鉴权(API token / 网络层)
