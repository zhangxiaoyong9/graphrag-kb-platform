# `update_clean_state` 增量收尾 + KB 图谱规模 stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让增量索引的 `update_clean_state` 步重建 `text_units.parquet`(修掉新文档 chunk 文本不被向量化的静默 bug),并给每个 job 成功后写一份 `data_root/stats.json` 图谱规模快照,在 KB 概览页展示。

**Architecture:** 两个分开的关注点。(1) `update_clean_state` 是增量计划里的真步骤(排在 `generate_text_embeddings` 前),从 chunk 表重建 `text_units.parquet`;复用从 full 路径 `_chunk_documents` 抽出的共享 helper `write_text_units_parquet`,保证两路产物同形。(2) `write_kb_stats` 是 best-effort 收尾钩子,挂在 `orchestrator.run` 成功收尾处(full + 增量都跑,在 `reconsolidate` 之后),写 `stats.json`;`GET /kbs/{id}/stats` 读它,概览页"图谱规模"卡展示。

**Tech Stack:** Python 3.11 + pandas + SQLAlchemy(已有)、FastAPI + Pydantic、React + TS + vitest + MSW。

## Global Constraints

- 无新增 DB 字段、无新增 alembic 迁移、无新增 Python/npm 依赖。
- Python ≥ 3.11,`uv run ruff check .` 通过(line-length 100);`uv run pytest` 全绿。
- 前端 `npm test` 与 `npm run build`(`tsc -b && vite build`)通过。
- `update_clean_state` 是真步骤(失败→步 FAILED→job FAILED,正确);`write_kb_stats` 是 best-effort(永不抛错拖垮 job)。
- worker 仅在 `orchestrator.run` 收尾处增量变化;`worker.py` 不得改动。
- 新增 UI 文案用中文,与现有概览页一致。
- `chunk_id` 即 `text_units.parquet` 的 `id` 列(沿用 full 路径约定)。

---

## File Structure

- **Modify** `kb_platform/engine/atomic_steps.py` — 新增 `write_text_units_parquet(data_root, chunks)` 共享 helper + `update_clean_state(repo, adapter, step)` 原子步。
- **Modify** `kb_platform/engine/orchestrator.py` — `_chunk_documents` 改用共享 helper(DRY);`_run_atomic` 把 `update_clean_state` 的 `pass` 换成真实调用;`run` 成功收尾处调 `write_kb_stats`(best-effort)。
- **Create** `kb_platform/engine/kb_stats.py` — `write_kb_stats(repo, kb_id)`:读 parquet 行数 + DB 计数 → 写 `stats.json`,永不抛错。
- **Modify** `kb_platform/api/models.py` — 加 `KbStatsOut`(全字段 `int | None`)。
- **Modify** `kb_platform/api/routes_kbs.py` — 加 `GET /kbs/{kb_id}/stats`(复用 `routes_export._data_root`)。
- **Modify** `web/src/api/types.ts` — 加 `KbStats` 接口。
- **Modify** `web/src/api/client.ts` — 加 `getKbStats(kbId)`。
- **Modify** `web/src/pages/KbOverviewPage.tsx` — 加"图谱规模"卡。
- **Create** `web/src/pages/KbOverviewPage.test.tsx` — 卡片渲染单测(MSW)。
- **Create** `docs/verify-update-clean-state-2026-06-28.md` — 验证记录。

---

## Task 1: `update_clean_state` 重建 text_units.parquet(+ 共享 helper)

**Files:**
- Modify: `kb_platform/engine/atomic_steps.py`
- Modify: `kb_platform/engine/orchestrator.py`(`_run_atomic` 路由 + `_chunk_documents` DRY 重构)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Repository.get_chunks(kb_id) -> list[Chunk]`、`Repository.get_job(job_id)`、`atomic_steps._data_root(repo, step) -> Path`(均已存在);`Chunk` 字段 `chunk_id/text/document_id`。
- Produces: `atomic_steps.write_text_units_parquet(data_root: Path, chunks: list) -> None`、`atomic_steps.update_clean_state(repo, adapter, step) -> None`。

- [ ] **Step 1: Write the failing test** — append to `tests/test_orchestrator.py`:

```python
def test_update_clean_state_rebuilds_text_units_from_chunk_table(tmp_path):
    """update_clean_state rebuilds text_units.parquet from ALL DB chunks (old+new).

    Mirrors the incremental gap: load_update_documents writes new chunk rows to
    the DB but never touches text_units.parquet. update_clean_state must rebuild
    it from the chunk table so the embeddings step covers the new chunks.
    """
    import pandas as pd

    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.enums import StepKind
    from kb_platform.db.models import Base, KnowledgeBase
    from kb_platform.db.repository import Repository
    from kb_platform.engine import atomic_steps
    from kb_platform.engine.spec import StepSpec

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # Seed two documents + their chunks in the DB (as if old + new), but NO
    # text_units.parquet yet. Documents are required because foreign_keys=ON.
    d1 = repo.add_document(kb_id=1, title="old", text="old")
    d2 = repo.add_document(kb_id=1, title="new", text="new")
    repo.add_chunks([
        Chunk(chunk_id="c-old", kb_id=1, document_id=d1.id, ordinal=0, text="old"),
        Chunk(chunk_id="c-new", kb_id=1, document_id=d2.id, ordinal=0, text="new"),
    ])
    step = repo.create_job(
        kb_id=1, type="incremental", specs=[StepSpec("update_clean_state", StepKind.ATOMIC)]
    ).steps[0]

    atomic_steps.update_clean_state(repo, adapter=object(), step=step)

    tu = pd.read_parquet(tmp_path / "text_units.parquet")
    assert len(tu) == 2
    assert set(tu["id"]) == {"c-old", "c-new"}
```

The test needs `Chunk` imported at the top of the file. Add it to the existing imports in `tests/test_orchestrator.py`:

```python
from kb_platform.db.models import Base, KnowledgeBase, Chunk
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::test_update_clean_state_rebuilds_text_units_from_chunk_table -v`
Expected: FAIL — `AttributeError: module 'kb_platform.engine.atomic_steps' has no attribute 'update_clean_state'`.

- [ ] **Step 3: Add the helper + the atomic step to `kb_platform/engine/atomic_steps.py`**

Append after the existing `merge_delta` function (before `_as_text`):

```python
def write_text_units_parquet(data_root: Path, chunks) -> None:
    """Write text_units.parquet from a list of Chunk rows.

    Shared by the full path (``_chunk_documents``) and the incremental wrap-up
    (``update_clean_state``) so both produce identical parquet. Columns mirror
    graphrag's text_units layout: id (=chunk_id), text, document_ids, n_tokens.
    No-op when there are no chunks (leaves any existing file untouched).
    """
    if not chunks:
        return
    pd.DataFrame(
        [
            {
                "id": c.chunk_id,
                "text": c.text,
                "document_ids": [str(c.document_id)],
                "n_tokens": 0,
            }
            for c in chunks
        ]
    ).to_parquet(data_root / "text_units.parquet")


def update_clean_state(repo: Repository, adapter, step) -> None:  # noqa: ARG001
    """Rebuild text_units.parquet from the chunk table (incremental wrap-up).

    ``load_update_documents`` writes new chunk rows to the DB but never updates
    text_units.parquet, so without this step the embeddings step would miss the
    new chunks' text (local search over text units would silently skip new
    documents). Rebuild from ALL chunks (old + new) right before embeddings.
    """
    root = _data_root(repo, step)
    job = repo.get_job(step.job_id)
    write_text_units_parquet(root, repo.get_chunks(job.kb_id))
```

- [ ] **Step 4: Route the step + DRY-refactor `_chunk_documents` in `kb_platform/engine/orchestrator.py`**

4a. In `_run_atomic`, replace the no-op branch:

```python
        elif step.name == "update_clean_state":
            pass  # MVP:空操作(state 合并留后续)
```

with:

```python
        elif step.name == "update_clean_state":
            atomic_steps.update_clean_state(self.repo, self.adapter, step)
```

4b. In `_chunk_documents`, replace the inline parquet write (the `if chunks:` block near the end):

```python
        if chunks:
            pd.DataFrame(
                [
                    {
                        "id": c.chunk_id,
                        "text": c.text,
                        "document_ids": [str(c.document_id)],
                        "n_tokens": 0,
                    }
                    for c in chunks
                ]
            ).to_parquet(f"{data_root}/text_units.parquet")
```

with:

```python
        if chunks:
            from pathlib import Path

            from kb_platform.engine.atomic_steps import write_text_units_parquet

            write_text_units_parquet(Path(data_root), chunks)
```

(The `import pandas as pd` and `from sqlalchemy import select` at the top of `_chunk_documents` may now be unused if this was their only use — leave them; ruff will flag in Step 6 and they're harmless. Actually check: `pd` was only used here, so remove the `import pandas as pd` line from `_chunk_documents` if ruff reports it unused.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py::test_update_clean_state_rebuilds_text_units_from_chunk_table -v`
Expected: PASS.

- [ ] **Step 6: Run ruff + the orchestrator/incremental regression**

Run: `uv run ruff check kb_platform/engine/atomic_steps.py kb_platform/engine/orchestrator.py tests/test_orchestrator.py && uv run pytest tests/test_orchestrator.py tests/test_incremental_pipeline.py -v`
Expected: ruff clean; all tests PASS (the `_chunk_documents` refactor is covered by `test_orchestrator_runs_pipeline_and_writes_parquet` + `test_full_then_incremental_only_llms_new_chunks`).

- [ ] **Step 7: Commit**

```bash
git add kb_platform/engine/atomic_steps.py kb_platform/engine/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(incr): update_clean_state rebuilds text_units.parquet"
```

---

## Task 2: `write_kb_stats` 收尾钩子(stats.json)

**Files:**
- Create: `kb_platform/engine/kb_stats.py`
- Modify: `kb_platform/engine/orchestrator.py`(`run` 收尾处调用)
- Test: `tests/test_kb_stats.py`(新建)、`tests/test_incremental_pipeline.py`、`tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Repository.get_documents(kb_id)`、`Repository.get_chunks(kb_id)`、`KnowledgeBase.data_root`。
- Produces: `kb_stats.write_kb_stats(repo: Repository, kb_id: int) -> None`;写 `<data_root>/stats.json`(字段:`updated_at/document_count/chunk_count/entity_count/relationship_count/community_count/community_report_count/text_unit_count`)。

- [ ] **Step 1: Write the failing tests** — new file `tests/test_kb_stats.py`:

```python
"""Tests for kb_platform.engine.kb_stats (stats.json snapshot, best-effort)."""

import json

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.kb_stats import write_kb_stats


@pytest.fixture()
def repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return Repository(engine), tmp_path


def test_write_kb_stats_counts_parquet_and_db_rows(repo):
    repo, root = repo
    # Two parquet artifacts with known row counts.
    pd.DataFrame({"title": ["A", "B", "C"]}).to_parquet(root / "entities.parquet")
    pd.DataFrame({"source": ["A"], "target": ["B"]}).to_parquet(root / "relationships.parquet")
    pd.DataFrame({"community": [10, 20]}).to_parquet(root / "communities.parquet")
    pd.DataFrame({"community": [10], "full_content": ["x"]}).to_parquet(root / "community_reports.parquet")
    pd.DataFrame({"id": ["c1", "c2"]}).to_parquet(root / "text_units.parquet")
    repo.add_document(kb_id=1, title="d1", text="x")
    repo.add_document(kb_id=1, title="d2", text="y")

    write_kb_stats(repo, kb_id=1)

    stats = json.loads((root / "stats.json").read_text())
    assert stats["entity_count"] == 3
    assert stats["relationship_count"] == 1
    assert stats["community_count"] == 2
    assert stats["community_report_count"] == 1
    assert stats["text_unit_count"] == 2
    assert stats["document_count"] == 2
    assert "updated_at" in stats


def test_write_kb_stats_missing_parquet_is_zero_and_never_raises(repo):
    repo, root = repo
    # No parquet at all; one document + its chunks in the DB.
    repo.add_document(kb_id=1, title="d1", text="hello world foo bar " * 50)
    # Manually add a chunk row so chunk_count > 0 without running the pipeline.
    from kb_platform.db.models import Chunk

    with session_scope(repo.engine) as s:
        s.add(Chunk(chunk_id="c1", kb_id=1, document_id=1, ordinal=0, text="t"))

    write_kb_stats(repo, kb_id=1)  # must not raise despite missing parquet

    stats = json.loads((root / "stats.json").read_text())
    assert stats["entity_count"] == 0
    assert stats["community_count"] == 0
    assert stats["document_count"] == 1
    assert stats["chunk_count"] == 1


def test_write_kb_stats_unknown_kb_is_noop(repo):
    repo, root = repo
    write_kb_stats(repo, kb_id=999)  # KB row absent
    assert not (root / "stats.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_kb_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kb_platform.engine.kb_stats'`.

- [ ] **Step 3: Implement `kb_platform/engine/kb_stats.py`**

```python
"""KB graph-scale stats snapshot: write <data_root>/stats.json at job end.

Best-effort observability: after a job finishes, count entities / relationships
/ communities / community reports / text units (parquet rows) plus documents /
chunks (DB rows) and persist the snapshot. Missing parquet -> 0; the function
never raises (stats are observability, not correctness — they must not fail a
job). Read by ``GET /kbs/{id}/stats``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository

logger = logging.getLogger(__name__)

STATS_FILE = "stats.json"

# (stats key, parquet file) — row count of each.
_PARQUET_COUNTS = (
    ("entity_count", "entities.parquet"),
    ("relationship_count", "relationships.parquet"),
    ("community_count", "communities.parquet"),
    ("community_report_count", "community_reports.parquet"),
    ("text_unit_count", "text_units.parquet"),
)


def _parquet_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_parquet(path))
    except Exception:  # noqa: BLE001 — best-effort; a bad file must not fail the job
        logger.warning("kb_stats: could not read %s; counting as 0", path)
        return 0


def write_kb_stats(repo: Repository, kb_id: int) -> None:
    """Write <data_root>/stats.json with the current graph-scale counts.

    Never raises: a missing/malformed parquet contributes 0 and is logged; an
    unknown kb_id is a silent no-op.
    """
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if kb is None:
            return
        data_root = Path(kb.data_root)

    stats: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    for key, fname in _PARQUET_COUNTS:
        stats[key] = _parquet_row_count(data_root / fname)
    stats["document_count"] = len(repo.get_documents(kb_id))
    stats["chunk_count"] = len(repo.get_chunks(kb_id))
    (data_root / STATS_FILE).write_text(json.dumps(stats))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_kb_stats.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire the call into `orchestrator.run`** — in `kb_platform/engine/orchestrator.py`, after the incremental `reconsolidate` block:

```python
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
            # Incremental jobs may have late-succeeded units (e.g. retried
            # units whose extraction landed after merge_delta already ran).
            # Reconsolidate those cached extractions into the final parquet.
            if job.type == "incremental":
                from kb_platform.reconsolidate import reconsolidate

                await reconsolidate(self.repo, self.adapter, job.kb_id, self.data_root)
```

append:

```python
            # Graph-scale stats snapshot (best-effort; never fails the job).
            try:
                from kb_platform.engine.kb_stats import write_kb_stats

                write_kb_stats(self.repo, job.kb_id)
            except Exception:
                logger.exception("write_kb_stats failed for kb %s; stats may be stale", job.kb_id)
```

- [ ] **Step 6: Add orchestrator-level assertions (stats written after full + incremental; best-effort)**

6a. In `tests/test_orchestrator.py`, extend `test_orchestrator_runs_pipeline_and_writes_parquet` — after `assert job2.status == "succeeded"`, append:

```python
    # stats.json snapshot written at full-job end.
    import json
    from pathlib import Path

    stats = json.loads(Path(data_root, "stats.json").read_text())
    assert stats["entity_count"] >= 1
    assert stats["document_count"] >= 1
```

(`entity_count`/`document_count` are guaranteed by this test's existing assertions — entities.parquet is non-empty and one document was seeded. Don't over-assert community/report counts here; `test_kb_stats.py` covers those explicitly.)

6b. In `tests/test_incremental_pipeline.py`, extend `test_full_then_incremental_only_llms_new_chunks` — at the very end (after the existing assertions), append:

```python
    # A2: update_clean_state rebuilt text_units.parquet to include doc B's new
    # chunks (the incremental gap fix), and stats.json was written at job end.
    import json

    tu = pd.read_parquet(f"{data_root}/text_units.parquet")
    assert incr_chunk_ids.issubset(set(tu["id"])), "new chunks missing from text_units.parquet"
    from pathlib import Path

    stats = json.loads(Path(data_root, "stats.json").read_text())
    assert stats["entity_count"] >= 1
```

(`pd` and `incr_chunk_ids` are already in scope in that test.)

6c. Add a best-effort test to `tests/test_orchestrator.py` — append:

```python
@pytest.mark.asyncio
async def test_job_succeeds_even_if_write_kb_stats_raises(setup, monkeypatch):
    """write_kb_stats is best-effort: a failure must not fail the job."""
    from kb_platform.engine import kb_stats

    def boom(repo, kb_id):
        raise RuntimeError("stats exploded")

    monkeypatch.setattr(kb_stats, "write_kb_stats", boom)

    repo, data_root = setup
    from kb_platform.graph.adapter import FakeGraphAdapter
    from kb_platform.graph.vector_store import FakeVectorStore

    orch = Orchestrator(
        repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, vector_store=FakeVectorStore(dim=8)
    )
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan_full())
    await orch.run(job.id)
    assert repo.get_job(job.id).status == "succeeded"
```

- [ ] **Step 7: Run the orchestrator + incremental + kb_stats suites**

Run: `uv run pytest tests/test_kb_stats.py tests/test_orchestrator.py tests/test_incremental_pipeline.py -v`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add kb_platform/engine/kb_stats.py kb_platform/engine/orchestrator.py tests/test_kb_stats.py tests/test_orchestrator.py tests/test_incremental_pipeline.py
git commit -m "feat(incr): write_kb_stats snapshot at job end (best-effort)"
```

---

## Task 3: API `GET /kbs/{kb_id}/stats`

**Files:**
- Modify: `kb_platform/api/models.py`
- Modify: `kb_platform/api/routes_kbs.py`
- Test: `tests/test_api_kbs.py`

**Interfaces:**
- Consumes: `routes_export._data_root(request, kb_id) -> Path`(已存在,KB 缺失→404)。
- Produces: `GET /kbs/{kb_id}/stats` → `KbStatsOut`(文件缺失返回全 `None` 的空对象,不 404)。

- [ ] **Step 1: Write the failing test** — append to `tests/test_api_kbs.py`:

```python
def test_get_kb_stats_returns_snapshot(tmp_path):
    """GET /kbs/{id}/stats returns the written stats.json content."""
    import json

    from fastapi.testclient import TestClient

    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    (tmp_path / "stats.json").write_text(json.dumps({
        "updated_at": "2026-06-28T00:00:00+00:00",
        "document_count": 2, "chunk_count": 5,
        "entity_count": 9, "relationship_count": 7,
        "community_count": 3, "community_report_count": 3, "text_unit_count": 5,
    }))
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    r = client.get("/kbs/1/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_count"] == 9
    assert body["community_count"] == 3
    assert body["document_count"] == 2


def test_get_kb_stats_empty_when_no_snapshot(tmp_path):
    """No stats.json yet -> 200 with all-None body (UI shows '—'), not 404."""
    from fastapi.testclient import TestClient

    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, KnowledgeBase
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    r = client.get("/kbs/1/stats")
    assert r.status_code == 200
    assert r.json() == {
        "updated_at": None, "document_count": None, "chunk_count": None,
        "entity_count": None, "relationship_count": None,
        "community_count": None, "community_report_count": None, "text_unit_count": None,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_kbs.py::test_get_kb_stats_returns_snapshot tests/test_api_kbs.py::test_get_kb_stats_empty_when_no_snapshot -v`
Expected: FAIL — 404 on `/kbs/1/stats` (route not registered).

- [ ] **Step 3: Add `KbStatsOut` to `kb_platform/api/models.py`**

Append (after `KbCostOut` / before the profile models, or at the end of the KB/cost group):

```python
class KbStatsOut(BaseModel):
    """Graph-scale snapshot read from <data_root>/stats.json.

    All fields None when no snapshot exists yet (unindexed KB) so the UI can
    degrade to '—' without a 404.
    """
    updated_at: str | None = None
    document_count: int | None = None
    chunk_count: int | None = None
    entity_count: int | None = None
    relationship_count: int | None = None
    community_count: int | None = None
    community_report_count: int | None = None
    text_unit_count: int | None = None
```

- [ ] **Step 4: Add the route to `kb_platform/api/routes_kbs.py`**

4a. Add `KbStatsOut` to the import from `kb_platform.api.models`:

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
    KbStatsOut,
    KbUpdate,
    ProfileRef,
)
```

4b. Append a new endpoint (e.g., after `get_kb`):

```python
@router.get("/kbs/{kb_id}/stats", response_model=KbStatsOut)
def get_kb_stats(kb_id: int, request: Request) -> KbStatsOut:
    """Graph-scale snapshot (entities/relationships/communities/... counts).

    Returns an all-None body (200) when no snapshot exists yet — never 404 for
    an existing KB — so the overview page can degrade gracefully.
    """
    from kb_platform.api.routes_export import _data_root

    root = _data_root(request, kb_id)  # 404 only if the KB row is absent
    path = root / "stats.json"
    if not path.exists():
        return KbStatsOut()
    return KbStatsOut(**json.loads(path.read_text()))
```

(`json` is already imported at the top of `routes_kbs.py`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_kbs.py -v`
Expected: PASS (including the two new stats tests; no regressions).

- [ ] **Step 6: Commit**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_kbs.py tests/test_api_kbs.py
git commit -m "feat(api): GET /kbs/{id}/stats graph-scale snapshot"
```

---

## Task 4: Frontend "图谱规模" 卡片

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/pages/KbOverviewPage.tsx`
- Test: `web/src/pages/KbOverviewPage.test.tsx`(新建)

**Interfaces:**
- Consumes: `useAsync(fn, deps)`(已有)、`Stat`/`Card`/`CardHeader`(已有)、`KbContext`(已有)。
- Produces: `getKbStats(kbId: number) => Promise<KbStats>`;概览页新增"图谱规模"卡。

- [ ] **Step 1: Write the failing test** — new file `web/src/pages/KbOverviewPage.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import KbOverviewPage from "./KbOverviewPage";
import { KbContext } from "./kb-context";

const kb = { id: 1, name: "kb1", method: "standard", settings: {}, llm_profile: null, embedding_profile: null };

const server = setupServer(
  http.get("/kbs/1/documents", () => HttpResponse.json([])),
  http.get("/kbs/1/jobs", () => HttpResponse.json([])),
  http.get("/kbs/1/cost", () => HttpResponse.json({ total_usd: 0, by_step: {}, by_model: {}, by_job: {} })),
  http.get("/kbs/1/stats", () =>
    HttpResponse.json({
      updated_at: "2026-06-28T00:00:00+00:00",
      document_count: 2, chunk_count: 5,
      entity_count: 9, relationship_count: 7,
      community_count: 3, community_report_count: 4, text_unit_count: 5,
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/kbs/1"]}>
      <KbContext.Provider value={{ kbId: 1, kb, reload: () => undefined }}>
        <Routes>
          <Route path="/kbs/:id" element={<KbOverviewPage />} />
        </Routes>
      </KbContext.Provider>
    </MemoryRouter>,
  );
}

test("renders graph-scale card with stats counts", async () => {
  renderPage();
  expect(await screen.findByText("图谱规模")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument();       // entity_count
  expect(screen.getByText("7")).toBeInTheDocument();       // relationship_count
  expect(screen.getByText("3")).toBeInTheDocument();       // community_count
});

test("shows dash placeholders when stats empty", async () => {
  server.use(http.get("/kbs/1/stats", () => HttpResponse.json({})));
  renderPage();
  await waitFor(() => expect(screen.getByText("图谱规模")).toBeInTheDocument());
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run src/pages/KbOverviewPage.test.tsx`
Expected: FAIL — `getKbStats is not a function` / "图谱规模" not found.

- [ ] **Step 3: Add the type + client call**

3a. In `web/src/api/types.ts`, append:

```ts
export interface KbStats {
  updated_at?: string;
  document_count?: number;
  chunk_count?: number;
  entity_count?: number;
  relationship_count?: number;
  community_count?: number;
  community_report_count?: number;
  text_unit_count?: number;
}
```

3b. In `web/src/api/client.ts`, add `KbStats` to the type import and append the call:

```ts
import type { KbOut, KbDetail, DocumentOut, DocumentDetail, EvidenceDetail, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate, QueryResult, JobCost, KbCost, GraphData, Health, ProviderProfile, ProfileCreate, KbStats } from "./types";
```

```ts
export const getKbStats = (kbId: number) => req<KbStats>(`/kbs/${kbId}/stats`);
```

- [ ] **Step 4: Add the "图谱规模" card to `web/src/pages/KbOverviewPage.tsx`**

4a. Add the import:

```tsx
import { listDocuments, listJobsByKb, getKbCost, getKbStats } from "../api/client";
```

4b. Inside the component, after the `cost` line, add:

```tsx
  const stats = useAsync(() => getKbStats(kbId).catch(() => null), [kbId]);
  const s = stats.data;
  const dash = "—";
```

4c. Add the card. Insert a new `<Card>` block right after the existing top `<div className="grid grid-cols-2 ...">...</div>` stat row (before the "快捷操作" card):

```tsx
      <Card>
        <CardHeader title="图谱规模" subtitle="最近一次索引后的实体 / 关系 / 社区计数" icon={<IconLayers width={18} height={18} />} />
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="实体" value={s?.entity_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="关系" value={s?.relationship_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="社区" value={s?.community_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="社区报告" value={s?.community_report_count ?? dash} icon={<IconLayers width={16} height={16} />} />
          <Stat label="分块" value={s?.chunk_count ?? dash} icon={<IconDoc width={16} height={16} />} />
        </div>
      </Card>
```

(`IconLayers` and `IconDoc` are already imported at the top of the file.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && npx vitest run src/pages/KbOverviewPage.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Run full frontend test + type-check/build**

Run: `cd web && npm test && npm run build`
Expected: PASS (vitest green, `tsc -b && vite build` succeeds).

- [ ] **Step 7: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/pages/KbOverviewPage.tsx web/src/pages/KbOverviewPage.test.tsx
git commit -m "feat(web): KB overview 图谱规模 card from stats snapshot"
```

---

## Task 5: 全量验证 + verify 文档

**Files:**
- Create: `docs/verify-update-clean-state-2026-06-28.md`

- [ ] **Step 1: 后端全量回归 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 全 PASS,ruff clean(无新增 unused-import 等)。

- [ ] **Step 2: 前端全量回归 + build**

Run: `cd web && npm test && npm run build`
Expected: PASS;`web/dist` 重建。

- [ ] **Step 3: 增量冒烟(可选,fake server / 真实 LLM 均可)**

跑一次 full → 加文档 → incremental 的流程(可用既有 e2e fake server,或真实 server+worker),确认:
- 增量 job 完成后 `<data_root>/text_units.parquet` 含新文档 chunk;`<data_root>/stats.json` 存在且计数合理。
- 概览页"图谱规模"卡显示计数;未索引过的 KB 显"—"。

- [ ] **Step 4: 写 `docs/verify-update-clean-state-2026-06-28.md`**

记录:环境、pytest/vitest/build 结果、上面冒烟观察(含 text_units 重建 + stats.json 计数 + 概览页展示 + 未索引 KB 降级显"—")。风格参照 `docs/verify-websocket-2026-06-28.md`。

- [ ] **Step 5: Commit**

```bash
git add docs/verify-update-clean-state-2026-06-28.md
git commit -m "docs(verify): update_clean_state + KB graph stats"
```

---

## Notes for the implementer

- `update_clean_state` 是增量计划(`plan_incremental`)里的步骤,排在 `generate_text_embeddings` **之前**——所以重建 `text_units.parquet` 正好赶在向量化之前。full 计划没这步,但 full 的 `_chunk_documents` 已写 text_units,故 full 不受影响。
- `write_text_units_parquet` 是 full(`_chunk_documents`)与增量(`update_clean_state`)的共享写出器,改一处两路同形——别再各自手写 DataFrame。
- `write_kb_stats` 在 `orchestrator.run` 收尾处、`reconsolidate` 之后跑(迟到重整合数据也计入),full + 增量都跑;调用点已包 try/except,内部每个 parquet 读取也容错,**两层都不抛**。
- `GET /kbs/{id}/stats` 复用 `routes_export._data_root`(仅 KB 行缺失才 404);stats.json 缺失返回全 None 空对象,概览页降级显"—"。
- 别动 `kb_platform/worker.py`。
- `incr_chunk_ids` 在 `test_full_then_incremental_only_llms_new_chunks` 里已是新 chunk id 集合,Task 2 Step 6b 直接复用它断言 text_units.parquet 覆盖新 chunk。
