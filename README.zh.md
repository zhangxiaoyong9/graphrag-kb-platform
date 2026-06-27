# KB Platform

基于 Microsoft [GraphRAG](https://github.com/microsoft/graphrag) 构建的知识库管理平台。提供 REST API + React 管理后台，支持创建知识库、文档索引、追踪每个分块和流水线步骤，以及使用 local / global / drift / basic 四种方式检索问答。

- **控制面：** SQLite（追踪 jobs / steps / units / documents / 重试）。
- **数据面：** parquet（entities / relationships / communities / reports / text units）+ LanceDB 向量。
- **两个进程：** HTTP API 服务（同时托管构建好的 SPA）+ 独立后台 worker 执行索引。API 服务绝不直接跑索引；worker 绝不服务 HTTP。

---

## 环境要求

- Python 3.11–3.13 + [`uv`](https://docs.astral.sh/uv/) 依赖管理
- Node 18+（仅用于构建前端；若用预构建的 `web/dist/` 则运行时不需要）
- 环境变量里的 LLM provider key（如 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`）。**密钥绝不入库**——按 `llm.api_key_env` → `{PROVIDER}_API_KEY` 环境变量 → 显式 `api_key` 顺序解析。
- _（可选）_ [Ollama](https://ollama.com) 做本地嵌入——若你的 LLM provider 没有嵌入模型（如 DeepSeek），向量检索方法需要它。

---

## 部署

### 1. 后端（API 服务 + worker）

```bash
# 克隆并安装
git clone https://github.com/zhangxiaoyong9/graphrag-kb-platform.git kb-platform
cd kb-platform
uv sync                              # 安装 Python 依赖

# 创建 SQLite 数据库（只需一次）
uv run alembic upgrade head
```

跑**两个**进程（各开一个终端）：

```bash
# 终端 1 — API 服务：REST 接口 + 托管构建好的 SPA
export DEEPSEEK_API_KEY=sk-...       # 你的 LLM 密钥
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000

# 终端 2 — 后台 worker：轮询 SQLite → 执行索引任务
export DEEPSEEK_API_KEY=sk-...       # 同一个密钥（LLM 调用在 worker 侧）
uv run python -m kb_platform.worker kb.db
```

服务 CLI：`python -m kb_platform.server [db_path] [data_root] [host] [port]`（默认 `kb.db . 127.0.0.1 8000`）。`data_root` 存放 parquet 索引产物 + `<data_root>/vectors/` LanceDB 向量表。

> **代理坑：** 若本机设了 `all_proxy`/`http_proxy`/`https_proxy`（如 Surge、Clash），litellm 会把 **localhost** 调用（Ollama 嵌入）走代理从而失败。给 server/worker 解除代理即可——`env -u all_proxy -u http_proxy -u https_proxy ... python -m kb_platform.server ...`，或设 `NO_PROXY=localhost,127.0.0.1`。

### 2. 前端（React 管理后台）

**生产（推荐）：** API 服务直接托管预构建的 SPA（`web/dist/`）。构建一次，之后只用服务即可——无需单独的前端进程。

```bash
cd web
npm install
npm run build        # 产物 web/dist/（tsc -b && vite build）
```

打开 **`http://127.0.0.1:8000`** 即可。前端升级后再跑一次 `npm run build`，服务重启后自动加载新 `dist/`。

**开发（热更新）：** 后端跑在 :8000，前端跑 Vite dev server（已配置代理把 `/kbs`、`/jobs`、`/steps`、`/units`、`/health` 转发到后端）：

```bash
cd web && npm install && npm run dev      # http://localhost:5173
```

### 3.（可选）用 Ollama 做本地嵌入

DeepSeek（及多数纯对话模型）没有嵌入模型，`local`/`basic`/`drift` 检索需要单独的嵌入提供方。用 Ollama 在本地跑：

```bash
ollama pull nomic-embed-text
ollama serve                                # http://localhost:11434
```

建 KB 时在 `settings_yaml` 里加 `embedding` 段（占位 `api_key` 用于满足 graphrag-llm 的校验；litellm 对 Ollama 忽略它）：

```json
POST /kbs
{
  "name": "my-kb",
  "settings_yaml": "{
    \"llm\": {\"model_provider\":\"deepseek\",\"model\":\"deepseek-chat\",\"api_key_env\":\"DEEPSEEK_API_KEY\"},
    \"embedding\": {\"model_provider\":\"ollama\",\"model\":\"nomic-embed-text\",\"api_base\":\"http://localhost:11434\",\"api_key\":\"ollama\"},
    \"community_reports\": {\"structured_output\": false}
  }"
}
```

---

## 配置（model / api_base / key）

所有 LLM / 嵌入设置都放在 KB 的 `settings_yaml`（建库时传入）。**密钥**是特例：调用时从环境变量解析、**绝不入库**，所以改密钥只需改环境变量（重启 worker/server 即可）——不用动 KB。但 `model` 和 `api_base` 会持久化进 KB 设置，目前**没有设置更新接口**，所以改一个已有 KB 的模型/api_base 意味着新建一个 KB（重新加文档 + 重新索引）。

### `llm` 字段

| 字段 | 含义 | 示例 |
|------|------|------|
| `model_provider` | provider | `deepseek`、`openai`、`azure`、`ollama` |
| `model` | 模型 id | `deepseek-chat`、`gpt-4o-mini` |
| `api_base` | 自定义端点（中转 / Azure / 自建） | `https://api.deepseek.com`、`http://localhost:11434` |
| `api_key_env` | 持有密钥的环境变量名（**推荐**） | `DEEPSEEK_API_KEY` |
| `api_key` | 明文密钥（**不推荐**——会入库） | `sk-...` |
| `api_version` | Azure API 版本（仅 Azure） | `2024-06-01` |

调用时凭证解析顺序：`llm.api_key` → `llm.api_key_env` 指定的环境变量 → `{PROVIDER}_API_KEY` 环境变量。优先用 `api_key_env`，让密钥留在环境里、不进数据库。

### 各项怎么改

- **密钥** —— `export DEEPSEEK_API_KEY=sk-new`（或对应的 `{PROVIDER}_API_KEY`），再重启 worker + server。下一个任务即生效，无需改 KB。
- **模型 / api_base** —— 建 KB 时在 `settings_yaml` 里传新值。改已有 KB 需新建（重新加文档 + 重新索引）。`embedding.model` / `embedding.api_base` 同理，写在 `embedding` 段下。
- 按 KB 指定密钥 —— 若某个 KB 想用与默认不同的密钥，把 `llm.api_key_env` 指到另一个环境变量名即可。

### 示例（对象以字符串形式塞进 `settings_yaml`）

DeepSeek，官方端点：
```json
{"llm":{"model_provider":"deepseek","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"}}
```

OpenAI 走中转 / 自定义 base：
```json
{"llm":{"model_provider":"openai","model":"gpt-4o-mini","api_base":"https://your-relay.example.com/v1","api_key_env":"OPENAI_API_KEY"}}
```

Azure OpenAI（需 `api_base` + `api_version`）：
```json
{"llm":{"model_provider":"azure","model":"my-deployment","api_base":"https://my-resource.openai.azure.com","api_version":"2024-06-01","api_key_env":"AZURE_OPENAI_API_KEY"}}
```

DeepSeek LLM + Ollama 嵌入 + 纯文本报告（完整组合）：
```json
{"llm":{"model_provider":"deepseek","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"},"embedding":{"model_provider":"ollama","model":"nomic-embed-text","api_base":"http://localhost:11434","api_key":"ollama"},"community_reports":{"structured_output":false}}
```

---

## 创建 KB 并索引

```bash
curl -X POST http://127.0.0.1:8000/kbs -H 'Content-Type: application/json' \
  -d '{"name":"my-kb","method":"standard","settings_yaml":"{...见上...}"}'
curl -X POST http://127.0.0.1:8000/kbs/1/documents -H 'Content-Type: application/json' \
  -d '{"title":"简介","text":"..."}'           # 也可 multipart 文件上传
curl -X POST http://127.0.0.1:8000/kbs/1/jobs -H 'Content-Type: application/json' \
  -d '{"type":"full"}'                         # "full" 或 "incremental"
```

或者直接用 `http://127.0.0.1:8000` 的管理后台。

## 管理后台

分组 SaaS 风格侧边栏（工作台 / 知识库 / 检索与问答 / 分析与监控 / 系统管理）。主要页面：

| 页面 | 功能 |
|------|------|
| 概览 | KB 数、最近任务、系统健康 |
| 知识库管理 / 文档管理 / 图谱管理 | 建 KB；跨 KB 文档中心；跨 KB 图谱浏览 |
| 检索测试 / 问答对话 | 选 KB + 方法（local/global/drift/basic），展示**答案 + 真实来源 + token 用量 + 服务端耗时** |
| 分析报表 / 任务管理 / 成本统计 | 聚合指标；跨 KB 全部任务；按 step/model/job 的成本 |
| KB 详情 | 文档管理（上传/粘贴/列表/删除）、文档详情浏览与来源证据抽屉、触发全量/增量、累计**成本**、**导出**（zip/GraphML）、可交互**图谱**、实体/关系浏览、任务、检索；**模型配置卡**展示 LLM/嵌入设置 |
| 任务详情 | 步骤时间线 + 每步进度 + unit 列表 + 单 unit/整步**重试** + 每步成本 |
| 系统状态 / 系统设置 / API Keys | 健康 + API 参考；只读配置说明；API Key 预留页 |

图谱可视化基于 [react-force-graph-2d](https://github.com/vasturiano/react-force-graph-2d)；成本条为纯 CSS。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 存活探针：数据库探活 + worker 心跳是否过期 |
| `POST` | `/kbs` | 创建知识库 |
| `GET` | `/kbs` | 获取所有知识库 |
| `GET` | `/kbs/{id}` | KB 详情（含脱敏后的 `settings`） |
| `POST` | `/kbs/{id}/documents` | 添加文档——JSON `{title,text}` **或** multipart 文件（经 [markitdown](https://github.com/microsoft/markitdown)：`.txt/.md/.pdf/.docx/.html` 等） |
| `GET` | `/kbs/{id}/documents` | 文档列表（含 `bytes` + `chunk_count`） |
| `GET` | `/kbs/{id}/documents/{doc_id}` | 文档详情：返回存储正文和基于分块的引用列表 |
| `GET` | `/kbs/{id}/documents/{doc_id}/citations/{citation_id}/evidence` | 单条引用的证据详情：命中分块 + 前后上下文 |
| `DELETE` | `/kbs/{id}/documents/{doc_id}` | 删除文档及其分块（**图不回缩**——重跑增量刷新） |
| `POST` | `/kbs/{id}/jobs` | 触发任务（`type: "full"` / `"incremental"`） |
| `GET` | `/kbs/{id}/jobs` | KB 任务列表 |
| `GET` | `/kbs/{id}/cost` | 累计成本（按 step/model/job） |
| `GET` | `/kbs/{id}/jobs/{jid}/cost` | 单任务成本（按 step/model） |
| `GET` | `/kbs/{id}/export?format=zip\|graphml` | 导出索引（zip 打包 parquet+GraphML，或单独 GraphML） |
| `GET` | `/kbs/{id}/graph?limit=&q=&hop=` | 图谱数据（按 degree 取 Top-N，或搜索邻域，含社区着色） |
| `GET` | `/jobs/{id}` | 任务状态 |
| `GET` | `/jobs/{id}/steps` | 步骤时间线及各步进度 |
| `GET` | `/steps/{id}/units` | 步骤的 unit 列表 |
| `POST` | `/steps/{id}/retry` | 重试某步骤中所有失败的 unit |
| `POST` | `/units/{id}/retry` | 重试单个失败的 unit |
| `POST` | `/kbs/{id}/query` | 检索 → `{answer, method, error, elapsed_ms, prompt_tokens, output_tokens, llm_calls, sources}` |

上传默认限制 25 MiB（环境变量 `KB_MAX_UPLOAD_BYTES`）。成本通过 graphrag-llm 的 model-cost 注册表按每次 LLM 调用采集；未知模型只记录 token、不计美元（绝不会让 unit 失败）。

## 索引流水线

全量（`type: "full"`）：

```
chunk_documents → extract_graph → summarize_descriptions → finalize_graph →
create_communities → community_reports → generate_text_embeddings
```

增量（`type: "incremental"`）只重跑变更部分：仅对新分块做 LLM 抽取（delta）、合并所有磁盘缓存的抽取结果（零 LLM 调用），随后对 **delta 作用域**的摘要和社区报告——只重新 LLM 描述发生变化的实体、上下文发生变化的社区（其余靠磁盘摘要 / `reports_by_hash` sidecar 结转，因 Leiden 每次重排社区 id）。**旧文档绝不重新解析。**

每个 LLM 步骤按 chunk/entity/community 粒度追踪（unit），状态流转 `pending → running → succeeded/failed`；失败的 unit 可单独重试或整步批量重试。

**DeepSeek 社区报告：**在 KB 设置中设 `community_reports.structured_output: false`，即可走纯文本 completion + 宽松 JSON 解析生成报告（DeepSeek 拒 `response_format: json_schema`，纯文本路径绕过该限制）。默认 `true`（graphrag 的 structured output，适用于 OpenAI/GPT-4o）。

## 查询

| 方法 | 需要社区报告 | 需要向量 | 说明 |
|------|-------------|---------|------|
| `local` | 否 | 是（实体） | 实体检索 + 社区摘要增强 |
| `global` | 是 | 否 | 全量社区报告 map-reduce |
| `drift` | 是 | 是 | 密集检索优先搜索 |
| `basic` | 否 | 是（文本单元） | 仅文本单元向量搜索（最简单、最快） |

查询端点从 KB 的 `llm` 设置解析 LLM、从 `embedding` 解析嵌入器（所以 Ollama 能支撑向量方法）。响应携带真实的服务端 `elapsed_ms`、token 用量，以及抽取出的来源实体 / 文本片段。

## 开发

```bash
uv sync                          # 安装所有依赖（含开发依赖）
uv run alembic upgrade head      # 创建/更新数据库结构
uv run pytest                    # 后端测试
uv run ruff check .              # lint
cd web && npm install && npm run build && npm test   # 前端构建 + vitest
```

测试使用 `FakeGraphAdapter`（确定性，无需 LLM）、`FakeVectorStore`（内存）和 `FakeQueryEngine`。真实 LLM 集成测试需要环境变量中配置 provider key。

## 项目结构

```
kb_platform/
  api/            FastAPI app、路由（kbs/jobs/query/health/cost/export/graph）、模型
  db/             SQLAlchemy 模型、repository、数据库引擎工具
  engine/         索引编排器、原子步骤、unit worker、策略（含 delta）
  graph/          GraphAdapter 接口、向量存储、GraphML 写出、成本采集、embed_items
  input/          文档读取（markitdown）
  query/          QueryEngine 接口（Fake + GraphRagQueryEngine + context 来源抽取）
  reconsolidate/  增量索引后的抽取结果重新合并
  worker.py       后台索引 worker（SQLite 作为任务队列、优雅关闭）
  server.py       HTTP API 服务入口（loop="asyncio"）
web/              React + TypeScript + Vite + Tailwind 前端
tests/            后端测试（单元 + 集成；pytest）
docs/             设计文档、实现计划、验证记录、截图
alembic/          数据库迁移
```
