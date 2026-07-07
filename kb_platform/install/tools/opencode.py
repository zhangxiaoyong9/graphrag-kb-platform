"""opencode adapter.

- MCP registration: merge into ``<config_dir>/opencode/opencode.json`` (key
  ``mcp`` — verified against the opencode 1.1.x published config schema, which
  defines ``mcp`` as a map of ``McpLocalConfig``; local servers use a ``command``
  list + optional ``environment``).
- Playbook: merge a section-marker-wrapped block into ``AGENTS.md`` (cwd for
  project scope; ``<config_dir>/opencode/agent.md`` for user scope).

All writes are idempotent (server keyed by name; section markers)."""

from __future__ import annotations

import json
from pathlib import Path

from kb_platform.install.mcp_config import MCP_SERVER_NAME, build_mcp_config
from kb_platform.install.recipe import render_for

# Top-level key for MCP servers in opencode.json. Verified via
# https://opencode.ai/config.json (McpLocalConfig: {type:"local", command:[...]}).
_MCP_KEY = "mcp"
_START = "<!-- kb-platform:start -->"
_END = "<!-- kb-platform:end -->"


class OpenCodeAdapter:
    name = "opencode"

    # --- paths ------------------------------------------------------------
    def _config_file(self) -> Path:
        from kb_platform.install.platform import config_dir
        return config_dir("opencode") / "opencode.json"

    def _agents_file(self, scope: str) -> Path:
        if scope == "user":
            from kb_platform.install.platform import config_dir
            return config_dir("opencode") / "agent.md"
        return Path.cwd() / "AGENTS.md"

    # --- MCP registration -------------------------------------------------
    def register_mcp(self, repo_root: Path, api_url: str, scope: str, dry_run: bool) -> list[str]:
        cfg = build_mcp_config(repo_root, api_url)
        # opencode expects command as a list for local servers.
        opencode_entry: dict = {
            "type": "local",
            "command": [cfg["command"], *cfg["args"]],
            "enabled": True,
        }
        if cfg.get("env"):
            opencode_entry["environment"] = cfg["env"]
        path = self._config_file()
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        servers = data.setdefault(_MCP_KEY, {})
        servers[MCP_SERVER_NAME] = opencode_entry  # idempotent
        if dry_run:
            return [f"would merge {MCP_SERVER_NAME} into {path}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        return [f"merged {MCP_SERVER_NAME} into {path}"]

    # --- playbook ---------------------------------------------------------
    def install_playbook(self, scope: str, dry_run: bool) -> list[str]:
        path = self._agents_file(scope)
        block = render_for("opencode")  # already has start/end markers
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if _START in text:
                pre = text[: text.index(_START)]
                post = text[text.index(_END) + len(_END):]
                new = pre + block + post
            else:
                new = text.rstrip() + "\n\n" + block
        else:
            new = block
        if dry_run:
            return [f"would merge playbook into {path}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        return [f"merged playbook into {path}"]

    # --- uninstall --------------------------------------------------------
    def uninstall(self, scope: str, dry_run: bool) -> list[str]:
        actions: list[str] = []
        path = self._config_file()
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
            if MCP_SERVER_NAME in data.get(_MCP_KEY, {}):
                del data[_MCP_KEY][MCP_SERVER_NAME]
                if not dry_run:
                    path.write_text(json.dumps(data, indent=2))
                actions.append(f"removed {MCP_SERVER_NAME} from {path}")
        agents = self._agents_file(scope)
        if agents.exists():
            text = agents.read_text(encoding="utf-8")
            if _START in text and _END in text:
                pre = text[: text.index(_START)]
                post = text[text.index(_END) + len(_END):]
                if not dry_run:
                    agents.write_text((pre + post).strip() + "\n", encoding="utf-8")
                actions.append(f"removed playbook section from {agents}")
        return actions or [f"nothing to uninstall for {self.name}"]
