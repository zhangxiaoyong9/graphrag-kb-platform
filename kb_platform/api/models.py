# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Pydantic request/response models for the API layer.

These give write endpoints 422 validation on bad input and all endpoints a
stable, field-restricted response shape via FastAPI ``response_model``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# --- Knowledge base ------------------------------------------------------


class KbCreate(BaseModel):
    name: str
    method: str = "standard"
    settings_yaml: str | None = None
    llm_profile_id: int
    embedding_profile_id: int | None = None
    min_unit_success_ratio: float | None = None
    llm_fallback_profile_ids: list[int] | None = None
    neo4j_profile_id: int | None = None
    data_root: str | None = None


class KbUpdate(BaseModel):
    """PATCH /kbs/{id} body — full replace of name/method/settings/profiles.

    Note: min_unit_success_ratio is NOT here — it's a per-job trigger param,
    not persisted on the KB (KnowledgeBase has no such column).
    """

    name: str
    method: str = "standard"
    settings_yaml: str | None = None
    llm_profile_id: int
    embedding_profile_id: int | None = None
    llm_fallback_profile_ids: list[int] | None = None
    neo4j_profile_id: int | None = None


class KbOut(BaseModel):
    id: int
    name: str
    method: str


class ProfileRef(BaseModel):
    id: int
    name: str
    provider: str
    model: str


class KbDetailOut(KbOut):
    """GET /kbs/{id}: adds the (redacted) parsed settings + resolved profiles."""

    settings: dict
    data_root: str
    llm_profile: ProfileRef | None = None
    embedding_profile: ProfileRef | None = None
    llm_fallback_profile_ids: list[int] = []
    llm_fallback_profiles: list[ProfileRef] = []
    neo4j_profile: ProfileRef | None = None


# --- Document ------------------------------------------------------------


class DocumentCreate(BaseModel):
    title: str | None = None
    text: str


class DocumentOut(BaseModel):
    id: int
    title: str
    status: str = "uploaded"
    bytes: int = 0
    chunk_count: int = 0


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


# --- Job / step / unit ---------------------------------------------------


class JobCreate(BaseModel):
    method: str = "standard"
    type: Literal["full", "incremental"] = "full"


class JobCreated(BaseModel):
    """Dedicated response for POST /kbs/{kb_id}/jobs (id + status only)."""

    id: int
    status: str


class JobListItem(BaseModel):
    """Lightweight job item for GET /kbs/{id}/jobs (id + status only)."""

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
    input_text: str | None = None


class UnitPage(BaseModel):
    """Paginated unit list for a step (display)."""

    items: list[UnitOut]
    total: int


class JobOut(BaseModel):
    id: int
    status: str
    steps: list[StepOut] = []


# --- Query ---------------------------------------------------------------


class QueryParamsIn(BaseModel):
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    hops: int | None = None
    cypher_timeout_ms: int | None = None


class QueryRequest(BaseModel):
    method: str
    query: str
    params: QueryParamsIn | None = None


class SourceOut(BaseModel):
    kind: str
    name: str
    text: str


class QueryResultOut(BaseModel):
    answer: str
    method: str
    error: str | None = None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    llm_calls: int | None = None
    sources: list[SourceOut] | None = None
    truncated: bool = False


# --- Cost -----------------------------------------------------------------


class CostItem(BaseModel):
    """Per-model cost summary; keys mirror Repository._sum_cost's model slot."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    usd: float | None


class JobCostOut(BaseModel):
    total_usd: float | None
    by_step: dict[str, float]
    by_model: dict[str, CostItem]


class KbCostOut(JobCostOut):
    by_job: dict[int, float]


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


# --- provider profiles --------------------------------------------------
class ProfileCreate(BaseModel):
    name: str
    kind: Literal["llm", "embedding", "neo4j"]
    provider: str
    model: str = ""
    api_base: str | None = None
    api_version: str | None = None
    api_keys: list[str] = []
    structured_output: bool = True
    ssl_verify: bool = True
    username: str | None = None  # neo4j kind only


class ProfileUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    api_keys: list[str] | None = None  # None=unchanged, []=clear
    structured_output: bool | None = None
    ssl_verify: bool | None = None  # None=unchanged
    username: str | None = None  # None=unchanged


class ProfileOut(BaseModel):
    id: int
    name: str
    kind: str
    provider: str
    model: str
    api_base: str | None = None
    api_version: str | None = None
    structured_output: bool
    ssl_verify: bool
    api_keys_count: int
    username: str | None = None


# --- Conversations -------------------------------------------------------


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationRename(BaseModel):
    title: str


class ConversationOut(BaseModel):
    id: int
    kb_id: int
    title: str
    updated_at: str | None = None
    snippet: str = ""


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    method: str | None = None
    rewritten_query: str | None = None
    rewrite_fell_back: bool = False
    sources: list[SourceOut] | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_ms: float | None = None
    error: str | None = None
    cypher: str | None = None
    truncated: bool = False


class MessageSend(BaseModel):
    content: str
    method: str | None = None


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut] = []


class QueryPresetIn(BaseModel):
    name: str
    description: str = ""
    method: str
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    hops: int | None = None
    cypher_timeout_ms: int | None = None


class QueryPresetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    method: str | None = None
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    hops: int | None = None
    cypher_timeout_ms: int | None = None


class QueryPresetOut(BaseModel):
    id: int
    name: str
    description: str
    method: str
    community_level: int | None = None
    response_type: str | None = None
    top_k: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    hops: int | None = None
    cypher_timeout_ms: int | None = None
    is_builtin: bool
