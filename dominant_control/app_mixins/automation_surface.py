from __future__ import annotations

from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *

class SurfaceAutomationMixin:
    def toggle_surface_preset(self):
        """Toggle between DRY/WET presets for the active track."""
        car = (self.current_car or self.combo_car.get().strip()).strip()
        track = (self.current_track or self.combo_track.get().strip()).strip()
        if not car or not track:
            return

        current = self._selected_surface()
        next_surface = "WET" if current == "DRY" else "DRY"
        self._set_active_surface(
            car,
            track,
            next_surface,
            load=True,
            notify=True
        )


    def set_surface_preset(self, surface: str) -> None:
        """Switch to a specific surface preset for the active track."""
        car = (self.current_car or self.combo_car.get().strip()).strip()
        track = (self.current_track or self.combo_track.get().strip()).strip()
        if not car or not track:
            return

        surface_key = self._normalize_surface_label(surface)
        self._set_active_surface(
            car,
            track,
            surface_key,
            load=True,
            notify=True
        )


    def _surface_dc_targets_for_profile(
        self,
        surface: str,
        car: Optional[str] = None
    ) -> Dict[str, float]:
        """Return validated targets from one independent per-car profile."""

        surface_key = self._normalize_surface_label(surface)
        field = "dry" if surface_key == "DRY" else "wet"
        car_key = str(car or self._surface_dc_current_car()).strip()
        if not car_key:
            return {}
        if car_key == self._surface_dc_rows_car:
            self._collect_surface_dc_profile_rows()
        profile = self._ensure_surface_dc_car_profile(car_key)
        controls_cfg = profile.get("controls", {})
        targets: Dict[str, float] = {}
        for var_name, raw_cfg in controls_cfg.items():
            if not isinstance(raw_cfg, dict):
                continue
            controller = self.controllers.get(var_name)
            if controller is None:
                continue
            if not _is_surface_dc_profile_control(
                var_name,
                controller.is_boolean
            ):
                continue
            raw_value = str(raw_cfg.get(field, "") or "").strip()
            if not raw_value:
                continue
            try:
                target = float(raw_value.replace(",", "."))
            except Exception:
                continue
            if not math.isfinite(target):
                continue
            if not controller.is_float:
                target = int(round(target))
            targets[var_name] = target
        return targets


    def _queue_surface_dc_targets(
        self,
        targets: Dict[str, float],
        *,
        reason: str,
        allow_pit_service: bool = False
    ) -> int:
        """Queue a set of persistent values and return the number accepted."""

        if self.app_state != "RUNNING":
            return 0
        queued = 0
        accepted_targets: Dict[str, float] = {}
        for var_name, target in targets.items():
            controller = self.controllers.get(var_name)
            if controller is None:
                continue
            if not _is_surface_dc_profile_control(
                var_name,
                controller.is_boolean
            ):
                continue
            controller.request_target_with_context(
                target,
                allow_pit_service=allow_pit_service,
                precision_persistent=True
            )
            accepted_targets[var_name] = target
            queued += 1

        if queued:
            state = self._surface_dc_state
            state["desired_targets"] = accepted_targets
            state["desired_allow_pit_service"] = bool(allow_pit_service)
            state["desired_reason"] = reason
            state["precision_started_at"] = time.time()
            state["last_precision_supervision"] = 0.0
            print(f'[Auto Surface] {reason}: {queued} target(s) queued')
        return queued


    def _cancel_surface_dc_precision_targets(
        self,
        reason: str,
        *,
        clear_desired: bool = True
    ) -> int:
        """Cancel only dry/wet workers, leaving ordinary macros untouched."""
        cancelled = 0
        for controller in list(self.controllers.values()):
            if controller.clear_precision_target():
                cancelled += 1

        if clear_desired:
            state = self._surface_dc_state
            state["desired_targets"] = {}
            state["desired_profile"] = None
            state["desired_allow_pit_service"] = False
            state["desired_reason"] = ""
            state["precision_started_at"] = 0.0

        if cancelled:
            print(
                f'[Auto Surface] {reason}: {cancelled} precision target(s) cancelled'
            )
        return cancelled


    def _supervise_surface_dc_precision_targets(
        self,
        *,
        in_car: Optional[bool]
    ) -> None:
        """Confirm the whole batch and requeue any interrupted value."""
        state = self._surface_dc_state
        desired = dict(state.get("desired_targets") or {})
        if not desired:
            return
        if (
            not self.surface_dc_profiles_enabled.get()
            or self.app_state != "RUNNING"
            or in_car is not True
        ):
            return

        confirmed = 0
        requeued = 0
        allow_pit_service = bool(state.get("desired_allow_pit_service", False))
        for var_name, target in desired.items():
            controller = self.controllers.get(var_name)
            if controller is None:
                continue

            current = controller.read_telemetry(use_cache=False)
            is_confirmed = (
                controller.precision_target_confirmed(target)
                and controller._target_reached(current, target)
            )
            if is_confirmed:
                confirmed += 1
                continue

            # A newer ordinary macro, a worker exception or a transient SDK
            # failure must not silently abandon the requested surface profile.
            if not controller.has_precision_target(target):
                controller.request_target_with_context(
                    target,
                    allow_pit_service=allow_pit_service,
                    precision_persistent=True
                )
                requeued += 1

        total = len(desired)
        profile = str(
            state.get("desired_profile")
            or state.get("active_profile")
            or ""
        ).upper()
        label = (
            "DRY"
            if profile == "DRY"
            else "WET"
            if profile == "WET"
            else "DC"
        )
        if confirmed >= total:
            state["desired_targets"] = {}
            state["desired_profile"] = None
            state["desired_allow_pit_service"] = False
            state["desired_reason"] = ""
            state["precision_started_at"] = 0.0
            if state.get("entry_guard_profile") == profile:
                state["entry_guard_until"] = (
                    time.time() + SURFACE_DC_ENTRY_GUARD_S
                )
                state["entry_guard_last_check"] = 0.0
            self.surface_dc_active_status_var.set(
                f'Profile {label} confirmed • {total}/{total} controls'
            )
            self.notify_overlay_status(
                f'DC {label}: all {total} values confirmed',
                "green"
            )
            return

        retry_text = f' • {requeued} retried' if requeued else ""
        self.surface_dc_active_status_var.set(
            f'Adjusting {label} • {confirmed}/{total} differences confirmed{retry_text}'
        )


    def apply_surface_dc_profile(
        self,
        surface: str,
        *,
        source: str = "manual",
        allow_pit_service: bool = False
    ) -> bool:
        """Apply one dry/wet value profile without loading any macro preset."""

        if threading.current_thread() is not threading.main_thread():
            self._ui_after(
                self.apply_surface_dc_profile,
                surface,
                source=source,
                allow_pit_service=allow_pit_service
            )
            return True

        surface_key = self._normalize_surface_label(surface)
        if not self.surface_dc_profiles_enabled.get():
            self.surface_dc_active_status_var.set(
                "Quick profiles are off; turn the option on in this tab."
            )
            return False
        if self.app_state != "RUNNING":
            self.surface_dc_active_status_var.set(
                "Switch to RUNNING mode to apply a profile."
            )
            return False
        if self._player_on_track_car() is not True:
            self.surface_dc_active_status_var.set(
                "Profile not applied: you have to be in the car."
            )
            return False

        state = self._surface_dc_state
        car = self._surface_dc_current_car()
        targets = self._surface_dc_targets_for_profile(surface_key, car)
        if not targets:
            label = "DRY" if surface_key == "DRY" else "WET"
            self.surface_dc_active_status_var.set(
                f'Profile {label} has no values filled in for this car.'
            )
            return False

        # The profile is a desired state, not a macro that must always replay.
        # Read the local player's live DC values and queue only what differs.
        different_targets: Dict[str, float] = {}
        for var_name, target in targets.items():
            controller = self.controllers.get(var_name)
            if controller is None:
                continue
            current = controller.read_telemetry(use_cache=False)
            if current is None or not controller._target_reached(current, target):
                different_targets[var_name] = target

        self._cancel_surface_dc_precision_targets(
            "a new profile was requested",
            clear_desired=True
        )
        now = time.time()
        state["active_profile"] = surface_key
        state["active_profile_car"] = car
        state["pending_profile"] = None
        state["last_apply_time"] = now
        state["last_apply_reason"] = source
        label = "DRY" if surface_key == "DRY" else "WET"

        if not different_targets:
            self.surface_dc_active_status_var.set(
                f"Profile {label} verified • the car's values already match"
            )
            return True

        queued = self._queue_surface_dc_targets(
            different_targets,
            reason=f'profile {surface_key} ({source})',
            allow_pit_service=allow_pit_service
        )
        if not queued:
            self.surface_dc_active_status_var.set(
                "Nothing sent: check the scan and the +/− keys of the controls."
            )
            return False

        state["desired_profile"] = surface_key
        self.surface_dc_active_status_var.set(
            f'Adjusting {label} • 0/{queued} differences confirmed • {source}'
        )
        color = "#a86b00" if surface_key == "DRY" else "#1677a8"
        self.notify_overlay_status(
            f'DC {label}: {queued} controls',
            color
        )
        return True


    def _surface_dc_endurance_surface(
        self,
        declared_wet: Optional[bool]
    ) -> str:
        """Resolve the user's selected stint profile.

        The chain never ends in "no idea": after the mode and the declared
        condition come the last profile actually applied, then the condition
        chosen on the Main tab, and only then DRY.
        """

        mode = str(self.surface_dc_endurance_mode.get() or "AUTO").upper()
        if mode in SURFACE_DC_PROFILE_KEYS:
            return mode
        if declared_wet is True:
            return "WET"
        if declared_wet is False:
            return "DRY"
        state = self._surface_dc_state
        active = state.get("active_profile")
        if active in SURFACE_DC_PROFILE_KEYS:
            return str(active)
        last_declared = state.get("last_declared_wet")
        if last_declared is True:
            return "WET"
        if last_declared is False:
            return "DRY"
        return self._normalize_surface_label(
            getattr(self, "current_surface", "") or "DRY"
        )


    def _surface_dc_start_stint(
        self,
        reason: str,
        *,
        now: Optional[float] = None
    ) -> None:
        """Open a new application window for the local driver's car.

        A stint starts when the driver enters the car, when the session or the
        car changes, and when a setting asks for a re-check.  This bookkeeping
        stays outside the "enabled and RUNNING" gate on purpose: the driver may
        enter the car while the app is in CONFIG, or before the telemetry is
        ready, and the profile still has to be applied afterwards — instead of
        the entry being consumed by a cycle that could not act on it.
        """

        state = self._surface_dc_state
        state["stint_id"] = int(state.get("stint_id", 0)) + 1
        state["stint_started_at"] = time.time() if now is None else now
        state["stint_reason"] = reason
        state["applied_signature"] = None
        state["stint_profile"] = None
        state["stint_profile_source"] = ""
        state["next_attempt"] = 0.0
        state["waiting_reason"] = ""
        state["entry_guard_profile"] = None
        state["entry_guard_until"] = 0.0
        state["entry_guard_last_check"] = 0.0


    def _surface_dc_desired_profile(
        self,
        declared_wet: Optional[bool],
        *,
        now: float
    ) -> Tuple[Optional[str], str, str]:
        """Return (profile, source, waiting) for the current settings.

        ``profile`` is what should be in the car right now; ``waiting`` is
        filled only while the automation is on and still has no condition to
        act on, and it is what the interface shows meanwhile.
        """

        state = self._surface_dc_state
        if self.surface_dc_endurance_enabled.get():
            mode = str(
                self.surface_dc_endurance_mode.get() or "AUTO"
            ).upper()
            if mode in SURFACE_DC_PROFILE_KEYS:
                return mode, "this session's fixed profile", ""
            if declared_wet is not None:
                return (
                    "WET" if declared_wet else "DRY",
                    "the session's declared condition",
                    ""
                )
            elapsed = now - float(state.get("stint_started_at", 0.0))
            if elapsed < SURFACE_DC_DECLARED_GRACE_S:
                return None, "", "waiting for the SDK's DRY/WET condition..."
            return (
                self._surface_dc_endurance_surface(declared_wet),
                "known condition (the SDK did not declare one)",
                ""
            )
        if (
            self.surface_dc_auto_declared_wet.get()
            and declared_wet is not None
        ):
            return (
                "WET" if declared_wet else "DRY",
                "the session's declared condition",
                ""
            )
        return None, "", ""


    def _reset_surface_dc_runtime(self, *, keep_token: bool = True) -> None:
        """Clear only transient surface/endurance state."""

        state = self._surface_dc_state
        token = state.get("session_token") if keep_token else None
        last_enabled = bool(state.get("last_enabled", False))
        state.update(
            {
                "session_token": token,
                "last_declared_wet": None,
                "last_in_car": None,
                "active_profile": None,
                "active_profile_car": "",
                "pending_profile": None,
                "last_apply_time": 0.0,
                "last_apply_reason": "",
                "force_reapply": False,
                "endurance_active": False,
                "last_enabled": last_enabled,
                "desired_targets": {},
                "desired_profile": None,
                "desired_allow_pit_service": False,
                "desired_reason": "",
                "precision_started_at": 0.0,
                "last_precision_supervision": 0.0,
                "stint_id": int(state.get("stint_id", 0)) + 1,
                "stint_started_at": time.time(),
                "stint_reason": "session or car changed",
                "applied_signature": None,
                "stint_profile": None,
                "stint_profile_source": "",
                "next_attempt": 0.0,
                "waiting_reason": "",
                "entry_guard_profile": None,
                "entry_guard_until": 0.0,
                "entry_guard_last_check": 0.0,
            }
        )


    def _surface_dc_profile_loop(self) -> None:
        """Keep the local driver's car matching the profile it should have.

        The cycle reconciles instead of reacting to edges.  Entering the car,
        a session or car change and a settings change all open an application
        window; while the profile that window wants is not applied, every cycle
        tries again.  An entry that lands in CONFIG mode, before the car name
        is known or before the scan finishes is therefore not lost — it is
        applied as soon as the app can act, instead of the edge being consumed
        by a cycle that could do nothing with it.

        The automatic Dry/Wet profile is one of the two automations the public
        edition keeps, so both editions run this same cycle.
        """

        interval_ms = 200
        try:
            state = self._surface_dc_state
            now = time.time()
            enabled = bool(self.surface_dc_profiles_enabled.get())
            previous_enabled = bool(state.get("last_enabled", False))
            if enabled != previous_enabled:
                self._cancel_surface_dc_precision_targets(
                    "profile settings changed",
                    clear_desired=True
                )
                state["last_enabled"] = enabled
                self._reset_surface_dc_runtime(keep_token=True)
                state["last_enabled"] = enabled
                state["force_reapply"] = enabled
                if not enabled:
                    self.surface_dc_active_status_var.set(
                        "Quick profiles are off"
                    )
                    self.surface_dc_endurance_status_var.set(
                        "Car entry: automation off"
                    )

            session_unique_id = self._read_ir_value(
                "SessionUniqueID",
                use_cache=True
            )
            token = (
                session_unique_id,
                getattr(self, "_last_weekend_key", None),
                self._last_session_id,
                self.last_session_num,
                self._surface_dc_current_car(),
            )
            if token != state.get("session_token"):
                self._cancel_surface_dc_precision_targets(
                    "session or car changed",
                    clear_desired=True
                )
                state["session_token"] = token
                self._reset_surface_dc_runtime(keep_token=True)
                state["last_enabled"] = enabled
                state["force_reapply"] = enabled

            declared_raw = self._read_ir_value(
                "WeatherDeclaredWet",
                use_cache=False
            )
            if declared_raw is None:
                # Compatibility fallback for SDK wrappers that expose the old
                # alias instead of iRacing's WeatherDeclaredWet variable.
                declared_raw = self._read_ir_value(
                    "SessionDeclaredWet",
                    use_cache=False
                )
            declared_wet = self._telemetry_value_bool(declared_raw)
            if declared_wet is True:
                self.surface_dc_declared_status_var.set(
                    "Declared condition: WET"
                )
            elif declared_wet is False:
                self.surface_dc_declared_status_var.set(
                    "Declared condition: DRY"
                )
            else:
                self.surface_dc_declared_status_var.set(
                    "Declared condition: unavailable in this session"
                )

            in_car = self._player_on_track_car()
            on_pit = self._pit_limiter_on_pit_road()
            last_in_car = state.get("last_in_car")
            entered_car = in_car is True and last_in_car is not True
            left_car = in_car is False and last_in_car is True
            if in_car is not None:
                state["last_in_car"] = in_car

            # Entry opens an application window; it is not an event that is
            # lost when this cycle cannot act on it.
            if entered_car:
                # IsOnTrackCar is the local driver's state in the SDK. A teammate
                # driving the team car does not open the window.
                self._surface_dc_start_stint("the driver entering the car", now=now)
                self.surface_dc_endurance_status_var.set(
                    "Car entry detected: checking car, condition and values..."
                )

            if left_car:
                had_desired = bool(state.get("desired_targets"))
                cancelled = self._cancel_surface_dc_precision_targets(
                    "the driver left the car",
                    clear_desired=True
                )
                if cancelled or had_desired:
                    self.surface_dc_active_status_var.set(
                        "Application stopped safely: you left the car."
                    )
                state["entry_guard_profile"] = None
                state["entry_guard_until"] = 0.0
                state["entry_guard_last_check"] = 0.0
                if state.get("endurance_active"):
                    state["endurance_active"] = False
                    self.surface_dc_endurance_status_var.set(
                        "Car entry: driver out of the car; no command will be sent."
                    )

            if (
                state.get("desired_targets")
                and (not enabled or self.app_state != "RUNNING")
            ):
                self._cancel_surface_dc_precision_targets(
                    "automation unavailable",
                    clear_desired=True
                )

            if bool(state.get("force_reapply")):
                state["force_reapply"] = False
                state["applied_signature"] = None
                state["next_attempt"] = 0.0

            desired, desired_source, waiting = self._surface_dc_desired_profile(
                declared_wet,
                now=now
            )
            if declared_wet is not None:
                state["last_declared_wet"] = declared_wet
            # "When you get in the car" and "follow the declared condition" are
            # different options: without the second one, the profile chosen on
            # entry holds for the whole window, even when the session changes
            # condition later.
            frozen = state.get("stint_profile")
            if (
                desired is not None
                and frozen in SURFACE_DC_PROFILE_KEYS
                and not self.surface_dc_auto_declared_wet.get()
            ):
                desired = str(frozen)
                desired_source = str(
                    state.get("stint_profile_source") or desired_source
                )
            state["pending_profile"] = desired

            # Reconciliation: while the wanted profile is not applied in this
            # window, the cycle tries again. That covers entering the car, the
            # declared condition changing, the car name arriving late, the scan
            # that has not finished yet and the trip back from CONFIG to
            # RUNNING — all through the same path.
            signature = (int(state.get("stint_id", 0)), desired)
            if enabled and self.app_state == "RUNNING" and in_car is True:
                if desired is None:
                    if waiting and state.get("waiting_reason") != waiting:
                        state["waiting_reason"] = waiting
                        self.surface_dc_endurance_status_var.set(
                            f'Car entry: {waiting}'
                        )
                elif (
                    state.get("applied_signature") != signature
                    and now >= float(state.get("next_attempt", 0.0))
                ):
                    applied = self.apply_surface_dc_profile(
                        desired,
                        source=desired_source,
                        allow_pit_service=on_pit is True
                    )
                    label = "DRY" if desired == "DRY" else "WET"
                    if applied:
                        state["applied_signature"] = signature
                        state["stint_profile"] = desired
                        state["stint_profile_source"] = desired_source
                        state["waiting_reason"] = ""
                        state["endurance_active"] = bool(
                            self.surface_dc_endurance_enabled.get()
                        )
                        state["entry_guard_profile"] = desired
                        state["entry_guard_last_check"] = 0.0
                        if state.get("desired_targets"):
                            state["entry_guard_until"] = 0.0
                            result = "adjusting only the differences"
                        else:
                            state["entry_guard_until"] = (
                                now + SURFACE_DC_ENTRY_GUARD_S
                            )
                            result = "the values already match"
                        self.surface_dc_endurance_status_var.set(
                            f'Profile {label} checked by {desired_source}; {result}.'
                        )
                    else:
                        # The car name, the scanned controls and the profile rows may only
                        # be ready after IsOnTrackCar turns true. The window stays open
                        # until then.
                        state["next_attempt"] = now + SURFACE_DC_ENTRY_RETRY_S
                        self.surface_dc_endurance_status_var.set(
                            f"Profile {label}: waiting for this car's values and controls..."
                        )

                guard_profile = state.get("entry_guard_profile")
                guard_until = float(state.get("entry_guard_until", 0.0))
                if (
                    guard_profile in SURFACE_DC_PROFILE_KEYS
                    and guard_until > 0.0
                    and now <= guard_until
                    and not state.get("desired_targets")
                    and now - float(
                        state.get("entry_guard_last_check", 0.0)
                    ) >= SURFACE_DC_ENTRY_GUARD_INTERVAL_S
                ):
                    state["entry_guard_last_check"] = now
                    guard_targets = self._surface_dc_targets_for_profile(
                        str(guard_profile)
                    )
                    drift_found = False
                    for var_name, target in guard_targets.items():
                        controller = self.controllers.get(var_name)
                        if controller is None:
                            continue
                        current = controller.read_telemetry(use_cache=False)
                        if (
                            current is not None
                            and not controller._target_reached(current, target)
                        ):
                            drift_found = True
                            break
                    if drift_found:
                        # The simulator changed the values after entry: reopen the
                        # application instead of applying from outside it.
                        state["entry_guard_until"] = 0.0
                        state["applied_signature"] = None
                        state["next_attempt"] = 0.0
                elif guard_until > 0.0 and now > guard_until:
                    state["entry_guard_profile"] = None
                    state["entry_guard_until"] = 0.0

            if (
                now - float(state.get("last_precision_supervision", 0.0))
                >= 0.7
            ):
                state["last_precision_supervision"] = now
                self._supervise_surface_dc_precision_targets(in_car=in_car)

            if now - float(state.get("last_ui_refresh", 0.0)) >= 0.7:
                state["last_ui_refresh"] = now
                self._update_surface_dc_current_values()
        except Exception as exc:
            print(f'[Auto Surface] Error in the processing cycle: {exc}')
        finally:
            self.root.after(interval_ms, self._surface_dc_profile_loop)


__all__ = ['SurfaceAutomationMixin']
