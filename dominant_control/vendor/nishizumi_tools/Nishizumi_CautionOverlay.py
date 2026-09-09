#!/usr/bin/env python3
"""iRacing Caution Overlay.

A small always-on-top window that shows, for the race session you are in:

    * CAUTIONS      - how many full course cautions have been thrown
    * CAUTION LAPS  - how many laps have been run under those cautions

How the counting works
----------------------
iRacing does not publish "number of cautions" or "caution laps" as telemetry
variables, so both numbers are derived from the live telemetry stream:

* ``SessionFlags`` is a bitmask.  ``caution`` (0x4000) means a full course
  caution is out and ``cautionWaving`` (0x8000) means one is being thrown.
  The ``yellow`` (0x8) / ``yellowWaving`` (0x100) bits are *local* yellows for
  a single corner and are deliberately ignored - counting those would inflate
  the caution count on road courses.
* Every rising edge of the full course caution state counts as one caution.
  The edge is debounced (see ``FLAG_DEBOUNCE_S``) because the flag bits can
  flicker for a couple of frames around a restart.
* Caution laps are counted from the race *leader*, which is how the stat is
  quoted in broadcasts ("5 cautions for 27 laps").  The leader is the car with
  the highest ``CarIdxLapCompleted`` (pace car and spectators excluded), and
  every start/finish crossing the leader makes while the caution is out adds
  one lap.  The restart crossing counts, because iRacing waves the green as
  the leader crosses the line: a caution thrown on lap 10 that goes green on
  lap 14 is 4 caution laps (10, 11, 12 and 13).
* Counting only happens in a Race session that is actually racing, so pace
  laps, practice and qualifying never touch the counters.  The counters reset
  automatically when the session changes (new subsession or new session
  number), and can be reset by hand from the right click menu.

Usage
-----
    python caution_overlay.py            # normal run, connects to iRacing
    python caution_overlay.py --demo     # fake data, for checking the layout

Hover the window to reveal the close button and the "Lock position" checkbox.
Drag it anywhere with the left mouse button; tick "Lock position" to pin it.
Drag the bottom right corner to resize - everything in the panel is sized from
the window height, so the numbers grow with it. Position, size and lock state
are remembered between runs.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

try:  # pyirsdk only works on Windows, where iRacing runs.
    import irsdk
except Exception:  # pragma: no cover - depends on the machine
    irsdk = None


APP_NAME = "iRacing Caution Overlay"
ORG_NAME = "TsNucleusAuto"
SETTINGS_APP = "iRacingCautionOverlay"

# --- iRacing SessionFlags bitmask (values from the iRacing SDK) -------------
FLAG_CHECKERED = 0x00000001
FLAG_GREEN = 0x00000004
FLAG_ONE_LAP_TO_GREEN = 0x00000200
FLAG_CAUTION = 0x00004000
FLAG_CAUTION_WAVING = 0x00008000
FLAG_FULL_COURSE_CAUTION = FLAG_CAUTION | FLAG_CAUTION_WAVING

# --- iRacing SessionState values -------------------------------------------
STATE_INVALID = 0
STATE_GET_IN_CAR = 1
STATE_WARMUP = 2
STATE_PARADE_LAPS = 3
STATE_RACING = 4
STATE_CHECKERED = 5
STATE_COOL_DOWN = 6

# How often the telemetry is polled, in milliseconds.
POLL_INTERVAL_MS = 100
# The caution bits can flicker; ignore anything shorter than this (seconds).
FLAG_DEBOUNCE_S = 1.0
# Session info YAML is only re-read this often (seconds) - it is the expensive
# part of the SDK.
SESSION_INFO_INTERVAL_S = 2.0
# How often to retry connecting while iRacing is not running (seconds).
RECONNECT_INTERVAL_S = 1.0

DEFAULT_SIZE = QSize(258, 120)
MINIMUM_SIZE = QSize(186, 94)
# The panel is laid out in fractions of the window height so the overlay can be
# resized to taste and still look deliberate.  The pixel sizes in _apply_scale
# are the ones that produce the default look at this height.
SCALE_REFERENCE_HEIGHT = 120.0

COLOR_PANEL = QColor(11, 12, 15, 220)
COLOR_BORDER = QColor(255, 255, 255, 28)
COLOR_BORDER_CAUTION = QColor(245, 197, 24, 190)
COLOR_TEXT = "#f4f5f7"
COLOR_MUTED = "#8b9199"
COLOR_GREEN = "#3ddc84"
COLOR_YELLOW = "#f5c518"
COLOR_IDLE = "#6c737c"


@dataclass
class Sample:
    """One snapshot of the telemetry the overlay cares about."""

    connected: bool = False
    session_key: tuple = ()
    session_state: int = STATE_INVALID
    session_type: str = ""
    flags: int = 0
    leader_lap: int = -1
    timestamp: float = field(default_factory=time.time)


class CautionTracker:
    """Turns a stream of :class:`Sample` into caution / caution lap counts."""

    def __init__(self) -> None:
        self._session_key = None
        self.reset()

    def reset(self) -> None:
        self.cautions = 0
        self.caution_laps = 0
        self.under_caution = False
        self._raw_caution = False
        self._raw_since = 0.0
        self._raw_leader_lap = -1
        self._leader_lap = -1

    def update(self, sample: Sample) -> None:
        if not sample.connected:
            return

        # A new session (or a new subsession) starts from scratch.
        if sample.session_key != self._session_key:
            self._session_key = sample.session_key
            self.reset()
            self._raw_since = sample.timestamp

        # Only a race that is under way can have cautions: this keeps pace
        # laps, practice and qualifying out of the counters.
        countable = sample.session_state in (STATE_RACING, STATE_CHECKERED) and (
            sample.session_type in ("", "Race")
        )
        raw = countable and bool(sample.flags & FLAG_FULL_COURSE_CAUTION)

        if raw != self._raw_caution:
            self._raw_caution = raw
            self._raw_since = sample.timestamp
            # Remember where the leader was when the flag actually changed, so
            # a lap completed during the debounce window is not lost.
            self._raw_leader_lap = sample.leader_lap

        settled = (sample.timestamp - self._raw_since) >= FLAG_DEBOUNCE_S
        if settled and raw != self.under_caution:
            self.under_caution = raw
            if raw:
                self.cautions += 1
                self._leader_lap = (
                    self._raw_leader_lap if self._raw_leader_lap >= 0 else sample.leader_lap
                )

        if self.under_caution and sample.leader_lap >= 0:
            if self._leader_lap < 0:
                self._leader_lap = sample.leader_lap
            elif sample.leader_lap > self._leader_lap:
                self.caution_laps += sample.leader_lap - self._leader_lap
                self._leader_lap = sample.leader_lap


class TelemetryWorker(QThread):
    """Polls iRacing in the background and emits a :class:`Sample` per tick."""

    sample_ready = Signal(object)

    def __init__(self, demo: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._demo = demo
        self._stop = False
        self._ir = irsdk.IRSDK() if (irsdk is not None and not demo) else None
        self._connected = False
        self._last_startup_try = 0.0
        self._session_cache: dict = {}
        self._session_cache_at = 0.0

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102 - QThread entry point
        if self._demo:
            self._run_demo()
            return
        while not self._stop:
            self._tick()
            self.msleep(POLL_INTERVAL_MS)
        self._disconnect()

    # -- live telemetry -----------------------------------------------------
    def _tick(self) -> None:
        ir = self._ir
        if ir is None:
            self.sample_ready.emit(Sample(connected=False))
            return

        try:
            if not (ir.is_initialized and ir.is_connected):
                self._disconnect()
                now = time.time()
                if now - self._last_startup_try < RECONNECT_INTERVAL_S:
                    self.sample_ready.emit(Sample(connected=False))
                    return
                self._last_startup_try = now
                if not ir.startup():
                    self.sample_ready.emit(Sample(connected=False))
                    return
                self._connected = True

            ir.freeze_var_buffer_latest()
            try:
                sample = self._read(ir)
            finally:
                try:
                    ir.unfreeze_var_buffer_latest()
                except Exception:
                    pass
        except Exception:
            # iRacing was closed halfway through a read, or the SDK is not
            # usable on this machine: just report "not connected" and retry.
            self._disconnect()
            sample = Sample(connected=False)

        self.sample_ready.emit(sample)

    def _disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        self._session_cache = {}
        try:
            self._ir.shutdown()
        except Exception:
            pass

    def _read(self, ir) -> Sample:
        now = time.time()
        info = self._session_info(ir, now)
        session_num = ir["SessionNum"] or 0
        return Sample(
            connected=True,
            session_key=(info.get("subsession_id", 0), session_num),
            session_state=ir["SessionState"] or STATE_INVALID,
            session_type=info.get("types", {}).get(session_num, ""),
            flags=ir["SessionFlags"] or 0,
            leader_lap=self._leader_lap(ir, info.get("cars")),
            timestamp=now,
        )

    def _session_info(self, ir, now: float) -> dict:
        """Session YAML data, refreshed at most every few seconds."""
        if self._session_cache and (now - self._session_cache_at) < SESSION_INFO_INTERVAL_S:
            return self._session_cache

        info: dict = {}
        weekend = ir["WeekendInfo"] or {}
        info["subsession_id"] = weekend.get("SubSessionID", 0)

        types: dict = {}
        for session in (ir["SessionInfo"] or {}).get("Sessions") or []:
            try:
                types[int(session.get("SessionNum", -1))] = str(session.get("SessionType", ""))
            except (TypeError, ValueError):
                continue
        info["types"] = types

        cars = []
        for driver in (ir["DriverInfo"] or {}).get("Drivers") or []:
            try:
                if int(driver.get("CarIsPaceCar", 0)) or int(driver.get("IsSpectator", 0)):
                    continue
                cars.append(int(driver["CarIdx"]))
            except (TypeError, ValueError, KeyError):
                continue
        info["cars"] = cars or None

        self._session_cache = info
        self._session_cache_at = now
        return info

    @staticmethod
    def _leader_lap(ir, cars) -> int:
        """Laps completed by the leader, i.e. the highest lap count on track."""
        laps = ir["CarIdxLapCompleted"]
        if not laps:
            current = ir["CarIdxLap"]
            if not current:
                return -1
            # CarIdxLap is the lap the car is on (1 based), -1 when unused.
            laps = [lap - 1 for lap in current]
        if cars:
            values = [laps[i] for i in cars if 0 <= i < len(laps)]
        else:
            values = list(laps)
        values = [v for v in values if v is not None and v >= 0]
        return max(values) if values else -1

    # -- demo mode ----------------------------------------------------------
    def _run_demo(self) -> None:
        """Fake race used to check the layout without iRacing running."""
        started = time.time()
        laps = 0.0
        windows = ((12.0, 26.0), (45.0, 66.0), (85.0, 99.0))
        while not self._stop:
            elapsed = time.time() - started
            caution = any(begin <= elapsed < end for begin, end in windows)
            laps += 0.03 if caution else 0.07
            self.sample_ready.emit(
                Sample(
                    connected=True,
                    session_key=("demo", 0),
                    session_state=STATE_RACING,
                    session_type="Race",
                    flags=FLAG_FULL_COURSE_CAUTION if caution else FLAG_GREEN,
                    leader_lap=int(laps),
                    timestamp=time.time(),
                )
            )
            self.msleep(POLL_INTERVAL_MS)


def clamp(value: float, lowest: float, highest: float) -> float:
    return max(lowest, min(highest, value))


def scaled_font(pixel_size: float, bold: bool = False, spacing: float = 0.0) -> QFont:
    """The application font at a given pixel size.

    Sizes live here rather than in the style sheets because Qt style sheets
    cannot express letter spacing, and because these have to change every time
    the window is resized.
    """
    font = QFont(QApplication.font())
    font.setPixelSize(max(1, int(round(pixel_size))))
    font.setBold(bold)
    if spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return font


class SilentSizeGrip(QSizeGrip):
    """A resize handle that draws nothing.

    The corner marks are painted by the overlay itself so they match the rest
    of the panel; this only supplies the resize cursor and the drag behaviour.
    """

    def paintEvent(self, event) -> None:  # noqa: D102 - Qt override
        pass


class StatBlock(QWidget):
    """A big number with a caption under it."""

    def __init__(self, caption: str, parent=None) -> None:
        super().__init__(parent)
        self.value = QLabel("0")
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
        self.caption = QLabel(caption)
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setStyleSheet(f"color: {COLOR_MUTED}; background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, text: str) -> None:
        self.value.setText(text)

    def apply_scale(self, value_size: float, caption_size: float) -> None:
        self.value.setFont(scaled_font(value_size, bold=True))
        self.caption.setFont(scaled_font(caption_size, bold=True, spacing=1.0))


class OverlayWindow(QWidget):
    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(MINIMUM_SIZE)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        self._settings = QSettings(ORG_NAME, SETTINGS_APP)
        self._locked = bool(self._settings.value("locked", False, type=bool))
        self._drag_offset: QPoint | None = None
        self._hover = False
        # Guards resizeEvent, which Qt can fire before the widgets exist.
        self._ui_ready = False
        self._tracker = CautionTracker()
        self._under_caution = False

        self._build_ui()
        self._restore_geometry()
        self._set_hover(False)

        # Persisting on every resize event would hit the settings file dozens
        # of times per drag, so it is written once the drag settles.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_geometry)

        # Qt sends a leave event when the cursor moves onto a child widget, so
        # the hover state is polled instead - it never flickers this way.
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._poll_hover)
        self._hover_timer.start(120)

        self._worker = TelemetryWorker(demo=demo, parent=self)
        self._worker.sample_ready.connect(self._on_sample)
        self._worker.start()

    # -- ui -----------------------------------------------------------------
    def _build_ui(self) -> None:
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {COLOR_IDLE}; background: transparent;")
        self._status_text = QLabel("WAITING FOR IRACING")
        self._status_text.setStyleSheet(f"color: {COLOR_MUTED}; background: transparent;")

        self._close_button = QPushButton("✕")
        self._close_button.setCursor(Qt.PointingHandCursor)
        self._close_button.setToolTip("Close overlay")
        self._close_button.clicked.connect(self.close)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(5)
        header.addWidget(self._status_dot)
        header.addWidget(self._status_text)
        header.addStretch(1)
        header.addWidget(self._close_button)

        self._cautions = StatBlock("CAUTIONS")
        self._caution_laps = StatBlock("CAUTION LAPS")

        separator = QWidget()
        separator.setFixedWidth(1)
        separator.setStyleSheet("background: rgba(255,255,255,25);")

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(8)
        stats.addWidget(self._cautions, 1)
        stats.addWidget(separator)
        stats.addWidget(self._caution_laps, 1)

        self._lock_box = QCheckBox("Lock position")
        self._lock_box.setChecked(self._locked)
        self._lock_box.setCursor(Qt.PointingHandCursor)
        self._lock_box.setToolTip("Stop the overlay from being dragged")
        self._lock_box.toggled.connect(self._set_locked)

        self._hint = QLabel("locked" if self._locked else "drag to move")
        self._hint.setStyleSheet(f"color: {COLOR_IDLE}; background: transparent;")

        self._footer_layout = QHBoxLayout()
        self._footer_layout.setSpacing(6)
        self._footer_layout.addWidget(self._lock_box)
        self._footer_layout.addStretch(1)
        self._footer_layout.addWidget(self._hint)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setSpacing(2)
        self._root_layout.addLayout(header)
        self._root_layout.addLayout(stats, 1)
        self._root_layout.addLayout(self._footer_layout)

        # Bottom right resize handle. It is invisible: paintEvent draws the
        # corner marks so they match the panel, and this only carries the
        # resize cursor and the drag itself.
        self._size_grip = SilentSizeGrip(self)
        self._size_grip.setToolTip("Drag to resize")

        self._ui_ready = True
        self._apply_scale()

    def paintEvent(self, event) -> None:  # noqa: D102 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        rect = self.rect().adjusted(1, 1, -1, -1)
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, COLOR_PANEL)
        border = COLOR_BORDER_CAUTION if self._under_caution else COLOR_BORDER
        painter.setPen(QPen(border, 1.4))
        painter.drawPath(path)
        if self._hover:
            self._paint_resize_marks(painter, rect)

    def _paint_resize_marks(self, painter: QPainter, rect) -> None:
        """Three diagonal strokes in the bottom right corner: the resize hint."""
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.4))
        span = self._size_grip.width()
        corner_x, corner_y = rect.right() - 5, rect.bottom() - 5
        for fraction in (0.25, 0.55, 0.85):
            offset = int(span * fraction)
            painter.drawLine(corner_x - offset, corner_y, corner_x, corner_y - offset)

    # -- scaling ------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: D102 - Qt override
        super().resizeEvent(event)
        if not self._ui_ready:
            return
        self._apply_scale()
        if hasattr(self, "_save_timer"):
            self._save_timer.start()

    def _apply_scale(self) -> None:
        """Size every part of the panel from the current window height."""
        scale = self.height() / SCALE_REFERENCE_HEIGHT
        value_size = clamp(30 * scale, 15, 110)
        small_size = clamp(9 * scale, 7, 24)
        box_size = clamp(10 * scale, 8, 24)
        button_size = int(clamp(18 * scale, 14, 40))

        self._cautions.apply_scale(value_size, small_size)
        self._caution_laps.apply_scale(value_size, small_size)
        self._status_dot.setFont(scaled_font(small_size + 1))
        self._status_text.setFont(scaled_font(small_size, bold=True, spacing=1.0))
        self._hint.setFont(scaled_font(small_size))
        self._lock_box.setFont(scaled_font(box_size))

        self._close_button.setFixedSize(button_size, button_size)
        self._close_button.setFont(scaled_font(button_size * 0.55, bold=True))
        self._close_button.setStyleSheet(
            "QPushButton {"
            f" color: {COLOR_MUTED}; background: rgba(255,255,255,18);"
            f" border: none; border-radius: {button_size // 2}px; }}"
            "QPushButton:hover { color: #ffffff; background: rgba(228,60,60,220); }"
        )

        indicator = int(clamp(round(11 * scale), 10, 22))
        self._lock_box.setStyleSheet(
            "QCheckBox {"
            f" color: {COLOR_MUTED}; background: transparent; spacing: 5px; }}"
            f"QCheckBox::indicator {{ width: {indicator}px; height: {indicator}px;"
            " border-radius: 3px; border: 1px solid rgba(255,255,255,60);"
            " background: rgba(255,255,255,10); }"
            "QCheckBox::indicator:checked {"
            f" background: {COLOR_YELLOW}; border: 1px solid {COLOR_YELLOW}; }}"
        )

        self._root_layout.setContentsMargins(
            int(clamp(14 * scale, 8, 34)),
            int(clamp(9 * scale, 5, 24)),
            int(clamp(12 * scale, 7, 30)),
            int(clamp(8 * scale, 5, 22)),
        )

        grip = int(clamp(round(14 * scale), 12, 30))
        # Keep the hint text clear of the corner marks.
        self._footer_layout.setContentsMargins(0, 0, grip, 0)
        self._size_grip.setFixedSize(grip, grip)
        self._size_grip.move(self.width() - grip - 2, self.height() - grip - 2)
        self._size_grip.raise_()

    # -- hover / drag / lock ------------------------------------------------
    def _poll_hover(self) -> None:
        inside = self.isVisible() and self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if inside != self._hover:
            self._set_hover(inside)

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._close_button.setVisible(hover)
        self._lock_box.setVisible(hover)
        self._hint.setVisible(hover)

    def _set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        if self._lock_box.isChecked() != self._locked:
            self._lock_box.setChecked(self._locked)
        self._settings.setValue("locked", self._locked)
        self._hint.setText("locked" if self._locked else "drag to move")

    def mousePressEvent(self, event) -> None:  # noqa: D102 - Qt override
        if event.button() == Qt.LeftButton and not self._locked:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: D102 - Qt override
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: D102 - Qt override
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_geometry()
            event.accept()

    def _show_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        lock_action = menu.addAction("Lock position")
        lock_action.setCheckable(True)
        lock_action.setChecked(self._locked)
        reset_action = menu.addAction("Reset counters")
        reset_size_action = menu.addAction("Reset size")
        menu.addSeparator()
        close_action = menu.addAction("Close overlay")

        chosen = menu.exec(self.mapToGlobal(position))
        if chosen is lock_action:
            self._set_locked(not self._locked)
        elif chosen is reset_action:
            self._tracker.reset()
            self._refresh_counters()
        elif chosen is reset_size_action:
            self.resize(DEFAULT_SIZE)
            self._save_geometry()
        elif chosen is close_action:
            self.close()

    # -- geometry persistence ----------------------------------------------
    def _restore_geometry(self) -> None:
        size = self._settings.value("size")
        if isinstance(size, QSize) and size.isValid():
            self.resize(size.expandedTo(MINIMUM_SIZE))
        else:
            self.resize(DEFAULT_SIZE)

        saved = self._settings.value("pos")
        if isinstance(saved, QPoint) and self._is_on_screen(saved):
            self.move(saved)
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.left() + 40, area.top() + 40)

    def _is_on_screen(self, position: QPoint) -> bool:
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(position):
                return True
        return False

    def _save_geometry(self) -> None:
        self._settings.setValue("pos", self.pos())
        self._settings.setValue("size", self.size())

    # -- telemetry ----------------------------------------------------------
    def _on_sample(self, sample: Sample) -> None:
        self._tracker.update(sample)
        self._refresh_counters()

        if not sample.connected:
            self._set_status(COLOR_IDLE, "WAITING FOR IRACING")
        elif self._tracker.under_caution:
            self._set_status(COLOR_YELLOW, "FULL COURSE CAUTION")
        elif sample.session_type not in ("", "Race"):
            self._set_status(COLOR_IDLE, (sample.session_type or "SESSION").upper())
        elif sample.session_state == STATE_RACING:
            self._set_status(COLOR_GREEN, "GREEN")
        else:
            self._set_status(COLOR_IDLE, "CONNECTED")

        if self._tracker.under_caution != self._under_caution:
            self._under_caution = self._tracker.under_caution
            self.update()

    def _refresh_counters(self) -> None:
        self._cautions.set_value(str(self._tracker.cautions))
        self._caution_laps.set_value(str(self._tracker.caution_laps))

    def _set_status(self, color: str, text: str) -> None:
        if self._status_text.text() != text:
            self._status_text.setText(text)
            self._status_dot.setStyleSheet(f"color: {color}; background: transparent;")

    # -- health check -------------------------------------------------------
    def is_running_ok(self) -> bool:
        """True when the window paints and the telemetry thread is alive.

        Used by ``--selftest`` so the packaged build is verified on Windows.
        """
        pixmap = self.grab()
        return (
            not pixmap.isNull()
            and pixmap.width() == self.width()
            and self._worker.isRunning()
        )

    # -- shutdown -----------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: D102 - Qt override
        self._save_geometry()
        self._worker.stop()
        self._worker.wait(1500)
        super().closeEvent(event)


def _build_app() -> QApplication:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    font = app.font()
    font.setFamily("Segoe UI" if sys.platform == "win32" else font.family())
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    return app


def _run_selftest(seconds: float) -> int:
    """Start the overlay, check it is alive, and quit. Used by the build.

    Returns 0 when the SDK bindings are present and the window is painting,
    which is what a packaged build has to prove before being released.
    """
    app = _build_app()
    window = OverlayWindow(demo=True)
    window.show()

    outcome = {"ok": False}

    def check() -> None:
        outcome["ok"] = irsdk is not None and window.is_running_ok()
        app.quit()

    QTimer.singleShot(max(500, int(seconds * 1000)), check)
    app.exec()
    window.close()
    return 0 if outcome["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run with simulated data instead of connecting to iRacing",
    )
    parser.add_argument(
        "--selftest",
        nargs="?",
        type=float,
        const=3.0,
        default=None,
        metavar="SECONDS",
        help="start, check the overlay is alive, then exit (used by the build)",
    )
    args = parser.parse_args()

    if args.selftest is not None:
        return _run_selftest(args.selftest)

    app = _build_app()
    window = OverlayWindow(demo=args.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
