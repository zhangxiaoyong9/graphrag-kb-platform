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
    entities: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=["title", "type", "description", "source_id"])
    )
    relationships: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            columns=["source", "target", "weight", "description", "source_id"]
        )
    )


@dataclass
class CommunityReport:
    title: str
    summary: str
    findings: list[str]
    rank: float
    full_content: str
    level: int
    community: str


class GraphAdapter(Protocol):
    """Interface every step uses. Implementations: FakeGraphAdapter (tests), GraphRagAdapter (real)."""

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]: ...

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult: ...

    def merge_extractions(
        self, results: list[ExtractionResult]
    ) -> tuple[pd.DataFrame, pd.DataFrame]: ...

    # --- Phase 2a (Task 4) extensions: summarize / report / cluster / finalize ---

    def summarize_entity_sync(self, name: str, descriptions: list[str]) -> str: ...

    async def summarize_entity(self, name: str, descriptions: list[str]) -> str: ...

    def report_community_sync(self, context: dict) -> CommunityReport: ...

    async def report_community(self, context: dict) -> CommunityReport: ...

    async def report_community_plain(self, context: dict) -> CommunityReport: ...

    def cluster_relationships(self, relationships: pd.DataFrame) -> pd.DataFrame: ...

    def finalize_entities_relationships(
        self, entities: pd.DataFrame, relationships: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]: ...

    # --- Phase 3b (Task 1) extension: embeddings ---

    def embed_items(self, texts: list[str]) -> list[list[float]]: ...


def _hash(text: str) -> str:
    return hashlib.sha512(text.encode()).hexdigest()


def cell_to_text(cell, sep: str = "; ") -> str:
    """Coerce a single DataFrame/parquet cell to a display string.

    ``merge_extractions`` aggregates ``description``/``text_unit_ids`` into Python
    lists, which parquet round-trips as numpy arrays. Evaluating such a cell in a
    boolean context (``cell or ""``) raises ``ValueError: truth value of an array
    is ambiguous`` for >1 element, and ``str(array)`` yields the ugly repr.
    Duck-type (str | scalar | sequence) so callers never index numpy; sequences are
    joined with ``sep``. Use this for any display-facing read of those columns.
    """
    if cell is None or isinstance(cell, str):
        return cell or ""
    if hasattr(cell, "__iter__"):  # list / tuple / ndarray / Series
        return sep.join(str(x) for x in cell)
    if pd.isna(cell):  # scalar NaN/NaT
        return ""
    return str(cell)


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
            [
                {"title": n.upper(), "type": "CONCEPT", "description": n, "source_id": chunk_id}
                for n in names
            ]
            or [
                {
                    "title": "PLACEHOLDER",
                    "type": "CONCEPT",
                    "description": text[:40],
                    "source_id": chunk_id,
                }
            ]
        )
        rels = pd.DataFrame(columns=["source", "target", "weight", "description", "source_id"])
        if len(names) >= 2:
            rels = pd.DataFrame(
                [
                    {
                        "source": names[0].upper(),
                        "target": names[1].upper(),
                        "weight": 1.0,
                        "description": "related",
                        "source_id": chunk_id,
                    }
                ]
            )
        return ExtractionResult(entities=entities, relationships=rels)

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult:
        return self.extract_chunk_sync(chunk_id, text)

    def merge_extractions(
        self, results: list[ExtractionResult]
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        entity_dfs = [r.entities for r in results if not r.entities.empty]
        rel_dfs = [r.relationships for r in results if not r.relationships.empty]
        entities = (
            pd.concat(entity_dfs, ignore_index=True)
            .groupby(["title", "type"], sort=False)
            .agg(
                description=("description", list),
                text_unit_ids=("source_id", list),
                frequency=("source_id", "count"),
            )
            .reset_index()
            if entity_dfs
            else pd.DataFrame(
                columns=["title", "type", "description", "text_unit_ids", "frequency"]
            )
        )
        relationships = (
            pd.concat(rel_dfs, ignore_index=True)
            .groupby(["source", "target"], sort=False)
            .agg(
                description=("description", list),
                text_unit_ids=("source_id", list),
                weight=("weight", "sum"),
            )
            .reset_index()
            if rel_dfs
            else pd.DataFrame(
                columns=["source", "target", "description", "text_unit_ids", "weight"]
            )
        )
        if not entities.empty and not relationships.empty:
            titles = set(entities["title"])
            relationships = relationships[
                relationships["source"].isin(titles) & relationships["target"].isin(titles)
            ].reset_index(drop=True)
        return entities, relationships

    # --- Phase 2a (Task 4): summarize / report / cluster / finalize (deterministic) ---

    def summarize_entity_sync(self, name: str, descriptions: list[str]) -> str:
        return "; ".join(descriptions)

    async def summarize_entity(self, name: str, descriptions: list[str]) -> str:
        return self.summarize_entity_sync(name, descriptions)

    def report_community_sync(self, context: dict) -> CommunityReport:
        names = [e["title"] for e in context.get("entities", [])]
        title = names[0] if names else f"Community {context['community']}"
        summary = (
            f"Community {context['community']} covers {', '.join(names[:5]) or 'no entities'}."
        )
        return CommunityReport(
            title=title,
            summary=summary,
            findings=[summary],
            rank=0.5,
            full_content=summary,
            level=context["level"],
            community=context["community"],
        )

    async def report_community(self, context: dict) -> CommunityReport:
        return self.report_community_sync(context)

    async def report_community_plain(self, context: dict) -> CommunityReport:
        return self.report_community_sync(context)

    def cluster_relationships(self, relationships: pd.DataFrame) -> pd.DataFrame:
        # Deterministic "clustering": connected components are communities (single level=0, parent=self).
        # Stand-in for graphrag's hierarchical Leiden (real one lands in Phase 2b).
        import networkx as nx

        g = nx.Graph()
        for _, row in relationships.iterrows():
            g.add_edge(row["source"], row["target"])
        rows = []
        for cid, comp in enumerate(nx.connected_components(g)):
            members = sorted(comp)
            rows.append(
                {"level": 0, "community_id": str(cid), "parent": str(cid), "entity_ids": members}
            )
        return (
            pd.DataFrame(rows)
            if rows
            else pd.DataFrame(columns=["level", "community_id", "parent", "entity_ids"])
        )

    def finalize_entities_relationships(
        self, entities: pd.DataFrame, relationships: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        deg: dict[str, int] = {}
        for _, r in relationships.iterrows():
            deg[r["source"]] = deg.get(r["source"], 0) + 1
            deg[r["target"]] = deg.get(r["target"], 0) + 1
        if not entities.empty:
            entities = entities.copy()
            entities["degree"] = entities["title"].map(lambda t: deg.get(t, 0))
        if not relationships.empty:
            relationships = relationships.copy()
            relationships["combined_degree"] = relationships.apply(
                lambda r: deg.get(r["source"], 0) + deg.get(r["target"], 0), axis=1
            )
        return entities, relationships

    # --- Phase 3b (Task 1): deterministic embeddings (hash-based fixed-length vectors) ---

    def embed_items(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                (int(hashlib.md5((t + str(i)).encode()).hexdigest(), 16) % 100) / 100.0
                for i in range(8)
            ]
            for t in texts
        ]
