"""InstallTarget Protocol — what every tool adapter implements."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class InstallTarget(Protocol):
    """One adapter per agent host (Claude Code, opencode, …).

    Methods return a list of human-readable action lines (for dry-run preview
    and logging); they perform real filesystem/CLI side effects when
    ``dry_run`` is False.
    """

    name: str

    def register_mcp(
        self, repo_root: Path, api_url: str, scope: str, dry_run: bool,
    ) -> list[str]: ...

    def install_playbook(self, scope: str, dry_run: bool) -> list[str]: ...

    def uninstall(self, scope: str, dry_run: bool) -> list[str]: ...
