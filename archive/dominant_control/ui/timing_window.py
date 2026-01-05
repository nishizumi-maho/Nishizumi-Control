"""Timing configuration window for Dominant Control."""

from typing import Callable

import tkinter as tk
from tkinter import messagebox, ttk

from dominant_control.config import DEFAULT_TIMING_PROFILES, GLOBAL_TIMING
from dominant_control.input_engine import _normalize_timing_config


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

        # Profile settings
        self.custom_frame = tk.LabelFrame(
            timing_frame,
            text="Profile Settings"
        )
        self.custom_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(self.custom_frame, text="Press Min (ms):").grid(
            row=0, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_press_min = tk.Entry(self.custom_frame, width=8)
        self.entry_press_min.grid(row=0, column=1, padx=5, pady=2)

        tk.Label(self.custom_frame, text="Press Max (ms):").grid(
            row=0, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_press_max = tk.Entry(self.custom_frame, width=8)
        self.entry_press_max.grid(row=0, column=3, padx=5, pady=2)

        tk.Label(self.custom_frame, text="Interval Min (ms):").grid(
            row=1, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_interval_min = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_min.grid(row=1, column=1, padx=5, pady=2)

        tk.Label(self.custom_frame, text="Interval Max (ms):").grid(
            row=1, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_interval_max = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_max.grid(row=1, column=3, padx=5, pady=2)

        self.var_random = tk.BooleanVar()
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

        self.var_customize = tk.BooleanVar()
        self.check_customize = tk.Checkbutton(
            self.custom_frame,
            text="Customize this profile",
            variable=self.var_customize,
            command=self._toggle_customize
        )
        self.check_customize.grid(
            row=4, column=0, columnspan=2, sticky="w", padx=5, pady=(5, 2)
        )

        tk.Button(
            self.custom_frame,
            text="Reset to Defaults",
            command=self._reset_profile_defaults
        ).grid(row=4, column=2, columnspan=2, sticky="e", padx=5, pady=(5, 2))

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
        timing_cfg = _normalize_timing_config(GLOBAL_TIMING)
        customized = timing_cfg.get("profile_customized", {}).get(profile, False)
        defaults = DEFAULT_TIMING_PROFILES.get(profile, {})
        settings = timing_cfg.get("profile_settings", {}).get(profile, defaults)
        values = settings if customized else defaults

        self.var_customize.set(customized)
        self._set_entry_value(self.entry_press_min, values.get("press_min_ms", 0))
        self._set_entry_value(self.entry_press_max, values.get("press_max_ms", 0))
        self._set_entry_value(
            self.entry_interval_min, values.get("interval_min_ms", 0)
        )
        self._set_entry_value(
            self.entry_interval_max, values.get("interval_max_ms", 0)
        )
        self.var_random.set(values.get("random_enabled", False))
        self._set_entry_value(
            self.entry_random_range, values.get("random_range_ms", 0)
        )

        self._toggle_customize()

    def _set_entry_value(self, entry: tk.Entry, value: int) -> None:
        entry.delete(0, tk.END)
        entry.insert(0, str(value))

    def _toggle_customize(self):
        """Enable customization only when checked."""
        enabled = self.var_customize.get()
        state = "normal" if enabled else "disabled"

        for widget in [
            self.entry_press_min,
            self.entry_press_max,
            self.entry_interval_min,
            self.entry_interval_max,
            self.check_random,
            self.entry_random_range,
        ]:
            widget.config(state=state)

        if not enabled:
            self._apply_default_values()

        self._toggle_random()

    def _apply_default_values(self) -> None:
        profile = self.var_profile.get()
        defaults = DEFAULT_TIMING_PROFILES.get(profile, {})
        self._set_entry_value(self.entry_press_min, defaults.get("press_min_ms", 0))
        self._set_entry_value(self.entry_press_max, defaults.get("press_max_ms", 0))
        self._set_entry_value(
            self.entry_interval_min, defaults.get("interval_min_ms", 0)
        )
        self._set_entry_value(
            self.entry_interval_max, defaults.get("interval_max_ms", 0)
        )
        self.var_random.set(defaults.get("random_enabled", False))
        self._set_entry_value(
            self.entry_random_range, defaults.get("random_range_ms", 0)
        )

    def _toggle_random(self):
        """Handle randomization toggle."""
        state = (
            "normal"
            if self.var_random.get() and self.var_customize.get()
            else "disabled"
        )
        self.entry_random_range.config(state=state)

    def _reset_profile_defaults(self):
        """Reset current profile to default values."""
        self.var_customize.set(False)
        self._apply_default_values()
        self._toggle_customize()

    def save_all(self):
        """Save timing configuration."""
        profile = self.var_profile.get()
        GLOBAL_TIMING["profile"] = profile

        customized = self.var_customize.get()
        GLOBAL_TIMING.setdefault("profile_customized", {})
        GLOBAL_TIMING.setdefault("profile_settings", {})
        GLOBAL_TIMING["profile_customized"][profile] = customized

        settings = DEFAULT_TIMING_PROFILES.get(profile, {}).copy()
        if customized:
            try:
                settings["press_min_ms"] = int(self.entry_press_min.get())
                settings["press_max_ms"] = int(self.entry_press_max.get())
                settings["interval_min_ms"] = int(
                    self.entry_interval_min.get()
                )
                settings["interval_max_ms"] = int(
                    self.entry_interval_max.get()
                )
                settings["random_enabled"] = self.var_random.get()
                settings["random_range_ms"] = int(
                    self.entry_random_range.get()
                )
            except ValueError:
                messagebox.showerror(
                    "Error",
                    "Please use numbers only in Customize mode."
                )
                return

        GLOBAL_TIMING["profile_settings"][profile] = settings

        self.callback(GLOBAL_TIMING)
        self.destroy()
