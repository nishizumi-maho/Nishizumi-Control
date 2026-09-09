"""Let the tests import the application on a machine without iRacing.

The build machine has every dependency, but a contributor checking the logic
out on Linux, or a CI job that only runs the tests, does not.  Only the modules
that cannot exist outside Windows are stubbed, and only when they are missing.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import types


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DATA = Path(tempfile.mkdtemp(prefix="dominant-control-tests-"))
os.environ.setdefault("DOMINANT_CONTROL_DATA_DIR", str(TEST_DATA / "app"))
os.environ.setdefault("DOMINANT_CONTROL_TOOLS_DATA_DIR", str(TEST_DATA / "tools"))


class _DummySDK:
    def __init__(self, *args, **kwargs):
        self.is_initialized = False
        self.is_connected = False

    def startup(self, *args, **kwargs):
        self.is_initialized = True
        self.is_connected = True
        return True

    def shutdown(self):
        self.is_initialized = False
        self.is_connected = False

    def __getitem__(self, key):
        return None

    def freeze_var_buffer_latest(self):
        return None

    def unfreeze_var_buffer_latest(self):
        return None


def _stub(name: str, **attributes) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)
    except Exception:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


_stub("irsdk", IRSDK=_DummySDK, IBT=_DummySDK)
_stub("keyboard", is_pressed=lambda *_args, **_kwargs: False)
