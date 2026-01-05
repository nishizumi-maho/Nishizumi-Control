"""Overlay feedback analyzer used by the HUD update loop."""

from __future__ import annotations

import numbers
import time
from typing import Any, Callable, Dict, List

import irsdk

from dominant_control.config import DEFAULT_OVERLAY_FEEDBACK


class OverlayFeedbackManager:
    """Encapsulates overlay feedback state and telemetry access."""

    def __init__(self, ir: irsdk.IRSDK, notifier: Callable[[str, str], None]):
        self.ir = ir
        self._notify = notifier
        self._state = {
            "last_time": time.time(),
            "abs_active": 0.0,
            "tc_active": 0.0,
            "spin_active": 0.0,
            "lock_active": 0.0,
            "last_alert": "",
            "last_alert_time": 0.0,
        }

    def set_ir(self, ir: irsdk.IRSDK) -> None:
        """Update the IRSDK handle used for telemetry reads."""

        self.ir = ir

    def _read_ir_value(self, key: str):
        """Safely read a telemetry key from the iRacing SDK."""

        try:
            if not getattr(self.ir, "is_initialized", False):
                self.ir.startup()
            return self.ir[key]
        except Exception:
            return None

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _bool_from_keys(self, keys: List[str]) -> bool:
        """Return True if any telemetry key resolves to a truthy value."""

        for key in keys:
            value = self._read_ir_value(key)

            if isinstance(value, (list, tuple)):
                if any(bool(v) for v in value):
                    return True
            elif isinstance(value, numbers.Real):
                if float(value) != 0.0:
                    return True
            elif isinstance(value, bool) and value:
                return True

        return False

    def _slip_values(self) -> List[float]:
        """Aggregate slip ratios from available telemetry fields."""

        slips: List[float] = []
        for key in ["WheelSlip", "WheelSlipPct", "WheelSlipRatio", "TireSlip"]:
            value = self._read_ir_value(key)
            if isinstance(value, (list, tuple)):
                slips.extend([self._safe_float(v, 0.0) for v in value])

        return slips

    def _push_overlay_alert(
        self, message: str, color: str, cfg: Dict[str, float], now: float
    ) -> None:
        """Send rate-limited feedback to the overlay status area."""

        cooldown = max(0.5, float(cfg.get("cooldown_s", 6.0)))

        if (
            now - self._state.get("last_alert_time", 0.0) < cooldown
            and self._state.get("last_alert") == message
        ):
            return

        self._notify(message, color)
        self._state["last_alert"] = message
        self._state["last_alert_time"] = now

    def update_feedback(
        self,
        current_car: str,
        car_overlay_feedback: Dict[str, Dict[str, float]],
        enabled: bool,
    ) -> None:
        """Analyze telemetry and surface ABS/TC/wheelspin hints on the HUD."""

        if not enabled:
            self._state["last_time"] = time.time()
            return

        car = current_car or "Generic Car"
        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(car_overlay_feedback.get(car, {}))

        now = time.time()
        dt = max(0.0, now - self._state.get("last_time", now))
        self._state["last_time"] = now

        throttle = self._safe_float(self._read_ir_value("Throttle"), 0.0)
        brake = self._safe_float(self._read_ir_value("Brake"), 0.0)

        abs_active = self._bool_from_keys(
            [
                "BrakeABSactive",
                "BrakeABSActive",
                "BrakeABSActiveLF",
                "BrakeABSActiveRF",
                "BrakeABSActiveLR",
                "BrakeABSActiveRR",
            ]
        )
        tc_active = self._bool_from_keys(
            [
                "TractionControlActive",
                "TractionControlEngaged",
                "TCActive",
                "TractionControlOn",
            ]
        )

        slips = self._slip_values()
        max_slip = max(slips) if slips else 0.0
        min_slip = min(slips) if slips else 0.0

        if abs_active and brake > 0.05:
            self._state["abs_active"] += dt
        else:
            self._state["abs_active"] = 0.0

        if tc_active and throttle > 0.2:
            self._state["tc_active"] += dt
        else:
            self._state["tc_active"] = 0.0

        if throttle > 0.2 and max_slip >= cfg["wheelspin_slip"]:
            self._state["spin_active"] += dt
        else:
            self._state["spin_active"] = 0.0

        lock_threshold = -abs(cfg["lockup_slip"])
        if brake > 0.05 and slips and min_slip <= lock_threshold:
            self._state["lock_active"] += dt
        else:
            self._state["lock_active"] = 0.0

        if self._state["abs_active"] >= cfg["abs_hold_s"]:
            self._push_overlay_alert(
                "ABS active too long: ease off the brake or lower ABS.",
                "orange",
                cfg,
                now,
            )
            self._state["abs_active"] = 0.0

        if self._state["tc_active"] >= cfg["tc_hold_s"]:
            self._push_overlay_alert(
                "TC constantly triggering: consider lowering TC or changing the map.",
                "orange",
                cfg,
                now,
            )
            self._state["tc_active"] = 0.0

        if self._state["spin_active"] >= cfg["wheelspin_hold_s"]:
            self._push_overlay_alert(
                "Wheelspin detected: raise TC or modulate the throttle.",
                "orange",
                cfg,
                now,
            )
            self._state["spin_active"] = 0.0

        if self._state["lock_active"] >= cfg["lockup_hold_s"]:
            self._push_overlay_alert(
                "Lock-up detected: increase ABS or ease pedal pressure.",
                "orange",
                cfg,
                now,
            )
            self._state["lock_active"] = 0.0

