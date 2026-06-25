"""Query endpoint: POST /kbs/{id}/query."""

from fastapi import APIRouter, Request

from kb_platform.api.models import QueryRequest, QueryResultOut

router = APIRouter()


@router.post("/kbs/{kb_id}/query", response_model=QueryResultOut)
async def query_kb(kb_id: int, payload: QueryRequest, request: Request) -> QueryResultOut:  # noqa: ARG001
    engine = request.app.state.query_engine
    result = await engine.search(payload.method, payload.query, request.app.state.data_root)
    return QueryResultOut(answer=result.answer, method=result.method, error=result.error)
