# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Per-unit LLM cost capture (Phase 4 Wave 2).

A ``CostCapturingCompletion`` wraps graphrag-llm's ``LLMCompletion`` so every
``completion_async`` call records ``response.usage`` into a unit-scoped
``CostRecorder`` held in a contextvar. The worker sets a fresh recorder per
unit (asyncio tasks each get their own context copy, so concurrent units are
isolated), then reads ``recorder.to_json()`` into ``Unit.cost_json``.

Cost is computed via graphrag-llm's ``model_cost_registry``; a model absent
from the registry contributes tokens with ``estimated_cost_usd=None`` (never
raises).
"""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class _Accum:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = 0.0


@dataclass
class CostRecorder:
    """Accumulates per-model token + cost totals for one unit."""

    _by_model: dict[str, _Accum] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self._by_model)

    def add(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float | None,
    ) -> None:
        a = self._by_model.setdefault(model, _Accum(model=model))
        a.prompt_tokens += prompt_tokens
        a.completion_tokens += completion_tokens
        if cost is None:
            a.estimated_cost_usd = None
        elif a.estimated_cost_usd is not None:
            a.estimated_cost_usd += cost

    def to_json(self) -> str:
        items = []
        total = 0.0
        known = True
        for a in self._by_model.values():
            items.append(
                {
                    "model": a.model,
                    "prompt_tokens": a.prompt_tokens,
                    "completion_tokens": a.completion_tokens,
                    "estimated_cost_usd": a.estimated_cost_usd,
                }
            )
            if a.estimated_cost_usd is None:
                known = False
            else:
                total += a.estimated_cost_usd
        return json.dumps({"items": items, "total_usd": total if known else None})


_recorder_var: contextvars.ContextVar[CostRecorder | None] = contextvars.ContextVar(
    "cost_recorder", default=None
)


def current_recorder() -> CostRecorder | None:
    return _recorder_var.get()


@contextmanager
def use_recorder():
    rec = CostRecorder()
    token = _recorder_var.set(rec)
    try:
        yield rec
    finally:
        _recorder_var.reset(token)


def _compute_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    try:
        from graphrag_llm.model_cost_registry import model_cost_registry

        costs = model_cost_registry.get_model_costs(model_id)
    except Exception:  # noqa: BLE001
        return None
    if not costs:
        return None
    try:
        return prompt_tokens * float(costs.get("input_cost_per_token", 0)) + (
            completion_tokens * float(costs.get("output_cost_per_token", 0))
        )
    except Exception:  # noqa: BLE001
        return None


class CostCapturingCompletion:
    """Delegates completion calls to ``inner`` and records usage into the current recorder."""

    def __init__(self, inner, *, model_id: str) -> None:
        self._inner = inner
        self._model_id = model_id

    def _record(self, response) -> None:
        rec = current_recorder()
        if rec is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        rec.add(
            model=self._model_id,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost=_compute_cost(self._model_id, pt, ct),
        )

    async def completion_async(self, **kwargs):
        resp = await self._inner.completion_async(**kwargs)
        self._record(resp)
        return resp

    def completion(self, **kwargs):
        resp = self._inner.completion(**kwargs)
        self._record(resp)
        return resp

    def __getattr__(self, name):  # proxy anything else (e.g. model_id) to the inner
        return getattr(self._inner, name)
