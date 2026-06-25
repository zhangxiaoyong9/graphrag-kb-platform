# Phase 3a — 增量索引 + 重新整合 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 加新文档时只对新 chunk 跑 LLM、把新实体/关系并入老图(新↔老关系),旧文档零重算;增量 job 收尾自动消费 `needs_reconsolidation`。

**Architecture:** 独立 `plan_incremental()`(full 管道零改动)。关键洞见:旧 chunk 的抽取结果在 `data_root/extractions/` 已缓存(2a 持久化),故 `merge_delta` = 用 2a 的 `merge_extractions` 重新合并**所有**磁盘抽取(无 LLM、无 graphrag 耦合、无需 delta 命名空间)。delta 感知只用在两处:extract_graph(仅新 chunk)+ community_reports(仅变化社区)。增量 job 收尾自动跑重新整合。

**Tech Stack:** Python 3.11–3.13 · SQLAlchemy 2.x · SQLite(WAL)· pandas/pyarrow · `graphrag==3.1.*`(本期**不新增** graphrag 耦合)· pytest。

## Global Constraints

- Python `>=3.11,<3.14`;SQLite WAL;**full 管道零改动**(2a/2b 的 78+7 测试必须回归通过)。
- graphrag 内部仍只能在 `kb_platform/graph/graphrag_adapter.py` 引用 —— **本期不新增** graphrag 耦合点(merge_delta 用平台自己的 `merge_extractions`)。
- Detached ORM 对象只读标量列。
- 每任务 TDD:失败测试 → 红 → 最小实现 → 绿 → 提交;约定式前缀。
- embeddings / 查询 / 文档删除 不在本计划(Phase 3b / 后续)。

## 关键接口契约(跨任务共享)

```python
# kb_platform/engine/orchestrator.py (Task 1 扩展)
@staticmethod
def plan_full() -> list[StepSpec]: ...          # 现有 6 步,不变
@staticmethod
def plan_incremental() -> list[StepSpec]: ...   # 新:delta 步序列
async def run(self, job_id, min_success_ratio=1.0) -> None:  # 读 job.type 选 plan
```

```python
# kb_platform/db/repository.py (Task 1)
def create_job_pending(self, kb_id, method="standard", type="full") -> Job: ...
```

```python
# kb_platform/engine/strategies/extract_graph.py (Task 2) — 增量变体
class ExtractGraphDeltaStrategy(ExtractGraphStrategy):
    def __init__(self, new_chunk_ids: set[str]): ...
    # next_units_batch 只返回 new_chunk_ids 内、无成功 unit 的 chunk
```

```python
# kb_platform/engine/atomic_steps.py (Task 3) — 新
def merge_delta(repo, adapter, step) -> None:
    # 读 data_root/extractions/*.json(全部)→ adapter.merge_extractions → 写 entities/relationships.parquet
```

---

### Task 1: job.type 路由 + `plan_incremental()` 骨架

**Files:**
- Modify: `kb_platform/db/repository.py`(`create_job_pending` 加 `type`)
- Modify: `kb_platform/engine/orchestrator.py`(`plan_full`/`plan_incremental` + run 按 type 选 plan)
- Test: `tests/test_orchestrator.py`(扩充)

**Interfaces:**
- Produces: `plan_full()`(现有,重命名自 `plan()`)、`plan_incremental()`(返回 delta 步名占位,后续任务填真实步骤)、`run` 按 `job.type` 选 plan;`create_job_pending(type="full")`。

- [ ] **Step 1: 写失败测试**

`tests/test_orchestrator.py` 追加:
```python
def test_plan_incremental_returns_delta_steps():
    from kb_platform.engine.orchestrator import Orchestrator

    names = [s.name for s in Orchestrator.plan_incremental()]
    # 起步:至少含 load_update_documents + extract_graph + merge_delta(后续任务补全)
    assert "extract_graph" in names
    assert "merge_delta" in names


def test_plan_full_unchanged():
    from kb_platform.engine.orchestrator import Orchestrator

    assert [s.name for s in Orchestrator.plan_full()] == [
        "chunk_documents", "extract_graph", "summarize_descriptions",
        "finalize_graph", "create_communities", "community_reports",
    ]
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_orchestrator.py -q`
Expected: FAIL(`plan_incremental`/`plan_full` 不存在)。

- [ ] **Step 3: 改 orchestrator**

把现有 `plan()` 重命名为 `plan_full()`;加 `plan_incremental()`;`run` 读 job.type:
```python
    @staticmethod
    def plan_full() -> list[StepSpec]:
        return [
            StepSpec("chunk_documents", StepKind.ATOMIC),
            StepSpec("extract_graph", StepKind.UNIT_FANOUT),
            StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT),
            StepSpec("finalize_graph", StepKind.ATOMIC),
            StepSpec("create_communities", StepKind.ATOMIC),
            StepSpec("community_reports", StepKind.UNIT_FANOUT),
        ]

    @staticmethod
    def plan_incremental() -> list[StepSpec]:
        # delta 步:只对新 chunk 抽取 → 合并 → 重聚类/报告受影响社区 → 收尾
        return [
            StepSpec("load_update_documents", StepKind.ATOMIC),
            StepSpec("create_base_text_units", StepKind.ATOMIC),
            StepSpec("extract_graph", StepKind.UNIT_FANOUT),
            StepSpec("merge_delta", StepKind.ATOMIC),
            StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT),
            StepSpec("finalize_graph", StepKind.ATOMIC),
            StepSpec("create_communities", StepKind.ATOMIC),
            StepSpec("community_reports", StepKind.UNIT_FANOUT),
            StepSpec("update_clean_state", StepKind.ATOMIC),
        ]
```
`run` 选 plan(读 job.type):
```python
    async def run(self, job_id, min_success_ratio=1.0) -> None:
        self.repo.set_job_status(job_id, JobStatus.RUNNING)
        try:
            job = self.repo.get_job(job_id)
            plan_name = "plan_incremental" if job.type == "incremental" else "plan_full"
            # plan 只用于日志/校验;steps 在 create_job_pending 时已按 type 建好
            for step in self.repo.get_steps(job_id):
                if step.status == StepStatus.SUCCEEDED:
                    continue
                await self._run_step(step, min_success_ratio)
                if self.repo.get_step(step.id).status != StepStatus.SUCCEEDED:
                    self.repo.set_job_status(job_id, JobStatus.FAILED)
                    return
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
        except Exception:
            logger.exception("job %s failed", job_id)
            self.repo.set_job_status(job_id, JobStatus.FAILED)
            raise
```
> `create_job_pending` 按 `type` 用 `plan_full()`/`plan_incremental()` 建对应 steps。

- [ ] **Step 4: 改 repository `create_job_pending`**

```python
    def create_job_pending(self, kb_id: int, method: str = "standard", type: str = "full") -> Job:
        from kb_platform.engine.orchestrator import Orchestrator

        specs = Orchestrator.plan_incremental() if type == "incremental" else Orchestrator.plan_full()
        return self.create_job(kb_id=kb_id, type=type, specs=specs, method=method)
```

- [ ] **Step 5: 跑绿 + 全量(此时 load_update_documents/merge_delta/update_clean_state 还没实现,先让 plan 结构测试过;完整跑通在后续任务)**

Run: `uv run pytest tests/test_orchestrator.py -q && uv run pytest -q`
Expected: 两个新测试过 + 既有回归通过(注意:`run` 现在跳过 SUCCEEDED 步,既有 e2e 仍绿)。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/engine/orchestrator.py kb_platform/db/repository.py tests/test_orchestrator.py
git commit -m "feat: job.type routing + plan_incremental skeleton"
```

---

### Task 2: 增量文档加载 + delta manifest + delta-filtered extract_graph

**Files:**
- Create: `kb_platform/engine/incremental.py`(`load_update_documents`、`create_base_text_units` 增量版、manifest 读写、`ExtractGraphDeltaStrategy`)
- Modify: `kb_platform/engine/strategies/__init__.py`(注册 delta extract)
- Test: `tests/test_incremental_extract.py`

**Interfaces:**
- Produces: `load_update_documents(repo, adapter, step)`(只加载新文档 + 写 `delta_manifest.json`)、`ExtractGraphDeltaStrategy(new_chunk_ids)`(next_units_batch 只返回新 chunk)、`read_delta_manifest(data_root) -> set[str]`。

- [ ] **Step 1: 写失败测试**

`tests/test_incremental_extract.py`:
```python
import json

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def repo_with_old_index(tmp_path):
    """一个已有 full 索引(老 chunk 在 extractions/ + entities.parquet)的 KB。"""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="old", text="Old ACME Org text " * 200)
    # 模拟老 chunk 已在 DB + 磁盘抽取(老 chunk 不应被增量重抽)
    from kb_platform.graph.adapter import FakeGraphAdapter, ExtractionResult

    fake = FakeGraphAdapter()
    old_chunks = fake.chunk_document(1, repo.get_documents(1)[0].text)
    from kb_platform.db.models import Chunk

    with session_scope(engine) as s:
        for i, c in enumerate(old_chunks):
            s.add(Chunk(chunk_id=c.chunk_id, kb_id=1, document_id=1, ordinal=i, text=c.text))
    # 写老 extractions 到磁盘(模拟 full job 已跑)
    import pathlib

    (pathlib.Path(tmp_path) / "extractions").mkdir(exist_ok=True)
    for c in old_chunks:
        r = fake.extract_chunk_sync(c.chunk_id, c.text)
        (pathlib.Path(tmp_path) / "extractions" / f"{c.chunk_id}.json").write_text(json.dumps({"entities": r.entities.to_dict("records"), "relationships": r.relationships.to_dict("records")}))
    return repo, str(tmp_path), {c.chunk_id for c in old_chunks}


def test_delta_extract_only_processes_new_chunks(repo_with_old_index):
    from kb_platform.engine.unit_worker import UnitWorker
    from kb_platform.engine.incremental import ExtractGraphDeltaStrategy, register_delta_strategies

    repo, data_root, old_ids = repo_with_old_index
    register_delta_strategies()  # 注册 extract_graph delta 变体(见实现)
    # 加一个新文档 → 切块 → 新 chunk_id 集合
    repo.add_document(kb_id=1, title="new", text="New Globex Corp text " * 200)
    from kb_platform.graph.adapter import FakeGraphAdapter

    fake = FakeGraphAdapter()
    new_chunks = fake.chunk_document(2, repo.get_documents(1)[1].text)
    new_ids = {c.chunk_id for c in new_chunks}
    # 构造一个增量 extract 步,delta manifest = 新 chunk_id
    step = repo.create_job(kb_id=1, type="incremental", specs=[__import__("kb_platform.engine.spec", fromlist=["StepSpec"]).StepSpec("extract_graph", StepKind.UNIT_FANOUT)]).steps[0]
    # 用 delta strategy 跑
    from kb_platform.engine.strategy import register_strategy

    register_strategy("extract_graph", ExtractGraphDeltaStrategy(new_ids))
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    import asyncio

    asyncio.run(worker.run_unit_fanout(step))
    # 断言:只有新 chunk 被处理(unit.subject 只含新 chunk_id)
    processed = {u.subject_id for u in repo.list_units(step.id)}
    assert processed == new_ids
    assert not (processed & old_ids)  # 老 chunk 一个都没重抽
```
> 实现时把 `__import__` 换成顶部 `from kb_platform.engine.spec import StepSpec`。

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_incremental_extract.py -q`
Expected: FAIL(模块/策略不存在)。

- [ ] **Step 3: 写 `incremental.py`**

`kb_platform/engine/incremental.py`:
```python
"""Incremental indexing: new-doc load + delta manifest + delta extract strategy."""

import json
from pathlib import Path

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import UnitStatus
from kb_platform.db.models import Chunk, Document, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy
from kb_platform.engine.strategy import Subject, register_strategy

from sqlalchemy import select


MANIFEST = "delta_manifest.json"


def load_update_documents(repo: Repository, adapter, step) -> None:
    """加载新文档(主索引 documents 表里没有的),切块,写 delta manifest。"""
    from kb_platform.db.engine import session_scope

    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        data_root = Path(kb.data_root)
        existing_uris = set(s.scalars(select(Document.source_uri).where(Document.kb_id == job.kb_id)))
    # 新文档 = add_document 时入的(本任务假设文档已通过 API add_document 入库;此处只判定哪些是新的)
    # 简化:本函数负责"切块新文档并写 manifest";新文档识别 = 该 job 启动后新增的 document 行(用 created_at > job 开始)
    # 为可测且简单:把所有"尚未有 chunk"的文档视为新文档
    new_chunk_ids: list[str] = []
    docs = repo.get_documents(job.kb_id)
    for doc in docs:
        with session_scope(repo.engine) as s:
            has_chunk = s.scalar(select(Chunk).where(Chunk.document_id == doc.id).limit(1))
        if has_chunk is not None:
            continue  # 已切块 = 老文档,跳过
        pieces = adapter.chunk_document(doc.id, doc.text or "")
        chunks = []
        for ordinal, p in enumerate(pieces):
            chunks.append(Chunk(chunk_id=p.chunk_id, kb_id=job.kb_id, document_id=doc.id, ordinal=ordinal, text=p.text))
            new_chunk_ids.append(p.chunk_id)
        repo.add_chunks(chunks)
    (data_root / MANIFEST).write_text(json.dumps(list(new_chunk_ids)))


def read_delta_manifest(data_root) -> set[str]:
    p = Path(data_root) / MANIFEST
    return set(json.loads(p.read_text())) if p.exists() else set()


class ExtractGraphDeltaStrategy(ExtractGraphStrategy):
    """extract_graph 但只处理 new_chunk_ids 内的 chunk。"""

    def __init__(self, new_chunk_ids: set[str]):
        self._new = set(new_chunk_ids)

    def next_units_batch(self, repo, step):
        job = repo.get_job(step.job_id)
        chunks = repo.get_chunks(job.kb_id)
        pending = []
        for c in chunks:
            if c.chunk_id not in self._new:
                continue
            u = repo.get_unit_by_subject(step.id, "chunk", c.chunk_id)
            if u is None or u.status == UnitStatus.PENDING:
                pending.append(Subject("chunk", c.chunk_id))
        return pending or None


def register_delta_strategies() -> None:
    # 注意:extract_graph 的 delta 变体需要 manifest 中的 new_chunk_ids;
    # 由 orchestrator/worker 在跑增量 job 时,先读 manifest 再 register。
    # 这里只占位注册一个"读 manifest"的工厂版,供测试显式注入 new_chunk_ids。
    pass
```
> **关键:** 真实增量 job 里,`ExtractGraphDeltaStrategy` 的 `new_chunk_ids` 来自 `read_delta_manifest(data_root)`。orchestrator 在跑增量 job 的 extract_graph 步前,读 manifest → `register_strategy("extract_graph", ExtractGraphDeltaStrategy(new_ids))`。Task 6(集成)把这个接线补上。本任务先让 delta 策略 + manifest 可测。

- [ ] **Step 4: 跑绿 + 全量**

Run: `uv run pytest tests/test_incremental_extract.py -q && uv run pytest -q`
Expected: delta extract 测试过(只处理新 chunk)+ 既有回归通过。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine/incremental.py tests/test_incremental_extract.py
git commit -m "feat: delta manifest + delta-filtered extract_graph"
```

---

### Task 3: `merge_delta` 原子步(重合并所有磁盘抽取)

**Files:**
- Modify: `kb_platform/engine/atomic_steps.py`(`merge_delta`)
- Modify: `kb_platform/engine/orchestrator.py`(`_run_atomic` 路由 `merge_delta`)
- Test: `tests/test_merge_delta.py`

**Interfaces:**
- Produces: `merge_delta(repo, adapter, step)` —— 读 `data_root/extractions/*.json`(全部,含老的)→ `adapter.merge_extractions` → 写 `entities.parquet`+`relationships.parquet`。

- [ ] **Step 1: 写失败测试**

`tests/test_merge_delta.py`:
```python
import json

import pandas as pd

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import merge_delta
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import ExtractionResult, FakeGraphAdapter


def test_merge_delta_combines_old_and_new_extractions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # 老抽取:ACME
    (tmp_path / "extractions").mkdir()
    (tmp_path / "extractions" / "old.json").write_text(json.dumps({
        "entities": [{"title": "ACME", "type": "ORG", "description": "old desc", "source_id": "old"}],
        "relationships": [],
    }))
    # 新抽取:ACME(老实体,描述增长)+ GLOBEX(新实体)
    (tmp_path / "extractions" / "new.json").write_text(json.dumps({
        "entities": [
            {"title": "ACME", "type": "ORG", "description": "new desc", "source_id": "new"},
            {"title": "GLOBEX", "type": "ORG", "description": "globex", "source_id": "new"},
        ],
        "relationships": [{"source": "ACME", "target": "GLOBEX", "weight": 1.0, "description": "acquires", "source_id": "new"}],
    }))
    step = repo.create_job(kb_id=1, type="incremental", specs=[StepSpec("merge_delta", StepKind.ATOMIC)]).steps[0]
    merge_delta(repo, FakeGraphAdapter(), step)
    ents = pd.read_parquet(tmp_path / "entities.parquet")
    rels = pd.read_parquet(tmp_path / "relationships.parquet")
    titles = set(ents["title"])
    assert titles == {"ACME", "GLOBEX"}
    acme = ents[ents["title"] == "ACME"].iloc[0]
    assert acme["frequency"] == 2  # 老+新两条描述合并
    assert len(rels) == 1 and rels.iloc[0]["source"] == "ACME" and rels.iloc[0]["target"] == "GLOBEX"
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_merge_delta.py -q`
Expected: FAIL(`merge_delta` 不存在)。

- [ ] **Step 3: 写 `merge_delta`**

`kb_platform/engine/atomic_steps.py` 加:
```python
def merge_delta(repo, adapter, step) -> None:
    """Re-merge ALL on-disk chunk extractions (old cached + new) -> entities/relationships parquet."""
    import json

    root = _data_root(repo, step)
    extraction_dir = root / "extractions"
    results = []
    if extraction_dir.exists():
        from kb_platform.graph.adapter import ExtractionResult

        for p in sorted(extraction_dir.glob("*.json")):
            raw = json.loads(p.read_text())
            results.append(ExtractionResult(entities=pd.DataFrame(raw["entities"]), relationships=pd.DataFrame(raw["relationships"])))
    entities, relationships = adapter.merge_extractions(results)
    entities.to_parquet(root / "entities.parquet")
    relationships.to_parquet(root / "relationships.parquet")
```
> `_data_root` 已存在(Task 6 of 2a)。`pd` 已 import。orchestrator `_run_atomic` 加 `elif step.name == "merge_delta": atomic_steps.merge_delta(self.repo, self.adapter, step)`。

- [ ] **Step 4: 跑绿 + 全量**

Run: `uv run pytest tests/test_merge_delta.py -q && uv run pytest -q`
Expected: 通过 + 回归。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine/atomic_steps.py kb_platform/engine/orchestrator.py tests/test_merge_delta.py
git commit -m "feat: merge_delta re-merges all cached extractions"
```

---

### Task 4: 增量 job 的 extract→merge 接线 + load_update_documents 路由

**Files:**
- Modify: `kb_platform/engine/orchestrator.py`(`_run_atomic` 路由 `load_update_documents`/`update_clean_state`;增量 extract_graph 步前读 manifest 注册 delta 策略)
- Modify: `kb_platform/engine/strategies/__init__.py`(保留 full extract_graph 注册)
- Test: `tests/test_incremental_pipeline.py`

**Interfaces:**
- Produces: 增量 job 的 `load_update_documents`(切块新文档+manifest)、extract_graph 用 delta 策略、`update_clean_state`(空操作/合并 state)、`_run_atomic` 路由。

- [ ] **Step 1: 写集成测试**

`tests/test_incremental_pipeline.py`:
```python
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def kb(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="A", text="ACME Org Bob Foo Bar Baz " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_full_then_incremental_only_llms_new_chunks(kb):
    repo, data_root = kb
    # 1) full 索引文档 A
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    full = repo.create_job_pending(kb_id=1, method="standard", type="full")
    await orch.run(full.id)
    assert repo.get_job(full.id).status == "succeeded"
    full_extract = [s for s in repo.get_steps(full.id) if s.name == "extract_graph"][0]
    full_chunk_ids = {u.subject_id for u in repo.list_units(full_extract.id)}

    # 2) 加文档 B,跑增量
    repo.add_document(kb_id=1, title="B", text="Globex Corp Alice Qux " * 200)
    incr = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    await orch.run(incr.id)
    assert repo.get_job(incr.id).status == "succeeded"
    incr_extract = [s for s in repo.get_steps(incr.id) if s.name == "extract_graph"][0]
    incr_chunk_ids = {u.subject_id for u in repo.list_units(incr_extract.id)}

    # 核心承诺:增量只处理新 chunk(与 full 的 chunk 不重叠)
    assert incr_chunk_ids.isdisjoint(full_chunk_ids)
    # merge 后实体表含 A+B 的实体
    import pandas as pd

    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    assert "GLOBEX" in set(ents["title"])
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_incremental_pipeline.py -q`
Expected: FAIL(load_update_documents/update_clean_state 未路由;extract_graph 未用 delta 策略)。

- [ ] **Step 3: 接线**

orchestrator `_run_atomic` 加路由:
```python
        elif step.name == "load_update_documents":
            from kb_platform.engine import incremental

            incremental.load_update_documents(self.repo, self.adapter, step)
        elif step.name == "update_clean_state":
            pass  # MVP:空操作(state 合并留后续)
```
增量 extract_graph 步前注册 delta 策略。在 `_run_step` 的 unit_fanout 分支,若 `job.type == "incremental" and step.name == "extract_graph"`,读 manifest → 注册 delta 策略:
```python
        else:
            from kb_platform.engine.unit_worker import UnitWorker

            if step.name == "extract_graph":
                job = self.repo.get_job(step.job_id)
                if job.type == "incremental":
                    from kb_platform.engine.incremental import ExtractGraphDeltaStrategy, read_delta_manifest
                    from kb_platform.engine.strategy import register_strategy

                    register_strategy("extract_graph", ExtractGraphDeltaStrategy(read_delta_manifest(self.data_root)))
                else:
                    from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy
                    from kb_platform.engine.strategy import register_strategy

                    register_strategy("extract_graph", ExtractGraphStrategy())
            worker = UnitWorker(repo=self.repo, adapter=self.adapter, data_root=self.data_root, concurrency=self.concurrency)
            await worker.run_unit_fanout(step, min_success_ratio=min_success_ratio)
```
> `load_update_documents`(Task 2)切块新文档 + 写 manifest;随后 extract_graph 步读 manifest → delta 策略只处理新 chunk。

- [ ] **Step 4: 跑绿 + 全量**

Run: `uv run pytest tests/test_incremental_pipeline.py -q && uv run pytest -q`
Expected: full→incremental 测试过(只 LLM 新 chunk + merge 含 A+B)+ 回归。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine/orchestrator.py tests/test_incremental_pipeline.py
git commit -m "feat: wire incremental job (load_update_documents + delta extract + merge)"
```

---

### Task 5: 重新整合(增量 job 收尾自动消费 needs_reconsolidation)

**Files:**
- Modify: `kb_platform/retry.py` 或新建 `kb_platform/reconsolidate.py`
- Modify: `kb_platform/engine/orchestrator.py`(增量 job 成功后自动跑 reconsolidate)
- Test: `tests/test_reconsolidate.py`

**Interfaces:**
- Produces: `reconsolidate(repo, adapter, kb_id, data_root)` —— 收集 `needs_reconsolidation` 单元 → 重跑受影响 step 的 finalize(读全部成功单元含迟到)→ 清 flag。

- [ ] **Step 1: 写失败测试**

`tests/test_reconsolidate.py`:
```python
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import UnitStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.reconsolidate import reconsolidate


@pytest.mark.asyncio
async def test_reconsolidate_clears_flag_and_incorporates_late_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # 制造一个 needs_reconsolidation 的 extract_graph 单元(其抽取在磁盘)
    from kb_platform.db.enums import StepKind

    step = repo.create_job(kb_id=1, type="full", specs=[__import__("kb_platform.engine.spec", fromlist=["StepSpec"]).StepSpec("extract_graph", StepKind.UNIT_FANOUT)]).steps[0]
    uid = repo.add_unit(step.id, "chunk", "late-chunk").id
    repo.set_unit_succeeded(uid, llm_raw_output="x")
    repo.mark_needs_reconsolidation(uid)
    import json, pathlib

    (pathlib.Path(tmp_path) / "extractions").mkdir()
    (pathlib.Path(tmp_path) / "extractions" / "late-chunk.json").write_text(json.dumps({"entities": [{"title": "LATE", "type": "ORG", "description": "d", "source_id": "late-chunk"}], "relationships": []}))
    await reconsolidate(repo, __import__("kb_platform.graph.adapter", fromlist=["FakeGraphAdapter"]).FakeGraphAdapter(), kb_id=1, data_root=str(tmp_path))
    # flag 清除
    assert repo.get_unit_by_subject(step.id, "chunk", "late-chunk").needs_reconsolidation is False
    # 迟到实体并入 parquet
    import pandas as pd

    assert "LATE" in set(pd.read_parquet(tmp_path / "entities.parquet")["title"])
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_reconsolidate.py -q`
Expected: FAIL(`reconsolidate` 不存在)。

- [ ] **Step 3: 写 `reconsolidate.py`**

`kb_platform/reconsolidate.py`:
```python
"""Reconsolidate: incorporate needs_reconsolidation units by re-running affected finalizes."""

import logging

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.db.models import Job, Unit
from kb_platform.db.repository import Repository

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def reconsolidate(repo: Repository, adapter, kb_id: int, data_root: str) -> None:
    """Re-merge all extractions (incl. late units) into entities/relationships; clear flags."""
    with session_scope(repo.engine) as s:
        late_units = list(s.scalars(select(Unit).where(Unit.needs_reconsolidation.is_(True))))
    if not late_units:
        return
    # 最简实现:重跑 merge_delta(读全部磁盘抽取,含迟到的)→ 实体/关系刷新
    from kb_platform.engine import atomic_steps
    from kb_platform.engine.spec import StepSpec

    step = repo.create_job(kb_id=kb_id, type="incremental", specs=[StepSpec("merge_delta", __import__("kb_platform.db.enums", fromlist=["StepKind"]).StepKind.ATOMIC)]).steps[0]
    atomic_steps.merge_delta(repo, adapter, step)
    repo.set_step_status(step.id, StepStatus.SUCCEEDED)
    # 清 flag
    with session_scope(repo.engine) as s:
        for u in s.scalars(select(Unit).where(Unit.needs_reconsolidation.is_(True))):
            u.needs_reconsolidation = False
```
> 最简:reconsolidate = 重跑 merge_delta(读全部磁盘抽取,含迟到的)+ 清 flag。迟到单元的抽取已在磁盘(2a/2b 持久化),所以零 LLM。orchestrator 在增量 job 成功后调 `await reconsolidate(...)`。

- [ ] **Step 4: orchestrator 收尾自动跑**

`run` 成功分支末尾,若 `job.type == "incremental"`:
```python
            if job.type == "incremental":
                from kb_platform.reconsolidate import reconsolidate

                await reconsolidate(self.repo, self.adapter, job.kb_id, self.data_root)
```

- [ ] **Step 5: 跑绿 + 全量**

Run: `uv run pytest tests/test_reconsolidate.py -q && uv run pytest -q`
Expected: 通过 + 回归。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/reconsolidate.py kb_platform/engine/orchestrator.py tests/test_reconsolidate.py
git commit -m "feat: reconsolidate consumes needs_reconsolidation (auto on incremental)"
```

---

### Task 6: API `type=incremental` 触发 + 端到端

**Files:**
- Modify: `kb_platform/api/models.py`(`JobCreate.type`)
- Modify: `kb_platform/api/routes_jobs.py`(`trigger_job` 透传 type)
- Modify: `web/src/api/types.ts` + `client.ts`(triggerJob 接 type)
- Test: `tests/test_api_jobs.py`(扩充)

**Interfaces:**
- Produces: `POST /kbs/{id}/jobs` 接 `{"type": "incremental"}`;worker 跑增量 plan。

- [ ] **Step 1: 写失败测试**

`tests/test_api_jobs.py` 追加:
```python
def test_trigger_incremental_job(client):
    r = client.post("/kbs/1/jobs", json={"method": "standard", "type": "incremental"})
    assert r.status_code == 202
    job_id = r.json()["id"]
    steps = client.get(f"/jobs/{job_id}/steps").json()
    names = [s["name"] for s in steps]
    assert "merge_delta" in names and "load_update_documents" in names  # 增量步序列
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_api_jobs.py -q`
Expected: FAIL(type 字段未接)。

- [ ] **Step 3: 接 type**

`models.py` `JobCreate` 加 `type: str = "full"`。`routes_jobs.py` `trigger_job`:`repo.create_job_pending(kb_id=kb_id, method=payload.method, type=payload.type)`。
`web/src/api/types.ts`:无新接口,但 `client.ts` 的 `triggerJob` 加可选 type:`triggerJob(kbId, method?, type?)`。`web/src/api/client.ts`:
```ts
export const triggerJob = (kbId: number, method = "standard", type = "full") =>
  req<{ id: number; status: string }>(`/kbs/${kbId}/jobs`, { method: "POST", body: JSON.stringify({ method, type }) });
```
> 前端 KB 详情页可加一个"增量更新"按钮(Task 7 dashboard 集成,可选;本任务只保证 API + worker)。

- [ ] **Step 4: 跑绿 + 全量 + 前端构建**

Run: `uv run pytest -q && uv run ruff check kb_platform tests && (cd web && npm run build)`
Expected: 全绿 + 前端构建成功。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_jobs.py web/src/api tests/test_api_jobs.py
git commit -m "feat: api type=incremental trigger"
```

---

## Self-Review(写完后自查)

**1. Spec 覆盖:**
- `plan_incremental()` 独立 delta 序列 → Task 1 ✓
- 只对新 chunk 抽取(旧零重算)→ Task 2(delta extract)+ Task 4(接线)✓
- merge_delta 把新并入老图(新↔老关系)→ Task 3 ✓
- delta manifest + IncrementalPlanner → Task 2 ✓
- 重新整合(消费 needs_reconsolidation,自动跑)→ Task 5 ✓
- API `type=incremental` + worker 路由 → Task 1(worker 经 orchestrator)+ Task 6(API)✓
- 测试(只对新 chunk LLM 的核心承诺 + merge 正确 + reconsolidate)→ Task 2/3/4/5/6 ✓
- embeddings/查询/删除 → 显式非目标 ✓

**重要设计修正(相对 spec §5):** spec 说 merge_delta"接 graphrag update 操作";实测 graphrag 的 `_group_and_resolve_entities`/`_update_and_merge_relationships` 需要 `id`/`human_readable_id` 列(平台简化 schema 没有)。**改为 merge_delta 用平台自己的 `merge_extractions`(2a 已实现)重合并所有磁盘抽取** —— 更干净(零 graphrag 耦合、复用已测代码、旧抽取缓存故零 LLM)。已在计划开头 Architecture 注明。

**2. 占位符扫描:** 无 TBD;`update_clean_state` 为 MVP 空操作(已注明 state 合并留后续);前端 dashboard 的"增量更新"按钮标注可选。

**3. 类型一致性:** `plan_full`/`plan_incremental`、`create_job_pending(type=)`、`ExtractGraphDeltaStrategy(new_chunk_ids)`、`merge_delta`、`load_update_documents`、`read_delta_manifest`、`reconsolidate`、`JobCreate.type` 跨任务一致。

**范围说明:** 增量里 summarize/community_reports 跑在合并后的整图(summarize 靠 LLM 缓存省、community_reports 重跑所有社区 = 较贵);"仅变化社区重报告"的 delta 优化留后续。核心承诺(不重抽旧 chunk)由 Task 2/4 保证。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-phase3a-incremental.md`. Two execution options:

**1. Subagent-Driven(推荐)** — 每任务派发独立 subagent + 两阶段评审。
**2. Inline Execution** — 当前会话批量执行 + 检查点。

Which approach?
