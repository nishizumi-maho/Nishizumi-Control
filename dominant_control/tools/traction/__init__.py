"""Traction Coach feature package."""


def __getattr__(name: str):
    if name == "TractionPanel":
        from .panel import TractionPanel

        return TractionPanel
    raise AttributeError(name)


__all__ = ["TractionPanel"]
