"""Device allowlist and safe-mode helpers for Dominant Control."""

from __future__ import annotations

from typing import List

from tkinter import messagebox

from dominant_control.input_manager import input_manager
from dominant_control.ui.device_selector import DeviceSelector


class DeviceAllowlistManager:
    """Manage keyboard-only mode and joystick allowlist interactions."""

    def __init__(self, app):
        self.app = app

    def update_safe_mode(self):
        """Apply the keyboard-only flag to the input manager."""

        input_manager.set_safe_mode(self.app.use_keyboard_only.get())
        if not self.app.use_keyboard_only.get():
            input_manager.connect_allowed_devices(input_manager.allowed_devices)

    def trigger_safe_mode_update(self):
        """Prompt for restart when toggling keyboard-only mode."""

        new_value = self.app.use_keyboard_only.get()
        if messagebox.askokcancel(
            "Restart Required",
            "Restart is required to apply Keyboard Only mode. Confirm?",
        ):
            self.app.save_config()
            self.app.restart_program()
        else:
            self.app.use_keyboard_only.set(not new_value)
            self.app.save_config()
        self.update_safe_mode()

    def open_device_manager(self):
        """Open the joystick allowlist dialog when not in keyboard-only mode."""

        if self.app.use_keyboard_only.get():
            messagebox.showinfo(
                "Keyboard Mode",
                "Disable 'Keyboard Only Mode' to manage joystick devices.",
            )
            return

        DeviceSelector(
            self.app.root,
            input_manager.allowed_devices,
            self.update_allowed_devices,
        )

    def update_allowed_devices(self, new_list: List[str]):
        """Persist and reconnect to the selected allowlist."""

        input_manager.allowed_devices = list(new_list)
        input_manager.connect_allowed_devices(input_manager.allowed_devices)
        self.app.save_config()

