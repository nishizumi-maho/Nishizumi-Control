"""Lazy package surface so one optional tool cannot break another tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .panel import ToolsPanel


def __getattr__(name: str):
    if name == "ToolsPanel":
        from .panel import ToolsPanel

        return ToolsPanel
    raise AttributeError(name)


__all__ = ["ToolsPanel"]
