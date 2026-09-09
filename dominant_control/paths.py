"""Centralized storage paths for normal and fully portable executions."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from .edition import APP_DATA_FOLDER
TRUE_VALUES = {'1', 'true', 'yes', 'on', 'sim'}

def application_dir() -> Path:
    """Return the writable folder containing the app or the source project."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]

def is_portable() -> bool:
    """Portable builds opt in through an environment flag or marker file."""
    raw = str(os.getenv('DOMINANT_CONTROL_PORTABLE', '')).strip().lower()
    return raw in TRUE_VALUES or (application_dir() / 'portable.mode').is_file()

def _appdata_root() -> Path:
    value = os.getenv('APPDATA') or os.getenv('LOCALAPPDATA')
    return Path(value) if value else Path.home() / '.config'

def dominant_control_data_dir(*parts: str) -> Path:
    """Dominant Control data, preserving the legacy folder outside portable mode."""
    override = os.getenv('DOMINANT_CONTROL_DATA_DIR')
    if override:
        root = Path(override)
    elif is_portable():
        root = application_dir() / 'data' / 'dominant_control'
    else:
        root = _appdata_root() / APP_DATA_FOLDER
    path = root.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path

def tools_data_dir(*parts: str) -> Path:
    """Tools data, preserving its historical folder outside portable mode."""
    override = os.getenv('DOMINANT_CONTROL_TOOLS_DATA_DIR')
    if override:
        root = Path(override)
    elif is_portable():
        root = application_dir() / 'data' / 'dominant_control_tools'
    else:
        root = _appdata_root() / 'DominantControlTools'
    path = root.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path

def profiles_bootstrap_path() -> Path:
    """Path used only to remember where the user's Documents/iRacing lives."""
    if is_portable():
        return dominant_control_data_dir('profiles') / 'bootstrap.json'
    return Path.home() / '.iracing_renderer_combo_profile_manager_bootstrap.json'
__all__ = ['application_dir', 'is_portable', 'dominant_control_data_dir', 'profiles_bootstrap_path', 'tools_data_dir']
