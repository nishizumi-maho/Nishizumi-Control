"""Every tool exposes its overlays the same way: one remembered checkbox each.

Each panel used to repeat the same wiring by hand — a checkbox here, a button
there, its own call to the preference store, its own restore and shutdown paths.
The button could only ever open an overlay, so turning the original one off meant
hunting for its window, and each copy of the logic remembered a slightly
different thing.  This module owns that behaviour once: a tool declares which
overlays it has and gets checkboxes that switch them on and off and come back in
the same state on the next launch.

The decisions live in :class:`OverlayGroup`, which knows nothing about Tk, so
they can be exercised without a display.  :class:`OverlayToggleBar` is only the
row of checkboxes bound to it.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk

from .overlay_preferences import OverlayPreferenceStore, get_overlay_preferences


class OverlayToggle:
    """One overlay a tool can show, described by what opens and closes it.

    ``open_action`` may return ``False`` to report that the overlay could not be
    opened; the checkbox then goes back to unchecked instead of lying about it.
    ``is_open`` lets the group notice overlays the user closed by their own
    window button, so the checkbox follows the window rather than the other way
    round.
    """

    __slots__ = (
        "overlay_id",
        "label",
        "open_action",
        "close_action",
        "shutdown_action",
        "is_open",
        "default",
    )

    def __init__(
        self,
        overlay_id: str,
        label: str,
        open_action: Callable[[], bool | None],
        close_action: Callable[[], None],
        *,
        shutdown_action: Callable[[], None] | None = None,
        is_open: Callable[[], bool] | None = None,
        default: bool = False,
    ) -> None:
        self.overlay_id = str(overlay_id)
        self.label = str(label)
        self.open_action = open_action
        # Unchecking the box means "I do not want this overlay"; closing the app
        # means "put it away, I will want it back". They are not the same.
        self.close_action = close_action
        self.shutdown_action = shutdown_action or close_action
        self.is_open = is_open
        self.default = bool(default)


class OverlayGroup:
    """The overlays of one tool, their live state and what is remembered."""

    def __init__(
        self,
        tool_id: str,
        toggles: Iterable[OverlayToggle],
        *,
        preferences: OverlayPreferenceStore | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.tool_id = str(tool_id)
        self.preferences = preferences or get_overlay_preferences()
        self.on_error = on_error
        self.toggles: dict[str, OverlayToggle] = {}
        self.states: dict[str, bool] = {}
        for toggle in toggles:
            self.toggles[toggle.overlay_id] = toggle
            self.states[toggle.overlay_id] = self.preferences.get(
                self.tool_id, toggle.overlay_id, toggle.default
            )

    def wanted(self, overlay_id: str) -> bool:
        return bool(self.states.get(str(overlay_id), False))

    def snapshot(self) -> dict[str, bool]:
        return dict(self.states)

    def apply(self, overlay_id: str, wanted: bool) -> bool:
        """Open or close one overlay and remember the result."""
        toggle = self.toggles.get(str(overlay_id))
        if toggle is None:
            return False
        if wanted:
            open_now = self._open(toggle) is not False
        else:
            open_now = False
            try:
                toggle.close_action()
            except Exception as exc:
                self._report(f"{toggle.label}: {type(exc).__name__}: {exc}")
        self.set_state(toggle.overlay_id, open_now)
        return open_now

    def set_state(self, overlay_id: str, open_: bool, *, persist: bool = True) -> None:
        """Follow an overlay that some other code path opened or closed."""
        key = str(overlay_id)
        if key not in self.states:
            return
        self.states[key] = bool(open_)
        if persist:
            self.preferences.set(self.tool_id, key, bool(open_))

    def restore(self) -> None:
        """Reopen whatever the user left open, without rewriting preferences."""
        for overlay_id, toggle in self.toggles.items():
            if not self.states.get(overlay_id):
                continue
            if self._open(toggle) is False:
                self.states[overlay_id] = False

    def sync(self) -> list[str]:
        """Follow overlays closed by their own window button. Returns the ids."""
        changed: list[str] = []
        for overlay_id, toggle in self.toggles.items():
            if toggle.is_open is None:
                continue
            try:
                open_now = bool(toggle.is_open())
            except Exception:
                continue
            if open_now != self.states.get(overlay_id):
                self.set_state(overlay_id, open_now)
                changed.append(overlay_id)
        return changed

    def shutdown(self) -> None:
        """Close the windows while keeping the state the user left them in."""
        wanted = self.snapshot()
        for toggle in self.toggles.values():
            try:
                toggle.shutdown_action()
            except Exception:
                continue
        # Closing an overlay during shutdown must never be read back as the user
        # having turned it off: the next launch has to reopen what was on screen.
        for overlay_id, open_ in wanted.items():
            self.states[overlay_id] = open_
            self.preferences.set(self.tool_id, overlay_id, open_)

    def _open(self, toggle: OverlayToggle) -> bool | None:
        try:
            result = toggle.open_action()
        except Exception as exc:
            self._report(f"{toggle.label}: {type(exc).__name__}: {exc}")
            return False
        if result is False:
            self._report(f'{toggle.label}: could not open')
        return result

    def _report(self, message: str) -> None:
        if self.on_error is None:
            return
        try:
            self.on_error(message)
        except Exception:
            pass


class OverlayToggleBar(ttk.Frame):
    """The row of overlay checkboxes shared by every tool panel."""

    def __init__(
        self,
        parent: tk.Misc,
        tool_id: str,
        toggles: Iterable[OverlayToggle],
        *,
        preferences: OverlayPreferenceStore | None = None,
        on_error: Callable[[str], None] | None = None,
        **frame_options: object,
    ) -> None:
        super().__init__(parent, **frame_options)
        self.group = OverlayGroup(
            tool_id, toggles, preferences=preferences, on_error=on_error
        )
        self._vars: dict[str, tk.BooleanVar] = {}
        for overlay_id, toggle in self.group.toggles.items():
            variable = tk.BooleanVar(value=self.group.wanted(overlay_id))
            ttk.Checkbutton(
                self,
                text=toggle.label,
                variable=variable,
                command=lambda name=overlay_id: self._on_clicked(name),
            ).pack(side="left", padx=(0, 10))
            self._vars[overlay_id] = variable

    def snapshot(self) -> dict[str, bool]:
        return self.group.snapshot()

    def restore(self) -> None:
        self.group.restore()
        self._refresh()

    def sync(self) -> None:
        if self.group.sync():
            self._refresh()

    def shutdown(self) -> None:
        self.group.shutdown()

    def _on_clicked(self, overlay_id: str) -> None:
        self.group.apply(overlay_id, bool(self._vars[overlay_id].get()))
        self._refresh()

    def _refresh(self) -> None:
        for overlay_id, variable in self._vars.items():
            wanted = self.group.wanted(overlay_id)
            if bool(variable.get()) != wanted:
                variable.set(wanted)


__all__ = ["OverlayGroup", "OverlayToggle", "OverlayToggleBar"]
