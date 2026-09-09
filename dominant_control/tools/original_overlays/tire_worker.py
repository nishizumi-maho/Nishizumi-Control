"""Render the original PyQt5 Tire Wear HUD from the main learned model."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .client import OverlayBridgeClient
from .ptbr import localize_qt_tree, translate_text


def run() -> int:
    from dominant_control.vendor.nishizumi_tools import Nishizumi_TireWear as tires

    client = OverlayBridgeClient("tires")
    data_dir = Path(os.environ.get("DOMINANT_CONTROL_TOOLS_DATA_DIR") or ".")
    data_dir.mkdir(parents=True, exist_ok=True)
    tires.APPDATA_DIR = data_dir
    tires.MODEL_PATH = str(data_dir / "nishizumi_tirewear_model.json")
    tires.SETTINGS_PATH = str(data_dir / "nishizumi_tirewear_original_settings.json")

    state_lock = threading.Lock()
    state: dict[str, object] = {
        "tread": {key: 100.0 for key in tires.TIRE_KEYS},
        "wear_per_lap": {key: 0.0 for key in tires.TIRE_KEYS},
        "connected": False,
        "estimate_ready": False,
        "model_report": "",
        "reset_requested": False,
    }

    class BridgeOverlayUI(tires.OverlayUI):
        """Keep the original UI while making the parent the only model owner."""

        def open_info(self) -> None:
            with self.state_lock:
                key = self.state.get("key", "unknown")
                connected = self.state.get("connected", False)
                temp = float(self.state.get("track_temp", 0.0))
                air = float(self.state.get("air_temp", 0.0))
                humidity = float(self.state.get("humidity", 0.0))
                env_t_avg = float(self.state.get("env_track_temp", temp))
                env_t_start = float(self.state.get("env_track_temp_start", temp))
                env_t_end = float(self.state.get("env_track_temp_end", temp))
                env_t_delta = float(self.state.get("env_track_temp_delta", 0.0))
                env_t_std = float(self.state.get("env_track_temp_std", 0.0))
                env_h_avg = float(self.state.get("env_humidity", humidity))
                env_h_start = float(self.state.get("env_humidity_start", humidity))
                env_h_end = float(self.state.get("env_humidity_end", humidity))
                env_h_delta = float(self.state.get("env_humidity_delta", 0.0))
                model_conf = float(self.state.get("model_confidence", 0.0))
                samples = int(self.state.get("sample_count", 0))
                report = str(self.state.get("model_report") or "")

            message = (
                f'Connection: {('connected' if connected else 'disconnected')}\nData set key: {key}\nTrack temperature now: {temp:.1f} °C\nAir temperature now: {air:.1f} °C\nHumidity now: {humidity:.1f} %\nStint ambient average: {env_t_avg:.1f} °C / {env_h_avg:.1f} %\nTrack swing: {env_t_start:.1f} → {env_t_end:.1f} °C  (Δ {env_t_delta:+.1f}, σ {env_t_std:.2f})\nHumidity swing: {env_h_start:.1f} → {env_h_end:.1f} %  (Δ {env_h_delta:+.1f})\nSamples: {samples}\nModel confidence: {model_conf:.1%}\n\n{translate_text(report)}'
            )
            self.info_dialog.set_info(message)
            self.info_dialog.show()
            self.info_dialog.raise_()

        def reset_all_data(self) -> None:
            confirm_box = self._build_light_message_box(
                icon=tires.QtWidgets.QMessageBox.Warning,
                title="Clear the data",
                text=(
                    "This will erase the learned tire model and the current session's memory. Continue?"
                ),
                buttons=(
                    tires.QtWidgets.QMessageBox.Yes
                    | tires.QtWidgets.QMessageBox.No
                ),
                default_button=tires.QtWidgets.QMessageBox.No,
            )
            localize_qt_tree(confirm_box, tires.QtWidgets)
            if confirm_box.exec_() != tires.QtWidgets.QMessageBox.Yes:
                return
            if not client.command("reset"):
                return
            with self.state_lock:
                self.state.update(
                    tread={key: 100.0 for key in tires.TIRE_KEYS},
                    wear_per_lap={key: 0.0 for key in tires.TIRE_KEYS},
                    key="",
                    model_confidence=0.0,
                    model_report="",
                    sample_count=0,
                    estimate_ready=False,
                    reset_requested=False,
                )
            self.toasts.clear()
            self.last_conf_bucket = -1
            self.last_sample_count = 0
            done_box = self._build_light_message_box(
                icon=tires.QtWidgets.QMessageBox.Information,
                title="Data cleared",
                text="Every learned value has been cleared.",
                buttons=tires.QtWidgets.QMessageBox.Ok,
                default_button=tires.QtWidgets.QMessageBox.Ok,
            )
            localize_qt_tree(done_box, tires.QtWidgets)
            done_box.exec_()

        def refresh(self) -> None:
            super().refresh()
            localize_qt_tree(self, tires.QtWidgets)

    app = tires.QtWidgets.QApplication(sys.argv)
    overlay = BridgeOverlayUI(state, state_lock)
    overlay.show()
    localize_qt_tree(overlay, tires.QtWidgets)
    last_show_generation = -1

    def poll() -> None:
        nonlocal last_show_generation
        response = client.state()
        if not response:
            localize_qt_tree(overlay, tires.QtWidgets)
            return
        remote = response.get("state") or {}
        if isinstance(remote, dict):
            with state_lock:
                state.update(remote)
                state["reset_requested"] = False
        show_generation = int(response.get("show_generation", 0))
        if show_generation != last_show_generation:
            last_show_generation = show_generation
            overlay.show()
            overlay.raise_()
            overlay.activateWindow()
        localize_qt_tree(overlay, tires.QtWidgets)

    timer = tires.QtCore.QTimer(overlay)
    timer.timeout.connect(poll)
    timer.start(100)
    poll()
    return int(app.exec_())


if __name__ == "__main__":
    raise SystemExit(run())
