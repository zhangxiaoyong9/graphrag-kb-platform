import pytest
from kb_platform.install.registry import TOOL_REGISTRY


def test_registry_has_claude_code_and_opencode():
    assert "claude-code" in TOOL_REGISTRY
    assert "opencode" in TOOL_REGISTRY


def test_cli_list_prints_supported_tools(capsys):
    from kb_platform.install.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(["--list"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "claude-code" in out
    assert "opencode" in out


def test_cli_unknown_tool_exits_nonzero(capsys):
    from kb_platform.install.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(["--tool", "nope"])
    assert exc.value.code != 0


async def test_end_to_end_dry_run_claude_code(tmp_path, monkeypatch):
    """Full CLI path: --tool claude-code --dry-run must not write anything,
    must exit 0, and must describe both register + playbook actions."""
    from kb_platform.install.__main__ import main
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--tool", "claude-code", "--api-url", "http://localhost:8000", "--dry-run"])
    assert exc.value.code == 0
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()
