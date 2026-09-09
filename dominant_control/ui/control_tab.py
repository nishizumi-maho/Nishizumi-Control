from __future__ import annotations
from ..foundation import *
from .dialogs import *
from .hud import *
from .controller import GenericController
from .scan_validator import ScanValidator

class ControlTab(tk.Frame):
    """
    Configuration tab for a single control variable.
    """

    def __init__(self, parent, controller: GenericController, label_name: str, app, *, create_default_rows: bool=True):
        super().__init__(parent)
        self.app = app
        self.controller = controller
        self.label_name = label_name
        self.controller.update_status = self.update_status_label
        self.controller.app = app
        self.preset_rows: List[Dict[str, Any]] = []
        self.is_pit_limiter = self.controller.var_name == 'dcPitSpeedLimiterToggle'
        self.is_push_to_pass = self.controller.var_name == 'dcPushToPass'
        self.is_hybrid_hold = self.controller.var_name in HYBRID_HOLD_VARS
        self.is_weight_jacker = self.controller.var_name in WEIGHT_JACKER_VARS
        self.is_fuel_mixture = self.controller.var_name == FUEL_MIXTURE_VAR
        self.is_wiper_toggle = self.controller.var_name in WIPER_TOGGLE_VARS
        self.is_wiper_trigger = self.controller.var_name in WIPER_TRIGGER_VARS
        self.is_tearoff = self._is_tearoff_control()
        self.action_kind = _driver_control_action_kind(self.controller.var_name, self.controller.is_boolean)
        self.is_toggle_action = self.action_kind == 'toggle'
        self.is_one_shot_action = self.action_kind in {'trigger', 'toggle'}
        self.supports_random_lap_trigger = False
        self.is_simple_boolean = self.controller.is_boolean and (not self.is_push_to_pass) and (not self.is_hybrid_hold) and (not self.is_wiper_toggle) and (not self.is_one_shot_action) and (not self.is_pit_limiter)
        self.pit_limiter_auto = tk.BooleanVar(value=False)
        self.pit_limiter_humanize = tk.BooleanVar(value=True)
        self.pit_limiter_force_on = tk.BooleanVar(value=False)
        self.pit_limiter_emergency_reenable = tk.BooleanVar(value=False)
        self.pit_limiter_trigger_on_approach = tk.BooleanVar(value=False)
        self.pit_limiter_configured = tk.BooleanVar(value=True)
        self.pit_limiter_delay_min = tk.StringVar(value='0.08')
        self.pit_limiter_delay_max = tk.StringVar(value='0.26')
        self.pit_limiter_approach_delay_min = tk.StringVar(value='0.00')
        self.pit_limiter_approach_delay_max = tk.StringVar(value='0.18')
        self.pit_limiter_poll_hz_min = tk.StringVar(value='7.0')
        self.pit_limiter_poll_hz_max = tk.StringVar(value='10.0')
        self.chk_pit_limiter_auto: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_humanize: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_force_on: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_emergency_reenable: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_trigger_on_approach: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_configured: Optional[tk.Checkbutton] = None
        self.entry_pit_limiter_delay_min: Optional[tk.Entry] = None
        self.entry_pit_limiter_delay_max: Optional[tk.Entry] = None
        self.entry_pit_limiter_approach_delay_min: Optional[tk.Entry] = None
        self.entry_pit_limiter_approach_delay_max: Optional[tk.Entry] = None
        self.entry_pit_limiter_poll_hz_min: Optional[tk.Entry] = None
        self.entry_pit_limiter_poll_hz_max: Optional[tk.Entry] = None
        self.lapdist_header_frame: Optional[tk.Frame] = None
        self.lapdist_helper_label: Optional[tk.Label] = None
        self.pit_limiter_frame: Optional[tk.LabelFrame] = None
        self.p2p_chain_delay_min = tk.StringVar(value='0.12')
        self.p2p_chain_delay_max = tk.StringVar(value='0.25')
        self.entry_p2p_chain_delay_min: Optional[tk.Entry] = None
        self.entry_p2p_chain_delay_max: Optional[tk.Entry] = None
        self.fuel_yellow_enabled = tk.BooleanVar(value=False)
        self.fuel_green_enabled = tk.BooleanVar(value=False)
        self.fuel_yellow_delay_min = tk.StringVar(value='1.00')
        self.fuel_yellow_delay_max = tk.StringVar(value='2.00')
        self.fuel_green_delay_min = tk.StringVar(value='0.25')
        self.fuel_green_delay_max = tk.StringVar(value='0.70')
        self.fuel_mixture_frame: Optional[tk.LabelFrame] = None
        self.chk_fuel_yellow_enabled: Optional[tk.Checkbutton] = None
        self.chk_fuel_green_enabled: Optional[tk.Checkbutton] = None
        self.entry_fuel_yellow_delay_min: Optional[tk.Entry] = None
        self.entry_fuel_yellow_delay_max: Optional[tk.Entry] = None
        self.entry_fuel_green_delay_min: Optional[tk.Entry] = None
        self.entry_fuel_green_delay_max: Optional[tk.Entry] = None
        self.hybrid_hold_enabled = tk.BooleanVar(value=True)
        if self.controller.var_name == HYBRID_BOOST_HOLD_VAR:
            self.hybrid_stop_soc_min = tk.StringVar(value='0.000')
            self.hybrid_stop_soc_max = tk.StringVar(value='0.020')
        else:
            self.hybrid_stop_soc_min = tk.StringVar(value='0.990')
            self.hybrid_stop_soc_max = tk.StringVar(value='1.000')
        self.hybrid_max_hold_s = tk.StringVar(value='12.0')
        self.hybrid_frame: Optional[tk.LabelFrame] = None
        self.chk_hybrid_hold_enabled: Optional[tk.Checkbutton] = None
        self.entry_hybrid_stop_soc_min: Optional[tk.Entry] = None
        self.entry_hybrid_stop_soc_max: Optional[tk.Entry] = None
        self.entry_hybrid_max_hold_s: Optional[tk.Entry] = None
        self.wiper_auto = tk.BooleanVar(value=True)
        self.wiper_precip_on = tk.StringVar(value='0.04')
        self.wiper_precip_off = tk.StringVar(value='0.03')
        self.wiper_humanize_delay_min = tk.StringVar(value='0.0')
        self.wiper_humanize_delay_max = tk.StringVar(value='0.0')
        self.chk_wiper_auto: Optional[tk.Checkbutton] = None
        self.entry_wiper_precip_on: Optional[tk.Entry] = None
        self.entry_wiper_precip_off: Optional[tk.Entry] = None
        self.entry_wiper_delay_min: Optional[tk.Entry] = None
        self.entry_wiper_delay_max: Optional[tk.Entry] = None
        self.wiper_frame: Optional[tk.LabelFrame] = None
        self.lap_trigger_enabled = tk.BooleanVar(value=False)
        self.lap_trigger_interval = tk.StringVar(value='1')
        self.lap_trigger_count = tk.StringVar(value='1')
        self.lap_trigger_frame: Optional[tk.LabelFrame] = None
        self.chk_lap_trigger_enabled: Optional[tk.Checkbutton] = None
        self.entry_lap_trigger_interval: Optional[tk.Entry] = None
        self.entry_lap_trigger_count: Optional[tk.Entry] = None
        self.boolean_pulse_only = tk.BooleanVar(value=False)
        self.boolean_pulse_double = tk.BooleanVar(value=False)
        self.chk_boolean_pulse: Optional[tk.Checkbutton] = None
        self.chk_boolean_pulse_double: Optional[tk.Checkbutton] = None
        self.manual_pulse_frame: Optional[tk.LabelFrame] = None
        self.btn_manual_increase_bind: Optional[tk.Button] = None
        self.btn_manual_decrease_bind: Optional[tk.Button] = None
        self.manual_increase_bind: Optional[str] = None
        self.manual_decrease_bind: Optional[str] = None
        self._manual_increase_source_id = f'control-manual:{id(self)}:increase'
        self._manual_decrease_source_id = f'control-manual:{id(self)}:decrease'
        self.bounds_supported = not self.controller.is_boolean
        bound_min, bound_max = self.controller.configured_bounds()
        self.bound_min_value = tk.StringVar(value=_format_limit_value(bound_min, self.controller.is_float))
        self.bound_max_value = tk.StringVar(value=_format_limit_value(bound_max, self.controller.is_float))
        self.bounds_frame: Optional[tk.LabelFrame] = None
        self.entry_bound_min: Optional[tk.Entry] = None
        self.entry_bound_max: Optional[tk.Entry] = None
        self.btn_apply_bounds: Optional[tk.Button] = None
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill='both', expand=True)
        body = scroll_frame.inner
        self.keys_frame = tk.LabelFrame(body, text=f'iRacing keys ({label_name})', padx=5, pady=5)
        self.keys_frame.pack(fill='x', padx=5, pady=5)
        self.btn_increase = tk.Button(self.keys_frame, text="Set Increase (+)", command=lambda: self.bind_game_key('increase'))
        self.btn_decrease = tk.Button(self.keys_frame, text="Set Decrease (-)", command=lambda: self.bind_game_key('decrease'))
        self.uses_toggle_key = self.controller.is_boolean and (not self.controller.allow_dual_keys)
        if self.is_pit_limiter or self.is_one_shot_action:
            self.uses_toggle_key = True
        if self.uses_toggle_key:
            self.btn_increase.config(text="Set Hold" if self.is_hybrid_hold else "Set Toggle" if self.is_toggle_action else "Set Trigger" if self.is_one_shot_action else "Set Toggle")
            self.btn_increase.pack(side='left', expand=True, fill='x', padx=2)
            self.btn_decrease.config(text="Not used", state='disabled')
        else:
            self.btn_increase.pack(side='left', expand=True, fill='x', padx=2)
            self.btn_decrease.pack(side='left', expand=True, fill='x', padx=2)
        if self.bounds_supported:
            self.bounds_frame = tk.LabelFrame(body, text="Control limits", padx=5, pady=5)
            self.bounds_frame.pack(fill='x', padx=5, pady=(0, 5))
            bounds_row = tk.Frame(self.bounds_frame)
            bounds_row.pack(fill='x')
            tk.Label(bounds_row, text="Min.:").pack(side='left')
            self.entry_bound_min = tk.Entry(bounds_row, width=8, textvariable=self.bound_min_value)
            self.entry_bound_min.pack(side='left', padx=(4, 10))
            tk.Label(bounds_row, text="Max.:").pack(side='left')
            self.entry_bound_max = tk.Entry(bounds_row, width=8, textvariable=self.bound_max_value)
            self.entry_bound_max.pack(side='left', padx=(4, 10))
            self.btn_apply_bounds = tk.Button(bounds_row, text="Apply", width=10, command=self.apply_bounds_from_entries)
            self.btn_apply_bounds.pack(side='left', padx=(0, 6))
            self._bind_bounds_entry(self.entry_bound_min)
            self._bind_bounds_entry(self.entry_bound_max)
            self.apply_bounds_from_entries(show_errors=False, persist=False)
        if self.is_simple_boolean:
            self.chk_boolean_pulse = tk.Checkbutton(body, text="One-shot macro (presses once, without checking telemetry)", variable=self.boolean_pulse_only, command=self._on_boolean_pulse_toggle)
            self.chk_boolean_pulse.pack(anchor='w', padx=10, pady=(0, 6))
            self.chk_boolean_pulse_double = tk.Checkbutton(body, text="Double tap while one-shot is on (turns on and off)", variable=self.boolean_pulse_double, command=self._on_boolean_pulse_double_toggle)
            self.chk_boolean_pulse_double.pack(anchor='w', padx=26, pady=(0, 6))
            self._update_boolean_pulse_double_state(editing=self.app.app_state == 'CONFIG')
        self.manual_pulse_frame = tk.LabelFrame(body, text="Manual hotkeys", padx=5, pady=5)
        self.manual_pulse_frame.pack(fill='x', padx=5, pady=(0, 5))
        tk.Label(self.manual_pulse_frame, text="Bind a joystick or keyboard hotkey here to send a pulse of the key set in the game.", fg='gray', font=('Arial', 8), wraplength=760, justify='left').pack(anchor='w', pady=(0, 4))
        manual_bind_row = tk.Frame(self.manual_pulse_frame)
        manual_bind_row.pack(fill='x')
        self.btn_manual_increase_bind = tk.Button(manual_bind_row, text="Set + hotkey", command=lambda: self.bind_manual_pulse_hotkey('increase'))
        self.btn_manual_decrease_bind = tk.Button(manual_bind_row, text="Set - hotkey", command=lambda: self.bind_manual_pulse_hotkey('decrease'))
        if self.uses_toggle_key:
            self.btn_manual_increase_bind.config(text="Set toggle hotkey" if self.is_toggle_action else "Set action hotkey" if self.is_one_shot_action else "Set toggle hotkey")
            self.btn_manual_decrease_bind.config(text="Not used", state='disabled')
            self.btn_manual_increase_bind.pack(side='left', expand=True, fill='x', padx=2)
        else:
            self.btn_manual_increase_bind.pack(side='left', expand=True, fill='x', padx=2)
            self.btn_manual_decrease_bind.pack(side='left', expand=True, fill='x', padx=2)
        self._refresh_manual_pulse_bind_button('increase')
        self._refresh_manual_pulse_bind_button('decrease')
        self.lbl_monitor = tk.Label(body, text="Value: --", font=('Arial', 14, 'bold'))
        self.lbl_monitor.pack(pady=5)
        self.lbl_status = tk.Label(body, text="Idle", fg='gray')
        self.lbl_status.pack()
        self.presets_frame = tk.LabelFrame(body, text="Presets / Macros", padx=5, pady=5)
        self.presets_frame.pack(fill='both', expand=True, padx=5, pady=5)
        if VOICE_FEATURES_ENABLED:
            tk.Label(self.presets_frame, text="Optional voice trigger: type the exact phrase you will say to run the macro. The options live in Options → Voice/Audio Settings.", fg='gray', font=('Arial', 8), wraplength=760, justify='left').pack(anchor='w', padx=2, pady=(0, 5))
        header = tk.Frame(self.presets_frame)
        header.pack(fill='x', padx=2, pady=(0, 2))
        self._configure_macro_grid(header)
        columns = self._macro_column_map()
        tk.Label(header, text="Action" if self.is_one_shot_action else "Macro value", anchor='w', font=('Arial', 8, 'bold')).grid(row=0, column=columns['value'], sticky='w', padx=5)
        if not self.is_pit_limiter:
            tk.Label(header, text="Macro hotkey", anchor='w', font=('Arial', 8, 'bold')).grid(row=0, column=columns['bind'], sticky='w', padx=5)
        if VOICE_FEATURES_ENABLED:
            tk.Label(header, text="Voice phrase", anchor='w', font=('Arial', 8, 'bold')).grid(row=0, column=columns['voice'], sticky='w', padx=5)
        self.presets_container = tk.Frame(self.presets_frame)
        self.presets_container.pack(fill='both', expand=True)
        self.btn_add_preset_row = tk.Button(self.presets_frame, text="Add Row (+)", command=self.add_preset_row, bg='#f0f0f0')
        self.btn_add_preset_row.pack(fill='x', padx=2, pady=(4, 0))
        if self.is_pit_limiter or self.is_wiper_toggle:
            self.btn_add_preset_row.pack_forget()
            self._toggle_pack_widget(self.presets_frame, False)
        self.p2p_frame = None
        if self.is_wiper_toggle and True:
            self.wiper_frame = tk.LabelFrame(body, text="Windshield Wiper Automation", padx=5, pady=5)
            self.wiper_frame.pack(fill='x', padx=5, pady=(4, 6))
            tk.Label(self.wiper_frame, text="Toggles the wipers based on precipitation. Values above the ON threshold turn them on; values below the OFF threshold turn them off.", fg='gray', font=('Arial', 8), wraplength=760, justify='left').pack(anchor='w', pady=(0, 4))
            self.chk_wiper_auto = tk.Checkbutton(self.wiper_frame, text="Enable wipers by precipitation", variable=self.wiper_auto, command=self._on_wiper_setting_change)
            self.chk_wiper_auto.pack(anchor='w')
            threshold_row = tk.Frame(self.wiper_frame)
            threshold_row.pack(fill='x', pady=(2, 0))
            tk.Label(threshold_row, text="Precipitation threshold to turn the wipers on (>=):").pack(side='left')
            self.entry_wiper_precip_on = tk.Entry(threshold_row, width=6, textvariable=self.wiper_precip_on)
            self.entry_wiper_precip_on.pack(side='left', padx=(6, 2))
            tk.Label(threshold_row, text="Turn the wipers off when <=:").pack(side='left', padx=(10, 0))
            self.entry_wiper_precip_off = tk.Entry(threshold_row, width=6, textvariable=self.wiper_precip_off)
            self.entry_wiper_precip_off.pack(side='left', padx=(6, 2))
            tk.Label(threshold_row, text="(example: on 0.04, off 0.03)").pack(side='left', padx=(8, 0))
            delay_row = tk.Frame(self.wiper_frame)
            delay_row.pack(fill='x', pady=(4, 0))
            tk.Label(delay_row, text="Delay range before toggling (seconds):").pack(side='left')
            self.entry_wiper_delay_min = tk.Entry(delay_row, width=6, textvariable=self.wiper_humanize_delay_min)
            self.entry_wiper_delay_min.pack(side='left', padx=(6, 2))
            tk.Label(delay_row, text="to").pack(side='left')
            self.entry_wiper_delay_max = tk.Entry(delay_row, width=6, textvariable=self.wiper_humanize_delay_max)
            self.entry_wiper_delay_max.pack(side='left', padx=4)
            tk.Label(delay_row, text="(use 0 to keep the default behavior)").pack(side='left', padx=(6, 0))
            self._bind_autosave_entry(self.entry_wiper_precip_on)
            self._bind_autosave_entry(self.entry_wiper_precip_off)
            self._bind_autosave_entry(self.entry_wiper_delay_min)
            self._bind_autosave_entry(self.entry_wiper_delay_max)
            self._update_wiper_option_state(editing=self.app.app_state == 'CONFIG')
        if create_default_rows:
            if self.is_pit_limiter:
                for _ in range(2):
                    self.add_preset_row()
            elif self.is_wiper_toggle:
                pass
            else:
                for _ in range(4):
                    self.add_preset_row()
        self.running = True
        self.after(500, self.monitor_loop)

    def set_discreet_mode(self, enabled: bool) -> None:
        """Show or hide automation-focused UI elements."""
        self._toggle_pack_widget(self.keys_frame, not enabled)
        self._toggle_pack_widget(self.bounds_frame, not enabled)
        self._toggle_pack_widget(self.manual_pulse_frame, not enabled)
        show_presets = not enabled and (not self.is_pit_limiter) and (not self.is_wiper_toggle)
        self._toggle_pack_widget(self.presets_frame, show_presets)
        self._toggle_pack_widget(self.p2p_frame, not enabled)
        self._toggle_pack_widget(self.lapdist_helper_label, not enabled)
        self._toggle_pack_widget(self.lapdist_header_frame, not enabled)
        self._toggle_pack_widget(self.pit_limiter_frame, not enabled)
        self._toggle_pack_widget(self.fuel_mixture_frame, not enabled)
        self._toggle_pack_widget(self.hybrid_frame, not enabled)
        self._toggle_pack_widget(self.wiper_frame, not enabled)
        self._toggle_pack_widget(self.lap_trigger_frame, not enabled)
        for row in self.preset_rows:
            self._apply_discreet_mode_to_row(row, enabled)

    def _apply_discreet_mode_to_row(self, row_data: Dict[str, Any], enabled: bool) -> None:
        self._toggle_pack_widget(row_data.get('lapdist_container'), not enabled)
        self._toggle_pack_widget(row_data.get('capture_button'), not enabled)

    def _macro_grid_columns(self) -> List[Tuple[str, int]]:
        columns: List[Tuple[str, int]] = [('value', 86)]
        if not self.is_pit_limiter:
            columns.append(('bind', 126))
        if VOICE_FEATURES_ENABLED:
            columns.append(('voice', 160))
        if not self.is_pit_limiter:
            columns.append(('delete', 34))
        return columns

    def _macro_column_map(self) -> Dict[str, int]:
        return {name: index for index, (name, _width) in enumerate(self._macro_grid_columns())}

    def _configure_macro_grid(self, frame: tk.Frame) -> None:
        columns = self._macro_grid_columns()
        for index, (_name, width) in enumerate(columns):
            frame.grid_columnconfigure(index, minsize=width, weight=0)
        frame.grid_columnconfigure(len(columns), weight=1)

    def _toggle_pack_widget(self, widget: Optional[tk.Widget], show: bool) -> None:
        if widget is None:
            return
        if show:
            if widget.winfo_manager() in {'pack', 'grid'}:
                return
            info = getattr(widget, '_pack_info', None)
            if info:
                widget.pack(**info)
                return
            info = getattr(widget, '_grid_info', None)
            if info:
                widget.grid(**info)
        else:
            manager = widget.winfo_manager()
            if manager == 'pack':
                info = widget.pack_info()
                if 'in' in info:
                    info['in_'] = info.pop('in')
                widget._pack_info = info
                widget.pack_forget()
            elif manager == 'grid':
                info = widget.grid_info()
                if 'in' in info:
                    info['in_'] = info.pop('in')
                widget._grid_info = info
                widget.grid_remove()

    def update_status_label(self, text: str, color: str):
        """Update status label."""
        if not self.app:
            return

        def _apply():
            try:
                if not self.lbl_status.winfo_exists():
                    return
                self.lbl_status.config(text=text, fg=color)
            except Exception:
                pass
        self.app.ui(_apply)

    def _bind_autosave_entry(self, entry: tk.Entry) -> None:
        """Attach auto-save handlers to entries."""
        entry.bind('<KeyRelease>', lambda _event: self.app.schedule_preset_save())
        entry.bind('<FocusOut>', lambda _event: self.app.schedule_preset_save())

    def _bind_bounds_entry(self, entry: tk.Entry) -> None:
        """Attach bounds parsing and auto-save handlers."""
        entry.bind('<KeyRelease>', lambda _event: self.apply_bounds_from_entries(show_errors=False, persist=True))
        entry.bind('<FocusOut>', lambda _event: self.apply_bounds_from_entries(show_errors=True, persist=True))

    def _parse_bound_entries(self) -> Tuple[Optional[float], Optional[float]]:
        """Parse min/max entries and validate their numeric values."""
        min_text = self.bound_min_value.get().strip()
        max_text = self.bound_max_value.get().strip()
        min_value = _parse_optional_float(min_text)
        max_value = _parse_optional_float(max_text)
        if min_text and min_value is None:
            raise ValueError("Invalid minimum.")
        if max_text and max_value is None:
            raise ValueError("Invalid maximum.")
        return (min_value, max_value)

    def apply_bounds_from_entries(self, *, show_errors: bool=True, persist: bool=True) -> bool:
        """Apply typed min/max limits to the controller."""
        if not self.bounds_supported:
            return True
        try:
            min_value, max_value = self._parse_bound_entries()
        except ValueError as exc:
            if show_errors:
                self.update_status_label(str(exc), 'red')
                self.app._show_warning("Limits", str(exc))
            return False
        self.controller.set_bounds(min_value, max_value)
        refresh_warning = getattr(self.app, '_refresh_bounds_warning', None)
        if callable(refresh_warning):
            refresh_warning()
        if show_errors:
            min_value, max_value = self.controller.configured_bounds()
            self.bound_min_value.set(_format_limit_value(min_value, self.controller.is_float))
            self.bound_max_value.set(_format_limit_value(max_value, self.controller.is_float))
        if persist:
            self.app.schedule_preset_save()
        return True

    def set_bounds_from_values(self, min_value: Optional[float], max_value: Optional[float], *, persist: bool=True) -> None:
        """Update limit entries and controller values from discovered bounds."""
        self.controller.set_bounds(min_value, max_value)
        min_value, max_value = self.controller.configured_bounds()
        self.bound_min_value.set(_format_limit_value(min_value, self.controller.is_float))
        self.bound_max_value.set(_format_limit_value(max_value, self.controller.is_float))
        if persist:
            self.app.schedule_preset_save()
        refresh_warning = getattr(self.app, '_refresh_bounds_warning', None)
        if callable(refresh_warning):
            refresh_warning()

    def _bind_lapdist_sort(self, entry: tk.Entry) -> None:
        """Attach LapDist sorting handlers to entries."""
        entry.bind('<FocusOut>', lambda _event: self._sort_lapdist_rows(), add='+')

    def _sort_lapdist_rows(self) -> None:
        """Sort LapDist macro rows from lowest to highest LapDist values."""
        if len(self.preset_rows) < 2:
            return

        def lapdist_value(row: Dict[str, Any]) -> Optional[float]:
            min_text = self._safe_entry_value(row.get('lap_dist_min_entry')).strip()
            max_text = self._safe_entry_value(row.get('lap_dist_max_entry')).strip()
            for candidate in (min_text, max_text):
                if not candidate:
                    continue
                try:
                    return float(candidate)
                except Exception:
                    continue
            return None

        def lap_number_value(row: Dict[str, Any]) -> Optional[int]:
            text = self._safe_entry_value(row.get('lap_number_entry')).strip()
            if not text:
                return None
            lowered = text.lower().replace(' ', '')
            if lowered in {'out', 'outlap'}:
                return 0
            try:
                return int(round(float(text)))
            except Exception:
                return None
        macro_rows = list(self.preset_rows)
        macro_rows.sort(key=lambda row: (lap_number_value(row) is None, lap_number_value(row) if lap_number_value(row) is not None else 0, lapdist_value(row) is None, lapdist_value(row) if lapdist_value(row) is not None else 0.0))
        self.preset_rows = macro_rows
        for row in self.preset_rows:
            frame = row.get('frame')
            if not frame:
                continue
            frame.pack_forget()
            frame.pack(fill='x', pady=2)
        self._refresh_pit_limiter_indices()

    def run_bot_timing_probe(self):
        """Run a fast timing probe to suggest a stable BOT delay."""

        def _worker():
            try:
                suggested = self.controller.find_minimum_effective_timing()
            except ValueError as exc:
                error_msg = str(exc)
                self.after(0, lambda msg=error_msg: self.app._show_error("Missing keys", msg))
                return
            if suggested is None:
                self.after(0, lambda: self.app._show_warning("Test Result", "No interval between 1 and 120 ms updated the telemetry reliably."))
            else:
                msg = f'Smallest stable pulse found at ~{suggested} ms.\nUse this value in the BOT/custom timings for reliable updates.'
                self.after(0, lambda: self.app._show_info("Test Result", msg))
        threading.Thread(target=_worker, daemon=True).start()

    def _test_single_game_input(self, direction: str) -> None:
        """Fire one increase/decrease pulse for diagnostics."""
        binding = self.controller.key_increase if direction == 'increase' else self.controller.key_decrease
        label = "Increase" if direction == 'increase' else "Decrease"
        if binding is None:
            self.app._show_warning("Missing key", f'The key for {label.lower()} is not set on this tab.')
            return

        def _worker():
            try:
                click_pulse(binding, self.controller.is_float)
                self.after(0, lambda: self.update_status_label(f'Tested {label}', 'green'))
            except Exception as exc:
                error_msg = str(exc)
                self.after(0, lambda msg=error_msg: self.app._show_warning("Test failed", msg or f'Could not test {label}.'))
        threading.Thread(target=_worker, daemon=True).start()

    def _manual_pulse_bind_default_text(self, direction: str) -> str:
        """Return the default label used by a ghost hotkey button."""
        if direction == 'increase':
            if self.is_toggle_action:
                return "Set toggle hotkey"
            if self.is_one_shot_action:
                return "Set action hotkey"
            return "Set toggle hotkey" if self.uses_toggle_key else "Set + hotkey"
        if self.uses_toggle_key:
            return "Not used"
        return "Set - hotkey"

    def _manual_pulse_bind_button(self, direction: str) -> Optional[tk.Button]:
        """Return the UI button associated with a ghost hotkey."""
        return self.btn_manual_increase_bind if direction == 'increase' else self.btn_manual_decrease_bind

    def _manual_pulse_bind_source_id(self, direction: str) -> str:
        """Return the conflict-detection source ID for a ghost hotkey."""
        return self._manual_increase_source_id if direction == 'increase' else self._manual_decrease_source_id

    def _manual_pulse_bind_value(self, direction: str) -> Optional[str]:
        """Return the configured ghost hotkey for the requested direction."""
        return self.manual_increase_bind if direction == 'increase' else self.manual_decrease_bind

    def _set_manual_pulse_bind_value(self, direction: str, code: Optional[str]) -> None:
        """Persist a ghost hotkey binding for one direction."""
        if direction == 'increase':
            self.manual_increase_bind = code
            if self.uses_toggle_key:
                self.manual_decrease_bind = None
        else:
            self.manual_decrease_bind = None if self.uses_toggle_key else code

    def _refresh_manual_pulse_bind_button(self, direction: str) -> None:
        """Refresh the ghost hotkey button label/color."""
        button = self._manual_pulse_bind_button(direction)
        if not button:
            return
        if direction == 'decrease' and self.uses_toggle_key:
            button.config(text="Not used", bg='#f0f0f0', state='disabled')
            return
        code = self._manual_pulse_bind_value(direction)
        if code:
            bg_color = '#90ee90' if 'JOY' in code else '#ADD8E6'
            button.config(text=_format_input_code_label(code), bg=bg_color)
        else:
            button.config(text=self._manual_pulse_bind_default_text(direction), bg='#f0f0f0')

    def bind_manual_pulse_hotkey(self, direction: str) -> None:
        """Bind a joystick/keyboard hotkey that sends one game-key pulse."""
        if direction == 'decrease' and self.uses_toggle_key:
            return
        if self.app.app_state != 'CONFIG':
            self.app._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.app.focus_window()
        button = self._manual_pulse_bind_button(direction)
        if not button:
            return
        previous_bind = self._manual_pulse_bind_value(direction)
        button.config(text='...', bg='yellow')
        self.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self.app._find_hotkey_conflict(code, self._manual_pulse_bind_source_id(direction))
            if conflict:
                self.app._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self._set_manual_pulse_bind_value(direction, previous_bind)
                self._refresh_manual_pulse_bind_button(direction)
                return
            self._set_manual_pulse_bind_value(direction, code)
        elif code == 'CANCEL':
            self._set_manual_pulse_bind_value(direction, None)
        else:
            self._set_manual_pulse_bind_value(direction, previous_bind)
        self._refresh_manual_pulse_bind_button(direction)
        self.app.schedule_preset_save()

    def trigger_manual_pulse_hotkey(self, direction: str) -> None:
        """Send one pulse of the configured game key for a ghost hotkey."""
        binding = self.controller.key_increase if direction == 'increase' else self.controller.key_decrease
        action = "toggle" if self.is_toggle_action else "action" if self.is_one_shot_action else "toggle" if self.uses_toggle_key else "increase" if direction == 'increase' else "decrease"
        if binding is None:
            self.update_status_label("No key set", 'red')
            if self.app:
                self.app.notify_overlay_status(f'{self.label_name}: key for {action} not set', 'red')
            return
        if self.app and (not self.app._commands_allowed()):
            return
        try:
            click_pulse(binding, self.controller.is_float)
        except Exception as exc:
            print(f'[Control tab] Manual hotkey failed ({action}): {exc}')
            self.update_status_label("Manual hotkey failed", 'red')

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = 'normal' if enabled else 'readonly'
        button_state = 'normal' if enabled else 'disabled'
        for row in self.preset_rows:
            try:
                if row.get('entry'):
                    row['entry'].config(state=state)
                if row.get('lap_number_entry'):
                    row['lap_number_entry'].config(state=state)
                if row.get('lap_dist_min_entry'):
                    row['lap_dist_min_entry'].config(state=state)
                if row.get('lap_dist_max_entry'):
                    row['lap_dist_max_entry'].config(state=state)
                if row.get('p2p_chain_entry'):
                    row['p2p_chain_entry'].config(state=state)
                if 'voice_entry' in row:
                    row['voice_entry'].config(state=state)
                delete_button = row.get('delete_button')
                if delete_button:
                    delete_button.config(state=button_state)
            except Exception:
                pass
        if self.btn_add_preset_row:
            self.btn_add_preset_row.config(state=button_state)
        if self.btn_manual_increase_bind:
            self.btn_manual_increase_bind.config(state=button_state)
        if self.btn_manual_decrease_bind:
            decrease_state = 'disabled' if self.uses_toggle_key else button_state
            self.btn_manual_decrease_bind.config(state=decrease_state)
        if self.bounds_supported:
            entry_state = 'normal' if enabled else 'readonly'
            detect_state = 'disabled' if enabled else 'normal'
            if self.entry_bound_min:
                self.entry_bound_min.config(state=entry_state)
            if self.entry_bound_max:
                self.entry_bound_max.config(state=entry_state)
            if self.btn_apply_bounds:
                self.btn_apply_bounds.config(state=button_state)
        if self.is_pit_limiter:
            self._update_pit_limiter_option_state(editing=enabled)
        if self.is_wiper_toggle:
            self._update_wiper_option_state(editing=enabled)
        if self.is_fuel_mixture:
            self._update_fuel_mixture_option_state(editing=enabled)
        if self.is_simple_boolean:
            if self.chk_boolean_pulse:
                self.chk_boolean_pulse.config(state=button_state)
            self._update_boolean_pulse_double_state(editing=enabled)

    def bind_game_key(self, direction: str):
        """
        Bind a game key for increase/decrease.
        
        Args:
            direction: "increase" or "decrease"
        """
        if self.app.app_state != 'CONFIG':
            self.app._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.app.focus_window()
        btn = self.btn_increase if direction == 'increase' else self.btn_decrease
        if direction == 'increase':
            default_text = "Set Hold" if self.is_hybrid_hold else "Set Toggle" if self.is_toggle_action else "Set Trigger" if self.is_one_shot_action else "Set Toggle" if self.uses_toggle_key else "Set Increase (+)"
        else:
            default_text = "Set Decrease (-)"
        original_text = btn['text']
        btn.config(text="PRESS A KEY...", bg='yellow')
        self.update_idletasks()
        try:
            binding, key_name = input_manager.capture_game_action_binding()
        except RuntimeError as exc:
            btn.config(text=original_text, bg='#f0f0f0')
            self.app._show_warning("Joystick output unavailable", str(exc))
            return
        if key_name == 'CANCEL':
            if direction == 'increase':
                self.controller.key_increase = None
                if self.is_pit_limiter and self.uses_toggle_key:
                    self.controller.key_decrease = None
            else:
                self.controller.key_decrease = None
            btn.config(text=default_text, bg='#f0f0f0')
        elif binding is not None:
            if direction == 'increase':
                self.controller.key_increase = binding
                if self.is_pit_limiter and self.uses_toggle_key:
                    self.controller.key_decrease = binding
            else:
                self.controller.key_decrease = binding
            bg_color = '#ADD8E6'
            btn.config(text=f'OK: {str(key_name).upper()}', bg=bg_color)
        else:
            btn.config(text=original_text, bg='#f0f0f0')
        refresh_warning = getattr(self.app, '_refresh_bounds_warning', None)
        if callable(refresh_warning):
            refresh_warning()
        self.app.schedule_preset_save()

    def _on_boolean_pulse_toggle(self) -> None:
        """Persist boolean one-shot toggle settings."""
        self.controller.boolean_pulse_only = bool(self.boolean_pulse_only.get())
        if not self.boolean_pulse_only.get():
            self.boolean_pulse_double.set(False)
            self.controller.boolean_pulse_double = False
        self._update_boolean_pulse_double_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_boolean_pulse_double_toggle(self) -> None:
        """Persist boolean double-tap settings."""
        self.controller.boolean_pulse_double = bool(self.boolean_pulse_double.get())
        self.app.schedule_preset_save()

    def _update_boolean_pulse_double_state(self, editing: bool=True) -> None:
        """Enable double-tap option only when one-shot is enabled."""
        if not self.chk_boolean_pulse_double:
            return
        if not editing:
            state = 'disabled'
        else:
            state = 'normal' if self.boolean_pulse_only.get() else 'disabled'
        self.chk_boolean_pulse_double.config(state=state)
        if not self.boolean_pulse_only.get():
            self.boolean_pulse_double.set(False)
            self.controller.boolean_pulse_double = False

    def _config_bind_button(self, button: tk.Button, data_store: Dict[str, Any]):
        """Configure binding button behavior."""

        def on_click():
            if self.app.app_state != 'CONFIG':
                self.app._show_info("Warning", "Enter CONFIG mode first.")
                return
            self.app.focus_window()
            default_text = "Macro hotkey"
            existing_bind = data_store.get('bind')
            button.config(text='...', bg='yellow')
            self.update_idletasks()
            code = input_manager.capture_any_input()
            if code and code != 'CANCEL':
                conflict = self.app._find_hotkey_conflict(code, data_store.get('source_id'))
                if conflict:
                    self.app._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                    if existing_bind:
                        bg_color = '#90ee90' if 'JOY' in existing_bind else '#ADD8E6'
                        button.config(text=_format_input_code_label(existing_bind), bg=bg_color)
                    else:
                        button.config(text=default_text, bg='#f0f0f0')
                    return
                data_store['bind'] = code
                bg_color = '#90ee90' if 'JOY' in code else '#ADD8E6'
                button.config(text=_format_input_code_label(code), bg=bg_color)
            elif code == 'CANCEL':
                data_store['bind'] = None
                button.config(text=default_text, bg='#f0f0f0')
            self.app.schedule_preset_save()
        button.config(command=on_click)

    def add_preset_row(self, existing: Optional[Dict[str, Any]]=None, is_reset: bool=False, pack_row: bool=True):
        """Add a preset row to the UI."""
        is_reset = False
        frame = tk.Frame(self.presets_container)
        if pack_row:
            frame.pack(fill='x', pady=2)
        self._configure_macro_grid(frame)
        columns = self._macro_column_map()
        row_index = len(self.preset_rows)
        value_entry = None
        value_var = None
        if self.is_one_shot_action:
            value_var = tk.StringVar(value="Toggle" if self.is_toggle_action else "Trigger")
            value_entry = tk.Label(frame, width=8, textvariable=value_var, anchor='w')
            value_entry.grid(row=0, column=columns['value'], sticky='ew', padx=5)
        elif self.is_pit_limiter or self.is_hybrid_hold:
            value_var = tk.StringVar(value='ON')
            value_entry = ttk.Combobox(frame, width=8, textvariable=value_var, values=['ON', 'OFF'], state='readonly')
            value_entry.grid(row=0, column=columns['value'], sticky='ew', padx=5)
            value_entry.bind('<<ComboboxSelected>>', lambda _event: self.app.schedule_preset_save())
            if self.app.app_state != 'CONFIG':
                value_entry.config(state='disabled')
        else:
            value_entry = ttk.Entry(frame, width=8)
            value_entry.grid(row=0, column=columns['value'], sticky='ew', padx=5)
            self._bind_autosave_entry(value_entry)
            if self.app.app_state != 'CONFIG':
                value_entry.config(state='readonly')
        lap_number_entry = None
        lapdist_container = None
        lapdist_to_label = None
        lap_dist_min_entry = None
        lap_dist_max_entry = None
        p2p_chain_entry = None
        bind_button = None
        if not self.is_pit_limiter:
            bind_button = tk.Button(frame, text="Macro hotkey", width=12)
            bind_button.grid(row=0, column=columns['bind'], sticky='ew', padx=5)
        voice_entry = None
        if VOICE_FEATURES_ENABLED:
            voice_entry = ttk.Entry(frame, width=18)
            voice_entry.grid(row=0, column=columns['voice'], sticky='ew', padx=5)
            voice_entry.insert(0, '')
            self._bind_autosave_entry(voice_entry)
            if self.app.app_state != 'CONFIG':
                voice_entry.config(state='readonly')
        row_data = {'frame': frame, 'entry': None if self.is_one_shot_action else value_entry, 'value_display': value_entry if self.is_one_shot_action else None, 'value_var': value_var, 'lap_number_entry': lap_number_entry, 'lap_dist_min_entry': lap_dist_min_entry, 'lap_dist_max_entry': lap_dist_max_entry, 'lapdist_container': lapdist_container, 'lapdist_to_label': lapdist_to_label, 'bind': None, 'is_reset': is_reset, 'voice_entry': voice_entry, 'delete_button': None, 'bind_button': bind_button, 'capture_button': None, 'source_id': f'control:{id(frame)}', 'p2p_chain_entry': p2p_chain_entry}
        if bind_button:
            self._config_bind_button(bind_button, row_data)
        if existing:
            if self.is_one_shot_action:
                if value_var is not None:
                    value_var.set("Toggle" if self.is_toggle_action else "Trigger")
            else:
                entry_state = 'normal'
                if self.app.app_state != 'CONFIG':
                    entry_state = 'disabled' if self.is_pit_limiter or self.is_hybrid_hold else 'readonly'
                value_entry.config(state='readonly' if self.is_pit_limiter or self.is_hybrid_hold else 'normal')
                if self.is_pit_limiter or self.is_hybrid_hold:
                    entry_value = self._tearoff_label_for_value(existing.get('val', ''))
                    value_entry.set(entry_value)
                else:
                    value_entry.delete(0, tk.END)
                    value_entry.insert(0, existing.get('val', ''))
                if self.app.app_state != 'CONFIG':
                    value_entry.config(state=entry_state)
            if p2p_chain_entry is not None:
                chain_text = existing.get('p2p_chain_count', '')
                if not chain_text:
                    chain_text = existing.get('p2p_chain_group', '')
                p2p_chain_entry.config(state='normal')
                p2p_chain_entry.delete(0, tk.END)
                p2p_chain_entry.insert(0, chain_text)
                if self.app.app_state != 'CONFIG':
                    p2p_chain_entry.config(state='readonly')
            row_data['bind'] = existing.get('bind')
            if row_data['bind'] and bind_button:
                bg_color = '#90ee90' if 'JOY' in row_data['bind'] else '#ADD8E6'
                bind_button.config(text=_format_input_code_label(row_data['bind']), bg=bg_color)
            if voice_entry is not None:
                voice_text = existing.get('voice_phrase', '')
                voice_entry.config(state='normal')
                voice_entry.delete(0, tk.END)
                voice_entry.insert(0, voice_text)
                if self.app.app_state != 'CONFIG':
                    voice_entry.config(state='readonly')
        if not self.is_pit_limiter:
            delete_button = tk.Button(frame, text='X', fg='red', width=2, command=lambda r=row_data: self.remove_row(r))
            delete_button.grid(row=0, column=columns['delete'], sticky='w', padx=5)
            if self.app.app_state != 'CONFIG':
                delete_button.config(state='disabled')
            row_data['delete_button'] = delete_button
        if self.is_pit_limiter and (not existing) and (not is_reset):
            default_value = None
            if row_index == 0:
                default_value = 'ON'
            elif row_index == 1:
                default_value = 'OFF'
            if default_value is not None:
                previous_state = value_entry.cget('state')
                if previous_state != 'normal':
                    value_entry.config(state='normal')
                if self.is_pit_limiter or self.is_one_shot_action:
                    value_entry.set(default_value)
                else:
                    value_entry.delete(0, tk.END)
                    value_entry.insert(0, default_value)
                if previous_state != 'normal':
                    value_entry.config(state=previous_state)
        self.preset_rows.append(row_data)
        if self.app.discreet_mode.get():
            self._apply_discreet_mode_to_row(row_data, True)
        return row_data

    def remove_row(self, row_data: Dict[str, Any]):
        """Remove a preset row."""
        if self.app.app_state != 'CONFIG':
            self.app._show_info("Warning", "Enter CONFIG mode first.")
            return
        capture = getattr(self.app, 'lapdist_capture', None)
        if capture is not None:
            capture.cancel_for_row(self, row_data)
        row_data['frame'].destroy()
        if row_data in self.preset_rows:
            self.preset_rows.remove(row_data)
        self._refresh_pit_limiter_indices()
        self.app.schedule_preset_save()

    def monitor_loop(self):
        """Background loop to monitor current value."""
        if not self.running:
            return
        value = self.controller.read_telemetry()
        if value is None:
            text = '--'
        else:
            text = f'{value:.3f}' if self.controller.is_float else str(value)
        try:
            self.lbl_monitor.config(text=f'Current: {text}')
        except Exception:
            pass
        if self.running:
            self.after(500, self.monitor_loop)

    @staticmethod
    def _safe_entry_value(widget: Optional[tk.Widget]) -> str:
        """Return entry text safely even if widget was destroyed."""
        if widget is None:
            return ''
        try:
            if hasattr(widget, 'winfo_exists') and (not widget.winfo_exists()):
                return ''
            return widget.get()
        except Exception:
            return ''

    @staticmethod
    def _safe_widget_text(widget: Optional[tk.Widget], default: str='') -> str:
        """Return widget text safely even if widget was destroyed."""
        if widget is None:
            return default
        try:
            if hasattr(widget, 'winfo_exists') and (not widget.winfo_exists()):
                return default
            return widget.cget('text')
        except Exception:
            return default

    def _is_tearoff_control(self) -> bool:
        """Return True when this control is a tear-off visor toggle."""
        name = (self.controller.var_name or '').lower()
        return 'tearoff' in name or 'tear_off' in name

    @staticmethod
    def _tearoff_label_for_value(value: Any) -> str:
        """Normalize tear-off macro values to ON/OFF labels."""
        if value is None:
            return 'OFF'
        text = str(value).strip().lower()
        if text in {'1', 'on', 'true', 'yes'}:
            return 'ON'
        if text in {'0', 'off', 'false', 'no', ''}:
            return 'OFF'
        try:
            numeric = float(text)
            return 'ON' if numeric > 0 else 'OFF'
        except Exception:
            return 'OFF'

    @staticmethod
    def _tearoff_value_for_label(value: str) -> str:
        """Convert ON/OFF labels into stored tear-off values."""
        return '1' if str(value).strip().upper() == 'ON' else '0'

    def _preset_value_for_row(self, row: Dict[str, Any]) -> str:
        """Return normalized preset value for storage."""
        if self.is_one_shot_action:
            return '1'
        value = self._safe_entry_value(row.get('entry'))
        if self.is_pit_limiter or self.is_hybrid_hold:
            return self._tearoff_value_for_label(value)
        return value

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        decrease_key = self.controller.key_decrease
        decrease_text = self._safe_widget_text(self.btn_decrease, '')
        if self.uses_toggle_key:
            decrease_key = None
            decrease_text = "Not used"
        config = {'meta_var': self.controller.var_name, 'meta_float': self.controller.is_float, 'key_increase': self.controller.key_increase, 'key_increase_text': self._safe_widget_text(self.btn_increase, ''), 'key_decrease': decrease_key, 'key_decrease_text': decrease_text, 'ghost_increase_bind': self.manual_increase_bind, 'ghost_decrease_bind': None if self.uses_toggle_key else self.manual_decrease_bind, 'bool_pulse_only': self.boolean_pulse_only.get() if self.is_simple_boolean else False, 'bool_pulse_double': self.boolean_pulse_double.get() if self.is_simple_boolean else False, 'value_bounds': {'min': self.bound_min_value.get().strip(), 'max': self.bound_max_value.get().strip()} if self.bounds_supported else {}, 'presets': [{'val': self._preset_value_for_row(row), 'lap_number': self._safe_entry_value(row.get('lap_number_entry')).strip(), 'lap_dist_min': self._safe_entry_value(row.get('lap_dist_min_entry')), 'lap_dist_max': self._safe_entry_value(row.get('lap_dist_max_entry')), 'bind': row['bind'], 'voice_phrase': self._safe_entry_value(row.get('voice_entry'))} for row in self.preset_rows]}
        for preset in config['presets']:
            preset.pop('lap_number', None)
            preset.pop('lap_dist_min', None)
            preset.pop('lap_dist_max', None)
        if self.is_wiper_toggle and True:
            config['wiper_auto'] = {'enabled': self.wiper_auto.get(), 'precip_on': self.wiper_precip_on.get(), 'precip_off': self.wiper_precip_off.get(), 'humanize_delay_min': self.wiper_humanize_delay_min.get(), 'humanize_delay_max': self.wiper_humanize_delay_max.get()}
        return config

    def apply_key_config(self, config: Dict[str, Any]) -> None:
        """Apply only increase/decrease key settings to the tab."""
        if not config:
            return
        if 'key_increase' in config:
            increase_key = config.get('key_increase')
            self.controller.key_increase = _normalize_game_input_binding(increase_key)
            self.btn_increase.config(text=config.get('key_increase_text', "Set Hold" if self.is_hybrid_hold else "Set Toggle" if self.is_toggle_action else "Set Trigger" if self.is_one_shot_action else "Set Toggle" if self.uses_toggle_key else "Set Increase (+)"))
        if 'key_decrease' in config and (not self.uses_toggle_key):
            decrease_key = config.get('key_decrease')
            self.controller.key_decrease = _normalize_game_input_binding(decrease_key)
            self.btn_decrease.config(text=config.get('key_decrease_text', "Set Decrease (-)"))
        elif self.uses_toggle_key:
            self.controller.key_decrease = None
            self.btn_decrease.config(text="Not used", state='disabled')
            if self.is_pit_limiter and self.controller.key_increase is not None:
                self.controller.key_decrease = self.controller.key_increase
        if 'ghost_increase_bind' in config:
            self.manual_increase_bind = config.get('ghost_increase_bind')
        if self.uses_toggle_key:
            self.manual_decrease_bind = None
        elif 'ghost_decrease_bind' in config:
            self.manual_decrease_bind = config.get('ghost_decrease_bind')
        if 'ghost_increase_bind' in config or 'ghost_decrease_bind' in config:
            self._refresh_manual_pulse_bind_button('increase')
            self._refresh_manual_pulse_bind_button('decrease')
        refresh_warning = getattr(self.app, '_refresh_bounds_warning', None)
        if callable(refresh_warning):
            refresh_warning()

    def destroy(self):
        """Ensure monitoring loop stops when widget is destroyed."""
        self.running = False
        capture = getattr(self.app, 'lapdist_capture', None)
        if capture is not None:
            capture.cancel_for_owner(self)
        super().destroy()

    def set_config(self, config: Dict[str, Any]):
        """Load configuration."""
        capture = getattr(self.app, 'lapdist_capture', None)
        if capture is not None:
            capture.cancel_for_owner(self)
        if not config:
            return
        increase_key = config.get('key_increase')
        decrease_key = config.get('key_decrease')
        self.controller.key_increase = _normalize_game_input_binding(increase_key)
        if not self.uses_toggle_key:
            self.controller.key_decrease = _normalize_game_input_binding(decrease_key)
        else:
            self.controller.key_decrease = None
            if self.is_pit_limiter and self.controller.key_increase is not None:
                self.controller.key_decrease = self.controller.key_increase
        self.btn_increase.config(text=config.get('key_increase_text', "Set Hold" if self.is_hybrid_hold else "Set Toggle" if self.is_toggle_action else "Set Trigger" if self.is_one_shot_action else "Set Toggle" if self.uses_toggle_key else "Set Increase (+)"))
        if not self.uses_toggle_key:
            self.btn_decrease.config(text=config.get('key_decrease_text', "Set Decrease (-)"))
        else:
            self.btn_decrease.config(text="Not used", state='disabled')
        self.manual_increase_bind = config.get('ghost_increase_bind')
        self.manual_decrease_bind = None if self.uses_toggle_key else config.get('ghost_decrease_bind')
        self._refresh_manual_pulse_bind_button('increase')
        self._refresh_manual_pulse_bind_button('decrease')
        if self.is_simple_boolean:
            pulse_only = bool(config.get('bool_pulse_only', False))
            self.boolean_pulse_only.set(pulse_only)
            self.controller.boolean_pulse_only = pulse_only
            pulse_double = bool(config.get('bool_pulse_double', False))
            self.boolean_pulse_double.set(pulse_double)
            self.controller.boolean_pulse_double = pulse_double
            self._update_boolean_pulse_double_state(editing=self.app.app_state == 'CONFIG')
        else:
            self.boolean_pulse_only.set(False)
            self.boolean_pulse_double.set(False)
            self.controller.boolean_pulse_only = False
            self.controller.boolean_pulse_double = False
        if self.bounds_supported and 'value_bounds' in config:
            bounds_cfg = config.get('value_bounds', {}) if isinstance(config.get('value_bounds'), dict) else {}
            self.bound_min_value.set(str(bounds_cfg.get('min', '') or ''))
            self.bound_max_value.set(str(bounds_cfg.get('max', '') or ''))
            self.apply_bounds_from_entries(show_errors=False, persist=False)
        for row in list(self.preset_rows):
            row['frame'].destroy()
        self.preset_rows.clear()
        saved_presets = [preset for preset in config.get('presets', []) if not preset.get('is_reset')]
        bulk_load = len(saved_presets) > 20
        if self.is_pit_limiter:
            for preset in saved_presets[:2]:
                self.add_preset_row(existing=preset, is_reset=False, pack_row=not bulk_load)
            while len(self.preset_rows) < 2:
                self.add_preset_row()
        else:
            for preset in saved_presets:
                self.add_preset_row(existing=preset, is_reset=False, pack_row=not bulk_load)
            if not self.preset_rows:
                for _ in range(4):
                    self.add_preset_row(pack_row=not bulk_load)
        if bulk_load:
            for row in self.preset_rows:
                frame = row.get('frame')
                if frame and frame.winfo_manager() != 'pack':
                    frame.pack(fill='x', pady=2)
        self._sort_lapdist_rows()
        self._refresh_pit_limiter_indices()
        self._apply_pit_limiter_config(config)
        self._apply_p2p_chain_config(config)
        self._apply_fuel_mixture_config(config)
        self._apply_hybrid_hold_config(config)
        self._apply_wiper_config(config)
        self._apply_lap_trigger_config(config)

    def _refresh_pit_limiter_indices(self) -> None:
        """No-op now that pit limiter targets derive from macro values."""
        return

    def _apply_pit_limiter_config(self, config: Dict[str, Any]) -> None:
        """Apply pit limiter automation settings from config."""
        if not self.is_pit_limiter:
            return
        self.pit_limiter_auto.set(False)
        self.pit_limiter_force_on.set(False)
        self.pit_limiter_emergency_reenable.set(False)
        self.pit_limiter_trigger_on_approach.set(False)

    def _apply_p2p_chain_config(self, config: Dict[str, Any]) -> None:
        """Apply push-to-pass chaining settings from config."""
        if not self.is_push_to_pass:
            return
        self._update_pit_limiter_option_state(editing=self.app.app_state == 'CONFIG')

    def _apply_fuel_mixture_config(self, config: Dict[str, Any]) -> None:
        """Apply fuel mixture flag automation settings from config."""
        if not self.is_fuel_mixture:
            return
        self.fuel_yellow_enabled.set(False)
        self.fuel_green_enabled.set(False)

    def _apply_hybrid_hold_config(self, config: Dict[str, Any]) -> None:
        """Apply hybrid hold/release automation settings from config."""
        if not self.is_hybrid_hold:
            return

    def _apply_wiper_config(self, config: Dict[str, Any]) -> None:
        """Apply windshield wiper automation settings from config."""
        if not self.is_wiper_toggle:
            return
        wiper_cfg = config.get('wiper_auto', {}) if isinstance(config, dict) else {}
        self.wiper_auto.set(wiper_cfg.get('enabled', True))
        self.wiper_precip_on.set(str(wiper_cfg.get('precip_on', '0.04')))
        self.wiper_precip_off.set(str(wiper_cfg.get('precip_off', '0.03')))
        self.wiper_humanize_delay_min.set(str(wiper_cfg.get('humanize_delay_min', '0.0')))
        self.wiper_humanize_delay_max.set(str(wiper_cfg.get('humanize_delay_max', '0.0')))
        self._update_wiper_option_state(editing=self.app.app_state == 'CONFIG')

    def _apply_lap_trigger_config(self, config: Dict[str, Any]) -> None:
        """Apply random lap trigger settings from config."""
        self.lap_trigger_enabled.set(False)

    def _on_pit_limiter_setting_change(self) -> None:
        """Persist pit limiter automation settings and refresh state."""
        self._update_pit_limiter_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_wiper_setting_change(self) -> None:
        """Persist windshield wiper automation settings and refresh state."""
        self._update_wiper_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_hybrid_hold_setting_change(self) -> None:
        """Persist hybrid hold settings and refresh widget state."""
        self._update_hybrid_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_fuel_mixture_setting_change(self) -> None:
        """Persist fuel mixture flag automation settings."""
        self._update_fuel_mixture_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_lap_trigger_setting_change(self) -> None:
        """Persist per-lap random trigger settings and refresh state."""
        self._update_lap_trigger_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _update_pit_limiter_option_state(self, editing: bool) -> None:
        """Enable or disable pit limiter option widgets."""
        if not self.is_pit_limiter:
            return
        enabled = self.pit_limiter_auto.get()
        humanize = self.pit_limiter_humanize.get()
        base_state = 'normal' if editing else 'disabled'
        if self.chk_pit_limiter_auto:
            self.chk_pit_limiter_auto.config(state=base_state)
        if self.chk_pit_limiter_configured:
            self.chk_pit_limiter_configured.config(state=base_state)
        if self.chk_pit_limiter_humanize:
            self.chk_pit_limiter_humanize.config(state=base_state)
        if self.chk_pit_limiter_force_on:
            self.chk_pit_limiter_force_on.config(state=base_state)
        if self.chk_pit_limiter_emergency_reenable:
            self.chk_pit_limiter_emergency_reenable.config(state=base_state)
        if self.chk_pit_limiter_trigger_on_approach:
            self.chk_pit_limiter_trigger_on_approach.config(state=base_state)
        entry_state = 'normal' if editing and enabled and humanize else 'disabled'
        if self.entry_pit_limiter_delay_min:
            self.entry_pit_limiter_delay_min.config(state=entry_state)
        if self.entry_pit_limiter_delay_max:
            self.entry_pit_limiter_delay_max.config(state=entry_state)
        approach_entry_state = 'normal' if editing and enabled and humanize and self.pit_limiter_trigger_on_approach.get() else 'disabled'
        if self.entry_pit_limiter_approach_delay_min:
            self.entry_pit_limiter_approach_delay_min.config(state=approach_entry_state)
        if self.entry_pit_limiter_approach_delay_max:
            self.entry_pit_limiter_approach_delay_max.config(state=approach_entry_state)
        poll_state = 'normal' if editing and enabled else 'disabled'
        if self.entry_pit_limiter_poll_hz_min:
            self.entry_pit_limiter_poll_hz_min.config(state=poll_state)
        if self.entry_pit_limiter_poll_hz_max:
            self.entry_pit_limiter_poll_hz_max.config(state=poll_state)

    def _update_fuel_mixture_option_state(self, editing: bool) -> None:
        """Enable or disable fuel mixture automation widgets."""
        if not self.is_fuel_mixture:
            return
        base_state = 'normal' if editing else 'disabled'
        yellow_state = 'normal' if editing and self.fuel_yellow_enabled.get() else 'disabled'
        green_state = 'normal' if editing and self.fuel_green_enabled.get() else 'disabled'
        if self.chk_fuel_yellow_enabled:
            self.chk_fuel_yellow_enabled.config(state=base_state)
        if self.chk_fuel_green_enabled:
            self.chk_fuel_green_enabled.config(state=base_state)
        if self.entry_fuel_yellow_delay_min:
            self.entry_fuel_yellow_delay_min.config(state=yellow_state)
        if self.entry_fuel_yellow_delay_max:
            self.entry_fuel_yellow_delay_max.config(state=yellow_state)
        if self.entry_fuel_green_delay_min:
            self.entry_fuel_green_delay_min.config(state=green_state)
        if self.entry_fuel_green_delay_max:
            self.entry_fuel_green_delay_max.config(state=green_state)

    def _update_hybrid_option_state(self, editing: bool) -> None:
        """Enable or disable hybrid hold option widgets."""
        if not self.is_hybrid_hold:
            return

    def _update_wiper_option_state(self, editing: bool) -> None:
        """Enable or disable windshield wiper option widgets."""
        if not self.is_wiper_toggle:
            return
        enabled = self.wiper_auto.get()
        base_state = 'normal' if editing else 'disabled'
        entry_state = 'normal' if editing and enabled else 'disabled'
        if self.chk_wiper_auto:
            self.chk_wiper_auto.config(state=base_state)
        if self.entry_wiper_precip_on:
            self.entry_wiper_precip_on.config(state=entry_state)
        if self.entry_wiper_precip_off:
            self.entry_wiper_precip_off.config(state=entry_state)
        if self.entry_wiper_delay_min:
            self.entry_wiper_delay_min.config(state=entry_state)
        if self.entry_wiper_delay_max:
            self.entry_wiper_delay_max.config(state=entry_state)

    def _update_lap_trigger_option_state(self, editing: bool) -> None:
        """Enable or disable random lap trigger option widgets."""
        if not self.supports_random_lap_trigger:
            return
__all__ = ['ControlTab']
