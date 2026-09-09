#!/usr/bin/env python3
"""Thin entry point for Dominant Control All-in-One."""

from __future__ import annotations

import multiprocessing
import sys
import traceback


def main() -> int:
    multiprocessing.freeze_support()
    if "--original-overlay-worker" in sys.argv:
        index = sys.argv.index("--original-overlay-worker")
        if index + 1 >= len(sys.argv):
            raise SystemExit("The original overlay's name is missing")
        from dominant_control.tools.original_overlays.worker_entry import (
            run_original_overlay_worker,
        )

        return run_original_overlay_worker(sys.argv[index + 1])

    # Normal application imports stay below the worker dispatch.  Original
    # overlay processes therefore load only their renderer instead of first
    # importing the complete Dominant Control Tk application.
    import tkinter as tk
    from tkinter import messagebox

    from dominant_control import iRacingControlApp
    from dominant_control.core import TelemetryHub
    from dominant_control.foundation import set_windows_app_user_model_id

    set_windows_app_user_model_id()
    telemetry = TelemetryHub(update_hz=60.0)
    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        iRacingControlApp(root, telemetry)
        root.deiconify()
        root.mainloop()
        return 0
    except Exception as exc:
        telemetry.stop()
        traceback.print_exc()
        try:
            if root is None:
                root = tk.Tk()
                root.withdraw()
            messagebox.showerror(
                "Dominant Control",
                f'The application could not start:\n\n{type(exc).__name__}: {exc}',
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
