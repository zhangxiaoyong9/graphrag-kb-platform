"""Centralized logging: setup + correlation-ID contextvar + cross-platform gzip rotation.

Call ``setup_logging("server"|"worker"|"mcp")`` at each entrypoint. Bind correlation
fields via ``bind_log_context(job_id=.., step_id=.., ...)``; a Filter stamps them onto
every LogRecord and the formatter renders them as ``[job=.. step=..]``.
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import subprocess
import sys
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Literal

_LOGGER = logging.getLogger(__name__)

_CTX: ContextVar[dict[str, str]] = ContextVar("kb_log_ctx")

_CONTEXT_FIELDS = (
    "request_id", "query_id", "kb_id", "job_id", "step_id", "unit_id",
    "llm_call_id", "operation",
)

# Short display names for the context block (job_id -> job, step_id -> step, ...).
_DISPLAY_NAMES: dict[str, str] = {
    "request_id": "request",
    "query_id": "query",
    "kb_id": "kb",
    "job_id": "job",
    "step_id": "step",
    "unit_id": "unit",
    "llm_call_id": "llm_call",
    "operation": "operation",
}

_PROCESS_NAME = "unknown"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[-_]?key|x-api-key)\s*[:=]\s*[\"']?)[^\s,;\"']+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact_text(value: object, limit: int | None = None) -> str:
    """Return a bounded, single-line string with common credential forms removed."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
    return text[:limit] if limit is not None else text

# Third-party libs whose INFO output is noise -> quieted to WARNING by default.
_NOISY_LIBS = ("httpx", "httpcore", "urllib3", "sqlalchemy")

# Per-process rotation defaults (overridden uniformly by KB_LOG_ROTATE_* env).
_PROCESS_DEFAULTS: dict[str, dict[str, object]] = {
    "server": {"when": "H", "interval": 1, "backup_count": 24},
    "worker": {"when": "M", "interval": 30, "backup_count": 48},
    "mcp": {"when": "D", "interval": 1, "backup_count": 7},
}

_CONSOLE_FMT = (
    "%(asctime)s.%(msecs)03d %(levelname)-5s service=%(service)s "
    "%(name)s%(context)s — %(message)s"
)
_FILE_FMT = (
    "%(asctime)s.%(msecs)03d %(levelname)-5s service=%(service)s "
    "pid=%(process)d %(name)s%(context)s — %(message)s"
)
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class ContextVarFilter(logging.Filter):
    """Stamp current contextvar fields onto each record so the formatter can render them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = _PROCESS_NAME
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
        return redact_text(super().format(record))


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


def _parse_log_levels(spec: str) -> dict[str, str]:
    """Parse ``a=DEBUG,b=WARNING`` into a dict. Empty/invalid entries skipped."""
    out: dict[str, str] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, lv = pair.split("=", 1)
        name, lv = name.strip(), lv.strip().upper()
        if name and lv in logging._nameToLevel:  # noqa: SLF001
            out[name] = lv
    return out


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("must be positive")
        return value
    except ValueError:
        sys.stderr.write(
            f"[kb_platform.logging_config] invalid {name}={raw!r}; using {default}\n"
        )
        return default


def _build_file_handler(process: str, log_dir: Path) -> logging.Handler | None:
    """Return a TimedRotatingFileHandler, or None if the dir isn't writable (degrade)."""
    try:
        ancestor = log_dir
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if ancestor.exists() and not (
            ancestor.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise PermissionError(f"parent directory has no writable permission bits: {ancestor}")
        log_dir.mkdir(parents=True, exist_ok=True)
        if not (log_dir.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)):
            raise PermissionError(f"directory has no writable permission bits: {log_dir}")
    except OSError as exc:
        sys.stderr.write(
            f"[kb_platform.logging_config] KB_LOG_DIR {log_dir} unusable: {exc}; "
            f"file logging disabled\n"
        )
        return None
    defaults = _PROCESS_DEFAULTS[process]
    when = os.environ.get("KB_LOG_ROTATE_WHEN", str(defaults["when"]))
    interval = _positive_int_env("KB_LOG_ROTATE_INTERVAL", int(defaults["interval"]))
    backup_count = _positive_int_env(
        "KB_LOG_ROTATE_BACKUP_COUNT", int(defaults["backup_count"])
    )
    fh = GzipTimedRotatingFileHandler(
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
    global _PROCESS_NAME
    _PROCESS_NAME = process
    level = os.environ.get("KB_LOG_LEVEL", "INFO").upper()
    if level not in logging._nameToLevel:  # noqa: SLF001 - stdlib's canonical level map
        sys.stderr.write(
            f"[kb_platform.logging_config] invalid KB_LOG_LEVEL={level!r}; using INFO\n"
        )
        level = "INFO"
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
    "GzipTimedRotatingFileHandler",
    "bind_log_context",
    "compress_rotated",
    "get_log_context",
    "redact_text",
    "setup_logging",
]
