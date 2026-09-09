# Dominant Control for iRacing
<img width="256" height="256" alt="dominant_control (1)-5" src="https://github.com/user-attachments/assets/994813f4-dff8-4789-8ba9-3b886bb1e794" />

**Uninstall vJoy to use this app!!**

Download it here: https://github.com/nishizumi-maho/Nishizumi-Control/releases

An accessibility-focused control manager for iRacing that provides:
- Dynamic driver control adjustment (brake bias, traction control, etc.)
- Multi-device input support (keyboard, joystick, wheel buttons)
- HUD overlay with real-time telemetry
- Per-car and per-track preset management
- Remembers control bindings per car when you switch tracks
- Macro/combo system for quick adjustments
- Automatic GitHub release checks (every 6 hours) with in-app update prompts

![Version](https://img.shields.io/github/v/release/nishizumi-maho/Nishizumi-Control?label=version)
![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Overview
Dominant Control manages iRacing’s driver-adjustable controls. Since v13 it is a
package (`dominant_control/`) started by `DominantControl.py`, and the Nishizumi
tools — Fuel Monitor, Tire Wear, Traction, Safety Rating, Pit Calibrator,
Caution and the Fair Share calculator — run inside it, sharing one telemetry
connection. It includes optional text-to-speech feedback, joystick support, and
offline voice control when the related dependencies are installed.

## Supported Platforms & Requirements
- **OS:** Windows 10/11 (64-bit)
- **iRacing:** Valid subscription with telemetry enabled
- **Python (source install):** 3.10+
- **Hardware:** 4 GB RAM (8 GB recommended), 100 MB free disk space

See [`requirements.txt`](requirements.txt) for the full Python dependency list.

## Project Layout
- `DominantControl.py` — application entry point.
- `dominant_control/` — the application package (UI, telemetry, tools, overlays).
- `TireOverlayOriginal.py` — the Tire Wear overlay, launched as its own Qt process.
- `installer/` — the Inno Setup script that produces the Windows installer.
- `build/` — PyInstaller specs, version info and the portable launcher.
- `tools/` — the two PowerShell scripts that build the portable folder and the installer.
- `tests/` — the automated suite the build runs before packaging.
- `archive/` — legacy utilities and archived source, including the previous
  single-file application (`archive/dominant_controlnew.py`).
- `docs/` — supplemental documentation.

## Installation

### Run From Source (Windows)
1. Install Python 3.10+ and Visual C++ Build Tools (for PyAudio) if missing.
2. Clone or download this repository.
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Start the app: `python DominantControl.py`.

## How to Use the App
1. **Run Dominant Control.** Start the app (`python DominantControl.py` or the packaged executable) so it is ready to detect telemetry.
2. **Launch iRacing.** Start iRacing and load into a session or practice so telemetry is active. The HUD overlay should appear once telemetry is detected.
3. **Pick your input method.**
   - **Keyboard-only:** Use the default hotkeys to adjust driver controls.
   - **Wheel/joystick buttons:** Connect your device and map the inputs inside the app (or follow the on-screen prompts if the app detects a new device).
4. **Adjust driver controls.** Use the mapped keys/buttons to tweak items like brake bias, traction control, ABS, and other car-specific settings. Changes apply in real time and are reflected in the HUD.
5. **Save presets.** Store per-car or per-track configurations so you can quickly restore preferred setups in future sessions.
6. **Use macros (optional).** If you set up combos or macros, trigger them to apply multiple adjustments with a single input.
7. **Enable speech features (optional).** If text-to-speech or voice commands are installed, toggle them in the app settings to get audio feedback or issue hands-free adjustments.
8. **Exit safely.** Close the app when done or before shutting down iRacing.

> **Tip:** If the HUD does not appear, confirm that iRacing telemetry is enabled and that the game has fully loaded into a session.

## What the App Automates
Two automations act on their own, and both follow the weather rather than the
position on track:

- **Automatic wipers** — driven by the precipitation the SDK reports.
- **Auto Dry/Wet** — the Dry or Wet profile is applied when you get in the car
  and when the session's declared condition changes.

Nothing changes the car by itself because of where it is on the lap. There are
no LapDist macros, no automatic pit limiter, no fuel mixture by flag, no hybrid
hold by state of charge, no Push To Pass chaining and no automatic pit macro on
the Second Throttle.

## Building the Windows Release
From a Windows checkout, in PowerShell:

```powershell
.\tools\build_portable.ps1            # portable folder + ZIP
.\tools\build_installer.ps1           # the same, plus the Inno installer
.\tools\build_portable.ps1 -TestsOnly # just run the suite
```

Both scripts keep the build Python and the Inno compiler inside
`.build_runtime/`, so nothing is installed on the machine that compiles them.
The release itself is published by
`.github/workflows/dominant-control-v13-release.yml` when the tag `v13.0.0` is
pushed.

Application artwork is optional: drop `assets/DominantControl.ico` and
`assets/DominantControl.png` in before building and both the window and the
executable pick them up.

## Optional Features
- **Joystick input:** Requires `pygame`.
- **Text-to-speech:** Requires `pyttsx3`.
- **Audio device selection:** Requires `pyaudio`.
- **Voice commands:** Requires `speech_recognition` and `vosk` (plus a downloaded Vosk model).

## Known Issues
1. The application becomes “frozen” while iRacing is loading and will only unfreeze after the game has fully loaded. The application does not stop working; it is simply scanning for large changes and fluctuations in the telemetry during the simulation’s loading process.
2. The application do not work if vJoy is installed.

## License & Attribution
- Licensed under the **MIT License** (see [`license.md`](license.md)).
- Not affiliated with iRacing.com Motorsport Simulations, LLC.
