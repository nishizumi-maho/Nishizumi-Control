"""
Analyses the history of reconciled sessions and, optionally, re-tunes the
rolling-average window used by sr/model.py. The CPI->SR mapping is NOT
calibratable here: it was measured against iRacing's own result data
(see sr/license_map.py).

Nothing here runs automatically during a live session. It is a separate tool
(`python -m sr.calibration`) on purpose: silently re-tuning parameters every
session would make the estimate hard to audit, which defeats the point.

Typical use once some reconciled sessions have accumulated:
    python -m sr.calibration
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sr.license_map import class_index_from_string
from sr.model import SRModelParams, session_weight

logger = logging.getLogger("sr_estimator.calibration")

MIN_SESSIONS_FOR_FIT = 8
MIN_SESSIONS_PER_TRACK = 5

# Session weights come from sr.model.SESSION_WEIGHTS (the official Sporting
# Code 3.6.1.1 table) and apply to corners AS WELL AS incidents.


@dataclass
class CalibrationStats:
    n: int
    mean_error: float
    mean_absolute_error: float
    bias: float  # same as mean_error; kept separate for semantic clarity


@dataclass
class TrackCalibrationResult:
    track_id: int
    track_name: str
    old_corners_per_lap: float
    new_corners_per_lap: float
    n_sessions: int


def confidence_level(n_reconciled_sessions: int) -> str:
    if n_reconciled_sessions < 5:
        return "Low"
    if n_reconciled_sessions < 20:
        return "Medium"
    return "High"


def _session_multiplier(record: dict) -> float:
    """Sporting Code 3.6.1.1 weight - applies to corners and incidents."""
    return session_weight(record.get("session_type"))


def compute_stats(history: list[dict]) -> Optional[CalibrationStats]:
    reconciled = [r for r in history if r.get("reconciled") and r.get("error") is not None]
    if not reconciled:
        return None
    errors = [r["error"] for r in reconciled]
    n = len(errors)
    mean_error = sum(errors) / n
    mae = sum(abs(e) for e in errors) / n
    return CalibrationStats(n=n, mean_error=mean_error, mean_absolute_error=mae, bias=mean_error)


def fit_window_corners(history: list[dict]) -> Optional[SRModelParams]:
    """Calibrate the rolling window against your own reconciled history.

    There is no free "gain" coefficient any more: the CPI->SR relationship was
    measured and reproduces iRacing's own number to within ~0.007 SR (see
    sr/license_map.py). The only approximate step left is how far one session
    moves the accumulated CPI, and that is what gets tuned here - by direct
    search on the predicted SR error, since the relationship is not linear.
    """
    reconciled = [r for r in history if r.get("reconciled") and r.get("actual_delta") is not None]
    if len(reconciled) < MIN_SESSIONS_FOR_FIT:
        logger.info(
            "Only %s reconciled session(s); %s needed to recalibrate. Keeping current parameters.",
            len(reconciled), MIN_SESSIONS_FOR_FIT,
        )
        return None

    from sr.model import project_sr

    def sse(window: float) -> float:
        params = SRModelParams(window_band1=window)
        total = 0.0
        for r in reconciled:
            sr0 = r.get("initial_sr")
            if sr0 is None or r.get("corners", 0) <= 0:
                continue
            idx = class_index_from_string(r.get("license_class", ""))
            if idx is None:
                continue
            mult = _session_multiplier(r)
            band = int(sr0)
            _, delta, _ = project_sr(sr0, idx, r["corners"] * mult,
                                     r["incidents"] * mult, band=band, params=params)
            total += (delta - r["actual_delta"]) ** 2
        return total

    best_w, best_v = None, None
    for window in range(400, 3001, 25):
        v = sse(float(window))
        if best_v is None or v < best_v:
            best_w, best_v = float(window), v

    if best_w is None:
        logger.warning("Could not calibrate the window: no usable records.")
        return None

    logger.info("Suggested recalibration: window_band1=%.0f (based on %s sessions)",
                best_w, len(reconciled))
    return SRModelParams(window_band1=best_w)


def calibrate_track_corners(history: list[dict], track_db, window_band1: float) -> list[TrackCalibrationResult]:
    """For tracks with enough reconciled sessions, adjust corners_per_lap by
    the scale factor that would have made the model match the observed
    actual_delta at that track. Writes back through
    TrackDatabase.set_manual(..., source="calibrated_from_history").

    Only runs when invoked explicitly (python -m sr.calibration) - never during
    a live session, same rule as the rest of this file.

    Note: with the official corner table in tracks/official_corners.json this
    is rarely needed; it remains useful if iRacing changes a layout's count.
    """
    reconciled = [
        r for r in history
        if r.get("reconciled") and r.get("actual_delta") is not None and r.get("track_id") is not None
    ]

    by_track: dict[int, list[dict]] = {}
    for r in reconciled:
        by_track.setdefault(int(r["track_id"]), []).append(r)

    results: list[TrackCalibrationResult] = []
    if window_band1 <= 0:
        return results
    from sr.model import project_sr

    for track_id, records in by_track.items():
        if len(records) < MIN_SESSIONS_PER_TRACK:
            continue

        existing = track_db.get_record(track_id)
        if existing is None or existing.corners_per_lap <= 0:
            continue

        # Search for the corner scale factor that minimises predicted SR error.
        # Replaces v2's algebraic inversion, which only worked because the old
        # model was linear.
        params = SRModelParams(window_band1=window_band1)
        usable = [r for r in records
                  if r["corners"] > 0 and r.get("initial_sr") is not None
                  and class_index_from_string(r.get("license_class", "")) is not None]
        if not usable:
            continue

        def error_for(scale: float) -> float:
            total = 0.0
            for r in usable:
                idx = class_index_from_string(r.get("license_class", ""))
                mult = _session_multiplier(r)
                _, delta, _ = project_sr(r["initial_sr"], idx,
                                         r["corners"] * scale * mult,
                                         r["incidents"] * mult,
                                         band=int(r["initial_sr"]), params=params)
                total += (delta - r["actual_delta"]) ** 2
            return total

        best_scale, best_err = 1.0, None
        for step in range(30, 301):
            cand = step / 100.0
            err = error_for(cand)
            if best_err is None or err < best_err:
                best_scale, best_err = cand, err

        # Never let calibration more than triple or third a value without manual
        # review - same sanity-clamp philosophy as the rest of the project.
        scale = max(0.3, min(3.0, best_scale))

        new_corners = round(existing.corners_per_lap * scale, 1)
        if abs(new_corners - existing.corners_per_lap) < 0.05:
            continue

        track_db.set_manual(
            track_id, existing.track_name, existing.config_name, new_corners, source="calibrated_from_history"
        )
        results.append(TrackCalibrationResult(
            track_id=track_id,
            track_name=existing.track_name,
            old_corners_per_lap=existing.corners_per_lap,
            new_corners_per_lap=new_corners,
            n_sessions=len(records),
        ))

    return results


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from sr.history import HistoryStore
    from tracks.database import TrackDatabase

    history = HistoryStore().all()
    stats = compute_stats(history)
    if stats is None:
        print("No reconciled sessions yet. Race a few sessions with the overlay running first.")
        return

    print(f"Reconciled sessions: {stats.n}")
    print(f"Confidence: {confidence_level(stats.n)}")
    print(f"Mean error (bias): {stats.mean_error:+.3f}")
    print(f"Mean absolute error (MAE): {stats.mean_absolute_error:.3f}")

    fitted = fit_window_corners(history)
    if fitted is None:
        return

    print("\nSuggested parameter (edit config/settings.json to apply):")
    print(f"  window_band1 = {fitted.window_band1:.0f}")

    track_db = TrackDatabase()
    track_results = calibrate_track_corners(history, track_db, fitted.window_band1)
    if track_results:
        print(f"\n{len(track_results)} track(s) recalibrated in tracks/track_data.json:")
        for res in track_results:
            print(f"  {res.track_name} (id={res.track_id}): "
                  f"{res.old_corners_per_lap:.1f} -> {res.new_corners_per_lap:.1f} corners/lap "
                  f"({res.n_sessions} sessions)")
    else:
        print("\nNo track has enough sessions to recalibrate yet "
              f"(minimum {MIN_SESSIONS_PER_TRACK} reconciled sessions at the same track).")


if __name__ == "__main__":
    _main()
