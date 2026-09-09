"""Integrated Pit Calibrator using the shared telemetry client."""

from __future__ import annotations

import math
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from ...core import TelemetryHub
from ..overlay_controls import OverlayToggle, OverlayToggleBar
from ..overlay_preferences import get_overlay_preferences
from ..original_overlays import OriginalOverlayLauncher, get_overlay_bridge

MAX_REASONABLE_RATE_LPS = 8.0
MIN_REASONABLE_RATE_LPS = 0.05


class PitCalibratorPanel(ttk.Frame):
    """Times pit stops and exposes the pit overlay.

    Arming and the tire marker used to be duplicated here as buttons; the
    overlay drives the same ``toggle_arm``/``mark_tire`` actions over the
    bridge, so this view only hosts the overlay checkbox.
    """

    feature_id = "tools.pit"
    api_version = "1.0"
    runs_in_background = True

    def __init__(
        self,
        parent: tk.Misc,
        telemetry: TelemetryHub,
        overlay_row: tk.Misc,
        on_overlay_error: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.telemetry = telemetry
        self.on_overlay_error = on_overlay_error
        self.overlay_preferences = get_overlay_preferences()
        self.overlay_bridge = get_overlay_bridge(telemetry)
        self.overlay_bridge.register("pit")
        self.original_overlay = OriginalOverlayLauncher("pit", self.overlay_bridge)
        self.ir = telemetry.proxy
        self.armed = False
        self.stop_data: dict[str, Any] | None = None
        self._last_wall_time: float | None = None
        self._last_fuel_level: float | None = None
        self._job: str | None = None
        self.status_var = tk.StringVar(value="Arm the next stop to measure it.")
        self.context_var = tk.StringVar(value="Waiting for iRacing")
        self.arm_var = tk.StringVar(value="DISARMED")
        self.live = {key: tk.StringVar(value="--") for key in ("total", "service", "base", "fuel", "rate", "tire", "pending")}
        self.saved = {key: tk.StringVar(value="--") for key in ("total", "service", "base", "fuel", "rate", "tire")}
        self._build_ui(overlay_row)
        self.after_idle(self._restore_overlay_visibility)
        self.start()

    def _build_ui(self, overlay_row: tk.Misc) -> None:
        self.overlays = OverlayToggleBar(
            overlay_row,
            "pit",
            [
                OverlayToggle(
                    "original",
                    "Overlay",
                    self._show_original_overlay,
                    lambda: self.original_overlay.stop(preserve=False),
                    shutdown_action=self.original_overlay.stop,
                    is_open=lambda: self.original_overlay.desired_open,
                ),
            ],
            preferences=self.overlay_preferences,
            on_error=self.on_overlay_error,
        )
        self.overlays.pack(side="left")

    def start(self) -> None:
        if self._job is None:
            self._job = self.after(60, self._update)

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.overlays.shutdown()
        self.overlay_bridge.unregister("pit")

    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if math.isfinite(parsed) else default

    def _read(self, name: str, default: Any = None) -> Any:
        value = self.telemetry.read(name, default)
        return default if value is None else value

    @staticmethod
    def _seconds(value: float | None) -> str:
        return "--" if value is None else f"{value:.2f} s"

    @staticmethod
    def _liters(value: float | None) -> str:
        return "--" if value is None else f"{value:.2f} L"

    @staticmethod
    def _rate(value: float | None) -> str:
        return "--" if value is None else f"{value:.3f} L/s"

    def toggle_arm(self) -> None:
        self.armed = not self.armed
        self.stop_data = None
        self._last_wall_time = None
        self._last_fuel_level = None
        self._clear_live()
        if self.armed:
            self.arm_var.set("ARMED")
            self.status_var.set("The next pit entry will be measured.")
        else:
            self.arm_var.set("DISARMED")
            self.status_var.set("Arm the next stop to measure it.")

    def mark_tire_done(self) -> None:
        if not self.stop_data or not self.stop_data.get("active"):
            self.status_var.set("The tire marker works during the armed stop.")
            return
        tire_time = float(self.stop_data.get("live_service", 0.0))
        self.stop_data["manual_tire_time"] = tire_time
        self.live["tire"].set(self._seconds(tire_time))
        self.status_var.set("Tire time recorded.")

    def _service_active(self, on_pit_road: bool) -> bool:
        if not on_pit_road:
            return False
        if bool(self._read("PitstopActive", 0)):
            return True
        status = self._safe_float(self._read("PlayerCarPitSvStatus"))
        return status is not None and int(status) == 1

    def _start_stop(self, now: float, fuel_level: float | None) -> None:
        self.stop_data = {
            "active": True,
            "live_total": 0.0,
            "live_service": 0.0,
            "live_base": 0.0,
            "fuel_start": fuel_level,
            "fuel_added": 0.0,
            "fuel_rate_samples": [],
            "manual_tire_time": None,
            "pending": self._safe_float(self._read("PitSvFuel")),
        }
        self._last_wall_time = now
        self._last_fuel_level = fuel_level
        self.status_var.set("Stop in progress.")

    def _update_stop(self, now: float, fuel_level: float | None) -> None:
        if self.stop_data is None:
            return
        dt = max(0.0, now - self._last_wall_time) if self._last_wall_time is not None else 0.0
        service = self._service_active(True)
        self.stop_data["live_total"] += dt
        if service:
            self.stop_data["live_service"] += dt
        self.stop_data["live_base"] = max(0.0, self.stop_data["live_total"] - self.stop_data["live_service"])
        start_fuel = self.stop_data.get("fuel_start")
        if fuel_level is not None and start_fuel is not None:
            self.stop_data["fuel_added"] = max(0.0, fuel_level - float(start_fuel))
        if service and dt > 0 and fuel_level is not None and self._last_fuel_level is not None:
            delta = fuel_level - self._last_fuel_level
            if delta > 0.01:
                rate = delta / dt
                if MIN_REASONABLE_RATE_LPS <= rate <= MAX_REASONABLE_RATE_LPS:
                    self.stop_data["fuel_rate_samples"].append(rate)
        samples = self.stop_data["fuel_rate_samples"]
        average_rate = sum(samples) / len(samples) if samples else None
        self.live["total"].set(self._seconds(self.stop_data["live_total"]))
        self.live["service"].set(self._seconds(self.stop_data["live_service"]))
        self.live["base"].set(self._seconds(self.stop_data["live_base"]))
        self.live["fuel"].set(self._liters(self.stop_data["fuel_added"]))
        self.live["rate"].set(self._rate(average_rate))
        self.live["tire"].set(self._seconds(self.stop_data.get("manual_tire_time")))
        self.live["pending"].set(self._liters(self.stop_data.get("pending")))
        self._last_wall_time = now
        self._last_fuel_level = fuel_level

    def _finish_stop(self) -> None:
        if self.stop_data is None:
            return
        samples = self.stop_data["fuel_rate_samples"]
        average_rate = sum(samples) / len(samples) if samples else None
        for key in ("total", "service", "base"):
            self.saved[key].set(self._seconds(self.stop_data[f"live_{key}"]))
        self.saved["fuel"].set(self._liters(self.stop_data["fuel_added"]))
        self.saved["rate"].set(self._rate(average_rate))
        self.saved["tire"].set(self._seconds(self.stop_data.get("manual_tire_time")))
        self.stop_data = None
        self.armed = False
        self.arm_var.set("DONE")
        self.status_var.set("Result frozen. Arm it again whenever you like.")

    def _update_context(self) -> None:
        weekend = self._read("WeekendInfo", {}) or {}
        driver_info = self._read("DriverInfo", {}) or {}
        track = weekend.get("TrackDisplayName") or weekend.get("TrackName") or "--" if isinstance(weekend, dict) else "--"
        car = "--"
        if isinstance(driver_info, dict):
            idx = driver_info.get("DriverCarIdx")
            for row in driver_info.get("Drivers") or []:
                if isinstance(row, dict) and row.get("CarIdx") == idx:
                    car = row.get("CarScreenNameShort") or row.get("CarScreenName") or row.get("CarPath") or "--"
                    break
        self.context_var.set(f"{car}  •  {track}")

    def _update(self) -> None:
        self._job = None
        try:
            self.original_overlay.poll()
            self.overlays.sync()
            for command in self.overlay_bridge.take_commands("pit"):
                if command.get("action") == "toggle_arm":
                    self.toggle_arm()
                elif command.get("action") == "mark_tire":
                    self.mark_tire_done()
            connected = self.telemetry.status().connected
            if not connected:
                self.context_var.set("Waiting for iRacing")
            else:
                self._update_context()
                on_pit = bool(self._read("OnPitRoad", False))
                fuel = self._safe_float(self._read("FuelLevel"))
                pending = self._safe_float(self._read("PitSvFuel"))
                now = time.perf_counter()
                if self.armed and self.stop_data is None and on_pit:
                    self._start_stop(now, fuel)
                if self.stop_data is not None:
                    if on_pit:
                        self._update_stop(now, fuel)
                    else:
                        self._finish_stop()
                elif self.armed:
                    self.status_var.set("Armed — waiting for the pit entry.")
                    self.live["pending"].set(self._liters(pending))
        except Exception as exc:
            self.status_var.set(f'Error: {type(exc).__name__}: {exc}')
        self._publish_original_state()
        if self.winfo_exists():
            self._job = self.after(60, self._update)

    def _clear_live(self) -> None:
        for variable in self.live.values():
            variable.set("--")

    def _restore_overlay_visibility(self) -> None:
        self.overlays.restore()

    def _show_original_overlay(self) -> bool:
        self._publish_original_state()
        if self.original_overlay.show():
            return True
        if self.on_overlay_error:
            self.on_overlay_error("Overlay: " + self.original_overlay.last_error)
        return False

    def _publish_original_state(self) -> None:
        def value(group: dict[str, tk.StringVar], key: str) -> str:
            return group[key].get()

        self.overlay_bridge.publish(
            "pit",
            {
                "armed": self.armed,
                "connection_var": (
                    "Connected to iRacing"
                    if self.telemetry.status().connected
                    else "Waiting for iRacing…"
                ),
                "context_var": self.context_var.get(),
                "arm_state_var": self.arm_var.get(),
                "status_var": self.status_var.get(),
                "live_total_var": "Live total: " + value(self.live, "total"),
                "live_service_var": "Service, live: " + value(self.live, "service"),
                "live_base_var": "Live base: " + value(self.live, "base"),
                "live_fuel_var": "Fuel added, live: " + value(self.live, "fuel"),
                "live_rate_var": "Fuel flow, live: " + value(self.live, "rate"),
                "live_tire_var": "Manual tire change, live: " + value(self.live, "tire"),
                "pending_fuel_var": "Fuel requested in the pits: " + value(self.live, "pending"),
                "saved_total_var": "Saved total: " + value(self.saved, "total"),
                "saved_service_var": "Service, saved: " + value(self.saved, "service"),
                "saved_base_var": "Saved base: " + value(self.saved, "base"),
                "saved_fuel_var": "Fuel added, saved: " + value(self.saved, "fuel"),
                "saved_rate_var": "Fuel flow, saved: " + value(self.saved, "rate"),
                "saved_tire_var": "Tire time saved: " + value(self.saved, "tire"),
            },
        )
