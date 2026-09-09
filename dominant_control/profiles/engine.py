#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import irsdk
import psutil

from ..paths import profiles_bootstrap_path
try:
    import win32con
    import win32gui
    import win32process
except ImportError:  # Allows model/config tests on non-Windows hosts.
    win32con = None
    win32gui = None
    win32process = None


# ============================================================
# CONFIG
# ============================================================
DEFAULT_IRACING_DOCS = Path.home() / "Documents" / "iRacing"
BOOTSTRAP_SETTINGS_PATH = profiles_bootstrap_path()

RENDERER_OPTIONS = {
    "Monitor": "rendererDX11Monitor.ini",
    "OpenXR": "rendererDX11OpenXR.ini",
    "OpenVR": "rendererDX11OpenVR.ini",
}

GROUPING_OPTIONS = {
    "Car + track": "car_track",
    "Car": "car",
    "Track": "track",
    "Series": "series",
    "Series + track": "series_track",
}

SIM_PROCESS_NAMES = {
    "iracingsim64dx11.exe",
    "iracingsim64dx12.exe",
    "iracingsim64.exe",
}

POLL_INTERVAL = 0.25
PRE_CLOSE_DELAY = 5.0
POST_CLOSE_SETTLE_TIMEOUT = 25.0
FILE_STABLE_SECONDS = 2.0
LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
APP_NAME = "Nishizumi_Profiles"
APP_VERSION = "1.0.0"
PROFILE_PRIORITY_ORDER = ["car_track", "car", "track", "series_track", "series"]


# ============================================================
# HELPERS
# ============================================================
def now_str() -> str:
    return time.strftime(LOG_TIMESTAMP_FORMAT)


def norm(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).strip().lower().split())


def slugify(text: str | None) -> str:
    raw = norm(text)
    if not raw:
        return "unknown"
    chars: list[str] = []
    for ch in raw:
        chars.append(ch if ch.isalnum() else "_")
    out = "".join(chars)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "unknown"


def file_sha1(path: Path) -> str | None:
    try:
        h = hashlib.sha1()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def files_equal(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except Exception:
        return False


def safe_get(ir: irsdk.IRSDK, key: str):
    try:
        return ir[key]
    except Exception:
        return None


def wait_for_file_stable(path: Path, stable_seconds: float, timeout: float) -> bool:
    start = time.time()
    last_state = None
    stable_since = None

    while time.time() - start < timeout:
        try:
            stat = path.stat()
            state = (stat.st_size, stat.st_mtime_ns)
        except FileNotFoundError:
            state = None

        if state == last_state:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_seconds:
                return True
        else:
            stable_since = None
            last_state = state

        time.sleep(0.25)

    return False


# ============================================================
# DATA MODEL
# ============================================================
@dataclass
class ComboInfo:
    track_internal: str = ""
    track_config: str = ""
    track_display: str = ""
    track_display_short: str = ""
    car_path: str = ""
    car_screen: str = ""
    car_short: str = ""
    series_id: str = ""

    def track_key(self) -> str:
        base = self.track_internal or self.track_display or "unknown_track"
        cfg = self.track_config or "default"
        return f"{slugify(base)}__cfg_{slugify(cfg)}"

    def car_key(self) -> str:
        base = self.car_path or self.car_screen or self.car_short or "unknown_car"
        return slugify(base)

    def series_key(self) -> str:
        return slugify(self.series_id or "unknown_series")

    def combo_key(self, grouping_mode: str) -> str:
        if grouping_mode == "car_track":
            return f"trk_{self.track_key()}__car_{self.car_key()}"
        if grouping_mode == "car":
            return f"car_{self.car_key()}"
        if grouping_mode == "track":
            return f"trk_{self.track_key()}"
        if grouping_mode == "series":
            return f"series_{self.series_key()}"
        if grouping_mode == "series_track":
            return f"series_{self.series_key()}__trk_{self.track_key()}"
        return f"trk_{self.track_key()}__car_{self.car_key()}"

    def base_filename(self, grouping_mode: str, renderer_stem: str) -> str:
        return f"{self.combo_key(grouping_mode)}__mode_{grouping_mode}__{renderer_stem}"

    def label(self) -> str:
        track_name = self.track_display or self.track_internal or "Unknown track"
        cfg = self.track_config or "default"
        car_name = self.car_screen or self.car_path or self.car_short or "Unknown car"
        series_name = self.series_id or "unknown"
        return f'{track_name} [{cfg}] / {car_name} / Series {series_name}'

    def is_complete(self) -> bool:
        track_present = bool(self.track_internal or self.track_display)
        car_present = bool(self.car_path or self.car_screen or self.car_short)
        series_present = bool(self.series_id)
        return track_present or car_present or series_present

    def normalized(self) -> "ComboInfo":
        return ComboInfo(
            track_internal=norm(self.track_internal),
            track_config=norm(self.track_config),
            track_display=self.track_display.strip() if self.track_display else "",
            track_display_short=self.track_display_short.strip() if self.track_display_short else "",
            car_path=norm(self.car_path),
            car_screen=self.car_screen.strip() if self.car_screen else "",
            car_short=self.car_short.strip() if self.car_short else "",
            series_id=norm(self.series_id),
        )

    @staticmethod
    def from_manifest_entry(entry: dict[str, Any]) -> "ComboInfo":
        return ComboInfo(
            track_internal=str(entry.get("track_internal") or ""),
            track_config=str(entry.get("track_config") or ""),
            track_display=str(entry.get("track_display") or ""),
            track_display_short=str(entry.get("track_display_short") or ""),
            car_path=str(entry.get("car_path") or ""),
            car_screen=str(entry.get("car_screen") or ""),
            car_short=str(entry.get("car_short") or ""),
            series_id=str(entry.get("series_id") or ""),
        )


# ============================================================
# PROFILE MANAGER
# ============================================================
class ProfileManager:
    def __init__(self, iracing_docs: Path) -> None:
        self.iracing_docs = iracing_docs
        self.profile_root = self.iracing_docs / "combo_profiles"
        self.app_settings_path = self.profile_root / "app_settings.json"
        self.manifest_path = self.profile_root / "index.json"
        self.lock = threading.RLock()
        self.ensure_dirs()
        self.settings = self.load_settings()
        self.manifest = self.load_manifest()

        self.ensure_renderer_section(self.get_selected_renderer())
        self.ensure_grouping_section(self.get_selected_renderer(), self.get_selected_grouping())
        self.ensure_global_backup_if_missing()

    def ensure_dirs(self) -> None:
        self.profile_root.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> dict[str, Any]:
        if self.app_settings_path.exists():
            try:
                data = json.loads(self.app_settings_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def save_settings(self) -> None:
        self.app_settings_path.write_text(
            json.dumps(self.settings, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def needs_initial_setup(self) -> bool:
        renderer_ok = self.settings.get("default_renderer") in RENDERER_OPTIONS.values()
        grouping_ok = self.settings.get("default_grouping") in GROUPING_OPTIONS.values()
        return not (renderer_ok and grouping_ok)

    def load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("renderers"), dict):
                    return data
            except Exception:
                pass
        return {
            "updated_at": now_str(),
            "renderers": {},
        }

    def save_manifest(self) -> None:
        with self.lock:
            self.manifest["updated_at"] = now_str()
            self.manifest_path.write_text(
                json.dumps(self.manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def get_selected_renderer(self) -> str:
        selected = str(self.settings.get("default_renderer") or "rendererDX11OpenXR.ini")
        if selected not in RENDERER_OPTIONS.values():
            selected = "rendererDX11OpenXR.ini"
        return selected

    def set_selected_renderer(self, renderer_file: str) -> None:
        if renderer_file not in RENDERER_OPTIONS.values():
            raise ValueError(f'Invalid renderer: {renderer_file}')
        with self.lock:
            self.settings["default_renderer"] = renderer_file
            self.save_settings()
            self.ensure_renderer_section(renderer_file)
            self.ensure_grouping_section(renderer_file, self.get_selected_grouping())
            self.ensure_global_backup_if_missing(renderer_file)

    def get_selected_grouping(self) -> str:
        selected = str(self.settings.get("default_grouping") or "car_track")
        if selected not in GROUPING_OPTIONS.values():
            selected = "car_track"
        return selected

    def get_bool_setting(self, key: str, default: bool) -> bool:
        raw = self.settings.get(key, default)
        return bool(raw) if isinstance(raw, bool) else default

    def set_bool_setting(self, key: str, value: bool) -> None:
        with self.lock:
            self.settings[key] = bool(value)
            self.save_settings()

    def set_selected_grouping(self, grouping_mode: str) -> None:
        if grouping_mode not in GROUPING_OPTIONS.values():
            raise ValueError(f'Invalid grouping mode: {grouping_mode}')
        with self.lock:
            self.settings["default_grouping"] = grouping_mode
            self.save_settings()
            self.ensure_renderer_section(self.get_selected_renderer())
            self.ensure_grouping_section(self.get_selected_renderer(), grouping_mode)

    def get_active_ini(self, renderer_file: str | None = None) -> Path:
        renderer_file = renderer_file or self.get_selected_renderer()
        return self.iracing_docs / renderer_file

    def get_active_app_ini(self) -> Path:
        return self.iracing_docs / "app.ini"

    def renderer_stem(self, renderer_file: str | None = None) -> str:
        renderer_file = renderer_file or self.get_selected_renderer()
        return Path(renderer_file).stem

    def get_renderer_dir(self, renderer_file: str | None = None) -> Path:
        renderer_file = renderer_file or self.get_selected_renderer()
        path = self.profile_root / self.renderer_stem(renderer_file)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_grouping_dir(self, renderer_file: str | None = None, grouping_mode: str | None = None) -> Path:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        path = self.get_renderer_dir(renderer_file) / grouping_mode
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_global_backup_path(self, renderer_file: str | None = None) -> Path:
        renderer_file = renderer_file or self.get_selected_renderer()
        return self.profile_root / f"{self.renderer_stem(renderer_file)}.global_backup.ini"

    def get_app_ini_dir(self) -> Path:
        path = self.profile_root / "app_ini_by_car"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_app_ini_profile_path(self, combo: ComboInfo) -> Path:
        combo = combo.normalized()
        return self.get_app_ini_dir() / f"car_{combo.car_key()}__app.ini"

    def get_app_ini_meta_path(self, combo: ComboInfo) -> Path:
        combo = combo.normalized()
        return self.get_app_ini_dir() / f"car_{combo.car_key()}__app.json"

    def write_app_ini_meta(self, combo: ComboInfo, app_ini_path: Path) -> None:
        combo = combo.normalized()
        meta = {
            "car_key": combo.car_key(),
            "car_path": combo.car_path,
            "car_screen": combo.car_screen,
            "car_short": combo.car_short,
            "profile_app_ini": str(app_ini_path),
            "saved_at": now_str(),
            "sha1": file_sha1(app_ini_path),
        }
        self.get_app_ini_meta_path(combo).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def ensure_renderer_section(self, renderer_file: str) -> None:
        with self.lock:
            renderers = self.manifest.setdefault("renderers", {})
            if renderer_file not in renderers:
                renderers[renderer_file] = {
                    "global_backup": str(self.get_global_backup_path(renderer_file)),
                    "groupings": {},
                }
                self.save_manifest()

    def ensure_grouping_section(self, renderer_file: str, grouping_mode: str) -> None:
        with self.lock:
            self.ensure_renderer_section(renderer_file)
            renderer_section = self.manifest["renderers"][renderer_file]
            groupings = renderer_section.setdefault("groupings", {})
            if grouping_mode not in groupings:
                groupings[grouping_mode] = {"combos": {}}
                self.save_manifest()

    def renderer_section(self, renderer_file: str | None = None) -> dict[str, Any]:
        renderer_file = renderer_file or self.get_selected_renderer()
        self.ensure_renderer_section(renderer_file)
        return self.manifest["renderers"][renderer_file]

    def grouping_section(self, renderer_file: str | None = None, grouping_mode: str | None = None) -> dict[str, Any]:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        self.ensure_grouping_section(renderer_file, grouping_mode)
        return self.manifest["renderers"][renderer_file]["groupings"][grouping_mode]

    def ensure_global_backup_if_missing(self, renderer_file: str | None = None) -> None:
        renderer_file = renderer_file or self.get_selected_renderer()
        backup_path = self.get_global_backup_path(renderer_file)
        active_ini = self.get_active_ini(renderer_file)
        with self.lock:
            if not backup_path.exists() and active_ini.exists():
                shutil.copy2(active_ini, backup_path)
                self.save_manifest()

    def create_or_refresh_global_backup(self, renderer_file: str | None = None) -> None:
        renderer_file = renderer_file or self.get_selected_renderer()
        active_ini = self.get_active_ini(renderer_file)
        backup_path = self.get_global_backup_path(renderer_file)
        if not active_ini.exists():
            raise FileNotFoundError(f'Active file not found: {active_ini}')
        shutil.copy2(active_ini, backup_path)
        self.save_manifest()

    def restore_global_backup(self, renderer_file: str | None = None) -> None:
        renderer_file = renderer_file or self.get_selected_renderer()
        active_ini = self.get_active_ini(renderer_file)
        backup_path = self.get_global_backup_path(renderer_file)
        if not backup_path.exists():
            raise FileNotFoundError(
                f'Global backup not found: {backup_path}'
            )
        if not active_ini.exists():
            raise FileNotFoundError(f'Active file not found: {active_ini}')
        shutil.copy2(backup_path, active_ini)

    def get_profile_ini_path(self, combo: ComboInfo, renderer_file: str | None = None, grouping_mode: str | None = None) -> Path:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        return self.get_grouping_dir(renderer_file, grouping_mode) / f"{combo.base_filename(grouping_mode, self.renderer_stem(renderer_file))}.ini"

    def get_profile_meta_path(self, combo: ComboInfo, renderer_file: str | None = None, grouping_mode: str | None = None) -> Path:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        return self.get_grouping_dir(renderer_file, grouping_mode) / f"{combo.base_filename(grouping_mode, self.renderer_stem(renderer_file))}.json"

    def write_profile_meta(self, combo: ComboInfo, ini_path: Path, renderer_file: str, grouping_mode: str) -> None:
        meta = {
            "renderer_file": renderer_file,
            "grouping_mode": grouping_mode,
            "combo_key": combo.combo_key(grouping_mode),
            "track_internal": combo.track_internal,
            "track_config": combo.track_config,
            "track_display": combo.track_display,
            "track_display_short": combo.track_display_short,
            "car_path": combo.car_path,
            "car_screen": combo.car_screen,
            "car_short": combo.car_short,
            "series_id": combo.series_id,
            "profile_ini": str(ini_path),
            "saved_at": now_str(),
            "sha1": file_sha1(ini_path),
        }
        self.get_profile_meta_path(combo, renderer_file, grouping_mode).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_entries(self, renderer_file: str | None = None, grouping_mode: str | None = None) -> list[dict[str, Any]]:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        combos = self.grouping_section(renderer_file, grouping_mode).get("combos", {})
        items = list(combos.values())
        items.sort(
            key=lambda item: (
                str(item.get("track_display") or item.get("track_internal") or ""),
                str(item.get("track_config") or ""),
                str(item.get("car_screen") or item.get("car_path") or ""),
                str(item.get("series_id") or ""),
            )
        )
        return items

    def get_entry(self, combo_key: str, renderer_file: str | None = None, grouping_mode: str | None = None) -> dict[str, Any] | None:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        return self.grouping_section(renderer_file, grouping_mode).get("combos", {}).get(combo_key)

    def register_combo(self, combo: ComboInfo, renderer_file: str | None = None, grouping_mode: str | None = None) -> dict[str, Any]:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        combo = combo.normalized()
        combo_key = combo.combo_key(grouping_mode)

        with self.lock:
            section = self.grouping_section(renderer_file, grouping_mode)
            combos = section.setdefault("combos", {})
            existing = combos.get(combo_key)
            ini_path = self.get_profile_ini_path(combo, renderer_file, grouping_mode)
            meta_path = self.get_profile_meta_path(combo, renderer_file, grouping_mode)

            if existing is None:
                entry = {
                    "combo_key": combo_key,
                    "renderer_file": renderer_file,
                    "grouping_mode": grouping_mode,
                    "track_internal": combo.track_internal,
                    "track_config": combo.track_config,
                    "track_display": combo.track_display,
                    "track_display_short": combo.track_display_short,
                    "car_path": combo.car_path,
                    "car_screen": combo.car_screen,
                    "car_short": combo.car_short,
                    "series_id": combo.series_id,
                    "profile_ini": str(ini_path),
                    "profile_meta": str(meta_path),
                    "profile_sha1": file_sha1(ini_path) if ini_path.exists() else None,
                    "enabled": True,
                    "autosave_on_manual_close": True,
                    "save_app_ini_on_autosave": True,
                    "last_used_at": now_str(),
                    "last_saved_at": None,
                }
                combos[combo_key] = entry
            else:
                entry = existing
                entry.update({
                    "renderer_file": renderer_file,
                    "grouping_mode": grouping_mode,
                    "track_internal": combo.track_internal,
                    "track_config": combo.track_config,
                    "track_display": combo.track_display,
                    "track_display_short": combo.track_display_short,
                    "car_path": combo.car_path,
                    "car_screen": combo.car_screen,
                    "car_short": combo.car_short,
                    "series_id": combo.series_id,
                    "profile_ini": str(ini_path),
                    "profile_meta": str(meta_path),
                    "last_used_at": now_str(),
                })
                if "save_app_ini_on_autosave" not in entry:
                    entry["save_app_ini_on_autosave"] = True

            self.save_manifest()
            return entry

    def update_entry_options(
        self,
        combo_key: str,
        enabled: bool,
        autosave: bool,
        save_app_ini: bool,
        renderer_file: str | None = None,
        grouping_mode: str | None = None,
    ) -> None:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        entry = self.get_entry(combo_key, renderer_file, grouping_mode)
        if not entry:
            raise KeyError(f'Combination not found: {combo_key}')
        entry["enabled"] = bool(enabled)
        entry["autosave_on_manual_close"] = bool(autosave)
        entry["save_app_ini_on_autosave"] = bool(save_app_ini)
        self.save_manifest()

    def resolve_best_entry(self, combo: ComboInfo, renderer_file: str | None = None) -> tuple[str, dict[str, Any]] | None:
        renderer_file = renderer_file or self.get_selected_renderer()
        combo = combo.normalized()
        for grouping_mode in PROFILE_PRIORITY_ORDER:
            combo_key = combo.combo_key(grouping_mode)
            entry = self.get_entry(combo_key, renderer_file, grouping_mode)
            if entry:
                return grouping_mode, entry
        return None

    def save_active_ini_as_profile(self, combo: ComboInfo, renderer_file: str | None = None, grouping_mode: str | None = None) -> dict[str, Any]:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        combo = combo.normalized()
        active_ini = self.get_active_ini(renderer_file)

        if not active_ini.exists():
            raise FileNotFoundError(f'Active file not found: {active_ini}')

        entry = self.register_combo(combo, renderer_file, grouping_mode)
        ini_path = self.get_profile_ini_path(combo, renderer_file, grouping_mode)
        shutil.copy2(active_ini, ini_path)
        self.write_profile_meta(combo, ini_path, renderer_file, grouping_mode)

        entry["profile_sha1"] = file_sha1(ini_path)
        entry["last_saved_at"] = now_str()
        self.save_manifest()
        return entry

    def save_active_app_ini_for_car(self, combo: ComboInfo) -> Path:
        combo = combo.normalized()
        active_app_ini = self.get_active_app_ini()
        if not active_app_ini.exists():
            raise FileNotFoundError(
                f'Active file not found: {active_app_ini}'
            )
        app_ini_path = self.get_app_ini_profile_path(combo)
        shutil.copy2(active_app_ini, app_ini_path)
        self.write_app_ini_meta(combo, app_ini_path)
        return app_ini_path

    def apply_app_ini_profile_for_car(self, combo: ComboInfo) -> Path:
        combo = combo.normalized()
        app_ini_path = self.get_app_ini_profile_path(combo)
        active_app_ini = self.get_active_app_ini()
        if not app_ini_path.exists():
            raise FileNotFoundError(
                f'App profile not found: {app_ini_path}'
            )
        if not active_app_ini.exists():
            raise FileNotFoundError(
                f'Active file not found: {active_app_ini}'
            )
        shutil.copy2(app_ini_path, active_app_ini)
        return app_ini_path

    def has_app_ini_profile_for_car(self, combo: ComboInfo) -> bool:
        combo = combo.normalized()
        return self.get_app_ini_profile_path(combo).exists()

    def active_app_ini_matches_car_profile(self, combo: ComboInfo) -> bool:
        combo = combo.normalized()
        app_ini_path = self.get_app_ini_profile_path(combo)
        active_app_ini = self.get_active_app_ini()
        if not app_ini_path.exists() or not active_app_ini.exists():
            return False
        return files_equal(app_ini_path, active_app_ini)

    def apply_profile_to_active_ini(self, combo_key: str, renderer_file: str | None = None, grouping_mode: str | None = None) -> dict[str, Any]:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        entry = self.get_entry(combo_key, renderer_file, grouping_mode)
        if not entry:
            raise KeyError(f'Combination not found: {combo_key}')

        ini_path = Path(str(entry.get("profile_ini") or ""))
        active_ini = self.get_active_ini(renderer_file)

        if not ini_path.exists():
            raise FileNotFoundError(f'Profile not found: {ini_path}')
        if not active_ini.exists():
            raise FileNotFoundError(f'Active file not found: {active_ini}')

        shutil.copy2(ini_path, active_ini)
        entry["last_used_at"] = now_str()
        self.save_manifest()
        return entry

    def active_ini_matches_profile(self, combo_key: str, renderer_file: str | None = None, grouping_mode: str | None = None) -> bool:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()
        entry = self.get_entry(combo_key, renderer_file, grouping_mode)
        if not entry:
            return False
        ini_path = Path(str(entry.get("profile_ini") or ""))
        active_ini = self.get_active_ini(renderer_file)
        if not ini_path.exists() or not active_ini.exists():
            return False
        return files_equal(ini_path, active_ini)

    def get_suggestions(self, renderer_file: str | None = None, grouping_mode: str | None = None) -> dict[str, list[str]]:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()

        tracks = set()
        configs = set()
        cars = set()
        track_displays = set()
        car_screens = set()
        series_ids = set()

        for entry in self.list_entries(renderer_file, grouping_mode):
            if entry.get("track_internal"):
                tracks.add(str(entry["track_internal"]))
            if entry.get("track_config"):
                configs.add(str(entry["track_config"]))
            if entry.get("car_path"):
                cars.add(str(entry["car_path"]))
            if entry.get("track_display"):
                track_displays.add(str(entry["track_display"]))
            if entry.get("car_screen"):
                car_screens.add(str(entry["car_screen"]))
            if entry.get("series_id"):
                series_ids.add(str(entry["series_id"]))

        return {
            "track_internal": sorted(tracks),
            "track_config": sorted(configs),
            "car_path": sorted(cars),
            "track_display": sorted(track_displays),
            "car_screen": sorted(car_screens),
            "series_id": sorted(series_ids),
        }

    def delete_profile(self, combo_key: str, renderer_file: str | None = None, grouping_mode: str | None = None) -> bool:
        renderer_file = renderer_file or self.get_selected_renderer()
        grouping_mode = grouping_mode or self.get_selected_grouping()

        with self.lock:
            combos = self.grouping_section(renderer_file, grouping_mode).setdefault("combos", {})
            entry = combos.pop(combo_key, None)
            if entry is None:
                return False

            ini_path = Path(str(entry.get("profile_ini") or ""))
            meta_path = Path(str(entry.get("profile_meta") or ""))
            for path in (ini_path, meta_path):
                if path and path.exists():
                    try:
                        path.unlink()
                    except Exception:
                        pass

            self.save_manifest()
            return True

    def delete_all_profiles(self) -> int:
        removed = 0
        with self.lock:
            renderers = self.manifest.setdefault("renderers", {})
            for renderer_section in renderers.values():
                groupings = renderer_section.setdefault("groupings", {})
                for grouping_section in groupings.values():
                    combos = grouping_section.setdefault("combos", {})
                    for entry in list(combos.values()):
                        ini_path = Path(str(entry.get("profile_ini") or ""))
                        meta_path = Path(str(entry.get("profile_meta") or ""))
                        for path in (ini_path, meta_path):
                            if path and path.exists():
                                try:
                                    path.unlink()
                                except Exception:
                                    pass
                    removed += len(combos)
                    combos.clear()

            self.save_manifest()
        return removed


# ============================================================
# IRACING RUNTIME
# ============================================================
class IRacingRuntime:
    def __init__(self, ir_client=None) -> None:
        self._shared_client = ir_client is not None
        self.ir = ir_client or irsdk.IRSDK(parse_yaml_async=False)

    def reset(self) -> None:
        if self._shared_client:
            return
        try:
            self.ir.shutdown()
        except Exception:
            pass
        self.ir = irsdk.IRSDK(parse_yaml_async=False)

    def is_sim_running(self) -> bool:
        return bool(self.get_sim_processes())

    def get_sim_processes(self) -> list[psutil.Process]:
        procs: list[psutil.Process] = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in SIM_PROCESS_NAMES:
                    procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return procs

    def get_sim_pids(self) -> set[int]:
        return {proc.pid for proc in self.get_sim_processes()}

    def ensure_started(self) -> None:
        if not self.ir.is_initialized:
            try:
                self.ir.startup()
            except Exception:
                pass

    def is_connected(self) -> bool:
        if not self.ir.is_initialized:
            return False
        try:
            return bool(self.ir.is_connected)
        except Exception:
            return False

    def detect_combo(self) -> ComboInfo | None:
        weekend = safe_get(self.ir, "WeekendInfo") or {}
        driver_info = safe_get(self.ir, "DriverInfo") or {}

        combo = ComboInfo()

        if isinstance(weekend, dict):
            combo.track_internal = str(weekend.get("TrackName") or "")
            combo.track_config = str(
                weekend.get("TrackConfigName")
                or weekend.get("TrackConfig")
                or ""
            )
            combo.track_display = str(
                weekend.get("TrackDisplayName")
                or weekend.get("TrackDisplayShortName")
                or ""
            )
            combo.track_display_short = str(weekend.get("TrackDisplayShortName") or "")
            combo.series_id = str(weekend.get("SeriesID") or "")

        if isinstance(driver_info, dict):
            driver_car_idx = driver_info.get("DriverCarIdx")
            drivers = driver_info.get("Drivers") or []
            if isinstance(drivers, list) and driver_car_idx is not None:
                for drv in drivers:
                    if isinstance(drv, dict) and drv.get("CarIdx") == driver_car_idx:
                        combo.car_path = str(drv.get("CarPath") or "")
                        combo.car_screen = str(drv.get("CarScreenName") or "")
                        combo.car_short = str(drv.get("CarScreenNameShort") or "")
                        break

        combo = combo.normalized()
        return combo if combo.is_complete() else None

    def post_wm_close_to_sim_windows(self) -> int:
        if win32con is None or win32gui is None or win32process is None:
            return 0
        target_pids = self.get_sim_pids()
        if not target_pids:
            return 0

        hwnds: list[int] = []

        def callback(hwnd: int, _: object) -> bool:
            try:
                if not win32gui.IsWindow(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in target_pids:
                    hwnds.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)

        sent = 0
        for hwnd in hwnds:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                sent += 1
            except Exception:
                pass
        return sent


# ============================================================
# PASSIVE BACKGROUND OBSERVER
# ============================================================
class ProfileContextObserver:
    """Continuously recognize the live car/track without performing actions.

    This worker is deliberately separate from :class:`MonitorService`.  It is
    always allowed to read the shared telemetry and warm the current context,
    while closing the simulator, applying INIs and autosaving remain behind the
    user's explicit automatic-monitor switch.
    """

    def __init__(
        self,
        runtime: IRacingRuntime,
        on_change: Callable[[ComboInfo | None], None] | None = None,
        *,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self.runtime = runtime
        self.on_change = on_change
        self.poll_interval = max(0.05, float(poll_interval))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._current_combo: ComboInfo | None = None
        self._signature: tuple[str, ...] | None = None

    @property
    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="DominantControl-ProfileContext",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.thread = None
        self.runtime.reset()

    def current_combo(self) -> ComboInfo | None:
        with self._lock:
            combo = self._current_combo
            if combo is None:
                return None
            return ComboInfo(**vars(combo))

    @staticmethod
    def _combo_signature(combo: ComboInfo | None) -> tuple[str, ...] | None:
        if combo is None:
            return None
        return (
            combo.track_internal,
            combo.track_config,
            combo.track_display,
            combo.track_display_short,
            combo.car_path,
            combo.car_screen,
            combo.car_short,
            combo.series_id,
        )

    def _publish(self, combo: ComboInfo | None) -> None:
        signature = self._combo_signature(combo)
        with self._lock:
            if signature == self._signature:
                return
            self._signature = signature
            self._current_combo = combo
        if self.on_change is not None:
            try:
                self.on_change(combo)
            except Exception:
                pass

    def _run(self) -> None:
        while not self.stop_event.is_set():
            combo: ComboInfo | None = None
            try:
                self.runtime.ensure_started()
                if self.runtime.is_connected():
                    combo = self.runtime.detect_combo()
            except Exception:
                combo = None
            self._publish(combo)
            self.stop_event.wait(self.poll_interval)


# ============================================================
# MONITOR THREAD
# ============================================================
class MonitorService:
    def __init__(
        self,
        manager: ProfileManager,
        log_func,
        on_session_closed: Callable[[], None] | None = None,
        confirm_new_combo_save: Callable[[ComboInfo], bool] | None = None,
        runtime: IRacingRuntime | None = None,
    ) -> None:
        self.manager = manager
        self.log_func = log_func
        self.on_session_closed = on_session_closed
        self.confirm_new_combo_save = confirm_new_combo_save
        self.runtime = runtime or IRacingRuntime()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.session: dict[str, Any] | None = None
        self.last_sim_running = False

    def log(self, text: str) -> None:
        self.log_func(f"[{now_str()}] {text}")

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="IRacingMonitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None
        # Disabling automation cancels every pending close/apply/autosave.  The
        # separate passive observer keeps recognizing the context safely.
        self.session = None
        self.last_sim_running = False
        self.runtime.reset()

    def _run(self) -> None:
        self.log(
            f'Automatic monitor started | renderer: {self.manager.get_selected_renderer()} | grouping: {self.manager.get_selected_grouping()}'
        )

        while not self.stop_event.is_set():
            renderer_file = self.manager.get_selected_renderer()
            grouping_mode = self.manager.get_selected_grouping()
            active_ini = self.manager.get_active_ini(renderer_file)

            sim_running = self.runtime.is_sim_running()

            if sim_running:
                self.runtime.ensure_started()

            connected = self.runtime.is_connected()

            if sim_running and connected:
                combo = self.runtime.detect_combo()
                if combo is not None:
                    combo_key = combo.combo_key(grouping_mode)
                    context_changed = (
                        self.session is None
                        or self.session.get("combo_key") != combo_key
                        or self.session.get("renderer_file") != renderer_file
                        or self.session.get("grouping_mode") != grouping_mode
                    )
                    if context_changed:
                        # Registration updates the on-disk manifest.  Do it once
                        # per context instead of four times per second.
                        selected_entry = self.manager.register_combo(
                            combo, renderer_file, grouping_mode
                        )
                        resolved = self.manager.resolve_best_entry(combo, renderer_file)
                        resolved_grouping = grouping_mode
                        resolved_entry = selected_entry
                        resolved_combo_key = combo_key
                        if resolved:
                            resolved_grouping, resolved_entry = resolved
                            resolved_combo_key = str(
                                resolved_entry.get("combo_key")
                                or combo.combo_key(resolved_grouping)
                            )

                        self.session = {
                            "combo_key": combo_key,
                            "resolved_combo_key": resolved_combo_key,
                            "combo": combo,
                            "renderer_file": renderer_file,
                            "grouping_mode": grouping_mode,
                            "resolved_grouping_mode": resolved_grouping,
                            "close_scheduled_at": None,
                            "wm_close_sent": False,
                            "profile_apply_pending": False,
                            "new_combo_for_selected_grouping": not Path(str(selected_entry.get("profile_ini") or "")).exists(),
                        }
                        self.log(
                            f'Context detected: {combo.label()} | renderer: {renderer_file} | grouping: {grouping_mode}'
                        )

                        profile_exists = Path(str(resolved_entry.get("profile_ini") or "")).exists()
                        enabled = bool(resolved_entry.get("enabled", True))

                        if profile_exists and enabled:
                            renderer_matches = self.manager.active_ini_matches_profile(
                                resolved_combo_key, renderer_file, resolved_grouping
                            )
                            has_app_profile = self.manager.has_app_ini_profile_for_car(combo)
                            app_matches = self.manager.active_app_ini_matches_car_profile(combo) if has_app_profile else True

                            if renderer_matches and app_matches:
                                self.log("The right profile is already active")
                            else:
                                self.session["close_scheduled_at"] = time.time() + PRE_CLOSE_DELAY
                                mismatch_reasons: list[str] = []
                                if not renderer_matches:
                                    mismatch_reasons.append(active_ini.name)
                                if not app_matches:
                                    mismatch_reasons.append("app.ini")
                                mismatch_text = " + ".join(mismatch_reasons) if mismatch_reasons else active_ini.name
                                self.log(
                                    f'The saved profile ({resolved_grouping}) differs from the active file ({mismatch_text}). Close scheduled in {PRE_CLOSE_DELAY:.0f}s'
                                )
                        else:
                            self.log("First use of this grouping, or the profile is off; nothing was closed automatically")

                    if self.session is not None:
                        close_due = self.session.get("close_scheduled_at")
                        wm_close_sent = bool(self.session.get("wm_close_sent"))

                        if close_due is not None and not wm_close_sent and time.time() >= float(close_due):
                            sent = self.runtime.post_wm_close_to_sim_windows()
                            if sent > 0:
                                self.session["wm_close_sent"] = True
                                self.session["profile_apply_pending"] = True
                                self.log(f'Close command sent to {sent} simulator window(s)')
                            else:
                                self.log("No simulator window was found")
                                self.session["close_scheduled_at"] = None

            if self.last_sim_running and not sim_running:
                self.log("Simulator closed")

                if self.session is not None:
                    combo = self.session["combo"]
                    session_renderer = str(self.session.get("renderer_file") or renderer_file)
                    session_grouping = str(self.session.get("grouping_mode") or grouping_mode)
                    session_combo_key = str(self.session.get("combo_key"))
                    resolved_grouping = str(self.session.get("resolved_grouping_mode") or session_grouping)
                    resolved_combo_key = str(self.session.get("resolved_combo_key") or session_combo_key)
                    session_active_ini = self.manager.get_active_ini(session_renderer)
                    session_active_app_ini = self.manager.get_active_app_ini()

                    self.log(f'Waiting for {session_active_ini.name} to settle...')
                    stable = wait_for_file_stable(
                        session_active_ini,
                        stable_seconds=FILE_STABLE_SECONDS,
                        timeout=POST_CLOSE_SETTLE_TIMEOUT,
                    )

                    if not stable:
                        self.log("Warning: the file did not settle in the expected time")
                    else:
                        self.log("File settled")

                    if session_active_app_ini.exists():
                        self.log(f'Waiting for {session_active_app_ini.name} to settle...')
                        app_stable = wait_for_file_stable(
                            session_active_app_ini,
                            stable_seconds=FILE_STABLE_SECONDS,
                            timeout=POST_CLOSE_SETTLE_TIMEOUT,
                        )
                        if not app_stable:
                            self.log("Warning: app.ini did not settle in the expected time")
                        else:
                            self.log("app.ini settled")
                    else:
                        self.log("app.ini not found; the related actions will be skipped")

                    if bool(self.session.get("profile_apply_pending")):
                        try:
                            self.manager.apply_profile_to_active_ini(resolved_combo_key, session_renderer, resolved_grouping)
                            self.log("The right profile was written to the active INI; reopen the simulator whenever you like")
                        except Exception as e:
                            self.log(f'Error applying the saved profile: {e}')
                        try:
                            app_path = self.manager.apply_app_ini_profile_for_car(combo)
                            self.log(f'car app.ini applied: {app_path.name}')
                        except FileNotFoundError:
                            self.log("No app.ini saved for this car yet")
                        except Exception as e:
                            self.log(f"Error applying the car's app.ini: {e}")
                    else:
                        entry = self.manager.get_entry(session_combo_key, session_renderer, session_grouping)
                        autosave = True if entry is None else bool(entry.get("autosave_on_manual_close", True))
                        should_save = autosave
                        is_new_combo = bool(self.session.get("new_combo_for_selected_grouping", False))
                        if should_save and is_new_combo and self.manager.get_bool_setting("prompt_new_combo_save", True):
                            if self.confirm_new_combo_save is not None:
                                should_save = self.confirm_new_combo_save(combo)
                        if should_save:
                            try:
                                self.manager.save_active_ini_as_profile(combo, session_renderer, session_grouping)
                                self.log("Manual close detected; profile saved")
                            except Exception as e:
                                self.log(f'Error saving the profile after the simulator closed: {e}')
                            save_app_ini_global = self.manager.get_bool_setting("save_app_ini_on_autosave_global", True)
                            save_app_ini_entry = True if entry is None else bool(entry.get("save_app_ini_on_autosave", True))
                            if save_app_ini_global and save_app_ini_entry:
                                try:
                                    app_path = self.manager.save_active_app_ini_for_car(combo)
                                    self.log(f'car app.ini saved: {app_path.name}')
                                except FileNotFoundError:
                                    self.log("app.ini not found; the car's file was not saved")
                                except Exception as e:
                                    self.log(f"Error saving the car's app.ini: {e}")
                            else:
                                self.log("Saving the car's app.ini is off in the preferences")
                        else:
                            if is_new_combo and autosave:
                                self.log("Saving the new context was cancelled by the user")
                            else:
                                self.log("Automatic saving is off; the profile was not updated")

                self.session = None
                self.runtime.reset()
                if self.on_session_closed is not None:
                    self.on_session_closed()

            self.last_sim_running = sim_running
            time.sleep(POLL_INTERVAL)

        self.log("Automatic monitor stopped")


# ============================================================
# End of UI-independent Profiles engine
# ============================================================
