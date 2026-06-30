# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Self-written GraphML writer (no networkx dependency).

Produces schema-valid GraphML: every ``<data>`` element is a child of a
``<node>`` or ``<edge>`` element (as required by the GraphML spec); text
content is XML-escaped (:func:`xml.sax.saxutils.escape`) and attribute values
go through :func:`xml.sax.saxutils.quoteattr` so a ``"`` in a title/endpoint
stays well-formed.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from xml.sax.saxutils import escape, quoteattr

from kb_platform.graph.adapter import cell_to_text

NS = "http://graphml.graphdrawing.org/xmlns"


def _text(value: Any) -> str:
    """Render a cell for an XML **text content** context (``<data>...</data>``).

    ``escape`` handles ``&``/``<``/``>``; quotes are legal in text content.
    ``description`` may be a list/ndarray of chunk-level strings (see
    :func:`cell_to_text`); flatten it so we never emit ``['d1' 'd2']`` repr.
    """
    return escape(cell_to_text(value))


def _attr(value: Any) -> str:
    """Render a cell for an XML **attribute value** context (``id`` / ``source``
    / ``target``).

    ``quoteattr`` returns the value already wrapped in quotes and escapes what
    those quotes require — unlike ``escape`` it handles ``"``, so a title like
    ``a"b`` stays well-formed. Caller must NOT add its own surrounding quotes.
    """
    return quoteattr(cell_to_text(value))


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
            lines.append(f"<node id={_attr(row['title'])}>")
            if "type" in entities.columns:
                lines.append(f'<data key="d_type">{_text(row.get("type"))}</data>')
            if "degree" in entities.columns:
                lines.append(f'<data key="d_deg">{_text(row.get("degree"))}</data>')
            if "description" in entities.columns:
                lines.append(f'<data key="d_desc">{_text(row.get("description"))}</data>')
            lines.append("</node>")

    if not relationships.empty:
        for _, row in relationships.iterrows():
            source = _attr(row["source"])
            target = _attr(row["target"])
            lines.append(f"<edge source={source} target={target}>")
            if "weight" in relationships.columns:
                lines.append(f'<data key="d_w">{_text(row.get("weight"))}</data>')
            if "description" in relationships.columns:
                lines.append(f'<data key="d_edesc">{_text(row.get("description"))}</data>')
            lines.append("</edge>")

    lines.append("</graph>")
    lines.append("</graphml>")
    return "\n".join(lines)
