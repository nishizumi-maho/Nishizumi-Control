#!/usr/bin/env python3
"""Very simple armed pit calibrator for iRacing.

Behavior:
- user arms the next stop
- during that armed stop, the app shows live timers for total, service, and base
- after leaving pit road, values freeze on screen so the user can write them down
- optional manual tire timing with one button
- no profile saving, no history, no automatic magic
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Optional, Tuple

import irsdk

UPDATE_MS = 16
MAX_REASONABLE_RATE_LPS = 8.0
MIN_REASONABLE_RATE_LPS = 0.05


class PitCalibratorApp:
    BG = "#0f1115"
    PANEL = "#171a21"
    PANEL_ALT = "#1c2230"
    BORDER = "#374151"
    TEXT = "#f3f4f6"
    MUTED = "#9ca3af"
    ACCENT = "#8ff0a4"
    BTN = "#243041"
    BTN_ACTIVE = "#7f1d1d"

    def __init__(self) -> None:
        self.ir = irsdk.IRSDK()

        self.root = tk.Tk()
        self.root.title("Nishizumi Pit Calibrator")
        self.root.configure(bg=self.BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)
        self.root.geometry("450x390+80+80")
        self.root.minsize(420, 340)

        self._drag_offset_x = 0
        self._drag_offset_y = 0

        self.connection_var = tk.StringVar(value="Connecting to iRacing…")
        self.context_var = tk.StringVar(value="Car: -- | Track: --")
        self.arm_state_var = tk.StringVar(value="Arming: off")
        self.status_var = tk.StringVar(value="Arm the next stop to start measuring.")

        self.live_total_var = tk.StringVar(value="Live total: --")
        self.live_service_var = tk.StringVar(value="Live service: --")
        self.live_base_var = tk.StringVar(value="Live base: --")
        self.live_fuel_var = tk.StringVar(value="Live fuel added: --")
        self.live_rate_var = tk.StringVar(value="Live fuel rate: --")
        self.live_tire_var = tk.StringVar(value="Live manual tire time: --")
        self.pending_fuel_var = tk.StringVar(value="Pending pit fuel: --")

        self.saved_total_var = tk.StringVar(value="Saved total: --")
        self.saved_service_var = tk.StringVar(value="Saved service: --")
        self.saved_base_var = tk.StringVar(value="Saved base: --")
        self.saved_fuel_var = tk.StringVar(value="Saved fuel added: --")
        self.saved_rate_var = tk.StringVar(value="Saved fuel rate: --")
        self.saved_tire_var = tk.StringVar(value="Saved tire time: --")

        self.active_car_name = "Unknown car"
        self.active_track_name = "Unknown track"

        self.armed = False
        self.stop: Optional[dict] = None
        self._last_wall_time: Optional[float] = None
        self._last_fuel_level: Optional[float] = None

        self._build_ui()

    # ---------------------------- UI ----------------------------

    def _build_ui(self) -> None:
        shell = tk.Frame(self.root, bg=self.BG, highlightthickness=1, highlightbackground=self.BORDER)
        shell.pack(fill="both", expand=True)

        title_bar = tk.Frame(shell, bg=self.BG, padx=10, pady=8)
        title_bar.pack(fill="x")
        title_bar.bind("<ButtonPress-1>", self._start_move)
        title_bar.bind("<B1-Motion>", self._on_move)

        title_stack = tk.Frame(title_bar, bg=self.BG)
        title_stack.pack(side="left", fill="x", expand=True)
        title_stack.bind("<ButtonPress-1>", self._start_move)
        title_stack.bind("<B1-Motion>", self._on_move)

        title = tk.Label(
            title_stack,
            text="Nishizumi Pit Calibrator",
            bg=self.BG,
            fg=self.ACCENT,
            font=("Segoe UI", 11, "bold"),
        )
        title.pack(anchor="w")
        title.bind("<ButtonPress-1>", self._start_move)
        title.bind("<B1-Motion>", self._on_move)

        subtitle = tk.Label(
            title_stack,
            text="arm once • watch timers live • they freeze after pit exit",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 8),
        )
        subtitle.pack(anchor="w")
        subtitle.bind("<ButtonPress-1>", self._start_move)
        subtitle.bind("<B1-Motion>", self._on_move)

        btns = tk.Frame(title_bar, bg=self.BG)
        btns.pack(side="right")

        self.arm_btn = tk.Button(
            btns,
            text="ARM",
            command=self._toggle_arm,
            bg=self.BTN,
            fg=self.TEXT,
            activebackground="#334155",
            activeforeground="white",
            relief="flat",
            bd=0,
            width=5,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
        )
        self.arm_btn.pack(side="left", padx=(0, 4))

        self.tire_btn = tk.Button(
            btns,
            text="TIRE",
            command=self._mark_tire_done,
            bg=self.BTN,
            fg=self.TEXT,
            activebackground="#334155",
            activeforeground="white",
            relief="flat",
            bd=0,
            width=5,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
        )
        self.tire_btn.pack(side="left", padx=(0, 4))

        close_btn = tk.Button(
            btns,
            text="✕",
            command=self.root.destroy,
            bg=self.BTN,
            fg=self.TEXT,
            activebackground="#334155",
            activeforeground="white",
            relief="flat",
            bd=0,
            width=3,
            cursor="hand2",
            font=("Segoe UI Symbol", 10, "bold"),
        )
        close_btn.pack(side="left")

        top = tk.Frame(shell, bg=self.PANEL_ALT, padx=12, pady=10, highlightthickness=1, highlightbackground=self.BORDER)
        top.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(top, textvariable=self.connection_var, bg=self.PANEL_ALT, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(top, textvariable=self.context_var, bg=self.PANEL_ALT, fg=self.TEXT, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 0))
        tk.Label(top, textvariable=self.arm_state_var, bg=self.PANEL_ALT, fg=self.ACCENT, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(top, textvariable=self.status_var, bg=self.PANEL_ALT, fg=self.TEXT, font=("Segoe UI", 9), wraplength=410, justify="left").pack(anchor="w", pady=(2, 0))

        live = tk.Frame(shell, bg=self.PANEL, padx=12, pady=10, highlightthickness=1, highlightbackground=self.BORDER)
        live.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(live, text="Live armed stop", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        for var in (
            self.live_total_var,
            self.live_service_var,
            self.live_base_var,
            self.live_fuel_var,
            self.live_rate_var,
            self.live_tire_var,
            self.pending_fuel_var,
        ):
            tk.Label(live, textvariable=var, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", pady=(4, 0))

        saved = tk.Frame(shell, bg=self.PANEL, padx=12, pady=10, highlightthickness=1, highlightbackground=self.BORDER)
        saved.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(saved, text="Frozen result", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        for var in (
            self.saved_total_var,
            self.saved_service_var,
            self.saved_base_var,
            self.saved_fuel_var,
            self.saved_rate_var,
            self.saved_tire_var,
        ):
            tk.Label(saved, textvariable=var, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", pady=(4, 0))

    def _start_move(self, event: tk.Event) -> None:
        self._drag_offset_x = event.x_root - self.root.winfo_x()
        self._drag_offset_y = event.y_root - self.root.winfo_y()

    def _on_move(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_offset_x
        y = event.y_root - self._drag_offset_y
        self.root.geometry(f"+{x}+{y}")

    # ---------------------------- helpers ----------------------------

    @staticmethod
    def _safe_float(value, default: Optional[float] = None) -> Optional[float]:
        try:
            if value is None:
                return default
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed

    @staticmethod
    def _format_seconds(value: Optional[float]) -> str:
        return "--" if value is None else f"{value:.2f}s"

    @staticmethod
    def _format_liters(value: Optional[float]) -> str:
        return "--" if value is None else f"{value:.2f} L"

    @staticmethod
    def _format_rate(value: Optional[float]) -> str:
        return "--" if value is None else f"{value:.3f} L/s"

    def _read_var(self, name: str, default=None):
        try:
            return self.ir[name]
        except Exception:
            return default

    def _read_yaml(self, name: str) -> Optional[dict]:
        value = self._read_var(name)
        return value if isinstance(value, dict) else None

    def _ensure_connection(self) -> bool:
        if self.ir.is_initialized and self.ir.is_connected:
            return True
        if self.ir.is_initialized and not self.ir.is_connected:
            try:
                self.ir.shutdown()
            except Exception:
                pass
        try:
            ok = self.ir.startup()
        except Exception:
            ok = False
        return bool(ok and self.ir.is_initialized and self.ir.is_connected)

    def _driver_identity(self) -> Tuple[str, str]:
        driver_info = self._read_yaml("DriverInfo") or {}
        driver_car_idx = driver_info.get("DriverCarIdx")
        drivers = driver_info.get("Drivers")
        if isinstance(drivers, list):
            for row in drivers:
                if not isinstance(row, dict):
                    continue
                if row.get("CarIdx") != driver_car_idx:
                    continue
                car_name = str(row.get("CarScreenNameShort") or row.get("CarScreenName") or row.get("CarPath") or "Unknown car")
                car_id = str(row.get("CarPath") or row.get("CarClassShortName") or driver_car_idx or "unknown_car")
                return car_id, car_name
        return "unknown_car", "Unknown car"

    def _track_identity(self) -> Tuple[str, str]:
        weekend = self._read_yaml("WeekendInfo") or {}
        track_name = str(weekend.get("TrackDisplayName") or weekend.get("TrackName") or weekend.get("TrackID") or "Unknown track")
        track_id = str(weekend.get("TrackName") or weekend.get("TrackDisplayShortName") or weekend.get("TrackID") or "unknown_track")
        return track_id, track_name

    def _service_active(self, on_pit_road: bool) -> bool:
        if not on_pit_road:
            return False
        if bool(self._read_var("PitstopActive", 0)):
            return True
        status = self._safe_float(self._read_var("PlayerCarPitSvStatus"), default=None)
        return status is not None and int(status) == 1

    def _clear_live(self) -> None:
        self.live_total_var.set("Live total: --")
        self.live_service_var.set("Live service: --")
        self.live_base_var.set("Live base: --")
        self.live_fuel_var.set("Live fuel added: --")
        self.live_rate_var.set("Live fuel rate: --")
        self.live_tire_var.set("Live manual tire time: --")
        self.pending_fuel_var.set("Pending pit fuel: --")

    # ---------------------------- actions ----------------------------

    def _toggle_arm(self) -> None:
        self.armed = not self.armed
        if self.armed:
            self.arm_btn.configure(bg=self.BTN_ACTIVE)
            self.arm_state_var.set("Arming: ON — next stop is the one that counts")
            self.status_var.set("Armed. Enter pit lane and watch the numbers move live. Press TIRE when the tire change finishes.")
            self.stop = None
            self._last_wall_time = None
            self._last_fuel_level = None
            self._clear_live()
        else:
            self.arm_btn.configure(bg=self.BTN)
            self.arm_state_var.set("Arming: off")
            self.status_var.set("Arm cancelled.")
            self.stop = None
            self._last_wall_time = None
            self._last_fuel_level = None
            self._clear_live()

    def _mark_tire_done(self) -> None:
        if not self.stop or not self.stop.get("active"):
            self.status_var.set("No armed stop running. TIRE only works during the armed stop.")
            return
        tire_time = float(self.stop.get("live_service", 0.0))
        self.stop["manual_tire_time"] = tire_time
        self.live_tire_var.set(f"Live manual tire time: {self._format_seconds(tire_time)}")
        self.status_var.set(f"Manual tire time captured at {tire_time:.2f}s.")

    # ---------------------------- stop flow ----------------------------

    def _start_armed_stop(self, now: float, fuel_level: Optional[float]) -> None:
        self.stop = {
            "active": True,
            "entry_wall": now,
            "service_wall_start": None,
            "service_running": False,
            "live_total": 0.0,
            "live_service": 0.0,
            "live_base": 0.0,
            "fuel_start": fuel_level,
            "fuel_added": 0.0,
            "fuel_rate_samples": [],
            "manual_tire_time": None,
            "pending_fuel_at_entry": self._safe_float(self._read_var("PitSvFuel"), default=None),
        }
        self._last_wall_time = now
        self._last_fuel_level = fuel_level
        self.status_var.set("Armed stop started. Total, service and base are now counting live.")

    def _update_armed_stop(self, now: float, fuel_level: Optional[float], on_pit_road: bool) -> None:
        if not self.stop:
            return

        stop = self.stop
        dt = 0.0
        if self._last_wall_time is not None:
            dt = max(0.0, now - self._last_wall_time)

        service_now = self._service_active(on_pit_road)

        if on_pit_road:
            stop["live_total"] += dt
            if service_now:
                stop["live_service"] += dt
            stop["live_base"] = max(0.0, float(stop["live_total"]) - float(stop["live_service"]))

        if fuel_level is not None and stop.get("fuel_start") is not None:
            stop["fuel_added"] = max(0.0, fuel_level - float(stop["fuel_start"]))

        if (
            service_now
            and dt > 0
            and fuel_level is not None
            and self._last_fuel_level is not None
        ):
            df = fuel_level - self._last_fuel_level
            if df > 0.01:
                rate = df / dt
                if MIN_REASONABLE_RATE_LPS <= rate <= MAX_REASONABLE_RATE_LPS:
                    stop["fuel_rate_samples"].append(rate)

        samples = stop.get("fuel_rate_samples") or []
        avg_rate = (sum(samples) / len(samples)) if samples else None

        self.live_total_var.set(f"Live total: {self._format_seconds(stop.get('live_total'))}")
        self.live_service_var.set(f"Live service: {self._format_seconds(stop.get('live_service'))}")
        self.live_base_var.set(f"Live base: {self._format_seconds(stop.get('live_base'))}")
        self.live_fuel_var.set(f"Live fuel added: {self._format_liters(stop.get('fuel_added'))}")
        self.live_rate_var.set(f"Live fuel rate: {self._format_rate(avg_rate)}")
        self.live_tire_var.set(f"Live manual tire time: {self._format_seconds(stop.get('manual_tire_time'))}")
        self.pending_fuel_var.set(f"Pending pit fuel: {self._format_liters(stop.get('pending_fuel_at_entry'))}")

        self._last_wall_time = now
        self._last_fuel_level = fuel_level

    def _finish_armed_stop(self) -> None:
        if not self.stop:
            return

        stop = self.stop
        samples = stop.get("fuel_rate_samples") or []
        avg_rate = (sum(samples) / len(samples)) if samples else None
        total = self._safe_float(stop.get("live_total"), default=None)
        service = self._safe_float(stop.get("live_service"), default=None)
        base = self._safe_float(stop.get("live_base"), default=None)
        fuel_added = self._safe_float(stop.get("fuel_added"), default=None)
        tire = self._safe_float(stop.get("manual_tire_time"), default=None)

        self.saved_total_var.set(f"Saved total: {self._format_seconds(total)}")
        self.saved_service_var.set(f"Saved service: {self._format_seconds(service)}")
        self.saved_base_var.set(f"Saved base: {self._format_seconds(base)}")
        self.saved_fuel_var.set(f"Saved fuel added: {self._format_liters(fuel_added)}")
        self.saved_rate_var.set(f"Saved fuel rate: {self._format_rate(avg_rate)}")
        self.saved_tire_var.set(f"Saved tire time: {self._format_seconds(tire)}")

        self.status_var.set("Armed stop finished. Values are frozen on screen so you can write them down.")
        self.arm_btn.configure(bg=self.BTN)
        self.arm_state_var.set("Arming: off")
        self.armed = False
        stop["active"] = False
        self.stop = None
        self._last_wall_time = None
        self._last_fuel_level = None

    # ---------------------------- update loop ----------------------------

    def _show_disconnected(self) -> None:
        self.connection_var.set("Not connected to iRacing. Open the sim and click Drive.")
        self.context_var.set("Car: -- | Track: --")
        if not self.armed and not self.stop:
            self.status_var.set("Arm the next stop to start measuring.")
        self.pending_fuel_var.set("Pending pit fuel: --")

    def _tick(self) -> None:
        _, self.active_car_name = self._driver_identity()
        _, self.active_track_name = self._track_identity()
        self.context_var.set(f"Car: {self.active_car_name} | Track: {self.active_track_name}")

        on_pit_road = bool(self._read_var("OnPitRoad", 0))
        fuel_level = self._safe_float(self._read_var("FuelLevel"), default=None)
        pending = self._safe_float(self._read_var("PitSvFuel"), default=None)
        now = time.perf_counter()

        if self.armed and self.stop is None and on_pit_road:
            self._start_armed_stop(now, fuel_level)

        if self.stop is not None:
            if on_pit_road:
                self._update_armed_stop(now, fuel_level, on_pit_road)
            else:
                self._finish_armed_stop()
        else:
            self.pending_fuel_var.set(f"Pending pit fuel: {self._format_liters(pending)}")

        if self.armed and self.stop is None and not on_pit_road:
            self.arm_state_var.set("Arming: ON — next stop is the one that counts")
        elif not self.armed:
            self.arm_state_var.set("Arming: off")

    def _update(self) -> None:
        try:
            if not self._ensure_connection():
                self._show_disconnected()
                self.root.after(500, self._update)
                return

            self.connection_var.set(f"Connected to iRacing | refresh {UPDATE_MS} ms")
            self.ir.freeze_var_buffer_latest()
            try:
                self._tick()
            finally:
                self.ir.unfreeze_var_buffer_latest()
        except Exception as exc:
            self.status_var.set(f"Runtime error: {type(exc).__name__}: {exc}")

        self.root.after(UPDATE_MS, self._update)

    def run(self) -> None:
        self._update()
        self.root.mainloop()


def main() -> int:
    app = PitCalibratorApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
