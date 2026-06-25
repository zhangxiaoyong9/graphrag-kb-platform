# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Tests for the self-written GraphML writer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pandas as pd

from kb_platform.graph.graphml import write_graphml


def test_write_graphml_well_formed_and_escaped():
    ents = pd.DataFrame([{"title": "A&B", "type": "CONCEPT", "degree": 2, "description": "<x>"}])
    rels = pd.DataFrame([{"source": "A&B", "target": "A&B", "weight": 1.0, "description": "self"}])
    xml = write_graphml(ents, rels)
    root = ET.fromstring(xml)  # parses -> well-formed
    assert root.tag == "{http://graphml.graphdrawing.org/xmlns}graphml"
    assert "A&amp;B" in xml  # escaped


def test_write_graphml_data_nested_in_node_and_edge():
    ents = pd.DataFrame([{"title": "A&B", "type": "CONCEPT", "degree": 2, "description": "<x>"}])
    rels = pd.DataFrame([{"source": "A&B", "target": "A&B", "weight": 1.0, "description": "self"}])
    root = ET.fromstring(write_graphml(ents, rels))
    ns = "{http://graphml.graphdrawing.org/xmlns}"
    nodes = root.findall(f".//{ns}node")
    edges = root.findall(f".//{ns}edge")
    assert len(nodes) == 1
    assert len(edges) == 1
    # data must be a child of node/edge (valid GraphML)
    node_data = nodes[0].findall(f"{ns}data")
    edge_data = edges[0].findall(f"{ns}data")
    assert len(node_data) >= 1
    assert len(edge_data) >= 1


def test_write_graphml_empty():
    xml = write_graphml(pd.DataFrame(columns=["title"]), pd.DataFrame(columns=["source", "target"]))
    assert "graphml" in xml  # no crash on empty
    ET.fromstring(xml)  # still well-formed
