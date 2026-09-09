"""Lazy package surface so one optional tool cannot break another tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .panel import SafetyRatingPanel


def __getattr__(name: str):
    if name == "SafetyRatingPanel":
        from .panel import SafetyRatingPanel

        return SafetyRatingPanel
    raise AttributeError(name)


__all__ = ["SafetyRatingPanel"]
