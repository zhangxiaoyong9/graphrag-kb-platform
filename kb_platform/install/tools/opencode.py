"""opencode adapter (stub — filled in Task 6)."""

from __future__ import annotations

from pathlib import Path


class OpenCodeAdapter:
    name = "opencode"

    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would register MCP for {self.name}"]

    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would install playbook for {self.name}"]

    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        return [f"(stub) would uninstall {self.name}"]
