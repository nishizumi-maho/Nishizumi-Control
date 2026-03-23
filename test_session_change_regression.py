import sys
import types
import unittest


sys.modules.setdefault("keyboard", types.SimpleNamespace())


class _FakeIRSDKModule:
    class IRSDK:
        def __init__(self, *args, **kwargs):
            pass


sys.modules.setdefault("irsdk", _FakeIRSDKModule())

import FINALOK


class _BoolVar:
    def __init__(self, value: bool):
        self._value = value

    def get(self) -> bool:
        return self._value


class SessionChangeRegressionTests(unittest.TestCase):
    def _make_app(self):
        app = FINALOK.iRacingControlApp.__new__(FINALOK.iRacingControlApp)
        app.last_session_type = "Race"
        app.last_session_num = 2
        app.skip_race_restart_once = False
        app.skip_session_scan_once = False
        app.auto_restart_on_race = _BoolVar(True)
        app.pending_scan_on_start = False
        app._last_auto_pair = ("car", "track")
        app.auto_load_attempted = {("car", "track")}
        app._session_scan_pending = False
        app._telemetry_active = True
        app._last_weekend_key = ("weekend",)
        app._skip_next_auto_load = True
        app._on_track_restart_seen = True
        app.scan_calls = 0
        app.save_calls = 0
        app._schedule_session_scan = lambda: setattr(
            app, "scan_calls", app.scan_calls + 1
        )
        app.save_config = lambda: setattr(app, "save_calls", app.save_calls + 1)
        return app

    def test_transient_gap_clears_cached_session_identity_only(self):
        app = self._make_app()

        result = app._handle_session_change("", None)

        self.assertFalse(result)
        self.assertEqual(app.last_session_type, "")
        self.assertIsNone(app.last_session_num)
        self.assertTrue(app._telemetry_active)
        self.assertEqual(app._last_auto_pair, ("car", "track"))
        self.assertEqual(app.auto_load_attempted, {("car", "track")})

    def test_same_session_index_after_gap_is_detected_as_new_race(self):
        app = self._make_app()

        app._handle_session_change("", None)

        restart_calls = []
        original_restart = FINALOK.restart_program
        original_mark_pending = FINALOK.mark_pending_scan
        FINALOK.restart_program = lambda: restart_calls.append("restart")
        FINALOK.mark_pending_scan = lambda: restart_calls.append("pending")
        try:
            result = app._handle_session_change("Race", 2)
        finally:
            FINALOK.restart_program = original_restart
            FINALOK.mark_pending_scan = original_mark_pending

        self.assertTrue(result)
        self.assertEqual(app.last_session_type, "Race")
        self.assertEqual(app.last_session_num, 2)
        self.assertTrue(app.pending_scan_on_start)
        self.assertEqual(app.save_calls, 1)
        self.assertEqual(restart_calls, ["pending", "restart"])


class TrackConditionSelectionRegressionTests(unittest.TestCase):
    def _make_app(self):
        app = FINALOK.iRacingControlApp.__new__(FINALOK.iRacingControlApp)
        app.current_car = "NASCAR Toyota Camry"
        app.current_track = "Martinsville Speedway"
        app.current_condition = "DRY"
        app.active_vars = []
        app.persist_calls = 0
        app.load_calls = []
        app.notify_calls = []
        app.save_calls = 0
        app.selector_sync_calls = 0
        app._normalize_condition_name = lambda value: value
        app._persist_current_condition_before_switch = lambda: setattr(
            app, "persist_calls", app.persist_calls + 1
        )
        app._sync_condition_selector = lambda: setattr(
            app, "selector_sync_calls", app.selector_sync_calls + 1
        )
        app._ensure_track_storage = lambda car, track: None
        app._get_condition_preset = lambda car, track, condition, create=True: {
            "active_vars": ["BrakeBias"]
        }
        app.load_specific_preset = lambda car, track, condition=None: app.load_calls.append(
            (car, track, condition)
        )
        app.rebuild_tabs = lambda active_vars: None
        app._apply_saved_bindings_for_car = lambda car: None
        app.register_current_listeners = lambda: None
        app.save_config = lambda: setattr(app, "save_calls", app.save_calls + 1)
        app._notify_condition_change = lambda condition, source: app.notify_calls.append(
            (condition, source)
        )
        return app

    def test_hotkey_selection_skips_persist_for_faster_switch(self):
        app = self._make_app()

        changed = app._apply_condition_selection(
            "WET",
            source="hotkey",
            save_current=False
        )

        self.assertTrue(changed)
        self.assertEqual(app.current_condition, "WET")
        self.assertEqual(app.persist_calls, 0)
        self.assertEqual(
            app.load_calls,
            [("NASCAR Toyota Camry", "Martinsville Speedway", "WET")]
        )
        self.assertEqual(app.notify_calls, [("WET", "hotkey")])

    def test_manual_selection_still_persists_before_switching(self):
        app = self._make_app()

        changed = app._apply_condition_selection("WET", source="manual")

        self.assertTrue(changed)
        self.assertEqual(app.persist_calls, 1)
        self.assertEqual(app.notify_calls, [("WET", "manual")])

    def test_toggle_uses_fast_switch_path(self):
        app = self._make_app()

        app._toggle_track_condition()

        self.assertEqual(app.current_condition, "WET")
        self.assertEqual(app.persist_calls, 0)
        self.assertEqual(app.notify_calls, [("WET", "toggle")])



if __name__ == "__main__":
    unittest.main()
