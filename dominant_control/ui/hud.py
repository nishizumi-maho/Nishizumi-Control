from __future__ import annotations

from ..foundation import *

class OverlayWindow(tk.Toplevel):
    """
    Premium HUD overlay with modern glass-morphism design,
    real-time telemetry visualization, and smooth animations.
    """

    # Modern icon set with better visual appeal
    ICONS = {
        "brake": "◉",      # Brake bias
        "fuel": "◈",       # Fuel
        "tc": "◎",         # Traction Control
        "abs": "◇",        # ABS
        "diff": "⬡",       # Differential
        "roll": "◫",       # Roll
        "weight": "◆",     # Weight
        "lap": "◐",        # Lap distance
        "speed": "▸",      # Speed
        "gear": "⬢",       # Gear
        "rpm": "◉",        # RPM
        "temp": "◈",       # Temperature
        "default": "▪",    # Default
        "status_ok": "●",
        "status_warn": "◐",
        "status_error": "○",
        "status_scan": "◑",
    }

    # Premium color palettes
    THEMES = {
        "midnight": {
            "bg": "#0d1117",
            "bg_secondary": "#161b22",
            "fg": "#58a6ff",
            "fg_secondary": "#8b949e",
            "accent": "#238636",
            "accent_glow": "#2ea043",
            "warning": "#d29922",
            "danger": "#f85149",
            "success": "#3fb950",
            "border": "#30363d",
            "highlight": "#1f6feb",
        },
        "neon": {
            "bg": "#0a0a0f",
            "bg_secondary": "#12121a",
            "fg": "#00ffc8",
            "fg_secondary": "#7a8899",
            "accent": "#00aaff",
            "accent_glow": "#00ddff",
            "warning": "#ffcc00",
            "danger": "#ff3366",
            "success": "#00ff88",
            "border": "#2a2a3a",
            "highlight": "#6366f1",
        },
        "racing": {
            "bg": "#0c0c0c",
            "bg_secondary": "#1a1a1a",
            "fg": "#ff4444",
            "fg_secondary": "#888888",
            "accent": "#ff6b35",
            "accent_glow": "#ff8c00",
            "warning": "#ffd700",
            "danger": "#dc143c",
            "success": "#32cd32",
            "border": "#333333",
            "highlight": "#ff4500",
        },
    }
    COMPACT_HEIGHT = 72
    CAPTURE_EXTRA_HEIGHT = 142
    CAPTURE_MIN_WIDTH = 340

    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.wm_attributes("-alpha", 0.95)
        self.wm_attributes("-transparentcolor", "")

        self._pos_x = 50
        self._pos_y = 50
        apply_app_icon(self)

        # Color cache for performance
        self._color_cache: Dict[str, str] = {}

        # Animation state
        self._pulse_state = 0
        self._last_status_color = None
        self._lapdist_capture_visible = False
        self._turbo_pit_enabled = True

        # Modern premium style configuration
        self.style_cfg = {
            "bg": "#0d1117",
            "bg_secondary": "#161b22",
            "fg": "#58a6ff",
            "fg_secondary": "#8b949e",
            "accent": "#238636",
            "accent_glow": "#2ea043",
            "warning": "#d29922",
            "danger": "#f85149",
            "success": "#3fb950",
            "border": "#30363d",
            "highlight": "#1f6feb",
            "font_size": 11,
            "opacity": 0.95,
            "width": 340,
            "height": 220,
            "corner_radius": 12,
            "show_graphs": True,
            "compact_mode": False,
            "theme": "midnight",
            "glow_enabled": True,
            "animations_enabled": True,
        }

        self.geometry(
            f"{self.style_cfg['width']}x{self.style_cfg['height']}"
            f"+{self._pos_x}+{self._pos_y}"
        )
        self.configure(bg=self.style_cfg["bg"])

        # Premium container with subtle border glow
        self.main_container = tk.Frame(
            self,
            bg=self.style_cfg["bg"],
            highlightthickness=1,
            highlightbackground=self.style_cfg["border"],
            highlightcolor=self.style_cfg["accent"]
        )
        self.main_container.pack(fill="both", expand=True, padx=2, pady=2)

        # === PREMIUM HEADER ===
        self.frame_header = tk.Frame(
            self.main_container,
            bg=self.style_cfg["bg_secondary"],
            height=44
        )
        self.frame_header.pack(fill="x", pady=(0, 0))
        self.frame_header.pack_propagate(False)

        # Left section: Icon + Title
        self.header_left = tk.Frame(self.frame_header, bg=self.style_cfg["bg_secondary"])
        self.header_left.pack(side="left", fill="y")

        # Modern status LED with glow effect
        self.status_canvas = tk.Canvas(
            self.header_left,
            width=24,
            height=24,
            bg=self.style_cfg["bg_secondary"],
            highlightthickness=0
        )
        self.status_canvas.pack(side="left", padx=(10, 6), pady=10)

        # Draw LED with outer glow
        self._draw_status_led(self.style_cfg["success"])

        # Premium title with icon
        self.lbl_title = tk.Label(
            self.header_left,
            text="▸ DRIVER HUD",
            fg=self.style_cfg["fg"],
            bg=self.style_cfg["bg_secondary"],
            font=("Segoe UI Semibold", self.style_cfg["font_size"] + 1)
        )
        self.lbl_title.pack(side="left", pady=10)

        # Right section: Controls
        self.header_right = tk.Frame(self.frame_header, bg=self.style_cfg["bg_secondary"])
        self.header_right.pack(side="right", fill="y")

        # Compact mode toggle with hover effect
        self.btn_compact = tk.Label(
            self.header_right,
            text="▾",
            fg=self.style_cfg["fg_secondary"],
            bg=self.style_cfg["bg_secondary"],
            font=("Segoe UI", 12),
            cursor="hand2",
            padx=8
        )
        self.btn_compact.pack(side="right", padx=(0, 8), pady=10)
        self.btn_compact.bind("<Button-1>", lambda e: self.toggle_compact_mode())
        self.btn_compact.bind("<Enter>", lambda e: self.btn_compact.config(fg=self.style_cfg["accent"]))
        self.btn_compact.bind("<Leave>", lambda e: self.btn_compact.config(fg=self.style_cfg["fg_secondary"]))

        # Gradient separator line
        self.separator1 = tk.Canvas(
            self.main_container,
            height=2,
            bg=self.style_cfg["bg"],
            highlightthickness=0
        )
        self.separator1.pack(fill="x", padx=0)
        self._draw_gradient_separator()

        # === STATUS BAR ===
        self.frame_status = tk.Frame(
            self.main_container,
            bg=self.style_cfg["bg"]
        )
        self.frame_status.pack(fill="x", pady=(6, 4))

        self.lbl_status = tk.Label(
            self.frame_status,
            text="● READY",
            fg=self.style_cfg["success"],
            bg=self.style_cfg["bg"],
            font=("JetBrains Mono", self.style_cfg["font_size"] - 1, "bold"),
            anchor="w"
        )
        self.lbl_status.pack(side="left", padx=12)

        self.lbl_turbo_pit = tk.Label(
            self.frame_status,
            text="● TURBO PIT",
            fg=self.style_cfg["success"],
            bg=self.style_cfg["bg"],
            font=("Segoe UI Semibold", self.style_cfg["font_size"] - 1),
            anchor="e",
        )
        self.lbl_turbo_pit.pack(side="right", padx=12)

        # === PITSTOP RULES ===
        # The ruleset iRacing publishes for the session, read for the car being
        # driven: which service comes first, when an adjustment is applied and
        # how long a full tank and a set of tires take.
        self.frame_pit_rules = tk.Frame(
            self.main_container,
            bg=self.style_cfg["bg"],
        )
        self.lbl_pit_rules_title = tk.Label(
            self.frame_pit_rules,
            text="PIT RULES",
            fg=self.style_cfg["fg_secondary"],
            bg=self.style_cfg["bg"],
            font=("Segoe UI Semibold", self.style_cfg["font_size"] - 2),
            anchor="w",
        )
        self.lbl_pit_rules_title.pack(fill="x", padx=12)
        self.lbl_pit_rules_headline = tk.Label(
            self.frame_pit_rules,
            text="--",
            fg=self.style_cfg["fg"],
            bg=self.style_cfg["bg"],
            font=("Segoe UI Semibold", self.style_cfg["font_size"]),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.lbl_pit_rules_headline.pack(fill="x", padx=12)
        self.lbl_pit_rules_detail = tk.Label(
            self.frame_pit_rules,
            text="",
            fg=self.style_cfg["fg_secondary"],
            bg=self.style_cfg["bg"],
            font=("Segoe UI", self.style_cfg["font_size"] - 1),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.lbl_pit_rules_detail.pack(fill="x", padx=12, pady=(0, 4))
        self._pit_rules_headline = ""
        self._pit_rules_detail = ""

        # Guided macro capture stays visible even when the telemetry list is
        # collapsed, so the driver can work in replay with the app unfocused.
        self.capture_frame = tk.Frame(
            self.main_container,
            bg="#2b2110",
            highlightthickness=1,
            highlightbackground=self.style_cfg["warning"],
        )
        self.capture_header = tk.Label(
            self.capture_frame,
            text="LAPDIST CAPTURE",
            bg="#2b2110",
            fg="#ffd166",
            font=("Segoe UI Semibold", self.style_cfg["font_size"]),
            anchor="w",
        )
        self.capture_header.pack(fill="x", padx=10, pady=(7, 0))
        self.capture_stage = tk.Label(
            self.capture_frame,
            text="RECORD THE MINIMUM",
            bg="#2b2110",
            fg="#ffffff",
            font=("Segoe UI Semibold", self.style_cfg["font_size"] - 1),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.capture_stage.pack(fill="x", padx=10)
        self.capture_live_row = tk.Frame(
            self.capture_frame,
            bg="#2b2110",
        )
        self.capture_live_row.pack(fill="x")
        self.capture_value = tk.Label(
            self.capture_live_row,
            text="--",
            bg="#2b2110",
            fg="#7ee787",
            font=("Consolas", self.style_cfg["font_size"] + 4, "bold"),
            anchor="w",
        )
        self.capture_value.pack(side="left", padx=(10, 8), pady=(0, 7))
        self.capture_hotkey = tk.Label(
            self.capture_live_row,
            text="NO HOTKEY",
            bg="#2b2110",
            fg="#ffffff",
            font=("Segoe UI", self.style_cfg["font_size"] - 2),
            anchor="e",
        )
        self.capture_hotkey.pack(
            side="right",
            fill="x",
            expand=True,
            padx=(8, 10),
            pady=(0, 7),
        )
        self.capture_actions = tk.Frame(
            self.capture_frame,
            bg="#2b2110",
        )
        self.capture_actions.pack(fill="x", padx=10, pady=(0, 8))
        self.capture_primary_button = tk.Button(
            self.capture_actions,
            text="RECORD MINIMUM",
            command=lambda: None,
            bg="#d29922",
            fg="#ffffff",
            activebackground="#e3a52b",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=9,
            pady=3,
            cursor="hand2",
            font=("Segoe UI Semibold", self.style_cfg["font_size"] - 2),
        )
        self.capture_primary_button.pack(side="left")
        self.capture_change_bind_button = tk.Button(
            self.capture_actions,
            text="CHANGE KEY",
            command=lambda: None,
            bg="#1f6feb",
            fg="#ffffff",
            activebackground="#388bfd",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            font=("Segoe UI Semibold", self.style_cfg["font_size"] - 2),
        )
        self.capture_change_bind_button.pack(side="left", padx=(6, 0))
        self.capture_secondary_button = tk.Button(
            self.capture_actions,
            text="CANCEL",
            command=lambda: None,
            bg="#30363d",
            fg="#ffffff",
            activebackground="#484f58",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=9,
            pady=3,
            cursor="hand2",
            font=("Segoe UI Semibold", self.style_cfg["font_size"] - 2),
        )
        self.capture_secondary_button.pack(side="right")

        # === CONTENT AREA WITH SCROLL ===
        self.content_frame = tk.Frame(
            self.main_container,
            bg=self.style_cfg["bg"]
        )
        self.content_frame.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        # Custom scrollable canvas
        self.canvas = tk.Canvas(
            self.content_frame,
            bg=self.style_cfg["bg"],
            highlightthickness=0
        )

        # Minimal modern scrollbar
        self.scrollbar = tk.Scrollbar(
            self.content_frame,
            orient="vertical",
            command=self.canvas.yview,
            width=6
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.frame_monitor = tk.Frame(self.canvas, bg=self.style_cfg["bg"])
        self.canvas_window = self.canvas.create_window(
            (0, 0),
            window=self.frame_monitor,
            anchor="nw"
        )

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.frame_monitor.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        # Monitor widgets storage
        self.monitor_widgets: Dict[str, Dict[str, Any]] = {}

        # Value history for mini-graphs
        self.value_history: Dict[str, Deque[float]] = {}
        self.max_history = 50

        # Drag bindings
        self._bind_drag(self.frame_header)
        self._bind_drag(self.header_left)
        self._bind_drag(self.lbl_title)
        self._bind_drag(self.status_canvas)

        # Mouse wheel scroll
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _draw_status_led(self, color: str):
        """Draw modern LED indicator with glow effect."""
        self.status_canvas.delete("all")

        # Outer glow (subtle)
        if self.style_cfg.get("glow_enabled", True):
            glow_color = self._adjust_color(color, 0.3)
            self.status_canvas.create_oval(
                4, 4, 20, 20,
                fill="",
                outline=glow_color,
                width=2
            )

        # Inner LED
        self.status_led = self.status_canvas.create_oval(
            7, 7, 17, 17,
            fill=color,
            outline=""
        )

        # Highlight reflection
        self.status_canvas.create_oval(
            9, 8, 13, 11,
            fill=self._lighten_color(color, 1.5),
            outline=""
        )

    def _draw_gradient_separator(self):
        """Draw a gradient separator line."""
        self.separator1.delete("all")
        width = self.style_cfg.get("width", 340)

        # Create gradient effect with multiple lines
        colors = [
            self.style_cfg["bg"],
            self.style_cfg["border"],
            self.style_cfg["accent"],
            self.style_cfg["border"],
            self.style_cfg["bg"]
        ]

        segment_width = width // (len(colors) - 1)
        for i, color in enumerate(colors[:-1]):
            x1 = i * segment_width
            x2 = (i + 1) * segment_width
            self.separator1.create_line(x1, 1, x2, 1, fill=colors[i+1], width=2)

    def toggle_compact_mode(self):
        """Toggle between normal and compact mode with smooth transition."""
        self.style_cfg["compact_mode"] = not self.style_cfg["compact_mode"]

        if self.style_cfg["compact_mode"]:
            self.btn_compact.config(text="▴")
            # Hide content in compact mode
            self.content_frame.pack_forget()
            compact_height = self.COMPACT_HEIGHT
            if self._lapdist_capture_visible:
                compact_height += self.CAPTURE_EXTRA_HEIGHT
            width = int(self.style_cfg["width"])
            if self._lapdist_capture_visible:
                width = max(width, self.CAPTURE_MIN_WIDTH)
            self.geometry(
                f"{width}x{compact_height}"
                f"+{self._pos_x}+{self._pos_y}"
            )
        else:
            self.btn_compact.config(text="▾")
            # Show content
            self.content_frame.pack(fill="both", expand=True, padx=4, pady=(0, 6))
            height = max(220, self.style_cfg.get("height", 220))
            if self._lapdist_capture_visible:
                height += self.CAPTURE_EXTRA_HEIGHT
            width = int(self.style_cfg["width"])
            if self._lapdist_capture_visible:
                width = max(width, self.CAPTURE_MIN_WIDTH)
            self.geometry(
                f"{width}x{height}"
                f"+{self._pos_x}+{self._pos_y}"
            )

    def set_theme(self, theme_name: str):
        """Apply a predefined theme."""
        if theme_name in self.THEMES:
            theme = self.THEMES[theme_name]
            self.style_cfg.update(theme)
            self.style_cfg["theme"] = theme_name
            self.apply_style(self.style_cfg)

    def _adjust_color(self, color: str, alpha: float) -> str:
        """Adjust color brightness by alpha factor."""
        cache_key = f"adj_{color}_{alpha}"
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]

        try:
            color = color.lstrip('#')
            r, g, b = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
            # Blend with background
            bg_val = 13  # Approximate dark bg
            r = int(r * alpha + bg_val * (1 - alpha))
            g = int(g * alpha + bg_val * (1 - alpha))
            b = int(b * alpha + bg_val * (1 - alpha))
            result = f"#{r:02x}{g:02x}{b:02x}"
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return "#1a1a1a"

    def _bind_mousewheel(self, event):
        """Enable mouse wheel scrolling."""
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        # Linux support
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_mousewheel(self, event):
        """Disable mouse wheel scrolling."""
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        """Handle mouse wheel scroll (Windows/Mac)."""
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event):
        """Handle mouse wheel scroll (Linux)."""
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")

    def _bind_drag(self, widget):
        """Bind drag events to a widget."""
        widget.bind("<Button-1>", self._start_move)
        widget.bind("<B1-Motion>", self._do_move)

    def _start_move(self, event):
        """Start dragging."""
        self.x = event.x
        self.y = event.y

    def _do_move(self, event):
        """Handle drag motion with smooth update."""
        dx = event.x - self.x
        dy = event.y - self.y
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self._pos_x = x
        self._pos_y = y
        self.geometry(f"+{x}+{y}")

    def apply_style(self, style_dict: Dict[str, Any]):
        """Apply modern style configuration to the overlay."""
        self.style_cfg.update(style_dict)
        self._color_cache.clear()  # Clear cache when style changes

        bg = self.style_cfg.get("bg", "#0d1117")
        bg_secondary = self.style_cfg.get("bg_secondary", "#161b22")
        fg = self.style_cfg.get("fg", "#58a6ff")
        fg_secondary = self.style_cfg.get("fg_secondary", "#8b949e")
        accent = self.style_cfg.get("accent", "#238636")
        border = self.style_cfg.get("border", "#30363d")
        success = self.style_cfg.get("success", "#3fb950")
        fs = self.style_cfg.get("font_size", 11)
        op = self.style_cfg.get("opacity", 0.95)
        width = int(self.style_cfg.get("width", 340))
        height = int(self.style_cfg.get("height", 220))

        self.wm_attributes("-alpha", op)

        if self.style_cfg.get("compact_mode", False):
            visible_height = self.COMPACT_HEIGHT
            if self._lapdist_capture_visible:
                visible_height += self.CAPTURE_EXTRA_HEIGHT
                width = max(width, self.CAPTURE_MIN_WIDTH)
            self.geometry(
                f"{width}x{visible_height}+{self._pos_x}+{self._pos_y}"
            )
        else:
            visible_height = height
            if self._lapdist_capture_visible:
                visible_height += self.CAPTURE_EXTRA_HEIGHT
                width = max(width, self.CAPTURE_MIN_WIDTH)
            self.geometry(
                f"{width}x{visible_height}+{self._pos_x}+{self._pos_y}"
            )

        # Update all element colors with premium styling
        self.configure(bg=bg)
        self.main_container.config(
            bg=bg,
            highlightbackground=border,
            highlightcolor=accent
        )

        # Header styling
        self.frame_header.config(bg=bg_secondary)
        self.header_left.config(bg=bg_secondary)
        self.header_right.config(bg=bg_secondary)
        self.lbl_title.config(
            bg=bg_secondary,
            fg=fg,
            font=("Segoe UI Semibold", fs + 1)
        )
        self.status_canvas.config(bg=bg_secondary)
        self.btn_compact.config(bg=bg_secondary, fg=fg_secondary)

        # Separator
        self.separator1.config(bg=bg)
        self._draw_gradient_separator()

        # Status bar
        self.frame_status.config(bg=bg)
        self.lbl_status.config(
            bg=bg,
            font=("JetBrains Mono", fs - 1, "bold")
        )
        self.lbl_turbo_pit.config(
            bg=bg,
            font=("Segoe UI Semibold", fs - 1),
        )
        self.update_turbo_pit_indicator(self._turbo_pit_enabled)

        # Pitstop rules
        self.frame_pit_rules.config(bg=bg)
        self.lbl_pit_rules_title.config(
            bg=bg,
            fg=fg_secondary,
            font=("Segoe UI Semibold", fs - 2),
        )
        self.lbl_pit_rules_headline.config(
            bg=bg,
            fg=fg,
            font=("Segoe UI Semibold", fs),
        )
        self.lbl_pit_rules_detail.config(
            bg=bg,
            fg=fg_secondary,
            font=("Segoe UI", fs - 1),
        )

        # Content area
        self.content_frame.config(bg=bg)
        self.canvas.config(bg=bg)
        self.frame_monitor.config(bg=bg)

        # Update LED
        self._draw_status_led(success)

        capture_bg = self.capture_frame.cget("bg")
        self.capture_frame.config(highlightbackground=self.style_cfg["warning"])
        for widget in (
            self.capture_header,
            self.capture_stage,
            self.capture_live_row,
            self.capture_value,
            self.capture_hotkey,
            self.capture_actions,
        ):
            widget.config(bg=capture_bg)

        # Update monitor widgets
        for var_name, widgets in self.monitor_widgets.items():
            widgets["row"].config(bg=bg)
            widgets["label"].config(
                bg=bg,
                fg=fg_secondary,
                font=("Segoe UI", fs)
            )
            widgets["value"].config(
                bg=bg,
                fg=fg,
                font=("JetBrains Mono", fs, "bold")
            )
            if "bar_frame" in widgets:
                widgets["bar_frame"].config(bg=bg)
            if "bar" in widgets and widgets["bar"]:
                widgets["bar"].config(bg=self._darken_color(bg, 0.6))

    def _darken_color(self, color: str, factor: float = 0.7) -> str:
        """Darken a hex color by factor."""
        cache_key = f"dark_{color}_{factor}"
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]

        try:
            color = color.lstrip('#')
            r, g, b = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
            r = int(r * factor)
            g = int(g * factor)
            b = int(b * factor)
            result = f"#{r:02x}{g:02x}{b:02x}"
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return "#0a0a0a"

    def _blend_colors(self, color1: str, color2: str, ratio: float) -> str:
        """Blend two colors together. ratio=0 is color1, ratio=1 is color2."""
        cache_key = f"blend_{color1}_{color2}_{ratio:.2f}"
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]

        try:
            c1 = color1.lstrip('#')
            c2 = color2.lstrip('#')
            r1, g1, b1 = tuple(int(c1[i:i+2], 16) for i in (0, 2, 4))
            r2, g2, b2 = tuple(int(c2[i:i+2], 16) for i in (0, 2, 4))
            r = int(r1 * (1 - ratio) + r2 * ratio)
            g = int(g1 * (1 - ratio) + g2 * ratio)
            b = int(b1 * (1 - ratio) + b2 * ratio)
            result = f"#{r:02x}{g:02x}{b:02x}"
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return color1

    def update_status_text(self, text: str, color: str = "white"):
        """Update the status header text with modern styling."""
        color_map = {
            "red": self.style_cfg.get("danger", "#f85149"),
            "green": self.style_cfg.get("success", "#3fb950"),
            "orange": self.style_cfg.get("warning", "#d29922"),
            "white": self.style_cfg.get("fg", "#58a6ff"),
            "deepskyblue": self.style_cfg.get("highlight", "#1f6feb")
        }
        c = color_map.get(color, color)

        # Modern status icons
        icon = self.ICONS["status_ok"]
        text_lower = text.lower()
        if "error" in text_lower or "fail" in text_lower:
            icon = self.ICONS["status_error"]
        elif "ok" in text_lower or "ready" in text_lower or "connected" in text_lower:
            icon = self.ICONS["status_ok"]
        elif "warning" in text_lower or "caution" in text_lower:
            icon = self.ICONS["status_warn"]
        elif "scan" in text_lower or "search" in text_lower or "wait" in text_lower:
            icon = self.ICONS["status_scan"]

        try:
            # Update status label with clean formatting
            display_text = text.upper()[:30]  # Limit length
            self.lbl_status.config(text=f"{icon} {display_text}", fg=c)

            # Update LED with glow
            self._draw_status_led(c)
            self._last_status_color = c
        except Exception:
            pass

    def update_turbo_pit_indicator(self, enabled: bool) -> None:
        """Show Turbo Pit state as a compact green/red HUD indicator."""
        self._turbo_pit_enabled = bool(enabled)
        color = (
            self.style_cfg.get("success", "#3fb950")
            if self._turbo_pit_enabled
            else self.style_cfg.get("danger", "#f85149")
        )
        try:
            self.lbl_turbo_pit.config(
                text="● TURBO PIT",
                fg=color,
            )
        except Exception:
            pass

    def update_pitstop_rules(self, headline: str, detail: str) -> None:
        """Show the active pitstop regulations, or hide the band when unknown."""

        headline = str(headline or "").strip()
        detail = str(detail or "").strip()
        if (headline, detail) == (self._pit_rules_headline, self._pit_rules_detail):
            return
        self._pit_rules_headline = headline
        self._pit_rules_detail = detail
        try:
            if not headline:
                if self.frame_pit_rules.winfo_manager():
                    self.frame_pit_rules.pack_forget()
                return
            self.lbl_pit_rules_headline.config(text=headline)
            self.lbl_pit_rules_detail.config(text=detail)
            if not self.frame_pit_rules.winfo_manager():
                self.frame_pit_rules.pack(
                    fill="x",
                    pady=(0, 2),
                    after=self.frame_status,
                )
        except Exception:
            pass

    def show_lapdist_capture(
        self,
        title: str,
        stage: str,
        current: str,
        hotkey: str,
        *,
        complete: bool = False,
        primary_text: str = "",
        primary_command: Any = None,
        secondary_text: str = "",
        secondary_command: Any = None,
        change_bind_text: str = "",
        change_bind_command: Any = None,
    ) -> None:
        """Display live, always-on-top guidance for a macro range capture."""
        self._lapdist_capture_visible = True
        bg = "#12351f" if complete else "#2b2110"
        border = (
            self.style_cfg.get("success", "#3fb950")
            if complete
            else self.style_cfg.get("warning", "#d29922")
        )
        value_color = "#7ee787" if complete else "#ffd166"
        self.capture_frame.config(
            bg=bg,
            highlightbackground=border,
        )
        for widget in (
            self.capture_header,
            self.capture_stage,
            self.capture_live_row,
            self.capture_value,
            self.capture_hotkey,
            self.capture_actions,
        ):
            widget.config(bg=bg)
        self.capture_header.config(text=f'LAPDIST CAPTURE • {title}')
        self.capture_stage.config(text=stage)
        self.capture_value.config(text=current, fg=value_color)
        self.capture_hotkey.config(text=hotkey or "NO HOTKEY")
        primary_bg = (
            self.style_cfg.get("success", "#3fb950")
            if complete
            else self.style_cfg.get("warning", "#d29922")
        )
        self.capture_primary_button.config(
            text=primary_text or "CONTINUE",
            command=primary_command or (lambda: None),
            bg=primary_bg,
            activebackground=primary_bg,
        )
        self.capture_secondary_button.config(
            text=secondary_text or "FINISH",
            command=secondary_command or (lambda: None),
        )
        self.capture_change_bind_button.config(
            text=change_bind_text or "CHANGE KEY",
            command=change_bind_command or (lambda: None),
        )
        if primary_text and not self.capture_primary_button.winfo_manager():
            self.capture_primary_button.pack(side="left")
        elif not primary_text and self.capture_primary_button.winfo_manager():
            self.capture_primary_button.pack_forget()
        if (
            change_bind_text
            and not self.capture_change_bind_button.winfo_manager()
        ):
            self.capture_change_bind_button.pack(side="left", padx=(6, 0))
        elif (
            not change_bind_text
            and self.capture_change_bind_button.winfo_manager()
        ):
            self.capture_change_bind_button.pack_forget()
        if secondary_text and not self.capture_secondary_button.winfo_manager():
            self.capture_secondary_button.pack(side="right")
        elif (
            not secondary_text
            and self.capture_secondary_button.winfo_manager()
        ):
            self.capture_secondary_button.pack_forget()
        if not self.capture_frame.winfo_manager():
            pack_options: dict[str, Any] = {
                "fill": "x",
                "padx": 6,
                "pady": (0, 5),
            }
            if self.content_frame.winfo_manager():
                pack_options["before"] = self.content_frame
            self.capture_frame.pack(**pack_options)
        width = max(
            int(self.style_cfg.get("width", 340)),
            self.CAPTURE_MIN_WIDTH,
        )
        if self.style_cfg.get("compact_mode", False):
            height = self.COMPACT_HEIGHT + self.CAPTURE_EXTRA_HEIGHT
        else:
            height = (
                max(220, int(self.style_cfg.get("height", 220)))
                + self.CAPTURE_EXTRA_HEIGHT
            )
        self.geometry(f"{width}x{height}+{self._pos_x}+{self._pos_y}")

    def hide_lapdist_capture(self) -> None:
        """Remove guided capture feedback without hiding the HUD itself."""
        self._lapdist_capture_visible = False
        try:
            self.capture_frame.pack_forget()
        except Exception:
            pass
        width = int(self.style_cfg.get("width", 340))
        height = (
            self.COMPACT_HEIGHT
            if self.style_cfg.get("compact_mode", False)
            else max(220, int(self.style_cfg.get("height", 220)))
        )
        try:
            self.geometry(f"{width}x{height}+{self._pos_x}+{self._pos_y}")
        except Exception:
            pass

    def rebuild_monitor(self, var_configs: Dict[str, Dict[str, Any]]):
        """Rebuild the monitor display with premium modern styling."""
        # Clear existing widgets
        for widget_dict in self.monitor_widgets.values():
            widget_dict["row"].destroy()
        self.monitor_widgets.clear()

        visible_vars = [v for v, cfg in var_configs.items() if cfg.get("show", False)]
        if not visible_vars:
            return

        bg = self.style_cfg.get("bg", "#0d1117")
        fg = self.style_cfg.get("fg", "#58a6ff")
        fg_secondary = self.style_cfg.get("fg_secondary", "#8b949e")
        fs = self.style_cfg.get("font_size", 11)

        for idx, var_name in enumerate(visible_vars):
            cfg = var_configs.get(var_name, {})
            label_text = cfg.get("label") or format_driver_control_name(var_name)

            # Row container with subtle alternating background
            row_bg = bg if idx % 2 == 0 else self._lighten_color(bg, 1.08)
            row = tk.Frame(
                self.frame_monitor,
                bg=row_bg,
                pady=4
            )
            row.pack(fill="x", padx=2, pady=1)
            self._bind_drag(row)

            # Modern grid layout
            row.columnconfigure(0, weight=0, minsize=24)   # Icon
            row.columnconfigure(1, weight=2, minsize=90)   # Label
            row.columnconfigure(2, weight=1, minsize=70)   # Value
            row.columnconfigure(3, weight=3, minsize=100)  # Progress bar

            # Modern icon
            icon = self._get_var_icon(var_name)
            icon_color = self._get_icon_color(var_name)
            l_icon = tk.Label(
                row,
                text=icon,
                bg=row_bg,
                fg=icon_color,
                font=("Segoe UI", fs),
                anchor="center",
                width=2
            )
            l_icon.grid(row=0, column=0, sticky="w", padx=(6, 2))
            self._bind_drag(l_icon)

            # Clean label
            l_name = tk.Label(
                row,
                text=label_text,
                bg=row_bg,
                fg=fg_secondary,
                font=("Segoe UI", fs),
                anchor="w"
            )
            l_name.grid(row=0, column=1, sticky="w", padx=(0, 5))
            self._bind_drag(l_name)

            # Value with monospace font for alignment
            l_value = tk.Label(
                row,
                text="---",
                bg=row_bg,
                fg=fg,
                font=("JetBrains Mono", fs, "bold"),
                anchor="e",
                width=7
            )
            l_value.grid(row=0, column=2, sticky="e", padx=(0, 8))
            self._bind_drag(l_value)

            # Modern progress bar with frame
            bar_frame = None
            bar_canvas = None
            if self.style_cfg.get("show_graphs", True) and not self.style_cfg.get("compact_mode", False):
                bar_frame = tk.Frame(row, bg=row_bg, pady=2)
                bar_frame.grid(row=0, column=3, sticky="we", padx=(0, 8))

                bar_canvas = tk.Canvas(
                    bar_frame,
                    height=10,
                    bg=self._darken_color(bg, 0.5),
                    highlightthickness=1,
                    highlightbackground=self._darken_color(bg, 0.7)
                )
                bar_canvas.pack(fill="x", expand=True)
                self._bind_drag(bar_canvas)
                self._bind_drag(bar_frame)

            self.monitor_widgets[var_name] = {
                "row": row,
                "icon": l_icon,
                "label": l_name,
                "value": l_value,
                "bar_frame": bar_frame,
                "bar": bar_canvas,
                "last_value": None,
                "row_bg": row_bg
            }

            # Initialize history
            if var_name not in self.value_history:
                self.value_history[var_name] = deque(maxlen=self.max_history)

    def _get_var_icon(self, var_name: str) -> str:
        """Get modern icon based on variable type."""
        name_lower = var_name.lower()
        if "bias" in name_lower or "brake" in name_lower:
            return self.ICONS["brake"]
        elif "fuel" in name_lower:
            return self.ICONS["fuel"]
        elif "tc" in name_lower or "traction" in name_lower:
            return self.ICONS["tc"]
        elif "abs" in name_lower:
            return self.ICONS["abs"]
        elif "diff" in name_lower:
            return self.ICONS["diff"]
        elif "roll" in name_lower or "arb" in name_lower:
            return self.ICONS["roll"]
        elif "weight" in name_lower or "jacker" in name_lower:
            return self.ICONS["weight"]
        elif "lap" in name_lower or "dist" in name_lower:
            return self.ICONS["lap"]
        elif "speed" in name_lower:
            return self.ICONS["speed"]
        elif "gear" in name_lower:
            return self.ICONS["gear"]
        elif "rpm" in name_lower:
            return self.ICONS["rpm"]
        elif "temp" in name_lower:
            return self.ICONS["temp"]
        else:
            return self.ICONS["default"]

    def _get_icon_color(self, var_name: str) -> str:
        """Get icon color based on variable type."""
        name_lower = var_name.lower()
        if "bias" in name_lower or "brake" in name_lower:
            return self.style_cfg.get("danger", "#f85149")
        elif "fuel" in name_lower:
            return self.style_cfg.get("warning", "#d29922")
        elif "tc" in name_lower or "traction" in name_lower:
            return self.style_cfg.get("accent", "#238636")
        elif "abs" in name_lower:
            return self.style_cfg.get("highlight", "#1f6feb")
        elif "lap" in name_lower or "dist" in name_lower:
            return self.style_cfg.get("fg", "#58a6ff")
        else:
            return self.style_cfg.get("fg_secondary", "#8b949e")

    def update_monitor_values(self, data_dict: Dict[str, Any]):
        """Update displayed telemetry values with modern visual bars."""
        for var_name, value in data_dict.items():
            if var_name not in self.monitor_widgets:
                continue

            widgets = self.monitor_widgets[var_name]
            last_value = widgets.get("last_value")

            # Skip update if value hasn't changed (performance optimization)
            if last_value == value and value is not None:
                continue

            widgets["last_value"] = value

            # Format and color the value
            if value is None:
                text = "---"
                color = self.style_cfg.get("fg_secondary", "#8b949e")
            elif isinstance(value, float):
                # Smart formatting based on magnitude
                if abs(value) >= 100:
                    text = f"{value:.1f}"
                elif abs(value) >= 10:
                    text = f"{value:.2f}"
                else:
                    text = f"{value:.3f}"
                color = self._get_value_color(value, var_name)
            else:
                text = str(value)
                color = self.style_cfg.get("fg", "#58a6ff")

            try:
                widgets["value"].config(text=text, fg=color)
            except Exception:
                pass

            # Update progress bar
            if "bar" in widgets and widgets["bar"] and value is not None:
                try:
                    float_val = float(value) if isinstance(value, (int, float)) else 0.0
                    self._update_progress_bar(widgets["bar"], var_name, float_val)
                except Exception:
                    pass

            # Add to history
            if value is not None and isinstance(value, (int, float)):
                if var_name not in self.value_history:
                    self.value_history[var_name] = deque(maxlen=self.max_history)
                self.value_history[var_name].append(float(value))

    def _get_value_color(self, value: float, var_name: str) -> str:
        """Get dynamic color based on value and variable type."""
        name_lower = var_name.lower()

        # Lap distance progress coloring
        if "lapdist" in name_lower:
            if value < 0.25:
                return self.style_cfg.get("fg", "#58a6ff")
            elif value < 0.50:
                return self.style_cfg.get("accent", "#238636")
            elif value < 0.75:
                return self.style_cfg.get("highlight", "#1f6feb")
            else:
                return self.style_cfg.get("warning", "#d29922")

        # Fuel warning
        if "fuel" in name_lower:
            if value < 0.15:
                return self.style_cfg.get("danger", "#f85149")
            elif value < 0.30:
                return self.style_cfg.get("warning", "#d29922")
            return self.style_cfg.get("fg", "#58a6ff")

        # Extreme values warning
        if value >= 0.95:
            return self.style_cfg.get("warning", "#d29922")
        if value <= 0.05:
            return self.style_cfg.get("danger", "#f85149")

        return self.style_cfg.get("fg", "#58a6ff")

    def _update_progress_bar(self, canvas: tk.Canvas, var_name: str, value: float):
        """Update progress bar with modern gradient and glow effects."""
        try:
            width = canvas.winfo_width()
            height = canvas.winfo_height()
            if width <= 1:
                width = 120
            if height <= 1:
                height = 10

            canvas.delete("all")

            # Background with subtle gradient
            bg_color = self._darken_color(self.style_cfg.get("bg", "#0d1117"), 0.5)
            canvas.create_rectangle(
                0, 0, width, height,
                fill=bg_color,
                outline=""
            )

            # Normalize value
            norm_value = max(0.0, min(1.0, value))
            bar_width = max(0, (width - 2) * norm_value)

            if bar_width < 2:
                return

            # Dynamic color based on value
            if norm_value < 0.33:
                main_color = self.style_cfg.get("success", "#3fb950")
                glow_color = self.style_cfg.get("accent_glow", "#2ea043")
            elif norm_value < 0.66:
                main_color = self.style_cfg.get("highlight", "#1f6feb")
                glow_color = self.style_cfg.get("fg", "#58a6ff")
            elif norm_value < 0.85:
                main_color = self.style_cfg.get("warning", "#d29922")
                glow_color = self._lighten_color(main_color, 1.2)
            else:
                main_color = self.style_cfg.get("danger", "#f85149")
                glow_color = self._lighten_color(main_color, 1.3)

            # Main bar
            canvas.create_rectangle(
                1, 1, bar_width + 1, height - 1,
                fill=main_color,
                outline=""
            )

            # Top highlight for 3D effect
            highlight_height = max(2, height // 3)
            canvas.create_rectangle(
                1, 1, bar_width + 1, highlight_height,
                fill=self._lighten_color(main_color, 1.4),
                outline=""
            )

            # Glow effect at the end (if enabled)
            if self.style_cfg.get("glow_enabled", True) and bar_width > 4:
                glow_width = min(6, bar_width // 4)
                for i in range(glow_width):
                    alpha = 1.0 - (i / glow_width)
                    glow = self._blend_colors(glow_color, main_color, alpha * 0.5)
                    canvas.create_line(
                        bar_width - i, 2,
                        bar_width - i, height - 2,
                        fill=glow,
                        width=1
                    )

            # Value marker line (subtle)
            if bar_width > 8:
                canvas.create_line(
                    bar_width, 1,
                    bar_width, height - 1,
                    fill=self._lighten_color(main_color, 1.6),
                    width=1
                )

        except Exception:
            pass  # Silently handle canvas errors

    def _lighten_color(self, color: str, factor: float = 1.3) -> str:
        """Lighten a hex color by factor."""
        cache_key = f"light_{color}_{factor}"
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]

        try:
            color = color.lstrip('#')
            r, g, b = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
            r = min(255, int(r * factor))
            g = min(255, int(g * factor))
            b = min(255, int(b * factor))
            result = f"#{r:02x}{g:02x}{b:02x}"
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return "#ffffff"

    def clear_color_cache(self):
        """Clear the color calculation cache."""
        self._color_cache.clear()


class LapDistOverlayWindow(tk.Toplevel):
    """Small always-on-top LapDistPct overlay that can be closed quickly."""

    def __init__(self, parent: tk.Tk, value_provider: Callable[[], Optional[float]]):
        super().__init__(parent)
        self.value_provider = value_provider
        self.configure(bg="#111111")
        self.title("LapDist Overlay")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.9)
        self.geometry("230x78+40+40")

        frame = tk.Frame(self, bg="#111111")
        frame.pack(fill="both", expand=True)

        tk.Button(
            frame,
            text="✕",
            command=self.close,
            bg="#2a2a2a",
            fg="#f8f8f8",
            activebackground="#3a3a3a",
            activeforeground="#ffffff",
            borderwidth=0,
            padx=8,
            pady=2
        ).pack(anchor="ne", padx=4, pady=4)

        self.label = tk.Label(
            frame,
            text="LapDist: --",
            font=("Segoe UI", 16, "bold"),
            fg="#f8f8f8",
            bg="#111111"
        )
        self.label.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self.bind("<Escape>", self.close)
        self.bind("<ButtonPress-1>", self._start_move)
        self.bind("<B1-Motion>", self._on_move)
        self.label.bind("<ButtonPress-1>", self._start_move)
        self.label.bind("<B1-Motion>", self._on_move)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._update_loop()

    def _start_move(self, event: tk.Event) -> None:
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y

    def _on_move(self, _event: tk.Event) -> None:
        x = self.winfo_pointerx() - self._drag_offset_x
        y = self.winfo_pointery() - self._drag_offset_y
        self.geometry(f"+{x}+{y}")

    def _update_loop(self) -> None:
        if not self.winfo_exists():
            return

        value = self.value_provider()
        if value is None:
            self.label.config(text="LapDist: --")
        else:
            self.label.config(text=f"LapDist: {float(value):.3f}")
        self.after(100, self._update_loop)

    def close(self, _event: tk.Event | None = None) -> None:
        if self.winfo_exists():
            self.destroy()


# ======================================================================
# SCROLLABLE FRAME WIDGET
# ======================================================================
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

        inner_window = canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_canvas_configure(event):
            canvas.itemconfigure(inner_window, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)

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

__all__ = [name for name in globals() if not name.startswith('__')]
