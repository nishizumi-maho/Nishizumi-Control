"""
Text formatting for each overlay mode (compact / normal / detailed).

Presentation only - no calculation happens here.
"""

from __future__ import annotations

from typing import Optional

from overlay.styles import COLOR_NEUTRAL, COLOR_TEXT_DIM, COLOR_WARNING, delta_color
from sr.calculator import SREstimate

SESSION_TYPE_LABELS = {
    "practice": "Practice",
    "qualify": "Qualifying",
    "warmup": "Warmup",
    "race": "Race",
    "time_trial": "Time Trial",
    "unknown": "Session",
}


def _cpi_text(cpi: Optional[float]) -> str:
    # "Clean" rather than an infinity symbol: easier to read at a glance mid-race,
    # and it avoids looking like something broke when it just means 0 incidents.
    return "Clean" if cpi is None else f"{cpi:.1f}"


def _sr_text(value: Optional[float]) -> str:
    return f"{value:.2f}" if value is not None else "--"


def format_waiting(message: str) -> str:
    return f'<span style="color:{COLOR_TEXT_DIM};">{message}</span>'


def format_practice_notice(estimate: SREstimate) -> str:
    return (
        f'<span style="color:{COLOR_WARNING};">PRACTICE (does not count towards SR)</span><br>'
        f'<span style="color:{COLOR_TEXT_DIM};">Corners: {estimate.corners:.0f} '
        f'&nbsp;|&nbsp; Incidents: {estimate.incidents}x</span>'
    )


def format_compact(estimate: SREstimate) -> str:
    color = delta_color(estimate.session_delta)
    return f'<span style="color:{color}; font-weight:600;">SR {estimate.session_delta:+.2f}</span>'


def format_normal(estimate: SREstimate) -> str:
    color = delta_color(estimate.session_delta)
    return (
        f'<span style="color:{COLOR_NEUTRAL};">SR {_sr_text(estimate.initial_sr)} '
        f'&#8594; {_sr_text(estimate.projected_sr)}</span><br>'
        f'<span style="color:{color}; font-weight:600;">{estimate.session_delta:+.2f}</span>'
        f'<span style="color:{COLOR_TEXT_DIM};"> | {estimate.incidents}x | CPI {_cpi_text(estimate.cpi)}</span>'
    )


def format_detailed(estimate: SREstimate, session_type: str, track_display_name: str) -> str:
    color = delta_color(estimate.session_delta)
    laps_text = f"{estimate.laps_completed} / {estimate.laps_total}" if estimate.laps_total else f"{estimate.laps_completed}"
    session_label = SESSION_TYPE_LABELS.get(session_type, session_type)

    return (
        f'<span style="color:{COLOR_TEXT_DIM}; letter-spacing:2px;">ESTIMATED SAFETY RATING</span><br>'
        f'<span style="color:{COLOR_TEXT_DIM};">{track_display_name} &middot; {session_label}</span><br><br>'
        f'<span style="color:{COLOR_NEUTRAL};">Current&nbsp;&nbsp;&nbsp;&nbsp;{_sr_text(estimate.initial_sr):>8}</span><br>'
        f'<span style="color:{color};">Session&nbsp;&nbsp;&nbsp;{estimate.session_delta:+8.2f}</span><br>'
        f'<span style="color:{COLOR_NEUTRAL};">Projected&nbsp;{_sr_text(estimate.projected_sr):>8}</span><br><br>'
        f'<span style="color:{COLOR_TEXT_DIM};">Incidents&nbsp;&nbsp;{estimate.incidents:>7}x</span><br>'
        f'<span style="color:{COLOR_TEXT_DIM};">Corners&nbsp;&nbsp;&nbsp;&nbsp;{estimate.corners:>8.0f}</span><br>'
        f'<span style="color:{COLOR_TEXT_DIM};">CPI&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{_cpi_text(estimate.cpi):>8}</span><br>'
        f'<span style="color:{COLOR_TEXT_DIM};">Target CPI&nbsp;{estimate.target_cpi:>8.1f}</span><br><br>'
        f'<span style="color:{COLOR_NEUTRAL};">Lap&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{laps_text:>8}</span><br>'
        f'<span style="color:{COLOR_TEXT_DIM}; font-size: 0.85em;">Confidence: {estimate.confidence}</span>'
    )


def format_session_complete(estimated_delta: float, actual_delta: Optional[float], final_sr: Optional[float]) -> str:
    lines = ['<span style="color:#E8E8E8; letter-spacing:2px;">SESSION COMPLETE</span><br><br>']
    lines.append(f'<span style="color:{COLOR_TEXT_DIM};">Estimated</span><br>')
    lines.append(f'<span style="color:{delta_color(estimated_delta)};">{estimated_delta:+.2f}</span><br><br>')
    if actual_delta is not None:
        lines.append(f'<span style="color:{COLOR_TEXT_DIM};">Actual</span><br>')
        lines.append(f'<span style="color:{delta_color(actual_delta)};">{actual_delta:+.2f}</span><br><br>')
        error = abs(actual_delta - estimated_delta)
        lines.append(f'<span style="color:{COLOR_TEXT_DIM};">Error</span><br>')
        lines.append(f'<span style="color:{COLOR_NEUTRAL};">{error:.2f}</span><br><br>')
    if final_sr is not None:
        lines.append(f'<span style="color:{COLOR_TEXT_DIM};">Final SR</span><br>')
        lines.append(f'<span style="color:{COLOR_NEUTRAL};">{final_sr:.2f}</span>')
    return "".join(lines)
