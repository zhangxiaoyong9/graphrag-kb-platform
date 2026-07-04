"""Per-profile circuit breaker: closed -> open (N consecutive failures) ->
half-open (after TTL) -> closed on success / open on failure.

Relaxed half-open: while half-open, ``allow()`` admits requests (the first to
succeed closes the breaker). This avoids cross-request locking; the gateway
drives one profile at a time per logical call."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        name: str | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self.name = name or "breaker"
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.time() - self._opened_at >= self.open_seconds:
                self._state = "half_open"
                logger.info("breaker %s half_open (probing)", self.name)
                return True
            return False
        # half_open
        return True

    def record_success(self) -> None:
        was_open = self._state in ("open", "half_open")
        self._failures = 0
        self._state = "closed"
        if was_open:
            logger.info("breaker %s closed (recovered)", self.name)

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == "half_open":
            self._state = "open"
            self._opened_at = time.time()
            logger.warning("breaker %s re-opened from half_open", self.name)
            return
        if self._failures >= self.failure_threshold and self._state == "closed":
            self._state = "open"
            self._opened_at = time.time()
            logger.warning(
                "breaker %s OPEN after %d consecutive failures", self.name, self._failures
            )
