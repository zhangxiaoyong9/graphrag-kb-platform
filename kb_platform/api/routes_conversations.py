# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Conversation CRUD + POST /conversations/{id}/messages (multi-turn Q&A).

Single-shot ``POST /kbs/{id}/query`` (MCP, query-test page) is unchanged; this
is the multi-turn path that runs ConversationService above the QueryEngine.
"""
import json

from fastapi import APIRouter, HTTPException, Request

from kb_platform.api.models import (
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationRename,
    MessageOut,
    MessageSend,
    SourceOut,
)

router = APIRouter()


def _conv_out(c, snippet: str = "") -> ConversationOut:
    return ConversationOut(
        id=c.id,
        kb_id=c.kb_id,
        title=c.title,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
        snippet=snippet,
    )


def _sources_from_json(sources_json: str | None) -> list[SourceOut] | None:
    if not sources_json:
        return None
    try:
        data = json.loads(sources_json)
        return [SourceOut(kind=s["kind"], name=s["name"], text=s["text"]) for s in data]
    except Exception:  # noqa: BLE001 - sources are a nice-to-have
        return None


def _message_out(m) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        method=m.method,
        rewritten_query=m.rewritten_query,
        rewrite_fell_back=m.rewrite_fell_back,
        sources=_sources_from_json(m.sources_json),
        prompt_tokens=m.prompt_tokens,
        output_tokens=m.output_tokens,
        elapsed_ms=m.elapsed_ms,
        error=m.error,
    )


@router.post("/kbs/{kb_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(kb_id: int, payload: ConversationCreate, request: Request):
    repo = request.app.state.repo
    if repo.get_kb(kb_id) is None:
        raise HTTPException(404)
    return _conv_out(repo.create_conversation(kb_id, title=payload.title))


@router.get("/kbs/{kb_id}/conversations", response_model=list[ConversationOut])
def list_conversations(kb_id: int, request: Request):
    repo = request.app.state.repo
    return [_conv_out(c, snip) for c, snip in repo.list_conversations(kb_id)]


@router.get("/conversations/{conv_id}", response_model=ConversationDetailOut)
def get_conversation(conv_id: int, request: Request):
    repo = request.app.state.repo
    c = repo.get_conversation(conv_id)
    if c is None:
        raise HTTPException(404)
    detail = ConversationDetailOut(
        id=c.id,
        kb_id=c.kb_id,
        title=c.title,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
        snippet="",
        messages=[_message_out(m) for m in repo.get_messages(conv_id)],
    )
    return detail


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
def rename_conversation(conv_id: int, payload: ConversationRename, request: Request):
    repo = request.app.state.repo
    if not repo.update_conversation_title(conv_id, payload.title):
        raise HTTPException(404)
    return _conv_out(repo.get_conversation(conv_id))


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, request: Request):
    repo = request.app.state.repo
    if not repo.delete_conversation(conv_id):
        raise HTTPException(404)


@router.post("/conversations/{conv_id}/messages", response_model=MessageOut)
async def send_message(conv_id: int, payload: MessageSend, request: Request):
    repo = request.app.state.repo
    engine = request.app.state.query_engine
    rewriter = request.app.state.rewriter
    if engine is None:
        # Production: resolve KB settings, then build a real engine + rewriter.
        conv = repo.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(404)
        kb = repo.get_kb(conv.kb_id)
        if kb is None:
            raise HTTPException(404)
        from kb_platform.conversation.rewriter import LlmRewriter
        from kb_platform.graph.graphrag_adapter import assemble_kb_settings, build_chat_complete
        from kb_platform.query.graphrag_engine import GraphRagQueryEngine

        try:
            settings = assemble_kb_settings(kb, repo)
        except Exception as exc:  # noqa: BLE001 - graceful error, never 500
            return MessageOut(id=0, role="assistant", content="", error=f"settings resolution failed: {exc}")
        engine = GraphRagQueryEngine(data_root=kb.data_root, model_config=settings)
        try:
            rewriter = LlmRewriter(build_chat_complete(settings))
        except Exception:  # noqa: BLE001 - rewriter optional; service falls back per-turn
            rewriter = None

    from kb_platform.conversation.service import ConversationService

    service = ConversationService(repo, engine, rewriter, request.app.state.data_root)
    msg = await service.send(conv_id, payload.content, payload.method)
    if msg is None:
        raise HTTPException(404)
    return _message_out(msg)
