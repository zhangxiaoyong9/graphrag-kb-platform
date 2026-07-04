"""Entry point: ``python -m kb_platform.mcp`` — stdio MCP query server.

A thin proxy that forwards to the KB Platform HTTP API. Configure the API
address with ``--api-url`` or the ``KB_API_URL`` env var
(default ``http://127.0.0.1:8000``).

Wire it into an MCP client (e.g. Claude Desktop / Claude Code) like::

    {
      "mcpServers": {
        "kb-platform": {
          "command": "uv",
          "args": ["run", "--directory", "/path/to/graphrag-kb-platform",
                   "python", "-m", "kb_platform.mcp"],
          "env": {"KB_API_URL": "http://127.0.0.1:8000"}
        }
      }
    }
"""

import argparse
import os

from kb_platform.mcp.server import KbApiClient, build_mcp_server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kb_platform.mcp",
        description="stdio MCP server exposing KB Platform search to AI agents.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("KB_API_URL", "http://127.0.0.1:8000"),
        help="KB Platform API base URL (default: $KB_API_URL or http://127.0.0.1:8000).",
    )
    args = parser.parse_args()

    from kb_platform.logging_config import setup_logging

    # stdio transport: setup_logging('mcp') attaches only stderr + file handlers,
    # NEVER stdout (stdout is the JSON-RPC channel — see logging_config stdout guard).
    setup_logging("mcp")

    server = build_mcp_server(KbApiClient(args.api_url))
    # stdio is the default; pass it explicitly for clarity and version-stability.
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
