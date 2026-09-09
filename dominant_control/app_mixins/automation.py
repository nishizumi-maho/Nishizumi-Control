from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *
from .automation_lapdist import LapDistAutomationMixin
from .automation_pit_weather import PitWeatherAutomationMixin
from .automation_fuel_hybrid import FuelHybridAutomationMixin
from .automation_surface import SurfaceAutomationMixin

class AutomationMixin(LapDistAutomationMixin, PitWeatherAutomationMixin, FuelHybridAutomationMixin, SurfaceAutomationMixin):

    def _refresh_overlay_discreet_state(self) -> None:
        car = self.current_car or "Generic Car"
        config = self.car_overlay_config.get(car, {})
        self.overlay.rebuild_monitor(self._overlay_display_config(config))

    def _voice_tuning_config(self) -> Dict[str, Any]:
        """Return sanitized voice tuning configuration from the UI."""

        def _safe_float(var: Any, default: float) -> float:
            try:
                return float(var.get())
            except Exception:
                return default
        energy_raw = self.voice_energy_threshold.get().strip()
        try:
            energy_val = float(energy_raw) if energy_raw else None
        except Exception:
            energy_val = None
        return {'ambient_duration': max(0.0, _safe_float(self.voice_ambient_duration, VOICE_TUNING_DEFAULTS['ambient_duration'])), 'initial_timeout': max(0.0, _safe_float(self.voice_initial_timeout, VOICE_TUNING_DEFAULTS['initial_timeout'])), 'continuous_timeout': max(0.0, _safe_float(self.voice_continuous_timeout, VOICE_TUNING_DEFAULTS['continuous_timeout'])), 'phrase_time_limit': max(0.0, _safe_float(self.voice_phrase_time_limit, VOICE_TUNING_DEFAULTS['phrase_time_limit'])), 'energy_threshold': energy_val, 'dynamic_energy': self.voice_dynamic_energy.get()}

    @staticmethod
    def _config_bool(value: Any, default: bool=False) -> bool:
        """Return a bool from config values that may be stored as strings."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {'1', 'true', 'on', 'yes', 'sim'}:
            return True
        if text in {'0', 'false', 'off', 'no', 'nao', 'não', 'nÃ£o'}:
            return False
        return default

    def _log_wiper_debug(self, precipitation: float, desired_state: Optional[bool], phase: str, on_threshold: float, off_threshold: float, on_effective: float, off_effective: float, state: Dict[str, Any], wiper_cfg: Dict[str, Any], *, reason: str, force: bool=False) -> None:
        """Print wiper debug telemetry and threshold data to the terminal."""
        if not self.wiper_debug_enabled.get():
            return
        now = time.time()
        if not force and now - state.get('debug_last_log', 0.0) < 0.6:
            return
        pending = state.get('pending_action')
        cooldown_remaining = max(0.0, state.get('cooldown_until', 0.0) - now)
        desired_label = 'ON' if desired_state is True else 'OFF' if desired_state is False else 'HOLD'
        print(f'[Wipers Debug] reason={reason} precip={precipitation:.4f} thresholds(on={on_threshold:.4f}/off={off_threshold:.4f}) effective(on={on_effective:.4f}/off={off_effective:.4f}) phase={phase} desired={desired_label} pending={pending} cooldown={cooldown_remaining:.2f}s delay_range={wiper_cfg.get('humanize_delay_min', '0')}-{wiper_cfg.get('humanize_delay_max', '0')}')
        state['debug_last_log'] = now

    def _trigger_wiper_toggle(self, controller: GenericController, desired_state: bool) -> bool:
        """Press the wiper toggle hotkey once without telemetry checks."""
        key = controller.key_increase
        if not key:
            if controller.update_status:
                controller.update_status("Toggle key not set", 'red')
            if self.wiper_debug_enabled.get():
                print("[Wipers] No toggle key is set.")
            return False
        try:
            click_pulse(key, is_float=False)
        except Exception as exc:
            if self.wiper_debug_enabled.get():
                print(f'[Wipers] Could not press the toggle key: {exc}')
            return False
        if controller.update_status:
            controller.update_status("Triggered", 'green')
        state_label = 'ON' if desired_state else 'OFF'
        self.notify_overlay_status(f'Wipers toggled ({state_label})', 'green')
        return True

    def _reset_wiper_state(self) -> None:
        """Reset windshield wiper automation state."""
        state = self._wiper_state
        state['startup_checked'] = False
        state['last_on_track'] = None
        state['cooldown_until'] = 0.0
        state['last_action'] = None
        state['last_desired'] = None
        state['last_phase'] = None
        state['last_trigger_phase'] = None
        state['pending_action'] = None
        state['pending_phase'] = None
        state['pending_since'] = None
        state['pending_delay'] = 0.0
        state['threshold_drift_on'] = 0.0
        state['threshold_drift_off'] = 0.0
        state['threshold_jitter_on'] = 0.0
        state['threshold_jitter_off'] = 0.0
        state['threshold_last_update'] = 0.0
        state['debug_last_log'] = 0.0

    def apply_voice_tuning(self, persist: bool=False):
        """Send current tuning settings to the listener and optionally save."""
        if not VOICE_FEATURES_ENABLED:
            return
        tuning = self._voice_tuning_config()
        voice_listener.update_tuning(tuning)
        if persist:
            self.schedule_save()

    def on_voice_tuning_changed(self, *_):
        """Propagate UI changes to the listener and persist them."""
        if not VOICE_FEATURES_ENABLED:
            return
        self.apply_voice_tuning(persist=True)

    def manual_restart_scan(self):
        """Restart the app and trigger a scan + preset reload."""
        detected_car, detected_track = self._detect_current_car_track()
        restart_car = (detected_car or self.combo_car.get().strip() or self.current_car).strip()
        restart_track = (detected_track or self.combo_track.get().strip() or self.current_track).strip()
        if restart_car and restart_track:
            self._rescan_restart_pair = (restart_car, restart_track)
        self.pending_scan_on_start = True
        mark_pending_scan(silent=True)
        self.save_config()
        restart_program()

    @staticmethod
    def _parse_lap_filter(value: Any) -> Optional[int]:
        """Parse a row lap filter. Blank means every lap; Outlap maps to 0."""
        text = str(value or '').strip()
        if not text:
            return None
        lowered = text.lower().replace(' ', '').replace('-', '')
        if lowered in {'out', 'outlap', "exit"}:
            return 0
        if lowered.startswith('lap'):
            lowered = lowered[3:]
        try:
            return int(round(float(lowered)))
        except Exception:
            return None

    def _read_current_sdk_lap(self) -> Optional[int]:
        """Read the SDK Lap value, falling back to LapCompleted + 1."""
        value = self._read_ir_value("Lap", use_cache=False)
        if isinstance(value, numbers.Real):
            return int(round(float(value)))
        completed = self._read_ir_value('LapCompleted', use_cache=False)
        if isinstance(completed, numbers.Real):
            return max(0, int(round(float(completed))) + 1)
        return None

    def _lap_filter_matches(self, filter_value: Any, sdk_lap: Optional[int]) -> bool:
        """Return True when a row lap filter matches the current SDK lap."""
        wanted_lap = self._parse_lap_filter(filter_value)
        if wanted_lap is None:
            return True
        if sdk_lap is None:
            return False
        return wanted_lap == sdk_lap

    def _read_lap_completed(self) -> Optional[int]:
        """Read LapCompleted as an integer when available."""
        completed = self._read_ir_value('LapCompleted', use_cache=False)
        if isinstance(completed, numbers.Real):
            return int(round(float(completed)))
        return None

    def _trigger_preset_action(self, controller: GenericController, target: float, var_name: str, preset_index: Optional[int], preset_config: Optional[Dict[str, Any]]) -> None:
        """Trigger a preset macro and handle push-to-pass chaining."""
        if self.app_state != 'RUNNING' or not self._commands_allowed():
            return
        if _is_one_shot_control_name(var_name, controller.is_boolean):
            if target > 0:
                controller.trigger_pulse()
            return
        controller.request_target(target)

    def _make_preset_action(self, controller: GenericController, target: float, var_name: str, preset_index: Optional[int], preset_config: Optional[Dict[str, Any]]):
        """Create an action that adjusts a single controller to a target."""
        return lambda: self._trigger_preset_action(controller, target, var_name, preset_index, preset_config)

    def _make_combo_action(self, values: Dict[str, str]):
        """Create an action that adjusts multiple controllers at once."""

        def combo_action():
            if self.app_state != 'RUNNING' or not self._commands_allowed():
                return
            for var_name, val_str in values.items():
                if var_name in self.controllers and val_str:
                    try:
                        target = float(val_str)
                    except Exception:
                        continue
                    ctrl = self.controllers[var_name]
                    if _is_one_shot_control_name(var_name, ctrl.is_boolean):
                        if target > 0:
                            ctrl.trigger_pulse()
                        continue
                    ctrl.request_target(target)
        return combo_action
__all__ = ['AutomationMixin']
