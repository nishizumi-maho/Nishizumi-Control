"""Dominant Control package with a deliberately lightweight import surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import iRacingControlApp


def __getattr__(name: str):
    if name == "iRacingControlApp":
        from .app import iRacingControlApp

        return iRacingControlApp
    raise AttributeError(name)


__all__ = ["iRacingControlApp"]
