"""WebSocket realtime progress: poll SQLite, diff, push step/unit progress to subscribers.

The worker writes job/step/unit status to SQLite; this module is the server-side
bridge that turns those changes into WS events. A global RealtimeHub (added in a
later task) polls jobs that have subscribers, diffs against the last-sent frame,
and pushes only what changed. Worker code is never touched.

Events carry StepOut-shaped data (with ``progress``) so the frontend can treat a
snapshot/delta as a ``JobOut`` directly.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from kb_platform.api.models import StepOut, UnitProgress
from kb_platform.db.repository import Repository

logger = logging.getLogger(__name__)


def _step_dict(repo: Repository, s) -> dict:
    """Serialize one Step to a StepOut-shaped dict (mirrors routes_jobs._step_out)."""
    progress = None
    if s.kind == "unit_fanout":
        progress = UnitProgress(**repo.unit_counts_by_status(s.id)).model_dump()
    return StepOut(
        id=s.id, name=s.name, ordinal=s.ordinal, kind=s.kind, status=s.status, progress=progress
    ).model_dump()


def _job_state(repo: Repository, job_id: int) -> tuple[str, dict[int, dict]]:
    """Current ``(job_status, {step_id: step_dict})`` from the DB — the source of truth."""
    job = repo.get_job(job_id)
    if job is None:
        return "", {}
    return job.status, {s.id: _step_dict(repo, s) for s in repo.get_steps(job_id)}


@dataclass
class JobBroadcaster:
    """One job's subscriber set + last-sent frame, used to diff and push deltas."""

    job_id: int
    repo: Repository
    subscribers: set = field(default_factory=set)
    _last_job_status: str | None = None
    _last_steps: dict[int, dict] = field(default_factory=dict)

    def snapshot(self) -> dict:
        """Full current frame; also seeds the diff baseline. Sent on subscribe."""
        job_status, steps = _job_state(self.repo, self.job_id)
        self._last_job_status = job_status
        self._last_steps = steps
        return {
            "type": "snapshot",
            "job": {"id": self.job_id, "status": job_status},
            "steps": list(steps.values()),
        }

    def diff_and_emit(self) -> dict | None:
        """Return a delta event for changed steps/job, or ``None`` if nothing changed.

        Reads the live DB each call and compares to the last frame, so intermediate
        states are never silently lost (worst case: the first poll after a burst
        emits the net change in one delta).
        """
        job_status, steps = _job_state(self.repo, self.job_id)
        changed = [s for sid, s in steps.items() if s != self._last_steps.get(sid)]
        job_changed = job_status != self._last_job_status
        self._last_job_status = job_status
        self._last_steps = steps
        if not changed and not job_changed:
            return None
        event: dict = {"type": "delta", "steps": changed}
        if job_changed:
            event["job"] = {"id": self.job_id, "status": job_status}
        return event

    async def broadcast(self, event: dict) -> None:
        """Send to all subscribers; drop any that error (don't let one break others)."""
        dead = []
        for ws in list(self.subscribers):
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            logger.debug("dropping dead subscriber on job %s", self.job_id)
            self.subscribers.discard(ws)


class RealtimeHub:
    """Owns one poll loop that fans DB changes out to all jobs with subscribers."""

    def __init__(self, repo: Repository, interval: float = 0.5) -> None:
        self.repo = repo
        self.interval = interval
        self.broadcasters: dict[int, JobBroadcaster] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def subscribe(self, job_id: int, ws) -> dict:
        bc = self.broadcasters.get(job_id)
        if bc is None:
            bc = JobBroadcaster(job_id=job_id, repo=self.repo)
            self.broadcasters[job_id] = bc
        bc.subscribers.add(ws)
        return bc.snapshot()

    def unsubscribe(self, job_id: int, ws) -> None:
        bc = self.broadcasters.get(job_id)
        if bc is not None:
            bc.subscribers.discard(ws)
            if not bc.subscribers:
                del self.broadcasters[job_id]

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            for job_id in list(self.broadcasters):
                try:
                    bc = self.broadcasters[job_id]
                    if not bc.subscribers:
                        continue
                    event = bc.diff_and_emit()
                    if event is not None:
                        await bc.broadcast(event)
                except Exception:  # noqa: BLE001 — per-job isolation; never kill the loop
                    logger.exception("realtime poll failed for job %s; continuing", job_id)
