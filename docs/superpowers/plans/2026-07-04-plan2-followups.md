# plan2 Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three gaps left by the neo4j-graph-query plan — surface generated Cypher + truncation in multi-turn chat (persisted), make `hops` + `cypher_timeout_ms` preset-persistable, and render the `truncated` row-cap flag across all result surfaces.

**Architecture:** One Alembic migration adds four nullable/defaulted columns (`message.cypher`, `message.truncated`, `query_preset.hops`, `query_preset.cypher_timeout_ms`). The conversation service's streaming event loop gains an explicit `StreamMeta` branch (it was being misclassified as `StreamDone`) and persists cypher/truncated on the assistant `Message`. On the frontend, the shared `QueryResultView` component (consumed by `QueryPage` / `QueryTestPage` / `ChatPage`) gains a `<TruncatedNotice />` banner and a collapsible Cypher `<details>`, so all three result surfaces inherit both from one edit. Presets gain the two method knobs end-to-end (DB column → CRUD model → form → apply/save), including the `cypher_timeout_ms` input that plan2 left resolver-only.

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy 2 + Alembic (backend); React + TypeScript + Vite + Tailwind + Vitest (frontend). No graphrag / graphrag-llm seam changes.

**Spec:** `docs/superpowers/specs/2026-07-04-plan2-followups-design.md`

## Global Constraints

- Backend line-length 100, target py311; lint via `uv run ruff check .` (must be clean).
- `loop="asyncio"` in any uvicorn run is unchanged; do not switch to uvloop.
- Dashboard copy is Chinese — match surrounding copy (warning color copy stays `text-[#b26b00]` on `bg-warning-soft`, matching the existing "需社区报告" badge).
- No graphrag / graphrag-llm imports added outside the sanctioned seams.
- Migration columns are nullable (or NOT NULL with `server_default`) so existing rows survive `alembic upgrade head`.
- `truncated` copy is fixed and decoupled from `ROW_CAP` (do not surface the number 1000).
- Every commit message ends with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Each task runs its own test command and commits only after green.

---

## File Structure

**Backend (create/modify):**
- Create `alembic/versions/0011_message_cypher_preset_knobs.py` — one migration, four columns across two tables.
- Modify `kb_platform/db/models_conversation.py` — `Message.cypher` + `Message.truncated`.
- Modify `kb_platform/db/models.py` — `QueryPreset.hops` + `QueryPreset.cypher_timeout_ms`.
- Modify `kb_platform/db/repository.py` — `add_message(...)` gains `cypher` / `truncated` kwargs.
- Modify `kb_platform/conversation/service.py` — streaming event loop three-branch + persist cypher/truncated.
- Modify `kb_platform/api/models.py` — `MessageOut.cypher`/`truncated`; `QueryPresetIn`/`Update`/`Out` gain `hops`/`cypher_timeout_ms`.
- Modify `kb_platform/api/routes_conversations.py` — `_message_out` passes the two new fields.
- Test: `tests/test_conversation_service.py` (extend), `tests/test_repository.py` (extend, if present — else add assertions here).

**Frontend (create/modify):**
- Create `web/src/components/TruncatedNotice.tsx` — shared amber banner.
- Modify `web/src/api/types.ts` — `QueryResult.cypher?`; `ChatMessage.cypher?`/`truncated?`; `QueryPreset.hops?`/`cypher_timeout_ms?`.
- Modify `web/src/components/QueryResultView.tsx` — render `<TruncatedNotice />` + Cypher `<details>`.
- Modify `web/src/pages/ChatPage.tsx` — capture cypher from `meta`; feed cypher/truncated into synthetic result.
- Modify `web/src/pages/QueryPage.tsx` — `cypher_timeout_ms` state/input/buildParams/applyPreset/savePreset.
- Modify `web/src/pages/QueryPresetsPage.tsx` — method `<select>` gains hybrid/cypher; form gains hops + cypher_timeout_ms; table gains a "方法旋钮" column.
- Tests: `QueryResultView.test.tsx`, `ChatPage.test.tsx`, `QueryPage.test.tsx`, `QueryPresetsPage.test.tsx`, `QueryTestPage.test.tsx`.

---

## Task 1: Schema — migration 0011 + ORM columns

**Files:**
- Modify: `kb_platform/db/models_conversation.py:29-44` (Message columns)
- Modify: `kb_platform/db/models.py:118-138` (QueryPreset columns)
- Create: `alembic/versions/0011_message_cypher_preset_knobs.py`
- Test: `tests/test_conversation_service.py` (the `_setup` helper uses `Base.metadata.create_all`, so new columns appear once the models declare them)

**Interfaces:**
- Consumes: nothing (foundation task).
- Produces: `Message.cypher` (Text nullable), `Message.truncated` (Boolean default False), `QueryPreset.hops` (Integer nullable), `QueryPreset.cypher_timeout_ms` (Integer nullable); migration `0011` whose `down_revision="0010"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversation_service.py`:

```python
def test_add_message_persists_cypher_and_truncated(tmp_path):
    """add_message persists cypher + truncated on the Message row (migration 0011 / ORM).

    Pure schema check — does NOT go through the service (that wiring is Task 2)."""
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    repo.add_message(
        cid, role="assistant", content="a",
        cypher="MATCH (n) RETURN n", truncated=True,
    )
    rows = repo.get_messages(cid)
    assistant = [r for r in rows if r.role == "assistant"][0]
    assert assistant.cypher == "MATCH (n) RETURN n"
    assert assistant.truncated is True


def test_query_preset_orm_carries_hops_and_timeout(tmp_path):
    """QueryPreset ORM accepts hops + cypher_timeout_ms (migration 0011 / ORM)."""
    repo = _setup(tmp_path)
    p = repo.create_query_preset(
        name="hyb", description="", method="hybrid",
        hops=3, cypher_timeout_ms=None,
    )
    assert p.hops == 3 and p.cypher_timeout_ms is None
    again = repo.get_query_preset(p.id)
    assert again is not None and again.hops == 3 and again.cypher_timeout_ms is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_conversation_service.py::test_add_message_persists_cypher_and_truncated tests/test_conversation_service.py::test_query_preset_orm_carries_hops_and_timeout -q`
Expected: FAIL — `Message`/`QueryPreset` have no `cypher`/`truncated`/`hops`/`cypher_timeout_ms` attributes; `add_message` does not accept `cypher`/`truncated` kwargs.

- [ ] **Step 3: Add the ORM columns**

In `kb_platform/db/models_conversation.py`, add two columns to `Message` (mirror the existing `rewrite_fell_back` style; place them after `error`):

```python
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cypher: Mapped[str | None] = mapped_column(Text, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
```

In `kb_platform/db/models.py`, add two columns to `QueryPreset` (after `system_prompt`):

```python
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    hops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cypher_timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```

- [ ] **Step 4: Create migration 0011**

Create `alembic/versions/0011_message_cypher_preset_knobs.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Add message.cypher + message.truncated + query_preset.hops + query_preset.cypher_timeout_ms.

Closes the three plan2 follow-ups:
- message.cypher/truncated persist the cypher/hybrid retrieval audit on each
  assistant turn (so reopening a conversation still shows what ran and whether
  the row cap bit).
- query_preset.hops/cypher_timeout_ms make the two method-specific knobs
  preset-persistable (hybrid -> hops, cypher -> cypher_timeout_ms).

All four columns are nullable except ``truncated`` (NOT NULL DEFAULT 0 so old
assistant rows read as not-truncated).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column("cypher", sa.Text(), nullable=True),
    )
    op.add_column(
        "message",
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "query_preset",
        sa.Column("hops", sa.Integer(), nullable=True),
    )
    op.add_column(
        "query_preset",
        sa.Column("cypher_timeout_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_preset", "cypher_timeout_ms")
    op.drop_column("query_preset", "hops")
    op.drop_column("message", "truncated")
    op.drop_column("message", "cypher")
```

- [ ] **Step 5: Add the kwargs to `add_message`**

In `kb_platform/db/repository.py`, extend the `add_message` signature and the `Message(...)` construction (around line 719-760). Add `cypher: str | None = None` and `truncated: bool = False` to the signature, and pass them to `Message(...)`:

```python
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
        cypher: str | None = None,
        truncated: bool = False,
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
                cypher=cypher,
                truncated=truncated,
            )
            s.add(m)
            s.flush()
            return m
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_conversation_service.py::test_add_message_persists_cypher_and_truncated tests/test_conversation_service.py::test_query_preset_orm_carries_hops_and_timeout -q`
Expected: PASS (Task 1 is self-green — these are pure schema/add_message checks; the service-level forwarding of cypher/truncated is Task 2).

- [ ] **Step 7: Verify the migration applies cleanly**

Run: `uv run alembic upgrade head`
Expected: exits 0, no error; then `uv run alembic current` shows `0011`.

- [ ] **Step 8: Lint**

Run: `uv run ruff check kb_platform/db/models_conversation.py kb_platform/db/models.py kb_platform/db/repository.py alembic/versions/0011_message_cypher_preset_knobs.py tests/test_conversation_service.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add kb_platform/db/models_conversation.py kb_platform/db/models.py kb_platform/db/repository.py alembic/versions/0011_message_cypher_preset_knobs.py tests/test_conversation_service.py
git commit -m "$(cat <<'EOF'
feat(db): migration 0011 — message cypher/truncated + preset hops/cypher_timeout_ms

Adds four columns (message.cypher, message.truncated NOT NULL DEFAULT 0,
query_preset.hops, query_preset.cypher_timeout_ms) and wires add_message to
accept cypher/truncated. Foundation for the plan2 follow-ups (chat Cypher
transparency, preset persistence, truncated UI).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: M1 backend — conversation service transparency + persistence

**Files:**
- Modify: `kb_platform/conversation/service.py` (import `StreamMeta`; three-branch event loop; persist cypher/truncated)
- Modify: `kb_platform/api/models.py:311-324` (`MessageOut` gains `cypher`/`truncated`)
- Modify: `kb_platform/api/routes_conversations.py:49-62` (`_message_out` passes the two fields)
- Test: `tests/test_conversation_service.py`

**Interfaces:**
- Consumes: `StreamMeta` from `kb_platform.query.engine` (Task 1 already imports it in the test); `Message.cypher`/`truncated` + `add_message` kwargs (Task 1).
- Produces: `ConversationService.send_streaming` yields a second `meta{method, cypher}` when the engine yields `StreamMeta`; the persisted assistant `Message` carries `cypher` + `truncated`; `MessageOut` (and thus the SSE `done` payload's `message`) serializes both.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversation_service.py`:

```python
async def test_send_streaming_emits_meta_cypher_and_persists(tmp_path):
    """When the engine yields StreamMeta, the service emits meta{cypher} and
    persists cypher + truncated on the assistant Message (carried in done.message)."""
    from kb_platform.query.engine import StreamDelta, StreamDone, StreamMeta

    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id

    class _CypherEngine:
        async def stream_search(self, method, query, kb_data_root, params=None):
            yield StreamMeta(cypher="MATCH (n) RETURN count(n)")
            yield StreamDelta(text="one ")
            yield StreamDelta(text="two")
            yield StreamDone(answer="one two", method=method, truncated=True)

    svc = ConversationService(repo, _CypherEngine(), None, data_root=".")
    events = await _drain(svc.send_streaming(cid, "how many?", "cypher"))

    metas = [e for e in events if e.type == "meta"]
    assert len(metas) == 2
    assert metas[0].data["method"] == "cypher"  # leading meta unchanged
    assert metas[1].data == {"method": "cypher", "cypher": "MATCH (n) RETURN count(n)"}

    done = next(e for e in events if e.type == "done")
    assert done.message.cypher == "MATCH (n) RETURN count(n)"
    assert done.message.truncated is True

    rows = repo.get_messages(cid)
    assistant = [r for r in rows if r.role == "assistant"][0]
    assert assistant.cypher == "MATCH (n) RETURN count(n)"
    assert assistant.truncated is True


async def test_send_streaming_without_meta_omits_cypher_and_not_truncated(tmp_path):
    """Engines that never yield StreamMeta (graphrag/Fake) emit only the leading
    meta, and the persisted row has cypher=None / truncated=False."""
    repo = _setup(tmp_path)
    cid = repo.create_conversation(1).id
    svc = ConversationService(repo, FakeQueryEngine(), None, data_root=".")
    events = await _drain(svc.send_streaming(cid, "hi", "local"))
    metas = [e for e in events if e.type == "meta"]
    assert len(metas) == 1 and "cypher" not in metas[0].data
    done = next(e for e in events if e.type == "done")
    assert done.message.cypher is None and done.message.truncated is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conversation_service.py::test_send_streaming_emits_meta_cypher_and_persists tests/test_conversation_service.py::test_send_streaming_without_meta_omits_cypher_and_not_truncated -q`
Expected: FAIL — the service swallows `StreamMeta` (no second meta event) and forwards no cypher/truncated.

- [ ] **Step 3: Fix the service event loop + persist cypher/truncated**

In `kb_platform/conversation/service.py`:

Update the import line (currently `from kb_platform.query.engine import SourceRef, StreamDone, StreamDelta`):

```python
from kb_platform.query.engine import SourceRef, StreamDone, StreamDelta, StreamMeta
```

Replace the event loop (currently `done: StreamDone | None = None` through the `else: # StreamDone` branch) with a three-branch loop and a captured `cypher`:

```python
        accumulated = ""
        done: StreamDone | None = None
        cypher: str | None = None
        async for ev in self._engine.stream_search(
            chosen_method, standalone, self._data_root, params=params
        ):
            if isinstance(ev, StreamDelta):
                accumulated += ev.text
                yield StreamEvent("delta", {"text": ev.text})
            elif isinstance(ev, StreamMeta):
                cypher = ev.cypher
                yield StreamEvent("meta", {"method": chosen_method, "cypher": ev.cypher})
            else:  # StreamDone
                done = ev
```

Then in the `add_message(...)` call for the assistant row, add the two kwargs (after `error=done.error,`):

```python
            error=done.error,
            cypher=cypher,
            truncated=bool(done.truncated),
        )
```

- [ ] **Step 4: Extend `MessageOut`**

In `kb_platform/api/models.py`, add two fields to `MessageOut` (after `error`):

```python
    elapsed_ms: float | None = None
    error: str | None = None
    cypher: str | None = None
    truncated: bool = False
```

- [ ] **Step 5: Extend `_message_out`**

In `kb_platform/api/routes_conversations.py`, extend the `_message_out` builder (add the two fields after `error=m.error,`):

```python
        elapsed_ms=m.elapsed_ms,
        error=m.error,
        cypher=m.cypher,
        truncated=m.truncated,
    )
```

- [ ] **Step 6: Run the conversation-service suite**

Run: `uv run pytest tests/test_conversation_service.py -q`
Expected: PASS — the two new streaming tests pass (service now yields `meta{cypher}` and persists cypher/truncated), alongside the existing streaming tests and Task 1's schema tests (which were already green).

- [ ] **Step 7: Lint**

Run: `uv run ruff check kb_platform/conversation/service.py kb_platform/api/models.py kb_platform/api/routes_conversations.py tests/test_conversation_service.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add kb_platform/conversation/service.py kb_platform/api/models.py kb_platform/api/routes_conversations.py tests/test_conversation_service.py
git commit -m "$(cat <<'EOF'
feat(chat): surface Cypher + truncated in multi-turn conversation stream

The ConversationService event loop now has an explicit StreamMeta branch
(previously StreamMeta fell into the else=StreamDone bucket and was swallowed).
It yields a second meta{method, cypher} event mirroring routes_query, and
persists cypher + truncated on the assistant Message (carried in the done
payload via MessageOut). graphrag/Fake engines still emit only the leading meta.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: M3 + shared frontend — TruncatedNotice + QueryResultView rendering

**Files:**
- Create: `web/src/components/TruncatedNotice.tsx`
- Modify: `web/src/api/types.ts:80-91` (`QueryResult` gains `cypher?`)
- Modify: `web/src/components/QueryResultView.tsx` (render notice + cypher details)
- Test: `web/src/components/QueryResultView.test.tsx`
- Test: `web/src/pages/QueryPage.test.tsx`, `web/src/pages/QueryTestPage.test.tsx` (assert notice on `truncated:true`)

**Interfaces:**
- Consumes: `QueryResult.truncated` (already on the type), gains `QueryResult.cypher`.
- Produces: `<TruncatedNotice />` (default export-free named export); `QueryResultView` renders the notice (top, when `result.truncated`) and a collapsible Cypher block (bottom, when `result.cypher`). All three pages inherit since they already render `<QueryResultView result={...} />` and their result objects already carry `truncated` (single-shot) or will carry it (chat, Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `web/src/components/QueryResultView.test.tsx`:

```tsx
test("renders the truncated notice when result.truncated is true", () => {
  render(<MemoryRouter><QueryResultView result={{ ...r, truncated: true }} /></MemoryRouter>);
  expect(screen.getByText(/结果已达行数上限/)).toBeInTheDocument();
});

test("omits the truncated notice when result.truncated is falsy", () => {
  render(<MemoryRouter><QueryResultView result={r} /></MemoryRouter>);
  expect(screen.queryByText(/结果已达行数上限/)).not.toBeInTheDocument();
});

test("renders Cypher in a collapsible details when result.cypher is set", () => {
  render(<MemoryRouter><QueryResultView result={{ ...r, cypher: "MATCH (n) RETURN n" }} /></MemoryRouter>);
  // <summary> is always visible — proves the section rendered because cypher was set.
  expect(screen.getByText("生成的 Cypher")).toBeInTheDocument();
  // expand and confirm the cypher body is present
  fireEvent.click(screen.getByText("生成的 Cypher"));
  expect(screen.getByText("MATCH (n) RETURN n")).toBeInTheDocument();
});

test("omits the Cypher section when result.cypher is absent", () => {
  render(<MemoryRouter><QueryResultView result={r} /></MemoryRouter>);
  expect(screen.queryByText("生成的 Cypher")).not.toBeInTheDocument();
});
```

Add the `fireEvent` import at the top of the file:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/components/QueryResultView.test.tsx`
Expected: FAIL — `<TruncatedNotice />` text not found; `QueryResult` has no `cypher` (the `{...r, cypher: ...}` literal will type-error under `tsc`/vitest).

- [ ] **Step 3: Add `cypher?` to the `QueryResult` type**

In `web/src/api/types.ts`, add `cypher` to `QueryResult` (after `truncated?`):

```ts
export interface QueryResult {
  answer: string;
  method: string;
  error: string | null;
  elapsed_ms?: number;
  prompt_tokens?: number;
  output_tokens?: number;
  llm_calls?: number;
  sources?: SourceRef[];
  truncated?: boolean;
  cypher?: string | null;
}
```

- [ ] **Step 4: Create `TruncatedNotice`**

Create `web/src/components/TruncatedNotice.tsx`:

```tsx
/** Amber row-cap notice shared by all result surfaces (rendered inside QueryResultView).
 * Copy is fixed and deliberately decoupled from the backend ROW_CAP constant. */
export function TruncatedNotice() {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-warning-soft px-3 py-2 text-[12px] text-[#b26b00]">
      <span>结果已达行数上限，已截断。可缩小范围或调整上限。</span>
    </div>
  );
}
```

- [ ] **Step 5: Render notice + Cypher details inside `QueryResultView`**

In `web/src/components/QueryResultView.tsx`, add the import:

```tsx
import { Badge } from "./ui";
import { IconClock, IconWarn } from "./icons";
import { TruncatedNotice } from "./TruncatedNotice";
```

Then inside the returned `<div className="space-y-3">`, render the notice immediately after the error block, and append a Cypher `<details>` after the sources block. The full returned JSX becomes:

```tsx
  return (
    <div className="space-y-3">
      {result.error && (
        <div className="flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          <IconWarn width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{result.error}</span>
        </div>
      )}

      {result.truncated && <TruncatedNotice />}

      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
        <Badge tone="brand">{result.method}</Badge>
        {elapsed != null && (
          <span className="flex items-center gap-1 nums">
            <IconClock width={13} height={13} /> {Math.round(elapsed)} ms
          </span>
        )}
        {hasTokens ? (
          <span className="nums">
            {result.prompt_tokens ?? 0} prompt · {result.output_tokens ?? 0} output
            {result.llm_calls ? ` · ${result.llm_calls} 次调用` : ""}
          </span>
        ) : null}
      </div>

      {result.sources && result.sources.length > 0 && (
        <div>
          <p className="mb-1.5 text-[12px] font-medium text-body">引用与来源</p>
          {entities.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {entities.map((e, i) => (
                <SourceChip key={`e-${i}-${e.name}`} s={e} />
              ))}
            </div>
          )}
          {texts.length > 0 && (
            <ul className="space-y-1.5">
              {texts.map((t, i) => (
                <li
                  key={`t-${i}`}
                  className="rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[12px] leading-relaxed text-body"
                >
                  {t.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {result.cypher && (
        <details className="rounded-lg border border-line bg-surface-2/60 px-3 py-2">
          <summary className="cursor-pointer text-[12px] font-medium text-body">生成的 Cypher</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-ink/80">
            {result.cypher}
          </pre>
        </details>
      )}
    </div>
  );
```

- [ ] **Step 6: Run the component tests to verify they pass**

Run: `cd web && npx vitest run src/components/QueryResultView.test.tsx`
Expected: PASS (all four new tests + the two existing ones).

- [ ] **Step 7: Assert the notice appears on the single-shot pages**

The single-shot pages already carry `truncated` on their `result` (backend `done` payload). Append to `web/src/pages/QueryPage.test.tsx` (inside the existing test file, mirroring its SSE-generator style — locate the generator that yields `meta`/`delta`/`done` and make its `done` payload set `truncated: true`, then assert):

```tsx
test("shows the truncated notice when the done result is truncated", async () => {
  server.use(
    http.post("/kbs/:id/query", () =>
      new HttpResponse(
       sseBody([
        { event: "meta", data: { method: "hybrid" } },
        { event: "delta", data: { text: "ans" } },
        { event: "done", data: { result: { answer: "ans", method: "hybrid", error: null, truncated: true } } },
      ]),
      { headers: { "content-type": "text/event-stream" } },
    ),
  );
  render(<MemoryRouter><QueryPage /></MemoryRouter>);
  // ...fire the query the way the existing tests do, then:
  expect(await screen.findByText(/结果已达行数上限/)).toBeInTheDocument();
});
```

> **Note for the implementer:** open `web/src/pages/QueryPage.test.tsx` and read the existing happy-path test to copy its exact SSE-mock helper (`sseBody` / generator + `parseSse` shape) and KB-loading mock. Reuse that helper verbatim; only the `done` payload changes (`truncated: true`). Do not invent a new mock mechanism. Apply the same pattern in `QueryTestPage.test.tsx`.

Run: `cd web && npx vitest run src/pages/QueryPage.test.tsx src/pages/QueryTestPage.test.tsx`
Expected: PASS.

- [ ] **Step 8: Type-check + build**

Run: `cd web && npm run build`
Expected: `tsc -b && vite build` succeeds (the new `cypher?` field and component are well-typed).

- [ ] **Step 9: Commit**

```bash
git add web/src/components/TruncatedNotice.tsx web/src/components/QueryResultView.tsx web/src/components/QueryResultView.test.tsx web/src/api/types.ts web/src/pages/QueryPage.test.tsx web/src/pages/QueryTestPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(ui): render truncated notice + Cypher details in QueryResultView

New shared <TruncatedNotice/> (amber, decoupled copy) renders at the top of
QueryResultView when result.truncated; a collapsible Cypher <details> renders
at the bottom when result.cypher. All three result pages (QueryPage /
QueryTestPage / ChatPage) inherit via QueryResultView. QueryResult type gains
optional cypher.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: M1 frontend — ChatPage feeds cypher/truncated into the result

**Files:**
- Modify: `web/src/api/types.ts` (`ChatMessage` gains `cypher?`/`truncated?`)
- Modify: `web/src/pages/ChatPage.tsx` (meta handler captures cypher; synthetic `result` carries cypher/truncated)
- Test: `web/src/pages/ChatPage.test.tsx`

**Interfaces:**
- Consumes: backend SSE `meta{cypher}` + `done{message:{cypher, truncated}}` (Task 2); `<QueryResultView>` rendering of cypher/truncated (Task 3).
- Produces: a `ChatMessage` whose `cypher`/`truncated` reach `QueryResultView`, so multi-turn chat shows both live (via meta) and on reload (via the persisted message).

- [ ] **Step 1: Write the failing test**

Append to `web/src/pages/ChatPage.test.tsx` (mirror the existing test's `parseSse` mock — read the file first and reuse its generator helper; the new bits are the `meta{cypher}` event and the `done` message carrying `cypher` + `truncated`):

```tsx
test("renders Cypher + truncated notice from the streamed meta and done message", async () => {
  // Reuse the existing ChatPage test's KB + conversation + sendMessage mocks.
  // Stream: leading meta{method, rewrite_fell_back} -> meta{method, cypher} ->
  //         delta{text} -> done{message:{..., cypher, truncated:true}}.
  // ...wire the mocks like the existing happy-path test, with these events:
  yield { event: "meta", data: { method: "cypher", rewrite_fell_back: false } };
  yield { event: "meta", data: { method: "cypher", cypher: "MATCH (n) RETURN n" } };
  yield { event: "delta", data: { text: "answer" } };
  yield { event: "done", data: { message: { /* same shape the existing test uses, plus: */
        ...existingMessageFields, cypher: "MATCH (n) RETURN n", truncated: true } } };

  render(<MemoryRouter><ChatPage /></MemoryRouter>);
  // ...select KB + conversation + ask, the way the existing test does:
  expect(await screen.findByText(/生成的 Cypher/)).toBeInTheDocument();
  expect(await screen.findByText(/结果已达行数上限/)).toBeInTheDocument();
});
```

> **Note for the implementer:** the snippet above is a shape reference. Open `ChatPage.test.tsx`, copy the full happy-path test (its KB list mock, conversation mock, `sendMessage` SSE mock, and the user interactions that select a KB + send a question), and produce a second test that differs only in the streamed events (add the second `meta{cypher}` and put `cypher` + `truncated:true` on the `done` message). Do not invent new mock plumbing.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run src/pages/ChatPage.test.tsx`
Expected: FAIL — "生成的 Cypher" not found (ChatPage drops cypher; synthetic result lacks it).

- [ ] **Step 3: Extend `ChatMessage`**

In `web/src/api/types.ts`, locate `ChatMessage` and add (next to its existing `rewritten_query?`/`method?` fields):

```ts
  cypher?: string | null;
  truncated?: boolean;
```

- [ ] **Step 4: Capture cypher from `meta`**

In `web/src/pages/ChatPage.tsx`, extend the `meta` branch of the SSE loop so a cypher-bearing meta writes `cypher` onto the pending message (alongside the existing `rewritten_query` handling):

```tsx
        if (ev.event === "meta") {
          rewritten = ev.data.rewritten_query;
          if (rewritten) {
            setMessages((m) =>
              m.map((msg) =>
                msg.id === pendingId ? { ...msg, rewritten_query: rewritten } : msg,
              ),
            );
          }
          if (ev.data.cypher) {
            setMessages((m) =>
              m.map((msg) =>
                msg.id === pendingId ? { ...msg, cypher: ev.data.cypher } : msg,
              ),
            );
          }
        } else if (ev.event === "delta") {
```

(The `done` branch needs no change: `ev.data.message` now carries `cypher`/`truncated` from `MessageOut`, and `{...persisted, elapsed_ms}` already merges them.)

- [ ] **Step 5: Feed cypher/truncated into the synthetic `result`**

In `ChatPage.tsx`, locate the `<QueryResultView result={{...}} />` call inside the message render (around line 357-367) and add the two fields to the result object:

```tsx
            <QueryResultView
              result={{
                answer: m.content,
                method: m.method ?? "local",
                error: m.error ?? null,
                elapsed_ms: m.elapsed_ms ?? undefined,
                prompt_tokens: m.prompt_tokens ?? undefined,
                output_tokens: m.output_tokens ?? undefined,
                sources: m.sources as SourceRef[] | undefined,
                cypher: m.cypher,
                truncated: m.truncated,
              }}
            />
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd web && npx vitest run src/pages/ChatPage.test.tsx`
Expected: PASS.

- [ ] **Step 7: Type-check + build**

Run: `cd web && npm run build`
Expected: succeeds.

- [ ] **Step 8: Commit**

```bash
git add web/src/api/types.ts web/src/pages/ChatPage.tsx web/src/pages/ChatPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(chat): feed cypher/truncated into the chat result view

ChatMessage gains optional cypher/truncated. ChatPage captures cypher from the
live meta{cypher} event onto the pending message, and passes cypher/truncated
into the synthetic result it hands to QueryResultView — so multi-turn chat
shows the generated Cypher + the truncated notice both live and after reload
(via the persisted Message in done.message).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: M2 backend — preset CRUD models carry hops + cypher_timeout_ms

**Files:**
- Modify: `kb_platform/api/models.py:334-365` (`QueryPresetIn`/`Update`/`Out`)
- Test: `tests/test_api_query_presets.py` (create if absent; else extend)

**Interfaces:**
- Consumes: `QueryPreset.hops`/`cypher_timeout_ms` ORM columns (Task 1); the CRUD already does `QueryPreset(**fields)` / `setattr`, so once the pydantic models accept the fields they flow through unchanged.
- Produces: `POST /query-presets` and `PATCH /query-presets/{id}` accept `hops` + `cypher_timeout_ms`; `GET` returns them.

- [ ] **Step 1: Locate or create the preset CRUD test**

Run: `ls tests/ | grep -i preset`
If `tests/test_api_query_presets.py` exists, open it and note its create-then-read pattern. If absent, create it now:

```python
"""Query preset CRUD round-trip via the API."""
import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "p.db"), data_root=str(tmp_path))
    with TestClient(app) as c:
        yield c


def _list(client):
    return client.get("/query-presets").json()
```

> **Note for the implementer:** if a fixture/helper with a different name already exists in the file, reuse it; only the new test below is the deliverable.

- [ ] **Step 2: Write the failing test**

Append:

```python
def test_preset_round_trips_hops_and_cypher_timeout(client):
    body = {
        "name": "hyb3",
        "description": "",
        "method": "hybrid",
        "hops": 3,
        "cypher_timeout_ms": None,
    }
    r = client.post("/query-presets", json=body)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["hops"] == 3 and created["cypher_timeout_ms"] is None

    # GET list reflects it
    match = [p for p in _list(client) if p["name"] == "hyb3"][0]
    assert match["hops"] == 3 and match["cypher_timeout_ms"] is None

    # PATCH updates cypher_timeout_ms
    pid = created["id"]
    upd = client.patch(f"/query-presets/{pid}", json={"cypher_timeout_ms": 8000})
    assert upd.status_code == 200, upd.text
    assert upd.json()["cypher_timeout_ms"] == 8000 and upd.json()["hops"] == 3
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_api_query_presets.py::test_preset_round_trips_hops_and_cypher_timeout -q`
Expected: FAIL — the pydantic models reject `hops`/`cypher_timeout_ms` (extra fields, or they're dropped so the assertion fails).

- [ ] **Step 4: Extend the three preset models**

In `kb_platform/api/models.py`, add `hops` + `cypher_timeout_ms` to `QueryPresetIn`, `QueryPresetUpdate`, and `QueryPresetOut` (after `system_prompt` in each):

```python
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_api_query_presets.py -q`
Expected: PASS.

- [ ] **Step 6: Lint**

Run: `uv run ruff check kb_platform/api/models.py tests/test_api_query_presets.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/api/models.py tests/test_api_query_presets.py
git commit -m "$(cat <<'EOF'
feat(presets): persist hops + cypher_timeout_ms through preset CRUD

QueryPresetIn/Update/Out accept hops (hybrid) + cypher_timeout_ms (cypher).
The repository CRUD already passes **fields through to the ORM, so the new
columns (migration 0011) round-trip with no DAO change.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: M2 frontend — preset form/table + QueryPage cypher_timeout_ms knob

**Files:**
- Modify: `web/src/api/types.ts` (`QueryPreset` gains `hops?`/`cypher_timeout_ms?`)
- Modify: `web/src/pages/QueryPresetsPage.tsx` (`BLANK` + `toDraft` + method `<select>` options + form inputs + table column)
- Modify: `web/src/pages/QueryPage.tsx` (`cypherTimeoutMs` state + buildParams + applyPreset + savePreset + tuning input)
- Test: `web/src/pages/QueryPresetsPage.test.tsx`, `web/src/pages/QueryPage.test.tsx`

**Interfaces:**
- Consumes: preset CRUD with hops/cypher_timeout_ms (Task 5); `QueryParams.cypher_timeout_ms` already in the `_FIELDS` resolver.
- Produces: a hybrid preset saves/restores `hops`; a cypher preset saves/restores `cypher_timeout_ms`; the QueryPage tuning panel exposes a `cypher_timeout_ms` input for the cypher method.

- [ ] **Step 1: Write the failing preset test**

Append to `web/src/pages/QueryPresetsPage.test.tsx`:

```tsx
test("saving a hybrid preset sends hops", async () => {
  const captured: any[] = [];
  server.use(
    http.get("/query-presets", () => HttpResponse.json([BUILTIN])),
    http.post("/query-presets", async ({ request }) => {
      captured.push(await request.json());
      return HttpResponse.json({ id: 9, is_builtin: false, ...(await request.json()) });
    }),
  );
  render(<QueryPresetsPage />);
  await screen.findByPlaceholderText("名称");
  fireEvent.change(screen.getByPlaceholderText("名称"), { target: { value: "hyb" } });
  fireEvent.change(screen.getByPlaceholderText("hops(可空,hybrid)"), { target: { value: "3" } });
  // select hybrid method
  fireEvent.change(screen.getByDisplayValue("local"), { target: { value: "hybrid" } });
  fireEvent.click(screen.getByRole("button", { name: /新建/ }));
  await waitFor(() => expect(captured.length).toBe(1));
  expect(captured[0].method).toBe("hybrid");
  expect(captured[0].hops).toBe(3);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run src/pages/QueryPresetsPage.test.tsx`
Expected: FAIL — no hops input; `hops` not sent.

- [ ] **Step 3: Extend the `QueryPreset` type**

In `web/src/api/types.ts`, add to `QueryPreset`:

```ts
  hops?: number | null;
  cypher_timeout_ms?: number | null;
```

- [ ] **Step 4: Update `QueryPresetsPage` — `BLANK`, `toDraft`, method select, form, table**

In `web/src/pages/QueryPresetsPage.tsx`:

Add the two fields to `BLANK` and `toDraft`:

```ts
const BLANK: Draft = {
  name: "",
  description: "",
  method: "local",
  community_level: null,
  response_type: null,
  top_k: null,
  temperature: null,
  system_prompt: null,
  hops: null,
  cypher_timeout_ms: null,
};

const toDraft = (p: QueryPreset): Draft => ({
  name: p.name,
  description: p.description,
  method: p.method,
  community_level: p.community_level,
  response_type: p.response_type,
  top_k: p.top_k,
  temperature: p.temperature,
  system_prompt: p.system_prompt,
  hops: p.hops,
  cypher_timeout_ms: p.cypher_timeout_ms,
});
```

Add `hybrid` + `cypher` options to the method `<select>`:

```tsx
            <select
              className="select"
              value={draft.method}
              onChange={(e) => setDraft({ ...draft, method: e.target.value })}
            >
              <option value="local">local</option>
              <option value="global">global</option>
              <option value="drift">drift</option>
              <option value="basic">basic</option>
              <option value="hybrid">hybrid</option>
              <option value="cypher">cypher</option>
            </select>
```

Add two conditionally-rendered inputs inside the form grid (after the `temperature` input):

```tsx
            {draft.method === "hybrid" && (
              <input
                className="input"
                type="number"
                min={1}
                max={5}
                placeholder="hops(可空,hybrid)"
                value={draft.hops ?? ""}
                onChange={(e) => setDraft({ ...draft, hops: e.target.value ? Number(e.target.value) : null })}
              />
            )}
            {draft.method === "cypher" && (
              <input
                className="input"
                type="number"
                min={1000}
                placeholder="cypher_timeout_ms(可空,cypher)"
                value={draft.cypher_timeout_ms ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, cypher_timeout_ms: e.target.value ? Number(e.target.value) : null })
                }
              />
            )}
```

Replace the standalone `temperature` table column with a combined "方法旋钮" column. In the `<thead>`:

```tsx
                <tr>
                  <th className="py-2">名称</th>
                  <th>method</th>
                  <th>community_level</th>
                  <th>response_type</th>
                  <th>top_k</th>
                  <th>temperature</th>
                  <th>方法旋钮</th>
                  <th></th>
                </tr>
```

And the matching `<td>` row (after the temperature cell):

```tsx
                    <td>{p.temperature ?? "—"}</td>
                    <td className="font-mono text-[12px] text-muted">
                      {p.method === "hybrid" && p.hops != null
                        ? `hops=${p.hops}`
                        : p.method === "cypher" && p.cypher_timeout_ms != null
                        ? `timeout=${p.cypher_timeout_ms}ms`
                        : "—"}
                    </td>
```

- [ ] **Step 5: Run the preset tests to verify they pass**

Run: `cd web && npx vitest run src/pages/QueryPresetsPage.test.tsx`
Expected: PASS (new test + all existing ones).

- [ ] **Step 6: Write the failing QueryPage test**

Append to `web/src/pages/QueryPage.test.tsx` a test that selects the `cypher` method, types a `cypher_timeout_ms`, submits, and asserts the request body carries it. Read the existing happy-path test first and reuse its KB + `apiQuery` mock + interaction pattern:

```tsx
test("cypher method sends cypher_timeout_ms", async () => {
  const captured: any[] = [];
  server.use(
    http.post("/kbs/:id/query", async ({ request }) => {
      captured.push(Object.fromEntries(new URLSearchParams(await request.text()).entries()));
      return new HttpResponse(
        // reuse the existing test's SSE body helper
        sseBody([
          { event: "meta", data: { method: "cypher" } },
          { event: "done", data: { result: { answer: "a", method: "cypher", error: null } } },
        ]),
        { headers: { "content-type": "text/event-stream" } },
      );
    }),
  );
  render(<MemoryRouter><QueryPage /></MemoryRouter>);
  // ...load KB + select cypher method + fill cypher_timeout_ms, mirroring existing tests:
  // fireEvent.click(screen.getByRole("button", { name: /cypher/i })) or however method is chosen
  // fireEvent.change(screen.getByLabelText("cypher_timeout_ms"), { target: { value: "8000" } })
  // ...submit the query the way the existing test does
  await waitFor(() => expect(captured.length).toBe(1));
  expect(captured[0].cypher_timeout_ms).toBe("8000");
});
```

> **Note for the implementer:** the snippet is a shape reference. Open `QueryPage.test.tsx`, copy the existing submit-query test, and produce a variant that (a) selects the `cypher` method, (b) fills the new `cypher_timeout_ms` input, and (c) asserts the request param. The request body format (JSON vs form) must match whatever `apiQuery` in `web/src/api/client.ts` actually sends — read it first and assert accordingly.

- [ ] **Step 7: Run it to verify it fails**

Run: `cd web && npx vitest run src/pages/QueryPage.test.tsx`
Expected: FAIL — no `cypher_timeout_ms` input / not sent.

- [ ] **Step 8: Wire `cypher_timeout_ms` into `QueryPage`**

In `web/src/pages/QueryPage.tsx`:

Add state next to `const [hops, setHops] = useState("");`:

```tsx
  const [hops, setHops] = useState("");
  const [cypherTimeoutMs, setCypherTimeoutMs] = useState("");
```

Add to the `params` memo (after the `hops.trim()` line):

```tsx
    if (hops.trim()) p.hops = Number(hops);
    if (cypherTimeoutMs.trim()) p.cypher_timeout_ms = Number(cypherTimeoutMs);
```

…and add `cypherTimeoutMs` to the memo's dependency array:

```tsx
  }, [cl, rt, topK, hops, cypherTimeoutMs, temp, sysPrompt]);
```

In `applyPreset`, add (after the `setHops` line):

```tsx
    setHops(p.hops != null ? String(p.hops) : "");
    setCypherTimeoutMs(p.cypher_timeout_ms != null ? String(p.cypher_timeout_ms) : "");
```

In `savePreset`, add the two fields to the `createQueryPreset` body:

```tsx
    await createQueryPreset({
      name, description: "", method,
      community_level: cl ? Number(cl) : null,
      response_type: rt || null,
      top_k: topK ? Number(topK) : null,
      temperature: temp ? Number(temp) : null,
      system_prompt: sysPrompt || null,
      hops: hops ? Number(hops) : null,
      cypher_timeout_ms: cypherTimeoutMs ? Number(cypherTimeoutMs) : null,
    });
```

Add the input to the tuning panel, right after the `{method === "hybrid" && (...)}` hops block:

```tsx
                  {method === "cypher" && (
                    <label className="text-[12px] text-muted">cypher_timeout_ms
                      <input className="input mt-1" type="number" min={1000} value={cypherTimeoutMs}
                        aria-label="cypher_timeout_ms"
                        onChange={(e) => setCypherTimeoutMs(e.target.value)} placeholder="留空=10000" />
                    </label>
                  )}
```

- [ ] **Step 9: Run the QueryPage tests to verify they pass**

Run: `cd web && npx vitest run src/pages/QueryPage.test.tsx`
Expected: PASS.

- [ ] **Step 10: Type-check + build**

Run: `cd web && npm run build`
Expected: succeeds.

- [ ] **Step 11: Commit**

```bash
git add web/src/api/types.ts web/src/pages/QueryPresetsPage.tsx web/src/pages/QueryPresetsPage.test.tsx web/src/pages/QueryPage.tsx web/src/pages/QueryPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(presets): hops + cypher_timeout_ms end-to-end in the dashboard

QueryPresetsPage: method select gains hybrid/cypher; form gains hops (hybrid)
and cypher_timeout_ms (cypher) inputs; table gains a 方法旋钮 column.
QueryPage: adds a cypher_timeout_ms tuning input (cypher method), wires it
into buildParams/applyPreset/savePreset (hops was already applied on load but
not saved — now saved too).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Integration gate — full suites + build + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Full backend suite + lint**

Run: `uv run ruff check . && uv run pytest -q`
Expected: ruff clean; all tests PASS.

- [ ] **Step 2: Migration up/down round-trip**

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: all three commands exit 0; `alembic current` shows `0011` at the end.

- [ ] **Step 3: Frontend suite + build**

Run: `cd web && npm test && npm run build`
Expected: vitest all green; `tsc -b && vite build` succeeds.

- [ ] **Step 4: Manual smoke (optional, no LLM/key needed for Fake paths)**

If a FakeGraphAdapter / FakeQueryEngine harness is handy, start the API server + open the dashboard and confirm:
- QueryPresetsPage: create a hybrid preset with hops=3 → it appears in the table as `hops=3`; edit it → hops prefilled.
- QueryPage: choose `cypher` → the `cypher_timeout_ms` input appears; choose `hybrid` → `hops` appears.
- (Cypher/truncated live rendering needs a real Neo4j + LLM profile; skip if unavailable — covered by automated tests.)

- [ ] **Step 5: Final commit (if any fixups)**

Only if Steps 1-3 surfaced fixups. Otherwise this task is verification-only and needs no commit.

---

## Self-Review

**Spec coverage** — every spec requirement maps to a task:

- Migration: `message.cypher`/`truncated` + `query_preset.hops`/`cypher_timeout_ms` → **Task 1**.
- M1 service three-branch loop + `meta{cypher}` + persist cypher/truncated → **Task 2**.
- M1 `MessageOut` + `_message_out` carry the two fields → **Task 2** (Steps 4-5).
- M1 frontend `ChatMessage` + ChatPage meta/synthetic-result → **Task 4**.
- M2 `QueryPresetIn/Update/Out` → **Task 5**.
- M2 frontend preset form/table/method-select → **Task 6** (Steps 3-5).
- M2 `QueryPage` `cypher_timeout_ms` input + applyPreset/savePreset (incl. saving `hops`) → **Task 6** (Steps 6-8).
- M3 shared `<TruncatedNotice/>` + `QueryResultView` rendering + `QueryResult.cypher` → **Task 3**.
- M3 single-shot pages show the notice → **Task 3** Step 7 (their `result` already carries `truncated`).

**Placeholder scan:** every code step shows complete code. Three explicit "implementer note" callouts (Task 3 Step 7, Task 4 Step 1, Task 6 Step 6) instruct reading the existing test to copy its SSE-mock helper verbatim rather than restating it — these are concrete locate-and-reuse instructions, not hand-waves, because restating an unseen helper risks divergence.

**Type consistency:** `cypher: str | None` / `truncated: bool` are identical across the `Message` model (Task 1), `add_message` kwargs (Task 1), `MessageOut` (Task 2), `_message_out` (Task 2), `ChatMessage` (Task 4), and `QueryResult` (Task 3, `cypher` only — `truncated` pre-exists). `hops: int | None` / `cypher_timeout_ms: int | None` are identical across `QueryPreset` ORM (Task 1), the three pydantic CRUD models (Task 5), and the frontend `QueryPreset` type (Task 6). The `<TruncatedNotice />` named export matches between its definition (Task 3 Step 4) and its import in `QueryResultView` (Task 3 Step 5). The `meta{method, cypher}` shape matches between the service (Task 2 Step 3), the ChatPage meta handler (Task 4 Step 4), and the ChatPage test (Task 4 Step 1).

**graphrag-isolation seam:** no task imports graphrag or graphrag-llm. The conversation service change is purely control-plane (event routing + persistence). No engine/query-protocol change.

**Scope:** six implementation tasks + one verification gate; each task is independently testable with its own green-bar commit. Tasks 2-6 each depend only on Task 1 (schema) and, for Task 4, on Task 3 (QueryResultView rendering) — the linear ordering respects that.
