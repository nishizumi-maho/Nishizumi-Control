"""Integrated Tk front end for Nishizumi Profiles."""

from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ..core import TelemetryHub
from .engine import (
    BOOTSTRAP_SETTINGS_PATH,
    DEFAULT_IRACING_DOCS,
    GROUPING_OPTIONS,
    RENDERER_OPTIONS,
    ComboInfo,
    IRacingRuntime,
    MonitorService,
    ProfileContextObserver,
    ProfileManager,
    now_str,
)


class ProfilesPanel(ttk.Frame):
    feature_id = "profiles"
    api_version = "1.0"
    runs_in_background = True

    def __init__(self, parent: tk.Misc, telemetry: TelemetryHub):
        super().__init__(parent)
        self.telemetry = telemetry
        self.iracing_docs = self._resolve_docs()
        self.manager = ProfileManager(self.iracing_docs)
        if self.manager.needs_initial_setup():
            self.manager.set_selected_renderer(RENDERER_OPTIONS["OpenXR"])
            self.manager.set_selected_grouping(GROUPING_OPTIONS["Car + track"])

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.refresh_requested = threading.Event()
        self.prompt_lock = threading.Lock()
        self.pending_prompt: dict[str, Any] | None = None
        self.selected_combo_key: str | None = None
        self.monitor_enabled = tk.BooleanVar(
            value=self.manager.get_bool_setting("integrated_monitor_enabled", False)
        )
        self.renderer_var = tk.StringVar(value=self._renderer_label(self.manager.get_selected_renderer()))
        self.grouping_var = tk.StringVar(value=self._grouping_label(self.manager.get_selected_grouping()))
        self.status_var = tk.StringVar(
            value=(
                "Watching • automation on"
                if self.monitor_enabled.get()
                else "Watching • no automatic actions"
            )
        )
        self.folder_var = tk.StringVar(value=str(self.iracing_docs))
        self.enabled_var = tk.BooleanVar(value=True)
        self.autosave_var = tk.BooleanVar(value=True)
        self.app_ini_var = tk.BooleanVar(value=True)
        self.form = {
            key: tk.StringVar(value="")
            for key in (
                "track_internal",
                "track_config",
                "track_display",
                "track_display_short",
                "car_path",
                "car_screen",
                "car_short",
                "series_id",
            )
        }
        self.observer = self._new_observer()
        self.monitor = self._new_monitor()
        self._job: str | None = None
        self._build_ui()
        self.refresh_all()
        self.start()

    def _new_monitor(self) -> MonitorService:
        return MonitorService(
            self.manager,
            self.enqueue_log,
            on_session_closed=self._request_refresh,
            confirm_new_combo_save=self._confirm_new_combo_from_worker,
            runtime=IRacingRuntime(self.telemetry.proxy),
        )

    def _new_observer(self) -> ProfileContextObserver:
        return ProfileContextObserver(
            IRacingRuntime(self.telemetry.proxy),
            on_change=self._background_combo_changed,
        )

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg="#f7f8fa")
        root.pack(fill="both", expand=True)
        header = tk.Frame(root, bg="#f7f8fa")
        header.pack(fill="x", padx=18, pady=(15, 4))
        title = tk.Frame(header, bg="#f7f8fa")
        title.pack(side="left", fill="x", expand=True)
        tk.Label(title, text="Nishizumi Graphics Profiles", bg="#f7f8fa", fg="#172033", font=("Segoe UI Semibold", 17)).pack(anchor="w")
        tk.Label(
            title,
            text="iRacing graphics configuration — not the car's setup — per car, track and series.",
            bg="#f7f8fa",
            fg="#667085",
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        tk.Label(title, textvariable=self.folder_var, bg="#f7f8fa", fg="#667085", font=("Segoe UI", 9)).pack(anchor="w")
        ttk.Checkbutton(header, text="Watch and sync on its own", variable=self.monitor_enabled, command=self._toggle_monitor).pack(side="right", padx=(10, 0))
        ttk.Button(header, text="iRacing folder", command=self.change_folder).pack(side="right")

        tk.Label(
            root,
            text=(
                "With \"Watch and sync on its own\" enabled, the app detects the car/track by itself and, when the saved profile differs from the active file, it can close and ask you to reopen iRacing so the right profile is applied."
            ),
            bg="#f7f8fa",
            fg="#8a6d00",
            font=("Segoe UI", 9),
            wraplength=900,
            justify="left",
        ).pack(fill="x", padx=18, pady=(0, 10), anchor="w")

        config = ttk.LabelFrame(root, text="Configuration")
        config.pack(fill="x", padx=18, pady=(0, 10))
        ttk.Label(config, text="Renderer").pack(side="left", padx=(10, 5), pady=8)
        renderer = ttk.Combobox(config, textvariable=self.renderer_var, values=list(RENDERER_OPTIONS), state="readonly", width=12)
        renderer.pack(side="left", pady=8)
        renderer.bind("<<ComboboxSelected>>", lambda _event: self._change_renderer())
        ttk.Label(config, text="Grouping").pack(side="left", padx=(14, 5), pady=8)
        grouping = ttk.Combobox(config, textvariable=self.grouping_var, values=list(GROUPING_OPTIONS), state="readonly", width=18)
        grouping.pack(side="left", pady=8)
        grouping.bind("<<ComboboxSelected>>", lambda _event: self._change_grouping())
        tk.Label(
            config,
            text="Grouping decides how widely a profile is shared: car+track is the most specific one.",
            fg="#8a94a6",
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(14, 5), pady=8)
        tk.Label(config, textvariable=self.status_var, fg="#175cd3", font=("Segoe UI Semibold", 9)).pack(side="right", padx=10)

        split = tk.PanedWindow(root, orient="horizontal", sashwidth=6, bg="#d0d5dd", bd=0)
        split.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        left = tk.Frame(split, bg="#ffffff", bd=1, relief="solid")
        right = tk.Frame(split, bg="#ffffff", bd=1, relief="solid")
        split.add(left, minsize=480, stretch="always")
        split.add(right, minsize=420, stretch="always")

        list_header = tk.Frame(left, bg="#ffffff")
        list_header.pack(fill="x", padx=10, pady=(9, 5))
        tk.Label(list_header, text="Profiles", bg="#ffffff", fg="#344054", font=("Segoe UI Semibold", 11)).pack(side="left")
        ttk.Button(list_header, text="Refresh", command=self.refresh_all).pack(side="right")
        # The list has to stay readable: a combo names a car, a track, its
        # layout and a series, which never fitted in a fixed column, and there
        # was no way to reach either the rows below the twelfth or the end of a
        # long name.
        tree_area = tk.Frame(left, bg="#ffffff")
        tree_area.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tree_area.rowconfigure(0, weight=1)
        tree_area.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_area,
            columns=("combo", "enabled", "autosave", "saved"),
            show="headings",
            height=12,
        )
        for column, label, width, minwidth, stretch in (
            ("combo", "Car / track / series", 340, 240, True),
            ("enabled", "Syncs", 80, 70, False),
            ("autosave", "Salv. auto", 80, 80, False),
            ("saved", "Last save", 140, 120, False),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(
                column, width=width, minwidth=minwidth, anchor="w", stretch=stretch
            )
        tree_scroll_y = ttk.Scrollbar(
            tree_area, orient="vertical", command=self.tree.yview
        )
        tree_scroll_x = ttk.Scrollbar(
            tree_area, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(
            yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        tk.Label(right, text="Selected profile", bg="#ffffff", fg="#344054", font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=12, pady=(10, 5))
        form_grid = tk.Frame(right, bg="#ffffff")
        form_grid.pack(fill="x", padx=12)
        labels = (
            ("track_display", "Track"),
            ("track_config", "Layout"),
            ("car_screen", "Car"),
            ("series_id", "Series ID"),
            ("track_internal", "Internal track"),
            ("car_path", "Car path"),
            ("track_display_short", "Short track"),
            ("car_short", "Short car"),
        )
        for index, (key, label) in enumerate(labels):
            row, column = divmod(index, 2)
            cell = tk.Frame(form_grid, bg="#ffffff")
            cell.grid(row=row, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0), pady=3)
            form_grid.columnconfigure(column, weight=1)
            tk.Label(cell, text=label, bg="#ffffff", fg="#667085", font=("Segoe UI", 8)).pack(anchor="w")
            ttk.Entry(cell, textvariable=self.form[key]).pack(fill="x")

        flags = tk.Frame(right, bg="#ffffff")
        flags.pack(fill="x", padx=12, pady=(10, 0))
        ttk.Checkbutton(
            flags,
            text="Sync automatically",
            variable=self.enabled_var,
            command=self._on_flag_toggled,
        ).pack(side="left")
        ttk.Checkbutton(
            flags,
            text="Save when iRacing closes",
            variable=self.autosave_var,
            command=self._on_flag_toggled,
        ).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(
            flags,
            text="Save app.ini as well",
            variable=self.app_ini_var,
            command=self._on_flag_toggled,
        ).pack(side="left", padx=(10, 0))
        tk.Label(
            right,
            text=(
                "These options apply only to the profile selected above and are saved right away — nothing to click."
            ),
            bg="#ffffff",
            fg="#8a94a6",
            font=("Segoe UI", 8),
            wraplength=380,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(2, 8))

        actions = tk.Frame(right, bg="#ffffff")
        actions.pack(fill="x", padx=12, pady=(0, 4))
        for index, (text, command) in enumerate((
            ("Use the current session", self.load_current_combo),
            ("Save profile", self.save_profile),
            ("Apply profile", self.apply_profile),
        )):
            button = ttk.Button(actions, text=text, command=command)
            button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 4, 0), pady=3)
            actions.columnconfigure(index, weight=1)

        maintenance = ttk.LabelFrame(right, text="Maintenance")
        maintenance.pack(fill="x", padx=12, pady=(6, 6))
        for index, (text, command, style_name) in enumerate((
            ("Backup global", self.create_backup, None),
            ("Restore backup", self.restore_backup, None),
            ("Open folder", self.open_profiles_folder, None),
            ("Delete profile", self.delete_profile, "Danger.TButton"),
        )):
            kwargs = {"style": style_name} if style_name else {}
            button = ttk.Button(maintenance, text=text, command=command, **kwargs)
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(8, 4) if index % 2 == 0 else (4, 8), pady=(6, 6))
            maintenance.columnconfigure(index % 2, weight=1)

        logs = tk.Frame(
            root,
            bg="#101828",
            bd=0,
            highlightthickness=1,
            highlightbackground="#344054",
        )
        logs.pack(fill="x", padx=18, pady=(0, 14))
        tk.Label(
            logs,
            text="Activity",
            bg="#101828",
            fg="#ffffff",
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=10, pady=(7, 1))
        log_area = tk.Frame(logs, bg="#101828")
        log_area.pack(fill="x", padx=9, pady=(1, 8))
        self.log_text = tk.Text(
            log_area,
            height=5,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            bg="#101828",
            fg="#ffffff",
            insertbackground="#ffffff",
            selectbackground="#175cd3",
            selectforeground="#ffffff",
            relief="flat",
            bd=0,
        )
        log_scroll = ttk.Scrollbar(
            log_area, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def start(self) -> None:
        self.observer.start()
        if self.monitor_enabled.get():
            self.monitor.start()
        if self._job is None:
            self._job = self.after(200, self._drain)

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.monitor.stop()
        self.observer.stop()
        with self.prompt_lock:
            pending = self.pending_prompt
            self.pending_prompt = None
        if pending is not None:
            pending["result"] = False
            pending["event"].set()

    def enqueue_log(self, text: str) -> None:
        self.log_queue.put(text)

    def _request_refresh(self) -> None:
        self.refresh_requested.set()

    def _background_combo_changed(self, combo: ComboInfo | None) -> None:
        if combo is None:
            self.enqueue_log(f'[{now_str()}] Background watch: waiting for iRacing')
            return
        self.enqueue_log(f'[{now_str()}] Context recognized in the background: {combo.label()}')

    def _confirm_new_combo_from_worker(self, combo: ComboInfo) -> bool:
        request = {"combo": combo, "event": threading.Event(), "result": False}
        with self.prompt_lock:
            self.pending_prompt = request
        request["event"].wait(timeout=120.0)
        return bool(request["result"])

    def _drain(self) -> None:
        self._job = None
        messages: list[str] = []
        while True:
            try:
                messages.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if messages:
            self.log_text.config(state="normal")
            for message in messages:
                self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
            self.status_var.set(messages[-1].split("] ", 1)[-1])

        with self.prompt_lock:
            request = self.pending_prompt
            self.pending_prompt = None
        if request is not None:
            combo: ComboInfo = request["combo"]
            request["result"] = messagebox.askyesno(
                "Nishizumi Graphics Profiles",
                f'Save the newly detected profile?\n\n{combo.label()}',
            )
            request["event"].set()

        if self.refresh_requested.is_set():
            self.refresh_requested.clear()
            self.refresh_all()
        if self.winfo_exists():
            self._job = self.after(200, self._drain)

    def refresh_all(self) -> None:
        self.manager.settings = self.manager.load_settings()
        self.manager.manifest = self.manager.load_manifest()
        for item in self.tree.get_children():
            self.tree.delete(item)
        renderer = self.current_renderer()
        grouping = self.current_grouping()
        for entry in self.manager.list_entries(renderer, grouping):
            key = str(entry.get("combo_key") or "")
            combo = ComboInfo.from_manifest_entry(entry)
            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(
                    combo.label(),
                    "Sim" if entry.get("enabled", True) else "Não",
                    "Sim" if entry.get("autosave_on_manual_close", True) else "Não",
                    entry.get("last_saved_at") or "--",
                ),
            )

    def current_renderer(self) -> str:
        return RENDERER_OPTIONS.get(self.renderer_var.get(), self.manager.get_selected_renderer())

    def current_grouping(self) -> str:
        return GROUPING_OPTIONS.get(self.grouping_var.get(), self.manager.get_selected_grouping())

    @staticmethod
    def _renderer_label(file_name: str) -> str:
        return next((label for label, value in RENDERER_OPTIONS.items() if value == file_name), "OpenXR")

    @staticmethod
    def _grouping_label(mode: str) -> str:
        return next((label for label, value in GROUPING_OPTIONS.items() if value == mode), "Car + track")

    def _change_renderer(self) -> None:
        self.manager.set_selected_renderer(self.current_renderer())
        self.refresh_all()

    def _change_grouping(self) -> None:
        self.manager.set_selected_grouping(self.current_grouping())
        self.refresh_all()

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        key = selection[0]
        entry = self.manager.get_entry(key, self.current_renderer(), self.current_grouping())
        if entry is None:
            return
        self.selected_combo_key = key
        self._fill_form(ComboInfo.from_manifest_entry(entry))
        self.enabled_var.set(bool(entry.get("enabled", True)))
        self.autosave_var.set(bool(entry.get("autosave_on_manual_close", True)))
        self.app_ini_var.set(bool(entry.get("save_app_ini_on_autosave", True)))

    def _fill_form(self, combo: ComboInfo) -> None:
        for key, variable in self.form.items():
            variable.set(str(getattr(combo, key)))

    def _combo_from_form(self) -> ComboInfo:
        return ComboInfo(**{key: variable.get().strip() for key, variable in self.form.items()})

    def load_current_combo(self) -> None:
        combo = self.observer.current_combo() or self.monitor.runtime.detect_combo()
        if combo is None:
            messagebox.showwarning("Nishizumi Graphics Profiles", "No connected session was detected.")
            return
        self._fill_form(combo)
        self.selected_combo_key = combo.combo_key(self.current_grouping())
        self.status_var.set("Current session loaded")

    def save_profile(self) -> None:
        combo = self._combo_from_form().normalized()
        if not combo.is_complete():
            messagebox.showwarning("Nishizumi Graphics Profiles", "Fill in or load the car/track data.")
            return
        try:
            entry = self.manager.save_active_ini_as_profile(combo, self.current_renderer(), self.current_grouping())
            if self.app_ini_var.get() and self.manager.get_active_app_ini().exists():
                self.manager.save_active_app_ini_for_car(combo)
            self.selected_combo_key = str(entry.get("combo_key") or combo.combo_key(self.current_grouping()))
            self.save_options(silent=True)
            self.refresh_all()
            self.status_var.set("Profile saved")
        except Exception as exc:
            messagebox.showerror("Nishizumi Graphics Profiles", f'Could not save:\n{exc}')

    def apply_profile(self) -> None:
        key = self.selected_combo_key
        if not key:
            messagebox.showwarning("Nishizumi Graphics Profiles", "Select a profile.")
            return
        if self.monitor.runtime.is_sim_running():
            messagebox.showwarning("Nishizumi Graphics Profiles", "Close the simulator before applying manually.")
            return
        try:
            self.manager.apply_profile_to_active_ini(key, self.current_renderer(), self.current_grouping())
            combo = self._combo_from_form().normalized()
            if self.manager.has_app_ini_profile_for_car(combo):
                self.manager.apply_app_ini_profile_for_car(combo)
            self.status_var.set("Profile applied")
        except Exception as exc:
            messagebox.showerror("Nishizumi Graphics Profiles", f'Could not apply:\n{exc}')

    def save_options(self, silent: bool = False) -> None:
        key = self.selected_combo_key
        if not key:
            if not silent:
                messagebox.showwarning("Nishizumi Graphics Profiles", "Select or save a profile.")
            return
        try:
            self.manager.update_entry_options(
                key,
                self.enabled_var.get(),
                self.autosave_var.get(),
                self.app_ini_var.get(),
                self.current_renderer(),
                self.current_grouping(),
            )
            if not silent:
                self.status_var.set("Options saved")
            self.refresh_all()
        except Exception as exc:
            if not silent:
                messagebox.showerror("Nishizumi Graphics Profiles", str(exc))

    def _on_flag_toggled(self) -> None:
        """Persist a per-profile flag the instant it changes; nothing to click."""
        if not self.selected_combo_key:
            return
        self.save_options(silent=True)
        self.status_var.set("Option saved")

    def create_backup(self) -> None:
        try:
            self.manager.create_or_refresh_global_backup(self.current_renderer())
            self.status_var.set("Global backup updated")
        except Exception as exc:
            messagebox.showerror("Nishizumi Graphics Profiles", str(exc))

    def restore_backup(self) -> None:
        if self.monitor.runtime.is_sim_running():
            messagebox.showwarning("Nishizumi Graphics Profiles", "Close the simulator before restoring.")
            return
        if not messagebox.askyesno(
            "Nishizumi Graphics Profiles", "Restore the global backup?"
        ):
            return
        try:
            self.manager.restore_global_backup(self.current_renderer())
            self.status_var.set("Backup restored")
        except Exception as exc:
            messagebox.showerror("Nishizumi Graphics Profiles", str(exc))

    def delete_profile(self) -> None:
        key = self.selected_combo_key
        if not key or not messagebox.askyesno("Nishizumi Graphics Profiles", "Delete the selected profile?"):
            return
        self.manager.delete_profile(key, self.current_renderer(), self.current_grouping())
        self.selected_combo_key = None
        self.refresh_all()

    def open_profiles_folder(self) -> None:
        path = self.manager.get_grouping_dir(self.current_renderer(), self.current_grouping())
        try:
            os.startfile(str(path))
        except Exception:
            pass

    def _toggle_monitor(self) -> None:
        enabled = self.monitor_enabled.get()
        self.manager.set_bool_setting("integrated_monitor_enabled", enabled)
        if enabled:
            self.monitor.start()
            self.status_var.set("Watching • automation on")
        else:
            self.monitor.stop()
            self.status_var.set("Watching • no automatic actions")

    def change_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Select the Documents/iRacing folder", initialdir=str(self.iracing_docs))
        if not chosen:
            return
        self.monitor.stop()
        self.observer.stop()
        self.iracing_docs = Path(chosen)
        self._save_docs(self.iracing_docs)
        self.folder_var.set(str(self.iracing_docs))
        self.manager = ProfileManager(self.iracing_docs)
        self.observer = self._new_observer()
        self.monitor = self._new_monitor()
        self.observer.start()
        if self.monitor_enabled.get():
            self.monitor.start()
        self.renderer_var.set(self._renderer_label(self.manager.get_selected_renderer()))
        self.grouping_var.set(self._grouping_label(self.manager.get_selected_grouping()))
        self.refresh_all()

    def _resolve_docs(self) -> Path:
        try:
            data = json.loads(BOOTSTRAP_SETTINGS_PATH.read_text(encoding="utf-8"))
            value = str(data.get("iracing_docs") or "") if isinstance(data, dict) else ""
            if value:
                return Path(value)
        except Exception:
            pass
        return DEFAULT_IRACING_DOCS

    @staticmethod
    def _save_docs(path: Path) -> None:
        try:
            BOOTSTRAP_SETTINGS_PATH.write_text(
                json.dumps({"iracing_docs": str(path), "saved_at": now_str()}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
