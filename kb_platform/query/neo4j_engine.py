"""Neo4j query engine + pure helpers (Text2Cypher prompt, hybrid Cypher template,
row formatter). The engine class (Task 7) is appended below; this task lands the
pure helpers so they are unit-testable in isolation.

No graphrag / graphrag_llm imports anywhere in this module. The engine receives
its completion / embed clients + driver pool by injection.
"""

from __future__ import annotations

import json

# --- Canonical graph schema (ours, stable; emitted by the cypher export) ------
_SCHEMA = """\
Graph schema (Neo4j, GraphRAG export):

Nodes:
  (:Entity {title, type, description, text_unit_ids, frequency, degree, ...})
    - title is the unique identity key (constraint entity_title_unique)
    - entity_description holds the embedding (index entity_description_vec)
  (:TextUnit {id, text, document_ids, n_tokens, ...})
    - text_unit_text holds the embedding (index text_unit_text_vec)

Relationships:
  (:Entity)-[:RELATED {description, weight, combined_degree, text_unit_ids}]->(:Entity)
  (:TextUnit)-[:FROM_CHUNK]->(:Entity)   # the text unit mentions the entity
"""

_FEW_SHOT = """\
Always answer with a SINGLE read-only Cypher query (MATCH/RETURN/OPTIONAL MATCH/
WITH/WHERE/ORDER BY/LIMIT/COUNT). Never write, create, merge, set, delete, or
load data. Return only the Cypher — no prose, no fences.

Examples:
- Entities of a given type:
    MATCH (e:Entity {type: 'ORG'}) RETURN e.title, e.description LIMIT 20
- K-hop neighbors of an entity:
    MATCH (e:Entity {title: $title})-[:RELATED*1..2]-(n:Entity)
    RETURN DISTINCT n.title, n.type
- Count entities per type:
    MATCH (e:Entity) RETURN e.type, count(*) AS n ORDER BY n DESC
- Path between two entities:
    MATCH p = shortestPath((a:Entity {title: $a})-[:RELATED*]-(b:Entity {title: $b}))
    RETURN [n IN nodes(p) | n.title] AS hops
- Text units that mention an entity:
    MATCH (t:TextUnit)-[:FROM_CHUNK]->(e:Entity {title: $title})
    RETURN t.id, t.text
"""


def build_text2cypher_messages(question: str) -> list[dict]:
    """Build the chat messages for the Text2Cypher LLM call."""
    system = (
        "You translate a user's question into one read-only Cypher query against "
        "the graph below.\n\n"
        f"{_SCHEMA}\n{_FEW_SHOT}"
    )
    user = f"User question: {question}\n\nCypher:"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_hybrid_cypher(top_k: int, hops: int) -> str:
    """Render the Vector+Cypher hybrid retrieval query.

    ``$vector`` stays a real parameter (the question embedding); ``top_k`` and
    ``hops`` are baked in as literals because Neo4j's variable-length path bound
    must be a literal, not a parameter. Returns one row with three collected
    bags: ``entities`` (seeds + their <=hops :RELATED neighborhood),
    ``relationships`` (the :RELATED edges along those paths), and ``chunks``
    (the :TextUnit nodes that mention any reached entity via :FROM_CHUNK).
    """
    return f"""CALL db.index.vector.queryNodes('entity_description_vec', {top_k}, $vector)
YIELD node AS seed, score
WITH collect(DISTINCT seed) AS seeds
// entities: seeds plus their <=hops :RELATED neighborhood
CALL {{
  WITH seeds
  UNWIND seeds AS s
  MATCH (s)-[:RELATED*0..{hops}]-(e:Entity)
  RETURN collect(DISTINCT e) AS entities
}}
// relationships: the :RELATED edges traversed within <=hops from each seed
CALL {{
  WITH seeds
  UNWIND seeds AS s
  MATCH p = (s)-[:RELATED*1..{hops}]-(:Entity)
  UNWIND relationships(p) AS r
  RETURN collect(DISTINCT r) AS relationships
}}
// chunks: text units mentioning any reached entity
CALL {{
  WITH entities
  UNWIND entities AS e
  MATCH (e)<-[:FROM_CHUNK]-(t:TextUnit)
  RETURN collect(DISTINCT t) AS chunks
}}
RETURN entities, relationships, chunks
"""


def format_rows_as_context(rows: list[dict]) -> str:
    """Flatten Text2Cypher result rows into a synthesis context string."""
    if not rows:
        return ""
    lines: list[str] = []
    for i, row in enumerate(rows, start=1):
        # Neo4j node/relationship values arrive as dicts (driver config); render
        # their properties compactly.
        rendered = {k: _render(v) for k, v in row.items()}
        lines.append(f"[{i}] " + json.dumps(rendered, ensure_ascii=False))
    return "\n".join(lines)


def _render(value) -> object:
    """Coerce a Neo4j value (node/relationship/list/scalar) into JSON-able form."""
    # Node / Relationship objects expose .id / .element_id / .items() in the
    # neo4j driver; the routing layer normalizes them to dicts before they reach
    # the engine (see Neo4jQueryEngine._rows_from_records). Lists recurse.
    if isinstance(value, (list, tuple)):
        return [_render(v) for v in value]
    if isinstance(value, dict):
        return {k: _render(v) for k, v in value.items()}
    return value
