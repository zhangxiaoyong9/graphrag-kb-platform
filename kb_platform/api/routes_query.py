"""Query endpoint: POST /kbs/{id}/query (SSE streaming)."""

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import StreamingResponse

from kb_platform.api.models import QueryRequest, QueryResultOut, SourceOut
from kb_platform.api.sse import format_sse
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.query.engine import QueryParams, StreamDelta
from kb_platform.query.params import resolve_query_params

router = APIRouter()


@router.post("/kbs/{kb_id}/query")
async def query_kb(kb_id: int, payload: QueryRequest, request: Request):
    engine = request.app.state.query_engine
    data_root = request.app.state.data_root

    async def gen():
        # Injected engine (tests) takes priority; otherwise build a real one per-KB.
        # Resolves QueryParams from KB settings (query_defaults) ← per-query params.
        nonlocal data_root
        local_engine = engine
        import json

        per_query = (
            QueryParams(**payload.params.model_dump()) if payload.params is not None else None
        )
        resolved: QueryParams | None = None
        if local_engine is None:
            from kb_platform.graph.graphrag_adapter import assemble_kb_settings
            from kb_platform.query.graphrag_engine import GraphRagQueryEngine

            repo = request.app.state.repo
            with session_scope(repo.engine) as s:
                kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
                if kb is None:
                    yield format_sse("error", {"message": f"kb {kb_id} not found"})
                    return
                data_root = kb.data_root
                kb_settings = json.loads(kb.settings_json or "{}")
                resolved = resolve_query_params(kb_settings, per_query)
                try:
                    model_config = assemble_kb_settings(kb, repo)
                except Exception as exc:  # noqa: BLE001 - graceful, never 500
                    yield format_sse("error", {"message": f"settings resolution failed: {exc}"})
                    return
            try:
                local_engine = GraphRagQueryEngine(data_root=data_root, model_config=model_config)
            except Exception as exc:  # noqa: BLE001 - graceful, never 500
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
            if isinstance(ev, StreamDelta):
                yield format_sse("delta", {"text": ev.text})
            else:  # StreamDone — unchanged QueryResultOut construction
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
                            sources=[
                                SourceOut(kind=s.kind, name=s.name, text=s.text)
                                for s in ev.sources
                            ]
                            if ev.sources
                            else None,
                        ).model_dump(mode="json")
                    },
                )

    return StreamingResponse(gen(), media_type="text/event-stream")
