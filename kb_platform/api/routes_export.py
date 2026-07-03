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


def _load_text_units(root: Path) -> pd.DataFrame:
    path = root / "text_units.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["id", "text"])


def _load_embeddings(root: Path, index_name: str) -> dict[str, list[float]]:
    """Read all (id, vector) pairs from a LanceDB vector table.

    Returns {} if the table is absent (e.g., embeddings never generated).
    Uses the `lancedb` package directly (a transitive graphrag dep) because
    graphrag's vector-store API exposes similarity search, not bulk reads.
    """
    import lancedb

    db = lancedb.connect(str(root / "vectors"))
    if index_name not in db.table_names():
        return {}
    df = db.open_table(index_name).to_pandas()
    out: dict[str, list[float]] = {}
    for _, row in df.iterrows():
        rid = row.get("id")
        vec = row.get("vector")
        if rid is None or vec is None:
            continue
        out[str(rid)] = [float(x) for x in vec]
    return out


@router.get("/kbs/{kb_id}/export")
def export(kb_id: int, request: Request, format: str = "zip") -> Response:
    """Export a KB index as GraphML, a Cypher script, or a zip bundle.

    - ``format=graphml``: standalone GraphML document.
    - ``format=cypher``: idempotent Cypher script (text/plain).
    - ``format=zip``: parquet artifacts plus ``graph.graphml`` and ``graph.cypher``.
    """
    root = _data_root(request, kb_id)

    if format == "graphml":
        from kb_platform.graph.graphml import write_graphml

        xml = write_graphml(_load_entities(root), _load_relationships(root))
        return Response(content=xml, media_type="application/graphml+xml")

    if format == "cypher":
        from kb_platform.graph.cypher import write_cypher

        script = write_cypher(
            _load_entities(root),
            _load_relationships(root),
            text_units=_load_text_units(root),
            entity_embeddings=_load_embeddings(root, "entity_description"),
            text_unit_embeddings=_load_embeddings(root, "text_unit_text"),
        )
        return Response(content=script, media_type="text/plain; charset=utf-8")

    if format == "zip":
        from kb_platform.graph.cypher import write_cypher
        from kb_platform.graph.graphml import write_graphml

        entities = _load_entities(root)
        relationships = _load_relationships(root)
        text_units = _load_text_units(root)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in _PARQUET_ARTIFACTS:
                path = root / name
                if path.exists():
                    archive.write(path, name)
            archive.writestr("graph.graphml", write_graphml(entities, relationships))
            archive.writestr(
                "graph.cypher",
                write_cypher(
                    entities,
                    relationships,
                    text_units=text_units,
                    entity_embeddings=_load_embeddings(root, "entity_description"),
                    text_unit_embeddings=_load_embeddings(root, "text_unit_text"),
                ),
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=kb-{kb_id}.zip"},
        )

    raise HTTPException(
        status_code=400, detail="format must be one of: zip, graphml, cypher"
    )
