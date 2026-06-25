"""Job / step / unit status + retry endpoints."""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.post("/kbs/{kb_id}/jobs", status_code=202)
def trigger_job(kb_id: int, payload: dict, request: Request):
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    repo = request.app.state.repo
    # Defense in depth: reject orphan jobs at the API boundary so the worker
    # never claims a job whose kb_id points at a missing KB. SQLite FKs may be
    # off depending on driver mode, so we check explicitly.
    with session_scope(repo.engine) as s:
        if s.get(KnowledgeBase, kb_id) is None:
            raise HTTPException(404, f"kb {kb_id} not found")
    job = repo.create_job_pending(kb_id=kb_id, method=payload.get("method", "standard"))
    return {"id": job.id, "status": job.status}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, request: Request):
    repo = request.app.state.repo
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return {
        "id": job.id,
        "status": job.status,
        "steps": [
            {"id": s.id, "name": s.name, "status": s.status}
            for s in repo.get_steps(job_id)
        ],
    }


@router.get("/jobs/{job_id}/steps")
def get_steps(job_id: int, request: Request):
    repo = request.app.state.repo
    return [
        {
            "id": s.id,
            "name": s.name,
            "ordinal": s.ordinal,
            "kind": s.kind,
            "status": s.status,
        }
        for s in repo.get_steps(job_id)
    ]


@router.get("/steps/{step_id}/units")
def get_units(step_id: int, request: Request, status: str | None = None):
    repo = request.app.state.repo
    units = repo.list_units(step_id)
    if status:
        units = [u for u in units if u.status == status]
    return [
        {
            "id": u.id,
            "subject_id": u.subject_id,
            "status": u.status,
            "error": u.error,
            "llm_raw_output": u.llm_raw_output,
            "needs_reconsolidation": u.needs_reconsolidation,
        }
        for u in units
    ]


@router.post("/units/{unit_id}/retry")
def retry_unit(unit_id: int, request: Request):
    repo = request.app.state.repo
    repo.reset_unit_to_pending(unit_id)
    return {"ok": True}


@router.post("/steps/{step_id}/retry")
def retry_step(step_id: int, request: Request):
    repo = request.app.state.repo
    n = repo.reset_failed_units_to_pending(step_id)
    return {"reset": n}
