"""VectorStore seam: Fake + LanceDB wrappers."""

from typing import Protocol


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
