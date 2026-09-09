"""Nishizumi SR's Safety Rating estimator, driven by the shared telemetry.

The standalone application owns a pyirsdk connection of its own and polls it
from a Qt timer.  Inside Dominant Control there is exactly one connection for
the whole application — the :class:`~dominant_control.core.TelemetryHub` — so
what runs here is the same estimator reading the hub instead, and the overlay
process only renders what this publishes.  That is the arrangement every other
tool already uses (see ``tools/original_overlays``); it also means the estimate
keeps accumulating while the user is on any other tab, and with the overlay
closed.

Everything that decides *a number* lives in the vendored modules. This file
only feeds them telemetry and decides which of the five display states the
overlay is in.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any

from ...core import TelemetryHub
from .vendored import load

logger = logging.getLogger("dominant_control.tools.sr")

_sdk = load("iracing.sdk")
_session = load("iracing.session")
_telemetry = load("iracing.telemetry")
_calculator = load("sr.calculator")
_calibration = load("sr.calibration")
_history = load("sr.history")
_model = load("sr.model")
_tracks = load("tracks.database")

# The estimator needs five telemetry variables plus the three session-string
# sections; registering them up front means the first tick already has values
# instead of priming the hub's field set one read at a time.
SR_FIELDS = (
    "DriverInfo",
    "IncidentCount",
    "Lap",
    "LapDistPct",
    "OnPitRoad",
    "PlayerCarIdx",
    "PlayerCarMyIncidentCount",
    "PlayerTrackSurface",
    "SessionInfo",
    "SessionNum",
    "SessionState",
    "SessionTime",
    "WeekendInfo",
)

STATUS_OFFLINE = "offline"
STATUS_WAITING = "waiting"
STATUS_PRACTICE = "practice"
STATUS_COMPLETE = "complete"
STATUS_LIVE = "live"

# How long the closing summary stays on screen before the overlay goes back to
# waiting for the next session. Same 8 seconds the standalone app uses.
SESSION_COMPLETE_SECONDS = 8.0

FALLBACK_CORNERS_PER_LAP = 10
SESSION_REFRESH_SECONDS = 1.0


class HubSDK(_sdk.IRacingSDK):  # type: ignore[misc,name-defined]
    """The vendored SDK wrapper, reading the hub instead of opening a handle.

    Only ``poll`` and ``shutdown`` are replaced: everything else already works,
    because :class:`~dominant_control.core.telemetry.TelemetryProxy` exposes the
    ``is_initialized`` / ``is_connected`` / ``__getitem__`` surface the wrapper
    reads.  Neither method may take the connection down — the handle belongs to
    the hub, and dropping it would blind every other tool at once.
    """

    def __init__(self, telemetry: TelemetryHub) -> None:
        self._ir = telemetry.proxy
        self._was_connected = False

    def poll(self) -> bool:
        connected = bool(self._ir.is_connected)
        if connected != self._was_connected:
            logger.info("SR: telemetry %s", "connected" if connected else "disconnected")
        self._was_connected = connected
        return connected

    def shutdown(self) -> None:
        return None


class SafetyRatingEngine:
    """Accumulates the Safety Rating estimate for the session being driven."""

    def __init__(self, telemetry: TelemetryHub) -> None:
        self.telemetry = telemetry
        self.telemetry.register(SR_FIELDS)
        self.sdk = HubSDK(telemetry)

        self.params = _model.SRModelParams()
        self.tracker = _calculator.SessionSRTracker(self.params)
        self.corner_accumulator = _telemetry.CornerAccumulator()
        self.lap_detector = _telemetry.LapDetector()
        self.tracks = _tracks.TrackDatabase(
            fallback_corners_per_lap=FALLBACK_CORNERS_PER_LAP
        )
        self.history = _history.HistoryStore()

        self.current_session_num: int | None = None
        self.track_info: Any = None
        self.player_info: Any = None
        self.session_meta: Any = None
        self.session_finalized = True
        self.complete_until = 0.0
        self.complete_state: dict[str, Any] | None = None
        self.last_session_refresh = 0.0
        self.state: dict[str, Any] = self._waiting("Waiting for iRacing…")

    # ------------------------------------------------------------- states --
    @staticmethod
    def _waiting(message: str, *, status: str = STATUS_WAITING) -> dict[str, Any]:
        return {
            "status": status,
            "message": message,
            "estimate": None,
            "session_type": "unknown",
            "track": "",
        }

    def _with_estimate(self, status: str, estimate: Any) -> dict[str, Any]:
        return {
            "status": status,
            "message": "",
            "estimate": asdict(estimate),
            "session_type": (
                self.session_meta.session_type if self.session_meta else "unknown"
            ),
            "track": self.track_info.display_name if self.track_info else "",
        }

    # -------------------------------------------------------------- ticks --
    def tick(self) -> dict[str, Any]:
        """One poll. Returns — and remembers — the state the overlay renders."""
        self.state = self._compute()
        return self.state

    def _compute(self) -> dict[str, Any]:
        if not self.sdk.poll():
            return self._waiting("Waiting for iRacing…", status=STATUS_OFFLINE)

        if self.complete_state is not None:
            if time.monotonic() < self.complete_until:
                return self.complete_state
            self.complete_state = None

        snap = _telemetry.read_snapshot(self.sdk)
        if snap is None:
            return self._waiting("Waiting for the session…")

        if snap.session_num != self.current_session_num:
            self._handle_new_session(snap)

        if self.session_meta is None:
            # The session string can still be half-written the instant a session
            # changes; retry next tick rather than latching onto a broken read.
            return self._waiting("Waiting for the session…")

        now = time.monotonic()
        if now - self.last_session_refresh >= SESSION_REFRESH_SECONDS:
            self.last_session_refresh = now
            self._refresh_session_laps()

        self.corner_accumulator.update(snap, self.tracker.corners_per_lap)
        self.tracker.set_corners(self.corner_accumulator.corners)
        lap_completed = self.lap_detector.update(snap.session_num, snap.lap)
        multiplier = _model.session_weight(self.session_meta.session_type)

        if lap_completed:
            estimate = self.tracker.on_lap_completed(snap.incident_count, multiplier)
        else:
            estimate = self.tracker.snapshot(multiplier)

        if self.session_meta.session_type == "practice":
            return self._with_estimate(STATUS_PRACTICE, estimate)

        finished = snap.session_state in (
            _telemetry.SESSION_STATE_COOL_DOWN,
            _telemetry.SESSION_STATE_CHECKERED,
        )
        if finished and not self.session_finalized:
            self._finalize_session(estimate)
            if self.complete_state is not None:
                return self.complete_state

        return self._with_estimate(STATUS_LIVE, estimate)

    def _handle_new_session(self, snap: Any) -> None:
        # Qualify -> Race often skips Checkered/Cool Down entirely, so close the
        # previous session here as well or it never reaches the history.
        if not self.session_finalized:
            previous = (
                _model.session_weight(self.session_meta.session_type)
                if self.session_meta
                else 0.0
            )
            self._finalize_session(self.tracker.snapshot(previous))

        track_info = _session.get_track_info(self.sdk)
        player_info = _session.get_player_info(self.sdk)
        session_meta = _session.get_session_meta(self.sdk, snap.session_num)

        # The SR reported at the start of a session already accounts for the
        # previous one, which is the only moment the real delta can be read back.
        if player_info is not None and player_info.safety_rating is not None:
            self.history.reconcile_last_pending(player_info.safety_rating)

        corners_per_lap = float(FALLBACK_CORNERS_PER_LAP)
        if track_info is not None:
            corners_per_lap, source, confidence = self.tracks.get_corners_per_lap(
                track_info.track_id,
                track_info.name,
                track_info.config_name,
                track_info.num_turns,
            )
            logger.info(
                "SR: %s | %.1f corners/lap (source=%s, confidence=%s)",
                track_info.display_name, corners_per_lap, source, confidence,
            )

        stats = _calibration.compute_stats(self.history.all())
        self.tracker.start_session(
            initial_sr=player_info.safety_rating if player_info else None,
            initial_incident_count=snap.incident_count,
            corners_per_lap=corners_per_lap,
            laps_total=session_meta.session_laps if session_meta else None,
            lic_string=player_info.lic_string if player_info else "",
            lic_level=getattr(player_info, "lic_level", None) if player_info else None,
            confidence=_calibration.confidence_level(stats.n if stats else 0),
        )
        self.lap_detector.reset(snap.session_num, snap.lap)
        self.corner_accumulator.reset()

        self.track_info = track_info
        self.player_info = player_info
        self.session_meta = session_meta
        self.session_finalized = False
        self.complete_state = None

        # Only claim the session as handled once its metadata really parsed;
        # otherwise the next tick retries instead of sitting on a broken read.
        if session_meta is not None:
            self.current_session_num = snap.session_num

    def _refresh_session_laps(self) -> None:
        if self.session_meta is None:
            return
        meta = _session.get_session_meta(self.sdk, self.session_meta.session_num)
        if meta is not None and meta.session_laps != self.session_meta.session_laps:
            self.session_meta = meta
            self.tracker.laps_total = meta.session_laps

    def _finalize_session(self, estimate: Any) -> None:
        """Write a scoreable session to the history and show the closing card."""
        self.session_finalized = True
        if self.session_meta is None or self.session_meta.session_type == "practice":
            return
        if self.tracker.laps_completed == 0:
            return

        self.history.append(
            _history.build_record(
                track_display_name=(
                    self.track_info.display_name if self.track_info else "unknown"
                ),
                track_id=self.track_info.track_id if self.track_info else None,
                car_name=(
                    self.player_info.car_screen_name if self.player_info else "unknown"
                ),
                lic_string=self.player_info.lic_string if self.player_info else "",
                initial_sr=self.tracker.initial_sr,
                estimate=estimate,
                session_type=self.session_meta.session_type,
            )
        )
        self.complete_state = self._with_estimate(STATUS_COMPLETE, estimate)
        self.complete_until = time.monotonic() + SESSION_COMPLETE_SECONDS


__all__ = [
    "SR_FIELDS",
    "STATUS_COMPLETE",
    "STATUS_LIVE",
    "STATUS_OFFLINE",
    "STATUS_PRACTICE",
    "STATUS_WAITING",
    "HubSDK",
    "SafetyRatingEngine",
]
