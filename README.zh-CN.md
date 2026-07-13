# KB Platform

[English](README.md) | [简体中文](README.zh-CN.md)

基于 Microsoft [GraphRAG](https://github.com/microsoft/graphrag) 构建的生产导向型知识库管理平台。项目将 REST API、React 管理后台、可观测索引流水线和面向 AI 智能体的可选 MCP 服务整合在一起。

你可以创建和管理知识库、导入文档、查看每个分块与流水线步骤、定向重试失败任务、统计 Token 与费用、探索实体图谱，并通过 **local、global、drift、basic、cypher、hybrid** 六种策略查询知识库。

- **控制面：** 使用 SQLite 保存知识库、文档、任务、步骤、执行单元、重试、会话和模型配置。
- **数据面：** 使用 Parquet 保存 GraphRAG 产物，LanceDB 保存向量，可选 Neo4j 支持 Cypher 与混合检索。
- **进程隔离：** API Server 提供 REST API 和前端页面；Worker 独立执行索引；可选 MCP Server 将智能体请求代理到 API。
- **全链路可观测：** 提供单元级进度、失败重试、真实引用、Token 用量、耗时和费用统计。

> **项目状态：** 当前版本为活跃开发中的早期版本（`0.1.0`），更适合本机或可信内网部署。暴露到公网前，请补充身份认证、权限控制和网络隔离。

## 目录

- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [Provider 与知识库配置](#provider-与知识库配置)
- [创建知识库并执行索引](#创建知识库并执行索引)
- [索引流水线](#索引流水线)
- [检索策略](#检索策略)
- [MCP 智能体接入](#mcp-智能体接入)
- [开发与测试](#开发与测试)
- [项目结构](#项目结构)
- [生产部署注意事项](#生产部署注意事项)

## 环境要求

- Python 3.11–3.13
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 18+（只在构建前端时需要）
- 一个可用的 LLM Provider Key
- 可选：[Ollama](https://ollama.com)，用于本地 Embedding
- 可选：Neo4j 5.20+，用于 `cypher` 和 `hybrid` 检索

Provider Key 在管理后台的“Provider 配置”页面录入，经过 Fernet 加密后存入数据库，不再依赖 Provider API Key 环境变量。加密主密钥可通过 `KB_SECRET_KEY` 指定；未指定时会在数据库旁自动创建 `.kb_secret_key`。

## 快速开始

### 1. 安装后端

```bash
git clone https://github.com/zhangxiaoyong9/graphrag-kb-platform.git kb-platform
cd kb-platform
uv sync
uv run alembic upgrade head
```

如需 MCP 或 Neo4j 支持：

```bash
uv sync --extra mcp
uv sync --extra neo4j
```

### 2. 一键启动

Linux / macOS：

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

Windows PowerShell / CMD：

```powershell
.\scripts\start.ps1
# 或在命令提示符中执行：
.\scripts\start.cmd
```

脚本会自动安装缺失的依赖、在需要时构建 `web/dist`、执行数据库迁移，并同时管理 API Server 和 Worker。按 `Ctrl+C` 会停止两个进程。

常用参数：

```bash
./scripts/start.sh --db kb.db --data-root ./data --host 127.0.0.1 --port 8000
./scripts/start.sh --skip-install --skip-build
```

```powershell
.\scripts\start.ps1 -DbPath kb.db -DataRoot .\data -HostAddress 127.0.0.1 -Port 8000
.\scripts\start.ps1 -SkipInstall -SkipBuild
```

也可以使用 `KB_DB_PATH`、`KB_DATA_ROOT`、`KB_HOST` 和 `KB_PORT` 环境变量。

### 3. 手动启动 API Server 和 Worker

需要分别启动两个进程：

```bash
# 终端 1：REST API + 已构建的前端
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000

# 终端 2：后台索引 Worker
uv run python -m kb_platform.worker kb.db
```

服务端命令格式：

```text
python -m kb_platform.server [db_path] [data_root] [host] [port]
```

默认值为 `kb.db . 127.0.0.1 8000`。`data_root` 用于保存 Parquet 索引数据及 `vectors/` 下的 LanceDB 表。

### 4. 构建前端

```bash
cd web
npm install
npm run build
```

构建产物位于 `web/dist/`，API Server 会直接托管该目录。浏览器打开：

```text
http://127.0.0.1:8000
```

前端开发模式：

```bash
cd web
npm run dev
```

Vite 默认运行在 `http://localhost:5173`，并将 API 请求代理到 `:8000`。

> 如果系统设置了 `all_proxy`、`http_proxy` 或 `https_proxy`，本地 Ollama 请求可能被错误转发。请配置 `NO_PROXY=localhost,127.0.0.1`，或启动进程时取消相关代理变量。

## Provider 与知识库配置

连接信息和密钥保存在可复用的 Provider Profile 中；知识库只引用 Provider，并保存分块、抽取、聚类和提示词等内容配置。

### Provider Profile

| 字段 | 说明 | 示例 |
|------|------|------|
| `kind` | `llm` 或 `embedding` | `llm` |
| `provider` | Provider 标识 | `openai`、`deepseek`、`azure`、`ollama` |
| `model` | 模型名称 | `gpt-4o-mini` |
| `api_base` | 自定义网关或服务地址 | `https://api.deepseek.com` |
| `api_version` | Azure API 版本 | `2024-06-01` |
| `structured_output` | 是否使用 JSON Schema | `true` |
| `api_keys` | 一个或多个密钥，只写且加密保存 | `["sk-..."]` |

多个 Key 会进行轮询负载均衡。查询 Provider Profile 时只返回 `api_keys_count`，不会返回明文密钥。

DeepSeek 等不支持 `json_schema` 的模型应设置：

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "structured_output": false
}
```

### 本地 Embedding

如果聊天模型不提供 Embedding，可使用 Ollama：

```bash
ollama pull nomic-embed-text
ollama serve
```

随后创建 `kind=embedding`、`provider=ollama`、`model=nomic-embed-text`、`api_base=http://localhost:11434` 的 Provider Profile。

## 创建知识库并执行索引

可以直接使用管理后台，也可以调用 API：

```bash
# 创建 LLM Provider
curl -X POST http://127.0.0.1:8000/provider-profiles \
  -H 'Content-Type: application/json' \
  -d '{"name":"DeepSeek","kind":"llm","provider":"deepseek","model":"deepseek-chat","api_keys":["sk-..."],"structured_output":false}'

# 创建知识库
curl -X POST http://127.0.0.1:8000/kbs \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-kb","method":"standard","llm_profile_id":1}'

# 添加文档
curl -X POST http://127.0.0.1:8000/kbs/1/documents \
  -H 'Content-Type: application/json' \
  -d '{"title":"intro","text":"文档内容"}'

# 启动完整索引
curl -X POST http://127.0.0.1:8000/kbs/1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"type":"full"}'
```

支持 JSON 文本和文件上传，可通过 MarkItDown 读取 `.txt`、`.md`、`.pdf`、`.docx`、`.html` 等格式。默认上传上限为 25 MiB，可通过 `KB_MAX_UPLOAD_BYTES` 调整。

## 索引流水线

完整索引流程：

```text
chunk_documents
→ extract_graph
→ summarize_descriptions
→ finalize_graph
→ create_communities
→ community_reports
→ generate_text_embeddings
```

每个 LLM 步骤均按 chunk、entity 或 community 记录执行单元：

```text
pending → running → succeeded / failed
```

失败后可以重试单个执行单元，也可以重试整个步骤。

增量索引只对新增或变化内容执行昂贵的 LLM 操作，并复用已有抽取、摘要和社区报告。删除文档后图谱不会立即自动缩减，需要重新执行增量索引。

## 检索策略

| 方法 | 社区报告 | Embedding | Neo4j | 适用场景 |
|------|----------|-----------|--------|----------|
| `local` | 不需要 | 实体向量 | 不需要 | 以实体及其邻近图谱为依据的问题 |
| `global` | 需要 | 不需要 | 不需要 | 全语料主题、趋势和总结 |
| `drift` | 需要 | 需要 | 不需要 | 从密集检索结果继续扩展探索 |
| `basic` | 不需要 | 文本单元向量 | 不需要 | 简单、快速的语义检索 |
| `cypher` | 不需要 | 不需要 | 需要 | 显式图遍历及可审计的生成式 Cypher |
| `hybrid` | 不需要 | 需要 | 需要 | 向量检索结合多跳图谱上下文 |

`global` 和 `drift` 要求索引中存在社区报告；`cypher` 和 `hybrid` 要求启用 Neo4j 并同步图谱快照。

查询结果可包含回答、引用来源、服务端耗时、Token 用量、LLM 调用次数，以及适用情况下的 Cypher 和截断信息。

## MCP 智能体接入

MCP Server 是独立的可选进程，通过 stdio 对外提供工具，并将请求代理到正在运行的 API Server。它不重复实现查询逻辑。

启动方式：

```bash
uv sync --extra mcp
uv run python -m kb_platform.mcp --api-url http://127.0.0.1:8000
```

提供的 MCP 工具：

| 工具 | 用途 |
|------|------|
| `list_knowledge_bases` | 获取可用知识库及 ID |
| `get_kb_details` | 查看索引统计和可用检索方法 |
| `query_knowledge_base` | 查询知识库并返回回答和引用 |
| `list_documents` | 浏览知识库文档 |
| `get_document` | 获取文档全文与分块片段 |
| `search_graph` | 搜索实体的多跳图谱邻域 |

当前 MCP 查询工具支持 `local`、`global`、`drift` 和 `basic`；Neo4j 支持的 `cypher` 与 `hybrid` 可通过 REST API 或管理后台使用。

> MCP Server 本身没有认证，请仅在本机或可信网络中运行。

## 开发与测试

```bash
uv sync
uv run alembic upgrade head
uv run pytest
uv run ruff check .

cd web
npm install
npm run build
npm test
```

可选的 Playwright E2E 测试：

```bash
cd web
npm run e2e:install
npm run e2e
```

测试使用 FakeGraphAdapter、FakeVectorStore 和 FakeQueryEngine，无需真实 Provider Key。

## 项目结构

```text
kb_platform/
  api/            FastAPI 应用、路由与模型
  db/             SQLAlchemy 模型、仓储和数据库工具
  engine/         索引编排、原子步骤、执行单元与增量策略
  graph/          GraphRAG 适配、向量存储、GraphML 与费用捕获
  input/          文档读取
  query/          查询引擎和来源提取
  reconsolidate/  增量索引后的抽取结果重新合并
  mcp/            MCP stdio 服务
  worker.py       后台索引 Worker
  server.py       HTTP API Server
web/              React + TypeScript + Vite + Tailwind 管理后台
tests/            后端单元和集成测试
docs/             设计、计划、验证记录和截图
alembic/          数据库迁移
```

## 生产部署注意事项

当前架构优先考虑单机部署的简单性。进入公网或多租户生产环境前，建议至少补充：

- API 身份认证、RBAC 和租户隔离
- MCP 与 Neo4j 的网络访问控制
- PostgreSQL 和适合多 Worker 的任务队列
- 请求限流、上传配额和审计日志
- 密钥托管与 `KB_SECRET_KEY` 安全注入
- 数据库、Parquet、LanceDB 和 Neo4j 的一致性备份
- CI/CD、依赖漏洞扫描和可复现部署

更完整的 API 字段、迁移说明和实现细节请参考[英文 README](README.md)。
