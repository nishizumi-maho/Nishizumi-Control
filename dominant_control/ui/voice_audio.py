"""UI helpers for the Voice/Audio settings window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import tkinter as tk
from tkinter import ttk

from dominant_control import audio_settings
from dominant_control.dependencies import HAS_SPEECH, HAS_TTS


@dataclass
class VoiceAudioControls:
    """Holds UI controls created for the voice/audio window."""

    window: tk.Toplevel
    voice_engine_combo: Optional[ttk.Combobox] = None
    btn_vosk_model: Optional[tk.Button] = None
    mic_combo: Optional[ttk.Combobox] = None
    audio_output_combo: Optional[ttk.Combobox] = None


def _refresh_audio_device_lists(app, controls: VoiceAudioControls):
    mic_devices = audio_settings.list_microphones()
    if app.microphone_device.get() not in [i for i, _ in mic_devices]:
        app.microphone_device.set(-1)
    mic_labels = [audio_settings.device_label(idx, name) for idx, name in mic_devices]
    if controls.mic_combo:
        controls.mic_combo["values"] = mic_labels
        current_label = audio_settings.device_label(
            app.microphone_device.get()
            if app.microphone_device.get() in [i for i, _ in mic_devices]
            else -1,
            dict(mic_devices).get(app.microphone_device.get(), "System default"),
        )
        controls.mic_combo.set(current_label)

    output_devices = audio_settings.list_output_devices()
    if app.audio_output_device.get() not in [i for i, _ in output_devices]:
        app.audio_output_device.set(-1)
    output_labels = [
        audio_settings.device_label(idx, name) for idx, name in output_devices
    ]
    if controls.audio_output_combo:
        controls.audio_output_combo["values"] = output_labels
        current_output_label = audio_settings.device_label(
            app.audio_output_device.get()
            if app.audio_output_device.get() in [i for i, _ in output_devices]
            else -1,
            dict(output_devices).get(app.audio_output_device.get(), "System default"),
        )
        controls.audio_output_combo.set(current_output_label)


def _on_microphone_selected(app, controls: VoiceAudioControls, *_):
    selection = (
        audio_settings.parse_device_index(controls.mic_combo.get())
        if controls.mic_combo
        else -1
    )
    app.microphone_device.set(selection)
    audio_settings.apply_audio_preferences(selection, app.audio_output_device.get())
    app.schedule_save()


def _on_output_selected(app, controls: VoiceAudioControls, *_):
    selection = (
        audio_settings.parse_device_index(controls.audio_output_combo.get())
        if controls.audio_output_combo
        else -1
    )
    app.audio_output_device.set(selection)
    audio_settings.apply_audio_preferences(app.microphone_device.get(), selection)
    app.schedule_save()


def _build_voice_audio_tab(app, parent: tk.Widget, controls: VoiceAudioControls):
    toggles_frame = tk.Frame(parent)
    toggles_frame.pack(fill="x", pady=4)

    if HAS_TTS:
        tk.Checkbutton(
            toggles_frame,
            text="Voice (TTS)",
            variable=app.use_tts,
            command=app.schedule_save,
        ).pack(side="left", padx=4)

    tk.Checkbutton(
        toggles_frame,
        text="Voice Triggers",
        variable=app.use_voice,
        state=("normal" if HAS_SPEECH else "disabled"),
        command=app.on_voice_toggle,
    ).pack(side="left", padx=4)

    tk.Button(
        toggles_frame,
        text="Test Voice",
        command=app.open_voice_test_dialog,
        state=("normal" if HAS_SPEECH else "disabled"),
    ).pack(side="left", padx=4)

    if not HAS_SPEECH:
        tk.Label(
            toggles_frame,
            text="(Install 'speech_recognition' for voice)",
            fg="gray",
            font=("Arial", 8),
        ).pack(side="left", padx=4)

    engine_frame = tk.LabelFrame(parent, text="Voice Recognition Engine")
    engine_frame.pack(fill="x", pady=6, padx=2)

    ttk.Label(engine_frame, text="Engine:").pack(side="left", padx=4)
    controls.voice_engine_combo = ttk.Combobox(
        engine_frame,
        values=["speech", "vosk"],
        state="readonly",
        width=12,
        textvariable=app.voice_engine,
    )
    controls.voice_engine_combo.pack(side="left", padx=4)
    controls.voice_engine_combo.bind(
        "<<ComboboxSelected>>", lambda *_: app.voice_control.on_voice_engine_changed()
    )

    controls.btn_vosk_model = tk.Button(
        engine_frame,
        text="Choose Vosk Model",
        command=app.voice_control.choose_vosk_model,
    )
    controls.btn_vosk_model.pack(side="left", padx=4)

    ttk.Label(engine_frame, textvariable=app.vosk_status_var).pack(
        side="left", padx=6
    )

    devices_frame = tk.LabelFrame(parent, text="Audio Devices")
    devices_frame.pack(fill="x", padx=2, pady=6)

    mic_frame = tk.Frame(devices_frame)
    mic_frame.pack(fill="x", padx=6, pady=4)
    tk.Label(mic_frame, text="Microphone:").pack(side="left")
    controls.mic_combo = ttk.Combobox(
        mic_frame,
        state="readonly",
        width=50,
    )
    controls.mic_combo.pack(side="left", padx=4, fill="x", expand=True)
    controls.mic_combo.bind(
        "<<ComboboxSelected>>",
        lambda *_: _on_microphone_selected(app, controls),
    )

    output_frame = tk.Frame(devices_frame)
    output_frame.pack(fill="x", padx=6, pady=4)
    tk.Label(output_frame, text="Audio output:").pack(side="left")
    controls.audio_output_combo = ttk.Combobox(
        output_frame,
        state="readonly",
        width=50,
    )
    controls.audio_output_combo.pack(side="left", padx=4, fill="x", expand=True)
    controls.audio_output_combo.bind(
        "<<ComboboxSelected>>",
        lambda *_: _on_output_selected(app, controls),
    )

    tk.Button(
        devices_frame,
        text="Refresh devices",
        command=lambda: _refresh_audio_device_lists(app, controls),
    ).pack(anchor="e", padx=6, pady=4)

    tuning_frame = tk.LabelFrame(
        parent,
        text="Voice Tuning (accuracy and speed)",
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
        textvariable=app.voice_ambient_duration,
    ).pack(side="left", padx=4)

    ttk.Label(tuning_row_1, text="Max phrase duration (s):").pack(side="left")
    ttk.Spinbox(
        tuning_row_1,
        from_=0.2,
        to=6.0,
        increment=0.1,
        width=6,
        textvariable=app.voice_phrase_time_limit,
    ).pack(side="left", padx=4)

    tk.Checkbutton(
        tuning_row_1,
        text="Dynamic energy (auto)",
        variable=app.voice_dynamic_energy,
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
        textvariable=app.voice_initial_timeout,
    ).pack(side="left", padx=4)

    ttk.Label(tuning_row_2, text="Continuous timeout (s):").pack(side="left")
    ttk.Spinbox(
        tuning_row_2,
        from_=0.0,
        to=5.0,
        increment=0.1,
        width=6,
        textvariable=app.voice_continuous_timeout,
    ).pack(side="left", padx=4)

    ttk.Label(tuning_row_2, text="Minimum energy: ").pack(side="left")
    ttk.Entry(
        tuning_row_2,
        width=8,
        textvariable=app.voice_energy_threshold,
    ).pack(side="left", padx=4)
    tk.Label(
        tuning_row_2,
        text="(blank = automatic)",
        fg="gray",
        font=("Arial", 8),
    ).pack(side="left", padx=2)

    if not app._voice_traces_attached:
        for var in (
            app.voice_ambient_duration,
            app.voice_phrase_time_limit,
            app.voice_initial_timeout,
            app.voice_continuous_timeout,
            app.voice_energy_threshold,
            app.voice_dynamic_energy,
        ):
            var.trace_add("write", app.voice_control.on_voice_tuning_changed)

        app._voice_traces_attached = True

    _refresh_audio_device_lists(app, controls)
    app.voice_control.update_voice_controls()


def build_voice_audio_window(app) -> VoiceAudioControls:
    """Create the voice/audio settings window for the given app instance."""

    window = tk.Toplevel(app.root)
    window.title("Voice and Audio Options")
    window.geometry("720x520")

    controls = VoiceAudioControls(window=window)

    def _cleanup():
        if controls.window and controls.window.winfo_exists():
            controls.window.destroy()
        app.voice_window = None
        app.voice_engine_combo = None
        app.btn_vosk_model = None
        app.mic_combo = None
        app.audio_output_combo = None

    window.protocol("WM_DELETE_WINDOW", _cleanup)

    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    voice_tab = ttk.Frame(notebook)
    notebook.add(voice_tab, text="Voice/Audio")
    _build_voice_audio_tab(app, voice_tab, controls)

    notebook.select(voice_tab)

    app.voice_window = window
    app.voice_engine_combo = controls.voice_engine_combo
    app.btn_vosk_model = controls.btn_vosk_model
    app.mic_combo = controls.mic_combo
    app.audio_output_combo = controls.audio_output_combo

    return controls
