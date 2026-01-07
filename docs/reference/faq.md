# Frequently Asked Questions (FAQ)

## General
**What is Dominant Control?**  
An accessibility-focused control manager for iRacing that maps driver-adjustable controls to keyboard/controller inputs, adds HUD overlays, presets, combo macros, TTS feedback, and offline voice commands via Vosk or whisper.cpp.

**Who should use it?**  
- Drivers with mobility limitations or restricted reach.  
- Users with basic hardware who need accessible bindings.  
- Learners studying telemetry, control timing, or input mapping.  
- Anyone wanting hands-free adjustments via voice.

**Is it open source?**  
Yes. Licensed under MIT (see `license.md`).

## Installation & Setup
**Do I need Python installed?**  
Only if running from source. The Windows build bundles everything. For source, install Python 3.10+ and run `python -m pip install -r requirements.txt`.

**Where are my profiles stored?**  
`%APPDATA%\DominantControl\configs\` (per-car/per-track JSON plus overlay and timing data).

**What are the key steps to get started?**  
1) Start the app before iRacing (or enable Keyboard Only Mode).  
2) Enter a private practice session and click Drive.  
3) CONFIG mode → **🔍 Scan Driver Controls**.  
4) Bind Increase/Decrease keys, presets, HUD visibility.  
5) Configure combos, timing profile, and Vosk model path if using voice.  
6) Save profile → RUNNING mode → test.

## Usage
**Can I run keyboard-only?**  
Yes. Enable **Keyboard Only Mode** to avoid joystick/FFB conflicts.

**How do I set presets?**  
Inside a control tab, enter a target value, click **Set Bind**, and press a key/button. Each tab supports four presets.

**What are combos?**  
Macros that set multiple controls at once (e.g., rain/dry presets). Configure in **⚡ Combos** and bind to one input.

**How do timing profiles work?**  
Choose Aggressive, Casual, Relaxed, or define **Custom** press/hold timings. Custom values save with the profile.

**Where do I manage devices?**  
Open device settings: enable/disable controllers or button boxes. Leave wheel/pedal HID devices unchecked if you only need button boxes to avoid FFB conflicts.

**How do I use the HUD?**  
In **HUD / Overlay**, select which variables to show, adjust colors/font/opacity, and drag the overlay. Settings save per car.

## Voice & Audio
**How do I enable TTS?**  
Turn on TTS in settings. If PyAudio is installed you can choose an output device; otherwise system default is used.

**How do I set up Vosk voice commands?**
1) Install `vosk`, `SpeechRecognition`, and `pyaudio` (already in `requirements.txt`).
2) Download a model from [Vosk releases](https://alphacephei.com/vosk/models) and extract it.
3) Set the **Vosk model path** to the extracted folder and select **Vosk** as the engine.
4) Enable **Voice Commands** and test phrases like “Brake bias up” or “Traction control preset two.”
5) For noisy rooms, use push-to-talk or a smaller model to reduce false triggers.

**How do I use whisper.cpp instead of Vosk?**
1) Download a whisper.cpp build for Windows and a compatible GGML/GGUF model.
2) Place the executable and model file somewhere stable (e.g., `C:\whisper\`).
3) In **Options → Voice/Audio Settings**, pick **whisper.cpp** as the voice engine.
4) Click **Select whisper.cpp...** and choose the executable.
5) Click **Select Whisper Model...** and choose the `.bin` or `.gguf` model file.
6) Enable **Voice Commands** and test your phrases. If recognition lags, try a smaller model.

## Troubleshooting
**The app can’t connect to iRacing.**  
Be in a live session and in the car (Drive). Rescan controls after joining.

**Force feedback stopped working.**  
Start Dominant Control before iRacing or use Keyboard Only Mode to bypass joystick initialization.

**HUD is missing.**  
Toggle overlay visibility, increase opacity, and drag it back on-screen.

**Voice commands don’t trigger.**  
Verify microphone input, confirm the Vosk model path, reduce ambient noise, or try a smaller model for faster response.

**PyAudio installation failed.**  
Install the latest Visual C++ Build Tools/Redistributable and re-run `pip install pyaudio`.

## Maintenance
**How do I back up my settings?**  
Copy `%APPDATA%\DominantControl\configs\` before reinstalling Windows or moving PCs.

**How do I update safely?**  
Overwrite the app with the latest release (or pull latest source) and re-verify bindings, HUD layout, timing profile, combos, and Vosk model path.

**Where can I report issues or request features?**  
Use the repository issue tracker or open a pull request. Include OS version, app version, reproduction steps, and logs.

## Ethics & Compliance
Dominant Control is intended for accessibility, education, and private practice. Use responsibly, follow iRacing policies, and respect fair competition. Not affiliated with iRacing.com Motorsport Simulations, LLC.
