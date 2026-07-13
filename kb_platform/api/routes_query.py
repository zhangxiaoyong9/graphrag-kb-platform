"""Query endpoint: POST /kbs/{id}/query (SSE streaming)."""

import logging
import time
from uuid import uuid4
import hashlib

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import StreamingResponse

from kb_platform.api.models import QueryRequest, QueryResultOut, SourceOut
from kb_platform.api.sse import format_sse
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.query.engine import QueryParams, StreamDelta, StreamMeta
from kb_platform.query.params import resolve_query_params

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/kbs/{kb_id}/query")
async def query_kb(kb_id: int, payload: QueryRequest, request: Request):
    engine = request.app.state.query_engine
    data_root = request.app.state.data_root

    async def gen():
        # Injected engine (tests) takes priority; otherwise build a real one per-KB.
        # Resolves QueryParams from KB settings (query_defaults) ← per-query params.
        from kb_platform.logging_config import bind_log_context

        nonlocal data_root
        local_engine = engine
        import json

        query_id = uuid4().hex[:12]
        t0 = time.perf_counter()
        delta_count = 0
        first_token_ms: float | None = None
        request_id = getattr(request.state, "request_id", None)
        query_text = payload.query or ""
        query_hash = hashlib.sha256(query_text.encode("utf-8", errors="replace")).hexdigest()[:12]
        with bind_log_context(request_id=request_id, query_id=query_id, kb_id=kb_id):
            logger.info(
                "query start method=%s query_chars=%d query_hash=%s",
                payload.method, len(query_text), query_hash,
            )
            try:
                per_query = (
                    QueryParams(**payload.params.model_dump()) if payload.params is not None else None
                )
                resolved: QueryParams | None = None
                if local_engine is None:
                    from kb_platform.query.factory import build_query_engine

                    app_state = request.app.state
                    repo = app_state.repo
                    with session_scope(repo.engine) as s:
                        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
                        if kb is None:
                            logger.warning("query kb %s not found", kb_id)
                            yield format_sse("error", {"message": f"kb {kb_id} not found"})
                            return
                        data_root = kb.data_root
                        kb_settings = json.loads(kb.settings_json or "{}")
                        resolved = resolve_query_params(kb_settings, per_query)
                    try:
                        local_engine = build_query_engine(payload.method, kb, repo, app_state)
                    except Exception as exc:  # noqa: BLE001 - graceful, never 500
                        logger.exception("engine build failed")
                        yield format_sse("error", {"message": f"engine build failed: {exc}"})
                        return
                else:
                    # Injected engine (tests): per-query params only; KB defaults are
                    # applied in the production branch where the KB is already loaded.
                    resolved = resolve_query_params({}, per_query)

                yield format_sse("meta", {"method": payload.method})
                async for ev in local_engine.stream_search(
                    payload.method, payload.query, data_root, params=resolved
                ):
                    if isinstance(ev, StreamMeta):
                        # L3 transparency: a cypher/hybrid engine reveals the generated
                        # Cypher. graphrag/Fake engines never yield a StreamMeta, so they
                        # emit only the leading meta{method} above.
                        yield format_sse(
                            "meta", {"method": payload.method, "cypher": ev.cypher}
                        )
                    elif isinstance(ev, StreamDelta):
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - t0) * 1000
                            logger.info("query first token in %.0fms", first_token_ms)
                        delta_count += 1
                        yield format_sse("delta", {"text": ev.text})
                    else:  # StreamDone — carries truncated for cypher/hybrid row-cap
                        yield format_sse(
                            "done",
                            {
                                "result": QueryResultOut(
                                    answer=ev.answer,
                                    method=payload.method,
                                    error=ev.error,
                                    elapsed_ms=ev.elapsed_ms,
                                    prompt_tokens=ev.prompt_tokens,
                                    output_tokens=ev.output_tokens,
                                    truncated=getattr(ev, "truncated", False),
                                    sources=[
                                        SourceOut(kind=s.kind, name=s.name, text=s.text)
                                        for s in ev.sources
                                    ]
                                    if ev.sources
                                    else None,
                                ).model_dump(mode="json")
                            },
                        )
                logger.info(
                    "query done in %.0fms; deltas=%s",
                    (time.perf_counter() - t0) * 1000, delta_count,
                )
            except Exception:
                logger.exception("query stream failed")
                raise

    return StreamingResponse(gen(), media_type="text/event-stream")
