"""Native window-style helpers for the Windows overlay.

WHY THIS EXISTS
---------------
Qt exposes click-through as ``Qt.WA_TransparentForMouseEvents``, and on Windows
the platform plugin turns that into the ``WS_EX_TRANSPARENT`` extended window
style. It does so **when the native window is created**; toggling the Qt
attribute afterwards never reaches the HWND.

The overlay is created click-through and then shown, so every later "unlock"
only cleared the Qt-side attribute while Windows carried on routing clicks
straight through to whatever sat behind the panel. That is why the released
.exe could not be dragged: the unlock worked in Qt and did nothing at all to
the window the user was actually clicking on.

Flipping the bit on the live HWND is the fix. The obvious alternative - calling
``setWindowFlags`` to add or remove ``Qt.WindowTransparentForInput`` - recreates
the native window, which hides it, drops always-on-top and loses the position,
so it trades one bug for three.

Everything here degrades to a no-op off Windows, so the module imports cleanly
on Linux/macOS for tests and CI.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Optional

logger = logging.getLogger("sr_estimator.overlay.native")

IS_WINDOWS = sys.platform.startswith("win")

# winuser.h
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

_user32 = None
_get_long = None
_set_long = None
_set_window_pos = None


def _load() -> bool:
    """Bind the three user32 entry points, once. False when unavailable."""
    global _user32, _get_long, _set_long, _set_window_pos
    if _get_long is not None and _set_long is not None:
        return True
    if not IS_WINDOWS:
        return False
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]

        # 64-bit Windows exposes the *Ptr variants; on 32-bit they are macros
        # for the plain ones, which is why the fallback is not dead code.
        if hasattr(_user32, "GetWindowLongPtrW"):
            get_fn = _user32.GetWindowLongPtrW
            set_fn = _user32.SetWindowLongPtrW
            long_t = ctypes.c_ssize_t   # LONG_PTR: pointer-wide
        else:
            get_fn = _user32.GetWindowLongW
            set_fn = _user32.SetWindowLongW
            long_t = ctypes.c_long

        get_fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_fn.restype = long_t
        set_fn.argtypes = [ctypes.c_void_p, ctypes.c_int, long_t]
        set_fn.restype = long_t

        _user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        _user32.SetWindowPos.restype = ctypes.c_bool

        _get_long, _set_long, _set_window_pos = get_fn, set_fn, _user32.SetWindowPos
        return True
    except Exception as exc:  # pragma: no cover - Windows-only path
        logger.error("Could not bind user32 (%s); click-through stays as Qt left it", exc)
        return False


def get_ex_style(hwnd: int) -> Optional[int]:
    """Current extended window style, or None when it cannot be read."""
    if not hwnd or not _load():
        return None
    ctypes.set_last_error(0)
    value = _get_long(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE)
    if value == 0 and ctypes.get_last_error() != 0:
        logger.debug("GetWindowLong failed: %s", ctypes.get_last_error())
        return None
    return int(value) & 0xFFFFFFFF


def set_ex_style(hwnd: int, ex_style: int) -> bool:
    """Write the extended window style back and flush the cached frame."""
    if not hwnd or not _load():
        return False
    ctypes.set_last_error(0)
    _set_long(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE, ex_style)
    err = ctypes.get_last_error()
    if err != 0:
        logger.error("SetWindowLong(GWL_EXSTYLE) failed: %s", err)
        return False
    # Style changes are only guaranteed to be picked up after this call.
    try:
        _set_window_pos(
            ctypes.c_void_p(int(hwnd)), None, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception as exc:  # pragma: no cover - Windows-only path
        logger.debug("SetWindowPos after style change failed: %s", exc)
    return True


def set_click_through(hwnd: int, enabled: bool) -> bool:
    """Add or remove WS_EX_TRANSPARENT on a live window.

    Returns True when the window really is in the requested state afterwards -
    the value is read back rather than assumed, so a failure shows up in the log
    instead of silently leaving the user unable to drag the overlay.

    Only the one bit is touched. WS_EX_LAYERED in particular must survive:
    Qt relies on it for the translucent background.
    """
    if not IS_WINDOWS:
        return True  # nothing to do; Qt's own attribute is enough elsewhere

    current = get_ex_style(hwnd)
    if current is None:
        return False

    wanted = (current | WS_EX_TRANSPARENT) if enabled else (current & ~WS_EX_TRANSPARENT)
    if wanted != current and not set_ex_style(hwnd, wanted):
        return False

    readback = get_ex_style(hwnd)
    if readback is None:
        return False
    return bool(readback & WS_EX_TRANSPARENT) == bool(enabled)


def is_click_through(hwnd: int) -> Optional[bool]:
    """Whether the window currently passes clicks through. None = unknown."""
    if not IS_WINDOWS:
        return None
    style = get_ex_style(hwnd)
    return None if style is None else bool(style & WS_EX_TRANSPARENT)
