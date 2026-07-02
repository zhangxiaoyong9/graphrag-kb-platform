"""Process-wide in-memory metrics for the FailoverGateway.

Records TTFT (time-to-first-token, streaming only) and failover
detection/recovery timings. T18 exposes `snapshot()` at `GET /llm/health`.
All recording is best-effort side-effect only — it must never raise into
the gateway hot path.
"""

from __future__ import annotations

import statistics
import threading


class MetricsStore:
    """Thread-safe rolling-window store of gateway timing metrics."""

    def __init__(self, *, window: int = 100) -> None:
        self._lock = threading.Lock()
        self._window = window
        self._ttft: list[float] = []            # ms, streaming only
        self._failover_detect: list[float] = []   # ms, request-start -> first retriable error
        self._failover_recover: list[float] = []  # ms, first error -> eventual success
        self._failovers = 0
        self._successes = 0

    # --- recorders (never raise) ---
    def record_ttft(self, ms: float) -> None:
        with self._lock:
            self._ttft.append(ms)
            self._ttft = self._ttft[-self._window:]

    def record_failover(self, detect_ms: float, recover_ms: float) -> None:
        with self._lock:
            self._failover_detect.append(detect_ms)
            self._failover_detect = self._failover_detect[-self._window:]
            self._failover_recover.append(recover_ms)
            self._failover_recover = self._failover_recover[-self._window:]
            self._failovers += 1

    def record_success(self) -> None:
        with self._lock:
            self._successes += 1

    @staticmethod
    def _p50(xs: list[float]) -> float | None:
        return statistics.median(xs) if xs else None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ttft_ms_p50": self._p50(self._ttft),
                "failover_detect_ms_p50": self._p50(self._failover_detect),
                "failover_recover_ms_p50": self._p50(self._failover_recover),
                "failovers": self._failovers,
                "successes": self._successes,
            }

    def _reset_for_test(self) -> None:
        """Test-only: clear all accumulated metrics for isolation."""
        with self._lock:
            self._ttft.clear()
            self._failover_detect.clear()
            self._failover_recover.clear()
            self._failovers = 0
            self._successes = 0


# Process-wide singleton. T18 reads this; gateway records into it.
METRICS = MetricsStore()
