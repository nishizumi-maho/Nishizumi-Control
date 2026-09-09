"""Make the vendored Nishizumi SR modules importable without editing them.

Nishizumi SR is a standalone application, so its modules import each other by
the names they have in that project — ``from sr.model import ...``,
``from overlay.styles import stylesheet``, ``from app_paths import user_file``.
Keeping the files byte-for-byte (the rule in ``vendor/nishizumi_sr/
PROVENANCE.md``, same as for the Nishizumi Tools sources) therefore means those
top-level names have to resolve, which is what :func:`bootstrap` arranges.

Two details are deliberate:

* the vendored folder is **appended** to ``sys.path``, never inserted at the
  front, so anything the application already imports under one of those names
  keeps winning;
* ``app_paths.user_data_dir`` is redirected **before** the first vendored
  import.  ``tracks.database``, ``sr.history`` and friends resolve their paths
  at import time, and left alone they would write the learned corner table and
  the session history next to the vendored source — inside the installation
  folder, which the installer replaces on every upgrade.

Frozen builds never take the ``sys.path`` route: PyInstaller collects these
modules into the archive from the ``pathex`` entry in ``build/
DominantControl.spec``, so the folder does not exist on disk any more and
``import sr.calculator`` is served by the frozen importer instead.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from ...paths import tools_data_dir

VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "nishizumi_sr"

#: Top-level names the vendored tree claims once it is importable.
VENDORED_MODULES = ("app_paths", "iracing", "overlay", "sr", "tracks")

_ready = False


def data_dir() -> Path:
    """The folder the SR estimator may write in: history, tracks, overlay state.

    A directory, never a file path — ``tools_data_dir`` creates every component
    it is given, so a name passed through here would come back as a folder.
    """
    return tools_data_dir("nishizumi_sr")


def bootstrap() -> None:
    """Prepare the import path and the writable folder. Safe to call repeatedly."""
    global _ready
    if _ready:
        return

    root = str(VENDOR_ROOT)
    if VENDOR_ROOT.is_dir() and root not in sys.path:
        sys.path.append(root)

    app_paths = importlib.import_module("app_paths")
    app_paths.user_data_dir = data_dir  # type: ignore[assignment]
    _ready = True


def load(name: str) -> ModuleType:
    """Import a vendored module by its original Nishizumi SR name."""
    bootstrap()
    return importlib.import_module(name)


__all__ = ["VENDORED_MODULES", "VENDOR_ROOT", "bootstrap", "data_dir", "load"]
