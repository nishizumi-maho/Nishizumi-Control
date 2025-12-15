"""Shared controller utilities for Dominant Control."""

from typing import Callable, Optional
import time

from dominant_control.input_engine import _direct_pulse, click_pulse
from dominant_control.tts import speak_text


class GenericController:
    """
    Controller for adjusting a single telemetry variable via key presses.
    """

    def __init__(
        self,
        ir_instance,
        var_name: str,
        is_float: bool = False,
        status_callback: Optional[Callable[[str, str], None]] = None,
        app_ref=None,
        *,
        click_handler: Optional[Callable[[Optional[int], bool], None]] = None,
        direct_pulse_handler: Optional[Callable[[Optional[int], int, int], None]] = None,
        speak_handler: Optional[Callable[[str], None]] = None,
    ):
        self.ir = ir_instance
        self.var_name = var_name
        self.is_float = is_float
        self.running_action = False
        self.key_increase = None
        self.key_decrease = None
        self.update_status = status_callback
        self.app = app_ref
        self._click_pulse = click_handler or click_pulse
        self._direct_pulse = direct_pulse_handler or _direct_pulse
        self._speak_text = speak_handler or speak_text

    def read_telemetry(self) -> Optional[float]:
        """
        Read current value of the controlled variable.

        Returns:
            Current value or None if unavailable
        """
        try:
            if not getattr(self.ir, "is_initialized", False):
                try:
                    self.ir.startup()
                except Exception:
                    return None

            value = self.ir[self.var_name]
            if value is None:
                return None

            if self.is_float:
                return float(value)
            else:
                return int(round(value))
        except Exception:
            return None

    def _detect_float_step(self) -> Optional[float]:
        """Detect the minimal float increment by pulsing once and restoring."""
        if not self.is_float:
            return None
        if not self.key_increase or not self.key_decrease:
            return None

        baseline = self.read_telemetry()
        if baseline is None:
            return None

        # Pulse upward and measure the delta
        self._click_pulse(self.key_increase, is_float=True)
        time.sleep(0.08)
        raised = self.read_telemetry()

        if raised is None:
            return None

        step = abs(float(raised) - float(baseline))

        # Try to return near the starting point
        self._click_pulse(self.key_decrease, is_float=True)
        time.sleep(0.08)

        if step < 1e-6:
            return None

        return step

    def _resolve_target(self, target: float) -> float:
        """Align float targets to the nearest reachable increment when needed."""
        if not self.is_float:
            return target

        step = self._detect_float_step()
        current = self.read_telemetry()

        if step is None or step <= 0 or current is None:
            return target

        aligned = current + round((target - current) / step) * step

        if abs(aligned - target) >= 0.0005:
            if self.update_status:
                self.update_status(f"Rounded to {aligned:.3f}", "orange")
            if self.app:
                short_name = self.var_name.replace("dc", "")
                self.app.notify_overlay_status(
                    f"{short_name}: using {aligned:.3f} (nearest)",
                    "orange",
                )

        return aligned

    def adjust_to_target(self, target: float):
        """
        Adjust variable to target value using discrete key presses.

        Args:
            target: Target value to reach
        """
        if self.running_action:
            return

        if not self.key_increase or not self.key_decrease:
            if self.update_status:
                self.update_status("No keys configured", "red")
            if self.app:
                self.app.notify_overlay_status(
                    f"{self.var_name.replace('dc', '')}: No keys",
                    "red",
                )
            return

        self.running_action = True
        short_name = self.var_name.replace("dc", "")

        target = self._resolve_target(target)
        if not self.is_float:
            target = int(round(target))

        if self.update_status:
            self.update_status("Adjusting...", "orange")
        if self.app:
            self.app.notify_overlay_status(
                f"Adjusting {short_name} -> {target}",
                "orange",
            )

        timeout = time.time() + 8
        success = False

        try:
            while time.time() < timeout:
                # Abort if entering CONFIG mode
                if self.app and self.app.app_state != "RUNNING":
                    break

                current = self.read_telemetry()
                if current is None:
                    break

                diff = target - current
                abs_diff = abs(diff)

                # Check if target reached
                if self.is_float and abs_diff < 0.001:
                    success = True
                    break
                if not self.is_float and diff == 0:
                    success = True
                    break

                # Press appropriate key
                key = self.key_increase if diff > 0 else self.key_decrease
                self._click_pulse(key, self.is_float)
                time.sleep(0.02)

        except Exception as e:
            print(f"[GenericController] Exception: {e}")
        finally:
            if success:
                message = f"{short_name} OK ({target})"
                if self.update_status:
                    self.update_status("Ready", "green")
                if self.app:
                    self.app.notify_overlay_status(message, "green")
                    if self.app.use_tts.get():
                        self._speak_text(message)
            else:
                status = "Cancelled" if (
                    self.app and self.app.app_state != "RUNNING"
                ) else "Failed"

                if self.update_status:
                    self.update_status(status, "red")
                if self.app:
                    status_msg = (
                        f"{short_name} Cancelled"
                        if self.app.app_state != "RUNNING"
                        else f"{short_name} Failed"
                    )
                    self.app.notify_overlay_status(status_msg, "red")

            self.running_action = False

    def find_minimum_effective_timing(
        self,
        start_ms: int = 1,
        max_ms: int = 120,
        step_ms: int = 1,
        settle_s: float = 0.05,
        confirmation_attempts: int = 2,
    ) -> Optional[int]:
        """
        Probe the minimal pulse timing that reliably updates telemetry.

        The probe fires fast pulses starting at ``start_ms`` and increments by
        ``step_ms`` until telemetry reflects a change. The first timing that
        consistently registers is returned.

        Args:
            start_ms: Initial press/interval duration in milliseconds.
            max_ms: Maximum duration to test in milliseconds.
            step_ms: Increment between attempts in milliseconds.
            settle_s: Delay after a pulse to allow telemetry to settle.
            confirmation_attempts: Number of retries per timing bucket.

        Returns:
            Suggested minimal working pulse duration in milliseconds, or None
            if no timing within bounds registers.
        """
        if not self.key_increase or not self.key_decrease:
            raise ValueError("Increase/decrease keys must be configured before probing.")

        baseline = self.read_telemetry()
        if baseline is None:
            return None

        def _changed(old, new) -> bool:
            if old is None or new is None:
                return False
            if self.is_float:
                return abs(float(new) - float(old)) >= 0.0005
            return int(round(new)) != int(round(old))

        def _restore(target_value: float, timing_ms: int):
            """Attempt to revert telemetry back near baseline after a test."""
            for _ in range(5):
                current = self.read_telemetry()
                if current is None:
                    break
                if not _changed(target_value, current):
                    break
                direction = self.key_decrease if current > target_value else self.key_increase
                self._direct_pulse(direction, timing_ms, timing_ms)
                time.sleep(settle_s)

        for delay_ms in range(max(1, start_ms), max_ms + 1, max(1, step_ms)):
            success_count = 0
            for _ in range(max(1, confirmation_attempts)):
                self._direct_pulse(self.key_increase, delay_ms, delay_ms)
                time.sleep(settle_s)
                updated = self.read_telemetry()
                if _changed(baseline, updated):
                    success_count += 1
                else:
                    break

            _restore(baseline, delay_ms)

            if success_count >= confirmation_attempts:
                return delay_ms

        return None
