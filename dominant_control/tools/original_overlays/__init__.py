"""Adapters that expose original overlays without duplicating feature engines."""

from .bridge import get_overlay_bridge
from .launcher import OriginalOverlayLauncher

__all__ = ["OriginalOverlayLauncher", "get_overlay_bridge"]

