"""Neo4j query engine + pure helpers (Text2Cypher prompt, hybrid Cypher template,
row formatter). The engine class (Task 7) is appended below; this task lands the
pure helpers so they are unit-testable in isolation.

No graphrag / graphrag_llm imports anywhere in this module. The engine receives
its completion / embed clients + driver pool by injection.
"""

from __future__ import annotations

import json
import logging
import re
import time

from kb_platform.neo4j.safety import is_readonly_cypher, truncate_rows
from kb_platform.query.engine import (
    QueryResult,
    SourceRef,
    StreamDelta,
    StreamDone,
    StreamMeta,
)

logger = logging.getLogger(__name__)

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


# --- Neo4jQueryEngine (Task 7) ----------------------------------------------
ROW_CAP = 1000
_DEFAULT_HOPS = 2
_DEFAULT_TIMEOUT_MS = 10_000
_FENCE_RE = re.compile(r"^```(?:cypher)?\s*\n?|\n?```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Strip a single wrapping ``` / ```cypher fence the LLM sometimes adds."""
    return _FENCE_RE.sub("", text.strip()).strip()


def _format_hybrid_context(record: dict) -> str:
    """Render the three bags (entities/relationships/chunks) as synthesis context."""
    if not record:
        return ""
    parts: list[str] = []
    ents = record.get("entities") or []
    if ents:
        parts.append("Entities:\n" + "\n".join(f"- {_render(e)}" for e in ents))
    rels = record.get("relationships") or []
    if rels:
        parts.append("Relationships:\n" + "\n".join(f"- {_render(r)}" for r in rels))
    chunks = record.get("chunks") or []
    if chunks:
        parts.append("Text units:\n" + "\n".join(f"- {_render(c)}" for c in chunks))
    return "\n\n".join(parts)


def _hybrid_sources(record: dict, limit: int = 8) -> list[SourceRef]:
    """Top entity titles + text-unit ids as citation sources."""
    out: list[SourceRef] = []
    for e in (record.get("entities") or [])[:limit]:
        title = e.get("title") if isinstance(e, dict) else None
        if title:
            out.append(SourceRef(kind="entity", name=str(title), text=str(e.get("description", ""))[:200]))
    for c in (record.get("chunks") or [])[:limit]:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid:
            out.append(SourceRef(kind="text_unit", name=str(cid), text=str(c.get("text", ""))[:200]))
    return out


def _rows_sources(rows: list[dict], limit: int = 8) -> list[SourceRef]:
    """Entity titles from flat Text2Cypher rows as citation sources."""
    out: list[SourceRef] = []
    for row in rows:
        title = row.get("title") if isinstance(row, dict) else None
        if title:
            out.append(SourceRef(kind="entity", name=str(title), text=str(row.get("description", ""))[:200]))
        if len(out) >= limit:
            break
    return out


class Neo4jQueryEngine:
    """QueryEngine backed by a live Neo4j graph store.

    Receives its driver pool, LLM completion, and (for hybrid) an embed callable
    by injection — this module imports neither graphrag nor graphrag_llm. The
    factory (build_query_engine) resolves the KB's profiles and constructs the
    clients; this class only runs retrieval + synthesis.

    Supports method ∈ {cypher, hybrid}. Any other method yields a terminal
    StreamDone(error=...); the factory routes only cypher/hybrid here.
    """

    def __init__(self, *, uri, username, password, driver_pool, completion, embed, model_id,
                 database: str = "neo4j") -> None:
        self._uri = uri
        self._username = username
        self._password = password
        self._pool = driver_pool
        self._completion = completion
        self._embed = embed
        self._model_id = model_id
        self._database = database

    async def search(self, method, query, kb_data_root, params=None) -> QueryResult:
        answer = ""
        async for ev in self.stream_search(method, query, kb_data_root, params):
            if isinstance(ev, StreamDelta):
                answer += ev.text
            elif isinstance(ev, StreamDone):
                return QueryResult(
                    answer=ev.answer or answer,
                    method=method,
                    error=ev.error,
                    elapsed_ms=ev.elapsed_ms,
                    prompt_tokens=ev.prompt_tokens,
                    output_tokens=ev.output_tokens,
                    sources=ev.sources,
                )
        return QueryResult(answer=answer, method=method)

    async def stream_search(self, method, query, kb_data_root, params=None):
        if method not in ("cypher", "hybrid"):
            yield StreamDone(method=method, answer="", error=f"neo4j engine does not support method '{method}'")
            return
        try:
            if method == "cypher":
                async for ev in self._cypher(query, params):
                    yield ev
            else:
                async for ev in self._hybrid(query, params):
                    yield ev
        except Exception as e:  # noqa: BLE001 - SSE error, never raise
            logger.exception("neo4j stream_search failed for method=%s", method)
            yield StreamDone(method=method, answer="", error=str(e))

    # --- Text2Cypher ---------------------------------------------------------
    async def _cypher(self, query, params):
        messages = build_text2cypher_messages(query)
        resp = await self._completion.completion_async(messages=messages, stream=False)
        cypher = _strip_fences(_message_content(resp))
        yield StreamMeta(cypher=cypher)

        if not is_readonly_cypher(cypher):
            yield StreamDone(method="cypher", answer="", error="generated Cypher is not read-only; refused")
            return

        timeout_s = _timeout_seconds(params)
        rows, truncated = await self._execute(cypher, {}, timeout_s, ROW_CAP)
        context = format_rows_as_context(rows)
        sources = _rows_sources(rows)
        async for ev in self._synthesize("cypher", query, context, sources, truncated, _usage(resp)):
            yield ev

    # --- Vector+Cypher hybrid ------------------------------------------------
    async def _hybrid(self, query, params):
        if self._embed is None:
            yield StreamDone(method="hybrid", answer="", error="hybrid needs an embedding profile; configure one on the KB")
            return
        vector = await self._embed(query)
        top_k = (params.top_k if params and params.top_k is not None else None) or 10
        hops = (params.hops if params and params.hops is not None else None) or _DEFAULT_HOPS
        cypher = build_hybrid_cypher(top_k, hops)
        yield StreamMeta(cypher=cypher)

        timeout_s = _timeout_seconds(params)
        rows, truncated = await self._execute(cypher, {"vector": vector}, timeout_s, ROW_CAP)
        record = rows[0] if rows else {}
        context = _format_hybrid_context(record)
        sources = _hybrid_sources(record)
        async for ev in self._synthesize("hybrid", query, context, sources, truncated, None):
            yield ev

    # --- shared --------------------------------------------------------------
    async def _execute(self, cypher, parameters, timeout_s, cap):
        # Use an explicit transaction with the timeout: AsyncSession.run's
        # `timeout=` kwarg folds into Cypher parameters ($timeout) rather than
        # setting a transaction timeout, so the L2 safety bound would be silently
        # inert. begin_transaction(timeout=...) is the real mechanism — the
        # database terminates transactions that run longer than the configured
        # timeout. Params are passed positionally (NOT as kwargs) for the same
        # reason: kwargs merge into Cypher parameters.
        driver = self._pool.get_driver(self._uri, self._username, self._password)
        session = driver.session(database=self._database)
        try:
            tx = await session.begin_transaction(timeout=timeout_s)
            try:
                result = await tx.run(cypher, parameters)
                rows = [r.data() async for r in result]
                await tx.commit()
            except Exception:
                await tx.rollback()
                raise
        finally:
            await session.close()
        return truncate_rows(rows, cap)

    async def _synthesize(self, method, query, context, sources, truncated, gen_usage):
        system = (
            "Answer the user's question using only the graph query results below. "
            "If the results are empty, say so plainly. Cite entities by title."
        )
        user = f"Graph query results:\n{context or '(none)'}\n\nQuestion: {query}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        start = time.time()
        accumulated = ""
        prompt_tokens = output_tokens = 0
        if gen_usage is not None:
            prompt_tokens += int(getattr(gen_usage, "prompt_tokens", 0) or 0)
            output_tokens += int(getattr(gen_usage, "completion_tokens", 0) or 0)
        async for chunk in await self._completion.completion_async(messages=messages, stream=True):
            delta = None
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = getattr(choices[0].delta, "content", None)
            if delta:
                accumulated += delta
                yield StreamDelta(text=delta)
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        yield StreamDone(
            answer=accumulated,
            method=method,
            elapsed_ms=round((time.time() - start) * 1000, 1),
            prompt_tokens=prompt_tokens or None,
            output_tokens=output_tokens or None,
            sources=sources or None,
            truncated=truncated,
        )


def _timeout_seconds(params) -> float:
    ms = (params.cypher_timeout_ms if params and params.cypher_timeout_ms is not None else _DEFAULT_TIMEOUT_MS)
    return ms / 1000.0


def _message_content(resp) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    return getattr(choices[0].message, "content", "") or ""


def _usage(resp):
    return getattr(resp, "usage", None)
