from pathlib import Path
from kb_platform.install.mcp_config import build_mcp_config


def test_build_mcp_config_shape():
    cfg = build_mcp_config(Path("/repo"), "http://localhost:8000")
    assert cfg["command"] == "uv"
    assert cfg["args"][:4] == ["run", "--directory", "/repo", "python"]
    assert "kb_platform.mcp" in cfg["args"]
    assert cfg["env"]["KB_API_URL"] == "http://localhost:8000"


def test_build_mcp_config_no_proxy_env():
    """CLAUDE.md gotcha: localhost must not go through proxy. Config must not
    set all_proxy/http_proxy/https_proxy."""
    cfg = build_mcp_config(Path("/repo"), "http://localhost:8000")
    for k in ("all_proxy", "http_proxy", "https_proxy"):
        assert k not in cfg["env"]
