"""Native, offline view of the Nishizumi Drive Fair Share Calculator."""

from __future__ import annotations

import math
import tkinter as tk
import webbrowser
from tkinter import ttk

from ...core import TelemetryHub
from .engine import (
    FairShareResult,
    FairShareValidationError,
    calculate_fair_share,
    format_duration,
)


FAIR_SHARE_URL = "https://nishizumi-maho.github.io/Nishizumi-Calculator/"

BG = "#f5f7fb"
CARD = "#ffffff"
TEXT = "#172033"
MUTED = "#667085"
BORDER = "#d0d5dd"
ACCENT = "#1570ef"
ACCENT_SOFT = "#eff8ff"
SUCCESS = "#067647"
SUCCESS_SOFT = "#ecfdf3"
ERROR = "#b42318"


class FairSharePanel(ttk.Frame):
    feature_id = "tools.fair_share"
    api_version = "1.0"
    runs_in_background = False

    def __init__(self, parent: tk.Misc, telemetry: TelemetryHub):
        super().__init__(parent)
        self.telemetry = telemetry

        self.total_laps_var = tk.StringVar()
        self.drivers_var = tk.StringVar()
        self.avg_minutes_var = tk.StringVar()
        self.avg_seconds_var = tk.StringVar()
        self.race_hours_var = tk.StringVar()
        self.race_minutes_var = tk.StringVar()

        self.message_var = tk.StringVar(
            value="Fill in the team data to calculate."
        )
        self.summary_var = tk.StringVar(value="Result waiting to be calculated")
        self.link_status_var = tk.StringVar()
        self.result_vars = {
            "equal": tk.StringVar(value="—"),
            "fair": tk.StringVar(value="—"),
            "total_time": tk.StringVar(value="—"),
            "equal_time": tk.StringVar(value="—"),
            "fair_time": tk.StringVar(value="—"),
        }

        self._result_cards: list[tk.Frame] = []
        self._entry_widgets: list[ttk.Entry] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True)

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 10))
        tk.Label(
            header,
            text="Fair Share Calculator",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Local calculation for iRacing team events.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=18)
        body.grid_columnconfigure(0, weight=4, uniform="fair-share")
        body.grid_columnconfigure(1, weight=6, uniform="fair-share")
        body.grid_rowconfigure(0, weight=1)

        inputs = tk.Frame(
            body,
            bg=CARD,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        inputs.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_inputs(inputs)

        results = tk.Frame(body, bg=BG)
        results.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._build_results(results)

        self._build_online_alternative(root)

    def _build_inputs(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text="Race data",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=16, pady=(15, 10))

        primary = tk.Frame(parent, bg=CARD)
        primary.pack(fill="x", padx=16)
        for column in (0, 1):
            primary.grid_columnconfigure(column, weight=1, uniform="primary")
        self._field(
            primary,
            column=0,
            label="Total laps",
            variable=self.total_laps_var,
            right_pad=5,
        )
        self._field(
            primary,
            column=1,
            label="Declared drivers",
            variable=self.drivers_var,
            left_pad=5,
        )

        tk.Frame(parent, bg="#eaecf0", height=1).pack(
            fill="x", padx=16, pady=(14, 10)
        )
        tk.Label(
            parent,
            text="Optional estimates",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=16)

        average = tk.Frame(parent, bg=CARD)
        average.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(
            average,
            text="Average lap",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
            width=15,
            anchor="w",
        ).pack(side="left")
        self._compact_field(average, self.avg_minutes_var, "min")
        self._compact_field(average, self.avg_seconds_var, "s")

        duration = tk.Frame(parent, bg=CARD)
        duration.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(
            duration,
            text="Race duration",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
            width=15,
            anchor="w",
        ).pack(side="left")
        self._compact_field(duration, self.race_hours_var, "h")
        self._compact_field(duration, self.race_minutes_var, "min")

        tk.Label(
            parent,
            text="Without the lap total, use duration + average lap.",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=16, pady=(8, 0))

        actions = tk.Frame(parent, bg=CARD)
        actions.pack(fill="x", padx=16, pady=(15, 0))
        ttk.Button(
            actions,
            text="Calculate Fair Share",
            command=self.calculate,
        ).pack(side="left")
        ttk.Button(actions, text="Clear", command=self.clear).pack(
            side="left", padx=(8, 0)
        )

        self.message_label = tk.Label(
            parent,
            textvariable=self.message_var,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            anchor="nw",
            wraplength=330,
        )
        self.message_label.pack(fill="x", padx=16, pady=(12, 14))

        for entry in self._entry_widgets:
            entry.bind("<Return>", lambda _event: self.calculate())
            entry.bind("<KeyRelease>", self._input_changed)

    def _field(
        self,
        parent: tk.Frame,
        *,
        column: int,
        label: str,
        variable: tk.StringVar,
        left_pad: int = 0,
        right_pad: int = 0,
    ) -> None:
        box = tk.Frame(parent, bg=CARD)
        box.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=(left_pad, right_pad),
        )
        tk.Label(
            box,
            text=label,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 4))
        entry = ttk.Entry(box, textvariable=variable)
        entry.pack(fill="x")
        self._entry_widgets.append(entry)

    def _compact_field(
        self,
        parent: tk.Frame,
        variable: tk.StringVar,
        unit: str,
    ) -> None:
        entry = ttk.Entry(parent, textvariable=variable, width=7)
        entry.pack(side="left", padx=(0, 4))
        tk.Label(
            parent,
            text=unit,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 10))
        self._entry_widgets.append(entry)

    def _build_results(self, parent: tk.Frame) -> None:
        heading = tk.Frame(parent, bg=BG)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading,
            text="Result",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")
        tk.Label(
            heading,
            textvariable=self.summary_var,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")

        main = tk.Frame(parent, bg=BG)
        main.pack(fill="x")
        for column in (0, 1):
            main.grid_columnconfigure(column, weight=1, uniform="main-result")
        self._result_card(
            main,
            row=0,
            column=0,
            title="EQUAL SPLIT",
            variable=self.result_vars["equal"],
            value_color=ACCENT,
            bg=ACCENT_SOFT,
            border="#b2ddff",
            padx=(0, 5),
            large=True,
        )
        self._result_card(
            main,
            row=0,
            column=1,
            title="FAIR SHARE MINIMUM",
            variable=self.result_vars["fair"],
            value_color=SUCCESS,
            bg=SUCCESS_SOFT,
            border="#abefc6",
            padx=(5, 0),
            large=True,
        )

        times = tk.Frame(parent, bg=BG)
        times.pack(fill="x", pady=(10, 0))
        for column in (0, 1, 2):
            times.grid_columnconfigure(column, weight=1, uniform="time-result")
        for column, title, key in (
            (0, "TOTAL TIME", "total_time"),
            (1, "TIME / DRIVER", "equal_time"),
            (2, "MINIMUM / DRIVER", "fair_time"),
        ):
            self._result_card(
                times,
                row=0,
                column=column,
                title=title,
                variable=self.result_vars[key],
                value_color=TEXT,
                bg=CARD,
                border=BORDER,
                padx=(0 if column == 0 else 5, 0 if column == 2 else 5),
                large=False,
            )

        formula = tk.Frame(
            parent,
            bg=CARD,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        formula.pack(fill="x", pady=(10, 0))
        tk.Label(
            formula,
            text="REGRA USADA",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", padx=12, pady=(9, 2))
        tk.Label(
            formula,
            text="Fair Share = 25% of the equal split, rounded up.",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(0, 9))

    def _result_card(
        self,
        parent: tk.Frame,
        *,
        row: int,
        column: int,
        title: str,
        variable: tk.StringVar,
        value_color: str,
        bg: str,
        border: str,
        padx: tuple[int, int],
        large: bool,
    ) -> None:
        card = tk.Frame(
            parent,
            bg=bg,
            highlightthickness=1,
            highlightbackground=border,
        )
        card.grid(row=row, column=column, sticky="nsew", padx=padx)
        tk.Label(
            card,
            text=title,
            bg=bg,
            fg=MUTED,
            font=("Segoe UI Semibold", 8 if large else 7),
        ).pack(pady=(12 if large else 10, 3))
        tk.Label(
            card,
            textvariable=variable,
            bg=bg,
            fg=value_color,
            font=("Segoe UI Semibold", 21 if large else 12),
        ).pack(padx=8, pady=(0, 13 if large else 10))
        self._result_cards.append(card)

    def _build_online_alternative(self, parent: tk.Frame) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", padx=18, pady=(9, 11))
        tk.Label(
            footer,
            text="Alternativa online:",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="left")
        link = tk.Label(
            footer,
            text="Open the Nishizumi calculator",
            bg=BG,
            fg=ACCENT,
            activeforeground="#004eeb",
            cursor="hand2",
            font=("Segoe UI", 8, "underline"),
        )
        link.pack(side="left", padx=(5, 8))
        link.bind("<Button-1>", lambda _event: self.open_calculator())
        ttk.Button(footer, text="Copy link", command=self.copy_link).pack(
            side="left"
        )
        tk.Label(
            footer,
            textvariable=self.link_status_var,
            bg=BG,
            fg=SUCCESS,
            font=("Segoe UI", 8),
        ).pack(side="right")

    @staticmethod
    def _parse_number(
        raw: str,
        label: str,
        *,
        optional: bool = False,
        whole: bool = False,
    ) -> int | float | None:
        text = str(raw).strip().replace(",", ".")
        if not text:
            if optional:
                return None
            raise FairShareValidationError(f'Enter {label.lower()}.')
        try:
            value = float(text)
        except ValueError as exc:
            raise FairShareValidationError(f'{label} has to be a valid number.') from exc
        if not math.isfinite(value):
            raise FairShareValidationError(f'{label} has to be a valid number.')
        if whole:
            if not value.is_integer():
                raise FairShareValidationError(f'{label} has to be a whole number.')
            return int(value)
        return value

    def calculate(self) -> None:
        try:
            total_laps = self._parse_number(
                self.total_laps_var.get(),
                "Total laps",
                optional=True,
                whole=True,
            )
            drivers = self._parse_number(
                self.drivers_var.get(),
                "Number of drivers",
                whole=True,
            )
            avg_minutes = self._parse_number(
                self.avg_minutes_var.get() or "0",
                "Average lap minutes",
                whole=True,
            )
            avg_seconds = self._parse_number(
                self.avg_seconds_var.get() or "0",
                "Average lap seconds",
            )
            race_hours = self._parse_number(
                self.race_hours_var.get() or "0",
                "Race hours",
                whole=True,
            )
            race_minutes = self._parse_number(
                self.race_minutes_var.get() or "0",
                "Race minutes",
                whole=True,
            )
            result = calculate_fair_share(
                total_laps=total_laps,
                drivers=drivers,  # type: ignore[arg-type]
                average_lap_minutes=avg_minutes or 0,
                average_lap_seconds=avg_seconds or 0,
                race_hours=race_hours or 0,
                race_minutes=race_minutes or 0,
            )
        except FairShareValidationError as exc:
            self._reset_results()
            self.message_var.set(str(exc))
            self.message_label.config(fg=ERROR)
            return

        self._render_result(result)

    def _render_result(self, result: FairShareResult) -> None:
        self.result_vars["equal"].set(f'{result.equal_share_laps} laps')
        self.result_vars["fair"].set(f'{result.fair_share_laps} laps')
        self.result_vars["total_time"].set(
            format_duration(result.total_time_seconds)
        )
        self.result_vars["equal_time"].set(
            format_duration(result.equal_share_time_seconds)
        )
        self.result_vars["fair_time"].set(
            format_duration(result.fair_share_time_seconds)
        )
        source = "estimated from the duration" if result.estimated_laps_from_duration else "entered"
        self.summary_var.set(
            f'{result.total_laps} laps {source} • {result.drivers} drivers'
        )
        if result.total_time_seconds is None:
            self.message_var.set(
                "Fair share calculated. Enter the average lap to estimate the times."
            )
        else:
            self.message_var.set("Calculation finished, with time estimates.")
        self.message_label.config(fg=SUCCESS)

    def _reset_results(self) -> None:
        for variable in self.result_vars.values():
            variable.set("—")
        self.summary_var.set("Result waiting to be calculated")

    def _input_changed(self, _event: tk.Event | None = None) -> None:
        self.link_status_var.set("")
        self._reset_results()
        if any(value.get().strip() for value in (self.total_laps_var, self.drivers_var)):
            self.message_var.set("Click Calculate or press Enter.")
        else:
            self.message_var.set("Fill in the team data to calculate.")
        self.message_label.config(fg=MUTED)

    def clear(self) -> None:
        for variable in (
            self.total_laps_var,
            self.drivers_var,
            self.avg_minutes_var,
            self.avg_seconds_var,
            self.race_hours_var,
            self.race_minutes_var,
        ):
            variable.set("")
        self._reset_results()
        self.message_var.set("Fill in the team data to calculate.")
        self.message_label.config(fg=MUTED)
        self.link_status_var.set("")
        if self._entry_widgets:
            self._entry_widgets[0].focus_set()

    def open_calculator(self) -> None:
        try:
            opened = webbrowser.open_new_tab(FAIR_SHARE_URL)
            self.link_status_var.set(
                "Opened in the browser" if opened else "Copy the link to open it"
            )
        except Exception as exc:
            self.link_status_var.set(f'Browser unavailable: {exc}')

    def copy_link(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(FAIR_SHARE_URL)
            self.update_idletasks()
            self.link_status_var.set("Link copied")
        except tk.TclError as exc:
            self.link_status_var.set(f'Could not copy: {exc}')

    def start(self) -> None:
        """Keep the same lifecycle contract as the telemetry-backed tools."""

    def stop(self) -> None:
        """The local calculator owns no worker or external resource."""


__all__ = ["FAIR_SHARE_URL", "FairSharePanel"]
