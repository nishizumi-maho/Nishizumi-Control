"""Configuration persistence helpers for Dominant Control."""

from __future__ import annotations

import json
from typing import Any, Dict

from dominant_control import config as config_module
from dominant_control.config import CONFIG_FILE, DEFAULT_OVERLAY_FEEDBACK
from dominant_control.input_engine import _normalize_timing_config
from dominant_control.input_manager import input_manager


class ConfigService:
    """Serialize and hydrate application state from the config file."""

    def __init__(self, app: Any):
        self.app = app

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load configuration from disk and apply it to the app."""

        data = self._read_config()
        if not data:
            return

        self._apply_timing(data)
        self._apply_overlay(data)
        self._apply_voice_and_devices(data)
        self._apply_presets(data)

    def save(self) -> None:
        """Collect the current application state and persist it."""

        payload = self._build_payload()
        self._write_config(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _read_config(self) -> Dict[str, Any]:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _write_config(self, payload: Dict[str, Any]) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=4)
        except Exception as exc:
            print(f"[Config] Failed to save configuration: {exc}")

    def _build_payload(self) -> Dict[str, Any]:
        car = self.app.current_car or "Generic Car"
        if self.app.overlay_tab:
            self.app.overlay_tab.collect_for_car(car)

        self._ensure_overlay_defaults(car)
        self._sync_overlay_with_presets(car)

        return {
            "global_timing": _normalize_timing_config(config_module.GLOBAL_TIMING),
            "hud_style": self.app.overlay.style_cfg,
            "show_overlay_feedback": self.app.show_overlay_feedback.get(),
            "use_keyboard_only": self.app.use_keyboard_only.get(),
            "use_tts": self.app.use_tts.get(),
            "use_voice": self.app.use_voice.get(),
            "voice_engine": self.app.voice_engine.get(),
            "vosk_model_path": self.app.vosk_model_path.get(),
            "voice_tuning": self.app._voice_tuning_config(),
            "microphone_device": self.app.microphone_device.get(),
            "audio_output_device": self.app.audio_output_device.get(),
            "auto_detect": self.app.auto_detect.get(),
            "auto_restart_on_rescan": self.app.auto_restart_on_rescan.get(),
            "auto_restart_on_race": self.app.auto_restart_on_race.get(),
            "pending_scan_on_start": self.app.pending_scan_on_start,
            "allowed_devices": input_manager.allowed_devices,
            "saved_presets": self.app.saved_presets,
            "car_overlay_config": self.app.car_overlay_config,
            "car_overlay_feedback": self.app.car_overlay_feedback,
            "active_vars": self.app.active_vars,
            "current_car": self.app.current_car,
            "current_track": self.app.current_track,
        }

    def _apply_timing(self, data: Dict[str, Any]) -> None:
        config_module.GLOBAL_TIMING = _normalize_timing_config(
            data.get("global_timing", config_module.GLOBAL_TIMING)
        )

    def _apply_overlay(self, data: Dict[str, Any]) -> None:
        style = data.get("hud_style")
        if style:
            self.app.overlay.style_cfg.update(style)
            self.app.overlay.apply_style(self.app.overlay.style_cfg)

        self.app.show_overlay_feedback.set(data.get("show_overlay_feedback", True))
        self.app.car_overlay_config = data.get("car_overlay_config", {})
        self.app.car_overlay_feedback = data.get(
            "car_overlay_feedback", self.app.car_overlay_feedback
        )

    def _apply_voice_and_devices(self, data: Dict[str, Any]) -> None:
        self.app.use_keyboard_only.set(data.get("use_keyboard_only", False))
        self.app.use_tts.set(data.get("use_tts", False))
        self.app.use_voice.set(data.get("use_voice", False))
        self.app.voice_engine.set(data.get("voice_engine", "speech"))
        self.app.vosk_model_path.set(data.get("vosk_model_path", ""))
        self.app.microphone_device.set(data.get("microphone_device", -1))
        self.app.audio_output_device.set(data.get("audio_output_device", -1))
        self.app._set_voice_tuning_vars(
            data.get("voice_tuning", config_module.VOICE_TUNING_DEFAULTS)
        )
        self.app.auto_detect.set(data.get("auto_detect", True))
        self.app.auto_restart_on_rescan.set(data.get("auto_restart_on_rescan", True))
        self.app.auto_restart_on_race.set(data.get("auto_restart_on_race", True))
        self.app.pending_scan_on_start = data.get("pending_scan_on_start", False)

        input_manager.allowed_devices = data.get("allowed_devices", [])

    def _apply_presets(self, data: Dict[str, Any]) -> None:
        self.app.saved_presets = data.get("saved_presets", {})
        self.app.active_vars = data.get("active_vars", [])
        self.app.current_car = data.get("current_car", "")
        self.app.current_track = data.get("current_track", "")

    def _ensure_overlay_defaults(self, car: str) -> None:
        if car not in self.app.car_overlay_feedback:
            self.app.car_overlay_feedback[car] = DEFAULT_OVERLAY_FEEDBACK.copy()

        if car not in self.app.car_overlay_config:
            self.app.car_overlay_config[car] = {}

    def _sync_overlay_with_presets(self, car: str) -> None:
        if car not in self.app.saved_presets:
            self.app.saved_presets[car] = {}

        self.app.saved_presets[car]["_overlay"] = self.app.car_overlay_config.get(
            car, {}
        )
        self.app.saved_presets[car]["_overlay_feedback"] = self.app.car_overlay_feedback.get(
            car, DEFAULT_OVERLAY_FEEDBACK.copy()
        )
