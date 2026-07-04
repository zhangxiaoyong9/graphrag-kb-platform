# 统一日志系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 KB Platform 三个进程(server/worker/mcp)加统一日志配置 + 关联 ID + 给当前黑箱流程(任务生命周期、LLM failover、查询、API 审计、输入侧)补生命周期日志。

**Architecture:** 新增 `kb_platform/logging_config.py`:stdlib `logging` 集中配置 + `ContextVar` 关联 ID(复用 cost-capture 套路) + `Filter` 注入 + 自定义 `Formatter` 渲染 `[job=.. step=..]` + 跨平台 gzip 的 `TimedRotatingFileHandler`。三个入口调 `setup_logging(process)`;worker/orchestrator/unit_worker/query 路由/middleware 各自 bind 关联字段。

**Tech Stack:** Python stdlib(`logging` / `contextvars` / `gzip` / `subprocess` / `sys`);pytest `caplog`;FastAPI http middleware。无新依赖。

**Spec:** `docs/superpowers/specs/2026-07-04-logging-system-design.md`

## Global Constraints

- **stdlib only** —— 不引入 structlog/loguru(项目已全局用 `logging.getLogger(__name__)`)。
- **Python ≥3.11**, ruff line-length 100, target py311。
- **MCP 进程绝不写 stdout**(stdio 是 JSON-RPC 通道);mcp 只挂 stderr + 文件 handler。
- **`loop="asyncio"`** 仍是 uvicorn 必需(graphrag-llm 的 nest_asyncio 无法 patch uvloop)。
- 日志目录默认 `logs/`(相对 CWD),不可写则降级"仅控制台",绝不崩进程。
- 测试沿用 `Fake*` adapter/engine + `caplog`;`tests/conftest.py` 加 autouse 隔离 fixture 防 setup_logging 全局污染。
- 仪表盘 UI 是中文 —— 本计划不涉及 UI。

---

## File Structure

**新增:**
- `kb_platform/logging_config.py` — 全部日志基础设施(setup_logging / bind_log_context / ContextVarFilter / ContextualFormatter / GzipTimedRotatingFileHandler / compress_rotated / _parse_log_levels)。
- `tests/test_logging.py` — logging_config 的单测 + 跨平台压缩测试。

**修改:**
- `tests/conftest.py` — autouse `_isolate_logging` fixture。
- `kb_platform/server.py` — `main()` 调 `setup_logging("server")` + `uvicorn.run(log_config=None)`。
- `kb_platform/worker.py` — `run_worker()` 调 `setup_logging("worker")`;`run_worker_once` bind `job_id` + 任务生命周期日志。
- `kb_platform/mcp/__main__.py` — `main()` 调 `setup_logging("mcp")`。
- `kb_platform/api/app.py` — 加 request_id middleware。
- `kb_platform/engine/orchestrator.py` — bind `step_id` + step 生命周期日志。
- `kb_platform/engine/unit_worker.py` — bind `unit_id` + unit 生命周期日志。
- `kb_platform/llm/gateway.py` — 加 logger + failover 日志。
- `kb_platform/llm/circuit_breaker.py` — 加 `name` + 状态翻转日志。
- `kb_platform/llm/breaker_registry.py` — 给 breaker 传 `name`。
- `kb_platform/query/graphrag_engine.py` — 首 token 日志。
- `kb_platform/conversation/service.py` — 改写日志。
- `kb_platform/api/routes_query.py` / `routes_conversations.py` — bind query_id/kb_id + 起/止日志。
- `kb_platform/api/routes_kbs.py` / `routes_jobs.py` / `routes_profiles.py` / `routes_presets.py` / `routes_export.py` — 审计日志。
- `kb_platform/api/realtime.py` — 订阅者生命周期日志。
- `kb_platform/engine/orchestrator.py`(`_chunk_documents`)— 每文档分块里程碑日志。
- `kb_platform/input/doc_reader.py` — 每文档解析里程碑日志。
- `.gitignore` — 加 `logs/`。

---

## Task 1: logging_config 核心 + 测试隔离 fixture

`setup_logging` + `bind_log_context` + `ContextVarFilter` + `ContextualFormatter` + 第三方库压制 + 降级 + `_parse_log_levels`。文件 handler 暂用普通 `TimedRotatingFileHandler`(下个 Task 换 gzip 版)。

**Files:**
- Create: `kb_platform/logging_config.py`
- Create: `tests/test_logging.py`
- Modify: `tests/conftest.py`(加 `_isolate_logging` autouse fixture)

**Interfaces:**
- Produces: `setup_logging(process: Literal["server","worker","mcp"]) -> None`;`bind_log_context(**fields)`(contextmanager);`get_log_context() -> dict`。

- [ ] **Step 1: 加 `_isolate_logging` autouse fixture**

Modify `tests/conftest.py` — 在文件末尾追加(不要动已有的 `_kb_secret_key` 和 `seed_profile`):

```python
import logging


@pytest.fixture(autouse=True)
def _isolate_logging():
    """Snapshot/restore root logger so setup_logging() calls don't leak between tests."""
    root = logging.getLogger()
    snap_handlers = list(root.handlers)
    snap_level = root.level
    snap_levels = {
        n: logging.getLogger(n).level
        for n in ("httpx", "httpcore", "urllib3", "sqlalchemy", "uvicorn", "uvicorn.access")
    }
    yield
    for h in list(root.handlers):
        if h not in snap_handlers:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
            root.removeHandler(h)
    root.setLevel(snap_level)
    for n, lv in snap_levels.items():
        logging.getLogger(n).setLevel(lv)
```

- [ ] **Step 2: 写失败测试(create `tests/test_logging.py`)**

```python
"""logging_config: setup, bind, filter, formatter, env parsing, degrade."""
import logging
import os
import sys
from pathlib import Path

import pytest

from kb_platform.logging_config import bind_log_context, get_log_context, setup_logging


def test_setup_attaches_stderr_and_file_handlers(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_CONSOLE", "true")
    setup_logging("worker")
    root = logging.getLogger()
    stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(stream_handlers) >= 1
    assert len(file_handlers) == 1
    assert (tmp_path / "worker.log") == Path(file_handlers[0].baseFilename)


def test_setup_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("worker")
    before = len(logging.getLogger().handlers)
    setup_logging("worker")
    after = len(logging.getLogger().handlers)
    assert before == after


def test_level_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_LEVEL", "DEBUG")
    setup_logging("server")
    assert logging.getLogger().level == logging.DEBUG


def test_noisy_libs_quieted(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("server")
    for name in ("httpx", "httpcore", "urllib3", "sqlalchemy"):
        assert logging.getLogger(name).level == logging.WARNING


def test_per_logger_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_LEVELS", "kb_platform.engine.unit_worker=WARNING,graphrag=DEBUG")
    setup_logging("server")
    assert logging.getLogger("kb_platform.engine.unit_worker").level == logging.WARNING
    assert logging.getLogger("graphrag").level == logging.DEBUG


def test_log_dir_unwritable_degrades_to_console_only(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "locked"
    bad.mkdir()
    bad.chmod(0o444)
    monkeypatch.setenv("KB_LOG_DIR", str(bad / "worker.log"))
    setup_logging("worker")
    root = logging.getLogger()
    # No FileHandler added when the dir isn't writable.
    assert not any(isinstance(h, logging.FileHandler) for h in root.handlers)
    captured = capsys.readouterr()
    assert "file logging disabled" in captured.err


def test_bind_log_context_sets_and_resets():
    assert get_log_context() == {}
    with bind_log_context(job_id=42):
        assert get_log_context() == {"job_id": "42"}
        with bind_log_context(step_id=2):
            assert get_log_context() == {"job_id": "42", "step_id": "42" if False else "2"}
        # step_id gone after inner exit
        assert get_log_context() == {"job_id": "42"}
    assert get_log_context() == {}


def test_bind_log_context_drops_none():
    with bind_log_context(job_id=1, step_id=None, unit_id=None):
        assert get_log_context() == {"job_id": "1"}


@pytest.mark.asyncio
async def test_bind_isolation_across_asyncio_tasks():
    import asyncio

    seen: dict[str, str] = {}

    async def child(tag: str):
        with bind_log_context(unit_id=tag):
            await asyncio.sleep(0.01)
            seen[tag] = get_log_context()["unit_id"]

    async with asyncio.TaskGroup() as tg:
        tg.create_task(child("a"))
        tg.create_task(child("b"))
    assert seen == {"a": "a", "b": "b"}


def test_filter_and_formatter_render_context(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("server")
    logger = logging.getLogger("kb_platform.test_subject")
    with bind_log_context(job_id=7, step_id=3):
        logger.info("hello world")
    err = capsys.readouterr().err
    assert "hello world" in err
    assert "[job=7 step=3]" in err


def test_formatter_omits_absent_fields(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("server")
    logging.getLogger("kb_platform.test_subject2").info("plain line")
    err = capsys.readouterr().err
    assert "[" not in err.split("—")[-1]  # no context block when nothing bound
```

> Note: `test_log_dir_unwritable_degrades_to_console_only` may need root/skip on systems where chmod is ignored (Windows). Keep it; CI is linux/mac.

- [ ] **Step 3: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `ImportError: cannot import name 'bind_log_context' ...` (module doesn't exist yet).

- [ ] **Step 4: 实现 `kb_platform/logging_config.py`**

```python
"""Centralized logging: setup + correlation-ID contextvar + cross-platform gzip rotation.

Call ``setup_logging("server"|"worker"|"mcp")`` at each entrypoint. Bind correlation
fields via ``bind_log_context(job_id=.., step_id=.., ...)``; a Filter stamps them onto
every LogRecord and the formatter renders them as ``[job=.. step=..]``.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Literal

_LOGGER = logging.getLogger(__name__)

_CTX: ContextVar[dict[str, str]] = ContextVar("kb_log_ctx")

_CONTEXT_FIELDS = ("request_id", "query_id", "kb_id", "job_id", "step_id", "unit_id")

# Third-party libs whose INFO output is noise → quieted to WARNING by default.
_NOISY_LIBS = ("httpx", "httpcore", "urllib3", "sqlalchemy")

# Per-process rotation defaults (overridden uniformly by KB_LOG_ROTATE_* env).
_PROCESS_DEFAULTS: dict[str, dict[str, object]] = {
    "server": {"when": "H", "interval": 1, "backup_count": 24},
    "worker": {"when": "M", "interval": 30, "backup_count": 48},
    "mcp": {"when": "D", "interval": 1, "backup_count": 7},
}

_CONSOLE_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s%(context)s — %(message)s"
_FILE_FMT = (
    "%(asctime)s.%(msecs)03d %(levelname)-5s pid=%(process)d %(name)s%(context)s — %(message)s"
)
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class ContextVarFilter(logging.Filter):
    """Stamp current contextvar fields onto each record so the formatter can render them."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _CTX.get({})
        for key in _CONTEXT_FIELDS:
            if key in ctx:
                setattr(record, key, ctx[key])
        return True


class ContextualFormatter(logging.Formatter):
    """Render present context fields as ``[k=v k=v]``; empty when none bound."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [f"{k}={getattr(record, k)}" for k in _CONTEXT_FIELDS if hasattr(record, k)]
        record.context = (" [" + " ".join(parts) + "]") if parts else ""
        return super().format(record)


@contextmanager
def bind_log_context(**fields):
    """Bind correlation fields for the duration of the ``with`` block (nested merge)."""
    current = _CTX.get({})
    new = {**current, **{k: str(v) for k, v in fields.items() if v is not None}}
    token = _CTX.set(new)
    try:
        yield
    finally:
        _CTX.reset(token)


def get_log_context() -> dict[str, str]:
    """Return a copy of the current correlation context (for inspection / tests)."""
    return dict(_CTX.get({}))


def _parse_log_levels(spec: str) -> dict[str, str]:
    """Parse ``a=DEBUG,b=WARNING`` into a dict. Empty/invalid entries skipped."""
    out: dict[str, str] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, lv = pair.split("=", 1)
        name, lv = name.strip(), lv.strip().upper()
        if name and lv:
            out[name] = lv
    return out


def _build_file_handler(process: str, log_dir: Path) -> logging.Handler | None:
    """Return a TimedRotatingFileHandler, or None if the dir isn't writable (degrade)."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(
            f"[kb_platform.logging_config] KB_LOG_DIR {log_dir} unusable: {exc}; "
            f"file logging disabled\n"
        )
        return None
    defaults = _PROCESS_DEFAULTS[process]
    when = os.environ.get("KB_LOG_ROTATE_WHEN", str(defaults["when"]))
    interval = int(os.environ.get("KB_LOG_ROTATE_INTERVAL", str(defaults["interval"])))
    backup_count = int(
        os.environ.get("KB_LOG_ROTATE_BACKUP_COUNT", str(defaults["backup_count"]))
    )
    # GzipTimedRotatingFileHandler is swapped in by Task 2; plain handler for now.
    fh = TimedRotatingFileHandler(
        filename=log_dir / f"{process}.log",
        when=when,
        interval=interval,
        backupCount=backup_count,
        encoding="utf-8",
    )
    fh.setFormatter(ContextualFormatter(_FILE_FMT, datefmt=_DATEFMT))
    return fh


def setup_logging(process: Literal["server", "worker", "mcp"]) -> None:
    """Configure root logging for one process. Idempotent (marker on handlers)."""
    level = os.environ.get("KB_LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)

    # Idempotency: don't stack handlers on repeat calls.
    if any(getattr(h, "_kb_log_marker", False) for h in root.handlers):
        return

    filt = ContextVarFilter()
    handlers: list[logging.Handler] = []

    if os.environ.get("KB_LOG_CONSOLE", "true").lower() in ("true", "1", "yes"):
        sh = logging.StreamHandler()  # stderr by default — never stdout
        sh.setFormatter(ContextualFormatter(_CONSOLE_FMT, datefmt=_DATEFMT))
        sh.addFilter(filt)
        sh._kb_log_marker = True  # type: ignore[attr-defined]
        handlers.append(sh)

    log_dir = Path(os.environ.get("KB_LOG_DIR", "logs"))
    fh = _build_file_handler(process, log_dir)
    if fh is not None:
        fh.addFilter(filt)
        fh._kb_log_marker = True  # type: ignore[attr-defined]
        handlers.append(fh)

    for h in handlers:
        root.addHandler(h)

    for name in _NOISY_LIBS:
        logging.getLogger(name).setLevel(logging.WARNING)

    for name, lv in _parse_log_levels(os.environ.get("KB_LOG_LEVELS", "")).items():
        logging.getLogger(name).setLevel(lv)

    # MCP stdout guard: stdout is the JSON-RPC transport; never log there.
    if process == "mcp":
        for h in list(root.handlers):
            if getattr(h, "stream", None) is sys.stdout:
                root.removeHandler(h)


__all__ = [
    "ContextVarFilter",
    "ContextualFormatter",
    "bind_log_context",
    "get_log_context",
    "setup_logging",
]
```

- [ ] **Step 5: 运行测试,确认通过**

Run: `uv run pytest tests/test_logging.py -v`
Expected: 11 passed.

- [ ] **Step 6: 提交**

```bash
git add kb_platform/logging_config.py tests/test_logging.py tests/conftest.py
git commit -m "feat(logging): centralized setup_logging + correlation-ID contextvar + tests"
```

---

## Task 2: 跨平台 gzip 轮转 handler

`GzipTimedRotatingFileHandler` + `compress_rotated`(Linux/mac 调系统 `gzip -c`,Windows/兜底用 Python `gzip`),并让 `setup_logging` 用它替换普通 handler。

**Files:**
- Modify: `kb_platform/logging_config.py`(新增 `compress_rotated` / `GzipTimedRotatingFileHandler`,`_build_file_handler` 改用它)
- Test: `tests/test_logging.py`(追加压缩测试)

**Interfaces:**
- Produces: `compress_rotated(source: Path, dest: Path) -> None`;`GzipTimedRotatingFileHandler`(TimedRotatingFileHandler 子类)。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_logging.py`)**

```python
import gzip
from unittest import mock

from kb_platform.logging_config import GzipTimedRotatingFileHandler, compress_rotated


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_compress_rotated_python_path(tmp_path):
    """Force the Python-gzip fallback by pretending gzip subprocess fails."""
    src = _write(tmp_path / "worker.log", "x" * 5000)
    dest = tmp_path / "worker.log.2026-07-04_14-30.gz"
    with mock.patch("platform.system", return_value="Windows"):
        compress_rotated(src, dest)
    assert not src.exists()
    assert dest.exists()
    assert gzip.decompress(dest.read_bytes()).decode() == "x" * 5000


def test_compress_rotated_system_gzip(tmp_path):
    """Linux/mac path: shell out to `gzip -c`; verify .gz output + source removed."""
    src = _write(tmp_path / "worker.log", "hello\n")
    dest = tmp_path / "worker.log.ts.gz"
    with mock.patch("kb_platform.logging_config.sys.platform", "linux"):
        compress_rotated(src, dest)
    assert not src.exists()
    assert gzip.decompress(dest.read_bytes()).decode() == "hello\n"


def test_compress_rotated_system_gzip_failure_falls_back(tmp_path):
    """If `gzip` binary is missing/broken, fall back to Python gzip (no crash)."""
    src = _write(tmp_path / "worker.log", "fallback\n")
    dest = tmp_path / "worker.log.ts.gz"
    with mock.patch("kb_platform.logging_config.sys.platform", "linux"), \
         mock.patch("subprocess.run", side_effect=FileNotFoundError("no gzip")):
        compress_rotated(src, dest)
    assert gzip.decompress(dest.read_bytes()).decode() == "fallback\n"


def test_file_handler_gzips_on_rollover(tmp_path, monkeypatch):
    """Rotating produces a .gz file; pruning keeps only backupCount files."""
    import time

    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_ROTATE_WHEN", "S")
    monkeypatch.setenv("KB_LOG_ROTATE_INTERVAL", "1")
    monkeypatch.setenv("KB_LOG_ROTATE_BACKUP_COUNT", "2")
    setup_logging("worker")
    log = logging.getLogger("kb_platform.rollover_test")
    for _ in range(3):
        log.info("filler line " * 50)
        # Force a rollover on each handler that supports it.
        for h in logging.getLogger().handlers:
            if isinstance(h, GzipTimedRotatingFileHandler):
                h.doRollover()
        time.sleep(0.01)
    gz_files = list(tmp_path.glob("worker.log.*.gz"))
    assert len(gz_files) >= 1, "rotated files should be gzipped"
    # Pruning: at most backupCount rotated files (plus the active worker.log).
    rotated = [p for p in tmp_path.glob("worker.log*") if p.name != "worker.log"]
    assert len(rotated) <= 2
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -v -k compress or rollover`
Expected: FAIL — `ImportError: cannot import name 'GzipTimedRotatingFileHandler'`。

- [ ] **Step 3: 实现 gzip handler + compress_rotated**

Modify `kb_platform/logging_config.py` — 在 import 区加 `gzip` / `shutil` / `subprocess`,然后在 `ContextualFormatter` 类之后插入:

```python
import gzip
import shutil
import subprocess
```
(放在文件顶部其它 `import` 一起。)

然后在 `_parse_log_levels` 函数之前插入:

```python
def compress_rotated(source: Path, dest: Path) -> None:
    """Gzip ``source`` to ``dest`` and remove ``source``. Cross-platform.

    Linux/macOS shell out to ``gzip -c`` (faster, native); Windows and any
    subprocess failure fall back to Python's ``gzip`` module so logging never
    crashes and the rotated file is never lost.
    """
    if sys.platform in ("linux", "darwin"):
        try:
            with open(dest, "wb") as out:
                subprocess.run(
                    ["gzip", "-c", str(source)],
                    check=True,
                    stdout=out,
                    stderr=subprocess.PIPE,
                )
            source.unlink()
            return
        except (OSError, subprocess.CalledProcessError) as exc:
            _LOGGER.warning(
                "system gzip failed for %s; falling back to Python gzip: %s", source, exc
            )
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    source.unlink()


class GzipTimedRotatingFileHandler(TimedRotatingFileHandler):
    """TimedRotatingFileHandler that gzips each rotated file (cross-platform).

    Overriding ``rotation_filename`` (append ``.gz``) and ``rotate`` (compress)
    keeps ``getFilesToDelete`` pruning correct: it globs ``baseFilename + "*"``，
    which matches ``.gz`` files, so backupCount retention still works.
    """

    def rotation_filename(self, default_name: str) -> str:  # type: ignore[override]
        return super().rotation_filename(default_name) + ".gz"

    def rotate(self, source: str, dest: str) -> None:  # type: ignore[override]
        if not os.path.exists(source):
            return
        compress_rotated(Path(source), Path(dest))
```

把 `_build_file_handler` 里的 `TimedRotatingFileHandler(...)` 改成 `GzipTimedRotatingFileHandler(...)`(同一组参数)。同时把 `GzipTimedRotatingFileHandler` / `compress_rotated` 加进 `__all__`。

- [ ] **Step 4: 运行测试,确认通过**

Run: `uv run pytest tests/test_logging.py -v`
Expected: all pass (包括新加的 4 个压缩/轮转测试)。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/logging_config.py tests/test_logging.py
git commit -m "feat(logging): cross-platform gzip rotation (system gzip w/ Python fallback)"
```

---

## Task 3: 接三个入口(server / worker / mcp)

每个入口调 `setup_logging`。server 用 `log_config=None` 让 uvicorn 不覆盖,access log 经 propagation 走我们的 handler。mcp 加 stdout 守卫测试。

**Files:**
- Modify: `kb_platform/server.py:22`(main 顶部)
- Modify: `kb_platform/worker.py:162`(run_worker 顶部)
- Modify: `kb_platform/mcp/__main__.py:25`(parse_args 之后)
- Test: `tests/test_logging.py`(追加 mcp stdout 守卫测试)

**Interfaces:**
- Consumes: `setup_logging(process)` from Task 1.

- [ ] **Step 1: 写 mcp stdout 守卫失败测试(追加到 `tests/test_logging.py`)**

```python
def test_mcp_never_logs_to_stdout(tmp_path, monkeypatch):
    """stdio MCP: stdout is JSON-RPC — setup_logging('mcp') must not attach a stdout handler."""
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("mcp")
    for h in logging.getLogger().handlers:
        stream = getattr(h, "stream", None)
        assert stream is not sys.stdout, "MCP process must never log to stdout"
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py::test_mcp_never_logs_to_stdout -v`
Expected: 可能已经 PASS(StreamHandler 默认 stderr)—— 这是回归守护。若 PASS 直接到 Step 3。

- [ ] **Step 3: 改 server.py**

Modify `kb_platform/server.py` 的 `main()` —— 在 `_bootstrap_llm()` **之前**插入 setup,并给 `uvicorn.run` 加 `log_config=None`:

```python
def main() -> None:
    import uvicorn

    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine
    from kb_platform.db.repository import Repository
    from kb_platform.llm.bootstrap import bootstrap as _bootstrap_llm
    from kb_platform.logging_config import setup_logging

    # Centralized logging FIRST, before any other code logs. uvicorn is told
    # log_config=None so it doesn't reconfigure logging; its `uvicorn` /
    # `uvicorn.access` loggers propagate to our root handlers instead.
    setup_logging("server")

    _bootstrap_llm()

    db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"
    os.environ.setdefault("KB_DB_URL", f"sqlite:///{db}")
    data_root = sys.argv[2] if len(sys.argv) > 2 else "."
    host = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 8000

    repo = Repository(create_engine(f"sqlite:///{db}"))
    app = create_app(repo, data_root=data_root)
    uvicorn.run(app, host=host, port=port, loop="asyncio", log_config=None)
```

- [ ] **Step 4: 改 worker.py**

Modify `kb_platform/worker.py` 的 `run_worker()` —— 在 `from kb_platform.llm.bootstrap import bootstrap as _bootstrap_llm` **之前**插入:

```python
    from kb_platform.logging_config import setup_logging

    setup_logging("worker")
```

(放在 `run_worker` 函数体最开头,`import signal` / `import threading` 之前。)

- [ ] **Step 5: 改 mcp/__main__.py**

Modify `kb_platform/mcp/__main__.py` 的 `main()` —— 在 `args = parser.parse_args()` **之后**、`server = build_mcp_server(...)` **之前**插入:

```python
    from kb_platform.logging_config import setup_logging

    # stdio transport: setup_logging('mcp') attaches only stderr + file handlers,
    # NEVER stdout (stdout is the JSON-RPC channel).
    setup_logging("mcp")
```

- [ ] **Step 6: 跑全量测试 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全绿(import 是函数内 lazy import,ruff 不报循环依赖)。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/server.py kb_platform/worker.py kb_platform/mcp/__main__.py tests/test_logging.py
git commit -m "feat(logging): wire setup_logging into server/worker/mcp entrypoints"
```

---

## Task 4: FastAPI request_id middleware

每个 HTTP 请求生成 `request_id`,bind 进 contextvar,写响应头 `X-Request-ID`,并打 request start/done 日志(含方法、路径、状态码、耗时)。

**Files:**
- Modify: `kb_platform/api/app.py`(在 `create_app` 里加 middleware)
- Test: `tests/test_logging.py`(追加)

**Interfaces:**
- Produces: 每个请求生命周期内,所有日志带 `request_id`(经 Task 1 的 Filter 注入)。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_logging.py`)**

```python
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_with_middleware() -> FastAPI:
    from kb_platform.api.app import create_app
    from kb_platform.db.repository import Repository
    from kb_platform.db.engine import create_engine
    import tempfile

    repo = Repository(create_engine(f"sqlite:///{tempfile.mktemp()}"))
    repo._bootstrap_tables() if hasattr(repo, "_bootstrap_tables") else None
    return create_app(repo, data_root=tempfile.mkdtemp())


def test_middleware_binds_request_id_and_sets_header(monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", tempfile.mkdtemp())
    setup_logging("server")
    app = _app_with_middleware()
    with TestClient(app) as client:
        r = client.get("/kbs")
    assert "x-request-id" in {k.lower() for k in r.headers}
    assert len(r.headers["x-request-id"]) == 12


def test_middleware_logs_request_start_and_done(monkeypatch, capsys):
    monkeypatch.setenv("KB_LOG_DIR", tempfile.mkdtemp())
    setup_logging("server")
    app = _app_with_middleware()
    with TestClient(app) as client:
        client.get("/kbs")
    err = capsys.readouterr().err
    assert "request start" in err
    assert "request done" in err
    assert "GET /kbs" in err
```

> Note: 若 `Repository` 没有 `_bootstrap_tables`,测试需用 alembic 或 `Base.metadata.create_all`。看 Step 4 的实现细节里用 `Base.metadata.create_all(repo.engine)` 建表;测试 helper 同样用。若已有 test fixture 建表更顺手,可改用 `tests/` 里现成的 app fixture。检查 `tests/` 是否已有 `client` fixture,有则复用。

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -k request_id or request_start -v`
Expected: FAIL — middleware 还没加,无 `x-request-id` 头。

- [ ] **Step 3: 检查现有 test app fixture**

Run: `rg -n "create_app|TestClient" tests/ | head -20`
看 tests 里是否已有现成的 app/client fixture 可复用,避免在 test_logging.py 里重新搭。若有(如 `tests/conftest.py` 里的 `app`/`client`),把 Step 1 的 `_app_with_middleware` 换成复用。没有就保留。

- [ ] **Step 4: 实现 middleware**

Modify `kb_platform/api/app.py` —— 在 `create_app` 里、`app.include_router(...)` 调用**之前**(router 之前之后都行,middleware 是包在请求外的)插入。需要新 import:

```python
import time
from uuid import uuid4
```

然后在 `app = FastAPI(...)` 之后、第一个 `app.include_router` 之前加:

```python
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Bind a per-request id, log start/done, stamp X-Request-ID on the response.

        NOTE: for SSE/StreamingResponse, ``call_next`` returns once headers are
        sent; the body streams after. So this 'request done' line marks dispatch
        time, not full stream completion. Query/stream timing is logged inside
        the route's generator (see routes_query). Don't double-count.
        """
        from kb_platform.logging_config import bind_log_context

        request_id = uuid4().hex[:12]
        with bind_log_context(request_id=request_id):
            logging.getLogger("kb_platform.api").info(
                "request start %s %s", request.method, request.url.path
            )
            t0 = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception:
                logging.getLogger("kb_platform.api").exception(
                    "request failed %s %s", request.method, request.url.path
                )
                raise
            duration_ms = (time.perf_counter() - t0) * 1000
            logging.getLogger("kb_platform.api").info(
                "request done %s %s -> %d %.1fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
```

文件顶部加 `import logging`(app.py 当前没 import logging)。

- [ ] **Step 5: 运行测试,确认通过**

Run: `uv run pytest tests/test_logging.py -k request_id or request_start -v`
Expected: PASS。

- [ ] **Step 6: 跑全量测试**

Run: `uv run pytest -q`
Expected: 全绿(已有 API 测试不受影响,middleware 只加日志)。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/api/app.py tests/test_logging.py
git commit -m "feat(logging): request_id middleware (X-Request-ID + start/done logs)"
```

---

## Task 5: 任务生命周期日志(worker / orchestrator / unit_worker)

bind `job_id`(worker) / `step_id`(orchestrator) / `unit_id`(unit_worker),并补 start/done 生命周期日志。worker 任务认领从 DEBUG 升 INFO。

**Files:**
- Modify: `kb_platform/worker.py`(run_worker_once bind job_id + 任务起/止/完成日志)
- Modify: `kb_platform/engine/orchestrator.py`(run 绑 job 上下文,_run_step bind step_id + step 起/止日志)
- Modify: `kb_platform/engine/unit_worker.py`(_process bind unit_id + unit 起/止日志)
- Test: `tests/test_logging.py`(追加 caplog 断言)

**Interfaces:**
- Consumes: `bind_log_context` from Task 1。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_logging.py`)**

```python
def test_worker_logs_job_claim_and_done(monkeypatch, tmp_path, caplog):
    """run_worker_once logs claim (INFO) and done (INFO with duration)."""
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("worker")
    # Reuse an existing engine test harness that runs a fake job end-to-end.
    # If tests/test_unit_worker.py has a helper that builds repo+adapter+job,
    # call it here; otherwise build minimal fakes inline.
    pytest.importorskip("tests.test_unit_worker")  # skip if harness absent
    # See Step 3 for the concrete harness reuse; placeholder assertion:
    with caplog.at_level(logging.INFO, logger="kb_platform.worker"):
        # ... drive run_worker_once with a fake repo/adapter that yields one job ...
        pass
    msgs = [r.getMessage() for r in caplog.records if r.name == "kb_platform.worker"]
    assert any("claimed" in m for m in msgs)
    assert any("done in" in m for m in msgs)
```

> 实现者注意:这个测试的"驱动一个 job"部分要复用 `tests/test_unit_worker.py` 或 `tests/test_engine.py` 里已有的 fake repo + FakeGraphAdapter + 建表的 fixture。先 `rg -n "run_worker_once\|run_job\|FakeGraphAdapter" tests/` 找现成 helper,把上面的 `pass` 替换成驱动代码。caplog 断言不变。

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -k job_claim -v`
Expected: FAIL — `claimed` / `done in` 还没打。

- [ ] **Step 3: 改 worker.py — bind job_id + 生命周期日志**

Modify `kb_platform/worker.py` 顶部 import 区加:

```python
import time
```

改 `run_worker_once` —— 把 claim 之后到 `orch.run` 那段用 `bind_log_context` + 计时包起来。把现有的:

```python
    job = repo.claim_one_pending_job()
    if job is None:
        return

    try:
        with session_scope(repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
            if kb is None:
                raise ValueError(f"job {job.id} references missing kb {job.kb_id}")
            data_root = kb.data_root
            settings_json = kb.settings_json
            llm_profile_id = kb.llm_profile_id
            embedding_profile_id = kb.embedding_profile_id
            llm_fallback_profile_ids = kb.llm_fallback_profile_ids

        adapter = adapter_factory(
            _SettingsKb(
                settings_json=settings_json,
                data_root=data_root,
                llm_profile_id=llm_profile_id,
                embedding_profile_id=embedding_profile_id,
                llm_fallback_profile_ids=llm_fallback_profile_ids,
            )
        )
        orch = Orchestrator(
            repo=repo,
            adapter=adapter,
            data_root=data_root,
            concurrency=_parse_concurrency(settings_json, concurrency),
        )
        await orch.run(job.id, min_success_ratio=_parse_min_ratio(settings_json))
    except Exception:  # noqa: BLE001
        logger.exception("job %s failed; marking FAILED", job.id)
        repo.set_job_status(job.id, JobStatus.FAILED)
```

改成:

```python
    job = repo.claim_one_pending_job()
    if job is None:
        return

    from kb_platform.logging_config import bind_log_context

    with bind_log_context(job_id=job.id, kb_id=job.kb_id):
        t0 = time.perf_counter()
        logger.info("job %s claimed; type=%s", job.id, getattr(job, "type", "full"))
        try:
            with session_scope(repo.engine) as s:
                kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
                if kb is None:
                    raise ValueError(f"job {job.id} references missing kb {job.kb_id}")
                data_root = kb.data_root
                settings_json = kb.settings_json
                llm_profile_id = kb.llm_profile_id
                embedding_profile_id = kb.embedding_profile_id
                llm_fallback_profile_ids = kb.llm_fallback_profile_ids

            adapter = adapter_factory(
                _SettingsKb(
                    settings_json=settings_json,
                    data_root=data_root,
                    llm_profile_id=llm_profile_id,
                    embedding_profile_id=embedding_profile_id,
                    llm_fallback_profile_ids=llm_fallback_profile_ids,
                )
            )
            orch = Orchestrator(
                repo=repo,
                adapter=adapter,
                data_root=data_root,
                concurrency=_parse_concurrency(settings_json, concurrency),
            )
            await orch.run(job.id, min_success_ratio=_parse_min_ratio(settings_json))
            final = repo.get_job(job.id).status
            logger.info(
                "job %s done in %.0fms; status=%s",
                job.id, (time.perf_counter() - t0) * 1000, final,
            )
        except Exception:  # noqa: BLE001
            logger.exception("job %s failed; marking FAILED", job.id)
            repo.set_job_status(job.id, JobStatus.FAILED)
```

`run_worker` 的 `logger = logging.getLogger(__name__)` 已有;`run_worker` 启动时加一行(在 `setup_logging("worker")` 之后):

```python
    logger.info("worker started; poll_interval=%.1fs", poll_interval)
```

- [ ] **Step 4: 改 orchestrator.py — bind step_id + step 日志**

Modify `kb_platform/engine/orchestrator.py` 顶部加:

```python
import time
```

改 `run` —— 把现有 `logger.debug("job %s using %s", job_id, plan_name)` 升级为 INFO 并保留 plan 信息(它已在 job context 内,job_id 由 worker 绑定):

```python
            plan_name = "plan_incremental" if job.type == "incremental" else "plan_full"
            logger.info("job %s using %s", job_id, plan_name)
```

改 `_run_step` —— 用 `bind_log_context(step_id=...)` + 计时包住 dispatch,done 时打 ok/failed 计数:

```python
    async def _run_step(self, step, min_success_ratio: float) -> None:
        from kb_platform.logging_config import bind_log_context

        self.repo.set_step_status(step.id, StepStatus.RUNNING)
        with bind_log_context(step_id=step.id):
            logger.info(
                "step %s [%s] start; kind=%s", step.id, step.name, step.kind
            )
            t0 = time.perf_counter()
            try:
                await self._dispatch_step(step, min_success_ratio)
            except Exception:
                self.repo.set_step_status(step.id, StepStatus.FAILED)
                logger.exception("step %s [%s] failed", step.id, step.name)
                raise
            counts = self.repo.unit_counts_by_status(step.id) if step.kind == "unit_fanout" else {}
            ok = counts.get("succeeded", 0)
            failed = counts.get("failed", 0)
            logger.info(
                "step %s [%s] done in %.0fms; ok=%s failed=%s",
                step.id, step.name, (time.perf_counter() - t0) * 1000, ok, failed,
            )
```

(把原 `_run_step` 的 try/except 体内 `self.repo.set_step_status(step.id, StepStatus.FAILED); raise` 保留并加日志;原 `_dispatch_step` 不动。)

- [ ] **Step 5: 改 unit_worker.py — bind unit_id + unit 日志**

Modify `kb_platform/engine/unit_worker.py` 的 `_process`:

```python
    async def _process(self, strategy, unit) -> None:
        from kb_platform.graph.cost_capture import use_recorder
        from kb_platform.logging_config import bind_log_context
        import time

        with bind_log_context(unit_id=unit.id):
            t0 = time.perf_counter()
            try:
                with use_recorder() as rec:
                    result = await strategy.run_unit(self.adapter, unit, self.repo)
                if result.cost_json is None and rec:
                    result.cost_json = rec.to_json()
                if result.llm_raw_output is None and rec:
                    result.llm_raw_output = rec.raw_output()
                strategy.persist(self.data_root, unit, result)
                self.repo.set_unit_succeeded(
                    unit.id,
                    input_hash=result.input_hash,
                    cost_json=result.cost_json,
                    llm_raw_output=result.llm_raw_output,
                )
                logger.info(
                    "unit %s [%s] done in %.0fms",
                    unit.id, strategy.kind, (time.perf_counter() - t0) * 1000,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("unit %s [%s] failed: %s", unit.id, strategy.kind, e)
                self.repo.set_unit_failed(unit.id, str(e))
```

- [ ] **Step 6: 运行测试,确认通过**

Run: `uv run pytest tests/test_logging.py -k job_claim -v && uv run pytest tests/test_unit_worker.py tests/test_engine*.py -q`
Expected: 新测试 PASS;现有 engine/worker 测试不回归。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/worker.py kb_platform/engine/orchestrator.py kb_platform/engine/unit_worker.py tests/test_logging.py
git commit -m "feat(logging): job/step/unit lifecycle logs + correlation IDs"
```

---

## Task 6: LLM gateway / 断路器 failover 日志

`gateway.py` 加 logger + 每个 profile 尝试 / failover / 全部耗尽日志。`circuit_breaker.py` 加 `name` + 状态翻转日志。`breaker_registry.py` 给 breaker 传 `name`。

**Files:**
- Modify: `kb_platform/llm/gateway.py`
- Modify: `kb_platform/llm/circuit_breaker.py`
- Modify: `kb_platform/llm/breaker_registry.py`
- Test: `tests/test_logging.py`(追加)+ 复用 `tests/llm/` 现有 gateway 测试

**Interfaces:**
- `CircuitBreaker.__init__` 新增 `name: str | None = None`(向后兼容,默认 None)。

- [ ] **Step 1: 写失败测试(追加到 `tests/test_logging.py`)**

```python
def test_breaker_logs_open_and_close(caplog):
    from kb_platform.llm.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=2, open_seconds=30.0, name="deepseek/main")
    with caplog.at_level(logging.WARNING, logger="kb_platform.llm.circuit_breaker"):
        cb.record_failure()  # 1
        cb.record_failure()  # 2 -> OPEN
    assert any("OPEN" in r.getMessage() and "deepseek/main" in r.getMessage() for r in caplog.records)


def test_gateway_logs_failover(caplog, monkeypatch):
    """Two profiles: first fails (5xx), second succeeds -> WARNING failover line."""
    import httpx
    from kb_platform.llm.gateway import FailoverGateway, ChatRequest
    from kb_platform.llm.circuit_breaker import CircuitBreaker
    from kb_platform.llm.request import ProviderConfig

    # Build a fake transport: profile 0 returns 500, profile 1 returns 200.
    # (Reuse the pattern from tests/llm/test_gateway.py; see Step 3.)
    pytest.importorskip("tests.llm.test_gateway")
    # ... construct FailoverGateway with two ProviderConfigs + breakers ...
    with caplog.at_level(logging.WARNING, logger="kb_platform.llm.gateway"):
        # await gateway.collect(ChatRequest(...)) or astream
        pass
    assert any("failover" in r.getMessage().lower() for r in caplog.records)
```

> 实现者注意:gateway 测试的 transport 构造复用 `tests/llm/test_gateway.py` 里已有的 mock httpx transport pattern。先 `rg -n "MockTransport\|httpx.Mock\|FailoverGateway" tests/llm/` 找现成构造代码替换上面的 `pass`。

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -k breaker_logs or failover -v`
Expected: FAIL — `name` 参数不存在 / failover 日志没打。

- [ ] **Step 3: 改 circuit_breaker.py — 加 name + 翻转日志**

Modify `kb_platform/llm/circuit_breaker.py` 全文替换为:

```python
"""Per-profile circuit breaker: closed -> open (N consecutive failures) ->
half-open (after TTL) -> closed on success / open on failure.

Relaxed half-open: while half-open, ``allow()`` admits requests (the first to
succeed closes the breaker). This avoids cross-request locking; the gateway
drives one profile at a time per logical call."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        name: str | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self.name = name or "breaker"
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.time() - self._opened_at >= self.open_seconds:
                self._state = "half_open"
                logger.info("breaker %s half_open (probing)", self.name)
                return True
            return False
        # half_open
        return True

    def record_success(self) -> None:
        was_open = self._state in ("open", "half_open")
        self._failures = 0
        self._state = "closed"
        if was_open:
            logger.info("breaker %s closed (recovered)", self.name)

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == "half_open":
            self._state = "open"
            self._opened_at = time.time()
            logger.warning("breaker %s re-opened from half_open", self.name)
            return
        if self._failures >= self.failure_threshold and self._state == "closed":
            self._state = "open"
            self._opened_at = time.time()
            logger.warning(
                "breaker %s OPEN after %d consecutive failures", self.name, self._failures
            )
```

- [ ] **Step 4: 改 breaker_registry.py — 给 breaker 传 name**

Modify `kb_platform/llm/breaker_registry.py` 的 `breaker_for` —— 构造 CircuitBreaker 时传 name:

```python
        if entry is None:
            cb = CircuitBreaker(
                failure_threshold=failure_threshold,
                open_seconds=open_seconds,
                name=f"{cfg.provider}/{cfg.model}",
            )
            entry = (cb, cfg)
            _ENTRIES[k] = entry
```

- [ ] **Step 5: 改 gateway.py — 加 logger + failover 日志**

Modify `kb_platform/llm/gateway.py` 顶部 import 区后加:

```python
import logging

logger = logging.getLogger(__name__)
```

改 `astream` —— 在 `for idx, pk in self._candidates():` 循环体开头(cfg 取出后)加尝试日志,在 failover 检测点加 failover 日志。把:

```python
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=True,
                response_format=req.response_format, params=req.params,
            )
            try:
                async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        retriable = resp.status_code >= 500 or resp.status_code == 429
                        self._on_attempt_error(idx, retriable=retriable)
                        if retriable and first_error_time is None:
                            first_error_time = time.time()
                        continue
                    self._on_success(idx)
```

改成(加两行日志):

```python
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            logger.info(
                "llm attempt provider=%s model=%s (stream)", cfg.provider, cfg.model
            )
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=True,
                response_format=req.response_format, params=req.params,
            )
            try:
                async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        retriable = resp.status_code >= 500 or resp.status_code == 429
                        self._on_attempt_error(idx, retriable=retriable)
                        if retriable and first_error_time is None:
                            first_error_time = time.time()
                            logger.warning(
                                "failover: provider=%s model=%s -> %s; reason=%s",
                                cfg.provider, cfg.model, "next", last_error,
                            )
                        continue
                    self._on_success(idx)
```

在最后的 `yield Error(...)` 之前加全部耗尽日志:

```python
        logger.error("all %d profiles exhausted (stream)", len(self._pks))
        yield Error(message=last_error or "all profiles failed", retriable=False)
```

对 `collect` 方法做同样三处加日志:`llm attempt`(非 stream 版)、`failover`(retriable 分支)、`return GatewayResult(... error=...)` 前加 `logger.error("all %d profiles exhausted", len(self._pks))`。

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/test_logging.py tests/llm/ -q`
Expected: 新测试 PASS + 现有 llm 测试不回归(breaker name 向后兼容)。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/llm/gateway.py kb_platform/llm/circuit_breaker.py kb_platform/llm/breaker_registry.py tests/test_logging.py
git commit -m "feat(logging): LLM gateway failover + circuit-breaker transition logs"
```

---

## Task 7: 查询路径日志

`routes_query.py` / `routes_conversations.py` bind `query_id` + `kb_id` + 起/止日志。`graphrag_engine.stream_search` 首 token 日志。`conversation/service.py` 改写日志。

**Files:**
- Modify: `kb_platform/api/routes_query.py`
- Modify: `kb_platform/api/routes_conversations.py`
- Modify: `kb_platform/query/graphrag_engine.py`(stream_search 首 token)
- Modify: `kb_platform/conversation/service.py`(_rewrite_once 加改写日志)
- Test: `tests/test_logging.py`(追加)

- [ ] **Step 1: 写失败测试(追加到 `tests/test_logging.py`)**

```python
def test_query_route_logs_start_and_done(monkeypatch, capsys):
    """POST /kbs/{id}/query logs query start (method+kb) and done (duration)."""
    monkeypatch.setenv("KB_LOG_DIR", tempfile.mkdtemp())
    setup_logging("server")
    # Reuse the existing query test harness (FakeQueryEngine injected).
    pytest.importorskip("tests.test_query")
    # ... drive a query via TestClient against an app with FakeQueryEngine ...
    err = capsys.readouterr().err
    assert "query start" in err
    assert "query done" in err
```

> 实现者注意:复用 `tests/test_query.py` / `tests/test_query_streaming.py` 里已有的 app + FakeQueryEngine fixture。`rg -n "FakeQueryEngine\|/kbs/.*/query" tests/` 找驱动代码替换 `...`。

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -k query_route -v`
Expected: FAIL。

- [ ] **Step 3: 改 routes_query.py — bind query_id + 起/止日志**

Modify `kb_platform/api/routes_query.py`。顶部加 import:

```python
import time
import logging
from uuid import uuid4
```

`logger = logging.getLogger(__name__)` 加在 `router = APIRouter()` 之前。

把 `gen()` 改成在入口 bind `query_id`/`kb_id` + 打起/止日志,并测首 token 时间。把:

```python
    async def gen():
        # Injected engine (tests) takes priority; otherwise build a real one per-KB.
        # Resolves QueryParams from KB settings (query_defaults) ← per-query params.
        nonlocal data_root
        local_engine = engine
        import json
```

改成:

```python
    async def gen():
        from kb_platform.logging_config import bind_log_context

        nonlocal data_root
        local_engine = engine
        import json

        query_id = uuid4().hex[:12]
        t0 = time.perf_counter()
        delta_count = 0
        first_token_ms: float | None = None
        with bind_log_context(query_id=query_id, kb_id=kb_id):
            logger.info(
                "query start method=%s q=%.80s", payload.method, payload.query or ""
            )
            try:
                yield_early = False  # placeholder; real yields below
                # === existing body (per_query / resolved / build engine) unchanged ===
                per_query = (
                    QueryParams(**payload.params.model_dump()) if payload.params is not None else None
                )
                resolved: QueryParams | None = None
                if local_engine is None:
                    from kb_platform.query.factory import build_query_engine

                    app_state = request.app.state
                    repo = app_state.repo
                    with session_scope(repo.engine) as s:
                        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
                        if kb is None:
                            logger.warning("query kb %s not found", kb_id)
                            yield format_sse("error", {"message": f"kb {kb_id} not found"})
                            return
                        data_root = kb.data_root
                        kb_settings = json.loads(kb.settings_json or "{}")
                        resolved = resolve_query_params(kb_settings, per_query)
                    try:
                        local_engine = build_query_engine(payload.method, kb, repo, app_state)
                    except Exception as exc:  # noqa: BLE001 - graceful, never 500
                        logger.exception("engine build failed")
                        yield format_sse("error", {"message": f"engine build failed: {exc}"})
                        return
                else:
                    resolved = resolve_query_params({}, per_query)

                yield format_sse("meta", {"method": payload.method})
                async for ev in local_engine.stream_search(
                    payload.method, payload.query, data_root, params=resolved
                ):
                    if isinstance(ev, StreamMeta):
                        yield format_sse("meta", {"method": payload.method, "cypher": ev.cypher})
                    elif isinstance(ev, StreamDelta):
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - t0) * 1000
                            logger.info("query first token in %.0fms", first_token_ms)
                        delta_count += 1
                        yield format_sse("delta", {"text": ev.text})
                    else:
                        yield format_sse(
                            "done",
                            {
                                "result": QueryResultOut(
                                    answer=ev.answer,
                                    method=payload.method,
                                    error=ev.error,
                                    elapsed_ms=ev.elapsed_ms,
                                    prompt_tokens=ev.prompt_tokens,
                                    output_tokens=ev.output_tokens,
                                    truncated=getattr(ev, "truncated", False),
                                    sources=[
                                        SourceOut(kind=s.kind, name=s.name, text=s.text)
                                        for s in ev.sources
                                    ]
                                    if ev.sources
                                    else None,
                                ).model_dump(mode="json")
                            },
                        )
                logger.info(
                    "query done in %.0fms; deltas=%s", (time.perf_counter() - t0) * 1000, delta_count
                )
            except Exception:
                logger.exception("query stream failed")
                raise
```

> 注:整段 `gen()` 函数体被替换为带计时/日志的版本,SSE 事件结构不变。现有 query 流式测试应仍通过。

- [ ] **Step 4: 改 routes_conversations.py — bind query_id + 起/止日志**

Modify `kb_platform/api/routes_conversations.py`。顶部加 import:

```python
import logging
import time
from uuid import uuid4

logger = logging.getLogger(__name__)
```

把 `send_message` 的 `gen()` 入口包一层:

```python
    async def gen():
        from kb_platform.logging_config import bind_log_context

        query_id = uuid4().hex[:12]
        t0 = time.perf_counter()
        conv = repo.get_conversation(conv_id)
        kb_id = conv.kb_id if conv else None
        with bind_log_context(query_id=query_id, kb_id=kb_id):
            logger.info("conversation message start conv=%s", conv_id)
            # === existing gen body (build engine / service.send_streaming / yield) ===
            # ... (保持现有代码不变,只把它整体缩进到 with 块内) ...
            try:
                # [现有 production/injected engine 分支 + service.send_streaming 循环 原样保留]
                ...
            finally:
                logger.info(
                    "conversation message done in %.0fms", (time.perf_counter() - t0) * 1000
                )
```

> 实现者注意:现有 `gen()` body 里两处 `return` 要替换成 `break` + `finally` 计时,或把整段用 try/finally 包起来确保 done 日志一定打(包括 error 早退)。最简方案:在最外层加 try/finally,return 路径里先 `yield format_sse("error", ...)` 再走 finally。

- [ ] **Step 5: 改 conversation/service.py — 改写日志**

Modify `kb_platform/conversation/service.py` 的 `_rewrite_once`,在成功改写后加日志:

```python
    async def _rewrite_once(self, content, history):
        if not history or self._rewriter is None:
            return False, False, 0, 0, content
        import time

        t0 = time.perf_counter()
        try:
            rr = await self._rewriter.rewrite(content, history)
            logger.info(
                "rewrite done in %.0fms -> %.60s",
                (time.perf_counter() - t0) * 1000, rr.standalone,
            )
            return True, False, rr.prompt_tokens, rr.output_tokens, rr.standalone
        except Exception:  # noqa: BLE001 - fall back to raw message, never block
            logger.exception("query rewrite failed; falling back to raw message")
            return False, True, 0, 0, content
```

(原 `logger.exception("query rewrite failed; ...")` 保留;只加成功分支的 info 日志。)

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/test_logging.py tests/test_query*.py tests/test_conversation*.py -q`
Expected: 新测试 PASS,现有 query/conversation SSE 测试不回归。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/api/routes_query.py kb_platform/api/routes_conversations.py kb_platform/conversation/service.py tests/test_logging.py
git commit -m "feat(logging): query path start/done + first-token + rewrite logs"
```

---

## Task 8: API 变更审计日志

在 KB / 文档 / job / profile / preset / export 的写操作 handler 各加一行 info 审计日志(`request_id` 由 middleware 已带)。

**Files:**
- Modify: `kb_platform/api/routes_kbs.py`(create_kb / add_document / delete_document / update_kb)
- Modify: `kb_platform/api/routes_jobs.py`(trigger_job / retry_unit / retry_step)
- Modify: `kb_platform/api/routes_profiles.py`(create_profile / update_profile / delete_profile)
- Modify: `kb_platform/api/routes_presets.py`(create_preset / update_preset / delete_preset)
- Modify: `kb_platform/api/routes_export.py`(export 端点)
- Test: `tests/test_logging.py`(一个 batch caplog 测试)

- [ ] **Step 1: 写 batch 失败测试(追加到 `tests/test_logging.py`)**

```python
def test_api_audit_logs(monkeypatch, capsys):
    """Mutating endpoints each emit one INFO audit line carrying request_id."""
    monkeypatch.setenv("KB_LOG_DIR", tempfile.mkdtemp())
    setup_logging("server")
    # Reuse an existing full-app test fixture (created repo + at least one KB+profile).
    pytest.importorskip("tests.test_api")
    # ... drive: create profile, create KB, add doc, trigger job, retry, create preset ...
    err = capsys.readouterr().err
    for needle in ("profile created", "KB created", "doc uploaded", "job created", "preset created"):
        assert needle in err, f"missing audit log: {needle}"
    # request_id is present on every line:
    assert "request_id=" in err
```

> 实现者注意:`...` 部分复用 `tests/test_api.py` / `tests/test_profiles.py` 的 client + seed_profile helper。`rg -n "seed_profile\|create_kb\|/provider-profiles" tests/` 找现成代码。

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py::test_api_audit_logs -v`
Expected: FAIL — 审计日志还没加。

- [ ] **Step 3: routes_kbs.py — 加 logger + 4 处审计**

Modify `kb_platform/api/routes_kbs.py` 顶部加 `import logging` + `logger = logging.getLogger(__name__)`。

- `create_kb` 在 `return KbOut(...)` 前加:
  ```python
        logger.info(
            "KB created id=%s name=%r method=%s llm_profile=%s",
            kb.id, payload.name, payload.method, payload.llm_profile_id,
        )
  ```
- `update_kb` 在 `if kb is None: raise HTTPException(404)` 之后、return 前加:
  ```python
        logger.info("KB updated id=%s name=%r", kb_id, payload.name)
  ```
- `add_document` 在 `return DocumentOut(...)` 前加:
  ```python
        logger.info(
            "doc uploaded kb=%s id=%s title=%r bytes=%s",
            kb_id, doc.id, doc.title, doc.bytes,
        )
  ```
- `delete_document` 在 `if not repo.delete_document(...)` 之后(job 决定后)加:
  ```python
        logger.info("doc deleted kb=%s doc=%s; shrink_job=%s", kb_id, doc_id, job.id if job else None)
  ```

- [ ] **Step 4: routes_jobs.py — 加 logger + 3 处审计**

Modify `kb_platform/api/routes_jobs.py` 顶部加 `import logging` + `logger = logging.getLogger(__name__)`。

- `trigger_job` 在 `return JobCreated(...)` 前加:
  ```python
        logger.info("job created id=%s kb=%s type=%s", job.id, kb_id, payload.type)
  ```
- `retry_unit` 在 return 前加:`logger.info("unit retried id=%s", unit_id)`
- `retry_step` 在 return 前加:
  ```python
        logger.info("step retried id=%s; reset=%s", step_id, n)
  ```

- [ ] **Step 5: routes_profiles.py — 3 处审计(logger 已有)**

`routes_profiles.py` 已有 `logger`。

- `create_profile` 在 `return _out(...)` 前加:
  ```python
        logger.info(
            "profile created id=%s name=%r provider=%s model=%s",
            p.id, p.name, p.provider, p.model,
        )
  ```
- `update_profile` 在 return 前加:`logger.info("profile updated id=%s", pid)`
- `delete_profile` 在 `if not repo.delete_profile(pid)` 之后(成功删除后)加:
  ```python
        logger.info("profile deleted id=%s", pid)
  ```

- [ ] **Step 6: routes_presets.py — 加 logger + 3 处审计**

Modify `kb_platform/api/routes_presets.py` 顶部加 `import logging` + `logger = logging.getLogger(__name__)`。

- `create_preset` return 前加:`logger.info("preset created id=%s name=%r", p.id, payload.name)`
- `update_preset` return 前加:`logger.info("preset updated id=%s", pid)`
- `delete_preset` 末尾加:`logger.info("preset deleted id=%s", pid)`

- [ ] **Step 7: routes_export.py — export 审计**

Modify `kb_platform/api/routes_export.py`:顶部加 `import logging` + `logger = logging.getLogger(__name__)`。在 export handler(返回 zip / GraphML 的那个端点)开始处加:

```python
        logger.info("export requested kb=%s format=%s", kb_id, "graphml" if path.endswith(".graphml") else "zip")
```

> 实现者注意:看 `routes_export.py` 里实际的 export 端点签名(`GET /kbs/{kb_id}/export` 之类),把 `format` 推断改对。

- [ ] **Step 8: 运行测试**

Run: `uv run pytest tests/test_logging.py::test_api_audit_logs -v && uv run pytest tests/test_api*.py tests/test_profiles*.py -q`
Expected: 新测试 PASS,现有 API 测试不回归。

- [ ] **Step 9: 提交**

```bash
git add kb_platform/api/routes_kbs.py kb_platform/api/routes_jobs.py kb_platform/api/routes_profiles.py kb_platform/api/routes_presets.py kb_platform/api/routes_export.py tests/test_logging.py
git commit -m "feat(logging): API mutation audit logs (KB/doc/job/profile/preset/export)"
```

---

## Task 9: realtime + 输入侧里程碑日志

`realtime.py` 订阅者 +/− 日志;`orchestrator._chunk_documents` 每文档分块里程碑;`doc_reader.read_document` 每文档解析里程碑。

**Files:**
- Modify: `kb_platform/api/realtime.py`
- Modify: `kb_platform/engine/orchestrator.py`(`_chunk_documents`)
- Modify: `kb_platform/input/doc_reader.py`
- Test: `tests/test_logging.py`(追加)

- [ ] **Step 1: 写失败测试(追加到 `tests/test_logging.py`)**

```python
def test_doc_reader_logs_parsed(monkeypatch, capsys):
    monkeypatch.setenv("KB_LOG_DIR", tempfile.mkdtemp())
    setup_logging("worker")
    from kb_platform.input.doc_reader import read_document

    read_document(b"hello world text", "note.md")
    err = capsys.readouterr().err
    assert "parsed" in err and "note.md" in err


def test_chunk_documents_logs_per_doc(monkeypatch, capsys):
    """orchestrator._chunk_documents emits one INFO line per document."""
    monkeypatch.setenv("KB_LOG_DIR", tempfile.mkdtemp())
    setup_logging("worker")
    pytest.importorskip("tests.test_engine")
    # ... drive a full job with FakeGraphAdapter over 2 docs; assert 2 'chunked doc' lines ...
    err = capsys.readouterr().err
    assert err.count("chunked doc") >= 2
```

> 实现者注意:复用 `tests/test_engine*.py` 的 FakeGraphAdapter + repo + 2 docs 驱动代码。`rg -n "_chunk_documents\|chunk_document\|FakeGraphAdapter" tests/`。

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run pytest tests/test_logging.py -k doc_reader or chunk_documents -v`
Expected: FAIL。

- [ ] **Step 3: 改 realtime.py — 订阅者日志**

Modify `kb_platform/api/realtime.py` 的 `subscribe` / `unsubscribe`:

```python
    def subscribe(self, job_id: int, ws) -> dict:
        bc = self.broadcasters.get(job_id)
        if bc is None:
            bc = JobBroadcaster(job_id=job_id, repo=self.repo)
            self.broadcasters[job_id] = bc
        bc.subscribers.add(ws)
        logger.info("realtime subscribe job=%s; subscribers=%d", job_id, len(bc.subscribers))
        return bc.snapshot()

    def unsubscribe(self, job_id: int, ws) -> None:
        bc = self.broadcasters.get(job_id)
        if bc is not None:
            bc.subscribers.discard(ws)
            logger.info("realtime unsubscribe job=%s; subscribers=%d", job_id, len(bc.subscribers))
            if not bc.subscribers:
                del self.broadcasters[job_id]
```

- [ ] **Step 4: 改 orchestrator._chunk_documents — 每文档里程碑**

Modify `kb_platform/engine/orchestrator.py` 的 `_chunk_documents` —— 在内层 `for ordinal, piece in enumerate(...)` 循环之后(doc 分块完成)加一行。把:

```python
        for doc in self.repo.get_documents(job.kb_id):
            for ordinal, piece in enumerate(self.adapter.chunk_document(doc.id, doc.text or "")):
                chunks.append(
                    Chunk(
                        chunk_id=piece.chunk_id,
                        kb_id=job.kb_id,
                        document_id=doc.id,
                        ordinal=ordinal,
                        text=piece.text,
                    )
                )
```

改成(在 doc 循环体末尾加日志):

```python
        for doc in self.repo.get_documents(job.kb_id):
            doc_chunks = 0
            for ordinal, piece in enumerate(self.adapter.chunk_document(doc.id, doc.text or "")):
                chunks.append(
                    Chunk(
                        chunk_id=piece.chunk_id,
                        kb_id=job.kb_id,
                        document_id=doc.id,
                        ordinal=ordinal,
                        text=piece.text,
                    )
                )
                doc_chunks += 1
            logger.info(
                "chunked doc=%s into %d chunks (kb=%s)", doc.id, doc_chunks, job.kb_id
            )
```

- [ ] **Step 5: 改 doc_reader.read_document — 解析里程碑**

Modify `kb_platform/input/doc_reader.py`:

```python
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def read_document(data: bytes, filename: str) -> str:
    """Extract plain text from uploaded bytes via markitdown.

    Never raises: if markitdown rejects the content (or is unavailable),
    fall back to a utf-8 decode (errors replaced) so an unusual file still
    produces storable text.
    """
    text = ""
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(io.BytesIO(data))
        text = getattr(result, "text_content", None) or ""
    except Exception:  # noqa: BLE001
        pass
    if not text:
        text = data.decode("utf-8", errors="replace")
    logger.info("parsed %d chars from %s", len(text), filename)
    return text
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/test_logging.py tests/test_engine*.py tests/test_realtime*.py -q`
Expected: 新测试 PASS,现有测试不回归。

- [ ] **Step 7: 提交**

```bash
git add kb_platform/api/realtime.py kb_platform/engine/orchestrator.py kb_platform/input/doc_reader.py tests/test_logging.py
git commit -m "feat(logging): realtime subscriber + chunking/doc-parse milestone logs"
```

---

## Task 10: .gitignore + 全量验证 + 收尾

加 `logs/` 到 .gitignore;跑全量后端测试 + lint;ruff 修形。

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 加 logs/ 到 .gitignore**

Modify `.gitignore` —— 在末尾加:

```
# Runtime logs (per-process rotated + gzipped)
logs/
```

- [ ] **Step 2: 跑全量后端测试**

Run: `uv run pytest -q`
Expected: 全绿。任何 setup_logging 在测试里被触发都要被 `_isolate_logging` fixture 兜住;若有遗漏的泄漏,在对应测试里加 `monkeypatch.setenv("KB_LOG_DIR", tmp_path)` 或显式调 setup_logging。

- [ ] **Step 3: ruff lint + format**

Run: `uv run ruff check . && uv run ruff format .`
Expected: 无错。修掉任何 import 顺序/行长问题(lazy import 在函数内、`logger = getLogger(__name__)` 位置)。

- [ ] **Step 4: 手动冒烟(可选但推荐)**

启动 server + worker,跑一个小索引 + 一次查询,检查:
- `logs/server.log` / `logs/worker.log` 存在且是明文(可 tail)。
- 关联 ID 串联:`grep "job=1"` 能看到一个 job 的 claim → step → unit 全链路。
- 等一个轮转周期(或临时把 worker `KB_LOG_ROTATE_WHEN=S KB_LOG_ROTATE_INTERVAL=2`)后,确认旧文件被压缩成 `.gz` 且能 `gunzip -c` 解出明文。

```bash
KB_LOG_DIR=./logs KB_LOG_LEVEL=INFO uv run python -m kb_platform.server &
KB_LOG_DIR=./logs uv run python -m kb_platform.worker &
# 触发一个 KB + 文档 + job,发一个查询,然后:
ls -la logs/
grep "job=1" logs/worker.log
```

- [ ] **Step 5: 提交**

```bash
git add .gitignore
git commit -m "chore(logging): gitignore logs/ + full-suite green"
```

- [ ] **Step 6: PR**

```bash
git push -u origin feat/logging-system
gh pr create --title "feat: unified logging system (config + correlation IDs + instrumentation)" --body "..."
```

PR body 引用 spec(`docs/superpowers/specs/2026-07-04-logging-system-design.md`)与本计划,列出 10 个 task 的提交。

---

## Self-Review Notes

**Spec coverage:**
- §4 架构 / 模块 → Task 1 ✓
- §5 配置 & 轮转(env / per-process 默认 / 第三方压制) → Task 1 ✓
- §5.4 跨平台 gzip → Task 2 ✓
- §5.5 .gitignore → Task 10 ✓
- §6 关联 ID(contextvar / 绑定点 / Filter / 格式 / X-Request-ID) → Task 1(filter/formatter/bind)+ Task 4(request_id)+ Task 5(job/step/unit)+ Task 7(query/kb)✓
- §7.A 任务生命周期 → Task 5 ✓
- §7.B LLM gateway/failover → Task 6 ✓
- §7.C 查询路径 → Task 7 ✓
- §7.D API 审计 → Task 8 ✓
- §7.E realtime + 输入侧 → Task 9 ✓
- §8 测试 → 每个 Task 的 caplog/tmp_path 测试 + Task 1 的 `_isolate_logging` fixture ✓
- §9 坑(MCP stdout 守卫 / uvicorn log_config=None / 降级 / 流式计时注释) → Task 1/3/4 ✓
- graphrag_engine 首 token:spec §7.C 提到;实现放 routes_query 的 generator(更靠近用户感知延迟),Task 7 Step 3 ✓

**类型一致性:** `bind_log_context(**fields)`、`setup_logging(process)`、`compress_rotated(source, dest)`、`CircuitBreaker(name=...)` 全计划统一。

**已知实现者决策点**(已在对应 Step 里标注,非占位):
- Task 4/5/7/8/9 的测试"驱动代码"复用 `tests/` 现成 fixture(已给 `rg` 检索命令),不重新造。
- Task 7 Step 4 的 conversation `gen()` finally 包裹:实现者保证 error 早退也打 done 日志。
