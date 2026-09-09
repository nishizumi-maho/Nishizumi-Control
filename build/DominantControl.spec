# -*- mode: python ; coding: utf-8 -*-

import os


ROOT = os.path.abspath(os.getcwd())
ASSETS = os.path.join(ROOT, "assets")
# The vendored Nishizumi SR tree imports itself by its own top-level names, so
# it has to be on `pathex` for PyInstaller to resolve them.
VENDORED_SR = os.path.join(ROOT, "dominant_control", "vendor", "nishizumi_sr")

hiddenimports = [
    "dominant_control.app",
    "dominant_control.core.ghost_keys",
    "dominant_control.core.iracing_controls_file",
    "dominant_control.core.second_throttle",
    "dominant_control.core.second_throttle_macros",
    "dominant_control.core.updates",
    "dominant_control.integration",
    "dominant_control.ui.ghost_keys",
    "dominant_control.ui.second_throttle",
    "dominant_control.profiles.panel",
    "dominant_control.tools.panel",
    "dominant_control.tools.caution.panel",
    "dominant_control.tools.caution.engine",
    "dominant_control.tools.fuel.panel",
    "dominant_control.tools.fair_share.panel",
    "dominant_control.tools.fair_share.engine",
    "dominant_control.tools.pit.panel",
    "dominant_control.tools.sr.panel",
    "dominant_control.tools.sr.engine",
    "dominant_control.tools.sr.vendored",
    "dominant_control.tools.tires.panel",
    "dominant_control.tools.traction.panel",
    "dominant_control.tools.original_overlays.bridge",
    "dominant_control.tools.original_overlays.client",
    "dominant_control.tools.original_overlays.launcher",
    "dominant_control.tools.original_overlays.worker_entry",
    "dominant_control.tools.original_overlays.caution_worker",
    "dominant_control.tools.original_overlays.fuel_worker",
    "dominant_control.tools.original_overlays.pit_worker",
    "dominant_control.tools.original_overlays.sr_worker",
    "dominant_control.tools.original_overlays.traction_worker",
    "dominant_control.vendor.nishizumi_tools.Nishizumi_CautionOverlay",
    "dominant_control.vendor.nishizumi_tools.Nishizumi_FuelMonitor",
    "dominant_control.vendor.nishizumi_tools.nishizumi_pitcalibrator",
    "dominant_control.vendor.nishizumi_tools.Nishizumi_Traction",
    # Vendored Nishizumi SR, by the names its own files import.
    "app_paths",
    "iracing.sdk",
    "iracing.session",
    "iracing.telemetry",
    "overlay.native",
    "overlay.styles",
    "overlay.widgets",
    "overlay.window",
    "sr.calculator",
    "sr.calibration",
    "sr.history",
    "sr.license_map",
    "sr.model",
    "tracks.database",
    "win32con",
    "win32gui",
    "win32process",
]

# The window and the executable use the application icon when it is present.
# The build works without it, so a checkout with no artwork still produces a
# runnable application.
ICON = os.path.join(ASSETS, "DominantControl.ico")
ICON_FILE = ICON if os.path.isfile(ICON) else None

datas = [
    # Read-only table the SR estimator resolves through sys._MEIPASS.
    (os.path.join(VENDORED_SR, "tracks", "official_corners.json"), "tracks"),
]
for artwork in ("DominantControl.ico", "DominantControl.png"):
    candidate = os.path.join(ASSETS, artwork)
    if os.path.isfile(candidate):
        datas.append((candidate, "."))

a = Analysis(
    [os.path.join(ROOT, "DominantControl.py")],
    pathex=[ROOT, VENDORED_SR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "pygame"],
    noarchive=False,
    optimize=1,
)
# A missing hidden import is only a warning in PyInstaller's log, and the
# Nishizumi SR modules arrive through `pathex` rather than through an import
# from the entry point: check that they were actually collected.
_collected = {entry[0] for entry in a.pure}
_missing = sorted(
    name
    for name in hiddenimports
    if name.split(".", 1)[0] in {"app_paths", "iracing", "overlay", "sr", "tracks"}
    and name not in _collected
)
if _missing:
    raise SystemExit(
        "PyInstaller did not collect Nishizumi SR: " + ", ".join(_missing)
    )

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DominantControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_FILE,
    version=os.path.join(ROOT, "build", "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DominantControl_Portable",
)
