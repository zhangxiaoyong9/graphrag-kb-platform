import json
import os
import shutil as _shutil

from kb_platform.install.mcp_config import MCP_SERVER_NAME
from kb_platform.install.tools.claude_code import ClaudeCodeAdapter


def test_register_mcp_project_scope_writes_dotmcpjson(tmp_path, monkeypatch):
    # Force the file-write fallback path even if `claude` CLI is installed.
    monkeypatch.setattr(_shutil, "which", lambda cmd: None)
    adapter = ClaudeCodeAdapter()
    repo = tmp_path / "repo"
    repo.mkdir()
    # project scope: cwd is the project root → writes ./.mcp.json
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        actions = adapter.register_mcp(repo, "http://localhost:8000", "project", dry_run=False)
    finally:
        os.chdir(cwd)
    cfg_path = tmp_path / ".mcp.json"
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text())
    assert MCP_SERVER_NAME in data["mcpServers"]
    assert data["mcpServers"][MCP_SERVER_NAME]["command"] == "uv"
    assert "kb_platform.mcp" in data["mcpServers"][MCP_SERVER_NAME]["args"]
    assert any("mcp.json" in a for a in actions)


def test_register_mcp_is_idempotent(tmp_path, monkeypatch):
    """Re-running must not duplicate the server entry."""
    monkeypatch.setattr(_shutil, "which", lambda cmd: None)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        adapter = ClaudeCodeAdapter()
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter.register_mcp(repo, "http://localhost:8000", "project", False)
        adapter.register_mcp(repo, "http://localhost:8000", "project", False)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert len(data["mcpServers"]) == 1
    finally:
        os.chdir(cwd)


def test_install_playbook_project_scope_writes_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(_shutil, "which", lambda cmd: None)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        adapter = ClaudeCodeAdapter()
        actions = adapter.install_playbook("project", dry_run=False)
        skill = tmp_path / ".claude" / "skills" / "kb-research" / "SKILL.md"
        assert skill.exists()
        text = skill.read_text()
        assert text.startswith("---\n")
        assert "name: kb-research" in text
        assert any("SKILL.md" in a for a in actions)
    finally:
        os.chdir(cwd)


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(_shutil, "which", lambda cmd: None)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        adapter = ClaudeCodeAdapter()
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter.register_mcp(repo, "http://localhost:8000", "project", dry_run=True)
        adapter.install_playbook("project", dry_run=True)
        assert not (tmp_path / ".mcp.json").exists()
        assert not (tmp_path / ".claude").exists()
    finally:
        os.chdir(cwd)


def test_uninstall_removes_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(_shutil, "which", lambda cmd: None)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        adapter = ClaudeCodeAdapter()
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter.register_mcp(repo, "http://localhost:8000", "project", False)
        adapter.install_playbook("project", False)
        adapter.uninstall("project", False)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert MCP_SERVER_NAME not in data.get("mcpServers", {})
        assert not (tmp_path / ".claude" / "skills" / "kb-research").exists()
    finally:
        os.chdir(cwd)
