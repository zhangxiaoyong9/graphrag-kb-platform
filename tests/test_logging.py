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


# --- Task 2: cross-platform gzip rotation ---------------------------------

import gzip  # noqa: E402
from unittest import mock  # noqa: E402

from kb_platform.logging_config import (  # noqa: E402
    GzipTimedRotatingFileHandler,
    compress_rotated,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_compress_rotated_python_path(tmp_path):
    """Force the Python-gzip fallback by pretending gzip subprocess fails."""
    src = _write(tmp_path / "worker.log", "x" * 5000)
    dest = tmp_path / "worker.log.2026-07-04_14-30.gz"
    # Implementation dispatches on `sys.platform` (NOT platform.system); patch the
    # real target so this test takes the Python-gzip branch on every OS.
    with mock.patch("kb_platform.logging_config.sys.platform", "win32"):
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
    """Rotating produces a .gz file; pruning keeps only backupCount rotated files."""
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


# --- Task 3: entrypoint guards --------------------------------------------

import sys  # noqa: E402


def test_mcp_never_logs_to_stdout(tmp_path, monkeypatch):
    """stdio MCP: stdout is JSON-RPC — setup_logging('mcp') must not attach a stdout handler."""
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("mcp")
    for h in logging.getLogger().handlers:
        stream = getattr(h, "stream", None)
        assert stream is not sys.stdout, "MCP process must never log to stdout"


# --- Task 4: FastAPI request_id middleware --------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from kb_platform.api.app import create_app  # noqa: E402
from kb_platform.db.engine import create_engine as _create_engine  # noqa: E402
from kb_platform.db.models import Base  # noqa: E402
from kb_platform.db.repository import Repository  # noqa: E402


def _middleware_app(tmp_path):
    """Build an app + client like the existing tests/test_api_query.py::client fixture:
    in-process SQLite with tables created via Base.metadata.create_all.
    """
    engine = _create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return TestClient(create_app(Repository(engine), data_root=str(tmp_path)))


def test_middleware_binds_request_id_and_sets_header(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_CONSOLE", "true")
    setup_logging("server")
    with _middleware_app(tmp_path) as client:
        r = client.get("/kbs")
    headers_lower = {k.lower() for k in r.headers}
    assert "x-request-id" in headers_lower
    assert len(r.headers["x-request-id"]) == 12


def test_middleware_logs_request_start_and_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_CONSOLE", "true")
    setup_logging("server")
    with _middleware_app(tmp_path) as client:
        client.get("/kbs")
    err = capsys.readouterr().err
    assert "request start" in err
    assert "request done" in err
    assert "GET /kbs" in err


# --- Task 5: job/step/unit lifecycle logs ----------------------------------

from kb_platform.graph.adapter import FakeGraphAdapter  # noqa: E402
from kb_platform.worker import run_worker_once  # noqa: E402
from conftest import seed_profile  # noqa: E402
from kb_platform.db.enums import JobStatus  # noqa: E402


def _seed_pending_job(tmp_path) -> tuple:
    """Build an in-memory-style SQLite repo with one KB + doc + pending full job.

    Mirrors tests/test_e2e_backend_service.py: in-process SQLite, Base metadata
    create_all, a provider profile (Fernet key set by autouse conftest fixture),
    one KB, one oversized doc (so chunking yields chunks), and one pending job.
    """
    engine = _create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    pid = seed_profile(client)
    r = client.post(
        "/kbs",
        json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": pid},
    )
    assert r.status_code == 201
    r = client.post(
        "/kbs/1/documents",
        json={"title": "d", "text": "ACME Org Bob Person Foo Bar Baz " * 200},
    )
    assert r.status_code == 201
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "pending"
    return repo, job_id


@pytest.mark.asyncio
async def test_worker_logs_job_claim_and_done(tmp_path, monkeypatch, caplog):
    """run_worker_once emits lifecycle logs at worker / orchestrator / unit_worker.

    One end-to-end fake job covers all three layers' correlation IDs:
    worker binds job_id (+kb_id); orchestrator's _run_step binds step_id;
    unit_worker's _process binds unit_id.
    """
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path))
    setup_logging("worker")
    # Prior tests in this module (test_per_logger_override) may have lowered
    # the per-logger level on kb_platform.engine.unit_worker / orchestrator and
    # that state is process-global. Reset to NOTSET so they inherit the root
    # INFO level set by setup_logging, letting caplog see the lifecycle logs.
    for name in (
        "kb_platform.engine.unit_worker",
        "kb_platform.engine.orchestrator",
        "kb_platform.worker",
    ):
        logging.getLogger(name).setLevel(logging.NOTSET)
    repo, job_id = _seed_pending_job(tmp_path)

    with caplog.at_level(logging.INFO):
        await run_worker_once(
            repo=repo,
            adapter_factory=lambda kb: FakeGraphAdapter(),
            heartbeat_interval=0.01,
        )

    assert repo.get_job(job_id).status == JobStatus.SUCCEEDED

    worker_msgs = [r.getMessage() for r in caplog.records if r.name == "kb_platform.worker"]
    orch_msgs = [
        r.getMessage() for r in caplog.records if r.name == "kb_platform.engine.orchestrator"
    ]
    unit_msgs = [
        r.getMessage() for r in caplog.records if r.name == "kb_platform.engine.unit_worker"
    ]

    # worker: claim + done-with-duration, both carrying job_id in context.
    assert any("claimed" in m for m in worker_msgs), worker_msgs
    assert any("done in" in m for m in worker_msgs), worker_msgs
    claimed_records = [r for r in caplog.records if r.name == "kb_platform.worker" and "claimed" in r.getMessage()]
    assert all(getattr(r, "job_id", None) == str(job_id) for r in claimed_records)

    # orchestrator: at least one step start + one step done-with-duration.
    assert any("start" in m for m in orch_msgs), orch_msgs
    assert any("done in" in m for m in orch_msgs), orch_msgs

    # unit_worker: at least one unit done-with-duration.
    assert any("done in" in m for m in unit_msgs), unit_msgs
