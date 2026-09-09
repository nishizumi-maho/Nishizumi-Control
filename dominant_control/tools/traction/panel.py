"""Run the original Traction coaching engine in the background and expose its overlays.

The coaching engine (lap learning, reference bins, segment detection) and its
Tk view are tightly interleaved in ``coach.TractionCircleOverlay`` — not safely
separable here. So the coach is still built against a host frame exactly as
before (needed for its background learning loop, which feeds
``_publish_original_state``), it just never gets packed into a visible tab:
the tab only shows the overlay checkboxes, which the real floating overlay
already backs with the same controls over the bridge.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from ...core import TelemetryHub
from ..overlay_controls import OverlayToggle, OverlayToggleBar
from ..overlay_preferences import get_overlay_preferences
from ..original_overlays import OriginalOverlayLauncher, get_overlay_bridge
from .coach import BG, TractionCircleOverlay


class TractionPanel(ttk.Frame):
    feature_id = "tools.traction"
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
        self.overlay_bridge.register("traction")
        self.original_overlay = OriginalOverlayLauncher("traction", self.overlay_bridge)
        self._publish_job: str | None = None
        self._stopping = False
        self.host = tk.Frame(self, bg=BG)
        self.coach = TractionCircleOverlay(self.host, telemetry.proxy)
        self._build_ui(overlay_row)
        self.coach.start()
        self.after_idle(self._restore_overlay_visibility)
        self._schedule_publish()

    def _build_ui(self, overlay_row: tk.Misc) -> None:
        self.overlays = OverlayToggleBar(
            overlay_row,
            "traction",
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
                    "lapdist",
                    "LapDist",
                    self.coach._open_lapdist_overlay,
                    self._close_lapdist_overlay,
                    is_open=self._lapdist_open,
                ),
            ],
            preferences=self.overlay_preferences,
            on_error=self.on_overlay_error,
        )
        self.overlays.pack(side="left")

    def start(self) -> None:
        self._stopping = False
        self.coach.start()
        self._schedule_publish()

    def stop(self) -> None:
        self._stopping = True
        if self._publish_job is not None:
            try:
                self.after_cancel(self._publish_job)
            except tk.TclError:
                pass
            self._publish_job = None
        self.coach.stop()
        self.overlays.shutdown()
        self.overlay_bridge.unregister("traction")

    def _show_original_overlay(self) -> bool:
        self._publish_original_state()
        if self.original_overlay.show():
            return True
        if self.on_overlay_error:
            self.on_overlay_error("Overlay: " + self.original_overlay.last_error)
        return False

    def _lapdist_open(self) -> bool:
        window = self.coach.lapdist_window
        try:
            return bool(window is not None and window.winfo_exists())
        except tk.TclError:
            return False

    def _close_lapdist_overlay(self) -> None:
        window = self.coach.lapdist_window
        try:
            if window is not None and window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass
        self.coach.lapdist_window = None

    def _schedule_publish(self) -> None:
        if self._publish_job is None and self.winfo_exists():
            self._publish_job = self.after(100, self._publish_tick)

    def _publish_tick(self) -> None:
        self._publish_job = None
        self.original_overlay.poll()
        if not self._stopping:
            self.overlays.sync()
        for command in self.overlay_bridge.take_commands("traction"):
            action = command.get("action")
            if action == "load_ibt":
                path = str(command.get("path") or "").strip()
                if path:
                    self.coach._load_ibt_reference_path(path)
                continue
            if action == "clear_ibt":
                self.coach._clear_ibt_reference()
                continue
            if action != "settings":
                continue
            try:
                self.coach.laps_for_feedback_var.set(
                    int(command.get("laps_for_feedback", 5))
                )
                self.coach.incident_free_only_var.set(
                    bool(command.get("incident_free_only", True))
                )
                self.coach._refresh_feedback_settings()
            except (TypeError, ValueError, tk.TclError):
                pass
        self._publish_original_state()
        if self.winfo_exists():
            self._publish_job = self.after(100, self._publish_tick)

    def _restore_overlay_visibility(self) -> None:
        self.overlays.restore()

    def _publish_original_state(self) -> None:
        coach = self.coach

        def simple(name: str) -> str:
            return str(getattr(coach, name).get())

        def info(name: str) -> list[str]:
            card = getattr(coach, name)
            return [card.value_var.get(), card.sub_var.get()]

        def advice(name: str) -> list[str]:
            card = getattr(coach, name)
            return [card.title_var.get(), card.meta_var.get(), card.body_var.get()]

        self.overlay_bridge.publish(
            "traction",
            {
                **{
                    name: simple(name)
                    for name in (
                        "status_var",
                        "context_var",
                        "reference_var",
                        "headline_var",
                        "subheadline_var",
                        "footer_var",
                        "settings_hint_var",
                        "lapdist_var",
                        "circle_caption_var",
                    )
                },
                **{
                    name: info(name)
                    for name in ("card_total", "card_long", "card_lat", "card_limit")
                },
                **{
                    name: advice(name)
                    for name in ("coach_card_1", "coach_card_2", "coach_card_3")
                },
                "circle": {
                    "long_g": coach.live_long_g,
                    "lat_g": coach.live_lat_g,
                    "usage_pct": coach.live_usage_pct,
                },
                "settings": {
                    "laps_for_feedback": coach.laps_for_feedback_var.get(),
                    "incident_free_only": coach.incident_free_only_var.get(),
                },
            },
        )
