"""The Auxiliary Keys tab.

Two areas, stacked.  The top one fetches every control iRacing knows about
and shows which keyboard key each one uses today - a read-only view, so a
refresh here can never move a key inside the Macros tab.  The bottom one is
the mapping table: each row owns the key the game listens to plus any number
of auxiliary keys that stand in for it, a description, and whether it is
saved app-wide or only in the current car's profile.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Optional

from ..core import (
    GHOST_MODE_HOLD,
    GHOST_MODE_LABELS,
    GHOST_MODE_PULSE,
    GHOST_SCOPE_CAR,
    GHOST_SCOPE_GLOBAL,
    GHOST_SCOPE_LABELS,
    MAX_GHOST_TRIGGERS,
    ghost_row_problem,
    ghost_row_title,
    normalize_ghost_mode,
    normalize_ghost_scope,
    normalize_ghost_triggers,
    sanitize_ghost_rows,
)
from ..foundation import (
    _format_game_binding_label,
    _format_input_code_label,
    _unsafe_system_binding_reason,
    input_manager,
)

_SCOPE_CHOICES = [GHOST_SCOPE_LABELS[GHOST_SCOPE_GLOBAL], GHOST_SCOPE_LABELS[GHOST_SCOPE_CAR]]
_SCOPE_BY_LABEL = {label: key for key, label in GHOST_SCOPE_LABELS.items()}
_MODE_CHOICES = [GHOST_MODE_LABELS[GHOST_MODE_PULSE], GHOST_MODE_LABELS[GHOST_MODE_HOLD]]
_MODE_BY_LABEL = {label: key for key, label in GHOST_MODE_LABELS.items()}


class GhostKeysPanel(tk.Frame):
    """Fetch the iRacing controls and map auxiliary keys onto them."""

    def __init__(self, parent: tk.Widget, app: Any) -> None:
        super().__init__(parent)
        self.app = app
        self._editing = app.app_state == "CONFIG"
        self._row_widgets: list[dict[str, Any]] = []
        self._catalog_rows: list[dict[str, Any]] = []
        self.filter_var = tk.StringVar(value="")
        self.only_with_key_var = tk.BooleanVar(value=False)
        self.catalog_status_var = tk.StringVar(value="")
        self._build()
        self._rebuild_mapping_rows()
        self.refresh()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build(self) -> None:
        header = tk.Frame(self)
        header.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(
            header,
            text="Auxiliary Keys",
            font=("Segoe UI Semibold", 18),
        ).pack(side="left")
        tk.Label(
            header,
            text=(
                "One key the game already understands; several wheel or keyboard keys that fire it."
            ),
            fg="#5f6b7a",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(12, 0))

        paned = tk.PanedWindow(
            self,
            orient="vertical",
            sashwidth=6,
            sashrelief="raised",
            bg="#dfe3e8",
        )
        paned.pack(fill="both", expand=True, padx=16, pady=(10, 14))

        self._build_catalog(paned)
        self._build_mapping(paned)

    def _build_catalog(self, parent: tk.Widget) -> None:
        card = tk.LabelFrame(parent, text="iRacing controls", padx=8, pady=6)
        parent.add(card, minsize=210, stretch="always")

        actions = tk.Frame(card)
        actions.pack(fill="x", pady=(2, 6))

        self.fetch_button = tk.Button(
            actions,
            text="Fetch iRacing controls",
            command=self._fetch_catalog,
            bg="#e7f4df",
            font=("Segoe UI Semibold", 10),
        )
        self.fetch_button.pack(side="left")

        tk.Label(actions, text="Filter:").pack(side="left", padx=(14, 4))
        filter_entry = tk.Entry(actions, textvariable=self.filter_var, width=26)
        filter_entry.pack(side="left")
        self.filter_var.trace_add("write", lambda *_: self._refresh_catalog_list())

        tk.Checkbutton(
            actions,
            text="Only the ones with a keyboard key",
            variable=self.only_with_key_var,
            command=self._refresh_catalog_list,
        ).pack(side="left", padx=(12, 0))

        self.use_button = tk.Button(
            actions,
            text="Create mapping",
            command=self._create_row_from_selection,
            bg="#ADD8E6",
            font=("Segoe UI Semibold", 10),
        )
        self.use_button.pack(side="right")

        tk.Label(
            card,
            textvariable=self.catalog_status_var,
            fg="#5f6b7a",
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(0, 4))

        tree_frame = tk.Frame(card)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("control", "key", "status", "aux"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.tree.heading("control", text="Control")
        self.tree.heading("key", text="In-game key")
        self.tree.heading("status", text="Source")
        self.tree.heading("aux", text="Auxiliary keys")
        self.tree.column("control", width=280, anchor="w")
        self.tree.column("key", width=170, anchor="w")
        self.tree.column("status", width=190, anchor="w")
        self.tree.column("aux", width=240, anchor="w")
        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind(
            "<Double-1>",
            lambda _event: self._create_row_from_selection(),
        )

    def _build_mapping(self, parent: tk.Widget) -> None:
        card = tk.LabelFrame(parent, text="Mappings", padx=8, pady=6)
        parent.add(card, minsize=240, stretch="always")

        actions = tk.Frame(card)
        actions.pack(fill="x", pady=(2, 6))
        self.add_button = tk.Button(
            actions,
            text="+ New mapping",
            command=self._add_row,
            bg="#e7f4df",
            font=("Segoe UI Semibold", 10),
        )
        self.add_button.pack(side="left")
        tk.Label(
            actions,
            text=(
                "Pulse sends one tap; Hold keeps the in-game key pressed while the auxiliary one is held."
            ),
            fg="#5f6b7a",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(12, 0))

        self.canvas = tk.Canvas(card, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(
            card,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.rows_frame = tk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window(
            (0, 0),
            window=self.rows_frame,
            anchor="nw",
        )
        self.rows_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                self._canvas_window,
                width=event.width,
            ),
        )
        self.canvas.bind(
            "<MouseWheel>",
            lambda event: self.canvas.yview_scroll(
                int(-event.delta / 120),
                "units",
            ),
        )
        self.rows_frame.columnconfigure(0, weight=1)

        self.empty_label = tk.Label(
            self.rows_frame,
            text=(
                "No mapping yet. Pick a control from the list above and click “Create mapping”, or use “+ New mapping” to capture the in-game key by hand."
            ),
            fg="#5f6b7a",
            font=("Segoe UI", 10),
            justify="left",
            wraplength=760,
        )

    # ------------------------------------------------------------------
    # Catalogue
    # ------------------------------------------------------------------
    def _fetch_catalog(self) -> None:
        self.fetch_button.config(text="Searching...", state="disabled")
        self.update_idletasks()
        try:
            self.app.fetch_ghost_control_catalog()
        finally:
            self.fetch_button.config(
                text="Fetch iRacing controls",
                state="normal",
            )

    def _aux_summary_by_control(self) -> dict[str, str]:
        summary: dict[str, str] = {}
        for row in self._rows_from_widgets():
            control = str(row.get("control") or "").strip()
            if not control:
                continue
            triggers = normalize_ghost_triggers(row.get("triggers"))
            if not triggers:
                continue
            labels = ", ".join(_format_input_code_label(code) for code in triggers)
            existing = summary.get(control)
            summary[control] = f"{existing}, {labels}" if existing else labels
        return summary

    def _refresh_catalog_list(self) -> None:
        catalog = list(getattr(self.app, "ghost_catalog", []) or [])
        needle = self.filter_var.get().strip().lower()
        only_with_key = self.only_with_key_var.get()
        aux_by_control = self._aux_summary_by_control()

        self.tree.delete(*self.tree.get_children())
        self._catalog_rows = []
        for entry in catalog:
            if only_with_key and not entry.get("importable"):
                continue
            if needle:
                haystack = (
                    f"{entry.get('name', '')} {entry.get('label', '')} "
                    f"{entry.get('key_label', '')}"
                ).lower()
                if needle not in haystack:
                    continue
            self._catalog_rows.append(entry)
            self.tree.insert(
                "",
                "end",
                iid=str(len(self._catalog_rows) - 1),
                values=(
                    entry.get("label") or entry.get("name", ""),
                    entry.get("key_label") or "—",
                    entry.get("status", ""),
                    aux_by_control.get(entry.get("name", ""), ""),
                ),
            )

        status = getattr(self.app, "ghost_catalog_status", "")
        if catalog and len(self._catalog_rows) != len(catalog):
            status = f'{status} • showing {len(self._catalog_rows)}'
        self.catalog_status_var.set(status)

    def _selected_catalog_entry(self) -> Optional[dict[str, Any]]:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self._catalog_rows[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def _create_row_from_selection(self) -> None:
        if not self._require_config_mode():
            return
        entry = self._selected_catalog_entry()
        if entry is None:
            self.app._show_info(
                "Auxiliary Keys",
                "Pick a control from the list to create the mapping.",
            )
            return
        if not entry.get("importable"):
            self.app._show_warning(
                "Auxiliary Keys",
                f'{entry.get('label') or entry.get('name')} has no usable keyboard key in iRacing ({entry.get('status') or 'no key'}).\n\nAssign a keyboard key to that control in iRacing, or create the mapping by hand and capture the key yourself.',
            )
            return

        self.sync_to_app(schedule=False)
        created = self.app.add_ghost_row(
            control=entry.get("name", ""),
            description=entry.get("label") or entry.get("name", ""),
            game_binding=entry.get("binding"),
            game_label=entry.get("key_label", ""),
        )
        if created is None:
            return
        self._rebuild_mapping_rows()
        self.sync_to_app(schedule=True)
        self.canvas.yview_moveto(1.0)

    # ------------------------------------------------------------------
    # Mapping rows
    # ------------------------------------------------------------------
    def _rebuild_mapping_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            if child is not self.empty_label:
                child.destroy()
        self._row_widgets = []

        rows = sanitize_ghost_rows(getattr(self.app, "ghost_rows", []))
        self.app.ghost_rows = rows
        for index, row in enumerate(rows):
            self._build_row_card(index, row)

        if rows:
            self.empty_label.grid_forget()
        else:
            self.empty_label.grid(row=0, column=0, sticky="w", padx=6, pady=12)

    def _build_row_card(self, index: int, row: dict[str, Any]) -> None:
        card = tk.LabelFrame(
            self.rows_frame,
            text=ghost_row_title(row, index + 1),
            padx=10,
            pady=8,
        )
        card.grid(row=index, column=0, sticky="ew", pady=(0, 8))
        card.columnconfigure(1, weight=1)

        widgets: dict[str, Any] = {
            "id": row["id"],
            "frame": card,
            "control": row.get("control", ""),
            "description_var": tk.StringVar(value=row.get("description", "")),
            "binding": row.get("game_binding"),
            "binding_label": row.get("game_label", ""),
            "triggers": normalize_ghost_triggers(row.get("triggers")),
            "mode_var": tk.StringVar(
                value=GHOST_MODE_LABELS[normalize_ghost_mode(row.get("mode"))]
            ),
            "scope_var": tk.StringVar(
                value=GHOST_SCOPE_LABELS[normalize_ghost_scope(row.get("scope"))]
            ),
            "enabled_var": tk.BooleanVar(value=bool(row.get("enabled", True))),
            "edit_widgets": [],
        }

        tk.Label(card, text="Description:").grid(row=0, column=0, sticky="w")
        description_entry = tk.Entry(
            card,
            textvariable=widgets["description_var"],
            width=34,
        )
        description_entry.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        description_entry.bind(
            "<Return>",
            lambda _event: self.sync_to_app(schedule=True),
        )
        description_entry.bind(
            "<FocusOut>",
            lambda _event: self.sync_to_app(schedule=True),
        )

        game_button = tk.Button(
            card,
            text="In-game key",
            width=24,
            command=lambda row_id=row["id"]: self.capture_game_key(row_id),
        )
        game_button.grid(row=0, column=2, sticky="e", padx=4)

        mode_combo = ttk.Combobox(
            card,
            textvariable=widgets["mode_var"],
            values=_MODE_CHOICES,
            state="readonly",
            width=10,
        )
        mode_combo.grid(row=0, column=3, sticky="e", padx=4)
        mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.sync_to_app(schedule=True),
        )

        remove_button = tk.Button(
            card,
            text="Remove",
            width=9,
            command=lambda row_id=row["id"]: self._remove_row(row_id),
        )
        remove_button.grid(row=0, column=4, sticky="e", padx=(4, 0))

        triggers_frame = tk.Frame(card)
        triggers_frame.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        widgets["triggers_frame"] = triggers_frame

        footer = tk.Frame(card)
        footer.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        enabled_check = tk.Checkbutton(
            footer,
            text="Active",
            variable=widgets["enabled_var"],
            command=lambda: self.sync_to_app(schedule=True),
        )
        enabled_check.pack(side="left")
        tk.Label(footer, text="Save to:").pack(side="left", padx=(14, 4))
        scope_combo = ttk.Combobox(
            footer,
            textvariable=widgets["scope_var"],
            values=_SCOPE_CHOICES,
            state="readonly",
            width=22,
        )
        scope_combo.pack(side="left")
        scope_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.sync_to_app(schedule=True),
        )

        status_label = tk.Label(
            footer,
            text="",
            fg="#5f6b7a",
            font=("Segoe UI", 9),
            anchor="w",
        )
        status_label.pack(side="left", padx=(14, 0), fill="x", expand=True)

        widgets.update(
            {
                "description_entry": description_entry,
                "game_button": game_button,
                "mode_combo": mode_combo,
                "remove_button": remove_button,
                "enabled_check": enabled_check,
                "scope_combo": scope_combo,
                "status_label": status_label,
            }
        )
        widgets["edit_widgets"] = [
            description_entry,
            game_button,
            mode_combo,
            remove_button,
            enabled_check,
            scope_combo,
        ]
        self._row_widgets.append(widgets)
        self._rebuild_trigger_buttons(widgets)

    def _rebuild_trigger_buttons(self, widgets: dict[str, Any]) -> None:
        frame = widgets["triggers_frame"]
        for child in frame.winfo_children():
            child.destroy()
        widgets["trigger_widgets"] = []

        tk.Label(frame, text="Auxiliary keys:").pack(side="left", padx=(0, 6))
        for position, code in enumerate(widgets["triggers"]):
            button = tk.Button(
                frame,
                text=_format_input_code_label(code),
                bg="#90ee90" if "JOY" in str(code) else "#ADD8E6",
                command=(
                    lambda row_id=widgets["id"], idx=position: self.capture_trigger(
                        row_id,
                        idx,
                    )
                ),
            )
            button.pack(side="left", padx=2)
            drop_button = tk.Button(
                frame,
                text="×",
                width=2,
                command=(
                    lambda row_id=widgets["id"], idx=position: self._remove_trigger(
                        row_id,
                        idx,
                    )
                ),
            )
            drop_button.pack(side="left", padx=(0, 8))
            widgets["trigger_widgets"].extend([button, drop_button])

        if len(widgets["triggers"]) < MAX_GHOST_TRIGGERS:
            add_button = tk.Button(
                frame,
                text="+ Add auxiliary key",
                command=(
                    lambda row_id=widgets["id"]: self.capture_trigger(row_id, None)
                ),
            )
            add_button.pack(side="left")
            widgets["trigger_widgets"].append(add_button)

        state = "normal" if self._editing else "disabled"
        for widget in widgets["trigger_widgets"]:
            widget.config(state=state)

    def _row_widgets_for_id(self, row_id: Any) -> Optional[dict[str, Any]]:
        wanted = str(row_id or "")
        for widgets in self._row_widgets:
            if str(widgets["id"]) == wanted:
                return widgets
        return None

    def _add_row(self) -> None:
        if not self._require_config_mode():
            return
        self.sync_to_app(schedule=False)
        if self.app.add_ghost_row() is None:
            return
        self._rebuild_mapping_rows()
        self.sync_to_app(schedule=True)
        self.canvas.yview_moveto(1.0)

    def _remove_row(self, row_id: Any) -> None:
        if not self._require_config_mode():
            return
        self.sync_to_app(schedule=False)
        self.app.remove_ghost_row(row_id)
        self._rebuild_mapping_rows()
        self.sync_to_app(schedule=True)

    def _remove_trigger(self, row_id: Any, index: int) -> None:
        if not self._require_config_mode():
            return
        widgets = self._row_widgets_for_id(row_id)
        if widgets is None:
            return
        if 0 <= index < len(widgets["triggers"]):
            widgets["triggers"].pop(index)
        self._rebuild_trigger_buttons(widgets)
        self.sync_to_app(schedule=True)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------
    def _require_config_mode(self) -> bool:
        if self.app.app_state == "CONFIG":
            return True
        self.app._show_info(
            "Auxiliary Keys",
            "Switch to CONFIG mode to edit the auxiliary keys.",
        )
        return False

    def capture_game_key(self, row_id: Any) -> None:
        """Capture the keyboard key the game already listens to."""
        if not self._require_config_mode():
            return
        widgets = self._row_widgets_for_id(row_id)
        if widgets is None:
            return

        self.sync_to_app(schedule=False)
        widgets["game_button"].config(text="Press the in-game key...", bg="yellow")
        self.app.focus_window()
        self.app.root.update_idletasks()

        binding, label = input_manager.capture_game_action_binding()
        if label == "CANCEL":
            widgets["binding"] = None
            widgets["binding_label"] = ""
        elif binding:
            unsafe_reason = _unsafe_system_binding_reason(binding)
            if unsafe_reason:
                self.app._show_warning(
                    "Choose another key",
                    f'{label or binding} is a Windows system key ({unsafe_reason}).',
                )
            else:
                widgets["binding"] = binding
                widgets["binding_label"] = label or _format_game_binding_label(
                    binding
                )
                # Typed by hand, so it is no longer tied to a catalogue entry.
                widgets["control"] = ""

        self.sync_to_app(schedule=True)

    def _show_capture_prompt(
        self,
        widgets: dict[str, Any],
        index: Optional[int],
    ) -> None:
        """Tell the user we are listening, since the capture blocks the UI."""
        buttons = widgets.get("trigger_widgets") or []
        # Each existing trigger owns a label button and a remove button, so
        # the replace target sits at twice its position; a new trigger goes
        # to the trailing "add" button.
        target = 2 * index if index is not None else len(buttons) - 1
        if 0 <= target < len(buttons):
            buttons[target].config(
                text="Press the auxiliary key...",
                bg="yellow",
            )

    def capture_trigger(self, row_id: Any, index: Optional[int]) -> None:
        """Capture one auxiliary trigger, replacing or appending it."""
        if not self._require_config_mode():
            return
        widgets = self._row_widgets_for_id(row_id)
        if widgets is None:
            return
        if index is None and len(widgets["triggers"]) >= MAX_GHOST_TRIGGERS:
            self.app._show_warning(
                "Auxiliary Keys",
                f'The limit is {MAX_GHOST_TRIGGERS} auxiliary keys per mapping.',
            )
            return

        # Syncing can rebuild the cards, so the widget handle is refetched.
        self.sync_to_app(schedule=False)
        widgets = self._row_widgets_for_id(row_id)
        if widgets is None:
            return
        self._show_capture_prompt(widgets, index)
        self.app.focus_window()
        self.app.root.update_idletasks()

        code = input_manager.capture_any_input()
        if code == "CANCEL":
            if index is not None and 0 <= index < len(widgets["triggers"]):
                widgets["triggers"].pop(index)
        elif code:
            slot = index if index is not None else len(widgets["triggers"])
            conflict = self.app._find_hotkey_conflict(
                code,
                f"ghost:{row_id}:{slot}",
            )
            if conflict:
                self.app._show_warning(
                    "Choose another hotkey",
                    f'{_format_input_code_label(code)} is already bound to {conflict}.',
                )
            elif code in widgets["triggers"]:
                self.app._show_info(
                    "Auxiliary Keys",
                    f'{_format_input_code_label(code)} is already in this mapping.',
                )
            elif index is not None and 0 <= index < len(widgets["triggers"]):
                widgets["triggers"][index] = code
            else:
                widgets["triggers"].append(code)

        self._rebuild_trigger_buttons(widgets)
        self.sync_to_app(schedule=True)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    def _rows_from_widgets(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for widgets in self._row_widgets:
            rows.append(
                {
                    "id": widgets["id"],
                    "description": widgets["description_var"].get(),
                    "control": widgets.get("control", ""),
                    "game_binding": widgets.get("binding"),
                    "game_label": widgets.get("binding_label", ""),
                    "triggers": list(widgets["triggers"]),
                    "mode": _MODE_BY_LABEL.get(
                        widgets["mode_var"].get(),
                        GHOST_MODE_PULSE,
                    ),
                    "scope": _SCOPE_BY_LABEL.get(
                        widgets["scope_var"].get(),
                        GHOST_SCOPE_GLOBAL,
                    ),
                    "enabled": widgets["enabled_var"].get(),
                }
            )
        return rows

    def sync_to_app(self, *, schedule: bool) -> None:
        """Commit every visible field to the application configuration."""
        self.app.set_ghost_rows(self._rows_from_widgets(), schedule=schedule)
        self.refresh()

    def set_editing_state(self, editing: bool) -> None:
        if self._editing and not editing:
            self.sync_to_app(schedule=False)
        self._editing = bool(editing)
        self.refresh()

    def refresh(self) -> None:
        """Repaint labels, colours and enabled state from the current data."""
        rows = sanitize_ghost_rows(getattr(self.app, "ghost_rows", []))
        if len(rows) != len(self._row_widgets) or any(
            str(row["id"]) != str(widgets["id"])
            for row, widgets in zip(rows, self._row_widgets)
        ):
            self._rebuild_mapping_rows()

        config_state = "normal" if self._editing else "disabled"
        self.add_button.config(state=config_state)
        self.use_button.config(state=config_state)

        for position, widgets in enumerate(self._row_widgets, start=1):
            binding = widgets.get("binding")
            if binding:
                label = widgets.get("binding_label") or _format_game_binding_label(
                    binding
                )
                widgets["game_button"].config(
                    text=f'In-game key: {label}',
                    bg="#ADD8E6",
                )
            else:
                widgets["game_button"].config(
                    text="Set the in-game key",
                    bg="#f0f0f0",
                )

            row = {
                "game_binding": binding,
                "triggers": widgets["triggers"],
                "enabled": widgets["enabled_var"].get(),
                "description": widgets["description_var"].get(),
                "control": widgets.get("control", ""),
            }
            widgets["frame"].config(text=ghost_row_title(row, position))
            problem = ghost_row_problem(row)
            if problem:
                widgets["status_label"].config(text=problem, fg="#b42318")
            else:
                count = len(widgets["triggers"])
                widgets["status_label"].config(
                    text=(
                        f'Ready • {count} auxiliary key(s) fire this in-game key.'
                    ),
                    fg="#19733b",
                )

            for widget in widgets["edit_widgets"]:
                widget.config(state=config_state)
            for widget in widgets.get("trigger_widgets", []):
                widget.config(state=config_state)

        self._refresh_catalog_list()


__all__ = ["GhostKeysPanel"]
