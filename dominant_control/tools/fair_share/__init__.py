"""Lazy export for the Fair Share Calculator panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .panel import FairSharePanel


def __getattr__(name: str):
    if name == "FairSharePanel":
        from .panel import FairSharePanel

        return FairSharePanel
    raise AttributeError(name)


__all__ = ["FairSharePanel"]
