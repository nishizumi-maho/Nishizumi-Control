from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *
from ..core import sanitize_second_throttle_macros, sanitize_second_throttle_pit_macro

class PresetsMixin:

    def update_safe_mode(self):
        """Update safe mode settings."""
        input_manager.set_safe_mode(self.use_keyboard_only.get())
        if not self.use_keyboard_only.get():
            input_manager.connect_allowed_devices(input_manager.allowed_devices, force=True)

    def trigger_safe_mode_update(self):
        """Trigger safe mode update with restart."""
        new_value = self.use_keyboard_only.get()
        if self._ask_ok_cancel("Restart Required", "A restart is needed to apply Keyboard Only Mode. Confirm?"):
            self.save_config()
            restart_program()
        else:
            self.use_keyboard_only.set(not new_value)
            self.save_config()
        self.update_safe_mode()

    def open_device_manager(self):
        """Open device management dialog."""
        if self.use_keyboard_only.get():
            self._show_info("Keyboard Mode", "Turn off 'Keyboard Only Mode' to manage joystick devices.")
            return
        DeviceSelector(self.root, input_manager.allowed_devices, self.update_allowed_devices)

    def update_allowed_devices(self, new_list: List[str]):
        """Update list of allowed devices."""
        input_manager.allowed_devices = list(new_list)
        input_manager.connect_allowed_devices(input_manager.allowed_devices, force=True)
        self.save_config()

    def _normalize_surface_label(self, value: Optional[str]) -> str:
        """Return a valid surface preset label."""
        if not value:
            return DEFAULT_SURFACE_PRESET
        label = str(value).strip().upper()
        if label in SURFACE_PRESET_KEYS:
            return label
        return DEFAULT_SURFACE_PRESET

    def _default_surface_preset(self) -> Dict[str, Any]:
        """Return a blank preset payload for a surface."""
        return {'active_vars': list(self.active_vars), 'tabs': {}, 'combo': {}}

    def _strip_tab_key_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Return a tab config without per-car increase/decrease keys."""
        if not config:
            return {}
        stripped = dict(config)
        for key in ('key_increase', 'key_decrease', 'key_increase_text', 'key_decrease_text'):
            stripped.pop(key, None)
        return stripped

    def _collect_car_key_config(self, car: str) -> None:
        """Persist increase/decrease keys per car (shared across tracks)."""
        if not car:
            return
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        car_keys = self.saved_presets[car].get('_car_keys')
        if not isinstance(car_keys, dict):
            car_keys = {}
        for var_name, tab in self.tabs.items():
            car_keys[var_name] = {'key_increase': tab.controller.key_increase, 'key_decrease': tab.controller.key_decrease, 'key_increase_text': tab.btn_increase['text'], 'key_decrease_text': tab.btn_decrease['text'], 'ghost_increase_bind': tab.manual_increase_bind, 'ghost_decrease_bind': None if tab.uses_toggle_key else tab.manual_decrease_bind}
        self.saved_presets[car]['_car_keys'] = car_keys

    def _apply_car_key_config(self, car: str) -> None:
        """Apply stored per-car increase/decrease keys to tabs."""
        car_keys = self.saved_presets.get(car, {}).get('_car_keys')
        if not isinstance(car_keys, dict):
            return
        for var_name, tab in self.tabs.items():
            key_cfg = car_keys.get(var_name)
            if not isinstance(key_cfg, dict) and var_name in WIPER_TOGGLE_VARS:
                key_cfg = self._wiper_alias_config(car_keys)
            if isinstance(key_cfg, dict):
                tab.apply_key_config(key_cfg)

    def _ensure_track_surface_presets(self, car: str, track: str) -> Dict[str, Any]:
        """Ensure a track entry uses the surface preset structure."""
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        entry = self.saved_presets[car].get(track)
        if not isinstance(entry, dict):
            entry = {}
        if 'surface_presets' in entry:
            surface_presets = entry.get('surface_presets')
            if not isinstance(surface_presets, dict):
                surface_presets = {}
            normalized_presets: Dict[str, Any] = {}
            for key, value in surface_presets.items():
                normalized_presets[self._normalize_surface_label(key)] = value
            entry['surface_presets'] = normalized_presets
        else:
            legacy = entry if entry else self._default_surface_preset()
            entry = {'surface_presets': {DEFAULT_SURFACE_PRESET: legacy, 'WET': copy.deepcopy(legacy)}, 'active_surface': DEFAULT_SURFACE_PRESET}
        entry['active_surface'] = self._normalize_surface_label(entry.get('active_surface'))
        for surface in SURFACE_PRESET_KEYS:
            if surface not in entry['surface_presets']:
                entry['surface_presets'][surface] = self._default_surface_preset()
        self.saved_presets[car][track] = entry
        return entry

    def _get_surface_preset(self, car: str, track: str, surface: Optional[str]=None) -> Dict[str, Any]:
        """Return the preset payload for the selected surface."""
        entry = self._ensure_track_surface_presets(car, track)
        surface_key = self._normalize_surface_label(surface or entry.get('active_surface'))
        return entry['surface_presets'][surface_key]

    def _set_surface_selection(self, surface: str) -> None:
        """Update current surface selection and UI."""
        surface_key = self._normalize_surface_label(surface)
        self.current_surface = surface_key
        if getattr(self, 'combo_surface', None):
            self.combo_surface.set(surface_key)

    def _selected_surface(self) -> str:
        """Return the surface selected in the UI or current state."""
        if getattr(self, 'combo_surface', None):
            return self._normalize_surface_label(self.combo_surface.get())
        return self._normalize_surface_label(self.current_surface)

    def _update_surface_selector(self, car: str, track: str) -> None:
        """Sync surface selector with saved preset data."""
        if not car or not track:
            self._set_surface_selection(DEFAULT_SURFACE_PRESET)
            return
        entry = self._ensure_track_surface_presets(car, track)
        self._set_surface_selection(entry.get('active_surface', DEFAULT_SURFACE_PRESET))

    def update_preset_ui(self):
        """Update car/track combo boxes."""
        cars = sorted(list(self.saved_presets.keys()))
        self.combo_car['values'] = [c for c in cars if c]
        if self.current_car and self.current_car in cars:
            self.combo_car.set(self.current_car)
            self.on_car_selected(None)
            if self.current_track:
                self._update_surface_selector(self.current_car, self.current_track)
        self._update_header_context()
        self._update_preset_lock_state()

    def _on_lock_preset_selection_toggle(self) -> None:
        """Toggle manual preset selection lock."""
        self._update_preset_lock_state()
        self.schedule_save()

    def _update_preset_lock_state(self) -> None:
        """Enable or disable manual preset selection controls."""
        locked = self.lock_preset_selection.get()
        state = 'disabled' if locked else 'normal'
        self.combo_car.configure(state=state)
        self.combo_track.configure(state=state)
        if getattr(self, 'combo_surface', None):
            surface_state = 'disabled' if locked else 'readonly'
            self.combo_surface.configure(state=surface_state)
        load_state = tk.DISABLED if locked else tk.NORMAL
        self.btn_load_preset.configure(state=load_state)
        self.btn_save_preset.configure(state=tk.NORMAL)
        self.btn_clear_preset.configure(state=tk.NORMAL)

    def on_car_selected(self, _event):
        """Handle car selection."""
        car = self.combo_car.get()
        if car in self.saved_presets:
            tracks = sorted([t for t in self.saved_presets[car].keys() if t not in {'_overlay', '_overlay_feedback', '_car_keys', '_ghost_keys'}])
            self.combo_track['values'] = tracks
        else:
            self.combo_track['values'] = []
        self.current_car = car
        self.sync_ghost_rows_to_car(car)
        if self.current_track:
            self._update_surface_selector(car, self.current_track)
        self._update_header_context()
        self._refresh_surface_dc_profile_rows()

    def on_track_selected(self, _event) -> None:
        """Handle track selection."""
        track = self.combo_track.get()
        self.current_track = track
        self._update_surface_selector(self.current_car, track)
        self._update_header_context()

    def on_surface_selected(self, _event) -> None:
        """Handle surface selection changes."""
        surface = self._selected_surface()
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        self.current_surface = surface
        if not car or not track:
            return
        self._set_active_surface(car, track, surface, load=True, notify=False)

    def auto_fill_ui(self, car: str, track: str):
        """Auto-fill car and track in UI."""
        self.current_car = car
        self.current_track = track
        self.combo_car.set(car)
        self.on_car_selected(None)
        self.combo_track.set(track)
        self._update_surface_selector(car, track)

    def _set_active_surface(self, car: str, track: str, surface: str, *, load: bool=True, notify: bool=False) -> None:
        """Set the active surface preset for a track."""
        surface_key = self._normalize_surface_label(surface)
        entry = self._ensure_track_surface_presets(car, track)
        entry['active_surface'] = surface_key
        self.saved_presets[car][track] = entry
        self._set_surface_selection(surface_key)
        if load:
            self.load_specific_preset(car, track, surface=surface_key)
        if notify:
            color = 'green' if surface_key == 'DRY' else 'deepskyblue'
            self.notify_overlay_status(f'Preset {surface_key}', color)
        self.schedule_save()

    def action_save_preset(self):
        """Save current configuration as preset."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        if not car or not track:
            self._show_warning("Error", "Set the car and the track.")
            return
        self._save_preset_for_pair(car, track, show_message=True)

    def _save_preset_for_pair(self, car: str, track: str, show_message: bool=False) -> None:
        """Save preset data for a specific car/track pair."""
        surface = self._selected_surface()
        if self.overlay_tab:
            self.overlay_tab.collect_for_car(car)
        if car not in self.car_overlay_feedback:
            self.car_overlay_feedback[car] = DEFAULT_OVERLAY_FEEDBACK.copy()
        self._collect_car_key_config(car)
        current_data = {'active_vars': self.active_vars, 'tabs': {}, 'combo': self.combo_tab.get_config() if self.combo_tab else {}}
        for var_name, tab in self.tabs.items():
            current_data['tabs'][var_name] = self._strip_tab_key_config(tab.get_config())
        entry = self._ensure_track_surface_presets(car, track)
        entry['surface_presets'][surface] = current_data
        entry['active_surface'] = surface
        self.saved_presets[car][track] = entry
        self._set_surface_selection(surface)
        if car not in self.car_overlay_config:
            self.car_overlay_config[car] = {}
        self.saved_presets[car]['_overlay'] = self.car_overlay_config[car]
        self.saved_presets[car]['_overlay_feedback'] = self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy())
        self.save_config()
        self.auto_load_attempted.discard((car, track))
        if (car, track) == (self.current_car, self.current_track):
            self.register_current_listeners()
        self.update_preset_ui()
        if show_message:
            self._show_info("Saved", f'Preset saved for {car} @ {track} ({surface})')

    def load_specific_preset(self, car: str, track: str, *, surface: Optional[str]=None):
        """Load a specific car/track preset."""
        if car not in self.saved_presets or track not in self.saved_presets[car]:
            return
        entry = self._ensure_track_surface_presets(car, track)
        surface_key = self._normalize_surface_label(surface or entry.get('active_surface'))
        signature = (car.strip(), track.strip(), surface_key)
        if signature == self._last_loaded_preset_signature and time.time() - self._last_loaded_preset_time < 10.0:
            return
        self._last_loaded_preset_signature = signature
        self._last_loaded_preset_time = time.time()
        data = entry['surface_presets'].get(surface_key, self._default_surface_preset())
        entry['active_surface'] = surface_key
        self.saved_presets[car][track] = entry
        self._set_surface_selection(surface_key)
        tabs_data = data.get('tabs', {})
        combo_data = data.get('combo')
        configs_applied_in_rebuild = False
        active_vars = data.get('active_vars')
        if active_vars:
            normalized_vars = [_normalize_var_tuple(item) for item in active_vars]
            missing_tabs = any((name not in self.tabs for name, _f, _b in normalized_vars))
            if normalized_vars != self.active_vars or missing_tabs:
                self.rebuild_tabs(normalized_vars, tab_configs=tabs_data, combo_config=combo_data)
                configs_applied_in_rebuild = True
        wiper_config_applied = False
        if not configs_applied_in_rebuild:
            for var_name, config in tabs_data.items():
                if var_name in self.tabs:
                    self.tabs[var_name].set_config(config)
                    if var_name in WIPER_TOGGLE_VARS:
                        wiper_config_applied = True
        if not configs_applied_in_rebuild and (not wiper_config_applied):
            wiper_tab = self._wiper_tab()
            wiper_cfg = self._wiper_alias_config(tabs_data)
            if wiper_tab and wiper_cfg:
                try:
                    wiper_tab.set_config(wiper_cfg)
                except Exception:
                    pass
        if self.combo_tab and combo_data and (not configs_applied_in_rebuild):
            self.combo_tab.set_config(combo_data)
        self._apply_car_key_config(car)
        self.sync_ghost_rows_to_car(car)
        overlay_config = self.saved_presets[car].get('_overlay', {})
        self.car_overlay_config[car] = overlay_config
        self.car_overlay_feedback[car] = self.saved_presets[car].get('_overlay_feedback', self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy()))
        self.overlay_tab.load_for_car(car, self._overlay_var_list(), overlay_config)
        self.register_current_listeners()
        print(f'[Preset] Loaded: {car} / {track} ({surface_key})')

    def action_load_preset(self):
        """Load selected preset."""
        car = self.combo_car.get()
        track = self.combo_track.get()
        surface = self._selected_surface()
        if not car or not track:
            return
        self.current_car = car
        self.current_track = track
        self._set_active_surface(car, track, surface, load=True, notify=False)

    def action_clear_preset(self):
        """Clear selected preset."""
        car = self.combo_car.get()
        track = self.combo_track.get()
        if not car or not track:
            return
        if car in self.saved_presets and track in self.saved_presets[car]:
            if not self._ask_yes_no("Restore profile", f'Restore the default values of {car} em {track}?'):
                return
            del self.saved_presets[car][track]
            self.save_config()
            self.current_car = car
            self.current_track = track
            self._set_surface_selection(DEFAULT_SURFACE_PRESET)
            self.rebuild_tabs(list(self.active_vars))
            self._save_preset_for_pair(car, track, show_message=False)

    def _strip_preset_hotkeys(self, preset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Return preset data without any hotkey bindings."""
        if not preset_data:
            return {}

        def _strip_tab(tab_cfg: Dict[str, Any]) -> Dict[str, Any]:
            clean: Dict[str, Any] = {}
            for key, value in tab_cfg.items():
                if key in {'key_increase', 'key_decrease', 'key_increase_text', 'key_decrease_text', 'ghost_increase_bind', 'ghost_decrease_bind'}:
                    continue
                if key == 'presets' and isinstance(value, list):
                    cleaned_presets = []
                    for preset in value:
                        cleaned = {preset_key: preset_val for preset_key, preset_val in preset.items() if preset_key != 'bind'}
                        cleaned_presets.append(cleaned)
                    clean['presets'] = cleaned_presets
                else:
                    clean[key] = value
            return clean
        tabs_data = {name: _strip_tab(tab_cfg) for name, tab_cfg in preset_data.get('tabs', {}).items()}
        combo_cfg = preset_data.get('combo', {})
        combo_presets = combo_cfg.get('presets', [])
        combo_clean = {}
        if combo_cfg:
            combo_clean = dict(combo_cfg)
            if isinstance(combo_presets, list):
                combo_clean['presets'] = [{preset_key: preset_val for preset_key, preset_val in preset.items() if preset_key != 'bind'} for preset in combo_presets]
        clean_data = dict(preset_data)
        clean_data['tabs'] = tabs_data
        clean_data['combo'] = combo_clean
        return clean_data

    def _preset_base_for_import(self, car: str, track: str) -> Dict[str, Any]:
        """Return base preset data used to preserve hotkeys during import."""
        surface = self._selected_surface()
        if car in self.saved_presets and track in self.saved_presets[car]:
            return self._get_surface_preset(car, track, surface)
        return {'active_vars': self.active_vars, 'tabs': {name: tab.get_config() for name, tab in self.tabs.items()}, 'combo': self.combo_tab.get_config() if self.combo_tab else {}}

    def _merge_tab_without_hotkeys(self, incoming_tab: Dict[str, Any], base_tab: Dict[str, Any]) -> Dict[str, Any]:
        """Merge tab data while preserving existing hotkeys."""
        merged = dict(incoming_tab)
        for key in ('key_increase', 'key_decrease', 'key_increase_text', 'key_decrease_text', 'ghost_increase_bind', 'ghost_decrease_bind'):
            if key in base_tab:
                merged[key] = base_tab[key]
        incoming_presets = incoming_tab.get('presets')
        if isinstance(incoming_presets, list):
            base_presets = base_tab.get('presets', [])
            merged_presets = []
            for idx, preset in enumerate(incoming_presets):
                preset_copy = dict(preset)
                base_bind = None
                if idx < len(base_presets):
                    base_bind = base_presets[idx].get('bind')
                preset_copy['bind'] = base_bind
                merged_presets.append(preset_copy)
            merged['presets'] = merged_presets
        return merged

    def _merge_combo_without_hotkeys(self, incoming_combo: Dict[str, Any], base_combo: Dict[str, Any]) -> Dict[str, Any]:
        """Merge combo data while preserving existing hotkeys."""
        merged = dict(incoming_combo)
        incoming_presets = incoming_combo.get('presets')
        if isinstance(incoming_presets, list):
            base_presets = base_combo.get('presets', [])
            merged_presets = []
            for idx, preset in enumerate(incoming_presets):
                preset_copy = dict(preset)
                base_bind = None
                if idx < len(base_presets):
                    base_bind = base_presets[idx].get('bind')
                preset_copy['bind'] = base_bind
                merged_presets.append(preset_copy)
            merged['presets'] = merged_presets
        return merged

    def _merge_preset_without_hotkeys(self, incoming: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
        """Merge a preset while keeping existing hotkey bindings intact."""
        incoming_clean = self._strip_preset_hotkeys(incoming)
        merged_tabs: Dict[str, Any] = {}
        base_tabs = base.get('tabs', {})
        for name, tab_cfg in incoming_clean.get('tabs', {}).items():
            base_tab = base_tabs.get(name, {})
            merged_tabs[name] = self._merge_tab_without_hotkeys(tab_cfg, base_tab)
        merged_combo = {}
        if 'combo' in incoming_clean:
            merged_combo = self._merge_combo_without_hotkeys(incoming_clean.get('combo', {}), base.get('combo', {}))
        return {'active_vars': incoming_clean.get('active_vars', base.get('active_vars', [])), 'tabs': merged_tabs, 'combo': merged_combo}

    def action_export_preset(self) -> None:
        """Export the selected preset without hotkey bindings."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        surface = self._selected_surface()
        if not car or not track:
            self._show_warning("Export Preset", "Select a car and a track first.")
            return
        if car not in self.saved_presets or track not in self.saved_presets[car]:
            self._show_warning("Export Preset", "No saved preset found for the selected car/track.")
            return
        preset_data = self._strip_preset_hotkeys(self._get_surface_preset(car, track, surface))
        payload = {'version': 1, 'car': car, 'track': track, 'surface': surface, 'preset': preset_data}
        filename = filedialog.asksaveasfilename(title="Export Preset", defaultextension='.json', filetypes=[("Preset Export", '*.json'), ("All Files", '*.*')])
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            self._show_error("Export Preset", f'Could not export the preset: {exc}')
            return
        self._show_info("Export Preset", f'Preset exported to {filename}')

    def action_import_preset(self) -> None:
        """Import preset values without overwriting hotkeys."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        surface = self._selected_surface()
        if not car or not track:
            self._show_warning("Import Preset", "Select a car and a track first.")
            return
        filename = filedialog.askopenfilename(title="Import Preset", filetypes=[("Preset Export", '*.json'), ("All Files", '*.*')])
        if not filename:
            return
        try:
            with open(filename, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self._show_error("Import Preset", f'Could not load the preset file: {exc}')
            return
        incoming = data.get('preset') if isinstance(data, dict) else None
        if incoming is None and isinstance(data, dict) and ('tabs' in data):
            incoming = data
        if not isinstance(incoming, dict):
            self._show_error("Import Preset", "The preset file has no preset data in it.")
            return
        base = self._preset_base_for_import(car, track)
        merged = self._merge_preset_without_hotkeys(incoming, base)
        entry = self._ensure_track_surface_presets(car, track)
        entry['surface_presets'][surface] = merged
        entry['active_surface'] = surface
        self.saved_presets[car][track] = entry
        self.save_config()
        self.update_preset_ui()
        if (car, track) == (self.current_car, self.current_track):
            self.load_specific_preset(car, track, surface=surface)
        self._show_info("Import Preset", f'Preset imported for {car} @ {track} ({surface}).')

    def auto_preset_loop(self):
        """Background loop for auto-detecting car/track."""
        idle_delay_ms = 3000
        active_delay_ms = 2000
        if not (self.auto_detect.get() or self.auto_restart_on_race.get() or self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get() or self.auto_restart_on_track_ready.get() or self.keep_scanning_until_valid.get()):
            self.root.after(idle_delay_ms, self.auto_preset_loop)
            return
        try:
            if not self._ensure_sdk_connected():
                self._set_telemetry_active(False)
                self.root.after(700, self.auto_preset_loop)
                return
            session_type, session_num = self._get_session_state()
            if self._handle_session_change(session_type, session_num):
                return
            if not (self.auto_detect.get() or self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get() or self.auto_restart_on_track_ready.get() or self.keep_scanning_until_valid.get()):
                self.root.after(idle_delay_ms, self.auto_preset_loop)
                return
            driver_info = self.ir['DriverInfo']
            if not driver_info:
                self._set_telemetry_active(False)
                self.root.after(500, self.auto_preset_loop)
                return
            try:
                idx = int(driver_info.get('DriverCarIdx', -1))
            except Exception:
                idx = -1
            drivers = driver_info.get('Drivers', [])
            if not isinstance(drivers, list) or idx < 0 or idx >= len(drivers):
                self.root.after(500, self.auto_preset_loop)
                return
            driver_entry = drivers[idx] if isinstance(drivers[idx], dict) else {}
            raw_car = str(driver_entry.get('CarScreenName', '')).strip()
            if not raw_car:
                self.root.after(500, self.auto_preset_loop)
                return
            weekend = self.ir['WeekendInfo']
            if not weekend:
                self._set_telemetry_active(False)
                self.root.after(500, self.auto_preset_loop)
                return
            self._detect_session_change(weekend)
            self._handle_weekend_change(weekend)
            raw_track = self.track_label_from_weekend(weekend)
            if not raw_track:
                self.root.after(500, self.auto_preset_loop)
                return
            telemetry_reconnected = self._set_telemetry_active(True)
            if self._maybe_restart_on_track_ready():
                return
            is_on_track = self._bool_from_keys(['IsOnTrack'])
            is_on_track_car = self._bool_from_keys(['IsOnTrackCar'])
            on_track_now = is_on_track and is_on_track_car
            if on_track_now and (not self._last_on_track_state):
                print("[Auto detect] Driver entered the car — validating the scan...")
                self._on_track_validation_pending = True
                self.root.after(500, self._validate_and_recover)
            self._last_on_track_state = on_track_now
            car_clean = ''.join((c for c in raw_car if c.isalnum() or c in ' -_'))
            track_clean = ''.join((c for c in raw_track if c.isalnum() or c in ' -_'))
            if not car_clean.strip() or not track_clean.strip():
                self.root.after(3000, self.auto_preset_loop)
                return
            current_pair = (car_clean, track_clean)
            already_reported_pair = current_pair == self._last_detected_pair
            if current_pair != self._last_auto_pair:
                self._last_auto_pair = current_pair
                self.current_car, self.current_track = (car_clean, track_clean)
                if not already_reported_pair:
                    self._last_detected_pair = current_pair
                    self._last_detected_pair_time = time.time()
                    print(f'[AutoDetect] {car_clean} @ {track_clean}')
                if self.auto_detect.get():
                    self.auto_fill_ui(car_clean, track_clean)
                if not already_reported_pair and (self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get()):
                    self._schedule_session_scan()
                if telemetry_reconnected:
                    self._schedule_session_scan()
                if car_clean not in self.saved_presets:
                    self.saved_presets[car_clean] = {}
                self._adopt_legacy_track_preset(car_clean, track_clean)
                if '_overlay' not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]['_overlay'] = self.car_overlay_config.get(car_clean, {})
                if '_overlay_feedback' not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]['_overlay_feedback'] = self.car_overlay_feedback.get(car_clean, DEFAULT_OVERLAY_FEEDBACK.copy())
                if track_clean not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean][track_clean] = {'surface_presets': {'DRY': {'active_vars': None, 'tabs': {}, 'combo': {}}, 'WET': {'active_vars': None, 'tabs': {}, 'combo': {}}}, 'active_surface': DEFAULT_SURFACE_PRESET}
                self.save_config()
                if (car_clean, track_clean) not in self.auto_load_attempted:
                    self.auto_load_attempted.add((car_clean, track_clean))
                    if self._skip_next_auto_load:
                        self._skip_next_auto_load = False
                    else:
                        entry = self._ensure_track_surface_presets(car_clean, track_clean)
                        surface_key = entry.get('active_surface', DEFAULT_SURFACE_PRESET)
                        surface_data = entry['surface_presets'].get(surface_key, {})
                        if surface_data.get('active_vars'):
                            self.load_specific_preset(car_clean, track_clean, surface=surface_key)
            elif telemetry_reconnected:
                self._schedule_session_scan()
        except Exception as e:
            print(f'[Auto detect] Error: {e}')
        self.root.after(active_delay_ms, self.auto_preset_loop)

    def schedule_save(self):
        """Schedule configuration save."""

        def _schedule():
            if self._config_save_job:
                try:
                    self.root.after_cancel(self._config_save_job)
                except Exception:
                    pass
            self._config_save_job = self.root.after(250, self._flush_scheduled_save)
        self.ui(_schedule)

    def _flush_scheduled_save(self) -> None:
        """Persist config after debounce to avoid excessive disk writes."""
        self._config_save_job = None
        self.save_config()

    def schedule_preset_save(self) -> None:
        """Auto-save current preset if the setting is enabled."""
        if not self.auto_save_presets.get():
            return
        if self.app_state != 'CONFIG':
            return
        if self._auto_save_job:
            self.root.after_cancel(self._auto_save_job)
        self._auto_save_job = self.root.after(400, self._auto_save_current_preset)

    def _auto_save_current_preset(self) -> None:
        """Persist the current preset without showing prompts."""
        self._auto_save_job = None
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        if not car or not track:
            return
        self._save_preset_for_pair(car, track, show_message=False)

    def save_config(self):
        """Save configuration to disk."""
        if self._config_save_job:
            try:
                self.root.after_cancel(self._config_save_job)
            except Exception:
                pass
            self._config_save_job = None
        self._collect_surface_dc_profile_rows()
        second_throttle_panel = getattr(self, 'second_throttle_panel', None)
        if second_throttle_panel is not None:
            try:
                second_throttle_panel.sync_to_app(schedule=False)
            except Exception as exc:
                print(f'[SAVE] Second Throttle: {exc}')
        ghost_panel = getattr(self, 'ghost_keys_panel', None)
        if ghost_panel is not None:
            try:
                ghost_panel.sync_to_app(schedule=False)
            except Exception as exc:
                print(f'[SAVE] Auxiliary Keys: {exc}')
        car = self.current_car or "Generic Car"
        if self.overlay_tab:
            self.overlay_tab.collect_for_car(car)
        if self.current_car:
            self._collect_ghost_rows_for_car(self.current_car)
        data = {'global_timing': GLOBAL_TIMING, 'hud_style': self.overlay.style_cfg, 'hud_lapdist_default_off_version': self._hud_lapdist_default_off_version, 'auto_sync_iracing_controls_default_off_version': self._auto_sync_iracing_controls_default_off_version, 'timing_force_bot_version': self._timing_force_bot_version, 'show_overlay_feedback': self.show_overlay_feedback.get(), 'overlay_visible': self._overlay_visible_before_discreet if self._overlay_visible_before_discreet is not None else self.overlay_visible, 'lapdist_overlay_visible': self.lapdist_overlay_visible, 'use_keyboard_only': self.use_keyboard_only.get(), 'use_tts': self.use_tts.get(), 'use_voice': self.use_voice.get(), 'voice_engine': self.voice_engine.get(), 'vosk_model_path': self.vosk_model_path.get(), 'whisper_binary_path': self.whisper_binary_path.get(), 'whisper_model_path': self.whisper_model_path.get(), 'voice_tuning': self._voice_tuning_config(), 'microphone_device': self.microphone_device.get(), 'audio_output_device': self.audio_output_device.get(), 'auto_detect': self.auto_detect.get(), 'auto_scan_on_change': self.auto_scan_on_change.get(), 'auto_restart_on_rescan': self.auto_restart_on_rescan.get(), 'auto_restart_on_race': self.auto_restart_on_race.get(), 'auto_restart_on_track_ready': self.auto_restart_on_track_ready.get(), 'auto_sync_iracing_controls': self.auto_sync_iracing_controls.get(), 'block_off_track_commands': self.block_off_track_commands.get(), 'auto_save_presets': self.auto_save_presets.get(), 'lock_preset_selection': self.lock_preset_selection.get(), 'start_with_windows': self.start_with_windows.get(), 'focus_on_start': self.focus_on_start.get(), 'keep_trying_targets': self.keep_trying_targets.get(), 'show_scan_popup': self.show_scan_popup.get(), 'keep_scanning_until_valid': self.keep_scanning_until_valid.get(), 'disable_popups': self.disable_popups.get(), 'show_getting_started': self.show_getting_started.get(), 'discreet_mode': self.discreet_mode.get(), 'use_lapdist_macros': self.use_lapdist_macros.get(), 'lapdist_humanize': self.lapdist_humanize.get(), 'lapdist_training_mode': self.lapdist_training_mode.get(), 'lapdist_humanize_delay_min': self.lapdist_humanize_delay_min.get(), 'lapdist_humanize_delay_max': self.lapdist_humanize_delay_max.get(), 'lapdist_min_spacing': self.lapdist_min_spacing.get(), 'lapdist_cooldown_sensitivity': self.lapdist_cooldown_sensitivity.get(), 'lapdist_show_close_log': self.lapdist_show_close_log.get(), 'lapdist_learning_enabled': self.lapdist_learning_enabled.get(), 'lapdist_learning_persist': self.lapdist_learning_persist.get(), 'lapdist_learning_data': self._lapdist_learning_data if self.lapdist_learning_persist.get() else {}, 'turbo_pit_enabled': self.turbo_pit_enabled.get(), 'turbo_pit_enabled_by_series': self.turbo_pit_enabled_by_series, 'turbo_pit_toggle_bind': self.turbo_pit_toggle_bind, 'second_throttle_output_bind': self.second_throttle_output_bind, 'second_throttle_output_label': self.second_throttle_output_label, 'second_throttle_macros': self.second_throttle_macros, 'second_throttle_pit_macro': self.second_throttle_pit_macro, 'second_throttle_toggle_bind': self.second_throttle_macros[0].get('bind') if self.second_throttle_macros else None, 'second_throttle_percent': self.second_throttle_engine.normalize_percentage(self.second_throttle_macros[0].get('percentage', 100) if self.second_throttle_macros else 100), 'wiper_debug_enabled': self.wiper_debug_enabled.get(), 'clear_target_bind': self.clear_target_bind, 'manual_rescan_bind': self.manual_rescan_bind, 'lapdist_toggle_bind': self.lapdist_toggle_bind, 'lapdist_capture_bind': self.lapdist_capture_bind, 'surface_toggle_bind': self.surface_toggle_bind, 'surface_dry_bind': self.surface_dry_bind, 'surface_wet_bind': self.surface_wet_bind, 'surface_dc_profiles_enabled': self.surface_dc_profiles_enabled.get(), 'surface_dc_auto_declared_wet': self.surface_dc_auto_declared_wet.get(), 'surface_dc_endurance_enabled': self.surface_dc_endurance_enabled.get(), 'surface_dc_endurance_mode': self.surface_dc_endurance_mode.get(), 'surface_dc_profiles': self.surface_dc_profiles, 'ghost_key_bindings': self._global_ghost_rows(), 'surface_dc_dry_bind': self.surface_dc_dry_bind, 'surface_dc_wet_bind': self.surface_dc_wet_bind, 'wiper_debug_bind': self.wiper_debug_bind, 'bounds_discovery_delay_s': self.bounds_discovery_delay_s.get(), 'pending_scan_on_start': self.pending_scan_on_start, 'rescan_restart_pair': list(self._rescan_restart_pair), 'allowed_devices': input_manager.allowed_devices, 'saved_presets': self.saved_presets, 'car_overlay_config': self.car_overlay_config, 'car_overlay_feedback': self.car_overlay_feedback, 'active_vars': self.active_vars, 'current_car': self.current_car, 'current_track': self.current_track, 'current_surface': self.current_surface}
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f'[SAVE] Error saving the configuration: {e}')

    def load_config(self):
        """Load configuration from disk."""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return
        normalized_timing = _normalize_timing_config(data.get('global_timing', GLOBAL_TIMING))
        foundation.GLOBAL_TIMING.clear()
        foundation.GLOBAL_TIMING.update(normalized_timing)
        try:
            self._timing_force_bot_version = int(data.get('timing_force_bot_version', 0))
        except Exception:
            self._timing_force_bot_version = 0
        if self._timing_force_bot_version < TIMING_FORCE_BOT_VERSION:
            GLOBAL_TIMING['profile'] = 'claude'
            self._timing_force_bot_version = TIMING_FORCE_BOT_VERSION
        style = data.get('hud_style')
        if style:
            self.overlay.style_cfg.update(style)
            self.overlay.apply_style(self.overlay.style_cfg)
        self.show_overlay_feedback.set(data.get('show_overlay_feedback', True))
        self.overlay_visible = bool(data.get('overlay_visible', True))
        self.hud_visible_var.set(self.overlay_visible)
        self.lapdist_overlay_visible = bool(data.get('lapdist_overlay_visible', False))
        self.use_keyboard_only.set(data.get('use_keyboard_only', False))
        self.use_tts.set(data.get('use_tts', False))
        self.use_voice.set(data.get('use_voice', True))
        self.voice_engine.set(data.get('voice_engine', 'speech'))
        self.vosk_model_path.set(data.get('vosk_model_path', ''))
        self.whisper_binary_path.set(data.get('whisper_binary_path', ''))
        self.whisper_model_path.set(data.get('whisper_model_path', ''))
        self.microphone_device.set(data.get('microphone_device', -1))
        self.audio_output_device.set(data.get('audio_output_device', -1))
        self._set_voice_tuning_vars(data.get('voice_tuning', VOICE_TUNING_DEFAULTS))
        self.auto_detect.set(data.get('auto_detect', True))
        self.auto_scan_on_change.set(data.get('auto_scan_on_change', True))
        self.auto_restart_on_rescan.set(data.get('auto_restart_on_rescan', False))
        self.auto_restart_on_race.set(data.get('auto_restart_on_race', False))
        self.auto_restart_on_track_ready.set(data.get('auto_restart_on_track_ready', False))
        self.auto_sync_iracing_controls.set(data.get('auto_sync_iracing_controls', False))
        self._migrate_auto_sync_iracing_controls_default_off(data.get('auto_sync_iracing_controls_default_off_version', 0))
        block_off_track = data.get('block_off_track_commands', False)
        if not isinstance(block_off_track, bool):
            block_off_track = False
        self.block_off_track_commands.set(block_off_track)
        self.auto_save_presets.set(data.get('auto_save_presets', True))
        self.lock_preset_selection.set(data.get('lock_preset_selection', True))
        self.start_with_windows.set(data.get('start_with_windows', True))
        self.focus_on_start.set(data.get('focus_on_start', False))
        self.keep_trying_targets.set(data.get('keep_trying_targets', True))
        self.show_scan_popup.set(data.get('show_scan_popup', False))
        self.keep_scanning_until_valid.set(data.get('keep_scanning_until_valid', True))
        self.disable_popups.set(data.get('disable_popups', True))
        self.show_getting_started.set(data.get('show_getting_started', True))
        self.discreet_mode.set(data.get('discreet_mode', False))
        self.use_lapdist_macros.set(data.get('use_lapdist_macros', True))
        self.lapdist_humanize.set(data.get('lapdist_humanize', True))
        self.lapdist_training_mode.set(data.get('lapdist_training_mode', False))
        self.lapdist_humanize_delay_min.set(str(data.get('lapdist_humanize_delay_min', '0.05')))
        self.lapdist_humanize_delay_max.set(str(data.get('lapdist_humanize_delay_max', '0.18')))
        self.lapdist_min_spacing.set(str(data.get('lapdist_min_spacing', '0.25')))
        self.lapdist_cooldown_sensitivity.set(str(data.get('lapdist_cooldown_sensitivity', '0.35')))
        self.lapdist_show_close_log.set(data.get('lapdist_show_close_log', False))
        self.lapdist_learning_enabled.set(data.get('lapdist_learning_enabled', True))
        self.lapdist_learning_persist.set(data.get('lapdist_learning_persist', False))
        if self.lapdist_learning_persist.get():
            learning_data = data.get('lapdist_learning_data', {})
            self._lapdist_learning_data = learning_data if isinstance(learning_data, dict) else {}
        else:
            self._lapdist_learning_data = {}
        self._pit_limiter_learning_data = {}
        self.turbo_pit_enabled.set(data.get('turbo_pit_enabled', True))
        turbo_by_series = data.get('turbo_pit_enabled_by_series', {})
        if isinstance(turbo_by_series, dict):
            self.turbo_pit_enabled_by_series = {str(k): bool(v) for k, v in turbo_by_series.items() if k not in (None, '')}
        else:
            self.turbo_pit_enabled_by_series = {}
        self.turbo_pit_enabled_by_series.setdefault('0', bool(self.turbo_pit_enabled.get()))
        self.turbo_pit_toggle_bind = data.get('turbo_pit_toggle_bind')
        self.second_throttle_output_bind = _normalize_game_input_binding(data.get('second_throttle_output_bind'))
        self.second_throttle_output_label = str(data.get('second_throttle_output_label') or '').strip()
        raw_second_throttle_trigger = data.get('second_throttle_toggle_bind')
        legacy_second_throttle_trigger = _normalize_key_scancode_code(raw_second_throttle_trigger) or raw_second_throttle_trigger
        legacy_second_throttle_percent = self.second_throttle_engine.normalize_percentage(data.get('second_throttle_percent', 100))
        raw_second_throttle_macros = data.get('second_throttle_macros')
        self.second_throttle_macros = sanitize_second_throttle_macros(raw_second_throttle_macros, fallback_binding=legacy_second_throttle_trigger, fallback_percentage=legacy_second_throttle_percent, ensure_one=True)
        for macro in self.second_throttle_macros:
            raw_bind = macro.get('bind')
            macro['bind'] = _normalize_key_scancode_code(raw_bind) or raw_bind if raw_bind else None
        self.second_throttle_pit_macro = sanitize_second_throttle_pit_macro(data.get('second_throttle_pit_macro'))
        first_second_throttle_macro = self.second_throttle_macros[0]
        self.second_throttle_toggle_bind = first_second_throttle_macro.get('bind')
        self.second_throttle_percent.set(str(first_second_throttle_macro['percentage']))
        if not isinstance(raw_second_throttle_macros, list):
            self._config_migration_save_needed = True
        self.wiper_debug_enabled.set(data.get('wiper_debug_enabled', False))
        self.clear_target_bind = data.get('clear_target_bind')
        self.manual_rescan_bind = data.get('manual_rescan_bind')
        self.lapdist_toggle_bind = data.get('lapdist_toggle_bind')
        self.lapdist_capture_bind = data.get('lapdist_capture_bind')
        self.surface_toggle_bind = data.get('surface_toggle_bind')
        self.surface_dry_bind = data.get('surface_dry_bind')
        self.surface_wet_bind = data.get('surface_wet_bind')
        self.surface_dc_profiles_enabled.set(bool(data.get('surface_dc_profiles_enabled', True)))
        self.surface_dc_auto_declared_wet.set(bool(data.get('surface_dc_auto_declared_wet', True)))
        self.surface_dc_endurance_enabled.set(bool(data.get('surface_dc_endurance_enabled', True)))
        endurance_mode = str(data.get('surface_dc_endurance_mode', 'AUTO') or 'AUTO').upper()
        if endurance_mode not in SURFACE_DC_ENDURANCE_MODES:
            endurance_mode = 'AUTO'
        self.surface_dc_endurance_mode.set(endurance_mode)
        surface_dc_profiles = data.get('surface_dc_profiles', {})
        self.surface_dc_profiles = surface_dc_profiles if isinstance(surface_dc_profiles, dict) else {}
        self.surface_dc_dry_bind = data.get('surface_dc_dry_bind')
        self.surface_dc_wet_bind = data.get('surface_dc_wet_bind')
        self.wiper_debug_bind = data.get('wiper_debug_bind')
        self.bounds_discovery_delay_s.set(str(data.get('bounds_discovery_delay_s', '5.0')))
        self.pending_scan_on_start = data.get('pending_scan_on_start', False)
        pair = data.get('rescan_restart_pair', ['', ''])
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            self._rescan_restart_pair = (pair[0], pair[1])
        input_manager.allowed_devices = data.get('allowed_devices', [])
        self._load_ghost_rows(data)
        self.saved_presets = data.get('saved_presets', {})
        self.car_overlay_config = data.get('car_overlay_config', {})
        self.car_overlay_feedback = data.get('car_overlay_feedback', self.car_overlay_feedback)
        self._migrate_hud_lapdist_default_off(data.get('hud_lapdist_default_off_version', 0))
        self.active_vars = [_normalize_var_tuple(item) for item in data.get('active_vars', [])]
        self.current_car = data.get('current_car', '')
        self.current_track = data.get('current_track', '')
        self.current_surface = self._normalize_surface_label(data.get('current_surface', DEFAULT_SURFACE_PRESET))
        self._normalize_saved_presets()
        self._apply_ghost_rows_for_car(self.current_car)

    def _migrate_auto_sync_iracing_controls_default_off(self, saved_version: Any) -> None:
        try:
            version = int(saved_version)
        except Exception:
            version = 0
        if version >= AUTO_SYNC_IRACING_CONTROLS_DEFAULT_OFF_VERSION:
            self._auto_sync_iracing_controls_default_off_version = version
            return
        self.auto_sync_iracing_controls.set(False)
        self._auto_sync_iracing_controls_default_off_version = AUTO_SYNC_IRACING_CONTROLS_DEFAULT_OFF_VERSION
        self._iracing_controls_watch_state.clear()
        self._config_migration_save_needed = True

    @staticmethod
    def _disable_lapdist_overlay_entry(config: Any) -> None:
        if not isinstance(config, dict):
            return
        for key in HUD_LAPDIST_KEYS:
            entry = config.get(key)
            if isinstance(entry, dict):
                entry['show'] = False
                entry.setdefault('label', 'LapDist')

    def _migrate_hud_lapdist_default_off(self, saved_version: Any) -> None:
        try:
            version = int(saved_version)
        except Exception:
            version = 0
        if version >= HUD_LAPDIST_DEFAULT_OFF_VERSION:
            self._hud_lapdist_default_off_version = version
            return
        for overlay_config in self.car_overlay_config.values():
            self._disable_lapdist_overlay_entry(overlay_config)
        for track_map in self.saved_presets.values():
            if isinstance(track_map, dict):
                self._disable_lapdist_overlay_entry(track_map.get('_overlay'))
        self._hud_lapdist_default_off_version = HUD_LAPDIST_DEFAULT_OFF_VERSION

    def _normalize_saved_presets(self) -> None:
        """Upgrade any saved presets to the surface-aware structure."""
        for car, track_map in list(self.saved_presets.items()):
            if not isinstance(track_map, dict):
                continue
            car_keys = track_map.get('_car_keys')
            if not isinstance(car_keys, dict):
                car_keys = {}
            for track in list(track_map.keys()):
                if track in {'_overlay', '_overlay_feedback', '_car_keys', '_ghost_keys'}:
                    continue
                entry = self._ensure_track_surface_presets(car, track)
                for surface_data in entry.get('surface_presets', {}).values():
                    tabs_data = surface_data.get('tabs', {})
                    for var_name, tab_cfg in tabs_data.items():
                        if not isinstance(tab_cfg, dict):
                            continue
                        if var_name in car_keys:
                            existing = car_keys[var_name]
                            if isinstance(existing, dict):
                                for ghost_key in ('ghost_increase_bind', 'ghost_decrease_bind'):
                                    if ghost_key in tab_cfg and ghost_key not in existing:
                                        existing[ghost_key] = tab_cfg.get(ghost_key)
                            continue
                        if 'key_increase' in tab_cfg or 'key_decrease' in tab_cfg:
                            car_keys[var_name] = {'key_increase': tab_cfg.get('key_increase'), 'key_decrease': tab_cfg.get('key_decrease'), 'key_increase_text': tab_cfg.get('key_increase_text', "Set Increase (+)"), 'key_decrease_text': tab_cfg.get('key_decrease_text', "Set Decrease (-)"), 'ghost_increase_bind': tab_cfg.get('ghost_increase_bind'), 'ghost_decrease_bind': tab_cfg.get('ghost_decrease_bind')}
            if car_keys:
                track_map['_car_keys'] = car_keys

    def _set_voice_tuning_vars(self, tuning: Dict[str, Any]):
        """Populate Tk variables with stored voice tuning values."""
        self.voice_ambient_duration.set(tuning.get('ambient_duration', VOICE_TUNING_DEFAULTS['ambient_duration']))
        self.voice_initial_timeout.set(tuning.get('initial_timeout', VOICE_TUNING_DEFAULTS['initial_timeout']))
        self.voice_continuous_timeout.set(tuning.get('continuous_timeout', VOICE_TUNING_DEFAULTS['continuous_timeout']))
        self.voice_phrase_time_limit.set(tuning.get('phrase_time_limit', VOICE_TUNING_DEFAULTS['phrase_time_limit']))
        energy_threshold = tuning.get('energy_threshold')
        self.voice_energy_threshold.set('' if energy_threshold in {None, ''} else str(energy_threshold))
        self.voice_dynamic_energy.set(tuning.get('dynamic_energy', VOICE_TUNING_DEFAULTS['dynamic_energy']))

    def restore_defaults(self):
        """Delete the configuration file and restart the app after confirmation."""
        if not self._ask_yes_no("Restore Defaults", "This will delete your configuration file and restart the application. Continue?"):
            return
        try:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
        except Exception as exc:
            self._show_error("Error", f'Could not delete the configuration: {exc}')
            return
        self._show_info("Defaults Restored", "Configuration reset. The application will restart now.")
        restart_program()
__all__ = ['PresetsMixin']
