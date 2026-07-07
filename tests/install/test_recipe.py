from kb_platform.install.recipe import load_recipe_text, render_for, RECIPE_TOOL_NAMES


def test_recipe_loads_nonempty_markdown():
    text = load_recipe_text()
    assert "# KB deep-research playbook" in text
    assert len(text) > 200  # real content, not a stub


def test_recipe_references_all_six_tools():
    # Every tool the MCP server exposes must appear in the recipe so the agent
    # learns to use it. Catches drift when tools are added/renamed.
    assert RECIPE_TOOL_NAMES == {
        "list_knowledge_bases", "query_knowledge_base", "get_kb_details",
        "list_documents", "get_document", "search_graph",
    }


def test_render_for_claude_code_has_frontmatter():
    out = render_for("claude-code")
    assert out.startswith("---\n")
    assert "name: kb-research" in out
    assert "description:" in out


def test_render_for_opencode_has_section_markers():
    out = render_for("opencode")
    assert "<!-- kb-platform:start -->" in out
    assert "<!-- kb-platform:end -->" in out


def test_render_for_unknown_tool_raises():
    import pytest
    with pytest.raises(ValueError):
        render_for("nope")
