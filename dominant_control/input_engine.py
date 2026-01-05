"""Low-level input helpers built on Windows SendInput."""

import ctypes
import os
import random
import time
from typing import Any, Dict, Optional, Tuple

from .config import DEFAULT_TIMING_PROFILES, GLOBAL_TIMING

IS_WINDOWS = os.name == "nt" and hasattr(ctypes, "windll")

if IS_WINDOWS:
    SendInput = ctypes.windll.user32.SendInput
else:
    SendInput = None
    print("Warning: Windows SendInput APIs unavailable; input injection disabled.")

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    """Keyboard input structure for SendInput."""

    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    """Hardware input structure for SendInput."""

    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class MouseInput(ctypes.Structure):
    """Mouse input structure for SendInput."""

    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class Input_I(ctypes.Union):
    """Union of input types."""

    _fields_ = [
        ("ki", KeyBdInput),
        ("mi", MouseInput),
        ("hi", HardwareInput),
    ]


class Input(ctypes.Structure):
    """Input structure for SendInput."""

    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", Input_I),
    ]


def press_key(scan_code: int):
    """Press a key using its scan code."""

    if SendInput is None:
        raise OSError("SendInput APIs are only available on Windows platforms.")

    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, 0x0008, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def release_key(scan_code: int):
    """Release a key using its scan code."""

    if SendInput is None:
        raise OSError("SendInput APIs are only available on Windows platforms.")

    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, 0x0008 | 0x0002, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def _normalize_timing_config(timing: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize timing configuration and ensure required keys exist."""

    normalized = dict(GLOBAL_TIMING)
    if not isinstance(timing, dict):
        return normalized

    normalized.update(timing)

    allowed_profiles = {"aggressive", "casual", "relaxed", "custom", "bot"}
    if normalized.get("profile") not in allowed_profiles:
        normalized["profile"] = "aggressive"

    profile_settings: Dict[str, Dict[str, Any]] = {}
    raw_profiles = normalized.get("profile_settings", {})
    legacy_custom = {
        "press_min_ms": normalized.get("press_min_ms"),
        "press_max_ms": normalized.get("press_max_ms"),
        "interval_min_ms": normalized.get("interval_min_ms"),
        "interval_max_ms": normalized.get("interval_max_ms"),
        "random_enabled": normalized.get("random_enabled"),
        "random_range_ms": normalized.get("random_range_ms"),
    }
    legacy_has_values = any(value is not None for value in legacy_custom.values())

    for profile, defaults in DEFAULT_TIMING_PROFILES.items():
        merged = dict(defaults)
        raw_profile = {}
        if isinstance(raw_profiles, dict):
            raw_profile = raw_profiles.get(profile, {}) or {}
        if not isinstance(raw_profile, dict):
            raw_profile = {}
        merged.update(raw_profile)
        if profile == "custom" and legacy_has_values:
            for key, value in legacy_custom.items():
                if value is not None:
                    merged[key] = value

        for key in [
            "press_min_ms",
            "press_max_ms",
            "interval_min_ms",
            "interval_max_ms",
            "random_range_ms",
        ]:
            try:
                merged[key] = max(1, int(merged.get(key, defaults[key])))
            except (TypeError, ValueError, KeyError):
                merged[key] = defaults.get(key, 10)

        merged["random_enabled"] = bool(
            merged.get("random_enabled", defaults.get("random_enabled", False))
        )
        profile_settings[profile] = merged

    raw_customized = normalized.get("profile_customized", {})
    profile_customized: Dict[str, bool] = {}
    for profile in DEFAULT_TIMING_PROFILES:
        default_customized = profile == "custom" and legacy_has_values
        if isinstance(raw_customized, dict):
            profile_customized[profile] = bool(
                raw_customized.get(profile, default_customized)
            )
        else:
            profile_customized[profile] = default_customized

    normalized["profile_settings"] = profile_settings
    normalized["profile_customized"] = profile_customized

    return normalized


def _effective_profile_settings(
    timing_cfg: Dict[str, Any], profile: str
) -> Dict[str, Any]:
    """Return the timing settings for the active profile."""

    profile_settings = timing_cfg.get("profile_settings", {})
    profile_customized = timing_cfg.get("profile_customized", {})
    if profile_customized.get(profile, False):
        return profile_settings.get(profile, DEFAULT_TIMING_PROFILES[profile])

    return DEFAULT_TIMING_PROFILES[profile]


def _compute_timing(is_float: bool = False) -> Tuple[float, float]:
    """Compute press and interval timing based on global profile."""

    timing_cfg = _normalize_timing_config(GLOBAL_TIMING)
    profile = timing_cfg.get("profile", "aggressive")

    settings = _effective_profile_settings(timing_cfg, profile)
    p_min = settings.get("press_min_ms", 60)
    p_max = settings.get("press_max_ms", 80)
    i_min = settings.get("interval_min_ms", 60)
    i_max = settings.get("interval_max_ms", 90)
    press_ms = random.uniform(p_min, p_max)
    interval_ms = random.uniform(i_min, i_max)

    if settings.get("random_enabled", False):
        rng = settings.get("random_range_ms", 10)
        press_ms += random.uniform(-rng, rng)
        interval_ms += random.uniform(-rng, rng)

    min_value = 1 if profile == "bot" else 10
    press_ms = max(min_value, press_ms)
    interval_ms = max(min_value, interval_ms)

    if is_float and profile != "bot":
        press_ms += 30

    return press_ms / 1000.0, interval_ms / 1000.0


def click_pulse(scan_code: Optional[int], is_float: bool = False):
    """Execute a single key press pulse with timing."""

    if not scan_code:
        return
    try:
        code = int(scan_code)
        t_press, t_interval = _compute_timing(is_float=is_float)
        press_key(code)
        time.sleep(t_press)
        release_key(code)
        time.sleep(t_interval)
    except Exception as e:
        print(f"[click_pulse] Error: {e}")


def _direct_pulse(scan_code: Optional[int], press_ms: int, interval_ms: int):
    """Execute a single key press pulse with explicit timing overrides."""

    if not scan_code:
        return

    try:
        code = int(scan_code)
        press_key(code)
        time.sleep(max(1, press_ms) / 1000.0)
        release_key(code)
        time.sleep(max(1, interval_ms) / 1000.0)
    except Exception as e:
        print(f"[_direct_pulse] Error: {e}")


__all__ = [
    "IS_WINDOWS",
    "PUL",
    "click_pulse",
    "press_key",
    "release_key",
    "_compute_timing",
    "_direct_pulse",
    "_normalize_timing_config",
]
