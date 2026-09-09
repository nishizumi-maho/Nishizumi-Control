from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *
from ..ui.ghost_keys import GhostKeysPanel
from ..ui.second_throttle import SecondThrottlePanel

def _pending_bounds_labels(tabs: Dict[str, ControlTab]) -> List[str]:
    """List key-bound numeric controls that still lack a complete range."""
    pending: List[str] = []
    for tab in tabs.values():
        if not getattr(tab, 'bounds_supported', False):
            continue
        try:
            if not tab.controller.can_discover_bounds():
                continue
            minimum, maximum = tab.controller.configured_bounds()
        except Exception:
            continue
        if minimum is None or maximum is None:
            pending.append(str(tab.label_name))
    return pending

def _refresh_turbo_pit_hotkey_button(app: Any) -> None:
    """Keep the Turbo Pit binding button understandable without a new mixin API."""
    button = getattr(app, 'btn_turbo_pit_bind', None)
    if button is None:
        return
    binding = getattr(app, 'turbo_pit_toggle_bind', None)
    if binding:
        color = '#90ee90' if 'JOY' in binding else '#ADD8E6'
        button.config(text=_format_input_code_label(binding), bg=color)
    else:
        button.config(text="Set hotkey", bg='#f0f0f0')

def _capture_turbo_pit_hotkey(app: Any) -> None:
    """Capture a global keyboard/joystick binding for Turbo Pit."""
    if app.app_state != 'CONFIG':
        app._show_info("Turbo Pit hotkey", "Switch to CONFIG mode to set or remove this hotkey.")
        return
    app.focus_window()
    previous = app.turbo_pit_toggle_bind
    if app.btn_turbo_pit_bind:
        app.btn_turbo_pit_bind.config(text='...', bg='yellow')
    app.root.update_idletasks()
    code = input_manager.capture_any_input()
    if code and code != 'CANCEL':
        conflict = app._find_hotkey_conflict(code, 'app:turbo_pit_toggle_bind')
        if conflict:
            app._show_warning("Hotkey already in use", f'{_format_input_code_label(code)} is already bound to {conflict}.')
            app.turbo_pit_toggle_bind = previous
            _refresh_turbo_pit_hotkey_button(app)
            return
        app.turbo_pit_toggle_bind = code
    elif code == 'CANCEL':
        app.turbo_pit_toggle_bind = None
    _refresh_turbo_pit_hotkey_button(app)
    if app.app_state == 'RUNNING':
        app.register_current_listeners()
    app.schedule_save()

class UiMixin:

    def _apply_startup_preference(self, notify: bool=False) -> None:
        """Create or remove the startup entry based on current preference."""
        enabled = self.start_with_windows.get()
        success = set_startup_entry(enabled)
        if success:
            return
        current = _startup_entry_exists()
        self.start_with_windows.set(current)
        if notify:
            self._show_warning("Start with Windows", "Could not update the Windows startup entry. Check that your user's Startup folder is reachable.")

    def _apply_startup_focus_mode(self) -> None:
        """Optionally keep the main window from stealing focus on startup."""
        if self.focus_on_start.get():
            return

        def _background_window() -> None:
            if not self.root.winfo_exists():
                return
            try:
                self.root.lower()
            except Exception:
                pass
        self.root.after(250, _background_window)

    def _on_startup_toggle(self) -> None:
        self._apply_startup_preference(notify=True)
        self.schedule_save()

    def _on_disable_popups_toggle(self) -> None:
        if self.disable_popups.get():
            if self.getting_started_window and self.getting_started_window.winfo_exists():
                self.getting_started_window.destroy()
                self.getting_started_window = None
        self.schedule_save()

    def _on_keep_scanning_toggle(self) -> None:
        if self.keep_scanning_until_valid.get():
            if not self._scan_in_progress:
                self.scan_driver_controls(silent_if_unavailable=True, allow_restart=False)
        else:
            self._cancel_continuous_scan_retry()
        self.schedule_save()

    def _on_auto_sync_iracing_controls_toggle(self) -> None:
        """Persist auto-sync and reset the observed controls.cfg baseline."""
        self._iracing_controls_watch_state.clear()
        self.schedule_save()

    def toggle_discreet_mode(self) -> None:
        """Toggle discreet mode visibility settings."""
        self.discreet_mode.set(not self.discreet_mode.get())
        self._apply_discreet_mode()
        self.schedule_save()

    def _apply_discreet_mode(self) -> None:
        """Show or hide automation/humanization UI based on discreet mode."""
        enabled = self.discreet_mode.get()
        if self.btn_discreet_mode:
            label = 'Layout: Simples' if enabled else "Layout: Default"
            color = '#f4b183' if enabled else '#f0f0f0'
            self.btn_discreet_mode.config(text=label, bg=color)
        if enabled:
            if self._overlay_visible_before_discreet is None:
                self._overlay_visible_before_discreet = self.overlay_visible
            self.overlay_visible = False
            try:
                if self.overlay.winfo_exists():
                    self.overlay.withdraw()
            except Exception:
                pass
        else:
            if self._overlay_visible_before_discreet is not None:
                self.overlay_visible = self._overlay_visible_before_discreet
                try:
                    if self.overlay_visible and self.overlay.winfo_exists():
                        self.overlay.deiconify()
                except Exception:
                    pass
            self._overlay_visible_before_discreet = None
        self.hud_visible_var.set(bool(self.overlay_visible))
        if self.stability_frame:
            if enabled:
                self.stability_frame.grid_remove()
            else:
                self.stability_frame.grid()
        if self.lapdist_frame:
            if enabled:
                self.lapdist_frame.grid_remove()
            else:
                self.lapdist_frame.grid()
        self._toggle_pack_widget(self.presets_frame, not enabled)
        self._toggle_pack_widget(self.devices_frame, not enabled)
        self._toggle_pack_widget(self.scan_frame, not enabled)
        for tab in self.tabs.values():
            tab.set_discreet_mode(enabled)
        if self.combo_tab:
            self.combo_tab.set_discreet_mode(enabled)
        self._apply_discreet_tabs()
        self._refresh_overlay_discreet_state()

    def _tab_is_visible(self, frame: tk.Frame) -> bool:
        if hasattr(self, 'main_tabs') and frame in (getattr(self, 'hud_tab', None), getattr(self, 'options_tab', None), getattr(self, 'diagnostics_tab', None)):
            return str(frame) in self.main_tabs.tabs()
        return str(frame) in self.notebook.tabs()

    def _apply_discreet_tabs(self) -> None:
        """Hide the advanced Combos sub-tab while the simple layout is active."""
        notebook = getattr(self, 'macros_notebook', None)
        combos_tab = getattr(self, 'combos_tab', None)
        if notebook is None or not combos_tab:
            return
        if self.discreet_mode.get():
            if str(combos_tab) == notebook.select():
                tabs = notebook.tabs()
                if tabs:
                    notebook.select(tabs[0])
            if str(combos_tab) in notebook.tabs():
                notebook.hide(combos_tab)
        elif str(combos_tab) not in notebook.tabs():
            notebook.add(combos_tab, text='Combos')

    def _schedule_control_tab_label_refresh(self, _event=None) -> None:
        """Debounce tab relabeling while the control notebook is resized."""
        if not getattr(self, 'root', None):
            return
        job = self._control_tab_label_refresh_job
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._control_tab_label_refresh_job = self.root.after(80, self._refresh_control_tab_labels)

    def _get_control_tab_font(self) -> tkfont.Font:
        if self._control_tab_font is None:
            self._control_tab_font = tkfont.Font(root=self.root, font=('Segoe UI Semibold', 9))
        return self._control_tab_font

    def _fit_control_tab_text(self, text: str, max_px: int) -> str:
        font = self._get_control_tab_font()
        if font.measure(text) <= max_px:
            return text
        if max_px <= font.measure('...'):
            return text[:1]
        base = text.strip()
        while len(base) > 1:
            base = base[:-1].rstrip()
            candidate = f'{base}...'
            if font.measure(candidate) <= max_px:
                return candidate
        return text[:1]

    def _select_control_tab_labels(self, var_names: List[str], available_width: int) -> List[str]:
        if not var_names:
            return []
        font = self._get_control_tab_font()
        candidates = [driver_control_name_candidates(name) for name in var_names]
        tab_padding_px = 22
        available_width = max(220, int(available_width) - 8)
        widths = [[font.measure(text) + tab_padding_px for text in items] for items in candidates]
        full_width = sum((item_widths[0] for item_widths in widths))
        if full_width <= available_width:
            return [items[0] for items in candidates]
        states: Dict[int, Tuple[int, int, List[str]]] = {0: (0, 0, [])}
        for items, item_widths in zip(candidates, widths):
            next_states: Dict[int, Tuple[int, int, List[str]]] = {}
            for used_width, (score, filled_width, labels) in states.items():
                for candidate_index, (text, width) in enumerate(zip(items, item_widths)):
                    new_width = used_width + width
                    if new_width > available_width:
                        continue
                    text_score = len(text.replace(' ', '')) * 12 + text.count(' ') * 4 - candidate_index * 10
                    candidate_state = (score + text_score, filled_width + width, labels + [text])
                    previous = next_states.get(new_width)
                    if previous is None or candidate_state[0] > previous[0] or (candidate_state[0] == previous[0] and candidate_state[1] > previous[1]):
                        next_states[new_width] = candidate_state
            states = next_states
            if not states:
                break
        if states:
            _width, (_score, _filled_width, labels) = max(states.items(), key=lambda item: (item[1][0], item[1][1]))
            return labels
        max_text_px = max(24, available_width // len(candidates) - tab_padding_px)
        return [self._fit_control_tab_text(items[-1], max_text_px) for items in candidates]

    def _control_notebook_width(self) -> int:
        notebook = getattr(self, 'notebook', self.root)
        notebook_width = notebook.winfo_width()
        if notebook_width > 80:
            return notebook_width
        root_width = self.root.winfo_width()
        if root_width > 80:
            return max(220, root_width - 40)
        try:
            geometry_width = int(str(self.root.geometry()).split('x', 1)[0])
            if geometry_width > 80:
                return max(220, geometry_width - 40)
        except Exception:
            pass
        req_width = notebook.winfo_reqwidth()
        if req_width > 80:
            return req_width
        return 900

    def _refresh_control_tab_labels(self) -> None:
        self._control_tab_label_refresh_job = None
        if not getattr(self, 'notebook', None):
            return
        tab_ids = list(self.notebook.tabs())
        if not tab_ids:
            return
        var_names = [var_name for var_name, _is_float, _is_boolean in self.active_vars][:len(tab_ids)]
        labels = self._select_control_tab_labels(var_names, self._control_notebook_width())
        for tab_id, label in zip(tab_ids, labels):
            try:
                self.notebook.tab(tab_id, text=label)
            except Exception:
                pass

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

    def _overlay_display_config(self, config: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return config

    def ui(self, fn: Callable, *args, **kwargs):
        """Thread-safe UI dispatcher."""
        self._uiq.put((fn, args, kwargs))

    def _ui_after(self, fn: Callable, *args, **kwargs) -> None:
        """Queue UI work without calling Tk directly from a thread."""
        self._uiq.put((fn, args, kwargs))

    def _drain_ui_queue(self):
        handled = 0
        while True:
            try:
                fn, args, kwargs = self._uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f'[Interface] Handler error: {exc}')
            finally:
                handled += 1
        next_interval = 20 if handled else 60
        self.root.after(next_interval, self._drain_ui_queue)

    def _popups_enabled(self) -> bool:
        return not self.disable_popups.get()

    def _show_info(self, title: str, message: str) -> None:
        if self._popups_enabled():
            messagebox.showinfo(title, message)

    def _show_warning(self, title: str, message: str) -> None:
        if self._popups_enabled():
            messagebox.showwarning(title, message)

    def _show_error(self, title: str, message: str) -> None:
        if self._popups_enabled():
            messagebox.showerror(title, message)

    def _ask_yes_no(self, title: str, message: str) -> bool:
        if not self._popups_enabled():
            print(f'[Pop-ups off] Automatic Yes/No answer: {title} - {message}')
            return True
        return messagebox.askyesno(title, message)

    def _ask_ok_cancel(self, title: str, message: str) -> bool:
        if not self._popups_enabled():
            print(f'[Pop-ups off] Automatic OK/Cancel answer: {title} - {message}')
            return True
        return messagebox.askokcancel(title, message)

    def _create_menu(self):
        """Create application menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        options_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Options", menu=options_menu)
        options_menu.add_command(label="Timing Settings", command=self.open_timing_window)
        if VOICE_FEATURES_ENABLED:
            options_menu.add_command(label="Voice/Audio Settings", command=self.open_voice_audio_settings)
        options_menu.add_separator()
        options_menu.add_command(label="Restart the application", command=restart_program)
        if UPDATE_CHECK_AVAILABLE:
            options_menu.add_separator()
            options_menu.add_command(label="Check for updates", command=self.check_for_updates_now)
            options_menu.add_command(label="Open the newest release page", command=self.open_latest_release_page)
        options_menu.add_separator()
        options_menu.add_command(label="Restore defaults (delete configuration)", command=self.restore_defaults)

    def _configure_styles(self) -> None:
        """Improve UI readability with consistent fonts and spacing."""
        base_font = ('Segoe UI', 10)
        heading_font = ('Segoe UI Semibold', 10)
        button_font = ('Segoe UI Semibold', 10)
        self.root.option_add('*Font', base_font)
        self.root.option_add('*Label.Font', base_font)
        self.root.option_add('*LabelFrame.Font', heading_font)
        self.root.option_add('*Button.Font', button_font)
        self.root.option_add('*Button.Padx', 10)
        self.root.option_add('*Button.Pady', 4)
        self.root.option_add('*Button.BorderWidth', 1)
        self.root.option_add('*Button.Relief', 'raised')
        self.root.option_add('*Checkbutton.Font', base_font)
        self.root.option_add('*Radiobutton.Font', base_font)
        self.root.option_add('*Entry.Font', base_font)
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TLabel', font=base_font)
        style.configure('TLabelFrame.Label', font=heading_font)
        style.configure('TButton', font=button_font, padding=(10, 6))
        style.configure('TCheckbutton', font=base_font, padding=(6, 2))
        style.configure('TRadiobutton', font=base_font, padding=(6, 2))
        style.configure('TNotebook.Tab', font=heading_font, padding=(10, 6))
        style.configure('Control.TNotebook.Tab', font=('Segoe UI Semibold', 9), padding=(7, 5))
        style.configure('TCombobox', padding=4)
        style.map('TCombobox', fieldbackground=[('disabled', '#e4e4e4')], foreground=[('disabled', '#7a7a7a')])
        style.configure('PresetLoad.TButton', font=button_font, padding=(10, 6), background='#e0e0e0', foreground='#000000')
        style.map('PresetLoad.TButton', background=[('disabled', '#c8c8c8'), ('!disabled', '#e0e0e0')], foreground=[('disabled', '#7a7a7a'), ('!disabled', '#000000')])
        style.configure('Danger.TButton', font=button_font, padding=(10, 6), background='#f8d7da', foreground='#7a1c22')
        style.map('Danger.TButton', background=[('disabled', '#e9e9e9'), ('!disabled', '#f5b5bb')], foreground=[('disabled', '#9a9a9a'), ('!disabled', '#5c1319')])

    def _create_main_ui(self):
        """Create main user interface."""
        header_frame = tk.Frame(self.root, pady=8)
        header_frame.pack(fill='x', padx=14)
        header_frame.columnconfigure(0, weight=1)
        title_box = tk.Frame(header_frame)
        title_box.grid(row=0, column=0, sticky='w')
        tk.Label(title_box, text='Dominant Control', font=('Segoe UI Semibold', 18)).pack(anchor='w')
        self.header_context_var = tk.StringVar(value="No car / No track")
        tk.Label(title_box, textvariable=self.header_context_var, fg='#5f6b7a', font=('Segoe UI', 10)).pack(anchor='w')
        mode_frame = tk.Frame(header_frame)
        mode_frame.grid(row=0, column=1, sticky='e')
        self.btn_mode = tk.Button(mode_frame, text="Mode: RUNNING", bg='#90ee90', command=self.toggle_mode, font=('Segoe UI Semibold', 10), width=28, height=2)
        self.btn_mode.pack(side='right')
        self.btn_sync_iracing_controls_header = tk.Button(mode_frame, text="Refresh Car Controls", command=self.import_iracing_controls_for_current_car, bg='#e7f4df', font=('Segoe UI Semibold', 10), width=25, height=2)
        self.btn_sync_iracing_controls_header.pack(side='right', padx=(0, 8))
        self.btn_discover_all_bounds_header = tk.Button(mode_frame, text="Detect Limits", command=self.auto_discover_all_control_bounds, bg='#f6c344', activebackground='#e4ae23', font=('Segoe UI Semibold', 10), width=20, height=2)
        self.btn_discover_all_bounds_header.pack(side='right', padx=(0, 8))
        self.main_tabs = ttk.Notebook(self.root)
        self.main_tabs.pack(fill='both', expand=True, padx=14, pady=(0, 10))
        self.bounds_warning_var = tk.StringVar(value='')
        self.bounds_warning_frame = tk.Frame(self.root, bg='#fff0b3', highlightthickness=2, highlightbackground='#e0a100')
        tk.Label(self.bounds_warning_frame, text='⚠', bg='#fff0b3', fg='#8a4b00', font=('Segoe UI Semibold', 20)).pack(side='left', padx=(12, 8), pady=8)
        tk.Label(self.bounds_warning_frame, textvariable=self.bounds_warning_var, bg='#fff0b3', fg='#5d3500', font=('Segoe UI Semibold', 10), justify='left', anchor='w', wraplength=1020).pack(side='left', fill='x', expand=True, padx=(0, 12), pady=8)

        def _refresh_bounds_warning() -> None:
            pending = _pending_bounds_labels(self.tabs)
            if not pending:
                try:
                    self.bounds_warning_frame.pack_forget()
                except tk.TclError:
                    pass
                return
            car = self.current_car or "current car"
            names = ', '.join(pending[:3])
            if len(pending) > 3:
                names += f' and {len(pending) - 3}'
            self.bounds_warning_var.set(f'PENDING LIMITS — {car}: “Detect Limits” has not been run yet for {len(pending)} control(s) with + and − keys assigned ({names}). Use the yellow “Detect Limits” button at the top.')
            try:
                if not self.bounds_warning_frame.winfo_manager():
                    self.bounds_warning_frame.pack(fill='x', padx=14, pady=(0, 8), before=self.main_tabs)
            except tk.TclError:
                pass
        self._refresh_bounds_warning = _refresh_bounds_warning
        main_tab = ttk.Frame(self.main_tabs)
        macros_tab = ttk.Frame(self.main_tabs)
        surface_dc_tab = ttk.Frame(self.main_tabs)
        hud_tab = ttk.Frame(self.main_tabs)
        options_tab = ttk.Frame(self.main_tabs)
        diagnostics_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(main_tab, text="Main")
        self.main_tabs.add(macros_tab, text='Macros')
        self.main_tabs.add(surface_dc_tab, text="Automatic")
        self.main_tabs.add(hud_tab, text='HUD')
        self.main_tabs.add(options_tab, text="Options")
        self.main_tabs.add(diagnostics_tab, text="Diagnostics")
        self.macros_tab = macros_tab
        self.macros_notebook = ttk.Notebook(macros_tab)
        self.macros_notebook.pack(fill='both', expand=True, padx=6, pady=6)
        controls_tab = ttk.Frame(self.macros_notebook)
        second_throttle_tab = ttk.Frame(self.macros_notebook)
        combos_tab = ttk.Frame(self.macros_notebook)
        ghost_keys_tab = ttk.Frame(self.macros_notebook)
        self.macros_notebook.add(controls_tab, text="Car Controls")
        self.macros_notebook.add(second_throttle_tab, text="Second Throttle")
        self.macros_notebook.add(combos_tab, text='Combos')
        self.macros_notebook.add(ghost_keys_tab, text="Auxiliary Keys")
        self.ghost_keys_tab = ghost_keys_tab
        self.controls_tab = controls_tab
        self.combos_tab = combos_tab
        self.second_throttle_tab = second_throttle_tab
        self.surface_dc_tab = surface_dc_tab
        self.hud_tab = hud_tab
        self.options_tab = options_tab
        self.diagnostics_tab = diagnostics_tab
        self.main_tabs.bind('<<NotebookTabChanged>>', lambda _event: self._refresh_diagnostics_tab())
        setup_container = tk.Frame(main_tab)
        setup_container.pack(fill='both', expand=True, padx=5, pady=(5, 2))
        setup_container.columnconfigure(0, weight=1)
        setup_container.columnconfigure(1, weight=0)
        setup_container.rowconfigure(0, weight=1)
        steps_column = tk.Frame(setup_container)
        steps_column.grid(row=0, column=0, columnspan=2, sticky='nsew')
        steps_column.columnconfigure(0, weight=1)
        options_column = tk.Frame(setup_container)
        options_column.grid(row=0, column=1, sticky='nsew')
        options_column.columnconfigure(0, weight=1)
        options_column.grid_remove()
        self.presets_frame = tk.LabelFrame(steps_column, text="Step 1: Pick car and track")
        self.presets_frame.pack(fill='x', pady=(0, 8))
        selector_frame = tk.Frame(self.presets_frame)
        selector_frame.pack(fill='x', padx=5, pady=2)
        selector_frame.columnconfigure(1, weight=2)
        selector_frame.columnconfigure(3, weight=2)
        tk.Label(selector_frame, text="Car:").grid(row=0, column=0, sticky='w')
        self.combo_car = ttk.Combobox(selector_frame, width=30)
        self.combo_car.grid(row=0, column=1, sticky='ew', padx=5)
        self.combo_car.bind('<<ComboboxSelected>>', self.on_car_selected)
        tk.Label(selector_frame, text="Track:").grid(row=0, column=2, sticky='w')
        self.combo_track = ttk.Combobox(selector_frame, width=30)
        self.combo_track.grid(row=0, column=3, sticky='ew', padx=5)
        self.combo_track.bind('<<ComboboxSelected>>', self.on_track_selected)
        tk.Label(selector_frame, text="Condition:").grid(row=0, column=4, sticky='w')
        self.combo_surface = ttk.Combobox(selector_frame, width=8, values=list(SURFACE_PRESET_KEYS), state='readonly')
        self.combo_surface.grid(row=0, column=5, sticky='ew', padx=(5, 0))
        self.combo_surface.set(DEFAULT_SURFACE_PRESET)
        self.combo_surface.bind('<<ComboboxSelected>>', self.on_surface_selected)
        actions_frame = tk.Frame(self.presets_frame)
        actions_frame.pack(fill='x', padx=5, pady=5)
        self.btn_load_preset = ttk.Button(actions_frame, text="Load", command=self.action_load_preset, style='PresetLoad.TButton')
        self.btn_load_preset.pack(side='left', expand=True, fill='x', padx=2)
        self.btn_save_preset = tk.Button(actions_frame, text="Save Current", command=self.action_save_preset, bg='#ADD8E6')
        self.btn_save_preset.pack(side='left', expand=True, fill='x', padx=2)
        self.btn_clear_preset = tk.Button(actions_frame, text="Clear", command=self.action_clear_preset, bg='#ffcccc')
        self.btn_clear_preset.pack(side='left', expand=True, fill='x', padx=2)
        preset_io_frame = tk.Frame(self.presets_frame)
        preset_io_frame.pack(fill='x', padx=5, pady=(0, 5))
        tk.Button(preset_io_frame, text="Import", command=self.action_import_preset, bg='#f0f0f0').pack(side='left', expand=True, fill='x', padx=2)
        tk.Button(preset_io_frame, text="Export", command=self.action_export_preset, bg='#f0f0f0').pack(side='left', expand=True, fill='x', padx=2)
        self.devices_frame = tk.LabelFrame(steps_column, text="Step 2: Confirm the input devices (joystick/wheel)")
        self.devices_frame.pack(fill='x', pady=(0, 8))
        self.check_safe = tk.Checkbutton(self.devices_frame, text="Keyboard only mode (requires restart)", variable=self.use_keyboard_only, command=self.trigger_safe_mode_update)
        self.check_safe.pack(anchor='w', padx=8, pady=(6, 2))
        tk.Button(self.devices_frame, text="🎮 Manage devices", command=self.open_device_manager, bg='#e0e0e0').pack(fill='x', padx=5, pady=5)
        self._update_preset_lock_state()
        self.scan_frame = tk.LabelFrame(steps_column, text="Step 3: Scan the driver controls")
        self.scan_frame.pack(fill='x')
        self.btn_scan = tk.Button(self.scan_frame, text="Scan the selected car's controls", command=self.scan_driver_controls, bg='lightblue')
        self.btn_scan.pack(fill='x', padx=5, pady=5)
        tk.Label(self.scan_frame, text="Tip: scan after changing devices or presets to keep the hotkeys in sync.", fg='gray', font=('Arial', 9)).pack(fill='x', padx=8, pady=(0, 6))
        self.btn_lapdist_overlay_main = None
        self.turbo_pit_frame = tk.LabelFrame(steps_column, text='Assistente Turbo Pit')
        self.turbo_pit_frame.pack(fill='x', pady=(8, 0))
        self.turbo_pit_status_card = tk.Frame(self.turbo_pit_frame, bg='#dff5e4', bd=1, relief='solid')
        self.turbo_pit_status_card.pack(fill='x', padx=8, pady=(8, 6))
        self.turbo_pit_status_card.columnconfigure(0, weight=1)
        self.turbo_pit_status_card.columnconfigure(1, weight=0)
        tk.Label(self.turbo_pit_status_card, text='TURBO PIT', bg='#dff5e4', fg='#3d4a45', font=('Segoe UI Semibold', 10)).grid(row=0, column=0, sticky='w', padx=12, pady=(10, 0))
        self.lbl_turbo_pit_status = tk.Label(self.turbo_pit_status_card, textvariable=self.turbo_pit_status_var, bg='#dff5e4', fg='#126b35', font=('Segoe UI Semibold', 24))
        self.lbl_turbo_pit_status.grid(row=1, column=0, sticky='w', padx=12, pady=(0, 0))
        self.lbl_turbo_pit_hint = tk.Label(self.turbo_pit_status_card, textvariable=self.turbo_pit_hint_var, bg='#dff5e4', fg='#3d4a45', font=('Segoe UI', 10), wraplength=390, justify='left')
        self.lbl_turbo_pit_hint.grid(row=2, column=0, sticky='w', padx=12, pady=(0, 10))
        self.btn_turbo_pit_toggle = tk.Button(self.turbo_pit_status_card, textvariable=self.turbo_pit_action_var, command=self._toggle_turbo_pit_from_card, bg='#1f8f45', fg='white', activebackground='#1f8f45', activeforeground='white', font=('Segoe UI Semibold', 11), width=18, height=2)
        self.btn_turbo_pit_toggle.grid(row=0, column=1, rowspan=3, sticky='e', padx=12, pady=10)
        tk.Label(self.turbo_pit_frame, text="Sends pit commands to clear tires and windshield when you enter the pit lane.", fg='gray', font=('Arial', 9), wraplength=520, justify='left').pack(anchor='w', padx=8, pady=(6, 2))
        self.lbl_turbo_pit_series = tk.Label(self.turbo_pit_frame, textvariable=self.turbo_pit_series_var, fg='#333333', font=('Segoe UI Semibold', 11), wraplength=620, justify='left')
        self.lbl_turbo_pit_series.pack(anchor='w', padx=8, pady=(4, 6))
        turbo_hotkey_row = tk.Frame(self.turbo_pit_frame)
        turbo_hotkey_row.pack(fill='x', padx=8, pady=(0, 5))
        tk.Label(turbo_hotkey_row, text="Global on/off hotkey:", anchor='w').pack(side='left')
        self.btn_turbo_pit_bind = tk.Button(turbo_hotkey_row, text="Set hotkey", width=18, command=lambda: _capture_turbo_pit_hotkey(self))
        self.btn_turbo_pit_bind.pack(side='right')
        _refresh_turbo_pit_hotkey_button(self)
        self._refresh_turbo_pit_visual()
        self._sync_turbo_pit_state_for_series()
        controls_container = tk.Frame(controls_tab)
        controls_container.pack(fill='both', expand=True, padx=5, pady=5)
        controls_header = tk.Frame(controls_container)
        controls_header.pack(fill='x', pady=(0, 6))
        tk.Label(controls_header, text="Driver Controls", font=('Segoe UI Semibold', 12)).pack(side='left')
        tk.Label(controls_header, text="Every scanned iRacing control gets its own page.", fg='gray').pack(side='left', padx=10)
        self.notebook = ttk.Notebook(controls_container, style='Control.TNotebook')
        self.notebook.pack(fill='both', expand=True, padx=0, pady=0)
        self.notebook.bind('<Configure>', self._schedule_control_tab_label_refresh)
        self.second_throttle_panel = SecondThrottlePanel(second_throttle_tab, self)
        self.second_throttle_panel.pack(fill='both', expand=True)
        self.ghost_keys_panel = GhostKeysPanel(ghost_keys_tab, self)
        self.ghost_keys_panel.pack(fill='both', expand=True)
        self.combo_page_container = tk.Frame(combos_tab)
        self.combo_page_container.pack(fill='both', expand=True, padx=5, pady=5)
        hud_visibility = tk.LabelFrame(hud_tab, text="Visibility")
        hud_visibility.pack(fill='x', padx=10, pady=(10, 4))
        tk.Checkbutton(hud_visibility, text="Show the HUD on screen", variable=self.hud_visible_var, command=self.toggle_overlay, font=('Segoe UI Semibold', 10)).pack(anchor='w', padx=8, pady=7)
        self.overlay_page_container = tk.Frame(hud_tab)
        self.overlay_page_container.pack(fill='both', expand=True, padx=5, pady=(0, 5))
        self._build_surface_dc_tab(surface_dc_tab)
        diagnostics_container = tk.Frame(diagnostics_tab)
        diagnostics_container.pack(fill='both', expand=True, padx=10, pady=10)
        diagnostics_container.columnconfigure(0, weight=1)
        diagnostics_card = tk.LabelFrame(diagnostics_container, text="Live Diagnostics")
        diagnostics_card.grid(row=0, column=0, sticky='nsew')
        self.diagnostics_text = tk.Text(diagnostics_card, height=16, wrap='none', font=('Consolas', 10), relief='flat')
        self.diagnostics_text.pack(fill='both', expand=True, padx=8, pady=8)
        diagnostics_actions = tk.Frame(diagnostics_container)
        diagnostics_actions.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        tk.Button(diagnostics_actions, text="Refresh", command=self._refresh_diagnostics_tab).pack(side='left')
        tk.Button(diagnostics_actions, text="Save configuration", command=self.save_config).pack(side='left', padx=6)
        options_canvas = tk.Canvas(options_tab, highlightthickness=0)
        options_scrollbar = ttk.Scrollbar(options_tab, orient='vertical', command=options_canvas.yview)
        options_canvas.configure(yscrollcommand=options_scrollbar.set)
        options_scrollbar.pack(side='right', fill='y')
        options_canvas.pack(side='left', fill='both', expand=True)
        options_container = tk.Frame(options_canvas)
        options_window = options_canvas.create_window((0, 0), window=options_container, anchor='nw')

        def _on_options_container_configure(_event):
            options_canvas.configure(scrollregion=options_canvas.bbox('all'))

        def _on_options_canvas_configure(event):
            options_canvas.itemconfigure(options_window, width=event.width)
        options_container.bind('<Configure>', _on_options_container_configure)
        options_canvas.bind('<Configure>', _on_options_canvas_configure)
        options_notebook = ttk.Notebook(options_container)
        options_notebook.pack(fill='both', expand=True, padx=5, pady=5)
        general_tab = ttk.Frame(options_notebook)
        options_notebook.add(general_tab, text="General Settings")
        privacy_tab = ttk.Frame(options_notebook)
        options_notebook.add(privacy_tab, text='Extras')
        general_container = tk.Frame(general_tab)
        general_container.pack(fill='both', expand=True, padx=5, pady=5)
        general_container.columnconfigure(0, weight=1)
        general_left = tk.LabelFrame(general_container, text="Presets and Devices")
        general_left.grid(row=0, column=0, sticky='nsew', pady=(0, 8))
        tk.Checkbutton(general_left, text="Save preset edits automatically (hotkeys/macros)", variable=self.auto_save_presets, command=self.schedule_save).pack(anchor='w', padx=8, pady=(8, 2))
        tk.Checkbutton(general_left, text="Lock car/track selection (managed automatically)", variable=self.lock_preset_selection, command=self._on_lock_preset_selection_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(general_left, text="Detect car/track automatically through iRacing", variable=self.auto_detect, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(general_left, text="Scan automatically when the car/track changes", variable=self.auto_scan_on_change, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(general_left, text="Show the getting started window on start", variable=self.show_getting_started, command=self.schedule_save).pack(anchor='w', padx=8, pady=(2, 8))
        self.stability_frame = tk.LabelFrame(general_container, text="Automation & Shortcuts")
        self.stability_frame.grid(row=1, column=0, sticky='nsew')
        tk.Button(self.stability_frame, text="Timing Settings", command=self.open_timing_window).pack(fill='x', padx=8, pady=(8, 6))
        if VOICE_FEATURES_ENABLED:
            tk.Button(self.stability_frame, text="Voice/Audio Options", command=self.open_voice_audio_settings).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(self.stability_frame, text="AUTOMATIC SCAN AND RESTART", font=('Segoe UI Semibold', 8), fg='#6b7280').pack(anchor='w', padx=8, pady=(4, 2))
        tk.Checkbutton(self.stability_frame, text="Refresh hotkeys automatically when controls.cfg changes", variable=self.auto_sync_iracing_controls, command=self._on_auto_sync_iracing_controls_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Restart before scanning again (after the first scan)", variable=self.auto_restart_on_rescan, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Restart and scan automatically when a race session starts", variable=self.auto_restart_on_race, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Restart and scan again when you go on track", variable=self.auto_restart_on_track_ready, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Block macros during replay", variable=self.block_off_track_commands, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Show a notice when the scan finishes", variable=self.show_scan_popup, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Keep scanning until driver controls are detected", variable=self.keep_scanning_until_valid, command=self._on_keep_scanning_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Label(self.stability_frame, text="APP BEHAVIOR", font=('Segoe UI Semibold', 8), fg='#6b7280').pack(anchor='w', padx=8, pady=(8, 2))
        tk.Checkbutton(self.stability_frame, text="Start with Windows", variable=self.start_with_windows, command=self._on_startup_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Focus the app window on start/restart", variable=self.focus_on_start, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Keep trying to reach the requested values (no time limit)", variable=self.keep_trying_targets, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text="Turn off every pop-up warning", variable=self.disable_popups, command=self._on_disable_popups_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Label(self.stability_frame, text="GLOBAL HOTKEYS", font=('Segoe UI Semibold', 8), fg='#6b7280').pack(anchor='w', padx=8, pady=(8, 0))
        bounds_discovery_frame = tk.Frame(self.stability_frame)
        bounds_discovery_frame.pack(fill='x', padx=8, pady=(6, 0))
        tk.Label(bounds_discovery_frame, text="Detect limits: button delay (s):").pack(side='left')
        self.entry_bounds_discovery_delay = tk.Entry(bounds_discovery_frame, width=6, textvariable=self.bounds_discovery_delay_s)
        self.entry_bounds_discovery_delay.pack(side='left', padx=(6, 12))
        self.entry_bounds_discovery_delay.bind('<KeyRelease>', lambda _event: self.schedule_save())
        self.entry_bounds_discovery_delay.bind('<FocusOut>', lambda _event: self.schedule_save())
        clear_frame = tk.Frame(self.stability_frame)
        clear_frame.pack(fill='x', padx=8, pady=6)
        tk.Label(clear_frame, text="Clear target hotkey (optional):").pack(side='left')
        self.btn_clear_target_bind = tk.Button(clear_frame, text="Set clear hotkey", width=18, command=self._set_clear_target_bind)
        self.btn_clear_target_bind.pack(side='left', padx=6)
        rescan_frame = tk.Frame(self.stability_frame)
        rescan_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(rescan_frame, text="Manual scan hotkey (restart, scan and load the profile):").pack(side='left')
        self.btn_manual_rescan_bind = tk.Button(rescan_frame, text="Set scan hotkey", width=18, command=self._set_manual_rescan_bind)
        self.btn_manual_rescan_bind.pack(side='left', padx=6)
        surface_frame = tk.Frame(self.stability_frame)
        surface_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(surface_frame, text="Dry/Wet hotkey (toggle the profile's condition):").pack(side='left')
        self.btn_surface_toggle_bind = tk.Button(surface_frame, text="Set Dry/Wet hotkey", width=18, command=self._set_surface_toggle_bind)
        self.btn_surface_toggle_bind.pack(side='left', padx=6)
        surface_dry_frame = tk.Frame(self.stability_frame)
        surface_dry_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(surface_dry_frame, text="Dry-only hotkey (switch to the dry profile):").pack(side='left')
        self.btn_surface_dry_bind = tk.Button(surface_dry_frame, text="Set Dry hotkey", width=18, command=self._set_surface_dry_bind)
        self.btn_surface_dry_bind.pack(side='left', padx=6)
        surface_wet_frame = tk.Frame(self.stability_frame)
        surface_wet_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(surface_wet_frame, text="Wet-only hotkey (switch to the wet profile):").pack(side='left')
        self.btn_surface_wet_bind = tk.Button(surface_wet_frame, text="Set Wet hotkey", width=18, command=self._set_surface_wet_bind)
        self.btn_surface_wet_bind.pack(side='left', padx=6)
        wiper_debug_check = tk.Checkbutton(self.stability_frame, text="Log wiper precipitation diagnostics to the terminal", variable=self.wiper_debug_enabled, command=self.schedule_save)
        wiper_debug_check.pack(anchor='w', padx=8, pady=(0, 6))
        wiper_debug_frame = tk.Frame(self.stability_frame)
        wiper_debug_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(wiper_debug_frame, text="Wiper diagnostics hotkey (log and apply the threshold):").pack(side='left')
        self.btn_wiper_debug_bind = tk.Button(wiper_debug_frame, text="Set wiper hotkey", width=22, command=self._set_wiper_debug_bind)
        self.btn_wiper_debug_bind.pack(side='left', padx=6)
        privacy_container = tk.Frame(privacy_tab)
        privacy_container.pack(fill='both', expand=True, padx=10, pady=10)
        privacy_container.columnconfigure(0, weight=1)
        self.btn_discreet_mode = tk.Button(privacy_container, text="Theme: Default", command=self.toggle_discreet_mode, bg='#f0f0f0', height=2)
        self.btn_discreet_mode.pack(fill='x')
        if not self.active_vars:
            self.active_vars = [('dcBrakeBias', True, False)]
        initial_tab_configs = None
        initial_combo_config = None
        initial_car = self.current_car.strip()
        initial_track = self.current_track.strip()
        if initial_car and initial_track and (initial_car in self.saved_presets) and (initial_track in self.saved_presets[initial_car]):
            entry = self._ensure_track_surface_presets(initial_car, initial_track)
            initial_surface = self._normalize_surface_label(self.current_surface or entry.get('active_surface'))
            preset_data = entry['surface_presets'].get(initial_surface, {})
            preset_vars = preset_data.get('active_vars')
            if preset_vars:
                self.active_vars = [_normalize_var_tuple(item) for item in preset_vars]
            initial_tab_configs = preset_data.get('tabs', {})
            initial_combo_config = preset_data.get('combo', {})
            self._last_loaded_preset_signature = (initial_car, initial_track, initial_surface)
            self._last_loaded_preset_time = time.time()
        self.rebuild_tabs(self.active_vars, tab_configs=initial_tab_configs, combo_config=initial_combo_config)
        if initial_tab_configs:
            self._apply_car_key_config(initial_car)
            self.register_current_listeners()
        self.update_preset_ui()
        self._refresh_clear_target_bind_button()
        self._refresh_manual_rescan_bind_button()
        self._refresh_surface_toggle_bind_button()
        self._refresh_surface_dry_bind_button()
        self._refresh_surface_wet_bind_button()
        self._refresh_wiper_debug_bind_button()
        self._refresh_bounds_discovery_bind_button()
        self._update_header_context()
        self._refresh_diagnostics_tab()
        self._apply_discreet_mode()

    def _build_surface_dc_tab(self, parent: tk.Widget) -> None:
        """Build the independent dry/wet driver-control profile page."""
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        content = tk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor='nw')
        content.columnconfigure(0, weight=1)
        content.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content_window, width=event.width))
        header = tk.Frame(content)
        header.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 8))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Auto Dry/Wet", font=('Segoe UI Semibold', 15), anchor='w').grid(row=0, column=0, sticky='w')
        tk.Label(header, text='Records values of a few car controls (brake bias, TC and so on) for dry and for wet, and applies the right set on its own. This is not the "Condition" on the Main tab, which swaps the car\'s whole preset.', fg='#4f5b66', justify='left', anchor='w', wraplength=760).grid(row=1, column=0, sticky='w', pady=(2, 0))
        overview = tk.LabelFrame(content, text="How it works")
        overview.grid(row=1, column=0, sticky='ew', padx=10, pady=(0, 8))
        overview.columnconfigure(0, weight=1)
        overview.columnconfigure(1, weight=1)
        enabled_check = tk.Checkbutton(overview, text="Enable Auto Dry/Wet", variable=self.surface_dc_profiles_enabled, command=self._on_surface_dc_setting_changed)
        enabled_check.grid(row=0, column=0, sticky='w', padx=10, pady=(8, 2))
        auto_check = tk.Checkbutton(overview, text="Follow the declared condition automatically", variable=self.surface_dc_auto_declared_wet, command=self._on_surface_dc_setting_changed)
        auto_check.grid(row=1, column=0, sticky='w', padx=10, pady=2)
        lapdist_check = tk.Checkbutton(overview, text="Keep LapDist macros", variable=self.use_lapdist_macros, command=self._on_lapdist_setting_change)
        self._surface_dc_edit_widgets.extend([enabled_check, auto_check, lapdist_check])
        status_card = tk.Frame(overview, bg='#eef4fb', bd=1, relief='solid')
        status_card.grid(row=0, column=1, rowspan=3, sticky='nsew', padx=10, pady=8)
        tk.Label(status_card, textvariable=self.surface_dc_declared_status_var, bg='#eef4fb', fg='#22313f', font=('Segoe UI Semibold', 10)).pack(anchor='w', padx=10, pady=(8, 2))
        tk.Label(status_card, textvariable=self.surface_dc_active_status_var, bg='#eef4fb', fg='#22313f', wraplength=480, justify='left').pack(anchor='w', padx=10, pady=(0, 8))
        actions = tk.LabelFrame(content, text="Hotkeys and manual application")
        actions.grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 8))
        actions.columnconfigure(0, weight=1, uniform='surface-profile')
        actions.columnconfigure(1, weight=1, uniform='surface-profile')
        dry_card = tk.Frame(actions, bg='#fff7e6', bd=1, relief='solid')
        dry_card.grid(row=0, column=0, sticky='nsew', padx=(8, 4), pady=8)
        dry_card.columnconfigure(0, weight=1)
        tk.Label(dry_card, text="DRY", bg='#f4d28c', fg='#5d3b00', font=('Segoe UI Semibold', 10)).grid(row=0, column=0, sticky='ew')
        self.btn_surface_dc_capture_dry = tk.Button(dry_card, text="1. Record current values", bg='#fff0c7', command=lambda: self._capture_surface_dc_profile('DRY'))
        self.btn_surface_dc_capture_dry.grid(row=1, column=0, sticky='ew', padx=8, pady=(8, 4))
        self.btn_surface_dc_apply_dry = tk.Button(dry_card, text="2. Apply to the car now", bg='#f4d28c', command=lambda: self.apply_surface_dc_profile('DRY', source="manual button"))
        self.btn_surface_dc_apply_dry.grid(row=2, column=0, sticky='ew', padx=8, pady=4)
        self.btn_surface_dc_dry_bind = tk.Button(dry_card, text="3. Set hotkey", command=self._set_surface_dc_dry_bind)
        self.btn_surface_dc_dry_bind.grid(row=3, column=0, sticky='ew', padx=8, pady=(4, 8))
        wet_card = tk.Frame(actions, bg='#eef7fd', bd=1, relief='solid')
        wet_card.grid(row=0, column=1, sticky='nsew', padx=(4, 8), pady=8)
        wet_card.columnconfigure(0, weight=1)
        tk.Label(wet_card, text="WET", bg='#b9ddf5', fg='#124b70', font=('Segoe UI Semibold', 10)).grid(row=0, column=0, sticky='ew')
        self.btn_surface_dc_capture_wet = tk.Button(wet_card, text="1. Record current values", bg='#d9eefb', command=lambda: self._capture_surface_dc_profile('WET'))
        self.btn_surface_dc_capture_wet.grid(row=1, column=0, sticky='ew', padx=8, pady=(8, 4))
        self.btn_surface_dc_apply_wet = tk.Button(wet_card, text="2. Apply to the car now", bg='#b9ddf5', command=lambda: self.apply_surface_dc_profile('WET', source="manual button"))
        self.btn_surface_dc_apply_wet.grid(row=2, column=0, sticky='ew', padx=8, pady=4)
        self.btn_surface_dc_wet_bind = tk.Button(wet_card, text="3. Set hotkey", command=self._set_surface_dc_wet_bind)
        self.btn_surface_dc_wet_bind.grid(row=3, column=0, sticky='ew', padx=8, pady=(4, 8))
        self._surface_dc_edit_widgets.extend([self.btn_surface_dc_dry_bind, self.btn_surface_dc_wet_bind, self.btn_surface_dc_capture_dry, self.btn_surface_dc_capture_wet])
        endurance = tk.LabelFrame(content, text="When you get in the car")
        endurance.grid(row=3, column=0, sticky='ew', padx=10, pady=(0, 8))
        endurance.columnconfigure(3, weight=1)
        endurance_check = tk.Checkbutton(endurance, text="Check and apply the profile automatically", variable=self.surface_dc_endurance_enabled, command=self._on_surface_dc_setting_changed)
        endurance_check.grid(row=0, column=0, sticky='w', padx=10, pady=8)
        tk.Label(endurance, text="Profile:").grid(row=0, column=1, sticky='e', padx=(8, 4), pady=8)
        self.surface_dc_endurance_combo = ttk.Combobox(endurance, textvariable=self.surface_dc_endurance_mode, values=list(SURFACE_DC_ENDURANCE_MODES), state='readonly', width=12)
        self.surface_dc_endurance_combo.grid(row=0, column=2, sticky='w', padx=4, pady=8)
        self.surface_dc_endurance_combo.bind('<<ComboboxSelected>>', lambda _event: self._on_surface_dc_setting_changed())
        tk.Label(endurance, text="AUTO follows the declared condition.", fg='#4f5b66', justify='left', anchor='w').grid(row=0, column=3, sticky='w', padx=(10, 8), pady=8)
        tk.Label(endurance, textvariable=self.surface_dc_endurance_status_var, fg='#1c5d8c', font=('Segoe UI Semibold', 10), justify='left').grid(row=1, column=0, columnspan=4, sticky='w', padx=10, pady=(0, 8))
        self._surface_dc_edit_widgets.extend([endurance_check, self.surface_dc_endurance_combo])
        values_card = tk.LabelFrame(content, text="Car values")
        values_card.grid(row=4, column=0, sticky='nsew', padx=10, pady=(0, 10))
        values_card.columnconfigure(0, weight=1)
        values_card.columnconfigure(1, weight=0)
        tk.Label(values_card, text="Value filled in = use it  •  Empty field = ignore it", fg='#4f5b66', justify='left', anchor='w').grid(row=0, column=0, sticky='w', padx=10, pady=(8, 4))
        tk.Label(values_card, textvariable=self.surface_dc_values_summary_var, fg='#36556b', font=('Segoe UI Semibold', 9), anchor='e').grid(row=0, column=1, sticky='e', padx=10, pady=(8, 4))
        self.surface_dc_rows_frame = tk.Frame(values_card)
        self.surface_dc_rows_frame.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=8, pady=(0, 8))
        self.surface_dc_rows_frame.columnconfigure(0, weight=1)
        self._refresh_surface_dc_bind_buttons()
        self._refresh_surface_dc_profile_rows()
        self._update_surface_dc_editing_state()

    def _surface_dc_current_car(self) -> str:
        """Return the car key used by independent surface profiles."""
        car = str(self.current_car or '').strip()
        combo = getattr(self, 'combo_car', None)
        if not car and combo is not None:
            try:
                car = str(combo.get() or '').strip()
            except Exception:
                car = ''
        return car

    def _ensure_surface_dc_car_profile(self, car: str) -> Dict[str, Any]:
        """Return a normalized independent profile payload for one car."""
        if not car:
            return {'controls': {}}
        payload = self.surface_dc_profiles.get(car)
        if not isinstance(payload, dict):
            payload = {}
        controls = payload.get('controls')
        if not isinstance(controls, dict):
            controls = {}
        payload['controls'] = controls
        self.surface_dc_profiles[car] = payload
        return payload

    def _surface_dc_eligible_vars(self) -> List[Tuple[str, bool, bool]]:
        """Return scanned persistent value controls in display order."""
        eligible = []
        for item in self.active_vars:
            var_name, is_float, is_boolean = _normalize_var_tuple(item)
            if _is_surface_dc_profile_control(var_name, is_boolean):
                eligible.append((var_name, is_float, is_boolean))
        return eligible

    def _refresh_surface_dc_profile_rows(self) -> None:
        """Rebuild dry/wet value editors after a car or scan change."""
        container = self.surface_dc_rows_frame
        if container is None:
            return
        self._collect_surface_dc_profile_rows()
        for child in container.winfo_children():
            child.destroy()
        self.surface_dc_rows.clear()
        self._surface_dc_edit_widgets = [widget for widget in self._surface_dc_edit_widgets if widget.winfo_exists() and widget.master is not container]
        car = self._surface_dc_current_car()
        self._surface_dc_rows_car = car
        if not car:
            self.surface_dc_values_summary_var.set("DRY: 0 values  •  WET: 0 values")
            tk.Label(container, text="Get in a car and scan the controls to set the values up.", fg='#7a4d00').grid(row=0, column=0, columnspan=4, sticky='w', padx=4, pady=8)
            return
        eligible = self._surface_dc_eligible_vars()
        if not eligible:
            self.surface_dc_values_summary_var.set("DRY: 0 values  •  WET: 0 values")
            tk.Label(container, text=f"No numeric DC control available for {car}. Scan the car's controls.", fg='#7a4d00').grid(row=0, column=0, columnspan=4, sticky='w', padx=4, pady=8)
            return
        headers = ("Control", "Current", "DRY", "WET")
        widths = (36, 12, 16, 16)
        for column, (label, width) in enumerate(zip(headers, widths)):
            header_bg = '#f4d28c' if label == "DRY" else '#b9ddf5' if label == "WET" else '#e9edf2'
            tk.Label(container, text=label, width=width, anchor='w', font=('Segoe UI Semibold', 9), bg=header_bg, relief='solid', bd=1).grid(row=0, column=column, sticky='ew', padx=1, pady=(0, 2))
        controls_cfg = self._ensure_surface_dc_car_profile(car)['controls']
        for row_idx, (var_name, _is_float, _is_boolean) in enumerate(eligible, start=1):
            raw_cfg = controls_cfg.get(var_name)
            if not isinstance(raw_cfg, dict):
                raw_cfg = {}
            dry_var = tk.StringVar(value=str(raw_cfg.get('dry', '') or ''))
            wet_var = tk.StringVar(value=str(raw_cfg.get('wet', '') or ''))
            current_var = tk.StringVar(value='--')
            tk.Label(container, text=format_driver_control_name(var_name), anchor='w').grid(row=row_idx, column=0, sticky='ew', padx=6, pady=2)
            tk.Label(container, textvariable=current_var, anchor='w', fg='#495864').grid(row=row_idx, column=1, sticky='ew', padx=6, pady=2)
            dry_entry = tk.Entry(container, textvariable=dry_var, width=14, disabledbackground='#fff7e6')
            dry_entry.grid(row=row_idx, column=2, sticky='ew', padx=4, pady=2)
            wet_entry = tk.Entry(container, textvariable=wet_var, width=14, disabledbackground='#eef7fd')
            wet_entry.grid(row=row_idx, column=3, sticky='ew', padx=4, pady=2)
            for entry in (dry_entry, wet_entry):
                entry.bind('<KeyRelease>', lambda _event: self._on_surface_dc_row_changed())
                entry.bind('<FocusOut>', lambda _event: self._on_surface_dc_row_changed())
            self.surface_dc_rows[var_name] = {'dry_var': dry_var, 'wet_var': wet_var, 'current_var': current_var, 'dry_entry': dry_entry, 'wet_entry': wet_entry, 'widgets': (dry_entry, wet_entry)}
            self._surface_dc_edit_widgets.extend([dry_entry, wet_entry])
        self._update_surface_dc_current_values()
        self._update_surface_dc_row_feedback()
        self._update_surface_dc_editing_state()

    @staticmethod
    def _surface_dc_value_is_valid(raw_value: Any) -> bool:
        """Return whether a non-empty profile value can be applied safely."""
        text = str(raw_value or '').strip()
        if not text:
            return False
        try:
            value = float(text.replace(',', '.'))
        except (TypeError, ValueError):
            return False
        return math.isfinite(value)

    def _update_surface_dc_row_feedback(self) -> None:
        """Show at a glance which profile fields are active or invalid."""
        counts = {'dry': 0, 'wet': 0}
        invalid = 0
        colors = {'dry': '#fff7e6', 'wet': '#eef7fd'}
        for row in self.surface_dc_rows.values():
            for profile in ('dry', 'wet'):
                raw_value = str(row[f'{profile}_var'].get() or '').strip()
                entry = row.get(f'{profile}_entry')
                if not raw_value:
                    background = '#ffffff'
                elif self._surface_dc_value_is_valid(raw_value):
                    counts[profile] += 1
                    background = colors[profile]
                else:
                    invalid += 1
                    background = '#ffd9d9'
                if entry is not None:
                    try:
                        entry.configure(bg=background, disabledbackground=background)
                    except tk.TclError:
                        pass
        summary = f'DRY: {counts['dry']} values  •  WET: {counts['wet']} values'
        if invalid:
            summary += f'  •  Invalid: {invalid}'
        self.surface_dc_values_summary_var.set(summary)

    def _collect_surface_dc_profile_rows(self) -> None:
        """Persist the visible row values into the per-car profile dictionary."""
        car = str(self._surface_dc_rows_car or '').strip()
        if not car or not self.surface_dc_rows:
            return
        controls_cfg = self._ensure_surface_dc_car_profile(car)['controls']
        for var_name, row in self.surface_dc_rows.items():
            dry_value = str(row['dry_var'].get()).strip()
            wet_value = str(row['wet_var'].get()).strip()
            if dry_value or wet_value:
                controls_cfg[var_name] = {'dry': dry_value, 'wet': wet_value}
            else:
                controls_cfg.pop(var_name, None)

    def _on_surface_dc_row_changed(self) -> None:
        self._update_surface_dc_row_feedback()
        self._collect_surface_dc_profile_rows()
        self.schedule_save()

    def _on_surface_dc_setting_changed(self) -> None:
        mode = str(self.surface_dc_endurance_mode.get() or 'AUTO').upper()
        if mode not in SURFACE_DC_ENDURANCE_MODES:
            self.surface_dc_endurance_mode.set('AUTO')
        self._surface_dc_state['last_declared_wet'] = None
        self._surface_dc_state['force_reapply'] = True
        self._collect_surface_dc_profile_rows()
        self.schedule_save()

    @staticmethod
    def _format_surface_dc_value(controller: GenericController, value: Any) -> str:
        try:
            number = float(value)
        except Exception:
            return '--'
        if not controller.is_float:
            return str(int(round(number)))
        return f'{number:.4f}'.rstrip('0').rstrip('.')

    def _update_surface_dc_current_values(self) -> None:
        """Refresh the live telemetry column without mutating saved profiles."""
        for var_name, row in self.surface_dc_rows.items():
            controller = self.controllers.get(var_name)
            if controller is None:
                row['current_var'].set('--')
                continue
            value = controller.read_telemetry(use_cache=True, cache_ttl_s=0.3)
            row['current_var'].set(self._format_surface_dc_value(controller, value) if value is not None else '--')

    def _capture_surface_dc_profile(self, surface: str) -> None:
        """Read current telemetry into every eligible value for one profile."""
        if self.app_state != 'CONFIG':
            self._show_info("Auto Dry/Wet", "Enter CONFIG mode first.")
            return
        surface_key = self._normalize_surface_label(surface)
        if not self.surface_dc_rows:
            self._show_warning("Auto Dry/Wet", "No control available. Scan the car first.")
            return
        capture_car = self._surface_dc_current_car()
        row_names = list(self.surface_dc_rows.keys())

        def _worker() -> None:
            captured: Dict[str, float] = {}
            for var_name in row_names:
                controller = self.controllers.get(var_name)
                if controller is None:
                    continue
                value = controller.read_telemetry(use_cache=False)
                if value is not None:
                    captured[var_name] = value
            self._ui_after(_finish, captured)

        def _finish(captured: Dict[str, float]) -> None:
            if capture_car != self._surface_dc_current_car():
                self.surface_dc_active_status_var.set("Capture discarded because the car changed while reading.")
                return
            field = 'dry_var' if surface_key == 'DRY' else 'wet_var'
            for var_name, value in captured.items():
                row = self.surface_dc_rows.get(var_name)
                controller = self.controllers.get(var_name)
                if row is None or controller is None:
                    continue
                row[field].set(self._format_surface_dc_value(controller, value))
            self._update_surface_dc_row_feedback()
            self._collect_surface_dc_profile_rows()
            self.schedule_save()
            color = '#8a5a00' if surface_key == 'DRY' else '#176a9c'
            label = "DRY" if surface_key == 'DRY' else "WET"
            self.surface_dc_active_status_var.set(f'Profile {label}: {len(captured)} values recorded')
            if captured:
                self.notify_overlay_status(f'Profile {label} recorded ({len(captured)})', color)
        threading.Thread(target=_worker, name=f'SurfaceDCCapture-{surface_key}', daemon=True).start()

    def _update_surface_dc_editing_state(self) -> None:
        """Keep profile editing in CONFIG while apply buttons stay in RUNNING."""
        editing = self.app_state == 'CONFIG'
        for widget in list(self._surface_dc_edit_widgets):
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state='readonly' if editing else 'disabled')
                else:
                    widget.configure(state=tk.NORMAL if editing else tk.DISABLED)
            except (tk.TclError, AttributeError):
                continue
        apply_state = tk.NORMAL if not editing else tk.DISABLED
        for button in (self.btn_surface_dc_apply_dry, self.btn_surface_dc_apply_wet):
            if button:
                button.configure(state=apply_state)

    def _update_header_context(self) -> None:
        """Refresh the compact car/track context in the app header."""
        if not hasattr(self, 'header_context_var'):
            return
        car = self.current_car or "No car"
        track = self.current_track or "No track"
        self.header_context_var.set(f'{car}  /  {track}')

    def _refresh_diagnostics_tab(self) -> None:
        """Refresh the diagnostics page when it is visible or requested."""
        text_widget = getattr(self, 'diagnostics_text', None)
        if text_widget is None:
            return
        input_thread = getattr(input_manager, '_input_thread', None)
        voice_thread = getattr(voice_listener, 'thread', None)
        try:
            ui_queue_size = self._uiq.qsize()
        except Exception:
            ui_queue_size = 0
        try:
            callback_pending = _CALLBACK_DISPATCHER.pending()
        except Exception:
            callback_pending = 0
        lines = [f'App state: {self.app_state}', f'SDK initialized: {getattr(self.ir, 'is_initialized', False)}', f'SDK connected: {getattr(self.ir, 'is_connected', None)}', f'Telemetry active: {getattr(self, '_telemetry_active', False)}', f'Scan in progress: {getattr(self, '_scan_in_progress', False)}', f'Controls: {len(self.controllers)}', f'Tk UI queue: {ui_queue_size}', f'Callback queue: {callback_pending}', f'Input thread alive: {bool(input_thread and input_thread.is_alive())}', f'Voice thread active: {bool(voice_thread and voice_thread.is_alive())}', f'Current car: {self.current_car or '-'}', f'Current track: {self.current_track or '-'}', f'Condition: {self.current_surface}', f'Auto Dry/Wet: {self.surface_dc_profiles_enabled.get()}', f'Auto Dry/Wet follows the declared condition: {self.surface_dc_auto_declared_wet.get()}', f'Auto Dry/Wet on car entry: {self.surface_dc_endurance_enabled.get()} ({self.surface_dc_endurance_mode.get()})', f'Active profile: {self._surface_dc_state.get('active_profile') or '-'}', f'Driver in the car with a personal profile: {self._surface_dc_state.get('endurance_active', False)}', f'Config: {CONFIG_FILE}']
        text_widget.configure(state='normal')
        text_widget.delete('1.0', tk.END)
        text_widget.insert('1.0', '\n'.join(lines))
        text_widget.configure(state='disabled')

    def open_getting_started_window(self) -> None:
        """Open the getting started guide in a popup window."""
        if not self._popups_enabled():
            return
        if self.getting_started_window and self.getting_started_window.winfo_exists():
            self.getting_started_window.lift()
            return
        self.getting_started_window = tk.Toplevel(self.root)
        self.getting_started_window.title('Primeiros Passos')
        self.getting_started_window.geometry('760x360')
        self.getting_started_window.transient(self.root)

        def _cleanup():
            if self.getting_started_window and self.getting_started_window.winfo_exists():
                self.getting_started_window.destroy()
            self.getting_started_window = None
        self.getting_started_window.protocol('WM_DELETE_WINDOW', _cleanup)
        container = tk.Frame(self.getting_started_window)
        container.pack(fill='both', expand=True, padx=12, pady=12)
        tk.Label(container, text=self.getting_started_text, wraplength=720, justify='left').pack(fill='x', pady=(0, 12))
        tk.Button(container, text="Close", command=_cleanup, bg='#e0e0e0').pack(anchor='e')

    def _maybe_show_getting_started(self) -> None:
        """Show the getting started popup once if enabled."""
        if not self.show_getting_started.get() or not self._popups_enabled():
            return
        self.open_getting_started_window()
        self.show_getting_started.set(False)
        self.schedule_save()

    def _list_microphones(self) -> List[Tuple[int, str]]:
        devices: List[Tuple[int, str]] = [(-1, "System default")]
        if not HAS_SPEECH:
            return devices
        try:
            mic_names = sr.Microphone.list_microphone_names() or []
            for idx, name in enumerate(mic_names):
                devices.append((idx, name))
        except Exception as exc:
            print(f'[Voice] Could not list the microphones: {exc}')
        return devices

    def _list_output_devices(self) -> List[Tuple[int, str]]:
        devices: List[Tuple[int, str]] = [(-1, "System default")]
        if not HAS_PYAUDIO:
            return devices
        try:
            pa = pyaudio.PyAudio()
            try:
                for idx in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(idx)
                    if info.get('maxOutputChannels', 0) > 0:
                        name = info.get('name', f'Output {idx}')
                        devices.append((idx, name))
            finally:
                pa.terminate()
        except Exception as exc:
            print(f'[Audio] Could not list the output devices: {exc}')
        return devices

    @staticmethod
    def _device_label(idx: int, name: str) -> str:
        return f'[{idx}] {name}'

    @staticmethod
    def _parse_device_index(label: str) -> int:
        try:
            start = label.find('[')
            end = label.find(']')
            return int(label[start + 1:end]) if start >= 0 and end > start else -1
        except Exception:
            return -1

    def _apply_audio_preferences(self):
        """Send selected devices to voice listener and TTS engine."""
        mic_index = self.microphone_device.get()
        voice_listener.set_device_index(mic_index if mic_index >= 0 else None)
        output_index = self.audio_output_device.get()
        foundation.TTS_OUTPUT_DEVICE_INDEX = output_index if output_index >= 0 else None

    def _refresh_audio_device_lists(self):
        mic_devices = self._list_microphones()
        if self.microphone_device.get() not in [i for i, _ in mic_devices]:
            self.microphone_device.set(-1)
        mic_labels = [self._device_label(idx, name) for idx, name in mic_devices]
        if self.mic_combo:
            self.mic_combo['values'] = mic_labels
            current_label = self._device_label(self.microphone_device.get() if self.microphone_device.get() in [i for i, _ in mic_devices] else -1, dict(mic_devices).get(self.microphone_device.get(), "System default"))
            self.mic_combo.set(current_label)
        output_devices = self._list_output_devices()
        if self.audio_output_device.get() not in [i for i, _ in output_devices]:
            self.audio_output_device.set(-1)
        output_labels = [self._device_label(idx, name) for idx, name in output_devices]
        if self.audio_output_combo:
            self.audio_output_combo['values'] = output_labels
            current_output_label = self._device_label(self.audio_output_device.get() if self.audio_output_device.get() in [i for i, _ in output_devices] else -1, dict(output_devices).get(self.audio_output_device.get(), "System default"))
            self.audio_output_combo.set(current_output_label)

    def _on_microphone_selected(self, *_):
        selection = self._parse_device_index(self.mic_combo.get()) if self.mic_combo else -1
        self.microphone_device.set(selection)
        self._apply_audio_preferences()
        self.schedule_save()

    def _on_output_selected(self, *_):
        selection = self._parse_device_index(self.audio_output_combo.get()) if self.audio_output_combo else -1
        self.audio_output_device.set(selection)
        self._apply_audio_preferences()
        self.schedule_save()

    def open_voice_audio_settings(self):
        """Open the options window focused on voice and audio settings."""
        if not VOICE_FEATURES_ENABLED:
            return
        if getattr(self, 'voice_window', None) is not None and self.voice_window.winfo_exists():
            self.voice_window.lift()
            return
        self.voice_window = tk.Toplevel(self.root)
        self.voice_window.title("Voice and Audio Options")
        self.voice_window.geometry('720x520')

        def _cleanup():
            self.close_voice_audio_window()
        self.voice_window.protocol('WM_DELETE_WINDOW', _cleanup)
        notebook = ttk.Notebook(self.voice_window)
        notebook.pack(fill='both', expand=True, padx=10, pady=(10, 4))
        voice_tab = ttk.Frame(notebook)
        notebook.add(voice_tab, text="Voice/Audio")
        self._build_voice_audio_tab(voice_tab)
        notebook.select(voice_tab)
        tk.Button(self.voice_window, text="💾 SAVE", command=self.save_and_close_voice_audio, bg='#90ee90', height=2).pack(fill='x', padx=10, pady=(4, 10))

    def save_and_close_voice_audio(self) -> None:
        """Save voice/audio preferences and close the options window."""
        self.schedule_save()
        self.close_voice_audio_window()

    def close_voice_audio_window(self) -> None:
        """Close and clean up the voice/audio options window."""
        if getattr(self, 'voice_window', None) is not None:
            if self.voice_window.winfo_exists():
                self.voice_window.destroy()
        self.voice_window = None
        self.voice_engine_combo = None
        self.btn_vosk_model = None
        self.mic_combo = None
        self.audio_output_combo = None

    def _build_voice_audio_tab(self, parent: tk.Widget):
        """Construct the tab containing voice and audio controls."""
        if not VOICE_FEATURES_ENABLED:
            return
        toggles_frame = tk.Frame(parent)
        toggles_frame.pack(fill='x', pady=4)
        if HAS_TTS:
            tk.Checkbutton(toggles_frame, text="Voice (TTS)", variable=self.use_tts, command=self.schedule_save).pack(side='left', padx=4)
        tk.Checkbutton(toggles_frame, text="Voice Triggers", variable=self.use_voice, state='normal' if HAS_SPEECH else 'disabled', command=self.on_voice_toggle).pack(side='left', padx=4)
        tk.Button(toggles_frame, text="Test Voice", command=self.open_voice_test_dialog, state='normal' if HAS_SPEECH else 'disabled').pack(side='left', padx=4)
        if not HAS_SPEECH:
            tk.Label(toggles_frame, text="(Install 'speech_recognition' for voice)", fg='gray', font=('Arial', 8)).pack(side='left', padx=4)
        engine_frame = tk.LabelFrame(parent, text="Recognition Engine")
        engine_frame.pack(fill='x', padx=2, pady=6)
        engine_row = tk.Frame(engine_frame)
        engine_row.pack(fill='x', padx=6, pady=2)
        ttk.Label(engine_row, text="Voice Engine:").pack(side='left', padx=4)
        engine_options = ['speech'] + (['vosk'] if HAS_VOSK else []) + ['whisper.cpp']
        self.voice_engine_combo = ttk.Combobox(engine_row, values=engine_options, state='readonly', width=12)
        default_engine = self.voice_engine.get()
        if default_engine not in engine_options:
            default_engine = 'speech'
            self.voice_engine.set(default_engine)
        self.voice_engine_combo.set(default_engine)
        self.voice_engine_combo.bind('<<ComboboxSelected>>', lambda _evt: self.on_voice_engine_changed())
        self.voice_engine_combo.pack(side='left', padx=4)
        model_row = tk.Frame(engine_frame)
        model_row.pack(fill='x', padx=6, pady=2)
        self.btn_vosk_model = tk.Button(model_row, text="Select Vosk Model...", command=self.choose_vosk_model)
        self.btn_vosk_model.pack(side='left', padx=4)
        self.btn_whisper_binary = tk.Button(model_row, text="Select whisper.cpp...", command=self.choose_whisper_binary)
        self.btn_whisper_binary.pack(side='left', padx=4)
        self.btn_whisper_model = tk.Button(model_row, text="Select Whisper Model...", command=self.choose_whisper_model)
        self.btn_whisper_model.pack(side='left', padx=4)
        status_row = tk.Frame(engine_frame)
        status_row.pack(fill='x', padx=6, pady=2)
        tk.Label(status_row, textvariable=self.vosk_status_var, fg='gray', anchor='w', justify='left', wraplength=280).pack(side='left', padx=6, fill='x', expand=True)
        tk.Label(status_row, textvariable=self.whisper_status_var, fg='gray', anchor='w', justify='left', wraplength=280).pack(side='left', padx=6, fill='x', expand=True)
        device_frame = tk.LabelFrame(parent, text="Input/Output Devices")
        device_frame.pack(fill='x', padx=2, pady=6)
        mic_row = tk.Frame(device_frame)
        mic_row.pack(fill='x', padx=6, pady=2)
        ttk.Label(mic_row, text="Microphone:").pack(side='left')
        self.mic_combo = ttk.Combobox(mic_row, state='readonly', width=50)
        self.mic_combo.pack(side='left', padx=4, fill='x', expand=True)
        self.mic_combo.bind('<<ComboboxSelected>>', self._on_microphone_selected)
        out_row = tk.Frame(device_frame)
        out_row.pack(fill='x', padx=6, pady=2)
        ttk.Label(out_row, text="Audio Output (TTS):").pack(side='left')
        self.audio_output_combo = ttk.Combobox(out_row, state='readonly', width=50)
        self.audio_output_combo.pack(side='left', padx=4, fill='x', expand=True)
        self.audio_output_combo.bind('<<ComboboxSelected>>', self._on_output_selected)
        tk.Button(device_frame, text="Refresh devices", command=self._refresh_audio_device_lists).pack(anchor='e', padx=6, pady=4)
        tuning_frame = tk.LabelFrame(parent, text="Voice Tuning (accuracy and speed)")
        tuning_frame.pack(fill='x', padx=2, pady=(6, 4))
        tuning_row_1 = tk.Frame(tuning_frame)
        tuning_row_1.pack(fill='x', padx=6, pady=2)
        ttk.Label(tuning_row_1, text="Ambient noise (s):").pack(side='left')
        ttk.Spinbox(tuning_row_1, from_=0.0, to=3.0, increment=0.1, width=6, textvariable=self.voice_ambient_duration).pack(side='left', padx=4)
        ttk.Label(tuning_row_1, text="Max phrase length (s):").pack(side='left')
        ttk.Spinbox(tuning_row_1, from_=0.2, to=6.0, increment=0.1, width=6, textvariable=self.voice_phrase_time_limit).pack(side='left', padx=4)
        tk.Checkbutton(tuning_row_1, text="Dynamic energy (auto)", variable=self.voice_dynamic_energy).pack(side='left', padx=8)
        tuning_row_2 = tk.Frame(tuning_frame)
        tuning_row_2.pack(fill='x', padx=6, pady=2)
        ttk.Label(tuning_row_2, text="Initial timeout (s):").pack(side='left')
        ttk.Spinbox(tuning_row_2, from_=0.0, to=5.0, increment=0.1, width=6, textvariable=self.voice_initial_timeout).pack(side='left', padx=4)
        ttk.Label(tuning_row_2, text="Continuous timeout (s):").pack(side='left')
        ttk.Spinbox(tuning_row_2, from_=0.0, to=5.0, increment=0.1, width=6, textvariable=self.voice_continuous_timeout).pack(side='left', padx=4)
        ttk.Label(tuning_row_2, text="Minimum energy: ").pack(side='left')
        ttk.Entry(tuning_row_2, width=8, textvariable=self.voice_energy_threshold).pack(side='left', padx=4)
        tk.Label(tuning_row_2, text="(blank = automatic)", fg='gray', font=('Arial', 8)).pack(side='left', padx=2)
        if not self._voice_traces_attached:
            for var in (self.voice_ambient_duration, self.voice_phrase_time_limit, self.voice_initial_timeout, self.voice_continuous_timeout, self.voice_energy_threshold, self.voice_dynamic_energy):
                var.trace_add('write', self.on_voice_tuning_changed)
            self._voice_traces_attached = True
        self._refresh_audio_device_lists()
        self._update_voice_controls()

    def toggle_mode(self):
        """Toggle between RUNNING and CONFIG modes."""
        if self.app_state == 'RUNNING':
            self._cancel_surface_dc_precision_targets("CONFIG mode enabled", clear_desired=True)
            self.app_state = 'CONFIG'
            self._deactivate_second_throttle()
            self._stop_hybrid_hold()
            self.btn_mode.config(text="Mode: CONFIG (click to save and run)", bg='orange')
            input_manager.active = False
            self._clear_keyboard_hotkeys()
            voice_listener.set_enabled(False)
        else:
            if self.second_throttle_panel:
                self.second_throttle_panel.sync_to_app(schedule=True)
            if self.ghost_keys_panel:
                self.ghost_keys_panel.sync_to_app(schedule=True)
            self.app_state = 'RUNNING'
            self.btn_mode.config(text="Mode: RUNNING", bg='#90ee90')
            input_manager.active = True
            self.register_current_listeners()
        editing = self.app_state == 'CONFIG'
        for tab in self.tabs.values():
            tab.set_editing_state(editing)
        if self.combo_tab:
            self.combo_tab.set_editing_state(editing)
        if self.second_throttle_panel:
            self.second_throttle_panel.set_editing_state(editing)
        if self.ghost_keys_panel:
            self.ghost_keys_panel.set_editing_state(editing)
        self._update_surface_dc_editing_state()
        self._set_bounds_discovery_buttons_state(True)

    def focus_window(self):
        """Force focus to main window."""
        self.root.focus_force()

    def toggle_overlay(self):
        """Apply the visibility selected in the HUD tab."""
        desired = bool(self.hud_visible_var.get())
        if self.discreet_mode.get():
            try:
                if self.overlay.winfo_exists():
                    self.overlay.withdraw()
            except Exception:
                pass
            self.overlay_visible = False
            self.hud_visible_var.set(False)
            return
        if desired:
            self.overlay.deiconify()
            self.overlay_visible = True
        else:
            self.overlay.withdraw()
            self.overlay_visible = False
        self.schedule_save()

    def notify_overlay_status(self, text: str, color: str):
        """Update overlay status text temporarily."""
        self.ui(self.overlay.update_status_text, text, color)
        self.ui(self.root.after, 2000, lambda: self.overlay.update_status_text("HUD ready", 'white'))

    def _control_bounds_discovery_candidates(self, var_names: Optional[List[str]]=None) -> List[Tuple[str, ControlTab]]:
        """Return tabs eligible for numeric min/max discovery."""
        wanted = set(var_names or [])
        candidates: List[Tuple[str, ControlTab]] = []
        for var_name, tab in self.tabs.items():
            if wanted and var_name not in wanted:
                continue
            if not getattr(tab, 'bounds_supported', False):
                continue
            if not tab.controller.can_discover_bounds():
                continue
            candidates.append((var_name, tab))
        return candidates

    def _set_bounds_discovery_buttons_state(self, enabled: bool) -> None:
        """Enable or disable all bounds discovery buttons."""
        state = 'normal' if enabled and self.app_state == 'RUNNING' else 'disabled'
        if self.btn_discover_all_bounds_header:
            self.btn_discover_all_bounds_header.config(state=state)
        refresh_warning = getattr(self, '_refresh_bounds_warning', None)
        if callable(refresh_warning):
            refresh_warning()

    def _persist_current_preset_after_bounds_discovery(self) -> None:
        """Persist detected bounds even while the app is in RUNNING mode."""
        car = self.combo_car.get().strip() if getattr(self, 'combo_car', None) else ''
        track = self.combo_track.get().strip() if getattr(self, 'combo_track', None) else ''
        car = car or self.current_car
        track = track or self.current_track
        if car and track:
            self._save_preset_for_pair(car, track, show_message=False)
        else:
            self.save_config()

    def _finish_bounds_discovery(self, discovered_count: int, errors: List[str]) -> None:
        """Finish a bounds discovery run on the UI thread."""
        self._set_bounds_discovery_buttons_state(True)
        if discovered_count:
            self._persist_current_preset_after_bounds_discovery()
        if errors:
            message = f'Limits detected: {discovered_count}. Falhas: {len(errors)}.'
            self.notify_overlay_status(message, 'orange')
            self._show_warning("Limits", '\n'.join(errors[:8]))
        else:
            self.notify_overlay_status(f'Limits detected: {discovered_count}', 'green')

    def _bounds_discovery_start_delay(self) -> float:
        """Return the safety delay after the user requests bounds discovery."""
        parsed = _parse_optional_float(self.bounds_discovery_delay_s.get())
        if parsed is None:
            parsed = 5.0
        parsed = max(0.0, min(30.0, float(parsed)))
        normalized = f'{parsed:.1f}'.rstrip('0').rstrip('.')
        if normalized != self.bounds_discovery_delay_s.get().strip():
            self.bounds_discovery_delay_s.set(normalized)
            self.schedule_save()
        return parsed

    def auto_discover_all_control_bounds(self, var_names: Optional[List[str]]=None, *, use_start_delay: bool=True) -> None:
        """Discover min/max limits only after an explicit user command."""
        if self.app_state != 'RUNNING':
            self._show_info("Limits", "Use RUNNING mode to detect the limits.")
            return
        if not self._commands_allowed():
            self._show_warning("Limits", "Commands are blocked while the car is off track.")
            return
        candidates = self._control_bounds_discovery_candidates(var_names)
        if not candidates:
            self._show_warning("Limits", "No numeric control has increase and decrease keys set.")
            return
        self._set_bounds_discovery_buttons_state(False)
        delay_s = self._bounds_discovery_start_delay() if use_start_delay else 0.0
        if delay_s > 0:
            self.notify_overlay_status(f'Detecting limits on {math.ceil(delay_s)}s', 'orange')
        else:
            self.notify_overlay_status("Detecting limits...", 'orange')

        def _worker() -> None:
            discovered_count = 0
            errors: List[str] = []
            if delay_s > 0:
                deadline = time.time() + delay_s
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    if self.app_state != 'RUNNING':
                        errors.append("Detection stopped: the app left RUNNING mode.")
                        self.ui(self._finish_bounds_discovery, discovered_count, errors)
                        return
                    self.notify_overlay_status(f'Detecting limits on {max(1, math.ceil(remaining))}s', 'orange')
                    time.sleep(min(1.0, max(0.05, remaining)))
                if not self._commands_allowed():
                    errors.append("Commands blocked while the car is off track.")
                    self.ui(self._finish_bounds_discovery, discovered_count, errors)
                    return
            for _var_name, tab in candidates:
                if self.app_state != 'RUNNING':
                    errors.append("Detection stopped: the app left RUNNING mode.")
                    break
                try:
                    self.notify_overlay_status(f'Detecting {tab.label_name}', 'orange')
                    tab.update_status_label("Detecting limits...", 'orange')
                    min_value, max_value = tab.controller.discover_bounds()
                    self.ui(tab.set_bounds_from_values, min_value, max_value, persist=False)
                    tab.update_status_label("Limits OK", 'green')
                    discovered_count += 1
                except Exception as exc:
                    tab.update_status_label("Limits failed", 'red')
                    errors.append(f'{tab.label_name}: {exc}')
            self.ui(self._finish_bounds_discovery, discovered_count, errors)
        threading.Thread(target=_worker, daemon=True).start()

    def _overlay_var_list(self) -> List[Tuple[str, bool, bool]]:
        """Return the active variable list plus overlay-only telemetry rows."""
        overlay_vars = [_normalize_var_tuple(item) for item in self.active_vars]
        existing = {name for name, _is_float, _is_boolean in overlay_vars}
        for name, is_float, is_boolean in self.overlay_extra_vars:
            if name not in existing:
                overlay_vars.append((name, is_float, is_boolean))
        return overlay_vars

    def update_overlay_loop(self):
        """Background loop to update HUD values."""
        telemetry_ready = self.app_state == 'RUNNING' and self._telemetry_active
        next_interval = 100
        if self.overlay_visible:
            if telemetry_ready:
                data = {}
                car = self.current_car or "Generic Car"
                config = self.car_overlay_config.get(car, {})
                visible_controller_keys = [var_name for var_name in self.controllers.keys() if config.get(var_name, {}).get('show', False)]
                batch_keys = [key for key in visible_controller_keys if key not in DRIVER_CONTROL_CONFIRMED_STATE_VARS]
                if batch_keys:
                    data.update(self._batch_read_ir_values(batch_keys))
                for key in visible_controller_keys:
                    if key not in DRIVER_CONTROL_CONFIRMED_STATE_VARS:
                        continue
                    controller = self.controllers.get(key)
                    if controller is None:
                        continue
                    data[key] = controller.read_telemetry(use_cache=True, cache_ttl_s=0.08)
                extra_keys = []
                for var_name, var_config in config.items():
                    if not var_config.get('show', False):
                        continue
                    if var_name in data:
                        continue
                    if var_name in self.controllers:
                        continue
                    extra_keys.append(var_name)
                if extra_keys:
                    data.update(self._batch_read_ir_values(extra_keys))
                self.overlay.update_monitor_values(data)
                next_interval = 100
            else:
                self.overlay.update_monitor_values({})
                next_interval = 250
        if self.show_overlay_feedback.get() and telemetry_ready:
            self._update_overlay_feedback()
        else:
            self._overlay_feedback_state['last_time'] = time.time()
            if not self.overlay_visible:
                next_interval = 400 if not telemetry_ready else 180
        self.root.after(next_interval, self.update_overlay_loop)
__all__ = ['UiMixin']
