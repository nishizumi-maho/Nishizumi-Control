"""Runs the Safety Rating estimator in the background and exposes its overlay.

Like the other tools in this tab, nothing is drawn here: the estimate is
published to the overlay process over the existing bridge, and this panel only
owns the two remembered checkboxes — whether the overlay is on screen, and
whether it is locked.

Why locking is a checkbox and not only a button on the overlay: a locked SR
overlay has no chrome at all (that is the point — it is click-through, so the
mouse belongs to the sim), and the standalone application recovers from that
with a global hotkey and a tray icon. Dominant Control deliberately does not
register more global hotkeys on the driver's behalf, so without this checkbox a
locked overlay would be a state the user cannot leave.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from ...core import TelemetryHub
from ..overlay_controls import OverlayToggle, OverlayToggleBar
from ..overlay_preferences import get_overlay_preferences
from ..original_overlays import OriginalOverlayLauncher, get_overlay_bridge
from .engine import SafetyRatingEngine

TOOL_ID = "sr"
POLL_INTERVAL_MS = 100


class SafetyRatingPanel(ttk.Frame):
    feature_id = "tools.sr"
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
        self.overlay_bridge.register(TOOL_ID)
        self.original_overlay = OriginalOverlayLauncher(TOOL_ID, self.overlay_bridge)
        self.engine = SafetyRatingEngine(telemetry)
        self._locked = self.overlay_preferences.get(TOOL_ID, "locked", False)
        # Bumped on every lock change so the renderer can tell a fresh command
        # from the value it is already showing (same guard the Fuel overlay uses
        # for its own controls).
        self._lock_generation = 0
        self._job: str | None = None
        self._build_ui(overlay_row)
        self.after_idle(self._restore_overlay_visibility)
        self.start()

    def _build_ui(self, overlay_row: tk.Misc) -> None:
        self.overlays = OverlayToggleBar(
            overlay_row,
            TOOL_ID,
            [
                OverlayToggle(
                    "original",
                    "Overlay",
                    self._show_original_overlay,
                    lambda: self.original_overlay.stop(preserve=False),
                    shutdown_action=self.original_overlay.stop,
                    is_open=lambda: self.original_overlay.desired_open,
                ),
                OverlayToggle(
                    "locked",
                    "Lock position",
                    lambda: self._set_locked(True),
                    lambda: self._set_locked(False),
                    # Closing the app must not unlock an overlay the driver
                    # left locked; the group re-persists the wanted state.
                    shutdown_action=lambda: None,
                    is_open=lambda: self._locked,
                ),
            ],
            preferences=self.overlay_preferences,
            on_error=self.on_overlay_error,
        )
        self.overlays.pack(side="left")

    def start(self) -> None:
        if self._job is None and self.winfo_exists():
            self._job = self.after(POLL_INTERVAL_MS, self._tick)

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.overlays.shutdown()
        self.overlay_bridge.unregister(TOOL_ID)

    def _tick(self) -> None:
        self._job = None
        self.original_overlay.poll()
        self.overlays.sync()
        for command in self.overlay_bridge.take_commands(TOOL_ID):
            if command.get("action") == "set_lock":
                # The overlay's own lock button and its right-click menu report
                # back here, so the checkbox follows the window.
                self._set_locked(bool(command.get("locked", False)))
                self.overlays.sync()
        self._publish()
        if self.winfo_exists():
            self._job = self.after(POLL_INTERVAL_MS, self._tick)

    def _publish(self) -> None:
        self.overlay_bridge.publish(
            TOOL_ID,
            {
                **self.engine.tick(),
                "locked": self._locked,
                "lock_generation": self._lock_generation,
            },
        )

    def _set_locked(self, locked: bool) -> bool:
        locked = bool(locked)
        if locked != self._locked:
            self._locked = locked
            self._lock_generation += 1
        self.overlay_preferences.set(TOOL_ID, "locked", locked)
        return locked

    def _show_original_overlay(self) -> bool:
        self._publish()
        if self.original_overlay.show():
            return True
        if self.on_overlay_error:
            self.on_overlay_error("Overlay: " + self.original_overlay.last_error)
        return False

    def _restore_overlay_visibility(self) -> None:
        self.overlays.restore()
