#!/usr/bin/env python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""E2E fake server: temp DB + FakeGraphAdapter worker + FakeQueryEngine.

No LLM, no provider key. Seeds one baseline KB ("E2E 基线") with a completed
full job, serves the built SPA + REST API on 127.0.0.1:18000, and runs a
background FakeGraphAdapter worker so any later triggered job completes too.
"""
import os
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.query.engine import FakeQueryEngine
from kb_platform.worker import run_worker

HOST = "127.0.0.1"
PORT = 18000
BASELINE_NAME = "E2E 基线"
# Multi-entity text so FakeGraphAdapter extracts several entities + relationships.
BASELINE_DOC = "ACME Org Bob Person ACME Org Alice Person Foo Bar Baz " * 200


def _wait_job(repo: Repository, job_id: int, timeout: float = 90.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = repo.get_job(job_id)
        if job and job.status in ("succeeded", "failed"):
            return job.status
        time.sleep(0.5)
    raise RuntimeError(f"baseline job {job_id} did not finish within {timeout}s")


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="kb-e2e-")
    db_path = os.path.join(tmp, "kb.db")
    data_root = os.path.join(tmp, "data")
    Path(data_root).mkdir(parents=True, exist_ok=True)
    print(f"[e2e] tmp={tmp}", flush=True)

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    repo = Repository(engine)

    # Fixed master key for the fake server so encrypted profile keys work
    # without writing a key file into the repo working directory.
    os.environ.setdefault("KB_SECRET_KEY", Fernet.generate_key().decode())

    # Seed an LLM provider profile. The worker uses FakeGraphAdapter (no real
    # key needed), but the API now requires a profile to create a KB and the
    # create-KB spec picks one from the LLM 配置 dropdown.
    llm_profile = repo.create_profile(
        name="E2E LLM",
        kind="llm",
        provider="openai",
        model="gpt-4o-mini",
        api_keys=["fake-key"],
        structured_output=True,
    )

    # Seed the baseline KB + document + a pending full job.
    with session_scope(engine) as s:
        kb = KnowledgeBase(
            name=BASELINE_NAME,
            method="standard",
            settings_json="{}",
            data_root=data_root,
            llm_profile_id=llm_profile.id,
        )
        s.add(kb)
        s.flush()
        kb_id = kb.id
    repo.add_document(kb_id=kb_id, title="baseline.md", text=BASELINE_DOC)
    baseline_job = repo.create_job_pending(kb_id=kb_id, method="standard", type="full")

    # Background FakeGraphAdapter worker (no signal handlers in a thread).
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=run_worker,
        kwargs=dict(
            repo=repo,
            adapter_factory=lambda kb: FakeGraphAdapter(),
            stop_event=stop_event,
            install_signal_handlers=False,
        ),
        daemon=True,
    )
    worker_thread.start()

    status = _wait_job(repo, baseline_job.id)
    if status != "succeeded":
        raise RuntimeError(f"baseline job ended {status}; fake server not usable")
    print(f"[e2e] baseline KB id={kb_id} job={baseline_job.id} {status}", flush=True)

    app = create_app(repo, data_root=data_root, query_engine=FakeQueryEngine())
    uvicorn.run(app, host=HOST, port=PORT, loop="asyncio")


if __name__ == "__main__":
    main()
