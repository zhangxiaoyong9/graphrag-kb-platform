"""FastAPI app factory with repo + data_root dependency injection.

When the built SPA (`web/dist`) exists, the app also serves it:
`/assets/*` are served as static files (Vite hashed assets), and a
catch-all `/{full_path:path}` returns `index.html` to support SPA history
routing (e.g. `/kbs/1/jobs/5`). API routers are registered BEFORE the
catch-all, so explicit API routes (like `GET /kbs`) always win.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kb_platform.api.routes_jobs import router as jobs_router
from kb_platform.api.routes_kbs import router
from kb_platform.db.repository import Repository

# Module-level so tests can monkeypatch `kb_platform.api.app.WEB_DIST`.
WEB_DIST = os.environ.get(
    "KB_WEB_DIST",
    str(Path(__file__).resolve().parents[2] / "web" / "dist"),
)


def create_app(repo: Repository, data_root: str = ".") -> FastAPI:
    """Build a FastAPI app with repo and data_root injected via app.state.

    If the SPA build directory (`WEB_DIST`) exists, static SPA hosting with
    history fallback is mounted AFTER all API routers, so API routes win.
    """
    app = FastAPI(title="KB Platform")
    app.state.repo = repo
    app.state.data_root = data_root

    # API routers registered first -> matched before the catch-all below.
    app.include_router(router)
    app.include_router(jobs_router)

    dist = Path(WEB_DIST)
    if dist.exists():
        assets_dir = dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str, request: Request):  # noqa: ARG001
            return FileResponse(dist / "index.html")

    return app
