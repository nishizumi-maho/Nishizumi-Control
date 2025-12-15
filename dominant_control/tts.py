"""Text-to-speech helpers shared across the application."""

import os
import tempfile
import threading
import time
import wave
from typing import Optional

from .config import (
    TTS_OUTPUT_DEVICE_INDEX,
    TTS_STATE,
    _TTS_ENGINE,
    _TTS_LOCK,
    _TTS_QUEUE,
    _TTS_THREAD,
)
from .dependencies import HAS_PYAUDIO, HAS_TTS, pyaudio, pyttsx3


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


__all__ = ["speak_text"]
