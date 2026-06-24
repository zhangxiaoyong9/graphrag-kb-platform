# Phase 2a — 后端补全 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Phase 1 的最小流水线补成完整的图谱流水线(抽取 → 描述合并 → 度数定稿 → Leiden 聚类 → 社区报告),并用 `UnitStepStrategy` 把引擎重构为通用驱动器,新增带失败前进。

**Architecture:** 每个 unit 步注册一个 `UnitStepStrategy`(next_units_batch / run_unit / persist / finalize);`UnitWorker` 改为与 step 类型无关的批量驱动循环;atomic 步(finish/cluster)留在 Orchestrator。控制面/数据面分离不变。全程用 `FakeGraphAdapter` 验证(真实 graphrag adapter 的新 LLM 方法延后 Phase 2b)。

**Tech Stack:** Python 3.11–3.13 · SQLAlchemy 2.x + Alembic · SQLite(WAL)· pandas/pyarrow · pytest + pytest-asyncio · asyncio。

## Global Constraints

- 锁定 `graphrag==3.1.*`;graphrag 内部仅可在 `kb_platform/graph/graphrag_adapter.py` 引用(本计划不新增 graphrag 耦合)。
- Python `>=3.11,<3.14`;asyncio 单线程事件循环,同步 DB 调用穿插其间。
- Detached ORM 对象:只读标量列(unit.subject_id / step.job_id / job.kb_id),不遍历关系。
- 每个任务 TDD:写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。提交信息约定式前缀(`feat:`/`refactor:`/`test:`/`chore:`)。
- **Task 3(strategy 重构)后,Phase 1 全部 21 个测试必须仍通过**(回归门)。
- 默认 `min_unit_success_ratio = 1.0`(严格),等价 Phase 1 行为。

## 关键接口契约(跨任务共享,务必一致)

```python
# kb_platform/engine/strategy.py  (Task 3 定义)
@dataclass
class Subject:
    subject_type: str   # "chunk" | "entity" | "community"
    subject_id: str

@dataclass
class UnitResult:
    payload: Any
    input_hash: str | None = None
    cost_json: str | None = None
    llm_raw_output: str | None = None

class UnitStepStrategy(Protocol):
    kind: UnitKind
    def next_units_batch(self, repo, step) -> list[Subject] | None: ...
    async def run_unit(self, adapter, unit, repo) -> UnitResult: ...
    def persist(self, data_root: Path, unit, result: UnitResult) -> None: ...
    def finalize(self, repo, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus: ...

STRATEGIES: dict[str, UnitStepStrategy] = {}   # name -> strategy
```

```python
# kb_platform/graph/adapter.py  (Task 4 扩展)
@dataclass
class CommunityReport:
    title: str; summary: str; findings: list[str]; rank: float
    full_content: str; level: int; community: str

class GraphAdapter(Protocol):
    # Phase 1 保留:
    def chunk_document(self, doc_id, text) -> list[ChunkText]: ...
    async def extract_chunk(self, chunk_id, text) -> ExtractionResult: ...
    def merge_extractions(self, results) -> tuple[pd.DataFrame, pd.DataFrame]: ...
    # Task 4 新增:
    async def summarize_entity(self, name: str, descriptions: list[str]) -> str: ...
    async def report_community(self, context: dict) -> CommunityReport: ...
    def cluster_relationships(self, relationships_df: pd.DataFrame) -> pd.DataFrame: ...
    def finalize_entities_relationships(self, entities_df, relationships_df) -> tuple[pd.DataFrame, pd.DataFrame]: ...
```

```python
# kb_platform/db/repository.py  (Task 2 新增/改)
def get_unit_by_subject(self, step_id, subject_type, subject_id) -> Unit | None: ...
def add_unit(self, step_id, subject_type, subject_id) -> Unit: ...
def set_unit_running(self, unit_id) -> None: ...      # PENDING->RUNNING, attempt_no+1
def set_unit_succeeded(self, unit_id, *, input_hash=None, cost_json=None, llm_raw_output=None) -> None: ...
def mark_needs_reconsolidation(self, unit_id) -> None: ...
```

```python
# kb_platform/engine/unit_worker.py  (Task 3 重构)
class UnitWorker:
    async def run_unit_fanout(self, step, min_success_ratio: float = 1.0) -> None: ...
```

---

### Task 1: `Unit` 模型缺失列 + Alembic 迁移

**Files:**
- Modify: `kb_platform/db/models.py`
- Create: `alembic/versions/0002_unit_tracking_columns.py`
- Test: `tests/test_migration.py`(已存在,扩充)

**Interfaces:**
- Produces: `Unit` 增 `input_hash`/`cost_json`/`llm_raw_output`/`needs_reconsolidation` 列;迁移 `0002`。

- [ ] **Step 1: 写失败测试(扩充 test_migration)**

在 `tests/test_migration.py` 末尾追加:
```python
def test_migration_adds_unit_tracking_columns(tmp_path):
    import subprocess
    import sys

    from sqlalchemy import inspect as sa_inspect

    from kb_platform.db.engine import create_engine

    db = tmp_path / "cols.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"],
        check=True,
    )
    cols = {c["name"] for c in sa_inspect(create_engine(f"sqlite:///{db}")).get_columns("unit")}
    for expected in ("input_hash", "cost_json", "llm_raw_output", "needs_reconsolidation"):
        assert expected in cols, f"missing column {expected}"
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_migration.py::test_migration_adds_unit_tracking_columns -q`
Expected: FAIL(列不存在)。

- [ ] **Step 3: 改 `models.py` 的 `Unit`**

在 `Unit` 类末尾(`step` 关系之前)追加(并在文件顶部 `from sqlalchemy import ...` 加入 `Boolean`):
```python
    input_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_reconsolidation: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```

- [ ] **Step 4: 生成迁移**

Run: `uv run alembic revision --autogenerate -m "unit tracking columns" --rev-id 0002`
打开生成的 `alembic/versions/0002_unit_tracking_columns.py`,确认 `upgrade()` 对 `unit` 表 `add_column` 了上述 4 列、`downgrade()` `drop_column`。若 autogenerate 漏列,检查 `models.py` 是否保存。

- [ ] **Step 5: 跑绿 + 全量**

Run: `uv run pytest tests/test_migration.py -q && uv run pytest -q`
Expected: 迁移测试通过 + 全套(含 Phase 1 的 21)仍通过。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/db/models.py alembic/versions/0002_unit_tracking_columns.py tests/test_migration.py
git commit -m "feat: add unit tracking columns (input_hash/cost_json/llm_raw_output/needs_reconsolidation)"
```

---

### Task 2: UnitKind 枚举 + Repository 方法

**Files:**
- Modify: `kb_platform/db/enums.py`
- Modify: `kb_platform/db/repository.py`
- Test: `tests/test_repository.py`(扩充)

**Interfaces:**
- Produces: `UnitKind.SUMMARIZE_DESCRIPTIONS`、`UnitKind.COMMUNITY_REPORT`;repo 的 `get_unit_by_subject`/`add_unit`/`set_unit_running`/`set_unit_succeeded(*, input_hash, cost_json, llm_raw_output)`/`mark_needs_reconsolidation`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_repository.py` 追加:
```python
from kb_platform.db.enums import UnitKind, UnitStatus


def test_get_or_create_and_running(repo):
    job = repo.create_job(kb_id=1, type="full", specs=[__import__("kb_platform.engine.spec", fromlist=["StepSpec"]).StepSpec("extract_graph", __import__("kb_platform.db.enums", fromlist=["StepKind"]).StepKind.UNIT_FANOUT)])
    step = job.steps[0]
    u = repo.add_unit(step.id, "chunk", "c1")
    assert u.status == UnitStatus.PENDING
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").id == u.id
    repo.set_unit_running(u.id)
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").status == UnitStatus.RUNNING


def test_set_unit_succeeded_stores_meta_and_reconsolidation(repo):
    job = repo.create_job(kb_id=1, type="full", specs=[__import__("kb_platform.engine.spec", fromlist=["StepSpec"]).StepSpec("extract_graph", __import__("kb_platform.db.enums", fromlist=["StepKind"]).StepKind.UNIT_FANOUT)])
    step = job.steps[0]
    u = repo.add_unit(step.id, "chunk", "c1")
    repo.set_unit_succeeded(u.id, input_hash="h", cost_json='{"t":1}', llm_raw_output="raw")
    fresh = repo.get_unit_by_subject(step.id, "chunk", "c1")
    assert fresh.status == UnitStatus.SUCCEEDED
    assert fresh.input_hash == "h" and fresh.cost_json == '{"t":1}' and fresh.llm_raw_output == "raw"
    repo.mark_needs_reconsolidation(u.id)
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").needs_reconsolidation is True


def test_new_unit_kinds_exist():
    assert UnitKind.SUMMARIZE_DESCRIPTIONS == "summarize_descriptions"
    assert UnitKind.COMMUNITY_REPORT == "community_report"
```
> 注:`repo` fixture 已存在于该文件(Phase 1)。上面用 `__import__` 规避顶部 import 顺序;实现时可在测试文件顶部正常 `from kb_platform.engine.spec import StepSpec` 与 `from kb_platform.db.enums import StepKind`,更整洁。

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_repository.py -q`
Expected: FAIL(`add_unit`/`get_unit_by_subject` 等不存在)。

- [ ] **Step 3: 扩充 enums**

在 `kb_platform/db/enums.py` 的 `UnitKind` 加入:
```python
class UnitKind(StrEnum):
    EXTRACT_GRAPH = "extract_graph"
    SUMMARIZE_DESCRIPTIONS = "summarize_descriptions"
    COMMUNITY_REPORT = "community_report"
```

- [ ] **Step 4: 扩充 repository**

在 `Repository` 中,把 Phase 1 的 `set_unit_succeeded` 改签名为关键字参数并新增方法:
```python
    def get_unit_by_subject(self, step_id: int, subject_type: str, subject_id: str) -> Unit | None:
        with session_scope(self.engine) as s:
            return s.scalar(
                select(Unit).where(
                    Unit.step_id == step_id,
                    Unit.subject_type == subject_type,
                    Unit.subject_id == subject_id,
                )
            )

    def add_unit(self, step_id: int, subject_type: str, subject_id: str) -> Unit:
        with session_scope(self.engine) as s:
            u = Unit(step_id=step_id, subject_type=subject_type, subject_id=subject_id, status=UnitStatus.PENDING, attempt_no=0)
            s.add(u)
            s.flush()
            return u

    def set_unit_running(self, unit_id: int) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.RUNNING
            u.attempt_no += 1

    def set_unit_succeeded(self, unit_id: int, *, input_hash: str | None = None, cost_json: str | None = None, llm_raw_output: str | None = None) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.SUCCEEDED
            u.input_hash, u.cost_json, u.llm_raw_output = input_hash, cost_json, llm_raw_output

    def mark_needs_reconsolidation(self, unit_id: int) -> None:
        with session_scope(self.engine) as s:
            s.get(Unit, unit_id).needs_reconsolidation = True
```
> 保留 Phase 1 的 `claim_pending_units`/`reset_*`/`list_units`/`add_units` 不变(后续 worker 重构后 `claim_pending_units` 可能不再用,但先保留以免破坏 Phase 1 测试,Task 3 再清理)。

- [ ] **Step 5: 跑绿 + 全量**

Run: `uv run pytest -q`
Expected: 全套通过。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/db/enums.py kb_platform/db/repository.py tests/test_repository.py
git commit -m "feat: new unit kinds and per-unit repo methods"
```

---

### Task 3: `UnitStepStrategy` 协议 + `ExtractGraphStrategy` 重构 + 通用 UnitWorker

**Files:**
- Create: `kb_platform/engine/strategy.py`
- Create: `kb_platform/engine/strategies/__init__.py`
- Create: `kb_platform/engine/strategies/extract_graph.py`
- Modify: `kb_platform/engine/unit_worker.py`
- Test: `tests/test_unit_worker.py`、`tests/test_orchestrator.py`、`tests/test_integration_e2e.py`(Phase 1,回归门)

**Interfaces:**
- Consumes: Task 2 repo 方法、Phase 1 `FakeGraphAdapter.extract_chunk`/`merge_extractions`。
- Produces: `Subject`、`UnitResult`、`UnitStepStrategy`、`STRATEGIES`、`ExtractGraphStrategy`、重构后的 `UnitWorker.run_unit_fanout(step, min_success_ratio=1.0)`。

- [ ] **Step 1: 写 `strategy.py`(协议 + 数据类)**

`kb_platform/engine/strategy.py`:
```python
"""Unit-step strategy abstraction."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kb_platform.db.enums import StepStatus, UnitKind
from kb_platform.db.repository import Repository


@dataclass
class Subject:
    subject_type: str
    subject_id: str


@dataclass
class UnitResult:
    payload: Any
    input_hash: str | None = None
    cost_json: str | None = None
    llm_raw_output: str | None = None


class UnitStepStrategy(Protocol):
    kind: UnitKind

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None: ...

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult: ...

    def persist(self, data_root: Path, unit, result: UnitResult) -> None: ...

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus: ...


STRATEGIES: dict[str, UnitStepStrategy] = {}


def register_strategy(name: str, strategy: UnitStepStrategy) -> None:
    STRATEGIES[name] = strategy
```

- [ ] **Step 2: 写 `ExtractGraphStrategy`(搬 Phase 1 逻辑)**

`kb_platform/engine/strategies/extract_graph.py`:
```python
"""ExtractGraphStrategy: per-chunk LLM extraction (refactored from Phase 1)."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from kb_platform.db.enums import StepStatus, UnitKind, UnitStatus
from kb_platform.db.models import Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject, UnitResult, UnitStepStrategy
from kb_platform.graph.adapter import ExtractionResult

from sqlalchemy import select


class ExtractGraphStrategy:
    kind = UnitKind.EXTRACT_GRAPH

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        job = repo.get_job(step.job_id)
        chunks = repo.get_chunks(job.kb_id)
        pending = []
        for c in chunks:
            u = repo.get_unit_by_subject(step.id, "chunk", c.chunk_id)
            if u is None or u.status != UnitStatus.SUCCEEDED:
                pending.append(Subject("chunk", c.chunk_id))
        return pending or None

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        from kb_platform.db.engine import session_scope

        from sqlalchemy import select

        job = repo.get_job(repo.get_step(unit.step_id).job_id)
        with session_scope(repo.engine) as s:
            chunk = s.scalar(select(Chunk).where(Chunk.chunk_id == unit.subject_id, Chunk.kb_id == job.kb_id))
            text = chunk.text if chunk else ""
        result = await adapter.extract_chunk(unit.subject_id, text)
        return UnitResult(payload=result, input_hash=hashlib.sha512(text.encode()).hexdigest())

    def persist(self, data_root: Path, unit, result: UnitResult) -> None:
        d = data_root / "extractions"
        d.mkdir(parents=True, exist_ok=True)
        er: ExtractionResult = result.payload
        (d / f"{unit.subject_id}.json").write_text(json.dumps({
            "entities": er.entities.to_dict("records"),
            "relationships": er.relationships.to_dict("records"),
        }))

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus:
        units = repo.list_units(step.id)
        if not units:
            return StepStatus.PARTIALLY_FAILED
        succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
        ratio = len(succeeded) / len(units)
        if ratio < min_success_ratio:
            return StepStatus.PARTIALLY_FAILED
        extractions = []
        for u in succeeded:
            path = data_root / "extractions" / f"{u.subject_id}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                extractions.append(ExtractionResult(entities=pd.DataFrame(raw["entities"]), relationships=pd.DataFrame(raw["relationships"])))
        entities, relationships = adapter.merge_extractions(extractions)
        entities.to_parquet(data_root / "entities.parquet")
        relationships.to_parquet(data_root / "relationships.parquet")
        return StepStatus.SUCCEEDED
```

- [ ] **Step 3: 写注册**

`kb_platform/engine/strategies/__init__.py`:
```python
"""Strategy registry bootstrap."""

from kb_platform.engine.strategy import register_strategy
from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy

register_strategy("extract_graph", ExtractGraphStrategy())
```

- [ ] **Step 4: 重构 `UnitWorker` 为通用批量驱动**

整体替换 `kb_platform/engine/unit_worker.py`:
```python
"""Generic UnitWorker: drives a unit_fanout step via its registered strategy."""

import asyncio
import logging
from pathlib import Path

from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import STRATEGIES, Subject
from kb_platform.graph.adapter import GraphAdapter

logger = logging.getLogger(__name__)


class UnitWorker:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = Path(data_root)
        self.concurrency = concurrency

    async def run_unit_fanout(self, step, min_success_ratio: float = 1.0) -> None:
        strategy = STRATEGIES[step.name]
        while (batch := strategy.next_units_batch(self.repo, step)) is not None:
            await self._run_batch(strategy, step, batch)
        status = strategy.finalize(self.repo, self.adapter, step, self.data_root, min_success_ratio)
        self.repo.set_step_status(step.id, status)

    async def _run_batch(self, strategy, step, subjects: list[Subject]) -> None:
        units = []
        for s in subjects:
            u = self.repo.get_unit_by_subject(step.id, s.subject_type, s.subject_id)
            if u is None:
                u = self.repo.add_unit(step.id, s.subject_type, s.subject_id)
            if u.status == UnitStatus.SUCCEEDED:
                continue
            units.append(u)
        if not units:
            return
        for u in units:
            self.repo.set_unit_running(u.id)
        sem = asyncio.Semaphore(self.concurrency)

        async def handle(u):
            async with sem:
                await self._process(strategy, u)

        await asyncio.gather(*(handle(u) for u in units))

    async def _process(self, strategy, unit) -> None:
        try:
            result = await strategy.run_unit(self.adapter, unit, self.repo)
            strategy.persist(self.data_root, unit, result)
            self.repo.set_unit_succeeded(unit.id, input_hash=result.input_hash, cost_json=result.cost_json, llm_raw_output=result.llm_raw_output)
        except Exception as e:  # noqa: BLE001
            logger.warning("unit %s failed: %s", unit.id, e)
            self.repo.set_unit_failed(unit.id, str(e))
```
> Phase 1 的 `Orchestrator._run_step` 调 `worker.run_unit_fanout(step)` —— 新签名默认 `min_success_ratio=1.0`,调用兼容。但 Orchestrator 仍 import `UnitWorker`,无需改(Task 8 再接 min_success_ratio)。

- [ ] **Step 5: 跑 Phase 1 回归门**

Run: `uv run pytest tests/test_unit_worker.py tests/test_orchestrator.py tests/test_integration_e2e.py -q`
Expected: 全绿(ExtractGraphStrategy 复现 Phase 1 行为;e2e 的 `frequency==200` 仍成立)。
若有失败,定位是 strategy 行为偏离还是 worker 重构 bug,修到绿。**不许改 Phase 1 测试的断言来凑过。**

- [ ] **Step 6: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add kb_platform/engine/strategy.py kb_platform/engine/strategies kb_platform/engine/unit_worker.py
git commit -m "refactor: unit-step strategy abstraction with generic worker"
```

---

### Task 4: `FakeGraphAdapter` 新增原语 + `CommunityReport`

**Files:**
- Modify: `kb_platform/graph/adapter.py`
- Test: `tests/test_adapter_fake.py`(扩充)

**Interfaces:**
- Produces: `CommunityReport` 数据类;`FakeGraphAdapter` 的 `summarize_entity`/`report_community`/`cluster_relationships`/`finalize_entities_relationships`;`GraphAdapter` Protocol 扩展。

- [ ] **Step 1: 写失败测试**

在 `tests/test_adapter_fake.py` 追加:
```python
import pandas as pd

from kb_platform.graph.adapter import CommunityReport, FakeGraphAdapter


def test_summarize_entity_joins_descriptions():
    a = FakeGraphAdapter()
    merged = a.summarize_entity_sync("ACME", ["desc one", "desc two"])
    assert "desc one" in merged and "desc two" in merged


def test_cluster_relationships_returns_communities():
    a = FakeGraphAdapter()
    rels = pd.DataFrame([
        {"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
        {"source": "B", "target": "C", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
        {"source": "X", "target": "Y", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c2"]},
    ])
    comms = a.cluster_relationships(rels)
    assert {"level", "community_id", "parent", "entity_ids"} <= set(comms.columns)
    assert len(comms) >= 1
    # all sources/targets appear in some community
    members = {e for ids in comms["entity_ids"] for e in ids}
    assert {"A", "B", "C", "X", "Y"} <= members


def test_finalize_entities_relationships_adds_degree():
    a = FakeGraphAdapter()
    ents = pd.DataFrame([{"title": "A", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1}])
    rels = pd.DataFrame([{"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]}])
    e2, r2 = a.finalize_entities_relationships(ents, rels)
    assert "degree" in e2.columns or "degree" in r2.columns


def test_report_community_returns_report():
    a = FakeGraphAdapter()
    ctx = {"community": "C0", "level": 0, "entities": [{"title": "A", "description": "d"}], "relationships": [], "sub_reports": []}
    rep = a.report_community_sync(ctx)
    assert isinstance(rep, CommunityReport)
    assert rep.community == "C0" and rep.level == 0
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_adapter_fake.py -q`
Expected: FAIL(`summarize_entity` 等不存在)。

- [ ] **Step 3: 扩展 `adapter.py`**

在 `kb_platform/graph/adapter.py` 顶部 `@dataclass` 区追加:
```python
@dataclass
class CommunityReport:
    title: str
    summary: str
    findings: list[str]
    rank: float
    full_content: str
    level: int
    community: str
```
把 `GraphAdapter` Protocol 加四方法(Task 契约里已列签名)。
在 `FakeGraphAdapter` 加(确定性实现):
```python
    def summarize_entity_sync(self, name: str, descriptions: list[str]) -> str:
        return "; ".join(descriptions)

    async def summarize_entity(self, name: str, descriptions: list[str]) -> str:
        return self.summarize_entity_sync(name, descriptions)

    def report_community_sync(self, context: dict) -> "CommunityReport":
        names = [e["title"] for e in context.get("entities", [])]
        title = names[0] if names else f"Community {context['community']}"
        summary = f"Community {context['community']} covers {', '.join(names[:5]) or 'no entities'}."
        return CommunityReport(
            title=title, summary=summary, findings=[summary],
            rank=0.5, full_content=summary, level=context["level"], community=context["community"],
        )

    async def report_community(self, context: dict) -> "CommunityReport":
        return self.report_community_sync(context)

    def cluster_relationships(self, relationships: "pd.DataFrame") -> "pd.DataFrame":
        # 确定性"聚类":连通分量即社区(单层 level=0,parent=自身)
        import networkx as nx

        g = nx.Graph()
        for _, row in relationships.iterrows():
            g.add_edge(row["source"], row["target"])
        rows = []
        for cid, comp in enumerate(nx.connected_components(g)):
            members = sorted(comp)
            rows.append({"level": 0, "community_id": str(cid), "parent": str(cid), "entity_ids": members})
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["level", "community_id", "parent", "entity_ids"])

    def finalize_entities_relationships(self, entities: "pd.DataFrame", relationships: "pd.DataFrame") -> tuple:
        import pandas as pd

        deg = {}
        for _, r in relationships.iterrows():
            deg[r["source"]] = deg.get(r["source"], 0) + 1
            deg[r["target"]] = deg.get(r["target"], 0) + 1
        if not entities.empty:
            entities = entities.copy()
            entities["degree"] = entities["title"].map(lambda t: deg.get(t, 0))
        if not relationships.empty:
            relationships = relationships.copy()
            relationships["combined_degree"] = relationships.apply(lambda r: deg.get(r["source"], 0) + deg.get(r["target"], 0), axis=1)
        return entities, relationships
```
> 需在 `pyproject.toml` 加 `networkx>=3.0`(graphrag 已依赖 networkx,但本包应显式声明)。若不想新增依赖,可手写并查集代替 `networkx.connected_components`——选其一并在报告里说明。

- [ ] **Step 4: 加依赖并跑绿**

`pyproject.toml` 的 `dependencies` 加 `"networkx>=3.0"`,运行 `uv sync`。
Run: `uv run pytest tests/test_adapter_fake.py -q`
Expected: 4 新测试通过。

- [ ] **Step 5: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add kb_platform/graph/adapter.py pyproject.toml uv.lock tests/test_adapter_fake.py
git commit -m "feat: fake adapter summarize/report/cluster/finalize + CommunityReport"
```

---

### Task 5: `SummarizeDescriptionsStrategy`

**Files:**
- Create: `kb_platform/engine/strategies/summarize_descriptions.py`
- Modify: `kb_platform/engine/strategies/__init__.py`(注册)
- Test: `tests/test_strategy_summarize.py`

**Interfaces:**
- Consumes: `entities.parquet`(由 extract_graph 产出;`description` 列为 list)、Task 4 `adapter.summarize_entity`。
- Produces: `SummarizeDescriptionsStrategy`;`next_units_batch` 只返回描述数>1 的实体。

- [ ] **Step 1: 写失败测试**

`tests/test_strategy_summarize.py`:
```python
import json

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    # 预置 entities.parquet:description 为 list
    ents = pd.DataFrame([
        {"title": "ACME", "type": "ORG", "description": ["d1", "d2"], "text_unit_ids": ["c1", "c2"], "frequency": 2},
        {"title": "SOLO", "type": "ORG", "description": ["only"], "text_unit_ids": ["c1"], "frequency": 1},
    ])
    ents.to_parquet(f"{tmp_path}/entities.parquet")
    repo = Repository(engine)
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT)]).steps[0]
    return repo, step, str(tmp_path)


def test_only_multi_desc_entities_get_units(setup):
    repo, step, _ = setup
    strat = SummarizeDescriptionsStrategy()
    batch = strat.next_units_batch(repo, step)
    assert batch is not None and {s.subject_id for s in batch} == {"ACME"}  # SOLO 单描述,不出现在批


@pytest.mark.asyncio
async def test_summarize_writes_merged_descriptions_back(setup):
    repo, step, data_root = setup
    from kb_platform.engine.unit_worker import UnitWorker

    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    await worker.run_unit_fanout(step)
    from kb_platform.db.enums import StepStatus

    assert repo.get_step(step.id).status == StepStatus.SUCCEEDED
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    acme = ents[ents["title"] == "ACME"].iloc[0]
    assert isinstance(acme["description"], str) and "d1" in acme["description"] and "d2" in acme["description"]
    solo = ents[ents["title"] == "SOLO"].iloc[0]
    assert solo["description"] == "only"  # 未合并,原值保留
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_strategy_summarize.py -q`
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 写实现**

`kb_platform/engine/strategies/summarize_descriptions.py`:
```python
"""SummarizeDescriptionsStrategy: merge multi-chunk entity descriptions."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from kb_platform.db.enums import StepStatus, UnitKind, UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject, UnitResult, UnitStepStrategy


class SummarizeDescriptionsStrategy:
    kind = UnitKind.SUMMARIZE_DESCRIPTIONS

    def _entities(self, data_root: Path) -> pd.DataFrame:
        return pd.read_parquet(data_root / "entities.parquet")

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        data_root = self._resolve_data_root(repo, step)
        ents = self._entities(data_root)
        pending = []
        for _, row in ents.iterrows():
            desc = row["description"]
            n = len(desc) if isinstance(desc, list) else 1
            if n > 1:
                u = repo.get_unit_by_subject(step.id, "entity", row["title"])
                if u is None or u.status != UnitStatus.SUCCEEDED:
                    pending.append(Subject("entity", row["title"]))
        return pending or None

    @staticmethod
    def _resolve_data_root(repo: Repository, step) -> Path:
        from kb_platform.db.models import KnowledgeBase

        from sqlalchemy import select

        job = repo.get_job(step.job_id)
        with __import__("kb_platform.db.engine", fromlist=["session_scope"]).session_scope(repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
            return Path(kb.data_root)

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        data_root = self._resolve_data_root(repo, repo.get_step(unit.step_id))
        ents = self._entities(data_root)
        row = ents[ents["title"] == unit.subject_id].iloc[0]
        descriptions = list(row["description"])
        merged = await adapter.summarize_entity(unit.subject_id, descriptions)
        return UnitResult(payload=merged, input_hash=hashlib.sha512(json.dumps(descriptions).encode()).hexdigest(), llm_raw_output=merged)

    def persist(self, data_root: Path, unit, result: UnitResult) -> None:
        d = data_root / "summaries"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{unit.subject_id}.json").write_text(json.dumps({"summary": result.payload}))

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus:
        units = repo.list_units(step.id)
        ents = self._entities(data_root).copy()
        if units:
            succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
            if units and len(succeeded) / len(units) < min_success_ratio:
                return StepStatus.PARTIALLY_FAILED
            summaries = {}
            for u in succeeded:
                p = data_root / "summaries" / f"{u.subject_id}.json"
                if p.exists():
                    summaries[u.subject_id] = json.loads(p.read_text())["summary"]
            def _desc(title, current):
                return summaries.get(title, current if not isinstance(current, list) else current[0])
            ents["description"] = [_desc(t, c) for t, c in zip(ents["title"], ents["description"])]
        ents.to_parquet(data_root / "entities.parquet")
        return StepStatus.SUCCEEDED
```
> `_resolve_data_root` 从 KB 行读 `data_root`(测试 fixture 设的 `data_root=str(tmp_path)` 正是 parquet 所在)。

- [ ] **Step 4: 注册 + 跑绿**

`kb_platform/engine/strategies/__init__.py` 追加:
```python
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy
register_strategy("summarize_descriptions", SummarizeDescriptionsStrategy())
```
Run: `uv run pytest tests/test_strategy_summarize.py -q`
Expected: 2 通过。

- [ ] **Step 5: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add kb_platform/engine/strategies/summarize_descriptions.py kb_platform/engine/strategies/__init__.py tests/test_strategy_summarize.py
git commit -m "feat: summarize_descriptions strategy"
```

---

### Task 6: Atomic 步 `finalize_graph` + `create_communities`

**Files:**
- Create: `kb_platform/engine/atomic_steps.py`
- Test: `tests/test_atomic_steps.py`

**Interfaces:**
- Consumes: `entities.parquet`/`relationships.parquet`(finalize)、`relationships.parquet`(cluster)、Task 4 adapter `cluster_relationships`/`finalize_entities_relationships`。
- Produces: `finalize_graph(repo, adapter, step)`、`create_communities(repo, adapter, step)`(写 `communities.parquet`,回写 entities/relationships)。

- [ ] **Step 1: 写失败测试**

`tests/test_atomic_steps.py`:
```python
import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.atomic_steps import create_communities, finalize_graph
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    pd.DataFrame([
        {"title": "A", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1},
        {"title": "B", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1},
    ]).to_parquet(f"{tmp_path}/entities.parquet")
    pd.DataFrame([
        {"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
    ]).to_parquet(f"{tmp_path}/relationships.parquet")
    repo = Repository(engine)
    return repo, str(tmp_path)


def test_finalize_graph_adds_degrees(setup):
    repo, data_root = setup
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("finalize_graph", StepKind.ATOMIC)]).steps[0]
    finalize_graph(repo, FakeGraphAdapter(), step)
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    rels = pd.read_parquet(f"{data_root}/relationships.parquet")
    assert "degree" in ents.columns
    assert "combined_degree" in rels.columns


def test_create_communities_writes_parquet(setup):
    repo, data_root = setup
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("create_communities", StepKind.ATOMIC)]).steps[0]
    create_communities(repo, FakeGraphAdapter(), step)
    comms = pd.read_parquet(f"{data_root}/communities.parquet")
    assert {"level", "community_id", "parent", "entity_ids"} <= set(comms.columns)
    assert len(comms) >= 1
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_atomic_steps.py -q`
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 写实现**

`kb_platform/engine/atomic_steps.py`:
```python
"""Atomic (non-unit) indexing steps."""

from pathlib import Path

import pandas as pd

from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository

from sqlalchemy import select


def _data_root(repo: Repository, step) -> Path:
    from kb_platform.db.engine import session_scope

    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        return Path(kb.data_root)


def finalize_graph(repo: Repository, adapter, step) -> None:
    root = _data_root(repo, step)
    entities = pd.read_parquet(root / "entities.parquet")
    relationships = pd.read_parquet(root / "relationships.parquet")
    e2, r2 = adapter.finalize_entities_relationships(entities, relationships)
    e2.to_parquet(root / "entities.parquet")
    r2.to_parquet(root / "relationships.parquet")


def create_communities(repo: Repository, adapter, step) -> None:
    root = _data_root(repo, step)
    relationships = pd.read_parquet(root / "relationships.parquet")
    communities = adapter.cluster_relationships(relationships)
    communities.to_parquet(root / "communities.parquet")
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_atomic_steps.py -q`
Expected: 2 通过。

- [ ] **Step 5: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add kb_platform/engine/atomic_steps.py tests/test_atomic_steps.py
git commit -m "feat: finalize_graph and create_communities atomic steps"
```

---

### Task 7: `CommunityReportsStrategy`(多层)

**Files:**
- Create: `kb_platform/engine/strategies/community_reports.py`
- Modify: `kb_platform/engine/strategies/__init__.py`(注册)
- Test: `tests/test_strategy_community_reports.py`

**Interfaces:**
- Consumes: `entities.parquet`/`relationships.parquet`/`communities.parquet`;Task 4 `adapter.report_community`。
- Produces: `CommunityReportsStrategy`;`next_units_batch` 自底向上按 level 返回;`community_reports.parquet`。

- [ ] **Step 1: 写失败测试(多层 + 父层含子层)**

`tests/test_strategy_community_reports.py`:
```python
import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, StepStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    pd.DataFrame([
        {"title": "A", "type": "T", "description": "da", "text_unit_ids": ["c1"], "frequency": 1, "degree": 1},
        {"title": "B", "type": "T", "description": "db", "text_unit_ids": ["c1"], "frequency": 1, "degree": 1},
    ]).to_parquet(f"{tmp_path}/entities.parquet")
    pd.DataFrame([{"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"], "combined_degree": 2}]).to_parquet(f"{tmp_path}/relationships.parquet")
    # 两层:level 1(叶子)C1={A,B},level 0(父)C0={A,B} parent=C0 children=C1
    pd.DataFrame([
        {"level": 1, "community_id": "C1", "parent": "C0", "entity_ids": ["A", "B"]},
        {"level": 0, "community_id": "C0", "parent": "C0", "entity_ids": ["A", "B"]},
    ]).to_parquet(f"{tmp_path}/communities.parquet")
    repo = Repository(engine)
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("community_reports", StepKind.UNIT_FANOUT)]).steps[0]
    return repo, step, str(tmp_path)


def test_batch_returns_deepest_level_first(setup):
    repo, step, _ = setup
    from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy

    batch = CommunityReportsStrategy().next_units_batch(repo, step)
    assert batch is not None and {s.subject_id for s in batch} == {"C1"}  # 叶子层(level 1)先


@pytest.mark.asyncio
async def test_reports_written_and_parent_includes_child(setup):
    repo, step, data_root = setup
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step.id).status == StepStatus.SUCCEEDED
    reports = pd.read_parquet(f"{data_root}/community_reports.parquet")
    assert set(reports["community"]) == {"C0", "C1"}
    assert len(reports[reports["community"] == "C1"]) == 1
    assert len(reports[reports["community"] == "C0"]) == 1
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_strategy_community_reports.py -q`
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 写实现**

`kb_platform/engine/strategies/community_reports.py`:
```python
"""CommunityReportsStrategy: generate community reports bottom-up by level."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from kb_platform.db.enums import StepStatus, UnitKind, UnitStatus
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject, UnitResult, UnitStepStrategy
from kb_platform.graph.adapter import CommunityReport

from sqlalchemy import select


def _data_root(repo: Repository, step) -> Path:
    from kb_platform.db.engine import session_scope

    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        return Path(kb.data_root)


class CommunityReportsStrategy:
    kind = UnitKind.COMMUNITY_REPORT

    def _read(self, root: Path):
        return (
            pd.read_parquet(root / "communities.parquet"),
            pd.read_parquet(root / "entities.parquet"),
            pd.read_parquet(root / "relationships.parquet"),
        )

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        root = _data_root(repo, step)
        comms, _, _ = self._read(root)
        levels = sorted(comms["level"].unique(), reverse=True)  # 最深(叶子)先
        for level in levels:
            rows = comms[comms["level"] == level]
            pending = []
            for _, row in rows.iterrows():
                u = repo.get_unit_by_subject(step.id, "community", row["community_id"])
                if u is None or u.status != UnitStatus.SUCCEEDED:
                    pending.append(Subject("community", row["community_id"]))
            if pending:
                return pending
        return None

    def _context(self, root: Path, comm_id: str) -> dict:
        comms, ents, rels = self._read(root)
        row = comms[comms["community_id"] == comm_id].iloc[0]
        members = list(row["entity_ids"])
        ent_rows = ents[ents["title"].isin(members)][["title", "description"]].to_dict("records")
        rel_rows = rels[rels["source"].isin(members) & rels["target"].isin(members)][["source", "target", "description"]].to_dict("records")
        child_ids = list(comms[comms["parent"] == comm_id]["community_id"])
        child_ids = [c for c in child_ids if c != comm_id]
        sub_reports = []
        for cid in child_ids:
            p = root / "reports" / f"{cid}.json"
            if p.exists():
                sub_reports.append(json.loads(p.read_text()))
        return {"community": comm_id, "level": int(row["level"]), "entities": ent_rows, "relationships": rel_rows, "sub_reports": sub_reports}

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        root = _data_root(repo, repo.get_step(unit.step_id))
        ctx = self._context(root, unit.subject_id)
        report: CommunityReport = await adapter.report_community(ctx)
        return UnitResult(payload=report, input_hash=hashlib.sha512(json.dumps(ctx, default=str).encode()).hexdigest(), llm_raw_output=report.full_content)

    def persist(self, data_root: Path, unit, result: UnitResult) -> None:
        d = data_root / "reports"
        d.mkdir(parents=True, exist_ok=True)
        rep: CommunityReport = result.payload
        (d / f"{unit.subject_id}.json").write_text(json.dumps({
            "title": rep.title, "summary": rep.summary, "findings": rep.findings,
            "rank": rep.rank, "full_content": rep.full_content, "level": rep.level, "community": rep.community,
        }))

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus:
        units = repo.list_units(step.id)
        if not units:
            return StepStatus.PARTIALLY_FAILED
        succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
        if len(succeeded) / len(units) < min_success_ratio:
            return StepStatus.PARTIALLY_FAILED
        rows = []
        for u in succeeded:
            p = data_root / "reports" / f"{u.subject_id}.json"
            if p.exists():
                rows.append(json.loads(p.read_text()))
        pd.DataFrame(rows).to_parquet(data_root / "community_reports.parquet")
        return StepStatus.SUCCEEDED
```

- [ ] **Step 4: 注册 + 跑绿**

`kb_platform/engine/strategies/__init__.py` 追加:
```python
from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy
register_strategy("community_reports", CommunityReportsStrategy())
```
Run: `uv run pytest tests/test_strategy_community_reports.py -q`
Expected: 2 通过。

- [ ] **Step 5: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add kb_platform/engine/strategies/community_reports.py kb_platform/engine/strategies/__init__.py tests/test_strategy_community_reports.py
git commit -m "feat: community_reports strategy (bottom-up multi-level)"
```

---

### Task 8: Orchestrator 接入完整流水线 + `min_success_ratio`

**Files:**
- Modify: `kb_platform/engine/orchestrator.py`
- Test: `tests/test_orchestrator.py`(扩充)

**Interfaces:**
- Consumes: Task 3/5/6/7(strategy + atomic 步)。
- Produces: `Orchestrator.plan()` 返回 6 步;`Orchestrator.run(job_id, min_success_ratio=1.0)`;atomic 步路由 `finalize_graph`/`create_communities`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_orchestrator.py` 追加:
```python
def test_plan_has_six_steps():
    from kb_platform.engine.orchestrator import Orchestrator

    names = [s.name for s in Orchestrator.plan()]
    assert names == ["chunk_documents", "extract_graph", "summarize_descriptions", "finalize_graph", "create_communities", "community_reports"]
```
(完整流水线的端到端测试见 Task 9。)

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_orchestrator.py::test_plan_has_six_steps -q`
Expected: FAIL(plan 仍返回 2 步)。

- [ ] **Step 3: 改 Orchestrator**

在 `kb_platform/engine/orchestrator.py`:
- `plan()` 改为:
```python
    @staticmethod
    def plan() -> list[StepSpec]:
        return [
            StepSpec("chunk_documents", StepKind.ATOMIC),
            StepSpec("extract_graph", StepKind.UNIT_FANOUT),
            StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT),
            StepSpec("finalize_graph", StepKind.ATOMIC),
            StepSpec("create_communities", StepKind.ATOMIC),
            StepSpec("community_reports", StepKind.UNIT_FANOUT),
        ]
```
- `run` 加 `min_success_ratio` 参数并透传 worker:
```python
    async def run(self, job_id: int, min_success_ratio: float = 1.0) -> None:
        self.repo.set_job_status(job_id, JobStatus.RUNNING)
        try:
            for step in self.repo.get_steps(job_id):
                await self._run_step(step, min_success_ratio)
                if self.repo.get_step(step.id).status != StepStatus.SUCCEEDED:
                    self.repo.set_job_status(job_id, JobStatus.FAILED)
                    return
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
        except Exception:
            logger.exception("job %s failed", job_id)
            self.repo.set_job_status(job_id, JobStatus.FAILED)
            raise

    async def _run_step(self, step, min_success_ratio: float) -> None:
        self.repo.set_step_status(step.id, StepStatus.RUNNING)
        if step.kind == StepKind.ATOMIC:
            await self._run_atomic(step)
        else:
            from kb_platform.engine.unit_worker import UnitWorker

            worker = UnitWorker(repo=self.repo, adapter=self.adapter, data_root=self.data_root)
            await worker.run_unit_fanout(step, min_success_ratio=min_success_ratio)

    async def _run_atomic(self, step) -> None:
        from kb_platform.engine import atomic_steps

        if step.name == "chunk_documents":
            await self._chunk_documents(step)
        elif step.name == "finalize_graph":
            atomic_steps.finalize_graph(self.repo, self.adapter, step)
        elif step.name == "create_communities":
            atomic_steps.create_communities(self.repo, self.adapter, step)
        else:
            raise ValueError(f"unknown atomic step: {step.name}")
        self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)
```
> Phase 1 的 `run(self, job_id)` 签名加了默认参数,旧调用 `await orch.run(job.id)` 仍兼容(min_success_ratio=1.0)。

- [ ] **Step 4: 跑绿 + Phase 1 回归**

Run: `uv run pytest tests/test_orchestrator.py -q && uv run pytest -q`
Expected: 新测试通过 + 全套(含 Phase 1 e2e)通过。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator full graph pipeline + min_success_ratio"
```

---

### Task 9: 完整流水线集成测试 + 带失败前进 + needs_reconsolidation

**Files:**
- Test: `tests/test_integration_full_pipeline.py`

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 端到端验证四张 parquet + 带失败前进 + 晚到单元标记。

- [ ] **Step 1: 写测试**

`tests/test_integration_full_pipeline.py`:
```python
import os

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import UnitStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.retry import RetryService


@pytest.fixture()
def kb(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # 多个实体 + 关系,确保聚出社区、有报告
    repo.add_document(kb_id=1, title="d1", text="ACME Org Bob Person ACME Org Alice Person Foo Bar Baz " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_full_pipeline_produces_all_four_parquets(kb):
    repo, data_root = kb
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id)
    assert repo.get_job(job.id).status == "succeeded"
    for name in ("entities", "relationships", "communities", "community_reports"):
        assert os.path.exists(f"{data_root}/{name}.parquet"), f"missing {name}.parquet"
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    assert ents["title"].is_unique
    assert "degree" in ents.columns
    reports = pd.read_parquet(f"{data_root}/community_reports.parquet")
    assert not reports.empty


@pytest.mark.asyncio
async def test_proceed_on_failure_with_threshold(kb):
    repo, data_root = kb
    # 让第一个 chunk 抽取失败(community_reports/summarize 仍可推进,因为 extract 比例够)
    failing = FakeGraphAdapter()
    fail_id = failing.chunk_document(1, repo.get_documents(1)[0].text)[0].chunk_id
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(fail_on={fail_id}), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id, min_success_ratio=0.01)  # 极宽松,允许单 chunk 失败仍推进
    # extract_graph 步在宽松阈值下 SUCCEEDED(带着缺口),整个 job 应成功
    assert repo.get_job(job.id).status == "succeeded"
    extract = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    assert extract.status == "succeeded"
    # 仍有 failed 单元
    units = repo.list_units(extract.id)
    assert any(u.status == UnitStatus.FAILED for u in units)


@pytest.mark.asyncio
async def test_late_retry_marks_needs_reconsolidation(kb):
    repo, data_root = kb
    failing = FakeGraphAdapter()
    fail_id = failing.chunk_document(1, repo.get_documents(1)[0].text)[0].chunk_id
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(fail_on={fail_id}), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id, min_success_ratio=0.01)
    extract = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    failed_unit = [u for u in repo.list_units(extract.id) if u.status == UnitStatus.FAILED][0]
    # 步已结算后,重试该单元(用不失败的 adapter)
    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    retry.retry_unit(failed_unit.id)
    await retry.rerun_step(extract.id)
    fresh = repo.get_unit_by_subject(extract.id, "chunk", failed_unit.subject_id)
    assert fresh.status == UnitStatus.SUCCEEDED
    assert fresh.needs_reconsolidation is True
```

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/test_integration_full_pipeline.py -q`
Expected: 3 通过。
> 若 `test_proceed_on_failure_with_threshold` 失败:检查 `min_success_ratio=0.01` 下 extract_graph.finalize 是否返回 SUCCEEDED(应:1 失败 / 多成功 比例 >> 0.01)。若 community_reports 在缺一个 chunk 的实体下报错,确认 FakeGraphAdapter 处理空实体社区(返回空 report 即可)。

- [ ] **Step 3: 处理 `rerun_step` 的 `needs_reconsolidation`**

`kb_platform/retry.py` 的 `rerun_step` 在重跑后需把"步已 SUCCEEDED 后才重试成功"的单元标 `needs_reconsolidation`。改 `RetryService.rerun_step`:
```python
    async def rerun_step(self, step_id: int) -> None:
        step = self.repo.get_step(step_id)
        already_succeeded = self.repo.get_step(step_id).status == StepStatus.SUCCEEDED
        worker = self._worker_cls(repo=self.repo, adapter=self.adapter, data_root=self.data_root, concurrency=self.concurrency)
        await worker.run_unit_fanout(step)
        if already_succeeded:
            for u in self.repo.list_units(step_id):
                if u.status == UnitStatus.SUCCEEDED and u.attempt_no > 1:
                    self.repo.mark_needs_reconsolidation(u.id)
```
> 顶部 `from kb_platform.db.enums import UnitStatus` 已在或需补 import。

- [ ] **Step 4: 全量回归 + ruff**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
Expected: 全套通过(Phase 1 + 2a)、ruff clean。

- [ ] **Step 5: 提交**

```bash
git add tests/test_integration_full_pipeline.py kb_platform/retry.py
git commit -m "test: full pipeline + proceed-on-failure + needs_reconsolidation"
```

---

## Self-Review(写完后自查)

**1. Spec 覆盖(对照 spec §3–§8):**
- `UnitStepStrategy` + 通用 worker → Task 3 ✓
- extract_graph 重构 → Task 3 ✓
- summarize_descriptions → Task 5 ✓
- finalize_graph / create_communities atomic → Task 6 ✓
- community_reports 多层 → Task 7 ✓
- 带失败前进(min_success_ratio) → Task 8(orchestrator)+ Task 9(测试)✓
- Unit 缺失列(llm_raw_output/cost_json/input_hash/needs_reconsolidation) → Task 1 ✓;worker_id/heartbeat_at 显式延后 2b ✓(非目标)
- graphrag Adapter 接缝扩展 → Task 4(FakeGraphAdapter)+ **真实 GraphRagAdapter 新方法延后 Phase 2b**(非目标,spec §7 的真实实现随 2b 真实 LLM 一起做)⚠ 已在计划开头声明
- 测试(每 strategy + atomic + 编排集成 + 带失败前进 + 迁移)→ Task 1/5/6/7/9 ✓

**已识别范围缺口(有意为之):** 真实 `GraphRagAdapter` 的 `summarize_entity`/`report_community`/`cluster_relationships`/`finalize_entities_relationships` 实现 + MockLLM 契约测试 **不在本计划**(延后 Phase 2b,与真实 LLM 运行一起做)。2a 全程用 `FakeGraphAdapter` 验证引擎正确性,这是核心价值。

**2. 占位符扫描:** Task 3/5 代码块内有标注的占位行(已用"清理 Step"说明给出最终形态),非 TBD;其余均含可执行代码/命令。

**3. 类型一致性:** `Subject(subject_type, subject_id)`、`UnitResult(payload, input_hash, cost_json, llm_raw_output)`、`UnitStepStrategy` 四方法签名、`STRATEGIES`、`run_unit_fanout(step, min_success_ratio=1.0)`、repo 新方法名、`CommunityReport` 字段、atomic 步函数名 `finalize_graph`/`create_communities` 跨任务一致。`GraphAdapter` Protocol 在 Task 4 扩展后被 strategies 引用的方法名(`summarize_entity`/`report_community`/`cluster_relationships`/`finalize_entities_relationships`)与 FakeGraphAdapter 实现一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-phase2a-backend-complete.md`. Two execution options:

**1. Subagent-Driven(推荐)** — 每任务派发独立 subagent,任务间两阶段评审,迭代快(与 Phase 1 同款)。

**2. Inline Execution** — 当前会话用 executing-plans 批量执行,带检查点。

Which approach?
