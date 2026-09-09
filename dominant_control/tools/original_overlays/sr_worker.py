"""Render the original Nishizumi SR overlay from the integrated estimate.

The window, its stylesheet and every formatter come from the vendored sources
(``vendor/nishizumi_sr``, byte-for-byte). What this module adds is the three
things the standalone application does in its own ``main.py`` and cannot do
here: read the estimate from the bridge instead of from a pyirsdk connection of
its own, speak Portuguese, and drag smoothly.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..sr.vendored import bootstrap, data_dir
from .client import OverlayBridgeClient
from .ptbr import translate_sr_html

MODES = ("compact", "normal", "detailed")
POLL_INTERVAL_MS = 100

# How long a drag may hold back a content update. Long enough that a whole
# reposition happens without a single relayout, short enough that the numbers
# cannot go visibly stale — and it doubles as the recovery path if a mouse
# release never arrives (the sim grabbing the pointer mid-drag, for instance).
MAX_DEFERRED_SECONDS = 1.5

DEFAULT_OVERLAY_STATE: dict[str, Any] = {
    "x": 20,
    "y": 20,
    "mode": "normal",
    "opacity": 0.85,
    "scale": 1.0,
}

_window_class: type | None = None


def state_path() -> Path:
    """Where the overlay remembers its position and display mode."""
    return data_dir() / "overlay.json"


def load_overlay_state() -> dict[str, Any]:
    """The remembered position/mode, falling back to the shipped defaults."""
    values = dict(DEFAULT_OVERLAY_STATE)
    try:
        stored = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return values
    if not isinstance(stored, dict):
        return values
    for key, fallback in DEFAULT_OVERLAY_STATE.items():
        try:
            values[key] = type(fallback)(stored.get(key, fallback))
        except (TypeError, ValueError):
            values[key] = fallback
    if values["mode"] not in MODES:
        values["mode"] = DEFAULT_OVERLAY_STATE["mode"]
    return values


def save_overlay_state(values: dict[str, Any]) -> None:
    path = state_path()
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        # Losing the remembered position is never worth taking the overlay down.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def overlay_window_class() -> type:
    """The vendored overlay window, with the drag fix and Portuguese chrome.

    Built here rather than at import time because the vendored modules only
    become importable after :func:`bootstrap`.
    """
    global _window_class
    if _window_class is not None:
        return _window_class

    bootstrap()
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QLabel, QMenu
    from overlay.styles import stylesheet
    from overlay.window import OverlayWindow

    class SmoothOverlayWindow(OverlayWindow):
        """``overlay.window.OverlayWindow`` that keeps up with the cursor.

        The vendored file is preserved byte-for-byte (see
        ``vendor/nishizumi_sr/PROVENANCE.md``), so the fix lives here — the same
        place the traction overlay's flicker fix lives.

        **What made dragging slow.** ``set_content`` runs on every poll, ten
        times a second, and each call does three expensive things: it re-parses
        the panel's rich text into a fresh document, re-lays the panel out, and
        calls ``adjustSize()``, which resizes a translucent always-on-top
        window. All of it runs on the GUI thread — the same thread that has to
        answer the mouse moves doing the dragging — so the panel fell behind the
        cursor and changed size underneath it. It only became noticeable once
        the overlay could be dragged at all, which is what the click-through fix
        in the latest release restored.

        **The two changes.** While a drag is in progress the incoming content is
        remembered instead of applied: for those few hundred milliseconds the
        window's only job is to follow the mouse, and the last update is applied
        as soon as the button comes up. Outside a drag, ``adjustSize()`` now
        runs only when the size hint really moved — most updates just change
        digits inside the same layout, so the window stops resizing itself ten
        times a second for nothing.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._pending_html: str | None = None
            self._deferred_since = 0.0
            super().__init__(*args, **kwargs)
            self.setWindowTitle("Nishizumi Safety Rating")
            self._mode_button.setText("mode")
            self._mode_button.setToolTip("Switch between compact, normal and detailed")
            self._close_button.setToolTip("Close the Safety Rating overlay")
            title = self._header.findChild(QLabel)
            if title is not None:
                title.setToolTip("Drag anywhere on the panel to move it")

        # --------------------------------------------------------- content --
        @property
        def dragging(self) -> bool:
            return self._drag_offset is not None

        def set_content(self, html: str) -> None:
            if self.dragging:
                now = time.monotonic()
                if not self._deferred_since:
                    self._deferred_since = now
                if now - self._deferred_since < MAX_DEFERRED_SECONDS:
                    self._pending_html = html
                    return

            self._pending_html = None
            self._deferred_since = 0.0
            if self._label.text() == html:
                return
            self._label.setText(html)
            # setText invalidated the layout, so this hint is the fresh one.
            if self.sizeHint() != self.size():
                self.adjustSize()

        def flush_content(self) -> None:
            """Apply whatever a drag held back."""
            pending = self._pending_html
            self._pending_html = None
            self._deferred_since = 0.0
            if pending is not None:
                self.set_content(pending)

        def mouseReleaseEvent(self, event) -> None:
            was_dragging = self.dragging
            super().mouseReleaseEvent(event)
            if was_dragging:
                self.flush_content()

        # ---------------------------------------------------------- chrome --
        def set_locked(self, locked: bool) -> None:
            super().set_locked(locked)
            self._lock_button.setText("lock" if not self._locked else "unlock")
            self._lock_button.setToolTip(
                "Lock: the overlay stops taking clicks and stays out of the way"
                if not self._locked
                else "Unlock it to move or close the overlay"
            )

        def contextMenuEvent(self, event) -> None:
            """Portuguese menu, and without the vendored "Hide overlay".

            Hiding leaves the tab's checkbox ticked with nothing on screen; the
            standalone app recovers from that through a tray icon this renderer
            does not have. Closing is the honest option here, and the checkbox
            follows it.
            """
            if self._locked:
                return
            menu = QMenu(self)
            menu.setStyleSheet(stylesheet(self._locked))
            for label, slot in (
                ("Lock (clicks pass through)", self.toggle_lock),
                ("Switch display mode", self.mode_cycle_requested.emit),
                (None, None),
                ("Close the overlay", self.close_requested.emit),
            ):
                if label is None:
                    menu.addSeparator()
                    continue
                action = QAction(label, menu)
                action.triggered.connect(slot)
                menu.addAction(action)
            menu.exec(event.globalPos())
            event.accept()

    _window_class = SmoothOverlayWindow
    return _window_class


def render(widgets: Any, estimate_type: type, state: dict[str, Any], mode: str) -> str:
    """Turn one published state into the panel's HTML, in Portuguese."""
    raw = state.get("estimate")
    status = str(state.get("status") or "waiting")
    if not isinstance(raw, dict):
        return widgets.format_waiting(
            str(state.get("message") or "Waiting for iRacing…")
        )

    try:
        estimate = estimate_type(**raw)
    except TypeError:
        return widgets.format_waiting("Waiting for the session…")

    if status == "practice":
        html = widgets.format_practice_notice(estimate)
    elif status == "complete":
        html = widgets.format_session_complete(estimate.session_delta, None, None)
    elif mode == "compact":
        html = widgets.format_compact(estimate)
    elif mode == "detailed":
        html = widgets.format_detailed(
            estimate,
            str(state.get("session_type") or "unknown"),
            str(state.get("track") or ""),
        )
    else:
        html = widgets.format_normal(estimate)
    return translate_sr_html(html)


def run() -> int:
    bootstrap()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from overlay import widgets
    from sr.calculator import SREstimate

    client = OverlayBridgeClient("sr")
    stored = load_overlay_state()
    mode_index = MODES.index(stored["mode"])
    last_html: str | None = None
    last_show_generation = -1
    last_lock_generation = -1

    app = QApplication(sys.argv)
    app.setApplicationName("Nishizumi Safety Rating — Dominant Control")
    app.setOrganizationName("NishizumiTools")
    app.setQuitOnLastWindowClosed(False)

    window = overlay_window_class()(
        opacity=float(stored["opacity"]),
        scale=float(stored["scale"]),
        locked=False,
    )
    window.set_content(widgets.format_waiting("Waiting for iRacing…"))
    window.move_onto_screen(int(stored["x"]), int(stored["y"]))
    window.show()

    def persist() -> None:
        position = window.pos()
        stored["x"] = int(position.x())
        stored["y"] = int(position.y())
        stored["mode"] = MODES[mode_index]
        save_overlay_state(stored)

    def cycle_mode() -> None:
        nonlocal mode_index, last_html
        mode_index = (mode_index + 1) % len(MODES)
        last_html = None
        persist()

    def on_lock_changed(locked: bool) -> None:
        # The tab owns the remembered lock state, so its checkbox has to hear
        # about the overlay's own lock button and right-click menu.
        client.command("set_lock", locked=bool(locked))

    window.position_changed.connect(persist)
    window.mode_cycle_requested.connect(cycle_mode)
    window.lock_changed.connect(on_lock_changed)
    window.close_requested.connect(app.quit)
    # Nothing emits this now that the menu closes instead of hiding, but a
    # future vendored build might: an overlay nobody can bring back is worse
    # than one that shut down, because the tab's checkbox still says it is on.
    window.hide_requested.connect(app.quit)

    def poll() -> None:
        nonlocal last_show_generation, last_lock_generation, last_html
        response = client.state()
        if not response:
            return
        state = response.get("state") or {}

        lock_generation = int(state.get("lock_generation", 0))
        if lock_generation != last_lock_generation:
            last_lock_generation = lock_generation
            wanted = bool(state.get("locked", False))
            if wanted != window.locked:
                window.set_locked(wanted)

        html = render(widgets, SREstimate, state, MODES[mode_index])
        if html != last_html:
            last_html = html
            window.set_content(html)

        show_generation = int(response.get("show_generation", 0))
        if show_generation != last_show_generation:
            last_show_generation = show_generation
            window.show()
            window.raise_()

    timer = QTimer(window)
    timer.timeout.connect(poll)
    timer.start(POLL_INTERVAL_MS)
    poll()

    app.aboutToQuit.connect(persist)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(run())
