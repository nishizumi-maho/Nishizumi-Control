"""Configuration model for auxiliary ("ghost") keys.

A ghost row says: *when the driver presses any of these auxiliary triggers,
send the key the game already listens to*.  It is the same trick the Macros
tab uses for the scanned ``dc*`` controls, generalised to every other iRacing
control - look left, pit commands, black boxes, replay, camera, chat.

One row owns one game key and any number of auxiliary triggers, so a wheel
button, a button box key and a keypad key can all reach the same in-game
action.  Rows carry a free-text description and live either in the app
(``global``) or in the current car's profile (``car``).

This module deliberately has no Tk, Win32 or iRacing SDK dependency: it only
normalises and validates dictionaries so the panel, the persistence layer and
the tests all agree on one shape.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

GHOST_SCOPE_GLOBAL = "global"
GHOST_SCOPE_CAR = "car"
GHOST_SCOPES = (GHOST_SCOPE_GLOBAL, GHOST_SCOPE_CAR)

GHOST_MODE_PULSE = "pulse"
GHOST_MODE_HOLD = "hold"
GHOST_MODES = (GHOST_MODE_PULSE, GHOST_MODE_HOLD)

MAX_GHOST_ROWS = 200
MAX_GHOST_TRIGGERS = 8

GHOST_SCOPE_LABELS = {
    GHOST_SCOPE_GLOBAL: "Global (every car)",
    GHOST_SCOPE_CAR: "This car only",
}
GHOST_MODE_LABELS = {
    GHOST_MODE_PULSE: "Pulse",
    GHOST_MODE_HOLD: "Hold",
}


def normalize_ghost_scope(value: Any) -> str:
    """Return a valid storage scope, defaulting to global."""

    text = str(value or "").strip().lower()
    return text if text in GHOST_SCOPES else GHOST_SCOPE_GLOBAL


def normalize_ghost_mode(value: Any) -> str:
    """Return a valid actuation mode, defaulting to a single pulse."""

    text = str(value or "").strip().lower()
    return text if text in GHOST_MODES else GHOST_MODE_PULSE


def _clean_text(value: Any, limit: int = 120) -> str:
    text = str(value if value is not None else "").strip()
    return text[:limit]


def normalize_ghost_triggers(value: Any) -> List[str]:
    """Return the auxiliary trigger codes, de-duplicated and order-preserving."""

    if value in (None, ""):
        return []
    raw: Iterable[Any]
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = [value]

    triggers: List[str] = []
    for item in raw:
        code = str(item or "").strip()
        if not code or code.upper() == "CANCEL":
            continue
        if code in triggers:
            continue
        triggers.append(code)
        if len(triggers) >= MAX_GHOST_TRIGGERS:
            break
    return triggers


def default_ghost_row(
    index: int = 1,
    *,
    control: Any = "",
    description: Any = "",
    game_binding: Any = None,
    game_label: Any = "",
    scope: Any = GHOST_SCOPE_GLOBAL,
) -> dict:
    """Return one empty auxiliary-key row ready for the panel."""

    safe_index = max(1, int(index))
    return {
        "id": f"ghost-{safe_index}",
        "description": _clean_text(description),
        "control": _clean_text(control, limit=64),
        "game_binding": game_binding or None,
        "game_label": _clean_text(game_label, limit=64),
        "triggers": [],
        "mode": GHOST_MODE_PULSE,
        "scope": normalize_ghost_scope(scope),
        "enabled": True,
    }


def _safe_row_id(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def sanitize_ghost_row(
    row: Any,
    index: int = 1,
    *,
    scope: Optional[str] = None,
) -> Optional[dict]:
    """Return one normalized row, or None when the payload is unusable."""

    if not isinstance(row, dict):
        return None

    sanitized = default_ghost_row(index)
    sanitized["id"] = _safe_row_id(row.get("id"), sanitized["id"])
    sanitized["description"] = _clean_text(row.get("description"))
    sanitized["control"] = _clean_text(row.get("control"), limit=64)
    binding = row.get("game_binding")
    sanitized["game_binding"] = binding if binding not in ("", None) else None
    sanitized["game_label"] = _clean_text(row.get("game_label"), limit=64)
    sanitized["triggers"] = normalize_ghost_triggers(row.get("triggers"))
    sanitized["mode"] = normalize_ghost_mode(row.get("mode"))
    sanitized["scope"] = normalize_ghost_scope(
        scope if scope is not None else row.get("scope")
    )
    sanitized["enabled"] = bool(row.get("enabled", True))
    return sanitized


def sanitize_ghost_rows(
    rows: Any,
    *,
    scope: Optional[str] = None,
) -> List[dict]:
    """Return a normalized list of rows with unique ids."""

    if not isinstance(rows, (list, tuple)):
        return []

    sanitized: List[dict] = []
    used_ids: set = set()
    for position, raw in enumerate(rows, start=1):
        row = sanitize_ghost_row(raw, position, scope=scope)
        if row is None:
            continue
        if row["id"] in used_ids:
            row["id"] = next_ghost_row_id(used_ids)
        used_ids.add(row["id"])
        sanitized.append(row)
        if len(sanitized) >= MAX_GHOST_ROWS:
            break
    return sanitized


def next_ghost_row_id(used_ids: Iterable[Any]) -> str:
    """Return an unused ``ghost-N`` identifier."""

    used = {str(item or "") for item in used_ids}
    index = 1
    while f"ghost-{index}" in used:
        index += 1
    return f"ghost-{index}"


def split_ghost_rows_by_scope(rows: Any) -> dict:
    """Split sanitized rows into the global and per-car buckets."""

    buckets: dict = {GHOST_SCOPE_GLOBAL: [], GHOST_SCOPE_CAR: []}
    for row in sanitize_ghost_rows(rows):
        buckets[row["scope"]].append(row)
    return buckets


def merge_ghost_rows(global_rows: Any, car_rows: Any) -> List[dict]:
    """Return the rows shown in the panel: global first, then this car's."""

    merged = list(sanitize_ghost_rows(global_rows, scope=GHOST_SCOPE_GLOBAL))
    used_ids = {row["id"] for row in merged}
    for row in sanitize_ghost_rows(car_rows, scope=GHOST_SCOPE_CAR):
        if row["id"] in used_ids:
            row = dict(row)
            row["id"] = next_ghost_row_id(used_ids)
        used_ids.add(row["id"])
        merged.append(row)
    return merged


def ghost_row_is_active(row: Any) -> bool:
    """Return True when a row can actually fire in-game."""

    if not isinstance(row, dict):
        return False
    if not row.get("enabled", True):
        return False
    if not row.get("game_binding"):
        return False
    return bool(normalize_ghost_triggers(row.get("triggers")))


def ghost_row_problem(row: Any) -> Optional[str]:
    """Return a short reason the row cannot fire yet, or None when it can."""

    if not isinstance(row, dict):
        return "Invalid row."
    if not row.get("game_binding"):
        return "Set the key the game understands."
    if not normalize_ghost_triggers(row.get("triggers")):
        return "Add at least one auxiliary key."
    if not row.get("enabled", True):
        return "Row disabled."
    return None


def ghost_row_title(row: Any, position: int = 1) -> str:
    """Return the heading shown on a row card."""

    if not isinstance(row, dict):
        return f'Mapping {position}'
    description = _clean_text(row.get("description"))
    if description:
        return description
    control = _clean_text(row.get("control"), limit=64)
    if control:
        return control
    return f'Mapping {position}'


def find_ghost_row(rows: Any, row_id: Any) -> Optional[dict]:
    """Return the row with the given id."""

    if not isinstance(rows, (list, tuple)):
        return None
    wanted = str(row_id or "")
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == wanted:
            return row
    return None


__all__ = [
    "GHOST_MODES",
    "GHOST_MODE_HOLD",
    "GHOST_MODE_LABELS",
    "GHOST_MODE_PULSE",
    "GHOST_SCOPES",
    "GHOST_SCOPE_CAR",
    "GHOST_SCOPE_GLOBAL",
    "GHOST_SCOPE_LABELS",
    "MAX_GHOST_ROWS",
    "MAX_GHOST_TRIGGERS",
    "default_ghost_row",
    "find_ghost_row",
    "ghost_row_is_active",
    "ghost_row_problem",
    "ghost_row_title",
    "merge_ghost_rows",
    "next_ghost_row_id",
    "normalize_ghost_mode",
    "normalize_ghost_scope",
    "normalize_ghost_triggers",
    "sanitize_ghost_row",
    "sanitize_ghost_rows",
    "split_ghost_rows_by_scope",
]
