"""Nishizumi Tools workspace; each tool remains an independent package.

The tab used to be a 5-way sub-notebook where four tools each duplicated, as
an embedded dashboard, what their own overlay already shows and does. Overlay
windows are self-sufficient (they exchange settings/actions with their panel
over the existing bridge), so this workspace now just lists every overlay
switch in one place; the calculator has no overlay and keeps its own tab.
"""

from __future__ import annotations

import importlib
import traceback
import tkinter as tk
from tkinter import ttk

from ..core import CORE_API_VERSION, TelemetryHub

_OVERLAY_TOOLS = (
    ("Fuel", ".fuel.panel", "FuelMonitorPanel"),
    ("Pit calibrator", ".pit.panel", "PitCalibratorPanel"),
    ("Tire wear", ".tires.panel", "TireWearPanel"),
    ("Traction", ".traction.panel", "TractionPanel"),
    ("Safety Rating", ".sr.panel", "SafetyRatingPanel"),
    ("Yellow flags", ".caution.panel", "CautionOverlayPanel"),
)


class ToolsPanel(ttk.Frame):
    feature_id = "tools"
    api_version = "1.0"

    def __init__(self, parent: tk.Misc, telemetry: TelemetryHub):
        super().__init__(parent)
        self.telemetry = telemetry
        self.features: list[object] = []
        self.background_features: list[object] = []
        self.activity_var = tk.StringVar(value="Starting continuous processing…")
        # Parent for the tool engines: they still run and publish to their
        # overlays in the background, but the dashboard they used to draw is
        # gone, so nothing here ever gets packed into a visible tab.
        self._engine_host = tk.Frame(self)

        activity = tk.Frame(self, bg="#eefaf6", bd=1, relief="solid")
        activity.pack(fill="x", padx=8, pady=(8, 0))
        self.activity_label = tk.Label(
            activity,
            textvariable=self.activity_var,
            bg="#eefaf6",
            fg="#067647",
            font=("Segoe UI Semibold", 9),
        )
        self.activity_label.pack(side="left", padx=10, pady=7)
        tk.Label(
            activity,
            text=(
                "Fuel, tires, pit stop, traction, Safety Rating and yellow flags follow the telemetry even while another tab is open."
            ),
            bg="#eefaf6",
            fg="#475467",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(4, 10), pady=7)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(6, 8))

        self.overlay_error_var = tk.StringVar(value="")
        overlays_tab = ttk.Frame(self.notebook)
        self.notebook.add(overlays_tab, text="Overlays")
        self._build_overlays_tab(overlays_tab, telemetry)

        calculator_tab = ttk.Frame(self.notebook)
        self.notebook.add(calculator_tab, text="Calculator")
        self._mount_calculator(calculator_tab, telemetry)

        active = len(self.background_features)
        expected = len(_OVERLAY_TOOLS)
        self.activity_var.set(
            "●  Continuous processing on"
            if active == expected
            else f'●  Partial processing: {active}/{expected} modules'
        )
        if active != expected:
            self.activity_label.config(fg="#b54708")

    def _build_overlays_tab(self, parent: tk.Misc, telemetry: TelemetryHub) -> None:
        container = tk.Frame(parent, bg="#f7f8fa")
        container.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(
            container,
            text=(
                "Turn each tool's overlay on or off while you drive. Lock position pins the Safety Rating overlay and lets clicks pass through to the game."
            ),
            bg="#f7f8fa",
            fg="#667085",
            font=("Segoe UI", 9),
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        for title, module_name, class_name in _OVERLAY_TOOLS:
            row = tk.Frame(container, bg="#ffffff", bd=1, relief="solid")
            row.pack(fill="x", pady=(0, 8))
            tk.Label(
                row,
                text=title,
                bg="#ffffff",
                fg="#172033",
                font=("Segoe UI Semibold", 10),
                width=20,
                anchor="w",
            ).pack(side="left", padx=12, pady=10)
            try:
                module = importlib.import_module(module_name, __package__)
                factory = getattr(module, class_name)
                feature = factory(
                    self._engine_host, telemetry, row, self.overlay_error_var.set
                )
                self._check_api_version(feature)
                self.features.append(feature)
                if bool(getattr(feature, "runs_in_background", False)):
                    self.background_features.append(feature)
            except Exception as exc:
                self._show_row_error(row, title, exc)

        self.error_label = tk.Label(
            container,
            textvariable=self.overlay_error_var,
            bg="#f7f8fa",
            fg="#b42318",
            font=("Segoe UI", 9),
            wraplength=760,
            justify="left",
        )
        self.error_label.pack(anchor="w", pady=(4, 0))

    def _mount_calculator(self, parent: tk.Misc, telemetry: TelemetryHub) -> None:
        try:
            module = importlib.import_module(".fair_share.panel", __package__)
            factory = getattr(module, "FairSharePanel")
            feature = factory(parent, telemetry)
            self._check_api_version(feature)
            feature.pack(fill="both", expand=True)
            self.features.append(feature)
        except Exception as exc:
            self._show_feature_error(parent, "Calculator", exc)

    @staticmethod
    def _check_api_version(feature: object) -> None:
        version = str(getattr(feature, "api_version", ""))
        if version.split(".", 1)[0] != CORE_API_VERSION.split(".", 1)[0]:
            raise RuntimeError(
                f'Incompatible API: module {version}, core {CORE_API_VERSION}'
            )

    @staticmethod
    def _show_row_error(row: tk.Misc, title: str, exc: Exception) -> None:
        tk.Label(
            row,
            text=f"{title}: {type(exc).__name__}: {exc}",
            bg="#ffffff",
            fg="#b42318",
            font=("Segoe UI", 9),
            wraplength=560,
            justify="left",
        ).pack(side="left", padx=(0, 12), pady=10, fill="x", expand=True)

    @staticmethod
    def _show_feature_error(parent: tk.Misc, title: str, exc: Exception) -> None:
        box = tk.Frame(parent, padx=22, pady=22)
        box.pack(fill="both", expand=True)
        tk.Label(box, text=f'{title} could not be loaded', font=("Segoe UI Semibold", 14), fg="#b42318").pack(anchor="w")
        tk.Label(box, text=f"{type(exc).__name__}: {exc}", justify="left", wraplength=800).pack(anchor="w", pady=(8, 0))
        details = tk.Text(box, height=10, wrap="word", font=("Consolas", 8))
        details.insert("1.0", traceback.format_exc())
        details.config(state="disabled")
        details.pack(fill="both", expand=True, pady=(12, 0))

    def start(self) -> None:
        for feature in self.features:
            start = getattr(feature, "start", None)
            if callable(start):
                start()

    def stop(self) -> None:
        for feature in reversed(self.features):
            stop = getattr(feature, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    continue
