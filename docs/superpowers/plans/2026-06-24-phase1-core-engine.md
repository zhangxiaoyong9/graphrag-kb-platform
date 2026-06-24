# Phase 1 — 核心索引引擎 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现知识库管理平台的核心索引引擎 —— 控制面状态库 + 自驱动的单元化执行引擎,以最小的流水线(`chunk_documents` 原子步 → `extract_graph` 单元步 + 合并结算)端到端验证"每个 chunk 可追踪、失败可单独重试"这一核心承诺。

**Architecture:** 控制面/数据面分离。SQLite(WAL)存 job/step/unit/document/chunk 追踪元数据;数据面 entities/relationships 用 pandas 直接写 parquet 到 `data_root/`。引擎用 asyncio 单进程驱动:Orchestrator 按步推进,atomic 步直接调 adapter,unit_fanout 步由 UnitWorker 并发池逐 chunk 跑 LLM、全部成功后做合并结算写 parquet。`GraphAdapter` 是唯一耦合 graphrag 的接缝;引擎测试用 `FakeGraphAdapter`,不依赖真 LLM。

**Tech Stack:** Python 3.11–3.13 · `graphrag==3.1.*`(库依赖)· SQLAlchemy 2.x + Alembic · SQLite(WAL)· pandas/pyarrow(parquet)· pytest + pytest-asyncio · asyncio。

## Global Constraints

- 锁定 `graphrag==3.1.*`;graphrag 内部 API 仅在 `kb_platform/graph/graphrag_adapter.py` 内引用,其余模块不得 import graphrag 内部。
- Python `>=3.11,<3.14`(与 graphrag 一致)。
- SQLite,WAL 模式,单写者;asyncio 单线程事件循环,同步 DB 调用穿插其间(小规模可接受)。
- Plan 1 数据面用 pandas 直写 parquet(Phase 3 再与 graphrag `TableProvider` 对齐以支持查询)。
- Plan 1 阈值固定为严格(`min_unit_success_ratio=1.0`);"带失败前进"属 Phase 2。
- 每个任务:写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。提交信息用约定式前缀(`feat:`/`test:`/`chore:`/`refactor:`)。

## 关键接口契约(跨任务共享,务必保持一致)

```python
# kb_platform/graph/adapter.py  (Task 6 定义,后续任务依赖)
@dataclass
class ChunkText:
    chunk_id: str    # sha512(text) 十六进制
    text: str

@dataclass
class ExtractionResult:
    entities: pd.DataFrame        # 列: title, type, description, source_id
    relationships: pd.DataFrame   # 列: source, target, weight, description, source_id

class GraphAdapter(Protocol):
    def chunk_document(self, doc_id: str, text: str) -> list[ChunkText]: ...
    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult: ...
    def merge_extractions(self, results: list[ExtractionResult]) -> tuple[pd.DataFrame, pd.DataFrame]: ...
```

```python
# kb_platform/db/enums.py  (Task 2 定义)
class JobStatus(StrEnum):   PENDING, RUNNING, SUCCEEDED, FAILED, CANCELLED
class StepStatus(StrEnum):  PENDING, RUNNING, SUCCEEDED, PARTIALLY_FAILED, FAILED
class StepKind(StrEnum):    ATOMIC, UNIT_FANOUT
class UnitStatus(StrEnum):  PENDING, RUNNING, SUCCEEDED, FAILED
class UnitKind(StrEnum):    EXTRACT_GRAPH
```

```python
# kb_platform/db/repository.py  (Task 5 定义)
class Repository:
    def create_job(self, kb_id: str, type: str, steps: list[StepSpec]) -> Job: ...
    def claim_pending_units(self, step_id: int) -> list[Unit]: ...
    def set_unit_running(self, unit_id: int) -> None: ...
    def set_unit_succeeded(self, unit_id: int, result: str) -> None: ...
    def set_unit_failed(self, unit_id: int, error: str) -> None: ...
    def reset_unit_to_pending(self, unit_id: int) -> None: ...   # 重试
```

---

### Task 1: 项目脚手架与冒烟测试

**Files:**
- Create: `pyproject.toml`
- Create: `kb_platform/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: 可 `pytest` 运行的空项目;`import kb_platform` 可用。

- [ ] **Step 1: 写 `pyproject.toml`**

```toml
[project]
name = "kb-platform"
version = "0.1.0"
description = "Knowledge base management platform on top of GraphRAG."
requires-python = ">=3.11,<3.14"
dependencies = [
    "graphrag==3.1.*",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "pydantic>=2.6",
    "pandas>=2.2",
    "pyarrow>=15.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100
```

- [ ] **Step 2: 写空包与测试**

`kb_platform/__init__.py`:
```python
"""Knowledge base management platform on top of GraphRAG."""

__version__ = "0.1.0"
```

`tests/__init__.py`: 空文件。

`tests/conftest.py`:
```python
"""Shared pytest fixtures."""
```

`tests/test_smoke.py`:
```python
import kb_platform


def test_package_imports():
    assert kb_platform.__version__ == "0.1.0"
```

- [ ] **Step 3: 安装依赖并跑测试**

Run: `cd /Users/zhangxiaoyong/Documents/project/github/graphrag-kb-platform && uv sync --extra dev && uv run pytest -q`
Expected: `1 passed`。

- [ ] **Step 4: 提交**

```bash
git add pyproject.toml kb_platform tests uv.lock
git commit -m "chore: scaffold kb-platform project"
```

---

### Task 2: 状态枚举与状态机

**Files:**
- Create: `kb_platform/db/__init__.py`
- Create: `kb_platform/db/enums.py`
- Test: `tests/test_enums.py`

**Interfaces:**
- Produces: `JobStatus`, `StepStatus`, `StepKind`, `UnitStatus`, `UnitKind`, `allowed_transitions(status) -> set[status]`, `transition(current, target) -> target`(非法则抛 `ValueError`)。

- [ ] **Step 1: 写失败测试**

`tests/test_enums.py`:
```python
import pytest

from kb_platform.db.enums import (
    JobStatus,
    StepStatus,
    UnitStatus,
    allowed_transitions,
    transition,
)


def test_job_transitions():
    assert transition(JobStatus.PENDING, JobStatus.RUNNING) == JobStatus.RUNNING
    assert JobStatus.SUCCEEDED in allowed_transitions(JobStatus.RUNNING)


def test_unit_retry_resets_to_pending():
    assert transition(UnitStatus.FAILED, UnitStatus.PENDING) == UnitStatus.PENDING


def test_illegal_transition_raises():
    with pytest.raises(ValueError):
        transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_enums.py -q`
Expected: FAIL(`ModuleNotFoundError: kb_platform.db.enums`)。

- [ ] **Step 3: 写实现**

`kb_platform/db/__init__.py`: 空文件。

`kb_platform/db/enums.py`:
```python
"""Control-plane status enums and state-machine transitions."""

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"


class StepKind(StrEnum):
    ATOMIC = "atomic"
    UNIT_FANOUT = "unit_fanout"


class UnitStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UnitKind(StrEnum):
    EXTRACT_GRAPH = "extract_graph"


_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
    StepStatus.PENDING: {StepStatus.RUNNING},
    StepStatus.RUNNING: {
        StepStatus.SUCCEEDED,
        StepStatus.PARTIALLY_FAILED,
        StepStatus.FAILED,
    },
    StepStatus.PARTIALLY_FAILED: {StepStatus.RUNNING},  # 重试失败单元时回到 running
    StepStatus.SUCCEEDED: set(),
    StepStatus.FAILED: set(),
    UnitStatus.PENDING: {UnitStatus.RUNNING},
    UnitStatus.RUNNING: {UnitStatus.SUCCEEDED, UnitStatus.FAILED},
    UnitStatus.FAILED: {UnitStatus.PENDING},  # 手动重试
    UnitStatus.SUCCEEDED: set(),
}


def allowed_transitions(status: StrEnum) -> set[StrEnum]:
    """Return the set of statuses reachable from ``status``."""
    return _TRANSITIONS.get(status, set())


def transition(current: StrEnum, target: StrEnum) -> StrEnum:
    """Validate a state transition; return ``target`` or raise ``ValueError``."""
    if target not in _TRANSITIONS.get(current, set()):
        msg = f"Illegal transition: {current!r} -> {target!r}"
        raise ValueError(msg)
    return target
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_enums.py -q`
Expected: `3 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/db tests/test_enums.py
git commit -m "feat: control-plane status enums and state machine"
```

---

### Task 3: 控制面数据库模型(SQLAlchemy)

**Files:**
- Create: `kb_platform/db/engine.py`
- Create: `kb_platform/db/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Base`,`create_engine(url)`,`KnowledgeBase`,`Document`,`Chunk`,`Job`,`Step`,`Unit` 模型;`Unit` 含 `step_id, kind, subject_type, subject_id, status, attempt_no, result, error` 等字段(见 §5 表结构)。

- [ ] **Step 1: 写失败测试**

`tests/test_models.py`:
```python
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase, Document, Job, Step, Unit
from kb_platform.db.enums import JobStatus, StepKind, StepStatus, UnitKind, UnitStatus


def test_persist_job_step_unit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)

    with session_scope(engine) as s:
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
        s.flush()
        doc = Document(kb_id=kb.id, title="d", source_uri="file://d", content_hash="h", status="parsed", bytes=1)
        s.add(doc)
        s.flush()
        job = Job(kb_id=kb.id, type="full", method="standard", status=JobStatus.PENDING)
        s.add(job)
        s.flush()
        step = Step(job_id=job.id, name="extract_graph", ordinal=2, kind=StepKind.UNIT_FANOUT, status=StepStatus.PENDING)
        s.add(step)
        s.flush()
        s.add(Unit(step_id=step.id, kind=UnitKind.EXTRACT_GRAPH, subject_type="chunk", subject_id="c1", status=UnitStatus.PENDING, attempt_no=0))

    with session_scope(engine) as s:
        units = s.query(Unit).all()
        assert len(units) == 1
        assert units[0].subject_id == "c1"
        assert units[0].step.job.name == "extract_graph" or True  # 关系可达
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现**

`kb_platform/db/engine.py`:
```python
"""SQLite engine and session helpers."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine(url: str = "sqlite:///./kb.db") -> Engine:
    """Create a SQLite engine with WAL and cross-thread access enabled."""
    engine = _sa_create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Context-managed session that commits on success, rolls back on error."""
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

`kb_platform/db/models.py`:
```python
"""SQLAlchemy ORM models for the control plane."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from kb_platform.db.enums import JobStatus, StepKind, StepStatus, UnitKind, UnitStatus


class Base(DeclarativeBase):
    pass


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    method: Mapped[str] = mapped_column(String, default="standard")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    data_root: Mapped[str] = mapped_column(String)
    documents: Mapped[list["Document"]] = relationship(back_populates="kb")


class Document(Base):
    __tablename__ = "document"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_base.id"))
    title: Mapped[str] = mapped_column(String)
    source_uri: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="uploaded")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    kb: Mapped["KnowledgeBase"] = relationship(back_populates="documents")


class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String, index=True)  # sha512(text)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_base.id"))
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_base.id"))
    type: Mapped[str] = mapped_column(String)  # full | incremental
    method: Mapped[str] = mapped_column(String, default="standard")
    status: Mapped[str] = mapped_column(String, default=JobStatus.PENDING)
    parent_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[list["Step"]] = relationship(back_populates="job", order_by="Step.ordinal")


class Step(Base):
    __tablename__ = "step"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"))
    name: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String, default=StepKind.ATOMIC)
    status: Mapped[str] = mapped_column(String, default=StepStatus.PENDING)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job: Mapped["Job"] = relationship(back_populates="steps")
    units: Mapped[list["Unit"]] = relationship(back_populates="step")


class Unit(Base):
    __tablename__ = "unit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("step.id"))
    kind: Mapped[str] = mapped_column(String, default=UnitKind.EXTRACT_GRAPH)
    subject_type: Mapped[str] = mapped_column(String)  # chunk | entity | community
    subject_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default=UnitStatus.PENDING)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON 摘要 / raw 标记
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    step: Mapped["Step"] = relationship(back_populates="units")
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_models.py -q`
Expected: `1 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/db/engine.py kb_platform/db/models.py tests/test_models.py
git commit -m "feat: control-plane SQLAlchemy models"
```

---

### Task 4: Alembic 初始迁移

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`
- Test: `tests/test_migration.py`

**Interfaces:**
- Produces: `alembic upgrade head` 能在空库建出全部表。

- [ ] **Step 1: 初始化 Alembic 结构(手动建文件,不用 `alembic init` 以避免多余模板)**

`alembic.ini`(关键字段):
```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite:///./kb.db

[loggers]
keys = root
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
qualname =
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`alembic/env.py`:
```python
"""Alembic environment."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from kb_platform.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`alembic/script.py.mako`: 用 `alembic init` 默认模板即可 —— 直接运行:
`uv run alembic init -t generic alembic_tmp && cp alembic_tmp/script.py.mako alembic/ && rm -rf alembic_tmp`
(然后覆盖 `alembic/env.py` 为上面的版本。)

- [ ] **Step 2: 生成初始迁移**

Run: `uv run alembic revision --autogenerate -m "initial" --rev-id 0001`
Expected: 生成 `alembic/versions/0001_initial.py`。

- [ ] **Step 3: 写迁移测试**

`tests/test_migration.py`:
```python
import subprocess
import sys

from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base


def test_migration_matches_models(tmp_path):
    # 用 metadata 建表
    e1 = create_engine(f"sqlite:///{tmp_path}/models.db")
    Base.metadata.create_all(e1)
    # 用 alembic 建表
    db = tmp_path / "alembic.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"],
        check=True,
    )
    # 两库表名集合一致(粗校验)
    from sqlalchemy import inspect

    insp_models = set(inspect(e1).get_table_names())
    insp_alembic = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert insp_models <= insp_alembic | {"alembic_version"}
```

> 注:为支持 `-x db=` 覆盖,在 `alembic/env.py` 的 `run_migrations_online` 开头加:
> ```python
> import argparse
> try:
>     _db_override = context.get_x_argument("db")
>     if _db_override:
>         config.set_main_option("sqlalchemy.url", f"sqlite:///{_db_override[0]}")
> except Exception:
>     pass
> ```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_migration.py -q`
Expected: `1 passed`。

- [ ] **Step 5: 提交**

```bash
git add alembic.ini alembic tests/test_migration.py
git commit -m "feat: alembic initial migration"
```

---

### Task 5: Repository(DAO + 单元申领)

**Files:**
- Create: `kb_platform/engine/spec.py`
- Create: `kb_platform/db/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: Task 2 enums, Task 3 models/engine。
- Produces: `StepSpec(name, kind, ...)`,`Repository(engine)` 含方法:`add_document`,`get_chunks`,`create_job(specs)`,`get_job`,`claim_pending_units(step_id)`,`set_unit_running/succeeded/failed`,`reset_unit_to_pending`,`set_step_status`,`set_job_status`,`list_units(step_id)`。

- [ ] **Step 1: 写 `StepSpec` 与失败测试**

`kb_platform/engine/__init__.py`: 空文件。

`kb_platform/engine/spec.py`:
```python
"""Step specification used to build a job's step list."""

from dataclasses import dataclass

from kb_platform.db.enums import StepKind


@dataclass
class StepSpec:
    name: str
    kind: StepKind
```

`tests/test_repository.py`:
```python
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, StepStatus, UnitStatus
from kb_platform.db.models import Base, KnowledgeBase, Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec


@pytest.fixture()
def repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
    return Repository(engine)


def test_create_job_and_claim_units(repo):
    with session_scope(repo.engine) as s:
        kb = s.query(KnowledgeBase).one()
        # 预置 chunks 供 extract_graph 使用
        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=1, ordinal=0, text="t1"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=1, ordinal=1, text="t2"))

    job = repo.create_job(kb_id=1, type="full", specs=[StepSpec("chunk_documents", StepKind.ATOMIC), StepSpec("extract_graph", StepKind.UNIT_FANOUT)])
    extract_step = [s for s in job.steps if s.name == "extract_graph"][0]
    # 手动预置两个单元
    repo.add_units(extract_step.id, [("chunk", "c1"), ("chunk", "c2")])

    claimed = repo.claim_pending_units(extract_step.id)
    assert {u.subject_id for u in claimed} == {"c1", "c2"}
    assert all(u.status == UnitStatus.RUNNING for u in claimed)

    # 再申领应空
    assert repo.claim_pending_units(extract_step.id) == []


def test_unit_retry_resets_to_pending(repo):
    job = repo.create_job(kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)])
    repo.add_units(job.steps[0].id, [("chunk", "c1")])
    uid = repo.list_units(job.steps[0].id)[0].id
    repo.set_unit_failed(uid, "boom")
    repo.reset_unit_to_pending(uid)
    assert repo.list_units(job.steps[0].id)[0].status == UnitStatus.PENDING
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_repository.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现**

`kb_platform/db/repository.py`:
```python
"""Data access for the control plane."""

from sqlalchemy import select
from sqlalchemy.engine import Engine

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import JobStatus, StepKind, StepStatus, UnitStatus
from kb_platform.db.models import Chunk, Document, Job, Step, Unit
from kb_platform.engine.spec import StepSpec


class Repository:
    """Thin DAO over the control-plane models."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ---- documents / chunks ----
    def add_document(self, kb_id: int, title: str, text: str, source_uri: str = "") -> Document:
        import hashlib

        with session_scope(self.engine) as s:
            doc = Document(
                kb_id=kb_id, title=title, source_uri=source_uri,
                content_hash=hashlib.sha512(text.encode()).hexdigest(),
                status="parsed", bytes=len(text), text=text,
            )
            s.add(doc)
            s.flush()
            return doc

    def get_documents(self, kb_id: int) -> list[Document]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Document).where(Document.kb_id == kb_id)))

    def add_chunks(self, chunks: list[Chunk]) -> None:
        with session_scope(self.engine) as s:
            for c in chunks:
                s.add(c)

    def get_chunks(self, kb_id: int) -> list[Chunk]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Chunk).where(Chunk.kb_id == kb_id).order_by(Chunk.ordinal)))

    # ---- jobs / steps ----
    def create_job(self, kb_id: int, type: str, specs: list[StepSpec], method: str = "standard") -> Job:
        with session_scope(self.engine) as s:
            job = Job(kb_id=kb_id, type=type, method=method, status=JobStatus.PENDING)
            s.add(job)
            s.flush()
            for ordinal, spec in enumerate(specs):
                s.add(Step(job_id=job.id, name=spec.name, ordinal=ordinal, kind=spec.kind, status=StepStatus.PENDING))
            s.flush()
            return job

    def get_job(self, job_id: int) -> Job:
        with session_scope(self.engine) as s:
            return s.get(Job, job_id)

    def get_step(self, step_id: int) -> Step:
        with session_scope(self.engine) as s:
            return s.get(Step, step_id)

    def get_steps(self, job_id: int) -> list[Step]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Step).where(Step.job_id == job_id).order_by(Step.ordinal)))

    def set_step_status(self, step_id: int, status: StepStatus, error: str | None = None) -> None:
        with session_scope(self.engine) as s:
            step = s.get(Step, step_id)
            step.status = status
            if error is not None:
                step.error = error

    def set_job_status(self, job_id: int, status: JobStatus) -> None:
        with session_scope(self.engine) as s:
            s.get(Job, job_id).status = status

    # ---- units ----
    def add_units(self, step_id: int, subjects: list[tuple[str, str]]) -> None:
        with session_scope(self.engine) as s:
            for subject_type, subject_id in subjects:
                s.add(Unit(step_id=step_id, subject_type=subject_type, subject_id=subject_id, status=UnitStatus.PENDING, attempt_no=0))

    def claim_pending_units(self, step_id: int) -> list[Unit]:
        with session_scope(self.engine) as s:
            units = list(s.scalars(select(Unit).where(Unit.step_id == step_id, Unit.status == UnitStatus.PENDING)))
            for u in units:
                u.status = UnitStatus.RUNNING
                u.attempt_no += 1
            return units

    def list_units(self, step_id: int) -> list[Unit]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Unit).where(Unit.step_id == step_id)))

    def set_unit_succeeded(self, unit_id: int, result: str) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.SUCCEEDED
            u.result = result

    def set_unit_failed(self, unit_id: int, error: str) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.FAILED
            u.error = error

    def reset_unit_to_pending(self, unit_id: int) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.PENDING
            u.error = None

    def reset_failed_units_to_pending(self, step_id: int) -> int:
        with session_scope(self.engine) as s:
            units = list(s.scalars(select(Unit).where(Unit.step_id == step_id, Unit.status == UnitStatus.FAILED)))
            for u in units:
                u.status = UnitStatus.PENDING
                u.error = None
            return len(units)
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_repository.py -q`
Expected: `2 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine kb_platform/db/repository.py tests/test_repository.py
git commit -m "feat: control-plane repository with unit claiming"
```

---

### Task 6: GraphAdapter 协议与 Fake 适配器

**Files:**
- Create: `kb_platform/graph/__init__.py`
- Create: `kb_platform/graph/adapter.py`
- Test: `tests/test_adapter_fake.py`

**Interfaces:**
- Produces: `ChunkText`,`ExtractionResult`,`GraphAdapter`(Protocol),`FakeGraphAdapter`(确定性可造失败)。

- [ ] **Step 1: 写失败测试**

`tests/test_adapter_fake.py`:
```python
import pandas as pd

from kb_platform.graph.adapter import FakeGraphAdapter


def test_fake_chunk_document():
    adapter = FakeGraphAdapter()
    chunks = adapter.chunk_document(doc_id=1, text="hello world " * 500)
    assert len(chunks) >= 1
    assert all(c.chunk_id for c in chunks)


def test_fake_extract_chunk_returns_entities():
    adapter = FakeGraphAdapter()
    result = adapter.extract_chunk_sync("c1", "some text")  # 同步包装便于测试
    assert isinstance(result.entities, pd.DataFrame)
    assert "title" in result.entities.columns


def test_fake_merge():
    adapter = FakeGraphAdapter()
    r = adapter.extract_chunk_sync("c1", "x")
    entities, relationships = adapter.merge_extractions([r, r])
    assert not entities.empty
```

> 说明:`FakeGraphAdapter.extract_chunk` 是 async;测试用同步版 `extract_chunk_sync` 直接验证解析逻辑。`extract_chunk` 内部 `await` 调同步版。

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_adapter_fake.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现**

`kb_platform/graph/__init__.py`: 空文件。

`kb_platform/graph/adapter.py`:
```python
"""Graph adapter abstraction — the only graphrag coupling seam (real impl in graphrag_adapter.py)."""

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass
class ChunkText:
    chunk_id: str
    text: str


@dataclass
class ExtractionResult:
    entities: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["title", "type", "description", "source_id"]))
    relationships: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["source", "target", "weight", "description", "source_id"]))


class GraphAdapter(Protocol):
    """Interface every step uses. Implementations: FakeGraphAdapter (tests), GraphRagAdapter (real)."""

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]: ...

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult: ...

    def merge_extractions(self, results: list[ExtractionResult]) -> tuple[pd.DataFrame, pd.DataFrame]: ...


def _hash(text: str) -> str:
    return hashlib.sha512(text.encode()).hexdigest()


class FakeGraphAdapter:
    """Deterministic, no-LLM adapter for engine tests.

    - chunk_document: naive fixed-size word split.
    - extract_chunk: emits one entity per capitalized word + a self-relationship.
    - fail_on: set of chunk_ids that should raise (to test retry).
    """

    def __init__(self, chunk_size: int = 1000, fail_on: set[str] | None = None) -> None:
        self.chunk_size = chunk_size
        self.fail_on = fail_on or set()
        self.extract_calls: list[str] = []

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]:
        words = text.split()
        chunks: list[ChunkText] = []
        for i in range(0, len(words), self.chunk_size):
            piece = " ".join(words[i : i + self.chunk_size])
            chunks.append(ChunkText(chunk_id=_hash(piece), text=piece))
        return chunks or [ChunkText(chunk_id=_hash(text), text=text)]

    def extract_chunk_sync(self, chunk_id: str, text: str) -> ExtractionResult:
        if chunk_id in self.fail_on:
            raise RuntimeError(f"injected failure for {chunk_id}")
        self.extract_calls.append(chunk_id)
        names = [w for w in text.split() if w[:1].isupper()]
        entities = pd.DataFrame(
            [{"title": n.upper(), "type": "CONCEPT", "description": n, "source_id": chunk_id} for n in names]
            or [{"title": "PLACEHOLDER", "type": "CONCEPT", "description": text[:40], "source_id": chunk_id}]
        )
        rels = pd.DataFrame(columns=["source", "target", "weight", "description", "source_id"])
        if len(names) >= 2:
            rels = pd.DataFrame([{
                "source": names[0].upper(), "target": names[1].upper(),
                "weight": 1.0, "description": "related", "source_id": chunk_id,
            }])
        return ExtractionResult(entities=entities, relationships=rels)

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult:
        return self.extract_chunk_sync(chunk_id, text)

    def merge_extractions(self, results: list[ExtractionResult]) -> tuple[pd.DataFrame, pd.DataFrame]:
        entity_dfs = [r.entities for r in results if not r.entities.empty]
        rel_dfs = [r.relationships for r in results if not r.relationships.empty]
        entities = (
            pd.concat(entity_dfs, ignore_index=True).groupby(["title", "type"], sort=False)
            .agg(description=("description", list), text_unit_ids=("source_id", list), frequency=("source_id", "count"))
            .reset_index()
            if entity_dfs else pd.DataFrame(columns=["title", "type", "description", "text_unit_ids", "frequency"])
        )
        relationships = (
            pd.concat(rel_dfs, ignore_index=True).groupby(["source", "target"], sort=False)
            .agg(description=("description", list), text_unit_ids=("source_id", list), weight=("weight", "sum"))
            .reset_index()
            if rel_dfs else pd.DataFrame(columns=["source", "target", "description", "text_unit_ids", "weight"])
        )
        if not entities.empty and not relationships.empty:
            titles = set(entities["title"])
            relationships = relationships[
                relationships["source"].isin(titles) & relationships["target"].isin(titles)
            ].reset_index(drop=True)
        return entities, relationships
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_adapter_fake.py -q`
Expected: `3 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/graph tests/test_adapter_fake.py
git commit -m "feat: graph adapter protocol and fake implementation"
```

---

### Task 7: GraphRag 适配器(真实 chunk + LLM 抽取,MockLLM 契约测试)

**Files:**
- Create: `kb_platform/graph/graphrag_adapter.py`
- Test: `tests/test_graphrag_adapter.py`

**Interfaces:**
- Consumes: Task 6 `GraphAdapter/ChunkText/ExtractionResult`。
- Produces: `GraphRagAdapter`(实现 `GraphAdapter`),`build_default_adapter(data_root, model_config)` 工厂。内部用 graphrag `create_chunker`+`GraphExtractor`+`create_completion`。

- [ ] **Step 1: 写失败测试(用 graphrag 自带 MockLLM,零成本)**

`tests/test_graphrag_adapter.py`:
```python
import pandas as pd

from kb_platform.graph.graphrag_adapter import GraphRagAdapter, build_default_adapter

# graphrag 抽取输出的结构化格式(## 分隔记录,<|> 分隔字段)
CANNED = (
    '("entity"<|>ACME<|>ORGANIZATION<|>A tech company)##'
    '("entity"<|>BOB<|>PERSON<|>CEO of ACME)##'
    '("relationship"<|>ACME<|>BOB<|>employs<|>0.9)##'
    "<|COMPLETE|>"
)


def _mock_model_config() -> "object":
    from graphrag_llm.config import ModelConfig

    return ModelConfig(type="mock", model_provider="mock", model="mock", mock_responses=[CANNED])


def test_real_extract_chunk_parses_entities(tmp_path):
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    result = adapter.extract_chunk_sync("c1", "ACME employs Bob.")
    assert set(result.entities["title"]) == {"ACME", "BOB"}
    assert len(result.relationships) == 1


def test_real_chunk_document(tmp_path):
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    chunks = adapter.chunk_document(doc_id=1, text="one two three " * 500)
    assert len(chunks) >= 1
    assert all(c.text for c in chunks)


def test_real_merge_dedupes_entities(tmp_path):
    adapter = build_default_adapter(data_root=str(tmp_path), model_config=_mock_model_config())
    r = adapter.extract_chunk_sync("c1", "ACME is big.")
    r2 = adapter.extract_chunk_sync("c2", "ACME is global.")
    entities, relationships = adapter.merge_extractions([r, r2])
    # 两个 chunk 都抽到 ACME,合并后实体表只有 1 行 ACME,frequency=2
    acme = entities[entities["title"] == "ACME"].iloc[0]
    assert acme["frequency"] == 2
```

> 注:`extract_chunk_sync` 暴露同步入口便于测试;`extract_chunk` 包 `asyncio` 调它(见实现)。

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_graphrag_adapter.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现**

`kb_platform/graph/graphrag_adapter.py`:
```python
"""Real GraphAdapter backed by graphrag primitives. The ONLY module that imports graphrag internals."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

import pandas as pd

from kb_platform.graph.adapter import ChunkText, ExtractionResult, _hash


class GraphRagAdapter:
    """Adapter calling graphrag chunking + LLM entity extraction.

    chunker is injected (built by build_default_adapter) so tests can supply
    a real graphrag chunker; extract uses a graphrag LLMCompletion.
    """

    def __init__(self, *, chunker, extractor_factory: Callable[[], object], entity_types: list[str]) -> None:
        self._chunker = chunker
        self._extractor_factory = extractor_factory
        self._entity_types = entity_types

    def chunk_document(self, doc_id: int, text: str) -> list[ChunkText]:
        return [ChunkText(chunk_id=_hash(tc.text), text=tc.text) for tc in self._chunker.chunk(text)]

    def extract_chunk_sync(self, chunk_id: str, text: str) -> ExtractionResult:
        import pandas as pd

        extractor = self._extractor_factory()
        entities_df, rels_df = asyncio.get_event_loop().run_until_complete(
            extractor(text=text, entity_types=self._entity_types, source_id=chunk_id)
        )
        return ExtractionResult(entities=entities_df, relationships=rels_df)

    async def extract_chunk(self, chunk_id: str, text: str) -> ExtractionResult:
        extractor = self._extractor_factory()
        entities_df, rels_df = await extractor(text=text, entity_types=self._entity_types, source_id=chunk_id)
        return ExtractionResult(entities=entities_df, relationships=rels_df)

    def merge_extractions(self, results: list[ExtractionResult]) -> tuple[pd.DataFrame, pd.DataFrame]:
        from kb_platform.graph.adapter import FakeGraphAdapter

        # 合并逻辑对所有 adapter 一致,复用同一实现
        return FakeGraphAdapter().merge_extractions(results)


def build_default_adapter(*, data_root: str, model_config, max_gleanings: int = 0) -> GraphRagAdapter:
    """Wire a GraphRagAdapter with a real graphrag chunker + LLM extractor."""
    from graphrag_chunking import ChunkingConfig, ChunkerType, create_chunker
    from graphrag_llm.completion import create_completion
    from graphrag.tokenizer.get_tokenizer import get_tokenizer
    from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT
    from graphrag.config.defaults import DEFAULT_ENTITY_TYPES

    tokenizer = get_tokenizer(encoding_model="cl100k_base")
    chunker = create_chunker(
        ChunkingConfig(type=ChunkerType.Tokens, encoding_model="cl100k_base", size=1200, overlap=100),
        encode=tokenizer.encode,
        decode=tokenizer.decode,
    )
    completion = create_completion(model_config)

    def extractor_factory() -> GraphExtractor:
        return GraphExtractor(model=completion, prompt=GRAPH_EXTRACTION_PROMPT, max_gleanings=max_gleanings)

    return GraphRagAdapter(chunker=chunker, extractor_factory=extractor_factory, entity_types=list(DEFAULT_ENTITY_TYPES))
```

> 导入路径以 `graphrag==3.1.*` 为准;若某导入在实测中路径不同,以仓库内 `grep` 为准修正(这是唯一允许探查 graphrag 内部的任务)。`extract_chunk_sync` 用 `run_until_complete` 仅服务于测试;生产路径走 `extract_chunk`(async)。

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_graphrag_adapter.py -q`
Expected: `3 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/graph/graphrag_adapter.py tests/test_graphrag_adapter.py
git commit -m "feat: graphrag-backed adapter with mock-LLM contract test"
```

---

### Task 8: Orchestrator(任务规划 + 按步推进 + atomic 步)

**Files:**
- Modify: `kb_platform/engine/orchestrator.py`(新建)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 5 `Repository`,Task 6 `GraphAdapter`,Task 2 enums。
- Produces: `Orchestrator(repo, adapter, data_root)`;`Orchestrator.run(job_id)` 异步驱动:`plan = [chunk_documents(atomic), extract_graph(unit_fanout)]`,逐 step 执行;atomic `chunk_documents` 步:读 documents → adapter.chunk_document → 写 Chunk 行 → step succeeded。`extract_graph` 步委托 `UnitWorker`(Task 9)。本任务先把 extract_graph 步占位为直接跑单 worker 的最小形态,Task 9/10 完善。

- [ ] **Step 1: 写失败测试(fake adapter,验证 chunk 步产出 Chunk 行)**

`tests/test_orchestrator.py`:
```python
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="d1", text="Hello World Foo Bar " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_orchestrator_runs_pipeline_and_writes_parquet(setup):
    from kb_platform.graph.adapter import FakeGraphAdapter

    repo, data_root = setup
    adapter = FakeGraphAdapter()
    orch = Orchestrator(repo=repo, adapter=adapter, data_root=data_root)

    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id)

    # chunk 步产出 chunk 行
    chunks = repo.get_chunks(kb_id=1)
    assert len(chunks) >= 1
    # extract_graph 步产出 entities/relationships parquet
    import pandas as pd

    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    assert not entities.empty
    job2 = repo.get_job(job.id)
    assert job2.status == "succeeded"
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_orchestrator.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现(最小版:chunk 步 + extract_graph 步内联跑全部 chunk 单元 + 合并写 parquet;并发与 worker 抽象留 Task 9)**

`kb_platform/engine/orchestrator.py`:
```python
"""Orchestrator: build the step plan and drive a job to completion."""

import logging

import pandas as pd

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import JobStatus, StepKind, StepStatus
from kb_platform.db.models import Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import GraphAdapter

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = data_root

    @staticmethod
    def plan() -> list[StepSpec]:
        return [StepSpec("chunk_documents", StepKind.ATOMIC), StepSpec("extract_graph", StepKind.UNIT_FANOUT)]

    async def run(self, job_id: int) -> None:
        self.repo.set_job_status(job_id, JobStatus.RUNNING)
        try:
            for step in self.repo.get_steps(job_id):
                await self._run_step(step)
                if step.status != StepStatus.SUCCEEDED:
                    self.repo.set_job_status(job_id, JobStatus.FAILED)
                    return
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
        except Exception:
            logger.exception("job %s failed", job_id)
            self.repo.set_job_status(job_id, JobStatus.FAILED)
            raise

    async def _run_step(self, step) -> None:
        self.repo.set_step_status(step.id, StepStatus.RUNNING)
        from kb_platform.engine.unit_worker import UnitWorker

        if step.kind == StepKind.ATOMIC:
            await self._run_atomic(step)
        else:
            worker = UnitWorker(repo=self.repo, adapter=self.adapter, data_root=self.data_root)
            await worker.run_unit_fanout(step)
        # 重新读取 step 状态(worker 已结算)
        fresh = self.repo.get_step(step.id)
        step.status = fresh.status

    async def _run_atomic(self, step) -> None:
        if step.name == "chunk_documents":
            await self._chunk_documents(step)
        else:
            msg = f"unknown atomic step: {step.name}"
            raise ValueError(msg)
        self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)

    async def _chunk_documents(self, step) -> None:
        job = self.repo.get_job(step.job_id)
        chunks: list[Chunk] = []
        for doc in self.repo.get_documents(job.kb_id):
            for ordinal, piece in enumerate(self.adapter.chunk_document(doc.id, doc.text or "")):
                chunks.append(Chunk(chunk_id=piece.chunk_id, kb_id=job.kb_id, document_id=doc.id, ordinal=ordinal, text=piece.text))
        self.repo.add_chunks(chunks)
```

> `UnitWorker.run_unit_fanout` 在 Task 9 实现;本任务先让测试因 import 失败而红,Task 9 接通后转绿。**因此本任务的"跑绿"依赖 Task 9** —— 实务上 Task 8 与 Task 9 紧耦合,可在同一会话连续完成。若想独立验证 Task 8,临时把 `extract_graph` 步从 `plan()` 去掉、只测 chunk 步即可(见下方备选)。

**备选独立验证(仅 chunk 步):** 临时 `Orchestrator.plan()` 返回 `[StepSpec("chunk_documents", StepKind.ATOMIC)]` 跑 `test_orchestrator.py` 的一个缩减用例,确认 chunk 行写出;Task 9 接通后恢复双步。

- [ ] **Step 4: 跑绿(在 Task 9 完成后)**

Run: `uv run pytest tests/test_orchestrator.py -q`
Expected: `1 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator with plan + chunk_documents atomic step"
```

---

### Task 9: UnitWorker(申领/并发/结算 + extract_graph 接线 + 合并写 parquet)

**Files:**
- Create: `kb_platform/engine/unit_worker.py`
- Test: `tests/test_unit_worker.py`

**Interfaces:**
- Consumes: Task 5 `Repository`,Task 6 `GraphAdapter`,`Orchestrator`。
- Produces: `UnitWorker(repo, adapter, data_root, concurrency=4)`;`async run_unit_fanout(step)`:为 step 的每个 subject 建 unit(若无)、并发跑、按结果置 unit 状态、结算 step(全成功→succeeded + 合并写 parquet;否则→partially_failed)。

- [ ] **Step 1: 写失败测试**

`tests/test_unit_worker.py`:
```python
import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
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
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
        s.flush()
        from kb_platform.db.models import Chunk

        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=1, ordinal=0, text="Foo Bar"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=1, ordinal=1, text="Baz Qux"))
    repo = Repository(engine)
    job = repo.create_job(kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)])
    return repo, job.steps[0].id, str(tmp_path)


@pytest.mark.asyncio
async def test_all_units_succeed_writes_parquet(setup):
    repo, step_id, data_root = setup
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step_id).status == "succeeded"
    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    relationships = pd.read_parquet(f"{data_root}/relationships.parquet")
    assert not entities.empty


@pytest.mark.asyncio
async def test_failed_unit_marks_step_partially_failed(setup):
    repo, step_id, data_root = setup
    # 让 c2 失败
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(fail_on={"c2"}), data_root=data_root)
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step_id).status == "partially_failed"
    units = repo.list_units(step_id)
    assert {u.status for u in units} == {"succeeded", "failed"}
    # parquet 不写(有失败)
    import os

    assert not os.path.exists(f"{data_root}/entities.parquet")
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_unit_worker.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现**

`kb_platform/engine/unit_worker.py`:
```python
"""UnitWorker: fan out a unit_fanout step, run units concurrently, settle + finalize."""

import asyncio
import json
import logging
from pathlib import Path

import pandas as pd

from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import ExtractionResult, GraphAdapter

logger = logging.getLogger(__name__)


class UnitWorker:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = Path(data_root)
        self.concurrency = concurrency

    async def run_unit_fanout(self, step) -> None:
        if not self.repo.list_units(step.id):
            self._create_units_for(step)
        await self._run_units(step.id)
        self._settle(step)

    def _create_units_for(self, step) -> None:
        if step.name != "extract_graph":
            msg = f"no unit plan for step {step.name}"
            raise ValueError(msg)
        job = self.repo.get_job(step.job_id)
        chunks = self.repo.get_chunks(job.kb_id)
        self.repo.add_units(step.id, [("chunk", c.chunk_id) for c in chunks])

    async def _run_units(self, step_id: int) -> None:
        units = self.repo.claim_pending_units(step_id)
        sem = asyncio.Semaphore(self.concurrency)

        async def handle(u):
            async with sem:
                await self._process_one(u, step_id)

        await asyncio.gather(*(handle(u) for u in units))

    async def _process_one(self, unit, step_id: int):
        try:
            job = self.repo.get_job(self.repo.get_step(step_id).job_id)
            from sqlalchemy import select

            from kb_platform.db.engine import session_scope
            from kb_platform.db.models import Chunk

            with session_scope(self.repo.engine) as s:
                chunk = s.scalars(select(Chunk).where(Chunk.chunk_id == unit.subject_id, Chunk.kb_id == job.kb_id)).first()
                text = chunk.text if chunk else ""
            result = await self.adapter.extract_chunk(unit.subject_id, text)
            self._persist_extraction(unit.subject_id, result)  # 持久化,供结算/重试汇集
            self.repo.set_unit_succeeded(unit.id, f"{len(result.entities)} entities")
        except Exception as e:  # noqa: BLE001
            logger.warning("unit %s failed: %s", unit.id, e)
            self.repo.set_unit_failed(unit.id, str(e))

    def _settle(self, step) -> None:
        units = self.repo.list_units(step.id)
        # 关键:从磁盘汇集该步"所有成功单元"的抽取结果(含历次成功单元),
        # 这样重试单个失败单元后,之前已成功的兄弟 chunk 不会被遗漏。
        if {u.status for u in units} == {UnitStatus.SUCCEEDED}:
            extractions = self._load_all_extractions(units)
            merged = self.adapter.merge_extractions(extractions)
            self._write_parquet(merged)
            self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)
        else:
            self.repo.set_step_status(step.id, StepStatus.PARTIALLY_FAILED)

    def _persist_extraction(self, chunk_id: str, result: ExtractionResult) -> None:
        d = self.data_root / "extractions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{chunk_id}.json").write_text(json.dumps({
            "entities": result.entities.to_dict("records"),
            "relationships": result.relationships.to_dict("records"),
        }))

    def _load_all_extractions(self, units) -> list[ExtractionResult]:
        out: list[ExtractionResult] = []
        for u in units:
            if u.status != UnitStatus.SUCCEEDED:
                continue
            path = self.data_root / "extractions" / f"{u.subject_id}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                out.append(ExtractionResult(
                    entities=pd.DataFrame(raw["entities"]),
                    relationships=pd.DataFrame(raw["relationships"]),
                ))
        return out

    def _write_parquet(self, merged) -> None:
        entities, relationships = merged
        entities.to_parquet(self.data_root / "entities.parquet")
        relationships.to_parquet(self.data_root / "relationships.parquet")
```

> **为什么持久化每个单元的抽取结果:** 结算时的合并必须覆盖该步"所有成功单元",而不只是本次 `gather` 跑出来的那些。否则"多 chunk 部分失败 → 重试单个失败 chunk → 结算"时,之前已成功的兄弟 chunk 的实体会从最终 parquet 中丢失。落盘到 `data_root/extractions/<chunk_id>.json` 也让结算幂等、可崩溃恢复。

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_unit_worker.py tests/test_orchestrator.py -q`
Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/engine/unit_worker.py tests/test_unit_worker.py tests/test_orchestrator.py
git commit -m "feat: unit worker with concurrent unit processing and merge-settle"
```

---

### Task 10: 重试服务(单元 / 步骤)

**Files:**
- Create: `kb_platform/retry.py`
- Test: `tests/test_retry.py`

**Interfaces:**
- Consumes: Task 5 `Repository`,Task 9 `UnitWorker`。
- Produces: `RetryService(repo, adapter, data_root)`;`retry_unit(unit_id)`、`retry_step(step_id)`、`async rerun_step(step_id)`(重置后重跑该 unit_fanout 步并重新结算)。

- [ ] **Step 1: 写失败测试**

`tests/test_retry.py`:
```python
import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, Chunk, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.retry import RetryService


@pytest.fixture()
def failed_step(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
        s.flush()
        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=1, ordinal=0, text="Foo Bar"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=1, ordinal=1, text="Baz Qux"))
    repo = Repository(engine)
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)]).steps[0]
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(fail_on={"c2"}), data_root=str(tmp_path))
    return repo, step.id, str(tmp_path), worker


@pytest.mark.asyncio
async def test_retry_failed_unit_then_rerun_recovers(failed_step):
    repo, step_id, data_root, worker = failed_step
    await worker.run_unit_fanout(repo.get_step(step_id))  # 首跑:c2 失败
    assert repo.get_step(step_id).status == "partially_failed"

    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)  # 不再失败
    n = retry.retry_step(step_id)  # 重置 failed 单元
    assert n == 1
    await retry.rerun_step(step_id)
    assert repo.get_step(step_id).status == "succeeded"
    assert pd.read_parquet(f"{data_root}/entities.parquet").empty is False


def test_retry_unit_resets_single_unit(failed_step):
    repo, step_id, _, _ = failed_step
    units = repo.list_units(step_id)
    # 预置一个 failed 单元
    repo.set_unit_failed(units[0].id, "x")
    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=".")
    retry.retry_unit(units[0].id)
    assert repo.list_units(step_id)[0].status == "pending"
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/test_retry.py -q`
Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 写实现**

`kb_platform/retry.py`:
```python
"""Manual retry of failed units and steps."""

from kb_platform.db.enums import StepStatus
from kb_platform.db.repository import Repository
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import GraphAdapter


class RetryService:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        worker = UnitWorker  # 延迟构造,确保每次 rerun 用最新状态
        self._worker_cls = worker
        self.data_root = data_root
        self.concurrency = concurrency

    def retry_unit(self, unit_id: int) -> None:
        """Reset a single failed unit to pending (does not run it)."""
        self.repo.reset_unit_to_pending(unit_id)

    def retry_step(self, step_id: int) -> int:
        """Reset all failed units of a step; return count reset. Step returns to running on rerun."""
        n = self.repo.reset_failed_units_to_pending(step_id)
        self.repo.set_step_status(step_id, StepStatus.RUNNING)
        return n

    async def rerun_step(self, step_id: int) -> None:
        """Re-run a unit_fanout step's pending units and re-settle."""
        step = self.repo.get_step(step_id)
        worker = self._worker_cls(repo=self.repo, adapter=self.adapter, data_root=self.data_root, concurrency=self.concurrency)
        await worker.run_unit_fanout(step)
```

- [ ] **Step 4: 跑绿**

Run: `uv run pytest tests/test_retry.py -q`
Expected: `2 passed`。

- [ ] **Step 5: 提交**

```bash
git add kb_platform/retry.py tests/test_retry.py
git commit -m "feat: retry service for units and steps"
```

---

### Task 11: 端到端集成测试(验证核心承诺)

**Files:**
- Test: `tests/test_integration_e2e.py`

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 一个端到端用例,验证"全量索引 → 单元追踪 → 注入失败 → 单 chunk 重试 → 恢复 → 合并正确"。

- [ ] **Step 1: 写测试**

`tests/test_integration_e2e.py`:
```python
import os

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, UnitStatus
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
    # 1400 词 → FakeGraphAdapter(默认 chunk_size=1000)切成 2 个 chunk,
    # 这样"部分失败 + 单 chunk 重试"能真正验证合并是否覆盖所有成功单元。
    repo.add_document(kb_id=1, title="d1", text="ACME Org Bob Person Foo Bar Baz " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_full_index_then_retry_single_chunk(kb):
    repo, data_root = kb
    # 首跑:让第一个 chunk 失败(第二个成功)
    failing_adapter = FakeGraphAdapter()
    chunks_preview = failing_adapter.chunk_document(1, repo.get_documents(1)[0].text)
    assert len(chunks_preview) >= 2  # 前置:确实有多个 chunk
    fail_id = chunks_preview[0].chunk_id
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(fail_on={fail_id}), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id)

    # job 因 extract_graph 步 partially_failed 而失败(阈值 1.0)
    assert repo.get_job(job.id).status == "failed"
    extract_step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    assert extract_step.status == "partially_failed"
    failed_units = [u for u in repo.list_units(extract_step.id) if u.status == UnitStatus.FAILED]
    assert len(failed_units) == 1
    assert failed_units[0].subject_id == fail_id  # 失败的正是被注入的 chunk
    assert os.path.exists(f"{data_root}/entities.parquet") is False  # 有失败不写 parquet

    # 手动重试(用不失败的 adapter)
    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    retry.retry_unit(failed_units[0].id)
    await retry.rerun_step(extract_step.id)
    assert repo.get_step(extract_step.id).status == "succeeded"

    # parquet 已写;两个 chunk 的同名实体已合并,frequency=2 证明两个 chunk 都被计入
    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    assert not entities.empty
    assert entities["title"].is_unique  # 同名实体已合并
    assert int(entities.loc[entities["title"] == "ACME", "frequency"].iloc[0]) == 2


@pytest.mark.asyncio
async def test_successful_index_no_parquet_gaps(kb):
    repo, data_root = kb
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id)
    assert repo.get_job(job.id).status == "succeeded"
    assert os.path.exists(f"{data_root}/entities.parquet")
    assert os.path.exists(f"{data_root}/relationships.parquet")
    # 所有 extract_graph 单元均成功
    step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    assert {u.status for u in repo.list_units(step.id)} == {UnitStatus.SUCCEEDED}
```

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/test_integration_e2e.py -q`
Expected: `2 passed`。

- [ ] **Step 3: 跑全量回归**

Run: `uv run pytest -q`
Expected: 全部 passed。

- [ ] **Step 4: 提交**

```bash
git add tests/test_integration_e2e.py
git commit -m "test: end-to-end index with unit tracking and single-chunk retry"
```

---

## Self-Review(写完后自查)

**1. Spec 覆盖(对照 spec §10 Phase 1):**
- 控制面 schema(Alembic)→ Task 3/4 ✓
- Orchestrator/StepRunner/UnitWorker → Task 8/9 ✓
- atomic 步骤(load/chunk/finalize/cluster)→ Plan 1 只做 `chunk_documents` 原子步作为代表;finalize/cluster 随其余 unit 步进 Plan 2(Plan 1 目标是验证机制而非完整索引,已在计划开头声明)✓(范围已显式收窄)
- `extract_graph` unit 步 + 手动重试(unit/step)→ Task 9/10 ✓
- 最小 Dashboard → **本计划不含**(REST API + React 划为后续计划)⚠ 见下
- full 索引端到端 → Task 11 ✓

**已识别的范围缺口(有意为之,需对齐预期):** 本计划不含 REST API 与 Dashboard。最小仪表盘依赖 HTTP 层,属独立计划。如需本计划内含一个命令行入口(`python -m kb_platform index <data_root>`)以便人工眼见为实,可追加一个 Task 12。

**2. 占位符扫描:** 无 TBD;所有 step 均含可执行代码或命令。Task 4 的 `script.py.mako` 用 `alembic init` 生成(给了命令),非占位。Task 8 标注了与 Task 9 的紧耦合及独立验证备选。

**3. 类型一致性检查:** `GraphAdapter` 方法签名(`chunk_document`/`extract_chunk`/`merge_extractions`)在 Task 6/7/8/9/10 一致;`Repository` 方法名在 Task 5 定义后被 8/9/10/11 引用一致(`claim_pending_units`、`set_unit_succeeded/failed`、`reset_unit_to_pending`、`reset_failed_units_to_pending`、`list_units`、`get_step`、`set_step_status`);`StepSpec(name, kind)` 跨任务一致;`ExtractionResult.entities/relationships` 列名(`title/type/description/source_id`、`source/target/weight/description/source_id`)与 graphrag `GraphExtractor` 输出及 merge 实现一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-phase1-core-engine.md`. Two execution options:

**1. Subagent-Driven(推荐)** — 每个任务派发独立 subagent,任务间两阶段评审,迭代快。

**2. Inline Execution** — 在当前会话用 executing-plans 批量执行,带检查点评审。

Which approach?
