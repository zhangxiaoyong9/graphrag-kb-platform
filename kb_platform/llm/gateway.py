"""FailoverGateway: ordered provider profiles + per-profile breakers.

Phase 1: single-profile pass-through with key round-robin (replaces
LoadBalancingCompletion). Phase 2 adds breaker-gated cross-profile failover by
extending the candidate-selection loop only."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from kb_platform.llm.circuit_breaker import CircuitBreaker
from kb_platform.llm.events import Done, Error, StreamEvent
from kb_platform.llm.request import ProviderConfig, build_chat_request
from kb_platform.llm.sse import parse_provider_stream


@dataclass
class ChatRequest:
    messages: list[dict[str, Any]]
    stream: bool
    response_format: Any
    params: dict[str, Any]


@dataclass
class GatewayResult:
    content: str
    usage: tuple[int, int]
    error: str | None = None


@dataclass
class _ProfileKeys:
    cfg: ProviderConfig
    keys: list[str]
    # round-robin cursor shared across calls within this profile
    _cycle: itertools.cycle = field(init=False)

    def __post_init__(self) -> None:
        self._cycle = itertools.cycle(self.keys or [self.cfg.key or ""])

    def next_key(self) -> str:
        return next(self._cycle)


class FailoverGateway:
    def __init__(
        self,
        *,
        profiles: list[ProviderConfig],
        client: httpx.AsyncClient,
        breakers: dict[int, CircuitBreaker],
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
    ) -> None:
        # group keys by provider cfg identity (index) — P1: exactly one profile
        self._pks = [_ProfileKeys(cfg=p, keys=[p.key or ""]) for p in profiles]
        self._profiles = profiles
        self._client = client
        self._breakers = breakers
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds

    # --- candidate selection (P2: breaker-gated) ---
    def _candidates(self) -> list[tuple[int, _ProfileKeys]]:
        # Admit a profile iff it has a breaker AND the breaker allows the call
        # (closed or half-open). P1 callers that pass breakers={} get an empty
        # candidate list — but NativeCompletion now always supplies breakers,
        # and the legacy empty-breakers path falls back to "all admitted" below
        # so existing single-profile tests keep working.
        if not self._breakers:
            return [(i, pk) for i, pk in enumerate(self._pks)]
        return [(i, pk) for i, pk in enumerate(self._pks)
                if i in self._breakers and self._breakers[i].allow()]

    def _on_attempt_error(self, idx: int, retriable: bool) -> None:
        cb = self._breakers.get(idx)
        if cb is not None and retriable:
            cb.record_failure()

    def _on_success(self, idx: int) -> None:
        cb = self._breakers.get(idx)
        if cb is not None:
            cb.record_success()

    # --- streaming ---
    async def astream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        last_error: str | None = None
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=True,
                response_format=req.response_format, params=req.params,
            )
            try:
                async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        self._on_attempt_error(
                            idx, retriable=resp.status_code >= 500 or resp.status_code == 429
                        )
                        continue
                    self._on_success(idx)
                    async for ev in parse_provider_stream(resp.aiter_lines()):
                        if isinstance(ev, Error):
                            self._on_attempt_error(idx, retriable=ev.retriable)
                            last_error = ev.message
                            break
                        yield ev
                        if isinstance(ev, Done):
                            return
                    else:
                        return  # stream ended cleanly
                    continue  # Error mid-stream -> try next candidate
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = str(exc)
                self._on_attempt_error(idx, retriable=True)
                continue
        yield Error(message=last_error or "all profiles failed", retriable=False)

    # --- non-streaming ---
    async def collect(self, req: ChatRequest) -> GatewayResult:
        last_error: str | None = None
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=False,
                response_format=req.response_format, params=req.params,
            )
            try:
                resp = await self._client.post(url, headers=headers, json=body)
                if resp.status_code >= 400:
                    last_error = f"HTTP {resp.status_code}"
                    self._on_attempt_error(
                        idx, retriable=resp.status_code >= 500 or resp.status_code == 429
                    )
                    continue
                obj = resp.json()
                content = ""
                choices = obj.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
                usage = obj.get("usage") or {}
                self._on_success(idx)
                return GatewayResult(
                    content=content,
                    usage=(
                        int(usage.get("prompt_tokens", 0) or 0),
                        int(usage.get("completion_tokens", 0) or 0),
                    ),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = str(exc)
                self._on_attempt_error(idx, retriable=True)
                continue
        return GatewayResult(content="", usage=(0, 0), error=last_error or "all profiles failed")

    def _cfg_with_key(self, pk: _ProfileKeys) -> ProviderConfig:
        return replace(pk.cfg, key=pk.next_key())
