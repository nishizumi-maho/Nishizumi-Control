# -*- mode: python ; coding: utf-8 -*-

import os


ROOT = os.path.abspath(os.getcwd())
ASSETS = os.path.join(ROOT, "assets")

hiddenimports = [
    "dominant_control.tools.original_overlays.client",
    "dominant_control.tools.original_overlays.tire_worker",
    "dominant_control.vendor.nishizumi_tools.Nishizumi_TireWear",
]

ICON = os.path.join(ASSETS, "DominantControl.ico")
ICON_FILE = ICON if os.path.isfile(ICON) else None

a = Analysis(
    [os.path.join(ROOT, "TireOverlayOriginal.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # This renderer is the one original Nishizumi Tools UI that uses PyQt5.
    # Keep every other Qt binding outside this isolated executable.
    excludes=["PyQt6", "PySide2", "PySide6", "tkinter", "pygame"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NishizumiTireOriginal",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NishizumiTireOriginal",
)
