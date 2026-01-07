import numbers
import sys
import time
from typing import List, Tuple

import irsdk

BASE_CANDIDATES = [
    "dcBrakeBias",
    "dcFuelMixture",
    "dcTractionControl",
    "dcTractionControl2",
    "dcABS",
    "dcAntiRollFront",
    "dcAntiRollRear",
    "dcWeightJackerRight",
    "dcDiffEntry",
    "dcDiffExit",
]


def collect_dc_vars(ir: irsdk.IRSDK) -> List[Tuple[str, bool]]:
    candidates = set(BASE_CANDIDATES)

    try:
        if hasattr(ir, "var_headers_dict") and ir.var_headers_dict:
            for key in ir.var_headers_dict.keys():
                if key.startswith("dc"):
                    candidates.add(key)
        elif hasattr(ir, "var_headers_names"):
            names = getattr(ir, "var_headers_names", None)
            if names:
                for key in names:
                    if key.startswith("dc"):
                        candidates.add(key)
    except Exception:
        pass

    found_vars: List[Tuple[str, bool]] = []
    for candidate in sorted(candidates):
        try:
            value = ir[candidate]
        except Exception:
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if not isinstance(value, numbers.Real):
            continue
        found_vars.append((candidate, True))

    return found_vars


def format_value(value: object, is_float: bool) -> str:
    if value is None:
        return "-"
    if is_float:
        try:
            return f"{float(value):.3f}"
        except Exception:
            return "-"
    return str(value)


def main() -> int:
    ir = irsdk.IRSDK()
    if not ir.startup():
        print("Unable to connect to iRacing telemetry.")
        print("Open iRacing, join a session, and click Drive.")
        return 1

    dc_vars = collect_dc_vars(ir)
    if not dc_vars:
        print("No numeric dc* controls found.")
        print("Make sure you're in the car and driver controls are available.")
        return 1

    print("Connected. Streaming dc* controls (Ctrl+C to stop)...")
    time.sleep(0.5)

    try:
        while True:
            if not getattr(ir, "is_initialized", False):
                if not ir.startup():
                    time.sleep(0.5)
                    continue
            lines = []
            for name, is_float in dc_vars:
                try:
                    value = ir[name]
                except Exception:
                    value = None
                lines.append(f"{name:24} {format_value(value, is_float)}")

            timestamp = time.strftime("%H:%M:%S")
            print("\033[2J\033[H", end="")
            print(f"iRacing dc controls @ {timestamp} (count: {len(dc_vars)})")
            print("-" * 48)
            print("\n".join(lines))
            sys.stdout.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping telemetry stream.")
    finally:
        try:
            ir.shutdown()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
