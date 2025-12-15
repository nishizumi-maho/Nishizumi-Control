"""UI components for configuring the HUD overlay."""

from typing import Any, Dict, List, Tuple

import tkinter as tk
from tkinter import colorchooser

from dominant_control.config import DEFAULT_OVERLAY_FEEDBACK


class ScrollableFrame(tk.Frame):
    """
    Frame with vertical scrollbar.
    Use self.inner as the container for child widgets.
    """

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = tk.Frame(canvas)

        self.inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel support (bind only while hovered)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(_event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)


class OverlayConfigTab(tk.Frame):
    """
    Configuration tab for HUD overlay appearance and variable display.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.var_rows: Dict[str, Dict[str, Any]] = {}

        # Scrollable layout
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill="both", expand=True)
        self.body = scroll_frame.inner

        # Header
        tk.Label(
            self.body,
            text="HUD / Overlay Configuration",
            font=("Arial", 11, "bold")
        ).pack(anchor="w", pady=(5, 5))

        # Global appearance settings
        appearance_frame = tk.LabelFrame(self.body, text="Global HUD Appearance")
        appearance_frame.pack(fill="x", padx=5, pady=5)

        self.btn_bg = tk.Button(
            appearance_frame,
            text="Background Color",
            command=self.pick_background_color
        )
        self.btn_bg.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        self.lbl_bg_preview = tk.Label(
            appearance_frame,
            text="   ",
            bg=self.app.overlay.style_cfg.get("bg", "black"),
            relief="solid"
        )
        self.lbl_bg_preview.grid(row=0, column=1, padx=5, pady=5)

        self.btn_fg = tk.Button(
            appearance_frame,
            text="Text Color",
            command=self.pick_text_color
        )
        self.btn_fg.grid(row=1, column=0, padx=5, pady=5, sticky="w")

        self.lbl_fg_preview = tk.Label(
            appearance_frame,
            text="ABC",
            fg=self.app.overlay.style_cfg.get("fg", "white"),
            bg="gray",
            relief="solid",
        )
        self.lbl_fg_preview.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(appearance_frame, text="Font Size:").grid(
            row=2, column=0, padx=5, sticky="w"
        )
        self.scale_font = tk.Scale(
            appearance_frame,
            from_=8,
            to=24,
            orient="horizontal"
        )
        self.scale_font.set(self.app.overlay.style_cfg.get("font_size", 10))
        self.scale_font.grid(row=2, column=1, padx=5, pady=5, sticky="we")

        tk.Label(appearance_frame, text="Opacity:").grid(
            row=3, column=0, padx=5, sticky="w"
        )
        self.scale_opacity = tk.Scale(
            appearance_frame,
            from_=0.1,
            to=1.0,
            resolution=0.05,
            orient="horizontal"
        )
        self.scale_opacity.set(self.app.overlay.style_cfg.get("opacity", 0.85))
        self.scale_opacity.grid(row=3, column=1, padx=5, pady=5, sticky="we")

        for i in range(2):
            appearance_frame.columnconfigure(i, weight=1)

        feedback_frame = tk.LabelFrame(
            self.body, text="Assist Feedback Thresholds (per car)"
        )
        feedback_frame.pack(fill="x", padx=5, pady=5)

        tk.Checkbutton(
            feedback_frame,
            text="Show ABS / TC / slip hints on the HUD",
            variable=self.app.show_overlay_feedback,
            command=self._on_feedback_toggle,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=(4, 6))

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
            ("ABS active longer than (s)", "abs_hold_s"),
            ("TC active longer than (s)", "tc_hold_s"),
            ("Wheelspin slip (ratio)", "wheelspin_slip"),
            ("Wheelspin duration (s)", "wheelspin_hold_s"),
            ("Lock-up slip (negative value)", "lockup_slip"),
            ("Lock-up duration (s)", "lockup_hold_s"),
            ("Cooldown between alerts (s)", "cooldown_s"),
        ]

        for idx, (label, key) in enumerate(feedback_rows, start=1):
            tk.Label(feedback_frame, text=label).grid(
                row=idx, column=0, padx=5, pady=2, sticky="w"
            )
            entry = tk.Entry(feedback_frame, width=10, textvariable=self.feedback_vars[key])
            entry.grid(row=idx, column=1, padx=5, pady=2, sticky="w")
            entry.bind("<FocusOut>", self._on_feedback_change)
            entry.bind("<KeyRelease>", self._on_feedback_change)
            self.feedback_entries[key] = entry

        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())

        tk.Button(
            appearance_frame,
            text="Apply Style",
            command=self.apply_style,
            bg="#90ee90"
        ).grid(row=4, column=0, columnspan=2, sticky="we", padx=5, pady=(5, 5))

        # Variable selection (per-car)
        variables_frame = tk.LabelFrame(
            self.body,
            text="Variables to Display (per car)"
        )
        variables_frame.pack(fill="both", expand=True, padx=5, pady=5)

        header = tk.Frame(variables_frame)
        header.pack(fill="x", pady=(3, 3))

        tk.Label(header, text="Show", width=8, anchor="w").pack(
            side="left", padx=2
        )
        tk.Label(header, text="Internal Name", width=25, anchor="w").pack(
            side="left", padx=2
        )
        tk.Label(header, text="HUD Label", width=20, anchor="w").pack(
            side="left", padx=2
        )

        self.variables_list_frame = tk.Frame(variables_frame)
        self.variables_list_frame.pack(fill="both", expand=True)

        tk.Label(
            self.body,
            text="Variable selections and labels are saved per car.\n"
                 "Appearance settings apply to all cars.",
            fg="gray",
            font=("Arial", 8)
        ).pack(anchor="w", padx=5, pady=(3, 10))

    def pick_background_color(self):
        """Open color picker for background color."""
        color = colorchooser.askcolor(title="Background Color")[1]
        if color:
            self.app.overlay.style_cfg["bg"] = color
            self.lbl_bg_preview.config(bg=color)
            self.apply_style()

    def pick_text_color(self):
        """Open color picker for text color."""
        color = colorchooser.askcolor(title="Text Color")[1]
        if color:
            self.app.overlay.style_cfg["fg"] = color
            self.lbl_fg_preview.config(fg=color)
            self.apply_style()

    def apply_style(self):
        """Apply current style settings to overlay."""
        self.app.overlay.style_cfg["font_size"] = int(self.scale_font.get())
        self.app.overlay.style_cfg["opacity"] = float(self.scale_opacity.get())
        self.app.overlay.apply_style(self.app.overlay.style_cfg)
        self.app.save_config()

    def load_for_car(
        self,
        car_name: str,
        var_list: List[Tuple[str, bool]],
        overlay_config: Dict[str, Dict[str, Any]]
    ):
        """
        Load HUD configuration for a specific car.

        Args:
            car_name: Name of the car
            var_list: List of (var_name, is_float) tuples
            overlay_config: Dict of var_name -> {"show": bool, "label": str}
        """
        self._load_feedback_for_car(car_name)

        # Rebuild variable rows
        for child in self.variables_list_frame.winfo_children():
            child.destroy()
        self.var_rows.clear()

        # Ensure all variables have config entries
        for var_name, _is_float in var_list:
            if var_name not in overlay_config:
                overlay_config[var_name] = {
                    "show": False,
                    "label": var_name.replace("dc", "")
                }

        # Create UI rows
        for var_name, _is_float in var_list:
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
                config.get("label") or var_name.replace("dc", "")
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
        self.app.overlay.rebuild_monitor(overlay_config)
        self.app.save_config()

    def _on_feedback_change(self, *_args):
        """Persist feedback edits and save lazily."""

        car = self.app.current_car or "Generic Car"
        self._collect_feedback_for_car(car)
        self.app.schedule_save()
        self.app.sync_overlay_manager()

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
        label = row["entry"].get().strip() or var_name.replace("dc", "")
        config[var_name] = {"show": show, "label": label}
        self.app.car_overlay_config[car] = config
        self.app.overlay.rebuild_monitor(config)
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
            label = row_config["entry"].get().strip() or var_name.replace("dc", "")
            config[var_name] = {"show": show, "label": label}

        self.app.car_overlay_config[car_name] = config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(config)
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
