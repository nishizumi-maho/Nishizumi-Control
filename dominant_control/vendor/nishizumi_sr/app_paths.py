"""Filesystem locations that behave correctly both from source and frozen.

Running from a checkout, everything sits next to the code and that is fine.
Once PyInstaller freezes the app into a single .exe the picture splits in two:

* **Read-only bundled resources** (the official corner table) are unpacked into
  a temporary directory that is deleted on exit and is not writable. They must
  be located through ``sys._MEIPASS``.
* **Writable state** (settings, learned tracks, session history, logs) must NOT
  go there, or it silently vanishes when the app closes. It belongs in the
  user's own application-data directory.

Keeping both behind these two helpers means no module has to know whether it is
frozen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "NishizumiSR"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_path(*parts: str) -> Path:
    """A read-only file shipped with the app (bundled into the .exe)."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def user_data_dir() -> Path:
    """Where the app may write. Created on first use.

    Frozen: %LOCALAPPDATA%\\NishizumiSR on Windows, ~/.local/share/NishizumiSR
    elsewhere. From source: the checkout itself, so a developer's files stay
    where they expect them.
    """
    if not is_frozen():
        return Path(__file__).resolve().parent

    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_file(*parts: str) -> Path:
    """A writable path under the user data directory, parents created."""
    path = user_data_dir().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
