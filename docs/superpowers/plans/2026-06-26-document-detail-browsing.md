# Document Detail Browsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a focused document detail browsing experience with readable document text, citation-based source verification through a right-side evidence drawer, and a separate entity/relation browser.

**Architecture:** Add narrow backend endpoints for document detail and chunk-backed citation evidence, then build focused React/Vite pages that consume those endpoints. Reuse the existing `/kbs/{kb_id}/graph` endpoint for the entity/relation browser so the first implementation avoids broad graph-pipeline changes.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pandas/parquet, React 18, TypeScript, Vite, Tailwind CSS, React Router, Vitest, Testing Library, MSW, pytest.

## Global Constraints

- Python support: `>=3.11,<3.14`.
- Frontend runtime: React 18 + TypeScript + Vite.
- Styling: use existing Tailwind/design-token classes and `web/src/components/ui.tsx`; do not add a new UI library.
- No new frontend or backend dependencies.
- Document reading and graph analysis remain separate focused views.
- Evidence drawer shows the matched snippet plus one small context segment before and one small context segment after.
- Entity/relation browsing is a separate tab/page, not inline entity highlighting inside the document body.
- Do not redesign the global graph explorer.
- Do not add PDF, Markdown, or CSV export.

---

## File Structure

### Backend

- Modify: `kb_platform/api/models.py`
  - Add response models for document detail, citations, evidence, and source metadata.
- Modify: `kb_platform/db/repository.py`
  - Add focused read helpers for one document and ordered chunks for one document.
- Modify: `kb_platform/api/routes_kbs.py`
  - Add `GET /kbs/{kb_id}/documents/{doc_id}`.
  - Add `GET /kbs/{kb_id}/documents/{doc_id}/citations/{citation_id}/evidence`.
- Test: `tests/test_api_document_detail.py`
  - Cover document detail, citation generation, evidence context, missing document, wrong KB, and missing citation.

### Frontend API/types

- Modify: `web/src/api/types.ts`
  - Add `DocumentCitation`, `DocumentDetail`, `EvidenceContext`, and `EvidenceDetail` interfaces.
- Modify: `web/src/api/client.ts`
  - Add `getDocumentDetail(kbId, docId)`.
  - Add `getDocumentEvidence(kbId, docId, citationId)`.
- Modify: `web/src/api/client.test.ts`
  - Cover the new client functions and endpoint paths.

### Frontend UI/pages

- Create: `web/src/components/EvidenceDrawer.tsx`
  - Present evidence loading, success, missing-context, and error states.
- Test: `web/src/components/EvidenceDrawer.test.tsx`
  - Cover drawer rendering and close behavior.
- Create: `web/src/pages/DocumentDetailPage.tsx`
  - Render document header, text, citation list, and evidence drawer integration.
- Test: `web/src/pages/DocumentDetailPage.test.tsx`
  - Cover loading, empty citation state, opening drawer, replacing evidence, close behavior, and evidence load failure.
- Create: `web/src/pages/EntityRelationPage.tsx`
  - Render KB-scoped entity and relationship lists using existing graph data.
- Test: `web/src/pages/EntityRelationPage.test.tsx`
  - Cover empty, success, click entity, click relationship, and load failure states.

### Routing/navigation

- Modify: `web/src/App.tsx`
  - Add routes for `/kbs/:id/documents/:docId` and `/kbs/:id/documents/:docId/entities`.
- Modify: `web/src/components/DocumentManager.tsx`
  - Add a view link for each document row.
- Modify: `web/src/components/DocumentManager.test.tsx`
  - Cover the new view link.
- Modify: `web/src/pages/KbLayout.tsx`
  - Existing `documents` tab uses `end: false`, so it remains active for nested document routes; no code change is planned.
- Test: `web/src/App.test.tsx`
  - Cover route rendering for document detail.

---

## Backend API Contract

### `GET /kbs/{kb_id}/documents/{doc_id}`

Response:

```json
{
  "id": 7,
  "title": "alpha.md",
  "status": "parsed",
  "bytes": 140,
  "chunk_count": 2,
  "text": "full document text",
  "citations": [
    {
      "id": "chunk:c1",
      "label": "分块 1",
      "snippet": "first chunk text",
      "chunk_id": "c1",
      "ordinal": 0
    }
  ]
}
```

### `GET /kbs/{kb_id}/documents/{doc_id}/citations/{citation_id}/evidence`

`citation_id` is URL-encoded. The first implementation uses `chunk:{chunk_id}`.

Response:

```json
{
  "citation_id": "chunk:c2",
  "matched": "current chunk text",
  "before": "previous chunk text",
  "after": "next chunk text",
  "source": {
    "document_id": 7,
    "document_title": "alpha.md",
    "chunk_id": "c2",
    "ordinal": 1
  }
}
```

If a before or after chunk is missing, return `null` for that field.

---

### Task 1: Backend document detail and evidence endpoints

**Files:**
- Modify: `kb_platform/api/models.py`
- Modify: `kb_platform/db/repository.py`
- Modify: `kb_platform/api/routes_kbs.py`
- Test: `tests/test_api_document_detail.py`

**Interfaces:**
- Consumes: existing `Document`, `Chunk`, `Repository`, `DocumentOut`, `session_scope`.
- Produces:
  - `Repository.get_document(kb_id: int, doc_id: int) -> Document | None`
  - `Repository.get_document_chunks(kb_id: int, doc_id: int) -> list[Chunk]`
  - `DocumentCitationOut`
  - `DocumentDetailOut`
  - `EvidenceSourceOut`
  - `EvidenceOut`
  - `GET /kbs/{kb_id}/documents/{doc_id}`
  - `GET /kbs/{kb_id}/documents/{doc_id}/citations/{citation_id}/evidence`

- [ ] **Step 1: Write failing backend tests**

Create `tests/test_api_document_detail.py` with this content:

```python
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
```

- [ ] **Step 2: Run backend tests to verify they fail**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
uv run pytest tests/test_api_document_detail.py -q
```

Expected: FAIL because `GET /kbs/{kb_id}/documents/{doc_id}` and the evidence endpoint are not registered.

- [ ] **Step 3: Add backend response models**

In `kb_platform/api/models.py`, insert these classes immediately after `DocumentOut`:

```python
class DocumentCitationOut(BaseModel):
    id: str
    label: str
    snippet: str
    chunk_id: str
    ordinal: int


class DocumentDetailOut(DocumentOut):
    text: str = ""
    citations: list[DocumentCitationOut] = []


class EvidenceSourceOut(BaseModel):
    document_id: int
    document_title: str
    chunk_id: str
    ordinal: int


class EvidenceOut(BaseModel):
    citation_id: str
    matched: str
    before: str | None = None
    after: str | None = None
    source: EvidenceSourceOut
```

- [ ] **Step 4: Add repository read helpers**

In `kb_platform/db/repository.py`, insert these methods after `get_documents`:

```python
    def get_document(self, kb_id: int, doc_id: int) -> Document | None:
        with session_scope(self.engine) as s:
            return s.scalar(select(Document).where(Document.id == doc_id, Document.kb_id == kb_id))

    def get_document_chunks(self, kb_id: int, doc_id: int) -> list[Chunk]:
        with session_scope(self.engine) as s:
            return list(
                s.scalars(
                    select(Chunk)
                    .where(Chunk.kb_id == kb_id, Chunk.document_id == doc_id)
                    .order_by(Chunk.ordinal)
                )
            )
```

- [ ] **Step 5: Add route imports and helpers**

In `kb_platform/api/routes_kbs.py`, extend the imports from `kb_platform.api.models` to include the new models:

```python
from kb_platform.api.models import (
    DocumentCitationOut,
    DocumentCreate,
    DocumentDetailOut,
    DocumentOut,
    EvidenceOut,
    EvidenceSourceOut,
    JobListItem,
    KbCreate,
    KbDetailOut,
    KbOut,
    KbUpdate,
)
```

Then insert these helper functions after `_parse_settings`:

```python
def _snippet(text: str, limit: int = 220) -> str:
    """Return a compact one-line snippet for citation lists."""
    one_line = " ".join((text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


def _citation_id(chunk_id: str) -> str:
    return f"chunk:{chunk_id}"


def _chunk_id_from_citation(citation_id: str) -> str | None:
    prefix = "chunk:"
    if not citation_id.startswith(prefix):
        return None
    return citation_id[len(prefix):]
```

- [ ] **Step 6: Add document detail and evidence routes**

In `kb_platform/api/routes_kbs.py`, insert these routes between `list_documents` and `delete_document`:

```python
@router.get("/kbs/{kb_id}/documents/{doc_id}", response_model=DocumentDetailOut)
def get_document_detail(kb_id: int, doc_id: int, request: Request) -> DocumentDetailOut:
    repo = request.app.state.repo
    doc = repo.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(404)
    chunks = repo.get_document_chunks(kb_id, doc_id)
    citations = [
        DocumentCitationOut(
            id=_citation_id(chunk.chunk_id),
            label=f"分块 {chunk.ordinal + 1}",
            snippet=_snippet(chunk.text),
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
        )
        for chunk in chunks
    ]
    return DocumentDetailOut(
        id=doc.id,
        title=doc.title,
        status=doc.status,
        bytes=doc.bytes,
        chunk_count=len(chunks),
        text=doc.text or "",
        citations=citations,
    )


@router.get(
    "/kbs/{kb_id}/documents/{doc_id}/citations/{citation_id}/evidence",
    response_model=EvidenceOut,
)
def get_document_evidence(
    kb_id: int,
    doc_id: int,
    citation_id: str,
    request: Request,
) -> EvidenceOut:
    repo = request.app.state.repo
    doc = repo.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(404)
    chunk_id = _chunk_id_from_citation(citation_id)
    if chunk_id is None:
        raise HTTPException(404)
    chunks = repo.get_document_chunks(kb_id, doc_id)
    index = next((i for i, chunk in enumerate(chunks) if chunk.chunk_id == chunk_id), None)
    if index is None:
        raise HTTPException(404)
    chunk = chunks[index]
    before = chunks[index - 1].text if index > 0 else None
    after = chunks[index + 1].text if index + 1 < len(chunks) else None
    return EvidenceOut(
        citation_id=citation_id,
        matched=chunk.text,
        before=before,
        after=after,
        source=EvidenceSourceOut(
            document_id=doc.id,
            document_title=doc.title,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
        ),
    )
```

- [ ] **Step 7: Run backend tests to verify they pass**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
uv run pytest tests/test_api_document_detail.py tests/test_api_documents.py -q
```

Expected: PASS for all tests in both files.

- [ ] **Step 8: Commit backend API work**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add kb_platform/api/models.py kb_platform/db/repository.py kb_platform/api/routes_kbs.py tests/test_api_document_detail.py
git commit -m "feat(api): add document detail evidence endpoints"
```

---

### Task 2: Frontend API types and client functions

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/api/client.test.ts`

**Interfaces:**
- Consumes:
  - `GET /kbs/{kb_id}/documents/{doc_id}` from Task 1.
  - `GET /kbs/{kb_id}/documents/{doc_id}/citations/{citation_id}/evidence` from Task 1.
- Produces:
  - `DocumentCitation`
  - `DocumentDetail`
  - `EvidenceContext`
  - `EvidenceDetail`
  - `getDocumentDetail(kbId: number, docId: number): Promise<DocumentDetail>`
  - `getDocumentEvidence(kbId: number, docId: number, citationId: string): Promise<EvidenceDetail>`

- [ ] **Step 1: Write failing client tests**

Replace `web/src/api/client.test.ts` with this content:

```tsx
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createKb, getDocumentDetail, getDocumentEvidence, listKbs, retryUnit } from "./client";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.post("/kbs", async ({ request }) => HttpResponse.json({ id: 2, name: (await request.json() as { name: string }).name, method: "standard" })),
  http.post("/units/5/retry", () => HttpResponse.json({ ok: true })),
  http.get("/kbs/1/documents/7", () =>
    HttpResponse.json({
      id: 7,
      title: "alpha.md",
      status: "parsed",
      bytes: 100,
      chunk_count: 1,
      text: "Alpha body",
      citations: [{ id: "chunk:c1", label: "分块 1", snippet: "Alpha body", chunk_id: "c1", ordinal: 0 }],
    }),
  ),
  http.get("/kbs/1/documents/7/citations/chunk%3Ac1/evidence", () =>
    HttpResponse.json({
      citation_id: "chunk:c1",
      matched: "Alpha body",
      before: null,
      after: "Beta context",
      source: { document_id: 7, document_title: "alpha.md", chunk_id: "c1", ordinal: 0 },
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("listKbs + createKb + retryUnit", async () => {
  const kbs = await listKbs();
  expect(kbs[0].name).toBe("kb1");
  const kb = await createKb({ name: "kb2" });
  expect(kb.id).toBe(2);
  expect((await retryUnit(5)).ok).toBe(true);
});

test("document detail client returns text and citations", async () => {
  const detail = await getDocumentDetail(1, 7);
  expect(detail.title).toBe("alpha.md");
  expect(detail.text).toBe("Alpha body");
  expect(detail.citations[0]).toMatchObject({ id: "chunk:c1", label: "分块 1" });
});

test("document evidence client encodes citation ids", async () => {
  const evidence = await getDocumentEvidence(1, 7, "chunk:c1");
  expect(evidence.matched).toBe("Alpha body");
  expect(evidence.after).toBe("Beta context");
  expect(evidence.source.chunk_id).toBe("c1");
});
```

- [ ] **Step 2: Run client tests to verify they fail**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- client.test.ts
```

Expected: FAIL because `getDocumentDetail` and `getDocumentEvidence` are not exported.

- [ ] **Step 3: Add frontend types**

In `web/src/api/types.ts`, insert these interfaces after `DocumentCreate`:

```ts
export interface DocumentCitation {
  id: string;
  label: string;
  snippet: string;
  chunk_id: string;
  ordinal: number;
}

export interface DocumentDetail extends DocumentOut {
  text: string;
  citations: DocumentCitation[];
}

export interface EvidenceContext {
  document_id: number;
  document_title: string;
  chunk_id: string;
  ordinal: number;
}

export interface EvidenceDetail {
  citation_id: string;
  matched: string;
  before: string | null;
  after: string | null;
  source: EvidenceContext;
}
```

- [ ] **Step 4: Add client functions**

In `web/src/api/client.ts`, update the type import on line 1 to include the new types:

```ts
import type { KbOut, DocumentOut, DocumentDetail, EvidenceDetail, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate, QueryResult, JobCost, KbCost, GraphData, Health } from "./types";
```

Then insert these functions after `listDocuments`:

```ts
export const getDocumentDetail = (kbId: number, docId: number) =>
  req<DocumentDetail>(`/kbs/${kbId}/documents/${docId}`);
export const getDocumentEvidence = (kbId: number, docId: number, citationId: string) =>
  req<EvidenceDetail>(`/kbs/${kbId}/documents/${docId}/citations/${encodeURIComponent(citationId)}/evidence`);
```

- [ ] **Step 5: Run client tests to verify they pass**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- client.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit frontend API work**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/api/types.ts web/src/api/client.ts web/src/api/client.test.ts
git commit -m "feat(web): add document evidence API client"
```

---

### Task 3: Evidence drawer component

**Files:**
- Create: `web/src/components/EvidenceDrawer.tsx`
- Create: `web/src/components/EvidenceDrawer.test.tsx`

**Interfaces:**
- Consumes: `EvidenceDetail` from Task 2.
- Produces:
  - `EvidenceDrawer(props: EvidenceDrawerProps): JSX.Element | null`
  - `EvidenceDrawerProps = { open: boolean; loading: boolean; evidence: EvidenceDetail | null; error: string | null; onClose: () => void }`

- [ ] **Step 1: Write failing component tests**

Create `web/src/components/EvidenceDrawer.test.tsx` with this content:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { EvidenceDrawer } from "./EvidenceDrawer";
import type { EvidenceDetail } from "../api/types";

const evidence: EvidenceDetail = {
  citation_id: "chunk:c2",
  matched: "Matched evidence text",
  before: "Before context",
  after: null,
  source: { document_id: 7, document_title: "alpha.md", chunk_id: "c2", ordinal: 1 },
};

test("does not render when closed", () => {
  render(<EvidenceDrawer open={false} loading={false} evidence={evidence} error={null} onClose={vi.fn()} />);
  expect(screen.queryByText("证据详情")).not.toBeInTheDocument();
});

test("renders matched evidence and missing context label", () => {
  render(<EvidenceDrawer open loading={false} evidence={evidence} error={null} onClose={vi.fn()} />);
  expect(screen.getByText("证据详情")).toBeInTheDocument();
  expect(screen.getByText("Matched evidence text")).toBeInTheDocument();
  expect(screen.getByText("Before context")).toBeInTheDocument();
  expect(screen.getByText("后文不可用")).toBeInTheDocument();
  expect(screen.getByText(/alpha.md/)).toBeInTheDocument();
});

test("renders loading and error states", () => {
  const { rerender } = render(<EvidenceDrawer open loading evidence={null} error={null} onClose={vi.fn()} />);
  expect(screen.getByText("加载证据…")).toBeInTheDocument();

  rerender(<EvidenceDrawer open loading={false} evidence={null} error="500 evidence" onClose={vi.fn()} />);
  expect(screen.getByText("证据加载失败")).toBeInTheDocument();
  expect(screen.getByText("500 evidence")).toBeInTheDocument();
});

test("close button calls onClose", () => {
  const onClose = vi.fn();
  render(<EvidenceDrawer open loading={false} evidence={evidence} error={null} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: "关闭证据抽屉" }));
  expect(onClose).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run component tests to verify they fail**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- EvidenceDrawer.test.tsx
```

Expected: FAIL because `EvidenceDrawer.tsx` does not exist.

- [ ] **Step 3: Implement evidence drawer**

Create `web/src/components/EvidenceDrawer.tsx` with this content:

```tsx
import type { EvidenceDetail } from "../api/types";
import { Button, Skeleton, Badge } from "./ui";
import { IconDoc } from "./icons";

export interface EvidenceDrawerProps {
  open: boolean;
  loading: boolean;
  evidence: EvidenceDetail | null;
  error: string | null;
  onClose: () => void;
}

export function EvidenceDrawer({ open, loading, evidence, error, onClose }: EvidenceDrawerProps) {
  if (!open) return null;

  return (
    <aside
      aria-label="证据详情"
      className="rounded-2xl border border-line bg-surface p-4 shadow-sm lg:sticky lg:top-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">证据详情</h3>
          <p className="mt-0.5 text-xs text-muted">命中片段 + 前后一小段上下文</p>
        </div>
        <Button size="sm" variant="ghost" aria-label="关闭证据抽屉" onClick={onClose}>
          关闭
        </Button>
      </div>

      <div className="mt-4 space-y-3">
        {loading ? (
          <div className="space-y-2 text-[13px] text-muted">
            <p>加载证据…</p>
            <Skeleton />
            <Skeleton className="w-5/6" />
            <Skeleton className="w-2/3" />
          </div>
        ) : error ? (
          <div className="rounded-xl border border-danger/20 bg-danger/5 p-3">
            <p className="text-sm font-medium text-danger">证据加载失败</p>
            <p className="mt-1 break-words text-xs text-muted">{error}</p>
          </div>
        ) : evidence ? (
          <EvidenceContent evidence={evidence} />
        ) : (
          <p className="rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
            选择一条引用查看证据。
          </p>
        )}
      </div>
    </aside>
  );
}

function EvidenceContent({ evidence }: { evidence: EvidenceDetail }) {
  return (
    <>
      <EvidenceBlock title="前文" empty="前文不可用" text={evidence.before} muted />
      <EvidenceBlock title="命中片段" text={evidence.matched} />
      <EvidenceBlock title="后文" empty="后文不可用" text={evidence.after} muted />
      <div className="rounded-xl border border-line bg-surface-2/60 p-3">
        <div className="mb-2 flex items-center gap-2 text-[13px] font-medium text-body">
          <IconDoc width={15} height={15} /> 来源信息
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge>{evidence.source.document_title}</Badge>
          <Badge tone="info">chunk {evidence.source.chunk_id}</Badge>
          <Badge tone="neutral">#{evidence.source.ordinal + 1}</Badge>
        </div>
      </div>
    </>
  );
}

function EvidenceBlock({
  title,
  text,
  empty,
  muted = false,
}: {
  title: string;
  text: string | null;
  empty?: string;
  muted?: boolean;
}) {
  return (
    <section className="rounded-xl border border-line bg-surface p-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">{title}</p>
      {text ? (
        <p className={muted ? "whitespace-pre-wrap text-[13px] leading-6 text-body" : "whitespace-pre-wrap text-sm leading-6 text-ink"}>
          {text}
        </p>
      ) : (
        <p className="text-[13px] text-muted">{empty ?? "上下文不可用"}</p>
      )}
    </section>
  );
}

export default EvidenceDrawer;
```

- [ ] **Step 4: Run component tests to verify they pass**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- EvidenceDrawer.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit evidence drawer**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/components/EvidenceDrawer.tsx web/src/components/EvidenceDrawer.test.tsx
git commit -m "feat(web): add evidence drawer"
```

---

### Task 4: Document detail page

**Files:**
- Create: `web/src/pages/DocumentDetailPage.tsx`
- Create: `web/src/pages/DocumentDetailPage.test.tsx`

**Interfaces:**
- Consumes:
  - `getDocumentDetail(kbId, docId)` from Task 2.
  - `getDocumentEvidence(kbId, docId, citationId)` from Task 2.
  - `EvidenceDrawer` from Task 3.
- Produces:
  - `DocumentDetailPage(): JSX.Element`

- [ ] **Step 1: Write failing page tests**

Create `web/src/pages/DocumentDetailPage.test.tsx` with this content:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DocumentDetailPage from "./DocumentDetailPage";
import { KbContext } from "./kb-context";

const kb = { id: 1, name: "kb1", method: "standard" };

const server = setupServer(
  http.get("/kbs/1/documents/7", () =>
    HttpResponse.json({
      id: 7,
      title: "alpha.md",
      status: "parsed",
      bytes: 100,
      chunk_count: 2,
      text: "Alpha body\n\nBeta body",
      citations: [
        { id: "chunk:c1", label: "分块 1", snippet: "Alpha body", chunk_id: "c1", ordinal: 0 },
        { id: "chunk:c2", label: "分块 2", snippet: "Beta body", chunk_id: "c2", ordinal: 1 },
      ],
    }),
  ),
  http.get("/kbs/1/documents/8", () =>
    HttpResponse.json({
      id: 8,
      title: "empty.md",
      status: "parsed",
      bytes: 11,
      chunk_count: 0,
      text: "Hello world",
      citations: [],
    }),
  ),
  http.get("/kbs/1/documents/7/citations/chunk%3Ac1/evidence", () =>
    HttpResponse.json({
      citation_id: "chunk:c1",
      matched: "Alpha body",
      before: null,
      after: "Beta body",
      source: { document_id: 7, document_title: "alpha.md", chunk_id: "c1", ordinal: 0 },
    }),
  ),
  http.get("/kbs/1/documents/7/citations/chunk%3Ac2/evidence", () =>
    HttpResponse.json({
      citation_id: "chunk:c2",
      matched: "Beta body",
      before: "Alpha body",
      after: null,
      source: { document_id: 7, document_title: "alpha.md", chunk_id: "c2", ordinal: 1 },
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage(path = "/kbs/1/documents/7") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <KbContext.Provider value={{ kbId: 1, kb, reload: () => undefined }}>
        <Routes>
          <Route path="/kbs/:id/documents/:docId" element={<DocumentDetailPage />} />
        </Routes>
      </KbContext.Provider>
    </MemoryRouter>,
  );
}

test("renders document title, body, and citations", async () => {
  renderPage();
  expect(await screen.findByText("alpha.md")).toBeInTheDocument();
  expect(screen.getByText(/Alpha body/)).toBeInTheDocument();
  expect(screen.getByText("分块 1")).toBeInTheDocument();
  expect(screen.getByText("分块 2")).toBeInTheDocument();
});

test("opens evidence drawer and replaces content when another citation is clicked", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /查看证据 分块 1/ }));
  expect(await screen.findByText("Alpha body")).toBeInTheDocument();
  expect(screen.getByText("后文")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /查看证据 分块 2/ }));
  await waitFor(() => expect(screen.getByText("Beta body")).toBeInTheDocument());
  expect(screen.getByText("前文")).toBeInTheDocument();
});

test("closes evidence drawer without removing document body", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /查看证据 分块 1/ }));
  expect(await screen.findByText("证据详情")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "关闭证据抽屉" }));
  expect(screen.queryByText("证据详情")).not.toBeInTheDocument();
  expect(screen.getByText(/Alpha body/)).toBeInTheDocument();
});

test("shows empty citation state", async () => {
  renderPage("/kbs/1/documents/8");
  expect(await screen.findByText("empty.md")).toBeInTheDocument();
  expect(screen.getByText("暂无可验证引用")).toBeInTheDocument();
});

test("evidence load failure stays local to drawer", async () => {
  server.use(
    http.get("/kbs/1/documents/7/citations/chunk%3Ac1/evidence", () => new HttpResponse(null, { status: 500 })),
  );
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /查看证据 分块 1/ }));
  expect(await screen.findByText("证据加载失败")).toBeInTheDocument();
  expect(screen.getByText(/Alpha body/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run page tests to verify they fail**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- DocumentDetailPage.test.tsx
```

Expected: FAIL because `DocumentDetailPage.tsx` does not exist.

- [ ] **Step 3: Implement document detail page**

Create `web/src/pages/DocumentDetailPage.tsx` with this content:

```tsx
import { Link, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { getDocumentDetail, getDocumentEvidence } from "../api/client";
import type { DocumentDetail, EvidenceDetail } from "../api/types";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from "../components/ui";
import { IconArrowLeft, IconDoc, IconGraph } from "../components/icons";
import { humanBytes } from "../lib/format";
import { statusLabel } from "../lib/status";
import { useKb } from "./kb-context";

export default function DocumentDetailPage() {
  const { kbId } = useKb();
  const { docId } = useParams();
  const numericDocId = Number(docId);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setSelectedCitation(null);
    setEvidence(null);
    setEvidenceError(null);
    getDocumentDetail(kbId, numericDocId)
      .then((value) => {
        if (alive) setDetail(value);
      })
      .catch((err: Error) => {
        if (alive) setError(err.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [kbId, numericDocId]);

  const openEvidence = (citationId: string) => {
    setSelectedCitation(citationId);
    setEvidence(null);
    setEvidenceError(null);
    setEvidenceLoading(true);
    getDocumentEvidence(kbId, numericDocId, citationId)
      .then(setEvidence)
      .catch((err: Error) => setEvidenceError(err.message))
      .finally(() => setEvidenceLoading(false));
  };

  if (loading) {
    return (
      <Card>
        <CardHeader title="文档详情" subtitle="加载文档正文与引用…" icon={<IconDoc width={18} height={18} />} />
        <div className="mt-5 space-y-3">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton />
          <Skeleton className="w-5/6" />
          <Skeleton className="w-2/3" />
        </div>
      </Card>
    );
  }

  if (error || !detail) {
    return (
      <EmptyState
        icon={<IconDoc />}
        title="文档加载失败"
        hint={error ?? "无法读取该文档。"}
        action={<Link to="../documents" className="btn btn-secondary btn-sm">返回文档列表</Link>}
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to="../documents" className="inline-flex items-center gap-1 text-[13px] font-medium text-brand hover:underline">
          <IconArrowLeft width={14} height={14} /> 返回文档列表
        </Link>
        <Link to="entities" className="btn btn-secondary btn-sm">
          <IconGraph width={14} height={14} /> 实体 / 关系
        </Link>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardHeader
            title={detail.title}
            subtitle={`${humanBytes(detail.bytes)} · ${detail.chunk_count} 个分块 · ${statusLabel(detail.status)}`}
            icon={<IconDoc width={18} height={18} />}
            actions={<Badge tone={detail.chunk_count > 0 ? "success" : "warning"}>{detail.chunk_count > 0 ? "已分块" : "待索引"}</Badge>}
          />

          <article className="mt-5 rounded-2xl border border-line bg-surface-2/40 p-4">
            <h4 className="mb-3 text-sm font-semibold text-ink">正文</h4>
            <p className="whitespace-pre-wrap text-sm leading-7 text-body">{detail.text || "该文档没有可显示正文。"}</p>
          </article>

          <section className="mt-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h4 className="text-sm font-semibold text-ink">引用列表</h4>
                <p className="text-xs text-muted">点击引用后在右侧查看命中片段与上下文。</p>
              </div>
            </div>

            {detail.citations.length === 0 ? (
              <EmptyState
                icon={<IconDoc />}
                title="暂无可验证引用"
                hint="文档正文仍可阅读；引用与实体关系会在索引完成后出现。"
              />
            ) : (
              <ul className="divide-y divide-line rounded-xl border border-line">
                {detail.citations.map((citation) => (
                  <li key={citation.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink">{citation.label}</p>
                      <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted">{citation.snippet}</p>
                    </div>
                    <Button
                      size="sm"
                      variant={selectedCitation === citation.id ? "primary" : "secondary"}
                      onClick={() => openEvidence(citation.id)}
                      aria-label={`查看证据 ${citation.label}`}
                    >
                      查看证据
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </Card>

        <EvidenceDrawer
          open={selectedCitation !== null}
          loading={evidenceLoading}
          evidence={evidence}
          error={evidenceError}
          onClose={() => {
            setSelectedCitation(null);
            setEvidence(null);
            setEvidenceError(null);
          }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run page tests to verify they pass**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- DocumentDetailPage.test.tsx EvidenceDrawer.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit document detail page**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/pages/DocumentDetailPage.tsx web/src/pages/DocumentDetailPage.test.tsx
git commit -m "feat(web): add document detail page"
```

---

### Task 5: Entity/relation browser page

**Files:**
- Create: `web/src/pages/EntityRelationPage.tsx`
- Create: `web/src/pages/EntityRelationPage.test.tsx`

**Interfaces:**
- Consumes:
  - `getGraph(kbId, { limit: 200 })` from existing frontend API.
  - `GraphNode` and `GraphEdge` from existing frontend types.
- Produces:
  - `EntityRelationPage(): JSX.Element`

- [ ] **Step 1: Write failing entity/relation page tests**

Create `web/src/pages/EntityRelationPage.test.tsx` with this content:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import EntityRelationPage from "./EntityRelationPage";
import { KbContext } from "./kb-context";

const kb = { id: 1, name: "kb1", method: "standard" };

const server = setupServer(
  http.get("/kbs/1/graph", () =>
    HttpResponse.json({
      nodes: [
        { id: "Alpha", title: "Alpha", type: "CONCEPT", degree: 2, community: "10" },
        { id: "Beta", title: "Beta", type: "PERSON", degree: 1, community: "10" },
        { id: "Gamma", title: "Gamma", type: "PLACE", degree: 0, community: "20" },
      ],
      edges: [
        { source: "Alpha", target: "Beta", weight: 2, description: "Alpha relates to Beta" },
      ],
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/kbs/1/documents/7/entities"]}>
      <KbContext.Provider value={{ kbId: 1, kb, reload: () => undefined }}>
        <Routes>
          <Route path="/kbs/:id/documents/:docId/entities" element={<EntityRelationPage />} />
        </Routes>
      </KbContext.Provider>
    </MemoryRouter>,
  );
}

test("renders entity and relationship lists", async () => {
  renderPage();
  expect(await screen.findByText("实体 / 关系")).toBeInTheDocument();
  expect(screen.getByText("Alpha")).toBeInTheDocument();
  expect(screen.getByText("Beta")).toBeInTheDocument();
  expect(screen.getByText("Alpha relates to Beta")).toBeInTheDocument();
});

test("clicking an entity filters related relationships", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "查看实体 Alpha 的关系" }));
  expect(screen.getByText("已选择实体：Alpha")).toBeInTheDocument();
  expect(screen.getByText("Alpha relates to Beta")).toBeInTheDocument();
});

test("clicking a relationship selects its connected entity", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "查看关系 Alpha 到 Beta" }));
  expect(screen.getByText("已选择实体：Alpha")).toBeInTheDocument();
});

test("renders empty state when graph has no data", async () => {
  server.use(http.get("/kbs/1/graph", () => HttpResponse.json({ nodes: [], edges: [] })));
  renderPage();
  expect(await screen.findByText("暂无实体或关系")).toBeInTheDocument();
});

test("renders local error when graph loading fails", async () => {
  server.use(http.get("/kbs/1/graph", () => new HttpResponse(null, { status: 500 })));
  renderPage();
  expect(await screen.findByText("实体关系加载失败")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run entity/relation tests to verify they fail**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- EntityRelationPage.test.tsx
```

Expected: FAIL because `EntityRelationPage.tsx` does not exist.

- [ ] **Step 3: Implement entity/relation browser page**

Create `web/src/pages/EntityRelationPage.tsx` with this content:

```tsx
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getGraph } from "../api/client";
import type { GraphEdge, GraphNode } from "../api/types";
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from "../components/ui";
import { IconArrowLeft, IconGraph } from "../components/icons";
import { useAsync } from "../hooks/useAsync";
import { useKb } from "./kb-context";

export default function EntityRelationPage() {
  const { kbId } = useKb();
  const { docId } = useParams();
  const graph = useAsync(() => getGraph(kbId, { limit: 200 }), [kbId]);
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null);

  const nodes = graph.data?.nodes ?? [];
  const edges = graph.data?.edges ?? [];
  const relatedEdges = useMemo(() => {
    if (!selectedEntity) return edges;
    return edges.filter((edge) => edge.source === selectedEntity || edge.target === selectedEntity);
  }, [edges, selectedEntity]);

  if (graph.loading) {
    return (
      <Card>
        <CardHeader title="实体 / 关系" subtitle="加载抽取结果…" icon={<IconGraph width={18} height={18} />} />
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      </Card>
    );
  }

  if (graph.error) {
    return (
      <EmptyState
        icon={<IconGraph />}
        title="实体关系加载失败"
        hint={graph.error.message}
        action={<BackToDocument docId={docId} />}
      />
    );
  }

  if (nodes.length === 0 && edges.length === 0) {
    return (
      <EmptyState
        icon={<IconGraph />}
        title="暂无实体或关系"
        hint="索引可能仍在运行，或当前知识库没有抽取到可浏览的结构化结果。"
        action={<BackToDocument docId={docId} />}
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <BackToDocument docId={docId} />
        {selectedEntity && (
          <Button size="sm" variant="ghost" onClick={() => setSelectedEntity(null)}>
            清除选择
          </Button>
        )}
      </div>

      <Card>
        <CardHeader
          title="实体 / 关系"
          subtitle="独立浏览抽取结果；选择实体后查看相关关系。"
          icon={<IconGraph width={18} height={18} />}
          actions={selectedEntity ? <Badge tone="info">已选择实体：{selectedEntity}</Badge> : <Badge>{nodes.length} 实体</Badge>}
        />

        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <EntityList nodes={nodes} selectedEntity={selectedEntity} onSelect={setSelectedEntity} />
          <RelationshipList edges={relatedEdges} onSelectEntity={setSelectedEntity} />
        </div>
      </Card>
    </div>
  );
}

function BackToDocument({ docId }: { docId: string | undefined }) {
  const { kbId } = useKb();
  return (
    <Link to={`/kbs/${kbId}/documents/${docId ?? ""}`} className="inline-flex items-center gap-1 text-[13px] font-medium text-brand hover:underline">
      <IconArrowLeft width={14} height={14} /> 返回文档
    </Link>
  );
}

function EntityList({
  nodes,
  selectedEntity,
  onSelect,
}: {
  nodes: GraphNode[];
  selectedEntity: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section>
      <h4 className="mb-3 text-sm font-semibold text-ink">实体</h4>
      <ul className="space-y-2">
        {nodes.map((node) => (
          <li key={node.id}>
            <button
              className={
                "w-full rounded-xl border px-3 py-3 text-left transition-colors " +
                (selectedEntity === node.id
                  ? "border-brand bg-brand-grad-soft"
                  : "border-line bg-surface hover:bg-surface-2")
              }
              onClick={() => onSelect(node.id)}
              aria-label={`查看实体 ${node.title} 的关系`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink">{node.title}</p>
                  <p className="mt-1 text-xs text-muted">{node.type || "未分类"}</p>
                </div>
                <Badge tone="neutral">度 {node.degree}</Badge>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RelationshipList({ edges, onSelectEntity }: { edges: GraphEdge[]; onSelectEntity: (id: string) => void }) {
  return (
    <section>
      <h4 className="mb-3 text-sm font-semibold text-ink">关系</h4>
      {edges.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
          当前实体没有可显示关系。
        </p>
      ) : (
        <ul className="space-y-2">
          {edges.map((edge) => (
            <li key={`${edge.source}-${edge.target}-${edge.description}`}>
              <button
                className="w-full rounded-xl border border-line bg-surface px-3 py-3 text-left hover:bg-surface-2"
                onClick={() => onSelectEntity(String(edge.source))}
                aria-label={`查看关系 ${edge.source} 到 ${edge.target}`}
              >
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
                  <span>{String(edge.source)}</span>
                  <span className="text-muted">→</span>
                  <span>{String(edge.target)}</span>
                </div>
                <p className="mt-1 text-[13px] leading-5 text-muted">{edge.description || "无关系描述"}</p>
                <p className="mt-2 text-xs text-muted">权重 {edge.weight}</p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run entity/relation tests to verify they pass**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- EntityRelationPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit entity/relation browser**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/pages/EntityRelationPage.tsx web/src/pages/EntityRelationPage.test.tsx
git commit -m "feat(web): add entity relation browser"
```

---

### Task 6: Wire routes and document list entry points

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/DocumentManager.tsx`
- Modify: `web/src/components/DocumentManager.test.tsx`
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Consumes:
  - `DocumentDetailPage` from Task 4.
  - `EntityRelationPage` from Task 5.
- Produces:
  - Route `/kbs/:id/documents/:docId`.
  - Route `/kbs/:id/documents/:docId/entities`.
  - Per-document `查看` link in `DocumentManager`.

- [ ] **Step 1: Write failing document manager link test**

In `web/src/components/DocumentManager.test.tsx`, add `MemoryRouter` import and wrap renders with a helper.

Update the imports to:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { DocumentManager } from "./DocumentManager";
import type { DocumentOut } from "../api/types";
import * as client from "../api/client";
```

Insert this helper after `docs`:

```tsx
function renderManager(reload = vi.fn()) {
  return render(
    <MemoryRouter>
      <DocumentManager kbId={1} docs={docs} reload={reload} />
    </MemoryRouter>,
  );
}
```

Replace existing `render(<DocumentManager ... />)` calls with `renderManager(...)`, then add this test:

```tsx
test("renders a detail link for each document", () => {
  renderManager();
  expect(screen.getByRole("link", { name: "查看文档 alpha.md" })).toHaveAttribute("href", "/kbs/1/documents/1");
  expect(screen.getByRole("link", { name: "查看文档 beta.txt" })).toHaveAttribute("href", "/kbs/1/documents/2");
});
```

- [ ] **Step 2: Write failing app route test**

In `web/src/App.test.tsx`, add these handlers inside `setupServer(...)`:

```tsx
  http.get("/kbs/1", () => HttpResponse.json({ id: 1, name: "kb1", method: "standard", settings: {} })),
  http.get("/kbs/1/documents/7", () =>
    HttpResponse.json({
      id: 7,
      title: "alpha.md",
      status: "parsed",
      bytes: 100,
      chunk_count: 0,
      text: "Alpha body",
      citations: [],
    }),
  ),
  http.get("/kbs/1/graph", () => HttpResponse.json({ nodes: [], edges: [] })),
```

Then add this test to `web/src/App.test.tsx`:

```tsx
test("renders document detail route", async () => {
  render(
    <MemoryRouter initialEntries={["/kbs/1/documents/7"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByText("alpha.md")).toBeInTheDocument();
  expect(screen.getByText(/Alpha body/)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run route tests to verify they fail**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- DocumentManager.test.tsx App.test.tsx
```

Expected: FAIL because routes and links are not wired.

- [ ] **Step 4: Wire routes in App**

In `web/src/App.tsx`, add imports:

```tsx
import DocumentDetailPage from "./pages/DocumentDetailPage";
import EntityRelationPage from "./pages/EntityRelationPage";
```

Then add the two nested routes immediately after the existing `documents` route:

```tsx
<Route path="documents/:docId" element={<DocumentDetailPage />} />
<Route path="documents/:docId/entities" element={<EntityRelationPage />} />
```

The KB route block should contain:

```tsx
<Route path="/kbs/:id" element={<KbLayout />}>
  <Route index element={<KbOverviewPage />} />
  <Route path="documents" element={<DocumentPage />} />
  <Route path="documents/:docId" element={<DocumentDetailPage />} />
  <Route path="documents/:docId/entities" element={<EntityRelationPage />} />
  <Route path="graph" element={<GraphPage />} />
  <Route path="jobs" element={<KbJobsPage />} />
  <Route path="jobs/:jobId" element={<JobDetailPage />} />
  <Route path="query" element={<QueryPage />} />
  <Route path="cost" element={<KbCostPage />} />
</Route>
```

- [ ] **Step 5: Add view links to DocumentManager**

In `web/src/components/DocumentManager.tsx`, add this import:

```tsx
import { Link } from "react-router-dom";
```

In the document row action area, replace the single delete button with this action group:

```tsx
<div className="flex shrink-0 items-center gap-2">
  <Link to={`/kbs/${kbId}/documents/${d.id}`} className="btn btn-sm btn-secondary" aria-label={`查看文档 ${d.title}`}>
    查看
  </Link>
  <button
    onClick={() => onDelete(d)}
    className="btn btn-sm btn-danger"
    aria-label={`删除文档 ${d.title}`}
  >
    <IconTrash width={14} height={14} />
    删除
  </button>
</div>
```

- [ ] **Step 6: Run route tests to verify they pass**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- DocumentManager.test.tsx App.test.tsx DocumentDetailPage.test.tsx EntityRelationPage.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit routing work**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add web/src/App.tsx web/src/App.test.tsx web/src/components/DocumentManager.tsx web/src/components/DocumentManager.test.tsx
git commit -m "feat(web): wire document browsing routes"
```

---

### Task 7: Full verification and documentation update

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`

**Interfaces:**
- Consumes: all endpoints, client functions, pages, and routes from Tasks 1-6.
- Produces: documented API/dashboard behavior and verified build/test output.

- [ ] **Step 1: Update English README API table**

In `README.md`, add these rows after the existing `GET /kbs/{id}/documents` row:

```markdown
| `GET` | `/kbs/{id}/documents/{doc_id}` | Document detail with stored text and chunk-backed citations |
| `GET` | `/kbs/{id}/documents/{doc_id}/citations/{citation_id}/evidence` | Evidence detail for one citation: matched chunk plus before/after context |
```

- [ ] **Step 2: Update English README dashboard table**

In `README.md`, replace the KB detail row in the dashboard table with:

```markdown
| KB detail | Document manager (upload/paste/list/delete), document detail browsing with source evidence drawer, trigger full/incremental, cumulative **cost**, **export** (zip/GraphML), interactive **graph**, entity/relation browser, jobs, query; **model-config card** shows the KB's LLM/embedding settings |
```

- [ ] **Step 3: Update Chinese README API table**

In `README.zh.md`, add these rows after the existing `GET /kbs/{id}/documents` row:

```markdown
| `GET` | `/kbs/{id}/documents/{doc_id}` | 文档详情：返回存储正文和基于分块的引用列表 |
| `GET` | `/kbs/{id}/documents/{doc_id}/citations/{citation_id}/evidence` | 单条引用的证据详情：命中分块 + 前后上下文 |
```

- [ ] **Step 4: Update Chinese README dashboard table**

In `README.zh.md`, replace the KB detail row in the dashboard table with:

```markdown
| KB 详情 | 文档管理（上传/粘贴/列表/删除）、文档详情浏览与来源证据抽屉、触发全量/增量、累计**成本**、**导出**（zip/GraphML）、可交互**图谱**、实体/关系浏览、任务、检索；**模型配置卡**展示 LLM/嵌入设置 |
```

- [ ] **Step 5: Run backend verification**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
uv run pytest tests/test_api_document_detail.py tests/test_api_documents.py tests/test_api_graph.py -q
```

Expected: PASS.

- [ ] **Step 6: Run frontend verification**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm test -- client.test.ts EvidenceDrawer.test.tsx DocumentDetailPage.test.tsx EntityRelationPage.test.tsx DocumentManager.test.tsx App.test.tsx
npm run build
```

Expected: all selected Vitest tests PASS, then TypeScript and Vite build complete successfully.

- [ ] **Step 7: Run lint/static checks available in this repo**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
uv run ruff check .
```

Expected: PASS.

- [ ] **Step 8: Commit docs and verification-ready state**

Run:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
git add README.md README.zh.md
git commit -m "docs: document detail browsing API"
```

---

## Manual Verification Script

After Task 7, run the app and verify these flows manually:

1. Open a KB with at least one uploaded document.
2. Go to `KB → 文档`.
3. Click `查看` on a document row.
4. Confirm the document title, metadata, and body render.
5. Click the first citation.
6. Confirm the right-side evidence drawer opens.
7. Click a second citation.
8. Confirm the same drawer updates content instead of opening a second drawer.
9. Close the drawer.
10. Confirm the document body is still visible.
11. Click `实体 / 关系`.
12. Confirm the entity/relation page is separate from the document reading page.
13. Select an entity.
14. Confirm relationships filter to that entity.
15. Open a document with no chunks.
16. Confirm the document body remains visible and the citation area shows `暂无可验证引用`.

Suggested commands:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform
uv run alembic upgrade head
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000
```

In a second terminal:

```bash
cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform/web
npm run dev
```

Open `http://localhost:5173`.

---

## Self-Review Notes

- Spec coverage:
  - Document reading surface: Task 4.
  - Citation list and source verification drawer: Tasks 1, 2, 3, 4.
  - Matched snippet plus before/after context: Task 1 backend contract and Task 3 drawer.
  - Separate entity/relation page: Task 5 and Task 6 routes.
  - Empty/error states: Tasks 1, 3, 4, 5 tests.
  - Responsive desktop/small-screen behavior: Task 3 and Task 4 use stacking grid with `lg:` right-side panel behavior.
  - Existing graph explorer remains unchanged: Task 5 reuses `getGraph` and does not modify `GraphView` or `GraphPage`.
- Type consistency:
  - Backend `DocumentCitationOut` maps to frontend `DocumentCitation`.
  - Backend `EvidenceOut` maps to frontend `EvidenceDetail`.
  - Citation IDs use `chunk:{chunk_id}` and frontend encodes them with `encodeURIComponent`.
- Scope control:
  - The entity/relation browser is KB-scoped for the first implementation because existing `/graph` data is KB-scoped.
  - The implementation does not add export formats, inline entity highlighting, or full-screen graph visualization inside document detail.
