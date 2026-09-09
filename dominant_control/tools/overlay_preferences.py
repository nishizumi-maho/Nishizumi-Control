"""Small persistent store shared by every integrated overlay."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..paths import tools_data_dir


class OverlayPreferenceStore:
    """Remember only whether each overlay should reopen on the next launch."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (tools_data_dir() / "overlay_visibility.json")
        self._lock = threading.RLock()
        self._overlays: dict[str, dict[str, bool]] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        raw = payload.get("overlays", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            return
        clean: dict[str, dict[str, bool]] = {}
        for raw_tool, raw_states in raw.items():
            tool = str(raw_tool or "").strip()
            if not tool or not isinstance(raw_states, dict):
                continue
            clean[tool] = {
                str(name): bool(value)
                for name, value in raw_states.items()
                if str(name).strip()
            }
        self._overlays = clean

    def get(self, tool_id: str, overlay_id: str, default: bool = False) -> bool:
        with self._lock:
            return bool(
                self._overlays.get(str(tool_id), {}).get(
                    str(overlay_id), default
                )
            )

    def set(self, tool_id: str, overlay_id: str, visible: bool) -> None:
        tool = str(tool_id or "").strip()
        overlay = str(overlay_id or "").strip()
        if not tool or not overlay:
            return
        value = bool(visible)
        with self._lock:
            states = self._overlays.setdefault(tool, {})
            if states.get(overlay) == value:
                return
            states[overlay] = value
            self._save_locked()

    def snapshot(self) -> dict[str, dict[str, bool]]:
        with self._lock:
            return {
                tool: dict(states) for tool, states in self._overlays.items()
            }

    def _save_locked(self) -> None:
        payload: dict[str, Any] = {
            "version": self.SCHEMA_VERSION,
            "overlays": self._overlays,
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


_STORE: OverlayPreferenceStore | None = None
_STORE_LOCK = threading.Lock()


def get_overlay_preferences() -> OverlayPreferenceStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = OverlayPreferenceStore()
        return _STORE


__all__ = ["OverlayPreferenceStore", "get_overlay_preferences"]
