"""
Local database of "corners per lap" by track configuration.

Resolution order (see docs/RESEARCH.md section 2):
1. A cached or manually curated entry, keyed by TrackID (unique per specific
   configuration - Nordschleife, Combined and GP all have different TrackIDs).
2. The official iRacing value from tracks/official_corners.json.
3. TrackNumTurns from the live session, saved automatically. This is what makes
   a season update a non-event for the overlay: TrackNumTurns IS corners_per_lap,
   so a brand-new layout is scored correctly the first time it is driven.
4. A fixed safety value (fallback_corners_per_lap).

Never assumes a "magic" corner count without recording where it came from.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sr_estimator.tracks")


from app_paths import resource_path, user_file

# Read-only, shipped with the app (bundled into the .exe).
OFFICIAL_CORNERS_PATH = resource_path("tracks", "official_corners.json")


def _load_official_corners() -> dict:
    """OFFICIAL corners-per-lap table (iRacing /data/track/get endpoint).

    436 tracks, keyed by the same `track_id` the SDK exposes as `TrackID`.
    If the file goes missing the app still works via the SDK's TrackNumTurns.
    """
    try:
        raw = json.loads(OFFICIAL_CORNERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Official corner table unavailable (%s); falling back to the SDK", exc)
        return {}
    return {int(t["track_id"]): t for t in raw.get("tracks", []) if t.get("corners_per_lap")}


_OFFICIAL_CORNERS = _load_official_corners()

# Writable: the local cache of resolved/learned tracks.
DEFAULT_PATH = user_file("tracks", "track_data.json")


@dataclass
class TrackRecord:
    track_id: int
    track_name: str
    config_name: str
    corners_per_lap: float
    source: str  # "manual" | "iracing_data_api" | "sdk_track_num_turns" | "generic_fallback"
    confidence: str  # "high" | "low" | "very_low"
    updated_at: str


class TrackDatabase:
    def __init__(self, path: Path = DEFAULT_PATH, fallback_corners_per_lap: int = 10) -> None:
        self._path = path
        self._fallback = fallback_corners_per_lap
        self._records: dict[int, TrackRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._records = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Could not read track_data.json (%s); starting empty", exc)
            raw = []
        self._records = {int(r["track_id"]): TrackRecord(**r) for r in raw}

    def _save(self) -> None:
        payload = [asdict(r) for r in self._records.values()]
        self._path.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")

    def get_corners_per_lap(
        self,
        track_id: int,
        track_name: str,
        config_name: str,
        sdk_num_turns: int,
    ) -> tuple[float, str, str]:
        """Returns (corners_per_lap, source, confidence)."""
        existing = self._records.get(track_id)
        if existing is not None:
            return existing.corners_per_lap, existing.source, existing.confidence

        # First choice: the OFFICIAL iRacing value (tracks/official_corners.json,
        # from the /data/track/get endpoint - 436 tracks). This is exactly the
        # number the SR calculation uses, so there is no reason to prefer the SDK.
        official = _OFFICIAL_CORNERS.get(track_id)
        if official is not None:
            record = TrackRecord(
                track_id=track_id,
                track_name=track_name or official.get("track_name", ""),
                config_name=config_name or official.get("config_name", ""),
                corners_per_lap=float(official["corners_per_lap"]),
                source="iracing_data_api",
                confidence="high",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._records[track_id] = record
            self._save()
            logger.info("Track resolved from the official table: %s (%s) -> %s corners/lap",
                        record.track_name, record.config_name, record.corners_per_lap)
            if sdk_num_turns and sdk_num_turns > 0 and int(sdk_num_turns) != int(record.corners_per_lap):
                # Divergence seen at Nordschleife Industriefahrten (SDK/subsession
                # 73 vs official table 154). Worth logging rather than hiding: the
                # live session's value is what that event actually used.
                logger.warning(
                    "SDK TrackNumTurns (%s) differs from the official table (%s) for "
                    "track_id=%s; using the SDK value as it is that session's own",
                    sdk_num_turns, record.corners_per_lap, track_id,
                )
                record.corners_per_lap = float(sdk_num_turns)
                record.source = "sdk_track_num_turns_override"
                self._save()
            return record.corners_per_lap, record.source, record.confidence

        if sdk_num_turns and sdk_num_turns > 0:
            # This is how a track added in a season update gets scored correctly
            # the very first time it is driven, with no table refresh needed:
            # TrackNumTurns in the session YAML IS corners_per_lap. Verified
            # against every track where both values were observable, and it is
            # the session's own number, so it is authoritative for that session.
            record = TrackRecord(
                track_id=track_id,
                track_name=track_name,
                config_name=config_name,
                corners_per_lap=float(sdk_num_turns),
                source="sdk_track_num_turns",
                confidence="high",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._records[track_id] = record
            self._save()
            logger.info("New track learned from the simulator: %s (%s) -> %s corners/lap",
                        track_name, config_name, sdk_num_turns)
            return record.corners_per_lap, record.source, record.confidence

        logger.warning(
            "TrackNumTurns unavailable for %s (%s); using the generic fallback of %s corners/lap",
            track_name, config_name, self._fallback,
        )
        return float(self._fallback), "generic_fallback", "very_low"

    def get_record(self, track_id: int) -> Optional[TrackRecord]:
        return self._records.get(track_id)

    def set_manual(
        self,
        track_id: int,
        track_name: str,
        config_name: str,
        corners_per_lap: float,
        source: str = "manual",
    ) -> None:
        """Manual correction (editing the file works too), or the update made by
        sr/calibration.py::calibrate_track_corners from reconciled real sessions.
        Either way confidence becomes "high", because the source is no longer the
        SDK's raw approximation."""
        self._records[track_id] = TrackRecord(
            track_id=track_id,
            track_name=track_name,
            config_name=config_name,
            corners_per_lap=corners_per_lap,
            source=source,
            confidence="high",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save()
