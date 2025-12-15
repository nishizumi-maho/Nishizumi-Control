"""Voice recognition helpers and dialogs for Dominant Control."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk

from .config import VOICE_TUNING_DEFAULTS
from .dependencies import HAS_SPEECH, HAS_VOSK, sr, vosk
from .watchdog import Watchdog


class VoiceListener:
    """Lightweight voice trigger engine backed by speech recognition."""

    def __init__(self):
        self.available = HAS_SPEECH
        self.recognizer = sr.Recognizer() if HAS_SPEECH else None
        self.callbacks: Dict[str, Callable] = {}
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self._noise_adjusted = False
        self.last_engine: Optional[str] = None
        self.engine = "speech"
        self.vosk_model_path: str = ""
        self.vosk_model: Optional[Any] = None
        self._vosk_error: Optional[str] = None
        self.device_index: Optional[int] = None
        self.ambient_duration = VOICE_TUNING_DEFAULTS["ambient_duration"]
        self.initial_timeout = VOICE_TUNING_DEFAULTS["initial_timeout"]
        self.continuous_timeout = VOICE_TUNING_DEFAULTS["continuous_timeout"]
        self.phrase_time_limit = VOICE_TUNING_DEFAULTS["phrase_time_limit"]
        self.energy_threshold: Optional[float] = VOICE_TUNING_DEFAULTS[
            "energy_threshold"
        ]
        self.dynamic_energy = VOICE_TUNING_DEFAULTS["dynamic_energy"]
        if self.recognizer:
            self._apply_recognizer_settings(self.recognizer)
        self._watchdog = Watchdog(
            "VoiceListener", interval_s=2.0, timeout_s=7.0, on_trip=self._recover_listener
        )

    def set_phrases(self, phrases: Dict[str, Callable]):
        """Replace the phrase-to-callback map."""
        with self.lock:
            self.callbacks = {k.strip().lower(): v for k, v in phrases.items() if k}

    def set_enabled(self, enabled: bool):
        """Start or stop the listener based on user preference."""
        if not self.available:
            self.stop()
            return

        if enabled and self.callbacks:
            self.start()
        else:
            self.stop()

    def set_device_index(self, device_index: Optional[int]):
        """Update the microphone device index and restart listener if needed."""

        self.device_index = device_index
        if self.running:
            self.stop()
            self.start()

    def start(self):
        if not self.available:
            return

        if self.running and self.thread and self.thread.is_alive():
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._listen_loop_with_watchdog, daemon=True, name="VoiceListener"
        )
        self.thread.start()
        self._watchdog.start()

    def stop(self):
        self.running = False
        self._watchdog.stop()

    def set_engine(self, engine: str, model_path: str = ""):
        """Configure which recognition engine to use."""
        engine = engine if engine in {"speech", "vosk"} else "speech"
        self.engine = engine
        self.vosk_model_path = model_path

        if engine == "vosk":
            self._init_vosk_model(model_path)
        else:
            self.vosk_model = None
            self._vosk_error = None

    def _apply_recognizer_settings(self, recognizer):
        """Apply tuning values to a speech_recognition.Recognizer."""

        try:
            recognizer.dynamic_energy_threshold = self.dynamic_energy
            if self.energy_threshold is not None:
                recognizer.energy_threshold = self.energy_threshold
        except Exception:
            pass

    def update_tuning(self, tuning: Dict[str, Any]):
        """Update microphone/recognition tuning parameters."""

        def _safe_float(value: Any, default: float) -> float:
            try:
                return float(value)
            except Exception:
                return default

        self.ambient_duration = max(
            0.0, _safe_float(tuning.get("ambient_duration"), self.ambient_duration)
        )
        self.initial_timeout = _safe_float(
            tuning.get("initial_timeout"), self.initial_timeout
        )
        self.continuous_timeout = _safe_float(
            tuning.get("continuous_timeout"), self.continuous_timeout
        )
        self.phrase_time_limit = _safe_float(
            tuning.get("phrase_time_limit"), self.phrase_time_limit
        )

        threshold_val = tuning.get("energy_threshold")
        try:
            self.energy_threshold = (
                float(threshold_val) if threshold_val not in {None, ""} else None
            )
        except Exception:
            self.energy_threshold = None

        self.dynamic_energy = bool(tuning.get("dynamic_energy", self.dynamic_energy))

        self._noise_adjusted = False

        if self.recognizer:
            self._apply_recognizer_settings(self.recognizer)
        elif HAS_SPEECH and sr is not None:
            self.recognizer = sr.Recognizer()
            self._apply_recognizer_settings(self.recognizer)

    def _recover_listener(self):
        """Restart the listener thread if it stops unexpectedly."""

        if not self.running:
            return

        if self.thread and self.thread.is_alive():
            return

        print("[Voice][Watchdog] Listener thread unresponsive, restarting...")
        self.start()

    def _listen_loop_with_watchdog(self):
        """Wrap the listener loop with heartbeat updates."""

        self._watchdog.beat()
        try:
            self._listen_loop()
        finally:
            self._watchdog.beat()

    def _init_vosk_model(self, model_path: str):
        """Load the Vosk model from disk if available."""
        if not HAS_VOSK or not model_path:
            self.vosk_model = None
            return

        if self.vosk_model_path == model_path and self.vosk_model is not None:
            return

        try:
            self.vosk_model = vosk.Model(model_path)
            self._vosk_error = None
        except Exception as exc:
            self.vosk_model = None
            self._vosk_error = str(exc)
            print(f"[Voice][Vosk] Failed to load model: {exc}")

    def _recognize_text(self, audio, recognizer=None) -> Optional[str]:
        """Try multiple engines to convert audio to text."""
        rec = recognizer or self.recognizer
        if not rec:
            return None

        if self.engine == "vosk" and HAS_VOSK and self.vosk_model:
            try:
                raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                vosk_rec = vosk.KaldiRecognizer(self.vosk_model, 16000)
                if vosk_rec.AcceptWaveform(raw):
                    result_json = vosk_rec.Result()
                else:
                    result_json = vosk_rec.FinalResult()

                parsed = json.loads(result_json or "{}")
                text = (parsed.get("text") or "").strip()
                if text:
                    self.last_engine = "vosk"
                    return text
            except Exception as exc:
                print(f"[Voice][Vosk] Recognition error: {exc}")

        engines: List[Tuple[str, Callable]] = []
        if hasattr(rec, "recognize_sapi"):
            engines.append(("sapi", rec.recognize_sapi))
        if hasattr(rec, "recognize_sphinx"):
            engines.append(("sphinx", rec.recognize_sphinx))
        if hasattr(rec, "recognize_google"):
            engines.append(("google", rec.recognize_google))

        for name, engine in engines:
            try:
                result = engine(audio)
                self.last_engine = name
                return result
            except Exception:
                continue

        return None

    def _listen_loop(self):
        if not self.recognizer:
            return

        try:
            with sr.Microphone(device_index=self.device_index) as source:
                self._apply_recognizer_settings(self.recognizer)
                if not self._noise_adjusted:
                    try:
                        self.recognizer.adjust_for_ambient_noise(
                            source,
                            duration=self.ambient_duration
                        )
                        self._noise_adjusted = True
                    except Exception:
                        pass

                listen_timeout = (
                    self.initial_timeout if self.initial_timeout > 0 else None
                )
                phrase_limit = (
                    self.phrase_time_limit if self.phrase_time_limit > 0 else None
                )

                while self.running:
                    self._watchdog.beat()
                    try:
                        audio = self.recognizer.listen(
                            source,
                            timeout=listen_timeout,
                            phrase_time_limit=phrase_limit
                        )
                        listen_timeout = (
                            self.continuous_timeout
                            if self.continuous_timeout > 0
                            else listen_timeout
                        )
                    except getattr(sr, "WaitTimeoutError", Exception):
                        continue
                    except Exception:
                        continue

                    text = self._recognize_text(audio)
                    if not text:
                        continue

                    phrase = text.strip().lower()
                    if not phrase:
                        continue

                    with self.lock:
                        cb = self.callbacks.get(phrase)

                    if cb:
                        threading.Thread(target=cb, daemon=True).start()
        except Exception as exc:
            print(f"[Voice] Listener stopped: {exc}")

    def capture_once(
        self,
        timeout: Optional[float] = None,
        phrase_time_limit: Optional[float] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Capture a single voice input for testing purposes."""

        if not self.available or sr is None:
            return None, "Voice recognition not available."

        recognizer = sr.Recognizer()
        self._apply_recognizer_settings(recognizer)

        try:
            with sr.Microphone(device_index=self.device_index) as source:
                try:
                    recognizer.adjust_for_ambient_noise(
                        source,
                        duration=self.ambient_duration
                    )
                except Exception:
                    pass

                audio = recognizer.listen(
                    source,
                    timeout=(
                        timeout
                        if timeout is not None
                        else (self.initial_timeout if self.initial_timeout > 0 else None)
                    ),
                    phrase_time_limit=(
                        phrase_time_limit
                        if phrase_time_limit is not None
                        else (
                            self.phrase_time_limit
                            if self.phrase_time_limit > 0
                            else None
                        )
                    )
                )
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)

        text = self._recognize_text(audio, recognizer=recognizer)
        if text is None:
            return "", None

        return text.strip().lower(), None


voice_listener = VoiceListener()


class VoiceTestDialog(tk.Toplevel):
    """Dialog for validating voice commands and macro triggers."""

    def __init__(
        self,
        parent,
        app,
        phrases_map: Dict[str, Callable]
    ):
        super().__init__(parent)
        self.app = app
        self.phrases_map = {k.strip().lower(): v for k, v in phrases_map.items()}
        self.title("Voice and Macro Test")
        self.geometry("430x360")

        info = tk.Label(
            self,
            text=(
                "Speak one of the configured phrases to trigger the macro.\n"
                "Use the test button to ensure your microphone and phrases are"
                " working."
            ),
            wraplength=400,
            justify="left"
        )
        info.pack(padx=10, pady=(10, 6), anchor="w")

        phrases_text = "\n".join(
            f"• {phrase}" for phrase in sorted(self.phrases_map.keys())
        ) or "No phrases configured."

        tk.Label(
            self,
            text="Available phrases:",
            font=("Arial", 10, "bold")
        ).pack(anchor="w", padx=10)

        tk.Message(
            self,
            text=phrases_text,
            width=400
        ).pack(fill="x", padx=10, pady=(0, 8))

        self.status_var = tk.StringVar(value="Waiting for test...")
        self.heard_var = tk.StringVar(value="(nothing yet)")

        self.btn_listen = tk.Button(
            self,
            text="🎤 Listen and Test",
            command=self.start_listen,
            bg="#ADD8E6"
        )
        self.btn_listen.pack(fill="x", padx=10, pady=4)

        tk.Label(self, textvariable=self.status_var, fg="gray").pack(
            anchor="w", padx=12
        )
        tk.Label(
            self,
            textvariable=self.heard_var,
            font=("Arial", 10, "bold")
        ).pack(anchor="w", padx=12, pady=(0, 8))

        manual = tk.Frame(self)
        manual.pack(fill="x", padx=10, pady=(6, 10))

        tk.Label(manual, text="Run phrase manually:").pack(
            anchor="w"
        )
        self.entry_manual = ttk.Entry(manual)
        self.entry_manual.pack(fill="x", pady=2)
        tk.Button(
            manual,
            text="Run macro",
            command=self.run_manual_phrase,
            bg="#90ee90"
        ).pack(fill="x", pady=2)

    def start_listen(self):
        """Start a one-off listening test."""
        self.btn_listen.config(state="disabled", text="Listening...")
        self.status_var.set("Speak the configured command now...")
        self.heard_var.set("(listening)")
        threading.Thread(target=self._listen_worker, daemon=True).start()

    def _listen_worker(self):
        phrase, error = voice_listener.capture_once()

        def finalize():
            self.btn_listen.config(state="normal", text="🎤 Listen and Test")
            if error:
                self.status_var.set(f"Error while listening: {error}")
                return

            if phrase is None:
                self.status_var.set("Voice unavailable.")
                return

            normalized = phrase.strip()
            self.heard_var.set(normalized or "(nothing recognized)")

            if not normalized:
                self.status_var.set("No phrase was recognized.")
                return

            triggered = self._trigger_phrase(normalized)
            if triggered:
                self.status_var.set("Macro triggered successfully!")
            else:
                self.status_var.set("Phrase recognized, but no macro is linked.")

        self.after(0, finalize)

    def _trigger_phrase(self, phrase: str) -> bool:
        """Execute macro for the given phrase if available."""
        action = self.phrases_map.get(phrase.strip().lower())
        if not action:
            return False

        threading.Thread(target=action, daemon=True).start()
        return True

    def run_manual_phrase(self):
        """Trigger macro manually from text input."""
        phrase = self.entry_manual.get().strip().lower()
        if not phrase:
            self.status_var.set("Enter a phrase to test.")
            return

        if self._trigger_phrase(phrase):
            self.status_var.set("Macro executed manually.")
        else:
            self.status_var.set("No macro is linked to that phrase.")


__all__ = [
    "VoiceListener",
    "VoiceTestDialog",
    "voice_listener",
]
