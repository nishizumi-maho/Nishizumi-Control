from __future__ import annotations

from ..foundation import *
from .dialogs import *
from .hud import *

class OverlayConfigTab(tk.Frame):
    """
    Configuration tab for HUD overlay appearance and variable display.
    Modern styling with theme presets and advanced customization.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.var_rows: Dict[str, Dict[str, Any]] = {}

        # Scrollable layout
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        self.body = scroll_frame.inner

        # Header with icon
        header_frame = tk.Frame(self.body)
        header_frame.pack(anchor="w", pady=(8, 10), padx=5)

        tk.Label(
            header_frame,
            text="▸",
            font=("Segoe UI", 12),
            fg="#58a6ff"
        ).pack(side="left")

        tk.Label(
            header_frame,
            text="HUD / Overlay Configuration",
            font=("Segoe UI Semibold", 11)
        ).pack(side="left", padx=(4, 0))

        # === THEME PRESETS SECTION ===
        theme_frame = tk.LabelFrame(
            self.body,
            text="● Temas",
            font=("Segoe UI", 9, "bold")
        )
        theme_frame.pack(fill="x", padx=5, pady=(0, 8))

        theme_inner = tk.Frame(theme_frame)
        theme_inner.pack(fill="x", padx=5, pady=8)

        tk.Label(
            theme_inner,
            text="Theme:",
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 10))

        self.theme_var = tk.StringVar(
            value=self.app.overlay.style_cfg.get("theme", "midnight")
        )

        themes = [("Midnight", "midnight"), ("Neon", "neon"), ("Race", "racing")]
        for label, value in themes:
            rb = tk.Radiobutton(
                theme_inner,
                text=label,
                variable=self.theme_var,
                value=value,
                command=self._on_theme_change,
                font=("Segoe UI", 9)
            )
            rb.pack(side="left", padx=8)

        # === APPEARANCE SECTION ===
        appearance_frame = tk.LabelFrame(
            self.body,
            text="● Appearance",
            font=("Segoe UI", 9, "bold")
        )
        appearance_frame.pack(fill="x", padx=5, pady=5)

        # Row 0: Background color
        tk.Label(
            appearance_frame,
            text="Background:",
            font=("Segoe UI", 9)
        ).grid(row=0, column=0, padx=8, pady=6, sticky="w")

        self.lbl_bg_preview = tk.Label(
            appearance_frame,
            text="     ",
            bg=self.app.overlay.style_cfg.get("bg", "#0d1117"),
            relief="solid",
            width=4
        )
        self.lbl_bg_preview.grid(row=0, column=1, padx=5, pady=6)

        self.btn_bg = tk.Button(
            appearance_frame,
            text="Choose",
            command=self.pick_background_color,
            font=("Segoe UI", 8),
            width=8
        )
        self.btn_bg.grid(row=0, column=2, padx=5, pady=6, sticky="w")

        # Row 1: Text color
        tk.Label(
            appearance_frame,
            text="Text color:",
            font=("Segoe UI", 9)
        ).grid(row=1, column=0, padx=8, pady=6, sticky="w")

        self.lbl_fg_preview = tk.Label(
            appearance_frame,
            text=" Aa ",
            fg=self.app.overlay.style_cfg.get("fg", "#58a6ff"),
            bg="#1a1a1a",
            relief="solid",
            font=("Segoe UI", 9, "bold")
        )
        self.lbl_fg_preview.grid(row=1, column=1, padx=5, pady=6)

        self.btn_fg = tk.Button(
            appearance_frame,
            text="Choose",
            command=self.pick_text_color,
            font=("Segoe UI", 8),
            width=8
        )
        self.btn_fg.grid(row=1, column=2, padx=5, pady=6, sticky="w")

        # Row 2: Font size
        tk.Label(
            appearance_frame,
            text="Font size:",
            font=("Segoe UI", 9)
        ).grid(row=2, column=0, padx=8, pady=4, sticky="w")

        self.scale_font = tk.Scale(
            appearance_frame,
            from_=8,
            to=18,
            orient="horizontal",
            length=150,
            showvalue=True
        )
        self.scale_font.set(self.app.overlay.style_cfg.get("font_size", 11))
        self.scale_font.grid(row=2, column=1, columnspan=2, padx=5, pady=4, sticky="w")

        # Row 3: Opacity
        tk.Label(
            appearance_frame,
            text="Opacity:",
            font=("Segoe UI", 9)
        ).grid(row=3, column=0, padx=8, pady=4, sticky="w")

        self.scale_opacity = tk.Scale(
            appearance_frame,
            from_=0.5,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=150,
            showvalue=True
        )
        self.scale_opacity.set(self.app.overlay.style_cfg.get("opacity", 0.95))
        self.scale_opacity.grid(row=3, column=1, columnspan=2, padx=5, pady=4, sticky="w")

        # === SIZE SECTION ===
        size_frame = tk.LabelFrame(
            self.body,
            text="● Dimensions",
            font=("Segoe UI", 9, "bold")
        )
        size_frame.pack(fill="x", padx=5, pady=5)

        # Width
        tk.Label(
            size_frame,
            text="Width:",
            font=("Segoe UI", 9)
        ).grid(row=0, column=0, padx=8, pady=4, sticky="w")

        self.scale_width = tk.Scale(
            size_frame,
            from_=200,
            to=600,
            orient="horizontal",
            length=180
        )
        self.scale_width.set(self.app.overlay.style_cfg.get("width", 340))
        self.scale_width.grid(row=0, column=1, padx=5, pady=4, sticky="w")

        # Height
        tk.Label(
            size_frame,
            text="Height:",
            font=("Segoe UI", 9)
        ).grid(row=1, column=0, padx=8, pady=4, sticky="w")

        self.scale_height = tk.Scale(
            size_frame,
            from_=120,
            to=500,
            orient="horizontal",
            length=180
        )
        self.scale_height.set(self.app.overlay.style_cfg.get("height", 220))
        self.scale_height.grid(row=1, column=1, padx=5, pady=4, sticky="w")

        # === EFFECTS SECTION ===
        effects_frame = tk.LabelFrame(
            self.body,
            text="● Efeitos visuais",
            font=("Segoe UI", 9, "bold")
        )
        effects_frame.pack(fill="x", padx=5, pady=5)

        self.glow_var = tk.BooleanVar(
            value=self.app.overlay.style_cfg.get("glow_enabled", True)
        )
        tk.Checkbutton(
            effects_frame,
            text="Enable glow on the progress bars",
            variable=self.glow_var,
            command=self._on_effects_change,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=8, pady=4)

        self.graphs_var = tk.BooleanVar(
            value=self.app.overlay.style_cfg.get("show_graphs", True)
        )
        tk.Checkbutton(
            effects_frame,
            text="Show progress bars",
            variable=self.graphs_var,
            command=self._on_effects_change,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=8, pady=4)

        # Apply button
        tk.Button(
            self.body,
            text="▸ Apply Style",
            command=self.apply_style,
            font=("Segoe UI Semibold", 9),
            bg="#238636",
            fg="white",
            activebackground="#2ea043",
            activeforeground="white",
            relief="flat",
            padx=20,
            pady=6
        ).pack(pady=(10, 5))

        for i in range(3):
            appearance_frame.columnconfigure(i, weight=1)
            size_frame.columnconfigure(i, weight=1)

        # === ASSIST FEEDBACK SECTION ===
        feedback_frame = tk.LabelFrame(
            self.body,
            text="● Assist feedback (per car)",
            font=("Segoe UI", 9, "bold")
        )
        feedback_frame.pack(fill="x", padx=5, pady=5)

        tk.Checkbutton(
            feedback_frame,
            text="Show ABS / TC / slip hints on the HUD",
            variable=self.app.show_overlay_feedback,
            command=self._on_feedback_toggle,
            font=("Segoe UI", 9)
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 8))

        self.feedback_vars = {
            "abs_hold_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["abs_hold_s"]),
            "tc_hold_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["tc_hold_s"]),
            "wheelspin_slip": tk.DoubleVar(
                value=DEFAULT_OVERLAY_FEEDBACK["wheelspin_slip"]
            ),
            "wheelspin_hold_s": tk.DoubleVar(
                value=DEFAULT_OVERLAY_FEEDBACK["wheelspin_hold_s"]
            ),
            "lockup_slip": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["lockup_slip"]),
            "lockup_hold_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["lockup_hold_s"]),
            "cooldown_s": tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK["cooldown_s"]),
        }
        self.feedback_entries: Dict[str, tk.Entry] = {}

        feedback_rows = [
            ("ABS active (s)", "abs_hold_s"),
            ("TC active (s)", "tc_hold_s"),
            ("Minimum wheelspin", "wheelspin_slip"),
            ("Wheelspin duration (s)", "wheelspin_hold_s"),
            ("Minimum lock-up", "lockup_slip"),
            ("Lock-up duration (s)", "lockup_hold_s"),
            ("Interval between alerts (s)", "cooldown_s"),
        ]

        for idx, (label, key) in enumerate(feedback_rows, start=1):
            tk.Label(
                feedback_frame,
                text=label,
                font=("Segoe UI", 9)
            ).grid(row=idx, column=0, padx=8, pady=2, sticky="w")
            entry = tk.Entry(
                feedback_frame,
                width=8,
                textvariable=self.feedback_vars[key],
                font=("Segoe UI", 9)
            )
            entry.grid(row=idx, column=1, padx=5, pady=2, sticky="w")
            entry.bind("<FocusOut>", self._on_feedback_change)
            entry.bind("<KeyRelease>", self._on_feedback_change)
            self.feedback_entries[key] = entry

        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())

        # === VARIABLE SELECTION SECTION ===
        variables_frame = tk.LabelFrame(
            self.body,
            text="● Variables to show (per car)",
            font=("Segoe UI", 9, "bold")
        )
        variables_frame.pack(fill="both", expand=True, padx=5, pady=5)

        header = tk.Frame(variables_frame)
        header.pack(fill="x", pady=(6, 4), padx=5)

        tk.Label(
            header,
            text="Show",
            width=6,
            anchor="w",
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=2)
        tk.Label(
            header,
            text="Variable",
            width=22,
            anchor="w",
            font=("Segoe UI", 9, "bold")
        ).pack(
            side="left", padx=2
        )
        tk.Label(
            header,
            text="HUD label",
            width=18,
            anchor="w",
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=2)

        self.variables_list_frame = tk.Frame(variables_frame)
        self.variables_list_frame.pack(fill="both", expand=True, padx=5)

        # Footer note
        note_frame = tk.Frame(self.body)
        note_frame.pack(anchor="w", padx=8, pady=(8, 12))

        tk.Label(
            note_frame,
            text="ℹ",
            font=("Segoe UI", 9),
            fg="#58a6ff"
        ).pack(side="left")

        tk.Label(
            note_frame,
            text=" The variables are saved per car. The theme is global.",
            fg="#8b949e",
            font=("Segoe UI", 8)
        ).pack(side="left")

    def _on_theme_change(self):
        """Handle theme selection change."""
        theme_name = self.theme_var.get()
        if theme_name in OverlayWindow.THEMES:
            theme = OverlayWindow.THEMES[theme_name]
            self.app.overlay.style_cfg.update(theme)
            self.app.overlay.style_cfg["theme"] = theme_name

            # Update preview widgets
            self.lbl_bg_preview.config(bg=theme.get("bg", "#0d1117"))
            self.lbl_fg_preview.config(fg=theme.get("fg", "#58a6ff"))

            self.apply_style()

    def _on_effects_change(self):
        """Handle visual effects toggle."""
        self.app.overlay.style_cfg["glow_enabled"] = self.glow_var.get()
        self.app.overlay.style_cfg["show_graphs"] = self.graphs_var.get()
        self.apply_style()

        # Rebuild monitor to apply graph visibility
        car = self.app.current_car or "Generic Car"
        config = self.app.car_overlay_config.get(car, {})
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(config))

    def pick_background_color(self):
        """Open color picker for background color."""
        current = self.app.overlay.style_cfg.get("bg", "#0d1117")
        color = colorchooser.askcolor(title="Background Color", initialcolor=current)[1]
        if color:
            self.app.overlay.style_cfg["bg"] = color
            self.lbl_bg_preview.config(bg=color)
            self.apply_style()

    def pick_text_color(self):
        """Open color picker for text color."""
        current = self.app.overlay.style_cfg.get("fg", "#58a6ff")
        color = colorchooser.askcolor(title="Text Color", initialcolor=current)[1]
        if color:
            self.app.overlay.style_cfg["fg"] = color
            self.lbl_fg_preview.config(fg=color)
            self.apply_style()

    def apply_style(self):
        """Apply current style settings to overlay."""
        self.app.overlay.style_cfg["font_size"] = int(self.scale_font.get())
        self.app.overlay.style_cfg["opacity"] = float(self.scale_opacity.get())
        self.app.overlay.style_cfg["width"] = int(self.scale_width.get())
        self.app.overlay.style_cfg["height"] = int(self.scale_height.get())
        self.app.overlay.style_cfg["glow_enabled"] = self.glow_var.get()
        self.app.overlay.style_cfg["show_graphs"] = self.graphs_var.get()
        self.app.overlay.apply_style(self.app.overlay.style_cfg)
        self.app.save_config()

    @staticmethod
    def _default_overlay_label(var_name: str) -> str:
        """Return the default label for an overlay variable."""
        return format_driver_control_name(var_name)

    def load_for_car(
        self, 
        car_name: str, 
        var_list: List[Tuple[str, bool, bool]], 
        overlay_config: Dict[str, Dict[str, Any]]
    ):
        """
        Load HUD configuration for a specific car.
        
        Args:
            car_name: Name of the car
            var_list: List of (var_name, is_float, is_boolean) tuples
            overlay_config: Dict of var_name -> {"show": bool, "label": str}
        """
        self._load_feedback_for_car(car_name)

        # Rebuild variable rows
        for child in self.variables_list_frame.winfo_children():
            child.destroy()
        self.var_rows.clear()

        # Ensure all variables have config entries
        for entry in var_list:
            var_name, _is_float, _is_boolean = _normalize_var_tuple(entry)
            if var_name not in overlay_config:
                default_label = self._default_overlay_label(var_name)
                overlay_config[var_name] = {
                    "show": False,
                    "label": default_label
                }

        # Create UI rows
        for entry in var_list:
            var_name, _is_float, _is_boolean = _normalize_var_tuple(entry)
            config = overlay_config.get(var_name, {})

            row = tk.Frame(self.variables_list_frame)
            row.pack(fill="x", pady=2)

            show_var = tk.BooleanVar(value=config.get("show", False))
            checkbox = tk.Checkbutton(row, variable=show_var)
            checkbox.pack(side="left", padx=2)

            tk.Label(row, text=var_name, width=25, anchor="w").pack(
                side="left", padx=2
            )

            label_entry = tk.Entry(row, width=20)
            label_entry.pack(side="left", padx=2)
            label_entry.insert(
                0,
                config.get("label")
                or self._default_overlay_label(var_name)
            )

            self.var_rows[var_name] = {
                "show_var": show_var,
                "entry": label_entry
            }

            show_var.trace_add(
                "write",
                lambda *_args, vn=var_name: self._on_overlay_row_change(vn)
            )
            label_entry.bind(
                "<KeyRelease>",
                lambda _event, vn=var_name: self._on_overlay_row_change(vn)
            )

        self.app.car_overlay_config[car_name] = overlay_config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(
            self.app._overlay_display_config(overlay_config)
        )
        self.app.save_config()

    def _on_feedback_change(self, *_args):
        """Persist feedback edits and save lazily."""

        car = self.app.current_car or "Generic Car"
        self._collect_feedback_for_car(car)
        self.app.schedule_save()

    def _on_feedback_toggle(self):
        """Enable or disable assist hints and persist the preference."""

        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())
        self._on_feedback_change()

    def _set_feedback_fields_enabled(self, enabled: bool) -> None:
        """Toggle entry state for assist thresholds."""

        state = "normal" if enabled else "disabled"
        for entry in self.feedback_entries.values():
            try:
                entry.config(state=state)
            except Exception:
                continue

    def _on_overlay_row_change(self, var_name: str):
        """Apply live updates when overlay rows change."""
        car = self.app.current_car or "Generic Car"
        config = self.app.car_overlay_config.get(car, {})
        row = self.var_rows.get(var_name)
        if not row:
            return

        show = row["show_var"].get()
        default_label = self._default_overlay_label(var_name)
        label = row["entry"].get().strip() or default_label
        config[var_name] = {"show": show, "label": label}
        self.app.car_overlay_config[car] = config
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(config))
        self.app.schedule_save()

    def collect_for_car(self, car_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Collect current HUD configuration for a car.
        
        Args:
            car_name: Name of the car
            
        Returns:
            Dict of var_name -> {"show": bool, "label": str}
        """
        config = self.app.car_overlay_config.get(car_name, {})

        for var_name, row_config in self.var_rows.items():
            show = row_config["show_var"].get()
            default_label = self._default_overlay_label(var_name)
            label = row_config["entry"].get().strip() or default_label
            config[var_name] = {"show": show, "label": label}

        self.app.car_overlay_config[car_name] = config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(config))
        return config

    def _load_feedback_for_car(self, car_name: str) -> None:
        """Load per-car feedback thresholds into the UI fields."""

        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(self.app.car_overlay_feedback.get(car_name, {}))

        for key, var in self.feedback_vars.items():
            try:
                var.set(float(cfg.get(key, DEFAULT_OVERLAY_FEEDBACK[key])))
            except Exception:
                var.set(DEFAULT_OVERLAY_FEEDBACK[key])

        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())

    def _collect_feedback_for_car(self, car_name: str) -> Dict[str, float]:
        """Persist feedback thresholds from UI fields for a car."""

        cfg: Dict[str, float] = DEFAULT_OVERLAY_FEEDBACK.copy()
        for key, var in self.feedback_vars.items():
            try:
                cfg[key] = float(var.get())
            except Exception:
                cfg[key] = DEFAULT_OVERLAY_FEEDBACK[key]

        self.app.car_overlay_feedback[car_name] = cfg
        return cfg


__all__ = ['OverlayConfigTab']
