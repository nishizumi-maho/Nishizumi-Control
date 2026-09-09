# Dominant Control v13

Dominant Control is now a package instead of a single file, and every tool
that used to be a separate download runs inside it.

**This is a pre-release.** The in-app update check reads GitHub's
`releases/latest`, which ignores pre-releases, so drivers on v12 are not
prompted to move until this release is promoted to the latest one.

## What is new

- **Nishizumi Tools inside the application** — Fuel Monitor, Tire Wear,
  Traction, Safety Rating, Pit Calibrator, Caution and the Fair Share
  calculator share one iRacing connection and keep working in the background.
- **Graphics Profiles** — per car/track `app.ini` profiles, applied and synced
  while the simulator is closed.
- **Auto Dry/Wet** — the Dry or Wet profile is applied when you get in the car
  and when the session's declared condition changes, and it keeps reconciling
  until every control matches.
- **Automatic wipers** — driven by the precipitation the SDK reports.
- **Auxiliary Keys** — any number of extra triggers for one in-game key.
- **Second Throttle** — hold a percentage of throttle from a key or a button.
- **Voice triggers, HUD and overlays** — rebuilt on the shared telemetry.
- **Update check** — every six hours, and on demand from the Options menu.
- **Pit rules on the HUD** — the ruleset iRacing publishes for the session
  (`WeekendInfo:AltAssetTag`: IMSA, NEC, DTM, and the global tags), read for
  the car being driven: whether fuel and tires are serviced together or in
  sequence, when an ARB, wing or bodywork change is applied, and how long a
  full tank and a set of four tires take.

## Install

- **Installer** — `DominantControl_v13_Setup.exe`, per user, no administrator
  rights. It updates an existing Dominant Control installation in place.
- **Portable** — `DominantControl_Portable_v13_Windows_x64.zip`. Unzip it and
  run `DominantControl.exe`; the data stays in `data\` beside the executable.

Each file has a `.sha256` beside it.

## Known issues

1. The application looks frozen while iRacing loads a session; it is scanning
   the telemetry and comes back on its own.
2. It does not work with vJoy installed.
