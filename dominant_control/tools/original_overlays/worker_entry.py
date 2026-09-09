"""Lazy dispatcher so the main executable only imports the requested toolkit."""

from __future__ import annotations


def run_original_overlay_worker(tool_id: str) -> int:
    tool_id = str(tool_id).strip().lower()
    if tool_id == "caution":
        from .caution_worker import run
    elif tool_id == "fuel":
        from .fuel_worker import run
    elif tool_id == "pit":
        from .pit_worker import run
    elif tool_id == "sr":
        from .sr_worker import run
    elif tool_id == "traction":
        from .traction_worker import run
    else:
        raise ValueError(f'Original overlay not recognized: {tool_id}')
    return int(run())


__all__ = ["run_original_overlay_worker"]
