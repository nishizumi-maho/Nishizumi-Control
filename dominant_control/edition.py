"""Product identity and the automation this build ships.

Everything that reacted to ``LapDistPct`` to change the car during a lap, and
every other autonomous actuation that would hand the driver a real advantage,
is absent from this source: it was removed before packaging, not disabled at
runtime.  Two automations are here on purpose, and both follow the weather
rather than the position on track:

* the windshield wipers, which follow the precipitation reported by the SDK;
* the automatic Dry/Wet profile, applied when the local driver enters the car
  and when the session's declared condition changes.

The flags below say, to the modules that ask, what this build can do.
"""

from __future__ import annotations

import copy
from typing import Any


# There is a single edition here, so the switch the modules read is a
# constant.  The automation the product does not ship was removed from
# the source, not merely turned off.
PUBLIC_EDITION = True

APP_DISPLAY_NAME = "Dominant Control"
APP_EXECUTABLE_NAME = "DominantControl.exe"
APP_DATA_FOLDER = "DominantControl"
APP_STARTUP_ENTRY = "DominantControl.bat"
APP_USER_MODEL_ID = "NishizumiControl.DominantControl.v13"

# Not part of this product: LapDistPct decides when the car changes.
LAPDIST_MACROS_AVAILABLE = False
# Not part of this product: it learns the pit entry by LapDistPct and
# actuates the limiter by itself.
PIT_LIMITER_AUTOMATION_AVAILABLE = False
# Not part of this product: it changes the mixture on its own when the
# session flags go yellow or green.
FUEL_MIXTURE_AUTOMATION_AVAILABLE = False
# Not part of this product: it keeps the hybrid button pressed until the
# state of charge reaches the target.
HYBRID_HOLD_AUTOMATION_AVAILABLE = False
# Not part of this product: it fires Push To Pass again after each cycle.
P2P_CHAIN_AUTOMATION_AVAILABLE = False
# Not part of this product: it drives the Second Throttle from OnPitRoad.
SECOND_THROTTLE_PIT_MACRO_AVAILABLE = False

# What this product does automate.
WIPER_AUTOMATION_AVAILABLE = True
SURFACE_PROFILE_AUTOMATION_AVAILABLE = True
TURBO_PIT_AVAILABLE = True

# Version of this product.  The public build looks for a release newer than
# this one; the private build is not published, so it never asks.
APP_VERSION = "13"
UPDATE_CHECK_AVAILABLE = True
UPDATE_REPO_OWNER = "nishizumi-maho"
UPDATE_REPO_NAME = "Nishizumi-Control"


def _disable_telemetry_automation(config: Any) -> Any:
    """Return a copy of a saved preset with autonomous actuation removed."""

    if isinstance(config, list):
        return [_disable_telemetry_automation(item) for item in config]
    if not isinstance(config, dict):
        return copy.deepcopy(config)

    sanitized = {
        key: _disable_telemetry_automation(value)
        for key, value in config.items()
    }
    for key in ("lap_dist", "lap_dist_min", "lap_dist_max", "lap_number"):
        sanitized.pop(key, None)
    for key in (
        "pit_limiter",
        "fuel_mixture_auto",
        "hybrid_hold",
        "lap_trigger",
        "p2p_chain_count",
        "p2p_chain_group",
    ):
        sanitized.pop(key, None)

    return sanitized


def sanitize_public_saved_presets(saved_presets: Any) -> dict[str, Any]:
    """Normalize imported preset trees for the public edition.

    ``wiper_auto`` is deliberately preserved: the windshield automation is one
    of the two exceptions that ship in the public build.
    """

    if not PUBLIC_EDITION or not isinstance(saved_presets, dict):
        return saved_presets if isinstance(saved_presets, dict) else {}
    result = _disable_telemetry_automation(saved_presets)
    return result if isinstance(result, dict) else {}


def sanitize_public_second_throttle_macros(macros: Any) -> list[dict[str, Any]]:
    """Normalize imported Second Throttle macros for the public edition."""

    if not isinstance(macros, list):
        return []
    result: list[dict[str, Any]] = []
    for item in macros:
        if not isinstance(item, dict):
            continue
        macro = copy.deepcopy(item)
        for key in (
            "lapdist_enabled",
            "lap_start_min",
            "lap_start_max",
            "lap_end_min",
            "lap_end_max",
        ):
            macro.pop(key, None)
        result.append(macro)
    return result


def enforce_public_state(app: Any) -> None:
    """Normalize the public edition's configuration after loading it."""

    if not PUBLIC_EDITION:
        return

    for name in (
        "use_lapdist_macros",
        "lapdist_humanize",
        "lapdist_training_mode",
        "lapdist_show_close_log",
        "lapdist_learning_enabled",
        "lapdist_learning_persist",
    ):
        variable = getattr(app, name, None)
        if variable is not None and hasattr(variable, "set"):
            variable.set(False)

    app.lapdist_toggle_bind = None
    app.lapdist_capture_bind = None
    app._lapdist_learning_data = {}
    app._pit_limiter_learning_data = {}
    app.saved_presets = sanitize_public_saved_presets(
        getattr(app, "saved_presets", {})
    )
    app.second_throttle_macros = sanitize_public_second_throttle_macros(
        getattr(app, "second_throttle_macros", [])
    )
    pit_macro = copy.deepcopy(
        getattr(app, "second_throttle_pit_macro", {})
        if isinstance(getattr(app, "second_throttle_pit_macro", {}), dict)
        else {}
    )
    pit_macro["enabled"] = False
    app.second_throttle_pit_macro = pit_macro


__all__ = [
    "APP_DATA_FOLDER",
    "APP_DISPLAY_NAME",
    "APP_EXECUTABLE_NAME",
    "APP_STARTUP_ENTRY",
    "APP_USER_MODEL_ID",
    "FUEL_MIXTURE_AUTOMATION_AVAILABLE",
    "HYBRID_HOLD_AUTOMATION_AVAILABLE",
    "LAPDIST_MACROS_AVAILABLE",
    "P2P_CHAIN_AUTOMATION_AVAILABLE",
    "PIT_LIMITER_AUTOMATION_AVAILABLE",
    "PUBLIC_EDITION",
    "SECOND_THROTTLE_PIT_MACRO_AVAILABLE",
    "SURFACE_PROFILE_AUTOMATION_AVAILABLE",
    "APP_VERSION",
    "TURBO_PIT_AVAILABLE",
    "UPDATE_CHECK_AVAILABLE",
    "UPDATE_REPO_NAME",
    "UPDATE_REPO_OWNER",
    "WIPER_AUTOMATION_AVAILABLE",
    "enforce_public_state",
    "sanitize_public_saved_presets",
    "sanitize_public_second_throttle_macros",
]
