"""Voice setup and hotkey registration helpers for Dominant Control."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

import keyboard

from dominant_control.config import VOICE_TUNING_DEFAULTS
from dominant_control.dependencies import HAS_SPEECH, HAS_VOSK
from dominant_control.input_manager import input_manager
from dominant_control.voice import VoiceTestDialog, voice_listener


class VoiceControlManager:
    """Centralizes voice setup, tuning, and hotkey registration."""

    def __init__(self, app):
        self.app = app
        self.voice_phrase_map: Dict[str, Callable] = {}
        self._hotkey_handles: List[Any] = []

    # Voice tuning -----------------------------------------------------
    def tuning_config(self) -> Dict[str, Any]:
        """Return sanitized voice tuning configuration from the UI."""

        def _safe_float(var: Any, default: float) -> float:
            try:
                return float(var.get())
            except Exception:
                return default

        energy_raw = self.app.voice_energy_threshold.get().strip()
        try:
            energy_val = float(energy_raw) if energy_raw else None
        except Exception:
            energy_val = None

        return {
            "ambient_duration": max(
                0.0,
                _safe_float(
                    self.app.voice_ambient_duration,
                    VOICE_TUNING_DEFAULTS["ambient_duration"],
                ),
            ),
            "initial_timeout": max(
                0.0,
                _safe_float(
                    self.app.voice_initial_timeout,
                    VOICE_TUNING_DEFAULTS["initial_timeout"],
                ),
            ),
            "continuous_timeout": max(
                0.0,
                _safe_float(
                    self.app.voice_continuous_timeout,
                    VOICE_TUNING_DEFAULTS["continuous_timeout"],
                ),
            ),
            "phrase_time_limit": max(
                0.0,
                _safe_float(
                    self.app.voice_phrase_time_limit,
                    VOICE_TUNING_DEFAULTS["phrase_time_limit"],
                ),
            ),
            "energy_threshold": energy_val,
            "dynamic_energy": self.app.voice_dynamic_energy.get(),
        }

    def apply_voice_tuning(self, persist: bool = False):
        """Send current tuning settings to the listener and optionally save."""

        tuning = self.tuning_config()
        voice_listener.update_tuning(tuning)
        if persist:
            self.app.schedule_save()

    def on_voice_tuning_changed(self, *_):
        """Propagate UI changes to the listener and persist them."""

        self.apply_voice_tuning(persist=True)

    def set_voice_tuning_vars(self, tuning: Dict[str, Any]):
        """Populate Tk variables with stored voice tuning values."""

        self.app.voice_ambient_duration.set(
            tuning.get("ambient_duration", VOICE_TUNING_DEFAULTS["ambient_duration"])
        )
        self.app.voice_initial_timeout.set(
            tuning.get("initial_timeout", VOICE_TUNING_DEFAULTS["initial_timeout"])
        )
        self.app.voice_continuous_timeout.set(
            tuning.get(
                "continuous_timeout",
                VOICE_TUNING_DEFAULTS["continuous_timeout"],
            )
        )
        self.app.voice_phrase_time_limit.set(
            tuning.get(
                "phrase_time_limit", VOICE_TUNING_DEFAULTS["phrase_time_limit"]
            )
        )

        energy_threshold = tuning.get("energy_threshold")
        self.app.voice_energy_threshold.set(
            "" if energy_threshold in {None, ""} else str(energy_threshold)
        )
        self.app.voice_dynamic_energy.set(
            tuning.get("dynamic_energy", VOICE_TUNING_DEFAULTS["dynamic_energy"])
        )

    # Voice engine setup -----------------------------------------------
    def format_vosk_status(self) -> str:
        """Return a user-friendly status string for Vosk usage."""

        engine = self.app.voice_engine.get()
        if engine != "vosk":
            return "Using Windows speech recognizer"

        if not HAS_VOSK:
            return "Vosk not installed"

        model_path = self.app.vosk_model_path.get()
        if not model_path:
            return "Select a Vosk model folder"

        if voice_listener._vosk_error:
            return f"Model error: {voice_listener._vosk_error}"

        if voice_listener.vosk_model is not None:
            import os

            name = os.path.basename(model_path.rstrip(os.sep)) or model_path
            return f"Vosk model: {name}"

        return "Loading Vosk model..."

    def on_voice_engine_changed(self):
        """Handle engine dropdown changes."""

        selection = (
            self.app.voice_engine_combo.get()
            if self.app.voice_engine_combo
            else self.app.voice_engine.get()
        )
        if selection not in {"speech", "vosk"}:
            selection = "speech"

        if selection == "vosk" and not HAS_VOSK:
            selection = "speech"

        self.app.voice_engine.set(selection)
        self.update_voice_controls()
        self.register_current_listeners()

    def choose_vosk_model(self):
        """Prompt the user to select a Vosk model directory."""

        path = self.app.filedialog.askdirectory(title="Select Vosk model folder")
        if not path:
            return

        self.app.vosk_model_path.set(path)
        self.update_voice_controls()
        self.register_current_listeners()

    def update_voice_controls(self):
        """Refresh UI state and listener config for voice engine selection."""

        voice_listener.update_tuning(self.tuning_config())
        self.app.apply_audio_preferences()
        engine = self.app.voice_engine.get()
        if engine == "vosk" and not HAS_VOSK:
            engine = "speech"
            self.app.voice_engine.set(engine)

            if self.app.voice_engine_combo:
                self.app.voice_engine_combo.set(engine)

        if engine == "vosk":
            voice_listener.set_engine(engine, self.app.vosk_model_path.get())
        else:
            voice_listener.set_engine("speech", "")

        btn_state = "normal" if engine == "vosk" and HAS_VOSK else "disabled"
        if self.app.btn_vosk_model:
            self.app.btn_vosk_model.config(state=btn_state)
        self.app.vosk_status_var.set(self.format_vosk_status())

    def open_voice_test_dialog(self):
        """Open the dialog that validates configured voice commands."""

        if not HAS_SPEECH:
            self.app.messagebox.showinfo(
                "Voice unavailable",
                "Install the 'speech_recognition' package to enable voice control.",
            )
            return

        phrases_map = self._build_voice_phrase_map()
        self.voice_phrase_map = phrases_map

        if not phrases_map:
            self.app.messagebox.showinfo(
                "No macros found",
                "Add phrases in the tabs to test voice commands.",
            )
            return

        VoiceTestDialog(self.app.root, self.app, phrases_map)

    def on_voice_toggle(self):
        """Persist and (re)register voice triggers when toggled."""

        self.register_current_listeners()
        self.app.schedule_save()

    # Hotkey/phrase registration --------------------------------------
    def _make_single_action(self, controller, target: float):
        """Create an action that adjusts a single controller to a target."""

        return lambda: threading.Thread(
            target=controller.adjust_to_target,
            args=(target,),
            daemon=True,
        ).start()

    def _make_combo_action(self, values: Dict[str, str]):
        """Create an action that adjusts multiple controllers at once."""

        def combo_action():
            if self.app.app_state != "RUNNING":
                return

            for var_name, val_str in values.items():
                if var_name in self.app.controllers and val_str:
                    try:
                        target = float(val_str)
                    except Exception:
                        continue

                    ctrl = self.app.controllers[var_name]
                    threading.Thread(
                        target=ctrl.adjust_to_target,
                        args=(target,),
                        daemon=True,
                    ).start()

        return combo_action

    def _build_voice_phrase_map(self) -> Dict[str, Callable]:
        """Collect current voice phrases mapped to their actions."""

        voice_phrases: Dict[str, Callable] = {}

        for var_name, tab in self.app.tabs.items():
            config = tab.get_config()
            controller = self.app.controllers[var_name]

            for preset in config.get("presets", []):
                val_str = preset.get("val")
                if not val_str:
                    continue

                try:
                    target = float(val_str)
                except Exception:
                    continue

                phrase = preset.get("voice_phrase", "").strip().lower()
                if phrase:
                    voice_phrases[phrase] = self._make_single_action(
                        controller, target
                    )

        if self.app.combo_tab:
            combo_config = self.app.combo_tab.get_config()

            for preset in combo_config.get("presets", []):
                values = preset.get("vals", {})
                phrase = preset.get("voice_phrase", "").strip().lower()
                if not phrase:
                    continue

                voice_phrases[phrase] = self._make_combo_action(values)

        return voice_phrases

    def register_current_listeners(self):
        """Register keyboard/joystick listeners based on current config."""

        self.clear_keyboard_hotkeys()
        input_manager.listeners.clear()
        voice_phrases: Dict[str, Callable] = {}

        for var_name, tab in self.app.tabs.items():
            config = tab.get_config()
            controller = self.app.controllers[var_name]

            for preset in config.get("presets", []):
                bind = preset.get("bind")
                val_str = preset.get("val")
                if not val_str:
                    continue

                try:
                    target = float(val_str)
                except Exception:
                    continue

                action = self._make_single_action(controller, target)
                if bind:
                    if bind.startswith("KEY:"):
                        key_name = bind.split(":", 1)[1].lower()
                        handle = keyboard.add_hotkey(key_name, action)
                        self._hotkey_handles.append(handle)
                    else:
                        input_manager.listeners[bind] = action

                phrase = preset.get("voice_phrase", "").strip().lower()
                if phrase:
                    voice_phrases[phrase] = action

        if self.app.combo_tab:
            combo_config = self.app.combo_tab.get_config()

            for preset in combo_config.get("presets", []):
                bind = preset.get("bind")
                values = preset.get("vals", {})

                action = self._make_combo_action(values)
                if bind:
                    if bind.startswith("KEY:"):
                        key_name = bind.split(":", 1)[1].lower()
                        handle = keyboard.add_hotkey(key_name, action)
                        self._hotkey_handles.append(handle)
                    else:
                        input_manager.listeners[bind] = action

                phrase = preset.get("voice_phrase", "").strip().lower()
                if phrase:
                    voice_phrases[phrase] = action

        self.voice_phrase_map = voice_phrases

        input_manager.active = self.app.app_state == "RUNNING"
        if self.app.app_state != "RUNNING":
            voice_listener.set_enabled(False)
        elif self.app.use_voice.get():
            voice_listener.update_tuning(self.tuning_config())
            voice_listener.set_engine(
                self.app.voice_engine.get(),
                self.app.vosk_model_path.get(),
            )
            voice_listener.set_phrases(self.voice_phrase_map)
            voice_listener.set_enabled(True)
        else:
            voice_listener.set_enabled(False)

    def clear_keyboard_hotkeys(self):
        """Remove all keyboard hotkeys registered by the app."""

        if not hasattr(self, "_hotkey_handles"):
            self._hotkey_handles = []

        for handle in self._hotkey_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._hotkey_handles.clear()

        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

