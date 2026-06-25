"""KB + document endpoints."""

import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.formparsers import UploadFile

from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase

router = APIRouter()


def _parse_settings(settings_yaml: str | None) -> str:
    """Validate the incoming YAML-as-string settings; return canonical JSON string."""
    return json.dumps(json.loads(settings_yaml or "{}"))


@router.post("/kbs", status_code=201)
def create_kb(payload: dict, request: Request) -> dict:
    repo = request.app.state.repo
    settings = _parse_settings(payload.get("settings_yaml"))
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(
            name=payload["name"],
            method=payload.get("method", "standard"),
            settings_json=settings,
            data_root=request.app.state.data_root,
        )
        s.add(kb)
        s.flush()
        return {"id": kb.id, "name": kb.name}


@router.get("/kbs")
def list_kbs(request: Request) -> list[dict]:
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        return [{"id": k.id, "name": k.name} for k in s.scalars(select(KnowledgeBase))]


@router.get("/kbs/{kb_id}")
def get_kb(kb_id: int, request: Request) -> dict:
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        return {"id": kb.id, "name": kb.name, "method": kb.method}


@router.post("/kbs/{kb_id}/documents", status_code=201)
async def add_document(kb_id: int, request: Request) -> dict:
    """Add a document via JSON body {title, text} or multipart file upload."""
    repo = request.app.state.repo
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        data = await request.json()
        text = data.get("text")
        title = data.get("title") or "untitled"
        if text is None:
            raise HTTPException(400, "provide 'text' or 'file'")
        doc = repo.add_document(kb_id=kb_id, title=title, text=text)
    elif content_type.startswith("multipart/form-data"):
        form = await request.form()
        title = form.get("title")
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(400, "provide 'text' or 'file'")
        raw = upload.file.read().decode("utf-8", errors="replace")
        doc = repo.add_document(kb_id=kb_id, title=title or upload.filename, text=raw)
    else:
        raise HTTPException(400, "provide 'text' or 'file'")
    return {"id": doc.id, "title": doc.title}


@router.get("/kbs/{kb_id}/documents")
def list_documents(kb_id: int, request: Request) -> list[dict]:
    repo = request.app.state.repo
    return [{"id": d.id, "title": d.title} for d in repo.get_documents(kb_id)]
