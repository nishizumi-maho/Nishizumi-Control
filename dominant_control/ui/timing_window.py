"""Timing configuration window for Dominant Control."""

from typing import Callable

import tkinter as tk
from tkinter import messagebox, ttk

from dominant_control.config import GLOBAL_TIMING


class GlobalTimingWindow(tk.Toplevel):
    """Window for configuring input timing profiles."""

    def __init__(self, parent, callback_save: Callable):
        super().__init__(parent)
        self.title("Timing Adjustments (Anti-Detection)")
        self.geometry("420x420")
        self.callback = callback_save

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        timing_frame = tk.Frame(notebook)
        notebook.add(timing_frame, text="Timing")

        # Profile selection
        profiles_frame = tk.LabelFrame(timing_frame, text="Behavior Profiles")
        profiles_frame.pack(fill="x", padx=10, pady=5)

        self.var_profile = tk.StringVar(
            value=GLOBAL_TIMING.get("profile", "aggressive")
        )

        tk.Radiobutton(
            profiles_frame,
            text="😈 Aggressive (fast, robotic)",
            variable=self.var_profile,
            value="aggressive",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🙂 Casual (more relaxed)",
            variable=self.var_profile,
            value="casual",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="😎 Relaxed (well-spaced)",
            variable=self.var_profile,
            value="relaxed",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🤖 BOT (experimental, near-zero delay)",
            variable=self.var_profile,
            value="bot",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🛠 Custom (define values below)",
            variable=self.var_profile,
            value="custom",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=(2, 5))

        # Custom settings
        self.custom_frame = tk.LabelFrame(
            timing_frame,
            text="Custom Settings (this profile only)"
        )
        self.custom_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(self.custom_frame, text="Press Min (ms):").grid(
            row=0, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_press_min = tk.Entry(self.custom_frame, width=8)
        self.entry_press_min.grid(row=0, column=1, padx=5, pady=2)
        self.entry_press_min.insert(
            0, str(GLOBAL_TIMING.get("press_min_ms", 60))
        )

        tk.Label(self.custom_frame, text="Press Max (ms):").grid(
            row=0, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_press_max = tk.Entry(self.custom_frame, width=8)
        self.entry_press_max.grid(row=0, column=3, padx=5, pady=2)
        self.entry_press_max.insert(
            0, str(GLOBAL_TIMING.get("press_max_ms", 80))
        )

        tk.Label(self.custom_frame, text="Interval Min (ms):").grid(
            row=1, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_interval_min = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_min.grid(row=1, column=1, padx=5, pady=2)
        self.entry_interval_min.insert(
            0, str(GLOBAL_TIMING.get("interval_min_ms", 60))
        )

        tk.Label(self.custom_frame, text="Interval Max (ms):").grid(
            row=1, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_interval_max = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_max.grid(row=1, column=3, padx=5, pady=2)
        self.entry_interval_max.insert(
            0, str(GLOBAL_TIMING.get("interval_max_ms", 90))
        )

        self.var_random = tk.BooleanVar(
            value=GLOBAL_TIMING.get("random_enabled", False)
        )
        self.check_random = tk.Checkbutton(
            self.custom_frame,
            text="Randomize (humanize)",
            variable=self.var_random,
            command=self._toggle_random
        )
        self.check_random.grid(
            row=2, column=0, columnspan=4, sticky="w", padx=5, pady=(5, 2)
        )

        tk.Label(self.custom_frame, text="Range (+/- ms):").grid(
            row=3, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_random_range = tk.Entry(self.custom_frame, width=8)
        self.entry_random_range.grid(row=3, column=1, padx=5, pady=2)
        self.entry_random_range.insert(
            0, str(GLOBAL_TIMING.get("random_range_ms", 10))
        )

        for i in range(4):
            self.custom_frame.columnconfigure(i, weight=1)

        # Save button
        tk.Button(
            self,
            text="💾 SAVE",
            command=self.save_all,
            bg="#90ee90",
            height=2
        ).pack(fill="x", padx=10, pady=10)

        self._on_profile_change()

    def _on_profile_change(self):
        """Handle profile selection change."""
        profile = self.var_profile.get()
        state = "normal" if profile == "custom" else "disabled"

        for widget in [
            self.entry_press_min,
            self.entry_press_max,
            self.entry_interval_min,
            self.entry_interval_max,
            self.check_random,
            self.entry_random_range
        ]:
            widget.config(state=state)

    def _toggle_random(self):
        """Handle randomization toggle."""
        state = (
            "normal"
            if self.var_random.get() and self.var_profile.get() == "custom"
            else "disabled"
        )
        self.entry_random_range.config(state=state)

    def save_all(self):
        """Save timing configuration."""
        profile = self.var_profile.get()
        GLOBAL_TIMING["profile"] = profile

        if profile == "custom":
            try:
                GLOBAL_TIMING["press_min_ms"] = int(
                    self.entry_press_min.get()
                )
                GLOBAL_TIMING["press_max_ms"] = int(
                    self.entry_press_max.get()
                )
                GLOBAL_TIMING["interval_min_ms"] = int(
                    self.entry_interval_min.get()
                )
                GLOBAL_TIMING["interval_max_ms"] = int(
                    self.entry_interval_max.get()
                )
                GLOBAL_TIMING["random_enabled"] = self.var_random.get()
                GLOBAL_TIMING["random_range_ms"] = int(
                    self.entry_random_range.get()
                )
            except ValueError:
                messagebox.showerror(
                    "Error",
                    "Please use numbers only in Custom mode."
                )
                return

        self.callback(GLOBAL_TIMING)
        self.destroy()
