# Quick Start Guide (v5.0.0)

Get running in minutes with Dominant Control.

## Before You Begin
- Windows 10/11 (64-bit)
- iRacing installed and an active subscription
- (Source install) Python 3.10+ with dependencies from `requirements.txt`
- Start the app **before** iRacing if you use controllers; or use **Keyboard Only Mode**.

## 1) Install & Launch
### Windows Build
1. Download the latest release ZIP and extract it (e.g., `C:\DominantControl\`).
2. Run `DominantControl.exe`.

### From Source
1. `python -m pip install -r requirements.txt`
2. `python FINALOK.py`

## 2) Connect to iRacing
1. Start iRacing and enter a **private practice session**.
2. Click **Drive** so telemetry is active.
3. Keep Dominant Control open in the foreground or on a second monitor.

## 3) Scan & Configure
1. In the app, switch to **CONFIG** mode (button in the title bar).
2. Follow the guided steps in **Step 1–3** to pick car/track, confirm devices, and scan controls.
3. For each tab:
   - Click **Set Increase (+)** and press your desired key/button.
   - Click **Set Decrease (-)** and press your desired key/button.
   - Add up to four **Presets** by typing a value and binding a key/button.
   - Toggle HUD visibility for that variable if desired.

## 4) HUD / Overlay Setup
1. Open the **HUD / Overlay** tab inside the control notebook.
2. Check the variables you want visible.
3. Adjust font size, colors, and opacity; drag the overlay into place.
4. Click **Apply Style** to save per-car settings.

## 5) Combos & Timing
- **⚡ Combos Tab:** create macros that set multiple controls at once (e.g., “Rain” or “Pit Exit”). Bind to a single key/button.
- **Timing Profiles:** pick Aggressive / Casual / Relaxed or create a **Custom** profile if adjustments fire too slowly/quickly.

## 6) Voice & Audio Feedback
- Open **Options → Voice/Audio Settings** to configure engines, devices, and tuning.
- **Text-to-Speech:** enable in settings for spoken confirmations; choose an output device if PyAudio is available.
- **Vosk Voice Commands:**
  1. Download a Vosk English model and note its folder path.
  2. Set the **Vosk model path** and enable **Voice Commands**.
  3. Test phrases like “Brake bias up” or “Traction control preset two.”
  4. Use a push-to-talk key if your room is noisy.
- **whisper.cpp (offline):**
  1. Download a whisper.cpp build and a GGML/GGUF model file.
  2. Select the whisper.cpp executable and model in **Voice/Audio Settings**.
  3. Enable **Voice Commands** and test your phrases.

## 7) Save & Run
1. Enter car and track names, then click **Save Current**.
2. Switch back to **RUNNING** mode.
3. Test in the practice session: bindings, presets, combos, HUD, and voice commands.

## 8) Maintenance
- Profiles live in `%APPDATA%\DominantControl\configs\`; back them up before reinstalling.
- After updates, re-verify bindings, HUD layout, timing profile, and Vosk model path.

## Common Fixes
- **No telemetry:** be in Drive mode and rescan controls.
- **Force feedback loss:** start Dominant Control before iRacing or use Keyboard Only Mode.
- **HUD missing:** toggle overlay visibility and drag it back on-screen.
- **Voice misfires:** lower background noise or switch to a smaller Vosk model.

Happy racing! 🏁
