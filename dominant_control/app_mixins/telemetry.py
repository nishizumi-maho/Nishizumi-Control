from __future__ import annotations

from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *

class TelemetryMixin:
    def _get_session_state(self) -> Tuple[str, Optional[int]]:
        """Return the current session type and session number if available."""
        try:
            session_info = self.ir["SessionInfo"]
        except Exception:
            return "", None

        session_num = None
        try:
            session_num = int(self.ir["SessionNum"])
        except Exception:
            pass

        session_type = ""
        try:
            sessions = session_info.get("Sessions") if session_info else None
            if isinstance(sessions, list):
                if session_num is not None and 0 <= session_num < len(sessions):
                    session_type = sessions[session_num].get("SessionType", "")
                if not session_type:
                    for entry in sessions:
                        session_type = entry.get("SessionType", "")
                        if session_type:
                            break
        except Exception:
            pass

        return session_type, session_num


    def _handle_session_change(
        self, session_type: str, session_num: Optional[int]
    ) -> bool:
        """Handle session transitions and restart if entering a race."""
        new_type = session_type or ""
        new_num = session_num

        if not new_type and new_num is None:
            self._mark_session_inactive()
            return False

        session_changed = (
            new_type != self.last_session_type
            or (new_num is not None and new_num != self.last_session_num)
        )

        if session_changed:
            self.last_session_type = new_type
            if new_num is not None:
                self.last_session_num = new_num

            if self.skip_race_restart_once and new_type == "Race":
                self.skip_race_restart_once = False
                return False

            if self.skip_session_scan_once:
                self.skip_session_scan_once = False
                return False

            if not self._scan_recent_for_pair(window_s=60.0):
                self._last_auto_pair = ("", "")
                self.auto_load_attempted.clear()

            if self.auto_restart_on_race.get() and new_type == "Race":
                self.pending_scan_on_start = True
                mark_pending_scan()
                self.save_config()
                restart_program()
                return True

            self._schedule_session_scan()

        return False


    def _maybe_restart_on_track_ready(self) -> bool:
        """Restart + rescan when on-track telemetry flips to true."""
        if not self.auto_restart_on_track_ready.get():
            self._on_track_restart_seen = False
            self.skip_on_track_restart_once = False
            return False

        is_on_track = self._bool_from_keys(["IsOnTrack"])
        is_on_track_car = self._bool_from_keys(["IsOnTrackCar"])
        on_track_now = is_on_track and is_on_track_car

        if self.skip_on_track_restart_once and on_track_now:
            self.skip_on_track_restart_once = False
            self._on_track_restart_seen = True
            return False

        if on_track_now and not self._on_track_restart_seen:
            self._on_track_restart_seen = True
            self.manual_restart_scan()
            return True

        if not on_track_now:
            self._on_track_restart_seen = False

        return False


    def _validate_and_recover(self) -> None:
        """Validate scan results and attempt recovery when telemetry is invalid."""

        if (
            self._recovery_in_progress
            or self._scan_in_progress
            or self._validation_in_progress
        ):
            print("[Recovery] Scan in progress; validation deferred")
            return

        if not self._on_track_validation_pending:
            return

        self._on_track_validation_pending = False
        self._validation_in_progress = True
        validator = ScanValidator(self.ir, dict(self.controllers))
        self.scan_validator = validator

        def _worker() -> None:
            try:
                success, message = validator.validate_scan()
            except Exception as exc:
                success = False
                message = f'Validation error: {exc}'
            self._ui_after(self._finish_validate_and_recover, success, message)

        threading.Thread(
            target=_worker,
            name="ScanValidation",
            daemon=True
        ).start()


    def _finish_validate_and_recover(self, success: bool, message: str) -> None:
        """Finish scan validation on the UI thread."""
        self._validation_in_progress = False
        print(f'[Validation] {message}')

        if success:
            self._validation_failures = 0
            self.notify_overlay_status("Scan finished", "green")
            return

        self._validation_failures += 1
        print(
            f'[Recovery] Validation failed ({self._validation_failures}/{self._max_validation_failures})'
        )

        if self._validation_failures >= self._max_validation_failures:
            self._show_warning(
                "Scan recovery",
                "Could not read telemetry after several attempts.\nRestarting the application to fix the connection..."
            )
            self._recovery_restart_and_scan()
        else:
            self.notify_overlay_status(
                f'Scan problem — trying again ({self._validation_failures}/3)',
                "orange"
            )
            self._recovery_rescan()


    def _recovery_rescan(self) -> None:
        """Attempt a rescan without restarting the app."""

        if self._recovery_in_progress:
            return

        if self._scan_in_progress and not self._reset_stale_scan_if_needed():
            self.root.after(500, self._recovery_rescan)
            return

        self._recovery_in_progress = True
        self._recovery_attempt += 1

        print(
            f'[Recovery] New scan (attempt {self._recovery_attempt})...'
        )

        def on_complete() -> None:
            self._recovery_in_progress = False
            self.root.after(1000, self._validate_after_recovery)

        self.scan_driver_controls(
            silent_if_unavailable=True,
            allow_restart=False,
            on_complete=on_complete
        )


    def _validate_after_recovery(self) -> None:
        """Validate scan after a recovery attempt."""

        if self._scan_in_progress and not self._reset_stale_scan_if_needed():
            self.root.after(500, self._validate_after_recovery)
            return
        if self._validation_in_progress:
            self.root.after(500, self._validate_after_recovery)
            return

        self._validation_in_progress = True
        validator = ScanValidator(self.ir, dict(self.controllers))
        self.scan_validator = validator

        def _worker() -> None:
            try:
                success, message = validator.validate_scan()
            except Exception as exc:
                success = False
                message = f'Validation error: {exc}'
            self._ui_after(self._finish_validate_after_recovery, success, message)

        threading.Thread(
            target=_worker,
            name="RecoveryValidation",
            daemon=True
        ).start()


    def _finish_validate_after_recovery(
        self,
        success: bool,
        message: str
    ) -> None:
        """Finish post-rescan validation on the UI thread."""
        self._validation_in_progress = False
        print(f'[Recovery] Validation after recovery: {message}')

        if success:
            self._validation_failures = 0
            self._recovery_attempt = 0
            self.notify_overlay_status("Recovery succeeded", "green")
            if self.use_tts.get():
                speak_text("Scan recovered")
            return

        if self._recovery_attempt >= self._max_recovery_attempts:
            self._show_warning(
                "Scan recovery",
                "The new scan failed. Restarting the application..."
            )
            self._recovery_restart_and_scan()
        else:
            print(
                "[Recovery] The new scan did not fix the problem; trying again..."
            )
            self.root.after(2000, self._recovery_rescan)


    def _recovery_restart_and_scan(self) -> None:
        """Restart the app and schedule a scan after reboot."""

        print("[Recovery] Starting restart and scan...")

        detected_car, detected_track = self._detect_current_car_track()
        restart_car = (
            detected_car
            or self.combo_car.get().strip()
            or self.current_car
        ).strip()
        restart_track = (
            detected_track
            or self.combo_track.get().strip()
            or self.current_track
        ).strip()

        if restart_car and restart_track:
            self._rescan_restart_pair = (restart_car, restart_track)

        self.pending_scan_on_start = True
        self.skip_race_restart_once = True
        self.skip_session_scan_once = True
        self.skip_on_track_restart_once = True

        mark_pending_scan(silent=True)
        self.save_config()
        restart_program()


    def _handle_none_scan_result(self) -> bool:
        """Return True if a repeated None scan triggered a restart."""
        if not self._telemetry_active or not self._telemetry_ready_for_scan():
            self._none_scan_attempts = 0
            return False

        self._none_scan_attempts += 1
        print(
            f'[Recovery] The scan returned no telemetry values ({self._none_scan_attempts}/{self._max_none_scan_attempts}).'
        )

        if self._none_scan_attempts < self._max_none_scan_attempts:
            return False

        self._none_scan_attempts = 0
        self._show_warning(
            "Scan recovery",
            "The scan returned empty telemetry several times over.\nRestarting the application to restore telemetry..."
        )
        self._recovery_restart_and_scan()
        return True


    def _set_telemetry_active(self, active: bool) -> bool:
        """Track telemetry connection state and report reconnections."""
        if active == self._telemetry_active:
            return False

        self._telemetry_active = active
        if not active:
            self._mark_session_inactive()
            return False

        already_scanned = self._scan_recent_for_pair(window_s=20.0)
        if not already_scanned:
            self._last_auto_pair = ("", "")
            self._last_detected_pair = ("", "")
            self.auto_load_attempted.clear()
            self._skip_next_auto_load = False
            # _last_weekend_key is deliberately left alone. Losing the session
            # already cleared it in _mark_session_inactive, and clearing it here
            # only made the next pass replay the same weekend change, because
            # _handle_weekend_change runs earlier in the detection loop.
        if (
            self.keep_scanning_until_valid.get()
            and not self.active_vars
            and not already_scanned
        ):
            self._schedule_continuous_scan_retry()
        return True


    def _mark_session_inactive(self) -> None:
        """Reset session tracking when not connected to a session."""
        self.last_session_type = ""
        self.last_session_num = None
        self._last_auto_pair = ("", "")
        self._last_detected_pair = ("", "")
        self._session_scan_pending = False
        self.auto_load_attempted.clear()
        self._telemetry_active = False
        self._last_weekend_key = None
        self._last_session_id = None
        self._skip_next_auto_load = False
        self._on_track_restart_seen = False


    def _live_scan_pair(self) -> Tuple[str, str]:
        """Return the car/track the telemetry reports right now.

        Session changes are detected before the loop re-reads the car and the
        track, so ``current_car``/``current_track`` still name the session the
        driver just left. Guards that ask "was this already scanned?" have to
        judge the session the driver is in, otherwise the pair left behind
        answers for the new one and its rescan is skipped.
        """
        car, track = self._detect_current_car_track()
        if car and track:
            return car, track
        return self.current_car, self.current_track


    def _scan_recent_for_pair(
        self,
        car: Optional[str] = None,
        track: Optional[str] = None,
        *,
        window_s: float = 12.0
    ) -> bool:
        """Return True when this car/track was scanned moments ago."""
        if car is None or track is None:
            live_car, live_track = self._live_scan_pair()
        else:
            live_car, live_track = "", ""
        pair = (
            (car if car is not None else live_car).strip(),
            (track if track is not None else live_track).strip()
        )
        if not pair[0] or not pair[1]:
            return False
        if pair != self._last_successful_scan_pair:
            return False
        return time.time() - self._last_successful_scan_time <= window_s


    def _sdk_connected_fast(self) -> bool:
        """Return True when the SDK looks ready without retry sleeps."""
        hub = getattr(self, "telemetry_hub", None)
        if hub is not None:
            status = hub.status()
            if not status.connected:
                return False
            return self.ir["SessionNum"] is not None
        try:
            with self.ir_lock:
                if not getattr(self.ir, "is_initialized", False):
                    return False
                if getattr(self.ir, "is_connected", True) is False:
                    return False
                self._repair_sdk_header_cache_if_needed()
                _ = self.ir["SessionNum"]
            return True
        except Exception:
            return False


    def _reset_sdk_header_cache(self) -> None:
        """Clear the SDK variable cache so it is rebuilt from memory."""
        for attr_name in (
            "_IRSDK__var_headers",
            "_IRSDK__var_headers_dict",
            "_IRSDK__var_headers_names"
        ):
            try:
                setattr(self.ir, attr_name, None)
            except Exception:
                pass


    def _repair_sdk_header_cache_if_needed(self) -> bool:
        """Repair an empty or partial irsdk cache created too early."""
        try:
            header = getattr(self.ir, "_header", None)
            if not header:
                return False
            expected = int(getattr(header, "num_vars", 0) or 0)
            if expected <= 0:
                return False

            cached_headers = getattr(self.ir, "_IRSDK__var_headers", None)
            cached_dict = getattr(self.ir, "_IRSDK__var_headers_dict", None)
            cached_names = getattr(self.ir, "_IRSDK__var_headers_names", None)
            # Any difference means the cache no longer describes this session.
            # A car with fewer adjustable controls than the previous one also
            # moves every offset, so a shorter-than-expected check alone let the
            # application read another variable's bytes after a session change.
            stale = False
            if isinstance(cached_headers, list) and len(cached_headers) != expected:
                stale = True
            if isinstance(cached_dict, dict) and len(cached_dict) != expected:
                stale = True
            if isinstance(cached_names, list) and len(cached_names) != expected:
                stale = True

            if not stale:
                return False

            self._reset_sdk_header_cache()
            print(
                f'[SDK] Old variable cache rebuilt (expected {expected} vars).'
            )
            return True
        except Exception:
            return False


    def _prime_sdk_metadata(self) -> None:
        """Touch SDK metadata in a worker so later UI callbacks are cheap."""
        keys = ("SessionNum", "DriverInfo", "WeekendInfo", "SessionInfo")
        try:
            with self.ir_lock:
                self._repair_sdk_header_cache_if_needed()
                for key in keys:
                    try:
                        _ = self.ir[key]
                    except Exception:
                        pass
                try:
                    header = getattr(self.ir, "_header", None)
                    expected = int(getattr(header, "num_vars", 0) or 0)
                    if expected > 0:
                        self._repair_sdk_header_cache_if_needed()
                        headers = getattr(self.ir, "_var_headers_dict", None)
                        _ = len(headers)
                except Exception:
                    pass
        except Exception:
            pass


    def _start_sdk_warmup(self) -> None:
        """Start a throttled background connection attempt."""
        hub = getattr(self, "telemetry_hub", None)
        if hub is not None:
            hub.start()
            return
        now = time.time()
        with self._sdk_warmup_lock:
            if now - self._sdk_last_warmup_attempt < 0.35:
                return
            if self._sdk_warmup_thread and self._sdk_warmup_thread.is_alive():
                return
            self._sdk_last_warmup_attempt = now

            def _warmup() -> None:
                if self._ensure_sdk_connected(
                    blocking=True,
                    start_warmup=False
                ):
                    self._prime_sdk_metadata()

            self._sdk_warmup_thread = threading.Thread(
                target=_warmup,
                name="SDKWarmup",
                daemon=True
            )
            self._sdk_warmup_thread.start()


    def _ensure_sdk_connected(
        self,
        *,
        blocking: bool = False,
        start_warmup: bool = True
    ) -> bool:
        """Ensure the shared SDK handle is initialized and connected."""
        hub = getattr(self, "telemetry_hub", None)
        if hub is not None:
            hub.start()
            return hub.status().connected
        if self._sdk_connected_fast():
            return True

        max_retries = 3 if blocking else 1
        base_delay = 0.06

        for attempt in range(max_retries):
            try:
                reconnected = False
                with self.ir_lock:
                    is_init = getattr(self.ir, "is_initialized", False)
                    is_conn = getattr(self.ir, "is_connected", True)

                    if not is_init:
                        reconnected = bool(self.ir.startup())
                    elif not is_conn:
                        try:
                            self.ir.shutdown()
                        except Exception:
                            pass
                        reconnected = bool(self.ir.startup())

                if reconnected:
                    self._reset_sdk_header_cache()
                    self._refresh_controller_ir()
                    _TELEMETRY_CACHE.invalidate()
                    _TELEMETRY_CIRCUIT_BREAKER.reset()
                    if (
                        blocking
                        or threading.current_thread() is not threading.main_thread()
                    ):
                        self._prime_sdk_metadata()
                    elif start_warmup:
                        self._start_sdk_warmup()

                if self._sdk_connected_fast():
                    return True

                if not blocking:
                    break
                if attempt < max_retries - 1:
                    time.sleep(min(base_delay * (2 ** attempt), 0.25))

            except Exception:
                if not blocking or attempt >= max_retries - 1:
                    break
                time.sleep(min(base_delay * (2 ** attempt), 0.25))

        if start_warmup and not blocking:
            self._start_sdk_warmup()

        return False


    def _detect_session_change(self, weekend: Optional[Dict[str, Any]]) -> bool:
        """Detect session changes to refresh controller SDK handles."""
        if not weekend:
            return False

        session_id = weekend.get("SessionID")
        if session_id is None:
            return False

        if session_id != self._last_session_id:
            self._last_session_id = session_id
            self._refresh_controller_ir()
            return True

        return False


    def _get_weekend_key(self, weekend: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
        """Return a stable identifier for the current weekend/session."""
        if not weekend:
            return None

        key_fields = (
            weekend.get("SessionID"),
            weekend.get("SubSessionID"),
            weekend.get("TrackID"),
            weekend.get("TrackDisplayName"),
        )

        if all(field in (None, "") for field in key_fields):
            return None

        return key_fields


    def _handle_weekend_change(self, weekend: Dict[str, Any]) -> None:
        """Reset auto-detect state when a new weekend/session loads."""
        weekend_key = self._get_weekend_key(weekend)
        if weekend_key is None or weekend_key == self._last_weekend_key:
            return

        self._last_weekend_key = weekend_key
        # A new weekend is always a new context: the car, the track and the
        # preset have to be resolved again even when a scan just finished.
        # Keeping the previous pair here left the application on the car and the
        # track of the session the driver had just left.
        self._last_auto_pair = ("", "")
        self._last_detected_pair = ("", "")
        self.auto_load_attempted.clear()

        # Only the rescan itself is skipped when this very car/track was just
        # scanned; the reset above always happens.
        if self._scan_recent_for_pair(window_s=20.0):
            return

        if self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get():
            self._schedule_session_scan()


    def _telemetry_ready_for_scan(self) -> bool:
        """Return True when telemetry data is stable enough to scan."""
        try:
            if not getattr(self.ir, "is_initialized", False):
                self._start_sdk_warmup()
                return False
            if getattr(self.ir, "is_connected", True) is False:
                self._start_sdk_warmup()
                return False
            with self.ir_lock:
                self._repair_sdk_header_cache_if_needed()
                driver_info = self.ir["DriverInfo"]
                weekend = self.ir["WeekendInfo"]
                session_info = self.ir["SessionInfo"]
        except Exception:
            self._start_sdk_warmup()
            return False

        if not driver_info or not weekend or not session_info:
            self._start_sdk_warmup()
            return False

        sessions = session_info.get("Sessions") if session_info else None
        if not sessions:
            return False

        return True


    def _note_none_telemetry(self, var_name: str) -> None:
        """Track None telemetry reads and trigger a rescan if persistent."""
        if not self._telemetry_active:
            return
        if self._scan_in_progress or self._recovery_in_progress:
            return
        if not self._telemetry_ready_for_scan():
            return

        now = time.time()
        if now - self._none_telemetry_last_trigger < self._none_telemetry_cooldown_s:
            return

        count = self._none_telemetry_counts.get(var_name, 0) + 1
        self._none_telemetry_counts[var_name] = count
        if count < self._none_telemetry_threshold:
            return

        self._none_telemetry_counts[var_name] = 0
        self._none_telemetry_last_trigger = now
        print(
            f'[Telemetry] {var_name} returned None; scanning the controls again.'
        )
        self.notify_overlay_status("Telemetry missing - rescanning", "orange")
        try:
            self.root.after(0, self._recovery_rescan)
        except Exception:
            self._recovery_rescan()


    def _clear_none_telemetry(self, var_name: str) -> None:
        """Reset None telemetry streak for a variable."""
        if var_name in self._none_telemetry_counts:
            self._none_telemetry_counts[var_name] = 0


    def _cancel_continuous_scan_retry(self) -> None:
        if not self._continuous_scan_job:
            return
        try:
            self.root.after_cancel(self._continuous_scan_job)
        except Exception:
            pass
        self._continuous_scan_job = None


    def _schedule_continuous_scan_retry(self) -> None:
        if not self.keep_scanning_until_valid.get():
            self._cancel_continuous_scan_retry()
            return
        if self._continuous_scan_job:
            return

        def _retry() -> None:
            self._continuous_scan_job = None
            if not self.keep_scanning_until_valid.get():
                return
            if self.active_vars:
                return
            self.scan_driver_controls(
                silent_if_unavailable=True,
                allow_restart=False
            )

        self._continuous_scan_job = self.root.after(
            self._continuous_scan_delay_ms,
            _retry
        )


    def _reset_stale_scan_if_needed(self, timeout_s: float = 12.0) -> bool:
        """Clear the scan state when the thread did not answer in time."""
        if not self._scan_in_progress:
            return False
        started_at = getattr(self, "_scan_started_at", 0.0) or 0.0
        if not started_at or time.time() - started_at <= timeout_s:
            return False
        print("[Scan] The previous one timed out; resetting the state.")
        self._scan_in_progress = False
        self._scan_started_at = 0.0
        self._session_scan_pending = False
        return True


    def _schedule_session_scan(self) -> None:
        """Schedule a rescan and preset reload for a session change."""
        if self._session_scan_pending:
            return

        if self.active_vars and self._scan_recent_for_pair(window_s=60.0):
            return

        if self._scan_recent_for_pair(window_s=20.0):
            return

        if self.skip_auto_scan_once:
            self.skip_auto_scan_once = False
            return

        if self._scan_in_progress and not self._reset_stale_scan_if_needed():
            self._session_scan_pending = True
            self._skip_next_auto_load = True
            self.root.after(
                self._session_scan_debounce_ms,
                self._auto_scan_and_load_preset
            )
            return

        self._session_scan_pending = True
        self._skip_next_auto_load = True
        self.root.after(
            self._session_scan_debounce_ms,
            self._auto_scan_and_load_preset
        )


    def _auto_scan_and_load_preset(self) -> None:
        """Scan controls and then reload the current car/track preset."""
        if not self._session_scan_pending:
            return

        if self._scan_in_progress and not self._reset_stale_scan_if_needed():
            self.root.after(
                self._session_scan_debounce_ms,
                self._auto_scan_and_load_preset
            )
            return

        if not self._telemetry_ready_for_scan():
            self.root.after(
                self._session_scan_debounce_ms,
                self._auto_scan_and_load_preset
            )
            return

        if self._scan_recent_for_pair(window_s=20.0):
            self._session_scan_pending = False
            self._skip_next_auto_load = False
            return

        self._session_scan_pending = False
        self._skip_next_auto_load = False

        def _finish_auto_load() -> None:
            car = (self.combo_car.get().strip() or self.current_car).strip()
            track = (self.combo_track.get().strip() or self.current_track).strip()
            if car and track and car in self.saved_presets:
                if track in self.saved_presets[car]:
                    entry = self._ensure_track_surface_presets(car, track)
                    surface_key = entry.get(
                        "active_surface",
                        DEFAULT_SURFACE_PRESET
                    )
                    self.load_specific_preset(
                        car,
                        track,
                        surface=surface_key
                    )

        self.scan_driver_controls(on_complete=_finish_auto_load)


    def scan_driver_controls(
        self,
        *,
        silent_if_unavailable: bool = False,
        allow_restart: bool = True,
        on_complete: Optional[Callable[[], None]] = None
    ):
        """Scan for dc* driver control variables in current car."""
        try:
            if self._scan_in_progress and not self._reset_stale_scan_if_needed():
                print(
                    "[Scan] Already running; request ignored"
                )
                if on_complete:
                    self._ui_after(on_complete)
                return

            if (
                allow_restart
                and self.auto_restart_on_rescan.get()
                and self.scans_since_restart >= 1
            ):
                detected_car, detected_track = self._detect_current_car_track()
                restart_car = (
                    detected_car
                    or self.combo_car.get().strip()
                    or self.current_car
                ).strip()
                restart_track = (
                    detected_track
                    or self.combo_track.get().strip()
                    or self.current_track
                ).strip()
                restart_pair = (restart_car, restart_track)
                detected_pair = (detected_car, detected_track)
                suppress_restart = (
                    detected_car
                    and detected_track
                    and detected_pair == self._rescan_restart_pair
                )
                if not suppress_restart:
                    self.pending_scan_on_start = True
                    if restart_car and restart_track:
                        self._rescan_restart_pair = restart_pair
                    mark_pending_scan()
                    self.save_config()
                    restart_program()
                    if on_complete:
                        self._ui_after(on_complete)
                    return

            # Preserve any inline (unsaved) bindings so rescans in the same
            # car/track session don't drop macros/hotkeys
            previous_pair = (self.current_car, self.current_track)
            fallback_tabs = {k: v.get_config() for k, v in self.tabs.items()}
            fallback_combo = self.combo_tab.get_config() if self.combo_tab else {}

            self._scan_in_progress = True
            self._scan_started_at = time.time()
            if not silent_if_unavailable:
                self.notify_overlay_status("Scanning controls...", "orange")

            def _worker():
                result = self._scan_driver_controls_worker()
                self._ui_after(
                    self._finish_scan_driver_controls,
                    result,
                    previous_pair,
                    fallback_tabs,
                    fallback_combo,
                    silent_if_unavailable,
                    on_complete
                )

            threading.Thread(
                target=_worker,
                name="DriverControlScan",
                daemon=True
            ).start()
        except Exception as exc:
            print(f'[Scan] Unexpected error: {exc}')
            import traceback

            traceback.print_exc()
            if not silent_if_unavailable:
                self._show_error(
                    "Scan",
                    f'The scan failed with the error below:\n{exc}\n\nSee the terminal for details.'
                )
            self._schedule_continuous_scan_retry()
            if on_complete:
                self._ui_after(on_complete)
            self._scan_in_progress = False
            self._scan_started_at = 0.0


    def _discover_driver_controls_from_headers(
        self
    ) -> List[Tuple[str, bool, bool]]:
        """Return dc* controls from the headers, repairing old caches."""
        try:
            with self.ir_lock:
                self._repair_sdk_header_cache_if_needed()
                headers = getattr(self.ir, "_var_headers_dict", None)
                names: List[str] = []
                try:
                    names = list(getattr(self.ir, "var_headers_names", None) or [])
                except Exception:
                    names = []
                if not names and headers:
                    names = [str(name) for name in headers.keys()]
                if not names:
                    return []

                found: List[Tuple[str, bool, bool]] = []
                for raw_name in names:
                    name = str(raw_name)
                    if not name.startswith("dc"):
                        continue
                    header = headers.get(name) if headers else None
                    try:
                        if header is not None:
                            var_type = int(getattr(header, "type", -1))
                            count = int(getattr(header, "count", 1))
                            if count != 1:
                                continue
                            if var_type == IRSDK_VAR_TYPE_BOOL:
                                found.append((name, False, True))
                                continue
                            if var_type in IRSDK_VAR_TYPE_INTEGER:
                                found.append((name, False, False))
                                continue
                            if var_type in IRSDK_VAR_TYPE_FLOAT:
                                found.append((name, True, False))
                                continue

                        value = self.ir[name]
                        if isinstance(value, bool):
                            found.append((name, False, True))
                        elif isinstance(value, numbers.Integral):
                            found.append((name, False, False))
                        elif isinstance(value, numbers.Real):
                            found.append((name, True, False))
                    except Exception:
                        continue

            found.sort(key=lambda item: item[0])
            return found
        except Exception as exc:
            print(f'[Scan] Could not discover headers: {exc}')
            return []


    def _scan_driver_controls_worker(self) -> Dict[str, Any]:
        """Worker thread for driver control scanning."""
        try:
            print("[Scan] Starting...")
            if not self._ensure_sdk_connected(blocking=True):
                print("[Scan] SDK disconnected")
                return {"status": "unavailable"}

            self._prime_sdk_metadata()

            weekend = None
            try:
                with self.ir_lock:
                    self._repair_sdk_header_cache_if_needed()
                    weekend = self.ir["WeekendInfo"]
            except Exception as e:
                print(f'[Scan] Could not read WeekendInfo: {e}')
                weekend = None
            self._detect_session_change(weekend)

            found_vars: List[Tuple[str, bool, bool]] = []
            for attempt in range(20):
                found_vars = self._discover_driver_controls_from_headers()
                if found_vars:
                    break
                if attempt == 0:
                    print("[Scan] Waiting for the SDK's variable headers...")
                time.sleep(0.1)
                self._prime_sdk_metadata()

            if found_vars:
                print(
                    f'[Scan] Headers revealed {len(found_vars)} driver controls'
                )
            else:
                # Base candidates for older SDK builds where headers are not exposed.
                candidates = [
                    "dcABS",
                    "dcABias",
                    "dcABSToggle",
                    "dcAntiRollFront",
                    "dcAntiRollRear",
                    "dcBrakeBias",
                    "dcBrakeBiasFine",
                    "dcBrakeFineBias",
                    "dcBrakeMigration",
                    "dcBrakeMisc",
                    "dcDashPage",
                    "dcDashPage2",
                    "dcDiffEntry",
                    "dcDiffExit",
                    "dcDiffMiddle",
                    "dcDiffPreload",
                    "dcDRSToggle",
                    "dcEngineBraking",
                    "dcEnginePower",
                    "dcFCYToggle",
                    "dcFuelCutPosition",
                    "dcFuelMixture",
                    "dcFuelNoCutToggle",
                    "dcHeadlightFlash",
                    "dcHysBoostHold",
                    "dcHysDisableBoostHold",
                    "dcHysRegenHold",
                    "dcInLapToggle",
                    "dcLaunchRPM",
                    "dcLowFuelAccept",
                    "dcMGUKDeployFixed",
                    "dcMGUKDeployMode",
                    "dcMGUKRegenGain",
                    "dcPeakBrakeBias",
                    "dcPitSpeedLimiterToggle",
                    "dcPowerSteering",
                    "dcPushToPass",
                    "dcRFBrakeAttachedToggle",
                    "dcStarter",
                    "dcTearOffVisor",
                    "dcThrottleShape",
                    "dcToggleWindShieldWipers",
                    "dcToggleWindshieldWipers",
                    "dcToogleWindShieldWipers",
                    "dcTractionControl",
                    "dcTractionControl2",
                    "dcTractionControl3",
                    "dcTractionControl4",
                    "dcTractionControlCut",
                    "dcTractionControlToggle",
                    "dcTriggerWindShieldWipers",
                    "dcTriggerWindshieldWipers",
                    "dcWeightJackerRight",
                ]

                try:
                    with self.ir_lock:
                        self._repair_sdk_header_cache_if_needed()
                        names = getattr(self.ir, "var_headers_names", None)
                    if names:
                        print(
                            f'[Scan] {len(names)} variables found in the SDK'
                        )
                        for key in names:
                            if str(key).startswith("dc"):
                                candidates.append(str(key))
                except Exception as e:
                    print(f"[Scan] Error discovering the SDK's variables: {e}")

                candidates = sorted(list(set(candidates)))
                print(
                    f'[Scan] Testing {len(candidates)} candidate variables'
                )

                if not candidates:
                    print("[Scan] No candidate variable found")
                    return {"status": "no_candidates"}

                try:
                    with self.ir_lock:
                        self._repair_sdk_header_cache_if_needed()
                        _ = self.ir["SessionNum"]
                except Exception as e:
                    print(
                        f'[Scan] Warning: could not read SessionNum: {e}'
                    )

                try:
                    for candidate in candidates:
                        try:
                            with self.ir_lock:
                                self._repair_sdk_header_cache_if_needed()
                                value = self.ir[candidate]
                        except Exception as e:
                            print(
                                f'[Scan] {candidate}: read failed ({type(e).__name__}: {e})'
                            )
                            continue

                        if value is None:
                            print(f'[Scan] {candidate}: value None')
                            continue

                        if isinstance(value, bool):
                            found_vars.append((candidate, False, True))
                            print(
                                f'[Scan] {candidate}: FOUND (value={value})'
                            )
                            continue
                        if not isinstance(value, numbers.Real):
                            print(
                                f'[Scan] {candidate}: ignored (type={type(value).__name__})'
                            )
                            continue

                        is_float = not isinstance(value, numbers.Integral)
                        found_vars.append((candidate, is_float, False))
                        print(
                            f'[Scan] {candidate}: FOUND (value={value})'
                        )

                except Exception as e:
                    print(f'[Scan] Error reading variables: {e}')
                    import traceback

                    traceback.print_exc()

            print(
                f'[Scan] Finished: {len(found_vars)} variables found'
            )

            if not found_vars:
                return {"status": "no_vars"}

            # Clean and sort
            seen = set()
            clean_vars = []
            for entry in found_vars:
                name, is_float, is_boolean = _normalize_var_tuple(entry)
                if name in seen:
                    continue
                seen.add(name)
                clean_vars.append((name, is_float, is_boolean))

            clean_vars.sort(key=lambda x: x[0])

            detected_car, detected_track = self._detect_current_car_track()
            for _ in range(6):
                if detected_car and detected_track:
                    break
                time.sleep(0.05)
                detected_car, detected_track = self._detect_current_car_track()
            print(f'[Scan] Detected: {detected_car} @ {detected_track}')

            return {
                "status": "ok",
                "vars": clean_vars,
                "detected_car": detected_car,
                "detected_track": detected_track
            }
        except Exception as exc:
            print(f'[Scan] Fatal error: {exc}')
            import traceback

            traceback.print_exc()
            return {"status": "error", "error": str(exc)}


    def _finish_scan_driver_controls(
        self,
        result: Dict[str, Any],
        previous_pair: Tuple[str, str],
        fallback_tabs: Dict[str, Dict[str, Any]],
        fallback_combo: Dict[str, Any],
        silent_if_unavailable: bool,
        on_complete: Optional[Callable[[], None]]
    ) -> None:
        """Finalize scan results on the main UI thread."""
        self._scan_in_progress = False
        self._scan_started_at = 0.0
        status = result.get("status")
        if status != "no_vars":
            self._none_scan_attempts = 0

        if status == "unavailable":
            if not silent_if_unavailable:
                self._show_error(
                    "Error",
                    "Open iRacing (or join a session)."
                )
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return

        if status == "no_candidates":
            self._show_warning(
                "Scan",
                "The SDK has not returned variables yet.\nGet in the car with the Drive button, adjust the controls and try again."
            )
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return

        if status == "no_vars":
            if self._handle_none_scan_result():
                if on_complete:
                    on_complete()
                return
            self._show_warning(
                "Scan",
                "No numeric or boolean 'dc*' variable found.\nThe car may have no adjustable controls, or you are not in Drive mode."
            )
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return

        if status != "ok":
            print(f'[Scan] Unexpected error: {result.get('error')}')
            if not silent_if_unavailable:
                self._show_error(
                    "Scan",
                    "The scan failed. Try again once you are in a session."
                )
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return

        clean_vars = result["vars"]

        self._set_telemetry_active(True)
        self._cancel_continuous_scan_retry()
        self._refresh_controller_ir()

        # Update active variables and rebuild tabs only when the control set changed.
        missing_tabs = any(name not in self.tabs for name, _f, _b in clean_vars)
        if clean_vars != self.active_vars or missing_tabs:
            self.active_vars = clean_vars
            self.rebuild_tabs(self.active_vars)
        else:
            self.active_vars = clean_vars

        # Update preset for current car/track
        detected_car = result.get("detected_car", "")
        detected_track = result.get("detected_track", "")
        car = (
            detected_car
            or self.combo_car.get().strip()
            or self.current_car
            or "Generic Car"
        )
        track = (
            detected_track
            or self.combo_track.get().strip()
            or self.current_track
            or "Generic Track"
        )

        # Telemetry has to name the car and the track for this scan to belong to
        # them. When it cannot, the controls found here are still applied to the
        # tabs, but they are never filed under a guessed name: doing that wrote
        # one car's control set into another car's saved preset and told
        # auto-detection that the previous car/track had just been scanned, which
        # kept the application on the session the driver had already left.
        attributed = bool(detected_car and detected_track)
        if not attributed:
            print(
                f'[Scan] Telemetry did not report car/track; {len(clean_vars)} controls applied without saving a preset.'
            )

        self.current_car, self.current_track = car, track
        self.auto_fill_ui(car, track)
        scan_pair = (car.strip(), track.strip())
        if attributed:
            self._last_successful_scan_pair = scan_pair
            self._last_successful_scan_time = time.time()
            self._last_auto_pair = scan_pair
            self.auto_load_attempted.add(scan_pair)
        else:
            # Let the detection loop resolve the real pair on its next pass.
            self._last_auto_pair = ("", "")
            self._last_detected_pair = ("", "")
            self.auto_load_attempted.discard(scan_pair)
            self._apply_car_key_config(car)
            self.register_current_listeners()
            self.update_preset_ui()
            if on_complete:
                on_complete()
            return

        if car not in self.saved_presets:
            self.saved_presets[car] = {}

        self._adopt_legacy_track_preset(car, track)
        entry = self._ensure_track_surface_presets(car, track)
        surface_key = self._normalize_surface_label(
            entry.get("active_surface", self._selected_surface())
        )
        surface_data = entry["surface_presets"].setdefault(
            surface_key,
            self._default_surface_preset()
        )
        surface_data["active_vars"] = self.active_vars
        entry["surface_presets"][surface_key] = surface_data
        entry["active_surface"] = surface_key
        self.saved_presets[car][track] = entry

        # Overlay config
        if "_overlay" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay"] = \
                self.car_overlay_config.get(car, {})

        if "_overlay_feedback" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay_feedback"] = \
                self.car_overlay_feedback.get(
                    car, DEFAULT_OVERLAY_FEEDBACK.copy()
                )

        self.car_overlay_config[car] = self.saved_presets[car]["_overlay"]
        self.car_overlay_feedback[car] = self.saved_presets[car][
            "_overlay_feedback"
        ]
        self.overlay_tab.load_for_car(
            car,
            self._overlay_var_list(),
            self.car_overlay_config[car]
        )
        self._refresh_surface_dc_profile_rows()

        # Reload saved bindings/macros for this car/track so they remain active
        preset_data = entry["surface_presets"].get(surface_key, {})
        if preset_data.get("tabs") or preset_data.get("combo"):
            # Load preset will rebuild tabs with configs and re-register listeners
            self.load_specific_preset(car, track, surface=surface_key)
        else:
            # Even without saved presets, ensure any current bindings stay active.
            # If this rescan is for the same car/track, reuse inline config.
            if (car, track) == previous_pair:
                self._apply_inline_config(fallback_tabs, fallback_combo)
            self._apply_car_key_config(car)
            self.register_current_listeners()

        self.update_preset_ui()
        self.save_config()

        self.scans_since_restart += 1

        self._cancel_continuous_scan_retry()
        if self.scan_validator:
            self.scan_validator.reset()
        self._validation_failures = 0
        self._recovery_attempt = 0

        if self.show_scan_popup.get():
            self._show_info(
                "Scan",
                f"{len(clean_vars)} 'dc' controls set up for this car."
            )
        if on_complete:
            on_complete()


    def rebuild_tabs(
        self,
        vars_list: List[Tuple[str, bool, bool]],
        *,
        tab_configs: Optional[Dict[str, Dict[str, Any]]] = None,
        combo_config: Optional[Dict[str, Any]] = None
    ):
        """Rebuild control tabs with new variable list."""
        self._cancel_surface_dc_precision_targets(
            "controls rebuilt",
            clear_desired=True
        )
        tab_configs = tab_configs if isinstance(tab_configs, dict) else {}

        # Clear control notebook.
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)

        for tab in self.tabs.values():
            try:
                tab.destroy()
            except Exception:
                pass

        for frame in (self.combo_frame, self.overlay_frame):
            if frame is not None:
                try:
                    frame.destroy()
                except Exception:
                    pass

        self.controllers.clear()
        self.tabs.clear()
        self.combo_frame = None
        self.overlay_frame = None

        self.active_vars = [_normalize_var_tuple(item) for item in vars_list]

        # Create tabs for each variable
        for var_name, is_float, is_boolean in self.active_vars:
            display_name = format_driver_control_name(var_name)
            allow_dual_keys = var_name == "dcPitSpeedLimiterToggle"
            controller = GenericController(
                self.ir, 
                var_name, 
                is_float,
                is_boolean,
                allow_dual_keys,
                app_ref=self
            )
            self.controllers[var_name] = controller

            frame = tk.Frame(self.notebook)
            initial_config = tab_configs.get(var_name)
            tab_widget = ControlTab(
                frame, 
                controller, 
                display_name,
                self,
                create_default_rows=not bool(initial_config)
            )
            if initial_config:
                tab_widget.set_config(initial_config)
            tab_widget.pack(fill="both", expand=True)

            self.notebook.add(frame, text=display_name)
            self.tabs[var_name] = tab_widget

        # Combo page
        self.combo_frame = tk.Frame(self.combo_page_container)
        self.combo_tab = ComboTab(self.combo_frame, self.controllers, self)
        if combo_config:
            self.combo_tab.set_config(combo_config)
        self.combo_tab.pack(fill="both", expand=True)
        self.combo_frame.pack(fill="both", expand=True)

        # Overlay config page
        self.overlay_frame = tk.Frame(self.overlay_page_container)
        self.overlay_tab = OverlayConfigTab(self.overlay_frame, self)
        self.overlay_tab.pack(fill="both", expand=True)
        self.overlay_frame.pack(fill="both", expand=True)

        self._apply_discreet_mode()
        self._refresh_control_tab_labels()
        if self.surface_dc_profiles_enabled.get():
            self._surface_dc_state["force_reapply"] = True
        self.root.after_idle(self._refresh_control_tab_labels)

        # Load overlay for current car
        car = self.current_car or "Generic Car"

        if car not in self.saved_presets:
            self.saved_presets[car] = {}

        if "_overlay" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay"] = \
                self.car_overlay_config.get(car, {})
        if "_overlay_feedback" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay_feedback"] = \
                self.car_overlay_feedback.get(
                    car, DEFAULT_OVERLAY_FEEDBACK.copy()
                )

        self.car_overlay_config[car] = self.saved_presets[car]["_overlay"]
        self.car_overlay_feedback[car] = self.saved_presets[car]["_overlay_feedback"]
        self.overlay_tab.load_for_car(
            car,
            self._overlay_var_list(),
            self.car_overlay_config[car]
        )

        # Set editing state
        editing = (self.app_state == "CONFIG")
        for tab in self.tabs.values():
            tab.set_editing_state(editing)
        if self.combo_tab:
            self.combo_tab.set_editing_state(editing)
        self._refresh_surface_dc_profile_rows()
        self._update_surface_dc_editing_state()
        self._set_bounds_discovery_buttons_state(True)

        self.register_current_listeners()


    def _read_ir_value(self, key: str, use_cache: bool = True):
        """Safely read a telemetry key from the iRacing SDK."""
        # Check cache first
        if use_cache:
            hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=0.08)
            if hit:
                return cached

        if not _TELEMETRY_CIRCUIT_BREAKER.can_execute(key):
            hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=2.0)
            if hit:
                return cached
            return None

        on_ui_thread = threading.current_thread() is threading.main_thread()

        try:
            if not getattr(self.ir, "is_initialized", False):
                self._start_sdk_warmup()
                if on_ui_thread:
                    return None
                self.ir.startup()
            self._repair_sdk_header_cache_if_needed()
            value = self.ir[key]
            if value is not None:
                _TELEMETRY_CACHE.set(key, value)
            _TELEMETRY_CIRCUIT_BREAKER.record_success(key)
            return value
        except Exception:
            if on_ui_thread:
                self._start_sdk_warmup()
            _TELEMETRY_CIRCUIT_BREAKER.record_failure(key)
            return None


    def _batch_read_ir_values(self, keys: List[str]) -> Dict[str, Any]:
        """Read multiple telemetry keys efficiently in a single batch."""
        results: Dict[str, Any] = {}
        keys_to_read = []

        # Check cache for each key
        for key in keys:
            if not _TELEMETRY_CIRCUIT_BREAKER.can_execute(key):
                hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=2.0)
                results[key] = cached if hit else None
                continue
            hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=0.08)
            if hit:
                results[key] = cached
            else:
                keys_to_read.append(key)

        # Read remaining keys from SDK
        if keys_to_read:
            try:
                if not getattr(self.ir, "is_initialized", False):
                    self._start_sdk_warmup()
                    if threading.current_thread() is threading.main_thread():
                        for key in keys_to_read:
                            results[key] = None
                        return results
                    self.ir.startup()
                self._repair_sdk_header_cache_if_needed()

                fresh_values = {}
                for key in keys_to_read:
                    try:
                        value = self.ir[key]
                        results[key] = value
                        if value is not None:
                            fresh_values[key] = value
                        _TELEMETRY_CIRCUIT_BREAKER.record_success(key)
                    except Exception:
                        results[key] = None
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(key)

                # Batch update cache
                if fresh_values:
                    _TELEMETRY_CACHE.batch_set(fresh_values)

            except Exception:
                for key in keys_to_read:
                    results[key] = None

        return results


    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default


    def _bool_from_keys(self, keys: List[str], use_cache: bool = True) -> bool:
        """Return True if any telemetry key resolves to a truthy value."""

        for key in keys:
            value = self._read_ir_value(key, use_cache=use_cache)

            if isinstance(value, (list, tuple, array)):
                if any(bool(v) for v in value):
                    return True
            elif isinstance(value, numbers.Real):
                if float(value) != 0.0:
                    return True
            elif isinstance(value, bool) and value:
                return True

        return False


    def _read_push_to_pass_status(self) -> Optional[bool]:
        """Return current push-to-pass state from telemetry when available."""
        value = self._read_ir_value("P2P_Status")
        status = self._telemetry_value_bool(value)
        if status is None:
            value = self._read_ir_value("CarIdxP2P_Status")
            status = self._telemetry_value_bool(value, use_player_idx=True)
        return status


    def _telemetry_value_bool(
        self,
        value: Any,
        use_player_idx: bool = False
    ) -> Optional[bool]:
        """Normalize telemetry values into a boolean when possible."""
        if isinstance(value, (list, tuple, array)):
            if use_player_idx:
                idx_value = self._read_ir_value("PlayerCarIdx", use_cache=False)
                if isinstance(idx_value, numbers.Real):
                    idx = int(idx_value)
                    if 0 <= idx < len(value):
                        return bool(value[idx])
                return None
            return any(bool(v) for v in value)
        if isinstance(value, numbers.Real):
            return float(value) != 0.0
        if isinstance(value, bool):
            return value
        return None


    def _player_indexed_telemetry_value(self, value: Any) -> Any:
        """Return the current player's item from car-indexed telemetry arrays."""
        if not isinstance(value, (list, tuple, array)):
            return value
        idx_value = self._read_ir_value("PlayerCarIdx", use_cache=False)
        if isinstance(idx_value, numbers.Real):
            idx = int(idx_value)
            if 0 <= idx < len(value):
                return value[idx]
        return None


    def _telemetry_value_int(
        self,
        value: Any,
        use_player_idx: bool = False
    ) -> Optional[int]:
        """Normalize scalar or car-indexed telemetry values into an int."""
        if use_player_idx:
            value = self._player_indexed_telemetry_value(value)
        elif isinstance(value, (list, tuple, array)):
            if len(value) == 1:
                value = value[0]
            else:
                return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, numbers.Real):
            return int(value)
        return None


    def _pit_limiter_on_pit_road(self) -> Optional[bool]:
        """Return whether the player's car is on pit road between the cones."""
        for key, use_player_idx in (
            ("OnPitRoad", False),
            ("PlayerCarOnPitRoad", False),
            ("CarIdxOnPitRoad", True),
        ):
            value = self._read_ir_value(key, use_cache=False)
            parsed = self._telemetry_value_bool(
                value,
                use_player_idx=use_player_idx
            )
            if parsed is not None:
                return parsed
        return None


    def _pit_limiter_approaching_pit_road(self) -> Optional[bool]:
        """Return True when iRacing reports the pit road lead-in/speed zone."""
        for key, use_player_idx in (
            ("PlayerTrackSurface", False),
            ("CarIdxTrackSurface", True),
        ):
            value = self._read_ir_value(key, use_cache=False)
            track_location = self._telemetry_value_int(
                value,
                use_player_idx=use_player_idx
            )
            if track_location is None:
                continue
            return track_location == IRSDK_TRACK_LOCATION_APPROACHING_PITS
        return None


    def _player_on_track_car(self) -> Optional[bool]:
        """Return True only when the local player is driving their car."""
        value = self._read_ir_value("IsOnTrackCar", use_cache=False)
        return self._telemetry_value_bool(value, use_player_idx=True)


    def _pit_limiter_track_ok(self) -> bool:
        """Return False when IsOnTrack or IsOnTrackCar is explicitly false."""
        on_track = self._telemetry_value_bool(
            self._read_ir_value("IsOnTrack", use_cache=False),
            use_player_idx=True
        )
        on_track_car = self._telemetry_value_bool(
            self._read_ir_value("IsOnTrackCar", use_cache=False),
            use_player_idx=True
        )
        if on_track is False or on_track_car is False:
            return False
        return True


    def _command_context_block_reason(
        self,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> Optional[str]:
        """Return a short reason when replay mode should block macros."""
        replay = self._telemetry_value_bool(
            self._read_ir_value("IsReplayPlaying", use_cache=True),
            use_player_idx=True
        )
        if replay is True:
            return "replay active"
        return None


    def _commands_allowed(
        self,
        *,
        allow_caution: bool = False,
        allow_pace: bool = False,
        allow_pit_service: bool = False
    ) -> bool:
        """Return True when macros are allowed by the replay protection option."""
        if not self.block_off_track_commands.get():
            return True
        reason = self._command_context_block_reason(
            allow_caution=allow_caution,
            allow_pace=allow_pace,
            allow_pit_service=allow_pit_service
        )
        return reason is None


    def _push_overlay_alert(
        self, message: str, color: str, cfg: Dict[str, float], now: float
    ) -> None:
        """Send rate-limited feedback to the overlay status area."""

        state = self._overlay_feedback_state
        cooldown = max(0.5, float(cfg.get("cooldown_s", 6.0)))

        if (
            now - state.get("last_alert_time", 0.0) < cooldown
            and state.get("last_alert") == message
        ):
            return

        self.notify_overlay_status(message, color)
        state["last_alert"] = message
        state["last_alert_time"] = now


    def _update_overlay_feedback(self):
        """Analyze telemetry and surface ABS/TC/wheelspin hints on the HUD."""

        car = self.current_car or "Generic Car"
        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(self.car_overlay_feedback.get(car, {}))

        state = self._overlay_feedback_state
        now = time.time()
        dt = max(0.0, now - state.get("last_time", now))
        state["last_time"] = now

        abs_keys = [
            "BrakeABSactive",
            "BrakeABSActive",
            "BrakeABSActiveLF",
            "BrakeABSActiveRF",
            "BrakeABSActiveLR",
            "BrakeABSActiveRR",
        ]
        tc_keys = [
            "TractionControlActive",
            "TractionControlEngaged",
            "TCActive",
            "TractionControlOn",
        ]
        slip_keys = ["WheelSlip", "WheelSlipPct", "WheelSlipRatio", "TireSlip"]
        snapshot = self._batch_read_ir_values(
            ["Throttle", "Brake", *abs_keys, *tc_keys, *slip_keys]
        )

        def _truthy(keys: List[str]) -> bool:
            for key in keys:
                value = snapshot.get(key)
                if isinstance(value, (list, tuple, array)):
                    if any(bool(v) for v in value):
                        return True
                elif isinstance(value, numbers.Real):
                    if bool(value):
                        return True
                elif value:
                    return True
            return False

        throttle = self._safe_float(snapshot.get("Throttle"), 0.0)
        brake = self._safe_float(snapshot.get("Brake"), 0.0)
        abs_active = _truthy(abs_keys)
        tc_active = _truthy(tc_keys)

        slips: List[float] = []
        for key in slip_keys:
            value = snapshot.get(key)
            if isinstance(value, (list, tuple, array)):
                slips.extend([self._safe_float(v, 0.0) for v in value])
        max_slip = max(slips) if slips else 0.0
        min_slip = min(slips) if slips else 0.0

        if abs_active and brake > 0.05:
            state["abs_active"] += dt
        else:
            state["abs_active"] = 0.0

        if tc_active and throttle > 0.2:
            state["tc_active"] += dt
        else:
            state["tc_active"] = 0.0

        if throttle > 0.2 and max_slip >= cfg["wheelspin_slip"]:
            state["spin_active"] += dt
        else:
            state["spin_active"] = 0.0

        lock_threshold = -abs(cfg["lockup_slip"])
        if brake > 0.05 and slips and min_slip <= lock_threshold:
            state["lock_active"] += dt
        else:
            state["lock_active"] = 0.0

        if state["abs_active"] >= cfg["abs_hold_s"]:
            self._push_overlay_alert(
                "ABS active too long: ease off the brake or lower ABS.",
                "orange",
                cfg,
                now
            )
            state["abs_active"] = 0.0

        if state["tc_active"] >= cfg["tc_hold_s"]:
            self._push_overlay_alert(
                "TC constantly triggering: consider lowering TC or changing the map.",
                "orange",
                cfg,
                now
            )
            state["tc_active"] = 0.0

        if state["spin_active"] >= cfg["wheelspin_hold_s"]:
            self._push_overlay_alert(
                "Wheelspin detected: raise TC or modulate the throttle.",
                "orange",
                cfg,
                now
            )
            state["spin_active"] = 0.0

        if state["lock_active"] >= cfg["lockup_hold_s"]:
            self._push_overlay_alert(
                "Lock-up detected: increase ABS or ease pedal pressure.",
                "orange",
                cfg,
                now
            )
            state["lock_active"] = 0.0


    def open_timing_window(self):
        """Open timing configuration window."""
        GlobalTimingWindow(self.root, self.save_timing_config, self._popups_enabled)


    def save_timing_config(self, new_timing: Dict[str, Any]):
        """Save timing configuration."""
        GLOBAL_TIMING.update(_normalize_timing_config(new_timing))
        self.save_config()


    def _perform_pending_scan(self):
        """Execute a deferred scan request set before restarting."""
        pending_scan, silent_scan = consume_pending_scan()
        if pending_scan:
            self.pending_scan_on_start = True
            self.skip_session_scan_once = True
            self.skip_auto_scan_once = True
            self.skip_on_track_restart_once = True
            self._pending_scan_silent = silent_scan

        if self.pending_scan_on_start:
            self.skip_race_restart_once = True
            self.pending_scan_on_start = False
            self.save_config()
            self.root.after(
                50,
                lambda: self.scan_driver_controls(
                    silent_if_unavailable=self._pending_scan_silent,
                    allow_restart=False
                )
            )


    def _kick_initial_scan(self) -> None:
        """Run the first scan only once the SDK metadata exists."""
        if not self.keep_scanning_until_valid.get():
            return
        if self._scan_in_progress:
            if not self._reset_stale_scan_if_needed():
                self.root.after(700, self._kick_initial_scan)
                return
        if self._telemetry_ready_for_scan():
            self.scan_driver_controls(
                silent_if_unavailable=True,
                allow_restart=False
            )
            return
        self.root.after(700, self._kick_initial_scan)


    @staticmethod
    def _read_u32_le(data: bytes, offset: int) -> Optional[int]:
        """Read a little-endian uint32 from a bytes payload."""
        if offset < 0 or offset + 4 > len(data):
            return None
        return int.from_bytes(data[offset:offset + 4], "little", signed=False)


    @staticmethod
    def _normalize_setup_folder_key(value: Any) -> str:
        """Normalize a car/setup folder candidate for loose matching."""
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


    def _iracing_documents_folder(self) -> str:
        """Return the expected Documents/iRacing folder."""
        return os.path.join(os.path.expanduser("~"), "Documents", "iRacing")


    def _current_iracing_car_path(self) -> str:
        """Return the current iRacing setup folder name when SDK exposes it."""
        try:
            with self.ir_lock:
                driver_info = self.ir["DriverInfo"]
        except Exception:
            driver_info = None

        if not isinstance(driver_info, dict):
            return ""

        try:
            idx = int(driver_info.get("DriverCarIdx", -1))
        except Exception:
            idx = -1
        drivers = driver_info.get("Drivers", [])
        if not isinstance(drivers, list) or idx < 0 or idx >= len(drivers):
            return ""
        driver = drivers[idx] if isinstance(drivers[idx], dict) else {}

        for key in (
            "CarPath",
            "CarDir",
            "CarFolder",
            "CarFolderName",
            "CarName"
        ):
            raw = str(driver.get(key, "") or "").strip()
            if not raw:
                continue
            raw = raw.replace("\\", "/").rstrip("/")
            folder = raw.rsplit("/", 1)[-1].strip()
            if folder:
                return folder
        return ""


    def _find_setup_folder_for_car(self, car_name: str) -> str:
        """Best-effort fallback from app car name to setup folder."""
        setups_root = os.path.join(self._iracing_documents_folder(), "setups")
        if not os.path.isdir(setups_root):
            return ""

        wanted = self._normalize_setup_folder_key(car_name)
        if not wanted:
            return ""

        candidates: List[Tuple[int, str]] = []
        try:
            for entry in os.scandir(setups_root):
                if not entry.is_dir():
                    continue
                key = self._normalize_setup_folder_key(entry.name)
                if not key:
                    continue
                if key == wanted:
                    candidates.append((0, entry.name))
                elif key in wanted or wanted in key:
                    candidates.append((1, entry.name))
        except Exception:
            return ""

        if not candidates:
            return ""
        candidates.sort(key=lambda item: (item[0], len(item[1])))
        return candidates[0][1]


    def _select_iracing_controls_cfg(
        self,
        car_name: str
    ) -> Tuple[Optional[str], str, str]:
        """Return controls.cfg path, source label, and setup folder."""
        iracing_dir = self._iracing_documents_folder()
        setups_root = os.path.join(iracing_dir, "setups")
        car_folder = self._current_iracing_car_path()
        if not car_folder:
            car_folder = self._find_setup_folder_for_car(car_name)

        if car_folder:
            car_path = os.path.join(setups_root, car_folder, "controls.cfg")
            if os.path.isfile(car_path):
                return car_path, "car", car_folder

        global_path = os.path.join(iracing_dir, "controls.cfg")
        if os.path.isfile(global_path):
            return global_path, "global", car_folder

        return None, "", car_folder


    @staticmethod
    def _iracing_controls_record(
        data: bytes,
        control_name: str
    ) -> Optional[Dict[str, int]]:
        """Read a single controls.cfg record by control name."""
        token = control_name.encode("ascii", errors="ignore")
        if not token:
            return None

        start = 0
        while True:
            idx = data.find(token, start)
            if idx < 0:
                return None
            end = idx + len(token)
            if end < len(data) and data[end] == 0:
                fields = end + 1
                active = self._read_u32_le(data, fields)
                mode = self._read_u32_le(data, fields + 4)
                input_type = self._read_u32_le(data, fields + 8)
                value = self._read_u32_le(data, fields + 12)
                modifiers = self._read_u32_le(data, fields + 16)
                if (
                    active is not None
                    and mode is not None
                    and input_type is not None
                    and value is not None
                    and modifiers is not None
                ):
                    return {
                        "active": active,
                        "mode": mode,
                        "input_type": input_type,
                        "value": value,
                        "modifiers": modifiers,
                    }
            start = end


    def _parse_iracing_controls_cfg(
        self,
        path: str,
        wanted_names: Iterable[str]
    ) -> Dict[str, Any]:
        """Parse selected keyboard bindings from an iRacing controls.cfg file."""
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except Exception as exc:
            return {"error": str(exc), "bindings": {}, "unsupported": []}

        bindings: Dict[str, Dict[str, str]] = {}
        unsupported: List[str] = []
        empty: List[str] = []
        for name in sorted(set(str(item) for item in wanted_names if item)):
            record = self._iracing_controls_record(data, name)
            if not record:
                empty.append(name)
                continue

            input_type = int(record.get("input_type", 0))
            if input_type == IRACING_CONTROLS_CFG_KEYBOARD_TYPE:
                binding = _iracing_vk_binding(
                    int(record.get("value", 0)),
                    int(record.get("modifiers", 0))
                )
                unsafe_reason = _unsafe_system_binding_reason(binding)
                if binding and not unsafe_reason:
                    bindings[name] = {
                        "binding": binding,
                        "label": _format_game_binding_label(binding)
                    }
                elif unsafe_reason:
                    unsupported.append(
                        f'{name} ({unsafe_reason}; Windows system key)'
                    )
                else:
                    unsupported.append(f"{name} (VK {record.get('value')})")
            elif input_type == IRACING_CONTROLS_CFG_JOYSTICK_TYPE:
                unsupported.append(f'{name} (wheel button/axis)')
            else:
                empty.append(name)

        return {
            "bindings": bindings,
            "unsupported": unsupported,
            "empty": empty
        }


    def _iracing_control_base_name(self, var_name: str) -> str:
        """Convert a dc* telemetry name to an iRacing controls.cfg base name."""
        override = IRACING_CONTROL_BASE_OVERRIDES.get(var_name)
        if override:
            return override
        raw = str(var_name or "").strip()
        if raw.startswith("dc") and len(raw) > 2:
            raw = raw[2:]
        raw = (
            raw.replace("WindShield", "Windshield")
            .replace("windShield", "windshield")
            .replace("Toogle", "Toggle")
        )
        return raw


    def _iracing_control_binding_names(
        self,
        var_name: str,
        tab: "ControlTab"
    ) -> Tuple[List[str], List[str]]:
        """Return iRacing controls.cfg names for increase/decrease bindings."""
        override = IRACING_CONTROL_NAME_OVERRIDES.get(var_name)
        if override:
            inc_name, dec_name = override
            return [inc_name], [dec_name] if dec_name else []

        if _is_one_shot_control_name(
            var_name,
            getattr(getattr(tab, "controller", None), "is_boolean", False),
        ):
            lowered = var_name.lower()
            if "tearoff" in lowered or "tear_off" in lowered:
                return ["TearOffVisor"], []

        base = self._iracing_control_base_name(var_name)
        if getattr(tab, "uses_toggle_key", False):
            return [base], []
        return [f"{base}Inc"], [f"{base}Dec"]


    @staticmethod
    def _controls_cfg_signature(path: str) -> Optional[Tuple[int, int]]:
        """Return a stable signature for detecting controls.cfg updates."""
        try:
            stat = os.stat(path)
            return int(stat.st_mtime_ns), int(stat.st_size)
        except Exception:
            return None


    def _current_controls_sync_car(self) -> str:
        """Return the best current car name for controls.cfg auto-sync."""
        detected_car, _detected_track = self._detect_current_car_track()
        if detected_car:
            return detected_car
        try:
            combo_car = self.combo_car.get().strip()
        except Exception:
            combo_car = ""
        return combo_car or self.current_car or ""


    def _refresh_iracing_controls_watch_baseline(self) -> None:
        """Refresh the observed controls.cfg file without importing."""
        car = self._current_controls_sync_car()
        if not car:
            self._iracing_controls_watch_state.clear()
            return

        cfg_path, source_label, car_folder = self._select_iracing_controls_cfg(car)
        signature = (
            self._controls_cfg_signature(cfg_path)
            if cfg_path
            else None
        )
        if not cfg_path or signature is None:
            self._iracing_controls_watch_state.clear()
            return

        self._iracing_controls_watch_state.update({
            "car": car,
            "path": cfg_path,
            "source": source_label,
            "folder": car_folder,
            "signature": signature
        })


    def _schedule_iracing_controls_auto_import(self) -> None:
        """Debounce automatic controls.cfg imports."""
        if self._iracing_controls_import_job:
            try:
                self.root.after_cancel(self._iracing_controls_import_job)
            except Exception:
                pass
        self._iracing_controls_import_job = self.root.after(
            IRACING_CONTROLS_IMPORT_DELAY_MS,
            self._run_iracing_controls_auto_import
        )


    def _run_iracing_controls_auto_import(self) -> None:
        """Import updated iRacing controls after the file settles."""
        self._iracing_controls_import_job = None
        if not self.auto_sync_iracing_controls.get():
            return
        if not self.tabs:
            return

        try:
            self.import_iracing_controls_for_current_car(silent=True)
        finally:
            self._refresh_iracing_controls_watch_baseline()


    def _check_iracing_controls_cfg_update(self) -> None:
        """Detect updates to the current car controls.cfg source."""
        if not self.auto_sync_iracing_controls.get():
            return
        if not self.tabs:
            return

        car = self._current_controls_sync_car()
        if not car:
            return

        cfg_path, source_label, car_folder = self._select_iracing_controls_cfg(car)
        if not cfg_path:
            self._iracing_controls_watch_state.clear()
            return

        signature = self._controls_cfg_signature(cfg_path)
        if signature is None:
            self._iracing_controls_watch_state.clear()
            return

        state = self._iracing_controls_watch_state
        previous_car = state.get("car")
        previous_path = state.get("path")
        previous_signature = state.get("signature")

        changed = False
        if previous_car and previous_car == car:
            if previous_path and previous_path != cfg_path:
                changed = True
            elif previous_signature is not None and previous_signature != signature:
                changed = True

        state.update({
            "car": car,
            "path": cfg_path,
            "source": source_label,
            "folder": car_folder,
            "signature": signature
        })

        if changed:
            self._schedule_iracing_controls_auto_import()


    def _iracing_controls_auto_sync_loop(self) -> None:
        """Watch iRacing controls.cfg and auto-sync changed bindings."""
        try:
            self._check_iracing_controls_cfg_update()
        except Exception as exc:
            print(f'[iRacing controls] Monitoring failed: {exc}')
        self.root.after(
            IRACING_CONTROLS_WATCH_INTERVAL_MS,
            self._iracing_controls_auto_sync_loop
        )


    def import_iracing_controls_for_current_car(self, silent: bool = False) -> bool:
        """Import keyboard bindings from iRacing controls.cfg for current car."""
        if not self.tabs:
            if not silent:
                self._show_warning(
                    "Sync iRacing",
                    "Scan the car's controls before importing hotkeys."
                )
            return False

        detected_car, detected_track = self._detect_current_car_track()
        car = (
            detected_car
            or self.combo_car.get().strip()
            or self.current_car
        )
        if not car:
            if not silent:
                self._show_warning(
                    "Sync iRacing",
                    "Could not identify the current car."
                )
            return False
        if detected_car:
            self.current_car = detected_car
            if detected_track:
                self.current_track = detected_track
                self.auto_fill_ui(detected_car, detected_track)

        cfg_path, source_label, car_folder = self._select_iracing_controls_cfg(car)
        if not cfg_path:
            if not silent:
                self._show_warning(
                    "Sync iRacing",
                    "controls.cfg not found in Documents\\iRacing."
                )
            return False

        wanted: set = set()
        names_by_var: Dict[str, Tuple[List[str], List[str]]] = {}
        for var_name, tab in self.tabs.items():
            names = self._iracing_control_binding_names(var_name, tab)
            names_by_var[var_name] = names
            wanted.update(names[0])
            wanted.update(names[1])

        parsed = self._parse_iracing_controls_cfg(cfg_path, wanted)
        if parsed.get("error"):
            if not silent:
                self._show_warning(
                    "Sync iRacing",
                    f'Could not read controls.cfg:\n{parsed['error']}'
                )
            return False

        bindings = parsed.get("bindings", {})
        applied: List[str] = []
        missing: List[str] = []
        for var_name, tab in self.tabs.items():
            inc_names, dec_names = names_by_var.get(var_name, ([], []))
            inc_entry = next(
                (bindings[name] for name in inc_names if name in bindings),
                None
            )
            dec_entry = next(
                (bindings[name] for name in dec_names if name in bindings),
                None
            )

            config: Dict[str, Any] = {}
            if inc_entry:
                config["key_increase"] = inc_entry["binding"]
                config["key_increase_text"] = f"OK: {inc_entry['label']}"
            if dec_entry and not getattr(tab, "uses_toggle_key", False):
                config["key_decrease"] = dec_entry["binding"]
                config["key_decrease_text"] = f"OK: {dec_entry['label']}"

            if config:
                tab.apply_key_config(config)
                applied.append(tab.label_name)
            elif inc_names or dec_names:
                missing.append(tab.label_name)

        if not applied:
            unsupported = parsed.get("unsupported", [])
            detail = ""
            if unsupported:
                detail = (
                    "\n\nSome controls exist in the file but sit on a wheel button/axis or a VK that cannot be imported."
                )
            if not silent:
                self._show_warning(
                    "Sync iRacing",
                    "No importable keyboard shortcut was found for the current tabs."
                    + detail
                )
            return False

        self._collect_car_key_config(car)
        self.register_current_listeners()
        self.save_config()

        unsupported_count = len(parsed.get("unsupported", []))
        missing_count = len(missing)
        source_text = (
            "car file"
            if source_label == "car"
            else "global file"
        )
        folder_text = f'\nCar folder: {car_folder}' if car_folder else ""
        report = (
            f'Imported {len(applied)} control(s) for {car}.\nSource: {source_text}\nFile: {cfg_path}{folder_text}\n\nNot found in the current tabs: {missing_count}\nNot importable because they are joystick/unknown VK: {unsupported_count}'
        )
        if silent:
            self.notify_overlay_status(
                f'iRacing hotkeys updated ({len(applied)})',
                "green"
            )
            print(f"[iRacing Controls] Auto-sync: {len(applied)} imported from {cfg_path}")
        else:
            self.notify_overlay_status(
                f'iRacing hotkeys imported ({len(applied)})',
                "green"
            )
            self._show_info("Sync iRacing", report)
        return True


    @staticmethod
    def track_label_from_weekend(weekend: Optional[Dict[str, Any]]) -> str:
        """Name a track by its layout, not only by the circuit.

        Most circuits publish several layouts under one ``TrackDisplayName``.
        Keeping presets under that name alone made every layout of a circuit
        share a single set of controls, so a preset saved on the oval came back
        on the road course. ``TrackConfigName`` is what tells them apart, and it
        is only appended when it adds something the display name does not
        already say.
        """
        if not isinstance(weekend, dict):
            return ""
        display = str(weekend.get("TrackDisplayName") or "").strip()
        config = str(
            weekend.get("TrackConfigName") or weekend.get("TrackConfig") or ""
        ).strip()
        if not display or not config:
            return display
        if config.lower() in {"-", "n/a", "none", "default"}:
            return display
        if config.casefold() in display.casefold():
            return display
        return f"{display} - {config}"

    def _legacy_track_label(self, track: str) -> str:
        """Return the pre-layout name this label was stored under."""
        return str(track).split(" - ", 1)[0].strip()

    def _adopt_legacy_track_preset(self, car: str, track: str) -> None:
        """Carry a preset saved before layouts were told apart onto its layout.

        Presets saved by earlier versions live under the bare circuit name. The
        first time that circuit is seen with a layout, the saved entry moves to
        the new name so no configuration is lost; a circuit with a single layout
        never reaches this because its label does not change.
        """
        car_presets = self.saved_presets.get(car)
        if not isinstance(car_presets, dict) or track in car_presets:
            return
        legacy = self._legacy_track_label(track)
        if legacy == track or legacy not in car_presets:
            return
        car_presets[track] = car_presets.pop(legacy)
        print(f"[Preset] {car}: '{legacy}' changed to '{track}'.")

    def _detect_current_car_track(self) -> Tuple[str, str]:
        """Detect current car/track names from the iRacing SDK, if available."""
        raw_car = ""
        raw_track = ""

        try:
            driver_info = self.ir["DriverInfo"]
            if driver_info:
                idx = driver_info.get("DriverCarIdx")
                if idx is not None:
                    try:
                        idx = int(idx)
                    except (TypeError, ValueError):
                        idx = None
                if idx is not None:
                    raw_car = driver_info["Drivers"][idx]["CarScreenName"]
        except Exception:
            pass

        try:
            raw_track = self.track_label_from_weekend(self.ir["WeekendInfo"])
        except Exception:
            pass

        if not raw_car and not raw_track:
            return "", ""

        car_clean = "".join(
            c for c in raw_car
            if c.isalnum() or c in " -_"
        ).strip()
        track_clean = "".join(
            c for c in raw_track
            if c.isalnum() or c in " -_"
        ).strip()
        return car_clean, track_clean


__all__ = ['TelemetryMixin']
