from __future__ import annotations

from ..foundation import *
from .dialogs import *
from .hud import *

class GenericController:
    """
    Controller for adjusting a single telemetry variable via key presses.
    """

    def __init__(
        self,
        ir_instance,
        var_name: str,
        is_float: bool = False,
        is_boolean: bool = False,
        allow_dual_keys: bool = False,
        status_callback: Optional[Callable[[str, str], None]] = None,
        app_ref=None
    ):
        self.ir = ir_instance
        self.var_name = var_name
        self.is_float = is_float
        self.is_boolean = is_boolean
        self.allow_dual_keys = allow_dual_keys or not is_boolean
        self.running_action = False
        self.key_increase = None
        self.key_decrease = None
        self.update_status = status_callback
        self.app = app_ref
        self._target_lock = threading.Lock()
        self._requested_target: Optional[float] = None
        self._requested_nonce = 0
        self._next_nonce = 0
        self._requested_allow_caution = False
        self._requested_allow_pace = False
        self._requested_allow_pit_service = False
        self._requested_precision_persistent = False
        self._precision_last_confirmed_target: Optional[float] = None
        self._precision_last_confirmed_nonce = 0
        self._clear_requested = False
        self._requested_direct_boundary = False
        self._worker_thread: Optional[threading.Thread] = None
        self._float_step: Optional[float] = None
        self.min_value: Optional[float] = None
        self.max_value: Optional[float] = None
        if self.var_name in WEIGHT_JACKER_VARS:
            self.min_value = -20.0
            self.max_value = 20.0
        self.boolean_pulse_only = False
        self.boolean_pulse_double = False
        self._momentary_armed = True
        self._momentary_last_request = 0.0
        self._momentary_release_timeout_s = 1.2

    def set_bounds(
        self,
        min_value: Optional[float],
        max_value: Optional[float]
    ) -> None:
        """Set optional reachable min/max limits for this control."""
        if not self.is_float:
            min_value = (
                None if min_value is None else int(round(float(min_value)))
            )
            max_value = (
                None if max_value is None else int(round(float(max_value)))
            )
        if min_value is not None and max_value is not None and max_value < min_value:
            min_value, max_value = max_value, min_value
        self.min_value = min_value
        self.max_value = max_value

    def configured_bounds(self) -> Tuple[Optional[float], Optional[float]]:
        """Return configured bounds in normalized min/max order."""
        min_value = self.min_value
        max_value = self.max_value
        if min_value is not None and max_value is not None and max_value < min_value:
            return max_value, min_value
        return min_value, max_value

    def can_discover_bounds(self) -> bool:
        """Return True when min/max discovery can be attempted safely."""
        return (
            bool(self.key_increase)
            and bool(self.key_decrease)
            and not _unsafe_system_binding_reason(self.key_increase)
            and not _unsafe_system_binding_reason(self.key_decrease)
            and not self.is_boolean
            and not self._is_momentary_pulse()
        )

    def _is_momentary_pulse(self) -> bool:
        """Return True for driver controls that should be a one-shot pulse."""
        return _is_one_shot_control_name(self.var_name, self.is_boolean)

    def _should_trigger_momentary(self, target: float) -> bool:
        """Debounce momentary pulses to avoid repeated firing loops."""
        now = time.time()
        if target <= 0:
            self._momentary_armed = True
            self._momentary_last_request = now
            return False

        if (
            self._momentary_last_request > 0.0
            and now - self._momentary_last_request > self._momentary_release_timeout_s
        ):
            self._momentary_armed = True

        self._momentary_last_request = now
        if not self._momentary_armed:
            return False

        self._momentary_armed = False
        return True

    def read_telemetry(
        self,
        use_cache: bool = True,
        cache_ttl_s: Optional[float] = None
    ) -> Optional[float]:
        """
        Read current value of the controlled variable.

        Args:
            use_cache: If True, return cached value when available.
            cache_ttl_s: Cache time-to-live in seconds (default 0.05).

        Returns:
            Current value or None if unavailable
        """
        if self.var_name == "dcPushToPass" and self.app:
            status = self.app._read_push_to_pass_status()
            if status is not None:
                result = 1 if status else 0
                _TELEMETRY_CACHE.set(self.var_name, result)
                return result
        if self.var_name == "dcPitSpeedLimiterToggle" and self.app:
            warnings = self.app._read_ir_value(
                "EngineWarnings",
                use_cache=use_cache,
            )
            status = _pit_limiter_state_from_engine_warnings(warnings)
            if status is not None:
                result = 1 if status else 0
                _TELEMETRY_CACHE.set(self.var_name, result)
                return result
        if self.var_name == "dcDRSToggle" and self.app:
            value = self.app._read_ir_value(
                "DRS_Status",
                use_cache=use_cache,
            )
            status = _drs_active_state_from_status(value)
            if status is not None:
                result = 1 if status else 0
                _TELEMETRY_CACHE.set(self.var_name, result)
                return result
        hybrid_status_field = HYBRID_HOLD_STATUS_FIELDS.get(self.var_name)
        if hybrid_status_field and self.app:
            value = self.app._read_ir_value(
                hybrid_status_field,
                use_cache=use_cache,
            )
            status = _scalar_boolean_state(value)
            if status is not None:
                result = 1 if status else 0
                _TELEMETRY_CACHE.set(self.var_name, result)
                return result
        # Check circuit breaker first
        if not _TELEMETRY_CIRCUIT_BREAKER.can_execute(self.var_name):
            # Circuit is open - skip read to avoid hammering broken variable
            hit, cached = _TELEMETRY_CACHE.get(self.var_name, ttl_s=2.0)
            if hit:
                return cached
            return None

        # Check cache for recent value
        if use_cache:
            hit, cached = _TELEMETRY_CACHE.get(self.var_name, ttl_s=cache_ttl_s)
            if hit and cached is not None:
                return cached

        on_ui_thread = threading.current_thread() is threading.main_thread()

        try:
            if not getattr(self.ir, "is_initialized", False):
                if self.app:
                    self.app._start_sdk_warmup()
                if on_ui_thread:
                    return None
                try:
                    self.ir.startup()
                    time.sleep(0.1)
                except Exception:
                    _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                    return None

            if self.app:
                self.app._repair_sdk_header_cache_if_needed()

            max_retries = 1 if on_ui_thread else 4
            base_delay = 0.03
            for attempt in range(max_retries):
                try:
                    value = self.ir[self.var_name]
                    if value is None:
                        if on_ui_thread:
                            return None
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            time.sleep(min(delay, 0.25))
                            continue
                        if self.app:
                            self.app._note_none_telemetry(self.var_name)
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                        return None

                    # Success - record and cache
                    _TELEMETRY_CIRCUIT_BREAKER.record_success(self.var_name)
                    if self.app:
                        self.app._clear_none_telemetry(self.var_name)

                    if self.is_float:
                        result = float(value)
                    else:
                        result = int(round(value))

                    _TELEMETRY_CACHE.set(self.var_name, result)
                    return result

                except Exception as e:
                    if on_ui_thread:
                        if self.app:
                            self.app._start_sdk_warmup()
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                        return None
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        try:
                            # A shared handle belongs to the telemetry owner.
                            # Restarting it from here took the telemetry away
                            # from the entire application: a single control that
                            # failed to read during a session change wiped the
                            # cached session string, and the reconnection that
                            # followed left the app showing the previous car and
                            # track until it was restarted. Backing off lets the
                            # owner recover for everyone.
                            if getattr(self.ir, "shared_owner", False):
                                time.sleep(min(delay, 0.25))
                            else:
                                if hasattr(self.ir, "shutdown"):
                                    self.ir.shutdown()
                                time.sleep(min(delay, 0.25))
                                self.ir.startup()
                                time.sleep(0.1)
                        except Exception:
                            pass
                    else:
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                        return None

        except Exception:
            _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
            return None

        return None

    def _resolve_target(self, target: float) -> float:
        """Align float targets to the nearest reachable increment when needed."""
        if not self.is_float:
            return target

        step = self._float_step
        current = self.read_telemetry()

        if step is None or step <= 0 or current is None:
            return target

        aligned = current + round((target - current) / step) * step

        if abs(aligned - target) >= 0.0005:
            if self.update_status:
                self.update_status(f'Rounded to {aligned:.3f}', "orange")
            if self.app:
                short_name = compact_driver_control_name(self.var_name)
                self.app.notify_overlay_status(
                    f'{short_name}: using {aligned:.3f} (closest)',
                    "orange"
                )

        return aligned

    def _apply_bounds(self, target: float) -> Tuple[float, bool]:
        """Clamp target to configured bounds and flag boundary requests."""
        min_value = self.min_value
        max_value = self.max_value
        if min_value is not None and max_value is not None and max_value < min_value:
            min_value, max_value = max_value, min_value

        if min_value is not None and target <= min_value:
            return min_value, True
        if max_value is not None and target >= max_value:
            return max_value, True
        return target, False

    def _read_stable_int(
        self,
        initial_value: int,
        samples: int = 3,
        delay: float = 0.01
    ) -> int:
        """Read multiple integer samples to reduce jitter near targets."""
        if samples <= 1:
            return initial_value

        values = [initial_value]
        for _ in range(samples - 1):
            time.sleep(delay)
            read_value = self.read_telemetry()
            if read_value is None:
                continue
            values.append(int(round(read_value)))

        values.sort()
        return values[len(values) // 2]

    def _read_stable_float(
        self,
        initial_value: float,
        samples: int = 3,
        delay: float = 0.01
    ) -> float:
        """Read multiple float samples to reduce jitter near targets."""
        if samples <= 1:
            return initial_value

        values = [float(initial_value)]
        for _ in range(samples - 1):
            time.sleep(delay)
            read_value = self.read_telemetry()
            if read_value is None:
                continue
            values.append(float(read_value))

        values.sort()
        return values[len(values) // 2]

    def _read_verified_value(
        self,
        samples: int = 3,
        delay: float = 0.012
    ) -> Optional[float]:
        """Read fresh telemetry samples and return the median value."""
        values: List[float] = []
        for idx in range(max(1, samples)):
            read_value = self.read_telemetry(use_cache=False)
            if read_value is not None:
                if self.is_float:
                    values.append(float(read_value))
                else:
                    values.append(float(int(round(read_value))))
            if idx < samples - 1:
                time.sleep(delay)

        if not values:
            return None

        values.sort()
        value = values[len(values) // 2]
        if self.is_float:
            return value
        return int(round(value))

    def _target_tolerance(self) -> float:
        """Return tolerance used for target verification."""
        if not self.is_float:
            return 0.0
        tolerance = 0.001
        if self._float_step and self._float_step > 0:
            tolerance = max(tolerance, self._float_step / 2.0)
        return tolerance

    def _target_reached(self, current: Optional[float], target: float) -> bool:
        """Return True when a telemetry value matches the requested target."""
        if current is None:
            return False
        if self.is_float:
            return abs(float(target) - float(current)) <= self._target_tolerance()
        return int(round(float(current))) == int(round(float(target)))

    def _target_request_changed(self, nonce: int) -> bool:
        """Return True if the active target was cleared or replaced."""
        with self._target_lock:
            return self._clear_requested or self._requested_nonce != nonce

    def _bot_correction_can_continue(
        self,
        nonce: int,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> bool:
        """Return False when the correction loop should yield immediately."""
        if self._target_request_changed(nonce):
            return False
        if self.app and self.app.app_state != "RUNNING":
            return False
        if self.app and not self.app._commands_allowed(
            allow_caution=allow_caution,
            allow_pace=allow_pace,
            allow_pit_service=allow_pit_service
        ):
            return False
        return True

    def _bot_correction_sleep(
        self,
        nonce: int,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> bool:
        """Wait 18 ms for telemetry while allowing newer macros to win."""
        deadline = time.perf_counter() + BOT_CORRECTION_SETTLE_S
        while True:
            if not self._bot_correction_can_continue(
                nonce,
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            ):
                return False
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(0.003, remaining))

    def _start_target_worker_if_needed(self) -> None:
        """Ensure a queued target has a worker thread to process it."""
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._run_target_loop,
                daemon=True
            )
            self._worker_thread.start()

    def _estimate_pulses_remaining(self, diff: float) -> Optional[int]:
        """Estimate how many key pulses remain to close the diff."""
        if self.is_float:
            step = self._float_step if self._float_step else 0.001
            if step <= 0:
                return None
            return max(0, int(math.ceil(abs(diff) / step)))
        return max(0, int(abs(round(diff))))

    def _trigger_boolean(self, target: float) -> None:
        """Trigger a boolean control once without retry loops."""
        if self.running_action:
            return
        if not self.key_increase:
            if self.update_status:
                self.update_status("No key set", "red")
            if self.app:
                self.app.notify_overlay_status(
                    f'{compact_driver_control_name(self.var_name)}: no key',
                    "red"
                )
            return

        if not self.boolean_pulse_only:
            desired_state = 1 if target > 0 else 0
            current = self.read_telemetry()
            if current is None and desired_state == 0:
                return
            if current is not None:
                current_state = 1 if current > 0 else 0
                if current_state == desired_state:
                    if self.update_status:
                        state_text = "ON" if desired_state else "OFF"
                        self.update_status(f'Already at {state_text}', "gray")
                    return

        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        try:
            click_pulse(self.key_increase, is_float=False)
            if self.boolean_pulse_only and self.boolean_pulse_double:
                time.sleep(0.08)
                click_pulse(self.key_increase, is_float=False)
            if self.update_status:
                self.update_status("Triggered", "green")
            if self.app:
                message = f'{short_name} triggered'
                self.app.notify_overlay_status(message, "green")
                if self.app.use_tts.get():
                    speak_text(message)
        finally:
            self.running_action = False

    def _trigger_momentary_pulse(self) -> None:
        """Send a one-shot pulse without consulting telemetry."""
        if self.running_action:
            return
        if not self.key_increase:
            if self.update_status:
                self.update_status("No key set", "red")
            if self.app:
                self.app.notify_overlay_status(
                    f'{compact_driver_control_name(self.var_name)}: no key',
                    "red"
                )
            return

        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        try:
            click_pulse(self.key_increase, is_float=False)
            if self.boolean_pulse_double:
                time.sleep(0.08)
                click_pulse(self.key_increase, is_float=False)
            if self.update_status:
                self.update_status("Triggered", "green")
            if self.app:
                message = f'{short_name} triggered'
                self.app.notify_overlay_status(message, "green")
                if self.app.use_tts.get():
                    speak_text(message)
        finally:
            self.running_action = False

    def trigger_pulse(self) -> None:
        """Force a one-shot pulse regardless of telemetry."""
        if self.app and not self.app._commands_allowed():
            return
        self._trigger_momentary_pulse()

    def request_target(self, target: float):
        """Queue a target adjustment request, overriding any active target."""
        self.request_target_with_context(target)

    def request_target_with_context(
        self,
        target: float,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False,
        precision_persistent: bool = False
    ):
        """Queue a target request with optional context-guard exceptions."""
        if (
            not precision_persistent
            and self.app
            and not self.app._commands_allowed(
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            )
        ):
            self.clear_target()
            return
        if self._is_momentary_pulse():
            if self._should_trigger_momentary(target):
                self._trigger_momentary_pulse()
            return
        if self.is_boolean and not self.allow_dual_keys:
            self._trigger_boolean(target)
            return
        target, direct_boundary = self._apply_bounds(target)
        with self._target_lock:
            self._next_nonce += 1
            self._requested_target = target
            self._requested_nonce = self._next_nonce
            self._clear_requested = False
            self._requested_direct_boundary = direct_boundary
            self._requested_allow_caution = bool(allow_caution)
            self._requested_allow_pace = bool(allow_pace)
            self._requested_allow_pit_service = bool(allow_pit_service)
            self._requested_precision_persistent = bool(precision_persistent)
            if precision_persistent:
                self._precision_last_confirmed_target = None
                self._precision_last_confirmed_nonce = 0

        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._run_target_loop,
                daemon=True
            )
            self._worker_thread.start()

    def clear_target(self):
        """Clear any pending target requests and stop adjusting."""
        with self._target_lock:
            self._requested_target = None
            self._clear_requested = True
            self._requested_precision_persistent = False

    def clear_precision_target(self) -> bool:
        """Cancel only a pending precision-profile request."""
        with self._target_lock:
            if (
                not self._requested_precision_persistent
                or self._requested_target is None
            ):
                return False
            self._requested_target = None
            self._clear_requested = True
            self._requested_precision_persistent = False
            return True

    def has_precision_target(self, target: Optional[float] = None) -> bool:
        """Return True while a matching precision target has a live worker."""
        with self._target_lock:
            pending = self._requested_target
            active = (
                self._requested_precision_persistent
                and pending is not None
                and not self._clear_requested
            )
            worker = self._worker_thread
        if not active or worker is None or not worker.is_alive():
            return False
        if target is None:
            return True
        return self._target_reached(pending, target)

    def precision_target_confirmed(self, target: float) -> bool:
        """Return True after the precision worker stably confirmed a target."""
        with self._target_lock:
            confirmed = self._precision_last_confirmed_target
        return self._target_reached(confirmed, target)

    def adjust_to_target(self, target: float):
        """
        Adjust variable to target value using discrete key presses.

        Args:
            target: Target value to reach
        """
        self.request_target(target)

    def _precision_context_state(
        self,
        nonce: int,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> str:
        """Return the current safety state for a persistent profile target."""
        with self._target_lock:
            if self._requested_nonce != nonce:
                return "replaced"
            if self._clear_requested or self._requested_target is None:
                return "cancelled"

        if self.app and self.app.app_state != "RUNNING":
            return "cancelled"

        if self.app and hasattr(self.app, "_player_on_track_car"):
            try:
                in_car = self.app._player_on_track_car()
            except Exception:
                in_car = None
            if in_car is False:
                return "cancelled"
            if in_car is not True:
                return "waiting"

        if self.app and not self.app._commands_allowed(
            allow_caution=allow_caution,
            allow_pace=allow_pace,
            allow_pit_service=allow_pit_service
        ):
            # Replay/context protection is temporary.  Preserve the target and
            # resume as soon as sending commands is safe again.
            return "blocked"
        return "ready"

    def _precision_wait(
        self,
        nonce: int,
        duration_s: float,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> str:
        """Wait interruptibly and return the latest precision context state."""
        deadline = time.perf_counter() + max(0.0, duration_s)
        state = "ready"
        while True:
            state = self._precision_context_state(
                nonce,
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            )
            if state in {"replaced", "cancelled"}:
                return state
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return state
            time.sleep(min(0.05, remaining))

    def _precision_confirm_target(
        self,
        target: float,
        nonce: int,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> str:
        """Require repeated, fully fresh matching samples before succeeding."""
        for _confirmation in range(SURFACE_DC_PRECISION_CONFIRMATIONS):
            for sample_idx in range(SURFACE_DC_PRECISION_CONFIRM_SAMPLES):
                state = self._precision_context_state(
                    nonce,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                if state in {"replaced", "cancelled"}:
                    return state
                if state != "ready":
                    return "retry"

                current = self.read_telemetry(use_cache=False)
                if not self._target_reached(current, target):
                    return "retry"

                if sample_idx < SURFACE_DC_PRECISION_CONFIRM_SAMPLES - 1:
                    state = self._precision_wait(
                        nonce,
                        SURFACE_DC_PRECISION_CONFIRM_DELAY_S,
                        allow_caution=allow_caution,
                        allow_pace=allow_pace,
                        allow_pit_service=allow_pit_service
                    )
                    if state in {"replaced", "cancelled"}:
                        return state
                    if state != "ready":
                        return "retry"

            state = self._precision_wait(
                nonce,
                SURFACE_DC_PRECISION_CONFIRM_DELAY_S,
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            )
            if state in {"replaced", "cancelled"}:
                return state
            if state != "ready":
                return "retry"
        return "success"

    def _run_precision_persistent_target(
        self,
        target: float,
        nonce: int,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> str:
        """Move one profile value slowly until telemetry confirms it."""
        last_value_before_pulse: Optional[float] = None
        last_status_time = 0.0
        last_status_text = ""

        def _report(text: str, color: str = "orange") -> None:
            nonlocal last_status_time, last_status_text
            now = time.time()
            if (
                text != last_status_text
                or now - last_status_time >= SURFACE_DC_PRECISION_STATUS_INTERVAL_S
            ):
                if self.update_status:
                    self.update_status(text, color)
                last_status_text = text
                last_status_time = now

        while True:
            state = self._precision_context_state(
                nonce,
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            )
            if state in {"replaced", "cancelled"}:
                return state
            if state == "blocked":
                _report("Paused safely; trying again...")
                self._precision_wait(
                    nonce,
                    SURFACE_DC_PRECISION_RETRY_S,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                continue
            if state == "waiting":
                _report("Waiting for telemetry; still trying...")
                self._precision_wait(
                    nonce,
                    SURFACE_DC_PRECISION_RETRY_S,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                continue

            current = self._read_verified_value(
                samples=SURFACE_DC_PRECISION_READ_SAMPLES,
                delay=SURFACE_DC_PRECISION_READ_DELAY_S
            )
            if current is None:
                _report("No reading; retrying slowly...")
                wait_state = self._precision_wait(
                    nonce,
                    SURFACE_DC_PRECISION_RETRY_S,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                if wait_state in {"replaced", "cancelled"}:
                    return wait_state
                continue

            if self.is_float and last_value_before_pulse is not None:
                delta = abs(float(current) - float(last_value_before_pulse))
                if delta >= 1e-4:
                    if self._float_step is None or delta < self._float_step:
                        self._float_step = round(delta, 6)
            last_value_before_pulse = None

            if self._target_reached(current, target):
                _report("Confirming the value...", "orange")
                confirmation = self._precision_confirm_target(
                    target,
                    nonce,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                if confirmation == "success":
                    return "success"
                if confirmation in {"replaced", "cancelled"}:
                    return confirmation
                self._precision_wait(
                    nonce,
                    SURFACE_DC_PRECISION_RETRY_S,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                continue

            diff = float(target) - float(current)
            key = self.key_increase if diff > 0 else self.key_decrease
            direction = "+" if diff > 0 else "-"
            if not key:
                _report(f'Waiting for key {direction}; the attempt stands...')
                wait_state = self._precision_wait(
                    nonce,
                    SURFACE_DC_PRECISION_RETRY_S,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                if wait_state in {"replaced", "cancelled"}:
                    return wait_state
                continue

            _report(f'Adjusting {current} -> {target}')

            # Serialize every dry/wet profile pulse across every control.  The
            # acquire is interruptible so a new profile or leaving the car wins.
            while not _SURFACE_DC_PRECISION_PULSE_LOCK.acquire(timeout=0.08):
                state = self._precision_context_state(
                    nonce,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                if state in {"replaced", "cancelled"}:
                    return state

            try:
                state = self._precision_context_state(
                    nonce,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                )
                if state in {"replaced", "cancelled"}:
                    return state
                if state != "ready":
                    continue
                last_value_before_pulse = float(current)
                _direct_pulse(
                    key,
                    press_ms=SURFACE_DC_PRECISION_PRESS_MS,
                    interval_ms=SURFACE_DC_PRECISION_INTERVAL_MS
                )
            finally:
                _SURFACE_DC_PRECISION_PULSE_LOCK.release()

            wait_state = self._precision_wait(
                nonce,
                SURFACE_DC_PRECISION_SETTLE_S,
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            )
            if wait_state in {"replaced", "cancelled"}:
                return wait_state

    def _bot_verify_and_correct_target(
        self,
        target: float,
        nonce: int,
        *,
        is_bot_safe: bool,
        direct_boundary: bool,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> bool:
        """Verify a finished BOT macro and correct until the target is stable."""
        if direct_boundary:
            press_ms, interval_ms = _boundary_pulse_timing_ms()
        elif is_bot_safe:
            press_ms, interval_ms = 8, 10
        else:
            press_ms, interval_ms = 3, 3

        short_name = compact_driver_control_name(self.var_name)
        last_status_time = 0.0
        stable_since: Optional[float] = None
        last_correction_value: Optional[float] = None
        last_correction_direction = 0
        last_correction_time = 0.0

        if self.update_status:
            self.update_status("Checking...", "orange")

        while True:
            if not self._bot_correction_sleep(
                nonce,
                allow_caution=allow_caution,
                allow_pace=allow_pace,
                allow_pit_service=allow_pit_service
            ):
                return False

            current = self._read_verified_value(samples=1, delay=0.0)
            if current is None:
                stable_since = None
                continue

            now_perf = time.perf_counter()
            if self._target_reached(current, target):
                last_correction_value = None
                last_correction_direction = 0
                if stable_since is None:
                    stable_since = now_perf
                    if self.update_status:
                        self.update_status("Monitoring...", "orange")
                    continue
                if now_perf - stable_since >= BOT_CORRECTION_STABILITY_WINDOW_S:
                    return True
                continue

            stable_since = None

            diff = float(target) - float(current)
            key = self.key_increase if diff > 0 else self.key_decrease
            if not key:
                return False
            direction = 1 if diff > 0 else -1

            same_stale_value = False
            if last_correction_value is not None:
                if self.is_float:
                    same_stale_value = (
                        abs(float(current) - float(last_correction_value))
                        <= max(0.0005, self._target_tolerance())
                    )
                else:
                    same_stale_value = (
                        int(round(float(current)))
                        == int(round(float(last_correction_value)))
                    )
            if (
                same_stale_value
                and direction == last_correction_direction
                and now_perf - last_correction_time < BOT_CORRECTION_RETRY_CONFIRM_S
            ):
                continue

            pulses = 1
            estimated = self._estimate_pulses_remaining(diff)
            if estimated is not None:
                burst_cap = 6 if direct_boundary else (2 if self.is_float else 3)
                pulses = max(1, min(burst_cap, estimated))

            now = time.time()
            if self.update_status:
                self.update_status(
                    f'Correcting... ({pulses})',
                    "orange"
                )
            if self.app and now - last_status_time >= BOT_CORRECTION_STATUS_INTERVAL_S:
                self.app.notify_overlay_status(
                    f'{short_name}: correcting -> {target}',
                    "orange"
                )
                last_status_time = now

            for _ in range(pulses):
                if not self._bot_correction_can_continue(
                    nonce,
                    allow_caution=allow_caution,
                    allow_pace=allow_pace,
                    allow_pit_service=allow_pit_service
                ):
                    return False
                _direct_pulse(key, press_ms=press_ms, interval_ms=interval_ms)
            last_correction_value = float(current)
            last_correction_direction = direction
            last_correction_time = time.perf_counter()

    def _discovery_value_changed(self, old: float, new: float) -> bool:
        """Return True when a discovery pulse moved the telemetry value."""
        if self.is_float:
            tolerance = max(0.0005, self._target_tolerance() / 2.0)
            return abs(float(new) - float(old)) > tolerance
        return int(round(float(old))) != int(round(float(new)))

    def _drive_to_value_direct(
        self,
        target: float,
        *,
        max_pulses: int,
        press_ms: int,
        interval_ms: int,
        settle_s: float
    ) -> bool:
        """Drive a control back toward a value using direct pulses."""
        for _ in range(max(1, max_pulses)):
            if self.app and self.app.app_state != "RUNNING":
                return False
            if self.app and not self.app._commands_allowed():
                return False
            current = self._read_verified_value(samples=2, delay=0.01)
            if self._target_reached(current, target):
                return True
            if current is None:
                time.sleep(settle_s)
                continue

            diff = float(target) - float(current)
            key = self.key_increase if diff > 0 else self.key_decrease
            if not key:
                return False
            _direct_pulse(key, press_ms=press_ms, interval_ms=interval_ms)
            time.sleep(settle_s)

        current = self._read_verified_value(samples=3, delay=0.012)
        return self._target_reached(current, target)

    def discover_bounds(
        self,
        max_pulses: int = BOUND_DISCOVERY_MAX_PULSES
    ) -> Tuple[Optional[float], Optional[float]]:
        """Discover min/max values by pulsing decrease/increase to the stops."""
        if self.app and self.app.app_state != "RUNNING":
            raise ValueError("Use RUNNING mode to detect the limits.")
        if self.app and not self.app._commands_allowed():
            raise ValueError("Commands blocked by the context protection.")
        unsafe_increase = _unsafe_system_binding_reason(self.key_increase)
        unsafe_decrease = _unsafe_system_binding_reason(self.key_decrease)
        if unsafe_increase or unsafe_decrease:
            unsafe_label = unsafe_increase or unsafe_decrease
            raise ValueError(
                f'System hotkey detected ({unsafe_label}). Change that control in iRacing to an ordinary key and refresh the car controls.'
            )
        if not self.can_discover_bounds():
            raise ValueError(
                "Set the increase and decrease keys on a numeric control."
            )
        if self.running_action:
            raise ValueError("The control is busy; wait for the current macro to finish.")

        self.running_action = True
        press_ms, interval_ms = _boundary_pulse_timing_ms()
        restore_target: Optional[float] = None

        def _walk_to_stop(
            key: Any,
            label: str
        ) -> Tuple[Optional[float], bool]:
            if self.update_status:
                self.update_status(label, "orange")

            last = self._read_verified_value(samples=3, delay=0.012)
            if last is None:
                return None, False

            limit_value = last
            unchanged = 0
            hit_stop = False

            for _ in range(max(1, max_pulses)):
                if self.app and self.app.app_state != "RUNNING":
                    return limit_value, False
                if self.app and not self.app._commands_allowed():
                    return limit_value, False

                _direct_pulse(key, press_ms=press_ms, interval_ms=interval_ms)
                time.sleep(BOUND_DISCOVERY_SETTLE_S)
                current = self._read_verified_value(samples=2, delay=0.01)
                if current is None:
                    continue

                if self._discovery_value_changed(last, current):
                    delta = abs(float(current) - float(last))
                    if self.is_float and delta >= 1e-4:
                        if self._float_step is None or delta < self._float_step:
                            self._float_step = round(delta, 6)
                    last = current
                    limit_value = current
                    unchanged = 0
                else:
                    unchanged += 1
                    if unchanged >= BOUND_DISCOVERY_STABLE_PULSES:
                        hit_stop = True
                        break

            return limit_value, hit_stop

        try:
            restore_target = self._read_verified_value(samples=3, delay=0.012)
            if restore_target is None:
                raise ValueError("Telemetry unavailable for this control.")

            min_value, min_hit = _walk_to_stop(
                self.key_decrease, "Detecting the minimum..."
            )
            if min_value is None or not min_hit:
                raise ValueError("Could not confirm the minimum.")

            max_value, max_hit = _walk_to_stop(
                self.key_increase, "Detecting the maximum..."
            )
            if max_value is None or not max_hit:
                raise ValueError("Could not confirm the maximum.")

            self.set_bounds(min_value, max_value)
            return self.configured_bounds()
        finally:
            if restore_target is not None:
                self._drive_to_value_direct(
                    restore_target,
                    max_pulses=max(8, max_pulses * 2),
                    press_ms=press_ms,
                    interval_ms=interval_ms,
                    settle_s=BOUND_DISCOVERY_SETTLE_S
                )
            self.running_action = False
            with self._target_lock:
                has_pending = self._requested_target is not None and not self._clear_requested
            if has_pending:
                self._start_target_worker_if_needed()

    def _read_weight_jacker_int(self, samples: int = 2) -> Optional[int]:
        """Return a stable integer Weight Jacker value when telemetry is ready."""
        values: List[int] = []
        for idx in range(max(1, samples)):
            current = self.read_telemetry()
            if current is not None:
                try:
                    values.append(int(round(float(current))))
                except Exception:
                    pass
            if idx < samples - 1:
                time.sleep(0.012)
        if not values:
            return None
        values.sort()
        return values[len(values) // 2]

    def _weight_jacker_request_changed(self, nonce: int) -> bool:
        with self._target_lock:
            return self._clear_requested or self._requested_nonce != nonce

    def _send_weight_jacker_pulses(
        self,
        key: Any,
        pulses: int,
        *,
        press_ms: int,
        interval_ms: int,
        nonce: int
    ) -> bool:
        for _ in range(max(0, pulses)):
            if self._weight_jacker_request_changed(nonce):
                return False
            _direct_pulse(key, press_ms=press_ms, interval_ms=interval_ms)
        return True

    def _run_weight_jacker_direct_loop(self):
        """Send a fast burst, verify telemetry, then correct close to target."""
        if self.running_action:
            return

        if not self.key_increase or not self.key_decrease:
            if self.update_status:
                self.update_status("No keys set", "red")
            if self.app:
                self.app.notify_overlay_status(
                    f'{compact_driver_control_name(self.var_name)}: no keys',
                    "red"
                )
            return

        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        success = False
        cancelled = False
        cleared = False
        restart_precision = False
        active_target: Optional[int] = None

        try:
            while True:
                with self._target_lock:
                    pending_target = self._requested_target
                    pending_nonce = self._requested_nonce
                    cleared = self._clear_requested
                    pending_precision = self._requested_precision_persistent

                if cleared or pending_target is None:
                    break
                if pending_precision:
                    # Hand a dry/wet request to the conservative generic
                    # loop instead of using Weight Jacker's fast burst mode.
                    restart_precision = True
                    break

                if self.app and self.app.app_state != "RUNNING":
                    cancelled = True
                    break

                if self.app and not self.app._commands_allowed():
                    cancelled = True
                    break

                current = self._read_weight_jacker_int(samples=2)
                if current is None:
                    time.sleep(0.02)
                    continue

                active_target = int(round(float(pending_target)))
                diff = active_target - current
                pulses = abs(diff)

                if pulses == 0:
                    with self._target_lock:
                        if self._requested_nonce == pending_nonce:
                            self._requested_target = None
                    success = True
                    break

                burst_pulses = pulses
                if pulses > WEIGHT_JACKER_BURST_GUARD + 2:
                    burst_pulses = pulses - WEIGHT_JACKER_BURST_GUARD
                key = self.key_increase if diff > 0 else self.key_decrease
                if self.update_status:
                    self.update_status(
                        f"Burst ({burst_pulses}+verify)",
                        "orange"
                    )
                if self.app:
                    self.app.notify_overlay_status(
                        f"{short_name} burst -> {active_target}",
                        "orange"
                    )

                if not self._send_weight_jacker_pulses(
                    key,
                    burst_pulses,
                    press_ms=WEIGHT_JACKER_BURST_PRESS_MS,
                    interval_ms=WEIGHT_JACKER_BURST_INTERVAL_MS,
                    nonce=pending_nonce
                ):
                    continue

                time.sleep(WEIGHT_JACKER_VERIFY_SETTLE_S)

                for pass_idx in range(WEIGHT_JACKER_MAX_CORRECTION_PASSES):
                    if self._weight_jacker_request_changed(pending_nonce):
                        break
                    current = self._read_weight_jacker_int(samples=2)
                    if current is None:
                        time.sleep(0.02)
                        continue
                    remaining = active_target - current
                    correction_pulses = abs(remaining)
                    if correction_pulses == 0:
                        break

                    key = self.key_increase if remaining > 0 else self.key_decrease
                    press_ms = (
                        WEIGHT_JACKER_FINE_PRESS_MS
                        if correction_pulses <= 2
                        else WEIGHT_JACKER_BURST_PRESS_MS
                    )
                    interval_ms = (
                        WEIGHT_JACKER_FINE_INTERVAL_MS
                        if correction_pulses <= 2
                        else WEIGHT_JACKER_BURST_INTERVAL_MS
                    )
                    if self.update_status:
                        self.update_status(
                            f"Correction {pass_idx + 1} ({correction_pulses})",
                            "orange"
                        )
                    if not self._send_weight_jacker_pulses(
                        key,
                        correction_pulses,
                        press_ms=press_ms,
                        interval_ms=interval_ms,
                        nonce=pending_nonce
                    ):
                        break
                    time.sleep(
                        WEIGHT_JACKER_VERIFY_SETTLE_S
                        if pass_idx < WEIGHT_JACKER_MAX_CORRECTION_PASSES - 1
                        else 0.02
                    )

                final_value = self._read_weight_jacker_int(samples=3)
                final_ok = final_value == active_target

                with self._target_lock:
                    same_request = self._requested_nonce == pending_nonce
                    if same_request and final_ok:
                        self._requested_target = None

                success = final_ok
                if same_request and final_ok:
                    break
                if same_request:
                    time.sleep(0.02)
                    continue

        except Exception as exc:
            print(f"[WeightJackerDirect] Exception: {exc}")
        finally:
            if restart_precision:
                if self.update_status:
                    self.update_status("Starting slow precision...", "orange")
            elif success:
                if self.update_status:
                    self.update_status("Ready", "green")
                if self.app and active_target is not None:
                    self.app.notify_overlay_status(
                        f'{short_name} sent ({active_target})',
                        "green"
                    )
            elif cancelled:
                if self.update_status:
                    self.update_status("Canceled", "red")
                if self.app:
                    self.app.notify_overlay_status(
                        f'{short_name} canceled',
                        "red"
                    )
            elif cleared:
                if self.update_status:
                    self.update_status("Ready", "green")
                if self.app:
                    self.app.notify_overlay_status(
                        f'{short_name} cleared',
                        "orange"
                    )
            else:
                if self.update_status:
                    self.update_status("Failed", "red")
                if self.app:
                    self.app.notify_overlay_status(
                        f'{short_name} failed',
                        "red"
                    )

            self.running_action = False
            if restart_precision:
                worker = threading.Thread(
                    target=self._run_target_loop,
                    daemon=True
                )
                self._worker_thread = worker
                worker.start()

    def _run_target_loop(self):
        with self._target_lock:
            initial_precision = self._requested_precision_persistent

        if self.var_name in WEIGHT_JACKER_VARS and not initial_precision:
            self._run_weight_jacker_direct_loop()
            return

        if self.running_action:
            return

        if (
            not initial_precision
            and (not self.key_increase or not self.key_decrease)
        ):
            if self.update_status:
                self.update_status("No keys set", "red")
            if self.app:
                self.app.notify_overlay_status(
                    f'{compact_driver_control_name(self.var_name)}: no keys',
                    "red"
                )
            return

        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        active_target: Optional[float] = None
        active_nonce: Optional[int] = None
        active_allow_caution = False
        active_allow_pace = False
        active_allow_pit_service = False
        active_precision_persistent = False
        direct_boundary = False
        timeout_deadline: Optional[float] = None
        cancelled = False
        cleared = False
        success = False
        completed_precision = False
        last_diff: Optional[float] = None
        last_value: Optional[float] = None
        off_by_one_streak = 0
        last_pulse_report_time = 0.0
        last_pulse_report_remaining: Optional[int] = None
        timing_profile = _normalize_timing_config(GLOBAL_TIMING).get("profile", "aggressive")
        # CLAUDE joins the bot family so it inherits verify-and-correct; it is
        # flagged separately because its pulses are measured, not guessed.
        is_claude = timing_profile == "claude"
        is_bot_profile = timing_profile in {"bot", "bot_safe", "claude"}
        is_bot_safe = timing_profile == "bot_safe"
        # Busy-control tracking: a control that will not move (pit service,
        # blocked state) must be waited out, not hammered.
        stalled_pulses = 0
        stall_backoff_s = CLAUDE_STALL_BACKOFF_S

        try:
            while True:
                with self._target_lock:
                    pending_target = self._requested_target
                    pending_nonce = self._requested_nonce
                    cleared = self._clear_requested
                    pending_direct_boundary = self._requested_direct_boundary
                    pending_allow_caution = self._requested_allow_caution
                    pending_allow_pace = self._requested_allow_pace
                    pending_allow_pit_service = self._requested_allow_pit_service
                    pending_precision = self._requested_precision_persistent

                if cleared or pending_target is None:
                    break

                if pending_nonce != active_nonce:
                    active_nonce = pending_nonce
                    direct_boundary = pending_direct_boundary
                    active_allow_caution = pending_allow_caution
                    active_allow_pace = pending_allow_pace
                    active_allow_pit_service = pending_allow_pit_service
                    active_precision_persistent = pending_precision
                    if direct_boundary:
                        active_target = pending_target
                    else:
                        active_target = self._resolve_target(pending_target)
                    if not self.is_float:
                        active_target = int(round(active_target))
                    keep_trying = bool(
                        self.app and self.app.keep_trying_targets.get()
                    )
                    timeout_deadline = None if keep_trying else time.time() + 8

                    if self.update_status:
                        self.update_status("Adjusting...", "orange")
                    if self.app:
                        self.app.notify_overlay_status(
                            f'Adjusting {short_name} -> {active_target}',
                            "orange"
                        )

                if (
                    active_precision_persistent
                    and active_target is not None
                    and active_nonce is not None
                ):
                    precision_result = self._run_precision_persistent_target(
                        active_target,
                        active_nonce,
                        allow_caution=active_allow_caution,
                        allow_pace=active_allow_pace,
                        allow_pit_service=active_allow_pit_service
                    )
                    if precision_result == "success":
                        with self._target_lock:
                            if self._requested_nonce == active_nonce:
                                self._precision_last_confirmed_target = active_target
                                self._precision_last_confirmed_nonce = active_nonce
                                self._requested_target = None
                                self._requested_precision_persistent = False
                        success = True
                        completed_precision = True
                        break
                    if precision_result == "replaced":
                        active_target = None
                        active_nonce = None
                        active_precision_persistent = False
                        last_diff = None
                        last_value = None
                        continue
                    cancelled = True
                    break

                if self.app and self.app.app_state != "RUNNING":
                    cancelled = True
                    break

                if self.app and not self.app._commands_allowed(
                    allow_caution=active_allow_caution,
                    allow_pace=active_allow_pace,
                    allow_pit_service=active_allow_pit_service
                ):
                    cancelled = True
                    break

                keep_trying = bool(
                    self.app and self.app.keep_trying_targets.get()
                )
                if keep_trying:
                    timeout_deadline = None
                elif timeout_deadline is None:
                    timeout_deadline = time.time() + 8
                if not keep_trying and timeout_deadline and time.time() > timeout_deadline:
                    break

                current = self.read_telemetry()
                if current is None:
                    time.sleep(0.05)
                    continue

                if self.is_float and last_value is not None:
                    delta = abs(float(current) - float(last_value))
                    if delta >= 1e-4:
                        if self._float_step is None or delta < self._float_step:
                            self._float_step = round(delta, 6)

                if active_target is None:
                    time.sleep(0.05)
                    continue

                fast_boundary = direct_boundary
                if not direct_boundary and is_bot_profile:
                    if self.is_float:
                        base_step = self._float_step if self._float_step else 0.001
                        close_threshold = max(0.001, base_step)
                        if abs(active_target - current) <= close_threshold * 2:
                            current = self._read_stable_float(
                                float(current),
                                samples=3,
                                delay=0.02 if is_bot_safe else 0.012
                            )
                    else:
                        if abs(active_target - current) <= 1:
                            stable_delay = 0.03 if is_bot_safe else 0.015
                            current = self._read_stable_int(
                                int(round(current)),
                                samples=3,
                                delay=stable_delay
                            )

                diff = active_target - current
                abs_diff = abs(diff)
                overshot = (
                    last_diff is not None
                    and diff != 0
                    and ((diff > 0 > last_diff) or (diff < 0 < last_diff))
                )

                # A control that does not budge after a pulse is busy, not
                # under-driven.  Count the stalls and let the wait grow rather
                # than firing into something that is ignoring us.
                if is_claude:
                    if last_value is not None and current == last_value and last_diff is not None:
                        stalled_pulses += 1
                    else:
                        stalled_pulses = 0
                        stall_backoff_s = CLAUDE_STALL_BACKOFF_S

                    if stalled_pulses >= CLAUDE_STALL_PULSES:
                        if self.update_status:
                            self.update_status("Control busy, waiting...", "orange")
                        time.sleep(stall_backoff_s)
                        stall_backoff_s = min(
                            CLAUDE_STALL_BACKOFF_MAX_S, stall_backoff_s * 1.6
                        )
                        # One deliberately long hold: some controls only latch
                        # when the key is held past their own debounce.
                        if diff != 0:
                            long_key = self.key_increase if diff > 0 else self.key_decrease
                            _direct_pulse(
                                long_key,
                                press_ms=CLAUDE_STALL_LONG_PRESS_MS,
                                interval_ms=CLAUDE_INTERVAL_MS,
                            )
                        stalled_pulses = 0
                        last_diff = diff
                        last_value = current
                        continue

                if fast_boundary:
                    pulses_remaining = self._estimate_pulses_remaining(diff)
                    if pulses_remaining is not None:
                        now = time.time()
                        if (
                            now - last_pulse_report_time >= 0.25
                            or pulses_remaining != last_pulse_report_remaining
                        ):
                            boundary_press_ms, boundary_interval_ms = _boundary_pulse_timing_ms()
                            rate_hz = _pulse_rate_hz(
                                boundary_press_ms, boundary_interval_ms
                            )
                            status = (
                                f'Adjusting... ({pulses_remaining} pulses, ~{rate_hz:.1f}/s)'
                            )
                            if self.update_status:
                                self.update_status(status, "orange")
                            last_pulse_report_time = now
                            last_pulse_report_remaining = pulses_remaining

                if self.is_float:
                    tolerance = 0.001
                    if self._float_step and self._float_step > 0:
                        tolerance = max(tolerance, self._float_step / 2.0)
                    if abs_diff <= tolerance:
                        success = True
                elif diff == 0:
                    success = True

                if success:
                    if is_bot_profile and active_target is not None and active_nonce is not None:
                        success = self._bot_verify_and_correct_target(
                            active_target,
                            active_nonce,
                            is_bot_safe=is_bot_safe,
                            direct_boundary=direct_boundary,
                            allow_caution=active_allow_caution,
                            allow_pace=active_allow_pace,
                            allow_pit_service=active_allow_pit_service
                        )
                        if not success:
                            last_diff = None
                            last_value = None
                            if (
                                active_nonce is not None
                                and self._target_request_changed(active_nonce)
                            ):
                                continue
                            time.sleep(BOT_CORRECTION_SETTLE_S)
                            continue
                    with self._target_lock:
                        if self._requested_nonce == active_nonce:
                            self._requested_target = None
                    break

                key = self.key_increase if diff > 0 else self.key_decrease

                if not direct_boundary and is_bot_profile and not self.is_float:
                    if abs_diff == 1:
                        if last_diff is not None and diff == last_diff:
                            off_by_one_streak += 1
                        else:
                            off_by_one_streak = 1
                    else:
                        off_by_one_streak = 0

                    if off_by_one_streak >= 2:
                        if is_claude:
                            # Stuck one step out: the pulse is landing but the
                            # read is racing it.  Hold the measured duration and
                            # give telemetry a full tick to catch up.
                            _direct_pulse(
                                key,
                                press_ms=CLAUDE_PRESS_MS,
                                interval_ms=CLAUDE_INTERVAL_MS,
                            )
                            _sleep_after_output(key, 0.05, 0.02)
                        elif is_bot_safe:
                            _direct_pulse(key, press_ms=10, interval_ms=12)
                            _sleep_after_output(key, 0.09, 0.03)
                        else:
                            _direct_pulse(key, press_ms=4, interval_ms=4)
                            _sleep_after_output(key, 0.03, 0.008)
                        last_diff = diff
                        last_value = current
                        continue

                base_step = self._float_step if self._float_step else 0.001
                close_threshold = max(0.001, base_step) if self.is_float else 1.0
                near_target = abs_diff <= close_threshold * 2
                if fast_boundary:
                    boundary_press_ms, boundary_interval_ms = _boundary_pulse_timing_ms()
                    _direct_pulse(
                        key,
                        press_ms=boundary_press_ms,
                        interval_ms=boundary_interval_ms
                    )
                elif not direct_boundary and is_bot_profile:
                    if abs_diff <= close_threshold * 2 or overshot:
                        if is_claude:
                            # Near the target, and on any overshoot, CLAUDE does
                            # not speed up: a missed step here costs a whole
                            # verify round trip, which dwarfs the few ms saved.
                            _direct_pulse(
                                key,
                                press_ms=CLAUDE_PRESS_MS,
                                interval_ms=CLAUDE_INTERVAL_MS,
                            )
                            _sleep_after_output(
                                key,
                                0.06 if (near_target or overshot) else 0.04,
                                0.02 if (near_target or overshot) else 0.012,
                            )
                        elif is_bot_safe:
                            _direct_pulse(key, press_ms=8, interval_ms=10)
                            _sleep_after_output(
                                key,
                                0.09 if near_target else 0.07,
                                0.025 if near_target else 0.012
                            )
                        else:
                            _direct_pulse(key, press_ms=3, interval_ms=3)
                            _sleep_after_output(
                                key,
                                0.04 if near_target else 0.025,
                                0.01 if near_target else 0.004
                            )
                    else:
                        click_pulse(key, self.is_float)
                        if abs_diff <= close_threshold * 4:
                            _sleep_after_output(
                                key,
                                0.035 if near_target else 0.02,
                                0.01 if near_target else 0.005
                            )
                        else:
                            _sleep_after_output(
                                key,
                                0.025 if is_bot_safe else 0.01,
                                0.006 if is_bot_safe else 0.003
                            )
                else:
                    click_pulse(key, self.is_float)
                    _sleep_after_output(key, 0.02, 0.005)
                last_diff = diff
                last_value = current

        except Exception as exc:
            print(f"[GenericController] Exception: {exc}")
        finally:
            if cancelled and active_nonce is not None:
                with self._target_lock:
                    if self._requested_nonce == active_nonce:
                        self._requested_target = None
                        self._clear_requested = True
            if success:
                message = f"{short_name} OK ({active_target})"
                if self.update_status:
                    self.update_status("Ready", "green")
                if self.app:
                    self.app.notify_overlay_status(message, "green")
                    if self.app.use_tts.get() and not completed_precision:
                        speak_text(message)
            elif cancelled:
                if self.update_status:
                    self.update_status("Canceled", "red")
                if self.app:
                    self.app.notify_overlay_status(
                        f'{short_name} canceled',
                        "red"
                    )
            elif cleared:
                if self.update_status:
                    self.update_status("Ready", "green")
                if self.app:
                    self.app.notify_overlay_status(
                        f'{short_name} cleared',
                        "orange"
                    )
            else:
                if self.update_status:
                    self.update_status("Failed", "red")
                if self.app:
                    self.app.notify_overlay_status(
                        f'{short_name} failed',
                        "red"
                    )

            self.running_action = False

    def find_minimum_effective_timing(
        self,
        start_ms: int = 1,
        max_ms: int = 120,
        step_ms: int = 1,
        settle_s: float = 0.05,
        confirmation_attempts: int = 2
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
        if self.app and not self.app._commands_allowed():
            raise ValueError("Commands are blocked by the context protection.")
        if not self.key_increase or not self.key_decrease:
            raise ValueError(
                "Set the increase and decrease keys before running the test."
            )

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
                _direct_pulse(direction, timing_ms, timing_ms)
                time.sleep(settle_s)

        for delay_ms in range(max(1, start_ms), max_ms + 1, max(1, step_ms)):
            success_count = 0
            for _ in range(max(1, confirmation_attempts)):
                _direct_pulse(self.key_increase, delay_ms, delay_ms)
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


__all__ = ['GenericController']
