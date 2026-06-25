"""QueryEngine seam: Fake + GraphRag wrappers."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class QueryResult:
    answer: str
    method: str
    error: str | None = None


class QueryEngine(Protocol):
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult: ...


class FakeQueryEngine:
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult:
        return QueryResult(answer=f"[{method}] You asked: {query}", method=method)
