"""VectorStore seam: Fake + LanceDB wrappers.

The platform writes embeddings through the ``VectorStore`` Protocol during
indexing (``generate_text_embeddings``). The production read path
(``GraphRagQueryEngine``) does NOT use this seam — it opens its own store via
graphrag's ``get_embedding_store`` and runs ``similarity_search_by_vector``
directly — so the wrappers here only need to cover the write side
(``connect`` / ``upsert``); ``query`` is exercised solely by the in-memory
``FakeVectorStore`` in engine tests.
"""

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)


class VectorStore(Protocol):
    def connect(self) -> None: ...
    def upsert(self, index_name: str, items: list[dict]) -> None: ...
    def query(self, index_name: str, text: str, k: int) -> list[dict]: ...


class FakeVectorStore:
    """In-memory deterministic vector store for tests."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim
        self._store: dict[str, list[dict]] = {}

    def connect(self) -> None:
        pass

    def upsert(self, index_name: str, items: list[dict]) -> None:
        self._store.setdefault(index_name, []).extend(items)

    def query(self, index_name: str, text: str, k: int) -> list[dict]:
        items = self._store.get(index_name, [])
        # Deterministic: return first k items by insertion order.
        return [{"id": it["id"], "score": 1.0 - i * 0.1} for i, it in enumerate(items[:k])]


class LanceDBVectorStoreWrapper:
    """Persistent VectorStore backed by graphrag's LanceDB store.

    Writes ``id`` + ``vector`` to ``<db_uri>/<index_name>.lance`` — the exact
    table graphrag's query read path opens via
    ``get_embedding_store(config.vector_store, index_name)`` when
    ``config.vector_store.db_uri == db_uri`` and ``type == "lancedb"``.

    The table name (``index_name``) MUST be one of graphrag's canonical
    embedding names — ``entity_description``, ``text_unit_text``,
    ``community_full_content`` (see ``graphrag.config.embeddings``) — so the
    read path finds it. ``generate_text_embeddings`` is the sole caller and
    uses those names.

    Re-indexing replaces stale vectors rather than accumulating duplicates:
    ``create_index`` opens the table with ``mode="overwrite"``.
    """

    def __init__(self, db_uri: str) -> None:
        self.db_uri = str(db_uri)

    def connect(self) -> None:
        os.makedirs(self.db_uri, exist_ok=True)

    def _build(self, index_name: str, vector_size: int):
        from graphrag_vectors.lancedb import LanceDBVectorStore

        store = LanceDBVectorStore(
            db_uri=self.db_uri,
            index_name=index_name,
            vector_size=vector_size,
        )
        store.connect()
        return store

    def upsert(self, index_name: str, items: list[dict]) -> None:
        from graphrag_vectors.vector_store import VectorStoreDocument

        if not items:
            return
        vector_size = len(items[0]["vector"])
        store = self._build(index_name, vector_size)
        # create_index() uses mode="overwrite" -> drops any prior table, so
        # re-indexing replaces stale vectors instead of appending duplicates.
        store.create_index()
        docs = [VectorStoreDocument(id=str(it["id"]), vector=list(it["vector"])) for it in items]
        store.load_documents(docs)
        logger.info("lancedb upsert: %d docs -> %s/%s", len(docs), self.db_uri, index_name)

    def query(self, index_name: str, text: str, k: int) -> list[dict]:
        # The production read path embeds the query text itself and calls
        # similarity_search_by_vector directly (see GraphRagQueryEngine);
        # this wrapper is intentionally write-only.
        raise NotImplementedError(
            "LanceDBVectorStoreWrapper is write-only; query via GraphRagQueryEngine"
        )


def build_vector_store(data_root: str) -> LanceDBVectorStoreWrapper:
    """Build the production LanceDB store rooted at ``<data_root>/vectors``.

    The same path MUST be set as ``vector_store.db_uri`` on the graphrag
    config used by the query read path (see ``GraphRagQueryEngine``).
    """
    return LanceDBVectorStoreWrapper(db_uri=os.path.join(str(data_root), "vectors"))
