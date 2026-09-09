"""Lazy package surface so one optional tool cannot break another tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .panel import CautionOverlayPanel


def __getattr__(name: str):
    if name == "CautionOverlayPanel":
        from .panel import CautionOverlayPanel

        return CautionOverlayPanel
    raise AttributeError(name)


__all__ = ["CautionOverlayPanel"]
