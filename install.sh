#!/usr/bin/env bash
# Thin wrapper: forwards every flag to the Python installer.
# Requires uv on PATH (the MCP server itself runs via uv).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --directory "$REPO_ROOT" python -m kb_platform.install "$@"
