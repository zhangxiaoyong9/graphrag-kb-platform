"""Tests for document list (bytes/chunk_count), markitdown upload, and delete cascade."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, Chunk, Document, KnowledgeBase
from kb_platform.db.repository import Repository


@pytest.fixture()
def repo_and_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    return repo, client


def _chunks_for_doc(repo, doc_id: int) -> list[Chunk]:
    with session_scope(repo.engine) as s:
        return list(s.scalars(select(Chunk).where(Chunk.document_id == doc_id)))


def test_upload_multipart_uses_markitdown(repo_and_client):
    repo, client = repo_and_client
    r = client.post(
        "/kbs/1/documents",
        files={"file": ("note.txt", b"the quick brown fox", "text/plain")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "note.txt"
    # Stored text equals parsed content (markitdown passes txt through).
    with session_scope(repo.engine) as s:
        doc = s.get(Document, body["id"])
        assert "quick brown fox" in (doc.text or "")


def test_upload_size_cap_413(repo_and_client, monkeypatch):
    _, client = repo_and_client
    monkeypatch.setenv("KB_MAX_UPLOAD_BYTES", "8")
    r = client.post(
        "/kbs/1/documents",
        files={"file": ("big.txt", b"x" * 100, "text/plain")},
    )
    assert r.status_code == 413


def test_delete_document_cascades_chunks(repo_and_client):
    repo, client = repo_and_client
    with session_scope(repo.engine) as s:
        doc = Document(
            kb_id=1,
            title="seed-doc",
            source_uri="",
            content_hash="h",
            status="parsed",
            bytes=9,
            text="seed text",
        )
        s.add(doc)
        s.flush()
        doc_id = doc.id
        s.add(Chunk(chunk_id="c1", kb_id=1, document_id=doc_id, ordinal=0, text="a"))
        s.add(Chunk(chunk_id="c2", kb_id=1, document_id=doc_id, ordinal=1, text="b"))

    assert len(_chunks_for_doc(repo, doc_id)) == 2

    r = client.delete(f"/kbs/1/documents/{doc_id}")
    assert r.status_code == 204
    assert _chunks_for_doc(repo, doc_id) == []
    with session_scope(repo.engine) as s:
        assert s.get(Document, doc_id) is None


def test_delete_document_wrong_kb_404(repo_and_client):
    repo, client = repo_and_client
    with session_scope(repo.engine) as s:
        doc = Document(
            kb_id=1, title="d", source_uri="", content_hash="h", status="parsed", bytes=1, text="x"
        )
        s.add(doc)
        s.flush()
        doc_id = doc.id
    r = client.delete(f"/kbs/999/documents/{doc_id}")
    assert r.status_code == 404


def test_delete_missing_document_404(repo_and_client):
    _, client = repo_and_client
    assert client.delete("/kbs/1/documents/9999").status_code == 404


def test_document_out_has_bytes_and_chunk_count(repo_and_client):
    repo, client = repo_and_client
    with session_scope(repo.engine) as s:
        doc = Document(
            kb_id=1,
            title="d1",
            source_uri="",
            content_hash="h",
            status="parsed",
            bytes=42,
            text="abc",
        )
        s.add(doc)
        s.flush()
        doc_id = doc.id
        s.add(Chunk(chunk_id="c1", kb_id=1, document_id=doc_id, ordinal=0, text="a"))
        s.add(Chunk(chunk_id="c2", kb_id=1, document_id=doc_id, ordinal=1, text="b"))

    docs = client.get("/kbs/1/documents").json()
    assert len(docs) == 1
    d = docs[0]
    assert d["bytes"] == 42
    assert d["chunk_count"] == 2
