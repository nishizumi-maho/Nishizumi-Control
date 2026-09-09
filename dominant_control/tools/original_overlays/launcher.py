"""Start original overlay renderers while their engines stay in the main app."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ...paths import application_dir, tools_data_dir
from ..overlay_preferences import get_overlay_preferences
from .bridge import OverlayStateBridge


class OriginalOverlayLauncher:
    def __init__(self, tool_id: str, bridge: OverlayStateBridge) -> None:
        self.tool_id = str(tool_id)
        self.bridge = bridge
        self.process: subprocess.Popen | None = None
        self.last_error = ""
        self.preferences = get_overlay_preferences()
        self._desired_open = self.preferences.get(self.tool_id, "original")
        self._started_once = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def desired_open(self) -> bool:
        return self._desired_open

    def restore(self) -> bool:
        if not self._desired_open:
            return False
        return self.show(persist=False)

    def show(self, *, persist: bool = True) -> bool:
        self.bridge.request_show(self.tool_id)
        if self.running:
            self._desired_open = True
            if persist:
                self.preferences.set(self.tool_id, "original", True)
            return True
        try:
            command = self._command()
            environment = os.environ.copy()
            environment.update(
                DOMINANT_CONTROL_OVERLAY_BRIDGE_PORT=str(self.bridge.port),
                DOMINANT_CONTROL_OVERLAY_BRIDGE_TOKEN=self.bridge.token,
                DOMINANT_CONTROL_OVERLAY_TOOL=self.tool_id,
                DOMINANT_CONTROL_TOOLS_DATA_DIR=str(tools_data_dir()),
            )
            self.process = subprocess.Popen(
                command,
                cwd=str(application_dir()),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._started_once = True
            self._desired_open = True
            self.preferences.set(self.tool_id, "original", True)
            self.last_error = ""
            return True
        except Exception as exc:
            self.process = None
            self._desired_open = False
            self.preferences.set(self.tool_id, "original", False)
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def poll(self) -> None:
        """Notice when the user closes the external overlay window."""
        if not self._started_once or not self._desired_open or self.running:
            return
        self._desired_open = False
        self.preferences.set(self.tool_id, "original", False)

    def stop(self, *, preserve: bool = True) -> None:
        process = self.process
        self.process = None
        if (
            preserve
            and self._started_once
            and process is not None
            and process.poll() is not None
        ):
            self._desired_open = False
            self.preferences.set(self.tool_id, "original", False)
        if not preserve:
            self._desired_open = False
            self.preferences.set(self.tool_id, "original", False)
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except Exception:
            pass

    def _command(self) -> list[str]:
        if self.tool_id == "tires":
            return self._tire_command()
        worker_args = ["--original-overlay-worker", self.tool_id]
        if getattr(sys, "frozen", False):
            return [sys.executable, *worker_args]
        entry = Path(__file__).resolve().parents[3] / "DominantControl.py"
        return [sys.executable, str(entry), *worker_args]

    @staticmethod
    def _tire_command() -> list[str]:
        if getattr(sys, "frozen", False):
            executable = (
                application_dir()
                / "external_overlays"
                / "NishizumiTireOriginal"
                / "NishizumiTireOriginal.exe"
            )
            if not executable.is_file():
                raise FileNotFoundError(f'Original overlay not found: {executable}')
            return [str(executable)]
        entry = Path(__file__).resolve().parents[3] / "TireOverlayOriginal.py"
        return [sys.executable, str(entry)]


__all__ = ["OriginalOverlayLauncher"]
