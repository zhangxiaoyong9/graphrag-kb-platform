# Phase 2b-1 — 后端服务 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Phase 1/2a 的引擎升级为一个可操作的后端服务:真实 graphrag LLM/聚类 + 独立 worker 进程(SQLite 队列、心跳、崩溃自动续跑)+ FastAPI 索引管理 API。

**Architecture:** API(uvicorn)与 Worker(asyncio)双进程共享 SQLite;job 表即队列;worker 顺序领取、跑 unit 时盖 `worker_id`/`heartbeat_at`、启动+周期恢复过期项;真实 `GraphRagAdapter` 在 2a 的同一 Protocol 下替换 Fake(引擎零改)。查询/前端/增量不在本计划。

**Tech Stack:** Python 3.11–3.13 · FastAPI + uvicorn + python-multipart + httpx(TestClient)· SQLAlchemy 2.x + Alembic · SQLite(WAL)· `graphrag==3.1.*` · pytest + pytest-asyncio。

## Global Constraints

- 锁定 `graphrag==3.1.*`;graphrag 内部仅可在 `kb_platform/graph/graphrag_adapter.py` 引用。
- Python `>=3.11,<3.14`;asyncio 单线程;SQLite WAL 单写者。
- Detached ORM 对象只读标量列。
- 每任务 TDD:失败测试 → 红 → 最小实现 → 绿 → 提交;约定式前缀(`feat:`/`refactor:`/`test:`/`chore:`/`fix:`)。
- 真实 adapter 四方法签名/返回必须与 2a `GraphAdapter` Protocol 一致 → **2a 全部 41 测试必须仍通过**。
- worker 一次只跑一个 job;崩溃恢复 = 自动续跑(重置过期 RUNNING → PENDING,续跑幂等)。
- 查询 / React / WebSocket / 增量 均不在本计划(Phase 3 / 2b-2)。

## 关键接口契约(跨任务共享)

```python
# kb_platform/db/repository.py 新增/改
def claim_one_pending_job(self) -> Job | None: ...        # 原子 UPDATE job SET status=RUNNING WHERE status=PENDING LIMIT 1
def set_unit_running(self, unit_id, worker_id: str, heartbeat_at) -> None: ...  # 扩展自 2a
def touch_unit_heartbeat(self, unit_id, heartbeat_at) -> None: ...
def recover_stale_units(self, stale_before) -> int: ...   # RUNNING+heartbeat<stale_before → PENDING;返回数
def recover_stale_jobs(self) -> int: ...                  # status=RUNNING job → PENDING;返回数
def create_job_pending(self, kb_id, method) -> Job: ...   # API 用:建 PENDING job + 6 步(Orchestrator.plan)
```

```python
# kb_platform/graph/graphrag_adapter.py 扩展(真实实现)
class GraphRagAdapter:
    # 2a 已有: chunk_document, extract_chunk, merge_extractions
    async def summarize_entity(self, name, descriptions) -> str: ...        # graphrag SummarizeExtractor
    async def report_community(self, context: dict) -> CommunityReport: ... # graphrag CommunityReportsExtractor
    def cluster_relationships(self, rels_df) -> pd.DataFrame: ...           # graphrag cluster_graph(真 Leiden)
    def finalize_entities_relationships(self, e_df, r_df) -> tuple: ...      # 度数计算(与 Fake 同构)

def build_default_adapter(*, data_root: str, model_config) -> GraphRagAdapter: ...  # 2a 已有,本计划补全四方法
def build_adapter_from_settings(settings_json: str, data_root: str) -> GraphRagAdapter: ...  # 解析 settings → ModelConfig → adapter
```

```python
# kb_platform/worker.py(新)
def run_worker(*, repo, adapter_factory, concurrency=4, poll_interval=2.0, heartbeat_interval=5.0, stale_seconds=30.0) -> None: ...
# adapter_factory: Callable[[KnowledgeBase], GraphAdapter];生产按 KB settings 建真实,测试注入 Fake
```

```python
# kb_platform/api/app.py(新)
def create_app(repo) -> FastAPI: ...   # 依赖注入 repo;TestClient 用内存 SQLite
```

---

### Task 1: `worker_id`/`heartbeat_at` 列 + 迁移

**Files:**
- Modify: `kb_platform/db/models.py`
- Create: `alembic/versions/0003_worker_heartbeat.py`
- Test: `tests/test_migration.py`(扩充)

**Interfaces:**
- Produces: `Unit.worker_id`(String nullable)、`Unit.heartbeat_at`(DateTime nullable);迁移 `0003`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_migration.py` 追加:
```python
def test_migration_adds_worker_heartbeat_columns(tmp_path):
    import subprocess
    import sys

    from sqlalchemy import inspect as sa_inspect

    from kb_platform.db.engine import create_engine

    db = tmp_path / "wh.db"
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"], check=True)
    cols = {c["name"] for c in sa_inspect(create_engine(f"sqlite:///{db}")).get_columns("unit")}
    assert "worker_id" in cols and "heartbeat_at" in cols
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_migration.py::test_migration_adds_worker_heartbeat_columns -q`
Expected: FAIL(列缺失)。

- [ ] **Step 3: 改 models**

`kb_platform/db/models.py` 顶部 `from sqlalchemy import ...` 加入 `DateTime`;`Unit` 类加(SQLAlchemy 2.x `Mapped`):
```python
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```
(`datetime` 顶部 `from datetime import datetime`。)

- [ ] **Step 4: 生成迁移**

Run: `uv run alembic revision --autogenerate -m "worker heartbeat columns" --rev-id 0003`
确认 `0003_worker_heartbeat.py` `upgrade()` 对 `unit` `add_column` 两列、`downgrade()` `drop_column`。

- [ ] **Step 5: 跑绿 + 全量**

Run: `uv run pytest -q`
Expected: 全套(含 2a 的 41)通过。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/db/models.py alembic/versions/0003_worker_heartbeat.py tests/test_migration.py
git commit -m "feat: add unit worker_id/heartbeat_at columns"
```

---

### Task 2: 真实 adapter —— `summarize_entity` + `cluster_relationships` + `finalize`

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py`
- Test: `tests/test_graphrag_adapter.py`(扩充)

**Interfaces:**
- Consumes: 2a `GraphAdapter` Protocol;graphrag `SummarizeExtractor`、`cluster_graph`。
- Produces: 真实 `summarize_entity`(MockLLM 契约)、`cluster_relationships`(小图真 Leiden)、`finalize_entities_relationships`(度数,与 Fake 同构)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_graphrag_adapter.py` 追加:
```python
def test_real_summarize_entity_via_mockllm(tmp_path):
    from kb_platform.graph.graphrag_adapter import build_default_adapter

    cfg = _mock_model_config_with_responses(['{"summary": "merged ACME"}'])  # 见下
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=cfg)
    import asyncio

    out = asyncio.run(adapter.summarize_entity("ACME", ["d1", "d2"]))
    assert isinstance(out, str) and out  # MockLLM 回 canned 文本


def test_real_cluster_relationships_real_leiden(tmp_path):
    import pandas as pd

    from kb_platform.graph.graphrag_adapter import build_default_adapter

    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    rels = pd.DataFrame([
        {"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
        {"source": "B", "target": "C", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]},
        {"source": "X", "target": "Y", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c2"]},
    ])
    comms = adapter.cluster_relationships(rels)
    assert {"level", "community_id", "parent", "entity_ids"} <= set(comms.columns)
    members = {e for ids in comms["entity_ids"] for e in ids}
    assert {"A", "B", "C", "X", "Y"} <= members


def test_real_finalize_adds_degrees(tmp_path):
    import pandas as pd

    from kb_platform.graph.graphrag_adapter import build_default_adapter

    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    ents = pd.DataFrame([{"title": "A", "type": "T", "description": ["d"], "text_unit_ids": ["c1"], "frequency": 1}])
    rels = pd.DataFrame([{"source": "A", "target": "B", "weight": 1.0, "description": ["d"], "text_unit_ids": ["c1"]}])
    e2, r2 = adapter.finalize_entities_relationships(ents, rels)
    assert "degree" in e2.columns and "combined_degree" in r2.columns
```
> 测试辅助(放测试文件顶部,沿用 Phase 1 Task 7 的 `_mock_model_config`):
```python
def _mock_model_config():
    from graphrag_llm.config import ModelConfig

    return ModelConfig(type="mock", model_provider="mock", model="mock", mock_responses=['("entity"<|>X<|>Y<|>z)##<|COMPLETE|>'])


def _mock_model_config_with_responses(responses):
    from graphrag_llm.config import ModelConfig

    return ModelConfig(type="mock", model_provider="mock", model="mock", mock_responses=responses)
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_graphrag_adapter.py -q`
Expected: FAIL(真实方法未实现/未接 graphrag)。

- [ ] **Step 3: 实现**

在 `kb_platform/graph/graphrag_adapter.py`:
- 顶部加:
```python
import pandas as pd
```
- `GraphRagAdapter.__init__` 增加 `summarize_factory`、`cluster_fn`、`finalize_fn`(可注入,默认 graphrag):
```python
    def __init__(self, *, chunker, extractor_factory, entity_types, summarize_factory=None, cluster_fn=None, finalize_fn=None) -> None:
        self._chunker = chunker
        self._extractor_factory = extractor_factory
        self._entity_types = entity_types
        self._summarize_factory = summarize_factory
        self._cluster_fn = cluster_fn
        self._finalize_fn = finalize_fn
```
- 三方法:
```python
    async def summarize_entity(self, name: str, descriptions: list[str]) -> str:
        extractor = self._summarize_factory()
        result = await extractor(id=name, descriptions=list(descriptions))
        return result.description

    def cluster_relationships(self, relationships: pd.DataFrame) -> pd.DataFrame:
        from graphrag.index.operations.cluster_graph import cluster_graph

        edges = relationships.rename(columns={"source": "source", "target": "target"})
        communities = cluster_graph(edges=relationships, max_cluster_size=10, use_lcc=True)
        # communities: list[(level, cluster_id, parent, [node,...])]
        return pd.DataFrame([
            {"level": level, "community_id": str(cid), "parent": str(parent), "entity_ids": list(nodes)}
            for (level, cid, parent, nodes) in communities
        ])

    def finalize_entities_relationships(self, entities: pd.DataFrame, relationships: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        # 度数计算(确定性,与 FakeGraphAdapter 同构;无 LLM)
        deg: dict[str, int] = {}
        for _, r in relationships.iterrows():
            deg[r["source"]] = deg.get(r["source"], 0) + 1
            deg[r["target"]] = deg.get(r["target"], 0) + 1
        e = entities.copy()
        if not e.empty:
            e["degree"] = e["title"].map(lambda t: deg.get(t, 0))
        rr = relationships.copy()
        if not rr.empty:
            rr["combined_degree"] = rr.apply(lambda r: deg.get(r["source"], 0) + deg.get(r["target"], 0), axis=1)
        return e, rr
```
- `build_default_adapter` 增加 summarize 工厂:
```python
def build_default_adapter(*, data_root: str, model_config, max_gleanings: int = 0) -> "GraphRagAdapter":
    from graphrag_chunking.chunking_config import ChunkingConfig
    from graphrag_chunking.chunk_strategy_type import ChunkerType
    from graphrag_chunking.chunker_factory import create_chunker
    from graphrag_llm.completion import create_completion
    from graphrag.tokenizer.get_tokenizer import get_tokenizer
    from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor
    from graphrag.index.operations.summarize_descriptions.description_summary_extractor import SummarizeExtractor
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT
    from graphrag.prompts.index.summarize_descriptions import SUMMARIZE_PROMPT
    from graphrag.config.defaults import DEFAULT_ENTITY_TYPES

    tokenizer = get_tokenizer(encoding_model="cl100k_base")
    chunker = create_chunker(ChunkingConfig(type=ChunkerType.Tokens, encoding_model="cl100k_base", size=1200, overlap=100), encode=tokenizer.encode, decode=tokenizer.decode)
    completion = create_completion(model_config)

    def extractor_factory():
        return GraphExtractor(model=completion, prompt=GRAPH_EXTRACTION_PROMPT, max_gleanings=max_gleanings)

    def summarize_factory():
        return SummarizeExtractor(model=completion, max_summary_length=500, max_input_tokens=32000, summarization_prompt=SUMMARIZE_PROMPT)

    return GraphRagAdapter(chunker=chunker, extractor_factory=extractor_factory, entity_types=list(DEFAULT_ENTITY_TYPES), summarize_factory=summarize_factory)
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_graphrag_adapter.py -q && uv run pytest -q`
Expected: 新 3 测试 + 全套通过(2a 回归)。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_graphrag_adapter.py
git commit -m "feat: real adapter summarize_entity/cluster_relationships/finalize"
```

---

### Task 3: 真实 adapter —— `report_community`

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py`
- Modify: `kb_platform/graph/graphrag_adapter.py` `build_default_adapter`(加 report 工厂)
- Test: `tests/test_graphrag_adapter.py`(扩充)

**Interfaces:**
- Produces: 真实 `report_community`(MockLLM 契约)。

- [ ] **Step 1: 写失败测试**

追加:
```python
def test_real_report_community_via_mockllm(tmp_path):
    import json

    from kb_platform.graph.graphrag_adapter import build_default_adapter

    canned = json.dumps({"title": "T", "summary": "S", "findings": [{"summary": "f", "explanation": "e"}], "rank": 0.5, "fields": []})
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config_with_responses([canned]))
    import asyncio

    rep = asyncio.run(adapter.report_community({"community": "C0", "level": 0, "entities": [{"title": "A", "description": "d"}], "relationships": [], "sub_reports": []}))
    assert rep.community == "C0" and rep.title == "T"
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_graphrag_adapter.py::test_real_report_community_via_mockllm -q`
Expected: FAIL。

- [ ] **Step 3: 实现**

`GraphRagAdapter.__init__` 加 `report_factory`;`build_default_adapter` 注入:
```python
    async def report_community(self, context: dict) -> "CommunityReport":
        extractor = self._report_factory()
        input_text = _format_community_context(context)
        result = await extractor(input_text=input_text)
        so = result.structured_output
        from kb_platform.graph.adapter import CommunityReport

        return CommunityReport(
            title=getattr(so, "title", context["community"]),
            summary=getattr(so, "summary", ""),
            findings=[f.summary for f in getattr(so, "findings", []) or []],
            rank=float(getattr(so, "rank", 0.0) or 0.0),
            full_content=result.output or "",
            level=context["level"],
            community=context["community"],
        )
```
模块级辅助:
```python
def _format_community_context(context: dict) -> str:
    ents = "\n".join(f"- {e['title']}: {e.get('description', '')}" for e in context.get("entities", []))
    rels = "\n".join(f"- {r['source']} -> {r['target']}" for r in context.get("relationships", []))
    subs = "\n".join(f"- {s.get('title', s.get('community', ''))}: {s.get('summary', '')}" for s in context.get("sub_reports", []))
    return f"Community: {context['community']} (level {context['level']})\nEntities:\n{ents}\nRelationships:\n{rels}\nSub-community reports:\n{subs}"
```
`build_default_adapter` 加:
```python
    from graphrag.index.operations.summarize_communities.community_reports_extractor import CommunityReportsExtractor
    from graphrag.prompts.index.community_report import COMMUNITY_REPORT_PROMPT

    def report_factory():
        return CommunityReportsExtractor(model=completion, extraction_prompt=COMMUNITY_REPORT_PROMPT, max_report_length=2000)
```
并传入 `GraphRagAdapter(..., report_factory=report_factory)`。
> 若 `COMMUNITY_REPORT_PROMPT` 路径或 `CommunityReportsExtractor` 构造签名与实测不符,以仓库 `grep` 为准修正(唯一允许探查 graphrag 内部的任务,同 Phase 1 Task 7)。

- [ ] **Step 4: 跑绿 + 全量**

Run: `uv run pytest tests/test_graphrag_adapter.py -q && uv run pytest -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_graphrag_adapter.py
git commit -m "feat: real adapter report_community"
```

---

### Task 4: UnitWorker 心跳(`worker_id`/`heartbeat_at`)

**Files:**
- Modify: `kb_platform/db/repository.py`(`set_unit_running` 扩展、`touch_unit_heartbeat`)
- Modify: `kb_platform/engine/unit_worker.py`(盖戳 + 后台刷新)
- Test: `tests/test_unit_worker.py`(扩充)

**Interfaces:**
- Produces: `set_unit_running(unit_id, worker_id, heartbeat_at)`、`touch_unit_heartbeat(unit_id, heartbeat_at)`;UnitWorker 跑 unit 时盖 `worker_id`+`heartbeat_at`,后台 task 周期刷新。

- [ ] **Step 1: 写失败测试**

在 `tests/test_unit_worker.py` 追加:
```python
@pytest.mark.asyncio
async def test_unit_running_stamps_worker_id_and_heartbeat(setup):
    import datetime

    repo, step_id, data_root = setup  # 复用 2a 的 fixture(2 chunk)
    from kb_platform.engine.unit_worker import UnitWorker
    from kb_platform.graph.adapter import FakeGraphAdapter

    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, worker_id="w1", heartbeat_interval=0.01)
    await worker.run_unit_fanout(repo.get_step(step_id))
    units = repo.list_units(step_id)
    assert all(u.worker_id == "w1" for u in units)
    assert all(u.heartbeat_at is not None for u in units)
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_unit_worker.py::test_unit_running_stamps_worker_id_and_heartbeat -q`
Expected: FAIL(worker_id 默认/未盖)。

- [ ] **Step 3: 改 repository**

`set_unit_running` 扩展:
```python
    def set_unit_running(self, unit_id: int, worker_id: str | None = None, heartbeat_at=None) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.RUNNING
            u.attempt_no += 1
            if worker_id is not None:
                u.worker_id = worker_id
            if heartbeat_at is not None:
                u.heartbeat_at = heartbeat_at

    def touch_unit_heartbeat(self, unit_id: int, heartbeat_at) -> None:
        with session_scope(self.engine) as s:
            s.get(Unit, unit_id).heartbeat_at = heartbeat_at
```

- [ ] **Step 4: 改 UnitWorker**

`UnitWorker.__init__` 加 `worker_id: str = "worker"`、`heartbeat_interval: float = 5.0`;`_run_batch` claim 后盖戳,并为每个 unit 起后台刷新;`_process` 内 try/finally 取消刷新:
```python
    def __init__(self, *, repo, adapter, data_root, concurrency=4, worker_id="worker", heartbeat_interval=5.0):
        # ...原有...
        self.worker_id = worker_id
        self.heartbeat_interval = heartbeat_interval
```
`_run_batch` 中 `for u in units: self.repo.set_unit_running(u.id, self.worker_id, datetime.now())`(`from datetime import datetime`)。
`handle` 改为带心跳刷新:
```python
        async def handle(u):
            async with sem:
                stop = asyncio.Event()
                hb = asyncio.create_task(self._heartbeat(u.id, stop))
                try:
                    await self._process(strategy, u)
                finally:
                    stop.set()
                    await hb

    async def _heartbeat(self, unit_id, stop):
        while not stop.is_set():
            self.repo.touch_unit_heartbeat(unit_id, datetime.now())
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 5: 跑绿 + 2a 回归**

Run: `uv run pytest tests/test_unit_worker.py -q && uv run pytest -q`
Expected: 全绿(含 2a)。

- [ ] **Step 6: 提交**

```bash
git add kb_platform/db/repository.py kb_platform/engine/unit_worker.py tests/test_unit_worker.py
git commit -m "feat: unit worker heartbeat (worker_id/heartbeat_at)"
```

---

### Task 5: Worker 进程 —— 轮询/领取/续跑/恢复

**Files:**
- Modify: `kb_platform/db/repository.py`(`claim_one_pending_job`、`recover_stale_units`、`recover_stale_jobs`、`create_job_pending`)
- Create: `kb_platform/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: Task 4 心跳、Orchestrator、`adapter_factory`。
- Produces: `run_worker(repo, adapter_factory, ...)`、repo 的 claim/recover/create_job_pending。

- [ ] **Step 1: 写失败测试**

`tests/test_worker.py`:
```python
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import FakeGraphAdapter


def _repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="d", text="ACME Org Bob Person Foo Bar Baz " * 200)
    return repo


@pytest.mark.asyncio
async def test_worker_picks_up_and_completes_pending_job(tmp_path):
    import asyncio

    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")
    assert job.status == "pending"
    await run_worker_once(repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), heartbeat_interval=0.01)
    assert repo.get_job(job.id).status == "succeeded"


@pytest.mark.asyncio
async def test_worker_crash_recovery_resumes(tmp_path):
    from datetime import datetime, timedelta

    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")
    # 模拟崩溃:手动把 job+一个 unit 置 RUNNING + 过期 heartbeat
    repo.set_job_status(job.id, __import__("kb_platform.db.enums", fromlist=["JobStatus"]).JobStatus.RUNNING)
    extract_step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    repo.add_unit(extract_step.id, "chunk", "stale-chunk")
    stale = datetime.now() - timedelta(seconds=999)
    with session_scope(repo.engine) as s:
        u = s.query(__import__("kb_platform.db.models", fromlist=["Unit"]).Unit).filter_by(step_id=extract_step.id).one()
        u.status = "running"; u.worker_id = "dead"; u.heartbeat_at = stale
    # 恢复 + 续跑
    await run_worker_once(repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), heartbeat_interval=0.01, recover=True)
    assert repo.get_job(job.id).status == "succeeded"
```
> 注:`run_worker_once` 是单次"恢复→领一个 job→跑完"的测试入口;`run_worker` 是无限循环的生产入口。`create_job_pending` 用 `Orchestrator.plan()` 建 6 步 PENDING job。

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_worker.py -q`
Expected: FAIL(模块/方法不存在)。

- [ ] **Step 3: 改 repository**

```python
    def create_job_pending(self, kb_id: int, method: str = "standard") -> Job:
        from kb_platform.engine.orchestrator import Orchestrator

        return self.create_job(kb_id=kb_id, type="full", specs=Orchestrator.plan(), method=method)  # create_job 默认 status=PENDING

    def claim_one_pending_job(self) -> Job | None:
        from sqlalchemy import update

        with session_scope(self.engine) as s:
            job = s.scalars(select(Job).where(Job.status == JobStatus.PENDING).order_by(Job.id).limit(1)).first()
            if job is None:
                return None
            s.execute(update(Job).where(Job.id == job.id).values(status=JobStatus.RUNNING))
            return s.get(Job, job.id)

    def recover_stale_units(self, stale_before) -> int:
        with session_scope(self.engine) as s:
            stale = list(s.scalars(select(Unit).where(Unit.status == UnitStatus.RUNNING, Unit.heartbeat_at < stale_before)))
            for u in stale:
                u.status = UnitStatus.PENDING
            return len(stale)

    def recover_stale_jobs(self) -> int:
        with session_scope(self.engine) as s:
            jobs = list(s.scalars(select(Job).where(Job.status == JobStatus.RUNNING)))
            for j in jobs:
                j.status = JobStatus.PENDING
            return len(jobs)
```

- [ ] **Step 4: 写 worker.py**

`kb_platform/worker.py`:
```python
"""Background worker: polls SQLite for pending jobs, runs them with crash recovery."""

import asyncio
import logging
from datetime import datetime, timedelta

from kb_platform.db.repository import Repository

logger = logging.getLogger(__name__)


async def run_worker_once(*, repo: Repository, adapter_factory, heartbeat_interval=5.0, stale_seconds=30.0, recover=False, concurrency=4) -> None:
    if recover:
        repo.recover_stale_units(datetime.now() - timedelta(seconds=stale_seconds))
        repo.recover_stale_jobs()
    job = repo.claim_one_pending_job()
    if job is None:
        return
    from kb_platform.db.models import KnowledgeBase
    from kb_platform.engine.orchestrator import Orchestrator
    from sqlalchemy import select

    from kb_platform.db.engine import session_scope

    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        data_root, min_ratio = kb.data_root, _parse_min_ratio(kb.settings_json)
    adapter = adapter_factory(kb) if adapter_factory.__code__.co_argcount else adapter_factory()
    orch = Orchestrator(repo=repo, adapter=adapter, data_root=data_root)
    await orch.run(job.id, min_success_ratio=min_ratio)


def _parse_min_ratio(settings_json: str) -> float:
    import json

    try:
        return float(json.loads(settings_json or "{}").get("min_unit_success_ratio", 1.0))
    except Exception:
        return 1.0


def run_worker(*, repo, adapter_factory, poll_interval=2.0, **kw) -> None:
    """Production entry: loop forever, recovering + claiming one job at a time."""
    while True:
        asyncio.run(run_worker_once(repo=repo, adapter_factory=adapter_factory, recover=True, **kw))
        # 简化:用 asyncio.run 每轮一个事件循环;若需长驻后台 task,见部署文档
        import time

        time.sleep(poll_interval)


if __name__ == "__main__":
    import sys

    from kb_platform.db.engine import create_engine
    from kb_platform.graph.graphrag_adapter import build_adapter_from_settings

    db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"
    repo = Repository(create_engine(f"sqlite:///{db}"))
    run_worker(repo=repo, adapter_factory=lambda kb: build_adapter_from_settings(kb.settings_json, kb.data_root))
```
> `build_adapter_from_settings` 在 Task 6/真实路径里实现;此处 worker 测试用 `lambda kb: FakeGraphAdapter()`(工厂接受 `kb` 参数)。`adapter_factory.__code__.co_argcount` 的判断是临时兼容写法 —— 若实现者觉得别扭,可统一约定 `adapter_factory(kb) -> GraphAdapter`(测试也传 `lambda kb: Fake()`),去掉该分支。

- [ ] **Step 5: 跑绿**

Run: `uv run pytest tests/test_worker.py -q`
Expected: 2 通过(含崩溃恢复续跑)。

- [ ] **Step 6: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add kb_platform/db/repository.py kb_platform/worker.py tests/test_worker.py
git commit -m "feat: worker process with poll/claim/crash-recovery"
```

---

### Task 6: FastAPI —— KB + 文档端点

**Files:**
- Modify: `pyproject.toml`(加 fastapi/uvicorn/python-multipart/httpx)
- Create: `kb_platform/api/__init__.py`
- Create: `kb_platform/api/app.py`
- Create: `kb_platform/api/routes_kbs.py`
- Test: `tests/test_api_kbs.py`

**Interfaces:**
- Produces: `create_app(repo) -> FastAPI`;`POST /kbs`、`GET /kbs`、`GET /kbs/{id}`、`POST /kbs/{id}/documents`(multipart+JSON)、`GET /kbs/{id}/documents`。repo 经依赖注入(含上传落盘)。

- [ ] **Step 1: 加依赖**

`pyproject.toml` `dependencies` 加:`"fastapi>=0.115"`、`"uvicorn[standard]>=0.30"`、`"python-multipart>=0.0.9"`;`dev` 加 `"httpx>=0.27"`。`uv sync --extra dev`。

- [ ] **Step 2: 写失败测试**

`tests/test_api_kbs.py`:
```python
import pytest
from fastapi.testclient import TestClient

from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.api.app import create_app


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return TestClient(create_app(Repository(engine), data_root=str(tmp_path)))


def test_create_and_list_kbs(client):
    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "min_unit_success_ratio": 1.0})
    assert r.status_code == 201 and r.json()["name"] == "kb1"
    assert client.get("/kbs").json()[0]["name"] == "kb1"


def test_upload_document_text(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post("/kbs/1/documents", json={"title": "d1", "text": "hello world"})
    assert r.status_code == 201
    docs = client.get("/kbs/1/documents").json()
    assert len(docs) == 1 and docs[0]["title"] == "d1"
```

- [ ] **Step 3: 跑红**

Run: `uv run pytest tests/test_api_kbs.py -q`
Expected: FAIL(模块不存在)。

- [ ] **Step 4: 实现**

`kb_platform/api/__init__.py`:空。
`kb_platform/api/app.py`:
```python
"""FastAPI app factory with repo + data_root dependency injection."""

from fastapi import FastAPI

from kb_platform.api.routes_kbs import router
from kb_platform.db.repository import Repository


def create_app(repo: Repository, data_root: str = ".") -> FastAPI:
    app = FastAPI(title="KB Platform")
    app.state.repo = repo
    app.state.data_root = data_root
    app.include_router(router)
    return app
```
`kb_platform/api/routes_kbs.py`:
```python
"""KB + document endpoints."""

import json
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException

from kb_platform.db.enums import JobStatus

router = APIRouter()


@router.post("/kbs", status_code=201)
def create_kb(payload: dict, request: Request):
    repo = request.app.state.repo
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    settings = json.dumps(json.loads(payload.get("settings_yaml") or "{}"))
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(name=payload["name"], method=payload.get("method", "standard"), settings_json=settings, data_root=request.app.state.data_root)
        s.add(kb); s.flush()
        return {"id": kb.id, "name": kb.name}


@router.get("/kbs")
def list_kbs(request: Request):
    from sqlalchemy import select

    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        return [{"id": k.id, "name": k.name} for k in s.scalars(select(KnowledgeBase))]


@router.get("/kbs/{kb_id}")
def get_kb(kb_id: int, request: Request):
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    repo = request.app.state.repo
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if not kb:
            raise HTTPException(404)
        return {"id": kb.id, "name": kb.name, "method": kb.method}


@router.post("/kbs/{kb_id}/documents", status_code=201)
def add_document(kb_id: int, request: Request, title: str | None = None, text: str | None = None, file: UploadFile | None = File(None)):
    repo = request.app.state.repo
    if text is not None:
        doc = repo.add_document(kb_id=kb_id, title=title or "untitled", text=text)
    elif file is not None:
        raw = file.file.read().decode("utf-8", errors="replace")
        doc = repo.add_document(kb_id=kb_id, title=title or file.filename, text=raw)
    else:
        raise HTTPException(400, "provide 'text' or 'file'")
    return {"id": doc.id, "title": doc.title}


@router.get("/kbs/{kb_id}/documents")
def list_documents(kb_id: int, request: Request):
    repo = request.app.state.repo
    return [{"id": d.id, "title": d.title} for d in repo.get_documents(kb_id)]
```

- [ ] **Step 5: 跑绿**

Run: `uv run pytest tests/test_api_kbs.py -q`
Expected: 2 通过。

- [ ] **Step 6: 全量 + 提交**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
```bash
git add pyproject.toml uv.lock kb_platform/api tests/test_api_kbs.py
git commit -m "feat: fastapi kb + document endpoints"
```

---

### Task 7: FastAPI —— 任务/状态/重试端点

**Files:**
- Create: `kb_platform/api/routes_jobs.py`
- Modify: `kb_platform/api/app.py`(挂载)
- Test: `tests/test_api_jobs.py`

**Interfaces:**
- Produces: `POST /kbs/{id}/jobs`(建 PENDING job,202)、`GET /jobs/{id}`、`GET /jobs/{id}/steps`、`GET /steps/{id}/units?status=`、`POST /units/{id}/retry`、`POST /steps/{id}/retry`。

- [ ] **Step 1: 写失败测试**

`tests/test_api_jobs.py`:
```python
import pytest
from fastapi.testclient import TestClient

from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.api.app import create_app


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    c.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    c.post("/kbs/1/documents", json={"title": "d", "text": "ACME Org Bob Foo Bar Baz " * 200})
    return c


def test_trigger_job_creates_pending(client):
    r = client.post("/kbs/1/jobs", json={"method": "standard"})
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "pending"
    assert len(client.get(f"/jobs/{job_id}/steps").json()) == 6


def test_step_units_filtered_by_status(client):
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    units = client.get(f"/steps/{extract['id']}/units").json()
    assert len(units) >= 1
```
> retry 端点单测:建 job + 手动置某 unit FAILED → `POST /units/{id}/retry` → 该 unit 回 pending。

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_api_jobs.py -q`
Expected: FAIL。

- [ ] **Step 3: 实现 routes_jobs.py**

```python
"""Job / step / unit status + retry endpoints."""

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


@router.post("/kbs/{kb_id}/jobs", status_code=202)
def trigger_job(kb_id: int, payload: dict, request: Request):
    repo = request.app.state.repo
    job = repo.create_job_pending(kb_id=kb_id, method=payload.get("method", "standard"))
    return {"id": job.id, "status": job.status}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, request: Request):
    repo = request.app.state.repo
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return {"id": job.id, "status": job.status, "steps": [{"id": s.id, "name": s.name, "status": s.status} for s in repo.get_steps(job_id)]}


@router.get("/jobs/{job_id}/steps")
def get_steps(job_id: int, request: Request):
    repo = request.app.state.repo
    return [{"id": s.id, "name": s.name, "ordinal": s.ordinal, "kind": s.kind, "status": s.status} for s in repo.get_steps(job_id)]


@router.get("/steps/{step_id}/units")
def get_units(step_id: int, request: Request, status: str | None = None):
    repo = request.app.state.repo
    units = repo.list_units(step_id)
    if status:
        units = [u for u in units if u.status == status]
    return [{"id": u.id, "subject_id": u.subject_id, "status": u.status, "error": u.error, "llm_raw_output": u.llm_raw_output, "needs_reconsolidation": u.needs_reconsolidation} for u in units]


@router.post("/units/{unit_id}/retry")
def retry_unit(unit_id: int, request: Request):
    repo = request.app.state.repo
    repo.reset_unit_to_pending(unit_id)
    return {"ok": True}


@router.post("/steps/{step_id}/retry")
def retry_step(step_id: int, request: Request):
    repo = request.app.state.repo
    n = repo.reset_failed_units_to_pending(step_id)
    return {"reset": n}
```
`app.py` 加 `app.include_router(routes_jobs.router)`(import `from kb_platform.api import routes_jobs`)。

- [ ] **Step 4: 跑绿 + 全量**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/api tests/test_api_jobs.py
git commit -m "feat: fastapi job/status/retry endpoints"
```

---

### Task 8: 后端服务端到端 + `build_adapter_from_settings`

**Files:**
- Create: `kb_platform/graph/graphrag_adapter.py` 增 `build_adapter_from_settings`
- Test: `tests/test_e2e_backend_service.py`

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: E2E:API 建库+传文档+触发 → worker 跑(FakeGraphAdapter)→ GET SUCCEEDED + units 齐全;`build_adapter_from_settings`(解析 settings_json → ModelConfig → adapter)。

- [ ] **Step 1: 写 `build_adapter_from_settings`**

在 `kb_platform/graph/graphrag_adapter.py`:
```python
def build_adapter_from_settings(settings_json: str, data_root: str) -> "GraphRagAdapter":
    """Parse KB settings_json (graphrag settings subset) → ModelConfig → real adapter."""
    import json

    from graphrag_llm.config import ModelConfig

    settings = json.loads(settings_json or "{}")
    llm = settings.get("llm", {}) or settings.get("completion", {})
    model_config = ModelConfig(
        type=llm.get("type", "litellm"),
        model_provider=llm.get("model_provider", "openai"),
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
    )
    return build_default_adapter(data_root=data_root, model_config=model_config)
```

- [ ] **Step 2: 写 E2E 测试**

`tests/test_e2e_backend_service.py`:
```python
import pytest
from fastapi.testclient import TestClient

from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.api.app import create_app
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.worker import run_worker_once


@pytest.mark.asyncio
async def test_full_backend_service_with_fake_adapter(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    client = TestClient(create_app(repo, data_root=str(tmp_path)))

    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    client.post("/kbs/1/documents", json={"title": "d", "text": "ACME Org Bob Person Foo Bar Baz " * 200})
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "pending"

    # worker 领取并跑(注入 FakeGraphAdapter)
    await run_worker_once(repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), heartbeat_interval=0.01)

    assert client.get(f"/jobs/{job_id}").json()["status"] == "succeeded"
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    units = client.get(f"/steps/{extract['id']}/units").json()
    assert len(units) >= 1 and all(u["status"] == "succeeded" for u in units)
    # 四张 parquet 产出
    import os

    for name in ("entities", "relationships", "communities", "community_reports"):
        assert os.path.exists(f"{tmp_path}/{name}.parquet")
```

- [ ] **Step 3: 跑测试**

Run: `uv run pytest tests/test_e2e_backend_service.py -q`
Expected: 1 通过(整条后端服务闭环)。

- [ ] **Step 4: 全量回归 + ruff**

Run: `uv run pytest -q && uv run ruff check kb_platform tests`
Expected: 全套通过、ruff clean。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_e2e_backend_service.py
git commit -m "feat: build_adapter_from_settings + backend service e2e"
```

---

## Self-Review(写完后自查)

**1. Spec 覆盖(对照 spec §3–§8):**
- worker_id/heartbeat_at 列 → Task 1 ✓
- 真实 adapter 四方法 → Task 2(summarize/cluster/finalize)+ Task 3(report)✓
- worker 进程(轮询/领取/心跳/崩溃续跑)→ Task 4(心跳)+ Task 5(进程)✓
- REST API(KB/文档/任务/状态/重试)→ Task 6 + Task 7 ✓
- LLM 配置(settings_json→adapter)→ Task 8(`build_adapter_from_settings`)✓
- 文档接入(文件/文本)→ Task 6 ✓
- 测试(API 层 / worker 机制 / 真实 adapter 契约 / E2E / 2a 回归)→ Task 2/4/5/6/7/8 ✓
- 查询/前端/增量/鉴权 → 显式非目标 ✓

**2. 占位符扫描:** 无 TBD;Task 3 的 prompt 路径标注"以 grep 为准"(唯一允许探查 graphrag 的任务,同 Phase 1 Task 7)。Task 5 worker 的 `adapter_factory` 兼容写法给了统一约定建议。

**3. 类型一致性:** `GraphAdapter` Protocol 四方法签名跨任务一致;`create_job_pending`/`claim_one_pending_job`/`recover_*`/`set_unit_running(worker_id, heartbeat_at)`/`touch_unit_heartbeat`/`run_worker_once`/`create_app(repo, data_root)` 名字一致;`CommunityReport` 字段与 2a 一致;`build_default_adapter` 扩展不破坏 Phase 1/2a 调用。

**已识别范围说明:** `run_worker` 生产入口用 `asyncio.run` 逐轮 + `time.sleep` 轮询(简化);长驻后台 task / 多 worker / 优雅关停留部署文档,不在本计划(个人/小团队单 worker)。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-phase2b1-backend-service.md`. Two execution options:

**1. Subagent-Driven(推荐)** — 每任务派发独立 subagent + 两阶段评审(与前两期同款)。
**2. Inline Execution** — 当前会话批量执行 + 检查点。

Which approach?
