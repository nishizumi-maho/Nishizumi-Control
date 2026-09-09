"""Tire Wear feature package."""


def __getattr__(name: str):
    if name == "TireWearPanel":
        from .panel import TireWearPanel

        return TireWearPanel
    raise AttributeError(name)


__all__ = ["TireWearPanel"]
