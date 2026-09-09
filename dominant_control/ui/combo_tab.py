from __future__ import annotations
from ..foundation import *
from .dialogs import *
from .hud import *
from .controller import GenericController
CHIP_COLUMNS = 3

class ComboTab(tk.Frame):
    """
    Tab for creating combo macros that adjust multiple variables with one
    trigger. Each combo only lists the variables it actually touches (added
    on demand as "chips"), instead of showing one fixed entry column per
    every scanned control in the car.
    """

    def __init__(self, parent, controllers_dict: Dict[str, GenericController], app):
        super().__init__(parent)
        self.app = app
        self.controllers = controllers_dict
        self.var_names = list(self.controllers.keys())
        self.var_display_names = {name: compact_driver_control_name(name) for name in self.var_names}
        self.preset_rows: List[Dict[str, Any]] = []
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill='both', expand=True)
        body = scroll_frame.inner
        tk.Label(body, text='⚡ Combos', fg='#b45f06', font=('Segoe UI Semibold', 13)).pack(anchor='w', padx=10, pady=(10, 0))
        tk.Label(body, text="One hotkey adjusts several variables at once. Add only the ones this combo has to change — the rest are left alone.", fg='gray', font=('Arial', 9), wraplength=760, justify='left').pack(anchor='w', padx=10, pady=(0, 8))
        if VOICE_FEATURES_ENABLED:
            tk.Label(body, text="Optional voice trigger: type the exact phrase you will say to fire the combo. The voice options live in Options → Voice/Audio Settings.", fg='gray', font=('Arial', 8), wraplength=760, justify='left').pack(anchor='w', padx=10, pady=(0, 4))
        self.presets_container = tk.Frame(body)
        self.presets_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.btn_add_combo_row = tk.Button(body, text="+ Add combo", command=self.add_dynamic_row, bg='#f0f0f0')
        self.btn_add_combo_row.pack(fill='x', padx=5, pady=(0, 5))
        for _ in range(2):
            self.add_dynamic_row()

    def set_discreet_mode(self, enabled: bool) -> None:
        """Show or hide automation-focused UI elements."""
        for row in self.preset_rows:
            self._toggle_pack_widget(row.get('lapdist_container'), not enabled)
            self._toggle_pack_widget(row.get('capture_button'), not enabled)

    def _toggle_pack_widget(self, widget: Optional[tk.Widget], show: bool) -> None:
        if widget is None:
            return
        if show:
            if widget.winfo_manager() == 'pack':
                return
            info = getattr(widget, '_pack_info', None)
            if not info:
                return
            widget.pack(**info)
        else:
            if widget.winfo_manager() != 'pack':
                return
            info = widget.pack_info()
            if 'in' in info:
                info['in_'] = info.pop('in')
            widget._pack_info = info
            widget.pack_forget()

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = 'normal' if enabled else 'readonly'
        button_state = 'normal' if enabled else 'disabled'
        for row in self.preset_rows:
            for entry in row['entries'].values():
                try:
                    entry.config(state=state)
                except Exception:
                    pass
            for lap_entry in (row.get('lap_dist_min_entry'), row.get('lap_dist_max_entry')):
                if lap_entry:
                    try:
                        lap_entry.config(state=state)
                    except Exception:
                        pass
            for widget in (row.get('voice_entry'), row.get('name_entry')):
                if widget:
                    try:
                        widget.config(state=state)
                    except Exception:
                        pass
            for widget in (row.get('delete_button'), row.get('add_var_button')):
                if widget:
                    try:
                        widget.config(state=button_state)
                    except Exception:
                        pass
            for button in row.get('chip_remove_buttons', {}).values():
                try:
                    button.config(state=button_state)
                except Exception:
                    pass
            picker = row.get('var_picker')
            if picker:
                try:
                    picker.config(state='readonly' if enabled else 'disabled')
                except Exception:
                    pass
        if self.btn_add_combo_row:
            try:
                self.btn_add_combo_row.config(state=button_state)
            except Exception:
                pass

    def _bind_autosave_entry(self, entry: tk.Entry) -> None:
        """Attach auto-save handlers to entries."""
        entry.bind('<KeyRelease>', lambda _event: self.app.schedule_preset_save())
        entry.bind('<FocusOut>', lambda _event: self.app.schedule_preset_save())

    def _bind_lapdist_sort(self, entry: tk.Entry) -> None:
        """Attach LapDist sorting handlers to entries."""
        entry.bind('<FocusOut>', lambda _event: self._sort_lapdist_rows(), add='+')

    def _sort_lapdist_rows(self) -> None:
        """Sort LapDist combo rows from lowest to highest LapDist values."""
        if len(self.preset_rows) < 2:
            return

        def lapdist_value(row: Dict[str, Any]) -> Optional[float]:
            min_entry = row.get('lap_dist_min_entry')
            max_entry = row.get('lap_dist_max_entry')
            for entry in (min_entry, max_entry):
                if not entry:
                    continue
                try:
                    candidate = entry.get().strip()
                except Exception:
                    continue
                if not candidate:
                    continue
                try:
                    return float(candidate)
                except Exception:
                    continue
            return None
        macro_rows = list(self.preset_rows)
        macro_rows.sort(key=lambda row: (lapdist_value(row) is None, lapdist_value(row) if lapdist_value(row) is not None else 0.0))
        self.preset_rows = macro_rows
        for row in self.preset_rows:
            frame = row.get('frame')
            if not frame:
                continue
            frame.pack_forget()
            frame.pack(fill='x', pady=4)

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

    def _refresh_variable_picker(self, row_data: Dict[str, Any]) -> None:
        """Keep the "add variable" dropdown limited to unused variables."""
        picker = row_data.get('var_picker')
        if picker is None:
            return
        used = set(row_data['entries'].keys())
        label_map: Dict[str, str] = {}
        seen_labels = set()
        labels: List[str] = []
        for name in self.var_names:
            if name in used:
                continue
            label = self.var_display_names.get(name, name)
            if label in seen_labels:
                label = f'{label} ({name})'
            seen_labels.add(label)
            label_map[label] = name
            labels.append(label)
        row_data['_picker_label_map'] = label_map
        picker.config(values=labels)
        if picker.get() not in label_map:
            picker.set(labels[0] if labels else '')
        editable = self.app.app_state == 'CONFIG'
        picker.config(state='readonly' if labels and editable else 'disabled')
        add_button = row_data.get('add_var_button')
        if add_button:
            add_button.config(state='normal' if labels and editable else 'disabled')

    def _layout_chips(self, row_data: Dict[str, Any]) -> None:
        """Arrange variable chips in a wrapping grid inside the row's card."""
        chips = row_data['chips']
        empty_label = row_data.get('empty_label')
        if not chips:
            if empty_label:
                empty_label.grid(row=0, column=0, sticky='w')
            return
        if empty_label:
            empty_label.grid_forget()
        for index, chip in enumerate(chips.values()):
            row, column = divmod(index, CHIP_COLUMNS)
            chip.grid(row=row, column=column, sticky='w', padx=(0, 6), pady=(0, 4))

    def _add_variable_chip(self, row_data: Dict[str, Any], var_name: str, value_text: str='') -> None:
        """Add one variable + target-value chip to a combo card."""
        if var_name not in self.controllers or var_name in row_data['entries']:
            return
        chips_frame = row_data['chips_frame']
        chip = tk.Frame(chips_frame, bg='#eef2f6', bd=1, relief='solid')
        display = self.var_display_names.get(var_name, var_name)
        tk.Label(chip, text=display, bg='#eef2f6', fg='#22313f', font=('Arial', 8, 'bold')).pack(side='left', padx=(6, 4), pady=3)
        entry = ttk.Entry(chip, width=8)
        entry.pack(side='left', padx=(0, 4), pady=3)
        if value_text:
            entry.insert(0, value_text)
        if self.app.app_state != 'CONFIG':
            entry.config(state='readonly')
        self._bind_autosave_entry(entry)
        remove_button = tk.Button(chip, text='x', fg='#a4161a', bd=0, padx=4, command=lambda r=row_data, v=var_name: self._remove_variable_chip(r, v))
        remove_button.pack(side='left', padx=(0, 4), pady=3)
        if self.app.app_state != 'CONFIG':
            remove_button.config(state='disabled')
        row_data['entries'][var_name] = entry
        row_data['chips'][var_name] = chip
        row_data['chip_remove_buttons'][var_name] = remove_button
        self._layout_chips(row_data)

    def _remove_variable_chip(self, row_data: Dict[str, Any], var_name: str) -> None:
        if self.app.app_state != 'CONFIG':
            self.app._show_info("Warning", "Enter CONFIG mode first.")
            return
        chip = row_data['chips'].pop(var_name, None)
        row_data['entries'].pop(var_name, None)
        row_data['chip_remove_buttons'].pop(var_name, None)
        if chip is not None:
            chip.destroy()
        self._layout_chips(row_data)
        self._refresh_variable_picker(row_data)
        self.app.schedule_preset_save()

    def _add_variable_from_picker(self, row_data: Dict[str, Any]) -> None:
        if self.app.app_state != 'CONFIG':
            self.app._show_info("Warning", "Enter CONFIG mode first.")
            return
        picker = row_data.get('var_picker')
        value_entry = row_data.get('var_value_entry')
        if picker is None:
            return
        label = picker.get().strip()
        var_name = row_data.get('_picker_label_map', {}).get(label)
        if not var_name:
            return
        value = value_entry.get().strip() if value_entry else ''
        self._add_variable_chip(row_data, var_name, value)
        if value_entry:
            value_entry.delete(0, tk.END)
        self._refresh_variable_picker(row_data)
        self.app.schedule_preset_save()

    def add_dynamic_row(self, existing: Optional[Dict[str, Any]]=None, is_reset: bool=False, pack_row: bool=True):
        """Add a combo card."""
        is_reset = False
        card = tk.Frame(self.presets_container, bd=1, relief='solid', bg='#fbfbfc')
        if pack_row:
            card.pack(fill='x', pady=4)
        row_data: Dict[str, Any] = {'frame': card, 'entries': {}, 'bind': None, 'is_reset': is_reset, 'lap_dist_min_entry': None, 'lap_dist_max_entry': None, 'lapdist_container': None, 'voice_entry': None, 'name_entry': None, 'delete_button': None, 'bind_button': None, 'capture_button': None, 'chips': {}, 'chip_remove_buttons': {}, 'chips_frame': None, 'empty_label': None, 'var_picker': None, 'var_value_entry': None, 'add_var_button': None, 'source_id': f'combo:{id(card)}'}
        top = tk.Frame(card, bg='#fbfbfc')
        top.pack(fill='x', padx=8, pady=(8, 4))
        tk.Label(top, text="Name:", bg='#fbfbfc', font=('Arial', 8)).pack(side='left')
        name_entry = ttk.Entry(top, width=16)
        name_entry.pack(side='left', padx=(4, 8))
        if existing and existing.get('label'):
            name_entry.insert(0, existing.get('label'))
        row_data['name_entry'] = name_entry
        self._bind_autosave_entry(name_entry)
        bind_button = tk.Button(top, text="Macro hotkey", width=15, fg='black')
        bind_button.pack(side='left', padx=(0, 4))
        row_data['bind_button'] = bind_button
        self._config_bind_button(bind_button, row_data)
        lapdist_container = None
        capture_button = None
        lap_dist_min_entry = None
        lap_dist_max_entry = None
        delete_button = tk.Button(top, text="Delete", fg='#a4161a', command=lambda r=row_data: self.remove_row(r), width=8)
        delete_button.pack(side='right')
        if self.app.app_state != 'CONFIG':
            delete_button.config(state='disabled')
        row_data['delete_button'] = delete_button
        if VOICE_FEATURES_ENABLED:
            voice_row = tk.Frame(card, bg='#fbfbfc')
            voice_row.pack(fill='x', padx=8, pady=(0, 4))
            tk.Label(voice_row, text="Voice phrase:", bg='#fbfbfc', font=('Arial', 8)).pack(side='left')
            voice_entry = ttk.Entry(voice_row, width=42)
            voice_entry.pack(side='left', padx=(4, 0))
            if existing and existing.get('voice_phrase'):
                voice_entry.insert(0, existing.get('voice_phrase', ''))
            if self.app.app_state != 'CONFIG':
                voice_entry.config(state='readonly')
            row_data['voice_entry'] = voice_entry
            self._bind_autosave_entry(voice_entry)
        tk.Frame(card, bg='#e3e3e6', height=1).pack(fill='x', padx=8)
        vars_section = tk.Frame(card, bg='#fbfbfc')
        vars_section.pack(fill='x', padx=8, pady=(6, 8))
        tk.Label(vars_section, text="Variables in this combo:", bg='#fbfbfc', fg='#555555', font=('Arial', 8, 'bold')).pack(anchor='w')
        chips_frame = tk.Frame(vars_section, bg='#fbfbfc')
        chips_frame.pack(fill='x', pady=(4, 4))
        row_data['chips_frame'] = chips_frame
        row_data['empty_label'] = tk.Label(chips_frame, text="No variable added yet.", bg='#fbfbfc', fg='#9aa2ab', font=('Arial', 8, 'italic'))
        self._layout_chips(row_data)
        add_var_frame = tk.Frame(vars_section, bg='#fbfbfc')
        add_var_frame.pack(fill='x')
        var_picker = ttk.Combobox(add_var_frame, state='readonly', width=24)
        var_picker.pack(side='left')
        value_entry = ttk.Entry(add_var_frame, width=10)
        value_entry.pack(side='left', padx=4)
        add_var_button = tk.Button(add_var_frame, text="+ Add", command=lambda r=row_data: self._add_variable_from_picker(r))
        add_var_button.pack(side='left')
        row_data['var_picker'] = var_picker
        row_data['var_value_entry'] = value_entry
        row_data['add_var_button'] = add_var_button
        if existing:
            for var_name, value in existing.get('vals', {}).items():
                if value in (None, ''):
                    continue
                self._add_variable_chip(row_data, var_name, str(value))
            lap_min = existing.get('lap_dist_min', existing.get('lap_dist', ''))
            lap_max = existing.get('lap_dist_max', existing.get('lap_dist', ''))
            if lap_dist_min_entry is not None and lap_dist_max_entry is not None:
                lap_dist_min_entry.config(state='normal')
                lap_dist_min_entry.delete(0, tk.END)
                lap_dist_min_entry.insert(0, lap_min)
                lap_dist_max_entry.config(state='normal')
                lap_dist_max_entry.delete(0, tk.END)
                lap_dist_max_entry.insert(0, lap_max)
                if self.app.app_state != 'CONFIG':
                    lap_dist_min_entry.config(state='readonly')
                    lap_dist_max_entry.config(state='readonly')
            row_data['bind'] = existing.get('bind')
            if row_data['bind']:
                bg_color = '#90ee90' if 'JOY' in row_data['bind'] else '#ADD8E6'
                bind_button.config(text=_format_input_code_label(row_data['bind']), bg=bg_color)
        self._refresh_variable_picker(row_data)
        self.preset_rows.append(row_data)
        if self.app.discreet_mode.get():
            self._toggle_pack_widget(lapdist_container, False)
            self._toggle_pack_widget(capture_button, False)
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
        self.app.schedule_preset_save()

    def get_config(self) -> Dict[str, Any]:
        """Get current combo configuration."""
        presets_data = []
        for row in self.preset_rows:
            values = {var_name: entry.get() for var_name, entry in row['entries'].items()}
            preset = {'vals': values, 'bind': row['bind'], 'label': row.get('name_entry').get() if row.get('name_entry') else '', 'voice_phrase': row.get('voice_entry').get() if row.get('voice_entry') else ''}
            presets_data.append(preset)
        return {'presets': presets_data}

    def set_config(self, config: Dict[str, Any]):
        """Load combo configuration."""
        capture = getattr(self.app, 'lapdist_capture', None)
        if capture is not None:
            capture.cancel_for_owner(self)
        for row in list(self.preset_rows):
            row['frame'].destroy()
        self.preset_rows.clear()
        if not config:
            for _ in range(2):
                self.add_dynamic_row()
            return
        saved_presets = [preset for preset in config.get('presets', []) if not preset.get('is_reset')]
        bulk_load = len(saved_presets) > 20
        for preset in saved_presets:
            self.add_dynamic_row(existing=preset, is_reset=False, pack_row=not bulk_load)
        if bulk_load:
            for row in self.preset_rows:
                frame = row.get('frame')
                if frame and frame.winfo_manager() != 'pack':
                    frame.pack(fill='x', pady=4)
        self._sort_lapdist_rows()
        if len(self.preset_rows) < 2:
            self.add_dynamic_row()

    def destroy(self):
        """Release an in-progress guided capture owned by this tab."""
        capture = getattr(self.app, 'lapdist_capture', None)
        if capture is not None:
            capture.cancel_for_owner(self)
        super().destroy()
__all__ = ['ComboTab']
