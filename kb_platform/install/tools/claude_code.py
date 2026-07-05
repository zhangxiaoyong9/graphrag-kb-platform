"""Claude Code adapter.

Two scopes:
- ``project`` → ``./.mcp.json`` (mcpServers) + ``./.claude/skills/kb-research/SKILL.md``
- ``user``    → ``~/.claude.json`` (mcpServers) + ``~/.claude/skills/kb-research/SKILL.md``

Prefers the ``claude mcp add`` CLI when available; falls back to writing the
JSON files directly. All write paths are idempotent."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kb_platform.install.mcp_config import MCP_SERVER_NAME, build_mcp_config
from kb_platform.install.recipe import render_for

SKILL_NAME = "kb-research"


class ClaudeCodeAdapter:
    name = "claude-code"

    # --- scope resolution -------------------------------------------------
    def _mcp_json_path(self, scope: str) -> Path:
        if scope == "user":
            from kb_platform.install.platform import home_dir
            return home_dir() / ".claude.json"
        return Path.cwd() / ".mcp.json"

    def _skill_dir(self, scope: str) -> Path:
        if scope == "user":
            from kb_platform.install.platform import home_dir
            return home_dir() / ".claude" / "skills" / SKILL_NAME
        return Path.cwd() / ".claude" / "skills" / SKILL_NAME

    # --- MCP registration -------------------------------------------------
    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        cfg = build_mcp_config(repo_root, api_url)
        path = self._mcp_json_path(scope)
        cli_scope = "user" if scope == "user" else "project"
        if shutil.which("claude") and not dry_run:
            args = ["claude", "mcp", "add", MCP_SERVER_NAME, "--scope", cli_scope,
                    "--"] + [cfg["command"], *cfg["args"]]
            try:
                subprocess.run(args, check=True, capture_output=True)
                return [f"registered MCP via `claude mcp add` → {path}"]
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # fall through to file write
        return self._write_mcp_json(path, cfg, dry_run)

    def _write_mcp_json(self, path: Path, cfg: dict, dry_run: bool) -> list[str]:
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        servers = data.setdefault("mcpServers", {})
        servers[MCP_SERVER_NAME] = cfg  # idempotent: same key overwrites
        if dry_run:
            return [f"would write {path}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        return [f"wrote {path}"]

    # --- playbook ---------------------------------------------------------
    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        skill_path = self._skill_dir(scope) / "SKILL.md"
        content = render_for("claude-code")
        if dry_run:
            return [f"would write {skill_path}"]
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")
        return [f"wrote {skill_path}"]

    # --- uninstall --------------------------------------------------------
    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        actions: list[str] = []
        path = self._mcp_json_path(scope)
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
            if MCP_SERVER_NAME in data.get("mcpServers", {}):
                del data["mcpServers"][MCP_SERVER_NAME]
                if not dry_run:
                    path.write_text(json.dumps(data, indent=2))
                actions.append(f"removed {MCP_SERVER_NAME} from {path}")
        skill_dir = self._skill_dir(scope)
        if skill_dir.exists():
            if not dry_run:
                shutil.rmtree(skill_dir)
            actions.append(f"removed {skill_dir}")
        return actions or [f"nothing to uninstall for {self.name}"]
