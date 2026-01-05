"""Optional dependency discovery for Dominant Control."""

import importlib
import warnings

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
)

try:
    import pygame

    HAS_PYGAME = True
except ImportError:
    pygame = None  # type: ignore[assignment]
    HAS_PYGAME = False
    print("Warning: 'pygame' not installed. Joystick support disabled.")

try:
    import pyttsx3

    HAS_TTS = True
except ImportError:
    pyttsx3 = None  # type: ignore[assignment]
    HAS_TTS = False
    print("Warning: 'pyttsx3' not installed. TTS disabled.")

try:
    import pyaudio

    HAS_PYAUDIO = True
except ImportError:
    pyaudio = None  # type: ignore[assignment]
    HAS_PYAUDIO = False
    print("Warning: 'pyaudio' not installed. Audio device selection limited.")

_speech_spec = importlib.util.find_spec("speech_recognition")
if _speech_spec is not None:
    import speech_recognition as sr

    HAS_SPEECH = True
else:
    sr = None  # type: ignore[assignment]
    HAS_SPEECH = False
    print("Warning: 'speech_recognition' not installed. Voice triggers disabled.")

try:
    import vosk

    HAS_VOSK = True
except ImportError:
    vosk = None  # type: ignore[assignment]
    HAS_VOSK = False

__all__ = [
    "HAS_PYGAME",
    "HAS_PYAUDIO",
    "HAS_SPEECH",
    "HAS_TTS",
    "HAS_VOSK",
    "pyttsx3",
    "pyaudio",
    "pygame",
    "sr",
    "vosk",
]
