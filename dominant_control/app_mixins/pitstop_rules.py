"""Keep the HUD's pitstop regulations in step with the session.

iRacing publishes the active ruleset in ``WeekendInfo:AltAssetTag``, and what
that ruleset means depends on the car: ``core/pitstop_rules.py`` answers that
question, and this loop is what asks it — reading the session every couple of
seconds and pushing the two lines to the overlay only when they change.
"""

from __future__ import annotations

from ..core import pitstop_rules


PITSTOP_RULES_INTERVAL_MS = 2500


class PitstopRulesMixin:
    """The active pitstop regulations, on the driver HUD."""

    def _read_pitstop_rules_identity(self) -> tuple[str, tuple[str, ...]]:
        """Return the session's ruleset tag and how the car identifies itself."""

        tag = ""
        driver_info: dict = {}
        try:
            with self.ir_lock:
                weekend = self.ir["WeekendInfo"] or {}
                driver_info = self.ir["DriverInfo"] or {}
            tag = str(weekend.get("AltAssetTag") or "")
        except Exception:
            driver_info = {}

        identity: list[str] = []
        try:
            index = driver_info.get("DriverCarIdx")
            drivers = driver_info.get("Drivers") or []
            if index is not None and 0 <= int(index) < len(drivers):
                driver = drivers[int(index)] or {}
                identity = [
                    str(driver.get("CarPath") or ""),
                    str(driver.get("CarScreenName") or ""),
                    str(driver.get("CarClassShortName") or ""),
                ]
        except Exception:
            identity = []

        if not any(part.strip() for part in identity):
            identity = [str(getattr(self, "current_car", "") or "")]
        return tag, tuple(identity)

    def refresh_pitstop_rules(self) -> None:
        """Read the rules once and hand them to the HUD."""

        overlay = getattr(self, "overlay", None)
        if overlay is None:
            return
        tag, identity = self._read_pitstop_rules_identity()
        if not any(part.strip() for part in identity):
            overlay.update_pitstop_rules("", "")
            return
        rules = pitstop_rules.describe(tag, *identity)
        overlay.update_pitstop_rules(
            pitstop_rules.headline(rules),
            pitstop_rules.detail(rules),
        )

    def _pitstop_rules_loop(self) -> None:
        try:
            self.refresh_pitstop_rules()
        except Exception as exc:
            print(f'[Pit rules] Could not read the ruleset: {exc}')
        finally:
            self.root.after(PITSTOP_RULES_INTERVAL_MS, self._pitstop_rules_loop)


__all__ = ["PITSTOP_RULES_INTERVAL_MS", "PitstopRulesMixin"]
