"""Tool adapter registry. Adapters are imported lazily so a missing optional
dep (e.g. the ``claude`` CLI) doesn't break ``--list`` or the other tool."""

from __future__ import annotations


def _load_registry() -> dict[str, type]:
    registry: dict[str, type] = {}
    try:
        from kb_platform.install.tools.claude_code import ClaudeCodeAdapter
        registry["claude-code"] = ClaudeCodeAdapter
    except ImportError:
        pass
    try:
        from kb_platform.install.tools.opencode import OpenCodeAdapter
        registry["opencode"] = OpenCodeAdapter
    except ImportError:
        pass
    return registry


# Module-level lazy registry: populated on first access.
class _LazyRegistry(dict):
    def _ensure_loaded(self) -> None:
        if not self:
            self.update(_load_registry())

    def __contains__(self, key) -> bool:  # type: ignore[override]
        self._ensure_loaded()
        return super().__contains__(key)

    def __iter__(self):
        self._ensure_loaded()
        return super().__iter__()

    def __missing__(self, key):  # noqa: D401
        self._ensure_loaded()
        raise KeyError(key)


TOOL_REGISTRY = _LazyRegistry()
