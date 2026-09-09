from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *

class FuelHybridAutomationMixin:
    def _fuel_mixture_flag_loop(self):
        return None

    def _stop_hybrid_hold(self, var_name=None):
        return None
    pass

__all__ = ['FuelHybridAutomationMixin']
