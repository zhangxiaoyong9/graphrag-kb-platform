"""Job / step / unit status + retry endpoints."""

from fastapi import APIRouter, HTTPException, Request

from kb_platform.api.models import (
    JobCreate,
    JobCreated,
    JobOut,
    StepOut,
    UnitOut,
    UnitProgress,
)

router = APIRouter()


def _step_out(repo, s) -> StepOut:
    progress = None
    if s.kind == "unit_fanout":
        progress = UnitProgress(**repo.unit_counts_by_status(s.id))
    return StepOut(
        id=s.id,
        name=s.name,
        ordinal=s.ordinal,
        kind=s.kind,
        status=s.status,
        progress=progress,
    )


@router.post("/kbs/{kb_id}/jobs", response_model=JobCreated, status_code=202)
def trigger_job(kb_id: int, payload: JobCreate, request: Request) -> JobCreated:
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    repo = request.app.state.repo
    # Defense in depth: reject orphan jobs at the API boundary so the worker
    # never claims a job whose kb_id points at a missing KB. SQLite FKs may be
    # off depending on driver mode, so we check explicitly.
    with session_scope(repo.engine) as s:
        if s.get(KnowledgeBase, kb_id) is None:
            raise HTTPException(404, f"kb {kb_id} not found")
    job = repo.create_job_pending(kb_id=kb_id, method=payload.method, type=payload.type)
    return JobCreated(id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, request: Request) -> JobOut:
    repo = request.app.state.repo
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(404)
    steps = [_step_out(repo, s) for s in repo.get_steps(job_id)]
    return JobOut(id=job.id, status=job.status, steps=steps)


@router.get("/jobs/{job_id}/steps", response_model=list[StepOut])
def get_steps(job_id: int, request: Request) -> list[StepOut]:
    repo = request.app.state.repo
    return [_step_out(repo, s) for s in repo.get_steps(job_id)]


@router.get("/steps/{step_id}/units", response_model=list[UnitOut])
def get_units(step_id: int, request: Request, status: str | None = None) -> list[UnitOut]:
    repo = request.app.state.repo
    units = repo.list_units(step_id)
    if status:
        units = [u for u in units if u.status == status]
    return [
        UnitOut(
            id=u.id,
            subject_id=u.subject_id,
            status=u.status,
            error=u.error,
            llm_raw_output=u.llm_raw_output,
            needs_reconsolidation=u.needs_reconsolidation,
        )
        for u in units
    ]


@router.post("/units/{unit_id}/retry")
def retry_unit(unit_id: int, request: Request):
    repo = request.app.state.repo
    repo.reset_unit_to_pending(unit_id)
    repo.reactivate_job_for_unit(unit_id)  # re-queue so the worker re-claims it
    return {"ok": True}


@router.post("/steps/{step_id}/retry")
def retry_step(step_id: int, request: Request):
    repo = request.app.state.repo
    n = repo.reset_failed_units_to_pending(step_id)
    repo.reactivate_job_for_step(step_id)  # re-queue so the worker re-claims it
    return {"reset": n}
