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

import json
import numbers
import os
import queue
import random
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import keyboard
import irsdk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from dominant_control.config import (
    APP_FOLDER,
    APP_NAME,
    APP_VERSION,
    CONFIG_FILE,
    CONFIG_FOLDER,
    DEFAULT_OVERLAY_FEEDBACK,
    GLOBAL_TIMING,
    ICON_CANDIDATES,
    PENDING_SCAN_FILE,
    TTS_OUTPUT_DEVICE_INDEX,
    VOICE_TUNING_DEFAULTS,
    apply_app_icon,
    consume_pending_scan,
    mark_pending_scan,
    resolve_resource_path,
    restart_program,
)
from dominant_control.controllers import GenericController
from dominant_control.controllers.device_allowlist import DeviceAllowlistManager
from dominant_control.controllers.lifecycle import LifecycleManager
from dominant_control.dependencies import (
    HAS_PYAUDIO,
    HAS_PYGAME,
    HAS_SPEECH,
    HAS_TTS,
    HAS_VOSK,
    pyaudio,
    pygame,
    sr,
    vosk,
)
from dominant_control.input_engine import (
    IS_WINDOWS,
    _normalize_timing_config,
)
from dominant_control.input_manager import input_manager
from dominant_control.tts import speak_text
from dominant_control.ui.combo_tab import ComboTab
from dominant_control.ui.control_tab import ControlTab
from dominant_control.ui.device_selector import DeviceSelector
from dominant_control.ui.overlay_config import OverlayConfigTab, ScrollableFrame
from dominant_control.ui.overlay_feedback import OverlayFeedbackManager
from dominant_control.ui.overlay_window import OverlayWindow
from dominant_control.ui.timing_window import GlobalTimingWindow
from dominant_control.voice import VoiceTestDialog, voice_listener
from dominant_control.voice_control import VoiceControlManager



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
        apply_app_icon(self.root)

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
        self.show_overlay_feedback = tk.BooleanVar(value=True)

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
        self._voice_traces_attached = False

        # Shared helpers
        self.filedialog = filedialog
        self.messagebox = messagebox

        # Managers
        self.overlay_feedback_manager = OverlayFeedbackManager(
            self.ir, self.notify_overlay_status
        )
        self.device_manager = DeviceAllowlistManager(self)
        self.lifecycle_manager = LifecycleManager(self)
        self.voice_control = VoiceControlManager(self)

        # Load configuration
        self.load_config()

        # Create UI
        self._create_menu()
        self._create_main_ui()
        self.voice_control.update_voice_controls()

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
        return self.voice_control.tuning_config()

    def apply_voice_tuning(self, persist: bool = False):
        self.voice_control.apply_voice_tuning(persist=persist)

    def on_voice_tuning_changed(self, *_):
        self.voice_control.on_voice_tuning_changed()

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

    @staticmethod
    def consume_pending_scan() -> bool:
        return consume_pending_scan()

    @staticmethod
    def restart_program():
        restart_program()

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

    def apply_audio_preferences(self):
        self._apply_audio_preferences()

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
        self.device_manager.update_safe_mode()

    def trigger_safe_mode_update(self):
        self.device_manager.trigger_safe_mode_update()

    def open_device_manager(self):
        self.device_manager.open_device_manager()

    def update_allowed_devices(self, new_list: List[str]):
        self.device_manager.update_allowed_devices(new_list)

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
            if self.lifecycle_manager.handle_session_change(session_type):
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
        return self.lifecycle_manager.handle_session_change(session_type)

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
        return self.overlay_feedback_manager._read_ir_value(key)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        return OverlayFeedbackManager._safe_float(value, default)

    def _bool_from_keys(self, keys: List[str]) -> bool:
        return self.overlay_feedback_manager._bool_from_keys(keys)

    def _slip_values(self) -> List[float]:
        return self.overlay_feedback_manager._slip_values()

    def _push_overlay_alert(
        self, message: str, color: str, cfg: Dict[str, float], now: float
    ) -> None:
        self.overlay_feedback_manager._push_overlay_alert(message, color, cfg, now)

    def _update_overlay_feedback(self):
        self.overlay_feedback_manager.update_feedback(
            self.current_car, self.car_overlay_feedback, self.show_overlay_feedback.get()
        )

    def open_timing_window(self):
        """Open timing configuration window."""
        GlobalTimingWindow(self.root, self.save_timing_config)

    def save_timing_config(self, new_timing: Dict[str, Any]):
        """Save timing configuration."""
        GLOBAL_TIMING.update(_normalize_timing_config(new_timing))
        self.save_config()

    def _perform_pending_scan(self):
        self.lifecycle_manager.perform_pending_scan()

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
            "show_overlay_feedback": self.show_overlay_feedback.get(),
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

        self.show_overlay_feedback.set(data.get("show_overlay_feedback", True))

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
        self.voice_control.set_voice_tuning_vars(tuning)

    # ------------------------------------------------------------------
    # Voice helpers
    # ------------------------------------------------------------------
    def _make_single_action(self, controller: GenericController, target: float):
        return self.voice_control._make_single_action(controller, target)

    def _make_combo_action(self, values: Dict[str, str]):
        return self.voice_control._make_combo_action(values)

    def _build_voice_phrase_map(self) -> Dict[str, Callable]:
        return self.voice_control._build_voice_phrase_map()

    def _format_vosk_status(self) -> str:
        return self.voice_control.format_vosk_status()

    def on_voice_engine_changed(self):
        self.voice_control.on_voice_engine_changed()

    def choose_vosk_model(self):
        self.voice_control.choose_vosk_model()

    def _update_voice_controls(self):
        self.voice_control.update_voice_controls()

    def open_voice_test_dialog(self):
        self.voice_control.open_voice_test_dialog()

    def on_voice_toggle(self):
        self.voice_control.on_voice_toggle()

    def register_current_listeners(self):
        self.voice_control.register_current_listeners()

    def _refresh_controller_ir(self):
        """Ensure all controllers use the latest IRSDK handle."""
        for controller in self.controllers.values():
            controller.ir = self.ir
        self.overlay_feedback_manager.set_ir(self.ir)

    def _clear_keyboard_hotkeys(self):
        self.voice_control.clear_keyboard_hotkeys()

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
        self.lifecycle_manager.restore_defaults()


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
