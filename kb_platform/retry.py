"""Manual retry of failed units and steps."""

from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import GraphAdapter


class RetryService:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        worker = UnitWorker  # 延迟构造,确保每次 rerun 用最新状态
        self._worker_cls = worker
        self.data_root = data_root
        self.concurrency = concurrency

    def retry_unit(self, unit_id: int) -> None:
        """Reset a single failed unit to pending (does not run it)."""
        self.repo.reset_unit_to_pending(unit_id)

    def retry_step(self, step_id: int) -> int:
        """Reset all failed units of a step; return count reset. Step returns to running on rerun."""
        n = self.repo.reset_failed_units_to_pending(step_id)
        self.repo.set_step_status(step_id, StepStatus.RUNNING)
        return n

    async def rerun_step(self, step_id: int) -> None:
        """Re-run a unit_fanout step's pending units and re-settle."""
        step = self.repo.get_step(step_id)
        already_succeeded = self.repo.get_step(step_id).status == StepStatus.SUCCEEDED
        worker = self._worker_cls(repo=self.repo, adapter=self.adapter, data_root=self.data_root, concurrency=self.concurrency)
        await worker.run_unit_fanout(step)
        if already_succeeded:
            # A unit that succeeds only after its step was already finalized
            # means downstream artifacts (communities/reports) are now stale.
            for u in self.repo.list_units(step_id):
                if u.status == UnitStatus.SUCCEEDED and u.attempt_no > 1:
                    self.repo.mark_needs_reconsolidation(u.id)
