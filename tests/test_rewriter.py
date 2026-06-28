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
