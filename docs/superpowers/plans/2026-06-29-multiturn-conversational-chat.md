# Multi-turn Conversational Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Chat page into a real multi-turn conversation: follow-up questions are rewritten into standalone queries using persisted history, then answered by the unchanged GraphRAG engine; conversations are saved in SQLite and survive reloads.

**Architecture:** A new `ConversationService` sits **above** the existing single-shot `QueryEngine` (which is untouched). On each follow-up it calls a `Rewriter` to fold recent history into one standalone query, runs the existing `engine.search(method, standalone)`, and persists a `user` + `assistant` message row. The rewriter needs an LLM, but graphrag-llm may only be imported inside `graph/`; so the route builds a chat-completion callable via a new `build_chat_complete(settings)` graph-seam helper and **injects** it into `LlmRewriter`. The whole conversational path is therefore testable with `FakeQueryEngine` + a fake completion, no real LLM.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy + Alembic (SQLite), pytest (asyncio_mode=auto), React + TypeScript + Vite + Tailwind, vitest + msw + @testing-library/react.

## Global Constraints

- **graphrag seam:** `kb_platform/graph/graphrag_adapter.py` is the only module that imports graphrag internals; `kb_platform/query/graphrag_engine.py` is the query-side seam. The new `kb_platform/conversation/` package **must never import graphrag / graphrag_llm** — it consumes an injected `complete` callable instead.
- **No real LLM in tests:** backend tests use `FakeQueryEngine` and a fake/injected rewriter; real-LLM verification is manual (Task 7). `pytest` config: `asyncio_mode = "auto"`, `pythonpath` includes `tests`; `tests/conftest.py` autouse-fixture sets a per-test Fernet master key.
- **`QueryEngine` Protocol stays single-shot:** do not change `search(self, method, query, kb_data_root)` or the existing `POST /kbs/{id}/query` route (MCP + Query-test page keep using it).
- **DB access** is always via `Repository` inside `session_scope`; `expire_on_commit=False` keeps ORM objects usable after the session closes. SQLite FK enforcement is NOT relied on — cascade deletes are application-level (mirror `delete_document`).
- **Alembic** migrations are numbered `0001…` in `alembic/versions/`; this adds `0006`. `Base.metadata` is the autogenerate target; new models are registered by importing them at the bottom of `db/models.py` (as `models_profile` already is).
- **ruff** line-length 100, target py311; run `uv run ruff check .` before committing.
- **UI copy is Chinese** — match surrounding dashboard copy. Icons live in `web/src/components/icons.tsx` (`IconPlus`, `IconTrash`, `IconChat`, `IconSparkle`, `IconWarn`, `IconClock`, `IconDatabase` all exist). `Button` props: `variant?: primary|secondary|ghost|danger|success`, `size?: md|sm`. `Card` has `pad?: boolean`.
- **Confirmed design defaults:** query `method` is per-message (defaults to the conversation's last assistant method); the rewriter history window is the **last 6 messages**.
- Commit each task with a `feat:`/`test:`/`docs:` prefix matching repo convention; end commit messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**Backend (new):**
- `kb_platform/db/models_conversation.py` — `Conversation` + `Message` ORM models (control-plane rows for persisted chat).
- `kb_platform/conversation/__init__.py` — package marker (empty).
- `kb_platform/conversation/rewriter.py` — `HistoryTurn`, `RewriteResult`, `Rewriter` Protocol, `FakeRewriter`, `LlmRewriter`, `HISTORY_WINDOW`, `REWRITE_SYSTEM_PROMPT`. **No graphrag import.**
- `kb_platform/conversation/service.py` — `ConversationService` (rewrite → search → persist).
- `kb_platform/api/routes_conversations.py` — conversation CRUD + `POST /conversations/{id}/messages`.
- `alembic/versions/0006_conversations.py` — migration creating `conversation` + `message`.

**Backend (modified):**
- `kb_platform/db/models.py` — register `Conversation`/`Message` on `Base.metadata` (import at bottom).
- `kb_platform/db/repository.py` — add `get_kb` + conversation/message DAO methods.
- `kb_platform/graph/graphrag_adapter.py` — add `ChatTurn` dataclass + `build_chat_complete(settings)` (the one place the conversation layer's LLM touches graphrag-llm).
- `kb_platform/api/models.py` — Pydantic conversation/message models.
- `kb_platform/api/app.py` — register the conversations router; add `rewriter` param + `app.state.rewriter`.

**Frontend (modified):**
- `web/src/api/types.ts` — `Conversation`, `ConversationDetail`, `ChatMessage` types.
- `web/src/api/client.ts` — `listConversations`, `createConversation`, `getConversation`, `renameConversation`, `deleteConversation`, `sendMessage`.
- `web/src/pages/ChatPage.tsx` — rewritten: KB picker | conversation sidebar | transcript; persistence + rewrite hint.

**Tests (new):**
- `tests/test_conversation_repo.py`, `tests/test_rewriter.py`, `tests/test_conversation_service.py`, `tests/test_api_conversations.py`.
- `web/src/api/client.test.ts` (extend), `web/src/pages/ChatPage.test.tsx` (new).

---

## Task 1: Conversation/Message models + Repository DAO + Alembic 0006

**Files:**
- Create: `kb_platform/db/models_conversation.py`
- Modify: `kb_platform/db/models.py` (append register-import at bottom)
- Modify: `kb_platform/db/repository.py` (add `get_kb` + 9 conversation/message methods)
- Create: `alembic/versions/0006_conversations.py`
- Test: `tests/test_conversation_repo.py`

**Interfaces:**
- Produces (used by later tasks):
  - `Conversation` model: attrs `id, kb_id, title, created_at, updated_at`.
  - `Message` model: attrs `id, conversation_id, ordinal, role, content, method, rewritten_query, rewrite_fell_back, sources_json, prompt_tokens, output_tokens, elapsed_ms, error, created_at`.
  - `Repository.get_kb(kb_id) -> KnowledgeBase | None`
  - `Repository.create_conversation(kb_id, title=None) -> Conversation`
  - `Repository.get_conversation(conv_id) -> Conversation | None`
  - `Repository.list_conversations(kb_id) -> list[tuple[Conversation, str]]` (snippet = last assistant content, truncated 80)
  - `Repository.update_conversation_title(conv_id, title) -> bool`
  - `Repository.touch_conversation(conv_id) -> None` (bump `updated_at`)
  - `Repository.delete_conversation(conv_id) -> bool` (app-level cascade of messages)
  - `Repository.add_message(conv_id, *, role, content, method=None, rewritten_query=None, rewrite_fell_back=False, sources_json=None, prompt_tokens=None, output_tokens=None, elapsed_ms=None, error=None) -> Message` (auto-assigns `ordinal = max+1`)
  - `Repository.get_messages(conv_id) -> list[Message]` (ordered by ordinal asc)
  - `Repository.recent_messages(conv_id, limit=6) -> list[Message]` (last `limit` by ordinal, returned ascending)

- [ ] **Step 1: Write the failing test**

Create `tests/test_conversation_repo.py`:

```python
"""Repository DAO for conversations + messages (in-memory, no LLM)."""
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return repo


def test_get_kb(tmp_path):
    repo = _setup(tmp_path)
    assert repo.get_kb(1) is not None and repo.get_kb(1).name == "kb1"
    assert repo.get_kb(999) is None


def test_create_and_get_conversation(tmp_path):
    repo = _setup(tmp_path)
    c = repo.create_conversation(1, title="t")
    assert c.kb_id == 1 and c.title == "t"
    assert repo.get_conversation(c.id).id == c.id
    assert repo.get_conversation(999) is None


def test_add_message_assigns_increasing_ordinals(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    a = repo.add_message(cid, role="user", content="q1").ordinal
    b = repo.add_message(cid, role="assistant", content="a1", method="local").ordinal
    c = repo.add_message(cid, role="user", content="q2").ordinal
    assert (a, b, c) == (0, 1, 2)


def test_get_and_recent_messages_ordering(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    for i, (role, txt) in enumerate([("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]):
        repo.add_message(cid, role=role, content=txt)
    rows = repo.get_messages(cid)
    assert [r.content for r in rows] == ["q1", "a1", "q2", "a2"]
    recent = repo.recent_messages(cid, limit=2)
    assert [r.content for r in recent] == ["q2", "a2"]  # ascending, last 2


def test_list_conversations_returns_snippet(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    repo.add_message(cid, role="user", content="hello")
    repo.add_message(cid, role="assistant", content="a long answer body", method="local")
    out = repo.list_conversations(1)
    assert len(out) == 1
    conv, snippet = out[0]
    assert conv.id == cid and snippet == "a long answer body"


def test_title_touch_delete(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    assert repo.update_conversation_title(cid, "renamed")
    assert repo.get_conversation(cid).title == "renamed"
    assert repo.update_conversation_title(999, "x") is False
    repo.touch_conversation(cid)  # no error
    repo.add_message(cid, role="user", content="q")
    assert len(repo.get_messages(cid)) == 1
    assert repo.delete_conversation(cid) is True
    assert repo.get_conversation(cid) is None
    assert repo.get_messages(cid) == []  # messages cascaded
    assert repo.delete_conversation(999) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_conversation_repo.py -v`
Expected: FAIL (collection error — `models_conversation` / methods do not exist).

- [ ] **Step 3: Create the models**

Create `kb_platform/db/models_conversation.py`:

```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Conversation + Message: persisted multi-turn Q&A (control plane).

Each conversation is bound to one KB; each assistant message carries its own
retrieval result (method, sources, tokens, the rewritten query) so a transcript
renders with the same richness as a single-shot query.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from kb_platform.db.models import Base


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = (Index("ix_conversation_kb_updated", "kb_id", "updated_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_base.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (Index("ix_message_conv_ordinal", "conversation_id", "ordinal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(String, nullable=True)
    rewritten_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewrite_fell_back: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
```

- [ ] **Step 4: Register the models on Base.metadata**

Append to the bottom of `kb_platform/db/models.py` (after the existing `ProviderProfile` import line):

```python
from kb_platform.db.models_conversation import Conversation, Message  # noqa: E402,F401
```

- [ ] **Step 5: Add Repository DAO methods**

In `kb_platform/db/repository.py`, add a new import line near the existing `models_profile` import so the DAO can reference the new models:

```python
from kb_platform.db.models_conversation import Conversation, Message
```

Then add `get_kb` (generic, near the other KB methods) and the conversation/message block (after the provider-profile block at the end of the class):

```python
    # ---- knowledge base (read) ----
    def get_kb(self, kb_id: int) -> KnowledgeBase | None:
        with session_scope(self.engine) as s:
            return s.get(KnowledgeBase, kb_id)

    # --- conversations / messages -----------------------------------------

    def create_conversation(self, kb_id: int, title: str | None = None) -> Conversation:
        with session_scope(self.engine) as s:
            c = Conversation(kb_id=kb_id, title=title or "")
            s.add(c)
            s.flush()
            return c

    def get_conversation(self, conversation_id: int) -> Conversation | None:
        with session_scope(self.engine) as s:
            return s.get(Conversation, conversation_id)

    def list_conversations(self, kb_id: int) -> list[tuple[Conversation, str]]:
        """List (conversation, last-assistant snippet) for a KB, newest-updated first."""
        with session_scope(self.engine) as s:
            convs = list(
                s.scalars(
                    select(Conversation)
                    .where(Conversation.kb_id == kb_id)
                    .order_by(Conversation.updated_at.desc())
                )
            )
            out: list[tuple[Conversation, str]] = []
            for c in convs:
                snippet = s.scalar(
                    select(Message.content)
                    .where(Message.conversation_id == c.id, Message.role == "assistant")
                    .order_by(Message.ordinal.desc())
                    .limit(1)
                ) or ""
                out.append((c, snippet[:80]))
            return out

    def update_conversation_title(self, conversation_id: int, title: str) -> bool:
        with session_scope(self.engine) as s:
            c = s.get(Conversation, conversation_id)
            if c is None:
                return False
            c.title = title
            return True

    def touch_conversation(self, conversation_id: int) -> None:
        from datetime import datetime

        with session_scope(self.engine) as s:
            c = s.get(Conversation, conversation_id)
            if c is not None:
                c.updated_at = datetime.now()

    def delete_conversation(self, conversation_id: int) -> bool:
        """Delete a conversation and its messages (application-level cascade)."""
        from sqlalchemy import delete as sa_delete

        with session_scope(self.engine) as s:
            c = s.get(Conversation, conversation_id)
            if c is None:
                return False
            s.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
            s.delete(c)
            return True

    def add_message(
        self,
        conversation_id: int,
        *,
        role: str,
        content: str,
        method: str | None = None,
        rewritten_query: str | None = None,
        rewrite_fell_back: bool = False,
        sources_json: str | None = None,
        prompt_tokens: int | None = None,
        output_tokens: int | None = None,
        elapsed_ms: float | None = None,
        error: str | None = None,
    ) -> Message:
        with session_scope(self.engine) as s:
            cur = s.scalar(
                select(func.max(Message.ordinal)).where(Message.conversation_id == conversation_id)
            )
            ordinal = 0 if cur is None else int(cur) + 1
            m = Message(
                conversation_id=conversation_id,
                ordinal=ordinal,
                role=role,
                content=content,
                method=method,
                rewritten_query=rewritten_query,
                rewrite_fell_back=rewrite_fell_back,
                sources_json=sources_json,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                error=error,
            )
            s.add(m)
            s.flush()
            return m

    def get_messages(self, conversation_id: int) -> list[Message]:
        with session_scope(self.engine) as s:
            return list(
                s.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.ordinal)
                )
            )

    def recent_messages(self, conversation_id: int, limit: int = 6) -> list[Message]:
        """Last ``limit`` messages by ordinal, returned ascending."""
        with session_scope(self.engine) as s:
            rows = list(
                s.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.ordinal.desc())
                    .limit(limit)
                )
            )
            rows.reverse()
            return rows
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_conversation_repo.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Add the Alembic migration**

Create `alembic/versions/0006_conversations.py`:

```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""conversations + messages for multi-turn Q&A.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "kb_id", sa.Integer, sa.ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_conversation_kb_updated", "conversation", ["kb_id", "updated_at"])
    op.create_table(
        "message",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("method", sa.String, nullable=True),
        sa.Column("rewritten_query", sa.Text, nullable=True),
        sa.Column("rewrite_fell_back", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("sources_json", sa.Text, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("elapsed_ms", sa.Float, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_message_conv_ordinal", "message", ["conversation_id", "ordinal"])


def downgrade() -> None:
    op.drop_index("ix_message_conv_ordinal", table_name="message")
    op.drop_table("message")
    op.drop_index("ix_conversation_kb_updated", table_name="conversation")
    op.drop_table("conversation")
```

- [ ] **Step 8: Lint + commit**

```bash
uv run ruff check .
git add kb_platform/db/models_conversation.py kb_platform/db/models.py kb_platform/db/repository.py alembic/versions/0006_conversations.py tests/test_conversation_repo.py
git commit -m "$(cat <<'EOF'
feat(db): conversation/message models + DAO + alembic 0006

Persisted multi-turn Q&A control-plane rows. Each assistant message carries
its own method/sources/tokens/rewritten_query. App-level cascade delete
(SQLite FK enforcement is not relied on).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Rewriter seam + graph-seam `build_chat_complete`

**Files:**
- Create: `kb_platform/conversation/__init__.py` (empty)
- Create: `kb_platform/conversation/rewriter.py`
- Modify: `kb_platform/graph/graphrag_adapter.py` (add `ChatTurn` + `build_chat_complete`)
- Test: `tests/test_rewriter.py`

**Interfaces:**
- Consumes: none from other tasks.
- Produces (used by Tasks 3 & 4):
  - `kb_platform.graph.graphrag_adapter.ChatTurn` — dataclass `text: str, prompt_tokens: int, output_tokens: int`.
  - `kb_platform.graph.graphrag_adapter.build_chat_complete(settings: dict) -> Callable[[str, str], Awaitable[ChatTurn]]` — builds a graphrag-llm completion from the resolved KB settings' `llm` block and returns an `async (system, user) -> ChatTurn` callable. Raises `ValueError` if the settings have no `llm.api_keys`.
  - `kb_platform.conversation.rewriter.HISTORY_WINDOW = 6`
  - `kb_platform.conversation.rewriter.HistoryTurn(role: str, content: str)`
  - `kb_platform.conversation.rewriter.RewriteResult(standalone: str, prompt_tokens: int = 0, output_tokens: int = 0)`
  - `kb_platform.conversation.rewriter.Rewriter` — Protocol: `async rewrite(message: str, history: list[HistoryTurn]) -> RewriteResult`
  - `kb_platform.conversation.rewriter.FakeRewriter` — deterministic test impl.
  - `kb_platform.conversation.rewriter.LlmRewriter(complete)` — real impl taking the injected completion callable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rewriter.py`:

```python
"""Rewriter seam: FakeRewriter, LlmRewriter (injected completion), graph-seam helper."""
from types import SimpleNamespace

import pytest

import graphrag_llm.completion as glc
from kb_platform.conversation.rewriter import (
    HISTORY_WINDOW,
    FakeRewriter,
    HistoryTurn,
    LlmRewriter,
)
from kb_platform.graph.graphrag_adapter import build_chat_complete


async def test_fake_rewriter_passthrough_on_empty_history():
    r = await FakeRewriter().rewrite("hello", [])
    assert r.standalone == "hello"
    assert r.prompt_tokens == 0 and r.output_tokens == 0


async def test_fake_rewriter_prefixes_followup():
    r = await FakeRewriter().rewrite("more?", [HistoryTurn("user", "first question")])
    assert r.standalone.startswith("[ctx:") and r.standalone.endswith(" more?")


async def test_llm_rewriter_builds_prompt_and_returns_tokens():
    seen = {}

    async def fake_complete(system: str, user: str):
        seen["system"] = system
        seen["user"] = user
        return SimpleNamespace(text="Who is the CEO of Acme?", prompt_tokens=20, output_tokens=7)

    r = await LlmRewriter(fake_complete).rewrite(
        "who is the CEO?",
        [HistoryTurn("user", "tell me about Acme"), HistoryTurn("assistant", "Acme makes widgets")],
    )
    assert r.standalone == "Who is the CEO of Acme?"
    assert r.prompt_tokens == 20 and r.output_tokens == 7
    assert "Acme" in seen["user"] and "who is the CEO?" in seen["user"]


async def test_llm_rewriter_trims_to_window():
    captured = {}

    async def fake_complete(system, user):
        captured["user"] = user
        return SimpleNamespace(text="q", prompt_tokens=1, output_tokens=1)

    history = [HistoryTurn("user", f"u{i}") for i in range(HISTORY_WINDOW + 10)]
    await LlmRewriter(fake_complete).rewrite("next", history)
    assert f"u{HISTORY_WINDOW + 9}" in captured["user"]  # newest kept
    assert f"u{HISTORY_WINDOW - 1}" not in captured["user"]  # older than window dropped


def test_build_chat_complete_raises_without_keys():
    with pytest.raises(ValueError):
        build_chat_complete({"llm": {"model_provider": "openai", "api_keys": []}})


async def test_build_chat_complete_returns_callable_mapping_usage(monkeypatch):
    class _Resp:
        content = "standalone"
        class _U:
            prompt_tokens = 11
            completion_tokens = 4
        usage = _U()

    class _Completion:
        async def completion_async(self, **kwargs):
            assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]
            assert kwargs["messages"][1]["content"] == "Q"
            return _Resp()

    monkeypatch.setattr(glc, "create_completion", lambda cfg: _Completion())
    complete = build_chat_complete(
        {"llm": {"model_provider": "openai", "model": "gpt-4o-mini", "api_keys": ["sk-x"]}}
    )
    turn = await complete("system-prompt", "Q")
    assert turn.text == "standalone"
    assert turn.prompt_tokens == 11 and turn.output_tokens == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rewriter.py -v`
Expected: FAIL (import error — `kb_platform.conversation.rewriter` does not exist).

- [ ] **Step 3: Create the rewriter package**

Create `kb_platform/conversation/__init__.py` (empty file — just the package marker).

Create `kb_platform/conversation/rewriter.py`:

```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Query rewriter: turn a follow-up + history into a standalone query.

This module is part of the conversation layer and MUST NOT import graphrag /
graphrag_llm. The real LLM call is made through an injected ``complete``
callable (built by ``build_chat_complete`` in the graph seam), so the whole
multi-turn path is unit-testable with a fake completion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

# Number of prior messages (≈ 3 Q&A pairs) fed to the rewriter.
HISTORY_WINDOW = 6

REWRITE_SYSTEM_PROMPT = (
    "You rewrite a user's follow-up question into a fully self-contained "
    "question that can be understood without the conversation history. "
    "Resolve pronouns and references using the history. Preserve the user's "
    "intent and language. Output ONLY the rewritten question, with no "
    "commentary, quotes, or prefixes."
)

# async (system, user) -> ChatTurn (built by the graph seam).
ChatComplete = Callable[[str, str], Awaitable[Any]]


@dataclass
class HistoryTurn:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class RewriteResult:
    standalone: str
    prompt_tokens: int = 0
    output_tokens: int = 0


class Rewriter(Protocol):
    async def rewrite(self, message: str, history: list[HistoryTurn]) -> RewriteResult: ...


class FakeRewriter:
    """Deterministic rewriter for tests: prefixes follow-ups with a context marker."""

    async def rewrite(self, message: str, history: list[HistoryTurn]) -> RewriteResult:
        if not history:
            return RewriteResult(standalone=message)
        last = history[-1].content[:10].replace("\n", " ")
        return RewriteResult(standalone=f"[ctx:{last}] {message}")


class LlmRewriter:
    """Real rewriter backed by an injected chat-completion callable."""

    def __init__(self, complete: ChatComplete) -> None:
        self._complete = complete

    async def rewrite(self, message: str, history: list[HistoryTurn]) -> RewriteResult:
        turns = history[-HISTORY_WINDOW:]
        lines = []
        for t in turns:
            tag = "用户" if t.role == "user" else "回答"
            lines.append(f"{tag}: {t.content}")
        transcript = "\n".join(lines)
        user_msg = (
            f"对话历史:\n{transcript}\n\n"
            f"后续问题:\n{message}\n\n"
            f"改写后的独立问题:"
        )
        turn = await self._complete(REWRITE_SYSTEM_PROMPT, user_msg)
        text = (getattr(turn, "text", "") or "").strip() or message
        return RewriteResult(
            standalone=text,
            prompt_tokens=int(getattr(turn, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(turn, "output_tokens", 0) or 0),
        )
```

- [ ] **Step 4: Add the graph-seam helper**

In `kb_platform/graph/graphrag_adapter.py`, add near the top with the other imports inside the module is fine; append the following at the end of the file (after `build_adapter_for_kb`). Note this is the **only** new graphrag-llm touch point for the conversation feature:

```python
@dataclass
class ChatTurn:
    """Result of a one-shot chat completion (text + token usage)."""

    text: str
    prompt_tokens: int
    output_tokens: int


def build_chat_complete(settings: dict):
    """Build an ``async (system, user) -> ChatTurn`` callable from resolved KB settings.

    This is the ONE place the conversation layer's rewriter needs graphrag-llm:
    it constructs a completion from the KB's resolved ``llm`` block (the same
    credential path as the indexing/query engines) and returns a thin callable.
    Callers (the conversation package) never import graphrag. Raises ValueError
    when the settings carry no ``llm.api_keys``.
    """
    from dataclasses import dataclass  # noqa: DTO008 - keep dataclass local-free at import

    from graphrag_llm.completion import create_completion
    from graphrag_llm.config import ModelConfig

    llm = (settings or {}).get("llm") or {}
    api_keys = list(llm.get("api_keys") or [])
    if not api_keys:
        raise ValueError("KB has no LLM API keys for the query rewriter.")
    model_config = ModelConfig(
        type=llm.get("type", "litellm"),
        model_provider=llm.get("model_provider", "openai"),
        model=llm.get("model", "gpt-4o-mini"),
        api_base=llm.get("api_base"),
        api_version=llm.get("api_version"),
        api_key=api_keys[0],
    )
    completion = create_completion(model_config)

    async def complete(system: str, user: str) -> ChatTurn:
        resp = await completion.completion_async(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = getattr(resp, "content", "") or ""
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        return ChatTurn(text=text, prompt_tokens=pt, output_tokens=ct)

    return complete
```

> The `from dataclasses import dataclass` line above is only needed if `dataclass` is not already imported at the top of `graphrag_adapter.py`. Check the existing imports first; if `dataclass` is already imported (it is used by `CommunityReport`/context helpers elsewhere in the file), drop that local import line. `ChatTurn` itself should be defined at module top-level (not inside the function) so it can be imported by tests/consumers — place it just above `build_chat_complete`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_rewriter.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check .
git add kb_platform/conversation/__init__.py kb_platform/conversation/rewriter.py kb_platform/graph/graphrag_adapter.py tests/test_rewriter.py
git commit -m "$(cat <<'EOF'
feat(conversation): rewriter seam + graph-seam build_chat_complete

Rewriter (Protocol + Fake + Llm impl) folds recent history into a standalone
query. graphrag-llm stays in graph/: build_chat_complete returns an injected
async (system,user)->ChatTurn callable the rewriter consumes. conversation/
imports no graphrag.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: ConversationService (rewrite → search → persist)

**Files:**
- Create: `kb_platform/conversation/service.py`
- Test: `tests/test_conversation_service.py`

**Interfaces:**
- Consumes: `Repository` (Task 1: `get_conversation`, `recent_messages`, `add_message`, `touch_conversation`, `update_conversation_title`); `Rewriter` + `HistoryTurn` (Task 2); `QueryEngine` (existing: `async search(method, query, kb_data_root) -> QueryResult`).
- Produces: `ConversationService(repo, engine, rewriter, data_root).async send(conversation_id, content, method) -> Message | None`. Returns the persisted assistant `Message` (or `None` if the conversation does not exist). Behaviour:
  - First turn (no history) → pass `content` through unchanged, no rewrite.
  - Follow-up → call `rewriter.rewrite`; on exception fall back to raw `content` and set the assistant message `rewrite_fell_back=True`, `rewritten_query=None`.
  - `method` defaults to the last assistant message's method, else `"local"`.
  - Persists a `user` message then an `assistant` message; assistant tokens = rewrite tokens + answer tokens; sources serialized to `sources_json`; auto-sets conversation title from the first user message when empty.

- [ ] **Step 1: Write the failing test**

Create `tests/test_conversation_service.py`:

```python
"""ConversationService: rewrite + retrieve + persist, above the QueryEngine."""
from kb_platform.conversation.rewriter import FakeRewriter, HistoryTurn, RewriteResult
from kb_platform.conversation.service import ConversationService
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    return repo


class _RecordingRewriter:
    """Records calls; returns a deterministic standalone so we can assert the
    engine received the rewritten query (FakeQueryEngine echoes the query)."""

    def __init__(self):
        self.calls = []

    async def rewrite(self, message, history):
        self.calls.append((message, [h.content for h in history]))
        return RewriteResult(standalone=f"REWRITTEN::{message}", prompt_tokens=5, output_tokens=2)


async def test_first_turn_passes_through_no_rewrite(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id

    class Boom:
        async def rewrite(self, m, h):
            raise AssertionError("rewriter must not run on the first turn")

    svc = ConversationService(repo, FakeQueryEngine(), Boom(), data_root=".")
    msg = await svc.send(cid, "What does Acme do?", None)
    assert msg is not None and msg.role == "assistant"
    assert "What does Acme do?" in msg.content  # FakeQueryEngine echoes the query
    assert msg.rewritten_query is None and msg.rewrite_fell_back is False
    rows = repo.get_messages(cid)
    assert [r.role for r in rows] == ["user", "assistant"]


async def test_follow_up_rewrites_and_carries_method_default(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    rw = _RecordingRewriter()
    svc = ConversationService(repo, FakeQueryEngine(), rw, data_root=".")
    await svc.send(cid, "What does Acme do?", "global")
    msg2 = await svc.send(cid, "who is the CEO?", None)
    assert len(rw.calls) == 1 and rw.calls[0][0] == "who is the CEO?"
    assert msg2.rewritten_query == "REWRITTEN::who is the CEO?"
    assert "REWRITTEN::who is the CEO?" in msg2.content  # reached the engine
    assert msg2.method == "global"  # defaulted from the prior assistant turn
    assert msg2.prompt_tokens is not None and msg2.prompt_tokens >= 5  # rewrite tokens merged


async def test_rewrite_failure_falls_back(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id

    class Fail:
        async def rewrite(self, m, h):
            raise RuntimeError("boom")

    svc = ConversationService(repo, FakeQueryEngine(), Fail(), data_root=".")
    await svc.send(cid, "first", "local")
    msg2 = await svc.send(cid, "next", None)
    assert msg2.rewrite_fell_back is True
    assert msg2.rewritten_query is None
    assert "next" in msg2.content  # engine answered using the raw message


async def test_skips_rewrite_when_rewriter_is_none(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), None, data_root=".")
    await svc.send(cid, "first", "local")
    msg2 = await svc.send(cid, "followup", None)
    assert msg2.rewritten_query is None and msg2.rewrite_fell_back is False
    assert "followup" in msg2.content


async def test_missing_conversation_returns_none(tmp_path):
    repo = _setup(tmp_path)
    svc = ConversationService(repo, FakeQueryEngine(), FakeRewriter(), data_root=".")
    assert await svc.send(999, "x", None) is None


async def test_auto_title_from_first_message(tmp_path):
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), FakeRewriter(), data_root=".")
    await svc.send(cid, "Tell me everything about the Acme corporation", None)
    assert repo.get_conversation(cid).title.startswith("Tell me everything about the Acme")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_conversation_service.py -v`
Expected: FAIL (import error — `kb_platform.conversation.service` does not exist).

- [ ] **Step 3: Implement ConversationService**

Create `kb_platform/conversation/service.py`:

```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""ConversationService: rewrite -> retrieve -> persist, above the QueryEngine.

The QueryEngine stays single-shot (``search(method, query, root)``). This
service owns the multi-turn orchestration: load recent history, rewrite a
follow-up into a standalone query (first turn passes through), call the
engine, then persist a user message and an assistant message. graphrag is
never imported here.
"""
from __future__ import annotations

import json
import logging

from kb_platform.conversation.rewriter import HistoryTurn, Rewriter
from kb_platform.query.engine import SourceRef

logger = logging.getLogger(__name__)


class ConversationService:
    def __init__(self, repo, engine, rewriter: Rewriter | None, data_root: str) -> None:
        self._repo = repo
        self._engine = engine
        self._rewriter = rewriter
        self._data_root = data_root

    async def send(self, conversation_id: int, content: str, method: str | None):
        conv = self._repo.get_conversation(conversation_id)
        if conv is None:
            return None

        history_rows = self._repo.recent_messages(conversation_id)
        history = [HistoryTurn(r.role, r.content) for r in history_rows]
        chosen_method = method or _last_method(history_rows) or "local"

        rewrote = False
        rewrite_fell_back = False
        rw_pt = 0
        rw_ot = 0
        standalone = content
        if history and self._rewriter is not None:
            try:
                rr = await self._rewriter.rewrite(content, history)
                standalone = rr.standalone
                rw_pt = rr.prompt_tokens
                rw_ot = rr.output_tokens
                rewrote = True
            except Exception:  # noqa: BLE001 - fall back to raw message, never block
                logger.exception("query rewrite failed; falling back to raw message")
                rewrite_fell_back = True

        # Persist the user message first (always).
        self._repo.add_message(conversation_id, role="user", content=content)

        result = await self._engine.search(chosen_method, standalone, self._data_root)

        assistant = self._repo.add_message(
            conversation_id,
            role="assistant",
            content=result.answer,
            method=chosen_method,
            rewritten_query=standalone if rewrote else None,
            rewrite_fell_back=rewrite_fell_back,
            sources_json=_serialize_sources(result.sources),
            prompt_tokens=_merge_tokens(rw_pt, result.prompt_tokens),
            output_tokens=_merge_tokens(rw_ot, result.output_tokens),
            elapsed_ms=result.elapsed_ms,
            error=result.error,
        )
        self._repo.touch_conversation(conversation_id)
        if not conv.title:
            self._repo.update_conversation_title(conversation_id, _title_from(content))
        return assistant


def _last_method(history_rows) -> str | None:
    for row in reversed(history_rows):
        if row.role == "assistant" and row.method:
            return row.method
    return None


def _merge_tokens(rewrite_tokens: int, answer_tokens: int | None) -> int | None:
    total = (rewrite_tokens or 0) + (answer_tokens or 0)
    return total or None


def _serialize_sources(sources: list[SourceRef] | None) -> str | None:
    if not sources:
        return None
    return json.dumps([{"kind": s.kind, "name": s.name, "text": s.text} for s in sources])


def _title_from(content: str) -> str:
    one = " ".join(content.split())
    return one[:40] + ("…" if len(one) > 40 else "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_conversation_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check .
git add kb_platform/conversation/service.py tests/test_conversation_service.py
git commit -m "$(cat <<'EOF'
feat(conversation): ConversationService rewrites + retrieves + persists

Sits above the single-shot QueryEngine. First turn passes through; follow-ups
rewrite via the injected Rewriter (fallback to raw on error). Persists user +
assistant messages; assistant tokens merge rewrite + answer; auto-titles the
conversation from the first user message.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: API routes + Pydantic models + app wiring

**Files:**
- Modify: `kb_platform/api/models.py` (append conversation/message Pydantic models)
- Create: `kb_platform/api/routes_conversations.py`
- Modify: `kb_platform/api/app.py` (register router; add `rewriter` param + `app.state.rewriter`)
- Test: `tests/test_api_conversations.py`

**Interfaces:**
- Consumes: `Repository` (Task 1), `ConversationService` (Task 3), `assemble_kb_settings` + `build_chat_complete` + `GraphRagQueryEngine` + `LlmRewriter` (existing + Task 2).
- Produces (HTTP):
  - `POST /kbs/{kb_id}/conversations` `{title?}` → `ConversationOut` (201; 404 if KB missing)
  - `GET /kbs/{kb_id}/conversations` → `list[ConversationOut]`
  - `GET /conversations/{id}` → `ConversationDetailOut` (404 if missing)
  - `PATCH /conversations/{id}` `{title}` → `ConversationOut` (404 if missing)
  - `DELETE /conversations/{id}` → 204 (404 if missing)
  - `POST /conversations/{id}/messages` `{content, method?}` → `MessageOut` (404 if conversation missing). In production (no injected engine) resolves KB settings → builds engine + rewriter; settings-resolution failure returns `MessageOut(error=...)` with HTTP 200 (no 500). With an injected engine+rewriter (tests) it skips KB/settings resolution.
- App wiring: `create_app(repo, data_root=".", query_engine=None, rewriter=None)` sets `app.state.rewriter = rewriter`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_conversations.py`:

```python
"""Conversation HTTP routes: real async round-trip via ASGITransport, no LLM."""
import httpx
import pytest

from kb_platform.api.app import create_app
from kb_platform.conversation.rewriter import FakeRewriter
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


def _make_app(tmp_path, *, inject=True):
    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    kwargs = {"query_engine": FakeQueryEngine(), "rewriter": FakeRewriter()} if inject else {}
    return create_app(repo, data_root=str(tmp_path), **kwargs)


@pytest.fixture()
def client(tmp_path):
    app = _make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_create_list_get_rename_delete(client):
    await client.__aenter__()
    try:
        r = await client.post("/kbs/1/conversations", json={})
        assert r.status_code == 201
        cid = r.json()["id"]
        # list
        lst = await client.get("/kbs/1/conversations")
        assert lst.status_code == 200 and lst.json()[0]["id"] == cid
        # rename
        assert (await client.patch(f"/conversations/{cid}", json={"title": "T"})).json()["title"] == "T"
        # 404s
        assert (await client.get("/conversations/999")).status_code == 404
        assert (await client.post("/kbs/999/conversations", json={})).status_code == 404
        # delete
        assert (await client.delete(f"/conversations/{cid}")).status_code == 204
        assert (await client.get(f"/conversations/{cid}")).status_code == 404
    finally:
        await client.__aexit__(None, None, None)


async def test_send_first_turn_then_followup(client):
    await client.__aenter__()
    try:
        cid = (await client.post("/kbs/1/conversations", json={})).json()["id"]
        m1 = await client.post(f"/conversations/{cid}/messages", json={"content": "hi", "method": "local"})
        assert m1.status_code == 200
        body1 = m1.json()
        assert body1["role"] == "assistant" and "hi" in body1["content"]
        assert body1["rewritten_query"] is None and body1["rewrite_fell_back"] is False
        # second turn rewrites
        m2 = await client.post(f"/conversations/{cid}/messages", json={"content": "more"})
        body2 = m2.json()
        assert body2["rewritten_query"] is not None  # follow-up was rewritten
        assert body2["method"] == "local"  # defaulted from prior assistant
        # detail has 4 rows
        det = await client.get(f"/conversations/{cid}")
        assert len(det.json()["messages"]) == 4
    finally:
        await client.__aexit__(None, None, None)


async def test_send_missing_conversation_404(client):
    await client.__aenter__()
    try:
        r = await client.post("/conversations/999/messages", json={"content": "x"})
        assert r.status_code == 404
    finally:
        await client.__aexit__(None, None, None)


async def test_production_settings_error_is_graceful(tmp_path):
    # No injected engine/rewriter -> production path; KB has no profile, so
    # assemble_kb_settings raises -> the route returns a 200 with an error
    # field instead of a 500.
    app = _make_app(tmp_path, inject=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/kbs/1/conversations", json={})).json()["id"]
        r = await ac.post(f"/conversations/{cid}/messages", json={"content": "hi"})
        assert r.status_code == 200
        assert r.json()["error"].startswith("settings resolution failed")
```

> Tests use the `asyncio_mode = "auto"` convention (plain `async def`), matching the existing suite. The `client` fixture returns an `AsyncClient`; the explicit `__aenter__/__aexit__` avoids needing pytest-asyncio session scoping. (If the suite's other tests use `async with` inline instead, mirror that — both work under `asyncio_mode=auto`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_conversations.py -v`
Expected: FAIL (404 for `/kbs/1/conversations` — route not registered).

- [ ] **Step 3: Add the Pydantic models**

Append to `kb_platform/api/models.py`:

```python
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


class MessageSend(BaseModel):
    content: str
    method: str | None = None


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut] = []
```

- [ ] **Step 4: Create the routes**

Create `kb_platform/api/routes_conversations.py`:

```python
# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Conversation CRUD + POST /conversations/{id}/messages (multi-turn Q&A).

Single-shot ``POST /kbs/{id}/query`` (MCP, query-test page) is unchanged; this
is the multi-turn path that runs ConversationService above the QueryEngine.
"""
import json

from fastapi import APIRouter, HTTPException, Request

from kb_platform.api.models import (
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationRename,
    MessageOut,
    MessageSend,
    SourceOut,
)

router = APIRouter()


def _conv_out(c, snippet: str = "") -> ConversationOut:
    return ConversationOut(
        id=c.id,
        kb_id=c.kb_id,
        title=c.title,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
        snippet=snippet,
    )


def _sources_from_json(sources_json: str | None) -> list[SourceOut] | None:
    if not sources_json:
        return None
    try:
        data = json.loads(sources_json)
        return [SourceOut(kind=s["kind"], name=s["name"], text=s["text"]) for s in data]
    except Exception:  # noqa: BLE001 - sources are a nice-to-have
        return None


def _message_out(m) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        method=m.method,
        rewritten_query=m.rewritten_query,
        rewrite_fell_back=m.rewrite_fell_back,
        sources=_sources_from_json(m.sources_json),
        prompt_tokens=m.prompt_tokens,
        output_tokens=m.output_tokens,
        elapsed_ms=m.elapsed_ms,
        error=m.error,
    )


@router.post("/kbs/{kb_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(kb_id: int, payload: ConversationCreate, request: Request):
    repo = request.app.state.repo
    if repo.get_kb(kb_id) is None:
        raise HTTPException(404)
    return _conv_out(repo.create_conversation(kb_id, title=payload.title))


@router.get("/kbs/{kb_id}/conversations", response_model=list[ConversationOut])
def list_conversations(kb_id: int, request: Request):
    repo = request.app.state.repo
    return [_conv_out(c, snip) for c, snip in repo.list_conversations(kb_id)]


@router.get("/conversations/{conv_id}", response_model=ConversationDetailOut)
def get_conversation(conv_id: int, request: Request):
    repo = request.app.state.repo
    c = repo.get_conversation(conv_id)
    if c is None:
        raise HTTPException(404)
    detail = ConversationDetailOut(
        id=c.id,
        kb_id=c.kb_id,
        title=c.title,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
        snippet="",
        messages=[_message_out(m) for m in repo.get_messages(conv_id)],
    )
    return detail


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
def rename_conversation(conv_id: int, payload: ConversationRename, request: Request):
    repo = request.app.state.repo
    if not repo.update_conversation_title(conv_id, payload.title):
        raise HTTPException(404)
    return _conv_out(repo.get_conversation(conv_id))


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, request: Request):
    repo = request.app.state.repo
    if not repo.delete_conversation(conv_id):
        raise HTTPException(404)


@router.post("/conversations/{conv_id}/messages", response_model=MessageOut)
async def send_message(conv_id: int, payload: MessageSend, request: Request):
    repo = request.app.state.repo
    engine = request.app.state.query_engine
    rewriter = request.app.state.rewriter
    if engine is None:
        # Production: resolve KB settings, then build a real engine + rewriter.
        conv = repo.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(404)
        kb = repo.get_kb(conv.kb_id)
        if kb is None:
            raise HTTPException(404)
        from kb_platform.conversation.rewriter import LlmRewriter
        from kb_platform.graph.graphrag_adapter import assemble_kb_settings, build_chat_complete
        from kb_platform.query.graphrag_engine import GraphRagQueryEngine

        try:
            settings = assemble_kb_settings(kb, repo)
        except Exception as exc:  # noqa: BLE001 - graceful error, never 500
            return MessageOut(id=0, role="assistant", content="", error=f"settings resolution failed: {exc}")
        engine = GraphRagQueryEngine(data_root=kb.data_root, model_config=settings)
        try:
            rewriter = LlmRewriter(build_chat_complete(settings))
        except Exception:  # noqa: BLE001 - rewriter optional; service falls back per-turn
            rewriter = None

    from kb_platform.conversation.service import ConversationService

    service = ConversationService(repo, engine, rewriter, request.app.state.data_root)
    msg = await service.send(conv_id, payload.content, payload.method)
    if msg is None:
        raise HTTPException(404)
    return _message_out(msg)
```

- [ ] **Step 5: Wire the router + state into the app**

In `kb_platform/api/app.py`:

1. Add the import with the other router imports:
```python
from kb_platform.api.routes_conversations import router as conversations_router
```
2. Change `create_app` signature and set state:
```python
def create_app(
    repo: Repository, data_root: str = ".", query_engine: QueryEngine | None = None, rewriter=None
) -> FastAPI:
```
and after `app.state.query_engine = (...)`:
```python
    app.state.rewriter = rewriter  # None = build real per-KB (production); injected in tests
```
3. Register the router (alongside the others, before the SPA catch-all):
```python
    app.include_router(conversations_router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_api_conversations.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the full backend suite + lint**

```bash
uv run pytest
uv run ruff check .
```
Expected: all green (existing tests unaffected; `create_app`'s new `rewriter` param has a default so existing callers compile).

- [ ] **Step 8: Commit**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_conversations.py kb_platform/api/app.py tests/test_api_conversations.py
git commit -m "$(cat <<'EOF'
feat(api): conversation CRUD + POST /conversations/{id}/messages

Multi-turn Q&A route runs ConversationService above the unchanged single-shot
query engine. Injected engine+rewriter (tests) skip KB/settings resolution;
production resolves profiles, builds engine + rewriter, and returns a graceful
error (never 500) when settings resolution fails.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend API client + types

**Files:**
- Modify: `web/src/api/types.ts` (append conversation types)
- Modify: `web/src/api/client.ts` (append conversation client functions + import)
- Test: `web/src/api/client.test.ts` (extend)

**Interfaces:**
- Produces (used by Task 6):
  - types: `ChatMessage`, `Conversation`, `ConversationDetail`.
  - client: `listConversations(kbId)`, `createConversation(kbId, title?)`, `getConversation(id)`, `renameConversation(id, title)`, `deleteConversation(id)`, `sendMessage(convId, content, method?)`.

- [ ] **Step 1: Extend the test**

Add handlers + a test to `web/src/api/client.test.ts`. First extend the import line:

```typescript
import { createKb, deleteDocument, getDocumentDetail, getDocumentEvidence, listKbs, retryUnit, createConversation, sendMessage } from "./client";
```

Add inside the `setupServer(...)` handler list:

```typescript
  http.post("/kbs/1/conversations", () =>
    HttpResponse.json({ id: 9, kb_id: 1, title: "", updated_at: null, snippet: "" }),
  ),
  http.post("/conversations/9/messages", async ({ request }) => {
    const b = (await request.json()) as { content: string; method: string };
    return HttpResponse.json({
      id: 10,
      role: "assistant",
      content: `A:${b.content}`,
      method: b.method,
      rewritten_query: null,
      rewrite_fell_back: false,
      sources: [],
    });
  }),
```

Append a test:

```typescript
test("conversation client posts to the right paths", async () => {
  const c = await createConversation(1);
  expect(c.id).toBe(9);
  const m = await sendMessage(9, "hi", "local");
  expect(m.role).toBe("assistant");
  expect(m.content).toBe("A:hi");
  expect(m.method).toBe("local");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- src/api/client.test.ts`
Expected: FAIL (`createConversation` / `sendMessage` not exported).

- [ ] **Step 3: Add the types**

Append to `web/src/api/types.ts`:

```typescript
export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  method?: string | null;
  rewritten_query?: string | null;
  rewrite_fell_back?: boolean;
  sources?: SourceRef[];
  prompt_tokens?: number | null;
  output_tokens?: number | null;
  elapsed_ms?: number | null;
  error?: string | null;
}

export interface Conversation {
  id: number;
  kb_id: number;
  title: string;
  updated_at?: string | null;
  snippet?: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}
```

- [ ] **Step 4: Add the client functions**

Extend the import line at the top of `web/src/api/client.ts` to include the new types:

```typescript
import type { KbOut, KbDetail, DocumentOut, DocumentDetail, EvidenceDetail, JobOut, StepOut, UnitOut, KbCreate, DocumentCreate, QueryResult, JobCost, KbCost, GraphData, Health, ProviderProfile, ProfileCreate, KbStats, Conversation, ConversationDetail, ChatMessage } from "./types";
```

Append the functions (e.g. after the `query` export):

```typescript
export const listConversations = (kbId: number) => req<Conversation[]>(`/kbs/${kbId}/conversations`);
export const createConversation = (kbId: number, title?: string) =>
  req<Conversation>(`/kbs/${kbId}/conversations`, { method: "POST", body: JSON.stringify({ title: title ?? null }) });
export const getConversation = (id: number) => req<ConversationDetail>(`/conversations/${id}`);
export const renameConversation = (id: number, title: string) =>
  req<Conversation>(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
export const deleteConversation = (id: number) => req<void>(`/conversations/${id}`, { method: "DELETE" });
export const sendMessage = (convId: number, content: string, method?: string) =>
  req<ChatMessage>(`/conversations/${convId}/messages`, { method: "POST", body: JSON.stringify({ content, method: method ?? null }) });
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && npm test -- src/api/client.test.ts`
Expected: PASS (all client tests).

- [ ] **Step 6: Commit**

```bash
cd web && git add src/api/types.ts src/api/client.ts src/api/client.test.ts
git commit -m "$(cat <<'EOF'
feat(web): conversation API client + types

listConversations/createConversation/getConversation/renameConversation/
deleteConversation/sendMessage for the multi-turn Chat page.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: ChatPage — conversation sidebar + persistence + rewrite hint

**Files:**
- Modify (rewrite): `web/src/pages/ChatPage.tsx`
- Create: `web/src/pages/ChatPage.test.tsx`

**Interfaces:**
- Consumes: `listKbs` + the Task 5 conversation client functions; `QUERY_METHODS`, `QueryResultView`, `Card`/`CardHeader`/`Button`/`Spinner`/`Badge`/`EmptyState`, icons.

- [ ] **Step 1: Write the failing component test**

Create `web/src/pages/ChatPage.test.tsx`:

```typescript
import { render, screen, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import ChatPage from "./ChatPage";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.get("/kbs/1/conversations", () => HttpResponse.json([])),
  http.post("/kbs/1/conversations", () =>
    HttpResponse.json({ id: 8, kb_id: 1, title: "", updated_at: null, snippet: "" }),
  ),
  http.get("/conversations/8", () => HttpResponse.json({ id: 8, kb_id: 1, title: "", messages: [] })),
  http.post("/conversations/8/messages", async ({ request }) => {
    const b = (await request.json()) as { content: string };
    return HttpResponse.json({
      id: 11,
      role: "assistant",
      content: `A:${b.content}`,
      method: "local",
      rewritten_query: null,
      rewrite_fell_back: false,
      sources: [],
    });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("creates a conversation and shows the answer", async () => {
  render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  );
  // KB list renders; create a new conversation
  const newBtn = await screen.findByRole("button", { name: /新建/ });
  fireEvent.click(newBtn);
  // type and send
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));
  expect(await screen.findByText("A:hello")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- src/pages/ChatPage.test.tsx`
Expected: FAIL (no "新建" button in the current single-turn ChatPage).

- [ ] **Step 3: Rewrite ChatPage**

Replace the entire contents of `web/src/pages/ChatPage.tsx` with:

```tsx
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import {
  listKbs,
  listConversations,
  createConversation,
  getConversation,
  deleteConversation,
  sendMessage,
} from "../api/client";
import { QUERY_METHODS } from "../lib/query-methods";
import { cn } from "../lib/cn";
import { Card, CardHeader, Button, Spinner, Badge, EmptyState } from "../components/ui";
import { QueryResultView } from "../components/QueryResultView";
import type { SourceRef, Conversation, ChatMessage } from "../api/types";
import { IconChat, IconSparkle, IconWarn, IconClock, IconDatabase, IconPlus, IconTrash } from "../components/icons";

// Local ids for optimistic bubbles are negative so they never clash with server ids.
let seq = 0;

/** Multi-turn chat: KB picker | conversation sidebar | transcript. */
export default function ChatPage() {
  const kbs = useAsync(() => listKbs(), []);
  const list = kbs.data ?? [];

  const [kbId, setKbId] = useState<number | null>(null);
  const [convId, setConvId] = useState<number | null>(null);
  const [convList, setConvList] = useState<Conversation[]>([]);
  const [method, setMethod] = useState("local");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (kbId == null && list.length > 0) setKbId(list[0].id);
  }, [list, kbId]);

  // Load conversation list whenever the KB changes; reset the open conversation.
  useEffect(() => {
    if (kbId == null) {
      setConvList([]);
      setConvId(null);
      setMessages([]);
      return;
    }
    let alive = true;
    listConversations(kbId)
      .then((cs) => {
        if (alive) {
          setConvList(cs);
          setConvId(null);
          setMessages([]);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [kbId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const reloadList = async () => {
    if (kbId == null) return;
    try {
      setConvList(await listConversations(kbId));
    } catch {
      /* ignore */
    }
  };

  const selectConv = async (id: number) => {
    setConvId(id);
    try {
      setMessages((await getConversation(id)).messages);
    } catch {
      setMessages([]);
    }
  };

  const newConversation = async () => {
    if (kbId == null) return;
    try {
      const c = await createConversation(kbId);
      setConvList((cs) => [c, ...cs]);
      setConvId(c.id);
      setMessages([]);
    } catch {
      /* ignore */
    }
  };

  const removeConv = async (id: number) => {
    try {
      await deleteConversation(id);
    } catch {
      /* ignore */
    }
    setConvList((cs) => cs.filter((c) => c.id !== id));
    if (convId === id) {
      setConvId(null);
      setMessages([]);
    }
  };

  const send = async () => {
    if (kbId == null || convId == null || !input.trim() || busy) return;
    const q = input.trim();
    const userId = --seq;
    const pendingId = --seq;
    setMessages((m) => [
      ...m,
      { id: userId, role: "user", content: q },
      { id: pendingId, role: "assistant", content: "", method, rewrite_fell_back: false },
    ]);
    setInput("");
    setBusy(true);
    const t0 = performance.now();
    try {
      const r = await sendMessage(convId, q, method);
      const fallbackElapsed = performance.now() - t0;
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId ? { ...r, elapsed_ms: r.elapsed_ms ?? fallbackElapsed } : msg,
        ),
      );
      void reloadList(); // refresh sidebar snippet/title
    } catch (e) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, content: "", error: (e as Error).message ?? String(e) }
            : msg,
        ),
      );
    } finally {
      setBusy(false);
    }
  };

  if (list.length === 0 && !kbs.loading) {
    return (
      <EmptyState
        icon={<IconChat />}
        title="还没有可对话的知识库"
        hint="先创建知识库并完成一次索引，再回来进行问答对话。"
        action={<Link to="/kbs" className="btn btn-primary btn-sm">前往知识库管理</Link>}
      />
    );
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[220px_240px_1fr]">
      {/* Col 1: KB picker */}
      <Card>
        <CardHeader title="知识库" icon={<IconDatabase width={18} height={18} />} />
        <div className="mt-3 space-y-1">
          {list.map((k) => (
            <button
              key={k.id}
              onClick={() => setKbId(k.id)}
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors",
                kbId === k.id ? "bg-brand-50 text-brand-700" : "text-body hover:bg-surface-2",
              )}
            >
              <span className="truncate font-medium">{k.name}</span>
              <Badge tone={kbId === k.id ? "brand" : "neutral"}>{k.method}</Badge>
            </button>
          ))}
          {kbs.loading && <p className="px-3 text-[12px] text-muted">加载中…</p>}
        </div>
      </Card>

      {/* Col 2: conversations */}
      <Card pad={false} className="flex max-h-[calc(100vh-180px)] flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <span className="text-[13px] font-semibold text-ink">对话</span>
          <Button variant="ghost" size="sm" disabled={kbId == null} onClick={newConversation}>
            <IconPlus width={14} height={14} /> 新建
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto p-2">
          {convList.length === 0 ? (
            <p className="px-2 py-4 text-center text-[12px] text-muted">点击「新建」开始一段对话</p>
          ) : (
            convList.map((c) => (
              <div
                key={c.id}
                className={cn(
                  "group flex items-center gap-1 rounded-lg px-2 py-2 text-left transition-colors",
                  convId === c.id ? "bg-brand-50" : "hover:bg-surface-2",
                )}
              >
                <button onClick={() => selectConv(c.id)} className="min-w-0 flex-1 text-left">
                  <div className="truncate text-[13px] font-medium text-ink">{c.title || "新对话"}</div>
                  <div className="truncate text-[11px] text-muted">{c.snippet || "（暂无消息）"}</div>
                </button>
                <button
                  onClick={() => removeConv(c.id)}
                  className="shrink-0 text-muted opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"
                  title="删除对话"
                >
                  <IconTrash width={13} height={13} />
                </button>
              </div>
            ))
          )}
        </div>
      </Card>

      {/* Col 3: transcript */}
      <Card pad={false} className="flex h-[calc(100vh-180px)] min-h-[420px] flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <IconChat width={18} height={18} className="text-brand" />
            <span className="text-[15px] font-semibold text-ink">问答对话</span>
          </div>
          <div className="flex items-center gap-1">
            {QUERY_METHODS.map((m) => (
              <button
                key={m.key}
                onClick={() => setMethod(m.key)}
                className={cn(
                  "rounded-md border px-2 py-1 text-[12px] font-mono",
                  method === m.key ? "border-brand bg-brand-50 text-brand-700" : "border-line text-body",
                )}
              >
                {m.name}
              </button>
            ))}
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {convId == null ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-grad-soft text-brand">
                <IconSparkle width={22} height={22} />
              </span>
              <p className="text-sm font-medium text-ink">开始提问</p>
              <p className="mt-1 max-w-xs text-[13px]">在左侧「新建」一段对话，后续提问会参考上下文。</p>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted">
              <p className="text-[13px]">
                用 <span className="font-mono">{method}</span> 方式提问。后续追问会自动结合上下文改写。
              </p>
            </div>
          ) : (
            messages.map((m) => <ChatBubble key={m.id} m={m} />)
          )}
        </div>

        <div className="border-t border-line px-4 py-3">
          <div className="flex items-end gap-2">
            <textarea
              className="textarea h-12 resize-none py-2.5"
              placeholder={
                convId == null
                  ? "先「新建」一段对话…"
                  : `向知识库提问（${method} 方式）…  ⌘/Ctrl + Enter 发送`
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
              }}
              disabled={convId == null}
            />
            <Button variant="primary" disabled={busy || !input.trim() || convId == null} onClick={send}>
              {busy ? <Spinner /> : <IconSparkle width={16} height={16} />}
              {busy ? "回答中…" : "发送"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function ChatBubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-brand px-4 py-2.5 text-sm text-white">
          {m.content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2">
        <div className="flex items-center gap-1.5 text-[11px] text-muted">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-grad-soft text-brand">
            <IconSparkle width={13} height={13} />
          </span>
          {m.method && <Badge tone="brand">{m.method}</Badge>}
          {m.elapsed_ms != null && (
            <span className="flex items-center gap-0.5 nums">
              <IconClock width={12} height={12} /> {m.elapsed_ms.toFixed(0)} ms
            </span>
          )}
          {m.rewrite_fell_back && <span className="text-warning">(改写失败，已按原文检索)</span>}
          {m.content === "" && !m.error && (
            <span className="flex items-center gap-1">
              <Spinner /> 生成中…
            </span>
          )}
        </div>
        {m.rewritten_query && (
          <div className="text-[11px] text-muted">
            理解为：<span className="font-mono text-ink/70">{m.rewritten_query}</span>
          </div>
        )}
        {m.error ? (
          <div className="flex items-start gap-2 rounded-2xl rounded-tl-sm bg-danger-soft px-4 py-2.5 text-[13px] text-danger">
            <IconWarn width={15} height={15} className="mt-0.5 shrink-0" />
            <span>{m.error}</span>
          </div>
        ) : (
          <div className="whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-surface-2 px-4 py-2.5 text-sm leading-relaxed text-ink">
            {m.content}
          </div>
        )}
        {!m.error && (
          <QueryResultView
            result={{
              answer: m.content,
              method: m.method ?? "local",
              error: m.error ?? null,
              elapsed_ms: m.elapsed_ms,
              prompt_tokens: m.prompt_tokens ?? undefined,
              output_tokens: m.output_tokens ?? undefined,
              sources: m.sources as SourceRef[] | undefined,
            }}
          />
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- src/pages/ChatPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run the full frontend suite + build**

```bash
cd web && npm test && npm run build
```
Expected: all vitest tests pass; `tsc -b && vite build` succeeds (no type errors).

- [ ] **Step 6: Commit**

```bash
cd web && git add src/pages/ChatPage.tsx src/pages/ChatPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): ChatPage multi-turn — conversation sidebar + persistence

KB | conversations | transcript. Conversations persist across reloads
(GET /conversations/{id} resumes). Follow-ups show the rewritten query hint
and a fallback warning; each assistant bubble keeps sources via QueryResultView.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Regression + migration smoke + docs

**Files:**
- Modify: `README.md` + `README.zh.md` (add conversation endpoints to the API table + a multi-turn chat note)
- Modify: `CLAUDE.md` (one line on the conversation layer above the QueryEngine)
- Create: `docs/verify-multiturn-conversational-chat-2026-06-29.md` (verification record)

**Interfaces:** none (docs + verification only).

- [ ] **Step 1: Apply the migration on a fresh DB**

```bash
cp kb.db kb.db.bak 2>/dev/null || true
uv run alembic upgrade head
uv run alembic current          # expect: 0006 (head)
uv run alembic downgrade -1     # back to 0005
uv run alembic upgrade head     # re-apply 0006
```
Expected: `alembic current` reports `0006`; downgrade then re-upgrade run cleanly (no orphaned tables/indexes).

- [ ] **Step 2: Full backend + frontend regression**

```bash
uv run pytest                   # expect: all pass (existing 252 + new tests)
uv run ruff check .             # expect: All checks passed!
cd web && npm test && npm run build
```
Expected: all green. Note the new test count in the verify record.

- [ ] **Step 3: E2E regression (no LLM)**

```bash
cd web && npm run e2e
```
Expected: the existing Playwright suite still passes (conversations routes are additive; the fake server is unaffected). If a Chat-page flow is in the suite, it still passes because the single-shot `/kbs/{id}/query` path is unchanged.

- [ ] **Step 4: Manual real-LLM smoke (optional but recommended)**

Start the two processes against an **already-indexed** KB with a working LLM profile:
```bash
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000   # terminal 1
uv run python -m kb_platform.worker kb.db                     # terminal 2
```
Open `http://127.0.0.1:8000` → 检索与问答 → Chat. Verify:
1. New conversation → ask "Acme 是做什么的?" → grounded answer + sources.
2. Follow-up "它的 CEO 是谁?" → the assistant bubble shows **理解为: <rewritten standalone query referencing Acme>** and the answer resolves the pronoun.
3. Reload the page → the conversation is still there (sidebar); reopening it restores the transcript.
4. Switch method mid-conversation (e.g. local → global) → the new turn uses the chosen method.
5. Delete a conversation → it disappears and its messages are gone.

Record results (with any `rewritten_query` examples) in the verify doc.

- [ ] **Step 5: Update docs**

- `README.md` + `README.zh.md` API table — add:
  ```
  | POST | /kbs/{kb_id}/conversations | Create a conversation bound to a KB |
  | GET  | /kbs/{kb_id}/conversations | List conversations (id, title, snippet) |
  | GET  | /conversations/{id} | Conversation + ordered messages |
  | PATCH| /conversations/{id} | Rename |
  | DELETE| /conversations/{id} | Delete + cascade messages |
  | POST | /conversations/{id}/messages | Multi-turn send: rewrite follow-up → search → persist; returns the assistant message |
  ```
  And add a short "Multi-turn chat" paragraph under the Query section: follow-ups are rewritten into standalone queries against the last ~6 messages, then answered by the same engine; conversations persist in SQLite.
- `CLAUDE.md` — under "Two graphrag isolation seams", add a line: multi-turn chat lives in `kb_platform/conversation/` as a layer **above** the `QueryEngine` Protocol; `ConversationService` rewrites (injected `complete` callable, graphrag stays in `graph/`) → calls the unchanged single-shot engine → persists `conversation`/`message` rows. The single-shot `POST /kbs/{id}/query` (MCP, query-test) is unchanged.

- [ ] **Step 6: Write the verification record**

Create `docs/verify-multiturn-conversational-chat-2026-06-29.md` recording: pytest count, ruff clean, npm test/build pass, `alembic current` = 0006, e2e result, and (if run) the manual real-LLM smoke results including example `rewritten_query` values.

- [ ] **Step 7: Commit**

```bash
git add README.md README.zh.md CLAUDE.md docs/verify-multiturn-conversational-chat-2026-06-29.md
git commit -m "$(cat <<'EOF'
docs(verify): multiturn conversational chat — endpoints, README, verify record

A1 multi-turn chat shipped: persisted conversations, follow-up rewrite above
the unchanged single-shot QueryEngine. A2 (streaming) and A3 (query-tuning)
remain as later roadmap items.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Definition of Done

- `conversation` + `message` tables exist (Alembic `0006`); `alembic current` reports `0006`.
- `ConversationService` rewrites follow-ups (first turn passes through), reuses the unchanged `QueryEngine.search`, and persists user + assistant messages with merged tokens + sources.
- `POST /kbs/{id}/query` and the MCP `query_knowledge_base` tool are unchanged.
- Six conversation endpoints work; settings-resolution failure returns a graceful 200 error, never 500.
- Chat page shows a conversation sidebar, persists across reloads, shows the rewritten-query hint + fallback warning, and renders sources per assistant message.
- `uv run pytest`, `uv run ruff check .`, `npm test`, `npm run build`, and `npm run e2e` all pass.
