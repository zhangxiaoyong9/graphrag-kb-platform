# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Health endpoint: process liveness + DB reachability + worker freshness."""

from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
def health(request: Request, stale_seconds: float = 60.0) -> dict:
    repo = request.app.state.repo
    db = "ok"
    try:
        with repo.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db = "down"
    worker = repo.worker_status(stale_seconds)
    status = "ok" if db == "ok" and not worker["stale"] else "degraded"
    return {"status": status, "db": db, "worker": worker}
