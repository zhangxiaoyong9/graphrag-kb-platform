"""Job / step / unit status + retry endpoints."""

from fastapi import APIRouter, HTTPException, Request

# Importing strategies registers all built-in strategies into STRATEGIES.
import kb_platform.engine.strategies  # noqa: F401
from kb_platform.db.models import Chunk
from kb_platform.engine.strategy import STRATEGIES
from kb_platform.graph.adapter import FakeGraphAdapter

router = APIRouter()


def _seed_chunks_and_units(repo, job) -> None:
    """Seed chunks from documents and PENDING units for every unit-fanout step.

    This makes a freshly triggered job immediately inspectable via the status
    endpoints and ready for a worker to claim without first running the
    chunk_documents atomic step inline.
    """
    docs = repo.get_documents(job.kb_id)
    adapter = FakeGraphAdapter()
    chunks: list[Chunk] = []
    for doc in docs:
        for ordinal, piece in enumerate(adapter.chunk_document(doc.id, doc.text or "")):
            chunks.append(
                Chunk(
                    chunk_id=piece.chunk_id,
                    kb_id=job.kb_id,
                    document_id=doc.id,
                    ordinal=ordinal,
                    text=piece.text,
                )
            )
    if chunks:
        repo.add_chunks(chunks)
    # Seed PENDING units for unit-fanout steps whose subjects are already
    # available at job-creation time. Only extract_graph qualifies (it depends
    # solely on chunks); later fanout steps (summarize, community_reports)
    # depend on artefacts produced by earlier atomic steps, so their units are
    # seeded by the worker during execution instead.
    for step in repo.get_steps(job.id):
        if step.name != "extract_graph":
            continue
        strategy = STRATEGIES.get(step.name)
        if strategy is None:
            continue
        batch = strategy.next_units_batch(repo, step)
        if batch:
            repo.add_units(step.id, [(s.subject_type, s.subject_id) for s in batch])


@router.post("/kbs/{kb_id}/jobs", status_code=202)
def trigger_job(kb_id: int, payload: dict, request: Request):
    repo = request.app.state.repo
    job = repo.create_job_pending(kb_id=kb_id, method=payload.get("method", "standard"))
    _seed_chunks_and_units(repo, job)
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
