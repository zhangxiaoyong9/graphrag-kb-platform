"""Generic UnitWorker: drives a unit_fanout step via its registered strategy."""

import asyncio
import logging
from pathlib import Path

from kb_platform.db.enums import UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import STRATEGIES, Subject
from kb_platform.graph.adapter import GraphAdapter

# Importing the strategies package registers all built-in strategies into STRATEGIES.
import kb_platform.engine.strategies  # noqa: F401,E402

logger = logging.getLogger(__name__)


class UnitWorker:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = Path(data_root)
        self.concurrency = concurrency

    async def run_unit_fanout(self, step, min_success_ratio: float = 1.0) -> None:
        strategy = STRATEGIES[step.name]
        # Process one batch of pending (non-succeeded) units, then finalize.
        # Idempotency across retries is provided by next_units_batch, which
        # skips subjects whose unit already SUCCEEDED; a fresh run_unit_fanout
        # call re-queries and re-runs only the still-pending/failed units.
        batch = strategy.next_units_batch(self.repo, step)
        if batch is not None:
            await self._run_batch(strategy, step, batch)
        status = strategy.finalize(self.repo, self.adapter, step, self.data_root, min_success_ratio)
        self.repo.set_step_status(step.id, status)

    async def _run_batch(self, strategy, step, subjects: list[Subject]) -> None:
        units = []
        for s in subjects:
            u = self.repo.get_unit_by_subject(step.id, s.subject_type, s.subject_id)
            if u is None:
                u = self.repo.add_unit(step.id, s.subject_type, s.subject_id)
            if u.status == UnitStatus.SUCCEEDED:
                continue
            units.append(u)
        if not units:
            return
        for u in units:
            self.repo.set_unit_running(u.id)
        sem = asyncio.Semaphore(self.concurrency)

        async def handle(u):
            async with sem:
                await self._process(strategy, u)

        await asyncio.gather(*(handle(u) for u in units))

    async def _process(self, strategy, unit) -> None:
        try:
            result = await strategy.run_unit(self.adapter, unit, self.repo)
            strategy.persist(self.data_root, unit, result)
            self.repo.set_unit_succeeded(unit.id, input_hash=result.input_hash, cost_json=result.cost_json, llm_raw_output=result.llm_raw_output)
        except Exception as e:  # noqa: BLE001
            logger.warning("unit %s failed: %s", unit.id, e)
            self.repo.set_unit_failed(unit.id, str(e))
