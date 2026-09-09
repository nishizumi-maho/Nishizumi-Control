from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *
from ..core import SECOND_THROTTLE_PIT_MACRO_ID, sanitize_second_throttle_macros, sanitize_second_throttle_pit_macro

class BindingsMixin:

    def _iter_hotkey_bindings(self) -> Iterator[Tuple[str, str, str]]:
        """Yield (code, label, source_id) for all hotkey bindings."""
        app_bindings = [('clear_target_bind', "Clear target hotkey"), ('manual_rescan_bind', "Restart and scan hotkey"), ('lapdist_toggle_bind', "LapDist hotkey"), ('lapdist_capture_bind', "LapDist capture hotkey"), ('surface_toggle_bind', "Dry/Wet hotkey"), ('surface_dry_bind', "Dry preset hotkey"), ('surface_wet_bind', "Wet profile hotkey"), ('surface_dc_dry_bind', "Dry DC profile hotkey"), ('surface_dc_wet_bind', "Wet DC profile hotkey"), ('turbo_pit_toggle_bind', "Turbo Pit hotkey"), ('wiper_debug_bind', "Wiper diagnostics hotkey")]
        for attr_name, label in app_bindings:
            code = getattr(self, attr_name, None)
            if code:
                yield (code, label, f'app:{attr_name}')
        for index, macro in enumerate(getattr(self, 'second_throttle_macros', []), start=1):
            code = macro.get('bind') if isinstance(macro, dict) else None
            if code:
                name = str(macro.get('name') or f'Macro {index}').strip()
                macro_id = str(macro.get('id') or index)
                yield (code, f'Second Throttle: {name}', f'second-throttle-macro:{macro_id}')
        for tab in self.tabs.values():
            label_prefix = getattr(tab, 'label_name', "Control")
            manual_increase_bind = getattr(tab, 'manual_increase_bind', None)
            if manual_increase_bind:
                yield (manual_increase_bind, f'{label_prefix}: manual hotkey +' if not getattr(tab, 'uses_toggle_key', False) else f'{label_prefix}: manual toggle hotkey', getattr(tab, '_manual_increase_source_id', f'control-manual:{id(tab)}:increase'))
            manual_decrease_bind = getattr(tab, 'manual_decrease_bind', None)
            if manual_decrease_bind:
                yield (manual_decrease_bind, f'{label_prefix}: manual hotkey -', getattr(tab, '_manual_decrease_source_id', f'control-manual:{id(tab)}:decrease'))
            for idx, row in enumerate(tab.preset_rows, start=1):
                code = row.get('bind')
                if code:
                    preset_label = f'{label_prefix} reset' if row.get('is_reset') else f'{label_prefix} preset {idx}'
                    source_id = row.get('source_id', f'control:{id(row)}')
                    yield (code, preset_label, source_id)
        if self.combo_tab:
            for idx, row in enumerate(self.combo_tab.preset_rows, start=1):
                code = row.get('bind')
                if code:
                    preset_label = 'Combo: redefinir' if row.get('is_reset') else f'Combo preset {idx}'
                    source_id = row.get('source_id', f'combo:{id(row)}')
                    yield (code, preset_label, source_id)
        yield from self._iter_ghost_hotkey_bindings()

    def _find_hotkey_conflict(self, code: str, current_source: Optional[str]) -> Optional[str]:
        """Return a conflicting label if the hotkey is already in use."""
        candidate = _normalize_key_scancode_code(code) or code
        candidate_identity = _keyboard_binding_scan_identity(candidate)
        for existing_code, label, source_id in self._iter_hotkey_bindings():
            existing = _normalize_key_scancode_code(existing_code) or existing_code
            existing_identity = _keyboard_binding_scan_identity(existing)
            same_physical_key = bool(candidate_identity is not None and existing_identity is not None and (candidate_identity == existing_identity))
            if (existing == candidate or same_physical_key) and source_id != current_source:
                return label
        return None

    def _second_throttle_macro_by_id(self, macro_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return a regular macro or the dedicated pit pseudo-row."""
        if macro_id == SECOND_THROTTLE_PIT_MACRO_ID:
            pit = dict(self.second_throttle_pit_macro)
            pit.setdefault('id', SECOND_THROTTLE_PIT_MACRO_ID)
            pit.setdefault('name', "Pit macro")
            return pit
        for macro in self.second_throttle_macros:
            if str(macro.get('id') or '') == str(macro_id or ''):
                return macro
        return None

    def _second_throttle_bindings_overlap(self, trigger_bind: Optional[Any]=None) -> bool:
        """Return True when injected output would recursively hit a macro."""
        output_identity = _keyboard_binding_scan_identity(self.second_throttle_output_bind)
        if output_identity is None:
            return False
        triggers = [trigger_bind] if trigger_bind is not None else [macro.get('bind') for macro in self.second_throttle_macros]
        return any((_keyboard_binding_scan_identity(trigger) == output_identity for trigger in triggers if trigger))

    def _second_throttle_configuration_error(self, macro_id: Optional[str]=None) -> Optional[str]:
        """Explain why output or a selected macro cannot run safely."""
        if not self.second_throttle_output_bind:
            return "Set the key sent to iRacing."
        output_hotkey = _game_binding_to_hotkey_code(self.second_throttle_output_bind)
        if not output_hotkey:
            return "The output has to be a supported keyboard key."
        conflict = self._find_hotkey_conflict(output_hotkey, 'app:second_throttle_output_bind')
        if conflict:
            return f'The key being sent is also in use by: {conflict}.'
        if macro_id is None:
            if not self.second_throttle_macros:
                return "Add at least one ON/OFF macro."
            return None
        macro = self._second_throttle_macro_by_id(macro_id)
        if macro is None:
            return "The selected macro no longer exists."
        trigger = macro.get('bind')
        if trigger and self._second_throttle_bindings_overlap(trigger):
            return "The iRacing key and the macro hotkey have to be different."
        if trigger and macro_id != SECOND_THROTTLE_PIT_MACRO_ID:
            conflict = self._find_hotkey_conflict(str(trigger), f'second-throttle-macro:{macro_id}')
            if conflict:
                return f'The macro hotkey is also in use by: {conflict}.'
        return None

    def _sync_second_throttle_engine(self) -> None:
        """Apply the active macro percentage, or the first macro while idle."""
        macro = self._second_throttle_macro_by_id(self.second_throttle_active_macro_id)
        if macro is None and self.second_throttle_macros:
            macro = self.second_throttle_macros[0]
        percentage = self.second_throttle_engine.normalize_percentage(macro.get('percentage', 100) if macro else 100)
        self.second_throttle_percent.set(str(percentage))
        self.second_throttle_engine.configure(self.second_throttle_output_bind, percentage)

    def _apply_second_throttle_config(self, *, schedule: bool=True, reregister: bool=False) -> None:
        """Normalize edited rows and safely apply the resulting configuration."""
        self.second_throttle_macros = sanitize_second_throttle_macros(self.second_throttle_macros, fallback_binding=self.second_throttle_toggle_bind, fallback_percentage=self.second_throttle_percent.get(), ensure_one=True)
        for macro in self.second_throttle_macros:
            raw_bind = macro.get('bind')
            macro['bind'] = _normalize_key_scancode_code(raw_bind) or raw_bind if raw_bind else None
        self.second_throttle_pit_macro = sanitize_second_throttle_pit_macro(self.second_throttle_pit_macro)
        first = self.second_throttle_macros[0]
        self.second_throttle_toggle_bind = first.get('bind')
        self.second_throttle_percent.set(str(first['percentage']))
        active = self._second_throttle_macro_by_id(self.second_throttle_active_macro_id)
        if self.second_throttle_active_macro_id and (active is None or (self.second_throttle_active_macro_id == SECOND_THROTTLE_PIT_MACRO_ID and (not active.get('enabled', False)))):
            self._deactivate_second_throttle()
        else:
            self._sync_second_throttle_engine()
        if schedule:
            self.second_throttle_automation.reset()
            self.schedule_save()
        if reregister:
            self.register_current_listeners()
        self._refresh_second_throttle_panel()

    def _refresh_second_throttle_panel(self) -> None:
        panel = getattr(self, 'second_throttle_panel', None)
        if panel is not None:
            try:
                panel.refresh()
            except Exception:
                pass

    def _on_second_throttle_state(self, snapshot: Any) -> None:
        """Publish engine state without touching Tk from its modulation thread."""

        def _apply() -> None:
            output_label = self.second_throttle_output_label or _format_game_binding_label(snapshot.output_binding) or "key not set"
            macro_name = self.second_throttle_active_macro_name or "Second Throttle"
            if snapshot.active:
                approximation = " APPROX." if snapshot.modulated else ''
                self.second_throttle_status_var.set(f'ON • {macro_name} • {snapshot.percentage}%{approximation}')
                if snapshot.modulated:
                    detail = f'Modulating {output_label}. Check the value on the iRacing pedal gauge.'
                else:
                    detail = f'{output_label} is being held down.'
                takeover = self.second_throttle_pedal_takeover
                if takeover.enabled:
                    if takeover.armed:
                        detail += f' Experimental cut armed: physical pedal above {takeover.threshold_percentage}% turns the macro off.'
                    else:
                        detail += " Experimental cut: release the physical pedal to arm it."
                self.second_throttle_detail_var.set(detail)
                self.second_throttle_action_var.set("Turn everything off")
            else:
                self.second_throttle_status_var.set("OFF")
                error = self._second_throttle_configuration_error()
                if error:
                    self.second_throttle_detail_var.set(error)
                else:
                    count = len(self.second_throttle_macros)
                    self.second_throttle_detail_var.set(f'{count} macro(s) ready. Each hotkey toggles its own percentage.')
                self.second_throttle_action_var.set("Turn on the first macro")
            self._refresh_second_throttle_panel()
        self.ui(_apply)

    def _on_second_throttle_error(self, message: str) -> None:
        """Report an output failure and leave the feature visibly off."""
        print(f'[Second Throttle] {message}')
        self.second_throttle_active_macro_id = None
        self.second_throttle_active_macro_name = ''
        self.second_throttle_active_source = ''
        self.second_throttle_pedal_takeover.reset()

        def _apply() -> None:
            self.second_throttle_status_var.set("ERROR • OFF")
            self.second_throttle_detail_var.set(str(message))
            self.second_throttle_action_var.set("Turn on the first macro")
            self._refresh_second_throttle_panel()
            self._show_error("Second Throttle", str(message))
        self.ui(_apply)

    def _apply_second_throttle_percentage(self) -> None:
        """Compatibility helper: update the first macro's percentage."""
        if not self.second_throttle_macros:
            return
        percentage = self.second_throttle_engine.normalize_percentage(self.second_throttle_percent.get())
        self.second_throttle_macros[0]['percentage'] = percentage
        self.second_throttle_percent.set(str(percentage))
        self._apply_second_throttle_config(schedule=True)

    def _set_second_throttle_output_bind(self) -> None:
        """Capture the dedicated keyboard key already mapped in iRacing."""
        if self.app_state != 'CONFIG':
            self._show_info("Second Throttle", "Switch to CONFIG mode to set the key sent to iRacing.")
            return
        previous_bind = self.second_throttle_output_bind
        previous_label = self.second_throttle_output_label
        self._deactivate_second_throttle()
        self.focus_window()
        panel = getattr(self, 'second_throttle_panel', None)
        if panel is not None:
            panel.output_button.config(text="Press a key...", bg='yellow')
        self.root.update_idletasks()
        binding, label = input_manager.capture_game_action_binding()
        if label == 'CANCEL':
            self.second_throttle_output_bind = None
            self.second_throttle_output_label = ''
        elif binding is not None:
            unsafe_reason = _unsafe_system_binding_reason(binding)
            output_hotkey = _game_binding_to_hotkey_code(binding)
            conflict = self._find_hotkey_conflict(output_hotkey, 'app:second_throttle_output_bind') if output_hotkey else None
            if unsafe_reason:
                self._show_warning("Key blocked", f'{unsafe_reason} is a system key and cannot be held down by the app.')
                self.second_throttle_output_bind = previous_bind
                self.second_throttle_output_label = previous_label
            elif not output_hotkey:
                self._show_warning("Use a keyboard key", "The Second Throttle output has to be a keyboard key that is also assigned inside iRacing.")
                self.second_throttle_output_bind = previous_bind
                self.second_throttle_output_label = previous_label
            elif conflict:
                self._show_warning("Choose a key of its own", f'That key is already bound to {conflict}. Use another key to avoid recursive commands.')
                self.second_throttle_output_bind = previous_bind
                self.second_throttle_output_label = previous_label
            else:
                self.second_throttle_output_bind = binding
                self.second_throttle_output_label = str(label or '').strip() or _format_game_binding_label(binding)
        self._apply_second_throttle_config(schedule=True)

    def _set_second_throttle_toggle_bind(self) -> None:
        """Compatibility helper: capture the first macro's ON/OFF hotkey."""
        panel = getattr(self, 'second_throttle_panel', None)
        if panel is not None and self.second_throttle_macros:
            panel.capture_macro_hotkey(self.second_throttle_macros[0]['id'])

    def _activate_second_throttle_macro(self, macro_id: str, source: str, *, notify: bool=True) -> bool:
        """Activate or switch to one macro while keeping a single game output."""
        if self.app_state != 'RUNNING':
            return False
        macro = self._second_throttle_macro_by_id(macro_id)
        error = self._second_throttle_configuration_error(macro_id)
        if macro is None or error:
            if source in {'hotkey', "button", 'manual'}:
                self._show_warning("Second Throttle", error or "The selected macro is not available.")
            return False
        if macro_id == SECOND_THROTTLE_PIT_MACRO_ID and (not bool(macro.get('enabled', False))):
            return False
        percentage = self.second_throttle_engine.normalize_percentage(macro.get('percentage', 100))
        self.second_throttle_active_macro_id = macro_id
        self.second_throttle_active_macro_name = str(macro.get('name') or 'Macro')
        self.second_throttle_active_source = source
        self.second_throttle_pedal_takeover.activate(macro_id, enabled=macro_id != SECOND_THROTTLE_PIT_MACRO_ID and macro.get('pedal_takeover_enabled', False), threshold_percentage=macro.get('pedal_takeover_threshold', 25))
        self.second_throttle_engine.configure(self.second_throttle_output_bind, percentage)
        active = self.second_throttle_engine.activate()
        if not active:
            self.second_throttle_active_macro_id = None
            self.second_throttle_active_macro_name = ''
            self.second_throttle_active_source = ''
            self.second_throttle_pedal_takeover.reset()
            return False
        if notify:
            suffix = '' if percentage == 100 else " approx."
            self.notify_overlay_status(f'2nd throttle • {macro.get('name', 'Macro')}: ON {percentage}%{suffix}', '#41d17d' if percentage == 100 else '#ffd166')
        self._refresh_second_throttle_panel()
        return True

    def toggle_second_throttle_macro(self, macro_id: str, source: str='manual') -> None:
        """Toggle a selected macro from its global hotkey or row button."""
        if self.app_state != 'RUNNING':
            if source in {'hotkey', "button", 'manual'}:
                self._show_info("Second Throttle", "Save the configuration and go back to RUNNING mode to use the macro.")
            return
        if self.second_throttle_engine.active and self.second_throttle_active_macro_id == macro_id:
            self._deactivate_second_throttle(notify=True)
            return
        self._activate_second_throttle_macro(macro_id, source, notify=True)

    def toggle_second_throttle(self) -> None:
        """Turn everything off, or activate the first macro while idle."""
        if self.second_throttle_engine.active:
            self._deactivate_second_throttle(notify=True)
            return
        if not self.second_throttle_macros:
            self._show_warning("Second Throttle", "Add at least one macro.")
            return
        self.toggle_second_throttle_macro(str(self.second_throttle_macros[0].get('id') or ''), "button")

    def _deactivate_second_throttle(self, *, notify: bool=False, reason: str='') -> None:
        engine = getattr(self, 'second_throttle_engine', None)
        was_active = bool(engine and engine.active)
        macro_name = self.second_throttle_active_macro_name
        self.second_throttle_active_macro_id = None
        self.second_throttle_active_macro_name = ''
        self.second_throttle_active_source = ''
        self.second_throttle_pedal_takeover.reset()
        if engine is not None:
            engine.deactivate()
        if notify and was_active:
            suffix = f' • {macro_name}' if macro_name else ''
            reason_suffix = f' • {reason}' if reason else ''
            self.notify_overlay_status(f'2nd throttle{suffix}: OFF{reason_suffix}', '#ff6b6b')
        self._refresh_second_throttle_panel()

    def _second_throttle_automation_loop(self) -> None:
        """Observe the configured pedal takeover while a macro is active."""
        interval_ms = 90
        try:
            if self.app_state != 'RUNNING':
                interval_ms = 250
                return
            status = self.telemetry_hub.status()
            if not status.connected:
                interval_ms = 250
                return
            if self.second_throttle_engine.active and self.second_throttle_pedal_takeover.enabled:
                event = self.second_throttle_pedal_takeover.update(self._read_ir_value('ThrottleRaw', use_cache=True), active_macro_id=self.second_throttle_active_macro_id)
                if event is not None and event.action == 'armed':
                    self.second_throttle_detail_var.set(f'Experimental cut armed: when the physical pedal comes back above {event.threshold_percentage}%, the macro turns off.')
                    self.notify_overlay_status(f'Second throttle • pedal cut armed above {event.threshold_percentage}%', '#ffd166')
                    self._refresh_second_throttle_panel()
                elif event is not None and event.action == 'cut':
                    self._deactivate_second_throttle(notify=True, reason=f'physical pedal back at {event.physical_percentage}%')
        except Exception as exc:
            print(f'[Second Throttle] Pedal protection failed: {exc}')
        finally:
            self.root.after(interval_ms, self._second_throttle_automation_loop)

    def _refresh_clear_target_bind_button(self):
        """Update the clear-target hotkey button text/color."""
        if not self.btn_clear_target_bind:
            return
        if self.clear_target_bind:
            bg_color = '#90ee90' if 'JOY' in self.clear_target_bind else '#ADD8E6'
            self.btn_clear_target_bind.config(text=_format_input_code_label(self.clear_target_bind), bg=bg_color)
        else:
            self.btn_clear_target_bind.config(text="Set clear hotkey", bg='#f0f0f0')

    def _refresh_manual_rescan_bind_button(self):
        """Update the manual-rescan hotkey button text/color."""
        if not self.btn_manual_rescan_bind:
            return
        if self.manual_rescan_bind:
            bg_color = '#90ee90' if 'JOY' in self.manual_rescan_bind else '#ADD8E6'
            self.btn_manual_rescan_bind.config(text=_format_input_code_label(self.manual_rescan_bind), bg=bg_color)
        else:
            self.btn_manual_rescan_bind.config(text="Set scan hotkey", bg='#f0f0f0')

    def _refresh_lapdist_toggle_bind_button(self):
        """Update the LapDist toggle hotkey button text/color."""
        if not self.btn_lapdist_toggle_bind:
            return
        if self.lapdist_toggle_bind:
            bg_color = '#90ee90' if 'JOY' in self.lapdist_toggle_bind else '#ADD8E6'
            self.btn_lapdist_toggle_bind.config(text=_format_input_code_label(self.lapdist_toggle_bind), bg=bg_color)
        else:
            self.btn_lapdist_toggle_bind.config(text="Set on/off", bg='#f0f0f0')

    def _refresh_surface_toggle_bind_button(self):
        """Update the Dry/Wet toggle hotkey button text/color."""
        if not self.btn_surface_toggle_bind:
            return
        if self.surface_toggle_bind:
            bg_color = '#90ee90' if 'JOY' in self.surface_toggle_bind else '#ADD8E6'
            self.btn_surface_toggle_bind.config(text=_format_input_code_label(self.surface_toggle_bind), bg=bg_color)
        else:
            self.btn_surface_toggle_bind.config(text="Set Dry/Wet hotkey", bg='#f0f0f0')

    def _refresh_surface_dry_bind_button(self):
        """Update the Dry-only hotkey button text/color."""
        if not self.btn_surface_dry_bind:
            return
        if self.surface_dry_bind:
            bg_color = '#90ee90' if 'JOY' in self.surface_dry_bind else '#ADD8E6'
            self.btn_surface_dry_bind.config(text=_format_input_code_label(self.surface_dry_bind), bg=bg_color)
        else:
            self.btn_surface_dry_bind.config(text="Set Dry hotkey", bg='#f0f0f0')

    def _refresh_surface_wet_bind_button(self):
        """Update the Wet-only hotkey button text/color."""
        if not self.btn_surface_wet_bind:
            return
        if self.surface_wet_bind:
            bg_color = '#90ee90' if 'JOY' in self.surface_wet_bind else '#ADD8E6'
            self.btn_surface_wet_bind.config(text=_format_input_code_label(self.surface_wet_bind), bg=bg_color)
        else:
            self.btn_surface_wet_bind.config(text="Set Wet hotkey", bg='#f0f0f0')

    def _refresh_surface_dc_bind_buttons(self) -> None:
        """Refresh independent dry/wet profile hotkey buttons."""
        for binding, button, empty_text in ((self.surface_dc_dry_bind, self.btn_surface_dc_dry_bind, "3. Set hotkey"), (self.surface_dc_wet_bind, self.btn_surface_dc_wet_bind, "3. Set hotkey")):
            if button is None:
                continue
            if binding:
                bg_color = '#90ee90' if 'JOY' in binding else '#ADD8E6'
                button.config(text=f'3. Hotkey: {_format_input_code_label(binding)}', bg=bg_color)
            else:
                button.config(text=empty_text, bg='#f0f0f0')

    def _refresh_wiper_debug_bind_button(self):
        """Update the wiper debug hotkey button text/color."""
        if not self.btn_wiper_debug_bind:
            return
        if self.wiper_debug_bind:
            bg_color = '#90ee90' if 'JOY' in self.wiper_debug_bind else '#ADD8E6'
            self.btn_wiper_debug_bind.config(text=_format_input_code_label(self.wiper_debug_bind), bg=bg_color)
        else:
            self.btn_wiper_debug_bind.config(text="Set wiper hotkey", bg='#f0f0f0')

    def _refresh_bounds_discovery_bind_button(self):
        """Compatibility no-op: the old bounds hotkey was retired."""
        return

    def _set_clear_target_bind(self):
        """Capture an optional hotkey for clearing target attempts."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.clear_target_bind
        if self.btn_clear_target_bind:
            self.btn_clear_target_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:clear_target_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.clear_target_bind = previous_bind
                self._refresh_clear_target_bind_button()
                return
            self.clear_target_bind = code
        elif code == 'CANCEL':
            self.clear_target_bind = None
        self._refresh_clear_target_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_lapdist_toggle_bind(self):
        """Capture an optional hotkey for toggling LapDist macros."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.lapdist_toggle_bind
        if self.btn_lapdist_toggle_bind:
            self.btn_lapdist_toggle_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:lapdist_toggle_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.lapdist_toggle_bind = previous_bind
                self._refresh_lapdist_toggle_bind_button()
                return
            self.lapdist_toggle_bind = code
        elif code == 'CANCEL':
            self.lapdist_toggle_bind = None
        self._refresh_lapdist_toggle_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_surface_toggle_bind(self):
        """Capture an optional hotkey for toggling DRY/WET presets."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.surface_toggle_bind
        if self.btn_surface_toggle_bind:
            self.btn_surface_toggle_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:surface_toggle_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.surface_toggle_bind = previous_bind
                self._refresh_surface_toggle_bind_button()
                return
            self.surface_toggle_bind = code
        elif code == 'CANCEL':
            self.surface_toggle_bind = None
        self._refresh_surface_toggle_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_surface_dry_bind(self):
        """Capture an optional hotkey for switching to DRY presets."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.surface_dry_bind
        if self.btn_surface_dry_bind:
            self.btn_surface_dry_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:surface_dry_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.surface_dry_bind = previous_bind
                self._refresh_surface_dry_bind_button()
                return
            self.surface_dry_bind = code
        elif code == 'CANCEL':
            self.surface_dry_bind = None
        self._refresh_surface_dry_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_surface_wet_bind(self):
        """Capture an optional hotkey for switching to WET presets."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.surface_wet_bind
        if self.btn_surface_wet_bind:
            self.btn_surface_wet_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:surface_wet_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.surface_wet_bind = previous_bind
                self._refresh_surface_wet_bind_button()
                return
            self.surface_wet_bind = code
        elif code == 'CANCEL':
            self.surface_wet_bind = None
        self._refresh_surface_wet_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _capture_surface_dc_hotkey(self, surface: str) -> None:
        """Capture a hotkey for applying only the independent DC value profile."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        surface_key = self._normalize_surface_label(surface)
        is_dry = surface_key == 'DRY'
        attr_name = 'surface_dc_dry_bind' if is_dry else 'surface_dc_wet_bind'
        source_id = f'app:{attr_name}'
        button = self.btn_surface_dc_dry_bind if is_dry else self.btn_surface_dc_wet_bind
        previous_bind = getattr(self, attr_name)
        self.focus_window()
        if button:
            button.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, source_id)
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                setattr(self, attr_name, previous_bind)
                self._refresh_surface_dc_bind_buttons()
                return
            setattr(self, attr_name, code)
        elif code == 'CANCEL':
            setattr(self, attr_name, None)
        self._refresh_surface_dc_bind_buttons()
        self.schedule_save()

    def _set_surface_dc_dry_bind(self) -> None:
        self._capture_surface_dc_hotkey('DRY')

    def _set_surface_dc_wet_bind(self) -> None:
        self._capture_surface_dc_hotkey('WET')

    def _set_wiper_debug_bind(self):
        """Capture an optional hotkey for wiper precipitation debug."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.wiper_debug_bind
        if self.btn_wiper_debug_bind:
            self.btn_wiper_debug_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:wiper_debug_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.wiper_debug_bind = previous_bind
                self._refresh_wiper_debug_bind_button()
                return
            self.wiper_debug_bind = code
        elif code == 'CANCEL':
            self.wiper_debug_bind = None
        self._refresh_wiper_debug_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_bounds_discovery_bind(self):
        """Compatibility entry point for builds that exposed the old hotkey."""
        self._show_info("Detect Limits", "The hotkey was removed. Use the yellow “Detect Limits” button at the top.")

    def clear_all_targets(self):
        """Stop all active target adjustments."""
        for controller in self.controllers.values():
            controller.clear_target()
        self._deactivate_second_throttle()
        self._stop_hybrid_hold()
        self._lapdist_macro_state.clear()
        self._lapdist_auto_state.clear()
        self._lapdist_clear_lap_index = self._lapdist_lap_index
        self.notify_overlay_status("Targets cleared", 'orange')

    def _set_manual_rescan_bind(self):
        """Capture an optional hotkey for manual restart + rescan."""
        if self.app_state != 'CONFIG':
            self._show_info("Warning", "Enter CONFIG mode first.")
            return
        self.focus_window()
        previous_bind = self.manual_rescan_bind
        if self.btn_manual_rescan_bind:
            self.btn_manual_rescan_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:manual_rescan_bind')
            if conflict:
                self._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                self.manual_rescan_bind = previous_bind
                self._refresh_manual_rescan_bind_button()
                return
            self.manual_rescan_bind = code
        elif code == 'CANCEL':
            self.manual_rescan_bind = None
        self._refresh_manual_rescan_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _build_voice_phrase_map(self) -> Dict[str, Callable]:
        """Collect current voice phrases mapped to their actions."""
        if not VOICE_FEATURES_ENABLED:
            return {}
        voice_phrases: Dict[str, Callable] = {}
        for var_name, tab in self.tabs.items():
            config = tab.get_config()
            controller = self.controllers[var_name]
            for idx, preset in enumerate(config.get('presets', [])):
                val_str = preset.get('val')
                if not val_str:
                    continue
                try:
                    target = float(val_str)
                except Exception:
                    continue
                phrase = preset.get('voice_phrase', '').strip().lower()
                if phrase:
                    voice_phrases[phrase] = self._make_preset_action(controller, target, var_name, idx, preset)
        if self.combo_tab:
            combo_config = self.combo_tab.get_config()
            for preset in combo_config.get('presets', []):
                values = preset.get('vals', {})
                phrase = preset.get('voice_phrase', '').strip().lower()
                if not phrase:
                    continue
                voice_phrases[phrase] = self._make_combo_action(values)
        return voice_phrases

    def _format_vosk_status(self) -> str:
        """Return a user-friendly status string for Vosk usage."""
        engine = self.voice_engine.get()
        model_path = self.vosk_model_path.get()
        model_name = os.path.basename(model_path.rstrip(os.sep)) if model_path else ''
        if engine != 'vosk':
            if model_path:
                return f'Vosk model selected: {model_name or model_path}'
            return "Using the Windows speech recognizer"
        if not HAS_VOSK:
            if VOSK_IMPORT_ERROR:
                return f'Vosk unavailable: {VOSK_IMPORT_ERROR}'
            return "Vosk not installed"
        if not model_path:
            return "Select a Vosk model folder"
        if voice_listener._vosk_error:
            return f'Model error: {voice_listener._vosk_error}'
        if voice_listener.vosk_model is not None:
            name = model_name or model_path
            return f'Vosk ready: {name}'
        return f'Vosk model selected: {model_name or model_path}'

    def _format_whisper_status(self) -> str:
        """Return a status string for whisper.cpp usage."""
        engine = self.voice_engine.get()
        if engine != 'whisper.cpp':
            if self.whisper_binary_path.get() or self.whisper_model_path.get():
                return self._format_whisper_status_details()
            return ''
        return self._format_whisper_status_details()

    def _format_whisper_status_details(self) -> str:
        """Return detailed whisper.cpp selection status."""
        if not self.whisper_binary_path.get():
            if self.whisper_model_path.get():
                model_name = os.path.basename(self.whisper_model_path.get())
                return f'Whisper model selected: {model_name}'
            return "Select the whisper.cpp executable"
        if not os.path.exists(self.whisper_binary_path.get()):
            return "Executable not found"
        if not self.whisper_model_path.get():
            exe_name = os.path.basename(self.whisper_binary_path.get())
            return f'whisper.cpp selected: {exe_name} (choose the model)'
        if not os.path.exists(self.whisper_model_path.get()):
            return "Model not found"
        if voice_listener._whisper_error:
            return f'Whisper error: {voice_listener._whisper_error}'
        model_name = os.path.basename(self.whisper_model_path.get())
        exe_name = os.path.basename(self.whisper_binary_path.get())
        return f'Whisper ready: {exe_name} | {model_name}'

    def on_voice_engine_changed(self):
        """Handle engine dropdown changes."""
        if not VOICE_FEATURES_ENABLED:
            return
        selection = self.voice_engine_combo.get() if self.voice_engine_combo else self.voice_engine.get()
        if selection not in {'speech', 'vosk', 'whisper.cpp'}:
            selection = 'speech'
        if selection == 'vosk' and (not HAS_VOSK):
            selection = 'speech'
        self.voice_engine.set(selection)
        self._update_voice_controls()
        self.register_current_listeners()

    def choose_vosk_model(self):
        """Prompt the user to select a Vosk model directory."""
        path = filedialog.askdirectory(title="Select the Vosk model folder")
        if not path:
            return
        self.vosk_model_path.set(path)
        self._update_voice_controls()
        self.register_current_listeners()
        self.schedule_save()

    def choose_whisper_binary(self):
        """Prompt the user to select the whisper.cpp executable."""
        path = filedialog.askopenfilename(title="Select the whisper.cpp executable", filetypes=[("Executable", '*'), ("All files", '*')])
        if not path:
            return
        self.whisper_binary_path.set(path)
        self._update_voice_controls()
        self.register_current_listeners()
        self.schedule_save()

    def choose_whisper_model(self):
        """Prompt the user to select a whisper.cpp model file."""
        path = filedialog.askopenfilename(title="Select the Whisper model (.bin/.gguf)", filetypes=[('Modelos Whisper', '*.bin *.gguf'), ("All files", '*')])
        if not path:
            return
        self.whisper_model_path.set(path)
        self._update_voice_controls()
        self.register_current_listeners()
        self.schedule_save()

    def _update_voice_controls(self):
        """Refresh UI state and listener config for voice engine selection."""
        if not VOICE_FEATURES_ENABLED:
            return
        voice_listener.update_tuning(self._voice_tuning_config())
        self._apply_audio_preferences()
        engine = self.voice_engine.get()
        if engine == 'vosk' and (not HAS_VOSK):
            engine = 'speech'
            self.voice_engine.set(engine)
            if self.voice_engine_combo:
                self.voice_engine_combo.set(engine)
        if engine == 'vosk':
            voice_listener.set_engine(engine, self.vosk_model_path.get())
        elif engine == 'whisper.cpp':
            voice_listener.set_engine(engine, self.vosk_model_path.get(), self.whisper_binary_path.get(), self.whisper_model_path.get())
        else:
            voice_listener.set_engine('speech', '')
        btn_state = 'normal' if engine == 'vosk' and HAS_VOSK else 'disabled'
        if self.btn_vosk_model:
            self.btn_vosk_model.config(state=btn_state)
        self.vosk_status_var.set(self._format_vosk_status())
        whisper_state = 'normal' if engine == 'whisper.cpp' else 'disabled'
        if self.btn_whisper_binary:
            self.btn_whisper_binary.config(state=whisper_state)
        if self.btn_whisper_model:
            self.btn_whisper_model.config(state=whisper_state)
        self.whisper_status_var.set(self._format_whisper_status())

    def open_voice_test_dialog(self):
        """Open the dialog that validates configured voice commands."""
        if not VOICE_FEATURES_ENABLED:
            return
        if not HAS_SPEECH:
            self._show_info("Voice unavailable", "Install the 'speech_recognition' package to enable voice control.")
            return
        phrases_map = self._build_voice_phrase_map()
        self.voice_phrase_map = phrases_map
        if not phrases_map:
            self._show_info("No macro found", "Add phrases in the tabs to test voice commands.")
            return
        VoiceTestDialog(self.root, self, phrases_map)

    def on_voice_toggle(self):
        """Persist and (re)register voice triggers when toggled."""
        if not VOICE_FEATURES_ENABLED:
            return
        self.register_current_listeners()
        self.schedule_save()

    def register_current_listeners(self):
        """Register keyboard/joystick listeners based on current config."""
        self._clear_keyboard_hotkeys()
        input_manager.listeners.clear()
        input_manager.release_listeners.clear()
        self._release_all_ghost_rows()
        self.voice_phrase_map = {}
        if self.app_state != 'RUNNING':
            input_manager.active = False
            voice_listener.set_enabled(False)
            return
        if not input_manager.safe_mode:
            input_manager.connect_allowed_devices(input_manager.allowed_devices)
        voice_phrases: Dict[str, Callable] = {}

        def _register_input_binding(bind_code: Optional[str], action: Callable, *, edge_only: bool=False, release_action: Optional[Callable]=None) -> None:
            """Register keyboard hotkeys or joystick listeners with compat aliases.

            ``release_action`` is only used by held bindings, which always
            arrive with ``edge_only`` set so the press fires once per stroke.
            """
            if not bind_code:
                return
            bind_code = str(bind_code).strip()
            keyscan_code = _normalize_key_scancode_code(bind_code)
            if keyscan_code:
                edge_state = {'down': False}

                def _on_keyscan_event(event, expected_code=keyscan_code, callback=action, on_release=release_action, state=edge_state):
                    event_code = _keyboard_event_input_code(event)
                    if event_code != expected_code:
                        return
                    if event.event_type == 'up':
                        was_down = state['down']
                        state['down'] = False
                        if on_release and was_down:
                            _CALLBACK_DISPATCHER.submit(on_release)
                        return
                    if event.event_type != 'down':
                        return
                    if edge_only and state['down']:
                        return
                    state['down'] = True
                    _CALLBACK_DISPATCHER.submit(callback)
                handle = keyboard.hook(_on_keyscan_event)
                self._hotkey_handles.append(handle)
                return
            if bind_code.startswith('KEY:'):
                key_name = bind_code.split(':', 1)[1].lower()
                if edge_only:
                    edge_state = {'down': False}

                    def _on_named_key_event(event, expected_name=key_name, callback=action, on_release=release_action, state=edge_state):
                        event_name = str(getattr(event, 'name', '') or '').lower()
                        if event_name != expected_name:
                            return
                        if event.event_type == 'up':
                            was_down = state['down']
                            state['down'] = False
                            if on_release and was_down:
                                _CALLBACK_DISPATCHER.submit(on_release)
                            return
                        if event.event_type != 'down' or state['down']:
                            return
                        state['down'] = True
                        _CALLBACK_DISPATCHER.submit(callback)
                    handle = keyboard.hook(_on_named_key_event)
                    self._hotkey_handles.append(handle)
                    return
                handle = keyboard.add_hotkey(key_name, action)
                self._hotkey_handles.append(handle)
                return
            input_manager.listeners[bind_code] = action
            button_idx = _parse_joy_button_code(bind_code)
            single_device_alias = f'JOYANY:{button_idx}' if button_idx is not None and len(input_manager.joysticks) == 1 else None
            if single_device_alias:
                input_manager.listeners.setdefault(single_device_alias, action)
            if release_action:
                input_manager.release_listeners[bind_code] = release_action
                if single_device_alias:
                    input_manager.release_listeners.setdefault(single_device_alias, release_action)
        for var_name, tab in self.tabs.items():
            config = tab.get_config()
            controller = self.controllers[var_name]
            manual_increase_bind = config.get('ghost_increase_bind')
            if manual_increase_bind:
                _register_input_binding(manual_increase_bind, lambda tab=tab: tab.trigger_manual_pulse_hotkey('increase'))
            manual_decrease_bind = config.get('ghost_decrease_bind')
            if manual_decrease_bind:
                _register_input_binding(manual_decrease_bind, lambda tab=tab: tab.trigger_manual_pulse_hotkey('decrease'))
            for idx, preset in enumerate(config.get('presets', [])):
                bind = preset.get('bind')
                val_str = preset.get('val')
                if not val_str:
                    continue
                try:
                    target = float(val_str)
                except Exception:
                    continue
                action = self._make_preset_action(controller, target, var_name, idx, preset)
                row = tab.preset_rows[idx] if idx < len(tab.preset_rows) else None
                if row is not None:
                    macro_action = action

                    def action(tab=tab, row=row, macro_action=macro_action):
                        if self.lapdist_capture.handle_hotkey(tab, row):
                            return
                        macro_action()
                _register_input_binding(bind, action)
                phrase = preset.get('voice_phrase', '').strip().lower()
                if phrase:
                    voice_phrases[phrase] = action
        if self.combo_tab:
            combo_config = self.combo_tab.get_config()
            for idx, preset in enumerate(combo_config.get('presets', [])):
                bind = preset.get('bind')
                values = preset.get('vals', {})
                action = self._make_combo_action(values)
                row = self.combo_tab.preset_rows[idx] if idx < len(self.combo_tab.preset_rows) else None
                if row is not None:
                    combo_action = action

                    def action(tab=self.combo_tab, row=row, combo_action=combo_action):
                        if self.lapdist_capture.handle_hotkey(tab, row):
                            return
                        combo_action()
                _register_input_binding(bind, action)
                phrase = preset.get('voice_phrase', '').strip().lower()
                if phrase:
                    voice_phrases[phrase] = action
        self.voice_phrase_map = voice_phrases
        if self.clear_target_bind:
            action = self.clear_all_targets
            _register_input_binding(self.clear_target_bind, action)
        if self.manual_rescan_bind:
            action = self.manual_restart_scan
            _register_input_binding(self.manual_rescan_bind, action)
        if self.lapdist_toggle_bind:
            action = self.toggle_lapdist_hotkey
            _register_input_binding(self.lapdist_toggle_bind, action)
        if self.surface_toggle_bind:
            action = self.toggle_surface_preset
            _register_input_binding(self.surface_toggle_bind, action)
        if self.surface_dry_bind:
            action = lambda: self.set_surface_preset('DRY')
            _register_input_binding(self.surface_dry_bind, action)
        if self.surface_wet_bind:
            action = lambda: self.set_surface_preset('WET')
            _register_input_binding(self.surface_wet_bind, action)
        if self.surface_dc_dry_bind:
            action = lambda: self.apply_surface_dc_profile('DRY', source='hotkey')
            _register_input_binding(self.surface_dc_dry_bind, action)
        if self.surface_dc_wet_bind:
            action = lambda: self.apply_surface_dc_profile('WET', source='hotkey')
            _register_input_binding(self.surface_dc_wet_bind, action)
        if self.turbo_pit_toggle_bind:
            action = lambda: self.ui(self._toggle_turbo_pit_from_card)
            _register_input_binding(self.turbo_pit_toggle_bind, action)
        for macro in self.second_throttle_macros:
            macro_id = str(macro.get('id') or '')
            bind = macro.get('bind')
            if not bind or self._second_throttle_configuration_error(macro_id):
                continue
            _register_input_binding(bind, lambda macro_id=macro_id: self.ui(self.toggle_second_throttle_macro, macro_id, 'hotkey'), edge_only=True)
        if self.wiper_debug_bind:
            action = self.trigger_wiper_debug
            _register_input_binding(self.wiper_debug_bind, action)
        self._register_ghost_key_listeners(_register_input_binding)
        input_manager.active = self.app_state == 'RUNNING'
        if self.app_state != 'RUNNING':
            voice_listener.set_enabled(False)
        elif self.use_voice.get():
            voice_listener.update_tuning(self._voice_tuning_config())
            voice_listener.set_engine(self.voice_engine.get(), self.vosk_model_path.get(), self.whisper_binary_path.get(), self.whisper_model_path.get())
            voice_listener.set_phrases(self.voice_phrase_map)
            voice_listener.set_enabled(True)
        else:
            voice_listener.set_enabled(False)

    def _refresh_controller_ir(self):
        """Ensure all controllers use the latest IRSDK handle."""
        for controller in self.controllers.values():
            controller.ir = self.ir

    def _clear_keyboard_hotkeys(self):
        """Remove all keyboard hotkeys registered by the app."""
        if not hasattr(self, '_hotkey_handles'):
            self._hotkey_handles: List[Any] = []
        for handle in self._hotkey_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
            try:
                keyboard.unhook(handle)
            except Exception:
                pass
        self._hotkey_handles.clear()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

    def _apply_inline_config(self, tab_configs: Dict[str, Dict[str, Any]], combo_config: Dict[str, Any]):
        """Reapply unsaved tab/combo configuration after a rescan."""
        wiper_config_applied = False
        for var_name, config in tab_configs.items():
            if var_name in self.tabs:
                try:
                    self.tabs[var_name].set_config(config)
                    if var_name in WIPER_TOGGLE_VARS:
                        wiper_config_applied = True
                except Exception:
                    pass
        if not wiper_config_applied:
            wiper_tab = self._wiper_tab()
            wiper_cfg = self._wiper_alias_config(tab_configs)
            if wiper_tab and wiper_cfg:
                try:
                    wiper_tab.set_config(wiper_cfg)
                except Exception:
                    pass
        if self.combo_tab and combo_config:
            try:
                self.combo_tab.set_config(combo_config)
            except Exception:
                pass
__all__ = ['BindingsMixin']
