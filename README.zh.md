# KB Platform

基于 Microsoft [GraphRAG](https://github.com/microsoft/graphrag) 构建的知识库管理平台。提供 REST API + React 管理后台，支持创建知识库、文档索引、追踪每个分块和流水线步骤，以及使用 local / global / drift / basic 四种方式查询知识图谱。

## 快速开始

```bash
# 克隆并安装依赖
git clone https://github.com/zhangxiaoyong9/graphrag-kb-platform.git kb-platform
cd kb-platform
uv sync

# 初始化数据库（只需执行一次）
uv run alembic upgrade head

# 启动 API 服务
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000

# 在另一个终端启动后台 worker
uv run python -m kb_platform.worker kb.db
```

管理后台在 `http://127.0.0.1:8000`（Vite SPA；API 路由优先级高于前端兜底路由）。

如需真实 LLM 索引和查询，在环境变量中设置你的 provider key（如 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`），并在创建 KB 时传入 `settings_yaml`：

```json
POST /kbs {
  "name": "我的知识库",
  "settings_yaml": "{\"llm\":{\"model_provider\":\"deepseek\",\"model\":\"deepseek-chat\"}}"
}
```

适配器按以下顺序解析凭证：`llm.api_key_env` 环境变量 → `{PROVIDER}_API_KEY` 环境变量 → 显式 `api_key` 参数——密钥绝不存入数据库。

## 架构

**控制面（SQLite）** — 追踪 jobs、steps、units、documents、重试记录。

**数据面（parquet + LanceDB）** — 知识图谱输出（entities、relationships、communities、community reports、text units）+ 向量嵌入存储在 `<data_root>/vectors/`。

**Worker** — 轮询 SQLite 获取待执行任务，运行索引引擎（分块 → 抽取 → 摘要 → 收尾 → 聚类 → 社区报告 → 向量嵌入）。每个 job 独立异常隔离；崩溃恢复自动重置过期单元。API 服务绝不直接运行索引。

**Server** — FastAPI REST API + 托管构建好的 React SPA（`/assets` 静态文件 + 前端路由兜底，API 路由先注册 → 优先命中）。

### 进程边界

| 进程 | 启动命令 | 职责 |
|------|---------|------|
| API 服务 | `python -m kb_platform.server` | REST 接口 + 前端页面 |
| Worker | `python -m kb_platform.worker` | 轮询 SQLite → 执行索引任务 |

## 前端界面

API 服务启动后，访问 `http://127.0.0.1:8000` 即可打开管理后台，无需额外配置。

| 页面 | 功能 |
|------|------|
| KB 列表 | 创建 / 查看知识库 |
| KB 详情 | 上传文档、触发全量 / 增量索引、查看任务列表、查询框 |
| 任务详情 | 步骤时间线 + 每步进度条（pending / running / succeeded / failed / total）+ unit 列表 |
| 重试 | 单 unit 重试 + 整步批量重试失败的 unit |
| 查询框 | 选择检索方式（local / global / drift / basic）→ 输入问题 → 查看答案 |

技术栈：React 18 + TypeScript + Vite + Tailwind CSS。构建产物放在 `web/dist/`，API 服务自动托管（`/assets` 静态文件 + 前端路由兜底，API 路由优先命中）。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/kbs` | 创建知识库 |
| `GET` | `/kbs` | 获取所有知识库 |
| `GET` | `/kbs/{id}` | 获取单个知识库 |
| `POST` | `/kbs/{id}/documents` | 添加文档 |
| `GET` | `/kbs/{id}/documents` | 获取文档列表 |
| `POST` | `/kbs/{id}/jobs` | 触发任务（`type: "full"` 全量或 `"incremental"` 增量） |
| `GET` | `/kbs/{id}/jobs` | 获取任务列表 |
| `GET` | `/jobs/{id}` | 获取任务状态 |
| `GET` | `/jobs/{id}/steps` | 获取步骤时间线及各步骤进度 |
| `GET` | `/steps/{id}/units` | 获取步骤的 unit 列表 |
| `POST` | `/steps/{id}/retry` | 重试某步骤中所有失败的 unit |
| `POST` | `/units/{id}/retry` | 重试单个失败的 unit |
| `POST` | `/kbs/{id}/query` | 查询（`method: "local"` / `"global"` / `"drift"` / `"basic"`） |

## 索引流水线

全量索引（`type: "full"`）：

```
chunk_documents → extract_graph → summarize_descriptions → finalize_graph →
create_communities → community_reports → generate_text_embeddings
```

增量索引（`type: "incremental"`）：仅对新分块进行 LLM 抽取（delta）、合并所有磁盘缓存的抽取结果（零 LLM 调用）、重新摘要/聚类/报告/嵌入。**旧文档绝不重新解析。**

每个 LLM 步骤按 chunk/entity/community 粒度追踪（unit），状态流转 `pending → running → succeeded/failed`；失败的 unit 可单独重试或整步批量重试。

## 查询

支持 graphrag 四种检索方式：

| 方法 | 需要社区报告 | 说明 |
|------|-------------|------|
| `local` | 否 | 基于实体的检索 + 社区摘要增强 |
| `global` | 是 | 全量社区报告 map-reduce 查询 |
| `drift` | 是 | 密集检索优先搜索 |
| `basic` | 否 | 仅基于文本单元的向量搜索（最简单、最快） |

`global` 和 `drift` 依赖社区报告；DeepSeek 不支持 `response_format: json_schema`，报告为空 → 这两种方法请使用支持 json_schema 的模型（如 GPT-4o）。

## 开发

```bash
uv sync                          # 安装所有依赖（含开发依赖）
uv run alembic upgrade head      # 创建/更新数据库结构
uv run pytest                    # 运行所有测试（107 后端 + 8 前端）
uv run ruff check .              # lint 检查
uv run ruff format --check .     # 格式检查

# 前端
cd web && npm install && npm run build && npm test
```

测试使用 `FakeGraphAdapter`（确定性，无需 LLM）、`FakeVectorStore`（内存）和 `FakeQueryEngine`。真实 LLM 集成测试需要环境变量中配置 provider key。

### 环境要求

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) 依赖管理
- Node 18+（前端管理后台）

## 项目结构

```
kb_platform/
  api/            FastAPI app、路由（kbs/jobs/query）、请求/响应模型
  db/             SQLAlchemy 模型、repository、数据库引擎工具
  engine/         索引编排器、原子步骤、unit worker、策略
  graph/          GraphAdapter 接口（Fake + GraphRagAdapter）、向量存储
  query/          QueryEngine 接口（Fake + GraphRagQueryEngine）
  reconsolidate/  增量索引后的抽取结果重新合并
  worker.py       后台索引 worker（SQLite 作为任务队列）
  server.py       HTTP API 服务入口
web/              React + TypeScript + Vite + Tailwind 前端
tests/            后端测试（单元 + 集成；pytest）
docs/             设计文档和实现计划
alembic/          数据库迁移
```
