from __future__ import annotations

from ..foundation import *
from .dialogs import *
from .hud import *

class ScanValidator:
    """Validate that scanned telemetry variables are returning values."""

    def __init__(self, ir_instance, controllers: Dict[str, "GenericController"]):
        self.ir = ir_instance
        self.controllers = controllers
        self.validation_attempts = 0
        self.max_attempts = 3
        self.last_validation_time = 0.0
        self.validation_cooldown = 2.0

    def validate_scan(self) -> Tuple[bool, str]:
        """Return (success, message) for scanned telemetry validation."""

        now = time.time()
        if now - self.last_validation_time < self.validation_cooldown:
            return True, "Validation waiting for its interval"

        self.last_validation_time = now

        if not self.controllers:
            return False, "No control found"

        valid_count = 0
        invalid_controllers = []

        for var_name, controller in self.controllers.items():
            value = controller.read_telemetry()

            if value is not None:
                valid_count += 1
            else:
                invalid_controllers.append(var_name)

        success_rate = valid_count / len(self.controllers)

        if success_rate >= 0.5:
            return True, (
                f'Validation finished ({valid_count}/{len(self.controllers)} working)'
            )

        msg = (
            f'Validation failed ({valid_count}/{len(self.controllers)} working). No answer: {', '.join(invalid_controllers[:3])}'
        )
        return False, msg

    def reset(self) -> None:
        """Reset validation counters."""

        self.validation_attempts = 0


__all__ = ['ScanValidator']
