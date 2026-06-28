"""KB graph-scale stats snapshot: write <data_root>/stats.json at job end.

Best-effort observability: after a job finishes, count entities / relationships
/ communities / community reports / text units (parquet rows) plus documents /
chunks (DB rows) and persist the snapshot. Missing parquet -> 0; the function
never raises (stats are observability, not correctness — they must not fail a
job). Read by ``GET /kbs/{id}/stats``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository

logger = logging.getLogger(__name__)

STATS_FILE = "stats.json"

# (stats key, parquet file) — row count of each.
_PARQUET_COUNTS = (
    ("entity_count", "entities.parquet"),
    ("relationship_count", "relationships.parquet"),
    ("community_count", "communities.parquet"),
    ("community_report_count", "community_reports.parquet"),
    ("text_unit_count", "text_units.parquet"),
)


def _parquet_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_parquet(path))
    except Exception:  # noqa: BLE001 — best-effort; a bad file must not fail the job
        logger.warning("kb_stats: could not read %s; counting as 0", path)
        return 0


def write_kb_stats(repo: Repository, kb_id: int) -> None:
    """Write <data_root>/stats.json with the current graph-scale counts.

    Never raises: a missing/malformed parquet contributes 0 and is logged; an
    unknown kb_id is a silent no-op.
    """
    with session_scope(repo.engine) as s:
        kb = s.get(KnowledgeBase, kb_id)
        if kb is None:
            return
        data_root = Path(kb.data_root)

    stats: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    for key, fname in _PARQUET_COUNTS:
        stats[key] = _parquet_row_count(data_root / fname)
    stats["document_count"] = len(repo.get_documents(kb_id))
    stats["chunk_count"] = len(repo.get_chunks(kb_id))
    (data_root / STATS_FILE).write_text(json.dumps(stats))
