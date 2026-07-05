"""Build the shared MCP server config dict every tool registers."""

from __future__ import annotations

from pathlib import Path

# Name under which the MCP server is registered in every host.
MCP_SERVER_NAME = "kb-platform"


def build_mcp_config(repo_root: Path, api_url: str) -> dict:
    """Dev-mode config: run the MCP server via ``uv run`` from the checked-out repo.

    ``repo_root`` must be the absolute path to this repository (the one
    containing ``kb_platform/``). ``api_url`` is the KB Platform API base URL.
    """
    return {
        "command": "uv",
        "args": [
            "run", "--directory", str(repo_root),
            "python", "-m", "kb_platform.mcp",
        ],
        "env": {"KB_API_URL": api_url},
    }
