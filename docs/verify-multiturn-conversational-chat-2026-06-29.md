# A1 多轮对话 (Multi-turn Conversational Chat) — 验证记录

- 日期: 2026-06-29
- 分支: `feat/a1-multiturn-conversational-chat`
- 规格: `docs/superpowers/specs/` (A1 multiturn conversational chat design)
- 计划: `.superpowers/sdd/task-7-brief.md`（Task 7 of 7：回归 + 迁移 + 文档）
- 提交范围: Task 1–6 已分别提交（7abd38e → 01aa3a1）；本任务为 Task 7。

## 功能（已随 Task 1–6 落地）

在单发 `POST /kbs/{id}/query` 之上叠加一层多轮对话：

- **数据层**：`db/models_conversation.py` 定义 `conversation` + `message` 表（外键到 KB / 用户/助手消息）；Alembic `0006_conversations.py` 建表。
- **改写层**：`kb_platform/conversation/rewriter.py` —— 通过 `build_chat_complete` 注入 `complete` 可调用对象（graphrag 留在 `graph/`），参考最近 ~6 条消息把后续问题改写成独立查询；首轮原样直通。
- **服务层**：`kb_platform/conversation/service.py` 的 `ConversationService` 串联「改写 → 调用未改动的 `QueryEngine.search` → 持久化 user + assistant 消息（合并后的 tokens 与 sources）」。
- **API**：`api/conversations.py` 提供 6 个端点（见下表）。设置解析失败返回 200 + `error` 字段，绝不 500。
- **前端**：`web/src/` 新增 `ChatPage`（对话侧栏 + 持久化、跨刷新恢复、显示「理解为: <改写后查询>」提示与回退告警，按助手消息渲染 sources）。

**新增 API 端点：**

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/kbs/{kb_id}/conversations` | 在某个 KB 下创建对话 |
| `GET` | `/kbs/{kb_id}/conversations` | 列出对话（id、标题、片段） |
| `GET` | `/conversations/{id}` | 对话 + 按序消息 |
| `PATCH` | `/conversations/{id}` | 重命名 |
| `DELETE` | `/conversations/{id}` | 删除并级联删除消息 |
| `POST` | `/conversations/{id}/messages` | 多轮发送：改写后续 → 检索 → 持久化；返回助手消息 |

**未改动：** `POST /kbs/{id}/query`、MCP `query_knowledge_base` tool、查询测试流程。

## 自动化验证（全部由本次 Task 7 实际运行）

### 1. Alembic 迁移冒烟（DB 实际被修改；备份 `kb.db.bak`）

```text
$ uv run alembic current                 # 起点
0006 (head)

$ uv run alembic downgrade -1            # 回到 0005
$ uv run alembic current
0005

$ uv run alembic upgrade head            # 重新应用 0006
$ uv run alembic current
0006 (head)
```

结论：`alembic current` 报告 **0006 (head)**；downgrade → 0005、re-upgrade → 0006 全部干净，无孤儿表/索引。

### 2. 后端全量回归

```text
$ uv run pytest
======================== 288 passed, 1 warning in 8.40s ========================
```

（288 例全过；唯一 warning 是预存的 `StarletteDeprecationWarning`（httpx/TestClient），非本次引入。）

```text
$ uv run ruff check .
All checks passed!
```

### 3. 前端回归

```text
$ cd web && npm test
 Test Files  20 passed (20)
      Tests  75 passed (75)
   Duration  4.09s
```

```text
$ cd web && npm run build
vite v5.4.21 building for production...
✓ 1114 modules transformed.
dist/index.html                   0.97 kB │ gzip:   0.56 kB
dist/assets/index-CzruTNvm.css   35.72 kB │ gzip:   6.73 kB
dist/assets/index-DNc2lGJF.js   479.47 kB │ gzip: 150.96 kB
✓ built in 1.18s
```

### 4. E2E（Playwright，无 LLM，FakeGraphAdapter 假服务 :18000）

```text
$ cd web && npm run e2e
Running 11 tests using 1 worker
  ✓  1  create-kb.spec.ts
  ✓  2  document-detail.spec.ts
  ✓  3  entity-relation.spec.ts
  ✓  4  graph-canvas.spec.ts
  ✓  5  navigation.spec.ts (2 tests)
  ✓  6  KB detail tabs switch
  ✓  7  paste-doc.spec.ts
  ✓  8  query.spec.ts (单发 /kbs/{id}/query 路径，未改动)
  ✓  9  realtime.smoke.spec.ts (WS 增量 / 终端关闭回退)
  ✓ 10  smoke.spec.ts (SPA 加载 / 品牌可见)
  ✓ 11  trigger-job.spec.ts
  11 passed (9.6s)
```

对话路由是**纯增量**（additive）：假服务器不感知对话端点，现有 e2e 全部通过。当前 e2e 套件**未覆盖** Chat 页面多轮交互（未来可在引入真实 / 打桩对话响应后补一条 Chat 流程）。

## 手动真实 LLM 冒烟（**未运行 —— 待操作员手动执行**）

> 原因：headless 环境无法启动 API server + worker、无已索引 KB、无真实 provider key。以下清单照抄 task-7-brief Step 4，供操作员补测。

前置：启动两个进程，指向一个**已索引**的 KB + 可用的 LLM profile：

```bash
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000   # 终端 1
uv run python -m kb_platform.worker kb.db                     # 终端 2
```

打开 `http://127.0.0.1:8000` → 检索与问答 → Chat，逐项核对：

1. 新建对话 → 提问 "Acme 是做什么的?" → 返回带 sources 的接地答案。
2. 追问 "它的 CEO 是谁?" → 助手气泡显示 **理解为: <改写后引用 Acme 的独立查询>**，答案正确消解代词。
3. 刷新页面 → 对话仍在侧栏；重新打开能恢复完整 transcript。
4. 对话中途切换检索方式（如 local → global）→ 新轮采用所选 method。
5. 删除对话 → 消失，其下消息一并清除。

请在补测后回填实际 `rewritten_query` 示例到本节。

## 自审

- 上述每条命令均**实际执行**，输出为真实结果（无编造 pass 数）。
- `alembic current` = **0006**；downgrade/re-upgrade 干净。
- pytest **288 passed**、ruff **All checks passed!**、npm test **75 passed (20 files)**、`npm run build` 成功、e2e **11 passed**。
- 手动真实 LLM 冒烟已如实标注为「未运行 —— 待操作员手动执行」并附完整清单。
