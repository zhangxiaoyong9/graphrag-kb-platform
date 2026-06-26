"""Real GraphAdapter backed by graphrag primitives. The ONLY module that imports graphrag internals."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pandas as pd

from kb_platform.graph.adapter import ChunkText, CommunityReport, ExtractionResult, _hash


class GraphRagAdapter:
    """Adapter calling graphrag chunking + LLM entity extraction.

    chunker is injected (built by build_default_adapter) so tests can supply
    a real graphrag chunker; extract uses a graphrag LLMCompletion.
    """

    def __init__(
        self,
        *,
        chunker,
        extractor_factory: Callable[[], object],
        entity_types: list[str],
        summarize_factory: Callable[[], object] | None = None,
        cluster_fn: Callable[..., list] | None = None,
        finalize_fn: Callable[..., tuple[pd.DataFrame, pd.DataFrame]] | None = None,
        report_factory: Callable[[], object] | None = None,
        embed_factory: Callable[[], object] | None = None,
        completion=None,
    ) -> None:
        self._chunker = chunker
        self._extractor_factory = extractor_factory
        self._entity_types = entity_types
        self._summarize_factory = summarize_factory
        self._cluster_fn = cluster_fn
        self._finalize_fn = finalize_fn
        self._report_factory = report_factory
        self._embed_factory = embed_factory
        self._completion = completion

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]:
        return [
            ChunkText(chunk_id=_hash(tc.text), text=tc.text) for tc in self._chunker.chunk(text)
        ]

    def extract_chunk_sync(self, chunk_id: str, text: str) -> ExtractionResult:
        extractor = self._extractor_factory()
        # Sync test helper — no running loop, so asyncio.run is the cross-version-safe path
        # (get_event_loop().run_until_complete is deprecated on 3.12+ without a running loop).
        entities_df, rels_df = asyncio.run(
            extractor(text=text, entity_types=self._entity_types, source_id=chunk_id)
        )
        return ExtractionResult(entities=entities_df, relationships=rels_df)

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult:
        extractor = self._extractor_factory()
        entities_df, rels_df = await extractor(
            text=text, entity_types=self._entity_types, source_id=chunk_id
        )
        return ExtractionResult(entities=entities_df, relationships=rels_df)

    def merge_extractions(
        self, results: list[ExtractionResult]
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        from kb_platform.graph.adapter import FakeGraphAdapter

        # merge logic is identical across adapters — reuse the shared implementation
        return FakeGraphAdapter().merge_extractions(results)

    async def summarize_entity(self, name: str, descriptions: list[str]) -> str:
        if self._summarize_factory is None:
            raise RuntimeError("summarize_factory not configured")
        extractor = self._summarize_factory()
        result = await extractor(id=name, descriptions=list(descriptions))
        return result.description

    async def report_community(self, context: dict) -> CommunityReport:
        if self._report_factory is None:
            raise RuntimeError("report_factory not configured")
        extractor = self._report_factory()
        input_text = _format_community_context(context)
        result = await extractor(input_text=input_text)
        so = result.structured_output
        # graphrag's CommunityReportResponse uses `rating`/`rating_explanation`
        # (not `rank`/`fields`); map rating -> CommunityReport.rank.
        return CommunityReport(
            title=getattr(so, "title", context["community"]) if so else context["community"],
            summary=getattr(so, "summary", "") if so else "",
            findings=[f.summary for f in getattr(so, "findings", []) or []],
            rank=max(0.0, min(1.0, float(getattr(so, "rating", 0.0) or 0.0) / 10.0)),
            full_content=result.output or "",
            level=context["level"],
            community=context["community"],
        )

    async def report_community_plain(self, context: dict) -> CommunityReport:
        """Structured-output-free community report (for providers that reject json_schema).

        Asks the model to return a JSON object, then leniently parses it. Falls
        back to a minimal report so a single bad community cannot fail the step.
        """
        if self._completion is None:
            raise RuntimeError("completion not configured for plain community reports")
        prompt = (
            _format_community_context(context)
            + "\n\nWrite a concise report. Respond with ONLY a JSON object, no prose, "
            'of the shape: {"title": str, "summary": str, "findings": [str], '
            '"rating": <0-10 float>}.'
        )
        resp = await self._completion.completion_async(messages=prompt, response_format=None)
        return _parse_report_json(getattr(resp, "output", "") or "", context)

    def cluster_relationships(self, relationships: pd.DataFrame) -> pd.DataFrame:
        from graphrag.index.operations.cluster_graph import cluster_graph

        cluster_fn = self._cluster_fn or cluster_graph
        communities = cluster_fn(edges=relationships, max_cluster_size=10, use_lcc=False)
        # communities: list[(level:int, cluster_id:int, parent:int, nodes:list[str])]
        return pd.DataFrame(
            [
                {
                    "level": level,
                    "community_id": str(cid),
                    "parent": str(parent),
                    "entity_ids": list(nodes),
                }
                for (level, cid, parent, nodes) in communities
            ]
        )

    def finalize_entities_relationships(
        self,
        entities: pd.DataFrame,
        relationships: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if self._finalize_fn is not None:
            return self._finalize_fn(entities, relationships)
        # Deterministic degree math, isomorphic with FakeGraphAdapter (no LLM).
        deg: dict[str, int] = {}
        for _, r in relationships.iterrows():
            deg[r["source"]] = deg.get(r["source"], 0) + 1
            deg[r["target"]] = deg.get(r["target"], 0) + 1
        e = entities.copy()
        if not e.empty:
            e["degree"] = e["title"].map(lambda t: deg.get(t, 0))
        rr = relationships.copy()
        if not rr.empty:
            rr["combined_degree"] = rr.apply(
                lambda r: deg.get(r["source"], 0) + deg.get(r["target"], 0), axis=1
            )
        return e, rr

    def embed_items(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via graphrag's configured embedding model."""
        if self._embed_factory is None:
            raise RuntimeError("embed_factory not configured")
        embedder = self._embed_factory()
        return embedder.embedding_batch(texts)


def _parse_report_json(text: str, context: dict) -> CommunityReport:
    """Best-effort parse of a plain-text JSON report into a CommunityReport.

    Extracts the first ``{...}`` block (regex) so leading/trailing prose is
    tolerated, maps ``rating`` (0-10) -> ``rank`` (0-1). Always returns a report
    (defaults on any parse failure) so one malformed community degrades, not crashes.
    """
    import json
    import re

    data: dict = {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            data = {}
    title = str(data.get("title") or f"Community {context['community']}")
    summary = str(data.get("summary") or title)
    findings = data.get("findings") or [summary]
    if not isinstance(findings, list):
        findings = [str(findings)]
    try:
        rank = float(data.get("rating", 0.0)) / 10.0
    except Exception:  # noqa: BLE001
        rank = 0.0
    rank = max(0.0, min(1.0, rank))
    return CommunityReport(
        title=title,
        summary=summary,
        findings=[str(f) for f in findings],
        rank=rank,
        full_content=str(data.get("full_content") or summary),
        level=int(context["level"]),
        community=str(context["community"]),
    )


def _format_community_context(context: dict) -> str:
    """Flatten a community context dict into the text fed to CommunityReportsExtractor."""
    ents = "\n".join(
        f"- {e['title']}: {e.get('description', '')}" for e in context.get("entities", [])
    )
    rels = "\n".join(f"- {r['source']} -> {r['target']}" for r in context.get("relationships", []))
    subs = "\n".join(
        f"- {s.get('title', s.get('community', ''))}: {s.get('summary', '')}"
        for s in context.get("sub_reports", [])
    )
    return (
        f"Community: {context['community']} (level {context['level']})\n"
        f"Entities:\n{ents}\nRelationships:\n{rels}\nSub-community reports:\n{subs}"
    )


def _build_embed_model_config(settings: dict):
    """Build an embedding ModelConfig from KB `embedding` settings, or None.

    Mirrors the LLM credential resolution in ``build_adapter_from_settings``.
    Providers without a key (ollama) get a placeholder api_key so graphrag-llm's
    ApiKey validator passes (litellm ignores it for ollama). A keyed provider
    whose key can't be resolved returns None -> embedding left unconfigured
    (build_default_adapter then falls back to the LLM model_config, whose own
    embedding creation is best-effort / optional).
    """
    emb = settings.get("embedding") or {}
    if not emb:
        return None
    import os

    from graphrag_llm.config import ModelConfig

    provider = emb.get("model_provider", "openai")
    resolved = (
        emb.get("api_key")
        or (os.getenv(emb["api_key_env"]) if emb.get("api_key_env") else None)
        or os.getenv(f"{provider.upper()}_API_KEY")
    )
    if not resolved:
        if provider == "ollama":
            resolved = "ollama"
        else:
            return None
    return ModelConfig(
        type=emb.get("type", "litellm"),
        model_provider=provider,
        model=emb.get("model", "text-embedding-3-small"),
        api_base=emb.get("api_base"),
        api_version=emb.get("api_version"),
        api_key=resolved,
    )


def build_default_adapter(
    *,
    data_root: str,
    model_config,
    embed_model_config=None,
    max_gleanings: int = 0,
) -> GraphRagAdapter:
    """Wire a GraphRagAdapter with a real graphrag chunker + LLM extractor."""
    # NOTE: graphrag_chunking/__init__.py is empty — import from submodules.
    from graphrag_chunking.chunking_config import ChunkingConfig
    from graphrag_chunking.chunk_strategy_type import ChunkerType
    from graphrag_chunking.chunker_factory import create_chunker
    from graphrag_llm.completion import create_completion
    from graphrag.tokenizer.get_tokenizer import get_tokenizer
    from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor
    from graphrag.index.operations.summarize_descriptions.description_summary_extractor import (
        SummarizeExtractor,
    )
    from graphrag.index.operations.summarize_communities.community_reports_extractor import (
        CommunityReportsExtractor,
    )
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT
    from graphrag.prompts.index.summarize_descriptions import SUMMARIZE_PROMPT
    from graphrag.prompts.index.community_report import COMMUNITY_REPORT_PROMPT
    from graphrag.config.defaults import DEFAULT_ENTITY_TYPES

    tokenizer = get_tokenizer(encoding_model="cl100k_base")
    chunker = create_chunker(
        ChunkingConfig(
            type=ChunkerType.Tokens, encoding_model="cl100k_base", size=1200, overlap=100
        ),
        encode=tokenizer.encode,
        decode=tokenizer.decode,
    )
    completion = create_completion(model_config)

    from kb_platform.graph.cost_capture import CostCapturingCompletion

    model_id = model_config.model
    completion = CostCapturingCompletion(completion, model_id=model_id)

    def extractor_factory() -> GraphExtractor:
        return GraphExtractor(
            model=completion, prompt=GRAPH_EXTRACTION_PROMPT, max_gleanings=max_gleanings
        )

    def summarize_factory() -> SummarizeExtractor:
        return SummarizeExtractor(
            model=completion,
            max_summary_length=500,
            max_input_tokens=32000,
            summarization_prompt=SUMMARIZE_PROMPT,
        )

    def report_factory() -> CommunityReportsExtractor:
        return CommunityReportsExtractor(
            model=completion,
            extraction_prompt=COMMUNITY_REPORT_PROMPT,
            max_report_length=2000,
        )

    try:
        from graphrag_llm.embedding import create_embedding

        embedder = create_embedding(embed_model_config or model_config)

        def embed_factory():
            return embedder

    except Exception:  # noqa: BLE001 — embedding optional (not every config supports it)
        embed_factory = None

    return GraphRagAdapter(
        chunker=chunker,
        extractor_factory=extractor_factory,
        entity_types=list(DEFAULT_ENTITY_TYPES),
        summarize_factory=summarize_factory,
        report_factory=report_factory,
        embed_factory=embed_factory,
        completion=completion,
    )


def build_adapter_from_settings(
    settings_json: str, data_root: str, api_key: str | None = None
) -> GraphRagAdapter:
    """Parse KB settings_json (graphrag settings subset) -> ModelConfig -> real adapter.

    Lenient: defaults to litellm/openai/gpt-4o-mini when keys are absent.

    Credential resolution (graphrag-llm's ModelConfig requires api_key at construction
    when auth_method=api_key, the default — it does not defer to litellm's env lookup):
      1. explicit ``api_key`` arg;
      2. ``llm.api_key`` literal in settings (not recommended — stored in DB);
      3. env var named by ``llm.api_key_env`` (e.g. "DEEPSEEK_API_KEY");
      4. env var ``{MODEL_PROVIDER}_API_KEY`` uppercased.
    The key is never persisted; prefer option 3/4 (env) so secrets stay out of the DB.
    """
    import json
    import os

    from graphrag_llm.config import ModelConfig

    settings = json.loads(settings_json or "{}")
    llm = settings.get("llm", {}) or settings.get("completion", {})
    provider = llm.get("model_provider", "openai")
    resolved_key = (
        api_key
        or llm.get("api_key")
        or (os.getenv(llm["api_key_env"]) if llm.get("api_key_env") else None)
        or os.getenv(f"{provider.upper()}_API_KEY")
    )
    model_config = ModelConfig(
        type=llm.get("type", "litellm"),
        model_provider=provider,
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=resolved_key,
    )
    embed_model_config = _build_embed_model_config(settings)
    return build_default_adapter(
        data_root=data_root,
        model_config=model_config,
        embed_model_config=embed_model_config,
    )
