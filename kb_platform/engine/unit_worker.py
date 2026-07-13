"""Generic UnitWorker: drives a unit_fanout step via its registered strategy."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from kb_platform.db.enums import UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject
from kb_platform.graph.adapter import GraphAdapter

logger = logging.getLogger(__name__)


class UnitWorker:
    def __init__(
        self,
        *,
        repo: Repository,
        adapter: GraphAdapter,
        data_root: str,
        strategies: dict,
        concurrency: int = 4,
        worker_id: str = "worker",
        heartbeat_interval: float = 5.0,
    ) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = Path(data_root)
        self.strategies = strategies
        self.concurrency = concurrency
        self.worker_id = worker_id
        self.heartbeat_interval = heartbeat_interval

    async def run_unit_fanout(self, step, min_success_ratio: float = 1.0) -> None:
        strategy = self.strategies[step.name]
        while (batch := strategy.next_units_batch(self.repo, step)) is not None:
            await self._run_batch(strategy, step, batch)
        status = strategy.finalize(self.repo, self.adapter, step, self.data_root, min_success_ratio)
        self.repo.set_step_status(step.id, status)

    async def _run_batch(self, strategy, step, subjects: list[Subject]) -> None:
        units = []
        for s in subjects:
            u = self.repo.get_unit_by_subject(step.id, s.subject_type, s.subject_id)
            if u is None:
                u = self.repo.add_unit(step.id, s.subject_type, s.subject_id, kind=strategy.kind)
            if u.status == UnitStatus.SUCCEEDED:
                continue
            units.append(u)
        if not units:
            return
        for u in units:
            self.repo.set_unit_running(u.id, self.worker_id, datetime.now())
        sem = asyncio.Semaphore(self.concurrency)

        async def handle(u):
            async with sem:
                stop = asyncio.Event()
                hb = asyncio.create_task(self._heartbeat(u.id, stop))
                try:
                    await self._process(strategy, u)
                finally:
                    stop.set()
                    await hb

        await asyncio.gather(*(handle(u) for u in units))

    async def _heartbeat(self, unit_id: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.repo.touch_unit_heartbeat(unit_id, datetime.now())
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass

    async def _process(self, strategy, unit) -> None:
        import time

        from kb_platform.graph.cost_capture import use_recorder
        from kb_platform.logging_config import bind_log_context

        with bind_log_context(unit_id=unit.id):
            t0 = time.perf_counter()
            try:
                with use_recorder() as rec:
                    result = await strategy.run_unit(self.adapter, unit, self.repo)
                if result.cost_json is None and rec:
                    result.cost_json = rec.to_json()
                if result.llm_raw_output is None and rec:
                    result.llm_raw_output = rec.raw_output()
                strategy.persist(self.data_root, unit, result)
                self.repo.set_unit_succeeded(
                    unit.id,
                    input_hash=result.input_hash,
                    cost_json=result.cost_json,
                    llm_raw_output=result.llm_raw_output,
                )
                logger.info(
                    "unit %s [%s] done in %.0fms",
                    unit.id, strategy.kind, (time.perf_counter() - t0) * 1000,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "unit %s [%s] failed; error_type=%s", unit.id, strategy.kind,
                    type(e).__name__,
                )
                self.repo.set_unit_failed(unit.id, str(e))
