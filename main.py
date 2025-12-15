"""
Dominant Control for iRacing 
~~~~~~~~~~~~~~~~~~~~~~~

A comprehensive control management application for iRacing that provides:
- Dynamic driver control adjustment (brake bias, traction control, etc.)
- Multi-device input support (keyboard, joystick, wheel buttons)
- HUD overlay with real-time telemetry
- Per-car and per-track preset management
- Macro/combo system for quick adjustments

Author: Nishizumi Maho
All Rights Reserved
Version: 1.0.0
"""

import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, filedialog
import time
import ctypes
import keyboard
import irsdk
import json
import os
import sys
import random
import warnings
import threading
import subprocess
import importlib
import queue
import numbers
import tempfile
import wave
from typing import Dict, List, Tuple, Optional, Any, Callable

# ======================================================================
# WATCHDOG UTILITY
# ======================================================================


class Watchdog:
    """Simple watchdog to monitor heartbeats and run recovery callbacks."""

    def __init__(
        self,
        name: str,
        *,
        interval_s: float = 2.0,
        timeout_s: float = 6.0,
        on_trip: Optional[Callable[[], None]] = None
    ):
        self.name = name
        self.interval_s = max(0.5, interval_s)
        self.timeout_s = max(self.interval_s, timeout_s)
        self.on_trip = on_trip
        self._last_heartbeat = time.time()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def beat(self):
        """Record a heartbeat from the monitored worker."""

        self._last_heartbeat = time.time()

    def start(self):
        """Start the watchdog monitor thread."""

        if self._thread and self._thread.is_alive():
            return

        if self._stop_event.is_set():
            self._stop_event = threading.Event()

        self._last_heartbeat = time.time()
        self._thread = threading.Thread(
            target=self._run, name=f"{self.name}-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop monitoring."""

        self._stop_event.set()

    def _run(self):
        while not self._stop_event.wait(self.interval_s):
            elapsed = time.time() - self._last_heartbeat
            if elapsed <= self.timeout_s:
                continue

            try:
                if self.on_trip:
                    self.on_trip()
            except Exception as exc:  # noqa: PERF203
                print(f"[Watchdog:{self.name}] Recovery failed: {exc}")

            self._last_heartbeat = time.time()

# ======================================================================
# WARNING SUPPRESSION
# ======================================================================
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*"
)

# Optional dependencies
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False
    print("Warning: 'pygame' not installed. Joystick support disabled.")

try:
    import pyttsx3
    HAS_TTS = True
except ImportError:
    HAS_TTS = False
    print("Warning: 'pyttsx3' not installed. TTS disabled.")

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    pyaudio = None
    HAS_PYAUDIO = False
    print("Warning: 'pyaudio' not installed. Audio device selection limited.")

_speech_spec = importlib.util.find_spec("speech_recognition")
if _speech_spec is not None:
    import speech_recognition as sr
    HAS_SPEECH = True
else:
    sr = None
    HAS_SPEECH = False
    print("Warning: 'speech_recognition' not installed. Voice triggers disabled.")

try:
    import vosk
    HAS_VOSK = True
except ImportError:
    vosk = None
    HAS_VOSK = False

# ======================================================================
# GLOBAL CONFIGURATION
# ======================================================================
APP_NAME = "DominantControl"
APP_VERSION = "3.0.0"
APP_FOLDER = "DominantControl"
BASE_PATH = os.getenv("APPDATA") or os.path.expanduser("~")
CONFIG_FOLDER = os.path.join(BASE_PATH, APP_FOLDER, "configs")
os.makedirs(CONFIG_FOLDER, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_FOLDER, "config_v3.json")
PENDING_SCAN_FILE = os.path.join(CONFIG_FOLDER, "pending_scan.flag")

# Overlay feedback defaults (per-car thresholds)
DEFAULT_OVERLAY_FEEDBACK = {
    "abs_hold_s": 0.35,
    "tc_hold_s": 0.35,
    "wheelspin_slip": 0.18,
    "wheelspin_hold_s": 0.25,
    "lockup_slip": 0.2,
    "lockup_hold_s": 0.25,
    "cooldown_s": 6.0
}

# Timing profiles for input simulation (click-click timing)
GLOBAL_TIMING = {
    "profile": "aggressive",  # "aggressive", "casual", "relaxed", "custom", "bot"
    # Custom profile settings:
    "press_min_ms": 60,
    "press_max_ms": 80,
    "interval_min_ms": 60,
    "interval_max_ms": 90,
    "random_enabled": False,
    "random_range_ms": 10
}

# TTS cooldown to prevent spam
TTS_STATE = {
    "last_text": "",
    "last_time": 0.0,
    "cooldown_s": 1.2
}

# Shared TTS resources (initialized lazily to avoid startup overhead)
_TTS_ENGINE = None
_TTS_THREAD: Optional[threading.Thread] = None
_TTS_QUEUE: "queue.Queue[str]" = queue.Queue()
_TTS_LOCK = threading.Lock()
TTS_OUTPUT_DEVICE_INDEX: Optional[int] = None

# Voice tuning defaults (speech recognition responsiveness/accuracy)
VOICE_TUNING_DEFAULTS = {
    "ambient_duration": 0.2,        # Time to calibrate ambient noise
    "initial_timeout": 1.0,         # First listen timeout (seconds)
    "continuous_timeout": 0.8,      # Subsequent listen timeout (seconds)
    "phrase_time_limit": 1.2,       # Max length of each phrase (seconds)
    "energy_threshold": None,       # Static mic threshold (None = auto)
    "dynamic_energy": True          # Auto-adjust mic threshold
}

# Initialize config file if it doesn't exist
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write("{}")


def restart_program():
    """Restart the application by closing and relaunching the process."""
    python = sys.executable
    script = os.path.abspath(sys.argv[0])
    args = [python, script, *sys.argv[1:]]

    try:
        subprocess.Popen(args, cwd=os.getcwd(), env=os.environ.copy())
    except Exception as exc:
        print(f"[Restart] Failed to spawn new process: {exc}")

    try:
        root = tk._default_root
        if root is not None:
            root.quit()
            root.destroy()
    except Exception:
        pass

    # Ensure the current process exits so the window fully closes
    os._exit(0)


def mark_pending_scan():
    """Persist a marker so the next launch triggers a rescan."""
    try:
        with open(PENDING_SCAN_FILE, "w", encoding="utf-8") as flag:
            flag.write("rescan")
    except Exception as exc:
        print(f"[PendingScan] Failed to persist marker: {exc}")


def consume_pending_scan() -> bool:
    """Return True if a persisted rescan marker was present and clear it."""
    if not os.path.exists(PENDING_SCAN_FILE):
        return False

    try:
        os.remove(PENDING_SCAN_FILE)
    except Exception as exc:
        print(f"[PendingScan] Failed to clear marker: {exc}")

    return True


# ======================================================================
# LOW-LEVEL INPUT ENGINE (CTYPES)
# ======================================================================
IS_WINDOWS = os.name == "nt" and hasattr(ctypes, "windll")

if IS_WINDOWS:
    SendInput = ctypes.windll.user32.SendInput
else:
    SendInput = None
    print("Warning: Windows SendInput APIs unavailable; input injection disabled.")

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    """Keyboard input structure for SendInput."""
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL)
    ]


class HardwareInput(ctypes.Structure):
    """Hardware input structure for SendInput."""
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort)
    ]


class MouseInput(ctypes.Structure):
    """Mouse input structure for SendInput."""
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL)
    ]


class Input_I(ctypes.Union):
    """Union of input types."""
    _fields_ = [
        ("ki", KeyBdInput),
        ("mi", MouseInput),
        ("hi", HardwareInput)
    ]


class Input(ctypes.Structure):
    """Input structure for SendInput."""
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", Input_I)
    ]


def press_key(scan_code: int):
    """
    Press a key using its scan code.

    Args:
        scan_code: The keyboard scan code to press
    """
    if SendInput is None:
        raise OSError("SendInput APIs are only available on Windows platforms.")

    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, 0x0008, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def release_key(scan_code: int):
    """
    Release a key using its scan code.

    Args:
        scan_code: The keyboard scan code to release
    """
    if SendInput is None:
        raise OSError("SendInput APIs are only available on Windows platforms.")

    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, 0x0008 | 0x0002, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def _normalize_timing_config(timing: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize timing configuration and ensure required keys exist.

    Args:
        timing: Raw timing configuration dictionary.

    Returns:
        A sanitized copy with validated bounds and known profiles.
    """
    normalized = dict(GLOBAL_TIMING)
    if not isinstance(timing, dict):
        return normalized

    normalized.update(timing)

    allowed_profiles = {"aggressive", "casual", "relaxed", "custom", "bot"}
    if normalized.get("profile") not in allowed_profiles:
        normalized["profile"] = "aggressive"

    for key in [
        "press_min_ms",
        "press_max_ms",
        "interval_min_ms",
        "interval_max_ms",
        "random_range_ms"
    ]:
        try:
            normalized[key] = max(1, int(normalized.get(key, GLOBAL_TIMING[key])))
        except (TypeError, ValueError, KeyError):
            normalized[key] = GLOBAL_TIMING.get(key, 10)

    normalized["random_enabled"] = bool(normalized.get("random_enabled", False))

    return normalized


def _compute_timing(is_float: bool = False) -> Tuple[float, float]:
    """
    Compute press and interval timing based on global profile.
    
    Args:
        is_float: Whether this is for a float variable (gets extra delay)
        
    Returns:
        Tuple of (press_time_seconds, interval_time_seconds)
    """
    timing_cfg = _normalize_timing_config(GLOBAL_TIMING)
    profile = timing_cfg.get("profile", "aggressive")

    if profile == "aggressive":
        press_ms = 10
        interval_ms = 10
    elif profile == "casual":
        press_ms = 80
        interval_ms = 100
    elif profile == "relaxed":
        press_ms = 150
        interval_ms = 200
    elif profile == "bot":
        press_ms = 1
        interval_ms = 1
    else:  # custom
        p_min = timing_cfg.get("press_min_ms", 60)
        p_max = timing_cfg.get("press_max_ms", 80)
        i_min = timing_cfg.get("interval_min_ms", 60)
        i_max = timing_cfg.get("interval_max_ms", 90)
        press_ms = random.uniform(p_min, p_max)
        interval_ms = random.uniform(i_min, i_max)

        if timing_cfg.get("random_enabled", False):
            rng = timing_cfg.get("random_range_ms", 10)
            press_ms += random.uniform(-rng, rng)
            interval_ms += random.uniform(-rng, rng)

    # Ensure minimum values, allowing extremely low latency for bot mode
    min_value = 1 if profile == "bot" else 10
    press_ms = max(min_value, press_ms)
    interval_ms = max(min_value, interval_ms)

    # Add extra delay for float variables unless running bot profile
    if is_float and profile != "bot":
        press_ms += 30

    return press_ms / 1000.0, interval_ms / 1000.0


def click_pulse(scan_code: Optional[int], is_float: bool = False):
    """
    Execute a single key press pulse with timing.
    
    Args:
        scan_code: The keyboard scan code to pulse
        is_float: Whether this is for a float variable
    """
    if not scan_code:
        return
    try:
        code = int(scan_code)
        t_press, t_interval = _compute_timing(is_float=is_float)
        press_key(code)
        time.sleep(t_press)
        release_key(code)
        time.sleep(t_interval)
    except Exception as e:
        print(f"[click_pulse] Error: {e}")


def _direct_pulse(scan_code: Optional[int], press_ms: int, interval_ms: int):
    """
    Execute a single key press pulse with explicit timing overrides.

    Args:
        scan_code: The keyboard scan code to pulse.
        press_ms: Duration to hold the key in milliseconds.
        interval_ms: Post-release interval in milliseconds.
    """
    if not scan_code:
        return

    try:
        code = int(scan_code)
        press_key(code)
        time.sleep(max(1, press_ms) / 1000.0)
        release_key(code)
        time.sleep(max(1, interval_ms) / 1000.0)
    except Exception as e:
        print(f"[_direct_pulse] Error: {e}")


def _select_english_voice(engine) -> Optional[str]:
    """Pick the best available English voice from the host engine."""

    try:
        voices = engine.getProperty("voices") or []
    except Exception:
        return None

    preferred: Optional[str] = None
    fallback: Optional[str] = None

    for voice in voices:
        name = str(getattr(voice, "name", "")).lower()
        vid = str(getattr(voice, "id", "")).lower()
        languages = [str(lang).lower() for lang in getattr(voice, "languages", [])]

        is_english = (
            "english" in name
            or "en" in vid
            or any("en" in lang for lang in languages)
        )

        if not is_english:
            continue

        if any(key in vid or key in name for key in ["en-us", "enus", "united states"]):
            return getattr(voice, "id", None)

        if fallback is None:
            fallback = getattr(voice, "id", None)

    return preferred or fallback


def _ensure_tts_engine():
    """Initialize and cache the shared TTS engine."""
    global _TTS_ENGINE, _TTS_THREAD

    if not HAS_TTS:
        return None

    with _TTS_LOCK:
        if _TTS_ENGINE is None:
            try:
                engine = pyttsx3.init()
                voice_id = _select_english_voice(engine)
                if voice_id:
                    engine.setProperty("voice", voice_id)
                engine.setProperty("rate", 185)
                _TTS_ENGINE = engine
            except Exception as exc:  # noqa: PERF203
                print(f"[TTS] Failed to initialize engine: {exc}")
                _TTS_ENGINE = None

        if _TTS_THREAD is None or not _TTS_THREAD.is_alive():
            _TTS_THREAD = threading.Thread(target=_tts_worker, daemon=True)
            _TTS_THREAD.start()

    return _TTS_ENGINE


def _play_wave_file(path: str, output_device: Optional[int]) -> bool:
    """Play a WAV file via PyAudio on the selected output device."""

    if not HAS_PYAUDIO:
        return False

    try:
        with wave.open(path, "rb") as wf:
            pa = pyaudio.PyAudio()
            try:
                stream_params = dict(
                    format=pa.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True
                )
                if output_device is not None and output_device >= 0:
                    stream_params["output_device_index"] = int(output_device)

                stream = pa.open(**stream_params)
                try:
                    data = wf.readframes(1024)
                    while data:
                        stream.write(data)
                        data = wf.readframes(1024)
                finally:
                    stream.stop_stream()
                    stream.close()
            finally:
                pa.terminate()
    except Exception as exc:  # noqa: PERF203
        print(f"[TTS] Audio playback failed: {exc}")
        return False

    return True


def _tts_worker():
    """Background worker to serialize speech requests and reduce latency."""
    while True:
        text = _TTS_QUEUE.get()
        if text is None:
            return

        engine = _ensure_tts_engine()
        if not engine:
            _TTS_QUEUE.task_done()
            continue

        try:
            if TTS_OUTPUT_DEVICE_INDEX is not None and HAS_PYAUDIO:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                    temp_path = tmp.name
                try:
                    engine.save_to_file(text, temp_path)
                    engine.runAndWait()
                    if not _play_wave_file(temp_path, TTS_OUTPUT_DEVICE_INDEX):
                        engine.say(text)
                        engine.runAndWait()
                finally:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
            else:
                engine.say(text)
                engine.runAndWait()
        except Exception as exc:  # noqa: PERF203
            print(f"[TTS] Playback failed: {exc}")
        finally:
            _TTS_QUEUE.task_done()


def speak_text(text: str):
    """Speak text using TTS with cooldown to prevent spam and reduce startup cost."""

    if not HAS_TTS or not text:
        return

    now = time.time()
    if text == TTS_STATE["last_text"] and (now - TTS_STATE["last_time"] < TTS_STATE["cooldown_s"]):
        return

    TTS_STATE["last_text"] = text
    TTS_STATE["last_time"] = now

    if not _ensure_tts_engine():
        return

    try:
        _TTS_QUEUE.put_nowait(text)
    except Exception:
        pass


# ======================================================================
# INPUT MANAGER (Keyboard + Joystick)
# ======================================================================
class InputManager:
    """
    Manages input from keyboard and joystick devices.
    
    Supports safe mode (keyboard only) and selective device enabling.
    """

    def __init__(self):
        self.joysticks: List[Any] = []
        self.listeners: Dict[str, Callable] = {}  # Input code -> callback
        self.active: bool = False
        self.allowed_devices: List[str] = []
        self.safe_mode: bool = False
        self._input_thread: Optional[threading.Thread] = None
        self._input_watchdog = Watchdog(
            "InputManager", interval_s=2.5, timeout_s=8.0, on_trip=self._restart_input_loop
        )
        # Prevent SDL from grabbing exclusive haptics/XInput handles when we only need button input
        self.preserve_game_ffb: bool = os.getenv("DOMINANTCONTROL_PRESERVE_FFB", "1") == "1"

        if HAS_PYGAME:
            try:
                if self.preserve_game_ffb:
                    # Prefer the legacy/direct drivers instead of HIDAPI/XInput to avoid taking over FFB
                    os.environ.setdefault("SDL_HINT_JOYSTICK_HIDAPI", "0")
                    os.environ.setdefault("SDL_HINT_XINPUT_ENABLED", "0")
                    os.environ.setdefault("SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
                pygame.init()
                pygame.joystick.init()
                self._start_input_loop()
            except Exception as e:
                print(f"[InputManager] Pygame init error: {e}")

    def set_safe_mode(self, enabled: bool):
        """
        Enable/disable safe mode (keyboard only).
        
        Args:
            enabled: True for keyboard only, False to enable joysticks
        """
        self.safe_mode = enabled
        if self.safe_mode:
            if HAS_PYGAME:
                try:
                    pygame.quit()
                except Exception:
                    pass
        else:
            if HAS_PYGAME:
                try:
                    if not pygame.get_init():
                        pygame.init()
                    if not pygame.joystick.get_init():
                        pygame.joystick.init()
                    self._start_input_loop()
                except Exception as e:
                    print(f"[InputManager] Error reactivating pygame: {e}")

    def get_all_devices(self) -> List[Tuple[int, str]]:
        """
        Get all available joystick devices.
        
        Returns:
            List of (device_id, device_name) tuples
        """
        if self.safe_mode or not HAS_PYGAME:
            return []

        try:
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            devices = []
            count = pygame.joystick.get_count()

            for i in range(count):
                try:
                    j = pygame.joystick.Joystick(i)
                    if not j.get_init():
                        j.init()
                    devices.append((i, j.get_name()))
                except Exception:
                    devices.append((i, f"Device {i} (Error)"))
                    
            return devices
        except Exception as e:
            print(f"[InputManager] Error getting devices: {e}")
            return []

    def connect_allowed_devices(self, allowed_names: List[str]):
        """
        Connect only devices in the allowed list.
        
        Args:
            allowed_names: List of device names to allow
        """
        if self.safe_mode or not HAS_PYGAME:
            return

        self.joysticks.clear()
        self.allowed_devices = list(allowed_names)

        try:
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()

            if not self.allowed_devices:
                # No devices have been approved yet
                return

            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                if j.get_name() in self.allowed_devices:
                    try:
                        j.init()
                        self.joysticks.append(j)
                        print(f"[InputManager] Connected: {j.get_name()}")
                    except Exception:
                        pass
        except Exception:
            pass

    def _start_input_loop(self, force: bool = False):
        """Start or restart the input loop thread with watchdog protection."""

        if not HAS_PYGAME:
            return

        if not force and self._input_thread and self._input_thread.is_alive():
            return

        self._input_thread = threading.Thread(
            target=self._input_loop_with_watchdog, daemon=True, name="InputLoop"
        )
        self._input_thread.start()
        self._input_watchdog.start()

    def _restart_input_loop(self):
        """Attempt to restart the input loop if the watchdog detects a stall."""

        if self.safe_mode or not HAS_PYGAME:
            return

        if self._input_thread and self._input_thread.is_alive():
            return

        print("[InputManager][Watchdog] Input loop unresponsive, restarting...")
        self._start_input_loop(force=True)

    def _input_loop_with_watchdog(self):
        """Background loop to capture joystick events and feed watchdog."""
        while True:
            try:
                if not self.safe_mode and HAS_PYGAME and pygame.get_init():
                    pygame.event.pump()
                    if self.active:
                        events = pygame.event.get()
                        for event in events:
                            if event.type == pygame.JOYBUTTONDOWN:
                                code = f"JOY:{event.joy}:{event.button}"
                                if code in self.listeners:
                                    threading.Thread(
                                        target=self.listeners[code],
                                        daemon=True
                                    ).start()
            except Exception:
                pass
            finally:
                self._input_watchdog.beat()
            time.sleep(0.01)

    def capture_any_input(self, timeout: float = 10.0) -> Optional[str]:
        """
        Capture any keyboard or joystick input.
        
        Args:
            timeout: Maximum time to wait for input in seconds
            
        Returns:
            Input code string (KEY:name or JOY:id:button) or None if timeout
        """
        captured_code = None
        start = time.time()

        def key_hook(e):
            nonlocal captured_code
            if e.event_type == 'down':
                if e.name == 'esc':
                    captured_code = "CANCEL"
                elif e.name:
                    captured_code = f"KEY:{e.name.upper()}"

        try:
            hook = keyboard.hook(key_hook)
        except Exception:
            hook = None

        try:
            while time.time() - start < timeout:
                if captured_code:
                    break

                # Check joystick buttons
                if not self.safe_mode and HAS_PYGAME and pygame.get_init():
                    try:
                        pygame.event.pump()
                        for joy in self.joysticks:
                            try:
                                for b_idx in range(joy.get_numbuttons()):
                                    if joy.get_button(b_idx):
                                        captured_code = f"JOY:{joy.get_id()}:{b_idx}"
                                        break
                            except Exception:
                                pass
                            if captured_code:
                                break
                    except Exception:
                        pass

                if captured_code:
                    break
                    
                time.sleep(0.02)
        finally:
            if hook:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass

        return captured_code

    def capture_keyboard_scancode(self, timeout: float = 10.0) -> Tuple[Optional[int], Optional[str]]:
        """
        Capture a keyboard scan code with timeout and cancellation support.

        Returns:
            Tuple of (scan_code, key_name) or (None, None) if timeout/cancel
        """
        while keyboard.is_pressed('enter'):
            time.sleep(0.05)

        done = threading.Event()
        result: Dict[str, Optional[Any]] = {"scan": None, "name": None}

        def on_event(e):
            if e.event_type == 'down':
                if e.name == 'esc':
                    result["name"] = "CANCEL"
                else:
                    result["scan"] = e.scan_code
                    result["name"] = e.name
                done.set()

        hook = keyboard.hook(on_event, suppress=True)
        done.wait(timeout)
        keyboard.unhook(hook)

        if result["name"] == "CANCEL":
            return None, "CANCEL"

        return result["scan"], result["name"]


# Global input manager instance
input_manager = InputManager()


# ======================================================================
# VOICE LISTENER (Windows Speech Recognition via speech_recognition)
# ======================================================================
class VoiceListener:
    """
    Lightweight voice trigger engine backed by Windows speech recognition.

    Uses the `speech_recognition` package with the default Windows recognizer
    (SAPI). Falls back to other recognizers if unavailable.
    """

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

                # Provide a slightly longer initial wait so the user can start
                # speaking before the listener times out, then use a steady
                # shorter timeout to avoid long blocking periods.
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
                        # After the first capture, keep a shorter timeout to
                        # remain responsive while still avoiding premature
                        # timeouts on slower environments.
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


# ======================================================================
# VOICE TEST DIALOG
# ======================================================================
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

# ======================================================================
# DEVICE SELECTOR DIALOG
# ======================================================================
class DeviceSelector(tk.Toplevel):
    """
    Dialog for selecting which USB devices the application can use.
    """

    def __init__(self, parent, current_allowed: List[str], callback: Callable[[List[str]], None]):
        super().__init__(parent)
        self.title("Manage USB Devices")
        self.geometry("450x400")
        self.callback = callback

        tk.Label(
            self,
            text="Select which devices the application can use",
            font=("Arial", 10, "bold"),
            pady=10
        ).pack()

        tk.Label(
            self,
            text="Check/uncheck to allow/disallow device usage",
            fg="gray"
        ).pack()

        self.frame_list = tk.Frame(self)
        self.frame_list.pack(fill="both", expand=True, padx=10, pady=10)

        self.check_vars: Dict[str, tk.BooleanVar] = {}
        all_devices = input_manager.get_all_devices()

        for idx, name in all_devices:
            var = tk.BooleanVar()
            if current_allowed:
                var.set(name in current_allowed)
            else:
                # First run defaults to nothing selected
                var.set(False)

            chk = tk.Checkbutton(
                self.frame_list, 
                text=name, 
                variable=var, 
                anchor="w"
            )
            chk.pack(fill="x")
            self.check_vars[name] = var

        tk.Button(
            self,
            text="Save and Apply",
            command=self.save,
            bg="#90ee90",
            height=2
        ).pack(fill="x", padx=10, pady=10)

    def save(self):
        """Save device selection and close dialog."""
        final_list = [name for name, var in self.check_vars.items() if var.get()]
        self.callback(final_list)
        self.destroy()


# ======================================================================
# HUD OVERLAY WINDOW
# ======================================================================
class OverlayWindow(tk.Toplevel):
    """
    Draggable HUD overlay showing real-time telemetry values.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.wm_attributes("-alpha", 0.85)
        self.geometry("250x150+50+50")

        self.style_cfg = {
            "bg": "black",
            "fg": "white",
            "font_size": 10,
            "opacity": 0.85
        }

        self.configure(bg=self.style_cfg["bg"])

        # Status header
        self.frame_status = tk.Frame(self, bg=self.style_cfg["bg"])
        self.frame_status.pack(fill="x", pady=2)

        self.lbl_status = tk.Label(
            self.frame_status,
            text="HUD Ready",
            fg="#00FF00",
            bg=self.style_cfg["bg"],
            font=("Consolas", self.style_cfg["font_size"] + 1, "bold")
        )
        self.lbl_status.pack(anchor="w", padx=5)

        self.separator = tk.Frame(self, bg="#333", height=1)
        self.separator.pack(fill="x", padx=2)

        # Content area
        self.frame_monitor = tk.Frame(self, bg=self.style_cfg["bg"])
        self.frame_monitor.pack(fill="both", expand=True, padx=5, pady=2)

        self.monitor_widgets: Dict[str, Tuple[tk.Label, tk.Label]] = {}

        # Drag support
        self.x = 0
        self.y = 0
        self._bind_drag(self.frame_status)
        self._bind_drag(self.lbl_status)
        self._bind_drag(self.frame_monitor)

    def _bind_drag(self, widget):
        """Bind drag events to a widget."""
        widget.bind("<Button-1>", self._start_move)
        widget.bind("<B1-Motion>", self._do_move)

    def _start_move(self, event):
        """Start dragging."""
        self.x = event.x
        self.y = event.y

    def _do_move(self, event):
        """Handle drag motion."""
        dx = event.x - self.x
        dy = event.y - self.y
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self.geometry(f"+{x}+{y}")

    def apply_style(self, style_dict: Dict[str, Any]):
        """
        Apply style configuration to the overlay.
        
        Args:
            style_dict: Dictionary with bg, fg, font_size, opacity keys
        """
        self.style_cfg.update(style_dict)
        bg = self.style_cfg["bg"]
        fg = self.style_cfg["fg"]
        fs = self.style_cfg["font_size"]
        op = self.style_cfg["opacity"]

        self.configure(bg=bg)
        self.wm_attributes("-alpha", op)

        self.frame_status.config(bg=bg)
        self.lbl_status.config(bg=bg, font=("Consolas", fs + 1, "bold"))
        self.frame_monitor.config(bg=bg)

        # Update all monitor widgets
        for row in self.frame_monitor.winfo_children():
            row.config(bg=bg)
            for child in row.winfo_children():
                txt = child.cget("text")
                is_value = (txt == "--" or (txt and (txt[0].isdigit() or txt[0] == "-")))
                if is_value:
                    child.config(bg=bg, fg=fg, font=("Consolas", fs, "bold"))
                else:
                    child.config(bg=bg, fg="#AAAAAA", font=("Consolas", fs))

    def update_status_text(self, text: str, color: str = "white"):
        """
        Update the status header text.
        
        Args:
            text: Status text to display
            color: Color name or hex code
        """
        color_map = {
            "red": "#FF4444",
            "green": "#00FF00",
            "orange": "#FFA500",
            "white": self.style_cfg["fg"]
        }
        c = color_map.get(color, color)
        try:
            self.lbl_status.config(text=text, fg=c)
        except Exception:
            pass

    def rebuild_monitor(self, var_configs: Dict[str, Dict[str, Any]]):
        """
        Rebuild the monitor display with new variables.
        
        Args:
            var_configs: Dict of var_name -> {"show": bool, "label": str}
        """
        # Clear existing widgets
        for widget in self.frame_monitor.winfo_children():
            widget.destroy()
        self.monitor_widgets.clear()

        visible_vars = [v for v, cfg in var_configs.items() if cfg.get("show", False)]
        if not visible_vars:
            return

        for var_name in visible_vars:
            cfg = var_configs.get(var_name, {})
            label_text = cfg.get("label") or var_name.replace("dc", "")

            row = tk.Frame(self.frame_monitor, bg=self.style_cfg["bg"])
            row.pack(fill="x")
            self._bind_drag(row)

            l_name = tk.Label(
                row,
                text=f"{label_text}:",
                bg=self.style_cfg["bg"],
                fg="#AAAAAA",
                font=("Consolas", self.style_cfg["font_size"]),
                width=15,
                anchor="w"
            )
            l_name.pack(side="left")
            self._bind_drag(l_name)

            l_value = tk.Label(
                row,
                text="--",
                bg=self.style_cfg["bg"],
                fg=self.style_cfg["fg"],
                font=("Consolas", self.style_cfg["font_size"], "bold")
            )
            l_value.pack(side="right")
            self._bind_drag(l_value)

            self.monitor_widgets[var_name] = (l_name, l_value)

        # Resize window
        line_height = self.style_cfg["font_size"] * 2 + 6
        h = 45 + (len(visible_vars) * line_height)
        h = max(60, min(h, 800))

        geometry = self.geometry().split('+')
        try:
            self.geometry(f"250x{h}+{geometry[1]}+{geometry[2]}")
        except Exception:
            self.geometry(f"250x{h}+50+50")

    def update_monitor_values(self, data_dict: Dict[str, Any]):
        """
        Update displayed telemetry values.
        
        Args:
            data_dict: Dict of var_name -> value
        """
        for var_name, value in data_dict.items():
            if var_name in self.monitor_widgets:
                _name_label, value_label = self.monitor_widgets[var_name]
                if value is None:
                    text = "--"
                elif isinstance(value, float):
                    text = f"{value:.3f}"
                else:
                    text = str(value)
                try:
                    value_label.config(text=text)
                except Exception:
                    pass


# ======================================================================
# SCROLLABLE FRAME WIDGET
# ======================================================================
class ScrollableFrame(tk.Frame):
    """
    Frame with vertical scrollbar.
    Use self.inner as the container for child widgets.
    """

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = tk.Frame(canvas)

        self.inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel support (bind only while hovered)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(_event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

class OverlayConfigTab(tk.Frame):
    """
    Configuration tab for HUD overlay appearance and variable display.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.var_rows: Dict[str, Dict[str, Any]] = {}

        # Scrollable layout
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        self.body = scroll_frame.inner

        # Header
        tk.Label(
            self.body,
            text="HUD / Overlay Configuration",
            font=("Arial", 11, "bold")
        ).pack(anchor="w", pady=(5, 5))

        # Global appearance settings
        appearance_frame = tk.LabelFrame(self.body, text="Global HUD Appearance")
        appearance_frame.pack(fill="x", padx=5, pady=5)

        self.btn_bg = tk.Button(
            appearance_frame, 
            text="Background Color", 
            command=self.pick_background_color
        )
        self.btn_bg.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        self.lbl_bg_preview = tk.Label(
            appearance_frame, 
            text="   ",
            bg=self.app.overlay.style_cfg.get("bg", "black"),
            relief="solid"
        )
        self.lbl_bg_preview.grid(row=0, column=1, padx=5, pady=5)

        self.btn_fg = tk.Button(
            appearance_frame, 
            text="Text Color", 
            command=self.pick_text_color
        )
        self.btn_fg.grid(row=1, column=0, padx=5, pady=5, sticky="w")

        self.lbl_fg_preview = tk.Label(
            appearance_frame,
            text="ABC",
            fg=self.app.overlay.style_cfg.get("fg", "white"),
            bg="gray",
            relief="solid"
        )
        self.lbl_fg_preview.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(appearance_frame, text="Font Size:").grid(
            row=2, column=0, padx=5, sticky="w"
        )
        self.scale_font = tk.Scale(
            appearance_frame, 
            from_=8, 
            to=24, 
            orient="horizontal"
        )
        self.scale_font.set(self.app.overlay.style_cfg.get("font_size", 10))
        self.scale_font.grid(row=2, column=1, padx=5, pady=5, sticky="we")

        tk.Label(appearance_frame, text="Opacity:").grid(
            row=3, column=0, padx=5, sticky="w"
        )
        self.scale_opacity = tk.Scale(
            appearance_frame,
            from_=0.1,
            to=1.0,
            resolution=0.05,
            orient="horizontal"
        )
        self.scale_opacity.set(self.app.overlay.style_cfg.get("opacity", 0.85))
        self.scale_opacity.grid(row=3, column=1, padx=5, pady=5, sticky="we")

        for i in range(2):
            appearance_frame.columnconfigure(i, weight=1)

        feedback_frame = tk.LabelFrame(
            self.body, text="Assist Feedback Thresholds (per car)"
        )
        feedback_frame.pack(fill="x", padx=5, pady=5)

        self.feedback_vars = {
            "abs_hold_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["abs_hold_s"]),
            "tc_hold_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["tc_hold_s"]),
            "wheelspin_slip": tk.DoubleVar(
                value=DEFAULT_OVERLAY_FEEDBACK["wheelspin_slip"]
            ),
            "wheelspin_hold_s": tk.DoubleVar(
                value=DEFAULT_OVERLAY_FEEDBACK["wheelspin_hold_s"]
            ),
            "lockup_slip": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["lockup_slip"]),
            "lockup_hold_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["lockup_hold_s"]),
            "cooldown_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["cooldown_s"]),
        }

        feedback_rows = [
            ("ABS active longer than (s)", "abs_hold_s"),
            ("TC active longer than (s)", "tc_hold_s"),
            ("Wheelspin slip (ratio)", "wheelspin_slip"),
            ("Wheelspin duration (s)", "wheelspin_hold_s"),
            ("Lock-up slip (negative value)", "lockup_slip"),
            ("Lock-up duration (s)", "lockup_hold_s"),
            ("Cooldown between alerts (s)", "cooldown_s"),
        ]

        for idx, (label, key) in enumerate(feedback_rows):
            tk.Label(feedback_frame, text=label).grid(
                row=idx, column=0, padx=5, pady=2, sticky="w"
            )
            entry = tk.Entry(feedback_frame, width=10, textvariable=self.feedback_vars[key])
            entry.grid(row=idx, column=1, padx=5, pady=2, sticky="w")
            entry.bind("<FocusOut>", self._on_feedback_change)
            entry.bind("<KeyRelease>", self._on_feedback_change)

        tk.Button(
            appearance_frame,
            text="Apply Style",
            command=self.apply_style,
            bg="#90ee90"
        ).grid(row=4, column=0, columnspan=2, sticky="we", padx=5, pady=(5, 5))

        # Variable selection (per-car)
        variables_frame = tk.LabelFrame(
            self.body, 
            text="Variables to Display (per car)"
        )
        variables_frame.pack(fill="both", expand=True, padx=5, pady=5)

        header = tk.Frame(variables_frame)
        header.pack(fill="x", pady=(3, 3))
        
        tk.Label(header, text="Show", width=8, anchor="w").pack(
            side="left", padx=2
        )
        tk.Label(header, text="Internal Name", width=25, anchor="w").pack(
            side="left", padx=2
        )
        tk.Label(header, text="HUD Label", width=20, anchor="w").pack(
            side="left", padx=2
        )

        self.variables_list_frame = tk.Frame(variables_frame)
        self.variables_list_frame.pack(fill="both", expand=True)

        tk.Label(
            self.body,
            text="Variable selections and labels are saved per car.\n"
                 "Appearance settings apply to all cars.",
            fg="gray",
            font=("Arial", 8)
        ).pack(anchor="w", padx=5, pady=(3, 10))

    def pick_background_color(self):
        """Open color picker for background color."""
        color = colorchooser.askcolor(title="Background Color")[1]
        if color:
            self.app.overlay.style_cfg["bg"] = color
            self.lbl_bg_preview.config(bg=color)
            self.apply_style()

    def pick_text_color(self):
        """Open color picker for text color."""
        color = colorchooser.askcolor(title="Text Color")[1]
        if color:
            self.app.overlay.style_cfg["fg"] = color
            self.lbl_fg_preview.config(fg=color)
            self.apply_style()

    def apply_style(self):
        """Apply current style settings to overlay."""
        self.app.overlay.style_cfg["font_size"] = int(self.scale_font.get())
        self.app.overlay.style_cfg["opacity"] = float(self.scale_opacity.get())
        self.app.overlay.apply_style(self.app.overlay.style_cfg)
        self.app.save_config()

    def load_for_car(
        self, 
        car_name: str, 
        var_list: List[Tuple[str, bool]], 
        overlay_config: Dict[str, Dict[str, Any]]
    ):
        """
        Load HUD configuration for a specific car.
        
        Args:
            car_name: Name of the car
            var_list: List of (var_name, is_float) tuples
            overlay_config: Dict of var_name -> {"show": bool, "label": str}
        """
        self._load_feedback_for_car(car_name)

        # Rebuild variable rows
        for child in self.variables_list_frame.winfo_children():
            child.destroy()
        self.var_rows.clear()

        # Ensure all variables have config entries
        for var_name, _is_float in var_list:
            if var_name not in overlay_config:
                overlay_config[var_name] = {
                    "show": False,
                    "label": var_name.replace("dc", "")
                }

        # Create UI rows
        for var_name, _is_float in var_list:
            config = overlay_config.get(var_name, {})

            row = tk.Frame(self.variables_list_frame)
            row.pack(fill="x", pady=2)

            show_var = tk.BooleanVar(value=config.get("show", False))
            checkbox = tk.Checkbutton(row, variable=show_var)
            checkbox.pack(side="left", padx=2)

            tk.Label(row, text=var_name, width=25, anchor="w").pack(
                side="left", padx=2
            )

            label_entry = tk.Entry(row, width=20)
            label_entry.pack(side="left", padx=2)
            label_entry.insert(
                0,
                config.get("label") or var_name.replace("dc", "")
            )

            self.var_rows[var_name] = {
                "show_var": show_var,
                "entry": label_entry
            }

            show_var.trace_add(
                "write",
                lambda *_args, vn=var_name: self._on_overlay_row_change(vn)
            )
            label_entry.bind(
                "<KeyRelease>",
                lambda _event, vn=var_name: self._on_overlay_row_change(vn)
            )

        self.app.car_overlay_config[car_name] = overlay_config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(overlay_config)
        self.app.save_config()

    def _on_feedback_change(self, *_args):
        """Persist feedback edits and save lazily."""

        car = self.app.current_car or "Generic Car"
        self._collect_feedback_for_car(car)
        self.app.schedule_save()

    def _on_overlay_row_change(self, var_name: str):
        """Apply live updates when overlay rows change."""
        car = self.app.current_car or "Generic Car"
        config = self.app.car_overlay_config.get(car, {})
        row = self.var_rows.get(var_name)
        if not row:
            return

        show = row["show_var"].get()
        label = row["entry"].get().strip() or var_name.replace("dc", "")
        config[var_name] = {"show": show, "label": label}
        self.app.car_overlay_config[car] = config
        self.app.overlay.rebuild_monitor(config)
        self.app.schedule_save()

    def collect_for_car(self, car_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Collect current HUD configuration for a car.
        
        Args:
            car_name: Name of the car
            
        Returns:
            Dict of var_name -> {"show": bool, "label": str}
        """
        config = self.app.car_overlay_config.get(car_name, {})

        for var_name, row_config in self.var_rows.items():
            show = row_config["show_var"].get()
            label = row_config["entry"].get().strip() or var_name.replace("dc", "")
            config[var_name] = {"show": show, "label": label}

        self.app.car_overlay_config[car_name] = config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(config)
        return config

    def _load_feedback_for_car(self, car_name: str) -> None:
        """Load per-car feedback thresholds into the UI fields."""

        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(self.app.car_overlay_feedback.get(car_name, {}))

        for key, var in self.feedback_vars.items():
            try:
                var.set(float(cfg.get(key, DEFAULT_OVERLAY_FEEDBACK[key])))
            except Exception:
                var.set(DEFAULT_OVERLAY_FEEDBACK[key])

    def _collect_feedback_for_car(self, car_name: str) -> Dict[str, float]:
        """Persist feedback thresholds from UI fields for a car."""

        cfg: Dict[str, float] = DEFAULT_OVERLAY_FEEDBACK.copy()
        for key, var in self.feedback_vars.items():
            try:
                cfg[key] = float(var.get())
            except Exception:
                cfg[key] = DEFAULT_OVERLAY_FEEDBACK[key]

        self.app.car_overlay_feedback[car_name] = cfg
        return cfg


# ======================================================================
# GENERIC CONTROLLER
# ======================================================================
class GenericController:
    """
    Controller for adjusting a single telemetry variable via key presses.
    """

    def __init__(
        self,
        ir_instance,
        var_name: str,
        is_float: bool = False,
        status_callback: Optional[Callable[[str, str], None]] = None,
        app_ref=None
    ):
        self.ir = ir_instance
        self.var_name = var_name
        self.is_float = is_float
        self.running_action = False
        self.key_increase = None
        self.key_decrease = None
        self.update_status = status_callback
        self.app = app_ref

    def read_telemetry(self) -> Optional[float]:
        """
        Read current value of the controlled variable.
        
        Returns:
            Current value or None if unavailable
        """
        try:
            if not getattr(self.ir, "is_initialized", False):
                try:
                    self.ir.startup()
                except Exception:
                    return None
                    
            value = self.ir[self.var_name]
            if value is None:
                return None
                
            if self.is_float:
                return float(value)
            else:
                return int(round(value))
        except Exception:
            return None

    def _detect_float_step(self) -> Optional[float]:
        """Detect the minimal float increment by pulsing once and restoring."""
        if not self.is_float:
            return None
        if not self.key_increase or not self.key_decrease:
            return None

        baseline = self.read_telemetry()
        if baseline is None:
            return None

        # Pulse upward and measure the delta
        click_pulse(self.key_increase, is_float=True)
        time.sleep(0.08)
        raised = self.read_telemetry()

        if raised is None:
            return None

        step = abs(float(raised) - float(baseline))

        # Try to return near the starting point
        click_pulse(self.key_decrease, is_float=True)
        time.sleep(0.08)

        if step < 1e-6:
            return None

        return step

    def _resolve_target(self, target: float) -> float:
        """Align float targets to the nearest reachable increment when needed."""
        if not self.is_float:
            return target

        step = self._detect_float_step()
        current = self.read_telemetry()

        if step is None or step <= 0 or current is None:
            return target

        aligned = current + round((target - current) / step) * step

        if abs(aligned - target) >= 0.0005:
            if self.update_status:
                self.update_status(f"Rounded to {aligned:.3f}", "orange")
            if self.app:
                short_name = self.var_name.replace("dc", "")
                self.app.notify_overlay_status(
                    f"{short_name}: using {aligned:.3f} (nearest)",
                    "orange"
                )

        return aligned

    def adjust_to_target(self, target: float):
        """
        Adjust variable to target value using discrete key presses.
        
        Args:
            target: Target value to reach
        """
        if self.running_action:
            return

        if not self.key_increase or not self.key_decrease:
            if self.update_status:
                self.update_status("No keys configured", "red")
            if self.app:
                self.app.notify_overlay_status(
                    f"{self.var_name.replace('dc', '')}: No keys", 
                    "red"
                )
            return

        self.running_action = True
        short_name = self.var_name.replace("dc", "")

        target = self._resolve_target(target)
        if not self.is_float:
            target = int(round(target))

        if self.update_status:
            self.update_status("Adjusting...", "orange")
        if self.app:
            self.app.notify_overlay_status(
                f"Adjusting {short_name} -> {target}",
                "orange"
            )

        timeout = time.time() + 8
        success = False

        try:
            while time.time() < timeout:
                # Abort if entering CONFIG mode
                if self.app and self.app.app_state != "RUNNING":
                    break

                current = self.read_telemetry()
                if current is None:
                    break

                diff = target - current
                abs_diff = abs(diff)

                # Check if target reached
                if self.is_float and abs_diff < 0.001:
                    success = True
                    break
                if not self.is_float and diff == 0:
                    success = True
                    break

                # Press appropriate key
                key = self.key_increase if diff > 0 else self.key_decrease
                click_pulse(key, self.is_float)
                time.sleep(0.02)

        except Exception as e:
            print(f"[GenericController] Exception: {e}")
        finally:
            if success:
                message = f"{short_name} OK ({target})"
                if self.update_status:
                    self.update_status("Ready", "green")
                if self.app:
                    self.app.notify_overlay_status(message, "green")
                    if self.app.use_tts.get():
                        speak_text(message)
            else:
                status = "Cancelled" if (
                    self.app and self.app.app_state != "RUNNING"
                ) else "Failed"
                
                if self.update_status:
                    self.update_status(status, "red")
                if self.app:
                    status_msg = (
                        f"{short_name} Cancelled" 
                        if self.app.app_state != "RUNNING" 
                        else f"{short_name} Failed"
                    )
                    self.app.notify_overlay_status(status_msg, "red")

            self.running_action = False

    def find_minimum_effective_timing(
        self,
        start_ms: int = 1,
        max_ms: int = 120,
        step_ms: int = 1,
        settle_s: float = 0.05,
        confirmation_attempts: int = 2
    ) -> Optional[int]:
        """
        Probe the minimal pulse timing that reliably updates telemetry.

        The probe fires fast pulses starting at ``start_ms`` and increments by
        ``step_ms`` until telemetry reflects a change. The first timing that
        consistently registers is returned.

        Args:
            start_ms: Initial press/interval duration in milliseconds.
            max_ms: Maximum duration to test in milliseconds.
            step_ms: Increment between attempts in milliseconds.
            settle_s: Delay after a pulse to allow telemetry to settle.
            confirmation_attempts: Number of retries per timing bucket.

        Returns:
            Suggested minimal working pulse duration in milliseconds, or None
            if no timing within bounds registers.
        """
        if not self.key_increase or not self.key_decrease:
            raise ValueError("Increase/decrease keys must be configured before probing.")

        baseline = self.read_telemetry()
        if baseline is None:
            return None

        def _changed(old, new) -> bool:
            if old is None or new is None:
                return False
            if self.is_float:
                return abs(float(new) - float(old)) >= 0.0005
            return int(round(new)) != int(round(old))

        def _restore(target_value: float, timing_ms: int):
            """Attempt to revert telemetry back near baseline after a test."""
            for _ in range(5):
                current = self.read_telemetry()
                if current is None:
                    break
                if not _changed(target_value, current):
                    break
                direction = self.key_decrease if current > target_value else self.key_increase
                _direct_pulse(direction, timing_ms, timing_ms)
                time.sleep(settle_s)

        for delay_ms in range(max(1, start_ms), max_ms + 1, max(1, step_ms)):
            success_count = 0
            for _ in range(max(1, confirmation_attempts)):
                _direct_pulse(self.key_increase, delay_ms, delay_ms)
                time.sleep(settle_s)
                updated = self.read_telemetry()
                if _changed(baseline, updated):
                    success_count += 1
                else:
                    break

            _restore(baseline, delay_ms)

            if success_count >= confirmation_attempts:
                return delay_ms

        return None


# ======================================================================
# CONTROL TAB
# ======================================================================
class ControlTab(tk.Frame):
    """
    Configuration tab for a single control variable.
    """

    def __init__(self, parent, controller: GenericController, label_name: str, app):
        super().__init__(parent)
        self.app = app
        self.controller = controller
        self.controller.update_status = self.update_status_label
        self.controller.app = app
        self.preset_rows: List[Dict[str, Any]] = []

        # Scrollable layout
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        body = scroll_frame.inner

        # Key configuration
        keys_frame = tk.LabelFrame(
            body, 
            text=f"Keys ({label_name})", 
            padx=5, 
            pady=5
        )
        keys_frame.pack(fill="x", padx=5, pady=5)

        self.btn_increase = tk.Button(
            keys_frame,
            text="Set Increase (+)",
            command=lambda: self.bind_game_key("increase")
        )
        self.btn_increase.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_decrease = tk.Button(
            keys_frame,
            text="Set Decrease (-)",
            command=lambda: self.bind_game_key("decrease")
        )
        self.btn_decrease.pack(side="left", expand=True, fill="x", padx=2)

        tk.Button(
            keys_frame,
            text="Test custom minimal time",
            command=self.run_bot_timing_probe,
            bg="#f0f8ff"
        ).pack(side="left", padx=2)

        # Current value monitor
        self.lbl_monitor = tk.Label(
            body, 
            text="Value: --", 
            font=("Arial", 14, "bold")
        )
        self.lbl_monitor.pack(pady=5)

        # Status label
        self.lbl_status = tk.Label(body, text="Idle", fg="gray")
        self.lbl_status.pack()

        # Presets/Macros
        presets_frame = tk.LabelFrame(
            body, 
            text="Presets / Macros", 
            padx=5, 
            pady=5
        )
        presets_frame.pack(fill="both", expand=True, padx=5, pady=5)

        tk.Label(
            presets_frame,
            text="RESET always returns to a base value (e.g., 0 or 50).",
            fg="gray",
            font=("Arial", 8)
        ).pack(anchor="w", pady=(0, 5))

        tk.Label(
            presets_frame,
            text=(
                "Voice column: type the exact phrase you will say to trigger or"
                " speak this macro. Voice/Audio Settings live under Options"
                " → Voice/Audio Settings."
            ),
            fg="gray",
            font=("Arial", 8),
            wraplength=760,
            justify="left"
        ).pack(anchor="w", padx=2, pady=(0, 5))

        self.presets_container = tk.Frame(presets_frame)
        self.presets_container.pack(fill="both", expand=True)

        # Add initial preset rows
        self.add_preset_row(is_reset=True)
        for _ in range(4):
            self.add_preset_row()

        # Start monitoring loop
        self.running = True
        self.after(500, self.monitor_loop)

    def update_status_label(self, text: str, color: str):
        """Update status label."""
        if self.app:
            self.app.ui(self.lbl_status.config, text=text, fg=color)

    def run_bot_timing_probe(self):
        """Run a fast timing probe to suggest a stable BOT delay."""

        def _worker():
            try:
                suggested = self.controller.find_minimum_effective_timing()
            except ValueError as exc:
                error_msg = str(exc)
                self.after(
                    0,
                    lambda msg=error_msg: messagebox.showerror("Keys Missing", msg)
                )
                return

            if suggested is None:
                self.after(
                    0,
                    lambda: messagebox.showwarning(
                        "Probe Result",
                        "No timing within 1-120 ms reliably updated telemetry."
                    )
                )
            else:
                msg = (
                    f"Minimal stable pulse detected at ~{suggested} ms.\n"
                    "Apply this value to BOT/custom timings for reliable updates."
                )
                self.after(0, lambda: messagebox.showinfo("Probe Result", msg))

        threading.Thread(target=_worker, daemon=True).start()

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = "normal" if enabled else "readonly"

        for row in self.preset_rows:
            try:
                row["entry"].config(state=state)
                if "voice_entry" in row:
                    row["voice_entry"].config(state=state)
            except Exception:
                pass

    def bind_game_key(self, direction: str):
        """
        Bind a game key for increase/decrease.
        
        Args:
            direction: "increase" or "decrease"
        """
        if self.app.app_state != "CONFIG":
            messagebox.showinfo("Notice", "Enter CONFIG mode first.")
            return

        self.app.focus_window()

        btn = self.btn_increase if direction == "increase" else self.btn_decrease
        original_text = btn["text"]
        btn.config(text="PRESS KEY...", bg="yellow")
        self.update_idletasks()

        scan_code, key_name = input_manager.capture_keyboard_scancode()

        if key_name == "CANCEL":
            if direction == "increase":
                self.controller.key_increase = None
            else:
                self.controller.key_decrease = None
            btn.config(text=original_text, bg="#f0f0f0")
        elif scan_code:
            if direction == "increase":
                self.controller.key_increase = scan_code
            else:
                self.controller.key_decrease = scan_code
            btn.config(text=f"OK: {key_name.upper()}", bg="#90ee90")
        else:
            btn.config(text=original_text, bg="#f0f0f0")

        self.app.schedule_save()

    def _config_bind_button(self, button: tk.Button, data_store: Dict[str, Any]):
        """Configure binding button behavior."""
        def on_click():
            if self.app.app_state != "CONFIG":
                messagebox.showinfo("Notice", "Enter CONFIG mode first.")
                return

            self.app.focus_window()

            button.config(text="...", bg="yellow")
            self.update_idletasks()

            code = input_manager.capture_any_input()

            if code and code != "CANCEL":
                data_store["bind"] = code
                bg_color = "#90ee90" if "JOY" in code else "#ADD8E6"
                button.config(text=code, bg=bg_color)
            elif code == "CANCEL":
                data_store["bind"] = None
                button.config(text="Set Bind", bg="#f0f0f0")

            self.app.schedule_save()

        button.config(command=on_click)

    def add_preset_row(
        self, 
        existing: Optional[Dict[str, Any]] = None, 
        is_reset: bool = False
    ):
        """Add a preset row to the UI."""
        frame = tk.Frame(self.presets_container)
        frame.pack(fill="x", pady=2)

        label_text = "RESET" if is_reset else "Macro"
        tk.Label(
            frame,
            text=label_text,
            width=6,
            anchor="w",
            fg="red" if is_reset else "black"
        ).pack(side="left")

        value_entry = ttk.Entry(frame, width=8)
        value_entry.pack(side="left", padx=5)

        if self.app.app_state != "CONFIG":
            value_entry.config(state="readonly")

        bind_button = tk.Button(frame, text="Set Bind", width=12)
        bind_button.pack(side="left", padx=5)

        voice_entry = ttk.Entry(frame, width=18)
        voice_entry.pack(side="left", padx=5)
        voice_entry.insert(0, "")
        if self.app.app_state != "CONFIG":
            voice_entry.config(state="readonly")

        row_data = {
            "frame": frame,
            "entry": value_entry,
            "bind": None,
            "is_reset": is_reset,
            "voice_entry": voice_entry
        }
        self._config_bind_button(bind_button, row_data)

        if existing:
            value_entry.config(state="normal")
            value_entry.delete(0, tk.END)
            value_entry.insert(0, existing.get("val", ""))
            if self.app.app_state != "CONFIG":
                value_entry.config(state="readonly")

            row_data["bind"] = existing.get("bind")
            if row_data["bind"]:
                bg_color = (
                    "#90ee90" if "JOY" in row_data["bind"] else "#ADD8E6"
                )
                bind_button.config(text=row_data["bind"], bg=bg_color)

            voice_text = existing.get("voice_phrase", "")
            voice_entry.config(state="normal")
            voice_entry.delete(0, tk.END)
            voice_entry.insert(0, voice_text)
            if self.app.app_state != "CONFIG":
                voice_entry.config(state="readonly")

        self.preset_rows.append(row_data)

    def monitor_loop(self):
        """Background loop to monitor current value."""
        if not self.running:
            return

        value = self.controller.read_telemetry()
        if value is None:
            text = "--"
        else:
            text = f"{value:.3f}" if self.controller.is_float else str(value)
        try:
            self.lbl_monitor.config(text=f"Current: {text}")
        except Exception:
            pass

        if self.running:
            self.after(500, self.monitor_loop)

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            "meta_var": self.controller.var_name,
            "meta_float": self.controller.is_float,
            "key_increase": self.controller.key_increase,
            "key_increase_text": self.btn_increase["text"],
            "key_decrease": self.controller.key_decrease,
            "key_decrease_text": self.btn_decrease["text"],
            "presets": [
                {
                    "val": row["entry"].get(),
                    "bind": row["bind"],
                    "is_reset": row.get("is_reset", False),
                    "voice_phrase": (
                        row.get("voice_entry").get() if row.get("voice_entry") else ""
                    )
                }
                for row in self.preset_rows
            ]
        }

    def destroy(self):  # type: ignore[override]
        """Ensure monitoring loop stops when widget is destroyed."""
        self.running = False
        super().destroy()

    def set_config(self, config: Dict[str, Any]):
        """Load configuration."""
        if not config:
            return

        # Set keys
        increase_key = config.get("key_increase")
        decrease_key = config.get("key_decrease")
        self.controller.key_increase = (
            int(increase_key) if increase_key is not None else None
        )
        self.controller.key_decrease = (
            int(decrease_key) if decrease_key is not None else None
        )

        self.btn_increase.config(text=config.get("key_increase_text", "Set Increase (+)"))
        self.btn_decrease.config(text=config.get("key_decrease_text", "Set Decrease (-)"))

        # Clear and rebuild preset rows
        for row in list(self.preset_rows):
            row["frame"].destroy()
        self.preset_rows.clear()

        saved_presets = config.get("presets", [])
        has_reset = any(p.get("is_reset") for p in saved_presets)

        if not has_reset:
            self.add_preset_row(is_reset=True)

        for preset in saved_presets:
            self.add_preset_row(
                existing=preset, 
                is_reset=preset.get("is_reset", False)
            )

        while sum(1 for p in self.preset_rows if not p["is_reset"]) < 4:
            self.add_preset_row()


# Due to length, I'll create a third artifact for ComboTab, GlobalTimingWindow, 
# and the main application class.


# ======================================================================
# COMBO TAB (Multi-variable macros)
# ======================================================================
class ComboTab(tk.Frame):
    """
    Tab for creating combo macros that adjust multiple variables with one trigger.
    """

    def __init__(
        self, 
        parent, 
        controllers_dict: Dict[str, GenericController], 
        app
    ):
        super().__init__(parent)
        self.app = app
        self.controllers = controllers_dict
        self.var_names = list(self.controllers.keys())
        self.preset_rows: List[Dict[str, Any]] = []

        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        body = scroll_frame.inner

        tk.Label(
            body,
            text="⚡ Combo Adjustments (one trigger -> multiple variables)",
            fg="orange",
            font=("Arial", 10, "bold")
        ).pack(pady=5)

        # Header row
        header = tk.Frame(body)
        header.pack(fill="x", padx=5, pady=5)

        tk.Label(
            header, 
            text="Trigger", 
            width=15, 
            anchor="w", 
            font=("Arial", 9, "bold")
        ).pack(side="left", padx=2)

        for var_name in self.var_names:
            tk.Label(
                header,
                text=var_name.replace("dc", ""),
                width=8,
                font=("Arial", 8)
            ).pack(side="left", padx=2)

        tk.Label(
            body,
            text=(
                "Voice column: type the exact phrase you will say to trigger"
                " this combo. Voice/Audio Settings live under Options"
                " → Voice/Audio Settings."
            ),
            fg="gray",
            font=("Arial", 8),
            wraplength=760,
            justify="left"
        ).pack(fill="x", padx=5, pady=(0, 4))

        self.presets_container = tk.Frame(body)
        self.presets_container.pack(fill="both", expand=True, padx=5, pady=5)

        tk.Button(
            body,
            text="Add Row (+)",
            command=self.add_dynamic_row,
            bg="#f0f0f0"
        ).pack(fill="x", padx=5, pady=(0, 5))

        # Add initial rows
        self.add_dynamic_row(is_reset=True)
        for _ in range(2):
            self.add_dynamic_row()

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = "normal" if enabled else "readonly"

        for row in self.preset_rows:
            for entry in row["entries"].values():
                try:
                    entry.config(state=state)
                except Exception:
                    pass
            voice_entry = row.get("voice_entry")
            if voice_entry:
                try:
                    voice_entry.config(state=state)
                except Exception:
                    pass

    def _config_bind_button(self, button: tk.Button, data_store: Dict[str, Any]):
        """Configure binding button behavior."""
        def on_click():
            if self.app.app_state != "CONFIG":
                messagebox.showinfo("Notice", "Enter CONFIG mode first.")
                return

            self.app.focus_window()

            button.config(text="...", bg="yellow")
            self.update_idletasks()

            code = input_manager.capture_any_input()

            if code and code != "CANCEL":
                data_store["bind"] = code
                bg_color = "#90ee90" if "JOY" in code else "#ADD8E6"
                button.config(text=code, bg=bg_color)
            elif code == "CANCEL":
                data_store["bind"] = None
                button.config(text="Set Bind", bg="#f0f0f0")

            self.app.schedule_save()

        button.config(command=on_click)

    def add_dynamic_row(
        self,
        existing: Optional[Dict[str, Any]] = None,
        is_reset: bool = False
    ):
        """Add a combo preset row."""
        frame = tk.Frame(self.presets_container)
        frame.pack(fill="x", pady=2)

        bind_button = tk.Button(
            frame,
            text="RESET" if is_reset else "Set Bind",
            width=15,
            fg="red" if is_reset else "black"
        )
        bind_button.pack(side="left", padx=2)

        row_data = {
            "frame": frame,
            "entries": {},
            "bind": None,
            "is_reset": is_reset,
            "voice_entry": None
        }
        self._config_bind_button(bind_button, row_data)

        # Create entry for each variable
        for var_name in self.var_names:
            entry = ttk.Entry(frame, width=8)
            entry.pack(side="left", padx=2)
            if self.app.app_state != "CONFIG":
                entry.config(state="readonly")
            row_data["entries"][var_name] = entry

        # Delete button (except for RESET)
        if not is_reset:
            tk.Button(
                frame,
                text="X",
                fg="red",
                command=lambda r=row_data: self.remove_row(r),
                width=2
            ).pack(side="left", padx=5)

        # Load existing data if provided
        if existing:
            values = existing.get("vals", {})
            for var_name, value in values.items():
                if var_name in row_data["entries"]:
                    entry = row_data["entries"][var_name]
                    entry.config(state="normal")
                    entry.insert(0, value)
                    if self.app.app_state != "CONFIG":
                        entry.config(state="readonly")

            row_data["bind"] = existing.get("bind")
            if row_data["bind"]:
                bg_color = (
                    "#90ee90" if "JOY" in row_data["bind"] else "#ADD8E6"
                )
                bind_button.config(text=row_data["bind"], bg=bg_color)

        voice_entry = ttk.Entry(frame, width=18)
        voice_entry.pack(side="left", padx=4)
        if existing and existing.get("voice_phrase"):
            voice_entry.insert(0, existing.get("voice_phrase", ""))
        if self.app.app_state != "CONFIG":
            voice_entry.config(state="readonly")
        row_data["voice_entry"] = voice_entry

        self.preset_rows.append(row_data)

    def remove_row(self, row_data: Dict[str, Any]):
        """Remove a preset row."""
        if self.app.app_state != "CONFIG":
            return

        row_data["frame"].destroy()
        if row_data in self.preset_rows:
            self.preset_rows.remove(row_data)
        self.app.schedule_save()

    def get_config(self) -> Dict[str, Any]:
        """Get current combo configuration."""
        presets_data = []
        for row in self.preset_rows:
            values = {
                var_name: entry.get()
                for var_name, entry in row["entries"].items()
            }
            presets_data.append({
                "vals": values,
                "bind": row["bind"],
                "is_reset": row["is_reset"],
                "voice_phrase": (
                    row.get("voice_entry").get() if row.get("voice_entry") else ""
                )
            })
        return {"presets": presets_data}

    def set_config(self, config: Dict[str, Any]):
        """Load combo configuration."""
        # Clear existing rows
        for row in list(self.preset_rows):
            row["frame"].destroy()
        self.preset_rows.clear()

        if not config:
            self.add_dynamic_row(is_reset=True)
            for _ in range(2):
                self.add_dynamic_row()
            return

        saved_presets = config.get("presets", [])
        has_reset = any(p.get("is_reset") for p in saved_presets)

        if not has_reset:
            self.add_dynamic_row(is_reset=True)

        for preset in saved_presets:
            self.add_dynamic_row(
                existing=preset, 
                is_reset=preset.get("is_reset", False)
            )

        if len(self.preset_rows) < 2:
            self.add_dynamic_row()


# ======================================================================
# GLOBAL TIMING CONFIGURATION WINDOW
# ======================================================================
class GlobalTimingWindow(tk.Toplevel):
    """
    Window for configuring input timing profiles.
    """

    def __init__(self, parent, callback_save: Callable):
        super().__init__(parent)
        self.title("Timing Adjustments (Anti-Detection)")
        self.geometry("420x420")
        self.callback = callback_save

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        timing_frame = tk.Frame(notebook)
        notebook.add(timing_frame, text="Timing")

        # Profile selection
        profiles_frame = tk.LabelFrame(timing_frame, text="Behavior Profiles")
        profiles_frame.pack(fill="x", padx=10, pady=5)

        self.var_profile = tk.StringVar(
            value=GLOBAL_TIMING.get("profile", "aggressive")
        )

        tk.Radiobutton(
            profiles_frame,
            text="😈 Aggressive (fast, robotic)",
            variable=self.var_profile,
            value="aggressive",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🙂 Casual (more relaxed)",
            variable=self.var_profile,
            value="casual",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="😎 Relaxed (well-spaced)",
            variable=self.var_profile,
            value="relaxed",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🤖 BOT (experimental, near-zero delay)",
            variable=self.var_profile,
            value="bot",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🛠 Custom (define values below)",
            variable=self.var_profile,
            value="custom",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=(2, 5))

        # Custom settings
        self.custom_frame = tk.LabelFrame(
            timing_frame, 
            text="Custom Settings (this profile only)"
        )
        self.custom_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(self.custom_frame, text="Press Min (ms):").grid(
            row=0, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_press_min = tk.Entry(self.custom_frame, width=8)
        self.entry_press_min.grid(row=0, column=1, padx=5, pady=2)
        self.entry_press_min.insert(
            0, str(GLOBAL_TIMING.get("press_min_ms", 60))
        )

        tk.Label(self.custom_frame, text="Press Max (ms):").grid(
            row=0, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_press_max = tk.Entry(self.custom_frame, width=8)
        self.entry_press_max.grid(row=0, column=3, padx=5, pady=2)
        self.entry_press_max.insert(
            0, str(GLOBAL_TIMING.get("press_max_ms", 80))
        )

        tk.Label(self.custom_frame, text="Interval Min (ms):").grid(
            row=1, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_interval_min = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_min.grid(row=1, column=1, padx=5, pady=2)
        self.entry_interval_min.insert(
            0, str(GLOBAL_TIMING.get("interval_min_ms", 60))
        )

        tk.Label(self.custom_frame, text="Interval Max (ms):").grid(
            row=1, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_interval_max = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_max.grid(row=1, column=3, padx=5, pady=2)
        self.entry_interval_max.insert(
            0, str(GLOBAL_TIMING.get("interval_max_ms", 90))
        )

        self.var_random = tk.BooleanVar(
            value=GLOBAL_TIMING.get("random_enabled", False)
        )
        self.check_random = tk.Checkbutton(
            self.custom_frame,
            text="Randomize (humanize)",
            variable=self.var_random,
            command=self._toggle_random
        )
        self.check_random.grid(
            row=2, column=0, columnspan=4, sticky="w", padx=5, pady=(5, 2)
        )

        tk.Label(self.custom_frame, text="Range (+/- ms):").grid(
            row=3, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_random_range = tk.Entry(self.custom_frame, width=8)
        self.entry_random_range.grid(row=3, column=1, padx=5, pady=2)
        self.entry_random_range.insert(
            0, str(GLOBAL_TIMING.get("random_range_ms", 10))
        )

        for i in range(4):
            self.custom_frame.columnconfigure(i, weight=1)

        # Save button
        tk.Button(
            self,
            text="💾 SAVE",
            command=self.save_all,
            bg="#90ee90",
            height=2
        ).pack(fill="x", padx=10, pady=10)

        self._on_profile_change()

    def _on_profile_change(self):
        """Handle profile selection change."""
        profile = self.var_profile.get()
        state = "normal" if profile == "custom" else "disabled"
        
        for widget in [
            self.entry_press_min, 
            self.entry_press_max,
            self.entry_interval_min, 
            self.entry_interval_max,
            self.check_random, 
            self.entry_random_range
        ]:
            widget.config(state=state)

    def _toggle_random(self):
        """Handle randomization toggle."""
        state = (
            "normal" 
            if self.var_random.get() and self.var_profile.get() == "custom" 
            else "disabled"
        )
        self.entry_random_range.config(state=state)

    def save_all(self):
        """Save timing configuration."""
        profile = self.var_profile.get()
        GLOBAL_TIMING["profile"] = profile

        if profile == "custom":
            try:
                GLOBAL_TIMING["press_min_ms"] = int(
                    self.entry_press_min.get()
                )
                GLOBAL_TIMING["press_max_ms"] = int(
                    self.entry_press_max.get()
                )
                GLOBAL_TIMING["interval_min_ms"] = int(
                    self.entry_interval_min.get()
                )
                GLOBAL_TIMING["interval_max_ms"] = int(
                    self.entry_interval_max.get()
                )
                GLOBAL_TIMING["random_enabled"] = self.var_random.get()
                GLOBAL_TIMING["random_range_ms"] = int(
                    self.entry_random_range.get()
                )
            except ValueError:
                messagebox.showerror(
                    "Error", 
                    "Please use numbers only in Custom mode."
                )
                return

        self.callback(GLOBAL_TIMING)
        self.destroy()


# ======================================================================
# MAIN APPLICATION CLASS
# ======================================================================
class iRacingControlApp:
    """
    Main application for iRacing control management.
    
    Features:
    - Dynamic driver control adjustment
    - Multi-device input support
    - HUD overlay with telemetry
    - Per-car/track preset management
    - Macro/combo system
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("820x900")

        # Thread-safe UI queue
        self._uiq: "queue.Queue[Tuple[Callable, tuple, dict]]" = queue.Queue()
        self.root.after(30, self._drain_ui_queue)

        # iRacing SDK instance
        self.ir = irsdk.IRSDK()
        self.ir_lock = threading.Lock()

        # Application state
        self.app_state = "RUNNING"  # "RUNNING" or "CONFIG"
        self.controllers: Dict[str, GenericController] = {}
        self.tabs: Dict[str, ControlTab] = {}
        self.combo_tab: Optional[ComboTab] = None
        self.overlay_tab: Optional[OverlayConfigTab] = None
        self.voice_window: Optional[tk.Toplevel] = None

        # Presets: saved_presets[car][track] = config
        self.saved_presets: Dict[str, Dict[str, Dict[str, Any]]] = {}
        
        # Overlay config per car
        self.car_overlay_config: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_feedback: Dict[str, Dict[str, float]] = {}

        self._overlay_feedback_state = {
            "last_time": time.time(),
            "abs_active": 0.0,
            "tc_active": 0.0,
            "spin_active": 0.0,
            "lock_active": 0.0,
            "last_alert": "",
            "last_alert_time": 0.0
        }

        # Active variables for current car
        self.active_vars: List[Tuple[str, bool]] = []

        # Current car and track
        self.current_car = ""
        self.current_track = ""
        self.last_session_type = ""
        self.scans_since_restart = 0
        self.pending_scan_on_start = False
        self.skip_race_restart_once = False
        self._last_auto_pair: Tuple[str, str] = ("", "")

        # Auto-load tracking
        self.auto_load_attempted: set = set()

        # HUD overlay
        self.overlay = OverlayWindow(root)
        self.overlay.withdraw()
        self.overlay_visible = True

        # Settings
        self.use_keyboard_only = tk.BooleanVar(value=False)
        self.use_tts = tk.BooleanVar(value=False)
        self.use_voice = tk.BooleanVar(value=False)
        self.voice_engine = tk.StringVar(value="speech")
        self.vosk_model_path = tk.StringVar(value="")
        self.microphone_device = tk.IntVar(value=-1)
        self.audio_output_device = tk.IntVar(value=-1)
        self.vosk_status_var = tk.StringVar(value="")
        self.voice_engine_combo: Optional[ttk.Combobox] = None
        self.btn_vosk_model: Optional[tk.Button] = None
        self.mic_combo: Optional[ttk.Combobox] = None
        self.audio_output_combo: Optional[ttk.Combobox] = None
        self.voice_ambient_duration = tk.DoubleVar(
            value=VOICE_TUNING_DEFAULTS["ambient_duration"]
        )
        self.voice_initial_timeout = tk.DoubleVar(
            value=VOICE_TUNING_DEFAULTS["initial_timeout"]
        )
        self.voice_continuous_timeout = tk.DoubleVar(
            value=VOICE_TUNING_DEFAULTS["continuous_timeout"]
        )
        self.voice_phrase_time_limit = tk.DoubleVar(
            value=VOICE_TUNING_DEFAULTS["phrase_time_limit"]
        )
        self.voice_energy_threshold = tk.StringVar(value="")
        self.voice_dynamic_energy = tk.BooleanVar(
            value=VOICE_TUNING_DEFAULTS["dynamic_energy"]
        )
        self.auto_detect = tk.BooleanVar(value=True)
        self.auto_restart_on_rescan = tk.BooleanVar(value=True)
        self.auto_restart_on_race = tk.BooleanVar(value=True)
        self.voice_phrase_map: Dict[str, Callable] = {}
        self._voice_traces_attached = False

        # Load configuration
        self.load_config()

        # Create UI
        self._create_menu()
        self._create_main_ui()
        self._update_voice_controls()

        # Initialize devices
        self.update_safe_mode()

        # Start background loops
        self.root.after(2000, self.auto_preset_loop)
        self.update_overlay_loop()

        # Show overlay if it was visible
        if self.overlay_visible:
            self.overlay.deiconify()

        # Activate input manager
        input_manager.active = (self.app_state == "RUNNING")

        # Honor any pending scan requests (set before a restart)
        self.root.after(200, self._perform_pending_scan)

    def _voice_tuning_config(self) -> Dict[str, Any]:
        """Return sanitized voice tuning configuration from the UI."""

        def _safe_float(var: Any, default: float) -> float:
            try:
                return float(var.get())
            except Exception:
                return default

        energy_raw = self.voice_energy_threshold.get().strip()
        try:
            energy_val = float(energy_raw) if energy_raw else None
        except Exception:
            energy_val = None

        return {
            "ambient_duration": max(
                0.0,
                _safe_float(
                    self.voice_ambient_duration,
                    VOICE_TUNING_DEFAULTS["ambient_duration"]
                )
            ),
            "initial_timeout": max(
                0.0,
                _safe_float(
                    self.voice_initial_timeout,
                    VOICE_TUNING_DEFAULTS["initial_timeout"]
                )
            ),
            "continuous_timeout": max(
                0.0,
                _safe_float(
                    self.voice_continuous_timeout,
                    VOICE_TUNING_DEFAULTS["continuous_timeout"]
                )
            ),
            "phrase_time_limit": max(
                0.0,
                _safe_float(
                    self.voice_phrase_time_limit,
                    VOICE_TUNING_DEFAULTS["phrase_time_limit"]
                )
            ),
            "energy_threshold": energy_val,
            "dynamic_energy": self.voice_dynamic_energy.get()
        }

    def apply_voice_tuning(self, persist: bool = False):
        """Send current tuning settings to the listener and optionally save."""

        tuning = self._voice_tuning_config()
        voice_listener.update_tuning(tuning)
        if persist:
            self.schedule_save()

    def on_voice_tuning_changed(self, *_):
        """Propagate UI changes to the listener and persist them."""

        self.apply_voice_tuning(persist=True)

    def ui(self, fn: Callable, *args, **kwargs):
        """Thread-safe UI dispatcher."""
        self._uiq.put((fn, args, kwargs))

    def _drain_ui_queue(self):
        while True:
            try:
                fn, args, kwargs = self._uiq.get_nowait()
            except queue.Empty:
                break

            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f"[UI] Handler error: {exc}")

        self.root.after(30, self._drain_ui_queue)

    def _create_menu(self):
        """Create application menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        options_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Options", menu=options_menu)

        options_menu.add_command(
            label="Timing Adjustments",
            command=self.open_timing_window
        )
        options_menu.add_command(
            label="Voice/Audio Settings",
            command=self.open_voice_audio_settings
        )
        options_menu.add_separator()
        options_menu.add_command(
            label="Show/Hide Overlay",
            command=self.toggle_overlay
        )
        options_menu.add_command(
            label="Restart Application",
            command=restart_program
        )

        options_menu.add_separator()
        options_menu.add_command(
            label="Restore Defaults (Delete Config)",
            command=self.restore_defaults
        )

    def _create_main_ui(self):
        """Create main user interface."""
        # Mode toggle button
        mode_frame = tk.Frame(self.root, pady=5)
        mode_frame.pack(fill="x", padx=10)

        self.btn_mode = tk.Button(
            mode_frame,
            text="Mode: RUNNING",
            bg="#90ee90",
            command=self.toggle_mode,
            font=("Arial", 10, "bold"),
            height=2
        )
        self.btn_mode.pack(fill="x")

        helper_frame = tk.LabelFrame(
            self.root,
            text="Getting started"
        )
        helper_frame.pack(fill="x", padx=10, pady=(0, 6))

        helper_text = (
            "Follow the steps below in order: 1) pick your car and track, "
            "2) confirm your input devices, then 3) scan driver controls. "
            "Use CONFIG mode when changing bindings and RUNNING mode when driving."
        )
        tk.Label(
            helper_frame,
            text=helper_text,
            wraplength=760,
            justify="left"
        ).pack(fill="x", padx=8, pady=4)

        # Settings row
        settings_frame = tk.Frame(self.root)
        settings_frame.pack(fill="x", padx=10, pady=5)

        self.check_safe = tk.Checkbutton(
            settings_frame,
            text="Keyboard Only Mode (requires restart)",
            variable=self.use_keyboard_only,
            command=self.trigger_safe_mode_update
        )
        self.check_safe.pack(side="left")

        tk.Label(
            settings_frame,
            text="(No joystick/wheel buttons)",
            fg="gray",
            font=("Arial", 8)
        ).pack(side="left", padx=4)

        tk.Button(
            settings_frame,
            text="Voice/Audio Options",
            command=self.open_voice_audio_settings
        ).pack(side="right")

        # Auto-detect
        auto_frame = tk.Frame(self.root)
        auto_frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Checkbutton(
            auto_frame,
            text="Auto-detect Car/Track via iRacing",
            variable=self.auto_detect
        ).pack(anchor="w")

        stability_frame = tk.LabelFrame(
            self.root,
            text="Stability Options"
        )
        stability_frame.pack(fill="x", padx=10, pady=5)

        tk.Checkbutton(
            stability_frame,
            text="Restart before rescanning controls (after the first scan)",
            variable=self.auto_restart_on_rescan,
            command=self.schedule_save
        ).pack(anchor="w", pady=2)

        tk.Checkbutton(
            stability_frame,
            text="Auto-restart and scan when joining a Race session",
            variable=self.auto_restart_on_race,
            command=self.schedule_save
        ).pack(anchor="w", pady=2)

        # Car/Track manager
        presets_frame = tk.LabelFrame(
            self.root,
            text="Step 1: Choose your car and track"
        )
        presets_frame.pack(fill="x", padx=10, pady=5)

        selector_frame = tk.Frame(presets_frame)
        selector_frame.pack(fill="x", padx=5, pady=2)

        tk.Label(selector_frame, text="Car:").pack(side="left")
        self.combo_car = ttk.Combobox(selector_frame, width=30)
        self.combo_car.pack(side="left", padx=5)
        self.combo_car.bind("<<ComboboxSelected>>", self.on_car_selected)

        tk.Label(selector_frame, text="Track:").pack(side="left")
        self.combo_track = ttk.Combobox(selector_frame, width=30)
        self.combo_track.pack(side="left", padx=5)

        actions_frame = tk.Frame(presets_frame)
        actions_frame.pack(fill="x", padx=5, pady=5)

        tk.Button(
            actions_frame,
            text="Load",
            command=self.action_load_preset,
            bg="#e0e0e0"
        ).pack(side="left", expand=True, fill="x", padx=2)

        tk.Button(
            actions_frame,
            text="Save Current",
            command=self.action_save_preset,
            bg="#ADD8E6"
        ).pack(side="left", expand=True, fill="x", padx=2)

        tk.Button(
            actions_frame,
            text="Delete",
            command=self.action_delete_preset,
            bg="#ffcccc"
        ).pack(side="left", expand=True, fill="x", padx=2)

        # Device management
        devices_frame = tk.LabelFrame(
            self.root,
            text="Step 2: Confirm input devices (joystick/wheel)"
        )
        devices_frame.pack(fill="x", padx=10, pady=5)

        tk.Button(
            devices_frame,
            text="🎮 Manage Devices",
            command=self.open_device_manager,
            bg="#e0e0e0"
        ).pack(fill="x", padx=5, pady=5)

        # Scan button
        self.btn_scan = tk.Button(
            self.root,
            text="Step 3: Scan driver controls for the selected car",
            command=self.scan_driver_controls,
            bg="lightblue"
        )
        self.btn_scan.pack(fill="x", padx=10, pady=5)

        tk.Label(
            self.root,
            text="Tip: Scan after changing devices or presets to keep bindings in sync.",
            fg="gray",
            font=("Arial", 9)
        ).pack(fill="x", padx=12, pady=(0, 4))

        # Main notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Initialize with default variables if none exist
        if not self.active_vars:
            self.active_vars = [("dcBrakeBias", True)]

        self.rebuild_tabs(self.active_vars)
        self.update_preset_ui()

    # ------------------------------------------------------------------
    # Options UI
    # ------------------------------------------------------------------
    def _list_microphones(self) -> List[Tuple[int, str]]:
        devices: List[Tuple[int, str]] = [(-1, "System default")]
        if not HAS_SPEECH:
            return devices

        try:
            mic_names = sr.Microphone.list_microphone_names() or []
            for idx, name in enumerate(mic_names):
                devices.append((idx, name))
        except Exception as exc:  # noqa: PERF203
            print(f"[Voice] Unable to list microphones: {exc}")

        return devices

    def _list_output_devices(self) -> List[Tuple[int, str]]:
        devices: List[Tuple[int, str]] = [(-1, "System default")]
        if not HAS_PYAUDIO:
            return devices

        try:
            pa = pyaudio.PyAudio()
            try:
                for idx in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(idx)
                    if info.get("maxOutputChannels", 0) > 0:
                        name = info.get("name", f"Output {idx}")
                        devices.append((idx, name))
            finally:
                pa.terminate()
        except Exception as exc:  # noqa: PERF203
            print(f"[Audio] Unable to list output devices: {exc}")

        return devices

    @staticmethod
    def _device_label(idx: int, name: str) -> str:
        return f"[{idx}] {name}"

    @staticmethod
    def _parse_device_index(label: str) -> int:
        try:
            start = label.find("[")
            end = label.find("]")
            return int(label[start + 1:end]) if start >= 0 and end > start else -1
        except Exception:
            return -1

    def _apply_audio_preferences(self):
        """Send selected devices to voice listener and TTS engine."""

        mic_index = self.microphone_device.get()
        voice_listener.set_device_index(mic_index if mic_index >= 0 else None)

        output_index = self.audio_output_device.get()
        global TTS_OUTPUT_DEVICE_INDEX
        TTS_OUTPUT_DEVICE_INDEX = output_index if output_index >= 0 else None

    def _refresh_audio_device_lists(self):
        mic_devices = self._list_microphones()
        if self.microphone_device.get() not in [i for i, _ in mic_devices]:
            self.microphone_device.set(-1)
        mic_labels = [self._device_label(idx, name) for idx, name in mic_devices]
        if self.mic_combo:
            self.mic_combo["values"] = mic_labels
            current_label = self._device_label(
                self.microphone_device.get() if self.microphone_device.get() in [i for i, _ in mic_devices] else -1,
                dict(mic_devices).get(self.microphone_device.get(), "System default")
            )
            self.mic_combo.set(current_label)

        output_devices = self._list_output_devices()
        if self.audio_output_device.get() not in [i for i, _ in output_devices]:
            self.audio_output_device.set(-1)
        output_labels = [self._device_label(idx, name) for idx, name in output_devices]
        if self.audio_output_combo:
            self.audio_output_combo["values"] = output_labels
            current_output_label = self._device_label(
                self.audio_output_device.get() if self.audio_output_device.get() in [i for i, _ in output_devices] else -1,
                dict(output_devices).get(self.audio_output_device.get(), "System default")
            )
            self.audio_output_combo.set(current_output_label)

    def _on_microphone_selected(self, *_):
        selection = self._parse_device_index(self.mic_combo.get()) if self.mic_combo else -1
        self.microphone_device.set(selection)
        self._apply_audio_preferences()
        self.schedule_save()

    def _on_output_selected(self, *_):
        selection = self._parse_device_index(self.audio_output_combo.get()) if self.audio_output_combo else -1
        self.audio_output_device.set(selection)
        self._apply_audio_preferences()
        self.schedule_save()

    def open_voice_audio_settings(self):
        """Open the options window focused on voice and audio settings."""

        if getattr(self, "voice_window", None) is not None and self.voice_window.winfo_exists():
            self.voice_window.lift()
            return

        self.voice_window = tk.Toplevel(self.root)
        self.voice_window.title("Voice and Audio Options")
        self.voice_window.geometry("720x520")

        def _cleanup():
            if self.voice_window and self.voice_window.winfo_exists():
                self.voice_window.destroy()
            self.voice_window = None
            self.voice_engine_combo = None
            self.btn_vosk_model = None
            self.mic_combo = None
            self.audio_output_combo = None

        self.voice_window.protocol("WM_DELETE_WINDOW", _cleanup)

        notebook = ttk.Notebook(self.voice_window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        voice_tab = ttk.Frame(notebook)
        notebook.add(voice_tab, text="Voice/Audio")
        self._build_voice_audio_tab(voice_tab)

        notebook.select(voice_tab)

    def _build_voice_audio_tab(self, parent: tk.Widget):
        """Construct the tab containing voice and audio controls."""

        toggles_frame = tk.Frame(parent)
        toggles_frame.pack(fill="x", pady=4)

        if HAS_TTS:
            tk.Checkbutton(
                toggles_frame,
                text="Voice (TTS)",
                variable=self.use_tts,
                command=self.schedule_save
            ).pack(side="left", padx=4)

        tk.Checkbutton(
            toggles_frame,
            text="Voice Triggers",
            variable=self.use_voice,
            state=("normal" if HAS_SPEECH else "disabled"),
            command=self.on_voice_toggle
        ).pack(side="left", padx=4)

        tk.Button(
            toggles_frame,
            text="Test Voice",
            command=self.open_voice_test_dialog,
            state=("normal" if HAS_SPEECH else "disabled")
        ).pack(side="left", padx=4)

        if not HAS_SPEECH:
            tk.Label(
                toggles_frame,
                text="(Install 'speech_recognition' for voice)",
                fg="gray",
                font=("Arial", 8)
            ).pack(side="left", padx=4)

        engine_frame = tk.LabelFrame(parent, text="Recognition Engine")
        engine_frame.pack(fill="x", padx=2, pady=6)

        ttk.Label(engine_frame, text="Voice Engine:").pack(side="left", padx=4)
        engine_options = ["speech"] + (["vosk"] if HAS_VOSK else [])
        self.voice_engine_combo = ttk.Combobox(
            engine_frame,
            values=engine_options,
            state="readonly",
            width=12
        )
        default_engine = self.voice_engine.get()
        if default_engine not in engine_options:
            default_engine = "speech"
            self.voice_engine.set(default_engine)
        self.voice_engine_combo.set(default_engine)
        self.voice_engine_combo.bind(
            "<<ComboboxSelected>>",
            lambda _evt: self.on_voice_engine_changed()
        )
        self.voice_engine_combo.pack(side="left", padx=4)

        self.btn_vosk_model = tk.Button(
            engine_frame,
            text="Select Vosk Model...",
            command=self.choose_vosk_model
        )
        self.btn_vosk_model.pack(side="left", padx=4)

        tk.Label(
            engine_frame,
            textvariable=self.vosk_status_var,
            fg="gray"
        ).pack(side="left", padx=6)

        device_frame = tk.LabelFrame(parent, text="Input/Output Devices")
        device_frame.pack(fill="x", padx=2, pady=6)

        mic_row = tk.Frame(device_frame)
        mic_row.pack(fill="x", padx=6, pady=2)

        ttk.Label(mic_row, text="Microphone:").pack(side="left")
        self.mic_combo = ttk.Combobox(mic_row, state="readonly", width=50)
        self.mic_combo.pack(side="left", padx=4, fill="x", expand=True)
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_microphone_selected)

        out_row = tk.Frame(device_frame)
        out_row.pack(fill="x", padx=6, pady=2)

        ttk.Label(out_row, text="Audio Output (TTS):").pack(side="left")
        self.audio_output_combo = ttk.Combobox(out_row, state="readonly", width=50)
        self.audio_output_combo.pack(side="left", padx=4, fill="x", expand=True)
        self.audio_output_combo.bind("<<ComboboxSelected>>", self._on_output_selected)

        tk.Button(
            device_frame,
            text="Refresh devices",
            command=self._refresh_audio_device_lists
        ).pack(anchor="e", padx=6, pady=4)

        tuning_frame = tk.LabelFrame(
            parent,
            text="Voice Tuning (accuracy and speed)"
        )
        tuning_frame.pack(fill="x", padx=2, pady=(6, 4))

        tuning_row_1 = tk.Frame(tuning_frame)
        tuning_row_1.pack(fill="x", padx=6, pady=2)

        ttk.Label(tuning_row_1, text="Ambient noise (s):").pack(side="left")
        ttk.Spinbox(
            tuning_row_1,
            from_=0.0,
            to=3.0,
            increment=0.1,
            width=6,
            textvariable=self.voice_ambient_duration
        ).pack(side="left", padx=4)

        ttk.Label(tuning_row_1, text="Max phrase duration (s):").pack(side="left")
        ttk.Spinbox(
            tuning_row_1,
            from_=0.2,
            to=6.0,
            increment=0.1,
            width=6,
            textvariable=self.voice_phrase_time_limit
        ).pack(side="left", padx=4)

        tk.Checkbutton(
            tuning_row_1,
            text="Dynamic energy (auto)",
            variable=self.voice_dynamic_energy
        ).pack(side="left", padx=8)

        tuning_row_2 = tk.Frame(tuning_frame)
        tuning_row_2.pack(fill="x", padx=6, pady=2)

        ttk.Label(tuning_row_2, text="Initial timeout (s):").pack(side="left")
        ttk.Spinbox(
            tuning_row_2,
            from_=0.0,
            to=5.0,
            increment=0.1,
            width=6,
            textvariable=self.voice_initial_timeout
        ).pack(side="left", padx=4)

        ttk.Label(tuning_row_2, text="Continuous timeout (s):").pack(side="left")
        ttk.Spinbox(
            tuning_row_2,
            from_=0.0,
            to=5.0,
            increment=0.1,
            width=6,
            textvariable=self.voice_continuous_timeout
        ).pack(side="left", padx=4)

        ttk.Label(tuning_row_2, text="Minimum energy: ").pack(side="left")
        ttk.Entry(
            tuning_row_2,
            width=8,
            textvariable=self.voice_energy_threshold
        ).pack(side="left", padx=4)
        tk.Label(
            tuning_row_2,
            text="(blank = automatic)",
            fg="gray",
            font=("Arial", 8)
        ).pack(side="left", padx=2)

        if not self._voice_traces_attached:
            for var in (
                self.voice_ambient_duration,
                self.voice_phrase_time_limit,
                self.voice_initial_timeout,
                self.voice_continuous_timeout,
                self.voice_energy_threshold,
                self.voice_dynamic_energy
            ):
                var.trace_add("write", self.on_voice_tuning_changed)

            self._voice_traces_attached = True

        self._refresh_audio_device_lists()
        self._update_voice_controls()

    def toggle_mode(self):
        """Toggle between RUNNING and CONFIG modes."""
        if self.app_state == "RUNNING":
            # Switch to CONFIG
            self.app_state = "CONFIG"
            self.btn_mode.config(
                text="Mode: CONFIG (Click to Save & Run)",
                bg="orange"
            )
            input_manager.active = False
            self._clear_keyboard_hotkeys()
            voice_listener.set_enabled(False)
        else:
            # Switch to RUNNING
            self.app_state = "RUNNING"
            self.btn_mode.config(text="Mode: RUNNING", bg="#90ee90")
            input_manager.active = True
            self.register_current_listeners()

        # Update tab editing states
        editing = (self.app_state == "CONFIG")
        for tab in self.tabs.values():
            tab.set_editing_state(editing)
        if self.combo_tab:
            self.combo_tab.set_editing_state(editing)

    def focus_window(self):
        """Force focus to main window."""
        self.root.focus_force()

    # Safe mode and device management
    def update_safe_mode(self):
        """Update safe mode settings."""
        input_manager.set_safe_mode(self.use_keyboard_only.get())
        if not self.use_keyboard_only.get():
            input_manager.connect_allowed_devices(input_manager.allowed_devices)

    def trigger_safe_mode_update(self):
        """Trigger safe mode update with restart."""
        new_value = self.use_keyboard_only.get()
        if messagebox.askokcancel(
            "Restart Required",
            "Restart is required to apply Keyboard Only mode. Confirm?"
        ):
            self.save_config()
            restart_program()
        else:
            self.use_keyboard_only.set(not new_value)
            self.save_config()
        self.update_safe_mode()

    def open_device_manager(self):
        """Open device management dialog."""
        if self.use_keyboard_only.get():
            messagebox.showinfo(
                "Keyboard Mode",
                "Disable 'Keyboard Only Mode' to manage joystick devices."
            )
            return

        DeviceSelector(
            self.root, 
            input_manager.allowed_devices, 
            self.update_allowed_devices
        )

    def update_allowed_devices(self, new_list: List[str]):
        """Update list of allowed devices."""
        input_manager.allowed_devices = list(new_list)
        input_manager.connect_allowed_devices(input_manager.allowed_devices)
        self.save_config()

    # Car/Track/Preset management
    def update_preset_ui(self):
        """Update car/track combo boxes."""
        cars = sorted(list(self.saved_presets.keys()))
        self.combo_car["values"] = [c for c in cars if c]

        if self.current_car and self.current_car in cars:
            self.combo_car.set(self.current_car)
            self.on_car_selected(None)

    def on_car_selected(self, _event):
        """Handle car selection."""
        car = self.combo_car.get()
        if car in self.saved_presets:
            tracks = sorted([
                t for t in self.saved_presets[car].keys()
                if t not in {"_overlay", "_overlay_feedback"}
            ])
            self.combo_track["values"] = tracks
        else:
            self.combo_track["values"] = []

        self.current_car = car

    def auto_fill_ui(self, car: str, track: str):
        """Auto-fill car and track in UI."""
        self.current_car = car
        self.current_track = track

        self.combo_car.set(car)
        self.on_car_selected(None)
        self.combo_track.set(track)

    def action_save_preset(self):
        """Save current configuration as preset."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()

        if not car or not track:
            messagebox.showwarning("Error", "Define Car and Track.")
            return

        # Collect overlay config
        self.overlay_tab.collect_for_car(car)

        if car not in self.car_overlay_feedback:
            self.car_overlay_feedback[car] = DEFAULT_OVERLAY_FEEDBACK.copy()

        # Collect tab configs
        current_data = {
            "active_vars": self.active_vars,
            "tabs": {},
            "combo": self.combo_tab.get_config() if self.combo_tab else {}
        }

        for var_name, tab in self.tabs.items():
            current_data["tabs"][var_name] = tab.get_config()

        if car not in self.saved_presets:
            self.saved_presets[car] = {}

        self.saved_presets[car][track] = current_data

        # Save overlay config
        if car not in self.car_overlay_config:
            self.car_overlay_config[car] = {}
        self.saved_presets[car]["_overlay"] = self.car_overlay_config[car]
        self.saved_presets[car]["_overlay_feedback"] = \
            self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy())

        self.save_config()
        # Allow auto-detection to load this preset the next time we see the pair
        self.auto_load_attempted.discard((car, track))
        # Immediately refresh listeners when saving the active car/track
        if (car, track) == (self.current_car, self.current_track):
            self.register_current_listeners()
        self.update_preset_ui()
        messagebox.showinfo("Saved", f"Preset saved for {car} @ {track}")

    def load_specific_preset(self, car: str, track: str):
        """Load a specific car/track preset."""
        if car not in self.saved_presets or track not in self.saved_presets[car]:
            return

        data = self.saved_presets[car][track]

        # Load active variables
        active_vars = data.get("active_vars")
        if active_vars:
            self.rebuild_tabs(active_vars)

        # Load tab configs
        tabs_data = data.get("tabs", {})
        for var_name, config in tabs_data.items():
            if var_name in self.tabs:
                self.tabs[var_name].set_config(config)

        # Load combo config
        combo_data = data.get("combo")
        if self.combo_tab and combo_data:
            self.combo_tab.set_config(combo_data)

        # Load overlay config
        overlay_config = self.saved_presets[car].get("_overlay", {})
        self.car_overlay_config[car] = overlay_config
        self.car_overlay_feedback[car] = self.saved_presets[car].get(
            "_overlay_feedback", self.car_overlay_feedback.get(
                car, DEFAULT_OVERLAY_FEEDBACK.copy()
            )
        )
        self.overlay_tab.load_for_car(car, self.active_vars, overlay_config)

        self.register_current_listeners()
        print(f"[Preset] Loaded {car} / {track}")

    def action_load_preset(self):
        """Load selected preset."""
        car = self.combo_car.get()
        track = self.combo_track.get()

        if not car or not track:
            return

        self.current_car = car
        self.current_track = track
        self.load_specific_preset(car, track)

    def action_delete_preset(self):
        """Delete selected preset."""
        car = self.combo_car.get()
        track = self.combo_track.get()

        if not car or not track:
            return

        if car in self.saved_presets and track in self.saved_presets[car]:
            if not messagebox.askyesno(
                "Confirm", 
                f"Delete preset for {car} @ {track}?"
            ):
                return

            del self.saved_presets[car][track]

            # Remove car if no more tracks
            if not [
                t for t in self.saved_presets[car].keys()
                if t not in {"_overlay", "_overlay_feedback"}
            ]:
                del self.saved_presets[car]
                if car in self.car_overlay_config:
                    del self.car_overlay_config[car]
                if car in self.car_overlay_feedback:
                    del self.car_overlay_feedback[car]

            self.save_config()
            self.update_preset_ui()
            self.combo_track.set("")
            self.current_track = ""

    def auto_preset_loop(self):
        """Background loop for auto-detecting car/track."""
        if not (self.auto_detect.get() or self.auto_restart_on_race.get()):
            self.root.after(2000, self.auto_preset_loop)
            return

        try:
            with self.ir_lock:
                if not getattr(self.ir, "is_initialized", False):
                    self.ir.startup()

            if not getattr(self.ir, "is_initialized", False):
                self.root.after(2000, self.auto_preset_loop)
                return

            session_type = self._get_session_type()
            if self._handle_session_change(session_type):
                return

            if not self.auto_detect.get():
                self.root.after(2000, self.auto_preset_loop)
                return

            driver_info = self.ir["DriverInfo"]
            if not driver_info:
                self.root.after(2000, self.auto_preset_loop)
                return

            idx = driver_info["DriverCarIdx"]
            raw_car = driver_info["Drivers"][idx]["CarScreenName"]

            weekend = self.ir["WeekendInfo"]
            if not weekend:
                self.root.after(2000, self.auto_preset_loop)
                return

            raw_track = weekend["TrackDisplayName"]

            # Clean names
            car_clean = "".join(
                c for c in raw_car
                if c.isalnum() or c in " -_"
            )
            track_clean = "".join(
                c for c in raw_track
                if c.isalnum() or c in " -_"
            )

            current_pair = (car_clean, track_clean)

            if current_pair != self._last_auto_pair:
                self._last_auto_pair = current_pair
                self.current_car, self.current_track = car_clean, track_clean
                print(f"[AutoDetect] {car_clean} @ {track_clean}")

                self.auto_fill_ui(car_clean, track_clean)

                # Create skeleton if doesn't exist
                if car_clean not in self.saved_presets:
                    self.saved_presets[car_clean] = {}

                if "_overlay" not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]["_overlay"] = \
                        self.car_overlay_config.get(car_clean, {})

                if "_overlay_feedback" not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]["_overlay_feedback"] = \
                        self.car_overlay_feedback.get(
                            car_clean, DEFAULT_OVERLAY_FEEDBACK.copy()
                        )

                if track_clean not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean][track_clean] = {
                        "active_vars": None,
                        "tabs": {},
                        "combo": {}
                    }

                self.save_config()

                # Auto-load once
                if (car_clean, track_clean) not in self.auto_load_attempted:
                    self.auto_load_attempted.add((car_clean, track_clean))
                    if self.saved_presets[car_clean][track_clean].get(
                        "active_vars"
                    ):
                        self.load_specific_preset(car_clean, track_clean)

        except Exception as e:
            print(f"[AutoDetect] Error: {e}")

        self.root.after(2000, self.auto_preset_loop)

    def _get_session_type(self) -> str:
        """Return the current session type if available."""
        try:
            session_info = self.ir["SessionInfo"]
        except Exception:
            return ""

        session_num = None
        try:
            session_num = int(self.ir["SessionNum"])
        except Exception:
            pass

        try:
            sessions = session_info.get("Sessions") if session_info else None
            if isinstance(sessions, list):
                if session_num is not None and 0 <= session_num < len(sessions):
                    session_type = sessions[session_num].get("SessionType", "")
                    if session_type:
                        return session_type

                for entry in sessions:
                    session_type = entry.get("SessionType", "")
                    if session_type:
                        return session_type
        except Exception:
            pass

        return ""

    def _handle_session_change(self, session_type: str) -> bool:
        """Handle session transitions and restart if entering a race."""
        new_type = session_type or ""

        if new_type != self.last_session_type:
            self.last_session_type = new_type

            if self.skip_race_restart_once and new_type == "Race":
                self.skip_race_restart_once = False
                return False

            if self.auto_restart_on_race.get() and new_type == "Race":
                self.pending_scan_on_start = True
                mark_pending_scan()
                self.save_config()
                restart_program()
                return True

        return False

    def scan_driver_controls(self):
        """Scan for dc* driver control variables in current car."""
        if self.auto_restart_on_rescan.get() and self.scans_since_restart >= 1:
            self.pending_scan_on_start = True
            mark_pending_scan()
            self.save_config()
            restart_program()
            return

        # Preserve any inline (unsaved) bindings so rescans in the same
        # car/track session don't drop macros/hotkeys
        previous_pair = (self.current_car, self.current_track)
        fallback_tabs = {k: v.get_config() for k, v in self.tabs.items()}
        fallback_combo = self.combo_tab.get_config() if self.combo_tab else {}

        with self.ir_lock:
            # Recreate SDK handle to avoid stale sessions between reconnects
            try:
                self.ir.shutdown()
            except Exception:
                pass

            self.ir = irsdk.IRSDK()
            self._refresh_controller_ir()

            # Always try to connect
            if not self.ir.startup():
                messagebox.showerror(
                    "Error",
                    "Open iRacing (or enter a session)."
                )
                return

        found_vars = []

        # Base candidates
        candidates = [
            "dcBrakeBias",
            "dcFuelMixture",
            "dcTractionControl",
            "dcTractionControl2",
            "dcABS",
            "dcAntiRollFront",
            "dcAntiRollRear",
            "dcWeightJackerRight",
            "dcDiffEntry",
            "dcDiffExit"
        ]

        # Try to add all dc* variables from SDK
        try:
            if hasattr(self.ir, "var_headers_dict") and self.ir.var_headers_dict:
                for key in self.ir.var_headers_dict.keys():
                    if key.startswith("dc"):
                        candidates.append(key)
            elif hasattr(self.ir, "var_headers_names"):
                names = getattr(self.ir, "var_headers_names", None)
                if names:
                    for key in names:
                        if key.startswith("dc"):
                            candidates.append(key)
        except Exception:
            pass

        # Remove duplicates and sort
        candidates = sorted(list(set(candidates)))

        if not candidates:
            messagebox.showwarning(
                "Scan",
                "SDK hasn't returned any variables yet.\n"
                "Enter the car (Drive), adjust controls, and try again."
            )
            return

        # Test each candidate
        try:
            for candidate in candidates:
                try:
                    value = self.ir[candidate]
                except Exception:
                    continue

                if value is None:
                    continue

                # Skip non-numeric/bool entries
                if isinstance(value, bool):
                    continue
                if not isinstance(value, numbers.Real):
                    continue

                is_float = (float(value) % 1.0) != 0.0
                found_vars.append((candidate, is_float))

        except Exception as e:
            print(f"[Scan] Error reading variables: {e}")

        if not found_vars:
            messagebox.showwarning(
                "Scan",
                "No numeric 'dc*' variables found.\n"
                "The car may not have driver controls or you're not in Drive mode."
            )
            return

        # Clean and sort
        seen = set()
        clean_vars = []
        for name, is_float in found_vars:
            if name in seen:
                continue
            seen.add(name)
            clean_vars.append((name, is_float))

        clean_vars.sort(key=lambda x: x[0])

        # Update active variables and rebuild tabs
        self.active_vars = clean_vars
        self.rebuild_tabs(self.active_vars)

        # Update preset for current car/track
        car = self.combo_car.get().strip() or self.current_car or "Generic Car"
        track = self.combo_track.get().strip() or \
                self.current_track or "Generic Track"

        self.current_car, self.current_track = car, track
        self.auto_fill_ui(car, track)

        if car not in self.saved_presets:
            self.saved_presets[car] = {}

        if track not in self.saved_presets[car]:
            self.saved_presets[car][track] = {
                "active_vars": self.active_vars,
                "tabs": {},
                "combo": {}
            }
        else:
            self.saved_presets[car][track]["active_vars"] = self.active_vars

        # Overlay config
        if "_overlay" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay"] = \
                self.car_overlay_config.get(car, {})

        if "_overlay_feedback" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay_feedback"] = \
                self.car_overlay_feedback.get(
                    car, DEFAULT_OVERLAY_FEEDBACK.copy()
                )

        self.car_overlay_config[car] = self.saved_presets[car]["_overlay"]
        self.car_overlay_feedback[car] = self.saved_presets[car]["_overlay_feedback"]
        self.overlay_tab.load_for_car(
            car,
            self.active_vars,
            self.car_overlay_config[car]
        )

        # Reload saved bindings/macros for this car/track so they remain active
        preset_data = self.saved_presets[car][track]
        if preset_data.get("tabs") or preset_data.get("combo"):
            # Load preset will rebuild tabs with configs and re-register listeners
            self.load_specific_preset(car, track)
        else:
            # Even without saved presets, ensure any current bindings stay active.
            # If this rescan is for the same car/track, reuse inline config.
            if (car, track) == previous_pair:
                self._apply_inline_config(fallback_tabs, fallback_combo)
            self.register_current_listeners()

        self.update_preset_ui()
        self.save_config()

        self.scans_since_restart += 1

        messagebox.showinfo(
            "Scan",
            f"{len(clean_vars)} 'dc' controls configured for this car."
        )

    def rebuild_tabs(self, vars_list: List[Tuple[str, bool]]):
        """Rebuild control tabs with new variable list."""
        # Clear notebook
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)

        for tab in self.tabs.values():
            try:
                tab.destroy()
            except Exception:
                pass

        self.controllers.clear()
        self.tabs.clear()

        self.active_vars = list(vars_list)

        # Create tabs for each variable
        for var_name, is_float in self.active_vars:
            controller = GenericController(
                self.ir, 
                var_name, 
                is_float, 
                app_ref=self
            )
            self.controllers[var_name] = controller

            frame = tk.Frame(self.notebook)
            tab_widget = ControlTab(
                frame, 
                controller, 
                var_name.replace("dc", ""), 
                self
            )
            tab_widget.pack(fill="both", expand=True)

            self.notebook.add(frame, text=var_name.replace("dc", ""))
            self.tabs[var_name] = tab_widget

        # Combo tab
        combo_frame = tk.Frame(self.notebook)
        self.combo_tab = ComboTab(combo_frame, self.controllers, self)
        self.combo_tab.pack(fill="both", expand=True)
        self.notebook.add(combo_frame, text="⚡ Combos")

        # Overlay config tab
        overlay_frame = tk.Frame(self.notebook)
        self.overlay_tab = OverlayConfigTab(overlay_frame, self)
        self.overlay_tab.pack(fill="both", expand=True)
        self.notebook.add(overlay_frame, text="HUD / Overlay")

        # Load overlay for current car
        car = self.current_car or "Generic Car"

        if car not in self.saved_presets:
            self.saved_presets[car] = {}

        if "_overlay" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay"] = \
                self.car_overlay_config.get(car, {})
        if "_overlay_feedback" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay_feedback"] = \
                self.car_overlay_feedback.get(
                    car, DEFAULT_OVERLAY_FEEDBACK.copy()
                )

        self.car_overlay_config[car] = self.saved_presets[car]["_overlay"]
        self.car_overlay_feedback[car] = self.saved_presets[car]["_overlay_feedback"]
        self.overlay_tab.load_for_car(
            car,
            self.active_vars,
            self.car_overlay_config[car]
        )

        # Set editing state
        editing = (self.app_state == "CONFIG")
        for tab in self.tabs.values():
            tab.set_editing_state(editing)
        if self.combo_tab:
            self.combo_tab.set_editing_state(editing)

        self.register_current_listeners()

    def toggle_overlay(self):
        """Toggle HUD overlay visibility."""
        if self.overlay.winfo_viewable():
            self.overlay.withdraw()
            self.overlay_visible = False
        else:
            self.overlay.deiconify()
            self.overlay_visible = True

    def notify_overlay_status(self, text: str, color: str):
        """Update overlay status text temporarily."""
        self.ui(self.overlay.update_status_text, text, color)
        self.ui(
            self.root.after,
            2000,
            lambda: self.overlay.update_status_text("HUD Ready", "white")
        )

    def update_overlay_loop(self):
        """Background loop to update HUD values."""
        if self.overlay_visible:
            data = {}
            car = self.current_car or "Generic Car"
            config = self.car_overlay_config.get(car, {})

            for var_name, controller in self.controllers.items():
                var_config = config.get(var_name, {})
                if not var_config.get("show", False):
                    continue
                value = controller.read_telemetry()
                data[var_name] = value

            self.overlay.update_monitor_values(data)

        self._update_overlay_feedback()

        self.root.after(100, self.update_overlay_loop)

    def _read_ir_value(self, key: str):
        """Safely read a telemetry key from the iRacing SDK."""

        try:
            if not getattr(self.ir, "is_initialized", False):
                self.ir.startup()
            return self.ir[key]
        except Exception:
            return None

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _bool_from_keys(self, keys: List[str]) -> bool:
        """Return True if any telemetry key resolves to a truthy value."""

        for key in keys:
            value = self._read_ir_value(key)

            if isinstance(value, (list, tuple)):
                if any(bool(v) for v in value):
                    return True
            elif isinstance(value, numbers.Real):
                if float(value) != 0.0:
                    return True
            elif isinstance(value, bool) and value:
                return True

        return False

    def _slip_values(self) -> List[float]:
        """Aggregate slip ratios from available telemetry fields."""

        slips: List[float] = []
        for key in ["WheelSlip", "WheelSlipPct", "WheelSlipRatio", "TireSlip"]:
            value = self._read_ir_value(key)
            if isinstance(value, (list, tuple)):
                slips.extend([self._safe_float(v, 0.0) for v in value])

        return slips

    def _push_overlay_alert(
        self, message: str, color: str, cfg: Dict[str, float], now: float
    ) -> None:
        """Send rate-limited feedback to the overlay status area."""

        state = self._overlay_feedback_state
        cooldown = max(0.5, float(cfg.get("cooldown_s", 6.0)))

        if (
            now - state.get("last_alert_time", 0.0) < cooldown
            and state.get("last_alert") == message
        ):
            return

        self.notify_overlay_status(message, color)
        state["last_alert"] = message
        state["last_alert_time"] = now

    def _update_overlay_feedback(self):
        """Analyze telemetry and surface ABS/TC/wheelspin hints on the HUD."""

        car = self.current_car or "Generic Car"
        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(self.car_overlay_feedback.get(car, {}))

        state = self._overlay_feedback_state
        now = time.time()
        dt = max(0.0, now - state.get("last_time", now))
        state["last_time"] = now

        throttle = self._safe_float(self._read_ir_value("Throttle"), 0.0)
        brake = self._safe_float(self._read_ir_value("Brake"), 0.0)

        abs_active = self._bool_from_keys([
            "BrakeABSactive",
            "BrakeABSActive",
            "BrakeABSActiveLF",
            "BrakeABSActiveRF",
            "BrakeABSActiveLR",
            "BrakeABSActiveRR",
        ])
        tc_active = self._bool_from_keys([
            "TractionControlActive",
            "TractionControlEngaged",
            "TCActive",
            "TractionControlOn",
        ])

        slips = self._slip_values()
        max_slip = max(slips) if slips else 0.0
        min_slip = min(slips) if slips else 0.0

        if abs_active and brake > 0.05:
            state["abs_active"] += dt
        else:
            state["abs_active"] = 0.0

        if tc_active and throttle > 0.2:
            state["tc_active"] += dt
        else:
            state["tc_active"] = 0.0

        if throttle > 0.2 and max_slip >= cfg["wheelspin_slip"]:
            state["spin_active"] += dt
        else:
            state["spin_active"] = 0.0

        lock_threshold = -abs(cfg["lockup_slip"])
        if brake > 0.05 and slips and min_slip <= lock_threshold:
            state["lock_active"] += dt
        else:
            state["lock_active"] = 0.0

        if state["abs_active"] >= cfg["abs_hold_s"]:
            self._push_overlay_alert(
                "ABS active too long: ease off the brake or lower ABS.",
                "orange",
                cfg,
                now
            )
            state["abs_active"] = 0.0

        if state["tc_active"] >= cfg["tc_hold_s"]:
            self._push_overlay_alert(
                "TC constantly triggering: consider lowering TC or changing the map.",
                "orange",
                cfg,
                now
            )
            state["tc_active"] = 0.0

        if state["spin_active"] >= cfg["wheelspin_hold_s"]:
            self._push_overlay_alert(
                "Wheelspin detected: raise TC or modulate the throttle.",
                "orange",
                cfg,
                now
            )
            state["spin_active"] = 0.0

        if state["lock_active"] >= cfg["lockup_hold_s"]:
            self._push_overlay_alert(
                "Lock-up detected: increase ABS or ease pedal pressure.",
                "orange",
                cfg,
                now
            )
            state["lock_active"] = 0.0

    def open_timing_window(self):
        """Open timing configuration window."""
        GlobalTimingWindow(self.root, self.save_timing_config)

    def save_timing_config(self, new_timing: Dict[str, Any]):
        """Save timing configuration."""
        GLOBAL_TIMING.update(_normalize_timing_config(new_timing))
        self.save_config()

    def _perform_pending_scan(self):
        """Execute a deferred scan request set before restarting."""
        if consume_pending_scan():
            self.pending_scan_on_start = True

        if self.pending_scan_on_start:
            self.skip_race_restart_once = True
            self.pending_scan_on_start = False
            self.save_config()
            self.root.after(50, self.scan_driver_controls)

    def schedule_save(self):
        """Schedule configuration save."""
        self.ui(self.save_config)

    def save_config(self):
        """Save configuration to disk."""
        # Collect overlay config
        car = self.current_car or "Generic Car"
        if self.overlay_tab:
            self.overlay_tab.collect_for_car(car)

        data = {
            "global_timing": GLOBAL_TIMING,
            "hud_style": self.overlay.style_cfg,
            "use_keyboard_only": self.use_keyboard_only.get(),
            "use_tts": self.use_tts.get(),
            "use_voice": self.use_voice.get(),
            "voice_engine": self.voice_engine.get(),
            "vosk_model_path": self.vosk_model_path.get(),
            "voice_tuning": self._voice_tuning_config(),
            "microphone_device": self.microphone_device.get(),
            "audio_output_device": self.audio_output_device.get(),
            "auto_detect": self.auto_detect.get(),
            "auto_restart_on_rescan": self.auto_restart_on_rescan.get(),
            "auto_restart_on_race": self.auto_restart_on_race.get(),
            "pending_scan_on_start": self.pending_scan_on_start,
            "allowed_devices": input_manager.allowed_devices,
            "saved_presets": self.saved_presets,
            "car_overlay_config": self.car_overlay_config,
            "car_overlay_feedback": self.car_overlay_feedback,
            "active_vars": self.active_vars,
            "current_car": self.current_car,
            "current_track": self.current_track
        }

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"[SAVE] Error saving config: {e}")

    def load_config(self):
        """Load configuration from disk."""
        global GLOBAL_TIMING
        
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        GLOBAL_TIMING = _normalize_timing_config(
            data.get("global_timing", GLOBAL_TIMING)
        )

        style = data.get("hud_style")
        if style:
            self.overlay.style_cfg.update(style)
            self.overlay.apply_style(self.overlay.style_cfg)

        self.use_keyboard_only.set(data.get("use_keyboard_only", False))
        self.use_tts.set(data.get("use_tts", False))
        self.use_voice.set(data.get("use_voice", False))
        self.voice_engine.set(data.get("voice_engine", "speech"))
        self.vosk_model_path.set(data.get("vosk_model_path", ""))
        self.microphone_device.set(data.get("microphone_device", -1))
        self.audio_output_device.set(data.get("audio_output_device", -1))
        self._set_voice_tuning_vars(
            data.get("voice_tuning", VOICE_TUNING_DEFAULTS)
        )
        self.auto_detect.set(data.get("auto_detect", True))
        self.auto_restart_on_rescan.set(data.get("auto_restart_on_rescan", True))
        self.auto_restart_on_race.set(data.get("auto_restart_on_race", True))
        self.pending_scan_on_start = data.get("pending_scan_on_start", False)

        input_manager.allowed_devices = data.get("allowed_devices", [])

        self.saved_presets = data.get("saved_presets", {})
        self.car_overlay_config = data.get("car_overlay_config", {})
        self.car_overlay_feedback = data.get(
            "car_overlay_feedback", self.car_overlay_feedback
        )
        self.active_vars = data.get("active_vars", [])
        self.current_car = data.get("current_car", "")
        self.current_track = data.get("current_track", "")

    def _set_voice_tuning_vars(self, tuning: Dict[str, Any]):
        """Populate Tk variables with stored voice tuning values."""

        self.voice_ambient_duration.set(
            tuning.get("ambient_duration", VOICE_TUNING_DEFAULTS["ambient_duration"])
        )
        self.voice_initial_timeout.set(
            tuning.get("initial_timeout", VOICE_TUNING_DEFAULTS["initial_timeout"])
        )
        self.voice_continuous_timeout.set(
            tuning.get(
                "continuous_timeout",
                VOICE_TUNING_DEFAULTS["continuous_timeout"]
            )
        )
        self.voice_phrase_time_limit.set(
            tuning.get(
                "phrase_time_limit",
                VOICE_TUNING_DEFAULTS["phrase_time_limit"]
            )
        )

        energy_threshold = tuning.get("energy_threshold")
        self.voice_energy_threshold.set(
            "" if energy_threshold in {None, ""} else str(energy_threshold)
        )
        self.voice_dynamic_energy.set(
            tuning.get("dynamic_energy", VOICE_TUNING_DEFAULTS["dynamic_energy"])
        )

    # ------------------------------------------------------------------
    # Voice helpers
    # ------------------------------------------------------------------
    def _make_single_action(self, controller: GenericController, target: float):
        """Create an action that adjusts a single controller to a target."""
        return lambda: threading.Thread(
            target=controller.adjust_to_target,
            args=(target,),
            daemon=True
        ).start()

    def _make_combo_action(self, values: Dict[str, str]):
        """Create an action that adjusts multiple controllers at once."""

        def combo_action():
            if self.app_state != "RUNNING":
                return

            for var_name, val_str in values.items():
                if var_name in self.controllers and val_str:
                    try:
                        target = float(val_str)
                    except Exception:
                        continue

                    ctrl = self.controllers[var_name]
                    threading.Thread(
                        target=ctrl.adjust_to_target,
                        args=(target,),
                        daemon=True
                    ).start()

        return combo_action

    def _build_voice_phrase_map(self) -> Dict[str, Callable]:
        """Collect current voice phrases mapped to their actions."""
        voice_phrases: Dict[str, Callable] = {}

        for var_name, tab in self.tabs.items():
            config = tab.get_config()
            controller = self.controllers[var_name]

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
                        controller,
                        target
                    )

        if self.combo_tab:
            combo_config = self.combo_tab.get_config()

            for preset in combo_config.get("presets", []):
                values = preset.get("vals", {})
                phrase = preset.get("voice_phrase", "").strip().lower()
                if not phrase:
                    continue

                voice_phrases[phrase] = self._make_combo_action(values)

        return voice_phrases

    def _format_vosk_status(self) -> str:
        """Return a user-friendly status string for Vosk usage."""
        engine = self.voice_engine.get()
        if engine != "vosk":
            return "Using Windows speech recognizer"

        if not HAS_VOSK:
            return "Vosk not installed"

        model_path = self.vosk_model_path.get()
        if not model_path:
            return "Select a Vosk model folder"

        if voice_listener._vosk_error:
            return f"Model error: {voice_listener._vosk_error}"

        if voice_listener.vosk_model is not None:
            name = os.path.basename(model_path.rstrip(os.sep)) or model_path
            return f"Vosk model: {name}"

        return "Loading Vosk model..."

    def on_voice_engine_changed(self):
        """Handle engine dropdown changes."""
        selection = (
            self.voice_engine_combo.get() if self.voice_engine_combo else self.voice_engine.get()
        )
        if selection not in {"speech", "vosk"}:
            selection = "speech"

        if selection == "vosk" and not HAS_VOSK:
            selection = "speech"

        self.voice_engine.set(selection)
        self._update_voice_controls()
        self.register_current_listeners()

    def choose_vosk_model(self):
        """Prompt the user to select a Vosk model directory."""
        path = filedialog.askdirectory(title="Select Vosk model folder")
        if not path:
            return

        self.vosk_model_path.set(path)
        self._update_voice_controls()
        self.register_current_listeners()

    def _update_voice_controls(self):
        """Refresh UI state and listener config for voice engine selection."""
        voice_listener.update_tuning(self._voice_tuning_config())
        self._apply_audio_preferences()
        engine = self.voice_engine.get()
        if engine == "vosk" and not HAS_VOSK:
            engine = "speech"
            self.voice_engine.set(engine)

            if self.voice_engine_combo:
                self.voice_engine_combo.set(engine)

        if engine == "vosk":
            voice_listener.set_engine(engine, self.vosk_model_path.get())
        else:
            voice_listener.set_engine("speech", "")

        btn_state = "normal" if engine == "vosk" and HAS_VOSK else "disabled"
        if self.btn_vosk_model:
            self.btn_vosk_model.config(state=btn_state)
        self.vosk_status_var.set(self._format_vosk_status())

    def open_voice_test_dialog(self):
        """Open the dialog that validates configured voice commands."""
        if not HAS_SPEECH:
            messagebox.showinfo(
                "Voice unavailable",
                "Install the 'speech_recognition' package to enable voice control."
            )
            return

        phrases_map = self._build_voice_phrase_map()
        self.voice_phrase_map = phrases_map

        if not phrases_map:
            messagebox.showinfo(
                "No macros found",
                "Add phrases in the tabs to test voice commands."
            )
            return

        VoiceTestDialog(self.root, self, phrases_map)

    def on_voice_toggle(self):
        """Persist and (re)register voice triggers when toggled."""

        self.register_current_listeners()
        self.schedule_save()

    def register_current_listeners(self):
        """Register keyboard/joystick listeners based on current config."""
        self._clear_keyboard_hotkeys()
        input_manager.listeners.clear()
        voice_phrases: Dict[str, Callable] = {}

        # Register individual tab presets
        for var_name, tab in self.tabs.items():
            config = tab.get_config()
            controller = self.controllers[var_name]

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

        # Register combo presets
        if self.combo_tab:
            combo_config = self.combo_tab.get_config()

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

        input_manager.active = (self.app_state == "RUNNING")
        if self.app_state != "RUNNING":
            voice_listener.set_enabled(False)
        elif self.use_voice.get():
            voice_listener.update_tuning(self._voice_tuning_config())
            voice_listener.set_engine(
                self.voice_engine.get(),
                self.vosk_model_path.get()
            )
            voice_listener.set_phrases(self.voice_phrase_map)
            voice_listener.set_enabled(True)
        else:
            voice_listener.set_enabled(False)

    def _refresh_controller_ir(self):
        """Ensure all controllers use the latest IRSDK handle."""
        for controller in self.controllers.values():
            controller.ir = self.ir

    def _clear_keyboard_hotkeys(self):
        """Remove all keyboard hotkeys registered by the app."""
        if not hasattr(self, "_hotkey_handles"):
            self._hotkey_handles: List[Any] = []

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

    def _apply_inline_config(
        self,
        tab_configs: Dict[str, Dict[str, Any]],
        combo_config: Dict[str, Any]
    ):
        """Reapply unsaved tab/combo configuration after a rescan."""
        for var_name, config in tab_configs.items():
            if var_name in self.tabs:
                try:
                    self.tabs[var_name].set_config(config)
                except Exception:
                    pass

        if self.combo_tab and combo_config:
            try:
                self.combo_tab.set_config(combo_config)
            except Exception:
                pass

    def restore_defaults(self):
        """Delete the configuration file and restart the app after confirmation."""
        if not messagebox.askyesno(
            "Restore Defaults",
            "This will delete your configuration file and restart the app. Continue?"
        ):
            return

        try:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
        except Exception as exc:
            messagebox.showerror(
                "Error",
                f"Failed to delete config: {exc}"
            )
            return

        messagebox.showinfo(
            "Defaults Restored",
            "Configuration reset. The application will restart now."
        )
        restart_program()


# ======================================================================
# APPLICATION ENTRY POINT
# ======================================================================
def main():
    """Main application entry point."""
    try:
        root = tk.Tk()
        iRacingControlApp(root)
        root.mainloop()
    except Exception as e:
        print(f"Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to close...")


if __name__ == "__main__":
    main()
