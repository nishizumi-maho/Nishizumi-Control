"""Pit Calibrator feature package."""


def __getattr__(name: str):
    if name == "PitCalibratorPanel":
        from .panel import PitCalibratorPanel

        return PitCalibratorPanel
    raise AttributeError(name)


__all__ = ["PitCalibratorPanel"]
