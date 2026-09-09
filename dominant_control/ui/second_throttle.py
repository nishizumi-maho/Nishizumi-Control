"""User interface for independent Second Throttle macros."""
from __future__ import annotations
import tkinter as tk
from typing import Any, Optional
from ..core import MAX_SECOND_THROTTLE_MACROS, SECOND_THROTTLE_PIT_MACRO_ID, default_second_throttle_macro, macro_lapdist_range, sanitize_second_throttle_macros, sanitize_second_throttle_pit_macro
from ..foundation import LAPDIST_MACROS_AVAILABLE, SECOND_THROTTLE_PIT_MACRO_AVAILABLE, _format_game_binding_label, _format_input_code_label, _keyboard_binding_scan_identity, input_manager

def _field_text(value: Any) -> str:
    if value in (None, ''):
        return ''
    if isinstance(value, float):
        return f'{value:.6f}'.rstrip('0').rstrip('.')
    return str(value)

class SecondThrottlePanel(tk.Frame):
    """Configure one iRacing output and any number of independent macros."""

    def __init__(self, parent: tk.Widget, app: Any) -> None:
        super().__init__(parent)
        self.app = app
        self._editing = app.app_state == 'CONFIG'
        self._row_widgets: list[dict[str, Any]] = []
        self._build()
        self._rebuild_macro_rows()
        self.refresh()

    def _build(self) -> None:
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        scrollbar = tk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.container = tk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.container, anchor='nw')
        self.container.bind('<Configure>', lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self._canvas_window, width=event.width))
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(int(-event.delta / 120), 'units'))
        self.container.columnconfigure(0, weight=1)
        tk.Label(self.container, text="Second Throttle Macros", font=('Segoe UI Semibold', 18)).grid(row=0, column=0, sticky='w', padx=18, pady=(16, 0))
        tk.Label(self.container, text="One key in iRacing; several hotkeys and intensities in Dominant Control.", fg='#5f6b7a', font=('Segoe UI', 10)).grid(row=1, column=0, sticky='w', padx=18, pady=(0, 12))
        self.status_card = tk.Frame(self.container, bg='#202833', highlightthickness=1, highlightbackground='#364152')
        self.status_card.grid(row=2, column=0, sticky='ew', padx=18, pady=(0, 12))
        self.status_card.columnconfigure(0, weight=1)
        self.status_label = tk.Label(self.status_card, textvariable=self.app.second_throttle_status_var, bg='#202833', fg='#ff7b72', font=('Segoe UI Semibold', 15), anchor='w')
        self.status_label.grid(row=0, column=0, sticky='ew', padx=14, pady=(12, 2))
        self.detail_label = tk.Label(self.status_card, textvariable=self.app.second_throttle_detail_var, bg='#202833', fg='#d0d7de', font=('Segoe UI', 10), anchor='w', justify='left')
        self.detail_label.grid(row=1, column=0, sticky='ew', padx=14, pady=(0, 12))
        self.toggle_button = tk.Button(self.status_card, textvariable=self.app.second_throttle_action_var, command=self.app.toggle_second_throttle, bg='#1f8f45', fg='white', activebackground='#18763a', activeforeground='white', font=('Segoe UI Semibold', 11), width=18, height=2)
        self.toggle_button.grid(row=0, column=1, rowspan=2, sticky='e', padx=14, pady=12)
        output_frame = tk.LabelFrame(self.container, text="Key sent to iRacing")
        output_frame.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 12))
        output_frame.columnconfigure(0, weight=1)
        tk.Label(output_frame, text="Assign a keyboard key of its own to the “Second Throttle” control in iRacing and record that same key here.", justify='left', wraplength=760).grid(row=0, column=0, sticky='w', padx=12, pady=10)
        self.output_button = tk.Button(output_frame, text="Set the iRacing key", command=self.app._set_second_throttle_output_bind, width=27)
        self.output_button.grid(row=0, column=1, sticky='e', padx=12, pady=10)
        macros_header = tk.Frame(self.container)
        macros_header.grid(row=4, column=0, sticky='ew', padx=18)
        macros_header.columnconfigure(0, weight=1)
        tk.Label(macros_header, text='Macros ON/OFF', font=('Segoe UI Semibold', 12)).grid(row=0, column=0, sticky='w')
        self.add_button = tk.Button(macros_header, text="+ Add macro", command=self._add_macro)
        self.add_button.grid(row=0, column=1, sticky='e')
        self.macros_frame = tk.Frame(self.container)
        self.macros_frame.grid(row=5, column=0, sticky='ew', padx=18, pady=(6, 12))
        self.macros_frame.columnconfigure(0, weight=1)
        self.pit_enabled_var = tk.BooleanVar(value=False)
        self.pit_percent_var = tk.StringVar(value='100')
        self.pit_check = None
        self.pit_percent_spinbox = None
        tk.Label(self.container, text="100% holds the key down. Lower values use approximate modulation; check the result on iRacing's pedal gauge.", fg='#5f6b7a', font=('Segoe UI', 9), justify='left', wraplength=860).grid(row=7, column=0, sticky='w', padx=18, pady=(0, 16))

    def _rebuild_macro_rows(self) -> None:
        for child in self.macros_frame.winfo_children():
            child.destroy()
        self._row_widgets = []
        macros = sanitize_second_throttle_macros(self.app.second_throttle_macros, ensure_one=True)
        self.app.second_throttle_macros = macros
        for index, macro in enumerate(macros):
            self._build_macro_card(index, macro)

    def _build_macro_card(self, index: int, macro: dict[str, Any]) -> None:
        card = tk.LabelFrame(self.macros_frame, text=f'Macro {index + 1}', padx=10, pady=8)
        card.grid(row=index, column=0, sticky='ew', pady=(0, 8))
        card.columnconfigure(1, weight=1)
        row: dict[str, Any] = {'id': macro['id'], 'frame': card, 'name_var': tk.StringVar(value=macro['name']), 'percent_var': tk.StringVar(value=str(macro['percentage'])), 'bind_var': tk.StringVar(value=str(macro.get('bind') or '')), 'lap_enabled_var': tk.BooleanVar(value=bool(macro.get('lapdist_enabled', False))), 'start_min_var': tk.StringVar(value=_field_text(macro.get('lap_start_min'))), 'start_max_var': tk.StringVar(value=_field_text(macro.get('lap_start_max'))), 'end_min_var': tk.StringVar(value=_field_text(macro.get('lap_end_min'))), 'end_max_var': tk.StringVar(value=_field_text(macro.get('lap_end_max'))), 'takeover_enabled_var': tk.BooleanVar(value=bool(macro.get('pedal_takeover_enabled', False))), 'takeover_threshold_var': tk.StringVar(value=str(macro.get('pedal_takeover_threshold', 25))), 'edit_widgets': []}
        tk.Label(card, text="Name:").grid(row=0, column=0, sticky='w')
        name_entry = tk.Entry(card, textvariable=row['name_var'], width=25)
        name_entry.grid(row=0, column=1, sticky='ew', padx=(6, 12))
        name_entry.bind('<Return>', lambda _event: self.sync_to_app(schedule=True))
        name_entry.bind('<FocusOut>', lambda _event: self.sync_to_app(schedule=True))
        tk.Label(card, text="Intensity:").grid(row=0, column=2, sticky='e')
        percent_spinbox = tk.Spinbox(card, from_=1, to=100, increment=1, width=6, justify='right', textvariable=row['percent_var'], command=lambda: self.sync_to_app(schedule=True))
        percent_spinbox.grid(row=0, column=3, sticky='w', padx=(6, 2))
        percent_spinbox.bind('<Return>', lambda _event: self.sync_to_app(schedule=True))
        percent_spinbox.bind('<FocusOut>', lambda _event: self.sync_to_app(schedule=True))
        tk.Label(card, text='%').grid(row=0, column=4, sticky='w', padx=(0, 8))
        bind_button = tk.Button(card, text="Set ON/OFF hotkey", width=22, command=lambda macro_id=macro['id']: self.capture_macro_hotkey(macro_id))
        bind_button.grid(row=0, column=5, sticky='e', padx=(4, 4))
        run_button = tk.Button(card, text="Turn on", width=9, command=lambda macro_id=macro['id']: self.app.toggle_second_throttle_macro(macro_id, "button"))
        run_button.grid(row=0, column=6, sticky='e', padx=4)
        remove_button = tk.Button(card, text="Remove", width=9, command=lambda macro_id=macro['id']: self._remove_macro(macro_id))
        remove_button.grid(row=0, column=7, sticky='e', padx=(4, 0))
        lap_check = None
        lap_entries: list[tk.Entry] = []
        takeover_row = tk.Frame(card)
        takeover_row.grid(row=2, column=0, columnspan=8, sticky='w', pady=(6, 0))
        takeover_check = tk.Checkbutton(takeover_row, text="Experimental: cut when the pedal comes back", variable=row['takeover_enabled_var'], command=lambda: self.sync_to_app(schedule=True))
        takeover_check.pack(side='left')
        tk.Label(takeover_row, text="Above").pack(side='left', padx=(12, 4))
        takeover_spinbox = tk.Spinbox(takeover_row, from_=1, to=100, increment=1, width=6, justify='right', textvariable=row['takeover_threshold_var'], command=lambda: self.sync_to_app(schedule=True))
        takeover_spinbox.pack(side='left')
        takeover_spinbox.bind('<Return>', lambda _event: self.sync_to_app(schedule=True))
        takeover_spinbox.bind('<FocusOut>', lambda _event: self.sync_to_app(schedule=True))
        tk.Label(takeover_row, text="% • arms after you release the pedal (down to 5%)").pack(side='left', padx=(3, 0))
        validation_label = tk.Label(card, text='', fg='#5f6b7a', font=('Segoe UI', 8), anchor='w')
        validation_label.grid(row=3, column=0, columnspan=8, sticky='w', pady=(4, 0))
        row.update({'name_entry': name_entry, 'percent_spinbox': percent_spinbox, 'bind_button': bind_button, 'run_button': run_button, 'remove_button': remove_button, 'lap_check': lap_check, 'lap_entries': lap_entries, 'takeover_check': takeover_check, 'takeover_spinbox': takeover_spinbox, 'validation_label': validation_label})
        row['edit_widgets'] = [name_entry, percent_spinbox, bind_button, remove_button, takeover_check, takeover_spinbox]
        if lap_check is not None:
            row['edit_widgets'].append(lap_check)
            row['edit_widgets'].extend(lap_entries)
        self._row_widgets.append(row)

    def _macro_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {'id': row['id'], 'name': row['name_var'].get(), 'percentage': row['percent_var'].get(), 'bind': row['bind_var'].get() or None, 'lapdist_enabled': False, 'lap_start_min': '', 'lap_start_max': '', 'lap_end_min': '', 'lap_end_max': '', 'pedal_takeover_enabled': row['takeover_enabled_var'].get(), 'pedal_takeover_threshold': row['takeover_threshold_var'].get()}

    def sync_to_app(self, *, schedule: bool) -> None:
        """Commit all visible fields to the application configuration."""
        macros = sanitize_second_throttle_macros([self._macro_from_row(row) for row in self._row_widgets], ensure_one=True)
        self.app.second_throttle_macros = macros
        first = macros[0]
        self.app.second_throttle_toggle_bind = first.get('bind')
        self.app.second_throttle_percent.set(str(first['percentage']))
        self.app.second_throttle_pit_macro = sanitize_second_throttle_pit_macro({'enabled': False, 'percentage': self.pit_percent_var.get()})
        for row, macro in zip(self._row_widgets, macros):
            row['percent_var'].set(str(macro['percentage']))
            row['takeover_threshold_var'].set(str(macro['pedal_takeover_threshold']))
        self.pit_percent_var.set(str(self.app.second_throttle_pit_macro['percentage']))
        self.app._apply_second_throttle_config(schedule=schedule, reregister=schedule and self.app.app_state == 'RUNNING')
        self.refresh()

    def _add_macro(self) -> None:
        if not self._editing:
            return
        self.sync_to_app(schedule=False)
        macros = list(self.app.second_throttle_macros)
        if len(macros) >= MAX_SECOND_THROTTLE_MACROS:
            self.app._show_warning("Second Throttle", f'The limit is {MAX_SECOND_THROTTLE_MACROS} macros.')
            return
        used = {str(item.get('id') or '') for item in macros}
        index = 1
        while f'second-throttle-{index}' in used:
            index += 1
        macros.append(default_second_throttle_macro(index))
        self.app.second_throttle_macros = macros
        self._rebuild_macro_rows()
        self.sync_to_app(schedule=True)
        self.canvas.yview_moveto(1.0)

    def _remove_macro(self, macro_id: str) -> None:
        if not self._editing:
            return
        if len(self._row_widgets) <= 1:
            self.app._show_info("Second Throttle", "Keep at least one ON/OFF macro.")
            return
        self.sync_to_app(schedule=False)
        self.app.second_throttle_macros = [item for item in self.app.second_throttle_macros if item.get('id') != macro_id]
        self._rebuild_macro_rows()
        self.sync_to_app(schedule=True)

    def _row_for_id(self, macro_id: str) -> Optional[dict[str, Any]]:
        for row in self._row_widgets:
            if row['id'] == macro_id:
                return row
        return None

    def capture_macro_hotkey(self, macro_id: str) -> None:
        """Capture one keyboard/joystick ON/OFF trigger for a macro row."""
        if self.app.app_state != 'CONFIG':
            self.app._show_info("Second Throttle", "Switch to CONFIG mode to set the macro's hotkey.")
            return
        row = self._row_for_id(macro_id)
        if row is None:
            return
        self.sync_to_app(schedule=False)
        previous = row['bind_var'].get()
        row['bind_button'].config(text="Press a key/button...", bg='yellow')
        self.app.focus_window()
        self.app.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            source_id = f'second-throttle-macro:{macro_id}'
            conflict = self.app._find_hotkey_conflict(code, source_id)
            output_identity = _keyboard_binding_scan_identity(self.app.second_throttle_output_bind)
            trigger_identity = _keyboard_binding_scan_identity(code)
            if output_identity is not None and trigger_identity is not None and (output_identity == trigger_identity):
                conflict = "key sent to iRacing's Second Throttle"
            if conflict:
                self.app._show_warning("Choose another hotkey", f'{_format_input_code_label(code)} is already bound to {conflict}.')
                row['bind_var'].set(previous)
            else:
                row['bind_var'].set(str(code))
        elif code == 'CANCEL':
            row['bind_var'].set('')
        self.sync_to_app(schedule=True)

    def set_editing_state(self, editing: bool) -> None:
        if self._editing and (not editing):
            self.sync_to_app(schedule=False)
        self._editing = bool(editing)
        self.refresh()

    def _refresh_range_status(self, row: dict[str, Any]) -> None:
        label = row['validation_label']
        label.config(text="Fired only by a button, a keyboard/joystick hotkey or voice.", fg='#19733b')

    def refresh(self) -> None:
        output = self.app.second_throttle_output_bind
        active = bool(self.app.second_throttle_engine.active)
        active_id = self.app.second_throttle_active_macro_id
        ready = bool(output and self.app.second_throttle_macros and (self.app._second_throttle_configuration_error() is None))
        if output:
            label = self.app.second_throttle_output_label or _format_game_binding_label(output)
            self.output_button.config(text=f'Key: {label or output}', bg='#ADD8E6')
        else:
            self.output_button.config(text="Set the iRacing key", bg='#f0f0f0')
        config_state = 'normal' if self._editing and (not active) else 'disabled'
        self.output_button.config(state=config_state)
        self.add_button.config(state=config_state)
        if self.pit_check is not None:
            self.pit_check.config(state=config_state)
        if self.pit_percent_spinbox is not None:
            self.pit_percent_spinbox.config(state=config_state)
        for row in self._row_widgets:
            bind = row['bind_var'].get().strip()
            if bind:
                color = '#90ee90' if 'JOY' in bind else '#ADD8E6'
                row['bind_button'].config(text=f'Hotkey: {_format_input_code_label(bind)}', bg=color)
            else:
                row['bind_button'].config(text="Set ON/OFF hotkey", bg='#f0f0f0')
            for widget in row['edit_widgets']:
                widget.config(state=config_state)
            row['run_button'].config(state='normal' if ready and (not self._editing) else 'disabled', text="Turn off" if active and active_id == row['id'] else "Turn on", bg='#f6c2bd' if active and active_id == row['id'] else '#f0f0f0')
            self._refresh_range_status(row)
        self.toggle_button.config(state='normal' if ready and (not self._editing) else 'disabled', bg='#b42318' if active else '#1f8f45', activebackground='#8f1c14' if active else '#18763a')
        self.status_label.config(fg='#3fb950' if active else '#ff7b72')
__all__ = ['SecondThrottlePanel']
