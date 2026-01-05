"""Overlay manager handling HUD visibility and update scheduling."""

from __future__ import annotations

from typing import Any, Dict, Optional

import tkinter as tk

import irsdk

from dominant_control.ui.overlay_feedback import OverlayFeedbackManager
from dominant_control.ui.overlay_window import OverlayWindow


class OverlayManager:
    """Encapsulates HUD overlay lifecycle and periodic updates."""

    def __init__(self, root: tk.Misc, ir: irsdk.IRSDK):
        self.root = root
        self.overlay = OverlayWindow(root)
        self.overlay.withdraw()

        self._ir = ir
        self._visible = True
        self._running = False
        self._loop_id: Optional[str] = None

        self._controllers: Dict[str, Any] = {}
        self._car_overlay_config: Dict[str, Dict[str, Any]] = {}
        self._car_overlay_feedback: Dict[str, Dict[str, float]] = {}
        self._current_car: str = ""
        self._show_overlay_feedback = True

        self.overlay_feedback_manager = OverlayFeedbackManager(
            ir, self.notify_overlay_status
        )

    def set_ir(self, ir: irsdk.IRSDK) -> None:
        """Update the iRacing SDK handle used for feedback."""

        self._ir = ir
        self.overlay_feedback_manager.set_ir(ir)

    def update_context(
        self,
        controllers: Dict[str, Any],
        car_overlay_config: Dict[str, Dict[str, Any]],
        car_overlay_feedback: Dict[str, Dict[str, float]],
        current_car: str,
        show_overlay_feedback: bool,
    ) -> None:
        """Provide the latest controller and configuration references."""

        self._controllers = controllers or {}
        self._car_overlay_config = car_overlay_config or {}
        self._car_overlay_feedback = car_overlay_feedback or {}
        self._current_car = current_car or ""
        self._show_overlay_feedback = bool(show_overlay_feedback)

    def start(self) -> None:
        """Begin the overlay update loop."""

        if self._running:
            return

        self._running = True
        if self._visible:
            self.overlay.deiconify()
        self._schedule_next()

    def stop(self) -> None:
        """Stop the overlay update loop."""

        self._running = False
        if self._loop_id:
            try:
                self.root.after_cancel(self._loop_id)
            except Exception:
                pass
            self._loop_id = None

    def toggle_overlay(self) -> None:
        """Toggle HUD overlay visibility."""

        if self.overlay.winfo_viewable():
            self.overlay.withdraw()
            self._visible = False
        else:
            self.overlay.deiconify()
            self._visible = True

    def notify_overlay_status(self, text: str, color: str) -> None:
        """Update overlay status text temporarily."""

        self.overlay.update_status_text(text, color)
        self.root.after(
            2000, lambda: self.overlay.update_status_text("HUD Ready", "white")
        )

    def _schedule_next(self) -> None:
        self._loop_id = self.root.after(100, self._update_overlay_loop)

    def _update_overlay_loop(self) -> None:
        if not self._running:
            return

        if self._visible:
            data: Dict[str, Any] = {}
            car = self._current_car or "Generic Car"
            config = self._car_overlay_config.get(car, {})

            for var_name, controller in self._controllers.items():
                var_config = config.get(var_name, {})
                if not var_config.get("show", False):
                    continue
                value = controller.read_telemetry()
                data[var_name] = value

            self.overlay.update_monitor_values(data)

        self.overlay_feedback_manager.update_feedback(
            self._current_car,
            self._car_overlay_feedback,
            self._show_overlay_feedback,
        )

        self._schedule_next()
