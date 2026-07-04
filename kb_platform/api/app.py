"""FastAPI app factory with repo + data_root dependency injection.

When the built SPA (`web/dist`) exists, the app also serves it:
`/assets/*` are served as static files (Vite hashed assets), and a
catch-all `/{full_path:path}` returns `index.html` to support SPA history
routing (e.g. `/kbs/1/jobs/5`). API routers are registered BEFORE the
catch-all, so explicit API routes (like `GET /kbs`) always win.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kb_platform.api.routes_conversations import router as conversations_router
from kb_platform.api.routes_cost import router as cost_router
from kb_platform.api.routes_export import router as export_router
from kb_platform.api.routes_graph import router as graph_router
from kb_platform.api.routes_health import router as health_router
from kb_platform.api.routes_jobs import router as jobs_router
from kb_platform.api.routes_kbs import router
from kb_platform.api.routes_llm_health import router as llm_health_router
from kb_platform.api.routes_profiles import router as profiles_router
from kb_platform.api.routes_presets import router as presets_router
from kb_platform.api.routes_query import router as query_router
from kb_platform.api.routes_realtime import router as realtime_router
from kb_platform.db.repository import Repository
from kb_platform.query.engine import QueryEngine

# Module-level so tests can monkeypatch `kb_platform.api.app.WEB_DIST`.
WEB_DIST = os.environ.get(
    "KB_WEB_DIST",
    str(Path(__file__).resolve().parents[2] / "web" / "dist"),
)


def create_app(
    repo: Repository,
    data_root: str = ".",
    query_engine: QueryEngine | None = None,
    rewriter=None,
) -> FastAPI:
    """Build a FastAPI app with repo and data_root injected via app.state.

    If the SPA build directory (`WEB_DIST`) exists, static SPA hosting with
    history fallback is mounted AFTER all API routers, so API routes win.

    A ``lifespan`` starts/stops the realtime hub (WebSocket progress push). The
    hub lives on ``app.state.realtime`` and is only present when the app runs its
    lifespan (production uvicorn + ``with TestClient(app)`` in tests).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from kb_platform.api.realtime import RealtimeHub
        from kb_platform.llm.bootstrap import close_clients, stop_probe

        interval_ms = float(os.environ.get("KB_POLL_INTERVAL_MS", "500"))
        hub = RealtimeHub(repo=app.state.repo, interval=interval_ms / 1000.0)
        app.state.realtime = hub
        hub.start()
        try:
            yield
        finally:
            await hub.stop()
            # Stop the process-wide HealthProbe (started by bootstrap()).
            await stop_probe()
            # Close the shared httpx client pool.
            await close_clients()

    app = FastAPI(title="KB Platform", lifespan=lifespan)
    app.state.repo = repo
    app.state.data_root = data_root
    app.state.query_engine = (
        query_engine  # None = build real per-KB (production); non-None = injected (tests)
    )
    app.state.rewriter = rewriter  # None = build real per-KB (production); injected in tests

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Bind a per-request id, log start/done, stamp X-Request-ID on the response.

        NOTE: for SSE/StreamingResponse, ``call_next`` returns once headers are
        sent; the body streams after. So the "request done" line below marks
        dispatch time, not full stream completion. Per-stream timing lives in
        the route generator (see routes_query). Don't double-count.
        """
        from kb_platform.logging_config import bind_log_context

        request_id = uuid4().hex[:12]
        api_log = logging.getLogger("kb_platform.api")
        with bind_log_context(request_id=request_id):
            api_log.info("request start %s %s", request.method, request.url.path)
            t0 = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception:
                api_log.exception(
                    "request failed %s %s", request.method, request.url.path
                )
                raise
            duration_ms = (time.perf_counter() - t0) * 1000
            api_log.info(
                "request done %s %s -> %d %.1fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response

    # API routers registered first -> matched before the catch-all below.
    app.include_router(router)
    app.include_router(jobs_router)
    app.include_router(conversations_router)
    app.include_router(query_router)
    app.include_router(cost_router)
    app.include_router(health_router)
    app.include_router(export_router)
    app.include_router(graph_router)
    app.include_router(profiles_router)
    app.include_router(presets_router)
    app.include_router(realtime_router)
    app.include_router(llm_health_router)

    dist = Path(WEB_DIST)
    if dist.exists():
        assets_dir = dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str, request: Request):  # noqa: ARG001
            return FileResponse(dist / "index.html")

        # Browser navigation to a SPA route whose path is *also* a GET API endpoint
        # (e.g. /kbs, /query-presets) would otherwise get the API JSON, because API
        # routers are registered before the catch-all and win the path match —
        # breaking refresh/deep-link on those pages. Sec-Fetch-Mode: navigate is the
        # reliable browser-vs-XHR signal (address bar / link / refresh send it; fetch
        # sends `cors`/`same-origin`; Accept is NOT reliable — Chrome's fetch Accept
        # varies). Swap only when the API actually returned JSON, so browser-clicked
        # file downloads (e.g. /kbs/{id}/export → application/zip) stay intact.
        @app.middleware("http")
        async def spa_browser_nav_fallback(request: Request, call_next):
            response = await call_next(request)
            if (
                request.method == "GET"
                and request.headers.get("sec-fetch-mode") == "navigate"
                and "application/json" in response.headers.get("content-type", "")
            ):
                # no-store + Vary: Sec-Fetch-Mode: this URL also serves JSON to XHR,
                # so the navigation HTML must neither be cached nor reused for a fetch.
                return FileResponse(
                    dist / "index.html",
                    headers={"Cache-Control": "no-store", "Vary": "Sec-Fetch-Mode"},
                )
            return response

    return app
