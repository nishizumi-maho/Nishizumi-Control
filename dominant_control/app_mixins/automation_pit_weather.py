from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *

class PitWeatherAutomationMixin:

    def _pit_limiter_macro_loop(self):
        return None

    @staticmethod
    def _resolve_broadcast_msg_id(name: str) -> Optional[int]:
        broadcast = getattr(irsdk, 'BroadcastMsg', None)
        if broadcast is None:
            return None
        if hasattr(broadcast, name):
            return getattr(broadcast, name)
        if isinstance(broadcast, dict):
            return broadcast.get(name)
        return None

    @staticmethod
    def _resolve_pit_command(command: str) -> Optional[int]:
        pit_enum = getattr(irsdk, 'PitCommandMode', None)
        if pit_enum is None:
            pit_enum = getattr(irsdk, 'PitCommand', None)
        if pit_enum is None:
            return None
        candidates = {command, command.upper(), command.lower(), command.replace(' ', ''), command.replace(' ', '_'), command.replace('-', '_')}
        for name in candidates:
            if hasattr(pit_enum, name):
                return getattr(pit_enum, name)
        if isinstance(pit_enum, dict):
            for name in candidates:
                if name in pit_enum:
                    return pit_enum[name]
        return None

    def _send_pit_command(self, command: str, value: int) -> bool:
        """Send a pit command broadcast via the iRacing SDK."""
        if not getattr(self.ir, 'is_initialized', False):
            try:
                self.ir.startup()
            except Exception:
                return False
        resolved = self._resolve_pit_command(command)
        if resolved is None:
            return False
        try:
            if hasattr(self.ir, 'pit_command'):
                self.ir.pit_command(resolved, value)
            elif hasattr(self.ir, 'broadcast_msg'):
                msg_id = self._resolve_broadcast_msg_id('pit_command')
                if msg_id is None:
                    return False
                self.ir.broadcast_msg(msg_id, resolved, value)
            else:
                return False
        except Exception:
            return False
        return True

    @staticmethod
    def _clean_series_field(value: Any) -> str:
        """Return a compact string for iRacing series metadata."""
        if value in (None, ''):
            return ''
        text = str(value).strip()
        if not text or text.lower() in {'none', 'null', 'nan'}:
            return ''
        return text

    @staticmethod
    def _turbo_pit_offline_status() -> str:
        return "Series: offline default (SeriesID 0) — no active session"

    def _resolve_turbo_pit_series_context(self) -> Tuple[Optional[str], str]:
        """Return the active series preference key and status text."""
        try:
            weekend = self.ir['WeekendInfo']
        except Exception:
            return ('0', self._turbo_pit_offline_status())
        if not isinstance(weekend, dict):
            return ('0', self._turbo_pit_offline_status())
        series_id = self._clean_series_field(weekend.get('SeriesID'))
        series_name = ''
        for key in ('SeriesName', 'SeriesDisplayName', 'SeriesShortName', 'Series', 'SeasonName'):
            series_name = self._clean_series_field(weekend.get(key))
            if series_name:
                break
        if series_id in {'0', '-1'} and (not series_name):
            return ('0', f'No active iRacing series detected (SeriesID {series_id})')
        if series_name and series_id:
            return (series_id, f'Applies to series: {series_name} (SeriesID {series_id})')
        if series_name:
            return ('0', f'Series: {series_name} (SeriesID unavailable; using the default)')
        if series_id:
            return (series_id, f'Applies to SeriesID: {series_id}')
        return ('0', self._turbo_pit_offline_status())

    def _resolve_series_id_key(self) -> Optional[str]:
        """Return the active iRacing series key for Turbo Pit preferences."""
        series_key, _status_text = self._resolve_turbo_pit_series_context()
        return series_key

    def _update_turbo_pit_series_status(self, status_text: str) -> None:
        """Refresh the Turbo Pit per-series status label."""
        try:
            if self.turbo_pit_series_var.get() != status_text:
                self.turbo_pit_series_var.set(status_text)
        except Exception:
            pass
        self._refresh_turbo_pit_visual()

    def _refresh_turbo_pit_visual(self) -> None:
        """Refresh the large Turbo Pit status card."""
        enabled = bool(self.turbo_pit_enabled.get())
        status_text = "ON" if enabled else "OFF"
        action_text = "Turn Turbo Pit off" if enabled else "Turn Turbo Pit on"
        hint_text = "Automatic clearing runs when you enter the pit lane." if enabled else "Automatic clearing is paused for this series."
        bg = '#dff5e4' if enabled else '#ffe1e1'
        fg = '#126b35' if enabled else '#9b1c1c'
        button_bg = '#1f8f45' if enabled else '#c63a3a'
        self.turbo_pit_status_var.set(status_text)
        self.turbo_pit_action_var.set(action_text)
        self.turbo_pit_hint_var.set(hint_text)
        try:
            self.overlay.update_turbo_pit_indicator(enabled)
        except Exception:
            pass
        widgets = (self.turbo_pit_status_card, self.lbl_turbo_pit_status, self.lbl_turbo_pit_hint)
        for widget in widgets:
            if not widget:
                continue
            try:
                widget.config(bg=bg)
            except tk.TclError:
                pass
        if self.turbo_pit_status_card:
            for child in self.turbo_pit_status_card.winfo_children():
                if child is self.btn_turbo_pit_toggle:
                    continue
                try:
                    child.config(bg=bg)
                except tk.TclError:
                    pass
        if self.lbl_turbo_pit_status:
            try:
                self.lbl_turbo_pit_status.config(fg=fg)
            except tk.TclError:
                pass
        if self.lbl_turbo_pit_hint:
            try:
                self.lbl_turbo_pit_hint.config(fg='#3d4a45')
            except tk.TclError:
                pass
        if self.lbl_turbo_pit_series:
            try:
                self.lbl_turbo_pit_series.config(fg=fg)
            except tk.TclError:
                pass
        if self.btn_turbo_pit_toggle:
            try:
                self.btn_turbo_pit_toggle.config(text=action_text, bg=button_bg, fg='white', activebackground=button_bg, activeforeground='white')
            except tk.TclError:
                pass

    def _toggle_turbo_pit_from_card(self) -> None:
        self.turbo_pit_enabled.set(not bool(self.turbo_pit_enabled.get()))
        self._on_turbo_pit_toggle()

    def _sync_turbo_pit_state_for_series(self) -> None:
        """Apply per-series Turbo Pit state when telemetry reports a series change."""
        series_key, status_text = self._resolve_turbo_pit_series_context()
        self._update_turbo_pit_series_status(status_text)
        if series_key == self._active_turbo_pit_series_key:
            return
        self._active_turbo_pit_series_key = series_key
        if not series_key:
            return
        enabled = self.turbo_pit_enabled_by_series.get(series_key, True)
        self.turbo_pit_enabled.set(enabled)
        self._refresh_turbo_pit_visual()

    def _on_turbo_pit_toggle(self) -> None:
        """Persist Turbo Pit state for the currently active series."""
        enabled = bool(self.turbo_pit_enabled.get())
        series_key, status_text = self._resolve_turbo_pit_series_context()
        self._update_turbo_pit_series_status(status_text)
        self._active_turbo_pit_series_key = series_key
        if series_key:
            self.turbo_pit_enabled_by_series[series_key] = enabled
        self._refresh_turbo_pit_visual()
        self.schedule_save()

    def _turbo_pit_clear(self) -> None:
        """Clear tires + windshield pit options via broadcast."""
        self._send_pit_command(PIT_COMMAND_CLEAR_TIRES, 0)
        self._send_pit_command(PIT_COMMAND_CLEAR_WS, 0)

    def _turbo_pit_loop(self) -> None:
        """Run Turbo Pit auto-clear when entering the pit lane."""
        interval_ms = 300
        try:
            self._sync_turbo_pit_state_for_series()
            if not self.turbo_pit_enabled.get() or self.app_state != 'RUNNING' or (not self._commands_allowed()):
                interval_ms = 500
                self._turbo_pit_state['last_on_pit'] = None
                return
            on_pit = self._bool_from_keys(['OnPitRoad', 'PlayerCarOnPitRoad'])
            if on_pit is None:
                return
            last_on_pit = self._turbo_pit_state.get('last_on_pit')
            if last_on_pit is None:
                self._turbo_pit_state['last_on_pit'] = on_pit
                return
            if on_pit and (not last_on_pit):
                self._turbo_pit_clear()
            self._turbo_pit_state['last_on_pit'] = on_pit
        finally:
            self.root.after(interval_ms, self._turbo_pit_loop)

    def _wiper_controller(self) -> Tuple[Optional[str], Optional[GenericController], Optional[ControlTab]]:
        """Return the first available wiper controller/tab pair."""
        for var_name in WIPER_TOGGLE_VARS:
            controller = self.controllers.get(var_name)
            if controller:
                return (var_name, controller, self.tabs.get(var_name))
        return (None, None, None)

    def _wiper_tab(self) -> Optional[ControlTab]:
        """Return the first available wiper tab."""
        for var_name in WIPER_TOGGLE_VARS:
            tab = self.tabs.get(var_name)
            if tab:
                return tab
        return None

    @staticmethod
    def _wiper_alias_config(configs: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the first wiper config stored under any known alias."""
        for alias in WIPER_TOGGLE_VARS:
            cfg = configs.get(alias)
            if isinstance(cfg, dict):
                return cfg
        return None

    def _wiper_thresholds(self, wiper_cfg: Dict[str, Any]) -> Tuple[float, float]:
        """Return parsed precipitation thresholds (on, off)."""

        def _parse(value: Any, fallback: float) -> float:
            try:
                return float(value)
            except Exception:
                return fallback
        on_threshold = _parse(wiper_cfg.get('precip_on', 0.04), 0.04)
        off_threshold = _parse(wiper_cfg.get('precip_off', 0.03), 0.03)
        if on_threshold < 0.0:
            on_threshold = 0.0
        if off_threshold < 0.0:
            off_threshold = 0.0
        if on_threshold < off_threshold:
            on_threshold, off_threshold = (off_threshold, on_threshold)
        return (on_threshold, off_threshold)

    def _wiper_humanized_thresholds(self, on_threshold: float, off_threshold: float, state: Dict[str, Any]) -> Tuple[float, float]:
        """Return a humanized set of precipitation thresholds."""
        if off_threshold == 0.0:
            return (on_threshold, off_threshold)
        now = time.time()
        last_update = state.get('threshold_last_update', now)
        elapsed = max(0.0, now - last_update)
        decay = 0.6 ** max(1.0, elapsed * 2.0)
        drift_on = state.get('threshold_drift_on', 0.0) * decay
        drift_off = state.get('threshold_drift_off', 0.0) * decay
        drift_scale = max(0.0015, on_threshold * 0.08)
        drift_on += self._lapdist_rng.gauss(0.0, drift_scale)
        drift_off += self._lapdist_rng.gauss(0.0, drift_scale * 0.85)
        drift_on = max(-0.018, min(0.018, drift_on))
        drift_off = max(-0.018, min(0.018, drift_off))
        state['threshold_drift_on'] = drift_on
        state['threshold_drift_off'] = drift_off
        jitter_on = state.get('threshold_jitter_on', 0.0)
        jitter_off = state.get('threshold_jitter_off', 0.0)
        jitter_on += self._lapdist_rng.uniform(-0.006, 0.006)
        jitter_off += self._lapdist_rng.uniform(-0.006, 0.006)
        jitter_on = max(-0.012, min(0.012, jitter_on))
        jitter_off = max(-0.012, min(0.012, jitter_off))
        state['threshold_jitter_on'] = jitter_on
        state['threshold_jitter_off'] = jitter_off
        state['threshold_last_update'] = now
        base_bias = self._lapdist_rng.uniform(-0.003, 0.003)
        on_effective = on_threshold + drift_on + jitter_on + base_bias
        off_effective = off_threshold + drift_off + jitter_off - base_bias * 0.4
        on_effective = max(0.0, on_effective)
        off_effective = max(0.0, off_effective)
        if on_effective < off_effective:
            on_effective, off_effective = (off_effective, on_effective)
        return (on_effective, off_effective)

    def _wiper_action_delay(self, wiper_cfg: Dict[str, Any], off_threshold: float) -> float:
        """Return the delay before toggling wipers based on config."""

        def _parse(value: Any, fallback: float) -> float:
            try:
                return float(value)
            except Exception:
                return fallback
        min_delay = _parse(wiper_cfg.get('humanize_delay_min', 0.0), 0.0)
        max_delay = _parse(wiper_cfg.get('humanize_delay_max', min_delay), min_delay)
        min_delay = max(0.0, min_delay)
        max_delay = max(0.0, max_delay)
        if max_delay < min_delay:
            min_delay, max_delay = (max_delay, min_delay)
        if max_delay > 0.0:
            return self._lapdist_rng.uniform(min_delay, max_delay)
        if off_threshold == 0.0:
            return self._lapdist_rng.uniform(5.0, 10.0)
        return 0.0

    def _wiper_desired_state(self, precipitation: float, wiper_cfg: Dict[str, Any], state: Dict[str, Any]) -> Tuple[Optional[bool], str, float, float, float, float]:
        """Return desired wiper state from precipitation and thresholds."""
        on_threshold, off_threshold = self._wiper_thresholds(wiper_cfg)
        on_effective, off_effective = self._wiper_humanized_thresholds(on_threshold, off_threshold, state)
        if precipitation >= on_effective:
            return (True, 'on', on_threshold, off_threshold, on_effective, off_effective)
        if precipitation <= off_effective:
            return (False, 'off', on_threshold, off_threshold, on_effective, off_effective)
        return (None, 'hold', on_threshold, off_threshold, on_effective, off_effective)

    def _wiper_should_trigger(self, desired_state: bool, phase: str, state: Dict[str, Any]) -> bool:
        """Return True if a new wiper toggle should be scheduled."""
        last_desired = state.get('last_desired')
        if desired_state is False and last_desired is not True:
            return False
        if desired_state is True and last_desired is True:
            return False
        return state.get('last_trigger_phase') != phase

    def _wiper_process_precipitation(self, controller: GenericController, wiper_cfg: Dict[str, Any], state: Dict[str, Any], precipitation: float) -> None:
        """Evaluate precipitation and schedule/trigger wiper toggles."""
        desired_state, phase, on_threshold, off_threshold, on_effective, off_effective = self._wiper_desired_state(precipitation, wiper_cfg, state)
        state['last_phase'] = phase
        self._log_wiper_debug(precipitation, desired_state, phase, on_threshold, off_threshold, on_effective, off_effective, state, wiper_cfg, reason='auto')
        now = time.time()
        pending_action = state.get('pending_action')
        if desired_state is None:
            if pending_action is not None:
                state['pending_action'] = None
                state['pending_phase'] = None
            return
        if pending_action is not None and pending_action != desired_state:
            state['pending_action'] = None
            state['pending_phase'] = None
            pending_action = None
        if pending_action is None:
            if not self._wiper_should_trigger(desired_state, phase, state):
                return
            if now < state.get('cooldown_until', 0.0):
                return
            delay = self._wiper_action_delay(wiper_cfg, off_threshold)
            state['pending_action'] = desired_state
            state['pending_phase'] = phase
            state['pending_since'] = now
            state['pending_delay'] = delay
            pending_action = desired_state
        pending_since = state.get('pending_since', now)
        pending_delay = state.get('pending_delay', 0.0)
        if pending_since is None:
            pending_since = now
            state['pending_since'] = now
        if now < state.get('cooldown_until', 0.0):
            return
        if now - pending_since < pending_delay:
            return
        if self._trigger_wiper_toggle(controller, bool(pending_action)):
            pending_phase = state.get('pending_phase')
            state['pending_action'] = None
            state['pending_phase'] = None
            state['last_trigger_phase'] = pending_phase or phase
            state['last_desired'] = pending_action
            state['last_action'] = now
            state['cooldown_until'] = now + 0.35

    def _precipitation_amount(self) -> Optional[float]:
        """Return the current precipitation amount when available."""
        value = self._read_ir_value('Precipitation')
        if value is None:
            return None
        if isinstance(value, (list, tuple, array)):
            values = [self._safe_float(item, 0.0) for item in value]
            return max(values) if values else 0.0
        if isinstance(value, numbers.Real):
            return float(value)
        return None

    def _wiper_apply_snapshot(self, controller: GenericController, wiper_cfg: Dict[str, Any]) -> bool:
        """Apply a one-time wiper toggle decision from current precipitation."""
        precipitation = self._precipitation_amount()
        if precipitation is None:
            return False
        self._wiper_process_precipitation(controller, wiper_cfg, self._wiper_state, precipitation)
        return True

    def _wiper_macro_loop(self) -> None:
        """Auto-toggle windshield wipers based on precipitation telemetry."""
        interval_ms = 200
        try:
            _name, controller, tab = self._wiper_controller()
            if not controller or not tab or self.app_state != 'RUNNING' or (not self._commands_allowed()):
                interval_ms = 400
                self._reset_wiper_state()
                return
            tab_config = tab.get_config()
            wiper_cfg = tab_config.get('wiper_auto', {})
            if not wiper_cfg.get('enabled'):
                interval_ms = 450
                self._reset_wiper_state()
                return
            state = self._wiper_state
            if self._wiper_apply_snapshot(controller, wiper_cfg):
                state['startup_checked'] = True
        finally:
            self.root.after(interval_ms, self._wiper_macro_loop)

    def trigger_wiper_debug(self) -> None:
        """Log precipitation thresholds and apply the wiper toggle decision."""
        if not self.wiper_debug_enabled.get():
            return
        _name, controller, tab = self._wiper_controller()
        if not controller or not tab:
            print("[Wipers] Control or tab unavailable.")
            return
        if not self._commands_allowed() or self.app_state != 'RUNNING':
            print("[Wipers] Commands blocked off track or outside RUNNING mode.")
            return
        tab_config = tab.get_config()
        wiper_cfg = tab_config.get('wiper_auto', {})
        enabled = bool(wiper_cfg.get('enabled', False))
        precipitation = self._precipitation_amount()
        if precipitation is None:
            print("[Wipers] Precipitation telemetry unavailable.")
            return
        desired_state, phase, on_threshold, off_threshold, on_effective, off_effective = self._wiper_desired_state(precipitation, wiper_cfg, self._wiper_state)
        self._log_wiper_debug(precipitation, desired_state, phase, on_threshold, off_threshold, on_effective, off_effective, self._wiper_state, wiper_cfg, reason='manual', force=True)
        if not enabled:
            print("[Wipers] Automation is off in this preset.")
            return
        if desired_state is None:
            print("[Wipers] Precipitation in the neutral band; no command sent.")
            return
        if self._trigger_wiper_toggle(controller, desired_state):
            now = time.time()
            state = self._wiper_state
            state['last_desired'] = desired_state
            state['last_phase'] = phase
            state['last_trigger_phase'] = phase
            state['last_action'] = now
            state['cooldown_until'] = now + 0.35
__all__ = ['PitWeatherAutomationMixin']
