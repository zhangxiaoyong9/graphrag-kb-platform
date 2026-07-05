import sys
from pathlib import Path
from unittest.mock import patch

from kb_platform.install.platform import config_dir, home_dir


def test_home_dir_macos():
    with patch.object(sys, "platform", "darwin"):
        with patch.dict("os.environ", {"HOME": "/Users/alice"}, clear=True):
            assert home_dir() == Path("/Users/alice")


def test_home_dir_windows():
    with patch.object(sys, "platform", "win32"):
        with patch.dict("os.environ", {"APPDATA": r"C:\Users\alice\AppData\Roaming"}, clear=True):
            assert home_dir() == Path(r"C:\Users\alice\AppData\Roaming")


def test_config_dir_opencode_macos():
    with patch.object(sys, "platform", "darwin"):
        with patch.dict("os.environ", {"HOME": "/Users/alice", "XDG_CONFIG_HOME": ""}, clear=True):
            d = config_dir("opencode")
            assert d == Path("/Users/alice/.config/opencode")


def test_config_dir_opencode_windows():
    with patch.object(sys, "platform", "win32"):
        with patch.dict("os.environ", {"APPDATA": r"C:\Users\a\AppData\Roaming"}, clear=True):
            d = config_dir("opencode")
            assert d == Path(r"C:\Users\a\AppData\Roaming\opencode")
