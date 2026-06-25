"""Regression: server must run uvicorn on the asyncio loop, not uvloop.

graphrag_llm calls nest_asyncio.apply() at import, which cannot patch a
uvloop. uvicorn auto-selects uvloop when installed, so we force loop="asyncio".
"""
import sys


def test_server_forces_asyncio_loop(monkeypatch):
    import uvicorn

    captured: dict = {}

    def fake_run(app, **kwargs):  # noqa: ANN001
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["server", ":memory:", "/tmp", "127.0.0.1", "8000"])

    from kb_platform import server

    server.main()
    assert captured.get("loop") == "asyncio"
