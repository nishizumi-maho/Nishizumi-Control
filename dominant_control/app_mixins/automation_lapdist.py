from __future__ import annotations
from ..foundation import *
from .. import foundation
from ..ui.dialogs import *
from ..ui.hud import *
from ..ui.control_tabs import *

class LapDistAutomationMixin:
    def _lapdist_macro_loop(self):
        return None

    def _update_lapdist_option_state(self):
        return None

    def _on_lapdist_setting_change(self):
        return None

    def toggle_lapdist_hotkey(self):
        return None

    def _reset_lapdist_runtime_state(self, *args, **kwargs):
        return None

    def _lapdist_learning_key(self, *args, **kwargs):
        return ''

    def _bounded_lognormal(self, min_val: float, max_val: float) -> float:
        """Draw a bounded log-normal sample within [min_val, max_val]."""
        if max_val <= min_val:
            return max(0.0, min_val)
        mid = (min_val + max_val) / 2.0
        if mid <= 0.0:
            return min_val
        sigma = 0.45
        mu = math.log(mid)
        for _ in range(12):
            sample = self._lapdist_rng.lognormvariate(mu, sigma)
            if min_val <= sample <= max_val:
                return sample
        return max(min_val, min(max_val, sample))

    def _seed_lapdist_rng(self) -> int:
        """Seed LapDist RNG once per session with a time-based salt."""
        salt = os.environ.get('DC_SESSION_SALT', '')
        salt_val = sum((ord(ch) for ch in salt)) if salt else 0
        return int(time.time() * 1000) ^ os.getpid() << 8 ^ id(self) & 4294967295 ^ salt_val

__all__ = ['LapDistAutomationMixin']
