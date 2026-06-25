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

from kb_platform.query.engine import QueryResult

logger = logging.getLogger(__name__)

# Methods that require community reports.
_REPORTS_REQUIRED = ("global", "drift")
_COMMUNITY_REPORTS_FILE = "community_reports.parquet"
_NO_REPORTS_MSG = "no community reports; re-index with a json_schema-capable model"

# graphrag canonical embedding names (graphrag.config.embeddings).
_ENTITY_DESCRIPTION = "entity_description"
_TEXT_UNIT_TEXT = "text_unit_text"


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

        entities_df = _read("entities.parquet")
        communities_df = _read("communities.parquet")
        reports_df = _read("community_reports.parquet")
        text_units_df = (
            _read("text_unit_ids.parquet")
            if os.path.exists(os.path.join(root, "text_unit_ids.parquet"))
            else _read("text_units.parquet")
        )
        relationships_df = _read("relationships.parquet")

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
        else:
            return QueryResult(
                answer="",
                method=method,
                error=f"unknown query method: {method}",
            )

        result = await engine.search(query=query)
        answer = getattr(result, "response", "") or ""
        if isinstance(answer, (list, dict)):
            answer = str(answer)
        return QueryResult(answer=answer, method=method)

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
        return GraphRagConfig.model_validate(values)

    def _build_embedding_store(self, config, embedding_name: str):
        """Open graphrag's LanceDB store for ``embedding_name``.

        ``config.vector_store.db_uri`` is forced to the indexing write path's
        ``<root>/vectors`` (see ``_resolve_config``), so the table opened here
        is exactly the one populated during indexing.
        """
        from graphrag.utils.api import get_embedding_store

        return get_embedding_store(config.vector_store, embedding_name)
