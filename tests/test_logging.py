"""logging_config: setup, bind, filter, formatter, env parsing, degrade."""
import logging
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
