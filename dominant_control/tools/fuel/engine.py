#!/usr/bin/env python3
"""UI-independent fuel strategy and endurance engine from Fuel Monitor."""

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

from ...paths import tools_data_dir


OPENXR_HOST = "127.0.0.1"
OPENXR_PORT = 61337
OPENXR_URL = f"http://{OPENXR_HOST}:{OPENXR_PORT}/"
RACING = 4
CHECKERED = 5
COOL_DOWN = 6
YELLOW_FLAGS = 0x0008 | 0x4000 | 0x8000
LITER_TO_GALLON = 0.2641720524


def _get_appdata_dir() -> Path:
    return tools_data_dir()


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

        status = "BOXES" if now < self._pit_hold_until else "ACTIVE"
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


# ---- Runtime adapter --------------------------------------------------------

try:
    import irsdk  # type: ignore
except ImportError:
    irsdk = None  # type: ignore[assignment]


class IRacingSDKSource:
    """Defensive adapter around pyirsdk's live shared-memory reader."""

    def __init__(self, retry_seconds: float = 2.0, ir_client: Any = None):
        self.retry_seconds = retry_seconds
        self.manual_team_car_number = ""
        self._ir: Any = ir_client
        self._shared_client = ir_client is not None
        self._next_retry = 0.0
        self.last_error = ""

    def read(self) -> TelemetrySnapshot:
        if self._ir is None and irsdk is None:
            self.last_error = "pyirsdk is not installed"
            return TelemetrySnapshot(connected=False)

        if self._ir is None and time.monotonic() >= self._next_retry:
            self._connect()
        if self._ir is None:
            return TelemetrySnapshot(connected=False)

        try:
            connected = getattr(self._ir, "is_connected", None)
            if connected is not None and not bool(connected):
                if not self._shared_client:
                    self._disconnect()
                return TelemetrySnapshot(connected=False)
            return self._read_connected()
        except Exception as exc:  # shared memory can disappear between reads
            self.last_error = f'Could not read the SDK: {exc}'
            if not self._shared_client:
                self._disconnect()
            return TelemetrySnapshot(connected=False)

    def close(self) -> None:
        if not self._shared_client:
            self._disconnect(schedule_retry=False)

    @staticmethod
    def _select_tracked_car(
        *,
        local_on_track: bool,
        raw_player_idx: int,
        automatic_team_idx: int,
        manual_number: str,
        manual_team_idx: int,
    ) -> tuple[int, TelemetrySource]:
        """Choose a safe telemetry car, falling back when an override is invalid."""

        if local_on_track and raw_player_idx >= 0:
            return raw_player_idx, TelemetrySource.LOCAL
        if manual_number and manual_team_idx >= 0:
            return manual_team_idx, TelemetrySource.TEAM
        return automatic_team_idx, TelemetrySource.TEAM

    def _connect(self) -> None:
        self._next_retry = time.monotonic() + self.retry_seconds
        if self._shared_client:
            try:
                self._ir.startup()
            except Exception as exc:
                self.last_error = f'Could not start the shared telemetry: {exc}'
            return
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
        if self._shared_client:
            return
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

            # CamCarIdx is intentionally never used here: the spectator camera
            # may be focused on an unrelated competitor.  An invalid manual
            # number falls back to the team car instead of selecting index -1.
            player_idx, telemetry_source = self._select_tracked_car(
                local_on_track=local_on_track,
                raw_player_idx=raw_player_idx,
                automatic_team_idx=automatic_team_idx,
                manual_number=manual_number,
                manual_team_idx=manual_team_idx,
            )

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


OPENXR_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Nishizumi Fuel Monitor</title><style>
html,body{margin:0;background:#07110f;color:#edfdf8;font-family:Segoe UI,Arial,sans-serif}
main{padding:18px;border:2px solid #1a745f;border-radius:16px;background:#0b1a17}
.top{display:flex;justify-content:space-between;color:#73e6c5;font-weight:700}
.hero{font-size:42px;font-weight:900;margin:10px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.card{background:#102520;border:1px solid #245a4c;border-radius:9px;padding:10px}.k{color:#8faea5;font-size:12px}.v{font-size:20px;font-weight:800}
.good{color:#62e7a9}.danger{color:#ff7070}.neutral{color:#edfdf8}.rivals{margin-top:8px;color:#cadfd9}
</style></head><body><main><div class="top"><span>FUEL MONITOR</span><span id="time">--:--:--</span></div>
<div id="laps" class="hero">WAITING FOR IRACING</div><div class="grid">
<div class="card"><div class="k">FUEL</div><div id="fuel" class="v">--</div></div>
<div class="card"><div class="k">AVERAGE</div><div id="average" class="v neutral">--</div></div>
<div class="card"><div class="k">DELTA</div><div id="delta" class="v neutral">--</div></div>
<div class="card"><div class="k">PLAN</div><div id="plan" class="v">--</div></div>
<div class="card"><div class="k">ESTIMATE</div><div id="estimate" class="v">--</div></div>
<div class="card"><div class="k">STINT</div><div id="current" class="v">--</div></div></div>
<div id="ahead" class="rivals"></div><div id="behind" class="rivals"></div></main><script>
const ids=['time','laps','fuel','average','delta','plan','estimate','current','ahead','behind'];
async function tick(){try{const r=await fetch('/api/state?t='+Date.now(),{cache:'no-store'});const s=await r.json();
ids.forEach(k=>document.getElementById(k).textContent=s[k]||'');
document.getElementById('average').className='v '+(s.average_state||'neutral');document.getElementById('delta').className='v '+(s.average_state||'neutral');}catch(e){}}
tick();setInterval(tick,250);</script></body></html>"""


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
