# A3 文档删除收敛（图收缩）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除文档后图谱自动收缩 —— `merge_delta` 按 chunk 表过滤掉孤儿 extraction，删除路由自动起一个增量重索引 job（已索引过的 KB），连删合并、从未索引守卫。

**Architecture:** 修复集中在 `atomic_steps.merge_delta`（按 chunk 表过滤 + best-effort 清孤儿 `extractions/*.json`）+ `routes_kbs.delete_document`（删行后按守卫决定是否 `create_job_pending(type="incremental")`）+ `write_text_units_parquet`（删到空时写空 parquet）。复用现有增量管道（`plan_incremental` + delta 策略 + reconsolidate），不新增 step / job type / DB 字段。向量侧靠 `LanceDBVectorStoreWrapper.upsert` 的 overwrite 模式自然清空，无需 `VectorStore.delete`。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / pandas / pytest（asyncio_mode=auto）；React + TS + Vite + vitest + msw。

**Spec:** `docs/superpowers/specs/2026-06-28-doc-delete-graph-shrink-design.md`

## Global Constraints

- 无新增 DB 字段、无 alembic 迁移、无新增 Python/npm 依赖。
- 不新增 atomic step、不改 `plan_incremental`（复用现有增量管道）。
- 后端 `ruff check .` 必过（line-length 100, target py311）；`uv run pytest` 全绿。
- 前端 `npm run build`（tsc -b && vite build）必过、`npm test` 全绿。
- 测试用 `FakeGraphAdapter` / `FakeVectorStore`（无 LLM）。
- 中文 UI copy 与现有风格一致。
- extractions 目录"不能整目录删/不能删仍有效的 extraction"约定保持 —— 这里只删 chunk 已不存在的孤儿文件。

---

## File Structure

**Modify:**
- `kb_platform/engine/atomic_steps.py` —— `merge_delta`（按 chunk 表过滤 + 清孤儿）、`write_text_units_parquet`（空 chunks 写空 parquet）；顶部加 `logging`。
- `kb_platform/api/routes_kbs.py` —— `delete_document`（202/204 + 起job）、新增 `_maybe_create_shrink_job`、imports 加 `Response` / `JobCreated`。
- `web/src/api/client.ts` —— `deleteDocument` 返回 `DeleteResult`（202→jobId / 204→false）。
- `web/src/components/DocumentManager.tsx` —— 删除确认文案改为"自动重建图谱"。
- `web/src/pages/DocumentPage.tsx` —— 卡片副标题文案同步。

**Test:**
- `tests/test_merge_delta.py` —— 更新既有用例（补 chunk 行）+ 3 个新用例。
- `tests/test_atomic_steps.py` —— `write_text_units_parquet` 空写入用例。
- `tests/test_api_documents.py` —— 删除路由自动起 job / 合并 / 守卫用例。
- `tests/test_incremental_pipeline.py` —— 删除收缩端到端用例。
- `web/src/api/client.test.ts` —— `deleteDocument` 202/204 用例。
- `web/src/components/DocumentManager.test.tsx` —— 文案 + mock 返回值更新。

---

### Task 1: `merge_delta` 按 chunk 表过滤 + 清孤儿 extraction

**Files:**
- Modify: `kb_platform/engine/atomic_steps.py:1-10`（顶部加 `import logging` + logger）、`:37-56`（`merge_delta` 体）。
- Test: `tests/test_merge_delta.py`

**Interfaces:**
- Consumes: `repo.get_chunks(kb_id) -> list[Chunk]`（`.chunk_id`）、`repo.get_job(step.job_id) -> Job`（`.kb_id`）、`_data_root(repo, step)`。
- Produces: 不变签名 `merge_delta(repo, adapter, step) -> None`；行为变更 = 只合并 chunk 仍在表的 extraction，并清孤儿文件。

- [ ] **Step 1: 更新既有用例 + 写 3 个新失败用例**

把 `tests/test_merge_delta.py` 整体替换为下面内容（既有用例补上 doc + chunk 行，使 extraction 文件名 `old`/`new` 对应存活 chunk；新增过滤/清理、best-effort、空 三例）：

```python
import json
from pathlib import Path

import pandas as pd

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, Chunk, Document, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import merge_delta
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return Repository(engine)


def _merge_step(repo):
    return repo.create_job(kb_id=1, type="incremental", specs=[StepSpec("merge_delta", StepKind.ATOMIC)]).steps[0]


def _ext(tmp_path, name, entities, relationships=None):
    (tmp_path / "extractions").mkdir(exist_ok=True)
    (tmp_path / "extractions" / f"{name}.json").write_text(
        json.dumps({"entities": entities, "relationships": relationships or []})
    )


def _add_doc_with_chunks(repo, doc_id, chunk_ids):
    with session_scope(repo.engine) as s:
        s.add(Document(id=doc_id, kb_id=1, title=f"d{doc_id}", source_uri="", content_hash=f"h{doc_id}", status="parsed", bytes=1, text="t"))
        for ordinal, cid in enumerate(chunk_ids):
            s.add(Chunk(chunk_id=cid, kb_id=1, document_id=doc_id, ordinal=ordinal, text=cid))


def test_merge_delta_combines_extractions_for_live_chunks(tmp_path):
    repo = _setup(tmp_path)
    _add_doc_with_chunks(repo, 1, ["old", "new"])  # 两个 chunk 都存活
    _ext(tmp_path, "old", [{"title": "ACME", "type": "ORG", "description": "old desc", "source_id": "old"}])
    _ext(tmp_path, "new", [
        {"title": "ACME", "type": "ORG", "description": "new desc", "source_id": "new"},
        {"title": "GLOBEX", "type": "ORG", "description": "globex", "source_id": "new"},
    ], relationships=[{"source": "ACME", "target": "GLOBEX", "weight": 1.0, "description": "acquires", "source_id": "new"}])
    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    rels = pd.read_parquet(tmp_path / "relationships.parquet")
    assert set(ents["title"]) == {"ACME", "GLOBEX"}
    assert ents[ents["title"] == "ACME"].iloc[0]["frequency"] == 2
    assert len(rels) == 1 and rels.iloc[0]["source"] == "ACME" and rels.iloc[0]["target"] == "GLOBEX"


def test_merge_delta_filters_and_prunes_orphan_extractions(tmp_path):
    repo = _setup(tmp_path)
    _add_doc_with_chunks(repo, 1, ["keep1", "keep2"])  # gone 不在表里
    _ext(tmp_path, "keep1", [{"title": "KEEP1", "type": "ORG", "description": "k1", "source_id": "keep1"}])
    _ext(tmp_path, "keep2", [{"title": "KEEP2", "type": "ORG", "description": "k2", "source_id": "keep2"}])
    _ext(tmp_path, "gone", [{"title": "GONE", "type": "ORG", "description": "g", "source_id": "gone"}])
    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    assert set(ents["title"]) == {"KEEP1", "KEEP2"}          # 孤儿 GONE 不合并
    assert not (tmp_path / "extractions" / "gone.json").exists()  # 孤儿文件被清
    assert (tmp_path / "extractions" / "keep1.json").exists()     # 存活文件保留
    assert (tmp_path / "extractions" / "keep2.json").exists()


def test_merge_delta_prune_failure_is_best_effort(tmp_path, monkeypatch):
    repo = _setup(tmp_path)
    _add_doc_with_chunks(repo, 1, ["keep"])
    _ext(tmp_path, "keep", [{"title": "KEEP", "type": "ORG", "description": "k", "source_id": "keep"}])
    _ext(tmp_path, "gone", [{"title": "GONE", "type": "ORG", "description": "g", "source_id": "gone"}])

    def boom(self, missing_ok=False):  # noqa: ARG001
        raise OSError("disk on fire")
    monkeypatch.setattr(Path, "unlink", boom)

    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))  # 不抛
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    assert set(ents["title"]) == {"KEEP"}                      # 过滤已保证正确
    assert (tmp_path / "extractions" / "gone.json").exists()   # 清理失败，文件仍在


def test_merge_delta_empty_when_no_live_chunks(tmp_path):
    repo = _setup(tmp_path)
    # 不加任何 chunk → live 为空；但磁盘上有两个孤儿 extraction
    _ext(tmp_path, "a", [{"title": "A", "type": "ORG", "description": "a", "source_id": "a"}])
    _ext(tmp_path, "b", [{"title": "B", "type": "ORG", "description": "b", "source_id": "b"}])
    merge_delta(repo, FakeGraphAdapter(), _merge_step(repo))
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    rels = pd.read_parquet(tmp_path / "relationships.parquet")
    assert len(ents) == 0 and len(rels) == 0                   # 空 schema
    assert set(ents.columns) == {"title", "type", "description", "text_unit_ids", "frequency"}
    assert not (tmp_path / "extractions" / "a.json").exists()  # 孤儿被清
```

- [ ] **Step 2: 跑新用例确认失败**

Run: `uv run pytest tests/test_merge_delta.py -v`
Expected: `test_merge_delta_filters_and_prunes_orphan_extractions`、`test_merge_delta_prune_failure_is_best_effort`、`test_merge_delta_empty_when_no_live_chunks` **FAIL**（当前实现 glob 全部、不清孤儿、不空）；`test_merge_delta_combines_extractions_for_live_chunks` PASS（补了 chunk 行，当前实现照样 glob 全部）。

- [ ] **Step 3: 实现 merge_delta 过滤 + 清孤儿**

`kb_platform/engine/atomic_steps.py` 顶部 imports 区加（与现有 `import json` / `from pathlib import Path` 同区）：

```python
import logging
```

顶部常量区（`logger = logging.getLogger(__name__)` 紧跟 imports 之后、函数之前）加：

```python
logger = logging.getLogger(__name__)
```

把 `merge_delta` 整体替换为：

```python
def merge_delta(repo: Repository, adapter, step) -> None:
    """Re-merge on-disk chunk extractions whose chunk still exists in the control plane.

    Extractions are cached per chunk under ``data_root/extractions/<chunk_id>.json``.
    A document deletion removes the Chunk row but leaves its extraction file behind,
    so globbing every file would re-merge orphans and keep deleted entities alive.
    The chunk table is the source of truth: only extractions whose ``chunk_id`` is
    still present are loaded, and orphan files are best-effort pruned (an unlink
    failure never blocks the merge — the filter already guarantees correctness).
    No LLM.
    """
    from kb_platform.graph.adapter import ExtractionResult

    root = _data_root(repo, step)
    job = repo.get_job(step.job_id)
    live = {c.chunk_id for c in repo.get_chunks(job.kb_id)}
    extraction_dir = root / "extractions"
    results: list[ExtractionResult] = []
    if extraction_dir.exists():
        for p in sorted(extraction_dir.glob("*.json")):
            if p.stem not in live:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    logger.warning("merge_delta: could not prune orphan extraction %s", p)
                continue
            raw = json.loads(p.read_text())
            results.append(
                ExtractionResult(
                    entities=pd.DataFrame(raw["entities"]),
                    relationships=pd.DataFrame(raw["relationships"]),
                )
            )
    entities, relationships = adapter.merge_extractions(results)
    entities.to_parquet(root / "entities.parquet")
    relationships.to_parquet(root / "relationships.parquet")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_merge_delta.py -v`
Expected: 4 passed。

- [ ] **Step 5: 回归 + lint + 提交**

Run: `uv run pytest tests/test_incremental_pipeline.py tests/test_orchestrator.py tests/test_atomic_steps.py -v && uv run ruff check kb_platform/engine/atomic_steps.py tests/test_merge_delta.py`
Expected: 全绿、ruff clean（增量管道用的是真实 chunk → live 命中 → 行为不变）。

```bash
git add kb_platform/engine/atomic_steps.py tests/test_merge_delta.py
git commit -m "feat(incr): merge_delta filters by chunk table + prunes orphan extractions"
```

---

### Task 2: `write_text_units_parquet` 空 chunks 写空 parquet

**Files:**
- Modify: `kb_platform/engine/atomic_steps.py:61-83`（`write_text_units_parquet`）。
- Test: `tests/test_atomic_steps.py`

**Interfaces:**
- Consumes: 无新依赖。
- Produces: `write_text_units_parquet(data_root, chunks)` —— chunks 为空时写出零行但列正确的 `text_units.parquet`（供删到空场景覆盖 stale 向量）。

- [ ] **Step 1: 写失败用例**

在 `tests/test_atomic_steps.py` 末尾追加（若该文件无 `write_text_units_parquet` / `pd` import，按需补 `import pandas as pd` 与 `from kb_platform.engine.atomic_steps import write_text_units_parquet`）：

```python
def test_write_text_units_parquet_empty_writes_zero_rows_with_columns(tmp_path):
    out = tmp_path / "text_units.parquet"
    write_text_units_parquet(tmp_path, [])
    assert out.exists()  # 不再 no-op
    df = pd.read_parquet(out)
    assert list(df.columns) == ["id", "text", "document_ids", "n_tokens"]
    assert len(df) == 0
```

- [ ] **Step 2: 跑确认失败**

Run: `uv run pytest tests/test_atomic_steps.py::test_write_text_units_parquet_empty_writes_zero_rows_with_columns -v`
Expected: FAIL（当前 `if not chunks: return` → 文件不存在，`out.exists()` 断言失败）。

- [ ] **Step 3: 实现 —— 去掉空 no-op，显式列**

把 `write_text_units_parquet` 整体替换为：

```python
def write_text_units_parquet(data_root: Path, chunks) -> None:
    """Write text_units.parquet from a list of Chunk rows.

    Shared by the full path (``_chunk_documents``) and the incremental wrap-up
    (``update_clean_state``) so both produce identical parquet. Columns mirror
    graphrag's text_units layout: id (=chunk_id), text, document_ids, n_tokens.
    Writes a zero-row parquet when there are no chunks so a delete-to-empty KB
    still overwrites stale text-unit vectors in the embeddings step instead of
    leaving the previous file untouched.
    """
    pd.DataFrame(
        [
            {
                "id": c.chunk_id,
                "text": c.text,
                "document_ids": [str(c.document_id)],
                "n_tokens": 0,
            }
            for c in chunks
        ],
        columns=["id", "text", "document_ids", "n_tokens"],
    ).to_parquet(data_root / "text_units.parquet")
```

- [ ] **Step 4: 跑确认通过**

Run: `uv run pytest tests/test_atomic_steps.py -v`
Expected: 全绿（含既有非空写入用例 —— 显式 columns 对非空 list 同样正确）。

- [ ] **Step 5: lint + 提交**

Run: `uv run ruff check kb_platform/engine/atomic_steps.py tests/test_atomic_steps.py`
Expected: clean。

```bash
git add kb_platform/engine/atomic_steps.py tests/test_atomic_steps.py
git commit -m "feat(incr): write_text_units_parquet writes empty parquet for delete-to-empty"
```

---

### Task 3: 删除路由自动起增量 job（A + A1 合并 + 从未索引守卫）

**Files:**
- Modify: `kb_platform/api/routes_kbs.py`（imports、`delete_document`、新增 `_maybe_create_shrink_job`）。
- Test: `tests/test_api_documents.py`

**Interfaces:**
- Consumes: `repo.delete_document`、`repo.list_jobs_by_kb -> list[Job]`（`.type`/`.status`）、`repo.create_job_pending(kb_id, method, type)`、`repo.set_job_status`、`JobStatus`、`JobCreated`。
- Produces: `DELETE /kbs/{id}/documents/{doc_id}` —— 202+`JobCreated` 当起了 job；204 无 body 当未起；404 不变。

- [ ] **Step 1: 写 4 个失败用例**

在 `tests/test_api_documents.py` 顶部 import 区补：

```python
from kb_platform.db.enums import JobStatus
```

在文件末尾追加（沿用 `repo_and_client` fixture，它已 seed KB 1、无 job）：

```python
def _seed_doc(repo, doc_id=10):
    with session_scope(repo.engine) as s:
        s.add(Document(id=doc_id, kb_id=1, title="d", source_uri="", content_hash="h", status="parsed", bytes=1, text="x"))


def test_delete_auto_creates_shrink_job_when_indexed(repo_and_client):
    repo, client = repo_and_client
    j = repo.create_job_pending(kb_id=1, method="standard", type="full")
    repo.set_job_status(j.id, JobStatus.SUCCEEDED)  # 标记已索引
    _seed_doc(repo, 10)
    r = client.delete("/kbs/1/documents/10")
    assert r.status_code == 202
    assert r.json()["status"] == "pending"
    assert any(x.type == "incremental" for x in repo.list_jobs_by_kb(1))


def test_delete_no_job_when_never_indexed(repo_and_client):
    repo, client = repo_and_client
    _seed_doc(repo, 10)  # 无任何 SUCCEEDED job
    r = client.delete("/kbs/1/documents/10")
    assert r.status_code == 204
    assert repo.list_jobs_by_kb(1) == []


def test_delete_coalesces_when_incremental_job_active(repo_and_client):
    repo, client = repo_and_client
    repo.create_job_pending(kb_id=1, method="standard", type="full")
    repo.set_job_status(repo.list_jobs_by_kb(1)[0].id, JobStatus.SUCCEEDED)
    repo.create_job_pending(kb_id=1, method="standard", type="incremental")  # 已有 pending 增量
    _seed_doc(repo, 10)
    r = client.delete("/kbs/1/documents/10")
    assert r.status_code == 204  # 合并：不另起
    # 仍只有一个 incremental job
    assert sum(1 for x in repo.list_jobs_by_kb(1) if x.type == "incremental") == 1


def test_delete_creates_job_when_only_full_job_active(repo_and_client):
    repo, client = repo_and_client
    repo.create_job_pending(kb_id=1, method="standard", type="full")
    repo.set_job_status(repo.list_jobs_by_kb(1)[0].id, JobStatus.SUCCEEDED)
    pending_full = repo.create_job_pending(kb_id=1, method="standard", type="full")  # 在跑 full
    _seed_doc(repo, 10)
    r = client.delete("/kbs/1/documents/10")
    assert r.status_code == 202  # full 在跑 → 仍另起增量
    incr = [x for x in repo.list_jobs_by_kb(1) if x.type == "incremental"]
    assert len(incr) == 1 and incr[0].id != pending_full.id
```

- [ ] **Step 2: 跑确认失败**

Run: `uv run pytest tests/test_api_documents.py -v`
Expected: 4 个新用例 FAIL（当前路由永远 204、从不起 job）；既有 `test_delete_document_cascades_chunks` / `test_delete_document_wrong_kb_404` / `test_delete_missing_document_404` 仍 PASS（无 SUCCEEDED job → 走守卫 204 / 404）。

- [ ] **Step 3: 改 imports**

`kb_platform/api/routes_kbs.py` 的 fastapi import 行改为：

```python
from fastapi import APIRouter, HTTPException, Request, Response
```

models import 块里加 `JobCreated`（在 `JobListItem,` 附近）：

```python
from kb_platform.api.models import (
    DocumentCitationOut,
    DocumentCreate,
    DocumentDetailOut,
    DocumentOut,
    EvidenceOut,
    EvidenceSourceOut,
    JobCreated,
    JobListItem,
    KbCreate,
    KbDetailOut,
    KbOut,
    KbStatsOut,
    KbUpdate,
    ProfileRef,
)
```

- [ ] **Step 4: 替换 delete_document + 加 _maybe_create_shrink_job**

把 `delete_document` 整体替换为：

```python
@router.delete("/kbs/{kb_id}/documents/{doc_id}")
def delete_document(kb_id: int, doc_id: int, request: Request, response: Response):
    """Delete a document and its chunks; auto-trigger an incremental shrink job.

    The shrink job rebuilds entities/relationships/text_units/vectors from the
    remaining chunks (merge_delta prunes orphan extractions). Returns 202 + the
    new job when one is created; 204 when no job is needed (KB never indexed, or
    an incremental job is already pending/running and will absorb the deletion).
    404 if the document does not exist or belongs to a different KB.
    """
    repo = request.app.state.repo
    if not repo.delete_document(kb_id, doc_id):
        raise HTTPException(404)
    job = _maybe_create_shrink_job(repo, kb_id)
    if job is not None:
        response.status_code = 202
        return JobCreated(id=job.id, status=job.status)
    response.status_code = 204
    return None


def _maybe_create_shrink_job(repo, kb_id: int):
    """Create an incremental shrink job after a deletion, or None if unnecessary.

    - Coalesce: an incremental job already pending/running will reconcile via
      merge_delta, so don't start another.
    - Never-indexed guard: a KB with no prior SUCCEEDED job has nothing to shrink.
    A pending/running *full* job is NOT a coalesce trigger — the new incremental
    job queues behind it and its merge_delta picks up the deletion.
    """
    from kb_platform.db.enums import JobStatus

    jobs = repo.list_jobs_by_kb(kb_id)
    if any(
        j.type == "incremental" and j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        for j in jobs
    ):
        return None
    if not any(j.status == JobStatus.SUCCEEDED for j in jobs):
        return None
    return repo.create_job_pending(kb_id=kb_id, method="standard", type="incremental")
```

- [ ] **Step 5: 跑确认通过**

Run: `uv run pytest tests/test_api_documents.py -v`
Expected: 全绿（4 新 + 3 既有）。

- [ ] **Step 6: lint + 提交**

Run: `uv run ruff check kb_platform/api/routes_kbs.py tests/test_api_documents.py`
Expected: clean。

```bash
git add kb_platform/api/routes_kbs.py tests/test_api_documents.py
git commit -m "feat(api): delete document auto-triggers incremental shrink job (coalesced)"
```

---

### Task 4: 删除收缩端到端（增量管道）

**Files:**
- Test: `tests/test_incremental_pipeline.py`（复用其 `kb` fixture 模式）

**Interfaces:**
- Consumes: Task 1 的 `merge_delta`、Task 2 的 `write_text_units_parquet`、`repo.delete_document`、`repo.create_job_pending`、`Orchestrator.run`。

- [ ] **Step 1: 写两个端到端用例**

在 `tests/test_incremental_pipeline.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_delete_doc_shrinks_unique_entities_keeps_shared(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="A", text="SHARED ALPHA " * 200)   # 实体 SHARED, ALPHA
    repo.add_document(kb_id=1, title="B", text="SHARED BETA " * 200)    # 实体 SHARED, BETA
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path))
    full = repo.create_job_pending(kb_id=1, method="standard", type="full")
    await orch.run(full.id)
    import pandas as pd
    assert {"SHARED", "ALPHA", "BETA"} <= set(pd.read_parquet(f"{tmp_path}/entities.parquet")["title"])

    # 删文档 B，跑增量 → BETA（B 独有）消失，SHARED（A 也有）保留
    repo.delete_document(kb_id=1, doc_id=2)
    incr = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    await orch.run(incr.id)
    titles = set(pd.read_parquet(f"{tmp_path}/entities.parquet")["title"])
    assert "BETA" not in titles
    assert {"SHARED", "ALPHA"} <= titles


@pytest.mark.asyncio
async def test_delete_last_doc_yields_empty_graph(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="A", text="SHARED ALPHA " * 200)
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path))
    full = repo.create_job_pending(kb_id=1, method="standard", type="full")
    await orch.run(full.id)

    repo.delete_document(kb_id=1, doc_id=1)  # 删到空
    incr = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    await orch.run(incr.id)
    assert repo.get_job(incr.id).status == "succeeded"
    import pandas as pd
    assert len(pd.read_parquet(f"{tmp_path}/entities.parquet")) == 0
    assert len(pd.read_parquet(f"{tmp_path}/relationships.parquet")) == 0
    assert len(pd.read_parquet(f"{tmp_path}/communities.parquet")) == 0
    tu = pd.read_parquet(f"{tmp_path}/text_units.parquet")
    assert list(tu.columns) == ["id", "text", "document_ids", "n_tokens"] and len(tu) == 0
```

- [ ] **Step 2: 跑确认通过**

Run: `uv run pytest tests/test_incremental_pipeline.py -v`
Expected: 全绿（含既有 `test_full_then_incremental_only_llms_new_chunks`）。空图路径已验证安全：`cluster_relationships(空)` 返回空 DF（`adapter.py:221-223`）、`finalize_entities_relationships(空,空)` 有 `if not *.empty` 守卫（`adapter.py:233-240`）、`CommunityReportsDeltaStrategy.next_units_batch` 对空 communities 不迭代直接返回 `None`（`delta.py:108-119`）→ 空步 SUCCEEDED。

- [ ] **Step 3: 提交**

```bash
git add tests/test_incremental_pipeline.py
git commit -m "test(incr): delete document shrinks graph end-to-end (unique gone, shared kept, empty ok)"
```

---

### Task 5: 前端 —— 修正误导文案 + deleteDocument 返回 job 信号

**Files:**
- Modify: `web/src/api/client.ts`（`deleteDocument`）、`web/src/components/DocumentManager.tsx`（确认文案）、`web/src/pages/DocumentPage.tsx`（副标题 + 文件注释）。
- Test: `web/src/api/client.test.ts`、`web/src/components/DocumentManager.test.tsx`。

**Interfaces:**
- Consumes: Task 3 的 202/204 契约。
- Produces: `deleteDocument(kbId, docId): Promise<DeleteResult>`，`DeleteResult = { shrinkJobCreated: boolean; jobId?: number }`。

- [ ] **Step 1: client.test.ts 加 202/204 用例**

`web/src/api/client.test.ts` 的 import 行加 `deleteDocument`：

```ts
import { createKb, deleteDocument, getDocumentDetail, getDocumentEvidence, listKbs, retryUnit } from "./client";
```

文件末尾追加：

```ts
test("deleteDocument returns shrinkJobCreated + jobId on 202", async () => {
  server.use(
    http.delete("/kbs/1/documents/9", () =>
      HttpResponse.json({ id: 42, status: "pending" }, { status: 202 }),
    ),
  );
  expect(await deleteDocument(1, 9)).toEqual({ shrinkJobCreated: true, jobId: 42 });
});

test("deleteDocument returns shrinkJobCreated false on 204", async () => {
  server.use(
    http.delete("/kbs/1/documents/9", () => new HttpResponse(null, { status: 204 })),
  );
  expect(await deleteDocument(1, 9)).toEqual({ shrinkJobCreated: false });
});
```

- [ ] **Step 2: 改 deleteDocument 返回 DeleteResult**

`web/src/api/client.ts` 把现有 `deleteDocument` 替换为：

```ts
export interface DeleteResult {
  /** true when the server auto-created an incremental shrink job (HTTP 202). */
  shrinkJobCreated: boolean;
  jobId?: number;
}

export const deleteDocument = async (kbId: number, docId: number): Promise<DeleteResult> => {
  const r = await fetch(`/kbs/${kbId}/documents/${docId}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`${r.status} /kbs/${kbId}/documents/${docId}`);
  if (r.status === 202) {
    const body = (await r.json()) as { id: number; status: string };
    return { shrinkJobCreated: true, jobId: body.id };
  }
  return { shrinkJobCreated: false };
};
```

- [ ] **Step 3: 更新 DocumentManager 确认文案**

`web/src/components/DocumentManager.tsx` 的 `onDelete` 里把 confirm 文案改为：

```ts
    const ok = window.confirm(
      `确定删除文档「${doc.title}」吗？\n\n删除后将自动重建图谱（增量任务）。`,
    );
```

（`await deleteDocument(kbId, doc.id);` 与 `reload();` 保持不变 —— shrink job 的可见性由 A1 realtime 覆盖。）

- [ ] **Step 4: 更新 DocumentManager.test.tsx 既有用例**

把 `delete button confirms...` 用例的文案断言与两个 spy mock 改为：

```ts
test("delete button confirms with auto-rebuild copy and calls deleteDocument", async () => {
  const spy = vi.spyOn(client, "deleteDocument").mockResolvedValue({ shrinkJobCreated: false });
  const reload = vi.fn();
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderManager(reload);
  fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);
  expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/自动重建图谱/));
  await waitFor(() => expect(spy).toHaveBeenCalledWith(1, 1));
  await waitFor(() => expect(reload).toHaveBeenCalled());
  spy.mockRestore();
  confirmSpy.mockRestore();
});

test("delete is cancelled when confirm returns false", async () => {
  const spy = vi.spyOn(client, "deleteDocument").mockResolvedValue({ shrinkJobCreated: false });
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderManager();
  fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);
  expect(spy).not.toHaveBeenCalled();
  spy.mockRestore();
});
```

- [ ] **Step 5: 更新 DocumentPage 副标题 + 注释**

`web/src/pages/DocumentPage.tsx`：
- 文件顶注释 `/** Documents tab: list / upload / paste / delete (graph does not shrink). */` 改为 `/** Documents tab: list / upload / paste / delete (deletion auto-rebuilds the graph). */`
- `CardHeader` 的 `subtitle="上传文件或粘贴文本；删除文档不会回缩图谱，需重跑增量任务"` 改为 `subtitle="上传文件或粘贴文本；删除文档将自动重建图谱（增量）"`

- [ ] **Step 6: 跑前端测试 + 构建**

Run: `cd web && npm test -- silent && npm run build`
Expected: vitest 全绿（含新 client 用例 + 改后的 DocumentManager 用例）、tsc+vite build 通过。

- [ ] **Step 7: 提交**

```bash
git add web/src/api/client.ts web/src/components/DocumentManager.tsx web/src/pages/DocumentPage.tsx web/src/api/client.test.ts web/src/components/DocumentManager.test.tsx
git commit -m "feat(web): delete auto-rebuilds graph — fix copy + deleteDocument returns shrink-job signal"
```

---

### Task 6: 全量回归 + verify 记录

- [ ] **Step 1: 后端全绿 + lint**

Run: `uv run pytest && uv run ruff check .`
Expected: 全绿、ruff clean。

- [ ] **Step 2: 前端全绿 + 构建**

Run: `cd web && npm test && npm run build`
Expected: 全绿、build 通过。

- [ ] **Step 3: 写 verify 记录**

写 `docs/verify-doc-delete-graph-shrink-2026-06-28.md`：记录后端 N passed / ruff clean、前端 N passed / build clean、覆盖的契约（merge_delta 过滤清孤儿、删除自动起 job/合并/守卫、删到空、前端文案）；真实 server+worker 浏览器冒烟（可选）留给用户：full → 删文档 → 看自动 incremental job → 确认 entities.parquet 收缩、概览页图谱规模计数下降。

```bash
git add docs/verify-doc-delete-graph-shrink-2026-06-28.md
git commit -m "docs(verify): doc-delete graph shrink"
```
