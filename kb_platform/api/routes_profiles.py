# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Provider-profile CRUD: reusable LLM/embedding connections + encrypted keys."""
from fastapi import APIRouter, HTTPException, Query, Request

from kb_platform.api.models import ProfileCreate, ProfileOut, ProfileUpdate

router = APIRouter()


def _out(repo, p) -> ProfileOut:
    return ProfileOut(
        id=p.id, name=p.name, kind=p.kind, provider=p.provider, model=p.model,
        api_base=p.api_base, api_version=p.api_version,
        structured_output=p.structured_output, api_keys_count=repo.profile_key_count(p.id),
    )


@router.get("/provider-profiles", response_model=list[ProfileOut])
def list_profiles(request: Request, kind: str | None = Query(default=None)):
    repo = request.app.state.repo
    return [_out(repo, p) for p in repo.list_profiles(kind=kind)]


@router.post("/provider-profiles", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileCreate, request: Request):
    repo = request.app.state.repo
    try:
        p = repo.create_profile(**payload.model_dump())
    except Exception as exc:  # noqa: BLE001 - IntegrityError on duplicate name
        raise HTTPException(409, f"profile name already exists: {exc}") from exc
    return _out(repo, p)


@router.patch("/provider-profiles/{pid}", response_model=ProfileOut)
def update_profile(pid: int, payload: ProfileUpdate, request: Request):
    repo = request.app.state.repo
    p = repo.update_profile(pid, **payload.model_dump(exclude_unset=True))
    if p is None:
        raise HTTPException(404)
    return _out(repo, p)


@router.delete("/provider-profiles/{pid}", status_code=204)
def delete_profile(pid: int, request: Request):
    repo = request.app.state.repo
    refs = repo.referencing_kbs(pid)
    if refs:
        raise HTTPException(409, detail={"referencing_kbs": refs})
    if not repo.delete_profile(pid):
        raise HTTPException(404)
