# Nishizumi (Dominant) Control for iRacing

<img width="256" height="256" alt="dominant_control (1)-5" src="https://github.com/user-attachments/assets/994813f4-dff8-4789-8ba9-3b886bb1e794" />



An accessibility-focused control manager for iRacing with keyboard/controller mapping, HUD overlays, per-car presets, combo macros, text-to-speech prompts, and offline voice control powered by Vosk.

![Version](https://img.shields.io/badge/version-3.0.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## What's New in 3.0
- 🎤 **Offline voice commands with Vosk** for hands-free adjustments.
- 🧭 **End-to-end tutorial** covering setup, usage, maintenance, and every option in the app.
- 📜 **MIT License** with refreshed documentation and clearer support guidance.
- 🔧 Updated requirements for running from source and clearer model download steps.

## Overview
Dominant Control maps iRacing’s driver-adjustable controls (e.g., brake bias, TC, ABS, ARB) to accessible inputs. The app provides:
- Real-time HUD overlay with per-car visibility and styling controls.
- Per-car/per-track configuration storage and quick preset buttons.
- Combo macros to change multiple controls at once.
- Text-to-speech feedback with configurable output devices.
- Optional offline voice control via Vosk (with downloadable models).

## Supported Platforms & Requirements
- **OS:** Windows 10/11 (64-bit)
- **iRacing:** Valid subscription with telemetry enabled
- **Python (source install):** 3.10+ (for running `main.py` directly)
- **Hardware:** 4 GB RAM (8 GB recommended), 100 MB free disk space

See [`requirements.txt`](requirements.txt) for the full Python dependency list.

## Installation Options
### Option A: Run the Windows Build
1. Download the latest release archive.
2. Extract anywhere (e.g., `C:\DominantControl\`).
3. Launch `DominantControl.exe`.

### Option B: Run From Source (Windows)
1. Install Python 3.10+ and Visual C++ Build Tools (for PyAudio) if missing.
2. Clone or download this repository.
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Start the app: `python main.py`.

## Quick Start (5 Minutes)
1. **Start order (for best FFB compatibility):** launch Dominant Control **before** starting iRacing. If you use only keyboard bindings, enable **Keyboard Only Mode** to avoid device conflicts entirely.
2. In iRacing, enter a **private practice session** and click **Drive**.
3. In the app, switch to **CONFIG** mode and click **🔍 Scan Driver Controls**.
4. Open each detected tab (e.g., BrakeBias) and bind **Increase (+)** and **Decrease (-)** keys.
5. (Optional) Add **Presets** for common values and bind a key/button.
6. Switch to **RUNNING** mode and test in the practice session.

## Full Tutorial
### 1) Modes
- **CONFIG Mode:**
  - Scan driver controls.
  - Set Increase/Decrease bindings per control.
  - Add presets and combo macros.
  - Configure HUD and overlay styling.
  - Save/load per car/track profiles.
- **RUNNING Mode:**
  - Execute bindings, presets, and combos.
  - View live HUD values and status.

### 2) Control Tabs (per detected driver control)
Each tab includes:
- **Bindings:** Set Increase (+) / Decrease (-) keys or buttons.
- **Presets:** Up to four quick-jump values with individual binds.
- **Options:** Per-control visibility toggle for the HUD and optional rate limits.

### 3) HUD / Overlay
- **Visibility:** Toggle individual variables and the overlay itself.
- **Style:** Colors, font size, opacity, and snap-to-corner options.
- **Positioning:** Drag the overlay in CONFIG mode; position is saved per car.

### 4) Combos (⚡ Combos Tab)
- Define multi-control macros (e.g., “Rain”: TC 5, ABS 3, Brake Bias 54.0).
- Bind one key/button to apply all values instantly.
- Useful for pit exit, weather changes, or caution procedures.

### 5) Timing Profiles
- **Aggressive:** Fast key pulses (~40 ms).
- **Casual:** Balanced timing (~80–100 ms).
- **Relaxed:** Slow/safer timing (~150–200 ms).
- **Custom:** Fully tune press/hold intervals; saved with your profile.

### 6) Device Management
- **Keyboard Only Mode:** Restart in keyboard-only configuration (best for avoiding FFB conflicts).
- **Manage Devices:** Enable/disable individual controllers or button boxes. Leave wheel/pedal HID devices unchecked if you only need button boxes to avoid haptics conflicts.

### 7) Text-to-Speech (TTS)
- **Enable TTS:** Provides spoken confirmation of mode changes and presets.
- **Output Device:** Choose an audio device if PyAudio is installed; falls back to default otherwise.
- **Voice Selection:** Uses the first available English voice; customize via OS voice settings.

### 8) Voice Control with Vosk
1. Install dependencies (`vosk`, `SpeechRecognition`, `pyaudio`).
2. Download an English model from the [Vosk releases](https://alphacephei.com/vosk/models) (e.g., `vosk-model-small-en-us-0.15`).
3. Extract the model folder somewhere convenient (e.g., `C:\vosk\vosk-model-small-en-us-0.15`).
4. In the app settings, set the **Vosk model path** to that folder and enable **Voice Commands**.
5. Speak commands like “Brake bias up,” “Traction control preset two,” or custom phrases you bind in the voice settings panel.
6. Use a quiet environment or a push-to-talk input to reduce false triggers.

### 9) Saving & Profiles
- **Save Current:** Stores car+track specific bindings, HUD state, timing profile, presets, combos, and Vosk settings.
- **Auto-Load:** When the detected car/track matches a saved profile, settings load automatically.
- **Backup:** Copy `%APPDATA%\DominantControl\configs\` to preserve setups before reinstalling.

### 10) Maintenance & Updates
- **Updating:** Replace files with the latest release or pull new commits; configs in `%APPDATA%\DominantControl\` remain intact.
- **Verifying:** After updates, open CONFIG mode and confirm bindings, HUD, combos, timing profile, and Vosk model path.
- **Logs & Health:** The built-in watchdog restarts critical loops if they stall; check console output for warnings.

## Troubleshooting
- **Force feedback stops:** Start the app before iRacing or use Keyboard Only Mode.
- **No telemetry:** Ensure you are in Drive mode in a live session; rescan controls.
- **HUD missing:** Toggle overlay visibility, reset opacity, and drag it back on-screen.
- **Voice commands unreliable:** Verify microphone input, model path, and ambient noise. Small models run faster; large models are more accurate.
- **PyAudio errors:** Install the latest Visual C++ Redistributable and re-run `pip install pyaudio`.

## Privacy & Network
- Works entirely offline except for iRacing’s local telemetry.
- Stores configs locally under `%APPDATA%\DominantControl\configs\`.
- No analytics, tracking, or cloud services are used.

## License & Attribution
- Licensed under the **MIT License** (see [`license.md`](license.md)).
- Not affiliated with iRacing.com Motorsport Simulations, LLC.

## Community & Support
- Read the full documentation set: `readmefirst.txt`, `quickstart.md`, `install_guide.md`, `faq.md`, and `changelog.md`.
- Report issues or feature requests through the repository’s issue tracker; include OS version, app version, and reproduction steps.
- Contributions are welcome via pull requests aligned with the MIT license.
