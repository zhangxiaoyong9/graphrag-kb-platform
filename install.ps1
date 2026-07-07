# Thin wrapper: forwards every flag to the Python installer.
# Requires uv on PATH (the MCP server itself runs via uv).
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
uv run --directory $RepoRoot python -m kb_platform.install @args
exit $LASTEXITCODE
