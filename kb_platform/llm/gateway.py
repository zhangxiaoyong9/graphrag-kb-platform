"""FailoverGateway: ordered provider profiles + per-profile breakers.

Phase 1: single-profile pass-through with key round-robin (replaces
LoadBalancingCompletion). Phase 2 adds breaker-gated cross-profile failover by
extending the candidate-selection loop only."""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from kb_platform.llm.circuit_breaker import CircuitBreaker
from kb_platform.llm.events import Done, Error, StreamEvent, TextDelta, Usage
from kb_platform.llm.metrics import METRICS
from kb_platform.llm.observability import message_stats, response_excerpt, safe_endpoint
from kb_platform.llm.request import ProviderConfig, build_chat_request
from kb_platform.llm.sse import parse_provider_stream

logger = logging.getLogger(__name__)


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
        t0 = time.time()
        message_count, input_chars, input_hash = message_stats(req.messages)
        first_error_time: float | None = None
        ttft_recorded = False
        last_error: str | None = None
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            attempt_t0 = time.time()
            output_chars = 0
            prompt_tokens = 0
            output_tokens = 0
            logger.info(
                "llm.start provider=%s model=%s endpoint=%s attempt=%d/%d stream=true "
                "messages=%d input_chars=%d input_hash=%s",
                cfg.provider, cfg.model, safe_endpoint(_chat_endpoint(cfg)), idx + 1,
                len(self._pks), message_count, input_chars, input_hash,
            )
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=True,
                response_format=req.response_format, params=req.params,
            )
            try:
                async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        raw = (await resp.aread()).decode(errors="replace")
                        last_error = f"HTTP {resp.status_code}: {response_excerpt(raw)}"
                        retriable = resp.status_code >= 500 or resp.status_code == 429
                        self._on_attempt_error(idx, retriable=retriable)
                        logger.log(
                            logging.WARNING if retriable else logging.ERROR,
                            "llm.error provider=%s model=%s status=%d retriable=%s "
                            "duration_ms=%.0f upstream_request_id=%s response=%r",
                            cfg.provider, cfg.model, resp.status_code, retriable,
                            (time.time() - attempt_t0) * 1000,
                            resp.headers.get("x-request-id") or resp.headers.get("request-id") or "-",
                            response_excerpt(raw),
                        )
                        if retriable and first_error_time is None:
                            first_error_time = time.time()
                            logger.warning(
                                "failover: provider=%s model=%s -> %s; reason=%s",
                                cfg.provider, cfg.model, "next", last_error,
                            )
                        continue
                    self._on_success(idx)
                    async for ev in parse_provider_stream(resp.aiter_lines()):
                        if isinstance(ev, Error):
                            self._on_attempt_error(idx, retriable=ev.retriable)
                            last_error = ev.message
                            logger.error(
                                "llm.stream_error provider=%s model=%s retriable=%s "
                                "duration_ms=%.0f error=%r",
                                cfg.provider, cfg.model, ev.retriable,
                                (time.time() - attempt_t0) * 1000,
                                response_excerpt(ev.message),
                            )
                            if ev.retriable and first_error_time is None:
                                first_error_time = time.time()
                                logger.warning(
                                    "failover: provider=%s model=%s -> %s; reason=%s",
                                    cfg.provider, cfg.model, "next", last_error,
                                )
                            break
                        if isinstance(ev, TextDelta) and not ttft_recorded:
                            METRICS.record_ttft((time.time() - t0) * 1000)
                            ttft_recorded = True
                        if isinstance(ev, TextDelta):
                            output_chars += len(ev.text)
                        elif isinstance(ev, Usage):
                            prompt_tokens = ev.prompt_tokens
                            output_tokens = ev.completion_tokens
                        # Record BEFORE yielding Done: NativeCompletion._stream_chunks
                        # stops consuming the gateway once it sees Done (it returns after
                        # emitting its own finish chunk), so post-yield code would never
                        # run and streaming successes/failovers would go unrecorded.
                        if isinstance(ev, Done):
                            self._record_failover_and_success(t0, first_error_time)
                            logger.info(
                                "llm.success provider=%s model=%s stream=true duration_ms=%.0f "
                                "prompt_tokens=%d output_tokens=%d output_chars=%d",
                                cfg.provider, cfg.model, (time.time() - attempt_t0) * 1000,
                                prompt_tokens, output_tokens, output_chars,
                            )
                        yield ev
                        if isinstance(ev, Done):
                            return
                    else:
                        self._record_failover_and_success(t0, first_error_time)
                        return  # stream ended cleanly
                    continue  # Error mid-stream -> try next candidate
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = str(exc)
                self._on_attempt_error(idx, retriable=True)
                logger.warning(
                    "llm.transport_error provider=%s model=%s error_type=%s "
                    "duration_ms=%.0f error=%r",
                    cfg.provider, cfg.model, type(exc).__name__,
                    (time.time() - attempt_t0) * 1000, response_excerpt(exc),
                )
                if first_error_time is None:
                    first_error_time = time.time()
                    logger.warning(
                        "failover: provider=%s model=%s -> %s; reason=%s",
                        cfg.provider, cfg.model, "next", last_error,
                    )
                continue
        logger.error(
            "llm.exhausted profiles=%d stream=true duration_ms=%.0f last_error=%r",
            len(self._pks), (time.time() - t0) * 1000, response_excerpt(last_error),
        )
        yield Error(message=last_error or "all profiles failed", retriable=False)

    @staticmethod
    def _record_failover_and_success(t0: float, first_error_time: float | None) -> None:
        """Best-effort metrics side-effect: failover timings (if any) + success."""
        if first_error_time is not None:
            METRICS.record_failover(
                detect_ms=(first_error_time - t0) * 1000,
                recover_ms=(time.time() - first_error_time) * 1000,
            )
        METRICS.record_success()

    # --- non-streaming ---
    async def collect(self, req: ChatRequest) -> GatewayResult:
        t0 = time.time()
        message_count, input_chars, input_hash = message_stats(req.messages)
        first_error_time: float | None = None
        last_error: str | None = None
        for idx, pk in self._candidates():
            cfg = self._cfg_with_key(pk)
            attempt_t0 = time.time()
            logger.info(
                "llm.start provider=%s model=%s endpoint=%s attempt=%d/%d stream=false "
                "messages=%d input_chars=%d input_hash=%s",
                cfg.provider, cfg.model, safe_endpoint(_chat_endpoint(cfg)), idx + 1,
                len(self._pks), message_count, input_chars, input_hash,
            )
            url, headers, body = build_chat_request(
                cfg, messages=req.messages, stream=False,
                response_format=req.response_format, params=req.params,
            )
            try:
                resp = await self._client.post(url, headers=headers, json=body)
                if resp.status_code >= 400:
                    raw = resp.text
                    last_error = f"HTTP {resp.status_code}: {response_excerpt(raw)}"
                    retriable = resp.status_code >= 500 or resp.status_code == 429
                    self._on_attempt_error(idx, retriable=retriable)
                    logger.log(
                        logging.WARNING if retriable else logging.ERROR,
                        "llm.error provider=%s model=%s status=%d retriable=%s "
                        "duration_ms=%.0f upstream_request_id=%s response=%r",
                        cfg.provider, cfg.model, resp.status_code, retriable,
                        (time.time() - attempt_t0) * 1000,
                        resp.headers.get("x-request-id") or resp.headers.get("request-id") or "-",
                        response_excerpt(raw),
                    )
                    if retriable and first_error_time is None:
                        first_error_time = time.time()
                        logger.warning(
                            "failover: provider=%s model=%s -> %s; reason=%s",
                            cfg.provider, cfg.model, "next", last_error,
                        )
                    continue
                try:
                    obj = resp.json()
                except Exception as exc:  # noqa: BLE001
                    last_error = f"invalid JSON response: {exc}"
                    logger.error(
                        "llm.invalid_response provider=%s model=%s status=%d "
                        "duration_ms=%.0f content_type=%s response=%r",
                        cfg.provider, cfg.model, resp.status_code,
                        (time.time() - attempt_t0) * 1000,
                        resp.headers.get("content-type", "-"), response_excerpt(resp.text),
                    )
                    continue
                content = ""
                choices = obj.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
                usage = obj.get("usage") or {}
                self._on_success(idx)
                self._record_failover_and_success(t0, first_error_time)
                logger.info(
                    "llm.success provider=%s model=%s stream=false duration_ms=%.0f "
                    "prompt_tokens=%d output_tokens=%d output_chars=%d",
                    cfg.provider, cfg.model, (time.time() - attempt_t0) * 1000,
                    int(usage.get("prompt_tokens", 0) or 0),
                    int(usage.get("completion_tokens", 0) or 0), len(content),
                )
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
                logger.warning(
                    "llm.transport_error provider=%s model=%s error_type=%s "
                    "duration_ms=%.0f error=%r",
                    cfg.provider, cfg.model, type(exc).__name__,
                    (time.time() - attempt_t0) * 1000, response_excerpt(exc),
                )
                if first_error_time is None:
                    first_error_time = time.time()
                    logger.warning(
                        "failover: provider=%s model=%s -> %s; reason=%s",
                        cfg.provider, cfg.model, "next", last_error,
                    )
                continue
        logger.error(
            "llm.exhausted profiles=%d stream=false duration_ms=%.0f last_error=%r",
            len(self._pks), (time.time() - t0) * 1000, response_excerpt(last_error),
        )
        return GatewayResult(content="", usage=(0, 0), error=last_error or "all profiles failed")

    def _cfg_with_key(self, pk: _ProfileKeys) -> ProviderConfig:
        return replace(pk.cfg, key=pk.next_key())


def _chat_endpoint(cfg: ProviderConfig) -> str:
    """Build only for safe diagnostic display; credentials are never included."""
    url, _, _ = build_chat_request(
        cfg, messages=[], stream=False, response_format=None, params={}
    )
    return url
