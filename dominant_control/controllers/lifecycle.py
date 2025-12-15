"""Lifecycle helpers for restart/config reset flows."""

from __future__ import annotations

import os
from tkinter import messagebox

from dominant_control.config import CONFIG_FILE, mark_pending_scan


class LifecycleManager:
    """Handle restart-related flows separate from the main app class."""

    def __init__(self, app):
        self.app = app

    def handle_session_change(self, session_type: str) -> bool:
        """Handle session transitions and restart if entering a race."""

        new_type = session_type or ""

        if new_type != self.app.last_session_type:
            self.app.last_session_type = new_type

            if self.app.skip_race_restart_once and new_type == "Race":
                self.app.skip_race_restart_once = False
                return False

            if self.app.auto_restart_on_race.get() and new_type == "Race":
                self.app.pending_scan_on_start = True
                mark_pending_scan()
                self.app.save_config()
                self.app.restart_program()
                return True

        return False

    def perform_pending_scan(self):
        """Execute a deferred scan request set before restarting."""

        if self.app.consume_pending_scan():
            self.app.pending_scan_on_start = True

        if self.app.pending_scan_on_start:
            self.app.skip_race_restart_once = True
            self.app.pending_scan_on_start = False
            self.app.save_config()
            self.app.root.after(50, self.app.scan_driver_controls)

    def restore_defaults(self):
        """Delete the configuration file and restart the app after confirmation."""

        if not messagebox.askyesno(
            "Restore Defaults",
            "This will delete your configuration file and restart the app. Continue?",
        ):
            return

        try:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
        except Exception as exc:  # noqa: PERF203
            messagebox.showerror(
                "Error",
                f"Failed to delete config: {exc}",
            )
            return

        messagebox.showinfo(
            "Defaults Restored",
            "Configuration reset. The application will restart now.",
        )
        self.app.restart_program()

