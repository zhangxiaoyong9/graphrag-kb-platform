# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License
"""HTTP API server entry point.

Usage:
    python -m kb_platform.server [db_path] [data_root] [host] [port]

- ``db_path``    defaults to ``kb.db``
- ``data_root``  defaults to ``.``  (index parquet output + LanceDB ``vectors/``
                 are written here; currently shared across KBs, so use a
                 dedicated directory per deployment)
- ``host``       defaults to ``127.0.0.1``
- ``port``       defaults to ``8000``

Run ``alembic upgrade head`` (once) to create the schema before starting.
The background worker is a separate process: ``python -m kb_platform.worker``.
"""

import os
import sys


def main() -> None:
    import uvicorn

    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine
    from kb_platform.db.repository import Repository
    from kb_platform.llm.bootstrap import bootstrap as _bootstrap_llm
    from kb_platform.logging_config import setup_logging

    # Centralized logging FIRST, before any other code logs. uvicorn is told
    # log_config=None below so it doesn't reconfigure logging; its `uvicorn` /
    # `uvicorn.access` loggers propagate to our root handlers instead.
    setup_logging("server")

    # Register kb_native completion/embedding factories before any adapter is
    # built (idempotent). Import is inside main() to keep module import clean.
    _bootstrap_llm()

    db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"
    os.environ.setdefault("KB_DB_URL", f"sqlite:///{db}")
    data_root = sys.argv[2] if len(sys.argv) > 2 else "."
    host = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 8000

    repo = Repository(create_engine(f"sqlite:///{db}"))
    app = create_app(repo, data_root=data_root)
    # Force the native asyncio loop: graphrag_llm runs nest_asyncio.apply()
    # at import, which cannot patch uvloop (uvicorn auto-selects uvloop when
    # installed -> "Can't patch loop of type <class 'uvloop.Loop'>").
    # log_config=None keeps uvicorn from reconfiguring logging so our root
    # handlers (set up above via setup_logging) own the layout and the
    # `uvicorn` / `uvicorn.access` loggers propagate to them.
    uvicorn.run(app, host=host, port=port, loop="asyncio", log_config=None)


if __name__ == "__main__":
    main()
