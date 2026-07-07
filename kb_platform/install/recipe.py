"""Neutral agent recipe (single source of truth) + per-tool renderer.

``recipe.md`` is the playbook content. ``render_for(tool)`` wraps it for a
specific agent host: Claude Code wants SKILL.md frontmatter; opencode wants a
section-marker-wrapped block for AGENTS.md.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

RECIPE_DIR = Path(__file__).parent
RECIPE_FILE = RECIPE_DIR / "recipe.md"

# Tool names the recipe references. The consistency test asserts this equals
# the MCP server's tool set — update both when adding/renaming a tool.
RECIPE_TOOL_NAMES: set[str] = {
    "list_knowledge_bases", "query_knowledge_base", "get_kb_details",
    "list_documents", "get_document", "search_graph",
}

_SUPPORTED_TOOLS = {"claude-code", "opencode"}


@lru_cache(maxsize=1)
def load_recipe_text() -> str:
    return RECIPE_FILE.read_text(encoding="utf-8")


def render_for(tool: str) -> str:
    """Render the recipe for a specific agent host.

    - ``claude-code`` → SKILL.md with YAML frontmatter (name/description).
    - ``opencode`` → section-marker-wrapped block for AGENTS.md.
    Raises ``ValueError`` for unknown tools.
    """
    if tool not in _SUPPORTED_TOOLS:
        raise ValueError(f"unknown tool {tool!r}; expected one of {_SUPPORTED_TOOLS}")
    body = load_recipe_text()
    if tool == "claude-code":
        return (
            "---\n"
            "name: kb-research\n"
            "description: Deep-research retrieval over indexed GraphRAG "
            "knowledge bases — discover, query, verify, cite.\n"
            "---\n\n"
            f"{body}\n"
        )
    # opencode: section markers so install is idempotent (replace between markers)
    return f"<!-- kb-platform:start -->\n{body}\n<!-- kb-platform:end -->\n"
