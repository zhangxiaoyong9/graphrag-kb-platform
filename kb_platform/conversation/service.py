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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from kb_platform.conversation.rewriter import HistoryTurn, Rewriter
from kb_platform.query.engine import SourceRef, StreamDone, StreamDelta, StreamMeta

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """One SSE-bound event from ``send_streaming``.

    ``data`` is the JSON-serializable payload for meta/delta/error. For ``done``
    the persisted ORM ``Message`` is carried on ``message`` and serialized by the
    route (the service stays free of ``api.models``).
    """

    type: str  # "meta" | "delta" | "done" | "error"
    data: dict = field(default_factory=dict)
    message: Any = None  # ORM Message, set only for type == "done"


class ConversationService:
    def __init__(self, repo, engine, rewriter: Rewriter | None, data_root: str) -> None:
        self._repo = repo
        self._engine = engine
        self._rewriter = rewriter
        self._data_root = data_root

    async def send(
        self,
        conversation_id: int,
        content: str,
        method: str | None,
        params=None,
    ):
        conv = self._repo.get_conversation(conversation_id)
        if conv is None:
            return None

        history_rows = self._repo.recent_messages(conversation_id)
        history = [HistoryTurn(r.role, r.content) for r in history_rows]
        chosen_method = method or _last_method(history_rows) or "local"

        rewrote, rewrite_fell_back, rw_pt, rw_ot, standalone = await self._rewrite_once(
            content, history
        )

        # Persist the user message first (always).
        self._repo.add_message(conversation_id, role="user", content=content)

        result = await self._engine.search(
            chosen_method, standalone, self._data_root, params=params
        )

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

    async def _rewrite_once(self, content, history):
        """Run the rewriter if there is history. Returns
        (rewrote, rewrite_fell_back, prompt_tokens, output_tokens, standalone)."""
        if not history or self._rewriter is None:
            return False, False, 0, 0, content
        import time

        t0 = time.perf_counter()
        try:
            rr = await self._rewriter.rewrite(content, history)
            logger.info(
                "rewrite done in %.0fms -> %.60s",
                (time.perf_counter() - t0) * 1000, rr.standalone,
            )
            return True, False, rr.prompt_tokens, rr.output_tokens, rr.standalone
        except Exception:  # noqa: BLE001 - fall back to raw message, never block
            logger.exception("query rewrite failed; falling back to raw message")
            return False, True, 0, 0, content

    async def send_streaming(
        self,
        conversation_id: int,
        content: str,
        method: str | None,
        params=None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming variant of ``send``: rewrite -> meta -> persist user ->
        stream answer deltas -> persist assistant -> done|error."""
        conv = self._repo.get_conversation(conversation_id)
        if conv is None:
            return

        history_rows = self._repo.recent_messages(conversation_id)
        history = [HistoryTurn(r.role, r.content) for r in history_rows]
        chosen_method = method or _last_method(history_rows) or "local"

        rewrote, rewrite_fell_back, rw_pt, rw_ot, standalone = await self._rewrite_once(
            content, history
        )
        meta = {"method": chosen_method, "rewrite_fell_back": rewrite_fell_back}
        if rewrote:
            meta["rewritten_query"] = standalone
        yield StreamEvent("meta", meta)

        self._repo.add_message(conversation_id, role="user", content=content)

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
        if done is None:  # engine misbehaved; synthesize an error terminal
            done = StreamDone(method=chosen_method, error="stream ended without a done event")
        if done.answer:
            accumulated = done.answer  # prefer the engine's authoritative full text

        assistant = self._repo.add_message(
            conversation_id,
            role="assistant",
            content=accumulated or (done.error or ""),
            method=chosen_method,
            rewritten_query=standalone if rewrote else None,
            rewrite_fell_back=rewrite_fell_back,
            sources_json=_serialize_sources(done.sources),
            prompt_tokens=_merge_tokens(rw_pt, done.prompt_tokens),
            output_tokens=_merge_tokens(rw_ot, done.output_tokens),
            elapsed_ms=done.elapsed_ms,
            error=done.error,
            cypher=cypher,
            truncated=bool(done.truncated),
        )
        self._repo.touch_conversation(conversation_id)
        if not conv.title:
            self._repo.update_conversation_title(conversation_id, _title_from(content))

        if done.error:
            yield StreamEvent("error", {"message": done.error})
        else:
            yield StreamEvent("done", {}, message=assistant)


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
