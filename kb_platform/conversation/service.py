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
