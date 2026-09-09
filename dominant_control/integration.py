"""Wire separately maintained feature packages into the main notebook."""

from __future__ import annotations

import traceback
import tkinter as tk
from tkinter import ttk

from .core import CORE_API_VERSION, TelemetryHub
from .profiles import ProfilesPanel
from .tools import ToolsPanel


class IntegrationCoordinator:
    def __init__(self, app, telemetry: TelemetryHub):
        self.app = app
        self.telemetry = telemetry
        self.modules: list[object] = []

    def attach(self, notebook: ttk.Notebook) -> None:
        tools_frame = ttk.Frame(notebook)
        profiles_frame = ttk.Frame(notebook)
        self._insert_before_options(notebook, tools_frame, "Tools")
        self._insert_before_options(notebook, profiles_frame, "Graphics Profiles")
        self._mount(tools_frame, ToolsPanel, "Nishizumi Tools")
        self._mount(profiles_frame, ProfilesPanel, "Nishizumi Graphics Profiles")

    @staticmethod
    def _insert_before_options(notebook: ttk.Notebook, frame: ttk.Frame, label: str) -> None:
        tabs = notebook.tabs()
        index = "end"
        for position, tab_id in enumerate(tabs):
            if notebook.tab(tab_id, "text") == "Options":
                index = position
                break
        notebook.insert(index, frame, text=label)

    def _mount(self, frame: ttk.Frame, factory, title: str) -> None:
        try:
            module = factory(frame, self.telemetry)
            api_version = str(getattr(module, "api_version", ""))
            if api_version.split(".", 1)[0] != CORE_API_VERSION.split(".", 1)[0]:
                raise RuntimeError(
                    f'Incompatible API: module {api_version}, core {CORE_API_VERSION}'
                )
            module.pack(fill="both", expand=True)
            self.modules.append(module)
        except Exception as exc:
            box = tk.Frame(frame, padx=24, pady=24)
            box.pack(fill="both", expand=True)
            tk.Label(box, text=f'{title} could not be loaded', font=("Segoe UI Semibold", 14), fg="#b42318").pack(anchor="w")
            tk.Label(box, text=f"{type(exc).__name__}: {exc}", justify="left").pack(anchor="w", pady=(8, 0))
            details = tk.Text(box, height=12, wrap="word", font=("Consolas", 8))
            details.insert("1.0", traceback.format_exc())
            details.config(state="disabled")
            details.pack(fill="both", expand=True, pady=(12, 0))

    def start(self) -> None:
        for module in self.modules:
            start = getattr(module, "start", None)
            if callable(start):
                start()

    def stop(self) -> None:
        for module in reversed(self.modules):
            stop = getattr(module, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    continue
