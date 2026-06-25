"""QueryEngine seam: Fake + GraphRag wrappers."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SourceRef:
    """A single cited source from a graphrag search context.

    kind is one of: entity | text_unit | relationship.
    """

    kind: str
    name: str
    text: str


@dataclass
class QueryResult:
    answer: str
    method: str
    error: str | None = None
    # Real server-side latency (SearchResult.completion_time * 1000), ms.
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    llm_calls: int | None = None
    sources: list[SourceRef] | None = None


class QueryEngine(Protocol):
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult: ...


class FakeQueryEngine:
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult:
        return QueryResult(answer=f"[{method}] You asked: {query}", method=method)
