"""Tk view and coordinator for the Tire Wear learning engine."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from ...core import TelemetryHub
from ..overlay_controls import OverlayToggle, OverlayToggleBar
from ..overlay_preferences import get_overlay_preferences
from ..original_overlays import OriginalOverlayLauncher, get_overlay_bridge
from .engine import ModelWorker, TIRE_KEYS, TelemetryReader


class TireWearPanel(ttk.Frame):
    """Runs the tire wear learning engine and exposes its overlay.

    The wear percentages and the reset button used to be duplicated here as a
    dashboard; the overlay already shows the same numbers and drives the same
    ``reset`` action over the bridge, so this view only hosts the overlay
    checkbox.
    """

    feature_id = "tools.tires"
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
        self.overlay_bridge.register("tires")
        self.original_overlay = OriginalOverlayLauncher("tires", self.overlay_bridge)
        self.stop_event = threading.Event()
        self.telemetry_queue: queue.Queue = queue.Queue(maxsize=1200)
        self.state_lock = threading.Lock()
        self.state = self._initial_state()
        self.telemetry_thread = TelemetryReader(self.telemetry_queue, self.stop_event, telemetry.proxy)
        self.model_thread = ModelWorker(self.telemetry_queue, self.state, self.state_lock, self.stop_event)
        self._job: str | None = None
        self._build_ui(overlay_row)
        self.after_idle(self._restore_overlay_visibility)
        self.start()

    @staticmethod
    def _initial_state() -> dict[str, object]:
        return {
            "tread": {t: 100.0 for t in TIRE_KEYS},
            "wear_per_lap": {t: 0.0 for t in TIRE_KEYS},
            "estimate_ready": False,
            "connected": False,
            "key": "",
            "model_confidence": 0.0,
            "model_report": "",
            "sample_count": 0,
            "track_temp": 0.0,
            "air_temp": 0.0,
            "humidity": 0.0,
            "track_name": "",
            "track_config": "",
            "car_path": "",
            "env_track_temp": 0.0,
            "env_air_temp": 0.0,
            "env_humidity": 0.0,
            "env_track_temp_start": 0.0,
            "env_track_temp_end": 0.0,
            "env_track_temp_delta": 0.0,
            "env_track_temp_std": 0.0,
            "env_air_temp_start": 0.0,
            "env_air_temp_end": 0.0,
            "env_air_temp_delta": 0.0,
            "env_humidity_start": 0.0,
            "env_humidity_end": 0.0,
            "env_humidity_delta": 0.0,
            "env_humidity_std": 0.0,
            "reset_requested": False,
            "updated_at": time.time(),
        }

    def _build_ui(self, overlay_row: tk.Misc) -> None:
        self.overlays = OverlayToggleBar(
            overlay_row,
            "tires",
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
        if not self.telemetry_thread.is_alive():
            self.telemetry_thread.start()
        if not self.model_thread.is_alive():
            self.model_thread.start()
        if self._job is None:
            self._job = self.after(120, self._refresh)

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.stop_event.set()
        for thread in (self.telemetry_thread, self.model_thread):
            if thread.is_alive():
                thread.join(timeout=1.5)
        self.overlays.shutdown()
        self.overlay_bridge.unregister("tires")

    def _refresh(self) -> None:
        self._job = None
        self.original_overlay.poll()
        self.overlays.sync()
        for command in self.overlay_bridge.take_commands("tires"):
            if command.get("action") == "reset":
                with self.state_lock:
                    self.state["reset_requested"] = True
        with self.state_lock:
            state = dict(self.state)
        self.overlay_bridge.publish("tires", state)
        if self.winfo_exists():
            self._job = self.after(150, self._refresh)

    def _show_original_overlay(self) -> bool:
        with self.state_lock:
            state = dict(self.state)
        self.overlay_bridge.publish("tires", state)
        if self.original_overlay.show():
            return True
        if self.on_overlay_error:
            self.on_overlay_error("Overlay: " + self.original_overlay.last_error)
        return False

    def _restore_overlay_visibility(self) -> None:
        self.overlays.restore()
