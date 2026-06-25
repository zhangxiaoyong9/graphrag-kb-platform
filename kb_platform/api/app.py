"""FastAPI app factory with repo + data_root dependency injection."""

from fastapi import FastAPI

from kb_platform.api.routes_kbs import router
from kb_platform.db.repository import Repository


def create_app(repo: Repository, data_root: str = ".") -> FastAPI:
    """Build a FastAPI app with repo and data_root injected via app.state."""
    app = FastAPI(title="KB Platform")
    app.state.repo = repo
    app.state.data_root = data_root
    app.include_router(router)
    return app
