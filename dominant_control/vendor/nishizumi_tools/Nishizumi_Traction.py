#!/usr/bin/env python3
"""Modernized Tkinter traction-circle overlay for iRacing telemetry.

What changed
- Cleaner layout with clear separation between live overlay, coaching, and setup.
- Minimal mode for driving: big circle + live metrics + one coaching hint.
- Detailed mode for review: top coaching cards, data quality, quickstart and settings.
- Better visual hierarchy and fewer always-visible status strings.
- Same telemetry and learning logic, so behaviour stays compatible with pyirsdk.
"""

from __future__ import annotations

import math
import os
import statistics
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from tkinter import filedialog, ttk
from typing import Any, Deque, List, Optional, Sequence, Tuple

import irsdk

G_CONSTANT = 9.80665
UPDATE_MS = 60
BINS_PER_LAP = 200
MIN_REFERENCE_G = 0.75
UNDERUSE_MARGIN = 0.12
MIN_SEGMENT_BINS = 3
MIN_SAMPLES_PER_BIN = 5
MAX_LAP_HISTORY = 1000
RECENT_VALID_LAPS = 5
TOGGLE_MODE_KEY = "m"
DEFAULT_LAPS_FOR_FEEDBACK = 5
TOGGLE_CIRCLE_KEY = "o"

BG = "#0b0f14"
PANEL = "#111821"
PANEL_2 = "#16202b"
BORDER = "#223142"
TEXT = "#e6edf3"
SUBTEXT = "#8aa0b6"
ACCENT = "#4cc9f0"
ACCENT_2 = "#7ae582"
WARNING = "#ffd166"
DANGER = "#ff6b6b"
RING = "#243344"
GRID = "#1c2834"
DOT = "#54d2ff"
GOOD = "#7ae582"
MEDIUM = "#ffd166"
BAD = "#ff6b6b"

QUICKSTART_TEXT = (
    "1. Join the session and click Drive to start telemetry.\n"
    "2. Run a few representative laps, ideally clean ones.\n"
    "3. Choose how many laps the app should learn before coaching starts.\n"
    "4. Use Minimal View for a smaller driving layout during the stint.\n"
    "5. Use Pop-out Circle for a detached mini overlay that follows the traction circle live."
)


@dataclass
class LapData:
    lap_number: int
    lap_time: float
    valid: bool
    bins: List[float]
    long_bins: List[float]
    lat_bins: List[float]


@dataclass
class UnderuseSegment:
    start_percent: float
    end_percent: float
    peak_percent: float
    reference_g: float
    achieved_g: float
    delta_g: float
    severity: str
    phase: str
    recommendation: str
    trend: str
    consistency: float
    confidence: bool


class InfoCard(ttk.Frame):
    def __init__(self, master: tk.Misc, title: str) -> None:
        super().__init__(master, style="Card.TFrame", padding=(14, 10))
        self.title_var = tk.StringVar(value=title)
        self.value_var = tk.StringVar(value="--")
        self.sub_var = tk.StringVar(value="")

        ttk.Label(self, textvariable=self.title_var, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.value_var, style="CardValue.TLabel").pack(anchor="w", pady=(6, 2))
        ttk.Label(self, textvariable=self.sub_var, style="CardSub.TLabel").pack(anchor="w")

    def set(self, value: str, sub: str = "") -> None:
        self.value_var.set(value)
        self.sub_var.set(sub)


class CoachCard(ttk.Frame):
    def __init__(self, master: tk.Misc, title: str) -> None:
        super().__init__(master, style="CardAlt.TFrame", padding=(14, 12))
        self.title_var = tk.StringVar(value=title)
        self.meta_var = tk.StringVar(value="")
        self.body_var = tk.StringVar(value="--")

        ttk.Label(self, textvariable=self.title_var, style="CoachTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.meta_var, style="CoachMeta.TLabel").pack(anchor="w", pady=(4, 6))
        ttk.Label(self, textvariable=self.body_var, style="CoachBody.TLabel", wraplength=310, justify="left").pack(anchor="w")

    def set(self, title: str, meta: str, body: str) -> None:
        self.title_var.set(title)
        self.meta_var.set(meta)
        self.body_var.set(body)


class TractionCircleOverlay:
    def __init__(self) -> None:
        self.ir = irsdk.IRSDK()

        self.root = tk.Tk()
        self.root.title("Traction Circle Coach")
        self.root.geometry("1180x760")
        self.root.minsize(920, 620)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)

        self.compact_mode = True
        self.minimal_mode = False
        self.sidebar_visible = True
        self.details_visible = True
        self.external_reference_bins: Optional[List[float]] = None
        self.external_reference_path: Optional[str] = None

        self.status_var = tk.StringVar(value="Connecting")
        self.context_var = tk.StringVar(value="Car --  •  Track --  •  Session --")
        self.reference_var = tk.StringVar(value="Adaptive live reference")
        self.headline_var = tk.StringVar(value="Waiting for telemetry...")
        self.subheadline_var = tk.StringVar(value="Open iRacing and click Drive.")
        self.footer_var = tk.StringVar(value="M: compact/detailed  •  V: minimal view  •  O: pop-out circle  •  S: setup panel")
        self.settings_hint_var = tk.StringVar(value="Coaching starts after 5 clean laps.")

        self.current_lap_num: Optional[int] = None
        self.current_lap_bins: List[float] = [0.0] * BINS_PER_LAP
        self.current_lap_long_bins: List[float] = [0.0] * BINS_PER_LAP
        self.current_lap_lat_bins: List[float] = [0.0] * BINS_PER_LAP
        self.current_lap_valid = True

        self.lap_history: Deque[LapData] = deque(maxlen=MAX_LAP_HISTORY)
        self.invalid_laps_count = 0
        self.outliers_removed_last = 0
        self.bin_confidence: List[bool] = [False] * BINS_PER_LAP

        self.context_key = ""
        self.current_track = "--"
        self.current_car = "--"
        self.current_session = "--"

        self.recent_peaks: Deque[float] = deque(maxlen=300)
        self.estimated_limit_g = 1.8

        self.laps_for_feedback_var = tk.IntVar(value=DEFAULT_LAPS_FOR_FEEDBACK)
        self.incident_free_only_var = tk.BooleanVar(value=True)
        self.quickstart_window: Optional[tk.Toplevel] = None
        self.circle_window: Optional[tk.Toplevel] = None
        self.circle_canvas: Optional[tk.Canvas] = None
        self.circle_caption_var = tk.StringVar(value="Usage --")

        self._drag_start: Optional[Tuple[int, int]] = None

        self._build_style()
        self._build_ui()
        self._refresh_feedback_settings()
        self._bind_shortcuts()
        self._apply_layout_mode()

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("PanelAlt.TFrame", background=PANEL_2)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("CardAlt.TFrame", background=PANEL_2)

        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 18))
        style.configure("SubTitle.TLabel", background=BG, foreground=SUBTEXT, font=("Segoe UI", 10))
        style.configure("HeaderChip.TLabel", background=PANEL_2, foreground=TEXT, font=("Segoe UI Semibold", 9), padding=(10, 6))
        style.configure("Section.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 12))
        style.configure("CardTitle.TLabel", background=PANEL, foreground=SUBTEXT, font=("Segoe UI Semibold", 9))
        style.configure("CardValue.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI Semibold", 18))
        style.configure("CardSub.TLabel", background=PANEL, foreground=SUBTEXT, font=("Segoe UI", 9))
        style.configure("CoachTitle.TLabel", background=PANEL_2, foreground=TEXT, font=("Segoe UI Semibold", 11))
        style.configure("CoachMeta.TLabel", background=PANEL_2, foreground=WARNING, font=("Segoe UI", 9))
        style.configure("CoachBody.TLabel", background=PANEL_2, foreground=TEXT, font=("Segoe UI", 9))
        style.configure("Hint.TLabel", background=PANEL, foreground=SUBTEXT, font=("Segoe UI", 9))
        style.configure("Body.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("BodyAlt.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 11))
        style.configure("Footer.TLabel", background=BG, foreground=SUBTEXT, font=("Segoe UI", 9))

        style.configure(
            "TButton",
            background=PANEL_2,
            foreground=TEXT,
            borderwidth=0,
            focusthickness=0,
            focuscolor=PANEL_2,
            padding=(12, 7),
            font=("Segoe UI Semibold", 9),
        )
        style.map("TButton", background=[("active", BORDER)])

        style.configure("Accent.TButton", background=ACCENT, foreground="#06131a")
        style.map("Accent.TButton", background=[("active", "#78dcff")])

        style.configure(
            "TCheckbutton",
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 9),
        )
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])

        style.configure(
            "TSpinbox",
            fieldbackground=PANEL_2,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            insertcolor=TEXT,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=5,
        )

        style.configure("Horizontal.TSeparator", background=BORDER)

    def _build_ui(self) -> None:
        self.outer = ttk.Frame(self.root, padding=14)
        self.outer.pack(fill="both", expand=True)

        self.topbar = ttk.Frame(self.outer)
        self.topbar.pack(fill="x")

        title_wrap = ttk.Frame(self.topbar)
        title_wrap.pack(side="left", fill="x", expand=True)
        ttk.Label(title_wrap, text="Traction Circle Coach", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_wrap, textvariable=self.context_var, style="SubTitle.TLabel").pack(anchor="w", pady=(2, 0))

        chips = ttk.Frame(self.topbar)
        chips.pack(side="right")
        self.status_chip = ttk.Label(chips, textvariable=self.status_var, style="HeaderChip.TLabel")
        self.status_chip.pack(side="left", padx=(0, 8))
        self.reference_chip = ttk.Label(chips, textvariable=self.reference_var, style="HeaderChip.TLabel")
        self.reference_chip.pack(side="left")

        toolbar = ttk.Frame(self.outer)
        toolbar.pack(fill="x", pady=(12, 12))

        self.btn_minimal = ttk.Button(toolbar, text="Minimal view (V)", command=self._toggle_minimal)
        self.btn_minimal.pack(side="left")
        self.btn_mode = ttk.Button(toolbar, text="Compact coaching (M)", command=self._toggle_mode)
        self.btn_mode.pack(side="left", padx=(8, 0))
        self.btn_circle = ttk.Button(toolbar, text="Pop-out circle (O)", command=self._toggle_circle_popout)
        self.btn_circle.pack(side="left", padx=(8, 0))
        self.btn_sidebar = ttk.Button(toolbar, text="Setup panel (S)", command=self._toggle_sidebar)
        self.btn_sidebar.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Load IBT", command=self._load_ibt_reference).pack(side="right")
        ttk.Button(toolbar, text="Use Live", command=self._clear_ibt_reference).pack(side="right", padx=(0, 8))

        self.content = ttk.Frame(self.outer)
        self.content.pack(fill="both", expand=True)

        self.main_panel = ttk.Frame(self.content, style="Panel.TFrame", padding=16)
        self.main_panel.pack(side="left", fill="both", expand=True)

        self.sidebar = ttk.Frame(self.content, style="Panel.TFrame", padding=16)
        self.sidebar.pack(side="left", fill="y", padx=(12, 0))

        ttk.Label(self.main_panel, textvariable=self.headline_var, style="BodyAlt.TLabel").pack(anchor="w")
        ttk.Label(self.main_panel, textvariable=self.subheadline_var, style="Hint.TLabel").pack(anchor="w", pady=(4, 10))

        self.canvas_frame = ttk.Frame(self.main_panel, style="Panel.TFrame")
        self.canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            self.canvas_frame,
            bg=BG,
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        metrics = ttk.Frame(self.main_panel)
        metrics.pack(fill="x", pady=(14, 0))
        metrics.columnconfigure((0, 1, 2, 3), weight=1)

        self.card_total = InfoCard(metrics, "Total grip")
        self.card_total.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.card_long = InfoCard(metrics, "Longitudinal")
        self.card_long.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.card_lat = InfoCard(metrics, "Lateral")
        self.card_lat.grid(row=0, column=2, sticky="nsew", padx=(0, 8))
        self.card_limit = InfoCard(metrics, "Estimated limit")
        self.card_limit.grid(row=0, column=3, sticky="nsew")

        ttk.Label(self.main_panel, textvariable=self.footer_var, style="Footer.TLabel").pack(anchor="w", pady=(12, 0))

        ttk.Label(self.sidebar, text="Coaching", style="Section.TLabel").pack(anchor="w")
        self.coach_intro = ttk.Label(
            self.sidebar,
            text="The biggest grip gaps appear here, already sorted by impact.",
            style="Hint.TLabel",
            wraplength=340,
            justify="left",
        )
        self.coach_intro.pack(anchor="w", pady=(4, 12))

        self.coach_cards_wrap = ttk.Frame(self.sidebar, style="Panel.TFrame")
        self.coach_cards_wrap.pack(fill="x")
        self.coach_card_1 = CoachCard(self.coach_cards_wrap, "#1")
        self.coach_card_1.pack(fill="x", pady=(0, 8))
        self.coach_card_2 = CoachCard(self.coach_cards_wrap, "#2")
        self.coach_card_2.pack(fill="x", pady=(0, 8))
        self.coach_card_3 = CoachCard(self.coach_cards_wrap, "#3")
        self.coach_card_3.pack(fill="x")

        ttk.Separator(self.sidebar, orient="horizontal").pack(fill="x", pady=14)
        ttk.Label(self.sidebar, text="Setup", style="Section.TLabel").pack(anchor="w")

        self.settings_box = ttk.Frame(self.sidebar, style="Card.TFrame", padding=14)
        self.settings_box.pack(fill="x", pady=(10, 0))
        ttk.Label(self.settings_box, text="Laps before coaching", style="Body.TLabel").grid(row=0, column=0, sticky="w")
        self.laps_spinbox = ttk.Spinbox(
            self.settings_box,
            from_=1,
            to=50,
            width=5,
            textvariable=self.laps_for_feedback_var,
            command=self._refresh_feedback_settings,
        )
        self.laps_spinbox.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.laps_spinbox.bind("<KeyRelease>", self._refresh_feedback_settings)
        self.laps_spinbox.bind("<<Increment>>", self._refresh_feedback_settings)
        self.laps_spinbox.bind("<<Decrement>>", self._refresh_feedback_settings)

        ttk.Checkbutton(
            self.settings_box,
            text="Use only clean laps for learning",
            variable=self.incident_free_only_var,
            command=self._refresh_feedback_settings,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.btn_quickstart = ttk.Button(
            self.settings_box,
            text="Open quickstart",
            command=self._open_quickstart_window,
        )
        self.btn_quickstart.grid(row=2, column=0, sticky="w", pady=(6, 0))

        ttk.Label(
            self.settings_box,
            text="Opens in a separate window so it never gets lost inside the panel.",
            style="Hint.TLabel",
            wraplength=180,
            justify="left",
        ).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(6, 0))

        ttk.Label(self.settings_box, textvariable=self.settings_hint_var, style="Hint.TLabel", wraplength=320).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        self.root.bind("<ButtonPress-1>", self._on_drag_start)
        self.root.bind("<B1-Motion>", self._on_drag_motion)

    def _bind_shortcuts(self) -> None:
        for key in (TOGGLE_MODE_KEY, TOGGLE_MODE_KEY.upper()):
            self.root.bind(f"<{key}>", self._toggle_mode)
        for key in ("v", "V"):
            self.root.bind(f"<{key}>", self._toggle_minimal)
        for key in ("s", "S"):
            self.root.bind(f"<{key}>", self._toggle_sidebar)
        for key in (TOGGLE_CIRCLE_KEY, TOGGLE_CIRCLE_KEY.upper()):
            self.root.bind(f"<{key}>", self._toggle_circle_popout)

    def _on_drag_start(self, event: tk.Event[tk.Misc]) -> None:
        widget = event.widget
        if isinstance(widget, (tk.Canvas, tk.Tk, ttk.Frame, ttk.Label)):
            self._drag_start = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag_motion(self, event: tk.Event[tk.Misc]) -> None:
        if not self.minimal_mode or self._drag_start is None:
            return
        offset_x, offset_y = self._drag_start
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _toggle_circle_popout(self, _event: object = None) -> None:
        if self.circle_window is not None and self.circle_window.winfo_exists():
            self._close_circle_popout()
            return
        self._open_circle_popout()

    def _open_circle_popout(self) -> None:
        window = tk.Toplevel(self.root)
        self.circle_window = window
        window.title("Circle Overlay")
        window.configure(bg=BG)
        window.geometry(f"320x360+{self.root.winfo_x() + 640}+{self.root.winfo_y() + 120}")
        window.minsize(240, 260)
        window.attributes("-topmost", True)
        try:
            window.wm_attributes("-toolwindow", True)
        except Exception:
            pass

        shell = ttk.Frame(window, style="Panel.TFrame", padding=10)
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="Detached traction circle", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            shell,
            text="Small live overlay for following grip usage while driving.",
            style="Hint.TLabel",
            wraplength=260,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))

        self.circle_canvas = tk.Canvas(
            shell,
            bg=BG,
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            bd=0,
        )
        self.circle_canvas.pack(fill="both", expand=True)
        ttk.Label(shell, textvariable=self.circle_caption_var, style="Footer.TLabel").pack(anchor="center", pady=(8, 0))

        def _close(_event: object = None) -> None:
            self._close_circle_popout()

        window.protocol("WM_DELETE_WINDOW", _close)
        window.bind("<Escape>", _close)
        self.btn_circle.configure(text="Dock circle (O)")
        self._draw_circle(0.0, 0.0, 0.0)

    def _close_circle_popout(self) -> None:
        if self.circle_window is not None and self.circle_window.winfo_exists():
            self.circle_window.destroy()
        self.circle_window = None
        self.circle_canvas = None
        self.circle_caption_var.set("Usage --")
        self.btn_circle.configure(text="Pop-out circle (O)")

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(parsed):
            return default
        return parsed

    def _read_var(self, name: str, default: object = None) -> object:
        try:
            return self.ir[name]
        except Exception:
            return default

    @staticmethod
    def _bin_index(lap_dist_pct: float) -> int:
        normalized = max(0.0, min(0.999999, lap_dist_pct))
        return int(normalized * BINS_PER_LAP)

    @staticmethod
    def _get_nested(data: object, *path: object) -> object:
        cur = data
        for item in path:
            if isinstance(item, int):
                if not isinstance(cur, list) or item < 0 or item >= len(cur):
                    return None
                cur = cur[item]
                continue
            if not isinstance(cur, dict):
                return None
            cur = cur.get(item)
            if cur is None:
                return None
        return cur

    def _detect_context(self) -> Tuple[str, str, str, str]:
        weekend = self._read_var("WeekendInfo", {})
        driver_info = self._read_var("DriverInfo", {})
        session_info = self._read_var("SessionInfo", {})
        driver_car_idx = int(self._safe_float(self._read_var("DriverCarIdx", 0)))

        track_id = self._get_nested(weekend, "TrackID")
        track_name = self._get_nested(weekend, "TrackName")
        track_cfg = self._get_nested(weekend, "TrackConfigName")
        track_value = track_id if track_id not in (None, "") else (track_name or track_cfg or "unknown")
        track_display = track_name or track_cfg or str(track_value)

        car_id = self._get_nested(driver_info, "Drivers", driver_car_idx, "CarID")
        car_path = self._get_nested(driver_info, "Drivers", driver_car_idx, "CarPath")
        car_screen = self._get_nested(driver_info, "Drivers", driver_car_idx, "CarScreenName")
        car_value = car_id if car_id not in (None, "") else (car_path or "unknown")
        car_display = car_screen or car_path or str(car_value)

        session_num = int(self._safe_float(self._read_var("SessionNum", 0)))
        session_name = self._get_nested(session_info, "Sessions", session_num, "SessionName")
        session_display = str(session_name or "Unknown")

        context_key = f"track:{track_value}|car:{car_value}"
        return context_key, str(track_display), str(car_display), session_display

    def _reset_for_new_context(self) -> None:
        self.current_lap_num = None
        self.current_lap_bins = [0.0] * BINS_PER_LAP
        self.current_lap_long_bins = [0.0] * BINS_PER_LAP
        self.current_lap_lat_bins = [0.0] * BINS_PER_LAP
        self.current_lap_valid = True
        self.lap_history.clear()
        self.invalid_laps_count = 0
        self.outliers_removed_last = 0
        self.bin_confidence = [False] * BINS_PER_LAP

    def _is_offtrack(self, driver_car_idx: int) -> bool:
        surfaces = self._read_var("CarIdxTrackSurface", [])
        if not isinstance(surfaces, list) or driver_car_idx < 0 or driver_car_idx >= len(surfaces):
            return False
        val = surfaces[driver_car_idx]
        if isinstance(val, str):
            return "offtrack" in val.lower()
        try:
            numeric = int(val)
        except (TypeError, ValueError):
            return False
        return numeric == 1

    def _finalize_current_lap(self, next_lap_num: int) -> None:
        if self.current_lap_num is None:
            self.current_lap_num = next_lap_num
            return
        if next_lap_num == self.current_lap_num:
            return
        if self.current_lap_num < 0:
            self.current_lap_num = next_lap_num
            return

        lap_time = self._safe_float(self._read_var("LapLastLapTime", 0.0), default=0.0)
        self.lap_history.append(
            LapData(
                lap_number=self.current_lap_num,
                lap_time=lap_time,
                valid=self.current_lap_valid,
                bins=self.current_lap_bins.copy(),
                long_bins=self.current_lap_long_bins.copy(),
                lat_bins=self.current_lap_lat_bins.copy(),
            )
        )
        if not self.current_lap_valid:
            self.invalid_laps_count += 1

        self.current_lap_bins = [0.0] * BINS_PER_LAP
        self.current_lap_long_bins = [0.0] * BINS_PER_LAP
        self.current_lap_lat_bins = [0.0] * BINS_PER_LAP
        self.current_lap_valid = True
        self.current_lap_num = next_lap_num

    def _update_lap_storage(
        self,
        lap_num: int,
        lap_dist_pct: float,
        g_total: float,
        long_g: float,
        lat_g: float,
        offtrack: bool,
    ) -> None:
        self._finalize_current_lap(lap_num)
        if offtrack:
            self.current_lap_valid = False

        idx = self._bin_index(lap_dist_pct)
        if g_total > self.current_lap_bins[idx]:
            self.current_lap_bins[idx] = g_total
            self.current_lap_long_bins[idx] = long_g
            self.current_lap_lat_bins[idx] = lat_g

    @staticmethod
    def _iqr_filter(values: Sequence[float]) -> Tuple[List[float], int]:
        if len(values) < 4:
            return list(values), 0
        sorted_vals = sorted(values)
        q1, _, q3 = statistics.quantiles(sorted_vals, n=4, method="inclusive")
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        filtered = [v for v in sorted_vals if lower <= v <= upper]
        return filtered, len(sorted_vals) - len(filtered)

    def _compute_reference_by_bin(self, valid_laps: Sequence[LapData]) -> List[float]:
        reference = [0.0] * BINS_PER_LAP
        self.outliers_removed_last = 0
        self.bin_confidence = [False] * BINS_PER_LAP
        if not valid_laps:
            return reference

        for i in range(BINS_PER_LAP):
            values = [lap.bins[i] for lap in valid_laps if lap.bins[i] > 0.05]
            if not values:
                continue
            filtered, removed = self._iqr_filter(values)
            self.outliers_removed_last += removed
            if len(filtered) < MIN_SAMPLES_PER_BIN:
                continue
            filtered.sort()
            idx = min(len(filtered) - 1, int(round((len(filtered) - 1) * 0.9)))
            reference[i] = filtered[idx]
            self.bin_confidence[i] = True
        return reference

    @staticmethod
    def _read_ibt_series(ibt: Any, name: str) -> List[float]:
        for method_name in ("get_all", "get"):
            method = getattr(ibt, method_name, None)
            if callable(method):
                try:
                    values = method(name)
                except Exception:
                    continue
                if values is not None:
                    return [TractionCircleOverlay._safe_float(v, default=0.0) for v in list(values)]

        try:
            values = ibt[name]  # type: ignore[index]
            return [TractionCircleOverlay._safe_float(v, default=0.0) for v in list(values)]
        except Exception:
            return []

    def _reference_from_ibt(self, file_path: str) -> Optional[List[float]]:
        ibt_reader = getattr(irsdk, "IBT", None)
        if ibt_reader is None:
            self.status_var.set("IBT unavailable")
            self.subheadline_var.set("Your pyirsdk build does not expose the IBT reader.")
            return None

        try:
            ibt = ibt_reader()
            opened = ibt.open(file_path)
        except Exception as exc:
            self.status_var.set("IBT error")
            self.subheadline_var.set(f"Failed to open IBT: {exc}")
            return None

        if opened is False:
            self.status_var.set("IBT error")
            self.subheadline_var.set("Failed to open the IBT file.")
            return None

        lap_dist = self._read_ibt_series(ibt, "LapDistPct")
        long_accel = self._read_ibt_series(ibt, "LongAccel")
        lat_accel = self._read_ibt_series(ibt, "LatAccel")
        if not lap_dist or not long_accel or not lat_accel:
            self.status_var.set("IBT incomplete")
            self.subheadline_var.set("The IBT file does not contain LapDistPct / LongAccel / LatAccel.")
            return None

        sample_count = min(len(lap_dist), len(long_accel), len(lat_accel))
        bins = [0.0] * BINS_PER_LAP
        for i in range(sample_count):
            idx = self._bin_index(self._safe_float(lap_dist[i], default=0.0))
            long_g = self._safe_float(long_accel[i], default=0.0) / G_CONSTANT
            lat_g = self._safe_float(lat_accel[i], default=0.0) / G_CONSTANT
            total_g = math.hypot(long_g, lat_g)
            if total_g > bins[idx]:
                bins[idx] = total_g

        if max(bins, default=0.0) < MIN_REFERENCE_G:
            self.status_var.set("IBT too weak")
            self.subheadline_var.set("IBT loaded, but it did not produce a useful grip reference.")
            return None
        return bins

    def _load_ibt_reference(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Load IBT reference",
            filetypes=[("iRacing telemetry", "*.ibt"), ("All files", "*.*")],
        )
        if not file_path:
            return

        bins = self._reference_from_ibt(file_path)
        if bins is None:
            return

        self.external_reference_bins = bins
        self.external_reference_path = file_path
        self.reference_var.set(f"IBT: {os.path.basename(file_path)}")
        lap_target = self._feedback_lap_target()
        lap_label = "clean" if self.incident_free_only_var.get() else "completed"
        self.subheadline_var.set(f"IBT reference loaded. Coaching starts after {lap_target} {lap_label} lap(s).")

    def _clear_ibt_reference(self) -> None:
        self.external_reference_bins = None
        self.external_reference_path = None
        self.reference_var.set("Adaptive live reference")
        lap_label = "clean" if self.incident_free_only_var.get() else "completed"
        self.subheadline_var.set(f"Using the adaptive live reference based on your {lap_label} laps.")

    @staticmethod
    def _phase_and_recommendation(neg_long: float, lat: float, pos_long: float) -> Tuple[str, str]:
        if neg_long > max(lat, pos_long):
            return "Entry", "You can brake a little later or release peak brake pressure more smoothly to keep rotation alive."
        if lat >= max(neg_long, pos_long):
            return "Mid", "There is room for more minimum speed here. Try releasing the brake slightly earlier and clipping the apex more cleanly."
        return "Exit", "There is margin on exit. Try starting the throttle a bit earlier and building the application progressively."

    @staticmethod
    def _severity_label(delta_g: float) -> str:
        if delta_g >= 0.25:
            return "high"
        if delta_g >= 0.12:
            return "medium"
        return "low"

    @staticmethod
    def _trend_label(values: Sequence[float]) -> str:
        if len(values) < 3:
            return "stable"
        split = max(1, len(values) // 2)
        first = statistics.mean(values[:split])
        second = statistics.mean(values[split:])
        if second - first > 0.03:
            return "improving"
        if second - first < -0.03:
            return "declining"
        return "stable"

    @staticmethod
    def _lapdist_hint(start_percent: float, end_percent: float, peak_percent: float) -> str:
        start_pct = start_percent * 100.0
        end_pct = end_percent * 100.0
        peak_pct = peak_percent * 100.0
        return f"LapDist {start_pct:.1f}%→{end_pct:.1f}%  •  peak at {peak_pct:.1f}%"

    def _detect_underuse_segments(self, valid_laps: Sequence[LapData], reference: Sequence[float]) -> List[UnderuseSegment]:
        if not valid_laps:
            return []

        recent = list(valid_laps[-RECENT_VALID_LAPS:])
        achieved = [0.0] * BINS_PER_LAP
        for i in range(BINS_PER_LAP):
            values = [lap.bins[i] for lap in recent if lap.bins[i] > 0.05]
            if values:
                achieved[i] = statistics.median(values)

        segments: List[Tuple[int, int]] = []
        start = -1
        for i in range(BINS_PER_LAP):
            ref = reference[i]
            ach = achieved[i]
            confident = self.bin_confidence[i]
            is_under = confident and ref >= MIN_REFERENCE_G and ach < ref * (1.0 - UNDERUSE_MARGIN)
            if is_under and start < 0:
                start = i
            if (not is_under or i == BINS_PER_LAP - 1) and start >= 0:
                end = i if is_under and i == BINS_PER_LAP - 1 else i - 1
                if end - start + 1 >= MIN_SEGMENT_BINS:
                    segments.append((start, end))
                start = -1

        results: List[UnderuseSegment] = []
        for seg_start, seg_end in segments:
            best_idx = seg_start
            best_gap = -1.0
            for i in range(seg_start, seg_end + 1):
                gap = reference[i] - achieved[i]
                if gap > best_gap:
                    best_gap = gap
                    best_idx = i

            neg_long = statistics.mean([max(0.0, -lap.long_bins[best_idx]) for lap in recent])
            lat_mag = statistics.mean([abs(lap.lat_bins[best_idx]) for lap in recent])
            pos_long = statistics.mean([max(0.0, lap.long_bins[best_idx]) for lap in recent])
            phase, rec = self._phase_and_recommendation(neg_long, lat_mag, pos_long)

            peak_ref = reference[best_idx]
            peak_ach = achieved[best_idx]
            delta = max(0.0, peak_ref - peak_ach)

            under_laps = 0
            used_laps = 0
            for lap in valid_laps:
                val = lap.bins[best_idx]
                if val <= 0.05:
                    continue
                used_laps += 1
                if val < peak_ref * (1.0 - UNDERUSE_MARGIN / 2.0):
                    under_laps += 1
            consistency = (under_laps / used_laps * 100.0) if used_laps else 0.0
            trend_values = [lap.bins[best_idx] for lap in recent if lap.bins[best_idx] > 0.05]
            trend = self._trend_label(trend_values)

            results.append(
                UnderuseSegment(
                    start_percent=seg_start / BINS_PER_LAP,
                    end_percent=(seg_end + 1) / BINS_PER_LAP,
                    peak_percent=(best_idx + 0.5) / BINS_PER_LAP,
                    reference_g=peak_ref,
                    achieved_g=peak_ach,
                    delta_g=delta,
                    severity=self._severity_label(delta),
                    phase=phase,
                    recommendation=rec,
                    trend=trend,
                    consistency=consistency,
                    confidence=self.bin_confidence[best_idx],
                )
            )

        results.sort(key=lambda s: s.delta_g, reverse=True)
        return results

    def _feedback_lap_target(self) -> int:
        try:
            laps = int(self.laps_for_feedback_var.get())
        except (TypeError, ValueError, tk.TclError):
            laps = DEFAULT_LAPS_FOR_FEEDBACK
        laps = max(1, min(50, laps))
        try:
            current = int(self.laps_for_feedback_var.get())
        except Exception:
            current = laps
        if current != laps:
            self.laps_for_feedback_var.set(laps)
        return laps

    def _refresh_feedback_settings(self, _event: object = None) -> None:
        lap_target = self._feedback_lap_target()
        lap_label = "clean" if self.incident_free_only_var.get() else "completed"
        self.settings_hint_var.set(f"Coaching starts after {lap_target} {lap_label} lap(s).")

    def _open_quickstart_window(self, _event: object = None) -> None:
        if self.quickstart_window is not None and self.quickstart_window.winfo_exists():
            self.quickstart_window.deiconify()
            self.quickstart_window.lift()
            self.quickstart_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.quickstart_window = window
        window.title("Quickstart")
        window.configure(bg=BG)
        window.transient(self.root)
        window.resizable(False, False)
        window.attributes("-topmost", True)
        window.geometry(f"440x320+{self.root.winfo_x() + 90}+{self.root.winfo_y() + 90}")

        card = ttk.Frame(window, style="CardAlt.TFrame", padding=16)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        ttk.Label(card, text="Quickstart", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            card,
            text=QUICKSTART_TEXT,
            style="CoachBody.TLabel",
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(10, 12))
        def _close_quickstart(_event: object = None) -> None:
            if window.winfo_exists():
                window.destroy()
            self.quickstart_window = None

        ttk.Button(card, text="Close", command=_close_quickstart).pack(anchor="e")
        window.protocol("WM_DELETE_WINDOW", _close_quickstart)
        window.bind("<Escape>", _close_quickstart)

    def _toggle_mode(self, _event: object = None) -> None:
        self.compact_mode = not self.compact_mode
        self.btn_mode.configure(text=("Compact coaching (M)" if self.compact_mode else "Detailed coaching (M)"))

    def _toggle_minimal(self, _event: object = None) -> None:
        self.minimal_mode = not self.minimal_mode
        self._apply_layout_mode()

    def _toggle_sidebar(self, _event: object = None) -> None:
        self.sidebar_visible = not self.sidebar_visible
        self._apply_layout_mode()

    def _apply_layout_mode(self) -> None:
        if self.minimal_mode:
            self.sidebar.pack_forget()
            self.root.geometry("560x620")
            self.btn_minimal.configure(text="Exit minimal (V)")
            self.btn_circle.configure(text=("Dock circle (O)" if self.circle_window is not None and self.circle_window.winfo_exists() else "Pop-out circle (O)"))
            self.footer_var.set("Drag the window to position it  •  M: compact/detailed  •  V: exit")
        else:
            if self.sidebar_visible:
                self.sidebar.pack(side="left", fill="y", padx=(12, 0))
            else:
                self.sidebar.pack_forget()
            self.root.geometry("1180x760")
            self.btn_minimal.configure(text="Minimal view (V)")
            self.btn_circle.configure(text=("Dock circle (O)" if self.circle_window is not None and self.circle_window.winfo_exists() else "Pop-out circle (O)"))
            self.footer_var.set("M: compact/detailed  •  V: minimal view  •  O: pop-out circle  •  S: setup panel")

    def _format_compact_headline(self, segments: Sequence[UnderuseSegment], lap_label: str) -> Tuple[str, str]:
        if not segments:
            return (
                "No strong coaching hint yet",
                f"Keep driving to build the reference with more {lap_label} laps.",
            )
        top = segments[0]
        headline = f"Biggest gap: Δ{top.delta_g:.2f}g in {top.phase.lower()}"
        sub = f"{self._lapdist_hint(top.start_percent, top.end_percent, top.peak_percent)}  •  {top.recommendation}"
        return headline, sub

    def _update_coach_cards(self, segments: Sequence[UnderuseSegment], lap_label: str, coaching_ready: bool) -> None:
        if not coaching_ready:
            waiting = f"Waiting for more {lap_label} laps before coaching starts."
            meta = "Still learning your reference"
            self.coach_card_1.set("Learning", meta, waiting)
            self.coach_card_2.set("", "", "")
            self.coach_card_3.set("", "", "")
            return

        cards = [self.coach_card_1, self.coach_card_2, self.coach_card_3]
        if not segments:
            cards[0].set("No clear gap", "Good consistency right now", "No section showed a strong grip underuse pattern in the latest laps.")
            cards[1].set("", "", "")
            cards[2].set("", "", "")
            return

        for i, card in enumerate(cards):
            if i >= len(segments):
                card.set("", "", "")
                continue
            seg = segments[i]
            priority = "high" if seg.severity == "high" else "medium" if seg.severity == "medium" else "low"
            confidence = "good confidence" if seg.confidence else "low confidence"
            title = f"#{i+1}  {seg.phase}  •  Δ{seg.delta_g:.2f}g"
            meta = f"Priority {priority}  •  {seg.trend}  •  {seg.consistency:.0f}% of laps  •  {confidence}"
            body = f"{self._lapdist_hint(seg.start_percent, seg.end_percent, seg.peak_percent)}\n{seg.recommendation}"
            card.set(title, meta, body)

    def _render_circle(self, canvas: tk.Canvas, long_g: float, lat_g: float, usage_pct: float, *, compact: bool) -> None:
        canvas.delete("all")
        canvas.update_idletasks()
        w = max(100, int(canvas.winfo_width()))
        h = max(100, int(canvas.winfo_height()))
        cx = w // 2
        cy = h // 2 - (2 if compact else 8)
        radius = min(w, h) * (0.35 if compact else 0.34)

        canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=RING, width=2)
        for frac in (0.2, 0.4, 0.6, 0.8):
            rr = radius * frac
            canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=GRID, width=1)

        canvas.create_line(cx - radius, cy, cx + radius, cy, fill=GRID, width=1)
        canvas.create_line(cx, cy - radius, cx, cy + radius, fill=GRID, width=1)

        limit = max(0.8, self.estimated_limit_g)
        scale = radius / limit
        dot_x = cx + lat_g * scale
        dot_y = cy - long_g * scale

        usage_color = GOOD if usage_pct < 85 else MEDIUM if usage_pct < 97 else BAD
        canvas.create_line(cx, cy, dot_x, dot_y, fill=usage_color, width=3)
        dot_radius = 6 if compact else 7
        canvas.create_oval(dot_x - dot_radius, dot_y - dot_radius, dot_x + dot_radius, dot_y + dot_radius, fill=DOT, outline="")
        canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=SUBTEXT, outline="")

        if compact:
            canvas.create_text(cx, 20, text="Traction circle", fill=TEXT, font=("Segoe UI Semibold", 12))
            canvas.create_text(cx, h - 22, text=f"{usage_pct:.0f}% of est. limit", fill=usage_color, font=("Segoe UI Semibold", 10))
            return

        label_y = cy + radius + 26
        canvas.create_text(cx, 24, text="Traction circle", fill=TEXT, font=("Segoe UI Semibold", 13))
        canvas.create_text(cx, 46, text="LongAccel ↑ / brake    •    throttle ↓    •    LatAccel ← →", fill=SUBTEXT, font=("Segoe UI", 9))
        canvas.create_text(cx, label_y, text=f"Usage {usage_pct:.0f}% of estimated limit", fill=usage_color, font=("Segoe UI Semibold", 11))

        gauge_w = min(int(w * 0.64), 360)
        gauge_h = 12
        gx0 = cx - gauge_w // 2
        gy0 = label_y + 18
        gx1 = gx0 + gauge_w
        gy1 = gy0 + gauge_h
        canvas.create_rectangle(gx0, gy0, gx1, gy1, fill=PANEL_2, outline=BORDER)
        fill_x = gx0 + int(max(0.0, min(1.0, usage_pct / 100.0)) * gauge_w)
        canvas.create_rectangle(gx0, gy0, fill_x, gy1, fill=usage_color, outline="")

    def _draw_circle(self, long_g: float, lat_g: float, usage_pct: float) -> None:
        self._render_circle(self.canvas, long_g, lat_g, usage_pct, compact=False)
        if self.circle_canvas is not None and self.circle_window is not None and self.circle_window.winfo_exists():
            self._render_circle(self.circle_canvas, long_g, lat_g, usage_pct, compact=True)
            self.circle_caption_var.set(
                f"Usage {usage_pct:.0f}%  •  Long {long_g:+.2f}g  •  Lat {lat_g:+.2f}g"
            )

    def _update_disconnected_ui(self) -> None:
        self.status_var.set("Offline")
        self.headline_var.set("Waiting for telemetry...")
        self.subheadline_var.set("Open iRacing, join the session, and click Drive.")
        self.card_total.set("--", "no data")
        self.card_long.set("--", "no data")
        self.card_lat.set("--", "no data")
        self.card_limit.set("--", "no data")
        self.coach_card_1.set("No connection", "iRacing not detected", "When telemetry comes online, the app will resume learning automatically.")
        self.coach_card_2.set("", "", "")
        self.coach_card_3.set("", "", "")
        self._draw_circle(0.0, 0.0, 0.0)

    def _update(self) -> None:
        connected = self.ir.startup() if not getattr(self.ir, "is_initialized", False) else True
        if not connected:
            self._update_disconnected_ui()
            self.root.after(400, self._update)
            return

        self.status_var.set("Live")

        context_key, track_name, car_name, session_name = self._detect_context()
        if self.context_key and context_key != self.context_key:
            self._reset_for_new_context()
        self.context_key = context_key
        self.current_track = track_name
        self.current_car = car_name
        self.current_session = session_name
        self.context_var.set(f"{self.current_car}  •  {self.current_track}  •  {self.current_session}")

        long_accel = self._safe_float(self._read_var("LongAccel", 0.0))
        lat_accel = self._safe_float(self._read_var("LatAccel", 0.0))
        lap_num = int(self._safe_float(self._read_var("Lap", 0.0)))
        lap_dist_pct = self._safe_float(self._read_var("LapDistPct", 0.0))
        driver_idx = int(self._safe_float(self._read_var("DriverCarIdx", 0)))

        long_g = long_accel / G_CONSTANT
        lat_g = lat_accel / G_CONSTANT
        g_total = math.hypot(long_g, lat_g)

        self.recent_peaks.append(g_total)
        if len(self.recent_peaks) > 20:
            sorted_vals = sorted(self.recent_peaks)
            self.estimated_limit_g = sorted_vals[int(0.95 * (len(sorted_vals) - 1))]

        offtrack = self._is_offtrack(driver_idx)
        self._update_lap_storage(lap_num, lap_dist_pct, g_total, long_g, lat_g, offtrack)

        incident_free_only = self.incident_free_only_var.get()
        coaching_laps = [lap for lap in self.lap_history if lap.valid] if incident_free_only else list(self.lap_history)

        if self.external_reference_bins is not None:
            reference = self.external_reference_bins
            self.outliers_removed_last = 0
            self.bin_confidence = [v >= MIN_REFERENCE_G for v in reference]
        else:
            reference = self._compute_reference_by_bin(coaching_laps)

        lap_target = self._feedback_lap_target()
        laps_used_label = "clean" if incident_free_only else "completed"
        coaching_ready = len(coaching_laps) >= lap_target
        segments: List[UnderuseSegment] = []

        if coaching_ready:
            segments = self._detect_underuse_segments(coaching_laps, reference)
        elif self.external_reference_bins is None:
            segments = self._detect_underuse_segments(coaching_laps, reference)

        usage_pct = (g_total / max(0.5, self.estimated_limit_g)) * 100.0
        self.card_total.set(f"{g_total:.2f}g", f"{usage_pct:.0f}% of estimated limit")
        self.card_long.set(f"{long_g:+.2f}g", "brake + / throttle -")
        self.card_lat.set(f"{lat_g:+.2f}g", "left - / right +")
        self.card_limit.set(f"{self.estimated_limit_g:.2f}g", f"{len(coaching_laps)} {laps_used_label} lap(s)")

        if self.external_reference_bins is not None:
            if coaching_ready:
                self.reference_var.set(f'IBT: {os.path.basename(self.external_reference_path or 'reference')}  •  ready')
            else:
                self.reference_var.set(f"IBT: {os.path.basename(self.external_reference_path or 'reference')}  •  waiting for {lap_target - len(coaching_laps)}")
        else:
            confident_bins = sum(1 for x in self.bin_confidence if x)
            self.reference_var.set(f"Adaptive live reference  •  {confident_bins}/{BINS_PER_LAP} confident bins")

        if coaching_ready:
            headline, sub = self._format_compact_headline(segments, laps_used_label)
        else:
            remaining = lap_target - len(coaching_laps)
            headline = "Learning your reference"
            if self.external_reference_bins is not None:
                sub = f"IBT loaded. Drive {remaining} more {laps_used_label} lap(s) to unlock coaching."
            else:
                sub = f"Drive {remaining} more {laps_used_label} lap(s) to raise coaching confidence."

        self.headline_var.set(headline)
        self.subheadline_var.set(
            f"{sub}  •  invalid laps skipped: {self.invalid_laps_count}  •  outliers removed: {self.outliers_removed_last}"
        )

        self._update_coach_cards(segments, laps_used_label, coaching_ready)
        self._draw_circle(long_g, lat_g, usage_pct)
        self.root.after(UPDATE_MS, self._update)

    def run(self) -> None:
        self._draw_circle(0.0, 0.0, 0.0)
        self._update()
        self.root.mainloop()


def main() -> int:
    app = TractionCircleOverlay()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
