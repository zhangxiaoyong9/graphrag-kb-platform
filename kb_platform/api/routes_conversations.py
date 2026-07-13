# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Conversation CRUD + POST /conversations/{id}/messages (multi-turn Q&A).

Single-shot ``POST /kbs/{id}/query`` (MCP, query-test page) is unchanged; this
is the multi-turn path that runs ConversationService above the QueryEngine.
"""
import json
import logging
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from kb_platform.api.models import (
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationRename,
    MessageOut,
    MessageSend,
    SourceOut,
)
from kb_platform.api.sse import format_sse
from kb_platform.conversation.service import ConversationService
from kb_platform.query.params import resolve_query_params

logger = logging.getLogger(__name__)
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
        cypher=m.cypher,
        truncated=m.truncated,
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


@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: int, payload: MessageSend, request: Request):
    repo = request.app.state.repo
    if repo.get_conversation(conv_id) is None:
        raise HTTPException(404)
    engine = request.app.state.query_engine
    rewriter = request.app.state.rewriter
    data_root = request.app.state.data_root

    async def gen():
        from kb_platform.logging_config import bind_log_context

        query_id = uuid4().hex[:12]
        t0 = time.perf_counter()
        conv = repo.get_conversation(conv_id)
        kb_id = conv.kb_id if conv else None
        request_id = getattr(request.state, "request_id", None)
        with bind_log_context(request_id=request_id, query_id=query_id, kb_id=kb_id):
            logger.info("conversation message start conv=%s", conv_id)
            try:
                # Production: resolve KB settings, build a real engine + rewriter.
                if engine is None:
                    kb = repo.get_kb(conv.kb_id) if conv else None
                    if kb is None:
                        yield format_sse("error", {"message": f"conversation {conv_id} has no kb"})
                        return
                    from kb_platform.conversation.rewriter import LlmRewriter
                    from kb_platform.graph.graphrag_adapter import assemble_kb_settings, build_chat_complete
                    from kb_platform.query.factory import build_query_engine

                    try:
                        settings = assemble_kb_settings(kb, repo)
                        kb_settings = json.loads(kb.settings_json or "{}")
                        resolved = resolve_query_params(kb_settings, None)
                    except Exception as exc:  # noqa: BLE001 - graceful error, never 500
                        logger.exception("conversation settings resolution failed")
                        yield format_sse("error", {"message": f"settings resolution failed: {exc}"})
                        return
                    try:
                        # method defaults to "local" (MessageSend.method is optional);
                        # build_query_engine dispatches to Neo4j for cypher/hybrid when the
                        # KB has a neo4j_profile_id, else graphrag.
                        method = payload.method or "local"
                        local_engine = build_query_engine(method, kb, repo, request.app.state)
                        try:
                            local_rewriter = LlmRewriter(build_chat_complete(settings))
                        except Exception:  # noqa: BLE001 - rewriter optional
                            logger.exception(
                                "conversation rewriter build failed; continuing without rewrite"
                            )
                            local_rewriter = None
                    except Exception as exc:  # noqa: BLE001 - graceful error, never 500
                        logger.exception("conversation engine build failed")
                        yield format_sse("error", {"message": f"engine build failed: {exc}"})
                        return
                else:
                    local_engine = engine
                    local_rewriter = rewriter
                    # Injected-engine branch: no KB at hand, so resolve from empty
                    # settings (per-query=None -> all-None QueryParams).
                    resolved = resolve_query_params({}, None)

                service = ConversationService(repo, local_engine, local_rewriter, data_root)
                async for ev in service.send_streaming(
                    conv_id, payload.content, payload.method, params=resolved
                ):
                    if ev.type == "done" and ev.message is not None:
                        yield format_sse("done", {"message": _message_out(ev.message).model_dump(mode="json")})
                    else:
                        yield format_sse(ev.type, ev.data)
            finally:
                logger.info(
                    "conversation message done in %.0fms",
                    (time.perf_counter() - t0) * 1000,
                )

    return StreamingResponse(gen(), media_type="text/event-stream")
