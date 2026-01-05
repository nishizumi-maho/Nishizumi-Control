"""Audio device helpers for Dominant Control."""

from __future__ import annotations

from typing import List, Tuple

from dominant_control.config import TTS_OUTPUT_DEVICE_INDEX
from dominant_control.dependencies import HAS_PYAUDIO, HAS_SPEECH, pyaudio, sr
from dominant_control.voice import voice_listener


def list_microphones() -> List[Tuple[int, str]]:
    """Return available microphone devices (index, name)."""

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


def list_output_devices() -> List[Tuple[int, str]]:
    """Return available audio output devices (index, name)."""

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


def device_label(idx: int, name: str) -> str:
    """Format a device label for display in UI controls."""

    return f"[{idx}] {name}"


def parse_device_index(label: str) -> int:
    """Extract the device index from a formatted label."""

    try:
        start = label.find("[")
        end = label.find("]")
        return int(label[start + 1 : end]) if start >= 0 and end > start else -1
    except Exception:
        return -1


def apply_audio_preferences(microphone_index: int, output_index: int) -> None:
    """Send device selections to voice listener and TTS engine."""

    voice_listener.set_device_index(microphone_index if microphone_index >= 0 else None)

    global TTS_OUTPUT_DEVICE_INDEX
    TTS_OUTPUT_DEVICE_INDEX = output_index if output_index >= 0 else None
