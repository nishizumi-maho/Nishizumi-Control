"""Read the full control catalogue out of an iRacing ``controls.cfg``.

The app already reads single records from this file to import the keyboard
keys of the scanned ``dc*`` controls.  The Auxiliary Keys tab needs the other
direction: every control the simulator knows about, whether or not the car
exposes it as telemetry, so the user can pick one and hang extra keys on it.

The file is a chunked binary.  A four byte tag, a 32 bit word and a 32 bit
payload size introduce each chunk; the control table lives in the ``LRTC``
chunk ("CTRL" stored little-endian).  Inside it every record is a
NUL-terminated ASCII name, five 32 bit fields (active, mode, input type,
value, modifiers) and a 48 byte trailer that holds the joystick device
identity when the control is bound to a wheel.

This module deliberately has no Tk, Win32 or iRacing SDK dependency: it turns
bytes into records and nothing else.  Translating a virtual-key code into a
binding stays in ``foundation`` where the rest of the key plumbing lives.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from typing import Any, Iterable, List, Optional


CONTROLS_CFG_MAGIC = b"GFCC"
CONTROLS_CFG_TABLE_TAG = b"LRTC"
CONTROLS_CFG_FIELD_BYTES = 20
CONTROLS_CFG_TRAILER_BYTES = 48
CONTROLS_CFG_INPUT_TYPE_UNBOUND = 0
CONTROLS_CFG_INPUT_TYPE_JOYSTICK = 2
CONTROLS_CFG_INPUT_TYPE_KEYBOARD = 4
CONTROLS_CFG_INPUT_TYPE_AXIS = 8

_NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]{2,63}\x00")
_MAX_RECORDS = 4096
# Literal text that lives inside the 48 byte device identity trailer.  It is
# not NUL-terminated, so a pattern scan glues it onto the name of the record
# that follows a wheel-bound control.
_DEVICE_BLOB_MARKERS = ("PIDVID", "DEST", "GUID")


@dataclass(frozen=True)
class ControlRecord:
    """One row of the ``controls.cfg`` control table."""

    name: str
    active: int
    mode: int
    input_type: int
    value: int
    modifiers: int

    @property
    def is_keyboard(self) -> bool:
        return self.input_type == CONTROLS_CFG_INPUT_TYPE_KEYBOARD

    @property
    def is_joystick(self) -> bool:
        return self.input_type == CONTROLS_CFG_INPUT_TYPE_JOYSTICK

    @property
    def is_axis(self) -> bool:
        return self.input_type == CONTROLS_CFG_INPUT_TYPE_AXIS

    @property
    def is_bound(self) -> bool:
        return self.input_type != CONTROLS_CFG_INPUT_TYPE_UNBOUND


def _plausible_record(name: str, fields: tuple) -> bool:
    """Reject regex hits that land inside a device identity blob."""

    if not name or not name[0].isalpha():
        return False
    active, mode, input_type, _value, _modifiers = fields
    return active <= 1 and mode <= 0xFF and input_type <= 0xFF


def _strip_device_blob_prefix(name: str) -> str:
    """Drop device identity text that a pattern scan glued onto a name."""

    for marker in _DEVICE_BLOB_MARKERS:
        if name.startswith(marker) and len(name) > len(marker):
            remainder = name[len(marker):]
            if remainder[0].isupper():
                return remainder
    return name


def _walk_table(data: bytes, start: int, end: int) -> List[ControlRecord]:
    """Walk fixed-stride records, which is exact when the chunk is intact."""

    records: List[ControlRecord] = []
    pos = start
    while pos < end and len(records) < _MAX_RECORDS:
        stop = data.find(b"\x00", pos, end)
        if stop < 0:
            break
        fields_at = stop + 1
        if fields_at + CONTROLS_CFG_FIELD_BYTES > end:
            break
        try:
            name = data[pos:stop].decode("ascii")
        except UnicodeDecodeError:
            return []
        fields = struct.unpack_from("<5I", data, fields_at)
        if not _plausible_record(name, fields):
            # The stride derailed; the caller falls back to scanning.
            return []
        records.append(
            ControlRecord(
                name=name,
                active=fields[0],
                mode=fields[1],
                input_type=fields[2],
                value=fields[3],
                modifiers=fields[4],
            )
        )
        pos = fields_at + CONTROLS_CFG_FIELD_BYTES + CONTROLS_CFG_TRAILER_BYTES
    return records


def _scan_table(data: bytes) -> List[ControlRecord]:
    """Locate records by pattern when the chunk header cannot be trusted.

    A device identity blob can glue its trailing text onto the next control
    name, so a leading device marker is trimmed off before the record is
    accepted.
    """

    records: List[ControlRecord] = []
    seen: set[str] = set()
    for match in _NAME_RE.finditer(data):
        fields_at = match.end()
        if fields_at + CONTROLS_CFG_FIELD_BYTES > len(data):
            continue
        name = _strip_device_blob_prefix(match.group()[:-1].decode("ascii"))
        fields = struct.unpack_from("<5I", data, fields_at)
        if not _plausible_record(name, fields):
            continue
        if name in seen:
            continue
        seen.add(name)
        records.append(
            ControlRecord(
                name=name,
                active=fields[0],
                mode=fields[1],
                input_type=fields[2],
                value=fields[3],
                modifiers=fields[4],
            )
        )
        if len(records) >= _MAX_RECORDS:
            break
    return records


def find_control_table(data: bytes) -> Optional[tuple]:
    """Return ``(start, end)`` of the control table chunk payload."""

    tag_at = data.find(CONTROLS_CFG_TABLE_TAG)
    while tag_at >= 0:
        header_end = tag_at + 12
        if header_end <= len(data):
            size = struct.unpack_from("<I", data, tag_at + 8)[0]
            end = header_end + size
            if 0 < size <= len(data) and end <= len(data):
                return header_end, end
        tag_at = data.find(CONTROLS_CFG_TABLE_TAG, tag_at + 1)
    return None


def parse_controls_catalog(data: Any) -> List[ControlRecord]:
    """Return every control record found in a ``controls.cfg`` payload."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        return []
    blob = bytes(data)
    if not blob:
        return []

    bounds = find_control_table(blob)
    if bounds is not None:
        records = _walk_table(blob, bounds[0], bounds[1])
        if records:
            return records

    return _scan_table(blob)


def catalog_by_name(records: Iterable[ControlRecord]) -> dict:
    """Index records by control name, keeping the first of any duplicates."""

    indexed: dict = {}
    for record in records:
        indexed.setdefault(record.name, record)
    return indexed


__all__ = [
    "CONTROLS_CFG_FIELD_BYTES",
    "CONTROLS_CFG_INPUT_TYPE_AXIS",
    "CONTROLS_CFG_INPUT_TYPE_JOYSTICK",
    "CONTROLS_CFG_INPUT_TYPE_KEYBOARD",
    "CONTROLS_CFG_INPUT_TYPE_UNBOUND",
    "CONTROLS_CFG_MAGIC",
    "CONTROLS_CFG_TABLE_TAG",
    "CONTROLS_CFG_TRAILER_BYTES",
    "ControlRecord",
    "catalog_by_name",
    "find_control_table",
    "parse_controls_catalog",
]
