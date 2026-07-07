import json
from unittest.mock import patch

from kb_platform.install.mcp_config import MCP_SERVER_NAME
from kb_platform.install.tools.opencode import OpenCodeAdapter


def test_register_mcp_merges_into_opencode_json(tmp_path):
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        adapter = OpenCodeAdapter()
        actions = adapter.register_mcp(tmp_path / "repo", "http://localhost:8000",
                                       "user", dry_run=False)
    cfg = json.loads((tmp_path / "opencode" / "opencode.json").read_text())
    servers = cfg["mcp"]  # top-level key confirmed via opencode.ai/config.json schema
    assert MCP_SERVER_NAME in servers
    assert servers[MCP_SERVER_NAME]["command"][0] == "uv"
    assert any("opencode.json" in a for a in actions)


def test_register_mcp_preserves_existing_servers(tmp_path):
    """Merge — don't clobber user's other MCP servers."""
    cfg_path = tmp_path / "opencode" / "opencode.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"mcp": {"other": {"type": "local"}}}))
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        OpenCodeAdapter().register_mcp(tmp_path / "repo", "http://localhost:8000",
                                       "user", dry_run=False)
    cfg = json.loads(cfg_path.read_text())
    assert "other" in cfg["mcp"]  # preserved
    assert MCP_SERVER_NAME in cfg["mcp"]


def test_install_playbook_merges_agents_md_idempotently(tmp_path):
    import os
    os.chdir(tmp_path)
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        adapter = OpenCodeAdapter()
        adapter.install_playbook("project", dry_run=False)
        adapter.install_playbook("project", dry_run=False)  # run twice
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.count("<!-- kb-platform:start -->") == 1  # no duplication
    assert "playbook" in text.lower()


def test_uninstall_removes_section_and_server(tmp_path):
    import os
    os.chdir(tmp_path)
    with patch("kb_platform.install.platform.config_dir",
               lambda tool: tmp_path / tool):
        adapter = OpenCodeAdapter()
        adapter.register_mcp(tmp_path / "repo", "http://localhost:8000", "user", False)
        adapter.install_playbook("project", False)
        adapter.uninstall("project", False)
    cfg = json.loads((tmp_path / "opencode" / "opencode.json").read_text())
    assert MCP_SERVER_NAME not in cfg.get("mcp", {})
    assert "<!-- kb-platform:start -->" not in (tmp_path / "AGENTS.md").read_text()
