"""Tk view for the newer Fuel Monitor engine."""

from __future__ import annotations

import json
import math
import tkinter as tk
from collections.abc import Callable
from dataclasses import asdict
from tkinter import ttk

from ...core import TelemetryHub
from ...paths import tools_data_dir
from ..overlay_controls import OverlayToggle, OverlayToggleBar
from ..overlay_preferences import get_overlay_preferences
from ..original_overlays import OriginalOverlayLauncher, get_overlay_bridge
from .engine import (
    AppConfig,
    EnduranceTracker,
    EnduranceViewModel,
    FuelStrategyEngine,
    FuelView,
    IRacingSDKSource,
    OPENXR_URL,
    OpenXRDashboardServer,
    TelemetrySnapshot,
)


def _number(value: str) -> float | None:
    try:
        parsed = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--:--"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class FuelMonitorPanel(ttk.Frame):
    """Runs the fuel strategy engine and exposes its overlays.

    The live numbers used to be duplicated here as a dashboard; the overlay
    already shows and edits them (see ``_consume_original_commands``), so this
    view only hosts the overlay checkboxes and the preferences dialog, which
    has no overlay equivalent.
    """

    feature_id = "tools.fuel"
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
        self.settings_path = tools_data_dir() / "fuel_integrated.json"
        settings = self._load_settings()
        self.config = self._config_from_settings(settings)
        self.source = IRacingSDKSource(ir_client=telemetry.proxy)
        self.tracker = EnduranceTracker(self.config)
        self.engine = FuelStrategyEngine()
        self.openxr = OpenXRDashboardServer()
        self.overlay_preferences = get_overlay_preferences()
        self.overlay_bridge = get_overlay_bridge(telemetry)
        self.overlay_bridge.register("fuel")
        self.original_overlay = OriginalOverlayLauncher("fuel", self.overlay_bridge)
        self.target_var = tk.StringVar(value=str(settings.get("target", "2.50")))
        self.buffer_var = tk.StringVar(value=str(settings.get("buffer", "0.00")))
        self.team_car_var = tk.StringVar(value=str(settings.get("team_car", "")))
        self.values = {
            name: tk.StringVar(value=value)
            for name, value in {
                "status": "Waiting for iRacing",
                "remaining": "--:--:--",
                "average": "--.-- L/lap",
                "delta": "--",
                "fuel": "-- L",
                "last": "-- L",
                "plan": "-- laps",
                "estimate": "-- laps",
                "stint": "--",
                "strategy": "Waiting for data to work out the strategy.",
                "context": "Shared telemetry: waiting for a connection",
            }.items()
        }
        self._job: str | None = None
        self._last_view = FuelView()
        self._last_endurance = EnduranceViewModel(False, "WAITING FOR iRACING")
        self._last_snapshot = TelemetrySnapshot(connected=False)
        self._control_generation = 1
        self._build_ui(overlay_row)
        self._publish_original_state()
        self.after_idle(self._restore_overlay_visibility)
        self.start()

    def _build_ui(self, overlay_row: tk.Misc) -> None:
        self.overlays = OverlayToggleBar(
            overlay_row,
            "fuel",
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
                    "openxr",
                    "OpenXR",
                    self.openxr.start,
                    self.openxr.stop,
                    is_open=lambda: self.openxr.running,
                ),
            ],
            preferences=self.overlay_preferences,
            on_error=self.on_overlay_error,
        )
        self.overlays.pack(side="left")
        ttk.Button(
            overlay_row, text="Preferences", command=self._open_preferences
        ).pack(side="right")

    def start(self) -> None:
        if self._job is None:
            self._job = self.after(80, self._update)

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.source.close()
        self.overlays.shutdown()
        self.overlay_bridge.unregister("fuel")

    def _update(self) -> None:
        self._job = None
        try:
            self._consume_original_commands()
            self.original_overlay.poll()
            self.overlays.sync()
            self.source.manual_team_car_number = self.team_car_var.get().strip()
            snapshot = self.source.read()
            target = _number(self.target_var.get())
            buffer_value = _number(self.buffer_var.get()) or 0.0
            fuel = self.engine.update(snapshot, target, buffer_value)
            endurance = self.tracker.update(snapshot)
            self._last_view = fuel
            self._last_endurance = endurance
            self._last_snapshot = snapshot
            self._render(snapshot, fuel, endurance)
            self._publish_original_state()
        except Exception as exc:
            self.values["status"].set("Error")
            self.values["context"].set(f'Fuel monitor: {type(exc).__name__}: {exc}')
        if self.winfo_exists():
            self._job = self.after(max(34, int(round(1000 / self.config.update_hz))), self._update)

    def _render(self, snapshot: TelemetrySnapshot, fuel: FuelView, endurance: EnduranceViewModel) -> None:
        self.values["status"].set(fuel.status)
        self.values["context"].set(
            "Shared telemetry: connected" if snapshot.connected else "Shared telemetry: waiting for iRacing"
        )
        self.values["remaining"].set(_duration(fuel.remaining_seconds))
        self.values["average"].set("--.-- L/lap" if fuel.average_per_lap is None else f'{fuel.average_per_lap:.3f} L/lap')
        self.values["delta"].set("--" if fuel.delta_to_target is None else f'{fuel.delta_to_target:+.3f} L/lap')
        self.values["fuel"].set("-- L" if fuel.fuel_level is None else f"{fuel.fuel_level:.2f} L")
        self.values["last"].set("-- L" if fuel.last_lap_used is None else f"{fuel.last_lap_used:.3f} L")
        self.values["plan"].set("-- laps" if fuel.planned_laps is None else f'{fuel.planned_laps} laps')
        self.values["estimate"].set("-- laps" if fuel.estimated_laps is None else f'{fuel.estimated_laps} laps')
        self.values["stint"].set(f'{endurance.player_current_stint_laps} laps')
        self.values["strategy"].set(fuel.strategy_text)
        if self.openxr.running:
            self.openxr.update(self._openxr_state(fuel, endurance))

    def _openxr_state(self, fuel: FuelView, endurance: EnduranceViewModel) -> dict[str, object]:
        state = self.openxr.empty_state()
        state.update(
            connected=fuel.connected,
            time=_duration(fuel.remaining_seconds),
            laps=("--" if fuel.remaining_laps is None else f'{fuel.remaining_laps:.1f} LAPS'),
            current=f'CURRENT {endurance.player_current_stint_laps} V',
            fuel=self.values["fuel"].get(),
            average=self.values["average"].get(),
            delta=self.values["delta"].get(),
            average_state="good" if fuel.within_target else "danger",
            plan=self.values["plan"].get(),
            estimate=self.values["estimate"].get(),
        )
        if endurance.cars_ahead:
            state["ahead"] = "Ahead: " + endurance.cars_ahead[0].driver_short_name
        if endurance.cars_behind:
            state["behind"] = "Behind: " + endurance.cars_behind[0].driver_short_name
        return state

    def _controls_changed(self) -> None:
        self._control_generation += 1
        self._save_settings()

    def _show_original_overlay(self) -> bool:
        self._publish_original_state()
        if self.original_overlay.show():
            return True
        if self.on_overlay_error:
            self.on_overlay_error("Overlay: " + self.original_overlay.last_error)
        return False

    def _publish_original_state(self) -> None:
        self.overlay_bridge.publish(
            "fuel",
            {
                "snapshot": self._last_snapshot,
                "fuel": self._last_view,
                "endurance": self._last_endurance,
                "controls": {
                    "generation": self._control_generation,
                    "target": _number(self.target_var.get()),
                    "buffer": _number(self.buffer_var.get()) or 0.0,
                    "team_car": self.team_car_var.get().strip(),
                    "same_class_only": self.config.same_class_only,
                    "cars_ahead": self.config.cars_ahead,
                    "cars_behind": self.config.cars_behind,
                },
            },
        )

    def _consume_original_commands(self) -> None:
        for command in self.overlay_bridge.take_commands("fuel"):
            action = command.get("action")
            if action == "reset":
                self.engine.reset()
                continue
            if action != "set_controls":
                continue
            old_team = self.team_car_var.get().strip()
            target = command.get("target")
            buffer_value = command.get("buffer")
            try:
                if "target" in command:
                    if target is None:
                        self.target_var.set("")
                    elif float(target) > 0:
                        self.target_var.set(f"{float(target):.3f}")
                if buffer_value is not None and float(buffer_value) >= 0:
                    self.buffer_var.set(f"{float(buffer_value):.3f}")
            except (TypeError, ValueError):
                pass
            self.team_car_var.set(str(command.get("team_car") or ""))
            for key in ("same_class_only", "cars_ahead", "cars_behind"):
                if key in command:
                    try:
                        setattr(self.config, key, command[key])
                    except Exception:
                        pass
            self.config.validate()
            if self.team_car_var.get().strip() != old_team:
                self.engine.reset()
                self.tracker.reset()
            self._controls_changed()

    def _restore_overlay_visibility(self) -> None:
        self.overlays.restore()

    @staticmethod
    def _config_from_settings(settings: dict[str, object]) -> AppConfig:
        config = AppConfig()
        raw = settings.get("config")
        if isinstance(raw, dict):
            for key in asdict(config):
                if key in raw:
                    try:
                        setattr(config, key, raw[key])
                    except Exception:
                        continue
        try:
            config.validate()
        except (TypeError, ValueError):
            config = AppConfig()
        return config

    def _open_preferences(self) -> None:
        window = tk.Toplevel(self)
        window.title("Fuel Monitor — Preferences")
        window.transient(self.winfo_toplevel())
        window.resizable(False, False)
        window.grab_set()

        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        numeric_specs = (
            ("cars_ahead", "Cars ahead", 0, 5),
            ("cars_behind", "Cars behind", 0, 5),
            ("previous_stints_shown", "Stints anteriores", 1, 5),
            ("team_trend_window", "Trend window", 2, 5),
            ("prediction_history_window", "Forecast window", 1, 10),
            ("prediction_min_stint_laps", "Minimum laps", 1, 30),
            ("update_hz", "Updates per second", 2, 30),
            ("overlay_width", "Overlay width", 480, 1100),
        )
        variables: dict[str, tk.Variable] = {}
        for index, (key, label, minimum, maximum) in enumerate(numeric_specs):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", padx=(0, 16), pady=4)
            variable = tk.IntVar(value=int(getattr(self.config, key)))
            variables[key] = variable
            ttk.Spinbox(body, from_=minimum, to=maximum, textvariable=variable, width=9).grid(row=index, column=1, sticky="e", pady=4)

        checks = (
            ("same_class_only", "Show the same class only"),
            ("exclude_caution_stints", "Ignore stints under yellow"),
            ("match_tire_compound", "Compare the same compound"),
        )
        check_box = ttk.LabelFrame(body, text="Filters", padding=8)
        check_box.grid(row=0, column=2, rowspan=6, sticky="nsew", padx=(18, 0), pady=2)
        for index, (key, label) in enumerate(checks):
            variable = tk.BooleanVar(value=bool(getattr(self.config, key)))
            variables[key] = variable
            ttk.Checkbutton(check_box, text=label, variable=variable).grid(row=index, column=0, sticky="w", pady=4)

        actions = ttk.Frame(body)
        actions.grid(row=len(numeric_specs), column=0, columnspan=3, sticky="e", pady=(14, 0))

        def save() -> None:
            for key, variable in variables.items():
                setattr(self.config, key, variable.get())
            self.config.validate()
            self._controls_changed()
            window.destroy()

        ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(actions, text="Save", command=save).pack(side="right", padx=(0, 8))

    def _load_settings(self) -> dict[str, object]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self) -> None:
        payload = {
            "target": self.target_var.get(),
            "buffer": self.buffer_var.get(),
            "team_car": self.team_car_var.get(),
            "openxr_url": OPENXR_URL,
            "config": asdict(self.config),
        }
        try:
            self.settings_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
