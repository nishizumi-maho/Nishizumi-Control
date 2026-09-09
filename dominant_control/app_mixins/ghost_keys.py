"""Auxiliary ("ghost") keys: catalogue fetch, persistence and actuation.

The Macros tab can already hang a wheel button on the key a scanned ``dc*``
control uses.  This mixin does the same for every other iRacing control: it
reads the whole ``controls.cfg`` catalogue for display, keeps the user's
auxiliary-key rows, and registers the listeners that turn an auxiliary press
into the in-game key.

The catalogue fetch is read-only on purpose.  Unlike
``import_iracing_controls_for_current_car`` it never writes to a control tab,
so refreshing the list here can never move a macro's key.
"""

from __future__ import annotations

from ..foundation import *
from ..core import (
    CONTROLS_CFG_INPUT_TYPE_AXIS,
    CONTROLS_CFG_INPUT_TYPE_JOYSTICK,
    CONTROLS_CFG_INPUT_TYPE_KEYBOARD,
    GHOST_MODE_HOLD,
    GHOST_SCOPE_CAR,
    GHOST_SCOPE_GLOBAL,
    MAX_GHOST_ROWS,
    default_ghost_row,
    find_ghost_row,
    ghost_row_is_active,
    ghost_row_title,
    merge_ghost_rows,
    next_ghost_row_id,
    normalize_ghost_triggers,
    parse_controls_catalog,
    sanitize_ghost_rows,
    split_ghost_rows_by_scope,
)

GHOST_CAR_PRESET_KEY = "_ghost_keys"
GHOST_CATALOG_STATUS_IDLE = (
    "No controls loaded yet. Use “Fetch iRacing controls”."
)


class GhostKeysMixin:
    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _init_ghost_keys_state(self) -> None:
        """Create the auxiliary-key state before the UI is built."""
        self.ghost_rows: List[Dict[str, Any]] = []
        self.ghost_catalog: List[Dict[str, Any]] = []
        self.ghost_catalog_source = ""
        self.ghost_catalog_path = ""
        self.ghost_catalog_status = GHOST_CATALOG_STATUS_IDLE
        self.ghost_keys_panel: Optional[Any] = None
        self._ghost_hold_state: Dict[str, bool] = {}
        self._ghost_rows_car = ""

    def _ghost_rows_list(self) -> List[Dict[str, Any]]:
        rows = getattr(self, "ghost_rows", None)
        if not isinstance(rows, list):
            rows = []
            self.ghost_rows = rows
        return rows

    def _ghost_listener_signature(self) -> Tuple[Any, ...]:
        """Return what the registered listeners depend on, and nothing else."""
        return tuple(
            (
                str(row.get("id") or ""),
                row.get("mode"),
                row.get("game_binding"),
                tuple(normalize_ghost_triggers(row.get("triggers"))),
            )
            for row in self._ghost_rows_list()
            if ghost_row_is_active(row)
        )

    def set_ghost_rows(self, rows: Any, *, schedule: bool = True) -> None:
        """Replace the auxiliary-key rows and re-arm the listeners if needed."""
        previous = self._ghost_listener_signature()
        self.ghost_rows = sanitize_ghost_rows(rows)
        if self.current_car:
            self._collect_ghost_rows_for_car(self.current_car)
        # Saving happens far more often than editing, so only pay for the
        # hook teardown/rebuild when the arming actually changed.
        if (
            self.app_state == "RUNNING"
            and self._ghost_listener_signature() != previous
        ):
            self.register_current_listeners()
        if schedule:
            self.schedule_save()

    def add_ghost_row(
        self,
        *,
        control: str = "",
        description: str = "",
        game_binding: Any = None,
        game_label: str = "",
        scope: str = GHOST_SCOPE_GLOBAL,
    ) -> Optional[Dict[str, Any]]:
        """Append one empty mapping row and return it."""
        rows = self._ghost_rows_list()
        if len(rows) >= MAX_GHOST_ROWS:
            self._show_warning(
                "Auxiliary Keys",
                f'The limit is {MAX_GHOST_ROWS} mappings.',
            )
            return None

        row = default_ghost_row(
            1,
            control=control,
            description=description,
            game_binding=game_binding,
            game_label=game_label,
            scope=scope,
        )
        row["id"] = next_ghost_row_id(item.get("id") for item in rows)
        rows.append(row)
        return row

    def remove_ghost_row(self, row_id: Any) -> None:
        """Drop one mapping row."""
        rows = self._ghost_rows_list()
        self.ghost_rows = [
            row for row in rows if str(row.get("id") or "") != str(row_id or "")
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _collect_ghost_rows_for_car(self, car: str) -> None:
        """Store this car's rows in its profile; global rows stay in config."""
        if not car:
            return
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        buckets = split_ghost_rows_by_scope(self._ghost_rows_list())
        car_rows = buckets[GHOST_SCOPE_CAR]
        if car_rows:
            self.saved_presets[car][GHOST_CAR_PRESET_KEY] = car_rows
        else:
            self.saved_presets[car].pop(GHOST_CAR_PRESET_KEY, None)

    def _apply_ghost_rows_for_car(self, car: str) -> None:
        """Rebuild the visible rows from the global set plus this car's set."""
        buckets = split_ghost_rows_by_scope(self._ghost_rows_list())
        car_entry = self.saved_presets.get(car, {}) if car else {}
        car_rows = (
            car_entry.get(GHOST_CAR_PRESET_KEY)
            if isinstance(car_entry, dict)
            else []
        )
        self.ghost_rows = merge_ghost_rows(buckets[GHOST_SCOPE_GLOBAL], car_rows)
        self._ghost_rows_car = car or ""
        self._refresh_ghost_keys_panel()

    def sync_ghost_rows_to_car(self, car: str) -> None:
        """Swap the car-scoped rows when the selected car changes.

        The outgoing car's rows are banked first so a car switch never drops
        mappings the user has not saved yet.
        """
        target = car or ""
        previous = getattr(self, "_ghost_rows_car", "")
        if target == previous:
            return
        if previous:
            self._collect_ghost_rows_for_car(previous)
        armed = self._ghost_listener_signature()
        self._apply_ghost_rows_for_car(target)
        if (
            self.app_state == "RUNNING"
            and self._ghost_listener_signature() != armed
        ):
            self.register_current_listeners()

    def _global_ghost_rows(self) -> List[Dict[str, Any]]:
        """Return only the rows saved app-wide, for ``save_config``."""
        return split_ghost_rows_by_scope(self._ghost_rows_list())[
            GHOST_SCOPE_GLOBAL
        ]

    def _load_ghost_rows(self, data: Dict[str, Any]) -> None:
        """Restore the global rows; the car's rows arrive with its profile."""
        self.ghost_rows = sanitize_ghost_rows(
            data.get("ghost_key_bindings"),
            scope=GHOST_SCOPE_GLOBAL,
        )

    # ------------------------------------------------------------------
    # iRacing control catalogue (read-only)
    # ------------------------------------------------------------------
    def _ghost_catalog_entry(self, record: Any) -> Dict[str, Any]:
        """Turn one parsed record into the row shown in the catalogue list."""
        entry: Dict[str, Any] = {
            "name": record.name,
            "label": format_driver_control_name(record.name),
            "binding": None,
            "key_label": "",
            "status": "",
            "importable": False,
        }

        if record.input_type == CONTROLS_CFG_INPUT_TYPE_KEYBOARD:
            binding = _iracing_vk_binding(record.value, record.modifiers)
            unsafe_reason = _unsafe_system_binding_reason(binding)
            if binding and not unsafe_reason:
                entry["binding"] = binding
                entry["key_label"] = _format_game_binding_label(binding)
                entry["status"] = "Keyboard"
                entry["importable"] = True
            elif unsafe_reason:
                entry["key_label"] = _format_game_binding_label(binding)
                entry["status"] = "Windows system key"
            else:
                entry["status"] = f'VK {record.value} not recognized'
        elif record.input_type == CONTROLS_CFG_INPUT_TYPE_JOYSTICK:
            entry["status"] = "Wheel button"
        elif record.input_type == CONTROLS_CFG_INPUT_TYPE_AXIS:
            entry["status"] = "Axis"
        else:
            entry["status"] = "No key in iRacing"

        return entry

    def fetch_ghost_control_catalog(self, silent: bool = False) -> bool:
        """Read every control out of controls.cfg, without touching macros."""
        car = self._current_controls_sync_car() or self.current_car
        cfg_path, source_label, car_folder = self._select_iracing_controls_cfg(car)
        if not cfg_path:
            self.ghost_catalog = []
            self.ghost_catalog_path = ""
            self.ghost_catalog_source = ""
            self.ghost_catalog_status = (
                "controls.cfg not found in Documents\\iRacing."
            )
            self._refresh_ghost_keys_panel()
            if not silent:
                self._show_warning(
                    "Auxiliary Keys",
                    "controls.cfg not found in Documents\\iRacing.",
                )
            return False

        try:
            with open(cfg_path, "rb") as handle:
                data = handle.read()
        except Exception as exc:
            self.ghost_catalog_status = f'Could not read controls.cfg: {exc}'
            self._refresh_ghost_keys_panel()
            if not silent:
                self._show_warning(
                    "Auxiliary Keys",
                    f'Could not read controls.cfg:\n{exc}',
                )
            return False

        records = parse_controls_catalog(data)
        if not records:
            self.ghost_catalog = []
            self.ghost_catalog_status = (
                "No control recognized in this controls.cfg."
            )
            self._refresh_ghost_keys_panel()
            if not silent:
                self._show_warning(
                    "Auxiliary Keys",
                    "No control was recognized in this controls.cfg.",
                )
            return False

        self.ghost_catalog = [
            self._ghost_catalog_entry(record) for record in records
        ]
        self.ghost_catalog_path = cfg_path
        self.ghost_catalog_source = (
            "car file" if source_label == "car" else "global file"
        )
        with_key = sum(1 for entry in self.ghost_catalog if entry["importable"])
        folder_text = f' • folder {car_folder}' if car_folder else ""
        self.ghost_catalog_status = (
            f'{len(self.ghost_catalog)} controls • {with_key} with a keyboard key • {self.ghost_catalog_source}{folder_text}'
        )
        self._refresh_ghost_keys_panel()
        if not silent:
            self.notify_overlay_status(
                f'iRacing controls loaded ({len(self.ghost_catalog)})',
                "green",
            )
        return True

    def _refresh_ghost_keys_panel(self) -> None:
        panel = getattr(self, "ghost_keys_panel", None)
        if panel is None:
            return
        try:
            panel.refresh()
        except Exception as exc:
            print(f'[Auxiliary Keys] Could not refresh the panel: {exc}')

    # ------------------------------------------------------------------
    # Conflict detection and actuation
    # ------------------------------------------------------------------
    def _iter_ghost_hotkey_bindings(self) -> Iterator[Tuple[str, str, str]]:
        """Yield (code, label, source_id) for every auxiliary trigger."""
        for position, row in enumerate(self._ghost_rows_list(), start=1):
            title = ghost_row_title(row, position)
            row_id = str(row.get("id") or position)
            for index, code in enumerate(
                normalize_ghost_triggers(row.get("triggers"))
            ):
                yield (
                    code,
                    f'Auxiliary key: {title}',
                    f"ghost:{row_id}:{index}",
                )

    def _ghost_row_binding(self, row_id: Any) -> Optional[Any]:
        row = find_ghost_row(self._ghost_rows_list(), row_id)
        if row is None or not ghost_row_is_active(row):
            return None
        return row.get("game_binding")

    def trigger_ghost_row(self, row_id: Any) -> None:
        """Send one pulse of a row's game key."""
        binding = self._ghost_row_binding(row_id)
        if binding is None:
            return
        if not self._commands_allowed():
            return
        try:
            click_pulse(binding)
        except Exception as exc:
            print(f'[Auxiliary Keys] Pulse failed: {exc}')

    def press_ghost_row(self, row_id: Any) -> None:
        """Hold a row's game key down until the auxiliary key is released."""
        binding = self._ghost_row_binding(row_id)
        if binding is None:
            return
        if not self._commands_allowed():
            return
        key = str(row_id)
        if self._ghost_hold_state.get(key):
            return
        try:
            _press_game_input(binding)
            self._ghost_hold_state[key] = True
        except Exception as exc:
            print(f'[Auxiliary Keys] Could not hold the key: {exc}')

    def release_ghost_row(self, row_id: Any) -> None:
        """Release a held game key."""
        key = str(row_id)
        if not self._ghost_hold_state.pop(key, False):
            return
        row = find_ghost_row(self._ghost_rows_list(), row_id)
        binding = row.get("game_binding") if isinstance(row, dict) else None
        if binding is None:
            return
        try:
            _release_game_input(binding)
        except Exception as exc:
            print(f'[Auxiliary Keys] Could not release the key: {exc}')

    def _release_all_ghost_rows(self) -> None:
        """Drop every held game key, e.g. when the listeners are rebuilt."""
        for row_id in list(self._ghost_hold_state.keys()):
            self.release_ghost_row(row_id)
        self._ghost_hold_state.clear()

    def _register_ghost_key_listeners(self, register: Callable) -> None:
        """Arm every auxiliary trigger through the caller's binding helper."""
        self._release_all_ghost_rows()
        for row in self._ghost_rows_list():
            if not ghost_row_is_active(row):
                continue
            row_id = str(row.get("id") or "")
            hold = row.get("mode") == GHOST_MODE_HOLD
            for code in normalize_ghost_triggers(row.get("triggers")):
                if hold:
                    register(
                        code,
                        lambda rid=row_id: self.press_ghost_row(rid),
                        edge_only=True,
                        release_action=lambda rid=row_id: self.release_ghost_row(
                            rid
                        ),
                    )
                else:
                    register(
                        code,
                        lambda rid=row_id: self.trigger_ghost_row(rid),
                        edge_only=True,
                    )


__all__ = ["GHOST_CAR_PRESET_KEY", "GhostKeysMixin"]
