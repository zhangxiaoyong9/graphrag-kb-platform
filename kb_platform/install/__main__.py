"""CLI entry: ``uv run python -m kb_platform.install --tool <name> [options]``.

Exit codes: 0 success, 1 bad args, 2 unknown tool, 3 install action failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kb_platform.install.registry import TOOL_REGISTRY


def _repo_root() -> Path:
    """This package's repo root (parent of kb_platform/)."""
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kb_platform.install",
        description="Install the KB Platform MCP server + agent playbook into an AI tool.",
    )
    parser.add_argument("--tool", help="one of: claude-code, opencode, all")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000",
                        help="KB Platform API base URL")
    parser.add_argument("--scope", choices=["user", "project"], default="project",
                        help="where to install (default: project)")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the MCP registration + playbook")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview actions without writing")
    parser.add_argument("--list", action="store_true",
                        help="list supported tools and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name in sorted(TOOL_REGISTRY):
            print(name)
        sys.exit(0)

    if not args.tool:
        parser.error("--tool is required (or pass --list)")
        sys.exit(1)  # parser.error exits already; for clarity

    tools = list(TOOL_REGISTRY) if args.tool == "all" else [args.tool]
    for t in tools:
        if t not in TOOL_REGISTRY:
            print(f"error: unknown tool {t!r}. Supported: {sorted(TOOL_REGISTRY)}",
                  file=sys.stderr)
            sys.exit(2)

    repo_root = _repo_root()
    failed = False
    for t in tools:
        target = TOOL_REGISTRY[t]()
        try:
            if args.uninstall:
                actions = target.uninstall(args.scope, args.dry_run)
            else:
                actions = target.register_mcp(repo_root, args.api_url, args.scope, args.dry_run)
                actions += target.install_playbook(args.scope, args.dry_run)
            for a in actions:
                print(f"[{t}] {a}")
        except Exception as exc:  # noqa: BLE001 - report, don't crash mid-loop
            print(f"[{t}] FAILED: {exc}", file=sys.stderr)
            failed = True

    sys.exit(3 if failed else 0)


if __name__ == "__main__":
    main()
