# Phase 4 — Wave 3 (UX) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document management (markitdown upload + list + delete), index export (zip + self-written GraphML), and an interactive graph visualization (react-force-graph-2d) in the KB detail page.

**Architecture:** All read/write goes through existing parquet under `data_root` and the existing FastAPI/React stack. Two spec refinements (recorded): (1) **document parsing lives at the API layer** — uploads arrive via the API and the worker only chunks already-stored `Document.text`, so markitdown is a small `kb_platform/input/doc_reader.py` the route calls directly (NOT `GraphAdapter.read_document` — the adapter is worker-side). (2) **delete uses application-level cascade** (repo deletes a document's chunks then the document in one transaction) instead of an SQLite FK-rebuild migration — lower risk, same functional result (graph is NOT shrunk; re-index via incremental). The `/graph` endpoint reads the platform's own parquet (entity `title`/`type`/`degree`, relationship `source`/`target`/`weight`, community `community_id`/`entity_ids`) directly — it does NOT need graphrag's `_norm_*` frames (those serve graphrag's query readers, not viz). GraphML is self-written XML (networkx is not installed).

**Tech Stack:** Python 3.11, markitdown 0.1.6 (`MarkItDown().convert(BytesIO).text_content`), FastAPI, pandas, React+TS+Vite+Tailwind, `react-force-graph-2d` (new npm dep).

**Spec:** `docs/superpowers/specs/2026-06-25-phase4-polish-design.md` §6 (C/D/B). J (Playwright E2E) is a separate follow-up plan.

## Global Constraints

- License header on every new `.py` (`# Copyright (c) 2024 Microsoft Corporation.` / `# Licensed under the MIT License.`).
- Static check: **ruff** clean (`uv run ruff check .`; touched files `ruff format --check`). kb-platform has **no poethepoet/pyright/semversioner**.
- `uv run pytest -q` (backend) + `cd web && npm test && npm run build` (frontend) green.
- **Delete does NOT shrink the graph** — only removes the `Document` (+ its `Chunk` rows) from the control plane; existing extracted/summarized parquet is untouched. UI must say so.
- File upload size cap default 25 MiB (configurable via `KB_MAX_UPLOAD_BYTES` env).
- `FakeGraphAdapter` is unaffected (no new adapter method).
- Do not edit package `version =`.

## File Structure

**Create:**
- `kb_platform/input/doc_reader.py` — `read_document(data, filename) -> str` (markitdown, lazy import).
- `kb_platform/api/routes_export.py` — `GET /kbs/{id}/export?format=zip|graphml`.
- `kb_platform/api/routes_graph.py` — `GET /kbs/{id}/graph?limit=&q=&hop=`.
- `kb_platform/graph/graphml.py` — `write_graphml(entities_df, relationships_df) -> str`.
- `web/src/components/DocumentManager.tsx` (+ test) — replaces `DocumentUpload`.
- `web/src/components/GraphView.tsx` (+ test) — react-force-graph-2d.
- `tests/test_doc_reader.py`, `tests/test_api_documents.py`, `tests/test_graphml.py`, `tests/test_api_export.py`, `tests/test_api_graph.py`.

**Modify:**
- `kb_platform/api/routes_kbs.py` — multipart upload → `read_document`; `DELETE /kbs/{id}/documents/{doc_id}`; `DocumentOut` bytes+chunk_count.
- `kb_platform/db/repository.py` — `delete_document` (cascade chunks) + chunk counts.
- `kb_platform/api/models.py` — `DocumentOut` fields.
- `kb_platform/api/app.py` — mount export + graph routers.
- `web/src/api/types.ts`, `web/src/api/client.ts` — document list/delete, export, graph types+calls.
- `web/src/pages/KbDetailPage.tsx` — use `DocumentManager`, add Export + `GraphView`.

---

### Task 1 (C-backend): markitdown upload + document list/delete

**Files:**
- Create: `kb_platform/input/doc_reader.py`
- Modify: `kb_platform/api/routes_kbs.py`, `kb_platform/db/repository.py`, `kb_platform/api/models.py`
- Test: `tests/test_doc_reader.py`, `tests/test_api_documents.py`

**Interfaces:**
- Produces: `read_document(data: bytes, filename: str) -> str`; `Repository.delete_document(kb_id, doc_id) -> bool` (cascades chunks); `DocumentOut` gains `bytes` + `chunk_count`; multipart upload parses via `read_document`; `DELETE /kbs/{kb_id}/documents/{doc_id}` → 204 (404 if missing).

- [ ] **Step 1: Write the failing tests**

`tests/test_doc_reader.py`:

```python
def test_read_document_txt():
    from kb_platform.input.doc_reader import read_document
    assert "hello" in read_document(b"hello world", "note.txt")


def test_read_document_markitdown_fallback_on_decode():
    """Binary-ish input still yields text (markitdown or decode fallback), never raises."""
    from kb_platform.input.doc_reader import read_document
    out = read_document(b"\xff\xfe\x00x", "weird.bin")
    assert isinstance(out, str)
```

`tests/test_api_documents.py` — build repo+app (`Base.metadata.create_all`), add a KB + document (with chunks), then:

```python
def test_upload_multipart_uses_markitdown(tmp_path):
    # POST /kbs/{id}/documents multipart file "note.txt" -> Document.text == file content
    ...
def test_delete_document_cascades_chunks(tmp_path):
    # seed doc + chunks; DELETE /kbs/{id}/documents/{doc_id} -> 204;
    # assert chunks for that document are gone (repo.get_chunks returns none for it)
    ...
def test_document_out_has_bytes_and_chunk_count(tmp_path):
    # GET /kbs/{id}/documents -> DocumentOut includes bytes + chunk_count
    ...
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_doc_reader.py tests/test_api_documents.py -v`
Expected: FAIL — modules/routes missing.

- [ ] **Step 3: Implement `doc_reader`**

`kb_platform/input/doc_reader.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Document → text extraction (markitdown). Used by the upload route at API time."""

from __future__ import annotations

import io


def read_document(data: bytes, filename: str) -> str:
    """Extract plain text from uploaded bytes via markitdown.

    Never raises: if markitdown rejects the content, fall back to utf-8 decode
    (errors replaced) so an unusual file still produces storable text.
    """
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(io.BytesIO(data))
        text = getattr(result, "text_content", None)
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    return data.decode("utf-8", errors="replace")
```

- [ ] **Step 4: Repo delete + chunk counts**

In `kb_platform/db/repository.py`:

```python
    def delete_document(self, kb_id: int, doc_id: int) -> bool:
        """Delete a document AND its chunks (application-level cascade).

        The graph/index is NOT shrunk (no reverse extraction); only the control-
        plane rows are removed. Returns True if a row was deleted.
        """
        from sqlalchemy import delete

        with session_scope(self.engine) as s:
            doc = s.get(Document, doc_id)
            if doc is None or doc.kb_id != kb_id:
                return False
            s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
            s.delete(doc)
        return True

    def chunk_counts_by_document(self, kb_id: int) -> dict[int, int]:
        from sqlalchemy import func, select

        with session_scope(self.engine) as s:
            rows = s.execute(
                select(Chunk.document_id, func.count()).where(Chunk.kb_id == kb_id).group_by(Chunk.document_id)
            ).all()
        return {int(d): int(c) for d, c in rows}
```

- [ ] **Step 5: Route changes + DocumentOut**

`kb_platform/api/models.py`:

```python
class DocumentOut(BaseModel):
    id: int
    title: str
    status: str = "uploaded"
    bytes: int = 0
    chunk_count: int = 0
```

`kb_platform/api/routes_kbs.py` — multipart branch: replace `raw = upload.file.read().decode(...)` with:

```python
        from kb_platform.input.doc_reader import read_document

        data = await upload.read()
        text = read_document(data, upload.filename or "upload")
        doc = repo.add_document(kb_id=kb_id, title=title or upload.filename, text=text)
```

Add the delete endpoint + extend list_documents:

```python
@router.delete("/kbs/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(kb_id: int, doc_id: int, request: Request):
    repo = request.app.state.repo
    if not repo.delete_document(kb_id, doc_id):
        raise HTTPException(404)
    return None
```

In `list_documents`, build `DocumentOut(..., bytes=d.bytes, chunk_count=counts.get(d.id, 0))` using `repo.chunk_counts_by_document(kb_id)`. Enforce the upload cap: in the multipart branch, `if len(data) > int(os.environ.get("KB_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)): raise HTTPException(413)`.

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_doc_reader.py tests/test_api_documents.py tests/test_api_kbs.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/input/doc_reader.py kb_platform/api/routes_kbs.py kb_platform/db/repository.py kb_platform/api/models.py tests/test_doc_reader.py tests/test_api_documents.py
git commit -m "feat(docs): markitdown upload, document list(bytes/chunks), delete (cascade chunks, no graph shrink)"
```

---

### Task 2 (C-frontend): DocumentManager

**Files:**
- Create: `web/src/components/DocumentManager.tsx` (+ `.test.tsx`)
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/pages/KbDetailPage.tsx`
- (Delete or keep `DocumentUpload.tsx`; remove its usage.)

**Interfaces:**
- Produces: `DocumentManager({ kbId, docs, reload })` — list (title/bytes/chunks/status/delete) + file upload (multipart) + text paste + delete-with-confirm; `deleteDocument(kbId, docId)`; `uploadFile(kbId, file)`.

- [ ] **Step 1: Types + client**

`types.ts`: extend `DocumentOut { id; title; status; bytes; chunk_count }`. `client.ts`: `uploadFile(kbId, file)` (multipart FormData POST to `/kbs/{id}/documents`), `deleteDocument(kbId, docId)` (DELETE).

- [ ] **Step 2: Failing test**

`DocumentManager.test.tsx`: render with a fixture doc list; assert the title/bytes render; assert delete button calls `deleteDocument` (mock); assert a file input is present.

- [ ] **Step 3: Implement + wire**

`DocumentManager.tsx`: list rows + `<input type="file">` (multipart upload via `uploadFile`) + textarea paste (existing `addDocument`) + delete button (window.confirm with "graph will not shrink" copy) calling `deleteDocument`. Replace `<DocumentUpload>` usage in `KbDetailPage` with `<DocumentManager>` (pass docs + reload).

- [ ] **Step 4: `npm test && npm run build`**

- [ ] **Step 5: Commit** — `feat(web): DocumentManager (upload/list/delete with graph-not-shrunk notice)`

---

### Task 3 (D-backend): GraphML + export endpoint

**Files:**
- Create: `kb_platform/graph/graphml.py`, `kb_platform/api/routes_export.py`
- Modify: `kb_platform/api/app.py`
- Test: `tests/test_graphml.py`, `tests/test_api_export.py`

**Interfaces:**
- Produces: `write_graphml(entities_df, relationships_df) -> str` (GraphML XML, XML-escaped); `GET /kbs/{id}/export?format=zip|graphml`.

- [ ] **Step 1: Failing tests**

`tests/test_graphml.py`:

```python
def test_write_graphml_well_formed_and_escaped():
    import pandas as pd
    import xml.etree.ElementTree as ET
    from kb_platform.graph.graphml import write_graphml

    ents = pd.DataFrame([{"title": "A&B", "type": "CONCEPT", "degree": 2, "description": "<x>"}])
    rels = pd.DataFrame([{"source": "A&B", "target": "A&B", "weight": 1.0, "description": "self"}])
    xml = write_graphml(ents, rels)
    root = ET.fromstring(xml)  # parses -> well-formed
    assert root.tag == "{http://graphml.graphdrawing.org/xmlns}graphml"
    assert "A&amp;B" in xml  # escaped


def test_write_graphml_empty():
    import pandas as pd
    from kb_platform.graph.graphml import write_graphml
    xml = write_graphml(pd.DataFrame(columns=["title"]), pd.DataFrame(columns=["source", "target"]))
    assert "graphml" in xml  # no crash on empty
```

`tests/test_api_export.py`: seed a KB + write entities/relationships parquet under its data_root; `GET /kbs/{id}/export?format=graphml` → 200 `application/graphml+xml` containing the entity title; `?format=zip` → 200 `application/zip`, and the zip contains `entities.parquet` + `graph.graphml`.

- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/test_graphml.py tests/test_api_export.py -v` FAIL.

- [ ] **Step 3: Implement GraphML**

`kb_platform/graph/graphml.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Self-written GraphML (no networkx dependency)."""

from __future__ import annotations

from xml.sax.saxutils import escape

NS = "http://graphml.graphdrawing.org/xmlns"


def write_graphml(entities, relationships) -> str:
    titles = set(entities["title"].tolist()) if not entities.empty else set()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<graphml xmlns="{NS}">',
        '<key attr.name="type" attr.type="string" for="node" id="d_type"/>',
        '<key attr.name="degree" attr.type="int" for="node" id="d_deg"/>',
        '<key attr.name="description" attr.type="string" for="node" id="d_desc"/>',
        '<key attr.name="weight" attr.type="double" for="edge" id="d_w"/>',
        '<key attr.name="description" attr.type="string" for="edge" id="d_edesc"/>',
        '<graph edgedefault="undirected">',
    ]
    for _, r in entities.iterrows():
        t = escape(str(r["title"]))
        lines.append(f'<node id="{t}"/>')
        lines.append(f'<data node="{t}" key="d_type">{escape(str(r.get("type", "")))}</data>')
    # (node data is simpler as <node><data/></node>; collapse into the node element below if preferred)
    for _, r in relationships.iterrows():
        s, t = escape(str(r["source"])), escape(str(r["target"]))
        lines.append(f'<edge source="{s}" target="{t}"/>')
    lines.append("</graph></graphml>")
    return "\n".join(lines)
```

(Refine to embed `<data>` inside `<node>`/`<edge>` per the GraphML spec — the test only requires well-formed + escaped; keep it simple but valid.)

- [ ] **Step 4: Implement export route**

`kb_platform/api/routes_export.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Index export: zip of parquet artifacts, or standalone GraphML."""

import io
import zipfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

router = APIRouter()


def _data_root(request: Request, kb_id: int) -> Path:
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(404)
    return Path(kb.data_root)


@router.get("/kbs/{kb_id}/export")
def export(kb_id: int, request: Request, format: str = "zip"):
    root = _data_root(request, kb_id)
    if format == "graphml":
        from kb_platform.graph.graphml import write_graphml

        ents = pd.read_parquet(root / "entities.parquet") if (root / "entities.parquet").exists() else pd.DataFrame(columns=["title"])
        rels = pd.read_parquet(root / "relationships.parquet") if (root / "relationships.parquet").exists() else pd.DataFrame(columns=["source", "target"])
        return Response(write_graphml(ents, rels), media_type="application/graphml+xml")
    if format == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("entities.parquet", "relationships.parquet", "communities.parquet", "community_reports.parquet", "text_units.parquet"):
                p = root / name
                if p.exists():
                    z.write(p, name)
            from kb_platform.graph.graphml import write_graphml
            ents = pd.read_parquet(root / "entities.parquet") if (root / "entities.parquet").exists() else pd.DataFrame(columns=["title"])
            rels = pd.read_parquet(root / "relationships.parquet") if (root / "relationships.parquet").exists() else pd.DataFrame(columns=["source", "target"])
            z.writestr("graph.graphml", write_graphml(ents, rels))
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=kb-{kb_id}.zip"})
    raise HTTPException(400, "format must be zip or graphml")
```

Mount in `app.py`: `from kb_platform.api.routes_export import router as export_router` + `app.include_router(export_router)`.

- [ ] **Step 5: Run to verify pass** — `uv run pytest tests/test_graphml.py tests/test_api_export.py -q` PASS.

- [ ] **Step 6: Commit** — `feat(export): GraphML writer + /kbs/{id}/export?format=zip|graphml`

---

### Task 4 (D-frontend): Export buttons

**Files:** `web/src/pages/KbDetailPage.tsx` (add "Export zip" + "Download GraphML" buttons linking to `/kbs/{id}/export?format=...`). No new component needed (two anchor/buttons). Add a tiny test if the page test covers it; otherwise rely on the build.

- [ ] **Step 1:** Add two buttons in `KbDetailPage` (`<a href={`/kbs/${kbId}/export?format=zip`}>Export zip</a>` and `?format=graphml`). 
- [ ] **Step 2:** `npm test && npm run build`.
- [ ] **Step 3:** Commit — `feat(web): export zip + GraphML buttons on KB detail`

---

### Task 5 (B-backend): `/graph` endpoint

**Files:**
- Create: `kb_platform/api/routes_graph.py`
- Modify: `kb_platform/api/app.py`
- Test: `tests/test_api_graph.py`

**Interfaces:**
- Produces: `GET /kbs/{id}/graph?limit=N&q=&hop=1|2` → `{nodes:[{id,title,type,degree,community}], edges:[{source,target,weight,description}]}`. Default Top-N by degree (cap 500); with `q` → title-substring matches + BFS neighborhood.

- [ ] **Step 1: Failing test**

`tests/test_api_graph.py`: seed a KB + write entities/relationships/communities parquet under data_root (entities with `title`/`type`/`degree`; relationships `source`/`target`/`weight`/`description`; communities `community_id`/`entity_ids`). Assert:
- `GET /kbs/{id}/graph?limit=2` returns the 2 highest-degree entities as nodes + edges among them; each node has a `community`.
- `GET /kbs/{id}/graph?q=<title>` returns the matching entity + its 1-hop neighbors.
- `limit` is capped (e.g., `limit=1000` → ≤ 500 nodes).

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement**

`kb_platform/api/routes_graph.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Graph visualization data: Top-N entities by degree, or a search neighborhood."""

from collections import deque
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
CAP = 500


def _read(root: Path):
    ents = pd.read_parquet(root / "entities.parquet") if (root / "entities.parquet").exists() else pd.DataFrame(columns=["title", "type", "degree"])
    rels = pd.read_parquet(root / "relationships.parquet") if (root / "relationships.parquet").exists() else pd.DataFrame(columns=["source", "target", "weight", "description"])
    comms = pd.read_parquet(root / "communities.parquet") if (root / "communities.parquet").exists() else pd.DataFrame(columns=["community_id", "entity_ids"])
    return ents, rels, comms


def _title_community(comms) -> dict[str, str]:
    out: dict[str, str] = {}
    for _, row in comms.iterrows():
        cid = str(row["community_id"])
        for t in list(row.get("entity_ids", [])):
            out[str(t)] = cid
    return out


@router.get("/kbs/{kb_id}/graph")
def graph(kb_id: int, request: Request, limit: int = 200, q: str = "", hop: int = 1):
    from kb_platform.api.routes_export import _data_root

    root = _data_root(request, kb_id)
    ents, rels, comms = _read(root)
    if ents.empty:
        return {"nodes": [], "edges": []}
    tc = _title_community(comms)
    limit = max(1, min(limit, CAP))
    if q:
        matches = [str(t) for t in ents["title"] if q.lower() in str(t).lower()]
        selected = _bfs(matches, rels, hop, limit)
    else:
        ordered = ents.sort_values("degree", ascending=False).head(limit)
        selected = set(ordered["title"].astype(str))
    nodes = [
        {"id": t, "title": t, "type": str(row.get("type", "")), "degree": int(row.get("degree", 0) or 0), "community": tc.get(t)}
        for t, row in [(str(r["title"]), r) for _, r in ents[ents["title"].astype(str).isin(selected)].iterrows()]
    ]
    er = rels[rels["source"].astype(str).isin(selected) & rels["target"].astype(str).isin(selected)]
    edges = [{"source": str(r["source"]), "target": str(r["target"]), "weight": float(r.get("weight", 0) or 0), "description": str(r.get("description", ""))} for _, r in er.iterrows()]
    return {"nodes": nodes, "edges": edges}


def _bfs(seeds, rels, hop, limit):
    if not seeds:
        return set()
    adj: dict[str, list[str]] = {}
    for _, r in rels.iterrows():
        adj.setdefault(str(r["source"]), []).append(str(r["target"]))
        adj.setdefault(str(r["target"]), []).append(str(r["source"]))
    seen = set(seeds)
    dq = deque((s, 0) for s in seeds)
    while dq and len(seen) < limit:
        node, d = dq.popleft()
        if d >= hop:
            continue
        for nb in adj.get(node, []):
            if nb not in seen:
                seen.add(nb)
                dq.append((nb, d + 1))
    return seen
```

Mount in `app.py`.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_api_graph.py -q` PASS.

- [ ] **Step 5: Commit** — `feat(graph): GET /kbs/{id}/graph (Top-N by degree + search neighborhood)`

---

### Task 6 (B-frontend): GraphView

**Files:**
- Create: `web/src/components/GraphView.tsx` (+ `.test.tsx`)
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/pages/KbDetailPage.tsx`, `web/package.json`

**Interfaces:**
- Produces: `GraphView({ kbId })` fetching `/graph`, rendering react-force-graph-2d (nodes colored by community, hover tooltip, search box refetch). `getGraph(kbId, { limit, q, hop })`.

- [ ] **Step 1:** `cd web && npm i react-force-graph-2d` (note: it pulls d3 as peer; this is a UI-only dep).

- [ ] **Step 2: Types + client** — `GraphData { nodes: {id,title,type,degree,community}[]; edges: {source,target,weight,description}[] }`; `getGraph(kbId, params)`.

- [ ] **Step 3: Failing test** — `GraphView.test.tsx`: mock `getGraph` to return a 2-node fixture; assert both node labels render (react-force-graph-2d renders to canvas — test by mocking the component or asserting the search input + a title; if canvas testing is hard, render `<GraphView>` with a lightweight mock of the force-graph dep via jest/vitest `vi.mock`, asserting the fetch + a search box). Keep the test focused on data flow, not canvas pixels.

- [ ] **Step 4: Implement** — `GraphView.tsx`: fetch `/graph` on mount; render `react-force-graph-2d` with `graphData`; node color by `community` (hash→hue); a search `<input>` that refetches with `q` + `hop=2`; a "showing N" note when capped. Add `<GraphView kbId={kbId} />` to `KbDetailPage`.

- [ ] **Step 5: `npm test && npm run build`** — green + build clean.

- [ ] **Step 6: Commit** — `feat(web): GraphView (react-force-graph-2d, community-colored, search)`

---

### Task 7: Integration gate

- [ ] **Step 1:** `uv run pytest -q` green; `uv run ruff check .` clean; touched files `ruff format --check` clean.
- [ ] **Step 2:** `cd web && npm test && npm run build` green.
- [ ] **Step 3:** Manual sanity (FakeGraphAdapter-friendly): upload a `.txt`/`.md` via multipart → document list shows bytes/chunks; delete → chunks gone, graph unchanged; `/graph` returns nodes/edges; export zip contains parquet + graphml.
- [ ] **Step 4:** Commit any cleanup (or skip if none).

---

## Out of scope (separate follow-up)

- **J** Playwright E2E (build + happy path) — its own plan; needs `FakeGraphAdapter`-backed dev server.
- markitdown for binary office formats (PDF/DOCX) — markitdown supports them; a real-file smoke is manual acceptance.
- by-job cost rendering in the UI (Wave 2 returned it; not displayed).
