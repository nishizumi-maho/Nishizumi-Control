"""UI dialog for selecting allowed USB devices."""

from typing import Callable, Dict, List

import tkinter as tk

from dominant_control.input_manager import input_manager


class DeviceSelector(tk.Toplevel):
    """Dialog for selecting which USB devices the application can use."""

    def __init__(self, parent, current_allowed: List[str], callback: Callable[[List[str]], None]):
        super().__init__(parent)
        self.title("Manage USB Devices")
        self.geometry("450x400")
        self.callback = callback

        tk.Label(
            self,
            text="Select which devices the application can use",
            font=("Arial", 10, "bold"),
            pady=10
        ).pack()

        tk.Label(
            self,
            text="Check/uncheck to allow/disallow device usage",
            fg="gray"
        ).pack()

        self.frame_list = tk.Frame(self)
        self.frame_list.pack(fill="both", expand=True, padx=10, pady=10)

        self.check_vars: Dict[str, tk.BooleanVar] = {}
        all_devices = input_manager.get_all_devices()

        for idx, name in all_devices:
            var = tk.BooleanVar()
            if current_allowed:
                var.set(name in current_allowed)
            else:
                # First run defaults to nothing selected
                var.set(False)

            chk = tk.Checkbutton(
                self.frame_list,
                text=name,
                variable=var,
                anchor="w",
            )
            chk.pack(fill="x")
            self.check_vars[name] = var

        tk.Button(
            self,
            text="Save and Apply",
            command=self.save,
            bg="#90ee90",
            height=2
        ).pack(fill="x", padx=10, pady=10)

    def save(self):
        """Save device selection and close dialog."""
        final_list = [name for name, var in self.check_vars.items() if var.get()]
        self.callback(final_list)
        self.destroy()
