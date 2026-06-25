"""KB + document endpoints."""

import json
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.formparsers import UploadFile

from kb_platform.api.models import DocumentCreate, DocumentOut, JobListItem, KbCreate, KbOut
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.input.doc_reader import read_document

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
            KbOut(id=k.id, name=k.name, method=k.method) for k in s.scalars(select(KnowledgeBase))
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
        try:
            body = DocumentCreate.model_validate(await request.json())
        except ValidationError as e:
            raise HTTPException(422, str(e)) from e
        doc = repo.add_document(kb_id=kb_id, title=body.title or "untitled", text=body.text)
    elif content_type.startswith("multipart/form-data"):
        form = await request.form()
        title = form.get("title")
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(400, "provide 'text' or 'file'")
        data = await upload.read()
        max_bytes = int(os.environ.get("KB_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
        if len(data) > max_bytes:
            raise HTTPException(413, "upload too large")
        text = read_document(data, upload.filename or "upload")
        doc = repo.add_document(kb_id=kb_id, title=title or upload.filename, text=text)
    else:
        raise HTTPException(400, "provide 'text' or 'file'")
    return DocumentOut(
        id=doc.id, title=doc.title, status=doc.status, bytes=doc.bytes, chunk_count=0
    )


@router.get("/kbs/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(kb_id: int, request: Request) -> list[DocumentOut]:
    repo = request.app.state.repo
    counts = repo.chunk_counts_by_document(kb_id)
    return [
        DocumentOut(
            id=d.id,
            title=d.title,
            status=d.status,
            bytes=d.bytes,
            chunk_count=counts.get(d.id, 0),
        )
        for d in repo.get_documents(kb_id)
    ]


@router.delete("/kbs/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(kb_id: int, doc_id: int, request: Request):
    """Delete a document and its chunks (application-level cascade).

    The graph/index is NOT shrunk. Returns 204 on success, 404 if the
    document does not exist or belongs to a different KB.
    """
    repo = request.app.state.repo
    if not repo.delete_document(kb_id, doc_id):
        raise HTTPException(404)
    return None


@router.get("/kbs/{kb_id}/jobs", response_model=list[JobListItem])
def list_jobs(kb_id: int, request: Request) -> list[JobListItem]:
    repo = request.app.state.repo
    return [JobListItem(id=j.id, status=j.status) for j in repo.list_jobs_by_kb(kb_id)]
