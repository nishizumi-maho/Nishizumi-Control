"""Counts the session's full course cautions and exposes the original overlay.

Like the other tools in this tab nothing is drawn here: the counters run off
the shared telemetry in the background — so the session's cautions keep being
counted with the overlay closed or another tab open — and the panel owns only
the remembered checkbox that puts the overlay on screen.

Zeroing the counters lives on the overlay's own right click menu, exactly where
the original puts it; it arrives here as a bridge command because the engine,
not the renderer, is the one holding the numbers.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from ...core import TelemetryHub
from ..overlay_controls import OverlayToggle, OverlayToggleBar
from ..overlay_preferences import get_overlay_preferences
from ..original_overlays import OriginalOverlayLauncher, get_overlay_bridge
from .engine import CautionEngine

TOOL_ID = "caution"
POLL_INTERVAL_MS = 100


class CautionOverlayPanel(ttk.Frame):
    feature_id = "tools.caution"
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
        self.engine = CautionEngine(telemetry)
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
        try:
            self.original_overlay.poll()
            self.overlays.sync()
            for command in self.overlay_bridge.take_commands(TOOL_ID):
                if command.get("action") == "reset":
                    self.engine.reset()
            self._publish()
        except Exception as exc:
            # A single malformed read must not end the polling loop: that would
            # stop the count for the rest of the session, silently, with the
            # overlay still on screen showing whatever it had.
            self._report(f'Yellow flags: {type(exc).__name__}: {exc}')
        if self.winfo_exists():
            self._job = self.after(POLL_INTERVAL_MS, self._tick)

    def _publish(self) -> None:
        self.overlay_bridge.publish(TOOL_ID, self.engine.tick())

    def _report(self, message: str) -> None:
        if self.on_overlay_error is None:
            return
        try:
            self.on_overlay_error(message)
        except Exception:
            pass

    def _show_original_overlay(self) -> bool:
        self._publish()
        if self.original_overlay.show():
            return True
        self._report("Overlay: " + self.original_overlay.last_error)
        return False

    def _restore_overlay_visibility(self) -> None:
        self.overlays.restore()
