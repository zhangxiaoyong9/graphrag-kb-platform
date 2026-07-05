"""MCP query server: expose GraphRAG knowledge-base search to AI agents.

This is a **thin HTTP proxy**. The MCP process holds only an ``httpx`` client and
forwards to the running KB Platform API server. It never imports graphrag — all
settings resolution / profile decryption / engine building stays in the API
server (the single query entry point), so there is zero duplicated logic.

The ``KbApiClient`` is the testable seam (inject an ``httpx`` transport to hit an
in-process app). The tool logic is a set of plain ``async`` functions closing
over a client; ``build_mcp_server`` registers them on an MCP server. Run the
stdio entry point with ``python -m kb_platform.mcp``.
"""

from __future__ import annotations

from typing import Literal

import httpx

QueryMethod = Literal["local", "global", "drift", "basic"]


class KbApiError(Exception):
    """Raised when the KB Platform API is unreachable or returns a non-2xx status."""


class KbApiClient:
    """Async HTTP client for the KB Platform API — the seam the MCP tools close over."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 180.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def list_kbs(self) -> list[dict]:
        """GET /kbs → [{id, name, method}, ...]."""
        return await self._get_json("/kbs")

    async def query(self, kb_id: int, method: str, query: str) -> dict:
        """POST /kbs/{id}/query → aggregate the SSE stream into a single result dict.

        The endpoint streams ``meta``/``delta``/``done``/``error`` events; we
        concatenate the ``delta`` text and lift sources/metadata off ``done`` so
        MCP tool callers still get one ``{answer, method, sources, ...}`` object.
        """
        from kb_platform.api.sse import iter_sse_events

        path = f"/kbs/{kb_id}/query"
        try:
            async with self._http.stream(
                "POST", path, json={"method": method, "query": query}
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:200]
                    raise KbApiError(f"POST {path} -> {resp.status_code}: {body}")
                answer_parts: list[str] = []
                result: dict = {"answer": "", "method": method}
                async for event, data in iter_sse_events(resp.aiter_lines()):
                    if event == "delta":
                        answer_parts.append(data.get("text", ""))
                    elif event == "done":
                        result = data.get("result") or result
                    elif event == "error":
                        result["error"] = data.get("message", "stream error")
                result["answer"] = result.get("answer") or "".join(answer_parts)
                if not result.get("method"):
                    result["method"] = method
                return result
        except httpx.HTTPError as exc:
            raise KbApiError(f"POST {path} failed: {exc}") from exc

    async def get_kb(self, kb_id: int) -> dict:
        """GET /kbs/{id} + /kbs/{id}/stats → {id, name, method, stats: {...}}.

        Stats fields are None when the KB has no snapshot yet (unindexed).
        """
        detail = await self._get_json(f"/kbs/{kb_id}")
        stats = await self._get_json(f"/kbs/{kb_id}/stats")
        if not isinstance(detail, dict) or not isinstance(stats, dict):
            raise KbApiError(f"unexpected response shape for kb {kb_id}")
        return {"id": detail["id"], "name": detail["name"],
                "method": detail["method"], "stats": stats}

    async def list_documents(self, kb_id: int) -> list[dict]:
        """GET /kbs/{id}/documents → [{id, title, status, bytes, chunk_count}, ...]."""
        data = await self._get_json(f"/kbs/{kb_id}/documents")
        if not isinstance(data, list):
            raise KbApiError(f"unexpected response shape for documents of kb {kb_id}")
        return data

    async def get_document(self, kb_id: int, doc_id: int) -> dict:
        """GET /kbs/{id}/documents/{doc_id} → {id, title, text, citations: [...], ...}."""
        return await self._get_json(f"/kbs/{kb_id}/documents/{doc_id}")

    async def search_graph(self, kb_id: int, q: str, hop: int = 1, limit: int = 200) -> dict:
        """GET /kbs/{id}/graph?q=&hop=&limit= → {nodes: [...], edges: [...]}."""
        from urllib.parse import quote

        path = f"/kbs/{kb_id}/graph?q={quote(q)}&hop={hop}&limit={limit}"
        data = await self._get_json(path)
        if not isinstance(data, dict):
            raise KbApiError(f"unexpected response shape for graph of kb {kb_id}")
        return data

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _get_json(self, path: str) -> list | dict:
        try:
            resp = await self._http.get(path)
        except httpx.HTTPError as exc:  # connection refused, timeout, DNS, ...
            raise KbApiError(f"GET {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise KbApiError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def _post_json(self, path: str, body: dict) -> dict:
        try:
            resp = await self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise KbApiError(f"POST {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise KbApiError(f"POST {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()


async def list_knowledge_bases(client: KbApiClient) -> list[dict]:
    """List knowledge bases the agent can query.

    Returns ``[{id, name, method}, ...]``; an empty list when there are none. On
    API failure returns ``[{"error": "..."}]`` so the agent is told rather than
    receiving an exception.
    """
    try:
        return await client.list_kbs()
    except KbApiError as exc:
        return [{"error": str(exc)}]


async def query_knowledge_base(
    client: KbApiClient,
    kb_id: int,
    query: str,
    method: QueryMethod = "local",
) -> dict:
    """Query one knowledge base and return an answer with cited sources.

    Returns ``{answer, method, sources: [{kind, name, text}, ...]}`` on success,
    plus an ``error`` key when the API or the search reports one. Returns
    ``{"error": "..."}`` (no answer) if the API is unreachable.
    """
    try:
        res = await client.query(kb_id=kb_id, method=method, query=query)
    except KbApiError as exc:
        return {"error": str(exc)}
    out: dict = {"answer": res.get("answer", ""), "method": res.get("method", method)}
    if res.get("error"):
        out["error"] = res["error"]
    out["sources"] = [
        {"kind": s.get("kind"), "name": s.get("name"), "text": s.get("text")}
        for s in (res.get("sources") or [])
    ]
    return out


def build_mcp_server(client: KbApiClient):
    """Register the KB query tools on an MCP server (FastMCP) and return it.

    The wrappers close over ``client``; their MCP tool names are set explicitly
    so they need not collide with the module-level logic functions of the same
    name (those take ``client`` as a positional arg, these do not).
    """
    mcp = _new_mcp_server("kb-platform")

    @mcp.tool(name="list_knowledge_bases")
    async def _list_tool() -> list[dict]:
        """List available knowledge bases.

        Returns one object per KB: {"id", "name", "method"}. Call this first to
        discover which knowledge bases exist and pick a kb_id before querying.
        """
        return await list_knowledge_bases(client)

    @mcp.tool(name="query_knowledge_base")
    async def _query_tool(
        kb_id: int,
        query: str,
        method: QueryMethod = "local",
    ) -> dict:
        """Search a knowledge base and return an answer with cited sources.

        Args:
            kb_id: Knowledge base id (find it via list_knowledge_bases).
            query: The natural-language question.
            method: Search strategy — "local" (default), "global", "drift", or
                "basic". "global" and "drift" require community reports produced
                during indexing; otherwise they return an error.

        Returns {"answer", "method", "sources": [{"kind","name","text"}, ...]},
        plus an "error" key only when something went wrong.
        """
        return await query_knowledge_base(client, kb_id=kb_id, query=query, method=method)

    return mcp


def _new_mcp_server(name: str):
    """Import shim for the MCP high-level server class.

    Prefers the long-standing ``mcp.server.fastmcp.FastMCP``; tolerates a future
    rename to ``mcp.server.mcpserver.MCPServer``. Raises a clear error if the
    ``mcp`` package is not installed.
    """
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError:
        try:
            from mcp.server.mcpserver import MCPServer as _Server  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for the MCP query server. "
                "Install it with: uv sync --extra mcp"
            ) from exc
    return _Server(name)
