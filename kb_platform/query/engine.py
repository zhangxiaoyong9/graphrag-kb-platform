"""QueryEngine seam: Fake + GraphRag wrappers."""

from collections.abc import AsyncIterator
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


@dataclass
class StreamDelta:
    """One incremental answer chunk (token run) from a streaming search."""

    text: str


@dataclass
class StreamDone:
    """Terminal event of a streaming search. Carries the full accumulated answer
    plus the same metadata `QueryResult` carries. ``error`` non-empty => failure
    (``answer`` then holds whatever streamed before the failure)."""

    answer: str = ""
    method: str | None = None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    sources: list[SourceRef] | None = None
    error: str | None = None


class QueryEngine(Protocol):
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult: ...

    async def stream_search(
        self, method: str, query: str, kb_data_root: str
    ) -> AsyncIterator["StreamDelta | StreamDone"]: ...


class FakeQueryEngine:
    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult:
        return QueryResult(answer=f"[{method}] You asked: {query}", method=method)

    async def stream_search(self, method: str, query: str, kb_data_root: str):
        answer = f"[{method}] You asked: {query}"
        # stream word-by-word so tests see multiple deltas
        parts = answer.split(" ")
        for i, w in enumerate(parts):
            yield StreamDelta(text=(w + (" " if i < len(parts) - 1 else "")))
        yield StreamDone(answer=answer, method=method)
