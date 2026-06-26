"""Real QueryEngine wrapping graphrag's search engines. Graphrag-coupling.

This module is the single seam where kb_platform reaches into graphrag's
query API (``graphrag.query.factory`` + ``graphrag.query.indexer_adapters``).

Verified against graphrag source (sibling ``graphrag`` repo, v3.1.0):
- ``graphrag.query.factory.get_local_search_engine`` / ``get_global_search_engine``
  / ``get_drift_search_engine`` / ``get_basic_search_engine`` build engines.
- ``graphrag.query.indexer_adapters.read_indexer_*`` adapt parquet DataFrames
  into graphrag's object model.
- Each engine exposes ``async def search(query, ...) -> SearchResult`` where
  ``SearchResult.response`` holds the answer text.
- Embedding stores are opened via ``graphrag.utils.api.get_embedding_store``
  which takes ``(config.vector_store, embedding_name)`` and resolves the
  LanceDB table name from ``config.vector_store.index_schema[embedding_name]``;
  the default table name == embedding name (``entity_description``,
  ``text_unit_text``, ``community_full_content``), which is exactly what the
  indexing write path (``generate_text_embeddings``) produces. Alignment is
  therefore: read path ``vector_store.db_uri`` == write path ``data_root/vectors``.

The graphrag factories require a fully constructed ``GraphRagConfig`` plus a
populated ``VectorStore`` for embeddings, both of which depend on the runtime
environment (settings.yaml + a configured embedder). The
``_run_graphrag_search`` path therefore wires everything it can from the
index parquet files on disk and the caller-supplied ``model_config`` (a
``GraphRagConfig`` or settings dict); any failure is caught and surfaced as a
``QueryResult.error`` so the API never 500s on a partial/missing index.
"""

import logging
import os

from kb_platform.query.engine import QueryResult, SourceRef

logger = logging.getLogger(__name__)

# Methods that require community reports.
_REPORTS_REQUIRED = ("global", "drift")
_COMMUNITY_REPORTS_FILE = "community_reports.parquet"
_NO_REPORTS_MSG = "no community reports; re-index with a json_schema-capable model"

# graphrag canonical embedding names (graphrag.config.embeddings).
_ENTITY_DESCRIPTION = "entity_description"
_TEXT_UNIT_TEXT = "text_unit_text"
_COMMUNITY_FULL_CONTENT = "community_full_content"


class _StreamFixWrapper:
    """Wraps a graphrag-llm completion so completion_async(stream=True) returns an
    async generator directly (NOT a coroutine).

    graphrag's BasicSearch assigns ``model.completion_async(stream=True)`` to a
    variable WITHOUT ``await``, then does ``async for chunk in it``. But
    graphrag-llm's ``completion_async`` is ``async def`` → returns a coroutine,
    not an async iterator → ``TypeError: 'async for' requires __aiter__``.

    This wrapper makes ``completion_async`` a regular method: for streaming calls
    it returns an async generator (awaitable + async-iterable); for non-streaming
    calls it returns the inner coroutine (graphrag awaits those correctly).
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def completion_async(self, **kwargs):
        if kwargs.get("stream"):
            return self._stream(**kwargs)
        return self._inner.completion_async(**kwargs)

    async def _stream(self, **kwargs):
        resp = await self._inner.completion_async(**kwargs)
        if hasattr(resp, "__aiter__"):
            async for chunk in resp:
                yield chunk
        else:
            yield resp

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _join_cell(value) -> str:
    """Flatten a parquet cell (str / list / numpy array) into a single string.

    The platform's merge step aggregates ``description`` as a list; parquet
    round-trips it as a numpy array. graphrag's readers call ``to_optional_str``
    on ``description`` (which would otherwise stringify the list repr).
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    try:
        import numpy as np  # noqa: PLC0415

        if isinstance(value, np.ndarray):
            return " ".join(str(v) for v in value.tolist())
    except Exception:  # noqa: BLE001
        pass
    return str(value)


def _ensure_col(df, col: str, values):
    """Set ``df[col]`` to ``values`` only when the column is absent."""
    if col not in df.columns:
        df[col] = values
    return df


def _norm_entities(df):
    """Make the platform entities parquet match graphrag's reader schema.

    graphrag keys entities by ``id``; the platform uses ``title`` as identity,
    so ``id`` := ``title`` (which also aligns with ``communities.entity_ids``,
    since clustering groups by title). ``description`` is flattened from a
    list to a string. ``human_readable_id`` is required by ``to_optional_str``
    even though it is cosmetic; synthesize a stable int.
    """
    df = df.copy()
    if "id" not in df.columns and "title" in df.columns:
        df["id"] = df["title"].astype(str)
    df = _ensure_col(df, "human_readable_id", range(len(df)))
    df = _ensure_col(df, "type", [""] * len(df))
    if "description" in df.columns:
        df["description"] = [_join_cell(v) for v in df["description"]]
    df = _ensure_col(df, "degree", [0] * len(df))
    return df


def _norm_relationships(df):
    df = df.copy()
    if "id" not in df.columns:
        df["id"] = [f"{s}->{t}" for s, t in zip(df["source"], df["target"], strict=False)]
    df = _ensure_col(df, "human_readable_id", range(len(df)))
    if "description" in df.columns:
        df["description"] = [_join_cell(v) for v in df["description"]]
    return df


def _to_int_comm(value):
    """Coerce a community id to int (graphrag merges communities↔reports on the
    int-typed ``community`` column). Platform ids are numeric strings like "0".
    Non-numeric ids fall back to a stable hash so the merge key stays consistent
    across the communities and reports frames."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return abs(hash(str(value))) % (2**31)


def _norm_communities(df):
    """The platform stores the community id as ``community_id``; graphrag's
    readers (and the reports merge) key on an int ``community``. ``id`` mirrors
    it, and ``title``/``children`` are required (``children`` must be a list)."""
    df = df.copy()
    if "community" not in df.columns and "community_id" in df.columns:
        df["community"] = df["community_id"]
    if "community" in df.columns:
        df["community"] = [_to_int_comm(c) for c in df["community"]]
        df = _ensure_col(df, "id", df["community"].astype(str))
        df = _ensure_col(df, "title", [f"Community {c}" for c in df["community"]])
    df = _ensure_col(df, "children", [[] for _ in range(len(df))])
    return df


def _norm_reports(df):
    """graphrag keys reports by ``id`` and merges them with communities on the
    int ``community`` column; the platform keys reports by a (string) community."""
    df = df.copy()
    if "community" in df.columns:
        df["community"] = [_to_int_comm(c) for c in df["community"]]
    if "id" not in df.columns and "community" in df.columns:
        df["id"] = df["community"].astype(str)
    if "full_content" not in df.columns and "summary" in df.columns:
        df["full_content"] = df["summary"].astype(str)
    return df


def _norm_text_units(df):
    """graphrag's reader requires a scalar ``document_id`` (to_optional_str);
    the platform stores ``document_ids`` as a list — take the first."""
    df = df.copy()
    if "document_id" not in df.columns and "document_ids" in df.columns:
        df["document_id"] = [
            (str(d[0]) if (hasattr(d, "__len__") and len(d) > 0) else "") or str(d)
            for d in df["document_ids"]
        ]
    df = _ensure_col(df, "document_id", [""] * len(df))
    return df


def _is_df(value) -> bool:
    try:
        import pandas as pd  # noqa: PLC0415

        return isinstance(value, pd.DataFrame)
    except Exception:  # noqa: BLE001
        return False


def _first(row, cols) -> str:
    for c in cols:
        if c in row.index and row[c] not in (None, ""):
            return str(row[c])
    return ""


class GraphRagQueryEngine:
    """QueryEngine backed by graphrag's four search engines.

    Parameters
    ----------
    data_root:
        Default root directory holding the graphrag index parquet files. May
        be overridden per-query via ``kb_data_root``.
    model_config:
        Either a ``graphrag.config.models.graph_rag_config.GraphRagConfig``
        instance or a settings dict used to build one. ``None`` is accepted
        for construction (e.g. in tests); the real search path will then fail
        gracefully when a config is actually required.
    """

    def __init__(self, data_root: str, model_config) -> None:
        self._data_root = data_root
        self._model_config = model_config

    async def search(self, method: str, query: str, kb_data_root: str) -> QueryResult:
        root = self._data_root or kb_data_root

        # Guard: global / drift need community reports produced during indexing.
        if method in _REPORTS_REQUIRED and not os.path.exists(
            os.path.join(root, _COMMUNITY_REPORTS_FILE)
        ):
            return QueryResult(answer="", method=method, error=_NO_REPORTS_MSG)

        try:
            return await self._run_graphrag_search(method, query, root)
        except Exception as e:  # noqa: BLE001 - surface as QueryResult.error
            logger.exception("graphrag search failed for method=%s", method)
            return QueryResult(answer="", method=method, error=str(e))

    async def _run_graphrag_search(self, method: str, query: str, root: str) -> QueryResult:
        """Best-effort real graphrag search.

        Loads index parquet files, adapts them via
        ``graphrag.query.indexer_adapters``, constructs the appropriate engine
        via ``graphrag.query.factory``, and awaits ``engine.search(query)``.

        Failures (missing parquet, unconfigured LLM/embedder, etc.) are caught
        by the caller and returned as ``QueryResult.error``.
        """
        import pandas as pd

        from graphrag.query.factory import (
            get_basic_search_engine,
            get_drift_search_engine,
            get_global_search_engine,
            get_local_search_engine,
        )
        from graphrag.query.indexer_adapters import (
            read_indexer_communities,
            read_indexer_entities,
            read_indexer_relationships,
            read_indexer_reports,
            read_indexer_text_units,
        )

        config = self._resolve_config(root=root)
        community_level = 2
        response_type = "multiple paragraphs"

        def _read(name: str) -> pd.DataFrame:
            path = os.path.join(root, name)
            if not os.path.exists(path):
                raise FileNotFoundError(f"missing index artifact: {name} under {root}")
            return pd.read_parquet(path)

        entities_df = _norm_entities(_read("entities.parquet"))
        communities_df = _norm_communities(_read("communities.parquet"))
        reports_df = _norm_reports(_read("community_reports.parquet"))
        text_units_df = _norm_text_units(
            _read("text_unit_ids.parquet")
            if os.path.exists(os.path.join(root, "text_unit_ids.parquet"))
            else _read("text_units.parquet")
        )
        relationships_df = _norm_relationships(_read("relationships.parquet"))

        communities = read_indexer_communities(communities_df, reports_df)
        reports = read_indexer_reports(
            reports_df,
            communities_df,
            community_level=community_level,
        )
        entities = read_indexer_entities(
            entities_df, communities_df, community_level=community_level
        )
        relationships = read_indexer_relationships(relationships_df)
        text_units = read_indexer_text_units(text_units_df)

        if method == "local":
            store = self._build_embedding_store(config, _ENTITY_DESCRIPTION)
            engine = get_local_search_engine(
                config,
                reports=reports,
                text_units=text_units,
                entities=entities,
                relationships=relationships,
                covariates={},
                response_type=response_type,
                description_embedding_store=store,
            )
        elif method == "global":
            engine = get_global_search_engine(
                config,
                reports=reports,
                entities=entities,
                communities=communities,
                response_type=response_type,
            )
        elif method == "drift":
            store = self._build_embedding_store(config, _ENTITY_DESCRIPTION)
            # drift needs community report full_content embeddings — populate them
            # from the community_full_content LanceDB table before constructing the engine
            # (graphrag's read_indexer_report_embeddings does the lookup by report.id).
            report_store = self._build_embedding_store(config, _COMMUNITY_FULL_CONTENT)
            from graphrag.query.indexer_adapters import read_indexer_report_embeddings
            read_indexer_report_embeddings(reports, report_store)
            engine = get_drift_search_engine(
                config,
                reports=reports,
                text_units=text_units,
                entities=entities,
                relationships=relationships,
                description_embedding_store=store,
                response_type=response_type,
            )
        elif method == "basic":
            store = self._build_embedding_store(config, _TEXT_UNIT_TEXT)
            engine = get_basic_search_engine(
                text_units=text_units,
                text_unit_embeddings=store,
                config=config,
                response_type=response_type,
            )
            # graphrag's BasicSearch calls model.completion_async(stream=True) WITHOUT
            # await, expecting an async iterator directly. graphrag-llm returns a
            # coroutine → TypeError. Wrap the model so streaming returns an async gen.
            engine.model = _StreamFixWrapper(engine.model)
        else:
            return QueryResult(
                answer="",
                method=method,
                error=f"unknown query method: {method}",
            )

        result = await engine.search(query=query)
        return self._result_from_search(method, result)

    def _result_from_search(self, method: str, search_result) -> QueryResult:
        """Map a graphrag SearchResult into an enriched QueryResult."""
        answer = getattr(search_result, "response", "") or ""
        if isinstance(answer, (list, dict)):
            answer = str(answer)
        return QueryResult(
            answer=answer,
            method=method,
            elapsed_ms=round(float(getattr(search_result, "completion_time", 0.0) or 0.0) * 1000, 1),
            prompt_tokens=int(getattr(search_result, "prompt_tokens", 0) or 0) or None,
            output_tokens=int(getattr(search_result, "output_tokens", 0) or 0) or None,
            llm_calls=int(getattr(search_result, "llm_calls", 0) or 0) or None,
            sources=self._extract_sources(getattr(search_result, "context_data", None), method),
        )

    def _resolve_config(self, root: str | None = None):
        """Return a GraphRagConfig from the supplied model_config.

        If ``root`` is given, force ``vector_store.db_uri`` to
        ``<root>/vectors`` so the read path opens the LanceDB tables the
        indexing write path produced (see ``generate_text_embeddings``).
        """
        from graphrag.config.models.graph_rag_config import GraphRagConfig

        cfg = self._model_config
        if isinstance(cfg, GraphRagConfig):
            return cfg

        if cfg is None:
            raise RuntimeError("no graphrag model_config provided; cannot run real search")

        values = dict(cfg)
        if root is not None:
            vs = dict(values.get("vector_store") or {})
            vs["type"] = "lancedb"
            vs["db_uri"] = os.path.join(root, "vectors")
            values["vector_store"] = vs
        # graphrag's query factory resolves the LLM via
        # config.completion_models["default_completion_model"]; the KB settings
        # only carry `llm.*`, so derive the default completion model from it
        # (with the same credential resolution as the indexing path — see
        # build_adapter_from_settings in graphrag_adapter.py).
        if not values.get("completion_models"):
            llm = dict(values.get("llm") or {})
            if llm:
                provider = llm.get("model_provider", "openai")
                resolved_key = (
                    llm.get("api_key")
                    or (os.getenv(llm["api_key_env"]) if llm.get("api_key_env") else None)
                    or os.getenv(f"{provider.upper()}_API_KEY")
                )
                entry = {
                    "type": llm.get("type", "litellm"),
                    "model_provider": provider,
                    "model": llm.get("model", "gpt-4o-mini"),
                    "api_base": llm.get("api_base"),
                    "api_version": llm.get("api_version"),
                }
                if resolved_key:
                    entry["api_key"] = resolved_key
                # graphrag validates api_key as required; only inject when a key
                # was resolved, otherwise leave unset and let graphrag surface
                # its own "not configured" error (per Task 12 review guidance).
                if "api_key" in entry:
                    values["completion_models"] = {"default_completion_model": entry}
        # graphrag's query factory also resolves embedding models via
        # config.embedding_models["default_embedding_model"] for vector-based
        # methods (local/basic/drift). Derive it from the KB `embedding`
        # settings when present (same credential resolution as above). Do NOT
        # fall back to `llm` — a chat model is not an embedding model; if no
        # embedding settings are configured, leave it unset so graphrag raises
        # its own honest "not configured" error.
        if not values.get("embedding_models"):
            emb = dict(values.get("embedding") or {})
            if emb:
                provider = emb.get("model_provider", "openai")
                resolved_key = (
                    emb.get("api_key")
                    or (os.getenv(emb["api_key_env"]) if emb.get("api_key_env") else None)
                    or os.getenv(f"{provider.upper()}_API_KEY")
                )
                entry = {
                    "type": emb.get("type", "litellm"),
                    "model_provider": provider,
                    "model": emb.get("model", "text-embedding-3-small"),
                    "api_base": emb.get("api_base"),
                    "api_version": emb.get("api_version"),
                }
                if resolved_key:
                    entry["api_key"] = resolved_key
                if "api_key" in entry:
                    values["embedding_models"] = {"default_embedding_model": entry}
        return GraphRagConfig.model_validate(values)

    def _build_embedding_store(self, config, embedding_name: str):
        """Open graphrag's LanceDB store for ``embedding_name``.

        ``config.vector_store.db_uri`` is forced to the indexing write path's
        ``<root>/vectors`` (see ``_resolve_config``), so the table opened here
        is exactly the one populated during indexing.
        """
        from graphrag.utils.api import get_embedding_store

        return get_embedding_store(config.vector_store, embedding_name)

    def _extract_sources(self, context_data, method: str, limit: int = 4):
        """Best-effort extraction of source entities + text snippets from a
        graphrag SearchResult.context_data.

        - dict[str, DataFrame]: "entities" -> entity name+description; the
          first text-bearing frame -> text_unit snippets.
        - str: wrapped as a single text_unit source.
        Anything else / any failure -> None (never raises; never blocks the
        answer).
        """
        try:
            sources: list[SourceRef] = []
            if isinstance(context_data, dict):
                ents = context_data.get("entities")
                if _is_df(ents):
                    for _, row in ents.head(limit).iterrows():
                        name = _first(row, ("name", "title", "id"))
                        if not name:
                            continue
                        desc = str(row.get("description", "") or "")[:200]
                        sources.append(SourceRef("entity", name, desc))
                for _key, df in context_data.items():
                    if not _is_df(df) or "text" not in df.columns:
                        continue
                    for _, row in df.head(limit).iterrows():
                        txt = str(row.get("text", "") or "")
                        if not txt.strip():
                            continue
                        sources.append(SourceRef("text_unit", str(row.get("id", _key)), txt[:200]))
                    break
            elif isinstance(context_data, str) and context_data.strip():
                sources.append(SourceRef("text_unit", "context", context_data.strip()[:200]))
            return sources or None
        except Exception:  # noqa: BLE001 - sources are a nice-to-have
            logger.exception("source extraction failed")
            return None
