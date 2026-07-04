# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Query-preset CRUD: global, cross-KB retrieval presets (A3)."""
import logging

from fastapi import APIRouter, HTTPException, Request

from kb_platform.api.models import QueryPresetIn, QueryPresetOut, QueryPresetUpdate

router = APIRouter()

logger = logging.getLogger(__name__)


def _out(p) -> QueryPresetOut:
    return QueryPresetOut(
        id=p.id, name=p.name, description=p.description, method=p.method,
        community_level=p.community_level, response_type=p.response_type, top_k=p.top_k,
        temperature=p.temperature, system_prompt=p.system_prompt,
        hops=p.hops, cypher_timeout_ms=p.cypher_timeout_ms, is_builtin=p.is_builtin,
    )


def _require_custom(p):
    if p is None:
        raise HTTPException(404)
    if p.is_builtin:
        raise HTTPException(403, "built-in presets are read-only")


@router.get("/query-presets", response_model=list[QueryPresetOut])
def list_presets(request: Request):
    return [_out(p) for p in request.app.state.repo.list_query_presets()]


@router.post("/query-presets", response_model=QueryPresetOut, status_code=201)
def create_preset(payload: QueryPresetIn, request: Request):
    repo = request.app.state.repo
    try:
        p = repo.create_query_preset(is_builtin=False, **payload.model_dump())
    except Exception as exc:  # noqa: BLE001 - IntegrityError on duplicate name
        raise HTTPException(409, f"preset name already exists: {exc}") from exc
    logger.info("preset created id=%s name=%r", p.id, payload.name)
    return _out(p)


@router.patch("/query-presets/{pid}", response_model=QueryPresetOut)
def update_preset(pid: int, payload: QueryPresetUpdate, request: Request):
    repo = request.app.state.repo
    p = repo.get_query_preset(pid)
    _require_custom(p)
    updated = repo.update_query_preset(pid, **payload.model_dump(exclude_unset=True))
    logger.info("preset updated id=%s", pid)
    return _out(updated)


@router.delete("/query-presets/{pid}", status_code=204)
def delete_preset(pid: int, request: Request):
    repo = request.app.state.repo
    p = repo.get_query_preset(pid)
    _require_custom(p)
    repo.delete_query_preset(pid)
    logger.info("preset deleted id=%s", pid)
