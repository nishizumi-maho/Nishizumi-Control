"""Lazy Profiles export used by the integration coordinator."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .panel import ProfilesPanel


def __getattr__(name: str):
    if name == "ProfilesPanel":
        from .panel import ProfilesPanel

        return ProfilesPanel
    raise AttributeError(name)


__all__ = ["ProfilesPanel"]
