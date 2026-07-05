"""Per-OS path resolution for installer config targets."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def home_dir() -> Path:
    """User home/config root: ``$HOME`` on mac/linux, ``%APPDATA%`` on windows."""
    if is_windows():
        return Path(os.environ.get("APPDATA") or str(Path.home()))
    return Path(os.environ.get("HOME") or str(Path.home()))


def config_dir(tool: str) -> Path:
    """Config directory for a tool, per OS convention.

    mac/linux: ``~/.config/<tool>`` (XDG).
    windows: ``%APPDATA%\\<tool>``.
    """
    if is_windows():
        # Windows uses backslash; build via string concat so the path round-trips
        # identically on non-windows hosts running tests with patched sys.platform.
        return Path(str(home_dir()) + "\\" + tool)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(str(home_dir()) + "/.config")
    return Path(str(base) + "/" + tool)
