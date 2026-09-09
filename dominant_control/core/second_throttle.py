"""Portable second-throttle output driven by a dedicated keyboard binding.

The iRacing control is analog, while a Windows keyboard key is binary.  A
100% request can therefore be represented exactly by holding the configured
key.  Lower requests use time-domain modulation and are intentionally exposed
as approximate by the user interface.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class SecondThrottleSnapshot:
    """Thread-safe state published to the application UI."""

    active: bool
    percentage: int
    modulated: bool
    output_binding: Any


class SecondThrottleEngine:
    """Hold or modulate one game-facing key with fail-safe release semantics."""

    DEFAULT_PERCENTAGE = 100
    BASE_PERIOD_S = 0.05
    MIN_PHASE_S = 0.005
    MAX_PERIOD_S = 0.50

    def __init__(
        self,
        press_input: Callable[[Any], None],
        release_input: Callable[[Any], None],
        *,
        state_callback: Optional[Callable[[SecondThrottleSnapshot], None]] = None,
        error_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._press_input = press_input
        self._release_input = release_input
        self._state_callback = state_callback
        self._error_callback = error_callback
        self._lock = threading.RLock()
        self._active = False
        self._output_down = False
        self._output_binding: Any = None
        self._percentage = self.DEFAULT_PERCENTAGE
        self._generation = 0
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    @staticmethod
    def normalize_percentage(value: Any) -> int:
        """Return an integer percentage in the supported 1..100 range."""

        try:
            number = float(str(value).strip().replace(",", "."))
        except Exception:
            return SecondThrottleEngine.DEFAULT_PERCENTAGE
        if number != number:  # NaN
            return SecondThrottleEngine.DEFAULT_PERCENTAGE
        return max(1, min(100, int(round(number))))

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def percentage(self) -> int:
        with self._lock:
            return self._percentage

    @property
    def output_binding(self) -> Any:
        with self._lock:
            return self._output_binding

    def snapshot(self) -> SecondThrottleSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def configure(self, output_binding: Any, percentage: Any) -> None:
        """Apply settings, preserving an active request when possible."""

        normalized_percentage = self.normalize_percentage(percentage)
        with self._lock:
            unchanged = (
                output_binding == self._output_binding
                and normalized_percentage == self._percentage
            )
            was_active = self._active
        if unchanged:
            self._publish_state()
            return

        if was_active:
            self.deactivate()
        with self._lock:
            self._output_binding = output_binding
            self._percentage = normalized_percentage
        if was_active and output_binding is not None:
            self.activate()
        else:
            self._publish_state()

    def toggle(self) -> bool:
        """Toggle the configured second-throttle output and return its state."""

        if self.active:
            self.deactivate()
            return False
        return self.activate()

    def activate(self) -> bool:
        """Activate continuous or modulated output."""

        with self._lock:
            if self._active:
                return True
            binding = self._output_binding
            percentage = self._percentage
            if binding is None:
                self._report_error(
                    "First set the key sent to iRacing's Second Throttle."
                )
                return False

            self._active = True
            self._output_down = False
            self._generation += 1
            generation = self._generation
            self._stop_event = threading.Event()

        if percentage >= 100:
            try:
                self._press_for_generation(binding, generation)
            except Exception as exc:  # pragma: no cover - platform dependent
                self._fail_generation(generation, binding, exc)
                return False
            self._publish_state()
            return True

        worker = threading.Thread(
            target=self._run_modulation,
            args=(generation, binding, percentage, self._stop_event),
            daemon=True,
            name="SecondThrottlePWM",
        )
        with self._lock:
            if generation != self._generation or not self._active:
                return False
            self._worker = worker
        worker.start()
        self._publish_state()
        return True

    def deactivate(self) -> None:
        """Stop all output and always release a key currently held by the engine."""

        with self._lock:
            binding = self._output_binding
            should_release = self._output_down and binding is not None
            self._active = False
            self._output_down = False
            self._generation += 1
            self._stop_event.set()
            self._worker = None

        if should_release:
            self._safe_release(binding)
        self._publish_state()

    def shutdown(self) -> None:
        """Fail-safe alias used by application lifecycle handlers."""

        self.deactivate()

    def _snapshot_locked(self) -> SecondThrottleSnapshot:
        return SecondThrottleSnapshot(
            active=self._active,
            percentage=self._percentage,
            modulated=self._active and self._percentage < 100,
            output_binding=self._output_binding,
        )

    def _publish_state(self) -> None:
        callback = self._state_callback
        if callback is None:
            return
        try:
            callback(self.snapshot())
        except Exception:
            return

    def _report_error(self, message: str) -> None:
        callback = self._error_callback
        if callback is None:
            return
        try:
            callback(str(message))
        except Exception:
            return

    def _press_for_generation(self, binding: Any, generation: int) -> None:
        with self._lock:
            if generation != self._generation or not self._active:
                return
            self._press_input(binding)
            self._output_down = True

    def _release_for_generation(self, binding: Any, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            should_release = self._output_down
            self._output_down = False
        if should_release:
            self._safe_release(binding)

    @classmethod
    def _modulation_timings(cls, percentage: int) -> tuple[float, float]:
        """Choose reliable key phases while preserving the requested duty cycle."""

        duty = max(0.01, min(0.99, float(percentage) / 100.0))
        shortest_fraction = min(duty, 1.0 - duty)
        period = max(cls.BASE_PERIOD_S, cls.MIN_PHASE_S / shortest_fraction)
        period = min(cls.MAX_PERIOD_S, period)
        return period * duty, period * (1.0 - duty)

    def _run_modulation(
        self,
        generation: int,
        binding: Any,
        percentage: int,
        stop_event: threading.Event,
    ) -> None:
        on_time, off_time = self._modulation_timings(percentage)
        try:
            while not stop_event.is_set():
                self._press_for_generation(binding, generation)
                if stop_event.wait(on_time):
                    break
                self._release_for_generation(binding, generation)
                if stop_event.wait(off_time):
                    break
        except Exception as exc:  # pragma: no cover - platform dependent
            self._fail_generation(generation, binding, exc)
            return
        finally:
            self._release_for_generation(binding, generation)

    def _fail_generation(
        self,
        generation: int,
        binding: Any,
        exc: BaseException,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            should_release = self._output_down
            self._active = False
            self._output_down = False
            self._generation += 1
            self._stop_event.set()
            self._worker = None
        if should_release:
            self._safe_release(binding)
        self._report_error(f'Could not send the second throttle key: {exc}')
        self._publish_state()

    def _safe_release(self, binding: Any) -> None:
        try:
            self._release_input(binding)
        except Exception as exc:  # pragma: no cover - platform dependent
            self._report_error(f'Could not release the second throttle key: {exc}')


__all__ = ["SecondThrottleEngine", "SecondThrottleSnapshot"]
