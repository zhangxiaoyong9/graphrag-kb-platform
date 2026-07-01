"""Real GraphAdapter backed by graphrag primitives. The ONLY module that imports graphrag internals."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from kb_platform.graph.adapter import ChunkText, CommunityReport, ExtractionResult, _hash

# Texts per embedding API call. A single embedding(input=[whole collection])
# overwhelms providers — verified against Ollama: ~hundreds OK, 2000 texts ->
# 400 Bad Request / 58s hang at /api/embed. Keep batches small so each call
# finishes well under any HTTP timeout.
_EMBED_BATCH_SIZE = 64


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
        max_cluster_size: int = 10,
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
        self._max_cluster_size = max_cluster_size

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
        communities = cluster_fn(
            edges=relationships, max_cluster_size=self._max_cluster_size, use_lcc=False
        )
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
        """Embed texts via graphrag-llm's configured embedding model, in batches.

        graphrag-llm's LiteLLMEmbedding.embedding takes ``input=list[str]``
        (LLMEmbeddingArgs) and returns an LLMEmbeddingResponse whose
        ``.embeddings`` is the list of vectors in input order.

        The input is chunked to ``_EMBED_BATCH_SIZE`` per call: passing the
        whole collection at once overwhelms providers (e.g. Ollama's
        ``/api/embed`` returns 400 / times out past ~hundreds of texts), so we
        batch and concatenate the vectors in input order.
        """
        if self._embed_factory is None:
            raise RuntimeError("embed_factory not configured")
        if not texts:
            return []
        embedder = self._embed_factory()
        out: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start : start + _EMBED_BATCH_SIZE]
            out.extend(embedder.embedding(input=batch).embeddings)
        return out


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


def _raise_on_error(err: BaseException | None, _trace: str | None, _data: dict | None) -> None:
    """graphrag extractor on_error hook: re-raise the LLM failure so the
    platform's run_unit catches it and marks the unit FAILED.

    graphrag's extractors default on_error to a no-op and return empty on any
    exception, which hides auth/network failures as fake successes. This makes
    them propagate. (Only the LLM call is wrapped by graphrag's try/except;
    parse errors already propagate.)
    """
    if err is not None:
        raise err


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
    ApiKey validator passes. A keyed provider whose key can't be resolved returns
    None -> embedding left unconfigured (build_default_adapter then falls back to
    the LLM model_config, whose own embedding creation is best-effort / optional).

    Embeddings route through the native gateway too (T10 spec): ``type=kb_native``
    + a single-element ``kb_profiles`` bundle so ``create_embedding`` returns a
    ``NativeEmbedding`` with within-profile key round-robin.
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
    # All embedding keys round-robin inside NativeEmbedding; api_keys is the
    # full decrypted list (assemble_kb_settings packs it). Fall back to a
    # single-element list from the resolved primary key for the env-var path.
    emb_keys = list(emb.get("api_keys") or [resolved])
    ssl_verify = emb.get("ssl_verify", True)
    return ModelConfig(
        type="kb_native",
        model_provider=provider,
        model=emb.get("model", "text-embedding-3-small"),
        api_base=emb.get("api_base"),
        api_version=emb.get("api_version"),
        api_key=emb_keys[0],
        call_args={"ssl_verify": ssl_verify},
        kb_profiles=[
            {
                "provider": provider,
                "model": emb.get("model", "text-embedding-3-small"),
                "api_base": emb.get("api_base"),
                "api_version": emb.get("api_version"),
                "keys": emb_keys,
                "ssl_verify": ssl_verify,
            }
        ],
    )


def build_default_adapter(
    *,
    data_root: str,
    model_config,
    embed_model_config=None,
    chunk_size: int = 1200,
    chunk_overlap: int = 100,
    encoding_model: str = "cl100k_base",
    chunk_strategy: str = "tokens",
    max_cluster_size: int = 10,
    entity_types=None,
    max_gleanings: int = 0,
    summarize_max_length: int = 500,
    summarize_max_input_tokens: int = 32000,
    report_max_length: int = 2000,
    extra_api_keys: list[str] | None = None,
    extract_prompt: str | None = None,
    summarize_prompt: str | None = None,
    community_report_prompt: str | None = None,
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

    tokenizer = get_tokenizer(encoding_model=encoding_model)
    if chunk_strategy == "markdown":
        # Structure-aware: never cuts inside a sentence / table row. Zero graphrag
        # imports in the chunker module; tokenizer injected like TokenChunker.
        from kb_platform.graph.markdown_chunker import MarkdownChunker

        chunker = MarkdownChunker(
            size=chunk_size, encode=tokenizer.encode, decode=tokenizer.decode
        )
    else:
        chunker = create_chunker(
            ChunkingConfig(
                type=ChunkerType.Tokens,
                encoding_model=encoding_model,
                size=chunk_size,
                overlap=chunk_overlap,
            ),
            encode=tokenizer.encode,
            decode=tokenizer.decode,
        )
    from kb_platform.graph.cost_capture import CostCapturingCompletion

    model_id = model_config.model

    # Production configs (litellm/openai/kb_native) route through the native
    # gateway: all keys round-robin INSIDE the gateway, so LoadBalancingCompletion
    # is retired (T10). Test configs (type="mock") are passed through verbatim so
    # graphrag's MockLLMCompletion keeps supplying deterministic responses.
    if model_config.type in {"mock"}:
        completion = create_completion(model_config)
        completion = CostCapturingCompletion(completion, model_id=model_id)
    else:
        from kb_platform.llm.registry import register_native

        register_native()
        from graphrag_llm.config import ModelConfig as _MC

        existing_extra = getattr(model_config, "model_extra", None) or {}
        existing_profiles = existing_extra.get("kb_profiles")
        if existing_profiles:
            # Caller already packed a kb_profiles bundle (e.g. test fixture or a
            # downstream caller that resolved profiles itself). Trust it verbatim.
            kb_cfg = _MC(
                type="kb_native",
                model_provider=model_config.model_provider,
                model=model_config.model,
                api_base=model_config.api_base,
                api_version=model_config.api_version,
                api_key=model_config.api_key,
                call_args=dict(model_config.call_args or {}),
                kb_profiles=list(existing_profiles),
            )
        else:
            # Primary key first, then extras; the gateway round-robins across all.
            all_keys = [model_config.api_key, *list(extra_api_keys or [])]
            kb_cfg = _MC(
                type="kb_native",
                model_provider=model_config.model_provider,
                model=model_config.model,
                api_base=model_config.api_base,
                api_version=model_config.api_version,
                api_key=all_keys[0],
                call_args=dict(model_config.call_args or {}),
                kb_profiles=[
                    {
                        "provider": model_config.model_provider,
                        "model": model_config.model,
                        "api_base": model_config.api_base,
                        "api_version": model_config.api_version,
                        "keys": all_keys,
                        "ssl_verify": (model_config.call_args or {}).get("ssl_verify", True),
                    }
                ],
            )
        completion = create_completion(kb_cfg)
        completion = CostCapturingCompletion(completion, model_id=model_id)

    def extractor_factory() -> GraphExtractor:
        return GraphExtractor(
            model=completion,
            prompt=extract_prompt or GRAPH_EXTRACTION_PROMPT,
            max_gleanings=max_gleanings,
            on_error=_raise_on_error,
        )

    def summarize_factory() -> SummarizeExtractor:
        return SummarizeExtractor(
            model=completion,
            max_summary_length=summarize_max_length,
            max_input_tokens=summarize_max_input_tokens,
            summarization_prompt=summarize_prompt or SUMMARIZE_PROMPT,
            on_error=_raise_on_error,
        )

    def report_factory() -> CommunityReportsExtractor:
        return CommunityReportsExtractor(
            model=completion,
            extraction_prompt=community_report_prompt or COMMUNITY_REPORT_PROMPT,
            max_report_length=report_max_length,
            on_error=_raise_on_error,
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
        entity_types=entity_types or list(DEFAULT_ENTITY_TYPES),
        summarize_factory=summarize_factory,
        report_factory=report_factory,
        embed_factory=embed_factory,
        completion=completion,
        max_cluster_size=max_cluster_size,
    )


def build_adapter_from_settings(
    settings_json: str, data_root: str, api_key: str | None = None
) -> GraphRagAdapter:
    """Parse KB settings_json (graphrag settings subset) -> ModelConfig -> real adapter.

    The settings dict carries a ``kb_profiles`` bundle (packed by
    ``assemble_kb_settings`` from the KB's provider profiles) that
    ``build_default_adapter`` forwards verbatim into the native gateway — all
    keys round-robin inside the gateway, so ``LoadBalancingCompletion`` is gone.
    Raises ValueError when no key is configured.
    """
    import json

    from graphrag_llm.config import ModelConfig

    settings = json.loads(settings_json or "{}")
    llm = settings.get("llm", {}) or settings.get("completion", {})
    provider = llm.get("model_provider", "openai")
    # API keys come from the KB's LLM provider profile (frontend-entered,
    # Fernet-encrypted, decrypted by assemble_kb_settings).
    api_keys = list(api_key and [api_key] or llm.get("api_keys") or [])
    if not api_keys:
        raise ValueError(
            f"KB has no API keys for provider '{provider}'. "
            "Add keys to its LLM provider profile."
        )
    resolved_key = api_keys[0]
    kb_profiles = llm.get("kb_profiles") or [
        {
            "provider": provider,
            "model": llm.get("model", "gpt-4o-mini"),
            "api_base": llm.get("api_base"),
            "api_version": llm.get("api_version"),
            "keys": api_keys,
            "ssl_verify": llm.get("ssl_verify", True),
        }
    ]
    model_config = ModelConfig(
        type=llm.get("type", "kb_native"),
        model_provider=provider,
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=resolved_key,
        call_args={"ssl_verify": llm.get("ssl_verify", True)},
        kb_profiles=kb_profiles,
    )
    chunking = settings.get("chunking") or {}
    cluster_graph = settings.get("cluster_graph") or {}
    extract_graph = settings.get("extract_graph") or {}
    summarize = settings.get("summarize_descriptions") or {}
    reports = settings.get("community_reports") or {}

    et = extract_graph.get("entity_types")
    if isinstance(et, str):
        et = [t.strip() for t in et.split(",") if t.strip()]

    embed_model_config = _build_embed_model_config(settings)

    return build_default_adapter(
        data_root=data_root,
        model_config=model_config,
        embed_model_config=embed_model_config,
        chunk_size=chunking.get("size", 1200),
        chunk_strategy=chunking.get("strategy", "tokens"),
        chunk_overlap=chunking.get("overlap", 100),
        encoding_model=chunking.get("encoding_model", "cl100k_base"),
        max_cluster_size=cluster_graph.get("max_cluster_size", 10),
        entity_types=et,
        max_gleanings=extract_graph.get("max_gleanings", 0),
        summarize_max_length=summarize.get("max_length", 500),
        summarize_max_input_tokens=summarize.get("max_input_tokens", 32000),
        report_max_length=reports.get("max_length", 2000),
        extract_prompt=extract_graph.get("prompt"),
        summarize_prompt=summarize.get("prompt"),
        community_report_prompt=reports.get("prompt"),
    )


def assemble_kb_settings(kb, repo) -> dict:
    """Resolve a KB's provider profiles + content settings -> full settings dict.

    This is the seam above graphrag: it merges the KB's referenced LLM/embedding
    provider profiles (connection + decrypted keys + structured_output) with the
    KB's own content params (chunking/extraction/prompts/lengths), producing the
    settings dict that ``build_adapter_from_settings`` consumes.
    """
    import json

    from kb_platform.db.crypto import decrypt_values

    if kb.llm_profile_id is None:
        raise ValueError("KB has no LLM provider profile.")
    lp = repo.get_profile(kb.llm_profile_id)
    api_keys = decrypt_values(lp.api_keys_enc)
    if not api_keys:
        raise ValueError(f"LLM profile '{lp.name}' has no API keys.")
    content = json.loads(kb.settings_json or "{}")
    reports = content.get("community_reports") or {}

    # Primary profile dict first, then one dict per declared fallback id, in
    # failover order. The gateway (T14) consumes the whole list; downstream code
    # still reads the PRIMARY keys from llm.api_keys. Missing profiles or empty
    # keys raise ValueError rather than silently degrade.
    primary_profile = {
        "provider": lp.provider,
        "model": lp.model,
        "api_base": lp.api_base,
        "api_version": lp.api_version,
        "keys": api_keys,
        "ssl_verify": lp.ssl_verify,
    }
    kb_profiles = [primary_profile]
    fallback_ids = json.loads(kb.llm_fallback_profile_ids or "[]")
    for fid in fallback_ids:
        fp = repo.get_profile(fid)
        if fp is None:
            raise ValueError(
                f"fallback provider profile {fid} not found (kb={kb.id})"
            )
        fk = decrypt_values(fp.api_keys_enc)
        if not fk:
            raise ValueError(
                f"fallback provider profile '{fp.name}' (id={fid}) has no API keys"
            )
        kb_profiles.append({
            "provider": fp.provider,
            "model": fp.model,
            "api_base": fp.api_base,
            "api_version": fp.api_version,
            "keys": fk,
            "ssl_verify": fp.ssl_verify,
        })

    assembled = {
        "llm": {
            "type": "kb_native",
            "model_provider": lp.provider,
            "model": lp.model,
            "api_base": lp.api_base,
            "api_version": lp.api_version,
            "api_keys": api_keys,
            "ssl_verify": lp.ssl_verify,
            "kb_profiles": kb_profiles,
        },
        "chunking": content.get("chunking", {}),
        "extract_graph": content.get("extract_graph", {}),
        "summarize_descriptions": content.get("summarize_descriptions", {}),
        "cluster_graph": content.get("cluster_graph", {}),
        "community_reports": {
            "structured_output": lp.structured_output,
            "max_length": reports.get("max_length", 2000),
        },
    }
    if content.get("prompts"):
        assembled["prompts"] = content["prompts"]
    if content.get("query_prompts"):
        assembled["query_prompts"] = content["query_prompts"]
    if kb.embedding_profile_id is not None:
        ep = repo.get_profile(kb.embedding_profile_id)
        emb_keys = decrypt_values(ep.api_keys_enc)
        assembled["embedding"] = {
            "type": "kb_native",
            "model_provider": ep.provider,
            "model": ep.model,
            "api_base": ep.api_base,
            "api_version": ep.api_version,
            "api_key": emb_keys[0] if emb_keys else None,
            "api_keys": emb_keys,
            "ssl_verify": ep.ssl_verify,
            "kb_profiles": [
                {
                    "provider": ep.provider,
                    "model": ep.model,
                    "api_base": ep.api_base,
                    "api_version": ep.api_version,
                    "keys": emb_keys,
                    "ssl_verify": ep.ssl_verify,
                }
            ],
        }
    return assembled


def build_adapter_for_kb(kb, repo):
    """Build a real GraphRagAdapter for a KB by resolving its profiles first."""
    import json

    return build_adapter_from_settings(json.dumps(assemble_kb_settings(kb, repo)), kb.data_root)


@dataclass
class ChatTurn:
    """Result of a one-shot chat completion (text + token usage)."""

    text: str
    prompt_tokens: int
    output_tokens: int


def build_chat_complete(settings: dict):
    """Build an ``async (system, user) -> ChatTurn`` callable from resolved KB settings.

    This is the ONE place the conversation layer's rewriter needs graphrag-llm:
    it constructs a completion from the KB's resolved ``llm`` block (the same
    credential path as the indexing/query engines) and returns a thin callable.
    Callers (the conversation package) never import graphrag. Raises ValueError
    when the settings carry no ``llm.api_keys``.
    """
    from graphrag_llm.completion import create_completion
    from graphrag_llm.config import ModelConfig

    from kb_platform.llm.registry import register_native

    register_native()
    llm = (settings or {}).get("llm") or {}
    api_keys = list(llm.get("api_keys") or [])
    if not api_keys:
        raise ValueError("KB has no LLM API keys for the query rewriter.")
    provider = llm.get("model_provider", "openai")
    model = llm.get("model", "gpt-4o-mini")
    ssl_verify = llm.get("ssl_verify", True)
    kb_profiles = llm.get("kb_profiles") or [
        {
            "provider": provider,
            "model": model,
            "api_base": llm.get("api_base"),
            "api_version": llm.get("api_version"),
            "keys": api_keys,
            "ssl_verify": ssl_verify,
        }
    ]
    model_config = ModelConfig(
        type="kb_native",
        model_provider=provider,
        model=model,
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=api_keys[0],
        call_args={"ssl_verify": ssl_verify},
        kb_profiles=kb_profiles,
    )
    completion = create_completion(model_config)

    async def complete(system: str, user: str) -> ChatTurn:
        resp = await completion.completion_async(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = getattr(resp, "content", "") or ""
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        return ChatTurn(text=text, prompt_tokens=pt, output_tokens=ct)

    return complete
