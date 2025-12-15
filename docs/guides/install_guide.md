# Installation & Setup Guide (v3.0.0)

This guide walks through installing Dominant Control, enabling every option, and keeping the app healthy over time.

## 1. Requirements
- **OS:** Windows 10/11 (64-bit)
- **Python (source run):** 3.10+ with `pip`
- **Hardware:** 4 GB RAM (8 GB recommended), 100 MB free disk space
- **iRacing:** Installed with an active subscription
- **Extras:** Visual C++ Build Tools (for PyAudio) if you build from source

## 2. Installation Paths
### A) Windows Build
1. Download the latest release ZIP from the project releases page.
2. Extract to a writable folder (e.g., `C:\DominantControl\`).
3. Launch `DominantControl.exe`.

### B) From Source
1. Install Python 3.10+.
2. In a terminal, run:
   ```bash
   python -m pip install -r requirements.txt
   ```
3. Start the app:
   ```bash
   python main.py
   ```

## 3. First Launch Checklist
1. **Start order:** launch Dominant Control **before** iRacing to avoid FFB conflicts, or enable **Keyboard Only Mode** if you do not need controllers.
2. **Enter iRacing practice:** join a private practice session and click **Drive** so telemetry is active.
3. **Open CONFIG mode:** click the mode toggle in the title bar (turns orange).
4. **Scan controls:** click **🔍 Scan Driver Controls** and wait for tabs to appear.
5. **Bind controls:** for each tab, set Increase/Decrease keys and optional presets.
6. **HUD setup:** open **HUD / Overlay**, choose variables, colors, opacity, and drag the overlay into place.
7. **Timing profile:** select Aggressive, Casual, Relaxed, or configure **Custom** press/hold timings.
8. **Save profile:** enter car and track names and click **Save Current**. Profiles auto-load on match.

## 4. Voice Command Engines (Vosk or Whisper)
You can run voice commands with either Vosk (lightweight) or Whisper (higher accuracy, can use GPU). Install dependencies from `requirements.txt` first.

### A) Vosk Setup (CPU-friendly)
1. Install voice dependencies: `vosk`, `SpeechRecognition`, `pyaudio`.
2. Download a Vosk English model (e.g., `vosk-model-small-en-us-0.15`) from [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models).
3. Extract the model folder to a stable path (e.g., `C:\vosk\vosk-model-small-en-us-0.15`).
4. In the app settings, set **Vosk model path** to the extracted folder and pick **Vosk** as the voice engine.
5. Enable **Voice Commands** and test phrases like “Brake bias up” or “Traction control preset two.”

### B) Whisper Setup (accuracy-focused, optional GPU)
1. Ensure `faster-whisper` is installed (included in `requirements.txt`).
2. Download a CTranslate2 Whisper model from the [faster-whisper collection](https://huggingface.co/collections/Systran/faster-whisper-655b5dd2959f16f30bdd5e23) — start with **tiny**/**base** for CPU or **small**/**medium**/**large-v2** if you have GPU headroom.
3. Extract the model folder somewhere stable (e.g., `C:\whisper\faster-whisper-small`). The folder should contain `model.bin` and tokenizer files.
4. In the app, set the voice engine to **Whisper**, click **Select Whisper model folder**, and point to the extracted folder.
5. Enable **Voice Commands** and test your phrases. If recognition lags, try a smaller model or keep the model on a faster drive.

## 5. Feature-by-Feature Guide
- **CONFIG / RUNNING Modes:** configure in CONFIG, drive in RUNNING. The mode button clearly shows the active state.
- **Control Tabs:** each detected driver control exposes Increase/Decrease bindings, four presets, and HUD visibility.
- **⚡ Combos:** build multi-control macros (e.g., rain/dry setups) and bind them to one key or button.
- **Timing Profiles:** choose Aggressive/Casual/Relaxed or fine-tune Custom press and release timings.
- **HUD / Overlay:** toggle variables, style colors/fonts/opacity, and drag placement. Settings save per car.
- **Device Management:**
  - **Keyboard Only Mode:** restarts the app with keyboard-only input (best for avoiding FFB conflicts).
  - **Manage Devices:** enable/disable controllers or button boxes; leave wheel/pedal HID devices unchecked if not needed.
- **Text-to-Speech:** enables spoken feedback. Select an output device if PyAudio is available; otherwise, default audio is used.
- **Watchdog:** monitors critical loops and restarts them if needed; errors are printed to the console.

## 6. Maintenance & Updates
- **Updating:** replace files with the latest release or pull new commits; configs stay in `%APPDATA%\DominantControl\configs\`.
- **Backup:** copy `%APPDATA%\DominantControl\configs\` before reinstalling Windows or changing PCs.
- **Verification after update:** check bindings, HUD layout, timing profile, combo macros, and the Vosk model path.
- **Logs:** keep the console visible during testing to spot warnings (e.g., missing dependencies or model path issues).

## 7. Troubleshooting
- **Application won’t start:** ensure Windows 10/11 64-bit; reinstall Visual C++ Redistributable; re-run dependency install.
- **Lost force feedback:** start Dominant Control before iRacing or switch to Keyboard Only Mode.
- **No telemetry:** you must be in a live session and in the car (Drive). Rescan controls after joining.
- **HUD missing:** toggle overlay visibility, raise opacity, and drag it back on-screen.
- **Voice commands unreliable:** confirm microphone input, reduce ambient noise, and validate the Vosk model path.
- **PyAudio errors:** reinstall PyAudio after installing the latest Visual C++ Build Tools/Redistributable.

## 8. Uninstallation
1. Delete the application folder (or remove the repository clone).
2. Optional: delete `%APPDATA%\DominantControl\configs\` to remove saved profiles.

## 9. Support & Contributions
- Read `README.md`, `quickstart.md`, `faq.md`, and `changelog.md` for details.
- File issues or pull requests with reproduction steps, OS version, app version, and logs where possible.
- Licensed under MIT; contributions are welcome.
