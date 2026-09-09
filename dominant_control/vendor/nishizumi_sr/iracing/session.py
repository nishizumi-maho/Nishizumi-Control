"""
Parses iRacing's session YAML blocks (WeekendInfo, DriverInfo, SessionInfo).

See docs/RESEARCH.md section 7 for the origin and confidence of each field.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from iracing.sdk import IRacingSDK

logger = logging.getLogger("sr_estimator.session")

_LIC_STRING_NUMBER_RE = re.compile(r"(\d+\.\d+)\s*$")


@dataclass
class TrackInfo:
    track_id: int
    name: str
    config_name: str
    display_name: str
    num_turns: int  # aproximacao vinda do SDK, ver RESEARCH.md 3.2


@dataclass
class PlayerInfo:
    car_idx: int
    user_id: Optional[int]
    user_name: str
    car_screen_name: str
    lic_string: str
    lic_level: Optional[int]  # LicLevel do SDK (1..24): classe + faixa de SR
    lic_sub_level: Optional[int]
    safety_rating: Optional[float]  # None when undeterminable - never invented


@dataclass
class SessionMeta:
    session_num: int
    session_type_raw: str
    session_type: str  # classificado: "practice" | "qualify" | "warmup" | "race" | "time_trial" | "unknown"
    session_name: str
    session_laps: Optional[int]  # None when the session is timed rather than lap-limited


def get_track_info(sdk: IRacingSDK) -> Optional[TrackInfo]:
    weekend = sdk.get("WeekendInfo")
    if not weekend:
        return None
    try:
        return TrackInfo(
            track_id=int(weekend.get("TrackID", -1)),
            name=str(weekend.get("TrackName", "unknown")),
            config_name=str(weekend.get("TrackConfigName", "")),
            display_name=str(weekend.get("TrackDisplayName", weekend.get("TrackName", "unknown"))),
            num_turns=int(weekend.get("TrackNumTurns", 0) or 0),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("WeekendInfo in an unexpected format: %s", exc)
        return None


def _parse_lic_level(value: object) -> Optional[int]:
    """SDK LicLevel: 1..24 (4 levels per class, one per whole SR band).

    Confirmed against the /data API on 2026-08-09: level 13 = Class B band 1.xx,
    level 20 = Class A band 4.xx. It is the most reliable source of the class and
    the band - better than the LicString letter alone (see sr/license_map.py)."""
    try:
        level = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if 1 <= level <= 24:
        return level
    logger.warning("LicLevel=%r outside the 1..24 range; ignoring", value)
    return None


def _parse_sr(lic_sub_level: object, lic_string: str) -> Optional[float]:
    # High-confidence convention (not formally documented, see RESEARCH.md section 7):
    # LicSubLevel costuma ser SR * 100.
    if isinstance(lic_sub_level, (int, float)) and lic_sub_level > 0:
        candidate = lic_sub_level / 100.0
        if 0.0 <= candidate <= 5.0:
            return round(candidate, 2)
        logger.warning("LicSubLevel=%s outside the plausible range; ignoring", lic_sub_level)

    # Fallback: extrai o numero final de algo como "A 4.99" ou "Rookie 1.34".
    match = _LIC_STRING_NUMBER_RE.search(lic_string or "")
    if match:
        try:
            candidate = float(match.group(1))
            if 0.0 <= candidate <= 5.0:
                return round(candidate, 2)
        except ValueError:
            pass

    logger.warning("Could not determine SR from LicSubLevel=%r LicString=%r",
                    lic_sub_level, lic_string)
    return None


def get_player_info(sdk: IRacingSDK) -> Optional[PlayerInfo]:
    driver_info = sdk.get("DriverInfo")
    if not driver_info:
        return None

    player_car_idx = sdk.get("PlayerCarIdx")
    if player_car_idx is None:
        player_car_idx = driver_info.get("DriverCarIdx")
    if player_car_idx is None:
        return None
    player_car_idx = int(player_car_idx)

    drivers = driver_info.get("Drivers") or []
    player_raw = next((d for d in drivers if int(d.get("CarIdx", -1)) == player_car_idx), None)
    if player_raw is None:
        logger.warning("PlayerCarIdx=%s not found in the Drivers list", player_car_idx)
        return None

    lic_sub_level = player_raw.get("LicSubLevel")
    lic_string = str(player_raw.get("LicString", ""))
    sr = _parse_sr(lic_sub_level, lic_string)

    return PlayerInfo(
        car_idx=player_car_idx,
        user_id=player_raw.get("UserID"),
        user_name=str(player_raw.get("UserName", "")),
        car_screen_name=str(player_raw.get("CarScreenName", "")),
        lic_string=lic_string,
        lic_level=_parse_lic_level(player_raw.get("LicLevel")),
        lic_sub_level=int(lic_sub_level) if isinstance(lic_sub_level, (int, float)) else None,
        safety_rating=sr,
    )


def classify_session(session_type_raw: str) -> str:
    """Substring matching: there is no closed official list of SessionType
    values (see RESEARCH.md 3.4)."""
    s = (session_type_raw or "").lower()
    if "time trial" in s:
        return "time_trial"
    if "practice" in s or "testing" in s:
        return "practice"
    if "qualify" in s:
        return "qualify"
    if "warmup" in s:
        return "warmup"
    if "race" in s:
        return "race"
    return "unknown"


def get_session_meta(sdk: IRacingSDK, session_num: int) -> Optional[SessionMeta]:
    session_info = sdk.get("SessionInfo")
    if not session_info:
        return None
    sessions = session_info.get("Sessions") or []
    raw = next((s for s in sessions if int(s.get("SessionNum", -1)) == session_num), None)
    if raw is None:
        return None

    laps = raw.get("SessionLaps")
    laps_int: Optional[int]
    try:
        laps_int = int(laps)
    except (TypeError, ValueError):
        laps_int = None  # timed session (e.g. "unlimited") rather than lap-limited

    session_type_raw = str(raw.get("SessionType", ""))
    return SessionMeta(
        session_num=session_num,
        session_type_raw=session_type_raw,
        session_type=classify_session(session_type_raw),
        session_name=str(raw.get("SessionName", "")),
        session_laps=laps_int,
    )
