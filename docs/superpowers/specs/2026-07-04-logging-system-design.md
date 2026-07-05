# 统一日志系统 — 设计文档

- 日期: 2026-07-04
- 状态: 已批准(待评审)
- 依赖: Python 标准库 `logging` / `gzip` / `subprocess` / `sys` / `contextvars`(无新依赖)

## 1. 背景与目标

代码库已经在用标准库 `logging`,且用法规范 —— 约 20 个模块都有 `logger = logging.getLogger(__name__)` 并调用 `logger.info/debug/warning/exception`。**但全项目没有任何统一的日志配置**:没有 `basicConfig`、没有 `dictConfig`、没有挂任何 handler(Alembic 那个 `fileConfig` 是它自己的)。

直接后果:

1. 所有 `logger.info(...)` / `logger.debug(...)` 调用**被静默丢弃**(Python 默认只把 WARNING 及以上输出到 stderr)。现在真正能看到的只有 `logger.warning` / `logger.exception`。
2. 大量关键流程**完全没有日志**(见 §3 审计表),正常运行的全过程都是黑的,排查无凭据。

本设计目标:**加一套统一的日志系统/配置** + **给当前黑箱流程补上生命周期日志**,让现有日志调用真正能输出,并配上合理的格式、级别控制、按时间轮转 + 跨平台压缩的持久化、以及关联 ID 全链路追踪。

## 2. 已确认需求

| 维度 | 决策 |
|------|------|
| 范围 | Python 后端三个入口(`server.py` / `worker.py` / `mcp/__main__.py`);前端不动(浏览器 console 已够) |
| 能力档位 | **运维级** —— 控制台 + 按时间轮转的文件落盘 + 关联 ID |
| 配置接口 | 环境变量:`KB_LOG_LEVEL` / `KB_LOG_DIR` / `KB_LOG_CONSOLE` / `KB_LOG_ROTATE_*` / `KB_LOG_LEVELS` |
| 轮转 | **按时间**(`TimedRotatingFileHandler` 子类),worker/server 间隔短、mcp 长 |
| 压缩 | 轮转旧文件 gzip;**按 OS 区分**:Linux/mac 调系统 `gzip` 命令,Windows 用 Python `gzip`,子进程异常兜底回退 |
| 关联 ID | contextvar 携带 `request_id` / `query_id` / `job_id` / `step_id` / `unit_id` / `kb_id`,Filter 注入每条记录 |
| 补日志范围 | A 任务生命周期 + B LLM gateway/failover + C 查询路径 + D API 变更审计 + E realtime/输入侧里程碑(全做) |
| 噪声控制 | unit 级默认 INFO(大任务会有轮转兜底);`KB_LOG_LEVELS` 支持 per-logger 覆盖;热循环(per chunk/SSE delta/SQL 行)不打 |

## 3. 现状审计(为什么要做)

**有日志但只 failure-only:**

| 模块 | 现状 |
|------|------|
| `worker.py` | 3 处(recovery info、job failure exception、shutdown debug) |
| `orchestrator.py` | 3 处(plan debug、stats write fail、job fail) |
| `unit_worker.py` | **仅 1 处**(unit failed warning) —— 整个 fan-out 就一行 |
| `query/graphrag_engine.py` | 4 处全是 exception |
| `conversation/service.py` | 1 处(rewrite fail exception) |
| `routes_profiles.py` | 2 处(SSL warning) |
| `realtime.py` | dead subscriber debug、poll fail exception |

**完全没有任何日志的关键模块:**

| 模块 | 排查痛点 |
|------|----------|
| `llm/gateway.py`(failover 大脑) | profile 切换、断路器开/合、重试全黑 |
| `engine/strategies/*` | 索引跑半天不知道进展 |
| 全部 API 路由(除 `routes_profiles`) | KB/doc/profile/job/查询的写入与发起无审计 |
| `markdown_chunker` / `doc_reader` / `cost_capture` / `repository` | 输入侧、入库侧黑箱 |
| `app.py` 中间件 | 只管 SPA 路由,没有请求级日志 |

结论:**happy-path / 生命周期 / 编排顺序完全不可见**,只有"炸了"才知道。本设计同时修"配置"和"补点"两层。

## 4. 整体架构

### 4.1 新增模块:`kb_platform/logging_config.py`

不用 `logging.py` 是为了避免和标准库撞名。对外暴露:

```python
setup_logging(process: Literal["server", "worker", "mcp"]) -> None
bind_log_context(**fields) -> ContextManager   # 进出 set/reset,嵌套自动合并
get_log_context() -> dict                       # 给想读当前上下文的代码用
compress_rotated(path: Path) -> Path            # rotator 调用,按 OS 分流
```

字段名约定:`request_id` / `query_id` / `job_id` / `step_id` / `unit_id` / `kb_id`(全字符串)。

### 4.2 调用点

每个入口在最早一刻调用 `setup_logging(...)`:

- `server.py` 的 `main()` 第一行(在 `_bootstrap_llm()` 之前)
- `worker.py` 的模块 import 后、`logger = logging.getLogger(__name__)` 之后,`run_worker` 之前
- `mcp/__main__.py` 的 `main()` 里 `parser.parse_args()` 之后第一行

### 4.3 Handler 拓扑(每个进程一组)

- 一个 `ContextVarFilter`(所有 handler 共用,注入关联 ID 字段)
- `StreamHandler(stderr)` —— 受 `KB_LOG_CONSOLE`(默认 `true`)控制
- `GzipTimedRotatingFileHandler` —— 落 `KB_LOG_DIR/<process>.log`,按时间轮转 + 压缩

uvicorn 自带的 `uvicorn` / `uvicorn.access` logger 也挂到同一组 handler,access log 走统一格式。

## 5. 配置 & 轮转

### 5.1 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `KB_LOG_LEVEL` | `INFO` | 根 logger 级别;`DEBUG`/`WARNING`/`ERROR` 等标准值 |
| `KB_LOG_DIR` | `logs/`(相对 CWD) | 落盘目录,不存在则 `mkdir -p`;不可写则降级"仅控制台"并记 warning |
| `KB_LOG_CONSOLE` | `true` | 是否同时输出到 stderr |
| `KB_LOG_ROTATE_WHEN` | _未设_ | `TimedRotatingFileHandler.when`:`S`/`M`/`H`/`D`/`midnight` 等;**未设时走 §5.2 的 per-process 默认** |
| `KB_LOG_ROTATE_INTERVAL` | _未设_ | 配合 `when`;**未设时走 §5.2 的 per-process 默认** |
| `KB_LOG_ROTATE_BACKUP_COUNT` | _未设_ | 保留份数;**未设时走 §5.2 的 per-process 默认** |
| `KB_LOG_LEVELS` | _空_ | per-logger 覆盖,如 `kb_platform.engine.unit_worker=WARNING,graphrag=DEBUG` |

注:轮转三参数是**一套全局 env,设了就覆盖所有三个进程**(避免配置爆炸);**都不设**时 per-process 默认(§5.2)生效。

### 5.2 per-process 默认轮转间隔

| 进程 | 文件 | 默认轮转 | 默认保留 | 理由 |
|------|------|----------|----------|------|
| worker | `logs/worker.log` | 30 分钟 | 48 份(≈1 天) | 索引时 per-unit 噪声最大 |
| server | `logs/server.log` | 1 小时 | 24 份(≈1 天) | 请求 + 查询,中等频率 |
| mcp | `logs/mcp.log` | 1 天 | 7 份 | stdio 代理,低频 |

未被对应 env 覆盖时,这三个默认值生效。文件名带时间戳后缀由 handler 自动加。

### 5.3 第三方库噪声压制

下列库的 logger 默认压到 `WARNING`(避免 INFO 被刷屏),可用 `KB_LOG_LEVELS` 单独抬升:

| logger | 默认级别 | 理由 |
|--------|----------|------|
| `httpx` / `httpcore` / `urllib3` | `WARNING` | HTTP 请求详情噪声大 |
| `sqlalchemy` | `WARNING` | 引擎/连接 info 级意义不大 |
| `matplotlib`(若被引入) | `WARNING` | font cache 之类的噪声 |

`graphrag` 自身 logger **默认继承根级别(INFO)** —— 它会打索引进度,对排查有用;若过吵用 `KB_LOG_LEVELS=graphrag=WARNING` 压。

### 5.4 跨平台 gzip 压缩

子类化 `TimedRotatingFileHandler`,重写 `namer`(文件名加 `.gz`)+ `rotator`(压缩旧文件后删原文件):

| 平台 | 压缩方式 |
|------|----------|
| Linux / macOS(`sys.platform` 为 `linux`/`darwin`) | `subprocess` 起系统 `gzip <file>`(原生、流式、比 Python `gzip` 快;这两个平台 `gzip` 二进制必然存在) |
| Windows(`win32`) | Python 标准库 `gzip` 模块(不依赖系统二进制) |
| 兜底 | Linux/mac 上 `gzip` 命令异常(极少见)→ 自动回退 Python `gzip`,并记一条 warning(不丢日志、不崩进程) |

要点:

- 两条路径产物都是 `.gz`,**文件命名和 `backupCount` 清理逻辑跨平台完全一致**。
- 用 `sys.platform` 启动一次定策略,轮转时直接用。
- 当前正在写的活动文件(如 `worker.log`)保持明文,方便 `tail -f`;只有被切走的那份才压缩。
- **实现坑**:`getFilesToDelete` 的清理逻辑要能识别 `.gz` 后缀,否则旧文件清不掉或误删 —— 子类里一并处理。
- 封装成 `compress_rotated(path)`,方便测试 mock 三条路径。
- **压缩始终开启**,无开关(轮转的旧文件一律 gzip;量大省盘,量小也几乎无开销)。

### 5.5 `.gitignore`

加一行 `logs/`,避免误提交。

## 6. 关联 ID 机制

### 6.1 contextvar(复用 cost-capture 套路)

```python
_ctx: ContextVar[dict[str, str]] = ContextVar("kb_log_ctx")

@contextmanager
def bind_log_context(**fields):
    current = _ctx.get({})  # 未绑定时返回每调用一次的新 {},避免共享可变默认
    new = {**current, **{k: str(v) for k, v in fields.items() if v is not None}}
    token = _ctx.set(new)
    try:
        yield
    finally:
        _ctx.reset(token)
```

每层只 bind 自己关心的字段,嵌套自动合并;**始终用 `_ctx.set({...})` 整体替换,绝不就地 mutate**;asyncio task 之间天然隔离(每个 task 拷贝 context)。worker 是 per-job `asyncio.run`,contextvar 不跨 job 泄漏。

### 6.2 绑定点

| 字段 | 绑在哪 | 生命周期 |
|------|--------|----------|
| `request_id` | FastAPI http middleware(每请求 `uuid4().hex[:12]`) | 单次 HTTP 请求 |
| `kb_id` | KB 相关的 handler/worker 入口 | 单次操作 |
| `query_id` | `routes_query.py` / `routes_conversations.py` 流式 handler 入口 | 单次查询(含流式全程) |
| `job_id` | `worker.py` 认领任务后 | 整个 job |
| `step_id` | `orchestrator.py` 进入每个 step | 单个 step |
| `unit_id` | `unit_worker.py` 跑每个 unit | 单个 unit |

### 6.3 注入与渲染

- `ContextVarFilter(logging.Filter)` 把 contextvar 字段 setattr 到每条 `LogRecord`。
- 自定义 `ContextualFormatter` 渲染存在的字段为 `[job=42 step=2 unit=5]`,不存在的字段不出现(避免标准 `%(...)s` 找不到字段抛 KeyError)。

### 6.4 格式

- **控制台(stderr)**:
  ```
  2026-07-04 14:03:21.123 INFO  kb_platform.worker [job=42] — recovered 3 stale units
  ```
- **文件**(多一列 PID,方便看 worker 多任务交错):
  ```
  2026-07-04 14:03:21.123 INFO  pid=12345 kb_platform.worker [job=42 step=2 unit=5] — unit done in 412ms
  ```
- 时间 `%(asctime)s.%(msecs)03d` → `%Y-%m-%d %H:%M:%S`;级别占 5 位左对齐;`%(name)s` 是模块 logger 名。

### 6.5 响应头

middleware 顺手把 `request_id` 写进响应头 `X-Request-ID`,用户报问题时给这个 ID,后端 `grep request_id=abc` 一捞到底。

## 7. 补日志范围(逐文件落点)

原则:**每个操作记 start + done(含耗时) + failure 三类,热循环里绝不逐条打**(per chunk / per SSE delta / per SQL row 一律不打)。`KB_LOG_LEVEL=WARNING` 是全局降噪;`KB_LOG_LEVELS=...` 做 per-logger 覆盖。

### 7.A 任务生命周期

| 位置 | 新增日志 |
|------|----------|
| `worker.py` 启动 | `INFO worker started; poll_interval=2s` |
| `worker.py` 认领任务 | 现 DEBUG → `INFO [job=42] claimed; plan=plan_full` |
| `worker.py` 任务完成 | `INFO [job=42] done in 12340ms; steps=4 units=128 ok=126 failed=2 cost=$0.12` |
| `orchestrator.py` step 进入/退出 | `INFO [job=42 step=2] UNIT_FANOUT extract_graph; units=128` → `done in 4200ms; ok=126 failed=2` |
| `unit_worker.py` 单 unit 起/止 | `INFO [job=42 step=2 unit=5] start extract_graph` → `done in 412ms`(失败已有 warning,保留并带 context) |

### 7.B LLM gateway / failover(当前最黑)

| 位置 | 新增日志 |
|------|----------|
| `gateway.py` 每个 profile 尝试前 | `INFO [req=..] llm attempt profile=deepseek-main model=deepseek-chat` |
| `gateway.py` failover 发生 | `WARNING [req=..] failover profile=deepseek-main -> backup; reason=5xx/timeout/...` |
| `gateway.py` 全部 profile 耗尽 | `ERROR [req=..] all N profiles exhausted` |
| `circuit_breaker.py` 状态翻转 | `WARNING breaker deepseek-main OPEN after 5 consecutive failures` / `INFO breaker deepseek-main closed (recovered)` |
| `health.py` 探针关键翻转 | 现多为 DEBUG,断路器翻转的探针事件 bump 到 INFO |

`gateway.py` 当前无 logger,需新增 `logger = logging.getLogger(__name__)`。

### 7.C 查询路径

| 位置 | 新增日志 |
|------|----------|
| `routes_query.py` / `routes_conversations.py` 入口 | bind `query_id` + `kb_id`;`INFO [query=abc kb=3] method=local q="前 80 字…"` |
| 流式结束(generator 的 finally) | `INFO [query=abc kb=3] done in 2400ms; streamed 12 deltas` |
| `graphrag_engine.py` 首 token | `INFO [query=abc] first token in 320ms`(衡量 LLM 实际响应延迟) |
| `conversation/service.py` 改写 | `INFO [query=abc] rewrite done in 180ms -> "…"`(失败已有 exception,保留) |

### 7.D API 变更审计

`routes_kbs` / `routes_jobs` / `routes_profiles` / `routes_presets` / `routes_conversations` / `routes_export` 每个写操作 handler 一行 info(`request_id` 由 middleware 已带):

- `INFO [req=.. kb=3] KB created name="…" llm_profile=2`
- `INFO [req=.. kb=3] doc uploaded name="…" chunks=N`
- `INFO [req=.. kb=3] job created id=42 type=full`
- `INFO [req=.. job=42] unit 5 retried`
- `INFO [req=..] profile 2 created/deleted provider=deepseek`
- `INFO [req=.. kb=3] export requested format=graphml`

### 7.E realtime + 输入侧里程碑

| 位置 | 新增日志 |
|------|----------|
| `realtime.py` 订阅 | `INFO [job=42] realtime subscriber +1 (total=3)` / `-1 (total=2)`(已有 dead-subscriber DEBUG,保留) |
| `markdown_chunker.py` | 每文档一行:`INFO [req=..] chunked doc=foo.md into 42 chunks (avg 320 tok)` |
| `doc_reader.py` | 每文档一行:`INFO [req=.. doc=..] parsed 18230 chars from foo.md` |

**明确不做:** per-chunk 内容、每条 SSE delta、SQL 行级、`cost_capture`(cost 已落 unit JSON,job-done 日志会汇总)。

## 8. 测试策略

新增 `tests/test_logging.py`,沿用项目 `Fake*` + `caplog` 套路:

| 测试 | 断言 |
|------|------|
| `setup_logging` 配置 | root logger 挂上 stderr + TimedRotatingFile handler;级别取自 `KB_LOG_LEVEL` |
| 幂等 | 连续调两次不重复堆 handler |
| `bind_log_context` | 进/出正确 set/reset;嵌套合并 `job→step→unit` |
| asyncio 隔离 | 两个 task 各 bind 不同 `unit_id`,互不串 |
| Filter + 格式 | record 带注入字段;`[job=.. step=..]` 只渲染存在的字段 |
| 跨平台压缩 | linux/mac → mock subprocess gzip;windows → Python `gzip`;子进程异常 → 回退(三条路径分开测) |
| per-logger 覆盖 | `KB_LOG_LEVELS=a=DEBUG,b=WARNING` 正确解析 |
| 现有流程增量 | 用 `caplog` 断言 worker 任务完成、gateway failover、query 起/止 各自打了预期 INFO/WARNING 行 |
| 隔离 fixture | 新增 autouse fixture:每用例前后快照/还原 root logger 配置,避免全局污染(仿现有 Fernet autouse fixture) |

## 9. 项目特定的坑

| 坑 | 处理 |
|------|------|
| **MCP stdout 是协议通道** | `setup_logging("mcp")` **绝不挂 stdout handler**,只 stderr + 文件。加测试断言无 stdout handler |
| **uvicorn 自带日志会重配** | `server.py` 里 `setup_logging("server")` 先跑,再 `uvicorn.run(..., log_config=None, access_log=False)`;把 `uvicorn` / `uvicorn.access` logger 挂到我们的 handler,access log 走统一格式 |
| **worker 是 per-job `asyncio.run`** | contextvar 不跨 job 泄漏(天然干净);`job_id` 在 worker 同步循环里 bind 即可 |
| **`KB_LOG_DIR` 不可写** | `mkdir(parents=True, exist_ok=True)` 失败 → 记 warning 到 stderr,降级"仅控制台",不让服务器起不来 |
| **流式响应 + middleware 计时** | middleware 的 "request done" 在 StreamingResponse **建立时**触发,不是客户端收完。query 真实耗时由 SSE generator 的 `finally` 打 —— 注释写清楚,别重复计时 |
| **alembic 自带 `fileConfig`** | 不动(迁移是独立进程) |
| **测试全局污染** | §8 的 autouse fixture 兜底 |
| **`scripts/*` 里的 `print()`** | 不动 —— 一次性脚本,`print` 合适。本设计只覆盖 `kb_platform/` 包 |
| **graphrag 库自身日志** | 默认继承根级别(INFO);若过吵用 `KB_LOG_LEVELS=graphrag=WARNING` 压 |

## 10. 实现顺序(留给后续 plan)

1. `logging_config.py` + 单测(setup/bind/filter/格式/压缩/per-logger/隔离 fixture)
2. 接三个入口(`server.py` / `worker.py` / `mcp/__main__.py`),含 uvicorn 接管 + MCP stdout 守卫
3. 绑关联 ID(worker / orchestrator / unit_worker / query 路由 / middleware)
4. 铺 §7 A~E 日志
5. 跨平台验证轮转 + 压缩(linux/mac/windows)

## 11. 不做(YAGNI)

- 结构化 JSON 输出(那是 C 生产级,本次不做;如后续要接日志聚合,加一个 `KB_LOG_JSON=true` 开关切 formatter 即可,架构不挡)
- `GET /logs/tail` 接口 + UI 查看面板(同上)
- 前端日志
- 第三方日志库(structlog/loguru)—— 与现有 stdlib 用法冲突,不引入
