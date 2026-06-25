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

**Worker** — 轮询 SQLite 获取待执行任务，运行索引引擎（分块 → 抽取 → 摘要 → 收尾 → 聚类 → 社区报告 → 向量嵌入）。每个 job 独立异常隔离；崩溃恢复自动重置过期单元；SIGTERM/SIGINT 优雅关闭（跑完在飞 unit 后退出）。API 服务绝不直接运行索引。

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
| KB 详情 | 文档管理（文件上传 / 粘贴 / 列表 / 删除）、触发全量 / 增量索引、累计**成本**、**导出**（zip / GraphML）、可交互**图谱**可视化、任务列表、查询框 |
| 任务详情 | 步骤时间线 + 每步进度条 + unit 列表 + 每步**成本**条 |
| 重试 | 单 unit 重试 + 整步批量重试失败的 unit |
| 查询框 | 选择检索方式（local / global / drift / basic）→ 输入问题 → 查看答案 |

图谱可视化基于 [react-force-graph-2d](https://github.com/vasturiano/react-force-graph-2d)：节点为实体（按 degree/社区着色/度量），带搜索框可聚焦邻域。成本条为纯 CSS（未引入图表库）。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 存活探针：数据库探活 + worker 心跳是否过期 |
| `POST` | `/kbs` | 创建知识库 |
| `GET` | `/kbs` | 获取所有知识库 |
| `GET` | `/kbs/{id}` | 获取单个知识库 |
| `POST` | `/kbs/{id}/documents` | 添加文档——JSON `{title, text}` **或** multipart 文件上传（经 [markitdown](https://github.com/microsoft/markitdown) 解析：`.txt`、`.md`、`.pdf`、`.docx`、`.html` 等） |
| `GET` | `/kbs/{id}/documents` | 获取文档列表（含 `bytes` + `chunk_count`） |
| `DELETE` | `/kbs/{id}/documents/{doc_id}` | 删除文档及其分块（**图不回缩**——重新跑增量刷新） |
| `POST` | `/kbs/{id}/jobs` | 触发任务（`type: "full"` 全量或 `"incremental"` 增量） |
| `GET` | `/kbs/{id}/jobs` | 获取任务列表 |
| `GET` | `/kbs/{id}/cost` | 累计成本（按 step / model / job 汇总） |
| `GET` | `/kbs/{id}/jobs/{jid}/cost` | 单个任务成本（按 step / model 汇总） |
| `GET` | `/kbs/{id}/export?format=zip\|graphml` | 导出索引（zip 打包 parquet + GraphML，或单独 GraphML） |
| `GET` | `/kbs/{id}/graph?limit=&q=&hop=` | 图可视化数据（按 degree 取 Top-N 实体，或搜索邻域，含社区着色） |
| `GET` | `/jobs/{id}` | 获取任务状态 |
| `GET` | `/jobs/{id}/steps` | 获取步骤时间线及各步骤进度 |
| `GET` | `/steps/{id}/units` | 获取步骤的 unit 列表 |
| `POST` | `/steps/{id}/retry` | 重试某步骤中所有失败的 unit |
| `POST` | `/units/{id}/retry` | 重试单个失败的 unit |
| `POST` | `/kbs/{id}/query` | 查询（`method: "local"` / `"global"` / `"drift"` / `"basic"`） |

上传大小默认限制 25 MiB（环境变量 `KB_MAX_UPLOAD_BYTES`）。成本通过 graphrag-llm 的 model-cost 注册表按每次 LLM 调用采集；未知模型只记录 token、不计美元（绝不会让 unit 失败）。

## 索引流水线

全量索引（`type: "full"`）：

```
chunk_documents → extract_graph → summarize_descriptions → finalize_graph →
create_communities → community_reports → generate_text_embeddings
```

增量索引（`type: "incremental"`）只重跑变更部分：仅对新分块做 LLM 抽取（delta）、合并所有磁盘缓存的抽取结果（零 LLM 调用），随后对 **delta 作用域** 的摘要和社区报告——只重新 LLM 描述发生变化的实体、上下文发生变化的社区（其余靠磁盘摘要 / `reports_by_hash` sidecar 结转，因 Leiden 每次重排社区 id）。**旧文档绝不重新解析。**

每个 LLM 步骤按 chunk/entity/community 粒度追踪（unit），状态流转 `pending → running → succeeded/failed`；失败的 unit 可单独重试或整步批量重试。

**DeepSeek 社区报告：**在 KB 设置中设 `community_reports.structured_output: false`，即可走纯文本 completion + 宽松 JSON 解析生成报告（DeepSeek 拒绝 `response_format: json_schema`，此开关绕过该限制）。默认 `true`（graphrag 的 structured output，适用于 OpenAI/GPT-4o）。

## 查询

支持 graphrag 四种检索方式：

| 方法 | 需要社区报告 | 说明 |
|------|-------------|------|
| `local` | 否 | 基于实体的检索 + 社区摘要增强 |
| `global` | 是 | 全量社区报告 map-reduce 查询 |
| `drift` | 是 | 密集检索优先搜索 |
| `basic` | 否 | 仅基于文本单元的向量搜索（最简单、最快） |

`global` 和 `drift` 依赖社区报告。DeepSeek 拒绝 `response_format: json_schema`，因此这两种方法要么使用支持 json_schema 的模型（如 GPT-4o），要么在 KB 设置中开启 `community_reports.structured_output: false` 走纯文本报告回退（见[索引流水线](#索引流水线)）。

## 开发

```bash
uv sync                          # 安装所有依赖（含开发依赖）
uv run alembic upgrade head      # 创建/更新数据库结构
uv run pytest                    # 运行所有后端测试（160）
uv run ruff check .              # lint 检查
uv run ruff format --check .     # 格式检查

# 前端
cd web && npm install && npm run build && npm test   # 19 个 vitest 测试
```

测试使用 `FakeGraphAdapter`（确定性，无需 LLM）、`FakeVectorStore`（内存）和 `FakeQueryEngine`。真实 LLM 集成测试需要环境变量中配置 provider key。

### 环境要求

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) 依赖管理
- Node 18+（前端管理后台）

## 项目结构

```
kb_platform/
  api/            FastAPI app、路由（kbs/jobs/query/health/cost/export/graph）、模型
  db/             SQLAlchemy 模型、repository、数据库引擎工具
  engine/         索引编排器、原子步骤、unit worker、策略（含 delta 策略）
  graph/          GraphAdapter 接口、向量存储、GraphML 写出、成本采集（completion wrapper）
  input/          文档读取（markitdown）
  query/          QueryEngine 接口（Fake + GraphRagQueryEngine）
  reconsolidate/  增量索引后的抽取结果重新合并
  worker.py       后台索引 worker（SQLite 作为任务队列、优雅关闭）
  server.py       HTTP API 服务入口
web/              React + TypeScript + Vite + Tailwind 前端（DocumentManager、GraphView、CostPanel 等）
tests/            后端测试（单元 + 集成；pytest）
docs/             设计文档和实现计划
alembic/          数据库迁移
```
