import threading
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk

from dominant_control.controllers import GenericController
from dominant_control.input_manager import input_manager
from .widgets import ScrollableFrame


class ControlTab(tk.Frame):
    """Configuration tab for a single control variable."""

    def __init__(self, parent, controller: GenericController, label_name: str, app):
        super().__init__(parent)
        self.app = app
        self.controller = controller
        self.controller.update_status = self.update_status_label
        self.controller.app = app
        self.preset_rows: List[Dict[str, Any]] = []

        # Scrollable layout
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        body = scroll_frame.inner

        # Key configuration
        keys_frame = tk.LabelFrame(
            body,
            text=f"Keys ({label_name})",
            padx=5,
            pady=5
        )
        keys_frame.pack(fill="x", padx=5, pady=5)

        self.btn_increase = tk.Button(
            keys_frame,
            text="Set Increase (+)",
            command=lambda: self.bind_game_key("increase")
        )
        self.btn_increase.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_decrease = tk.Button(
            keys_frame,
            text="Set Decrease (-)",
            command=lambda: self.bind_game_key("decrease")
        )
        self.btn_decrease.pack(side="left", expand=True, fill="x", padx=2)

        tk.Button(
            keys_frame,
            text="Test custom minimal time",
            command=self.run_bot_timing_probe,
            bg="#f0f8ff"
        ).pack(side="left", padx=2)

        # Current value monitor
        self.lbl_monitor = tk.Label(
            body,
            text="Value: --",
            font=("Arial", 14, "bold")
        )
        self.lbl_monitor.pack(pady=5)

        # Status label
        self.lbl_status = tk.Label(body, text="Idle", fg="gray")
        self.lbl_status.pack()

        # Presets/Macros
        presets_frame = tk.LabelFrame(
            body,
            text="Presets / Macros",
            padx=5,
            pady=5
        )
        presets_frame.pack(fill="both", expand=True, padx=5, pady=5)

        tk.Label(
            presets_frame,
            text="RESET always returns to a base value (e.g., 0 or 50). Add your go-to macro values below.",
            fg="gray",
            font=("Arial", 8),
            wraplength=760,
            justify="left"
        ).pack(anchor="w", pady=(0, 5))

        tk.Label(
            presets_frame,
            text=(
                "Optional voice trigger: type the exact phrase you will say to run the macro. "
                "Voice/Audio Settings live under Options → Voice/Audio Settings."
            ),
            fg="gray",
            font=("Arial", 8),
            wraplength=760,
            justify="left"
        ).pack(anchor="w", padx=2, pady=(0, 5))

        header = tk.Frame(presets_frame)
        header.pack(fill="x", padx=2, pady=(0, 2))
        tk.Label(
            header, text="Type", width=6, anchor="w", font=("Arial", 8, "bold")
        ).pack(side="left")
        tk.Label(
            header,
            text="Macro value",
            width=10,
            anchor="w",
            font=("Arial", 8, "bold")
        ).pack(side="left", padx=5)
        tk.Label(
            header,
            text="Keybinding",
            width=12,
            anchor="w",
            font=("Arial", 8, "bold")
        ).pack(side="left", padx=5)
        tk.Label(
            header,
            text="Voice trigger phrase",
            width=20,
            anchor="w",
            font=("Arial", 8, "bold")
        ).pack(side="left", padx=5)

        self.presets_container = tk.Frame(presets_frame)
        self.presets_container.pack(fill="both", expand=True)

        # Add initial preset rows
        self.add_preset_row(is_reset=True)
        for _ in range(4):
            self.add_preset_row()

        # Start monitoring loop
        self.running = True
        self.after(500, self.monitor_loop)

    def update_status_label(self, text: str, color: str):
        """Update status label."""
        if self.app:
            self.app.ui(self.lbl_status.config, text=text, fg=color)

    def run_bot_timing_probe(self):
        """Run a fast timing probe to suggest a stable BOT delay."""

        def _worker():
            try:
                suggested = self.controller.find_minimum_effective_timing()
            except ValueError as exc:
                error_msg = str(exc)
                self.after(
                    0,
                    lambda msg=error_msg: messagebox.showerror("Keys Missing", msg)
                )
                return

            if suggested is None:
                self.after(
                    0,
                    lambda: messagebox.showwarning(
                        "Probe Result",
                        "No timing within 1-120 ms reliably updated telemetry."
                    )
                )
            else:
                msg = (
                    f"Minimal stable pulse detected at ~{suggested} ms.\n"
                    "Apply this value to BOT/custom timings for reliable updates."
                )
                self.after(0, lambda: messagebox.showinfo("Probe Result", msg))

        threading.Thread(target=_worker, daemon=True).start()

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = "normal" if enabled else "readonly"

        for row in self.preset_rows:
            try:
                row["entry"].config(state=state)
                if "voice_entry" in row:
                    row["voice_entry"].config(state=state)
            except Exception:
                pass

    def bind_game_key(self, direction: str):
        """Bind a game key for increase/decrease.

        Args:
            direction: "increase" or "decrease"
        """
        if self.app.app_state != "CONFIG":
            messagebox.showinfo("Notice", "Enter CONFIG mode first.")
            return

        self.app.focus_window()

        btn = self.btn_increase if direction == "increase" else self.btn_decrease
        original_text = btn["text"]
        btn.config(text="PRESS KEY...", bg="yellow")
        self.update_idletasks()

        scan_code, key_name = input_manager.capture_keyboard_scancode()

        if key_name == "CANCEL":
            if direction == "increase":
                self.controller.key_increase = None
            else:
                self.controller.key_decrease = None
            btn.config(text=original_text, bg="#f0f0f0")
        elif scan_code:
            if direction == "increase":
                self.controller.key_increase = scan_code
            else:
                self.controller.key_decrease = scan_code
            btn.config(text=f"OK: {key_name.upper()}", bg="#90ee90")
        else:
            btn.config(text=original_text, bg="#f0f0f0")

        self.app.schedule_save()

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

    def add_preset_row(
        self,
        existing: Optional[Dict[str, Any]] = None,
        is_reset: bool = False
    ):
        """Add a preset row to the UI."""
        frame = tk.Frame(self.presets_container)
        frame.pack(fill="x", pady=2)

        label_text = "RESET" if is_reset else "Macro"
        tk.Label(
            frame,
            text=label_text,
            width=6,
            anchor="w",
            fg="red" if is_reset else "black"
        ).pack(side="left")

        value_entry = ttk.Entry(frame, width=8)
        value_entry.pack(side="left", padx=5)

        if self.app.app_state != "CONFIG":
            value_entry.config(state="readonly")

        bind_button = tk.Button(frame, text="Set Bind", width=12)
        bind_button.pack(side="left", padx=5)

        voice_entry = ttk.Entry(frame, width=18)
        voice_entry.pack(side="left", padx=5)
        voice_entry.insert(0, "")
        if self.app.app_state != "CONFIG":
            voice_entry.config(state="readonly")

        row_data = {
            "frame": frame,
            "entry": value_entry,
            "bind": None,
            "is_reset": is_reset,
            "voice_entry": voice_entry
        }
        self._config_bind_button(bind_button, row_data)

        if existing:
            value_entry.config(state="normal")
            value_entry.delete(0, tk.END)
            value_entry.insert(0, existing.get("val", ""))
            if self.app.app_state != "CONFIG":
                value_entry.config(state="readonly")

            row_data["bind"] = existing.get("bind")
            if row_data["bind"]:
                bg_color = (
                    "#90ee90" if "JOY" in row_data["bind"] else "#ADD8E6"
                )
                bind_button.config(text=row_data["bind"], bg=bg_color)

            voice_text = existing.get("voice_phrase", "")
            voice_entry.config(state="normal")
            voice_entry.delete(0, tk.END)
            voice_entry.insert(0, voice_text)
            if self.app.app_state != "CONFIG":
                voice_entry.config(state="readonly")

        self.preset_rows.append(row_data)

    def monitor_loop(self):
        """Background loop to monitor current value."""
        if not self.running:
            return

        value = self.controller.read_telemetry()
        if value is None:
            text = "--"
        else:
            text = f"{value:.3f}" if self.controller.is_float else str(value)
        try:
            self.lbl_monitor.config(text=f"Current: {text}")
        except Exception:
            pass

        if self.running:
            self.after(500, self.monitor_loop)

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            "meta_var": self.controller.var_name,
            "meta_float": self.controller.is_float,
            "key_increase": self.controller.key_increase,
            "key_increase_text": self.btn_increase["text"],
            "key_decrease": self.controller.key_decrease,
            "key_decrease_text": self.btn_decrease["text"],
            "presets": [
                {
                    "val": row["entry"].get(),
                    "bind": row["bind"],
                    "is_reset": row.get("is_reset", False),
                    "voice_phrase": (
                        row.get("voice_entry").get() if row.get("voice_entry") else ""
                    )
                }
                for row in self.preset_rows
            ]
        }

    def destroy(self):  # type: ignore[override]
        """Ensure monitoring loop stops when widget is destroyed."""
        self.running = False
        super().destroy()

    def set_config(self, config: Dict[str, Any]):
        """Load configuration."""
        if not config:
            return

        # Set keys
        increase_key = config.get("key_increase")
        decrease_key = config.get("key_decrease")
        self.controller.key_increase = (
            int(increase_key) if increase_key is not None else None
        )
        self.controller.key_decrease = (
            int(decrease_key) if decrease_key is not None else None
        )

        self.btn_increase.config(text=config.get("key_increase_text", "Set Increase (+)"))
        self.btn_decrease.config(text=config.get("key_decrease_text", "Set Decrease (-)"))

        # Clear and rebuild preset rows
        for row in list(self.preset_rows):
            row["frame"].destroy()
        self.preset_rows.clear()

        saved_presets = config.get("presets", [])
        has_reset = any(p.get("is_reset") for p in saved_presets)

        if not has_reset:
            self.add_preset_row(is_reset=True)

        for preset in saved_presets:
            self.add_preset_row(
                existing=preset,
                is_reset=preset.get("is_reset", False)
            )

        while sum(1 for p in self.preset_rows if not p["is_reset"]) < 4:
            self.add_preset_row()
