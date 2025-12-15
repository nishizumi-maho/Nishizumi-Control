from __future__ import annotations

from typing import Any, Dict, List, Tuple

from dominant_control.config import DEFAULT_OVERLAY_FEEDBACK


class PresetManager:
    """Handle preset CRUD, UI combos, and auto-detection."""

    def __init__(self, app: Any):
        self.app = app
        self.saved_presets: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_config: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_feedback: Dict[str, Dict[str, float]] = {}
        self.auto_load_attempted: set = set()
        self._last_auto_pair: Tuple[str, str] = ("", "")

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def update_preset_ui(self):
        """Update car/track combo boxes."""
        if not self.app.combo_car:
            return

        cars = sorted(list(self.saved_presets.keys()))
        self.app.combo_car["values"] = [c for c in cars if c]

        if self.app.current_car and self.app.current_car in cars:
            self.app.combo_car.set(self.app.current_car)
            self.on_car_selected(None)

    def on_car_selected(self, _event):
        """Handle car selection."""
        if not self.app.combo_car or not self.app.combo_track:
            return

        car = self.app.combo_car.get()
        if car in self.saved_presets:
            tracks = sorted([
                t for t in self.saved_presets[car].keys()
                if t not in {"_overlay", "_overlay_feedback"}
            ])
            self.app.combo_track["values"] = tracks
        else:
            self.app.combo_track["values"] = []

        self.app.current_car = car

    def auto_fill_ui(self, car: str, track: str):
        """Auto-fill car and track in UI."""
        if not self.app.combo_car or not self.app.combo_track:
            return

        self.app.current_car = car
        self.app.current_track = track

        self.app.combo_car.set(car)
        self.on_car_selected(None)
        self.app.combo_track.set(track)

    # ------------------------------------------------------------------
    # Preset CRUD
    # ------------------------------------------------------------------
    def save_preset(self):
        """Save current configuration as preset."""
        app = self.app
        car = app.combo_car.get().strip()
        track = app.combo_track.get().strip()

        if not car or not track:
            app.messagebox.showwarning("Error", "Define Car and Track.")
            return

        # Collect overlay config
        app.overlay_tab.collect_for_car(car)

        if car not in self.car_overlay_feedback:
            self.car_overlay_feedback[car] = DEFAULT_OVERLAY_FEEDBACK.copy()

        current_data = {
            "active_vars": app.active_vars,
            "tabs": {},
            "combo": app.combo_tab.get_config() if app.combo_tab else {},
        }

        for var_name, tab in app.tabs.items():
            current_data["tabs"][var_name] = tab.get_config()

        if car not in self.saved_presets:
            self.saved_presets[car] = {}

        self.saved_presets[car][track] = current_data

        if car not in self.car_overlay_config:
            self.car_overlay_config[car] = {}
        self.saved_presets[car]["_overlay"] = self.car_overlay_config[car]
        self.saved_presets[car]["_overlay_feedback"] = self.car_overlay_feedback.get(
            car, DEFAULT_OVERLAY_FEEDBACK.copy()
        )

        app.save_config()
        self.auto_load_attempted.discard((car, track))
        if (car, track) == (app.current_car, app.current_track):
            app.register_current_listeners()
        self.update_preset_ui()
        app.messagebox.showinfo("Saved", f"Preset saved for {car} @ {track}")

    def load_specific_preset(self, car: str, track: str):
        """Load a specific car/track preset."""
        app = self.app
        if car not in self.saved_presets or track not in self.saved_presets[car]:
            return

        data = self.saved_presets[car][track]
        active_vars = data.get("active_vars")
        if active_vars:
            app.rebuild_tabs(active_vars)

        tabs_data = data.get("tabs", {})
        for var_name, config in tabs_data.items():
            if var_name in app.tabs:
                app.tabs[var_name].set_config(config)

        combo_data = data.get("combo")
        if app.combo_tab and combo_data:
            app.combo_tab.set_config(combo_data)

        overlay_config = self.saved_presets[car].get("_overlay", {})
        self.car_overlay_config[car] = overlay_config
        self.car_overlay_feedback[car] = self.saved_presets[car].get(
            "_overlay_feedback",
            self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy()),
        )
        app.overlay_tab.load_for_car(car, app.active_vars, overlay_config)

        app.register_current_listeners()
        print(f"[Preset] Loaded {car} / {track}")

    def load_selected_preset(self):
        """Load the preset selected in the combo boxes."""
        if not self.app.combo_car or not self.app.combo_track:
            return

        car = self.app.combo_car.get()
        track = self.app.combo_track.get()

        if not car or not track:
            return

        self.app.current_car = car
        self.app.current_track = track
        self.load_specific_preset(car, track)

    def delete_preset(self):
        """Delete selected preset."""
        if not self.app.combo_car or not self.app.combo_track:
            return

        car = self.app.combo_car.get()
        track = self.app.combo_track.get()

        if not car or not track:
            return
        if car in self.saved_presets and track in self.saved_presets[car]:
            if not self.app.messagebox.askyesno(
                "Confirm", f"Delete preset for {car} @ {track}?"
            ):
                return

            del self.saved_presets[car][track]

            if not [
                t for t in self.saved_presets[car].keys()
                if t not in {"_overlay", "_overlay_feedback"}
            ]:
                del self.saved_presets[car]
                self.car_overlay_config.pop(car, None)
                self.car_overlay_feedback.pop(car, None)

            self.app.save_config()
            self.update_preset_ui()
            self.app.combo_track.set("")
            self.app.current_track = ""

    # ------------------------------------------------------------------
    # Auto detect loop
    # ------------------------------------------------------------------
    def start_auto_preset_loop(self):
        self.app.root.after(2000, self.auto_preset_loop)

    def auto_preset_loop(self):
        """Background loop for auto-detecting car/track."""
        app = self.app
        if not (app.auto_detect.get() or app.auto_restart_on_race.get()):
            app.root.after(2000, self.auto_preset_loop)
            return

        try:
            with app.ir_lock:
                if not getattr(app.ir, "is_initialized", False):
                    app.ir.startup()

            if not getattr(app.ir, "is_initialized", False):
                app.root.after(2000, self.auto_preset_loop)
                return

            session_type = self._get_session_type()
            if app.lifecycle_manager.handle_session_change(session_type):
                return

            if not app.auto_detect.get():
                app.root.after(2000, self.auto_preset_loop)
                return

            driver_info = app.ir["DriverInfo"]
            if not driver_info:
                app.root.after(2000, self.auto_preset_loop)
                return
            idx = driver_info["DriverCarIdx"]
            raw_car = driver_info["Drivers"][idx]["CarScreenName"]

            weekend = app.ir["WeekendInfo"]
            if not weekend:
                app.root.after(2000, self.auto_preset_loop)
                return
            raw_track = weekend["TrackDisplayName"]

            car_clean = "".join(c for c in raw_car if c.isalnum() or c in " -_")
            track_clean = "".join(c for c in raw_track if c.isalnum() or c in " -_")

            current_pair = (car_clean, track_clean)

            if current_pair != self._last_auto_pair:
                self._last_auto_pair = current_pair
                app.current_car, app.current_track = car_clean, track_clean
                print(f"[AutoDetect] {car_clean} @ {track_clean}")

                self.auto_fill_ui(car_clean, track_clean)

                if car_clean not in self.saved_presets:
                    self.saved_presets[car_clean] = {}

                if "_overlay" not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]["_overlay"] = self.car_overlay_config.get(
                        car_clean, {}
                    )

                if "_overlay_feedback" not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]["_overlay_feedback"] = (
                        self.car_overlay_feedback.get(
                            car_clean, DEFAULT_OVERLAY_FEEDBACK.copy()
                        )
                    )

                if track_clean not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean][track_clean] = {
                        "active_vars": None,
                        "tabs": {},
                        "combo": {},
                    }

                app.save_config()

                if (car_clean, track_clean) not in self.auto_load_attempted:
                    self.auto_load_attempted.add((car_clean, track_clean))
                    if self.saved_presets[car_clean][track_clean].get("active_vars"):
                        self.load_specific_preset(car_clean, track_clean)

        except Exception as e:  # noqa: PERF203
            print(f"[AutoDetect] Error: {e}")

        app.root.after(2000, self.auto_preset_loop)

    def _get_session_type(self) -> str:
        """Return the current session type if available."""
        app = self.app
        try:
            session_info = app.ir["SessionInfo"]
        except Exception:
            return ""

        session_num = None
        try:
            session_num = int(app.ir["SessionNum"])
        except Exception:
            pass

        try:
            sessions = session_info.get("Sessions") if session_info else None
            if isinstance(sessions, list):
                if session_num is not None and 0 <= session_num < len(sessions):
                    session_type = sessions[session_num].get("SessionType", "")
                    if session_type:
                        return session_type

                for entry in sessions:
                    session_type = entry.get("SessionType", "")
                    if session_type:
                        return session_type
        except Exception:
            pass

        return ""

    def ensure_overlay_defaults(self, car: str):
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        if "_overlay" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay"] = self.car_overlay_config.get(car, {})
        if "_overlay_feedback" not in self.saved_presets[car]:
            self.saved_presets[car]["_overlay_feedback"] = self.car_overlay_feedback.get(
                car, DEFAULT_OVERLAY_FEEDBACK.copy()
            )
        self.car_overlay_config[car] = self.saved_presets[car]["_overlay"]
        self.car_overlay_feedback[car] = self.saved_presets[car]["_overlay_feedback"]

    def update_overlay_config(self, car: str):
        """Update overlay tab binding for the given car."""
        self.ensure_overlay_defaults(car)
        self.app.overlay_tab.load_for_car(
            car, self.app.active_vars, self.car_overlay_config[car]
        )

    def update_preset_active_vars(self, car: str, track: str, active_vars: List[Tuple[str, bool]]):
        self.ensure_overlay_defaults(car)
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        if track not in self.saved_presets[car]:
            self.saved_presets[car][track] = {
                "active_vars": active_vars,
                "tabs": {},
                "combo": {},
            }
        else:
            self.saved_presets[car][track]["active_vars"] = active_vars
        self.car_overlay_config[car] = self.saved_presets[car]["_overlay"]
        self.car_overlay_feedback[car] = self.saved_presets[car]["_overlay_feedback"]
