"""Query endpoint: POST /kbs/{id}/query."""

from fastapi import APIRouter, Request

from kb_platform.api.models import QueryRequest, QueryResultOut
from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase

from sqlalchemy import select

router = APIRouter()


@router.post("/kbs/{kb_id}/query", response_model=QueryResultOut)
async def query_kb(kb_id: int, payload: QueryRequest, request: Request) -> QueryResultOut:
    # Injected engine (tests) takes priority; otherwise build a real engine per-KB
    engine = request.app.state.query_engine
    if engine is None:
        from kb_platform.graph.graphrag_adapter import assemble_kb_settings
        from kb_platform.query.graphrag_engine import GraphRagQueryEngine

        repo = request.app.state.repo
        with session_scope(repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
            if kb is None:
                return QueryResultOut(answer="", method=payload.method, error=f"kb {kb_id} not found")
            data_root = kb.data_root
            # Resolve provider profiles + decrypted keys into a full settings dict
            # (the same seam the indexing path uses). Passing raw kb.settings_json
            # here would omit the llm/embedding blocks, so graphrag would have no
            # completion model and every query would fail with
            # "default_completion_model not found".
            try:
                model_config = assemble_kb_settings(kb, repo)
            except Exception as exc:  # noqa: BLE001 - surface as a graceful error, not a 500
                return QueryResultOut(
                    answer="", method=payload.method, error=f"settings resolution failed: {exc}"
                )
        engine = GraphRagQueryEngine(data_root=data_root, model_config=model_config)
    from kb_platform.api.models import SourceOut

    result = await engine.search(payload.method, payload.query, request.app.state.data_root)
    return QueryResultOut(
        answer=result.answer,
        method=result.method,
        error=result.error,
        elapsed_ms=result.elapsed_ms,
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        llm_calls=result.llm_calls,
        sources=[SourceOut(kind=s.kind, name=s.name, text=s.text) for s in result.sources]
        if result.sources
        else None,
    )
