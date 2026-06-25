"""Pydantic request/response models for the API layer.

These give write endpoints 422 validation on bad input and all endpoints a
stable, field-restricted response shape via FastAPI ``response_model``.
"""

from __future__ import annotations

from pydantic import BaseModel


# --- Knowledge base ------------------------------------------------------

class KbCreate(BaseModel):
    name: str
    method: str = "standard"
    settings_yaml: str | None = None
    min_unit_success_ratio: float | None = None


class KbOut(BaseModel):
    id: int
    name: str
    method: str


# --- Document ------------------------------------------------------------

class DocumentCreate(BaseModel):
    title: str | None = None
    text: str


class DocumentOut(BaseModel):
    id: int
    title: str
    status: str = "uploaded"


# --- Job / step / unit ---------------------------------------------------

class JobCreate(BaseModel):
    method: str = "standard"


class JobCreated(BaseModel):
    """Dedicated response for POST /kbs/{kb_id}/jobs (id + status only)."""

    id: int
    status: str


class UnitProgress(BaseModel):
    pending: int
    running: int
    succeeded: int
    failed: int
    total: int


class StepOut(BaseModel):
    id: int
    name: str
    ordinal: int
    kind: str
    status: str
    progress: UnitProgress | None = None


class UnitOut(BaseModel):
    id: int
    subject_id: str
    status: str
    error: str | None = None
    llm_raw_output: str | None = None
    needs_reconsolidation: bool = False


class JobOut(BaseModel):
    id: int
    status: str
    steps: list[StepOut] = []
