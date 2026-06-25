"""Reconsolidate: incorporate needs_reconsolidation units by re-running merge_delta.

Late-succeeded units (e.g. retried after a job's merge_delta already ran) have
their extraction cached on disk (2a/2b persistence) but were not incorporated
into the final entities/relationships parquet. Reconsolidate re-merges ALL
on-disk extractions (old + late) via ``merge_delta`` and clears the flags.
No LLM is invoked — every extraction is already cached on disk.
"""

import logging

from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import StepKind, StepStatus
from kb_platform.db.models import Unit
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec

logger = logging.getLogger(__name__)


async def reconsolidate(repo: Repository, adapter, kb_id: int, data_root: str) -> None:
    """Re-merge all cached extractions (incl. late units); clear needs_reconsolidation flags.

    Builds a throwaway ``merge_delta`` step under a fresh incremental job so
    ``atomic_steps._data_root`` can resolve the KB's ``data_root`` via
    ``step.job_id -> job.kb_id -> kb.data_root``.
    """
    with session_scope(repo.engine) as s:
        late_units = list(s.scalars(select(Unit).where(Unit.needs_reconsolidation.is_(True))))
    if not late_units:
        return

    from kb_platform.engine import atomic_steps

    step = repo.create_job(
        kb_id=kb_id,
        type="incremental",
        specs=[StepSpec("merge_delta", StepKind.ATOMIC)],
    ).steps[0]
    atomic_steps.merge_delta(repo, adapter, step)
    repo.set_step_status(step.id, StepStatus.SUCCEEDED)

    with session_scope(repo.engine) as s:
        for u in s.scalars(select(Unit).where(Unit.needs_reconsolidation.is_(True))):
            u.needs_reconsolidation = False

    logger.info("reconsolidate: re-merged extractions, cleared %d flag(s)", len(late_units))
