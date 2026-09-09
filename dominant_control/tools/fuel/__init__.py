"""Fuel Monitor feature package."""


def __getattr__(name: str):
    if name == "FuelMonitorPanel":
        from .panel import FuelMonitorPanel

        return FuelMonitorPanel
    raise AttributeError(name)


__all__ = ["FuelMonitorPanel"]
