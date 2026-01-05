"""Global configuration and resource helpers."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from typing import Any, Dict, Optional

APP_NAME = "DominantControl"
APP_VERSION = "3.0.0"
APP_FOLDER = "DominantControl"
BASE_PATH = os.getenv("APPDATA") or os.path.expanduser("~")
CONFIG_FOLDER = os.path.join(BASE_PATH, APP_FOLDER, "configs")
os.makedirs(CONFIG_FOLDER, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_FOLDER, "config_v3.json")
PENDING_SCAN_FILE = os.path.join(CONFIG_FOLDER, "pending_scan.flag")
ICON_CANDIDATES = ["DominantControl.ico", "DominantControl.png", "app.ico", "app.png"]

DEFAULT_OVERLAY_FEEDBACK: Dict[str, Any] = {
    "abs_hold_s": 0.35,
    "tc_hold_s": 0.35,
    "wheelspin_slip": 0.18,
    "wheelspin_hold_s": 0.25,
    "lockup_slip": 0.2,
    "lockup_hold_s": 0.25,
    "cooldown_s": 6.0,
}

DEFAULT_TIMING_PROFILES: Dict[str, Dict[str, Any]] = {
    "aggressive": {
        "press_min_ms": 10,
        "press_max_ms": 10,
        "interval_min_ms": 10,
        "interval_max_ms": 10,
        "random_enabled": False,
        "random_range_ms": 10,
    },
    "casual": {
        "press_min_ms": 80,
        "press_max_ms": 80,
        "interval_min_ms": 100,
        "interval_max_ms": 100,
        "random_enabled": False,
        "random_range_ms": 10,
    },
    "relaxed": {
        "press_min_ms": 150,
        "press_max_ms": 150,
        "interval_min_ms": 200,
        "interval_max_ms": 200,
        "random_enabled": False,
        "random_range_ms": 10,
    },
    "bot": {
        "press_min_ms": 20,
        "press_max_ms": 20,
        "interval_min_ms": 20,
        "interval_max_ms": 20,
        "random_enabled": False,
        "random_range_ms": 10,
    },
    "custom": {
        "press_min_ms": 60,
        "press_max_ms": 80,
        "interval_min_ms": 60,
        "interval_max_ms": 90,
        "random_enabled": False,
        "random_range_ms": 10,
    },
}

GLOBAL_TIMING: Dict[str, Any] = {
    "profile": "aggressive",  # "aggressive", "casual", "relaxed", "custom", "bot"
    "profile_customized": {
        profile: profile == "custom"
        for profile in DEFAULT_TIMING_PROFILES
    },
    "profile_settings": {
        profile: settings.copy()
        for profile, settings in DEFAULT_TIMING_PROFILES.items()
    },
}

TTS_STATE: Dict[str, Any] = {
    "last_text": "",
    "last_time": 0.0,
    "cooldown_s": 1.2,
}

_TTS_ENGINE = None
_TTS_THREAD: Optional[threading.Thread] = None
_TTS_QUEUE: "queue.Queue[str]" = queue.Queue()
_TTS_LOCK = threading.Lock()
TTS_OUTPUT_DEVICE_INDEX: Optional[int] = None

VOICE_TUNING_DEFAULTS: Dict[str, Any] = {
    "ambient_duration": 0.2,  # Time to calibrate ambient noise
    "initial_timeout": 1.0,  # First listen timeout (seconds)
    "continuous_timeout": 0.8,  # Subsequent listen timeout (seconds)
    "phrase_time_limit": 1.2,  # Max length of each phrase (seconds)
    "energy_threshold": None,  # Static mic threshold (None = auto)
    "dynamic_energy": True,  # Auto-adjust mic threshold
}

if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write("{}")


def resolve_resource_path(filename: str) -> Optional[str]:
    """Return the first existing path for a bundled or local resource."""

    possible_roots = [
        getattr(sys, "_MEIPASS", None),
        os.path.dirname(sys.argv[0]),
        os.path.abspath(os.path.dirname(__file__)),
    ]

    for root in possible_roots:
        if not root:
            continue
        candidate = os.path.join(root, filename)
        if os.path.exists(candidate):
            return candidate

    return None


def apply_app_icon(root: tk.Tk) -> None:
    """Set the window icon to the packaged icon when available."""

    for icon_name in ICON_CANDIDATES:
        icon_path = resolve_resource_path(icon_name)
        if not icon_path:
            continue

        try:
            if icon_path.lower().endswith(".ico"):
                root.iconbitmap(icon_path)
            else:
                image = tk.PhotoImage(file=icon_path)
                root.iconphoto(True, image)
                root._icon_ref = image  # Prevent garbage collection
            return
        except Exception as exc:  # noqa: PERF203
            print(f"[ICON] Failed to load {icon_path}: {exc}")


def restart_program():
    """Restart the application by closing and relaunching the process."""

    python = sys.executable
    script = os.path.abspath(sys.argv[0])
    args = [python, script, *sys.argv[1:]]

    try:
        subprocess.Popen(args, cwd=os.getcwd(), env=os.environ.copy())
    except Exception as exc:
        print(f"[Restart] Failed to spawn new process: {exc}")

    try:
        root = tk._default_root
        if root is not None:
            root.quit()
            root.destroy()
    except Exception:
        pass

    os._exit(0)


def mark_pending_scan():
    """Persist a marker so the next launch triggers a rescan."""

    try:
        with open(PENDING_SCAN_FILE, "w", encoding="utf-8") as flag:
            flag.write("rescan")
    except Exception as exc:
        print(f"[PendingScan] Failed to persist marker: {exc}")


def consume_pending_scan() -> bool:
    """Return True if a persisted rescan marker was present and clear it."""

    if not os.path.exists(PENDING_SCAN_FILE):
        return False

    try:
        os.remove(PENDING_SCAN_FILE)
    except Exception as exc:
        print(f"[PendingScan] Failed to clear marker: {exc}")

    return True


__all__ = [
    "APP_FOLDER",
    "APP_NAME",
    "APP_VERSION",
    "BASE_PATH",
    "CONFIG_FILE",
    "CONFIG_FOLDER",
    "DEFAULT_TIMING_PROFILES",
    "DEFAULT_OVERLAY_FEEDBACK",
    "GLOBAL_TIMING",
    "ICON_CANDIDATES",
    "PENDING_SCAN_FILE",
    "TTS_OUTPUT_DEVICE_INDEX",
    "TTS_STATE",
    "VOICE_TUNING_DEFAULTS",
    "_TTS_ENGINE",
    "_TTS_LOCK",
    "_TTS_QUEUE",
    "_TTS_THREAD",
    "apply_app_icon",
    "consume_pending_scan",
    "mark_pending_scan",
    "resolve_resource_path",
    "restart_program",
]
