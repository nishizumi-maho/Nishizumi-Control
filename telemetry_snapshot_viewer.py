#!/usr/bin/env python3
"""Snapshot viewer for all readable iRacing telemetry variables."""

from __future__ import annotations

import numbers
import sys
from typing import Iterable, List, Sequence, Tuple

import irsdk


def collect_var_names(ir: irsdk.IRSDK) -> List[str]:
    var_names: Iterable[str] = []
    if hasattr(ir, "var_headers_dict") and ir.var_headers_dict:
        var_names = ir.var_headers_dict.keys()
    elif hasattr(ir, "var_headers_names"):
        names = getattr(ir, "var_headers_names", None)
        if names:
            var_names = names
    return sorted(set(var_names))


def format_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, numbers.Real):
        return f"{value:.3f}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    return str(value)


def read_snapshot(ir: irsdk.IRSDK, names: Sequence[str]) -> List[Tuple[str, object]]:
    rows: List[Tuple[str, object]] = []
    for name in names:
        try:
            value = ir[name]
        except Exception:
            continue
        rows.append((name, value))
    return rows


def render_snapshot(rows: Sequence[Tuple[str, object]]) -> str:
    lines = ["iRacing telemetry snapshot", "-" * 72]
    for name, value in rows:
        lines.append(f"{name:32} {format_value(value)}")
    return "\n".join(lines)


def main() -> int:
    ir = irsdk.IRSDK()
    if not ir.startup():
        print("Unable to connect to iRacing telemetry.")
        print("Open iRacing, join a session, and click Drive.")
        return 1

    var_names = collect_var_names(ir)
    if not var_names:
        print("No telemetry variables found.")
        return 1

    rows = read_snapshot(ir, var_names)
    print(render_snapshot(rows))
    print(f"\nVariables read: {len(rows)}")

    try:
        input("\nPress Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        try:
            ir.shutdown()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
