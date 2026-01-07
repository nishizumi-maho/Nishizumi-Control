# Dominant Control for iRacing
<img width="256" height="256" alt="dominant_control (1)-5" src="https://github.com/user-attachments/assets/994813f4-dff8-4789-8ba9-3b886bb1e794" />


An accessibility-focused control manager for iRacing that provides:
- Dynamic driver control adjustment (brake bias, traction control, etc.)
- Multi-device input support (keyboard, joystick, wheel buttons)
- HUD overlay with real-time telemetry
- Per-car and per-track preset management
- Macro/combo system for quick adjustments

![Version](https://img.shields.io/badge/version-5.0.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Overview
Dominant Control is a single-file application (`FINALOK.py`) that manages iRacing’s driver-adjustable controls. It includes optional text-to-speech feedback, joystick support, and offline voice control when the related dependencies are installed.

## Release 5.0.0 Highlights
- **Refreshed layout:** new Main/Options tabs with a guided Step 1–3 setup flow for car/track, device confirmation, and control scanning.
- **Voice & audio window:** all voice engines, devices, and tuning controls now live in **Options → Voice/Audio Settings**.
- **Automation & shortcuts:** expanded options for rescan/restart automation plus manual rescan and clear-target hotkeys.
- **Whisper.cpp support:** optional offline recognition via a local whisper.cpp executable and GGML/GGUF models.

## Supported Platforms & Requirements
- **OS:** Windows 10/11 (64-bit)
- **iRacing:** Valid subscription with telemetry enabled
- **Python (source install):** 3.10+
- **Hardware:** 4 GB RAM (8 GB recommended), 100 MB free disk space

See [`requirements.txt`](requirements.txt) for the full Python dependency list.

## Project Layout
- `FINALOK.py` — primary application entry point (single-file app).
- `archive/` — legacy utilities and archived source (including `archive/dominant_control/`).
- `docs/` — supplemental documentation.

## Installation

### Run From Source (Windows)
1. Install Python 3.10+ and Visual C++ Build Tools (for PyAudio) if missing.
2. Clone or download this repository.
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Start the app: `python FINALOK.py`.

## Optional Features
- **Joystick input:** Requires `pygame`.
- **Text-to-speech:** Requires `pyttsx3`.
- **Audio device selection:** Requires `pyaudio`.
- **Voice commands:** Requires `speech_recognition` and `vosk` (plus a downloaded Vosk model).

## Known Issues
1. The application becomes “frozen” while iRacing is loading and will only unfreeze after the game has fully loaded. The application does not stop working; it is simply scanning for large changes and fluctuations in the telemetry during the simulation’s loading process.

## License & Attribution
- Licensed under the **MIT License** (see [`license.md`](license.md)).
- Not affiliated with iRacing.com Motorsport Simulations, LLC.
