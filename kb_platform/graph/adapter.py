"""Graph adapter abstraction — the only graphrag coupling seam (real impl in graphrag_adapter.py)."""

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass
class ChunkText:
    chunk_id: str
    text: str


@dataclass
class ExtractionResult:
    entities: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["title", "type", "description", "source_id"]))
    relationships: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["source", "target", "weight", "description", "source_id"]))


class GraphAdapter(Protocol):
    """Interface every step uses. Implementations: FakeGraphAdapter (tests), GraphRagAdapter (real)."""

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]: ...

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult: ...

    def merge_extractions(self, results: list[ExtractionResult]) -> tuple[pd.DataFrame, pd.DataFrame]: ...


def _hash(text: str) -> str:
    return hashlib.sha512(text.encode()).hexdigest()


class FakeGraphAdapter:
    """Deterministic, no-LLM adapter for engine tests.

    - chunk_document: naive fixed-size word split.
    - extract_chunk: emits one entity per capitalized word + a self-relationship.
    - fail_on: set of chunk_ids that should raise (to test retry).
    """

    def __init__(self, chunk_size: int = 1000, fail_on: set[str] | None = None) -> None:
        self.chunk_size = chunk_size
        self.fail_on = fail_on or set()
        self.extract_calls: list[str] = []

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]:
        words = text.split()
        chunks: list[ChunkText] = []
        for i in range(0, len(words), self.chunk_size):
            piece = " ".join(words[i : i + self.chunk_size])
            chunks.append(ChunkText(chunk_id=_hash(piece), text=piece))
        return chunks or [ChunkText(chunk_id=_hash(text), text=text)]

    def extract_chunk_sync(self, chunk_id: str, text: str) -> ExtractionResult:
        if chunk_id in self.fail_on:
            raise RuntimeError(f"injected failure for {chunk_id}")
        self.extract_calls.append(chunk_id)
        names = [w for w in text.split() if w[:1].isupper()]
        entities = pd.DataFrame(
            [{"title": n.upper(), "type": "CONCEPT", "description": n, "source_id": chunk_id} for n in names]
            or [{"title": "PLACEHOLDER", "type": "CONCEPT", "description": text[:40], "source_id": chunk_id}]
        )
        rels = pd.DataFrame(columns=["source", "target", "weight", "description", "source_id"])
        if len(names) >= 2:
            rels = pd.DataFrame([{
                "source": names[0].upper(), "target": names[1].upper(),
                "weight": 1.0, "description": "related", "source_id": chunk_id,
            }])
        return ExtractionResult(entities=entities, relationships=rels)

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult:
        return self.extract_chunk_sync(chunk_id, text)

    def merge_extractions(self, results: list[ExtractionResult]) -> tuple[pd.DataFrame, pd.DataFrame]:
        entity_dfs = [r.entities for r in results if not r.entities.empty]
        rel_dfs = [r.relationships for r in results if not r.relationships.empty]
        entities = (
            pd.concat(entity_dfs, ignore_index=True).groupby(["title", "type"], sort=False)
            .agg(description=("description", list), text_unit_ids=("source_id", list), frequency=("source_id", "count"))
            .reset_index()
            if entity_dfs else pd.DataFrame(columns=["title", "type", "description", "text_unit_ids", "frequency"])
        )
        relationships = (
            pd.concat(rel_dfs, ignore_index=True).groupby(["source", "target"], sort=False)
            .agg(description=("description", list), text_unit_ids=("source_id", list), weight=("weight", "sum"))
            .reset_index()
            if rel_dfs else pd.DataFrame(columns=["source", "target", "description", "text_unit_ids", "weight"])
        )
        if not entities.empty and not relationships.empty:
            titles = set(entities["title"])
            relationships = relationships[
                relationships["source"].isin(titles) & relationships["target"].isin(titles)
            ].reset_index(drop=True)
        return entities, relationships
