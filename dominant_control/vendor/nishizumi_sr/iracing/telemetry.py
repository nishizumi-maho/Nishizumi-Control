"""
Per-tick telemetry reads, lap detection and corner accumulation.

Deliberately cheap: no heavy work happens here. The SR estimate itself is only
recomputed on lap completion (see sr/calculator.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from iracing.sdk import IRacingSDK

logger = logging.getLogger("sr_estimator.telemetry")

# irsdk_TrkLoc - stable SDK constant, see RESEARCH.md section 7
TRK_LOC_NOT_IN_WORLD = -1
TRK_LOC_OFF_TRACK = 0
TRK_LOC_IN_PIT_STALL = 1
TRK_LOC_APPROACHING_PITS = 2
TRK_LOC_ON_TRACK = 3

# irsdk_SessionState - confirmed in the irsdk.py source, see RESEARCH.md section 7
SESSION_STATE_INVALID = 0
SESSION_STATE_GET_IN_CAR = 1
SESSION_STATE_WARMUP = 2
SESSION_STATE_PARADE_LAPS = 3
SESSION_STATE_RACING = 4
SESSION_STATE_CHECKERED = 5
SESSION_STATE_COOL_DOWN = 6


@dataclass
class TelemetrySnapshot:
    session_num: int
    session_state: int
    session_time: float
    lap: int
    lap_dist_pct: float
    on_pit_road: bool
    player_track_surface: int
    incident_count: int
    incident_count_source: str


_incident_source_logged = False


def _pick_incident_count(sdk: IRacingSDK) -> tuple[int, str]:
    """PlayerCarMyIncidentCount isolates the local driver even across driver
    swaps (RESEARCH.md section 7). Falls back to IncidentCount on older builds."""
    global _incident_source_logged
    value = sdk.get("PlayerCarMyIncidentCount")
    if isinstance(value, (int, float)):
        source = "PlayerCarMyIncidentCount"
    else:
        value = sdk.get("IncidentCount")
        source = "IncidentCount"

    if not _incident_source_logged:
        logger.info("Using '%s' as the incident source", source)
        _incident_source_logged = True

    return int(value or 0), source


def read_snapshot(sdk: IRacingSDK) -> Optional[TelemetrySnapshot]:
    session_num = sdk.get("SessionNum")
    lap = sdk.get("Lap")
    if session_num is None or lap is None:
        return None

    incident_count, source = _pick_incident_count(sdk)

    return TelemetrySnapshot(
        session_num=int(session_num),
        session_state=int(sdk.get("SessionState", SESSION_STATE_INVALID)),
        session_time=float(sdk.get("SessionTime", 0.0)),
        lap=int(lap),
        lap_dist_pct=float(sdk.get("LapDistPct", 0.0) or 0.0),
        on_pit_road=bool(sdk.get("OnPitRoad", False)),
        player_track_surface=int(sdk.get("PlayerTrackSurface", TRK_LOC_NOT_IN_WORLD)),
        incident_count=incident_count,
        incident_count_source=source,
    )


class CornerAccumulator:
    """Counts corners by DISTANCE actually driven, not by laps counted.

    Why: at Nurburgring it is common to take a tow just after the pits, or to
    simply not finish a lap. The lap counter can move without the driver having
    driven those corners - and iRacing's CPI is about corners driven. Adding
    `corners_per_lap` per counted lap would overcount in exactly those cases.

    The reverse happens too, and shows up in real results: records exist with
    0 completed laps, 2 incidents and a genuine -0.06 SR change, meaning iRacing
    credited the corners of a PARTIAL lap. Counting by distance fixes both sides.

    Rules:
    - only accumulates while the car is in the world and out of the pit box;
    - `LapDistPct` wrapping back to zero is treated as a lap boundary;
    - a large single-tick distance jump (tow, teleport, reset) is not credited;
      it only resynchronises the reference point.
    """

    # Largest plausible fraction of a lap in a single tick. At 100ms polling
    # even a fast car on a short track covers far less; a tow or teleport covers
    # far more. Deliberately generous.
    MAX_TICK_FRACTION = 0.10

    def __init__(self) -> None:
        self._prev_pct: Optional[float] = None
        self.corners: float = 0.0
        self.distance_laps: float = 0.0
        self.skipped_jumps: int = 0

    def reset(self) -> None:
        self._prev_pct = None
        self.corners = 0.0
        self.distance_laps = 0.0
        self.skipped_jumps = 0

    def update(self, snap: "TelemetrySnapshot", corners_per_lap: float) -> float:
        """Accumulate and return how many corners were credited this tick."""
        in_world = snap.player_track_surface not in (
            TRK_LOC_NOT_IN_WORLD, TRK_LOC_IN_PIT_STALL,
        )
        if not in_world:
            # In the pit box or out of the world: no corners, and position may jump.
            self._prev_pct = None
            return 0.0

        pct = snap.lap_dist_pct
        if pct < 0.0 or pct > 1.0:
            self._prev_pct = None
            return 0.0

        if self._prev_pct is None:
            self._prev_pct = pct
            return 0.0

        delta = pct - self._prev_pct
        if delta < 0.0:
            delta += 1.0  # crossed the start/finish line

        if delta > self.MAX_TICK_FRACTION:
            # Tow, teleport, session reset or a paused app: invent no corners.
            self.skipped_jumps += 1
            logger.debug("Ignored distance jump (%.3f of a lap)", delta)
            self._prev_pct = pct
            return 0.0

        self._prev_pct = pct
        self.distance_laps += delta
        credited = corners_per_lap * delta
        self.corners += credited
        return credited


class LapDetector:
    """Detects the moment a lap is completed, per session."""

    def __init__(self) -> None:
        self._last_lap: Optional[int] = None
        self._session_num: Optional[int] = None

    def reset(self, session_num: int, current_lap: int) -> None:
        self._session_num = session_num
        self._last_lap = current_lap

    def update(self, session_num: int, current_lap: int) -> bool:
        """Returns True exactly once per lap increment."""
        if self._session_num != session_num or self._last_lap is None:
            self.reset(session_num, current_lap)
            return False

        if current_lap > self._last_lap:
            self._last_lap = current_lap
            return True

        if current_lap < self._last_lap:
            # Session restarted or went backwards - do not treat this as a
            # completed lap, just realign.
            logger.warning("Lap number went backwards (%s -> %s); realigning",
                            self._last_lap, current_lap)
            self._last_lap = current_lap
        return False
