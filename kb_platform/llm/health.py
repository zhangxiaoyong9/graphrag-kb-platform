"""HealthProbe: a background loop that proactively drives the shared breakers.

One ``HealthProbe`` per process (started by ``bootstrap()``). Each tick it
issues a tiny (``max_tokens=1``) chat completion against every registered
endpoint and feeds the result into that endpoint's shared breaker via
``breaker_registry``: success closes a half-open breaker; failure counts toward
opening. This is how a transient ``NativeCompletion`` gateway (built per call)
ends up sharing breaker state with the long-lived probe — both look the breaker
up from the registry by endpoint identity.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from kb_platform.llm.breaker_registry import snapshot
from kb_platform.llm.request import ProviderConfig, build_chat_request

logger = logging.getLogger(__name__)

# Injectable probe function: async (cfg) -> True (ok) | False (fail).
ProbeFn = Callable[[ProviderConfig], Awaitable[bool]]


class HealthProbe:
    """Background task that probes every registered endpoint each interval."""

    def __init__(
        self,
        *,
        interval: float = 60.0,
        client: httpx.AsyncClient | None = None,
        probe_fn: ProbeFn | None = None,
    ) -> None:
        self._interval = interval
        self._client = client
        self._probe_fn = probe_fn
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def _probe_one(self, cfg: ProviderConfig) -> bool:
        if self._probe_fn is not None:
            try:
                return await self._probe_fn(cfg)
            except Exception:  # noqa: BLE001 - probe must never crash the loop
                logger.debug("probe_fn raised for %s", cfg.model, exc_info=True)
                return False

        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0)
        )
        own = self._client is None
        try:
            url, headers, body = build_chat_request(
                cfg,
                messages=[{"role": "user", "content": "ping"}],
                stream=False,
                response_format=None,
                params={"max_tokens": 1},
            )
            resp = await client.post(url, headers=headers, json=body)
            # 4xx auth/quota issues are NOT endpoint-health: treat only
            # 5xx + 429 as unhealthy (matches the gateway's retriable rule).
            return resp.status_code < 500 and resp.status_code != 429
        except (httpx.TimeoutException, httpx.TransportError):
            return False
        except Exception:  # noqa: BLE001 - probe must never crash the loop
            logger.debug("probe error for %s", cfg.model, exc_info=True)
            return False
        finally:
            if own:
                await client.aclose()

    async def tick(self) -> None:
        """Probe every registered endpoint once. Public for tests."""
        for _key, (cb, cfg) in snapshot().items():
            ok = await self._probe_one(cfg)
            if ok:
                cb.record_success()
            else:
                cb.record_failure()

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - never let the loop die
                logger.exception("HealthProbe tick raised")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval
                )
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        """Create the background task. No-op if already started."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Signal the loop to stop and cancel the task."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._stop_event = None


__all__ = ["HealthProbe", "ProbeFn"]
