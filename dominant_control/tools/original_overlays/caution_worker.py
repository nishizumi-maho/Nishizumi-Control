"""Render the original Caution Overlay from the integrated caution counters.

The window, its palette and its resize behaviour come from the vendored
``Nishizumi_CautionOverlay.py`` untouched. What this module adds is what the
standalone application does in its own ``main`` and cannot do here: take the
numbers from the bridge instead of from a pyirsdk connection of its own, keep
the remembered geometry with the other tools' state, and speak Portuguese.

The counters themselves are deliberately *not* recomputed here. They belong to
``tools/caution/engine.py``, which runs on the shared telemetry whether this
window is open or not — so closing the overlay mid-race and reopening it shows
the session's real caution count rather than restarting from zero.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .client import OverlayBridgeClient
from .ptbr import localize_qt_tree

POLL_INTERVAL_MS = 100
WINDOW_TITLE = "Nishizumi Yellow Flags"

_window_class: type | None = None


def vendored() -> Any:
    """The preserved original module. Imported lazily: it pulls in Qt."""
    from dominant_control.vendor.nishizumi_tools import Nishizumi_CautionOverlay

    return Nishizumi_CautionOverlay


def status_color(caution: Any, status: str) -> str:
    """The dot colour for one engine status, from the original's palette."""
    if status == "caution":
        return caution.COLOR_YELLOW
    if status == "green":
        return caution.COLOR_GREEN
    return caution.COLOR_IDLE


def overlay_window_class() -> type:
    """The vendored window, fed by the bridge and with a Portuguese menu.

    Built here rather than at import time so that importing this module — which
    the worker dispatcher does before it knows which renderer is wanted — does
    not drag Qt in with it.
    """
    global _window_class
    if _window_class is not None:
        return _window_class

    caution = vendored()

    class BridgeOnlyWorker(caution.QThread):
        """Never touch iRacing shared memory from this render-only process.

        The vendored window builds and starts a telemetry thread of its own;
        this replaces it, because the one connection to the simulator belongs
        to the main application and a second reader would fight it for the
        session string. It emits nothing: every number arrives over the bridge.
        """

        sample_ready = caution.Signal(object)

        def __init__(self, demo: bool = False, parent: Any = None) -> None:
            super().__init__(parent)

        def stop(self) -> None:
            return None

        def run(self) -> None:  # noqa: D102 - QThread entry point
            return None

    caution.TelemetryWorker = BridgeOnlyWorker

    class BridgeOverlayWindow(caution.OverlayWindow):
        """``OverlayWindow`` whose counters and reset live in the main app."""

        def __init__(self, client: Any = None, settings: Any = None) -> None:
            self._client = client
            super().__init__()
            if settings is not None:
                self.use_settings(settings)
            self.setWindowTitle(WINDOW_TITLE)

        def use_settings(self, settings: Any) -> None:
            """Adopt a store other than the one the original picked for itself."""
            self._settings = settings
            self._set_locked(bool(settings.value("locked", False, type=bool)))
            self._restore_geometry()

        def apply_state(self, state: dict[str, Any]) -> None:
            """Show one published state; the tracker here is only a mirror."""
            tracker = self._tracker
            tracker.cautions = _count(state.get("cautions"))
            tracker.caution_laps = _count(state.get("caution_laps"))
            tracker.under_caution = bool(state.get("under_caution", False))
            self._refresh_counters()
            self._set_status(
                status_color(caution, str(state.get("status") or "")),
                str(state.get("status_text") or "WAITING FOR IRACING"),
            )
            if tracker.under_caution != self._under_caution:
                # The panel border turns yellow under caution, so the repaint
                # only happens when the state really flips.
                self._under_caution = tracker.under_caution
                self.update()

        def request_reset(self) -> None:
            """Zero the counters where they are actually kept."""
            self._tracker.reset()
            self._refresh_counters()
            if self._client is not None:
                self._client.command("reset")

        def _show_menu(self, position: Any) -> None:
            """The original menu, in Portuguese, with reset sent to the engine.

            Reimplemented rather than translated in place because a QMenu is
            built and executed inside this one call, so there is no built tree
            for ``localize_qt_tree`` to walk before the user sees it.
            """
            menu = caution.QMenu(self)
            lock_action = menu.addAction("Lock position")
            lock_action.setCheckable(True)
            lock_action.setChecked(self._locked)
            reset_action = menu.addAction("Zerar contadores")
            reset_size_action = menu.addAction("Default size")
            menu.addSeparator()
            close_action = menu.addAction("Close the overlay")

            chosen = menu.exec(self.mapToGlobal(position))
            if chosen is lock_action:
                self._set_locked(not self._locked)
            elif chosen is reset_action:
                self.request_reset()
            elif chosen is reset_size_action:
                self.resize(caution.DEFAULT_SIZE)
                self._save_geometry()
            elif chosen is close_action:
                self.close()

    _window_class = BridgeOverlayWindow
    return _window_class


def tools_settings(caution: Any, data_dir: str) -> Any:
    """The store for the remembered position, size and lock state.

    The standalone application lets Qt choose where those go, which on Windows
    is the per-user registry under its own organisation name. Inside Private
    Control they belong in the tools data folder, next to every other tool's
    state.

    Redirecting the path is not enough on its own: the vendored window builds
    its ``QSettings`` from the organisation/application pair alone, and which
    *format* that resolves to is Qt's decision rather than ours — asking for
    ``IniFormat`` by default is honoured on some builds and quietly ignored on
    others, which would leave the file wherever Qt felt like putting it. So the
    store is built here with the format and scope named explicitly, and handed
    to the window.

    Returns ``None`` when no data folder was given, leaving the original's own
    choice of location in place.
    """
    if not data_dir:
        return None
    settings = caution.QSettings
    settings.setPath(settings.Format.IniFormat, settings.Scope.UserScope, data_dir)
    return settings(
        settings.Format.IniFormat,
        settings.Scope.UserScope,
        caution.ORG_NAME,
        caution.SETTINGS_APP,
    )


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def run() -> int:
    caution = vendored()

    client = OverlayBridgeClient("caution")
    window_class = overlay_window_class()

    app = caution.QApplication(sys.argv)
    app.setApplicationName("Nishizumi Caution Overlay — Dominant Control")
    app.setOrganizationName("NishizumiTools")
    app.setQuitOnLastWindowClosed(True)
    font = app.font()
    font.setFamily("Segoe UI" if sys.platform == "win32" else font.family())
    font.setStyleStrategy(caution.QFont.PreferAntialias)
    app.setFont(font)

    # After the application exists: a QSettings built before it cannot pick up
    # the organisation and format defaults Qt installs with it.
    settings = tools_settings(
        caution, os.environ.get("DOMINANT_CONTROL_TOOLS_DATA_DIR", "")
    )
    window = window_class(client, settings)
    window.show()
    localize_qt_tree(window, caution)

    last_show_generation = -1

    def poll() -> None:
        nonlocal last_show_generation
        response = client.state()
        if not response:
            return
        window.apply_state(response.get("state") or {})
        localize_qt_tree(window, caution)

        show_generation = int(response.get("show_generation", 0))
        if show_generation != last_show_generation:
            last_show_generation = show_generation
            window.show()
            window.raise_()

    timer = caution.QTimer(window)
    timer.timeout.connect(poll)
    timer.start(POLL_INTERVAL_MS)
    poll()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(run())
