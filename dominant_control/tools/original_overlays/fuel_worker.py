"""Render the untouched PySide6 Fuel Monitor UI from the main engine state."""

from __future__ import annotations

import os
import sys
from typing import Any

from .client import OverlayBridgeClient
from .ptbr import localize_qt_tree


def _decode_driver(module, raw: dict[str, Any]):
    return module.DriverMeta(**raw)


def _decode_snapshot(module, raw: dict[str, Any]):
    values = dict(raw)
    values["telemetry_source"] = module.TelemetrySource(
        values.get("telemetry_source", module.TelemetrySource.LOCAL.value)
    )
    values["drivers"] = {
        int(key): _decode_driver(module, value)
        for key, value in (values.get("drivers") or {}).items()
    }
    for key in (
        "car_lap_completed",
        "car_lap_dist_pct",
        "car_on_pit_road",
        "car_track_surface",
        "car_last_lap_time",
        "car_position",
        "car_class_position",
        "car_tire_compound",
    ):
        values[key] = tuple(values.get(key) or ())
    return module.TelemetrySnapshot(**values)


def _decode_stint(module, raw: dict[str, Any] | None):
    if not raw:
        return None
    values = dict(raw)
    values["condition"] = module.TrackCondition(values["condition"])
    return module.StintRecordView(**values)


def _decode_relative(module, raw: dict[str, Any]):
    values = dict(raw)
    values["side"] = module.RelativeSide(values["side"])
    values["condition"] = module.TrackCondition(values["condition"])
    values["team_trend_direction"] = module.TrendDirection(
        values["team_trend_direction"]
    )
    values["previous_stints"] = tuple(
        _decode_stint(module, item) for item in values.get("previous_stints") or ()
    )
    values["team_last_stint"] = _decode_stint(
        module, values.get("team_last_stint")
    )
    values["team_recent_laps"] = tuple(values.get("team_recent_laps") or ())
    return module.RelativeCarView(**values)


def _decode_endurance(module, raw: dict[str, Any]):
    values = dict(raw)
    values["cars_ahead"] = tuple(
        _decode_relative(module, item) for item in values.get("cars_ahead") or ()
    )
    values["cars_behind"] = tuple(
        _decode_relative(module, item) for item in values.get("cars_behind") or ()
    )
    return module.EnduranceViewModel(**values)


def _decode_fuel(module, raw: dict[str, Any]):
    values = dict(raw)
    values["strategy_details"] = tuple(values.get("strategy_details") or ())
    return module.FuelView(**values)


def run() -> int:
    from dominant_control.vendor.nishizumi_tools import Nishizumi_FuelMonitor as fuel

    client = OverlayBridgeClient("fuel")
    data_dir = os.environ.get("DOMINANT_CONTROL_TOOLS_DATA_DIR", "")
    if data_dir:
        fuel.QSettings.setDefaultFormat(fuel.QSettings.Format.IniFormat)
        fuel.QSettings.setPath(
            fuel.QSettings.Format.IniFormat,
            fuel.QSettings.Scope.UserScope,
            data_dir,
        )

    class BridgeOnlySource:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.manual_team_car_number = ""
            self.last_error = "Waiting for the shared state"

        def read(self):
            return fuel.TelemetrySnapshot(connected=False)

        def close(self) -> None:
            return None

    fuel.IRacingSDKSource = BridgeOnlySource
    app = fuel.QApplication(sys.argv)
    app.setApplicationName("Nishizumi FuelMonitor — Dominant Control")
    app.setOrganizationName("NishizumiTools")
    app.setQuitOnLastWindowClosed(True)
    window = fuel.FuelMonitorWindow()
    window.timer.stop()
    window.openxr_server.stop()
    window.show()
    localize_qt_tree(window, fuel)

    last_control_generation = -1
    last_show_generation = -1
    applying_remote = False
    last_sent_controls: tuple[Any, ...] | None = None

    def controls_fingerprint() -> tuple[Any, ...]:
        return (
            window._current_target_liters(),
            window._current_buffer_liters(),
            window.source.manual_team_car_number.strip(),
            bool(window.config.same_class_only),
            int(window.config.cars_ahead),
            int(window.config.cars_behind),
        )

    def send_controls() -> None:
        nonlocal last_sent_controls
        if applying_remote:
            return
        fingerprint = controls_fingerprint()
        if fingerprint == last_sent_controls:
            return
        last_sent_controls = fingerprint
        client.command(
            "set_controls",
            target=fingerprint[0],
            buffer=fingerprint[1],
            team_car=fingerprint[2],
            same_class_only=fingerprint[3],
            cars_ahead=fingerprint[4],
            cars_behind=fingerprint[5],
        )

    window.target_entry.editingFinished.connect(send_controls)
    window.buffer_entry.editingFinished.connect(send_controls)
    window.target_lock_checkbox.toggled.connect(lambda _checked: send_controls())
    window.team_car_combo.currentIndexChanged.connect(lambda _index: send_controls())
    window.same_class_checkbox.toggled.connect(lambda _checked: send_controls())
    window.cars_ahead_spin.valueChanged.connect(lambda _value: send_controls())
    window.cars_behind_spin.valueChanged.connect(lambda _value: send_controls())
    window.plus_one_button.clicked.connect(lambda: send_controls())
    window.minus_one_button.clicked.connect(lambda: send_controls())
    window.reset_button.clicked.connect(lambda: client.command("reset"))

    def poll() -> None:
        nonlocal last_control_generation, last_show_generation
        nonlocal applying_remote, last_sent_controls
        response = client.state()
        if not response:
            return
        state = response.get("state") or {}
        try:
            snapshot = _decode_snapshot(fuel, state["snapshot"])
            fuel_view = _decode_fuel(fuel, state["fuel"])
            endurance = _decode_endurance(fuel, state["endurance"])
        except (KeyError, TypeError, ValueError):
            return

        controls = state.get("controls") or {}
        generation = int(controls.get("generation", 0))
        if generation != last_control_generation:
            applying_remote = True
            try:
                target = controls.get("target")
                buffer_value = controls.get("buffer")
                if target is not None:
                    target_liters = float(target)
                    window.target_entry.setText(
                        f"{window._from_liters(target_liters):.3f}"
                    )
                    if window.target_lock_checkbox.isChecked():
                        window._locked_target = target_liters
                else:
                    window.target_lock_checkbox.blockSignals(True)
                    window.target_lock_checkbox.setChecked(False)
                    window.target_lock_checkbox.blockSignals(False)
                    window._locked_target = None
                    window._locked_buffer = None
                    window.target_entry.setEnabled(True)
                    window.buffer_entry.setEnabled(True)
                    window.target_entry.clear()
                if buffer_value is not None:
                    buffer_liters = float(buffer_value)
                    window.buffer_entry.setText(
                        f"{window._from_liters(buffer_liters):.3f}"
                    )
                    if window.target_lock_checkbox.isChecked():
                        window._locked_buffer = buffer_liters
                window.source.manual_team_car_number = str(
                    controls.get("team_car") or ""
                )
                window.config.same_class_only = bool(
                    controls.get("same_class_only", False)
                )
                window.config.cars_ahead = int(controls.get("cars_ahead", 3))
                window.config.cars_behind = int(controls.get("cars_behind", 3))
                window.same_class_checkbox.blockSignals(True)
                window.same_class_checkbox.setChecked(window.config.same_class_only)
                window.same_class_checkbox.blockSignals(False)
                window.cars_ahead_spin.blockSignals(True)
                window.cars_ahead_spin.setValue(window.config.cars_ahead)
                window.cars_ahead_spin.blockSignals(False)
                window.cars_behind_spin.blockSignals(True)
                window.cars_behind_spin.setValue(window.config.cars_behind)
                window.cars_behind_spin.blockSignals(False)
                window.ahead_section.set_limit(window.config.cars_ahead)
                window.behind_section.set_limit(window.config.cars_behind)
                last_control_generation = generation
                last_sent_controls = controls_fingerprint()
            finally:
                applying_remote = False

        if snapshot.display_units in (0, 1):
            window._set_display_units(int(snapshot.display_units))
        window._last_fuel_view = fuel_view
        window._refresh_team_car_selector(snapshot)
        window._update_model(fuel_view, endurance, snapshot)
        localize_qt_tree(window, fuel)

        show_generation = int(response.get("show_generation", 0))
        if show_generation != last_show_generation:
            last_show_generation = show_generation
            window.show()
            window.raise_()
            window.activateWindow()

    timer = fuel.QTimer(window)
    timer.timeout.connect(poll)
    timer.start(100)
    poll()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(run())
