# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Self-written GraphML writer (no networkx dependency).

Produces schema-valid GraphML: every ``<data>`` element is a child of a
``<node>`` or ``<edge>`` element (as required by the GraphML spec), and all
text content is XML-escaped via :func:`xml.sax.saxutils.escape`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from xml.sax.saxutils import escape

NS = "http://graphml.graphdrawing.org/xmlns"


def _fmt(value: Any) -> str:
    """Render a scalar as an XML-safe GraphML attribute string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return escape(str(value))


def write_graphml(entities: pd.DataFrame, relationships: pd.DataFrame) -> str:
    """Render entities + relationships as a GraphML XML document string.

    Parameters
    ----------
    entities:
        DataFrame with at least a ``title`` column; optional columns are
        ``type``, ``degree`` and ``description``.
    relationships:
        DataFrame with ``source`` and ``target`` columns; optional columns are
        ``weight`` and ``description``.
    """
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<graphml xmlns="{NS}">',
        '<key attr.name="type" attr.type="string" for="node" id="d_type"/>',
        '<key attr.name="degree" attr.type="int" for="node" id="d_deg"/>',
        '<key attr.name="description" attr.type="string" for="node" id="d_desc"/>',
        '<key attr.name="weight" attr.type="double" for="edge" id="d_w"/>',
        '<key attr.name="description" attr.type="string" for="edge" id="d_edesc"/>',
        '<graph edgedefault="undirected">',
    ]

    if not entities.empty:
        for _, row in entities.iterrows():
            node_id = _fmt(row["title"])
            lines.append(f'<node id="{node_id}">')
            if "type" in entities.columns:
                lines.append(f'<data key="d_type">{_fmt(row.get("type"))}</data>')
            if "degree" in entities.columns:
                lines.append(f'<data key="d_deg">{_fmt(row.get("degree"))}</data>')
            if "description" in entities.columns:
                lines.append(f'<data key="d_desc">{_fmt(row.get("description"))}</data>')
            lines.append("</node>")

    if not relationships.empty:
        for _, row in relationships.iterrows():
            source = _fmt(row["source"])
            target = _fmt(row["target"])
            lines.append(f'<edge source="{source}" target="{target}">')
            if "weight" in relationships.columns:
                lines.append(f'<data key="d_w">{_fmt(row.get("weight"))}</data>')
            if "description" in relationships.columns:
                lines.append(f'<data key="d_edesc">{_fmt(row.get("description"))}</data>')
            lines.append("</edge>")

    lines.append("</graph>")
    lines.append("</graphml>")
    return "\n".join(lines)
