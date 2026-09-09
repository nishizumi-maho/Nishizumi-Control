"""
Thin wrapper over pyirsdk.

Handles connection state and defensive reads of keys that may not exist on
older builds of the simulator.
"""

from __future__ import annotations

import logging
from typing import Any

import irsdk

logger = logging.getLogger("sr_estimator.sdk")


class IRacingSDK:
    """Connection to iRacing through shared memory (irsdk.IRSDK)."""

    def __init__(self) -> None:
        self._ir = irsdk.IRSDK()
        self._was_connected = False

    @property
    def raw(self) -> irsdk.IRSDK:
        return self._ir

    def poll(self) -> bool:
        """Call once per tick. True when a live iRacing session is available."""
        if not self._ir.is_initialized:
            try:
                self._ir.startup()
            except Exception as exc:  # the lib can raise if the sim is not running
                logger.debug("startup() failed: %s", exc)

        is_connected = bool(getattr(self._ir, "is_initialized", False) and self._ir.is_connected)

        if is_connected and not self._was_connected:
            logger.info("Connected to iRacing")
        elif not is_connected and self._was_connected:
            logger.info("Disconnected from iRacing")
            try:
                self._ir.shutdown()
            except Exception as exc:
                logger.debug("shutdown() failed: %s", exc)

        self._was_connected = is_connected
        return is_connected

    def get(self, key: str, default: Any = None) -> Any:
        """Defensive read of a telemetry key or a session-YAML key."""
        if not getattr(self._ir, "is_initialized", False):
            return default
        try:
            value = self._ir[key]
        except Exception as exc:
            logger.debug("Could not read '%s': %s", key, exc)
            return default
        return default if value is None else value

    def shutdown(self) -> None:
        try:
            self._ir.shutdown()
        except Exception:
            pass
