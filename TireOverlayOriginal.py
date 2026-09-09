#!/usr/bin/env python3
"""Dedicated PyQt5 entry point for the original Tire Wear overlay."""

from __future__ import annotations

import multiprocessing

from dominant_control.tools.original_overlays.tire_worker import run


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(run())

