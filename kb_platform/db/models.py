"""SQLAlchemy ORM models for the control plane."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
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
    input_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_reconsolidation: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    step: Mapped["Step"] = relationship(back_populates="units")
