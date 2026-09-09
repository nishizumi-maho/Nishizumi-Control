"""
Frameless, semi-transparent PySide6 overlay window.

Two states:

* **locked** - click-through, no chrome. The panel is a pure heads-up display
  and the mouse belongs entirely to iRacing.
* **unlocked** - a header bar appears with a title, a mode button, a lock
  button and a close button; the whole panel can be dragged.

Click-through is *not* left to ``Qt.WA_TransparentForMouseEvents`` alone. That
attribute only reaches the native window when it is created, so toggling it on
a window that is already on screen changed nothing on Windows and the overlay
could never be dragged. ``overlay.native`` flips ``WS_EX_TRANSPARENT`` on the
live HWND instead, and the state is re-applied on every show because a
hide/show cycle can recreate the native window.

Updates arrive from the polling timer on the GUI thread; hotkey callbacks
arrive on the ``keyboard`` library's own thread and cross over through Qt
signals, which is thread-safe.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from overlay import native
from overlay.styles import stylesheet

logger = logging.getLogger("sr_estimator.overlay")

try:  # optional: the app must still run when the hook library is unavailable
    import keyboard
except Exception as _exc:  # pragma: no cover - depends on the platform
    keyboard = None
    logger.warning("The 'keyboard' module is unavailable (%s); global hotkeys are off", _exc)


class GlobalHotkeys(QObject):
    """System-wide hotkeys, so they work while iRacing has focus.

    ``registered`` reports how many actually bound. Zero means the user has no
    keyboard control at all - main.py reacts to that by leaving the overlay
    unlocked, so there is always *some* way to move and close it.
    """

    toggle_overlay = Signal()
    toggle_lock = Signal()
    cycle_mode = Signal()

    def __init__(self, toggle_overlay_key: str, toggle_lock_key: str, cycle_mode_key: str) -> None:
        super().__init__()
        self._registered: list[str] = []
        self._bind(toggle_overlay_key, self.toggle_overlay.emit)
        self._bind(toggle_lock_key, self.toggle_lock.emit)
        self._bind(cycle_mode_key, self.cycle_mode.emit)

    @property
    def registered(self) -> list[str]:
        return list(self._registered)

    @property
    def available(self) -> bool:
        return bool(self._registered)

    def _bind(self, key: str, callback) -> None:
        if keyboard is None:
            return
        try:
            keyboard.add_hotkey(key, callback)
            self._registered.append(key)
            logger.info("Hotkey registered: %s", key)
        except Exception as exc:
            # Typically iRacing running elevated while this app is not: Windows
            # refuses to deliver the hook across privilege levels.
            logger.error("Could not register hotkey '%s': %s", key, exc)

    def shutdown(self) -> None:
        if keyboard is None:
            return
        for key in self._registered:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass
        self._registered.clear()


class OverlayWindow(QWidget):
    """The panel itself. Emits intent; main.py decides what it means."""

    close_requested = Signal()
    hide_requested = Signal()
    lock_changed = Signal(bool)
    mode_cycle_requested = Signal()
    position_changed = Signal()

    def __init__(self, opacity: float, scale: float, locked: bool = False) -> None:
        super().__init__()
        self._locked = True          # real value set by set_locked() below
        self._drag_offset: Optional[QPoint] = None
        self._click_through_failed = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                       # keeps it off the taskbar / alt-tab
            | Qt.WindowDoesNotAcceptFocus   # never steals focus from the sim
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(opacity)
        self.setWindowTitle("Nishizumi SR")

        self._build_ui()
        self.set_scale(scale)
        self.set_locked(locked)

    # ------------------------------------------------------------------ ui --
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._panel = QFrame(self)
        self._panel.setObjectName("panel")
        root.addWidget(self._panel)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(12, 8, 12, 10)
        panel_layout.setSpacing(6)

        self._header = self._build_header()
        panel_layout.addWidget(self._header)

        self._label = QLabel(self._panel)
        self._label.setObjectName("content")
        self._label.setTextFormat(Qt.RichText)
        self._label.setWordWrap(False)
        # Let clicks fall through to the window so the panel drags from anywhere.
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        panel_layout.addWidget(self._label)

    def _build_header(self) -> QWidget:
        header = QWidget(self._panel)
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)

        title = QLabel("NISHIZUMI SR", header)
        title.setObjectName("title")
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        title.setToolTip("Drag anywhere on the panel to move it")
        layout.addWidget(title)
        layout.addStretch(1)

        self._mode_button = self._make_button(
            header, "mode", "headerButton", "Cycle compact / normal / detailed",
            self.mode_cycle_requested.emit,
        )
        self._lock_button = self._make_button(
            header, "lock", "headerButton",
            "Lock: make the overlay click-through so it never blocks the sim",
            self.toggle_lock,
        )
        self._close_button = self._make_button(
            header, "✕", "closeButton", "Close Nishizumi SR",
            self.close_requested.emit,
        )
        for button in (self._mode_button, self._lock_button, self._close_button):
            layout.addWidget(button)
        return header

    @staticmethod
    def _make_button(parent: QWidget, text: str, object_name: str, tooltip: str, slot) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName(object_name)
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFlat(True)
        button.clicked.connect(slot)
        return button

    def set_scale(self, scale: float) -> None:
        font = QFont("Consolas")
        font.setPointSizeF(max(6.0, 10.0 * scale))
        self._label.setFont(font)
        self.adjustSize()

    def set_content(self, html: str) -> None:
        if self._label.text() == html:
            return  # nothing changed: skip the relayout (this runs 10x/second)
        self._label.setText(html)
        self.adjustSize()

    # ---------------------------------------------------------------- lock --
    @property
    def locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        locked = bool(locked)
        self._locked = locked

        # Qt-side attribute, so child widgets behave, plus the native style,
        # which is what Windows actually hit-tests against.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, locked)
        self._header.setVisible(not locked)
        self._lock_button.setText("lock" if not locked else "unlock")
        self.setStyleSheet(stylesheet(locked))
        self._apply_click_through()
        self.adjustSize()

        logger.info("Overlay %s", "locked (click-through)" if locked else "unlocked (draggable)")
        self.lock_changed.emit(locked)

    def toggle_lock(self) -> None:
        self.set_locked(not self._locked)

    def _apply_click_through(self) -> None:
        """Push the lock state down to the native window.

        Called on every lock change and again on every show, because hiding and
        re-showing can recreate the HWND and reset its extended style.
        """
        if not native.IS_WINDOWS:
            return
        if self.windowHandle() is None:
            # Not realised yet. Forcing it here with winId() would create the
            # native window before show() and is not needed: showEvent() applies
            # the state as soon as there is a window to apply it to.
            return
        try:
            hwnd = int(self.winId())
        except Exception as exc:  # pragma: no cover - Windows-only path
            logger.error("Could not obtain the native window handle: %s", exc)
            return
        if not hwnd:
            return

        if native.set_click_through(hwnd, self._locked):
            self._click_through_failed = False
            return

        if not self._click_through_failed:
            # Log once per failure streak rather than on every toggle.
            self._click_through_failed = True
            logger.error(
                "Could not change WS_EX_TRANSPARENT on the overlay window; "
                "click-through may not match the lock state"
            )

    def toggle_visibility(self) -> None:
        self.setVisible(not self.isVisible())
        logger.info("Overlay %s", "shown" if self.isVisible() else "hidden")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_click_through()

    # ------------------------------------------------------- position/drag --
    def move_onto_screen(self, x: int, y: int) -> None:
        """Move to (x, y), pulled back onto a real screen if it lands outside.

        A saved position from a monitor that is no longer attached would
        otherwise put the overlay somewhere invisible, with no way to get it
        back short of editing settings.json by hand.
        """
        self.adjustSize()
        size = self.size()
        target = QPoint(int(x), int(y))

        screen = QGuiApplication.screenAt(target) or QGuiApplication.primaryScreen()
        if screen is None:
            self.move(target)
            return

        area = screen.availableGeometry()
        max_x = area.right() - size.width() + 1
        max_y = area.bottom() - size.height() + 1
        clamped = QPoint(
            max(area.left(), min(target.x(), max(area.left(), max_x))),
            max(area.top(), min(target.y(), max(area.top(), max_y))),
        )
        if clamped != target:
            logger.warning(
                "Saved overlay position (%s, %s) is off-screen; moved to (%s, %s)",
                target.x(), target.y(), clamped.x(), clamped.y(),
            )
        self.move(clamped)

    def mousePressEvent(self, event) -> None:
        if not self._locked and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._locked and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            self.position_changed.emit()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------- context menu --
    def contextMenuEvent(self, event) -> None:
        """Right-click menu - a second route to every control.

        Only reachable while unlocked; a click-through window never receives
        the event, which is exactly what locking is for.
        """
        if self._locked:
            return
        menu = QMenu(self)
        menu.setStyleSheet(stylesheet(self._locked))

        lock_action = QAction("Lock (click-through)", menu)
        lock_action.triggered.connect(self.toggle_lock)
        mode_action = QAction("Cycle display mode", menu)
        mode_action.triggered.connect(self.mode_cycle_requested.emit)
        hide_action = QAction("Hide overlay", menu)
        hide_action.triggered.connect(self.hide_requested.emit)
        quit_action = QAction("Quit Nishizumi SR", menu)
        quit_action.triggered.connect(self.close_requested.emit)

        menu.addAction(lock_action)
        menu.addAction(mode_action)
        menu.addSeparator()
        menu.addAction(hide_action)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())
        event.accept()

    def closeEvent(self, event) -> None:
        """Alt+F4 and friends mean "quit", not "hide silently".

        Without this the window would vanish while the process kept running,
        which is the worst of both worlds: no overlay, and something still
        holding the hotkeys.
        """
        event.ignore()
        self.close_requested.emit()
