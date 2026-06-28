# Copyright (c) 2024 Microsoft Corporation.
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
