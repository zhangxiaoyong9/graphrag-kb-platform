"""KB + document endpoints."""

import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.formparsers import UploadFile

from kb_platform.api.models import DocumentOut, KbCreate, KbOut
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase

router = APIRouter()


def _parse_settings(settings_yaml: str | None) -> str:
    """Validate the incoming YAML-as-string settings; return canonical JSON string."""
    return json.dumps(json.loads(settings_yaml or "{}"))


@router.post("/kbs", response_model=KbOut, status_code=201)
def create_kb(payload: KbCreate, request: Request) -> KbOut:
    repo = request.app.state.repo
    settings = _parse_settings(payload.settings_yaml)
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(
            name=payload.name,
            method=payload.method,
            settings_json=settings,
            data_root=request.app.state.data_root,
        )
        s.add(kb)
        s.flush()
        return KbOut(id=kb.id, name=kb.name, method=kb.method)


@router.get("/kbs", response_model=list[KbOut])
def list_kbs(request: Request) -> list[KbOut]:
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        return [
            KbOut(id=k.id, name=k.name, method=k.method)
            for k in s.scalars(select(KnowledgeBase))
        ]


@router.get("/kbs/{kb_id}", response_model=KbOut)
def get_kb(kb_id: int, request: Request) -> KbOut:
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        return KbOut(id=kb.id, name=kb.name, method=kb.method)


@router.post("/kbs/{kb_id}/documents", response_model=DocumentOut, status_code=201)
async def add_document(kb_id: int, request: Request) -> DocumentOut:
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
    return DocumentOut(id=doc.id, title=doc.title, status=doc.status)


@router.get("/kbs/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(kb_id: int, request: Request) -> list[DocumentOut]:
    repo = request.app.state.repo
    return [
        DocumentOut(id=d.id, title=d.title, status=d.status)
        for d in repo.get_documents(kb_id)
    ]
