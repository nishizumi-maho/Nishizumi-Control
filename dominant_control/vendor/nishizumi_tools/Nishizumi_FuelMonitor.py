#!/usr/bin/env python3
"""Nishizumi FuelMonitor: fuel strategy and endurance intelligence for iRacing.

The application is intentionally delivered as one Python file.  Its telemetry
and strategy engines do not depend on the UI, while the interface is built
entirely with PySide6/Qt.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from typing import Any, Sequence


OPENXR_HOST = "127.0.0.1"
OPENXR_PORT = 61337
OPENXR_URL = f"http://{OPENXR_HOST}:{OPENXR_PORT}/"
RACING = 4
CHECKERED = 5
COOL_DOWN = 6
YELLOW_FLAGS = 0x0008 | 0x4000 | 0x8000
LITER_TO_GALLON = 0.2641720524


def _get_appdata_dir() -> Path:
    root = Path(os.getenv("APPDATA") or Path.home() / ".config")
    path = root / "NishizumiTools"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TrackCondition(str, Enum):
    DRY = "dry"
    WET = "wet"


class TelemetrySource(str, Enum):
    LOCAL = "local"
    TEAM = "team"


class RelativeSide(str, Enum):
    AHEAD = "ahead"
    BEHIND = "behind"


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    STABLE = "stable"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class AppConfig:
    cars_ahead: int = 3
    cars_behind: int = 3
    same_class_only: bool = False
    previous_stints_shown: int = 3
    team_trend_window: int = 3
    prediction_history_window: int = 3
    prediction_min_stint_laps: int = 3
    exclude_caution_stints: bool = True
    match_tire_compound: bool = True
    overlay_width: int = 720
    update_hz: int = 10

    def validate(self) -> None:
        self.cars_ahead = max(0, min(5, int(self.cars_ahead)))
        self.cars_behind = max(0, min(5, int(self.cars_behind)))
        self.previous_stints_shown = max(1, min(5, int(self.previous_stints_shown)))
        self.team_trend_window = max(2, min(5, int(self.team_trend_window)))
        self.prediction_history_window = max(1, min(10, int(self.prediction_history_window)))
        self.prediction_min_stint_laps = max(1, int(self.prediction_min_stint_laps))
        self.overlay_width = max(480, min(1100, int(self.overlay_width)))
        self.update_hz = max(2, min(30, int(self.update_hz)))


@dataclass(frozen=True, slots=True)
class DriverMeta:
    car_idx: int
    user_id: int
    name: str
    team_name: str
    car_number: str
    car_class_id: int
    car_class_color: str = "#7C8AA5"

    @property
    def key(self) -> str:
        if self.user_id > 0:
            return f"uid:{self.user_id}"
        normalized = re.sub(r"\s+", " ", self.name.strip().casefold())
        team = re.sub(r"\s+", " ", self.team_name.strip().casefold())
        return f"name:{normalized}|team:{team}"

    @property
    def short_name(self) -> str:
        parts = [part for part in self.name.strip().split() if part]
        if not parts:
            return "Unknown"
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0][0]}. {parts[-1]}"


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    connected: bool
    session_id: int = 0
    session_time: float = 0.0
    session_time_remaining: float | None = None
    session_laps_remaining: int | None = None
    session_state: int = 0
    session_flags: int = 0
    player_car_idx: int = -1
    telemetry_source: TelemetrySource = TelemetrySource.LOCAL
    pace_car_idx: int = -1
    player_fuel_level: float | None = None
    player_fuel_level_pct: float | None = None
    player_lap: int = -1
    player_lap_dist_pct: float = -1.0
    player_last_lap_time: float | None = None
    player_best_lap_time: float | None = None
    display_units: int | None = None
    is_on_track: bool = False
    on_pit_road: bool = False
    declared_wet: bool = False
    drivers: dict[int, DriverMeta] = field(default_factory=dict)
    car_lap_completed: tuple[int, ...] = ()
    car_lap_dist_pct: tuple[float, ...] = ()
    car_on_pit_road: tuple[bool, ...] = ()
    car_track_surface: tuple[int, ...] = ()
    car_last_lap_time: tuple[float, ...] = ()
    car_position: tuple[int, ...] = ()
    car_class_position: tuple[int, ...] = ()
    car_tire_compound: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class StintRecordView:
    driver_name: str
    driver_short_name: str
    laps: int
    complete: bool
    condition: TrackCondition
    had_caution: bool


@dataclass(frozen=True, slots=True)
class RelativeCarView:
    car_idx: int
    side: RelativeSide
    track_gap_pct: float
    race_lap_difference: int
    car_number: str
    team_name: str
    driver_name: str
    driver_short_name: str
    class_position: int
    class_color: str
    current_stint_laps: int
    current_stint_known: bool
    on_pit_road: bool
    condition: TrackCondition
    previous_stints: tuple[StintRecordView, ...]
    team_last_stint: StintRecordView | None
    team_recent_laps: tuple[int, ...]
    team_trend_delta: float | None
    team_trend_direction: TrendDirection
    predicted_total_laps: float | None
    predicted_remaining_laps: float | None
    prediction_samples: int


@dataclass(frozen=True, slots=True)
class EnduranceViewModel:
    connected: bool
    status: str
    player_current_stint_laps: int = 0
    player_current_stint_known: bool = False
    player_current_stint_progress: float = 0.0
    cars_ahead: tuple[RelativeCarView, ...] = ()
    cars_behind: tuple[RelativeCarView, ...] = ()
    tracked_driver_name: str = ""
    player_predicted_total_laps: float | None = None
    player_predicted_remaining_laps: float | None = None
    player_prediction_basis: str = ""


def value_at(values: Sequence[Any], index: int, default: Any) -> Any:
    if index < 0 or index >= len(values):
        return default
    value = values[index]
    return default if value is None else value


@dataclass(slots=True)
class StintRecord:
    car_idx: int
    driver_key: str
    driver_name: str
    team_name: str
    car_number: str
    car_class_id: int
    start_lap: int
    end_lap: int
    laps: int
    start_time: float
    end_time: float
    condition: TrackCondition
    tire_compound: int | None
    complete: bool
    had_caution: bool
    condition_changed: bool


@dataclass(slots=True)
class ActiveStint:
    driver_key: str
    driver_name: str
    team_name: str
    car_number: str
    car_class_id: int
    start_lap: int
    start_time: float
    condition: TrackCondition
    tire_compound: int | None
    known_start: bool
    had_caution: bool = False
    condition_changed: bool = False


@dataclass(slots=True)
class CarState:
    car_idx: int
    last_on_pit_road: bool | None = None
    last_lap_completed: int | None = None
    active: ActiveStint | None = None
    pit_entry_pending: bool = False
    pit_entry_lap: int | None = None
    history: list[StintRecord] = field(default_factory=list)


class EnduranceTracker:
    """Observe stints and build honest opponent forecasts.

    An opponent forecast only uses complete stints from that same driver in
    equivalent conditions.  Team-mates remain visible as team trend/history,
    but they are never silently mixed into the individual forecast.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._session_id: int | None = None
        self._last_session_time = 0.0
        self._last_session_state = 0
        self._cars: dict[int, CarState] = {}

    @property
    def records(self) -> tuple[StintRecord, ...]:
        records = [record for state in self._cars.values() for record in state.history]
        return tuple(sorted(records, key=lambda record: record.end_time))

    def reset(self, session_id: int | None = None) -> None:
        self._session_id = session_id
        self._last_session_time = 0.0
        self._last_session_state = 0
        self._cars.clear()

    def update(self, snapshot: TelemetrySnapshot) -> EnduranceViewModel:
        if not snapshot.connected:
            return EnduranceViewModel(False, "WAITING FOR iRACING")

        session_changed = self._session_id is None or snapshot.session_id != self._session_id
        clock_restarted = snapshot.session_time + 30.0 < self._last_session_time
        if session_changed or clock_restarted:
            self.reset(snapshot.session_id)

        started_now = self._last_session_state != RACING and snapshot.session_state == RACING
        if started_now and self._last_session_state not in (0, RACING):
            self._cars.clear()

        self._last_session_time = snapshot.session_time
        race_visible = snapshot.session_state in (RACING, CHECKERED, COOL_DOWN)
        tracked_driver_name = ""
        predicted_total: float | None = None
        predicted_remaining: float | None = None
        prediction_basis = ""

        if race_visible:
            self._observe_all_cars(snapshot)
            ahead, behind = self._build_relatives(snapshot)
            player_state = self._cars.get(snapshot.player_car_idx)
            player_lap = int(
                value_at(snapshot.car_lap_completed, snapshot.player_car_idx, snapshot.player_lap)
            )
            player_laps, player_known = self._current_stint_progress(player_state, player_lap)
            player_pct = float(
                value_at(
                    snapshot.car_lap_dist_pct,
                    snapshot.player_car_idx,
                    snapshot.player_lap_dist_pct,
                )
            )
            player_progress = float(player_laps)
            if player_known and 0.0 <= player_pct <= 1.0:
                player_progress += player_pct
            player_meta = snapshot.drivers.get(snapshot.player_car_idx)
            if player_meta is not None:
                tracked_driver_name = player_meta.short_name
                condition = (
                    TrackCondition.WET
                    if snapshot.declared_wet
                    else TrackCondition.DRY
                )
                tire = self._optional_int(
                    value_at(snapshot.car_tire_compound, snapshot.player_car_idx, None)
                )
                predicted_total, samples = self._predict_for_driver(
                    player_meta,
                    condition,
                    tire,
                )
                if predicted_total is not None and samples > 0:
                    prediction_basis = "DRIVER"
                else:
                    _last, recent, _delta, _direction = self._team_trend(
                        player_state,
                        condition,
                        tire,
                    )
                    if recent:
                        predicted_total = float(median(recent))
                        prediction_basis = "TEAM"
                if predicted_total is not None and player_known:
                    predicted_remaining = max(0.0, predicted_total - player_progress)
        else:
            ahead, behind = (), ()
            player_laps, player_known = 0, False
            player_progress = 0.0

        status = {
            RACING: "LIVE",
            CHECKERED: "CHECKERED",
            COOL_DOWN: "FINISHED",
        }.get(snapshot.session_state, "WAITING FOR THE START")
        self._last_session_state = snapshot.session_state
        return EnduranceViewModel(
            connected=True,
            status=status,
            player_current_stint_laps=player_laps,
            player_current_stint_known=player_known,
            player_current_stint_progress=player_progress,
            cars_ahead=ahead,
            cars_behind=behind,
            tracked_driver_name=tracked_driver_name,
            player_predicted_total_laps=predicted_total,
            player_predicted_remaining_laps=predicted_remaining,
            player_prediction_basis=prediction_basis,
        )

    def _observe_all_cars(self, snapshot: TelemetrySnapshot) -> None:
        count = max(
            len(snapshot.car_lap_completed),
            len(snapshot.car_lap_dist_pct),
            len(snapshot.car_on_pit_road),
        )
        indices = set(range(count)) | set(snapshot.drivers)
        for car_idx in sorted(indices):
            if car_idx == snapshot.pace_car_idx:
                continue
            lap = int(value_at(snapshot.car_lap_completed, car_idx, -1))
            surface = int(value_at(snapshot.car_track_surface, car_idx, 3))
            if lap < 0 or surface < 0:
                continue
            meta = snapshot.drivers.get(car_idx) or self._unknown_driver(car_idx)
            on_pit = bool(value_at(snapshot.car_on_pit_road, car_idx, False))
            tire = self._optional_int(value_at(snapshot.car_tire_compound, car_idx, None))
            condition = TrackCondition.WET if snapshot.declared_wet else TrackCondition.DRY
            self._observe_car(
                car_idx=car_idx,
                meta=meta,
                lap=lap,
                on_pit=on_pit,
                session_time=snapshot.session_time,
                condition=condition,
                tire_compound=tire,
                under_caution=bool(snapshot.session_flags & YELLOW_FLAGS),
            )

    def _observe_car(
        self,
        *,
        car_idx: int,
        meta: DriverMeta,
        lap: int,
        on_pit: bool,
        session_time: float,
        condition: TrackCondition,
        tire_compound: int | None,
        under_caution: bool,
    ) -> None:
        state = self._cars.setdefault(car_idx, CarState(car_idx))

        if state.last_lap_completed is not None and lap + 1 < state.last_lap_completed:
            state.active = None
            state.last_on_pit_road = None
            state.pit_entry_pending = False
            state.pit_entry_lap = None

        if state.last_on_pit_road is None:
            state.last_on_pit_road = on_pit
            state.last_lap_completed = lap
            if not on_pit:
                state.active = self._new_active(
                    meta,
                    lap,
                    session_time,
                    condition,
                    tire_compound,
                    known_start=lap <= 0,
                )
                state.active.had_caution = under_caution
            return

        entered_pits = not state.last_on_pit_road and on_pit
        exited_pits = state.last_on_pit_road and not on_pit

        if entered_pits and state.active is not None:
            # The pit-entry cone precedes start/finish on most tracks.  Wait for
            # LapCompleted to advance so the outgoing stint is not one lap short.
            state.pit_entry_pending = True
            state.pit_entry_lap = lap

        if (
            on_pit
            and state.pit_entry_pending
            and state.pit_entry_lap is not None
            and lap > state.pit_entry_lap
        ):
            self._finish_active(state, lap, session_time)
            state.pit_entry_pending = False
            state.pit_entry_lap = None

        if exited_pits:
            if state.pit_entry_pending and state.active is not None:
                self._finish_active(state, lap, session_time)
            state.pit_entry_pending = False
            state.pit_entry_lap = None
            state.active = self._new_active(
                meta, lap, session_time, condition, tire_compound, known_start=True
            )
            state.active.had_caution = under_caution

        active = state.active
        if active is not None:
            current_laps = max(0, lap - active.start_lap)
            if active.driver_key != meta.key and current_laps == 0 and not on_pit:
                # DriverInfo may update a few frames after pit exit.  Before a
                # timed lap is completed it is safe to reattribute the new stint.
                active.driver_key = meta.key
                active.driver_name = meta.name
                active.team_name = meta.team_name
                active.car_number = meta.car_number
                active.car_class_id = meta.car_class_id
            if active.condition != condition:
                active.condition_changed = True
            if under_caution:
                active.had_caution = True

        state.last_on_pit_road = on_pit
        state.last_lap_completed = lap

    @staticmethod
    def _new_active(
        meta: DriverMeta,
        lap: int,
        session_time: float,
        condition: TrackCondition,
        tire_compound: int | None,
        known_start: bool,
    ) -> ActiveStint:
        return ActiveStint(
            driver_key=meta.key,
            driver_name=meta.name,
            team_name=meta.team_name,
            car_number=meta.car_number,
            car_class_id=meta.car_class_id,
            start_lap=lap,
            start_time=session_time,
            condition=condition,
            tire_compound=tire_compound,
            known_start=known_start,
        )

    @staticmethod
    def _finish_active(state: CarState, end_lap: int, end_time: float) -> None:
        active = state.active
        if active is None:
            return
        laps = max(0, end_lap - active.start_lap)
        if laps > 0:
            state.history.append(
                StintRecord(
                    car_idx=state.car_idx,
                    driver_key=active.driver_key,
                    driver_name=active.driver_name,
                    team_name=active.team_name,
                    car_number=active.car_number,
                    car_class_id=active.car_class_id,
                    start_lap=active.start_lap,
                    end_lap=end_lap,
                    laps=laps,
                    start_time=active.start_time,
                    end_time=end_time,
                    condition=active.condition,
                    tire_compound=active.tire_compound,
                    complete=active.known_start,
                    had_caution=active.had_caution,
                    condition_changed=active.condition_changed,
                )
            )
        state.active = None

    def _build_relatives(
        self, snapshot: TelemetrySnapshot
    ) -> tuple[tuple[RelativeCarView, ...], tuple[RelativeCarView, ...]]:
        player_idx = snapshot.player_car_idx
        if player_idx < 0:
            return (), ()
        player_pct = float(value_at(snapshot.car_lap_dist_pct, player_idx, -1.0))
        player_lap = int(value_at(snapshot.car_lap_completed, player_idx, -1))
        player_meta = snapshot.drivers.get(player_idx)
        if not 0.0 <= player_pct <= 1.0 or player_lap < 0:
            return (), ()

        candidates: list[tuple[float, RelativeCarView]] = []
        condition = TrackCondition.WET if snapshot.declared_wet else TrackCondition.DRY
        for car_idx, meta in snapshot.drivers.items():
            if car_idx in (player_idx, snapshot.pace_car_idx):
                continue
            if (
                self.config.same_class_only
                and player_meta is not None
                and meta.car_class_id != player_meta.car_class_id
            ):
                continue
            surface = int(value_at(snapshot.car_track_surface, car_idx, -1))
            other_pct = float(value_at(snapshot.car_lap_dist_pct, car_idx, -1.0))
            other_lap = int(value_at(snapshot.car_lap_completed, car_idx, -1))
            if surface < 0 or not 0.0 <= other_pct <= 1.0 or other_lap < 0:
                continue

            delta = other_pct - player_pct
            if delta > 0.5:
                delta -= 1.0
            elif delta <= -0.5:
                delta += 1.0
            if abs(delta) < 0.00001:
                continue

            side = RelativeSide.AHEAD if delta > 0 else RelativeSide.BEHIND
            state = self._cars.get(car_idx)
            current_laps, known = self._current_stint_progress(state, other_lap)
            tire = self._optional_int(value_at(snapshot.car_tire_compound, car_idx, None))
            predicted, samples = self._predict_for_driver(meta, condition, tire)
            predicted_remaining = (
                max(0.0, predicted - current_laps)
                if predicted is not None and known
                else None
            )
            team_last, team_recent, team_delta, team_direction = self._team_trend(
                state, condition, tire
            )
            row = RelativeCarView(
                car_idx=car_idx,
                side=side,
                track_gap_pct=abs(delta) * 100.0,
                race_lap_difference=other_lap - player_lap,
                car_number=meta.car_number,
                team_name=meta.team_name,
                driver_name=meta.name,
                driver_short_name=meta.short_name,
                class_position=int(value_at(snapshot.car_class_position, car_idx, 0)),
                class_color=meta.car_class_color,
                current_stint_laps=current_laps,
                current_stint_known=known,
                on_pit_road=bool(value_at(snapshot.car_on_pit_road, car_idx, False)),
                condition=condition,
                previous_stints=self._history_views(state),
                team_last_stint=team_last,
                team_recent_laps=team_recent,
                team_trend_delta=team_delta,
                team_trend_direction=team_direction,
                predicted_total_laps=predicted,
                predicted_remaining_laps=predicted_remaining,
                prediction_samples=samples,
            )
            candidates.append((delta, row))

        ahead = [row for delta, row in sorted(candidates, key=lambda item: item[0]) if delta > 0]
        behind = [
            row
            for delta, row in sorted(candidates, key=lambda item: item[0], reverse=True)
            if delta < 0
        ]
        return (
            tuple(ahead[: self.config.cars_ahead]),
            tuple(behind[: self.config.cars_behind]),
        )

    def _predict_for_driver(
        self,
        meta: DriverMeta,
        condition: TrackCondition,
        tire_compound: int | None,
    ) -> tuple[float | None, int]:
        valid: list[StintRecord] = []
        for record in self.records:
            if record.driver_key != meta.key or not record.complete:
                continue
            if record.laps < self.config.prediction_min_stint_laps:
                continue
            if record.condition != condition or record.condition_changed:
                continue
            if self.config.exclude_caution_stints and record.had_caution:
                continue
            if (
                self.config.match_tire_compound
                and tire_compound is not None
                and record.tire_compound is not None
                and record.tire_compound != tire_compound
            ):
                continue
            valid.append(record)
        valid = valid[-self.config.prediction_history_window :]
        if not valid:
            return None, 0
        return float(median(record.laps for record in valid)), len(valid)

    def _history_views(self, state: CarState | None) -> tuple[StintRecordView, ...]:
        if state is None:
            return ()
        records = state.history[-self.config.previous_stints_shown :]
        return tuple(self._record_view(record) for record in reversed(records))

    def _team_trend(
        self,
        state: CarState | None,
        condition: TrackCondition,
        tire_compound: int | None,
    ) -> tuple[StintRecordView | None, tuple[int, ...], float | None, TrendDirection]:
        if state is None or not state.history:
            return None, (), None, TrendDirection.UNKNOWN

        last_stint = self._record_view(state.history[-1])
        valid: list[StintRecord] = []
        for record in state.history:
            if not record.complete or record.condition_changed:
                continue
            if record.laps < self.config.prediction_min_stint_laps:
                continue
            if record.condition != condition:
                continue
            if self.config.exclude_caution_stints and record.had_caution:
                continue
            if (
                self.config.match_tire_compound
                and tire_compound is not None
                and record.tire_compound is not None
                and record.tire_compound != tire_compound
            ):
                continue
            valid.append(record)

        valid = valid[-self.config.team_trend_window :]
        recent_laps = tuple(record.laps for record in valid)
        if len(valid) < 2:
            return last_stint, recent_laps, None, TrendDirection.UNKNOWN
        delta = float(valid[-1].laps - valid[-2].laps)
        if delta >= 0.75:
            direction = TrendDirection.UP
        elif delta <= -0.75:
            direction = TrendDirection.DOWN
        else:
            direction = TrendDirection.STABLE
        return last_stint, recent_laps, delta, direction

    @staticmethod
    def _record_view(record: StintRecord) -> StintRecordView:
        parts = [part for part in record.driver_name.strip().split() if part]
        short_name = (
            f"{parts[0][0]}. {parts[-1]}"
            if len(parts) >= 2
            else (parts[0] if parts else "Unknown")
        )
        return StintRecordView(
            driver_name=record.driver_name,
            driver_short_name=short_name,
            laps=record.laps,
            complete=record.complete,
            condition=record.condition,
            had_caution=record.had_caution,
        )

    @staticmethod
    def _current_stint_progress(state: CarState | None, lap: int) -> tuple[int, bool]:
        if state is None or state.active is None:
            return 0, False
        return max(0, lap - state.active.start_lap), state.active.known_start

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _unknown_driver(car_idx: int) -> DriverMeta:
        return DriverMeta(
            car_idx=car_idx,
            user_id=0,
            name=f'Car {car_idx}',
            team_name="Unknown team",
            car_number=str(car_idx),
            car_class_id=0,
        )


@dataclass(slots=True)
class FuelStintState:
    fuel_start: float
    lap_start: int
    lapdist_start: float
    started_at: float


@dataclass(frozen=True, slots=True)
class FuelView:
    connected: bool = False
    telemetry_ready: bool = False
    status: str = "Waiting for iRacing"
    fuel_level: float | None = None
    average_per_lap: float | None = None
    delta_to_target: float | None = None
    within_target: bool = False
    last_lap_used: float | None = None
    remaining_laps: float | None = None
    remaining_seconds: float | None = None
    lap_time_estimate: float | None = None
    can_finish: bool = False
    on_pit_road: bool = False
    planned_laps: int | None = None
    estimated_laps: int | None = None
    observed_stint_laps: int = 0
    observed_stint_known: bool = False
    strategy_text: str = "Waiting for data to work out the strategy."
    strategy_state: str = "neutral"
    strategy_details: tuple[str, ...] = ()
    plus_one_target: float | None = None
    minus_one_target: float | None = None
    pit_summary_active: bool = False
    pit_summary_average: float | None = None


class FuelStrategyEngine:
    """Fuel-consumption and race-strategy engine independent from Qt."""

    def __init__(self) -> None:
        self.refuel_threshold_l = 0.3
        self.avg_min_progress = 0.05
        self.anomaly_threshold = 0.3
        self._estimated_tank_capacity_l: float | None = None
        self._strategy_cache: tuple[tuple[Any, ...], str, str] | None = None
        self._strategy_cache_until = 0.0
        self.reset()

    def reset(self) -> None:
        self._estimated_tank_capacity_l = None
        self._strategy_cache = None
        self._strategy_cache_until = 0.0
        self._stint: FuelStintState | None = None
        self._last_fuel: float | None = None
        self._last_lap: int | None = None
        self._lap_start_fuel: float | None = None
        self._last_lap_used: float | None = None
        self._last_lap_time: float | None = None
        self._lap_consumptions: list[float] = []
        self._lap_times: list[float] = []
        self._last_on_pitroad: bool | None = None
        self._pit_hold_until = 0.0
        self._pit_summary_until = 0.0
        self._pit_summary_average: float | None = None

    def disconnected_view(self, status: str = "Waiting for a connection to iRacing") -> FuelView:
        return FuelView(connected=False, status=status)

    def update(
        self,
        snapshot: TelemetrySnapshot,
        target: float | None,
        finish_buffer: float,
    ) -> FuelView:
        if not snapshot.connected:
            return self.disconnected_view()

        fuel = snapshot.player_fuel_level
        lap = snapshot.player_lap
        lapdist = snapshot.player_lap_dist_pct
        usable_telemetry = (
            fuel is not None
            and fuel >= 0
            and lap >= 0
            and 0.0 <= lapdist <= 1.0
            and (snapshot.is_on_track or snapshot.on_pit_road)
        )
        if not usable_telemetry:
            self.reset()
            return FuelView(
                connected=True,
                status="Waiting for the car's telemetry",
                on_pit_road=snapshot.on_pit_road,
            )

        assert fuel is not None
        finish_buffer = max(0.0, float(finish_buffer))
        self._update_tank_capacity_estimate(fuel, snapshot.player_fuel_level_pct)
        self._update_stint(snapshot)

        progress = self._compute_progress(lap, lapdist)
        rolling_average: float | None = None
        if progress is not None and progress >= self.avg_min_progress and self._stint is not None:
            fuel_used = max(0.0, self._stint.fuel_start - fuel)
            if progress > 0:
                rolling_average = fuel_used / progress
        average = self._filtered_average(rolling_average)

        usable_fuel = max(0.0, fuel - finish_buffer)
        remaining_laps = usable_fuel / average if average is not None and average > 0 else None
        lap_time = self._estimated_lap_time(
            snapshot.player_last_lap_time,
            snapshot.player_best_lap_time,
        )
        remaining_seconds: float | None = None
        can_finish = False
        if remaining_laps is not None and lap_time is not None and lap_time > 0:
            fuel_seconds = remaining_laps * lap_time
            session_remaining = snapshot.session_time_remaining
            finite_session = (
                session_remaining is not None
                and 0.0 <= session_remaining <= 48.0 * 3600.0
            )
            can_finish = bool(finite_session and fuel_seconds >= float(session_remaining))
            remaining_seconds = (
                min(fuel_seconds, float(session_remaining)) if finite_session else fuel_seconds
            )

        session_laps = self._estimate_session_laps_remaining(
            snapshot.session_time_remaining,
            snapshot.session_laps_remaining,
            lap_time,
        )
        strategy_text, strategy_state, strategy_details = self._build_race_strategy(
            average,
            fuel,
            session_laps,
            finish_buffer,
        )

        delta = average - target if average is not None and target is not None else None
        within_target = bool(
            average is not None and target is not None and average <= target
        )
        estimated_laps = math.floor(remaining_laps) if remaining_laps is not None else None
        planned_laps = (
            math.floor(usable_fuel / target) if target is not None and target > 0 else None
        )
        plus_target = (
            usable_fuel / (estimated_laps + 1)
            if estimated_laps is not None and estimated_laps + 1 > 0
            else None
        )
        minus_laps = max(estimated_laps - 1, 1) if estimated_laps is not None and estimated_laps >= 1 else None
        minus_target = usable_fuel / minus_laps if minus_laps else None

        observed_laps = max(0, math.floor(progress or 0.0))
        observed_known = bool(self._stint and self._stint.lap_start <= 0)

        now = time.monotonic()
        if snapshot.on_pit_road and not bool(self._last_on_pitroad):
            self._pit_summary_average = self._stint_average(average)
            self._pit_summary_until = now + 10.0
        self._last_on_pitroad = snapshot.on_pit_road

        status = "PIT" if now < self._pit_hold_until else "ACTIVE"
        return FuelView(
            connected=True,
            telemetry_ready=True,
            status=status,
            fuel_level=fuel,
            average_per_lap=average,
            delta_to_target=delta,
            within_target=within_target,
            last_lap_used=self._last_lap_used,
            remaining_laps=remaining_laps,
            remaining_seconds=remaining_seconds,
            lap_time_estimate=lap_time,
            can_finish=can_finish,
            on_pit_road=snapshot.on_pit_road,
            planned_laps=planned_laps,
            estimated_laps=estimated_laps,
            observed_stint_laps=observed_laps,
            observed_stint_known=observed_known,
            strategy_text=strategy_text,
            strategy_state=strategy_state,
            strategy_details=tuple(strategy_details),
            plus_one_target=plus_target,
            minus_one_target=minus_target,
            pit_summary_active=now < self._pit_summary_until,
            pit_summary_average=self._pit_summary_average,
        )

    def _update_stint(self, snapshot: TelemetrySnapshot) -> None:
        assert snapshot.player_fuel_level is not None
        fuel = snapshot.player_fuel_level
        lap = snapshot.player_lap
        lapdist = snapshot.player_lap_dist_pct
        now = time.monotonic()
        if self._stint is None:
            self._stint = FuelStintState(fuel, lap, lapdist, now)
            self._last_fuel = fuel
            self._last_lap = lap
            self._lap_start_fuel = fuel
            return

        if self._last_fuel is not None and fuel - self._last_fuel >= self.refuel_threshold_l:
            self._pit_hold_until = now + 4.0
            self._stint = FuelStintState(fuel, lap, lapdist, now)
            self._lap_start_fuel = fuel
            self._last_lap_used = None

        if self._last_lap is not None and lap > self._last_lap and self._lap_start_fuel is not None:
            lap_progress = self._compute_progress(lap, lapdist)
            lap_used = max(0.0, self._lap_start_fuel - fuel)
            self._lap_start_fuel = fuel
            if lap_progress is None or lap_progress < 1:
                self._last_lap_used = None
                self._last_lap_time = None
            else:
                self._last_lap_used = lap_used
                valid_green_lap = (
                    lap_used > 0
                    and not snapshot.on_pit_road
                    and not bool(snapshot.session_flags & YELLOW_FLAGS)
                    and not self._is_anomalous_lap(lap_used)
                )
                if valid_green_lap:
                    self._append_window(self._lap_consumptions, lap_used, 20)
                last_time = snapshot.player_last_lap_time
                if last_time is not None and last_time > 0:
                    self._last_lap_time = last_time
                    if valid_green_lap and not self._is_anomalous_lap_time(last_time):
                        self._append_window(self._lap_times, last_time, 20)
                else:
                    self._last_lap_time = None

        self._last_fuel = fuel
        self._last_lap = lap

    def _compute_progress(self, lap: int, lapdist: float) -> float | None:
        if self._stint is None:
            return None
        progress = (lap - self._stint.lap_start) + (lapdist - self._stint.lapdist_start)
        return progress if progress >= 0 else None

    def _filtered_average(self, fallback: float | None) -> float | None:
        if fallback is None:
            return sum(self._lap_consumptions) / len(self._lap_consumptions) if self._lap_consumptions else None
        if self._lap_consumptions:
            return (sum(self._lap_consumptions) + fallback) / (len(self._lap_consumptions) + 1)
        return fallback

    def _stint_average(self, fallback: float | None) -> float | None:
        return sum(self._lap_consumptions) / len(self._lap_consumptions) if self._lap_consumptions else fallback

    def _is_anomalous_lap(self, lap_used: float) -> bool:
        if lap_used <= 0:
            return True
        if len(self._lap_consumptions) < 3:
            return False
        average = sum(self._lap_consumptions) / len(self._lap_consumptions)
        return bool(average > 0 and abs(lap_used - average) / average >= self.anomaly_threshold)

    def _is_anomalous_lap_time(self, lap_time: float) -> bool:
        if lap_time <= 0:
            return True
        if len(self._lap_times) < 3:
            return False
        average = sum(self._lap_times) / len(self._lap_times)
        return bool(average > 0 and abs(lap_time - average) / average >= 0.35)

    def _estimated_lap_time(
        self, last_lap_time: float | None, best_lap_time: float | None
    ) -> float | None:
        if self._lap_times:
            return sum(self._lap_times) / len(self._lap_times)
        for value in (self._last_lap_time, last_lap_time, best_lap_time):
            if value is not None and value > 0:
                return value
        return None

    def _update_tank_capacity_estimate(
        self, fuel_level: float, fuel_level_pct: float | None
    ) -> None:
        if fuel_level_pct is None or fuel_level_pct <= 0.02 or fuel_level_pct > 1.02:
            return
        estimate = fuel_level / fuel_level_pct
        if estimate <= 0:
            return
        if self._estimated_tank_capacity_l is None:
            self._estimated_tank_capacity_l = estimate
        else:
            self._estimated_tank_capacity_l = max(self._estimated_tank_capacity_l, estimate)

    @staticmethod
    def _estimate_session_laps_remaining(
        session_time_remaining: float | None,
        session_laps_remaining: int | None,
        lap_time_estimate: float | None,
    ) -> float | None:
        if (
            session_time_remaining is not None
            and session_time_remaining > 0
            and lap_time_estimate is not None
            and lap_time_estimate > 0
        ):
            return max(0.0, session_time_remaining / lap_time_estimate)
        if session_laps_remaining is not None and session_laps_remaining >= 0:
            return float(session_laps_remaining)
        return None

    @staticmethod
    def _calculate_required_stops(
        laps_to_go: float, current_laps: float, full_tank_laps: float
    ) -> int:
        if laps_to_go <= current_laps:
            return 0
        if full_tank_laps <= 0:
            return 999
        return max(0, math.ceil((laps_to_go - current_laps) / full_tank_laps))

    def _scenario_average_map(self, average: float | None) -> dict[str, float]:
        if average is None or average <= 0:
            return {}
        samples = sorted(value for value in self._lap_consumptions if value > 0)
        if len(samples) >= 4:
            band_size = max(1, math.ceil(len(samples) * 0.35))
            save_average = sum(samples[:band_size]) / band_size
            push_average = sum(samples[-band_size:]) / band_size
        else:
            save_average = average * 0.97
            push_average = average * 1.03
        save_average = min(average * 0.995, max(average * 0.88, save_average))
        push_average = max(average * 1.005, min(average * 1.12, push_average))
        return {"save": save_average, "current": average, "push": push_average}

    def _build_scenario_plan(
        self,
        average: float,
        fuel_level: float,
        laps_to_go: float,
        finish_buffer: float,
    ) -> dict[str, float | None]:
        usable_fuel = max(0.0, fuel_level - finish_buffer)
        current_laps = usable_fuel / average if average > 0 else 0.0
        plan: dict[str, float | None] = {
            "current_laps": current_laps,
            "stops": None,
            "margin_laps": current_laps - laps_to_go,
            "save_to_cut_one": None,
            "push_room_same_stop": None,
        }
        if laps_to_go <= 0:
            plan["stops"] = 0.0
            return plan
        capacity = self._estimated_tank_capacity_l
        if capacity is None or capacity <= 0:
            if current_laps >= laps_to_go:
                plan["stops"] = 0.0
                max_average = usable_fuel / laps_to_go
                plan["push_room_same_stop"] = max(0.0, max_average - average)
            return plan

        full_tank_laps = capacity / average
        stops = self._calculate_required_stops(laps_to_go, current_laps, full_tank_laps)
        total_available_laps = current_laps + stops * full_tank_laps
        max_average = (fuel_level + stops * capacity - finish_buffer) / laps_to_go
        plan["stops"] = float(stops)
        plan["margin_laps"] = total_available_laps - laps_to_go
        plan["push_room_same_stop"] = max(0.0, max_average - average)
        if stops > 0:
            max_average_one_less = (
                fuel_level + (stops - 1) * capacity - finish_buffer
            ) / laps_to_go
            plan["save_to_cut_one"] = max(0.0, average - max_average_one_less)
        else:
            plan["save_to_cut_one"] = 0.0
        return plan

    @staticmethod
    def _format_stops(value: float | None) -> str:
        if value is None:
            return "--"
        stops = max(0, int(round(value)))
        return f'{stops} stop' if stops == 1 else f'{stops} stops'

    def _build_race_strategy(
        self,
        average: float | None,
        fuel_level: float,
        laps_to_go: float | None,
        finish_buffer: float,
    ) -> tuple[str, str, list[str]]:
        if average is None or average <= 0 or laps_to_go is None or laps_to_go <= 0:
            return "Waiting for data to work out the strategy.", "neutral", []

        scenarios = self._scenario_average_map(average)
        if not scenarios:
            return "Waiting for data to work out the strategy.", "neutral", []
        plans = {
            name: self._build_scenario_plan(value, fuel_level, laps_to_go, finish_buffer)
            for name, value in scenarios.items()
        }
        current = plans["current"]
        save = plans["save"]
        push = plans["push"]
        details = [
            f'Push / current / save: {scenarios['push']:.3f} / {scenarios['current']:.3f} / {scenarios['save']:.3f} L/lap'
        ]
        if current["stops"] is not None:
            details.append(
                f'Estimated stops: push {self._format_stops(push['stops'])} · current {self._format_stops(current['stops'])} · save {self._format_stops(save['stops'])}'
            )

        if current["stops"] == 0:
            push_room = current["push_room_same_stop"] or 0.0
            margin = current["margin_laps"] or 0.0
            if push_room > 0.02:
                text = (
                    f'~{laps_to_go:.1f} laps to the end · you can spend {push_room:.2f} L/lap without adding a stop'
                )
            elif margin > 0.35:
                text = f'~{laps_to_go:.1f} laps to the end · fuel is comfortable'
            else:
                text = f'~{laps_to_go:.1f} laps to the end · fuel is on the edge'
            state = "good"
        elif (
            save["stops"] is not None
            and current["stops"] is not None
            and save["stops"] < current["stops"]
        ):
            save_needed = current["save_to_cut_one"] or 0.0
            text = (
                f'~{laps_to_go:.1f} laps to the end · save {save_needed:.2f} L/lap to cut one stop'
            )
            details.append(f'Target to cut one stop: -{save_needed:.3f} L/lap')
            state = "warning"
        elif (
            push["stops"] is not None
            and current["stops"] is not None
            and push["stops"] > current["stops"]
        ):
            room = current["push_room_same_stop"] or 0.0
            text = (
                f'~{laps_to_go:.1f} laps to the end · pushing risks an extra stop; margin {room:.2f} L/lap'
            )
            details.append(f'Safe margin to push: +{room:.3f} L/lap')
            state = "danger"
        elif current["stops"] is not None:
            margin = current["margin_laps"] or 0.0
            text = (
                f'~{laps_to_go:.1f} laps to the end · current pace means {self._format_stops(current['stops'])}'
            )
            state = "good" if margin >= 0.6 else "warning"
        else:
            required = max(0.0, (fuel_level - finish_buffer) / laps_to_go)
            save_needed = max(0.0, average - required)
            text = (
                f'~{laps_to_go:.1f} laps to the end · save {save_needed:.2f} L/lap to avoid stopping'
            )
            details.append(f'Target usage to the end: {required:.3f} L/lap')
            state = "warning"

        cache_key = (
            round(laps_to_go, 1),
            round(average, 3),
            round(scenarios["save"], 3),
            round(scenarios["push"], 3),
            round(current["stops"] if current["stops"] is not None else -1, 0),
            round(finish_buffer, 3),
        )
        now = time.monotonic()
        if self._strategy_cache and now < self._strategy_cache_until:
            old_key, old_text, old_state = self._strategy_cache
            if (
                abs(cache_key[0] - old_key[0]) < 0.4
                and abs(cache_key[1] - old_key[1]) < 0.04
                and cache_key[4] == old_key[4]
            ):
                return old_text, old_state, details
        self._strategy_cache = (cache_key, text, state)
        self._strategy_cache_until = now + 3.0
        return text, state, details

    @staticmethod
    def _append_window(values: list[float], value: float, limit: int) -> None:
        values.append(float(value))
        if len(values) > limit:
            del values[: len(values) - limit]


# ---- Runtime adapter and PySide6 interface ---------------------------------

try:
    import irsdk  # type: ignore
except ImportError:
    irsdk = None  # type: ignore[assignment]


class IRacingSDKSource:
    """Defensive adapter around pyirsdk's live shared-memory reader."""

    def __init__(self, retry_seconds: float = 2.0):
        self.retry_seconds = retry_seconds
        self.manual_team_car_number = ""
        self._ir: Any = None
        self._next_retry = 0.0
        self.last_error = ""

    def read(self) -> TelemetrySnapshot:
        if irsdk is None:
            self.last_error = "pyirsdk is not installed"
            return TelemetrySnapshot(connected=False)

        if self._ir is None and time.monotonic() >= self._next_retry:
            self._connect()
        if self._ir is None:
            return TelemetrySnapshot(connected=False)

        try:
            connected = getattr(self._ir, "is_connected", None)
            if connected is not None and not bool(connected):
                self._disconnect()
                return TelemetrySnapshot(connected=False)
            return self._read_connected()
        except Exception as exc:  # shared memory can disappear between reads
            self.last_error = f'Could not read the SDK: {exc}'
            self._disconnect()
            return TelemetrySnapshot(connected=False)

    def close(self) -> None:
        self._disconnect(schedule_retry=False)

    def _connect(self) -> None:
        self._next_retry = time.monotonic() + self.retry_seconds
        try:
            candidate = irsdk.IRSDK()
            if candidate.startup():
                self._ir = candidate
                self.last_error = ""
            else:
                candidate.shutdown()
                self.last_error = "Waiting for an iRacing session"
        except Exception as exc:
            self._ir = None
            self.last_error = f'Could not start pyirsdk: {exc}'

    def _disconnect(self, schedule_retry: bool = True) -> None:
        if self._ir is not None:
            try:
                self._ir.shutdown()
            except Exception:
                pass
        self._ir = None
        if schedule_retry:
            self._next_retry = time.monotonic() + self.retry_seconds

    def _read_connected(self) -> TelemetrySnapshot:
        frozen = False
        freeze = getattr(self._ir, "freeze_var_buffer_latest", None)
        unfreeze = getattr(self._ir, "unfreeze_var_buffer_latest", None)
        if callable(freeze):
            freeze()
            frozen = True
        try:
            driver_info = self._safe("DriverInfo", {}) or {}
            if not isinstance(driver_info, dict):
                driver_info = {}
            raw_player_idx = self._as_int(
                self._safe("PlayerCarIdx", driver_info.get("DriverCarIdx", -1)), -1
            )
            driver_car_idx = self._as_int(driver_info.get("DriverCarIdx", -1), -1)
            local_on_track = bool(
                self._safe("IsOnTrack", False)
                or self._safe("IsOnTrackCar", False)
            )
            drivers = self._extract_drivers(driver_info)
            session_id = self._as_int(self._safe("SessionUniqueID", 0), 0)
            if session_id <= 0:
                weekend = self._safe("WeekendInfo", {}) or {}
                if isinstance(weekend, dict):
                    session_id = self._as_int(weekend.get("SubSessionID", 0), 0)

            car_laps = self._tuple_int(self._safe("CarIdxLapCompleted", ()))
            car_pct = self._tuple_float(self._safe("CarIdxLapDistPct", ()))
            car_pit = self._tuple_bool(self._safe("CarIdxOnPitRoad", ()))
            car_surface = self._tuple_int(self._safe("CarIdxTrackSurface", ()))
            car_last_lap = self._tuple_float(self._safe("CarIdxLastLapTime", ()))

            automatic_team_idx = driver_car_idx
            if automatic_team_idx < 0 and raw_player_idx >= 0:
                automatic_team_idx = raw_player_idx
            manual_number = self.manual_team_car_number.strip()
            manual_team_idx = next(
                (
                    car_idx
                    for car_idx, meta in drivers.items()
                    if manual_number and meta.car_number.strip() == manual_number
                ),
                -1,
            )

            if local_on_track and raw_player_idx >= 0:
                player_idx = raw_player_idx
                telemetry_source = TelemetrySource.LOCAL
            elif manual_number:
                player_idx = manual_team_idx
                telemetry_source = TelemetrySource.TEAM
            else:
                # CamCarIdx is intentionally never used here: the spectator
                # camera may be focused on an unrelated competitor.
                player_idx = automatic_team_idx
                telemetry_source = TelemetrySource.TEAM

            tracked_lap = int(value_at(car_laps, player_idx, -1))
            tracked_pct = float(value_at(car_pct, player_idx, -1.0))
            tracked_pit = bool(value_at(car_pit, player_idx, False))
            tracked_surface = int(value_at(car_surface, player_idx, -1))
            remote_source = telemetry_source != TelemetrySource.LOCAL
            if remote_source:
                player_lap = tracked_lap
                player_pct = tracked_pct
                on_pit_road = tracked_pit
                is_on_track = (
                    tracked_lap >= 0
                    and 0.0 <= tracked_pct <= 1.0
                    and tracked_surface > 0
                    and not tracked_pit
                )
                player_last_lap = self._optional_float(
                    value_at(car_last_lap, player_idx, None)
                )
            else:
                player_lap = self._as_int(
                    self._safe("Lap", tracked_lap), tracked_lap
                )
                player_pct = self._as_float(
                    self._safe("LapDistPct", tracked_pct), tracked_pct
                )
                on_pit_road = bool(self._safe("OnPitRoad", tracked_pit))
                is_on_track = local_on_track
                player_last_lap = self._optional_float(
                    self._safe(
                        "LapLastLapTime",
                        value_at(car_last_lap, player_idx, None),
                    )
                )

            remote_fuel_available = (
                telemetry_source == TelemetrySource.TEAM
                and player_idx >= 0
                and player_idx == automatic_team_idx
            )
            fuel_level = (
                self._optional_float(self._safe("FuelLevel", None))
                if not remote_source or remote_fuel_available
                else None
            )
            fuel_level_pct = (
                self._optional_float(self._safe("FuelLevelPct", None))
                if not remote_source or remote_fuel_available
                else None
            )

            return TelemetrySnapshot(
                connected=True,
                session_id=session_id,
                session_time=self._as_float(self._safe("SessionTime", 0.0), 0.0),
                session_time_remaining=self._optional_float(
                    self._safe("SessionTimeRemain", None)
                ),
                session_laps_remaining=self._optional_int(
                    self._safe("SessionLapsRemainEx", None)
                ),
                session_state=self._as_int(self._safe("SessionState", 0), 0),
                session_flags=self._as_int(self._safe("SessionFlags", 0), 0),
                player_car_idx=player_idx,
                telemetry_source=telemetry_source,
                pace_car_idx=self._as_int(driver_info.get("PaceCarIdx", -1), -1),
                player_fuel_level=fuel_level,
                player_fuel_level_pct=fuel_level_pct,
                player_lap=player_lap,
                player_lap_dist_pct=player_pct,
                player_last_lap_time=player_last_lap,
                player_best_lap_time=self._optional_float(
                    self._safe("LapBestLapTime", None)
                    if not remote_source
                    else player_last_lap
                ),
                display_units=self._optional_int(self._safe("DisplayUnits", None)),
                is_on_track=is_on_track,
                on_pit_road=on_pit_road,
                declared_wet=bool(self._safe("WeatherDeclaredWet", False)),
                drivers=drivers,
                car_lap_completed=car_laps,
                car_lap_dist_pct=car_pct,
                car_on_pit_road=car_pit,
                car_track_surface=car_surface,
                car_last_lap_time=car_last_lap,
                car_position=self._tuple_int(self._safe("CarIdxPosition", ())),
                car_class_position=self._tuple_int(
                    self._safe("CarIdxClassPosition", ())
                ),
                car_tire_compound=self._tuple_int(
                    self._safe("CarIdxTireCompound", ())
                ),
            )
        finally:
            if frozen and callable(unfreeze):
                unfreeze()

    def _safe(self, key: str, default: Any) -> Any:
        try:
            value = self._ir[key]
        except Exception:
            return default
        return default if value is None else value

    @classmethod
    def _extract_drivers(cls, driver_info: dict[str, Any]) -> dict[int, DriverMeta]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for raw in driver_info.get("Drivers", []) or []:
            if not isinstance(raw, dict):
                continue
            car_idx = cls._as_int(raw.get("CarIdx", -1), -1)
            if (
                car_idx < 0
                or cls._as_int(raw.get("CarIsPaceCar", 0), 0) != 0
            ):
                continue
            grouped.setdefault(car_idx, []).append(raw)

        result: dict[int, DriverMeta] = {}
        for car_idx, candidates in grouped.items():
            # Team sessions contain several entries for the same CarIdx.  The
            # active driver is the non-spectator entry; choosing the local user
            # here would incorrectly attribute a team-mate's stint to the viewer.
            chosen = next(
                (
                    item
                    for item in reversed(candidates)
                    if cls._as_int(item.get("IsSpectator", 0), 0) == 0
                ),
                candidates[-1],
            )
            name = str(
                chosen.get("UserName")
                or chosen.get("AbbrevName")
                or f'Car {car_idx}'
            )
            team_name = str(chosen.get("TeamName") or name)
            result[car_idx] = DriverMeta(
                car_idx=car_idx,
                user_id=cls._as_int(chosen.get("UserID", 0), 0),
                name=name,
                team_name=team_name,
                car_number=str(chosen.get("CarNumber") or car_idx),
                car_class_id=cls._as_int(chosen.get("CarClassID", 0), 0),
                car_class_color=cls._parse_color(chosen.get("CarClassColor")),
            )
        return result

    @staticmethod
    def _parse_color(value: Any) -> str:
        if isinstance(value, str):
            raw = value.strip().lower()
            try:
                if raw.startswith(("0x", "#")):
                    number = int(raw.removeprefix("0x").removeprefix("#"), 16)
                else:
                    number = int(raw, 10)
            except ValueError:
                return "#7C8AA5"
        else:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return "#7C8AA5"
        return f"#{number & 0xFFFFFF:06X}"

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _tuple_int(cls, values: Any) -> tuple[int, ...]:
        try:
            return tuple(cls._as_int(item, -1) for item in (values or ()))
        except TypeError:
            return ()

    @classmethod
    def _tuple_float(cls, values: Any) -> tuple[float, ...]:
        try:
            return tuple(cls._as_float(item, -1.0) for item in (values or ()))
        except TypeError:
            return ()

    @staticmethod
    def _tuple_bool(values: Any) -> tuple[bool, ...]:
        try:
            return tuple(bool(item) for item in (values or ()))
        except TypeError:
            return ()


class _OpenXRHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class OpenXRDashboardServer:
    """Local-only telemetry page intended for an OpenKneeboard Web Dashboard tab."""

    def __init__(self, host: str = OPENXR_HOST, port: int = OPENXR_PORT):
        self.host = host
        self.port = int(port)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self.empty_state()
        self._server: _OpenXRHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.last_error = ""

    @staticmethod
    def empty_state() -> dict[str, Any]:
        return {
            "connected": False,
            "time": "--:--:--",
            "laps": "WAITING FOR IRACING",
            "destination": "STINT",
            "current": "CURRENT -- L",
            "fuel": "--",
            "average": "--.-- L/lap",
            "delta": "--",
            "average_state": "neutral",
            "plan": "-- L",
            "estimate": "-- L",
            "comparison": "-- L",
            "comparison_state": "neutral",
            "ahead": "",
            "behind": "",
        }

    @property
    def running(self) -> bool:
        return self._server is not None

    def update(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._state = dict(state)

    def snapshot_json(self) -> bytes:
        with self._lock:
            state = dict(self._state)
        return json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def start(self) -> bool:
        if self._server is not None:
            return True

        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = self.path.partition("?")[0]
                if path in {"/", "/index.html"}:
                    body = OPENXR_DASHBOARD_HTML.encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                    status = 200
                elif path == "/api/state":
                    body = dashboard.snapshot_json()
                    content_type = "application/json; charset=utf-8"
                    status = 200
                else:
                    body = b"Not found"
                    content_type = "text/plain; charset=utf-8"
                    status = 404

                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            server = _OpenXRHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self.last_error = f'Could not open {OPENXR_URL}: {exc}'
            return False

        self._server = server
        self.last_error = ""
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="FuelMonitor-OpenXR",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)


try:
    from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
    from PySide6.QtGui import (
        QAction,
        QColor,
        QCloseEvent,
        QFont,
        QIcon,
        QMouseEvent,
        QPainter,
        QPixmap,
        QResizeEvent,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMenu,
        QPushButton,
        QSizePolicy,
        QSlider,
        QSpinBox,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "PySide6 is required by the Nishizumi FuelMonitor. Install it with: pip install PySide6"
    ) from exc


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--:--"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_lap_time(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "--:--.---"
    minutes, remainder = divmod(seconds, 60.0)
    return f"{int(minutes):02d}:{remainder:06.3f}"


def format_laps(value: float | None) -> str:
    if value is None:
        return "--"
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def plan_comparison(
    planned_laps: int | None,
    estimated_laps: int | None,
) -> tuple[str, str, str]:
    if planned_laps is None:
        return "neutral", "-- L", "Set the target to compare the estimate against the plan."
    if estimated_laps is None:
        return "neutral", "-- L", "Waiting for valid laps to estimate the stint."

    difference = estimated_laps - planned_laps
    if difference >= 1:
        return (
            "ahead",
            f"+{difference} V",
            f'The estimate is {difference} lap(s) above the plan.',
        )
    if difference <= -1:
        return (
            "behind",
            f"{difference} V",
            f'The estimate is {abs(difference)} lap(s) below the plan.',
        )
    return "onplan", "0 V", "The estimate is on plan."


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class RelativeRowWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("relativeRow")
        self._ui_scale = 1.0
        self._show_prediction = True
        self._show_team_trend = True
        self._show_history = True

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 10, 0)
        root.setSpacing(9)

        self.class_bar = QFrame()
        self.class_bar.setFixedWidth(4)
        root.addWidget(self.class_bar)

        self.direction = QLabel("↑")
        self.direction.setObjectName("direction")
        self.direction.setFixedWidth(17)
        self.direction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.direction)

        identity = QVBoxLayout()
        identity.setSpacing(0)
        identity.setContentsMargins(0, 6, 0, 5)
        identity_top = QHBoxLayout()
        identity_top.setSpacing(6)
        self.number = QLabel("#00")
        self.number.setObjectName("carNumber")
        self.driver = QLabel("Driver")
        self.driver.setObjectName("driverName")
        identity_top.addWidget(self.number)
        identity_top.addWidget(self.driver, 1)
        identity.addLayout(identity_top)
        self.team = QLabel("Team")
        self.team.setObjectName("teamName")
        identity.addWidget(self.team)
        root.addLayout(identity, 4)

        stint = QVBoxLayout()
        stint.setSpacing(1)
        stint.setContentsMargins(0, 5, 0, 4)
        self.current = QLabel("CURRENT --")
        self.current.setObjectName("currentStint")
        self.estimate = QLabel("DRIVER: no history")
        self.estimate.setObjectName("estimate")
        self.team_trend = QLabel("TEAM: --")
        self.team_trend.setObjectName("teamTrend")
        self.history = QLabel("PREV.: --")
        self.history.setObjectName("history")
        for label in (self.current, self.estimate, self.team_trend, self.history):
            stint.addWidget(label)
        root.addLayout(stint, 7)
        self.apply_display_options(True, True, True, 1.0)

    def apply_display_options(
        self,
        show_prediction: bool,
        show_team_trend: bool,
        show_history: bool,
        ui_scale: float,
    ) -> None:
        self._show_prediction = bool(show_prediction)
        self._show_team_trend = bool(show_team_trend)
        self._show_history = bool(show_history)
        self._ui_scale = max(0.80, min(1.80, float(ui_scale)))
        self.estimate.setVisible(self._show_prediction)
        self.team_trend.setVisible(self._show_team_trend)
        self.history.setVisible(self._show_history)
        optional_lines = sum(
            (self._show_prediction, self._show_team_trend, self._show_history)
        )
        self.setFixedHeight(int((44 + optional_lines * 9) * self._ui_scale))
        self.class_bar.setFixedWidth(max(4, int(4 * self._ui_scale)))
        self.direction.setFixedWidth(max(17, int(17 * self._ui_scale)))

    def set_data(self, car: RelativeCarView | None) -> None:
        if car is None:
            self.hide()
            return
        self.show()
        ahead = car.side == RelativeSide.AHEAD
        self.direction.setText("↑" if ahead else "↓")
        self.direction.setProperty("side", "ahead" if ahead else "behind")
        repolish(self.direction)
        self.class_bar.setStyleSheet(
            f"background-color: {car.class_color}; border-radius: 2px;"
        )
        self.number.setText(f"#{car.car_number}")
        self.driver.setText(car.driver_short_name)
        self.driver.setToolTip(car.driver_name)

        identity_bits: list[str] = []
        if car.class_position > 0:
            identity_bits.append(f"P{car.class_position}")
        if car.race_lap_difference:
            identity_bits.append(f'{car.race_lap_difference:+d} lap')
        identity_bits.append(car.team_name)
        self.team.setText(" · ".join(identity_bits))
        self.team.setToolTip(car.team_name)

        prefix = "" if car.current_stint_known else "≥"
        pit = " · PIT" if car.on_pit_road else ""
        wet = " · WET" if car.condition == TrackCondition.WET else ""
        self.current.setText(f'CURRENT  {prefix}{car.current_stint_laps}v{pit}{wet}')

        if car.on_pit_road:
            self.estimate.setText("DRIVER: stint finished")
        elif car.predicted_total_laps is None:
            self.estimate.setText("DRIVER: no comparable history")
        elif car.predicted_remaining_laps is None:
            self.estimate.setText(
                f'DRIVER: ~{format_laps(car.predicted_total_laps)}v total · {car.prediction_samples} amostra(s)'
            )
        else:
            self.estimate.setText(
                f'DRIVER: ~{format_laps(car.predicted_total_laps)} laps total · ~{format_laps(car.predicted_remaining_laps)}v · {car.prediction_samples} amostra(s)'
            )

        self._set_team_trend(car)
        if not car.previous_stints:
            self.history.setText("PREV.: --")
            self.history.setToolTip("")
        else:
            items: list[str] = []
            for record in car.previous_stints:
                partial = "≥" if not record.complete else ""
                caution = " SC" if record.had_caution else ""
                condition = " W" if record.condition == TrackCondition.WET else ""
                items.append(
                    f"{record.driver_short_name} {partial}{record.laps}v{caution}{condition}"
                )
            self.history.setText("PREV.: " + " · ".join(items))
            self.history.setToolTip(
                "Recent stints and who drove them. SC = caution; W = wet; ≥ = partially observed."
            )

    def _set_team_trend(self, car: RelativeCarView) -> None:
        last = car.team_last_stint
        if last is None:
            self.team_trend.setText("TEAM: no earlier stint observed")
            return
        partial = "≥" if not last.complete else ""
        caution = " SC" if last.had_caution else ""
        wet = " W" if last.condition == TrackCondition.WET else ""
        text = (
            f'TEAM: last {last.driver_short_name} {partial}{last.laps}v{caution}{wet}'
        )
        if len(car.team_recent_laps) >= 2:
            sequence = "→".join(str(laps) for laps in car.team_recent_laps)
            arrow = {
                TrendDirection.UP: " ↑",
                TrendDirection.DOWN: " ↓",
                TrendDirection.STABLE: " ↔",
            }.get(car.team_trend_direction, "")
            delta = (
                f" ({car.team_trend_delta:+.0f})"
                if car.team_trend_delta not in (None, 0)
                else ""
            )
            text += f" · {sequence}{arrow}{delta}"
        self.team_trend.setText(text)
        self.team_trend.setToolTip(
            "Trend over the car's valid stints, even with different drivers. It stays separate from the individual forecast."
        )


class RelativeSection(QFrame):
    MAX_ROWS = 5

    def __init__(self, title: str, row_count: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("relativeSection")
        self._row_limit = max(0, min(self.MAX_ROWS, int(row_count)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.caption = QLabel(title)
        self.caption.setObjectName("sectionTitle")
        layout.addWidget(self.caption)
        self.rows = [RelativeRowWidget() for _ in range(self.MAX_ROWS)]
        for row in self.rows:
            layout.addWidget(row)

    def set_rows(self, cars: tuple[RelativeCarView, ...]) -> None:
        for index, row in enumerate(self.rows):
            row.set_data(
                cars[index]
                if index < self._row_limit and index < len(cars)
                else None
            )

    def set_limit(self, limit: int) -> None:
        self._row_limit = max(0, min(self.MAX_ROWS, int(limit)))

    def apply_display_options(
        self,
        show_prediction: bool,
        show_team_trend: bool,
        show_history: bool,
        ui_scale: float,
    ) -> None:
        for row in self.rows:
            row.apply_display_options(
                show_prediction,
                show_team_trend,
                show_history,
                ui_scale,
            )


class FuelMonitorWindow(QWidget):
    def __init__(self, config: AppConfig | None = None):
        super().__init__()
        self.settings = QSettings("NishizumiTools", "FuelMonitor")
        self.config = config or AppConfig()
        self.config.same_class_only = self._setting_bool(
            "same_class_only", self.config.same_class_only
        )
        self.config.cars_ahead = self._setting_int("cars_ahead", self.config.cars_ahead)
        self.config.cars_behind = self._setting_int("cars_behind", self.config.cars_behind)
        self.config.overlay_width = self._setting_int(
            "overlay_width", self.config.overlay_width
        )
        self.config.validate()

        self.source = IRacingSDKSource()
        saved_team_car = self.settings.value("team_car_number", "")
        self.source.manual_team_car_number = str(saved_team_car or "").strip()
        self.endurance_tracker = EnduranceTracker(self.config)
        self.fuel_engine = FuelStrategyEngine()
        self._last_fuel_view = FuelView()
        self._fuel_context: tuple[int, int, TelemetrySource] | None = None
        self._was_connected = False
        self._display_units = 1
        self._unit_label = "L"
        self._locked_target: float | None = None
        self._locked_buffer: float | None = None
        self._drag_offset: QPoint | None = None
        self._click_through = False
        self._position_locked = self._setting_bool("position_locked", False)
        self._ui_scale = max(0.80, min(1.80, self._setting_float("ui_scale", 1.25)))
        self._window_opacity = max(
            0.65, min(1.0, self._setting_float("window_opacity", 1.0))
        )
        self._high_contrast = self._setting_bool("high_contrast", True)
        self.openxr_server = OpenXRDashboardServer()
        self._openxr_enabled = False
        self._team_car_choices_signature: tuple[Any, ...] | None = None
        self._last_layout_signature: tuple[Any, ...] | None = None
        self._shutdown_complete = False

        self._initial_target_l = self._setting_float("target_liters", 2.50)
        self._initial_buffer_l = max(0.0, self._setting_float("buffer_liters", 0.0))

        self._build_window()
        self._build_ui()
        self._build_unlock_window()
        self._build_tray()
        self._restore_position()

        self._load_customization_controls()
        self.set_details_visible(self._setting_bool("show_details", False))
        self.set_metrics_visible(self._setting_bool("show_metrics_card", True))
        self.set_rivals_visible(self._setting_bool("show_rivals", True))
        self.set_customization_visible(self._setting_bool("show_customization", False))
        self.same_class_checkbox.setChecked(self.config.same_class_only)
        self.set_position_locked(self._position_locked)
        # Never reopen in click-through mode: the user must always be able to
        # interact with the overlay after starting it.
        self.settings.setValue("click_through", False)
        self.set_click_through(False)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(max(33, int(1000 / self.config.update_hz)))
        self._poll()

    def _build_window(self) -> None:
        self.setObjectName("fuelMonitorWindow")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(self._window_opacity)
        self.setFixedWidth(self.config.overlay_width)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("shell")
        root.addWidget(self.shell)
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(10, 7, 10, 9)
        shell_layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(5)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.data_button = self._small_button("DATA", checkable=True)
        self.rivals_button = self._small_button("RIVALS", checkable=True)
        self.details_button = self._small_button("EXTRAS", checkable=True)
        self.customize_button = self._small_button("OPTIONS", checkable=True)
        self.openxr_button = self._small_button("VR", checkable=True)
        self.edit_lock_button = self._small_button("MOVE", checkable=True)
        self.details_button.setToolTip("Show or hide the extra scenarios")
        self.customize_button.setToolTip("Customize the content, font and size")
        self.openxr_button.setToolTip("Turn on the simplified OpenXR panel")
        self.edit_lock_button.setToolTip(
            "Locks the position only. The button stays clickable so you can unlock it."
        )
        self.close_button = self._small_button("×")
        self.close_button.setObjectName("closeButton")
        self.close_button.setFixedWidth(26)
        header.addWidget(self.data_button)
        header.addWidget(self.rivals_button)
        header.addWidget(self.details_button)
        header.addWidget(self.customize_button)
        header.addWidget(self.openxr_button)
        header.addWidget(self.edit_lock_button)
        header.addStretch(1)
        header.addWidget(self.status_dot)
        header.addWidget(self.close_button)
        shell_layout.addLayout(header)

        self.fuel_card = QFrame()
        self.fuel_card.setObjectName("fuelCard")
        fuel_layout = QHBoxLayout(self.fuel_card)
        fuel_layout.setContentsMargins(16, 12, 15, 12)
        fuel_layout.setSpacing(20)

        time_box = QVBoxLayout()
        time_box.setSpacing(0)
        caption = QLabel("STINT TIME LEFT")
        caption.setObjectName("eyebrow")
        self.remaining_time_label = QLabel("--:--:--")
        self.remaining_time_label.setObjectName("bigTime")
        self.remaining_laps_label = QLabel("waiting for telemetry")
        self.remaining_laps_label.setObjectName("lapsRemaining")
        self.remaining_laps_label.setWordWrap(True)
        time_box.addWidget(caption)
        time_box.addWidget(self.remaining_time_label)
        time_box.addWidget(self.remaining_laps_label)
        fuel_layout.addLayout(time_box, 5)

        stats = QVBoxLayout()
        stats.setSpacing(5)
        stats.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.fuel_badge = QLabel("NO CONNECTION")
        self.fuel_badge.setObjectName("fuelBadge")
        self.fuel_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fuel_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.source_badge = QLabel("TEAM")
        self.source_badge.setObjectName("sourceBadge")
        self.source_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_badge.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.source_badge.hide()

        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        badge_row.addWidget(self.fuel_badge)
        badge_row.addWidget(self.source_badge)
        badge_row.addStretch(1)

        average_row = QHBoxLayout()
        average_row.setSpacing(8)
        self.average_label = QLabel("--.-- L/lap")
        self.average_label.setObjectName("averageValue")
        self.delta_label = QLabel("(--)")
        self.delta_label.setObjectName("deltaValue")
        average_row.addWidget(self.average_label)
        average_row.addWidget(self.delta_label)
        average_row.addStretch(1)

        self.current_stint_label = QLabel("CURRENT STINT  --")
        self.current_stint_label.setObjectName("fuelDetailStrong")
        stats.addLayout(badge_row)
        stats.addLayout(average_row)
        stats.addWidget(self.current_stint_label)
        # Give the compact stint summary enough horizontal room for a larger
        # average/delta without competing with the countdown.
        fuel_layout.addLayout(stats, 5)
        shell_layout.addWidget(self.fuel_card)

        self.metrics_card = QFrame()
        self.metrics_card.setObjectName("metricsCard")
        metrics_layout = QHBoxLayout(self.metrics_card)
        metrics_layout.setContentsMargins(10, 9, 10, 9)
        metrics_layout.setSpacing(7)

        def metric_tile(caption_text: str, initial_value: str) -> tuple[QFrame, QLabel]:
            tile = QFrame()
            tile.setObjectName("metricTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(9, 6, 9, 7)
            tile_layout.setSpacing(1)
            caption_label = QLabel(caption_text)
            caption_label.setObjectName("metricCaption")
            value_label = QLabel(initial_value)
            value_label.setObjectName("metricValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            tile_layout.addWidget(caption_label)
            tile_layout.addWidget(value_label)
            metrics_layout.addWidget(tile, 1)
            return tile, value_label

        self.fuel_metric_frame, self.fuel_metric_value = metric_tile("FUEL", "--.-- L")
        self.last_metric_frame, self.last_metric_value = metric_tile("LAST LAP", "--.-- L")
        self.plan_metric_frame, self.plan_metric_value = metric_tile("PLAN", "-- L")
        self.estimate_metric_frame, self.estimate_metric_value = metric_tile(
            "ESTIMATE", "-- L"
        )
        self.pace_metric_frame, self.pace_metric_value = metric_tile("PACE", "--:--.---")
        self.comparison_metric_frame, self.comparison_metric_value = metric_tile(
            "VS PLAN", "-- L"
        )
        self.comparison_metric_value.setObjectName("comparisonValue")
        comparison_help = (
            "Difference between the estimated laps and the plan. Purple = above; green = on plan; red = below."
        )
        self.comparison_metric_frame.setToolTip(comparison_help)
        self.comparison_metric_value.setToolTip(comparison_help)
        shell_layout.addWidget(self.metrics_card)

        self.strategy_card = QFrame()
        self.strategy_card.setObjectName("strategyCard")
        strategy_layout = QVBoxLayout(self.strategy_card)
        strategy_layout.setContentsMargins(12, 8, 12, 9)
        strategy_layout.setSpacing(2)
        strategy_caption = QLabel("RACE STRATEGY")
        strategy_caption.setObjectName("eyebrow")
        self.strategy_label = QLabel("Waiting for data to work out the strategy.")
        self.strategy_label.setObjectName("strategyText")
        self.strategy_label.setWordWrap(True)
        strategy_layout.addWidget(strategy_caption)
        strategy_layout.addWidget(self.strategy_label)
        shell_layout.addWidget(self.strategy_card)

        self.controls_card = QFrame()
        self.controls_card.setObjectName("controlsCard")
        controls = QHBoxLayout(self.controls_card)
        controls.setContentsMargins(11, 7, 11, 7)
        controls.setSpacing(8)
        target_caption = QLabel("TARGET")
        target_caption.setObjectName("controlCaption")
        self.target_entry = QLineEdit(f"{self._initial_target_l:.2f}")
        self.target_entry.setObjectName("numberEntry")
        self.target_entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.target_entry.setFixedWidth(66)
        self.target_unit_label = QLabel("L/lap")
        self.target_unit_label.setObjectName("unitLabel")
        self.target_lock_checkbox = QCheckBox("LOCK")
        self.target_lock_checkbox.setObjectName("compactCheck")
        buffer_caption = QLabel("RESERVE")
        buffer_caption.setObjectName("controlCaption")
        self.buffer_entry = QLineEdit(f"{self._initial_buffer_l:.2f}")
        self.buffer_entry.setObjectName("numberEntry")
        self.buffer_entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.buffer_entry.setFixedWidth(62)
        self.buffer_unit_label = QLabel("L")
        self.buffer_unit_label.setObjectName("unitLabel")
        self.reset_button = self._small_button("RESET FUEL")
        controls.addWidget(target_caption)
        controls.addWidget(self.target_entry)
        controls.addWidget(self.target_unit_label)
        controls.addWidget(self.target_lock_checkbox)
        controls.addSpacing(5)
        controls.addWidget(buffer_caption)
        controls.addWidget(self.buffer_entry)
        controls.addWidget(self.buffer_unit_label)
        controls.addStretch(1)
        controls.addWidget(self.reset_button)
        shell_layout.addWidget(self.controls_card)

        self.customization_card = QFrame()
        self.customization_card.setObjectName("customizationCard")
        customization_layout = QVBoxLayout(self.customization_card)
        customization_layout.setContentsMargins(13, 10, 13, 11)
        customization_layout.setSpacing(8)

        customization_header = QHBoxLayout()
        customization_caption = QLabel("OVERLAY CUSTOMIZATION")
        customization_caption.setObjectName("eyebrow")
        self.reset_visual_button = self._small_button("RESTORE THE LOOK")
        customization_header.addWidget(customization_caption)
        customization_header.addStretch(1)
        customization_header.addWidget(self.reset_visual_button)
        customization_layout.addLayout(customization_header)

        team_car_row = QHBoxLayout()
        team_car_row.setSpacing(8)
        team_car_caption = QLabel("TEAM CAR IN SPECTATOR")
        team_car_caption.setObjectName("settingLabel")
        self.team_car_combo = QComboBox()
        self.team_car_combo.setObjectName("teamCarCombo")
        self.team_car_combo.setMinimumContentsLength(22)
        self.team_car_combo.addItem("AUTOMATIC", "")
        self.team_car_combo.setToolTip(
            "Pick the car by hand if the SDK does not identify your team car on its own."
        )
        team_car_row.addWidget(team_car_caption)
        team_car_row.addWidget(self.team_car_combo, 1)
        customization_layout.addLayout(team_car_row)

        options_grid = QGridLayout()
        options_grid.setHorizontalSpacing(15)
        options_grid.setVerticalSpacing(5)
        self.show_fuel_checkbox = self._option_checkbox("Stint summary")
        self.show_metrics_checkbox = self._option_checkbox("Data panel")
        self.show_strategy_checkbox = self._option_checkbox("Race strategy")
        self.show_controls_checkbox = self._option_checkbox("Target and reserve")
        self.show_rivals_checkbox = self._option_checkbox("Rivals")
        self.show_ahead_checkbox = self._option_checkbox("Cars ahead")
        self.show_behind_checkbox = self._option_checkbox("Cars behind")
        self.show_prediction_checkbox = self._option_checkbox("Driver forecast")
        self.show_team_checkbox = self._option_checkbox("Team trend")
        self.show_history_checkbox = self._option_checkbox("Earlier history")
        self.show_fuel_metric_checkbox = self._option_checkbox("Fuel")
        self.show_last_metric_checkbox = self._option_checkbox("Last lap")
        self.show_plan_checkbox = self._option_checkbox("Plan and estimate")
        self.show_pace_checkbox = self._option_checkbox("Pace")
        self.openxr_checkbox = self._option_checkbox("Simplified OpenXR")
        self.openxr_rivals_checkbox = self._option_checkbox("1 rival per side on OpenXR")
        option_widgets = (
            self.show_fuel_checkbox,
            self.show_metrics_checkbox,
            self.show_strategy_checkbox,
            self.show_controls_checkbox,
            self.show_rivals_checkbox,
            self.show_ahead_checkbox,
            self.show_behind_checkbox,
            self.show_prediction_checkbox,
            self.show_team_checkbox,
            self.show_history_checkbox,
            self.show_fuel_metric_checkbox,
            self.show_last_metric_checkbox,
            self.show_plan_checkbox,
            self.show_pace_checkbox,
            self.openxr_checkbox,
            self.openxr_rivals_checkbox,
        )
        for index, checkbox in enumerate(option_widgets):
            options_grid.addWidget(checkbox, index // 3, index % 3)
        customization_layout.addLayout(options_grid)

        openxr_setup = QHBoxLayout()
        openxr_setup.setSpacing(8)
        self.openxr_help_label = QLabel(
            f'OPENXR: add {OPENXR_URL} como Web Dashboard no OpenKneeboard.'
        )
        self.openxr_help_label.setObjectName("openXRHelp")
        self.openxr_help_label.setWordWrap(True)
        self.copy_openxr_url_button = self._small_button("COPY URL")
        openxr_setup.addWidget(self.openxr_help_label, 1)
        openxr_setup.addWidget(self.copy_openxr_url_button)
        customization_layout.addLayout(openxr_setup)

        visual_grid = QGridLayout()
        visual_grid.setHorizontalSpacing(9)
        visual_grid.setVerticalSpacing(6)
        self.cars_ahead_spin = QSpinBox()
        self.cars_ahead_spin.setObjectName("settingSpin")
        self.cars_ahead_spin.setRange(0, 5)
        self.cars_behind_spin = QSpinBox()
        self.cars_behind_spin.setObjectName("settingSpin")
        self.cars_behind_spin.setRange(0, 5)
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setObjectName("settingSlider")
        self.font_slider.setRange(80, 180)
        self.font_slider.setSingleStep(5)
        self.font_slider.setPageStep(10)
        self.font_value_label = QLabel("125%")
        self.font_value_label.setObjectName("settingValue")
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setObjectName("settingSlider")
        self.width_slider.setRange(480, 1100)
        self.width_slider.setSingleStep(20)
        self.width_value_label = QLabel("720 px")
        self.width_value_label.setObjectName("settingValue")
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setObjectName("settingSlider")
        self.opacity_slider.setRange(65, 100)
        self.opacity_slider.setSingleStep(5)
        self.opacity_value_label = QLabel("100%")
        self.opacity_value_label.setObjectName("settingValue")
        self.high_contrast_checkbox = self._option_checkbox("Alto contraste")

        def setting_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setObjectName("settingLabel")
            return label

        visual_grid.addWidget(setting_label("Ahead"), 0, 0)
        visual_grid.addWidget(self.cars_ahead_spin, 0, 1)
        visual_grid.addWidget(setting_label("Behind"), 0, 2)
        visual_grid.addWidget(self.cars_behind_spin, 0, 3)
        visual_grid.addWidget(self.high_contrast_checkbox, 0, 4, 1, 2)
        visual_grid.addWidget(setting_label("Font size"), 1, 0)
        visual_grid.addWidget(self.font_slider, 1, 1, 1, 4)
        visual_grid.addWidget(self.font_value_label, 1, 5)
        visual_grid.addWidget(setting_label("Width"), 2, 0)
        visual_grid.addWidget(self.width_slider, 2, 1, 1, 4)
        visual_grid.addWidget(self.width_value_label, 2, 5)
        visual_grid.addWidget(setting_label("Opacity"), 3, 0)
        visual_grid.addWidget(self.opacity_slider, 3, 1, 1, 4)
        visual_grid.addWidget(self.opacity_value_label, 3, 5)
        customization_layout.addLayout(visual_grid)
        shell_layout.addWidget(self.customization_card)

        self.advanced_card = QFrame()
        self.advanced_card.setObjectName("advancedCard")
        advanced_layout = QVBoxLayout(self.advanced_card)
        advanced_layout.setContentsMargins(12, 8, 12, 9)
        advanced_layout.setSpacing(5)
        advanced_caption = QLabel("SCENARIOS AND MARGINS")
        advanced_caption.setObjectName("eyebrow")
        self.advanced_info_label = QLabel("Waiting for valid laps...")
        self.advanced_info_label.setObjectName("advancedText")
        self.advanced_info_label.setWordWrap(True)
        advanced_buttons = QHBoxLayout()
        advanced_buttons.setSpacing(8)
        self.plus_one_button = self._small_button("TARGET FOR +1 LAP")
        self.minus_one_button = self._small_button("TARGET FOR -1 LAP")
        advanced_buttons.addWidget(self.plus_one_button)
        advanced_buttons.addWidget(self.minus_one_button)
        advanced_buttons.addStretch(1)
        advanced_layout.addWidget(advanced_caption)
        advanced_layout.addWidget(self.advanced_info_label)
        advanced_layout.addLayout(advanced_buttons)
        shell_layout.addWidget(self.advanced_card)

        self.rivals_frame = QFrame()
        self.rivals_frame.setObjectName("rivalsFrame")
        rivals_layout = QVBoxLayout(self.rivals_frame)
        rivals_layout.setContentsMargins(0, 0, 0, 0)
        rivals_layout.setSpacing(5)
        rivals_header = QHBoxLayout()
        self.rivals_legend = QLabel(
            "DRIVER = individual forecast  ·  TEAM = car trend  ·  PREV. = who drove it"
        )
        self.rivals_legend.setObjectName("legend")
        self.same_class_checkbox = QCheckBox("SAME CLASS ONLY")
        self.same_class_checkbox.setObjectName("compactCheck")
        rivals_header.addWidget(self.rivals_legend)
        rivals_header.addStretch(1)
        rivals_header.addWidget(self.same_class_checkbox)
        rivals_layout.addLayout(rivals_header)
        self.ahead_section = RelativeSection("AHEAD", self.config.cars_ahead)
        self.behind_section = RelativeSection("BEHIND", self.config.cars_behind)
        rivals_layout.addWidget(self.ahead_section)
        rivals_layout.addWidget(self.behind_section)
        shell_layout.addWidget(self.rivals_frame)

        self.pit_overlay = QFrame(self)
        self.pit_overlay.setObjectName("pitOverlay")
        pit_layout = QVBoxLayout(self.pit_overlay)
        pit_layout.setContentsMargins(30, 30, 30, 30)
        pit_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pit_caption = QLabel("STINT FINISHED")
        pit_caption.setObjectName("pitCaption")
        pit_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pit_average_label = QLabel("--.-- L/lap")
        self.pit_average_label.setObjectName("pitAverage")
        self.pit_average_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pit_subtitle = QLabel("average use over the stint")
        pit_subtitle.setObjectName("pitSubtitle")
        pit_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pit_layout.addWidget(pit_caption)
        pit_layout.addWidget(self.pit_average_label)
        pit_layout.addWidget(pit_subtitle)
        self.pit_overlay.hide()

        self.setStyleSheet(STYLE_SHEET)

        self.details_button.toggled.connect(self.set_details_visible)
        self.data_button.toggled.connect(self.set_metrics_visible)
        self.rivals_button.toggled.connect(self.set_rivals_visible)
        self.customize_button.toggled.connect(self.set_customization_visible)
        self.openxr_button.toggled.connect(self.set_openxr_enabled)
        self.close_button.clicked.connect(self._request_quit)
        self.target_lock_checkbox.toggled.connect(self._toggle_target_lock)
        self.reset_button.clicked.connect(self._manual_reset)
        self.plus_one_button.clicked.connect(lambda: self._apply_advanced_target("plus"))
        self.minus_one_button.clicked.connect(lambda: self._apply_advanced_target("minus"))
        self.same_class_checkbox.toggled.connect(self._set_same_class_only)
        self.edit_lock_button.toggled.connect(self.set_position_locked)
        self.target_entry.editingFinished.connect(self._persist_inputs)
        self.buffer_entry.editingFinished.connect(self._persist_inputs)
        self.reset_visual_button.clicked.connect(self._reset_visual_customization)
        self.copy_openxr_url_button.clicked.connect(self._copy_openxr_url)
        self.team_car_combo.currentIndexChanged.connect(
            self._team_car_selection_changed
        )
        for checkbox in option_widgets:
            if checkbox is self.openxr_checkbox:
                checkbox.toggled.connect(self.set_openxr_enabled)
            else:
                checkbox.toggled.connect(self._apply_customization)
        self.high_contrast_checkbox.toggled.connect(self._apply_customization)
        self.cars_ahead_spin.valueChanged.connect(self._apply_customization)
        self.cars_behind_spin.valueChanged.connect(self._apply_customization)
        self.font_slider.valueChanged.connect(self._apply_customization)
        self.width_slider.valueChanged.connect(self._apply_customization)
        self.opacity_slider.valueChanged.connect(self._apply_customization)

    @staticmethod
    def _small_button(text: str, checkable: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("smallButton")
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    @staticmethod
    def _option_checkbox(text: str) -> QCheckBox:
        checkbox = QCheckBox(text)
        checkbox.setObjectName("optionCheck")
        checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        return checkbox

    def _build_unlock_window(self) -> None:
        self.unlock_window = QWidget()
        self.unlock_window.setObjectName("unlockWindow")
        self.unlock_window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.unlock_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QVBoxLayout(self.unlock_window)
        layout.setContentsMargins(0, 0, 0, 0)
        self.unlock_clicks_button = QPushButton("UNLOCK CLICKS")
        self.unlock_clicks_button.setObjectName("unlockButton")
        self.unlock_clicks_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlock_clicks_button.clicked.connect(lambda: self.set_click_through(False))
        layout.addWidget(self.unlock_clicks_button)
        self.unlock_window.setStyleSheet(UNLOCK_STYLE_SHEET)
        self.unlock_window.adjustSize()
        self.unlock_window.hide()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self._make_icon(), self)
        self.tray.setToolTip("Nishizumi FuelMonitor")
        menu = QMenu()

        self.click_through_action = QAction("Ignore clicks", self)
        self.click_through_action.setCheckable(True)
        self.click_through_action.toggled.connect(self.set_click_through)
        self.position_lock_action = QAction("Lock position", self)
        self.position_lock_action.setCheckable(True)
        self.position_lock_action.toggled.connect(self.set_position_locked)
        self.details_action = QAction("Show details", self)
        self.details_action.setCheckable(True)
        self.details_action.toggled.connect(self.set_details_visible)
        self.metrics_action = QAction("Show the data panel", self)
        self.metrics_action.setCheckable(True)
        self.metrics_action.toggled.connect(self.set_metrics_visible)
        self.rivals_action = QAction("Show rivals", self)
        self.rivals_action.setCheckable(True)
        self.rivals_action.toggled.connect(self.set_rivals_visible)
        self.customize_action = QAction("Show the customization", self)
        self.customize_action.setCheckable(True)
        self.customize_action.toggled.connect(self.set_customization_visible)
        self.openxr_action = QAction("Turn on the OpenXR dashboard", self)
        self.openxr_action.setCheckable(True)
        self.openxr_action.toggled.connect(self.set_openxr_enabled)
        self.same_class_action = QAction("Same class only", self)
        self.same_class_action.setCheckable(True)
        self.same_class_action.toggled.connect(self._set_same_class_only)
        show_action = QAction("Show / hide", self)
        show_action.triggered.connect(self.toggle_visible)
        center_action = QAction("Centralizar overlay", self)
        center_action.triggered.connect(self.center_on_screen)
        reset_action = QAction("Reset the fuel monitor", self)
        reset_action.triggered.connect(self._manual_reset)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._request_quit)

        menu.addAction(self.click_through_action)
        menu.addAction(self.position_lock_action)
        menu.addAction(self.details_action)
        menu.addAction(self.metrics_action)
        menu.addAction(self.rivals_action)
        menu.addAction(self.customize_action)
        menu.addAction(self.openxr_action)
        menu.addAction(self.same_class_action)
        menu.addSeparator()
        menu.addAction(show_action)
        menu.addAction(center_action)
        menu.addAction(reset_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _poll(self) -> None:
        snapshot = self.source.read()
        self._refresh_team_car_selector(snapshot)
        if snapshot.display_units in (0, 1):
            self._set_display_units(int(snapshot.display_units))

        if self._was_connected and not snapshot.connected:
            self.fuel_engine.reset()
            self.endurance_tracker.reset()
            self._fuel_context = None
        self._was_connected = snapshot.connected

        endurance = self.endurance_tracker.update(snapshot)
        if snapshot.connected:
            context = (
                snapshot.session_id,
                snapshot.player_car_idx,
                snapshot.telemetry_source,
            )
            if context != self._fuel_context:
                self.fuel_engine.reset()
                self._last_fuel_view = FuelView()
                self._fuel_context = context
            target = self._current_target_liters()
            buffer = self._current_buffer_liters()
            if (
                snapshot.telemetry_source != TelemetrySource.LOCAL
                and snapshot.player_fuel_level is None
            ):
                previous = self._last_fuel_view
                fuel_view = replace(
                    previous,
                    connected=True,
                    telemetry_ready=False,
                    status="Remote fuel unavailable or late",
                    fuel_level=None,
                    remaining_laps=None,
                    remaining_seconds=None,
                    lap_time_estimate=(
                        snapshot.player_last_lap_time
                        or previous.lap_time_estimate
                    ),
                    can_finish=False,
                    on_pit_road=snapshot.on_pit_road,
                    planned_laps=None,
                    estimated_laps=None,
                    pit_summary_active=False,
                )
            else:
                fuel_view = self.fuel_engine.update(snapshot, target, buffer)
            fuel_view = self._apply_remote_stint_fallback(
                fuel_view,
                endurance,
                snapshot,
            )
        else:
            status = self.source.last_error or "Waiting for a connection to iRacing"
            fuel_view = self.fuel_engine.disconnected_view(status)
        self._last_fuel_view = fuel_view
        self._update_model(fuel_view, endurance, snapshot)

    def _refresh_team_car_selector(self, snapshot: TelemetrySnapshot) -> None:
        if not hasattr(self, "team_car_combo"):
            return

        choices = tuple(
            sorted(
                (
                    (meta.car_number.strip(), meta.team_name.strip(), meta.car_idx)
                    for meta in snapshot.drivers.values()
                    if meta.car_number.strip()
                ),
                key=lambda item: (
                    0 if item[0].isdigit() else 1,
                    int(item[0]) if item[0].isdigit() else item[0].casefold(),
                    item[2],
                ),
            )
        )
        manual = self.source.manual_team_car_number.strip()
        signature = (snapshot.connected, choices, manual)
        if signature == self._team_car_choices_signature:
            return
        self._team_car_choices_signature = signature

        combo = self.team_car_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("AUTOMATIC", "")
        available_numbers: set[str] = set()
        for number, team_name, _car_idx in choices:
            if number in available_numbers:
                continue
            available_numbers.add(number)
            label = f'#{number} · {team_name or 'TEAM WITH NO NAME'}'
            combo.addItem(label, number)
        if manual and manual not in available_numbers:
            state = "NOT FOUND" if snapshot.connected else "WAITING FOR THE SESSION"
            combo.addItem(f"#{manual} · {state}", manual)
        selected = combo.findData(manual)
        combo.setCurrentIndex(max(0, selected))
        combo.blockSignals(False)

    def _team_car_selection_changed(self, index: int) -> None:
        number = str(self.team_car_combo.itemData(index) or "").strip()
        if number == self.source.manual_team_car_number:
            return
        self.source.manual_team_car_number = number
        self.settings.setValue("team_car_number", number)
        self.fuel_engine.reset()
        self._last_fuel_view = FuelView()
        self._fuel_context = None
        self._team_car_choices_signature = None

    @staticmethod
    def _apply_remote_stint_fallback(
        fuel: FuelView,
        endurance: EnduranceViewModel,
        snapshot: TelemetrySnapshot,
    ) -> FuelView:
        if snapshot.telemetry_source == TelemetrySource.LOCAL:
            return fuel
        track_ready = (
            snapshot.player_lap >= 0
            and 0.0 <= snapshot.player_lap_dist_pct <= 1.0
            and (snapshot.is_on_track or snapshot.on_pit_road)
        )
        if not track_ready or fuel.remaining_laps is not None:
            return fuel

        remaining = endurance.player_predicted_remaining_laps
        lap_time = fuel.lap_time_estimate or snapshot.player_last_lap_time
        remaining_seconds = (
            remaining * lap_time
            if remaining is not None and lap_time is not None and lap_time > 0
            else None
        )
        source_name = "team"
        if remaining is None:
            strategy_text = (
                f'Following the car of {source_name}; collecting history to estimate the end of the stint.'
            )
        else:
            basis = endurance.player_prediction_basis.lower() or "history"
            strategy_text = (
                f'Stint estimated from the history of {basis}; remote fuel telemetry can arrive late.'
            )
        return replace(
            fuel,
            connected=True,
            telemetry_ready=True,
            status=f'Following {source_name}',
            remaining_laps=remaining,
            remaining_seconds=remaining_seconds,
            lap_time_estimate=lap_time,
            on_pit_road=snapshot.on_pit_road,
            estimated_laps=(
                math.floor(endurance.player_predicted_total_laps)
                if endurance.player_predicted_total_laps is not None
                else fuel.estimated_laps
            ),
            observed_stint_laps=endurance.player_current_stint_laps,
            observed_stint_known=endurance.player_current_stint_known,
            strategy_text=strategy_text,
            strategy_state="neutral",
        )

    def _update_model(
        self,
        fuel: FuelView,
        endurance: EnduranceViewModel,
        snapshot: TelemetrySnapshot,
    ) -> None:
        self.status_dot.setProperty("connected", "true" if fuel.connected else "false")
        self.status_dot.setToolTip(
            "iRacing connected" if fuel.connected else "Waiting for iRacing"
        )
        repolish(self.status_dot)
        source_name = {
            TelemetrySource.TEAM: "TEAM",
        }.get(snapshot.telemetry_source, "")
        remote_history = bool(
            source_name
            and snapshot.player_car_idx >= 0
            and snapshot.player_fuel_level is None
        )
        manual_number = self.source.manual_team_car_number.strip()
        self.source_badge.setVisible(bool(source_name))
        if source_name:
            self.source_badge.setText(
                f"{source_name} · HIST."
                if remote_history
                else f"{source_name} · MANUAL"
                if manual_number
                else source_name
            )
            if snapshot.player_car_idx < 0:
                self.source_badge.setToolTip(
                    f'Car #{manual_number} was not found in the session.'
                    if manual_number
                    else "Waiting for the SDK to identify the car tied to your team."
                )
            else:
                driver = endurance.tracked_driver_name or "driver on track"
                selection = (
                    f'Manual pick #{manual_number}. ' if manual_number else ""
                )
                self.source_badge.setToolTip(
                    selection
                    + f'Following {driver}. '
                    + (
                        "Time worked out from history; the last valid usage stays on screen when there is one."
                        if remote_history
                        else "Remote fuel comes from the SDK; it can arrive late."
                    )
                )

        if not fuel.connected:
            self.remaining_time_label.setText("--:--:--")
            self.remaining_laps_label.setText("open an iRacing session")
            self._set_badge("NO CONNECTION", "neutral")
        elif not fuel.telemetry_ready:
            remote = snapshot.telemetry_source != TelemetrySource.LOCAL
            self.remaining_time_label.setText("TEAM" if remote else "NO DATA")
            self.remaining_laps_label.setText(
                (
                    f'car #{manual_number} not found · change it under OPTIONS'
                    if manual_number and snapshot.player_car_idx < 0
                    else "waiting for the team car · pick it under OPTIONS"
                )
                if remote
                else "waiting for the car to get on track"
            )
            self._set_badge("WAITING", "neutral")
        elif fuel.on_pit_road:
            self.remaining_time_label.setText("NO PIT")
            self.remaining_laps_label.setText("waiting for the pit exit")
            self._set_badge("PIT", "pit")
        elif fuel.remaining_laps is None:
            remote = snapshot.telemetry_source != TelemetrySource.LOCAL
            self.remaining_time_label.setText("COLLECTING" if remote else "CALIBRATING")
            self.remaining_laps_label.setText(
                "not enough driver/team history yet"
                if remote
                else "complete valid laps to estimate"
            )
            self._set_badge("COLLECTING", "neutral")
        else:
            self.remaining_time_label.setText(format_duration(fuel.remaining_seconds))
            current_laps = (
                endurance.player_current_stint_laps
                if endurance.player_current_stint_laps or endurance.player_current_stint_known
                else fuel.observed_stint_laps
            )
            current_known = (
                endurance.player_current_stint_known or fuel.observed_stint_known
            )
            prefix = "" if current_known else "≥"
            total = current_laps + fuel.remaining_laps
            self.remaining_laps_label.setText(
                f'{fuel.remaining_laps:.1f} laps left · full stint ~{prefix}{total:.1f}v'
            )
            self._set_badge(
                "TO THE END" if fuel.can_finish else "TO THE PIT",
                "finish" if fuel.can_finish else "pit",
            )

        if fuel.average_per_lap is None:
            self.average_label.setText(f"--.-- {self._unit_label}/v")
            self.average_label.setProperty("state", "neutral")
            self.delta_label.setText("(--)")
            self.delta_label.setProperty("state", "neutral")
        else:
            average = self._from_liters(fuel.average_per_lap)
            state = "good" if fuel.within_target else "danger"
            if self._current_target_liters() is None:
                state = "neutral"
            self.average_label.setText(f"{average:.2f} {self._unit_label}/v")
            self.average_label.setProperty("state", state)
            if fuel.delta_to_target is None:
                self.delta_label.setText("(--)")
                self.delta_label.setProperty("state", "neutral")
            else:
                delta = self._from_liters(fuel.delta_to_target)
                self.delta_label.setText(f"({delta:+.2f})")
                self.delta_label.setProperty("state", state)
        repolish(self.average_label)
        repolish(self.delta_label)

        fuel_text = (
            f"{self._from_liters(fuel.fuel_level):.2f} {self._unit_label}"
            if fuel.fuel_level is not None
            else "--"
        )
        last_text = (
            f"{self._from_liters(fuel.last_lap_used):.2f} {self._unit_label}"
            if fuel.last_lap_used is not None
            else "--"
        )
        self.fuel_metric_value.setText(fuel_text)
        self.last_metric_value.setText(last_text)
        self.plan_metric_value.setText(
            f"{fuel.planned_laps} V" if fuel.planned_laps is not None else "-- L"
        )
        self.estimate_metric_value.setText(
            f"{fuel.estimated_laps} V"
            if fuel.estimated_laps is not None
            else "-- L"
        )
        self.pace_metric_value.setText(format_lap_time(fuel.lap_time_estimate))
        self._set_plan_comparison(fuel.planned_laps, fuel.estimated_laps)

        player_laps = (
            endurance.player_current_stint_laps
            if endurance.player_current_stint_laps or endurance.player_current_stint_known
            else fuel.observed_stint_laps
        )
        player_known = endurance.player_current_stint_known or fuel.observed_stint_known
        player_prefix = "" if player_known else "≥"
        if source_name and endurance.tracked_driver_name:
            self.current_stint_label.setText(
                f"{endurance.tracked_driver_name.upper()}  ·  "
                f"STINT {player_prefix}{player_laps} V"
            )
        else:
            self.current_stint_label.setText(
                f'CURRENT STINT  {player_prefix}{player_laps} V'
            )

        self.strategy_label.setText(self._convert_strategy_units(fuel.strategy_text))
        self.strategy_label.setProperty("state", fuel.strategy_state)
        repolish(self.strategy_label)

        if fuel.strategy_details:
            lines = [self._convert_strategy_units(line) for line in fuel.strategy_details]
            if self._current_buffer_liters() > 0:
                lines.append(
                    f'Reserve at the finish: {self._from_liters(self._current_buffer_liters()):.2f} {self._unit_label}'
                )
            self.advanced_info_label.setText("\n".join(lines))
        else:
            self.advanced_info_label.setText("Waiting for valid laps...")
        self.plus_one_button.setEnabled(fuel.plus_one_target is not None)
        self.minus_one_button.setEnabled(fuel.minus_one_target is not None)

        self.ahead_section.set_rows(endurance.cars_ahead)
        self.behind_section.set_rows(endurance.cars_behind)
        self._update_openxr_dashboard(fuel, endurance, snapshot)

        if fuel.pit_summary_active:
            if fuel.pit_summary_average is None:
                summary = f"--.-- {self._unit_label}/v"
            else:
                summary = (
                    f"{self._from_liters(fuel.pit_summary_average):.2f} "
                    f"{self._unit_label}/v"
                )
            self.pit_average_label.setText(summary)
            self.pit_overlay.show()
            self.pit_overlay.raise_()
        else:
            self.pit_overlay.hide()

        layout_signature = (
            min(len(endurance.cars_ahead), self.config.cars_ahead),
            min(len(endurance.cars_behind), self.config.cars_behind),
            self.strategy_label.text(),
            self.advanced_info_label.text() if self.details_button.isChecked() else "",
            self.show_fuel_checkbox.isChecked(),
            self.show_metrics_checkbox.isChecked(),
            self.show_fuel_metric_checkbox.isChecked(),
            self.show_last_metric_checkbox.isChecked(),
            self.show_plan_checkbox.isChecked(),
            self.show_pace_checkbox.isChecked(),
            self.show_strategy_checkbox.isChecked(),
            self.show_controls_checkbox.isChecked(),
            self.show_rivals_checkbox.isChecked(),
            self.show_ahead_checkbox.isChecked(),
            self.show_behind_checkbox.isChecked(),
            self.show_prediction_checkbox.isChecked(),
            self.show_team_checkbox.isChecked(),
            self.show_history_checkbox.isChecked(),
            self.config.overlay_width,
            self._ui_scale,
        )
        if layout_signature != self._last_layout_signature:
            self._last_layout_signature = layout_signature
            self._resize_to_content()

    def _set_badge(self, text: str, state: str) -> None:
        self.fuel_badge.setText(text)
        self.fuel_badge.setProperty("state", state)
        repolish(self.fuel_badge)

    def _set_plan_comparison(
        self, planned_laps: int | None, estimated_laps: int | None
    ) -> None:
        state, text, help_text = plan_comparison(planned_laps, estimated_laps)
        self.comparison_metric_value.setText(text)
        self.comparison_metric_frame.setToolTip(help_text)
        self.comparison_metric_value.setToolTip(help_text)
        for widget in (
            self.plan_metric_frame,
            self.estimate_metric_frame,
            self.comparison_metric_frame,
            self.plan_metric_value,
            self.estimate_metric_value,
            self.comparison_metric_value,
        ):
            widget.setProperty("comparison", state)
            repolish(widget)

    def _update_openxr_dashboard(
        self,
        fuel: FuelView,
        endurance: EnduranceViewModel,
        snapshot: TelemetrySnapshot,
    ) -> None:
        if not self.openxr_server.running:
            return

        state = self.openxr_server.empty_state()
        state["connected"] = fuel.connected

        player_laps = (
            endurance.player_current_stint_laps
            if endurance.player_current_stint_laps
            or endurance.player_current_stint_known
            else fuel.observed_stint_laps
        )
        player_known = endurance.player_current_stint_known or fuel.observed_stint_known
        player_prefix = "" if player_known else "≥"
        current_text = f'CURRENT {player_prefix}{player_laps} V'
        if (
            snapshot.telemetry_source != TelemetrySource.LOCAL
            and endurance.tracked_driver_name
        ):
            current_text = (
                f"{endurance.tracked_driver_name.upper()} · {current_text}"
            )
        state["current"] = current_text

        if not fuel.connected:
            state["laps"] = "WAITING FOR IRACING"
        elif not fuel.telemetry_ready:
            remote = snapshot.telemetry_source != TelemetrySource.LOCAL
            state["time"] = "TEAM" if remote else "NO DATA"
            state["laps"] = (
                (
                    f'CAR #{self.source.manual_team_car_number} NOT FOUND'
                    if self.source.manual_team_car_number
                    and snapshot.player_car_idx < 0
                    else "WAITING FOR THE TEAM CAR"
                )
                if remote
                else "GET ON TRACK"
            )
        elif fuel.on_pit_road:
            state["time"] = "NO PIT"
            state["laps"] = "WAITING FOR THE PIT EXIT"
            state["destination"] = "PIT"
        elif fuel.remaining_laps is None:
            remote = snapshot.telemetry_source != TelemetrySource.LOCAL
            state["time"] = "COLLECTING" if remote else "CALIBRATING"
            state["laps"] = (
                "NOT ENOUGH HISTORY YET"
                if remote
                else "COMPLETE VALID LAPS"
            )
        else:
            state["time"] = format_duration(fuel.remaining_seconds)
            state["laps"] = f'{fuel.remaining_laps:.1f} LAPS LEFT'
            state["destination"] = "TO THE END" if fuel.can_finish else "TO THE PIT"

        source_label = {
            TelemetrySource.TEAM: "TEAM",
        }.get(snapshot.telemetry_source)
        if source_label:
            if (
                snapshot.player_car_idx >= 0
                and snapshot.player_fuel_level is None
            ):
                source_label += " · HIST."
            state["destination"] = f"{source_label} · {state['destination']}"

        if fuel.fuel_level is not None:
            state["fuel"] = (
                f"{self._from_liters(fuel.fuel_level):.2f} {self._unit_label}"
            )
        if fuel.average_per_lap is not None:
            state["average"] = (
                f"{self._from_liters(fuel.average_per_lap):.2f} "
                f"{self._unit_label}/v"
            )
            if self._current_target_liters() is None:
                state["average_state"] = "neutral"
            else:
                state["average_state"] = "good" if fuel.within_target else "danger"
        if fuel.delta_to_target is not None:
            state["delta"] = f"{self._from_liters(fuel.delta_to_target):+.2f}"

        state["plan"] = (
            f"{fuel.planned_laps} V" if fuel.planned_laps is not None else "-- L"
        )
        state["estimate"] = (
            f"{fuel.estimated_laps} V" if fuel.estimated_laps is not None else "-- L"
        )
        comparison_state, comparison_text, _help = plan_comparison(
            fuel.planned_laps,
            fuel.estimated_laps,
        )
        state["comparison"] = comparison_text
        state["comparison_state"] = comparison_state

        show_rivals = (
            hasattr(self, "openxr_rivals_checkbox")
            and self.openxr_rivals_checkbox.isChecked()
        )
        if show_rivals:
            state["ahead"] = self._openxr_rival_text(
                endurance.cars_ahead[0] if endurance.cars_ahead else None,
                True,
            )
            state["behind"] = self._openxr_rival_text(
                endurance.cars_behind[0] if endurance.cars_behind else None,
                False,
            )
        self.openxr_server.update(state)

    @staticmethod
    def _openxr_rival_text(car: RelativeCarView | None, ahead: bool) -> str:
        if car is None:
            return ""
        arrow = "↑" if ahead else "↓"
        current_prefix = "" if car.current_stint_known else "≥"
        current = "PIT" if car.on_pit_road else f"{current_prefix}{car.current_stint_laps} V"
        last = car.team_last_stint
        if last is None:
            previous = "LAST --"
        else:
            previous_prefix = "≥" if not last.complete else ""
            previous = (
                f'LAST {last.driver_short_name} {previous_prefix}{last.laps} V'
            )
        return (
            f'{arrow}  #{car.car_number}  {car.driver_short_name}  ·  CURRENT {current}  ·  {previous}'
        )

    def set_openxr_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled:
            enabled = self.openxr_server.start()
        else:
            self.openxr_server.stop()
        self._openxr_enabled = enabled

        for control_name in ("openxr_checkbox", "openxr_button", "openxr_action"):
            control = getattr(self, control_name, None)
            if control is None:
                continue
            control.blockSignals(True)
            control.setChecked(enabled)
            control.blockSignals(False)

        if hasattr(self, "openxr_help_label"):
            if enabled:
                message = (
                    f'OPENXR ON: add {OPENXR_URL} como Web Dashboard no OpenKneeboard.'
                )
            elif self.openxr_server.last_error:
                message = self.openxr_server.last_error
            else:
                message = (
                    f'OPENXR: turn it on and add {OPENXR_URL} como Web Dashboard no OpenKneeboard.'
                )
            self.openxr_help_label.setText(message)
        self.settings.setValue("openxr_enabled", enabled)

    def _copy_openxr_url(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(OPENXR_URL)
        self.copy_openxr_url_button.setText("COPIED")
        QTimer.singleShot(
            1200,
            lambda: self.copy_openxr_url_button.setText("COPY URL"),
        )

    def _set_display_units(self, display_units: int) -> None:
        if display_units not in (0, 1) or display_units == self._display_units:
            return
        target_l = self._parse_entry_liters(self.target_entry, self._display_units)
        buffer_l = self._parse_entry_liters(self.buffer_entry, self._display_units)
        self._display_units = display_units
        self._unit_label = "gal" if display_units == 0 else "L"
        if target_l is not None:
            self.target_entry.setText(f"{self._from_liters(target_l):.2f}")
        if buffer_l is not None:
            self.buffer_entry.setText(f"{self._from_liters(buffer_l):.2f}")
        self.target_unit_label.setText(f"{self._unit_label}/v")
        self.buffer_unit_label.setText(self._unit_label)
        self._persist_inputs()

    def _from_liters(self, value: float) -> float:
        return value * LITER_TO_GALLON if self._display_units == 0 else value

    @staticmethod
    def _parse_number(text: str) -> float | None:
        try:
            return float(text.strip().replace(",", "."))
        except ValueError:
            return None

    def _parse_entry_liters(self, entry: QLineEdit, units: int | None = None) -> float | None:
        value = self._parse_number(entry.text())
        if value is None:
            return None
        effective_units = self._display_units if units is None else units
        return value / LITER_TO_GALLON if effective_units == 0 else value

    def _current_target_liters(self) -> float | None:
        if self.target_lock_checkbox.isChecked():
            return self._locked_target
        value = self._parse_entry_liters(self.target_entry)
        return value if value is not None and value > 0 else None

    def _current_buffer_liters(self) -> float:
        if self.target_lock_checkbox.isChecked() and self._locked_buffer is not None:
            return self._locked_buffer
        value = self._parse_entry_liters(self.buffer_entry)
        return max(0.0, value or 0.0)

    def _toggle_target_lock(self, checked: bool) -> None:
        if checked:
            target = self._parse_entry_liters(self.target_entry)
            if target is None or target <= 0:
                self.target_lock_checkbox.blockSignals(True)
                self.target_lock_checkbox.setChecked(False)
                self.target_lock_checkbox.blockSignals(False)
                return
            self._locked_target = target
            self._locked_buffer = self._current_buffer_liters()
            self.target_entry.setEnabled(False)
            self.buffer_entry.setEnabled(False)
        else:
            self._locked_target = None
            self._locked_buffer = None
            self.target_entry.setEnabled(True)
            self.buffer_entry.setEnabled(True)
        self._persist_inputs()

    def _apply_advanced_target(self, mode: str) -> None:
        value = (
            self._last_fuel_view.plus_one_target
            if mode == "plus"
            else self._last_fuel_view.minus_one_target
        )
        if value is None:
            return
        self.target_entry.setText(f"{self._from_liters(value):.2f}")
        if self.target_lock_checkbox.isChecked():
            self._locked_target = value
        self._persist_inputs()

    def _manual_reset(self) -> None:
        self.fuel_engine.reset()
        self.status_dot.setToolTip("Fuel monitor reset")

    def _persist_inputs(self) -> None:
        target = self._current_target_liters()
        buffer = self._current_buffer_liters()
        if target is not None:
            self.settings.setValue("target_liters", target)
        self.settings.setValue("buffer_liters", buffer)

    def _convert_strategy_units(self, text: str) -> str:
        if self._display_units != 0:
            return text

        def replace(match: re.Match[str]) -> str:
            liters = float(match.group(1))
            return f'{liters * LITER_TO_GALLON:.2f} gal/lap'

        return re.sub(r"(\d+(?:\.\d+)?) L/v", replace, text)

    def set_details_visible(self, visible: bool) -> None:
        visible = bool(visible)
        self.advanced_card.setVisible(visible)
        self.details_button.blockSignals(True)
        self.details_button.setChecked(visible)
        self.details_button.blockSignals(False)
        if hasattr(self, "details_action"):
            self.details_action.blockSignals(True)
            self.details_action.setChecked(visible)
            self.details_action.blockSignals(False)
        self.settings.setValue("show_details", visible)
        self._resize_to_content()

    def set_metrics_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if hasattr(self, "show_metrics_checkbox"):
            self.show_metrics_checkbox.blockSignals(True)
            self.show_metrics_checkbox.setChecked(visible)
            self.show_metrics_checkbox.blockSignals(False)
        self.metrics_card.setVisible(visible)
        self.data_button.blockSignals(True)
        self.data_button.setChecked(visible)
        self.data_button.blockSignals(False)
        if hasattr(self, "metrics_action"):
            self.metrics_action.blockSignals(True)
            self.metrics_action.setChecked(visible)
            self.metrics_action.blockSignals(False)
        self.settings.setValue("show_metrics_card", visible)
        if hasattr(self, "show_fuel_metric_checkbox"):
            self._apply_customization()
        else:
            self._resize_to_content()

    def set_rivals_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if hasattr(self, "show_rivals_checkbox"):
            self.show_rivals_checkbox.blockSignals(True)
            self.show_rivals_checkbox.setChecked(visible)
            self.show_rivals_checkbox.blockSignals(False)
        self.rivals_frame.setVisible(visible)
        self.rivals_button.blockSignals(True)
        self.rivals_button.setChecked(visible)
        self.rivals_button.blockSignals(False)
        if hasattr(self, "rivals_action"):
            self.rivals_action.blockSignals(True)
            self.rivals_action.setChecked(visible)
            self.rivals_action.blockSignals(False)
        self.settings.setValue("show_rivals", visible)
        if hasattr(self, "show_fuel_checkbox"):
            self._apply_customization()
        else:
            self._resize_to_content()

    def set_customization_visible(self, visible: bool) -> None:
        visible = bool(visible)
        self.customization_card.setVisible(visible)
        self.customize_button.blockSignals(True)
        self.customize_button.setChecked(visible)
        self.customize_button.blockSignals(False)
        if hasattr(self, "customize_action"):
            self.customize_action.blockSignals(True)
            self.customize_action.setChecked(visible)
            self.customize_action.blockSignals(False)
        self.settings.setValue("show_customization", visible)
        self._resize_to_content()

    def _load_customization_controls(self) -> None:
        boolean_controls = (
            (self.show_fuel_checkbox, "show_fuel_card", True),
            (self.show_metrics_checkbox, "show_metrics_card", True),
            (self.show_strategy_checkbox, "show_strategy_card", True),
            (self.show_controls_checkbox, "show_controls_card", True),
            (self.show_rivals_checkbox, "show_rivals", True),
            (self.show_ahead_checkbox, "show_ahead", True),
            (self.show_behind_checkbox, "show_behind", True),
            (self.show_prediction_checkbox, "show_driver_prediction", True),
            (self.show_team_checkbox, "show_team_trend", True),
            (self.show_history_checkbox, "show_stint_history", True),
            (self.show_fuel_metric_checkbox, "show_fuel_metric", True),
            (self.show_last_metric_checkbox, "show_last_metric", True),
            (self.show_plan_checkbox, "show_plan", True),
            (self.show_pace_checkbox, "show_pace", True),
            (self.openxr_checkbox, "openxr_enabled", False),
            (self.openxr_rivals_checkbox, "openxr_show_rivals", True),
            (self.high_contrast_checkbox, "high_contrast", True),
        )
        for checkbox, key, default in boolean_controls:
            checkbox.blockSignals(True)
            checkbox.setChecked(self._setting_bool(key, default))
            checkbox.blockSignals(False)

        for spin, value in (
            (self.cars_ahead_spin, self.config.cars_ahead),
            (self.cars_behind_spin, self.config.cars_behind),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

        self.font_slider.blockSignals(True)
        self.font_slider.setValue(int(round(self._ui_scale * 100)))
        self.font_slider.blockSignals(False)

        self.width_slider.blockSignals(True)
        self.width_slider.setValue(self.config.overlay_width)
        self.width_slider.blockSignals(False)
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(int(round(self._window_opacity * 100)))
        self.opacity_slider.blockSignals(False)
        self._apply_customization()

    def _apply_customization(self, *_args: Any) -> None:
        self.config.cars_ahead = int(self.cars_ahead_spin.value())
        self.config.cars_behind = int(self.cars_behind_spin.value())
        self.config.overlay_width = int(self.width_slider.value())
        self._ui_scale = max(0.80, min(1.80, self.font_slider.value() / 100.0))
        self._window_opacity = max(0.65, min(1.0, self.opacity_slider.value() / 100.0))
        self._high_contrast = self.high_contrast_checkbox.isChecked()

        show_rivals = self.show_rivals_checkbox.isChecked()
        show_metrics = self.show_metrics_checkbox.isChecked()
        self.fuel_card.setVisible(self.show_fuel_checkbox.isChecked())
        self.metrics_card.setVisible(show_metrics)
        self.strategy_card.setVisible(self.show_strategy_checkbox.isChecked())
        self.controls_card.setVisible(self.show_controls_checkbox.isChecked())
        self.rivals_frame.setVisible(show_rivals)
        self.ahead_section.setVisible(
            show_rivals
            and self.show_ahead_checkbox.isChecked()
            and self.config.cars_ahead > 0
        )
        self.behind_section.setVisible(
            show_rivals
            and self.show_behind_checkbox.isChecked()
            and self.config.cars_behind > 0
        )
        self.fuel_metric_frame.setVisible(
            show_metrics and self.show_fuel_metric_checkbox.isChecked()
        )
        self.last_metric_frame.setVisible(
            show_metrics and self.show_last_metric_checkbox.isChecked()
        )
        show_plan = show_metrics and self.show_plan_checkbox.isChecked()
        self.plan_metric_frame.setVisible(show_plan)
        self.estimate_metric_frame.setVisible(show_plan)
        self.comparison_metric_frame.setVisible(show_plan)
        self.pace_metric_frame.setVisible(
            show_metrics and self.show_pace_checkbox.isChecked()
        )

        self.ahead_section.set_limit(self.config.cars_ahead)
        self.behind_section.set_limit(self.config.cars_behind)
        display_options = (
            self.show_prediction_checkbox.isChecked(),
            self.show_team_checkbox.isChecked(),
            self.show_history_checkbox.isChecked(),
            self._ui_scale,
        )
        self.ahead_section.apply_display_options(*display_options)
        self.behind_section.apply_display_options(*display_options)

        self.rivals_button.blockSignals(True)
        self.rivals_button.setChecked(show_rivals)
        self.rivals_button.blockSignals(False)
        if hasattr(self, "rivals_action"):
            self.rivals_action.blockSignals(True)
            self.rivals_action.setChecked(show_rivals)
            self.rivals_action.blockSignals(False)
        self.data_button.blockSignals(True)
        self.data_button.setChecked(show_metrics)
        self.data_button.blockSignals(False)
        if hasattr(self, "metrics_action"):
            self.metrics_action.blockSignals(True)
            self.metrics_action.setChecked(show_metrics)
            self.metrics_action.blockSignals(False)

        self.width_value_label.setText(f"{self.config.overlay_width} px")
        self.font_value_label.setText(f"{int(round(self._ui_scale * 100))}%")
        self.opacity_value_label.setText(f"{int(round(self._window_opacity * 100))}%")
        self.setFixedWidth(self.config.overlay_width)
        self.setWindowOpacity(self._window_opacity)
        self._apply_visual_style()
        self.set_openxr_enabled(self.openxr_checkbox.isChecked())

        settings_values: tuple[tuple[str, Any], ...] = (
            ("cars_ahead", self.config.cars_ahead),
            ("cars_behind", self.config.cars_behind),
            ("overlay_width", self.config.overlay_width),
            ("ui_scale", self._ui_scale),
            ("window_opacity", self._window_opacity),
            ("high_contrast", self._high_contrast),
            ("show_fuel_card", self.show_fuel_checkbox.isChecked()),
            ("show_metrics_card", show_metrics),
            ("show_strategy_card", self.show_strategy_checkbox.isChecked()),
            ("show_controls_card", self.show_controls_checkbox.isChecked()),
            ("show_rivals", show_rivals),
            ("show_ahead", self.show_ahead_checkbox.isChecked()),
            ("show_behind", self.show_behind_checkbox.isChecked()),
            ("show_driver_prediction", self.show_prediction_checkbox.isChecked()),
            ("show_team_trend", self.show_team_checkbox.isChecked()),
            ("show_stint_history", self.show_history_checkbox.isChecked()),
            ("show_fuel_metric", self.show_fuel_metric_checkbox.isChecked()),
            ("show_last_metric", self.show_last_metric_checkbox.isChecked()),
            ("show_plan", self.show_plan_checkbox.isChecked()),
            ("show_pace", self.show_pace_checkbox.isChecked()),
            ("openxr_enabled", self._openxr_enabled),
            ("openxr_show_rivals", self.openxr_rivals_checkbox.isChecked()),
        )
        for key, value in settings_values:
            self.settings.setValue(key, value)
        self._last_layout_signature = None
        self._resize_to_content()
        self._sync_unlock_window()

    def _apply_visual_style(self) -> None:
        def scale_font(match: re.Match[str]) -> str:
            size = max(8, int(round(int(match.group(1)) * self._ui_scale)))
            return f"font-size: {size}px"

        style = re.sub(r"font-size:\s*(\d+)px", scale_font, STYLE_SHEET)
        if self._high_contrast:
            style += HIGH_CONTRAST_STYLE_SHEET
        self.setStyleSheet(style)

    def _reset_visual_customization(self) -> None:
        defaults = (
            self.show_fuel_checkbox,
            self.show_metrics_checkbox,
            self.show_strategy_checkbox,
            self.show_controls_checkbox,
            self.show_rivals_checkbox,
            self.show_ahead_checkbox,
            self.show_behind_checkbox,
            self.show_prediction_checkbox,
            self.show_team_checkbox,
            self.show_history_checkbox,
            self.show_fuel_metric_checkbox,
            self.show_last_metric_checkbox,
            self.show_plan_checkbox,
            self.show_pace_checkbox,
            self.openxr_rivals_checkbox,
            self.high_contrast_checkbox,
        )
        for checkbox in defaults:
            checkbox.blockSignals(True)
            checkbox.setChecked(True)
            checkbox.blockSignals(False)
        self.openxr_checkbox.blockSignals(True)
        self.openxr_checkbox.setChecked(False)
        self.openxr_checkbox.blockSignals(False)
        for spin in (self.cars_ahead_spin, self.cars_behind_spin):
            spin.blockSignals(True)
            spin.setValue(3)
            spin.blockSignals(False)
        self.font_slider.blockSignals(True)
        self.font_slider.setValue(125)
        self.font_slider.blockSignals(False)
        self.width_slider.blockSignals(True)
        self.width_slider.setValue(720)
        self.width_slider.blockSignals(False)
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(100)
        self.opacity_slider.blockSignals(False)
        self._apply_customization()

    def _set_same_class_only(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.config.same_class_only = enabled
        self.same_class_checkbox.blockSignals(True)
        self.same_class_checkbox.setChecked(enabled)
        self.same_class_checkbox.blockSignals(False)
        if hasattr(self, "same_class_action"):
            self.same_class_action.blockSignals(True)
            self.same_class_action.setChecked(enabled)
            self.same_class_action.blockSignals(False)
        self.settings.setValue("same_class_only", enabled)

    def set_position_locked(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._position_locked = enabled
        self.edit_lock_button.blockSignals(True)
        self.edit_lock_button.setChecked(enabled)
        self.edit_lock_button.setText("PINNED" if enabled else "MOVE")
        self.edit_lock_button.blockSignals(False)
        if hasattr(self, "position_lock_action"):
            self.position_lock_action.blockSignals(True)
            self.position_lock_action.setChecked(enabled)
            self.position_lock_action.blockSignals(False)
        self.settings.setValue("position_locked", enabled)

    def set_click_through(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._click_through = enabled
        if hasattr(self, "click_through_action"):
            self.click_through_action.blockSignals(True)
            self.click_through_action.setChecked(enabled)
            self.click_through_action.blockSignals(False)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        self.settings.setValue("click_through", enabled)
        self.show()
        if hasattr(self, "unlock_window"):
            if enabled:
                self._sync_unlock_window()
                self.unlock_window.show()
                self.unlock_window.raise_()
            else:
                self.unlock_window.hide()

    def _resize_to_content(self) -> None:
        def resize_now() -> None:
            layout = self.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            self.setMinimumHeight(0)
            self.adjustSize()
            target_height = max(70, self.sizeHint().height())
            self.resize(self.config.overlay_width, target_height)
            self._sync_unlock_window()

        QTimer.singleShot(0, resize_now)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        self.pit_overlay.setGeometry(8, 8, max(0, self.width() - 16), max(0, self.height() - 16))
        self._sync_unlock_window()
        super().resizeEvent(event)

    def moveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self._sync_unlock_window()
        super().moveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            not self._click_through
            and not self._position_locked
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._sync_unlock_window()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None:
            self._drag_offset = None
            self.settings.setValue("position", self.pos())
        super().mouseReleaseEvent(event)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
            if hasattr(self, "unlock_window"):
                self.unlock_window.hide()
        else:
            self.show()
            if self._click_through and hasattr(self, "unlock_window"):
                self._sync_unlock_window()
                self.unlock_window.show()

    def center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center() - self.rect().center())
        self.show()
        self._sync_unlock_window()

    def _sync_unlock_window(self) -> None:
        if not hasattr(self, "unlock_window"):
            return
        self.unlock_window.adjustSize()
        width = self.unlock_window.sizeHint().width()
        height = self.unlock_window.sizeHint().height()
        x = self.x() + max(0, self.width() - width)
        y = self.y() - height - 5
        screen = QApplication.screenAt(QPoint(self.x(), self.y()))
        if screen is not None:
            area = screen.availableGeometry()
            if y < area.top():
                y = self.y() + 8
            x = max(area.left(), min(x, area.right() - width))
        self.unlock_window.move(x, y)

    def _restore_position(self) -> None:
        position = self.settings.value("position")
        if not isinstance(position, QPoint):
            legacy_path = _get_appdata_dir() / "fuel_consumption_monitor.json"
            try:
                raw = json.loads(legacy_path.read_text(encoding="utf-8"))
                position = QPoint(int(raw["x"]), int(raw["y"]))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                position = None
        if isinstance(position, QPoint) and self._position_is_visible(position):
            self.move(position)
        else:
            self.center_on_screen()

    def _position_is_visible(self, point: QPoint) -> bool:
        return any(screen.availableGeometry().contains(point) for screen in QApplication.screens())

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def _request_quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.settings.setValue("position", self.pos())
        self._persist_inputs()
        self.openxr_server.stop()
        self.source.close()
        if hasattr(self, "tray"):
            self.tray.hide()
        if hasattr(self, "unlock_window"):
            self.unlock_window.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._shutdown()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _setting_float(self, key: str, default: float) -> float:
        try:
            return float(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def _setting_int(self, key: str, default: int) -> int:
        try:
            return int(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _make_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#21D4A7"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
        painter.setPen(QColor("#07110F"))
        painter.setFont(QFont("Segoe UI", 26, QFont.Weight.Black))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "F")
        painter.end()
        return QIcon(pixmap)


STYLE_SHEET = """
QWidget {
    color: #E9EDF5;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}
QWidget#fuelMonitorWindow { background: transparent; }
QFrame#shell {
    background: rgba(7, 10, 16, 246);
    border: 1px solid rgba(124, 138, 165, 60);
    border-radius: 13px;
}
QLabel#statusDot[connected="true"] { color: #21D4A7; }
QLabel#statusDot[connected="false"] { color: #EF6673; }
QFrame#fuelCard {
    background: rgba(18, 25, 36, 248);
    border: 1px solid rgba(33, 212, 167, 78);
    border-radius: 10px;
}
QFrame#metricsCard {
    background: rgba(10, 14, 22, 246);
    border: 1px solid rgba(124, 138, 165, 48);
    border-radius: 9px;
}
QFrame#metricTile {
    background: rgba(20, 27, 39, 245);
    border: 1px solid rgba(124, 138, 165, 42);
    border-radius: 7px;
}
QLabel#metricCaption {
    color: #96A3B7;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 1px;
}
QLabel#metricValue {
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 900;
}
QLabel#comparisonValue {
    color: #BCC6D7;
    font-size: 16px;
    font-weight: 900;
}
QLabel#metricValue[comparison="ahead"], QLabel#comparisonValue[comparison="ahead"] {
    color: #B784FF;
}
QLabel#metricValue[comparison="onplan"], QLabel#comparisonValue[comparison="onplan"] {
    color: #6FE38F;
}
QLabel#metricValue[comparison="behind"], QLabel#comparisonValue[comparison="behind"] {
    color: #FF6B6B;
}
QLabel#metricValue[comparison="neutral"], QLabel#comparisonValue[comparison="neutral"] {
    color: #BCC6D7;
}
QFrame#metricTile[comparison="ahead"] { border-color: #8053C7; }
QFrame#metricTile[comparison="onplan"] { border-color: #2D8D57; }
QFrame#metricTile[comparison="behind"] { border-color: #A94450; }
QFrame#strategyCard, QFrame#controlsCard, QFrame#advancedCard, QFrame#customizationCard {
    background: rgba(14, 19, 28, 242);
    border: 1px solid rgba(124, 138, 165, 38);
    border-radius: 8px;
}
QFrame#advancedCard { border-color: rgba(121, 168, 255, 54); }
QFrame#customizationCard { border-color: rgba(33, 212, 167, 62); }
QLabel#eyebrow, QLabel#sectionTitle, QLabel#controlCaption {
    color: #8996AA;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 1px;
}
QLabel#bigTime { color: #FFFFFF; font-size: 34px; font-weight: 800; }
QLabel#lapsRemaining { color: #21D4A7; font-size: 13px; font-weight: 700; }
QLabel#fuelBadge {
    padding: 4px 9px;
    border-radius: 7px;
    font-size: 11px;
    font-weight: 900;
}
QLabel#fuelBadge[state="finish"] { background: #173E35; color: #52E2BD; }
QLabel#fuelBadge[state="pit"] { background: #493519; color: #F6C45A; }
QLabel#fuelBadge[state="neutral"] { background: #293244; color: #BCC6D7; }
QLabel#sourceBadge {
    padding: 4px 8px;
    color: #D6C1FF;
    background: #2B1D46;
    border: 1px solid #6E45A8;
    border-radius: 7px;
    font-size: 9px;
    font-weight: 900;
}
QLabel#averageValue { font-size: 24px; font-weight: 900; }
QLabel#deltaValue { font-size: 14px; font-weight: 800; }
QLabel#averageValue[state="good"], QLabel#deltaValue[state="good"] { color: #52E2BD; }
QLabel#averageValue[state="danger"], QLabel#deltaValue[state="danger"] { color: #EF6673; }
QLabel#averageValue[state="neutral"], QLabel#deltaValue[state="neutral"] { color: #BCC6D7; }
QLabel#fuelDetailStrong { color: #E9EDF5; font-size: 12px; font-weight: 800; }
QLabel#fuelDetail { color: #9CA8BA; font-size: 9px; font-weight: 600; }
QLabel#strategyText { font-size: 11px; font-weight: 700; }
QLabel#strategyText[state="good"] { color: #52E2BD; }
QLabel#strategyText[state="warning"] { color: #F6C45A; }
QLabel#strategyText[state="danger"] { color: #EF6673; }
QLabel#strategyText[state="neutral"] { color: #79A8FF; }
QLabel#advancedText { color: #AEB8C8; font-size: 10px; }
QLabel#unitLabel { color: #738097; font-size: 9px; font-weight: 700; }
QLineEdit#numberEntry {
    color: #F2F5FA;
    background: #151C28;
    border: 1px solid #2A3547;
    border-radius: 5px;
    padding: 4px 6px;
    selection-background-color: #276C5D;
}
QLineEdit#numberEntry:focus { border-color: #21D4A7; }
QLineEdit#numberEntry:disabled { color: #758096; background: #101620; }
QComboBox#teamCarCombo {
    color: #F2F5FA;
    background: #151C28;
    border: 1px solid #2A3547;
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 9px;
    font-weight: 700;
}
QComboBox#teamCarCombo:hover { border-color: #21D4A7; }
QComboBox#teamCarCombo QAbstractItemView {
    color: #F2F5FA;
    background: #111824;
    border: 1px solid #3B4A62;
    selection-background-color: #276C5D;
}
QPushButton#smallButton {
    color: #AEB9CA;
    background: rgba(39, 49, 67, 190);
    border: 1px solid rgba(124, 138, 165, 45);
    border-radius: 5px;
    padding: 3px 6px;
    font-size: 8px;
    font-weight: 800;
}
QPushButton#smallButton:hover { color: #FFFFFF; background: #324158; }
QPushButton#smallButton:checked { color: #52E2BD; background: #173E35; border-color: #276C5D; }
QPushButton#smallButton:disabled { color: #536075; background: #151C27; }
QPushButton#closeButton {
    color: #AEB9CA;
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 2px;
    font-size: 15px;
    font-weight: 700;
}
QPushButton#closeButton:hover { color: #FFFFFF; background: #A83C4A; }
QCheckBox#compactCheck { color: #8F9BAE; font-size: 8px; font-weight: 800; spacing: 5px; }
QCheckBox#compactCheck::indicator {
    width: 12px;
    height: 12px;
    background: #151C28;
    border: 1px solid #354158;
    border-radius: 3px;
}
QCheckBox#compactCheck::indicator:checked { background: #21D4A7; border-color: #52E2BD; }
QCheckBox#optionCheck { color: #B8C2D2; font-size: 9px; font-weight: 700; spacing: 6px; }
QCheckBox#optionCheck::indicator {
    width: 13px;
    height: 13px;
    background: #151C28;
    border: 1px solid #445269;
    border-radius: 3px;
}
QCheckBox#optionCheck::indicator:checked { background: #21D4A7; border-color: #63E8C6; }
QLabel#settingLabel { color: #8996AA; font-size: 9px; font-weight: 800; }
QLabel#settingValue { color: #E9EDF5; font-size: 9px; font-weight: 800; }
QLabel#openXRHelp { color: #79A8FF; font-size: 9px; font-weight: 700; }
QSpinBox#settingSpin {
    color: #F2F5FA;
    background: #151C28;
    border: 1px solid #354158;
    border-radius: 5px;
    padding: 3px 6px;
    min-width: 48px;
}
QSpinBox#settingSpin:focus { border-color: #21D4A7; }
QSlider#settingSlider::groove:horizontal {
    height: 5px;
    background: #263145;
    border-radius: 2px;
}
QSlider#settingSlider::sub-page:horizontal { background: #21D4A7; border-radius: 2px; }
QSlider#settingSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    background: #E9EDF5;
    border: 1px solid #21D4A7;
    border-radius: 7px;
}
QLabel#legend { color: #647187; font-size: 9px; padding: 0 2px 2px 2px; }
QFrame#relativeRow {
    background: rgba(16, 21, 31, 238);
    border: 1px solid rgba(124, 138, 165, 35);
    border-radius: 7px;
}
QLabel#direction { font-size: 16px; font-weight: 900; }
QLabel#direction[side="ahead"] { color: #F6C45A; }
QLabel#direction[side="behind"] { color: #79A8FF; }
QLabel#carNumber { color: #FFFFFF; font-size: 12px; font-weight: 900; }
QLabel#driverName { color: #E9EDF5; font-size: 12px; font-weight: 700; }
QLabel#teamName { color: #7F8CA0; font-size: 9px; }
QLabel#currentStint { color: #FFFFFF; font-size: 10px; font-weight: 800; }
QLabel#estimate { color: #21D4A7; font-size: 10px; font-weight: 700; }
QLabel#teamTrend { color: #F6C45A; font-size: 9px; font-weight: 700; }
QLabel#history { color: #8B97A9; font-size: 9px; }
QFrame#pitOverlay {
    background: rgba(7, 10, 16, 252);
    border: 1px solid rgba(33, 212, 167, 100);
    border-radius: 13px;
}
QLabel#pitCaption { color: #8996AA; font-size: 11px; font-weight: 900; letter-spacing: 2px; }
QLabel#pitAverage { color: #52E2BD; font-size: 38px; font-weight: 900; }
QLabel#pitSubtitle { color: #8290A5; font-size: 11px; font-weight: 600; }
QMenu {
    color: #E9EDF5;
    background: #111722;
    border: 1px solid #2B3547;
    padding: 5px;
}
QMenu::item { padding: 6px 24px 6px 9px; border-radius: 4px; }
QMenu::item:selected { background: #243247; }
"""


HIGH_CONTRAST_STYLE_SHEET = """
QFrame#shell {
    background: #05080D;
    border: 1px solid #53627A;
}
QFrame#fuelCard {
    background: #111A27;
    border: 1px solid #21D4A7;
}
QFrame#metricsCard, QFrame#metricTile, QFrame#strategyCard, QFrame#controlsCard,
QFrame#advancedCard, QFrame#customizationCard,
QFrame#relativeRow {
    background: #0F1621;
    border-color: #334159;
}
QLabel#bigTime, QLabel#carNumber, QLabel#driverName,
QLabel#currentStint, QLabel#fuelDetailStrong { color: #FFFFFF; }
QLabel#fuelDetail, QLabel#teamName, QLabel#history,
QLabel#legend, QLabel#advancedText { color: #B3BECE; }
QLabel#eyebrow, QLabel#sectionTitle, QLabel#controlCaption,
QLabel#settingLabel, QLabel#unitLabel { color: #AAB6C8; }
QPushButton#smallButton {
    color: #E0E6EF;
    background: #26344A;
    border-color: #53627A;
}
QLineEdit#numberEntry, QSpinBox#settingSpin, QComboBox#teamCarCombo {
    color: #FFFFFF;
    background: #101925;
    border-color: #53627A;
}
"""


OPENXR_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FuelMonitor OpenXR</title>
<style>
:root {
  color-scheme: dark;
  font-family: "Segoe UI", Inter, system-ui, sans-serif;
  font-synthesis: none;
}
* { box-sizing: border-box; }
html, body {
  width: 100%;
  min-height: 100%;
  margin: 0;
  overflow: hidden;
  background: transparent;
}
body { padding: 12px; }
.panel {
  width: 936px;
  padding: 18px;
  color: #f7f9fc;
  background: rgba(5, 8, 13, .97);
  border: 2px solid #53627a;
  border-radius: 18px;
  box-shadow: 0 12px 34px rgba(0, 0, 0, .5);
}
.hero {
  display: grid;
  grid-template-columns: 1.35fr 1fr;
  align-items: center;
  gap: 22px;
  min-height: 128px;
  padding: 8px 14px 14px;
  border-bottom: 1px solid #334159;
}
.caption {
  color: #aab6c8;
  font-size: 15px;
  font-weight: 900;
  letter-spacing: .16em;
}
.time {
  margin-top: 1px;
  color: #fff;
  font-size: 60px;
  font-weight: 900;
  line-height: 1.05;
  letter-spacing: .02em;
}
.stint-summary {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.destination {
  padding: 5px 11px;
  color: #ffd37a;
  background: #493519;
  border-radius: 9px;
  font-size: 14px;
  font-weight: 900;
  letter-spacing: .12em;
}
.laps {
  color: #52e2bd;
  font-size: 28px;
  font-weight: 900;
  line-height: 1.1;
}
.current {
  color: #dce4ef;
  font-size: 18px;
  font-weight: 800;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 9px;
  padding-top: 12px;
}
.tile {
  min-width: 0;
  padding: 10px 12px 11px;
  background: #0f1621;
  border: 1px solid #334159;
  border-radius: 11px;
}
.tile .caption { font-size: 12px; }
.value {
  display: inline-block;
  margin-top: 3px;
  color: #fff;
  font-size: 25px;
  font-weight: 900;
  line-height: 1.1;
  white-space: nowrap;
}
.delta {
  display: block;
  min-height: 17px;
  margin-top: 2px;
  color: #aab6c8;
  font-size: 14px;
  font-style: normal;
  font-weight: 900;
}
.good { color: #52e2bd !important; }
.danger, .behind { color: #ff6b6b !important; }
.ahead { color: #b784ff !important; }
.onplan { color: #6fe38f !important; }
.neutral { color: #bcc6d7 !important; }
.rivals {
  display: grid;
  gap: 7px;
  margin-top: 10px;
}
.rival {
  padding: 9px 13px;
  overflow: hidden;
  color: #f1f4f8;
  background: #0c121c;
  border: 1px solid #2d394d;
  border-radius: 9px;
  font-size: 18px;
  font-weight: 800;
  line-height: 1.15;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rival.ahead-row { border-left: 5px solid #f6c45a; }
.rival.behind-row { border-left: 5px solid #79a8ff; }
</style>
</head>
<body>
<main class="panel">
  <section class="hero">
    <div>
      <div class="caption">STINT</div>
      <div id="time" class="time">--:--:--</div>
    </div>
    <div class="stint-summary">
      <div id="destination" class="destination">STINT</div>
      <div id="laps" class="laps">WAITING FOR IRACING</div>
      <div id="current" class="current">CURRENT -- L</div>
    </div>
  </section>
  <section class="metrics">
    <div class="tile"><div class="caption">FUEL</div><strong id="fuel" class="value">--</strong></div>
    <div class="tile"><div class="caption">AVERAGE</div><strong id="average" class="value neutral">--.-- L/lap</strong><em id="delta" class="delta neutral">--</em></div>
    <div class="tile"><div class="caption">PLAN</div><strong id="plan" class="value neutral">-- L</strong></div>
    <div class="tile"><div class="caption">EST.</div><strong id="estimate" class="value neutral">-- L</strong></div>
    <div class="tile"><div class="caption">VS PLAN</div><strong id="comparison" class="value neutral">-- L</strong></div>
  </section>
  <section id="rivals" class="rivals" hidden>
    <div id="ahead" class="rival ahead-row" hidden></div>
    <div id="behind" class="rival behind-row" hidden></div>
  </section>
</main>
<script>
const byId = id => document.getElementById(id);
let preferredHeight = 0;

function setText(id, value) {
  byId(id).textContent = value ?? "--";
}

function setState(id, value, state, extraClass = "value") {
  const element = byId(id);
  element.textContent = value ?? "--";
  element.className = `${extraClass} ${state || "neutral"}`;
}

function setRival(id, value) {
  const row = byId(id);
  row.textContent = value || "";
  row.hidden = !value;
}

async function update() {
  try {
    const response = await fetch(`/api/state?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    setText("time", state.time);
    setText("destination", state.destination);
    setText("laps", state.laps);
    setText("current", state.current);
    setText("fuel", state.fuel);
    setState("average", state.average, state.average_state);
    setState("delta", state.delta, state.average_state, "delta");
    setState("plan", state.plan, state.comparison_state);
    setState("estimate", state.estimate, state.comparison_state);
    setState("comparison", state.comparison, state.comparison_state);
    setRival("ahead", state.ahead);
    setRival("behind", state.behind);
    const haveRivals = Boolean(state.ahead || state.behind);
    byId("rivals").hidden = !haveRivals;
    const height = haveRivals ? 430 : 330;
    if (height !== preferredHeight && window.OpenKneeboard?.SetPreferredPixelSize) {
      preferredHeight = height;
      await window.OpenKneeboard.SetPreferredPixelSize(960, height);
    }
  } catch (_error) {
    setText("time", "--:--:--");
    setText("laps", "WAITING FOR FUELMONITOR");
  }
}

update();
setInterval(update, 250);
</script>
</body>
</html>
"""


UNLOCK_STYLE_SHEET = """
QWidget#unlockWindow { background: transparent; }
QPushButton#unlockButton {
    color: #07110F;
    background: #52E2BD;
    border: 2px solid #FFFFFF;
    border-radius: 7px;
    padding: 7px 12px;
    font-family: "Segoe UI", sans-serif;
    font-size: 11px;
    font-weight: 900;
}
QPushButton#unlockButton:hover { background: #FFFFFF; color: #08130F; }
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Nishizumi FuelMonitor")
    app.setOrganizationName("NishizumiTools")
    app.setQuitOnLastWindowClosed(False)
    window = FuelMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
