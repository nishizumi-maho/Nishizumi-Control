"""Input manager for keyboard and joystick devices."""

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import keyboard

from .dependencies import HAS_PYGAME, pygame
from .watchdog import Watchdog


class InputManager:
    """
    Manages input from keyboard and joystick devices.

    Supports safe mode (keyboard only) and selective device enabling.
    """

    def __init__(self):
        self.joysticks: List[Any] = []
        self.listeners: Dict[str, Callable] = {}  # Input code -> callback
        self.active: bool = False
        self.allowed_devices: List[str] = []
        self.safe_mode: bool = False
        self._input_thread: Optional[threading.Thread] = None
        self._input_watchdog = Watchdog(
            "InputManager", interval_s=2.5, timeout_s=8.0, on_trip=self._restart_input_loop
        )
        # Prevent SDL from grabbing exclusive haptics/XInput handles when we only need button input
        self.preserve_game_ffb: bool = os.getenv("DOMINANTCONTROL_PRESERVE_FFB", "1") == "1"

        if HAS_PYGAME:
            try:
                if self.preserve_game_ffb:
                    # Prefer the legacy/direct drivers instead of HIDAPI/XInput to avoid taking over FFB
                    os.environ.setdefault("SDL_HINT_JOYSTICK_HIDAPI", "0")
                    os.environ.setdefault("SDL_HINT_XINPUT_ENABLED", "0")
                    os.environ.setdefault("SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
                pygame.init()
                pygame.joystick.init()
                self._start_input_loop()
            except Exception as e:
                print(f"[InputManager] Pygame init error: {e}")

    def set_safe_mode(self, enabled: bool):
        """
        Enable/disable safe mode (keyboard only).

        Args:
            enabled: True for keyboard only, False to enable joysticks
        """
        self.safe_mode = enabled
        if self.safe_mode:
            if HAS_PYGAME:
                try:
                    pygame.quit()
                except Exception:
                    pass
        else:
            if HAS_PYGAME:
                try:
                    if not pygame.get_init():
                        pygame.init()
                    if not pygame.joystick.get_init():
                        pygame.joystick.init()
                    self._start_input_loop()
                except Exception as e:
                    print(f"[InputManager] Error reactivating pygame: {e}")

    def get_all_devices(self) -> List[Tuple[int, str]]:
        """
        Get all available joystick devices.

        Returns:
            List of (device_id, device_name) tuples
        """
        if self.safe_mode or not HAS_PYGAME:
            return []

        try:
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            devices = []
            count = pygame.joystick.get_count()

            for i in range(count):
                try:
                    j = pygame.joystick.Joystick(i)
                    if not j.get_init():
                        j.init()
                    devices.append((i, j.get_name()))
                except Exception:
                    devices.append((i, f"Device {i} (Error)"))

            return devices
        except Exception as e:
            print(f"[InputManager] Error getting devices: {e}")
            return []

    def connect_allowed_devices(self, allowed_names: List[str]):
        """
        Connect only devices in the allowed list.

        Args:
            allowed_names: List of device names to allow
        """
        if self.safe_mode or not HAS_PYGAME:
            return

        self.joysticks.clear()
        self.allowed_devices = list(allowed_names)

        try:
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()

            if not self.allowed_devices:
                # No devices have been approved yet
                return

            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                if j.get_name() in self.allowed_devices:
                    try:
                        j.init()
                        self.joysticks.append(j)
                        print(f"[InputManager] Connected: {j.get_name()}")
                    except Exception:
                        pass
        except Exception:
            pass

    def _start_input_loop(self, force: bool = False):
        """Start or restart the input loop thread with watchdog protection."""

        if not HAS_PYGAME:
            return

        if not force and self._input_thread and self._input_thread.is_alive():
            return

        self._input_thread = threading.Thread(
            target=self._input_loop_with_watchdog, daemon=True, name="InputLoop"
        )
        self._input_thread.start()
        self._input_watchdog.start()

    def _restart_input_loop(self):
        """Attempt to restart the input loop if the watchdog detects a stall."""

        if self.safe_mode or not HAS_PYGAME:
            return

        if self._input_thread and self._input_thread.is_alive():
            return

        print("[InputManager][Watchdog] Input loop unresponsive, restarting...")
        self._start_input_loop(force=True)

    def _input_loop_with_watchdog(self):
        """Background loop to capture joystick events and feed watchdog."""
        while True:
            try:
                if not self.safe_mode and HAS_PYGAME and pygame.get_init():
                    pygame.event.pump()
                    if self.active:
                        events = pygame.event.get()
                        for event in events:
                            if event.type == pygame.JOYBUTTONDOWN:
                                code = f"JOY:{event.joy}:{event.button}"
                                if code in self.listeners:
                                    threading.Thread(
                                        target=self.listeners[code],
                                        daemon=True
                                    ).start()
            except Exception:
                pass
            finally:
                self._input_watchdog.beat()
            time.sleep(0.01)

    def capture_any_input(self, timeout: float = 10.0) -> Optional[str]:
        """
        Capture any keyboard or joystick input.

        Args:
            timeout: Maximum time to wait for input in seconds

        Returns:
            Input code string (KEY:name or JOY:id:button) or None if timeout
        """
        captured_code = None
        start = time.time()

        def key_hook(e):
            nonlocal captured_code
            if e.event_type == 'down':
                if e.name == 'esc':
                    captured_code = "CANCEL"
                elif e.name:
                    captured_code = f"KEY:{e.name.upper()}"

        try:
            hook = keyboard.hook(key_hook)
        except Exception:
            hook = None

        try:
            while time.time() - start < timeout:
                if captured_code:
                    break

                # Check joystick buttons
                if not self.safe_mode and HAS_PYGAME and pygame.get_init():
                    try:
                        pygame.event.pump()
                        for joy in self.joysticks:
                            try:
                                for b_idx in range(joy.get_numbuttons()):
                                    if joy.get_button(b_idx):
                                        captured_code = f"JOY:{joy.get_id()}:{b_idx}"
                                        break
                            except Exception:
                                pass
                            if captured_code:
                                break
                    except Exception:
                        pass

                if captured_code:
                    break

                time.sleep(0.02)
        finally:
            if hook:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass

        return captured_code

    def capture_keyboard_scancode(self, timeout: float = 10.0) -> Tuple[Optional[int], Optional[str]]:
        """
        Capture a keyboard scan code with timeout and cancellation support.

        Returns:
            Tuple of (scan_code, key_name) or (None, None) if timeout/cancel
        """
        while keyboard.is_pressed('enter'):
            time.sleep(0.05)

        done = threading.Event()
        result: Dict[str, Optional[Any]] = {"scan": None, "name": None}

        def on_event(e):
            if e.event_type == 'down':
                if e.name == 'esc':
                    result["name"] = "CANCEL"
                else:
                    result["scan"] = e.scan_code
                    result["name"] = e.name
                done.set()

        hook = keyboard.hook(on_event, suppress=True)
        done.wait(timeout)
        keyboard.unhook(hook)

        if result["name"] == "CANCEL":
            return None, "CANCEL"

        return result["scan"], result["name"]


# Global input manager instance
input_manager = InputManager()


__all__ = ["InputManager", "input_manager"]
