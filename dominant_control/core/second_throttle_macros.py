"""Second Throttle macros for Dominant Control."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Optional

from .second_throttle import SecondThrottleEngine

SECOND_THROTTLE_PIT_MACRO_ID = "second-throttle-pit-disabled"
MAX_SECOND_THROTTLE_MACROS = 24


def default_second_throttle_macro(index: int = 1, *, binding: Any = None, percentage: Any = 100) -> dict[str, Any]:
    safe_index = max(1, int(index))
    return {
        "id": f"second-throttle-{safe_index}",
        "name": f"Macro {safe_index}",
        "percentage": SecondThrottleEngine.normalize_percentage(percentage),
        "bind": binding,
        "pedal_takeover_enabled": False,
        "pedal_takeover_threshold": 25,
    }


def default_second_throttle_pit_macro() -> dict[str, Any]:
    return {"id": SECOND_THROTTLE_PIT_MACRO_ID, "name": "Unavailable", "enabled": False, "percentage": 100}


def _safe_macro_id(value: Any, fallback: str) -> str:
    text = str(value or '').strip()
    if not text:
        return fallback
    text = re.sub('[^A-Za-z0-9_.:-]+', '-', text).strip('-')
    return (text or fallback)[:80]

def normalize_pedal_takeover_threshold(value: Any) -> int:
    """Return the experimental physical-pedal cutoff in the 1..100 range."""
    try:
        number = float(str(value).strip().replace(',', '.'))
    except Exception:
        return 25
    if not math.isfinite(number):
        return 25
    return max(1, min(100, int(round(number))))

@dataclass(frozen=True, slots=True)
class SecondThrottlePedalEvent:
    """One transition from the experimental physical-pedal takeover guard."""
    action: str
    physical_percentage: int
    threshold_percentage: int

class SecondThrottlePedalTakeover:
    """Cut a macro only after the driver releases and retakes the pedal.

    A macro is commonly enabled while the physical pedal is already at 100%.
    Checking the threshold immediately would therefore cancel it on the same
    telemetry tick.  This guard first waits for a real lift (5% or less), then
    arms a rising-edge cutoff above the configured threshold.
    """
    RELEASE_PERCENTAGE = 5

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.macro_id: Optional[str] = None
        self.enabled = False
        self.armed = False
        self.threshold_percentage = 25
        self.physical_percentage: Optional[int] = None

    def activate(self, macro_id: str, *, enabled: Any, threshold_percentage: Any=25) -> None:
        self.reset()
        if not bool(enabled):
            return
        self.macro_id = str(macro_id or '') or None
        self.enabled = self.macro_id is not None
        self.threshold_percentage = normalize_pedal_takeover_threshold(threshold_percentage)

    @staticmethod
    def _physical_percentage(value: Any) -> Optional[int]:
        try:
            ratio = float(value)
        except Exception:
            return None
        if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
            return None
        return max(0, min(100, int(round(ratio * 100.0))))

    def update(self, raw_throttle: Any, *, active_macro_id: Optional[str]) -> Optional[SecondThrottlePedalEvent]:
        """Consume ``ThrottleRaw`` and return a one-shot ``armed``/``cut`` event."""
        if not self.enabled or not self.macro_id or str(active_macro_id or '') != self.macro_id:
            return None
        percentage = self._physical_percentage(raw_throttle)
        if percentage is None:
            return None
        self.physical_percentage = percentage
        if not self.armed:
            if percentage <= self.RELEASE_PERCENTAGE:
                self.armed = True
                return SecondThrottlePedalEvent('armed', percentage, self.threshold_percentage)
            return None
        if percentage > self.threshold_percentage:
            event = SecondThrottlePedalEvent('cut', percentage, self.threshold_percentage)
            self.reset()
            return event
        return None


def sanitize_second_throttle_macro(raw: Any, index: int) -> dict[str, Any]:
    base = default_second_throttle_macro(index)
    if not isinstance(raw, Mapping):
        return base
    binding = raw.get("bind")
    if binding in (None, "", "CANCEL"):
        binding = None
    elif not isinstance(binding, (str, int, float)):
        binding = str(binding)
    name = str(raw.get("name") or base["name"]).strip()[:48]
    base.update({
        "id": _safe_macro_id(raw.get("id"), base["id"]),
        "name": name or base["name"],
        "percentage": SecondThrottleEngine.normalize_percentage(raw.get("percentage", 100)),
        "bind": binding,
        "pedal_takeover_enabled": bool(raw.get("pedal_takeover_enabled", False)),
        "pedal_takeover_threshold": normalize_pedal_takeover_threshold(raw.get("pedal_takeover_threshold", 25)),
    })
    return base


def sanitize_second_throttle_macros(raw_macros: Any, *, fallback_binding: Any = None, fallback_percentage: Any = 100, ensure_one: bool = True) -> list[dict[str, Any]]:
    candidates = raw_macros if isinstance(raw_macros, list) else []
    if not candidates and ensure_one:
        candidates = [default_second_throttle_macro(1, binding=fallback_binding, percentage=fallback_percentage)]
    result = []
    used_ids = set()
    for index, raw in enumerate(candidates[:MAX_SECOND_THROTTLE_MACROS], start=1):
        macro = sanitize_second_throttle_macro(raw, index)
        base_id = macro["id"]
        candidate_id = base_id
        suffix = 2
        while candidate_id in used_ids:
            candidate_id = f"{base_id}-{suffix}"
            suffix += 1
        macro["id"] = candidate_id
        used_ids.add(candidate_id)
        result.append(macro)
    return result


def sanitize_second_throttle_pit_macro(raw: Any) -> dict[str, Any]:
    return default_second_throttle_pit_macro()


def macro_lapdist_range(*_args, **_kwargs):
    return None


def parse_lapdist_pct(*_args, **_kwargs):
    return None


def lapdist_in_range(*_args, **_kwargs):
    return False


@dataclass(frozen=True, slots=True)
class SecondThrottleAutomationEvent:
    action: str
    macro_id: str
    source: str


class SecondThrottleAutomation:
    def reset(self) -> None:
        return None

    def evaluate(self, *_args, **_kwargs):
        return None


__all__ = [name for name in globals() if not name.startswith("_")]
