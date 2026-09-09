"""UI-independent full course caution counting, from the Caution Overlay.

Same split as ``tools/fuel/engine.py``: the counting rules come from the
vendored ``Nishizumi_CautionOverlay.py``, but that file imports PySide6 at
module level and the main application is Tk — so what decides the numbers lives
here, reading the shared :class:`~dominant_control.core.TelemetryHub`, while the
vendored window is imported only by the renderer process
(``original_overlays/caution_worker.py``).

How the counting works (unchanged from the original)
----------------------------------------------------
iRacing publishes neither "number of cautions" nor "caution laps", so both are
derived from the live stream:

* ``SessionFlags`` is a bitmask. ``caution`` (0x4000) means a full course
  caution is out and ``cautionWaving`` (0x8000) means one is being thrown. The
  ``yellow`` / ``yellowWaving`` bits are *local* yellows for a single corner and
  are deliberately ignored — counting those would inflate the count on road
  courses.
* Every rising edge of the full course caution state is one caution, debounced
  by :data:`FLAG_DEBOUNCE_S` because the bits flicker around a restart.
* Caution laps are counted from the race *leader*, the way broadcasts quote the
  stat ("5 cautions for 27 laps"). The leader is the car with the highest
  ``CarIdxLapCompleted`` (pace car and spectators excluded), and every start/
  finish crossing it makes under caution adds one lap. The restart crossing
  counts, because iRacing waves the green as the leader crosses the line.
* Only a race that is actually racing is counted, so pace laps, practice and
  qualifying never touch the counters, which reset on a session change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ...core import TelemetryHub

# --- iRacing SessionFlags bitmask (values from the iRacing SDK) -------------
FLAG_CAUTION = 0x00004000
FLAG_CAUTION_WAVING = 0x00008000
FLAG_FULL_COURSE_CAUTION = FLAG_CAUTION | FLAG_CAUTION_WAVING

# --- iRacing SessionState values -------------------------------------------
STATE_INVALID = 0
STATE_RACING = 4
STATE_CHECKERED = 5

# The caution bits can flicker; ignore anything shorter than this (seconds).
FLAG_DEBOUNCE_S = 1.0
# The session string is only re-read this often (seconds): walking the driver
# list on every tick would cost more than the numbers it produces.
SESSION_INFO_INTERVAL_S = 2.0

# Registered up front so the first tick already has values instead of priming
# the hub's field set one read at a time.
CAUTION_FIELDS = (
    "CarIdxLap",
    "CarIdxLapCompleted",
    "DriverInfo",
    "SessionFlags",
    "SessionInfo",
    "SessionNum",
    "SessionState",
    "WeekendInfo",
)

STATUS_OFFLINE = "offline"
STATUS_CAUTION = "caution"
STATUS_OTHER_SESSION = "other_session"
STATUS_GREEN = "green"
STATUS_CONNECTED = "connected"

# The overlay names the session it is sitting out, so the driver can tell
# "nothing to count here" from "connected but not racing yet".
SESSION_TYPE_LABELS = {
    "practice": "PRACTICE",
    "open practice": "OPEN PRACTICE",
    "offline testing": "TESTING",
    "testing": "TESTING",
    "qualify": "QUALIFYING",
    "open qualify": "OPEN QUALIFYING",
    "lone qualify": "LONE QUALIFYING",
    "warmup": "WARMUP",
    "heat race": "HEAT RACE",
    "consolation race": "CONSOLATION RACE",
}


@dataclass
class Sample:
    """One snapshot of the telemetry the counters care about."""

    connected: bool = False
    session_key: tuple = ()
    session_state: int = STATE_INVALID
    session_type: str = ""
    flags: int = 0
    leader_lap: int = -1
    # The original samples the wall clock. A monotonic clock is used here
    # because only elapsed time matters and a clock step backwards mid-race
    # would otherwise hold the debounce open and swallow a caution.
    timestamp: float = field(default_factory=time.monotonic)


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


class CautionEngine:
    """Counts the cautions of the session being driven, off the shared hub."""

    def __init__(self, telemetry: TelemetryHub) -> None:
        self.telemetry = telemetry
        self.telemetry.register(CAUTION_FIELDS)
        self.tracker = CautionTracker()
        self._session_cache: dict[str, Any] = {}
        self._session_cache_at = 0.0

    # -------------------------------------------------------------- ticks --
    def tick(self) -> dict[str, Any]:
        """One poll. Returns the state the overlay renders."""
        sample = self._read()
        self.tracker.update(sample)
        status, text = self._status(sample)
        return {
            "cautions": self.tracker.cautions,
            "caution_laps": self.tracker.caution_laps,
            "under_caution": self.tracker.under_caution,
            "status": status,
            "status_text": text,
        }

    def reset(self) -> None:
        """Zero the counters by hand, from the overlay's right click menu."""
        self.tracker.reset()

    # ------------------------------------------------------------ reading --
    def _read(self) -> Sample:
        now = time.monotonic()
        if not self.telemetry.status().connected:
            # Nothing read while disconnected can be trusted, and dropping the
            # cached session info means the next connection re-reads it.
            self._session_cache = {}
            self._session_cache_at = 0.0
            return Sample(connected=False, timestamp=now)

        info = self._session_info(now)
        session_num = self._value("SessionNum", 0) or 0
        return Sample(
            connected=True,
            session_key=(info.get("subsession_id", 0), session_num),
            session_state=self._value("SessionState", STATE_INVALID) or STATE_INVALID,
            session_type=info.get("types", {}).get(session_num, ""),
            flags=self._value("SessionFlags", 0) or 0,
            leader_lap=self._leader_lap(info.get("cars")),
            timestamp=now,
        )

    def _value(self, name: str, default: Any = None) -> Any:
        value = self.telemetry.read(name, default)
        return default if value is None else value

    def _session_info(self, now: float) -> dict[str, Any]:
        """Session string data, refreshed at most every few seconds."""
        if self._session_cache and (now - self._session_cache_at) < SESSION_INFO_INTERVAL_S:
            return self._session_cache

        info: dict[str, Any] = {}
        weekend = self._value("WeekendInfo", {})
        info["subsession_id"] = (
            weekend.get("SubSessionID", 0) if isinstance(weekend, dict) else 0
        )

        types: dict[int, str] = {}
        session_info = self._value("SessionInfo", {})
        sessions = session_info.get("Sessions") if isinstance(session_info, dict) else None
        for session in sessions or []:
            if not isinstance(session, dict):
                continue
            try:
                types[int(session.get("SessionNum", -1))] = str(session.get("SessionType", ""))
            except (TypeError, ValueError):
                continue
        info["types"] = types

        cars: list[int] = []
        driver_info = self._value("DriverInfo", {})
        drivers = driver_info.get("Drivers") if isinstance(driver_info, dict) else None
        for driver in drivers or []:
            if not isinstance(driver, dict):
                continue
            try:
                if int(driver.get("CarIsPaceCar", 0)) or int(driver.get("IsSpectator", 0)):
                    continue
                cars.append(int(driver["CarIdx"]))
            except (TypeError, ValueError, KeyError):
                continue
        info["cars"] = cars or None

        # A session string that parsed into nothing is a half-written read, not
        # an empty session: leaving the cache empty retries on the next tick.
        if types or cars:
            self._session_cache = info
            self._session_cache_at = now
        return info

    def _leader_lap(self, cars: list[int] | None) -> int:
        """Laps completed by the leader, i.e. the highest lap count on track."""
        laps = self._value("CarIdxLapCompleted")
        if not laps:
            current = self._value("CarIdxLap")
            if not current:
                return -1
            # CarIdxLap is the lap the car is on (1 based), -1 when unused.
            # A slot that is not a number reads as "no lap" rather than
            # bringing the tick down: the positions have to line up with the
            # car indexes below.
            laps = [
                lap - 1 if isinstance(lap, (int, float)) else -1 for lap in current
            ]
        if cars:
            values = [laps[i] for i in cars if 0 <= i < len(laps)]
        else:
            values = list(laps)
        values = [v for v in values if v is not None and v >= 0]
        return max(values) if values else -1

    # ------------------------------------------------------------- status --
    def _status(self, sample: Sample) -> tuple[str, str]:
        if not sample.connected:
            return STATUS_OFFLINE, "WAITING FOR IRACING"
        if self.tracker.under_caution:
            return STATUS_CAUTION, "FULL COURSE YELLOW"
        if sample.session_type not in ("", "Race"):
            label = SESSION_TYPE_LABELS.get(
                sample.session_type.strip().lower(), sample.session_type.upper()
            )
            return STATUS_OTHER_SESSION, label or "SESSION"
        if sample.session_state == STATE_RACING:
            return STATUS_GREEN, "GREEN"
        return STATUS_CONNECTED, "CONNECTED"


__all__ = [
    "CAUTION_FIELDS",
    "FLAG_DEBOUNCE_S",
    "FLAG_FULL_COURSE_CAUTION",
    "STATUS_CAUTION",
    "STATUS_CONNECTED",
    "STATUS_GREEN",
    "STATUS_OFFLINE",
    "STATUS_OTHER_SESSION",
    "CautionEngine",
    "CautionTracker",
    "Sample",
]
