"""Pure calculation core for the Nishizumi Drive Fair Share Calculator.

The rules mirror the official static calculator while staying independent from
Tkinter, telemetry and the network.  Keeping this module UI-free makes the
calculation easy to test and safe to reuse in other Dominant Control surfaces.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class FairShareValidationError(ValueError):
    """A user-facing validation error raised for incomplete or invalid input."""


@dataclass(frozen=True, slots=True)
class FairShareResult:
    """Calculated laps and optional duration estimates."""

    total_laps: int
    drivers: int
    equal_share_laps: int
    fair_share_laps: int
    total_time_seconds: float | None
    equal_share_time_seconds: float | None
    fair_share_time_seconds: float | None
    estimated_laps_from_duration: bool


def _finite_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FairShareValidationError(f'{label} has to be a valid number.') from exc
    if not math.isfinite(number):
        raise FairShareValidationError(f'{label} has to be a valid number.')
    return number


def _whole_number(value: object, label: str, *, positive: bool = False) -> int:
    number = _finite_number(value, label)
    if not number.is_integer() or (positive and number <= 0):
        suffix = "a whole number greater than zero" if positive else "a whole number"
        raise FairShareValidationError(f'{label} must be {suffix}.')
    return int(number)


def _round_positive_like_javascript(value: float) -> int:
    """Match ``Math.round`` for the non-negative values used by the web app."""

    return int(math.floor(value + 0.5))


def calculate_fair_share(
    *,
    total_laps: int | float | None,
    drivers: int | float,
    average_lap_minutes: int | float = 0,
    average_lap_seconds: int | float = 0,
    race_hours: int | float = 0,
    race_minutes: int | float = 0,
) -> FairShareResult:
    """Calculate the official equal-share and minimum fair-share values.

    ``total_laps`` may be omitted.  In that case, race duration and average lap
    time are used to estimate it with the same rounding rule as the original
    JavaScript calculator.
    """

    driver_count = _whole_number(drivers, "Number of drivers", positive=True)

    avg_minutes = _whole_number(average_lap_minutes, "Average lap minutes")
    avg_seconds = _finite_number(average_lap_seconds, "Average lap seconds")
    if avg_minutes < 0 or avg_seconds < 0 or avg_seconds >= 60:
        raise FairShareValidationError(
            "The average lap has to use non-negative minutes and seconds between 0 and 59."
        )

    duration_hours = _whole_number(race_hours, "Race hours")
    duration_minutes = _whole_number(race_minutes, "Race minutes")
    if duration_hours < 0 or duration_minutes < 0 or duration_minutes >= 60:
        raise FairShareValidationError(
            "The duration has to use non-negative hours and minutes between 0 and 59."
        )

    average_lap_total = avg_minutes * 60.0 + avg_seconds
    race_duration_total = (duration_hours * 60.0 + duration_minutes) * 60.0
    estimated = total_laps is None

    if total_laps is None:
        if race_duration_total <= 0:
            raise FairShareValidationError(
                "Enter the total number of laps or the race duration."
            )
        if average_lap_total <= 0:
            raise FairShareValidationError(
                "Enter the average lap to estimate the total number of laps."
            )
        laps = max(
            1,
            _round_positive_like_javascript(
                race_duration_total / average_lap_total
            ),
        )
    else:
        laps = _whole_number(total_laps, "Total laps", positive=True)

    equal_share = int(math.ceil(laps / driver_count))
    fair_share = int(math.ceil(equal_share * 0.25))

    if average_lap_total > 0:
        total_time = laps * average_lap_total
        equal_time = equal_share * average_lap_total
        fair_time = fair_share * average_lap_total
    else:
        total_time = equal_time = fair_time = None

    return FairShareResult(
        total_laps=laps,
        drivers=driver_count,
        equal_share_laps=equal_share,
        fair_share_laps=fair_share,
        total_time_seconds=total_time,
        equal_share_time_seconds=equal_time,
        fair_share_time_seconds=fair_time,
        estimated_laps_from_duration=estimated,
    )


def format_duration(seconds: float | None) -> str:
    """Format a calculator duration compactly for the Portuguese UI."""

    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return "—"
    total_minutes = _round_positive_like_javascript(seconds / 60.0)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes:02d} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


__all__ = [
    "FairShareResult",
    "FairShareValidationError",
    "calculate_fair_share",
    "format_duration",
]
