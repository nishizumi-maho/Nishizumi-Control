from typing import Any, Dict, List

import tkinter as tk
from tkinter import messagebox, ttk

from dominant_control.controllers import GenericController
from dominant_control.input_manager import input_manager
from .widgets import ScrollableFrame


class ComboTab(tk.Frame):
    """Tab for creating combo macros that adjust multiple variables with one trigger."""

    def __init__(
        self,
        parent,
        controllers_dict: Dict[str, GenericController],
        app
    ):
        super().__init__(parent)
        self.app = app
        self.controllers = controllers_dict
        self.var_names = list(self.controllers.keys())
        self.preset_rows: List[Dict[str, Any]] = []

        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        body = scroll_frame.inner

        tk.Label(
            body,
            text="⚡ Combo Adjustments (one trigger -> multiple variables)",
            fg="orange",
            font=("Arial", 10, "bold")
        ).pack(pady=5)

        # Header row
        header = tk.Frame(body)
        header.pack(fill="x", padx=5, pady=5)

        tk.Label(
            header,
            text="Trigger",
            width=15,
            anchor="w",
            font=("Arial", 9, "bold")
        ).pack(side="left", padx=2)

        for var_name in self.var_names:
            tk.Label(
                header,
                text=var_name.replace("dc", ""),
                width=8,
                font=("Arial", 8)
            ).pack(side="left", padx=2)

        tk.Label(
            header,
            text="Voice trigger phrase",
            width=18,
            anchor="w",
            font=("Arial", 8, "bold")
        ).pack(side="left", padx=4)

        tk.Label(
            body,
            text=(
                "Optional voice trigger: type the exact phrase you will say to fire this combo. "
                "Voice/Audio Settings live under Options → Voice/Audio Settings."
            ),
            fg="gray",
            font=("Arial", 8),
            wraplength=760,
            justify="left"
        ).pack(fill="x", padx=5, pady=(0, 4))

        self.presets_container = tk.Frame(body)
        self.presets_container.pack(fill="both", expand=True, padx=5, pady=5)

        tk.Button(
            body,
            text="Add Row (+)",
            command=self.add_dynamic_row,
            bg="#f0f0f0"
        ).pack(fill="x", padx=5, pady=(0, 5))

        # Add initial rows
        self.add_dynamic_row(is_reset=True)
        for _ in range(2):
            self.add_dynamic_row()

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = "normal" if enabled else "readonly"

        for row in self.preset_rows:
            for entry in row["entries"].values():
                try:
                    entry.config(state=state)
                except Exception:
                    pass
            voice_entry = row.get("voice_entry")
            if voice_entry:
                try:
                    voice_entry.config(state=state)
                except Exception:
                    pass

    def _config_bind_button(self, button: tk.Button, data_store: Dict[str, Any]):
        """Configure binding button behavior."""

        def on_click():
            if self.app.app_state != "CONFIG":
                messagebox.showinfo("Notice", "Enter CONFIG mode first.")
                return

            self.app.focus_window()

            button.config(text="...", bg="yellow")
            self.update_idletasks()

            code = input_manager.capture_any_input()

            if code and code != "CANCEL":
                data_store["bind"] = code
                bg_color = "#90ee90" if "JOY" in code else "#ADD8E6"
                button.config(text=code, bg=bg_color)
            elif code == "CANCEL":
                data_store["bind"] = None
                button.config(text="Set Bind", bg="#f0f0f0")

            self.app.schedule_save()

        button.config(command=on_click)

    def add_dynamic_row(
        self,
        existing: Optional[Dict[str, Any]] = None,
        is_reset: bool = False
    ):
        """Add a combo preset row."""
        frame = tk.Frame(self.presets_container)
        frame.pack(fill="x", pady=2)

        bind_button = tk.Button(
            frame,
            text="RESET" if is_reset else "Set Bind",
            width=15,
            fg="red" if is_reset else "black"
        )
        bind_button.pack(side="left", padx=2)

        row_data = {
            "frame": frame,
            "entries": {},
            "bind": None,
            "is_reset": is_reset,
            "voice_entry": None
        }
        self._config_bind_button(bind_button, row_data)

        # Create entry for each variable
        for var_name in self.var_names:
            entry = ttk.Entry(frame, width=8)
            entry.pack(side="left", padx=2)
            if self.app.app_state != "CONFIG":
                entry.config(state="readonly")
            row_data["entries"][var_name] = entry

        # Delete button (except for RESET)
        if not is_reset:
            tk.Button(
                frame,
                text="X",
                fg="red",
                command=lambda r=row_data: self.remove_row(r),
                width=2
            ).pack(side="left", padx=5)

        # Load existing data if provided
        if existing:
            values = existing.get("vals", {})
            for var_name, value in values.items():
                if var_name in row_data["entries"]:
                    entry = row_data["entries"][var_name]
                    entry.config(state="normal")
                    entry.insert(0, value)
                    if self.app.app_state != "CONFIG":
                        entry.config(state="readonly")

            row_data["bind"] = existing.get("bind")
            if row_data["bind"]:
                bg_color = (
                    "#90ee90" if "JOY" in row_data["bind"] else "#ADD8E6"
                )
                bind_button.config(text=row_data["bind"], bg=bg_color)

        voice_entry = ttk.Entry(frame, width=18)
        voice_entry.pack(side="left", padx=4)
        if existing and existing.get("voice_phrase"):
            voice_entry.insert(0, existing.get("voice_phrase", ""))
        if self.app.app_state != "CONFIG":
            voice_entry.config(state="readonly")
        row_data["voice_entry"] = voice_entry

        self.preset_rows.append(row_data)

    def remove_row(self, row_data: Dict[str, Any]):
        """Remove a preset row."""
        if self.app.app_state != "CONFIG":
            return

        row_data["frame"].destroy()
        if row_data in self.preset_rows:
            self.preset_rows.remove(row_data)
        self.app.schedule_save()

    def get_config(self) -> Dict[str, Any]:
        """Get current combo configuration."""
        presets_data = []
        for row in self.preset_rows:
            values = {
                var_name: entry.get()
                for var_name, entry in row["entries"].items()
            }
            presets_data.append({
                "vals": values,
                "bind": row["bind"],
                "is_reset": row["is_reset"],
                "voice_phrase": (
                    row.get("voice_entry").get() if row.get("voice_entry") else ""
                )
            })
        return {"presets": presets_data}

    def set_config(self, config: Dict[str, Any]):
        """Load combo configuration."""
        # Clear existing rows
        for row in list(self.preset_rows):
            row["frame"].destroy()
        self.preset_rows.clear()

        if not config:
            self.add_dynamic_row(is_reset=True)
            for _ in range(2):
                self.add_dynamic_row()
            return

        saved_presets = config.get("presets", [])
        has_reset = any(p.get("is_reset") for p in saved_presets)

        if not has_reset:
            self.add_dynamic_row(is_reset=True)

        for preset in saved_presets:
            self.add_dynamic_row(
                existing=preset,
                is_reset=preset.get("is_reset", False)
            )

        if len(self.preset_rows) < 2:
            self.add_dynamic_row()
