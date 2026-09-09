"""Exact CPI <-> Safety Rating mapping for iRacing.

WHERE THIS COMES FROM
iRacing's official results expose, per driver, `old_cpi`/`new_cpi` (the real
CPI the sim uses) alongside `old_sub_level`/`new_sub_level` (SR x 100) and
`old_license_level`/`new_license_level`. See docs/RESEARCH.md section 2.

Fitted to 12,639 real (SR, CPI, licence level) observations, the mapping is a
straight line in ln(CPI) inside every band:

    SR = band + a * ln( CPI / floor_cpi(class, band) )

    floor_cpi(class, band) = F0 * r ** (class_index + band)

Global least squares:
    a  = 1.5236      MAE 0.0068 SR
    r  = 1.4562      max 0.040 SR
    F0 = 3.4949

Validated against a completely independent series after fitting, where a refit
moved the constants by less than 0.5%.

TWO THINGS THAT FALL OUT OF THIS GEOMETRY FOR FREE
1. The "+0.40 on crossing a whole number" iRacing documents does not need to be
   coded: it is the result of remapping the same CPI into the neighbouring
   band. The implied jump is 1 - a*ln(r) = 0.427, which the official article
   rounds to 0.40. See `remap_band`.
2. A class promotion costs exactly 1.00 SR: because the floor depends on
   (class_index + band), moving up one class with the same CPI drops the band
   by exactly one. Matches Sporting Code 3.9.3 ("approximately 1.00").

WHAT IS NOT EXACT HERE: nothing. The rolling-average step lives in sr/model.py
and is the only fitted part of the pipeline.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Optional

logger = logging.getLogger("sr_estimator.license_map")

# Constants fitted to the real observations described above.
A_SLOPE = 1.5236
R_RATIO = 1.4562
F0_CPI = 3.4949

BAND_MIN = 0
BAND_MAX = 4          # displayed SR tops out at 4.99 (Sporting Code 3.7.2)
SR_DISPLAY_MAX = 4.99
SR_DISPLAY_MIN = 0.00

CLASS_INDEX = {
    "R": 0, "ROOKIE": 0,
    "D": 1,
    "C": 2,
    "B": 3,
    "A": 4,
    "P": 5, "PRO": 5,
}
_CLASS_BY_INDEX = {0: "R", 1: "D", 2: "C", 3: "B", 4: "A", 5: "P"}

_LIC_LETTER_RE = re.compile(r"^([A-Za-z]+)")


def class_and_band_from_license_level(lic_level: int) -> tuple[int, int]:
    """SDK `LicLevel` (1..24) -> (class_index, band).

    Confirmed against real data: level 13 -> Class B band 1; level 20 -> Class A
    band 4. Each class occupies 4 consecutive levels, one per whole-number SR
    band.
    """
    idx = (int(lic_level) - 1) // 4
    band = ((int(lic_level) - 1) % 4) + 1
    return idx, band


def class_index_from_string(lic_string: Optional[str]) -> Optional[int]:
    """"B 3.54" / "Class B" -> 3. Returns None when unrecognisable."""
    s = (lic_string or "").strip().upper()
    s = s.replace("CLASS", "").strip()
    match = _LIC_LETTER_RE.match(s)
    if not match:
        return None
    return CLASS_INDEX.get(match.group(1))


def band_floor_cpi(class_index: int, band: int) -> float:
    """CPI at the floor of a whole SR band (e.g. the exact CPI of "3.00")."""
    return F0_CPI * (R_RATIO ** (class_index + band))


def sr_from_cpi(cpi: float, class_index: int, band: int) -> float:
    """Displayed SR for a CPI within a specific band (no remapping)."""
    if cpi <= 0:
        return float(band)
    return band + A_SLOPE * math.log(cpi / band_floor_cpi(class_index, band))


def cpi_from_sr(sr: float, class_index: int, band: Optional[int] = None) -> float:
    """Inverse of sr_from_cpi: the CPI sitting behind a displayed SR.

    This is what lets the app start from the real SR read off the SDK and work
    in the same quantity the actual algorithm uses.
    """
    if band is None:
        band = int(math.floor(sr))
    band = max(BAND_MIN, min(BAND_MAX, band))
    return band_floor_cpi(class_index, band) * math.exp((sr - band) / A_SLOPE)


def remap_band(cpi: float, class_index: int, band: int) -> tuple[float, int]:
    """Applies the overlapping-band rule from Sporting Code 3.7.4.

    While the computed SR falls outside [band, band+1), the same CPI is
    remapped into the neighbouring band. This is where the official article's
    "+0.40 / -0.40" comes from: here it works out to 1 - a*ln(r) = 0.427, and
    it is not a hardcoded number - it is a consequence of the geometry
    (see band_crossing_bonus()).

    Returns (displayed_sr, final_band).
    """
    band = max(BAND_MIN, min(BAND_MAX, int(band)))
    for _ in range(8):  # convergence is guaranteed; the bound only avoids a hang
        sr = sr_from_cpi(cpi, class_index, band)
        if sr >= band + 1.0 and band < BAND_MAX:
            band += 1
            continue
        if sr < band and band > BAND_MIN:
            band -= 1
            continue
        return max(SR_DISPLAY_MIN, min(SR_DISPLAY_MAX, sr)), band
    return max(SR_DISPLAY_MIN, min(SR_DISPLAY_MAX, sr_from_cpi(cpi, class_index, band))), band


def band_crossing_bonus() -> float:
    """The real jump when crossing a whole number (iRacing documents 0.40)."""
    return 1.0 - A_SLOPE * math.log(R_RATIO)


def target_cpi_to_hold(class_index: int, sr: float = 3.00) -> float:
    """CPI required to hold a given SR (3.00 by default) in that class.

    Replaces the community estimates this project used to rely on. The measured
    numbers are well below the third-party figures for the higher classes
    (A: ~48.5 measured, versus the widely repeated "70-80").
    """
    band = int(math.floor(sr))
    return cpi_from_sr(sr, class_index, band)


def describe_ladder() -> str:
    """Readable table of CPI floors - handy in logs and for cross-checking."""
    lines = ["class   SR1.00   SR2.00   SR3.00   SR4.00"]
    for idx in range(6):
        floors = "  ".join(f"{band_floor_cpi(idx, b):7.2f}" for b in (1, 2, 3, 4))
        lines.append(f"  {_CLASS_BY_INDEX[idx]:<5} {floors}")
    return "\n".join(lines)
