from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *

class LifecycleMixin:

    def _close_app(self):
        """Close the application window and exit."""
        capture = getattr(self, 'lapdist_capture', None)
        if capture is not None:
            capture.cancel(silent=True)
        self._deactivate_second_throttle()
        if getattr(self, 'integration', None) is not None:
            self.integration.stop()
        self._cancel_surface_dc_precision_targets("application closed", clear_desired=True)
        self._stop_hybrid_hold()
        if self._auto_save_job:
            try:
                self.root.after_cancel(self._auto_save_job)
            except Exception:
                pass
            self._auto_save_current_preset()
        self.save_config()
        self.telemetry_hub.stop()
        self.root.destroy()

    def _minimize_to_tray(self, _event=None):
        """Minimize the window instead of closing it."""
        if self.root.state() != 'iconic':
            self.root.iconify()
        return 'break'

    def _handle_minimize_event(self, _event):
        if self.root.state() == 'iconic':
            self._minimize_to_tray()
__all__ = ['LifecycleMixin']
