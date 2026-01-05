"""Watchdog utilities for monitoring background threads."""

import threading
import time
from typing import Callable, Optional


class Watchdog:
    """Simple watchdog to monitor heartbeats and run recovery callbacks."""

    def __init__(
        self,
        name: str,
        *,
        interval_s: float = 2.0,
        timeout_s: float = 6.0,
        on_trip: Optional[Callable[[], None]] = None,
    ):
        self.name = name
        self.interval_s = max(0.5, interval_s)
        self.timeout_s = max(self.interval_s, timeout_s)
        self.on_trip = on_trip
        self._last_heartbeat = time.time()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def beat(self):
        """Record a heartbeat from the monitored worker."""

        self._last_heartbeat = time.time()

    def start(self):
        """Start the watchdog monitor thread."""

        if self._thread and self._thread.is_alive():
            return

        if self._stop_event.is_set():
            self._stop_event = threading.Event()

        self._last_heartbeat = time.time()
        self._thread = threading.Thread(
            target=self._run, name=f"{self.name}-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop monitoring."""

        self._stop_event.set()

    def _run(self):
        while not self._stop_event.wait(self.interval_s):
            elapsed = time.time() - self._last_heartbeat
            if elapsed <= self.timeout_s:
                continue

            try:
                if self.on_trip:
                    self.on_trip()
            except Exception as exc:  # noqa: PERF203
                print(f"[Watchdog:{self.name}] Recovery failed: {exc}")

            self._last_heartbeat = time.time()


__all__ = ["Watchdog"]
