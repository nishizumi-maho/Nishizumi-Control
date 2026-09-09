"""Safety Rating model (v4).

WHAT THIS IS
Not iRacing's source code - a reconstruction measured against iRacing's own
published result data. Two of the three steps are essentially exact; the
third is fitted and its residual error is stated below.

THE MODEL, IN THREE STEPS
1. **Invert.** Turn the SR read from the SDK into the CPI sitting behind it
   -> `license_map.cpi_from_sr`. Exact: the CPI<->SR mapping reproduces
   iRacing's own number with MAE 0.007 SR over 12,639 observations.

2. **Update the CPI** with the session just driven:

       x     = 1 / CPI                       (incidents per corner)
       alpha = 1 - exp(-corners / window)    (this session's weight)
       x_new = x*(1 - alpha) + alpha * (incident_points / corners)

   Corners and incidents both arrive already multiplied by the session weight
   from Sporting Code 3.6.1.1 - the official name is "Corner AND Incident
   Multipliers", so the weight applies to both sides.

3. **Remap to SR** using the overlapping-band rule (Sporting Code 3.7.4)
   -> `license_map.remap_band`. The documented "+0.40 when you cross a whole
   number" is not hardcoded anywhere; it falls out of the band geometry.

THE WINDOW - what was measured, and how
Because official results carry both `old_cpi` and `new_cpi`, alpha can be
solved exactly for every session:

    alpha = (1/CPI_new - 1/CPI_old) / (incidents/corners - 1/CPI_old)

Doing that for 10,773 driver-sessions (GT Sprint plus four endurance series)
gave two results that the earlier model had wrong:

- **The window depends only on the SR band, never on the licence class.**
  A Class B driver and a Class A driver sitting in the same whole-number band
  share the same window to within 2% (B band 3 = 2274 corners, A band 3 =
  2314). It grows by a factor of ~1.41 per band, so a higher SR means a longer
  memory - which is why SR gets progressively harder to move as it climbs.

- **The weighting is an exponential moving average, not a fixed window.**
  Binning the solved alpha against corners/window reproduces 1 - exp(-x)
  across the entire observed range (x from 0.06 to 2.9). The previous model
  used min(cap, x), which is only correct for small x and needed an arbitrary
  cap near 1/3 to stop long races blowing up. That cap was an artifact of
  fitting a fixed window to nine races.

That first fit gave window_band1 = 1050 corners with a ratio of 1.4142 (sqrt 2)
per band. Giving each band its own free window converges onto exactly that
geometric progression, so the law is real rather than imposed.
Accuracy over the 10,773 sessions: 1.4% RMSE on the predicted CPI, 0.026 SR
mean absolute error, 6.5% of predictions off by more than 0.05 SR. The old
linear-plus-cap form scored 9.2% / 0.090 SR / 51% on the same data.

WHAT IS ACTUALLY SHIPPED
The constants in SRModelParams below, not the ones above: window_band1 = 1060
with the same sqrt(2) ratio, plus the Rookie (0.69) and Class D (0.83) factors.
They come from the later, much larger fit over 123,361 driver-sessions, which
also produced the per-class factors the 10,773-session fit could not resolve.
Held out on a random half of that set: 2.90% CPI error, 0.033 SR mean absolute
error, 88% within 0.05 SR. See docs/MODEL.md section 9.

config/settings.py stamps these values into settings.json, so a file written by
an older build cannot silently keep an app running last release's constants.

KNOWN LIMITATION
Corners are counted from laps in offline validation, so a driver who pits and
rejoins mid-lap has their corners undercounted. Running live the app uses
LapDistPct and counts the fraction actually driven, so this case is worse in
validation than in real use.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from sr.license_map import cpi_from_sr, remap_band

logger = logging.getLogger("sr_estimator.model")

# Sporting Code v2025.09.09, section 3.6.1.1 - "Table of Corner and Incident
# Multipliers". These apply to corners AND incidents.
SESSION_WEIGHTS = {
    "race": 1.00,
    "heat": 1.00,
    "qualify": 0.50,        # Qualify - Open (the usual case in official events)
    "qualify_lone": 0.35,   # Qualify - Lone
    "warmup": 0.50,
    "practice": 0.50,       # Practice - Open inside a Ranked event
    "practice_lone": 0.35,
    "time_trial": 0.35,
    "unknown": 0.00,        # "All Other Session Types" -> 0.00
}


@dataclass
class SRModelParams:
    """The shipped constants: fitted to 123,361 driver-sessions, held out on half.

    `window_band1` is the EMA decay constant, in corners, for drivers sitting in
    the 1.xx SR band; `window_band_ratio` multiplies it for each band above that.
    The earlier 10,773-session fit over sprint and endurance races (docs/
    RESEARCH.md 9.5) agreed on the shape and the ratio; the larger set moved
    window_band1 from 1050 to 1060 and resolved the Rookie and Class D factors.
    """
    window_band1: float = 1060.0
    window_band_ratio: float = 1.4142  # sqrt(2)
    # Rookie and Class D move faster than everyone else: their windows are
    # measurably shorter at the same band. C, B, A and Pro share one window.
    window_factor_rookie: float = 0.69
    window_factor_class_d: float = 0.83
    max_abs_session_delta: float = 3.0  # sanity clamp


DEFAULT_PARAMS = SRModelParams()


def session_weight(session_type: Optional[str]) -> float:
    """Sporting Code weight for a session type (0.00 when unrecognised)."""
    return SESSION_WEIGHTS.get((session_type or "unknown").lower(), 0.00)


def window_corners(band: int, class_index: int = 2, params: "SRModelParams" = None) -> float:
    """EMA decay constant, in corners, for this band and licence class.

    Measured across 13,849 driver-sessions:

    - Within Class C, B, A and Pro the window depends ONLY on the SR band, not
      on the class. At band 3 the medians are C 2451, B 2342, A 2448 corners.
    - Rookie and Class D are genuinely shorter, so their SR moves faster:
      at band 2, R ~1024 and D ~1367 against ~1750 for C/B/A.
    - Each band up multiplies the window by sqrt(2).

    Higher SR therefore means a longer memory, which is why SR gets
    progressively harder to move as it climbs.
    """
    p = params or DEFAULT_PARAMS
    if class_index <= 0:
        factor = p.window_factor_rookie
    elif class_index == 1:
        factor = p.window_factor_class_d
    else:
        factor = 1.0
    return p.window_band1 * (p.window_band_ratio ** max(0, int(band) - 1)) * factor


def session_alpha(corners: float, band: int, class_index: int = 2,
                  params: "SRModelParams" = None) -> float:
    """Weight this session carries in the rolling average.

    alpha = 1 - exp(-corners / window)

    This is an exponentially weighted moving average, not a fixed window with a
    cutoff. The shape was measured directly: solving alpha exactly for each of
    10,773 sessions and binning by corners/window reproduces 1 - exp(-x) across
    the whole observed range (x from 0.06 to 2.9). The previous model used
    min(cap, x), which is only correct for small x and needed an arbitrary cap
    to stop long races blowing up.
    """
    if corners <= 0:
        return 0.0
    return 1.0 - math.exp(-corners / max(1.0, window_corners(band, class_index, params)))


def update_cpi(
    cpi_old: float,
    corners: float,
    incident_points: float,
    band: int,
    class_index: int = 2,
    params: SRModelParams = None,
) -> float:
    """Apply one session to the accumulated CPI (step 2 of the model).

    `corners` and `incident_points` must already carry the session weight.
    Never divides by zero and never returns infinity.
    """
    params = params or DEFAULT_PARAMS
    if corners <= 0 or cpi_old <= 0:
        return cpi_old
    alpha = session_alpha(corners, band, class_index, params)
    x_old = 1.0 / cpi_old
    x_session = incident_points / corners
    x_new = x_old * (1.0 - alpha) + alpha * x_session
    if x_new <= 1e-9:
        # A perfect session filling the whole window: huge CPI, but finite.
        # SR is clamped to 4.99 in the remap anyway, so this only avoids inf.
        return 1e9
    return 1.0 / x_new


def project_sr(
    sr_before: float,
    class_index: int,
    corners: float,
    incident_points: float,
    band: Optional[int] = None,
    params: SRModelParams = DEFAULT_PARAMS,
) -> tuple[float, float, float]:
    """Full estimate for a session.

    Returns (projected_sr, delta, new_cpi). `corners` and `incident_points`
    must already carry the session weight.
    """
    if band is None:
        band = int(math.floor(sr_before))
    cpi_old = cpi_from_sr(sr_before, class_index, band)
    cpi_new = update_cpi(cpi_old, corners, incident_points, band, class_index, params)
    sr_new, _ = remap_band(cpi_new, class_index, band)

    delta = sr_new - sr_before
    bound = abs(params.max_abs_session_delta)
    if abs(delta) > bound:
        logger.error(
            "Estimated SR delta (%+.2f) exceeded the safety bound (%.2f); clamped",
            delta, bound,
        )
        delta = max(-bound, min(bound, delta))
        sr_new = sr_before + delta
    return sr_new, delta, cpi_new


def estimate_delta(
    sr_before: float,
    class_index: int,
    corners: float,
    incident_points: float,
    band: Optional[int] = None,
    params: SRModelParams = DEFAULT_PARAMS,
) -> float:
    """Shortcut when only the delta is needed."""
    return project_sr(sr_before, class_index, corners, incident_points, band, params)[1]


def compute_cpi(corners: float, incident_points: float) -> Optional[float]:
    """CPI of the CURRENT session (not the accumulated one). None = clean."""
    if incident_points <= 0:
        return None
    return corners / incident_points
