"""HUD overlay window implementation."""

from typing import Any, Dict, Tuple

import tkinter as tk

from dominant_control.config import apply_app_icon


class OverlayWindow(tk.Toplevel):
    """Draggable HUD overlay showing real-time telemetry values."""

    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.wm_attributes("-alpha", 0.85)
        self.geometry("250x150+50+50")
        apply_app_icon(self)

        self.style_cfg = {
            "bg": "black",
            "fg": "white",
            "font_size": 10,
            "opacity": 0.85,
        }

        self.configure(bg=self.style_cfg["bg"])

        # Status header
        self.frame_status = tk.Frame(self, bg=self.style_cfg["bg"])
        self.frame_status.pack(fill="x", pady=2)

        self.lbl_status = tk.Label(
            self.frame_status,
            text="HUD Ready",
            fg="#00FF00",
            bg=self.style_cfg["bg"],
            font=("Consolas", self.style_cfg["font_size"] + 1, "bold"),
        )
        self.lbl_status.pack(anchor="w", padx=5)

        self.separator = tk.Frame(self, bg="#333", height=1)
        self.separator.pack(fill="x", padx=2)

        # Content area
        self.frame_monitor = tk.Frame(self, bg=self.style_cfg["bg"])
        self.frame_monitor.pack(fill="both", expand=True, padx=5, pady=2)

        self.monitor_widgets: Dict[str, Tuple[tk.Label, tk.Label]] = {}

        # Drag support
        self.x = 0
        self.y = 0
        self._bind_drag(self.frame_status)
        self._bind_drag(self.lbl_status)
        self._bind_drag(self.frame_monitor)

    def _bind_drag(self, widget):
        """Bind drag events to a widget."""
        widget.bind("<Button-1>", self._start_move)
        widget.bind("<B1-Motion>", self._do_move)

    def _start_move(self, event):
        """Start dragging."""
        self.x = event.x
        self.y = event.y

    def _do_move(self, event):
        """Handle drag motion."""
        dx = event.x - self.x
        dy = event.y - self.y
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self.geometry(f"+{x}+{y}")

    def apply_style(self, style_dict: Dict[str, Any]):
        """
        Apply style configuration to the overlay.

        Args:
            style_dict: Dictionary with bg, fg, font_size, opacity keys
        """
        self.style_cfg.update(style_dict)
        bg = self.style_cfg["bg"]
        fg = self.style_cfg["fg"]
        fs = self.style_cfg["font_size"]
        op = self.style_cfg["opacity"]

        self.configure(bg=bg)
        self.wm_attributes("-alpha", op)

        self.frame_status.config(bg=bg)
        self.lbl_status.config(bg=bg, font=("Consolas", fs + 1, "bold"))
        self.frame_monitor.config(bg=bg)

        # Update all monitor widgets
        for row in self.frame_monitor.winfo_children():
            row.config(bg=bg)
            for child in row.winfo_children():
                txt = child.cget("text")
                is_value = (txt == "--" or (txt and (txt[0].isdigit() or txt[0] == "-")))
                if is_value:
                    child.config(bg=bg, fg=fg, font=("Consolas", fs, "bold"))
                else:
                    child.config(bg=bg, fg="#AAAAAA", font=("Consolas", fs))

    def update_status_text(self, text: str, color: str = "white"):
        """
        Update the status header text.

        Args:
            text: Status text to display
            color: Color name or hex code
        """
        color_map = {
            "red": "#FF4444",
            "green": "#00FF00",
            "orange": "#FFA500",
            "white": self.style_cfg["fg"],
        }
        c = color_map.get(color, color)
        try:
            self.lbl_status.config(text=text, fg=c)
        except Exception:
            pass

    def rebuild_monitor(self, var_configs: Dict[str, Dict[str, Any]]):
        """
        Rebuild the monitor display with new variables.

        Args:
            var_configs: Dict of var_name -> {"show": bool, "label": str}
        """
        # Clear existing widgets
        for widget in self.frame_monitor.winfo_children():
            widget.destroy()
        self.monitor_widgets.clear()

        visible_vars = [v for v, cfg in var_configs.items() if cfg.get("show", False)]
        if not visible_vars:
            return

        for var_name in visible_vars:
            cfg = var_configs.get(var_name, {})
            label_text = cfg.get("label") or var_name.replace("dc", "")

            row = tk.Frame(self.frame_monitor, bg=self.style_cfg["bg"])
            row.pack(fill="x")
            self._bind_drag(row)

            l_name = tk.Label(
                row,
                text=f"{label_text}:",
                bg=self.style_cfg["bg"],
                fg="#AAAAAA",
                font=("Consolas", self.style_cfg["font_size"]),
                width=15,
                anchor="w",
            )
            l_name.pack(side="left")
            self._bind_drag(l_name)

            l_value = tk.Label(
                row,
                text="--",
                bg=self.style_cfg["bg"],
                fg=self.style_cfg["fg"],
                font=("Consolas", self.style_cfg["font_size"], "bold"),
            )
            l_value.pack(side="right")
            self._bind_drag(l_value)

            self.monitor_widgets[var_name] = (l_name, l_value)

        # Resize window
        line_height = self.style_cfg["font_size"] * 2 + 6
        h = 45 + (len(visible_vars) * line_height)
        h = max(60, min(h, 800))

        geometry = self.geometry().split('+')
        try:
            self.geometry(f"250x{h}+{geometry[1]}+{geometry[2]}")
        except Exception:
            self.geometry(f"250x{h}+50+50")

    def update_monitor_values(self, data_dict: Dict[str, Any]):
        """
        Update displayed telemetry values.

        Args:
            data_dict: Dict of var_name -> value
        """
        for var_name, value in data_dict.items():
            if var_name in self.monitor_widgets:
                _name_label, value_label = self.monitor_widgets[var_name]
                if value is None:
                    text = "--"
                elif isinstance(value, float):
                    text = f"{value:.3f}"
                else:
                    text = str(value)
                try:
                    value_label.config(text=text)
                except Exception:
                    pass
