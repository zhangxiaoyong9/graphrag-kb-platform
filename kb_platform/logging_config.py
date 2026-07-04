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

# Short display names for the context block (job_id -> job, step_id -> step, ...).
_DISPLAY_NAMES: dict[str, str] = {
    "request_id": "request",
    "query_id": "query",
    "kb_id": "kb",
    "job_id": "job",
    "step_id": "step",
    "unit_id": "unit",
}

# Third-party libs whose INFO output is noise -> quieted to WARNING by default.
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
        parts = [
            f"{_DISPLAY_NAMES.get(k, k)}={getattr(record, k)}"
            for k in _CONTEXT_FIELDS
            if hasattr(record, k)
        ]
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
