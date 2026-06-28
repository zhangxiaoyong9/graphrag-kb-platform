# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""KB + document endpoints."""

import json
import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from starlette.formparsers import UploadFile

from kb_platform.api.models import (
    DocumentCitationOut,
    DocumentCreate,
    DocumentDetailOut,
    DocumentOut,
    EvidenceOut,
    EvidenceSourceOut,
    JobCreated,
    JobListItem,
    KbCreate,
    KbDetailOut,
    KbOut,
    KbStatsOut,
    KbUpdate,
    ProfileRef,
)
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.input.doc_reader import read_document

router = APIRouter()


def _parse_settings(settings_yaml: str | None) -> str:
    """Validate the incoming YAML-as-string settings; return canonical JSON string."""
    return json.dumps(json.loads(settings_yaml or "{}"))


def _require_profile(repo, pid: int, kind: str):
    p = repo.get_profile(pid)
    if p is None:
        raise HTTPException(400, f"unknown {kind} profile id {pid}")
    return p


def _profileref(repo, pid: int | None) -> ProfileRef | None:
    if pid is None:
        return None
    p = repo.get_profile(pid)
    return ProfileRef(id=p.id, name=p.name, provider=p.provider, model=p.model) if p else None


def _snippet(text: str, limit: int = 220) -> str:
    """Return a compact one-line snippet for citation lists."""
    one_line = " ".join((text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


def _citation_id(chunk_row_id: int) -> str:
    return f"chunk:{chunk_row_id}"


def _chunk_row_id_from_citation(citation_id: str) -> int | None:
    prefix = "chunk:"
    if not citation_id.startswith(prefix):
        return None
    try:
        return int(citation_id[len(prefix):])
    except ValueError:
        return None


_SENSITIVE = ("key", "token", "secret", "password")


def _is_sensitive(key: str) -> bool:
    """True if ``key`` looks like it holds a credential.

    Matches a sensitive token as the whole key (``token``) or as a trailing
    segment in snake_case (``api_key``, ``auth_token``, ``client_secret``).
    Substring matching is intentionally avoided so that non-secret variants
    such as ``api_key_env`` (the *name* of an env var, recommended in the
    README) are returned in cleartext for the config-edit form to backfill.
    """
    k = key.lower()
    return any(k == s or k.endswith("_" + s) for s in _SENSITIVE)


def _redact(settings_json: str | None) -> dict:
    """Parse stored settings JSON and mask any sensitive values.

    Keys are never stored in the DB (resolved from env at runtime), but this
    is defense-in-depth for anything a user may have pasted into settings_yaml.
    """
    try:
        data = json.loads(settings_json or "{}")
    except (TypeError, ValueError):
        return {}

    def _walk(node):
        if isinstance(node, dict):
            return {
                k: ("***" if _is_sensitive(k) else _walk(v)) for k, v in node.items()
            }
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(data)


@router.post("/kbs", response_model=KbOut, status_code=201)
def create_kb(payload: KbCreate, request: Request) -> KbOut:
    repo = request.app.state.repo
    _require_profile(repo, payload.llm_profile_id, "llm")
    if payload.embedding_profile_id is not None:
        _require_profile(repo, payload.embedding_profile_id, "embedding")
    settings = _parse_settings(payload.settings_yaml)
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(
            name=payload.name,
            method=payload.method,
            settings_json=settings,
            data_root=request.app.state.data_root,
            llm_profile_id=payload.llm_profile_id,
            embedding_profile_id=payload.embedding_profile_id,
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


@router.get("/kbs/{kb_id}", response_model=KbDetailOut)
def get_kb(kb_id: int, request: Request) -> KbDetailOut:
    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        return KbDetailOut(
            id=kb.id, name=kb.name, method=kb.method,
            settings=_redact(kb.settings_json),
            llm_profile=_profileref(repo, kb.llm_profile_id),
            embedding_profile=_profileref(repo, kb.embedding_profile_id),
        )


@router.get("/kbs/{kb_id}/stats", response_model=KbStatsOut)
def get_kb_stats(kb_id: int, request: Request) -> KbStatsOut:
    """Graph-scale snapshot (entities/relationships/communities/... counts).

    Returns an all-None body (200) when no snapshot exists yet — never 404 for
    an existing KB — so the overview page can degrade gracefully.
    """
    from kb_platform.api.routes_export import _data_root

    root = _data_root(request, kb_id)  # 404 only if the KB row is absent
    path = root / "stats.json"
    if not path.exists():
        return KbStatsOut()
    return KbStatsOut(**json.loads(path.read_text()))


@router.patch("/kbs/{kb_id}", response_model=KbDetailOut)
def update_kb(kb_id: int, payload: KbUpdate, request: Request) -> KbDetailOut:
    """Update a KB's name/method/settings/profiles (full replace)."""
    repo = request.app.state.repo
    _require_profile(repo, payload.llm_profile_id, "llm")
    if payload.embedding_profile_id is not None:
        _require_profile(repo, payload.embedding_profile_id, "embedding")
    settings = _parse_settings(payload.settings_yaml)
    kb = repo.update_kb(
        kb_id, name=payload.name, method=payload.method, settings_json=settings,
        llm_profile_id=payload.llm_profile_id,
        embedding_profile_id=payload.embedding_profile_id,
    )
    if kb is None:
        raise HTTPException(404)
    return KbDetailOut(
        id=kb.id, name=kb.name, method=kb.method,
        settings=_redact(kb.settings_json),
        llm_profile=_profileref(repo, kb.llm_profile_id),
        embedding_profile=_profileref(repo, kb.embedding_profile_id),
    )


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


@router.get("/kbs/{kb_id}/documents/{doc_id}", response_model=DocumentDetailOut)
def get_document_detail(kb_id: int, doc_id: int, request: Request) -> DocumentDetailOut:
    repo = request.app.state.repo
    doc = repo.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(404)
    chunks = repo.get_document_chunks(kb_id, doc_id)
    citations = [
        DocumentCitationOut(
            id=_citation_id(chunk.id),
            label=f"分块 {chunk.ordinal + 1}",
            snippet=_snippet(chunk.text),
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
        )
        for chunk in chunks
    ]
    return DocumentDetailOut(
        id=doc.id,
        title=doc.title,
        status=doc.status,
        bytes=doc.bytes,
        chunk_count=len(chunks),
        text=doc.text or "",
        citations=citations,
    )


@router.get(
    "/kbs/{kb_id}/documents/{doc_id}/citations/{citation_id}/evidence",
    response_model=EvidenceOut,
)
def get_document_evidence(
    kb_id: int,
    doc_id: int,
    citation_id: str,
    request: Request,
) -> EvidenceOut:
    repo = request.app.state.repo
    doc_title = repo.get_document_title(kb_id, doc_id)
    if doc_title is None:
        raise HTTPException(404)
    chunk_row_id = _chunk_row_id_from_citation(citation_id)
    if chunk_row_id is None:
        raise HTTPException(404)
    chunk = repo.get_document_chunk_by_id(kb_id, doc_id, chunk_row_id)
    if chunk is None:
        raise HTTPException(404)
    before = repo.get_document_chunk_by_ordinal(kb_id, doc_id, chunk.ordinal - 1)
    after = repo.get_document_chunk_by_ordinal(kb_id, doc_id, chunk.ordinal + 1)
    return EvidenceOut(
        citation_id=citation_id,
        matched=chunk.text,
        before=before.text if before is not None else None,
        after=after.text if after is not None else None,
        source=EvidenceSourceOut(
            document_id=doc_id,
            document_title=doc_title,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
        ),
    )


@router.delete("/kbs/{kb_id}/documents/{doc_id}")
def delete_document(kb_id: int, doc_id: int, request: Request, response: Response):
    """Delete a document and its chunks; auto-trigger an incremental shrink job.

    The shrink job rebuilds entities/relationships/text_units/vectors from the
    remaining chunks (merge_delta prunes orphan extractions). Returns 202 + the
    new job when one is created; 204 when no job is needed (KB never indexed, or
    an incremental job is already pending/running and will absorb the deletion).
    404 if the document does not exist or belongs to a different KB.
    """
    repo = request.app.state.repo
    if not repo.delete_document(kb_id, doc_id):
        raise HTTPException(404)
    job = _maybe_create_shrink_job(repo, kb_id)
    if job is not None:
        response.status_code = 202
        return JobCreated(id=job.id, status=job.status)
    response.status_code = 204
    return None


def _maybe_create_shrink_job(repo, kb_id: int):
    """Create an incremental shrink job after a deletion, or None if unnecessary.

    - Coalesce: an incremental job already pending/running will reconcile via
      merge_delta, so don't start another.
    - Never-indexed guard: a KB with no prior SUCCEEDED job has nothing to shrink.
    A pending/running *full* job is NOT a coalesce trigger — the new incremental
    job queues behind it and its merge_delta picks up the deletion.
    """
    from kb_platform.db.enums import JobStatus

    jobs = repo.list_jobs_by_kb(kb_id)
    if any(
        j.type == "incremental" and j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        for j in jobs
    ):
        return None
    if not any(j.status == JobStatus.SUCCEEDED for j in jobs):
        return None
    return repo.create_job_pending(kb_id=kb_id, method="standard", type="incremental")


@router.get("/kbs/{kb_id}/jobs", response_model=list[JobListItem])
def list_jobs(kb_id: int, request: Request) -> list[JobListItem]:
    repo = request.app.state.repo
    return [JobListItem(id=j.id, status=j.status) for j in repo.list_jobs_by_kb(kb_id)]


@router.get("/prompts/defaults")
def prompt_defaults() -> dict:
    """Return graphrag's built-in indexing + query prompts (for the form's 'view default')."""
    from graphrag.prompts.index.community_report import COMMUNITY_REPORT_PROMPT
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT
    from graphrag.prompts.index.summarize_descriptions import SUMMARIZE_PROMPT
    from graphrag.prompts.query.basic_search_system_prompt import BASIC_SEARCH_SYSTEM_PROMPT
    from graphrag.prompts.query.global_search_map_system_prompt import MAP_SYSTEM_PROMPT
    from graphrag.prompts.query.global_search_reduce_system_prompt import REDUCE_SYSTEM_PROMPT
    from graphrag.prompts.query.local_search_system_prompt import LOCAL_SEARCH_SYSTEM_PROMPT

    return {
        "extract_graph": GRAPH_EXTRACTION_PROMPT,
        "summarize_descriptions": SUMMARIZE_PROMPT,
        "community_reports": COMMUNITY_REPORT_PROMPT,
        "local_system": LOCAL_SEARCH_SYSTEM_PROMPT,
        "global_map": MAP_SYSTEM_PROMPT,
        "global_reduce": REDUCE_SYSTEM_PROMPT,
        "basic_system": BASIC_SEARCH_SYSTEM_PROMPT,
    }
