# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Cost aggregation endpoints."""

from fastapi import APIRouter, Request

from kb_platform.api.models import JobCostOut, KbCostOut

router = APIRouter()


def _attach_model_name(by_model: dict) -> dict:
    """Inject the dict key as ``model`` on each CostItem-shaped slot.

    ``Repository._sum_cost`` keys ``by_model`` by model name and keeps each
    slot as ``{prompt_tokens, completion_tokens, usd}`` (no ``model`` field).
    ``CostItem`` requires ``model``, so we lift the key into the value here.
    """
    return {m: {**slot, "model": m} for m, slot in by_model.items()}


@router.get("/kbs/{kb_id}/jobs/{job_id}/cost", response_model=JobCostOut)
def job_cost(kb_id: int, job_id: int, request: Request) -> JobCostOut:  # noqa: ARG001
    repo = request.app.state.repo
    data = repo.job_cost(job_id)
    data["by_model"] = _attach_model_name(data["by_model"])
    return JobCostOut(**data)


@router.get("/kbs/{kb_id}/cost", response_model=KbCostOut)
def kb_cost(kb_id: int, request: Request) -> KbCostOut:  # noqa: ARG001
    repo = request.app.state.repo
    data = repo.kb_cost(kb_id)
    data["by_model"] = _attach_model_name(data["by_model"])
    return KbCostOut(**data)
