# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Index export endpoint: zip of parquet artifacts, or standalone GraphML."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

router = APIRouter()

# Parquet artifacts bundled into the zip export (in priority order).
_PARQUET_ARTIFACTS = (
    "entities.parquet",
    "relationships.parquet",
    "communities.parquet",
    "community_reports.parquet",
    "text_units.parquet",
)


def _data_root(request: Request, kb_id: int) -> Path:
    """Resolve the on-disk data_root for a KB; raise 404 if it doesn't exist.

    Defined here so that Task 5 (``/graph``) can reuse it.
    """
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    repo = request.app.state.repo
    with session_scope(repo.engine) as session:
        kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return Path(kb.data_root)


def _load_entities(root: Path) -> pd.DataFrame:
    path = root / "entities.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["title"])


def _load_relationships(root: Path) -> pd.DataFrame:
    path = root / "relationships.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["source", "target"])


@router.get("/kbs/{kb_id}/export")
def export(kb_id: int, request: Request, format: str = "zip") -> Response:
    """Export a KB index either as a standalone GraphML document or a zip
    bundle containing the existing parquet artifacts plus ``graph.graphml``.
    """
    root = _data_root(request, kb_id)

    if format == "graphml":
        from kb_platform.graph.graphml import write_graphml

        xml = write_graphml(_load_entities(root), _load_relationships(root))
        return Response(content=xml, media_type="application/graphml+xml")

    if format == "zip":
        from kb_platform.graph.graphml import write_graphml

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in _PARQUET_ARTIFACTS:
                path = root / name
                if path.exists():
                    archive.write(path, name)
            xml = write_graphml(_load_entities(root), _load_relationships(root))
            archive.writestr("graph.graphml", xml)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=kb-{kb_id}.zip"},
        )

    raise HTTPException(status_code=400, detail="format must be one of: zip, graphml")
