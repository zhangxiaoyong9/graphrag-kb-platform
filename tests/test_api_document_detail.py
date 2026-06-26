"""Tests for document detail browsing and chunk-backed evidence."""

from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient

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
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
        s.add(KnowledgeBase(name="kb2", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return repo, client


def _seed_document(repo: Repository, *, kb_id: int = 1) -> int:
    with session_scope(repo.engine) as s:
        doc = Document(
            kb_id=kb_id,
            title="alpha.md",
            source_uri="",
            content_hash="hash-alpha",
            status="parsed",
            bytes=140,
            text="Alpha introduction.\nBeta details.\nGamma conclusion.",
        )
        s.add(doc)
        s.flush()
        doc_id = doc.id
        s.add(Chunk(chunk_id="c1", kb_id=kb_id, document_id=doc_id, ordinal=0, text="Alpha introduction."))
        s.add(Chunk(chunk_id="c2", kb_id=kb_id, document_id=doc_id, ordinal=1, text="Beta details."))
        s.add(Chunk(chunk_id="c3", kb_id=kb_id, document_id=doc_id, ordinal=2, text="Gamma conclusion."))
    return doc_id


def test_document_detail_returns_text_and_chunk_citations(repo_and_client):
    repo, client = repo_and_client
    doc_id = _seed_document(repo)

    r = client.get(f"/kbs/1/documents/{doc_id}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == doc_id
    assert body["title"] == "alpha.md"
    assert body["status"] == "parsed"
    assert body["bytes"] == 140
    assert body["chunk_count"] == 3
    assert body["text"] == "Alpha introduction.\nBeta details.\nGamma conclusion."
    assert body["citations"] == [
        {"id": "chunk:c1", "label": "分块 1", "snippet": "Alpha introduction.", "chunk_id": "c1", "ordinal": 0},
        {"id": "chunk:c2", "label": "分块 2", "snippet": "Beta details.", "chunk_id": "c2", "ordinal": 1},
        {"id": "chunk:c3", "label": "分块 3", "snippet": "Gamma conclusion.", "chunk_id": "c3", "ordinal": 2},
    ]


def test_document_detail_without_chunks_has_empty_citations(repo_and_client):
    repo, client = repo_and_client
    with session_scope(repo.engine) as s:
        doc = Document(
            kb_id=1,
            title="unindexed.txt",
            source_uri="",
            content_hash="hash-unindexed",
            status="parsed",
            bytes=11,
            text="hello world",
        )
        s.add(doc)
        s.flush()
        doc_id = doc.id

    r = client.get(f"/kbs/1/documents/{doc_id}")

    assert r.status_code == 200, r.text
    assert r.json()["citations"] == []
    assert r.json()["chunk_count"] == 0


def test_document_detail_missing_document_404(repo_and_client):
    _, client = repo_and_client

    r = client.get("/kbs/1/documents/9999")

    assert r.status_code == 404


def test_document_detail_wrong_kb_404(repo_and_client):
    repo, client = repo_and_client
    doc_id = _seed_document(repo, kb_id=1)

    r = client.get(f"/kbs/2/documents/{doc_id}")

    assert r.status_code == 404


def test_evidence_returns_matched_chunk_with_before_after_context(repo_and_client):
    repo, client = repo_and_client
    doc_id = _seed_document(repo)
    citation_id = urllib.parse.quote("chunk:c2", safe="")

    r = client.get(f"/kbs/1/documents/{doc_id}/citations/{citation_id}/evidence")

    assert r.status_code == 200, r.text
    assert r.json() == {
        "citation_id": "chunk:c2",
        "matched": "Beta details.",
        "before": "Alpha introduction.",
        "after": "Gamma conclusion.",
        "source": {
            "document_id": doc_id,
            "document_title": "alpha.md",
            "chunk_id": "c2",
            "ordinal": 1,
        },
    }


def test_evidence_allows_missing_before_or_after_context(repo_and_client):
    repo, client = repo_and_client
    doc_id = _seed_document(repo)
    citation_id = urllib.parse.quote("chunk:c1", safe="")

    r = client.get(f"/kbs/1/documents/{doc_id}/citations/{citation_id}/evidence")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] == "Alpha introduction."
    assert body["before"] is None
    assert body["after"] == "Beta details."


def test_evidence_missing_citation_404(repo_and_client):
    repo, client = repo_and_client
    doc_id = _seed_document(repo)
    citation_id = urllib.parse.quote("chunk:not-real", safe="")

    r = client.get(f"/kbs/1/documents/{doc_id}/citations/{citation_id}/evidence")

    assert r.status_code == 404
