from __future__ import annotations

from ..foundation import *
from .dialogs import *
from .hud import *

class GlobalTimingWindow(tk.Toplevel):
    """
    Window for configuring input timing profiles.
    """

    def __init__(
        self,
        parent,
        callback_save: Callable,
        popups_enabled: Optional[Callable[[], bool]] = None
    ):
        super().__init__(parent)
        self.title("Timing Settings")
        self.geometry("420x420")
        self.callback = callback_save
        self.popups_enabled = popups_enabled
        self._profile_initialized = False

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        timing_frame = tk.Frame(notebook)
        notebook.add(timing_frame, text="Timing")

        # Profile selection
        profiles_frame = tk.LabelFrame(timing_frame, text="Behavior Profiles")
        profiles_frame.pack(fill="x", padx=10, pady=5)

        self.var_profile = tk.StringVar(
            value=GLOBAL_TIMING.get("profile", "bot")
        )

        tk.Radiobutton(
            profiles_frame,
            text="⭐ CLAUDE (measured: the fastest that lands 100%)",
            variable=self.var_profile,
            value="claude",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Label(
            profiles_frame,
            text=(
                f'     {CLAUDE_PRESS_MS}/{CLAUDE_INTERVAL_MS} ms — the simulator takes 1 adjustment per tick of {CLAUDE_TICK_MS:.1f} ms;\n     pulsing faster only throws commands away. It corrects overshoot\n     and waits while the control is busy.'
            ),
            font=("Segoe UI", 8),
            fg="#5f6b7a",
            justify="left"
        ).pack(anchor="w", padx=5, pady=(0, 4))

        tk.Radiobutton(
            profiles_frame,
            text="🤖 BOT (experimental, almost no delay)",
            variable=self.var_profile,
            value="bot",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🤖 Steady BOT (fast, more reliable)",
            variable=self.var_profile,
            value="bot_safe",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="😈 Aggressive (fast, robotic)",
            variable=self.var_profile,
            value="aggressive",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🙂 Casual (calmer)",
            variable=self.var_profile,
            value="casual",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="😎 Relaxed (well spaced)",
            variable=self.var_profile,
            value="relaxed",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=2)

        tk.Radiobutton(
            profiles_frame,
            text="🛠 Custom (set the values below)",
            variable=self.var_profile,
            value="custom",
            command=self._on_profile_change
        ).pack(anchor="w", padx=5, pady=(2, 5))

        legacy_ms, current_ms, savings_ms = _bot_legacy_savings_ms(16)
        tk.Label(
            profiles_frame,
            text=(
                f'BOT macro estimate over 16 steps: {current_ms:.0f} ms (was {legacy_ms:.0f} ms; gain of {savings_ms:.0f} ms)'
            ),
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=5, pady=(0, 4))

        boundary_frame = tk.LabelFrame(
            timing_frame,
            text="BOT limit pulses (min./max.)"
        )
        boundary_frame.pack(fill="x", padx=10, pady=(5, 10))

        tk.Label(boundary_frame, text="Press (ms):").grid(
            row=0, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_boundary_press = tk.Entry(boundary_frame, width=8)
        self.entry_boundary_press.grid(row=0, column=1, padx=5, pady=2)
        self.entry_boundary_press.insert(
            0, str(GLOBAL_TIMING.get("boundary_press_ms", 6))
        )

        tk.Label(boundary_frame, text="Interval (ms):").grid(
            row=0, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_boundary_interval = tk.Entry(boundary_frame, width=8)
        self.entry_boundary_interval.grid(row=0, column=3, padx=5, pady=2)
        self.entry_boundary_interval.insert(
            0, str(GLOBAL_TIMING.get("boundary_interval_ms", 6))
        )

        self.lbl_boundary_rate = tk.Label(
            boundary_frame,
            text="Approx. -- pulses/s",
            font=("Segoe UI", 9)
        )
        self.lbl_boundary_rate.grid(
            row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(2, 4)
        )

        for widget in (self.entry_boundary_press, self.entry_boundary_interval):
            widget.bind("<KeyRelease>", self._update_boundary_rate_label)
            widget.bind("<FocusOut>", self._update_boundary_rate_label)

        for i in range(4):
            boundary_frame.columnconfigure(i, weight=1)

        # Custom settings
        self.custom_frame = tk.LabelFrame(
            timing_frame, 
            text="Custom Settings (this profile only)"
        )
        self.custom_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(self.custom_frame, text="Min. press (ms):").grid(
            row=0, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_press_min = tk.Entry(self.custom_frame, width=8)
        self.entry_press_min.grid(row=0, column=1, padx=5, pady=2)
        self.entry_press_min.insert(
            0, str(GLOBAL_TIMING.get("press_min_ms", 60))
        )

        tk.Label(self.custom_frame, text="Max. press (ms):").grid(
            row=0, column=2, sticky="w", padx=5, pady=2
        )
        self.entry_press_max = tk.Entry(self.custom_frame, width=8)
        self.entry_press_max.grid(row=0, column=3, padx=5, pady=2)
        self.entry_press_max.insert(
            0, str(GLOBAL_TIMING.get("press_max_ms", 80))
        )

        tk.Label(self.custom_frame, text="Min. interval (ms):").grid(
            row=1, column=0, sticky="w", padx=5, pady=2
        )
        self.entry_interval_min = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_min.grid(row=1, column=1, padx=5, pady=2)
        self.entry_interval_min.insert(
            0, str(GLOBAL_TIMING.get("interval_min_ms", 60))
        )

        tk.Label(self.custom_frame, text="Max. interval (ms):").grid(
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
            text="Aleatorizar (humanizar)",
            variable=self.var_random,
            command=self._toggle_random
        )
        self.check_random.grid(
            row=2, column=0, columnspan=4, sticky="w", padx=5, pady=(5, 2)
        )

        tk.Label(self.custom_frame, text="Jitter (+/- ms):").grid(
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

        self.update_idletasks()
        min_width = max(420, self.winfo_reqwidth())
        min_height = self.winfo_reqheight()
        self.minsize(min_width, min_height)
        self.geometry(f"{min_width}x{min_height}")

        self._on_profile_change()
        self._update_boundary_rate_label()
        self._profile_initialized = True

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

        if self._profile_initialized and profile != "custom":
            GLOBAL_TIMING["profile"] = profile
            self.callback(GLOBAL_TIMING)

    def _update_boundary_rate_label(self, _event: Optional[tk.Event] = None):
        """Update the boundary pulse rate preview."""
        try:
            press_ms = int(self.entry_boundary_press.get())
            interval_ms = int(self.entry_boundary_interval.get())
            rate_hz = _pulse_rate_hz(
                max(1, press_ms),
                max(1, interval_ms)
            )
            label = f'Approx. {rate_hz:.1f} pulses/s'
        except ValueError:
            label = "Approx. -- pulses/s"
        self.lbl_boundary_rate.config(text=label)

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

        try:
            GLOBAL_TIMING["boundary_press_ms"] = int(
                self.entry_boundary_press.get()
            )
            GLOBAL_TIMING["boundary_interval_ms"] = int(
                self.entry_boundary_interval.get()
            )
        except ValueError:
            if self.popups_enabled is None or self.popups_enabled():
                messagebox.showerror(
                    "Error",
                    "Use numbers only in the BOT limit pulses."
                )
            return

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
                if self.popups_enabled is None or self.popups_enabled():
                    messagebox.showerror(
                        "Error", 
                        "Use numbers only in Custom mode."
                    )
                return

        self.callback(GLOBAL_TIMING)
        self.destroy()


__all__ = ['GlobalTimingWindow']
