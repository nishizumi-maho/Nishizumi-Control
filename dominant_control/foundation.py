import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, filedialog
import time
import ctypes
import keyboard
import irsdk
import json
import os
import re
import sys
import random
import math
import warnings
import threading
import subprocess
import queue
import numbers
import copy
from array import array
import tempfile
import wave
from collections import deque
from typing import Dict, List, Tuple, Optional, Any, Callable, Iterable, Deque, Iterator
from tkinter import font as tkfont
from .edition import APP_DATA_FOLDER, APP_DISPLAY_NAME, APP_VERSION, APP_STARTUP_ENTRY, APP_USER_MODEL_ID, PUBLIC_EDITION, FUEL_MIXTURE_AUTOMATION_AVAILABLE, HYBRID_HOLD_AUTOMATION_AVAILABLE, LAPDIST_MACROS_AVAILABLE, P2P_CHAIN_AUTOMATION_AVAILABLE, PIT_LIMITER_AUTOMATION_AVAILABLE, PUBLIC_EDITION, SECOND_THROTTLE_PIT_MACRO_AVAILABLE, SURFACE_PROFILE_AUTOMATION_AVAILABLE, TURBO_PIT_AVAILABLE, UPDATE_CHECK_AVAILABLE, UPDATE_REPO_NAME, UPDATE_REPO_OWNER, WIPER_AUTOMATION_AVAILABLE
from .paths import dominant_control_data_dir

class Watchdog:
    """Simple watchdog to monitor heartbeats and run recovery callbacks."""

    def __init__(self, name: str, *, interval_s: float=2.0, timeout_s: float=6.0, on_trip: Optional[Callable[[], None]]=None):
        self.name = name
        self.interval_s = max(0.5, interval_s)
        self.timeout_s = max(self.interval_s, timeout_s)
        self.on_trip = on_trip
        self._last_heartbeat = time.time()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def beat(self):
        """Record a heartbeat from the monitored worker."""
        self._last_heartbeat = time.time()

    def start(self):
        """Start the watchdog monitor thread."""
        if self._thread and self._thread.is_alive():
            return
        if self._stop_event.is_set():
            self._stop_event = threading.Event()
        self._last_heartbeat = time.time()
        self._thread = threading.Thread(target=self._run, name=f'{self.name}-watchdog', daemon=True)
        self._thread.start()

    def stop(self):
        """Stop monitoring."""
        self._stop_event.set()

    def _run(self):
        while not self._stop_event.wait(self.interval_s):
            elapsed = time.time() - self._last_heartbeat
            if elapsed <= self.timeout_s:
                continue
            try:
                if self.on_trip:
                    self.on_trip()
            except Exception as exc:
                print(f'[Supervisor:{self.name}] Recovery failed: {exc}')
            self._last_heartbeat = time.time()

class CallbackDispatcher:
    """Run callback work on a small pool of daemon workers."""

    def __init__(self, worker_count: int=4):
        self._queue: 'queue.Queue[Tuple[Callable, tuple, dict]]' = queue.Queue()
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        for idx in range(max(1, worker_count)):
            thread = threading.Thread(target=self._worker, name=f'CallbackDispatcher-{idx}', daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, fn: Callable, *args, **kwargs) -> None:
        if self._stop_event.is_set():
            return
        self._queue.put((fn, args, kwargs))

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                fn, args, kwargs = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f'[Dispatcher] Callback error: {exc}')
            finally:
                self._queue.task_done()

    def pending(self) -> int:
        try:
            return self._queue.qsize()
        except Exception:
            return 0
_CALLBACK_DISPATCHER = CallbackDispatcher(worker_count=4)

class TelemetryCache:
    """Thread-safe cache for telemetry values with TTL support."""

    def __init__(self, default_ttl_s: float=0.05):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl_s

    def get(self, key: str, ttl_s: Optional[float]=None) -> Tuple[bool, Any]:
        """Return (hit, value) for cached telemetry key."""
        ttl = ttl_s if ttl_s is not None else self._default_ttl
        now = time.time()
        with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if now - timestamp < ttl:
                    return (True, value)
        return (False, None)

    def set(self, key: str, value: Any) -> None:
        """Store a telemetry value with current timestamp."""
        now = time.time()
        with self._lock:
            self._cache[key] = (value, now)

    def invalidate(self, key: Optional[str]=None) -> None:
        """Clear one or all cached values."""
        with self._lock:
            if key is None:
                self._cache.clear()
            elif key in self._cache:
                del self._cache[key]

    def batch_set(self, data: Dict[str, Any]) -> None:
        """Store multiple telemetry values atomically."""
        now = time.time()
        with self._lock:
            for key, value in data.items():
                self._cache[key] = (value, now)

class TelemetryCircuitBreaker:
    """Circuit breaker to prevent repeated failures on broken telemetry vars."""
    CLOSED = 'closed'
    OPEN = 'open'
    HALF_OPEN = 'half_open'

    def __init__(self, failure_threshold: int=5, recovery_timeout_s: float=10.0, success_threshold: int=2):
        self._states: Dict[str, str] = {}
        self._failure_counts: Dict[str, int] = {}
        self._success_counts: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.success_threshold = success_threshold

    def can_execute(self, var_name: str) -> bool:
        """Return True if the circuit allows reading this variable."""
        now = time.time()
        with self._lock:
            state = self._states.get(var_name, self.CLOSED)
            if state == self.CLOSED:
                return True
            if state == self.OPEN:
                last_fail = self._last_failure_time.get(var_name, 0.0)
                if now - last_fail >= self.recovery_timeout_s:
                    self._states[var_name] = self.HALF_OPEN
                    self._success_counts[var_name] = 0
                    return True
                return False
            return True

    def record_success(self, var_name: str) -> None:
        """Record a successful telemetry read."""
        with self._lock:
            state = self._states.get(var_name, self.CLOSED)
            if state == self.HALF_OPEN:
                count = self._success_counts.get(var_name, 0) + 1
                self._success_counts[var_name] = count
                if count >= self.success_threshold:
                    self._states[var_name] = self.CLOSED
                    self._failure_counts[var_name] = 0
            elif state == self.CLOSED:
                self._failure_counts[var_name] = 0

    def record_failure(self, var_name: str) -> None:
        """Record a failed telemetry read."""
        now = time.time()
        with self._lock:
            state = self._states.get(var_name, self.CLOSED)
            if state == self.HALF_OPEN:
                self._states[var_name] = self.OPEN
                self._last_failure_time[var_name] = now
            elif state == self.CLOSED:
                count = self._failure_counts.get(var_name, 0) + 1
                self._failure_counts[var_name] = count
                if count >= self.failure_threshold:
                    self._states[var_name] = self.OPEN
                    self._last_failure_time[var_name] = now

    def reset(self, var_name: Optional[str]=None) -> None:
        """Reset circuit breaker state for one or all variables."""
        with self._lock:
            if var_name is None:
                self._states.clear()
                self._failure_counts.clear()
                self._success_counts.clear()
                self._last_failure_time.clear()
            else:
                self._states.pop(var_name, None)
                self._failure_counts.pop(var_name, None)
                self._success_counts.pop(var_name, None)
                self._last_failure_time.pop(var_name, None)

    def get_state(self, var_name: str) -> str:
        """Return current circuit state for a variable."""
        with self._lock:
            return self._states.get(var_name, self.CLOSED)
_TELEMETRY_CACHE = TelemetryCache(default_ttl_s=0.05)
_TELEMETRY_CIRCUIT_BREAKER = TelemetryCircuitBreaker(failure_threshold=5, recovery_timeout_s=10.0, success_threshold=2)
IRSDK_VAR_TYPE_BOOL = 1
IRSDK_VAR_TYPE_INTEGER = {2, 3}
IRSDK_VAR_TYPE_FLOAT = {4, 5}
IRSDK_VAR_TYPE_NUMERIC = IRSDK_VAR_TYPE_INTEGER | IRSDK_VAR_TYPE_FLOAT
IRSDK_ENGINE_WARNING_PIT_SPEED_LIMITER = 16
IRSDK_DRS_STATUS_INACTIVE = 0
IRSDK_DRS_STATUS_AVAILABLE_NEXT_ZONE = 1
IRSDK_DRS_STATUS_AVAILABLE_IN_ZONE = 2
IRSDK_DRS_STATUS_ACTIVE = 3

def _pit_limiter_state_from_engine_warnings(value: Any) -> Optional[bool]:
    """Read the real limiter state from the EngineWarnings bitfield."""
    if isinstance(value, (list, tuple, array)):
        if len(value) != 1:
            return None
        value = value[0]
    if not isinstance(value, numbers.Real):
        return None
    try:
        return bool(int(value) & IRSDK_ENGINE_WARNING_PIT_SPEED_LIMITER)
    except Exception:
        return None

def _scalar_boolean_state(value: Any) -> Optional[bool]:
    """Normalize a scalar SDK boolean without collapsing per-car arrays."""
    if isinstance(value, (list, tuple, array)):
        if len(value) != 1:
            return None
        value = value[0]
    if isinstance(value, bool):
        return value
    if not isinstance(value, numbers.Real):
        return None
    try:
        numeric = float(value)
    except Exception:
        return None
    if not math.isfinite(numeric) or numeric not in (0.0, 1.0):
        return None
    return bool(int(numeric))

def _drs_active_state_from_status(value: Any) -> Optional[bool]:
    """Return whether the DRS flap is open from the four-state SDK value."""
    if isinstance(value, (list, tuple, array)):
        if len(value) != 1:
            return None
        value = value[0]
    if isinstance(value, bool):
        return value
    if not isinstance(value, numbers.Real):
        return None
    try:
        numeric = float(value)
        status = int(numeric)
    except Exception:
        return None
    if not math.isfinite(numeric) or numeric != float(status):
        return None
    if status not in {IRSDK_DRS_STATUS_INACTIVE, IRSDK_DRS_STATUS_AVAILABLE_NEXT_ZONE, IRSDK_DRS_STATUS_AVAILABLE_IN_ZONE, IRSDK_DRS_STATUS_ACTIVE}:
        return None
    return status == IRSDK_DRS_STATUS_ACTIVE
warnings.filterwarnings('ignore', message='pkg_resources is deprecated as an API.*')
VOICE_FEATURES_ENABLED = False
pygame = None
_PREFERRED_JOYSTICK_BACKEND = os.getenv('DOMINANTCONTROL_JOYSTICK_BACKEND', '').strip().lower()
_PRESERVE_GAME_FFB_DEFAULT = os.getenv('DOMINANTCONTROL_PRESERVE_FFB', '1') == '1'
_IMPORT_PYGAME_ON_START = _PREFERRED_JOYSTICK_BACKEND == 'pygame' or (_PREFERRED_JOYSTICK_BACKEND != 'winmm' and (not _PRESERVE_GAME_FFB_DEFAULT)) or (not sys.platform.startswith('win'))
if _IMPORT_PYGAME_ON_START:
    try:
        import pygame
        HAS_PYGAME = True
    except ImportError:
        HAS_PYGAME = False
        print("Warning: 'pygame' is not installed. Joystick support is off.")
else:
    HAS_PYGAME = False
pyttsx3 = None
pyaudio = None
sr = None
vosk = None
HAS_TTS = False
HAS_PYAUDIO = False
HAS_SPEECH = False
HAS_VOSK = False
VOSK_IMPORT_ERROR: Optional[str] = "Voice features disabled."
APP_NAME = APP_DISPLAY_NAME
SURFACE_PRESET_KEYS = ('DRY', 'WET')
DEFAULT_SURFACE_PRESET = 'DRY'
SURFACE_DC_PROFILE_KEYS = ('DRY', 'WET')
SURFACE_DC_ENDURANCE_MODES = ('AUTO', 'DRY', 'WET')
HUD_LAPDIST_DEFAULT_OFF_VERSION = 1
AUTO_SYNC_IRACING_CONTROLS_DEFAULT_OFF_VERSION = 1
HUD_LAPDIST_KEYS = ('LapDistPct', 'OverlayLapDistPct')
WIPER_TOGGLE_VARS = {'dcToggleWindShieldWipers', 'dcToggleWindshieldWipers', 'dcToogleWindShieldWipers'}
WIPER_TRIGGER_VARS = {'dcTriggerWindShieldWipers', 'dcTriggerWindshieldWipers'}
HYBRID_BOOST_HOLD_VAR = 'dcHysBoostHold'
HYBRID_REGEN_HOLD_VAR = 'dcHysRegenHold'
HYBRID_DISABLE_BOOST_HOLD_VAR = 'dcHysDisableBoostHold'
HYBRID_HOLD_VARS = {HYBRID_BOOST_HOLD_VAR, HYBRID_REGEN_HOLD_VAR, HYBRID_DISABLE_BOOST_HOLD_VAR}
HYBRID_HOLD_STATUS_FIELDS = {HYBRID_BOOST_HOLD_VAR: 'ManualBoost', HYBRID_REGEN_HOLD_VAR: 'ManualNoBoost', HYBRID_DISABLE_BOOST_HOLD_VAR: 'ManualNoBoost'}
DRIVER_CONTROL_CONFIRMED_STATE_VARS = {'dcDRSToggle', 'dcPitSpeedLimiterToggle', 'dcPushToPass', *HYBRID_HOLD_VARS}
DRIVER_CONTROL_TRIGGER_VARS = {'dcHeadlightFlash', 'dcLowFuelAccept', 'dcStarter', 'dcTearOffVisor', *WIPER_TRIGGER_VARS}
DRIVER_CONTROL_STATEFUL_BOOLEAN_VARS = {'dcPushToPass', 'dcPitSpeedLimiterToggle', *HYBRID_HOLD_VARS}
SURFACE_DC_EXCLUDED_VARS = {'dcPitSpeedLimiterToggle', 'dcPushToPass', 'dcHeadlightFlash', 'dcLowFuelAccept', 'dcStarter', 'dcHysBoostHold', 'dcHysRegenHold', 'dcHysDisableBoostHold', 'dcTractionControlToggle', 'dcABSToggle', *WIPER_TOGGLE_VARS, *WIPER_TRIGGER_VARS}

def _driver_control_action_kind(var_name: Optional[str], is_boolean: bool=False) -> str:
    """Classify a driver control by how its assigned key must be operated.

    The SDK header describes the storage type (bool/int/float), not the
    semantics of a boolean command.  Known stateful controls keep their
    dedicated handling; ordinary triggers and toggles are always one key
    pulse and never use the dc* value as confirmation.
    """
    name = str(var_name or '').strip()
    lowered = name.lower()
    if name in HYBRID_HOLD_VARS:
        return 'hold'
    if name == 'dcPushToPass':
        return 'stateful'
    if name == 'dcPitSpeedLimiterToggle':
        return 'stateful_toggle'
    if name in WIPER_TOGGLE_VARS or 'toggle' in lowered or 'toogle' in lowered:
        return 'toggle'
    if name in DRIVER_CONTROL_TRIGGER_VARS or 'trigger' in lowered or 'tearoff' in lowered or ('tear_off' in lowered):
        return 'trigger'
    if is_boolean and name not in DRIVER_CONTROL_STATEFUL_BOOLEAN_VARS:
        return 'trigger'
    return 'value'

def _is_one_shot_control_name(var_name: Optional[str], is_boolean: bool=False) -> bool:
    """Return True for trigger/toggle controls that need exactly one pulse."""
    return _driver_control_action_kind(var_name, is_boolean) in {'trigger', 'toggle'}

def _is_surface_dc_profile_control(var_name: Optional[str], is_boolean: bool=False) -> bool:
    """Return True for value controls that are safe to store in a surface profile.

    Momentary/special actions stay in their own automation pages.  Surface
    profiles are deliberately limited to persistent numeric values such as TC,
    ABS, brake bias, ARBs, differential and fuel mixture.
    """
    name = str(var_name or '').strip()
    lowered = name.lower()
    if not name.startswith('dc') or is_boolean:
        return False
    if name in SURFACE_DC_EXCLUDED_VARS or _is_one_shot_control_name(name, is_boolean):
        return False
    special_tokens = ('toggle', 'trigger', 'tearoff', 'tear_off', 'flash', 'starter', 'pushtopass', 'pitlimiter', 'pitspeed', 'boosthold', 'regenhold', 'lowfuelaccept', 'headlight', 'drs')
    return not any((token in lowered for token in special_tokens))
HYBRID_SOC_KEYS = ('EnergyERSBatteryPct', 'EnergyERSBattery')
HYBRID_BATTERY_FULL_J = 145000.0
WEIGHT_JACKER_VARS = {'dcWeightJackerRight'}
WEIGHT_JACKER_BURST_PRESS_MS = 4
WEIGHT_JACKER_BURST_INTERVAL_MS = 4
WEIGHT_JACKER_FINE_PRESS_MS = 6
WEIGHT_JACKER_FINE_INTERVAL_MS = 8
WEIGHT_JACKER_VERIFY_SETTLE_S = 0.045
WEIGHT_JACKER_BURST_GUARD = 2
WEIGHT_JACKER_MAX_CORRECTION_PASSES = 3
FUEL_MIXTURE_VAR = 'dcFuelMixture'
CONTROL_NAME_ALIASES: Dict[str, Tuple[str, ...]] = {'dcABS': ('ABS',), 'dcABias': ('Aero Bias', 'Aero', 'A'), 'dcAntiRollFront': ('Anti Roll Front', 'Anti Roll F', 'ARB Front', 'ARB F'), 'dcAntiRollRear': ('Anti Roll Rear', 'Anti Roll R', 'ARB Rear', 'ARB R'), 'dcBrakeBias': ('Brake Bias', 'Brk Bias', 'BB'), 'dcBrakeMigration': ('Brake Migration', 'Brake Mig', 'Brk Mig', 'BM'), 'dcDashPage': ('Dash Page', 'Dash', 'DP'), 'dcDiffEntry': ('Diff Entry', 'Diff In', 'D In'), 'dcDiffExit': ('Diff Exit', 'Diff Out', 'D Out'), 'dcDiffMiddle': ('Diff Middle', 'Diff Mid', 'D Mid'), 'dcDiffPreload': ('Diff Preload', 'Preload', 'D Pre'), 'dcFuelMixture': ('Fuel Mixture', 'Fuel Mix', 'Fuel'), 'dcHeadlightFlash': ('Headlight Flash', 'Flash', 'Light'), 'dcHysBoostHold': ('Hybrid Boost Hold', 'Boost Hold', 'Boost'), 'dcHysRegenHold': ('Hybrid Regen Hold', 'Regen Hold', 'Regen'), 'dcHysDisableBoostHold': ('Hybrid Disable Boost Hold', 'No Boost Hold', 'No Boost'), 'dcLowFuelAccept': ('Low Fuel Accept', 'Low Fuel', 'Fuel Ack'), 'LapDistPct': ('LapDist', 'LapDistPct', 'Lap Dist'), 'dcMGUKDeployFixed': ('MGU-K Deploy', 'Deploy', 'MGU Dep'), 'dcMGUKRegenGain': ('MGU-K Regen', 'Regen Gain', 'MGU Reg'), 'dcPitSpeedLimiterToggle': ('Pit Speed Limiter', 'Pit Limiter', 'Pit Lim'), 'dcPushToPass': ('Push To Pass', 'P2P', 'P2P'), 'dcStarter': ('Starter', 'Start'), 'dcToggleWindShieldWipers': ('Wiper Toggle', 'Wiper Tgl', 'Wip Tgl'), 'dcToggleWindshieldWipers': ('Wiper Toggle', 'Wiper Tgl', 'Wip Tgl'), 'dcToogleWindShieldWipers': ('Wiper Toggle', 'Wiper Tgl', 'Wip Tgl'), 'dcTractionControl': ('Traction Control', 'TC', 'TC'), 'dcTractionControl2': ('Traction Control 2', 'TC 2', 'TC2'), 'dcTractionControlCut': ('Traction Cut', 'TC Cut', 'TCC'), 'dcTriggerWindShieldWipers': ('Wiper Trigger', 'Wiper Trig', 'Wip Trg'), 'dcTriggerWindshieldWipers': ('Wiper Trigger', 'Wiper Trig', 'Wip Trg'), 'dcWeightJackerRight': ('Weight Jacker Right', 'WJ Right', 'WJ R')}
IRACING_CONTROL_NAME_OVERRIDES: Dict[str, Tuple[str, Optional[str]]] = {'dcPitSpeedLimiterToggle': ('PitSpeedLimiter', None), 'dcPushToPass': ('PushToPass', None), 'dcHeadlightFlash': ('HeadlightFlash', None), 'dcLowFuelAccept': ('LowFuelAccept', None), 'dcStarter': ('Starter', None), 'dcToggleWindShieldWipers': ('ToggleWindshieldWipers', None), 'dcToggleWindshieldWipers': ('ToggleWindshieldWipers', None), 'dcToogleWindShieldWipers': ('ToggleWindshieldWipers', None), 'dcTriggerWindShieldWipers': ('TriggerWindshieldWipers', None), 'dcTriggerWindshieldWipers': ('TriggerWindshieldWipers', None), 'dcHysBoostHold': ('HysBoostHold', None), 'dcHysRegenHold': ('HysRegenHold', None), 'dcHysDisableBoostHold': ('HysDisableBoostHold', None), 'dcTractionControlToggle': ('TractionControlToggle', None), 'dcABSToggle': ('ABSToggle', None)}
IRACING_CONTROL_BASE_OVERRIDES: Dict[str, str] = {'dcBrakeFineBias': 'BrakeBiasFine', 'dcBrakeBiasFine': 'BrakeBiasFine'}
IRACING_CONTROLS_CFG_KEYBOARD_TYPE = 4
IRACING_CONTROLS_CFG_JOYSTICK_TYPE = 2
IRACING_CONTROL_MOD_SHIFT = 3
IRACING_CONTROL_MOD_CTRL = 12
IRACING_CONTROL_MOD_ALT = 48
IRACING_CONTROLS_WATCH_INTERVAL_MS = 1200
IRACING_CONTROLS_IMPORT_DELAY_MS = 700
IRACING_INTERNAL_FKEY_BASE = 197
CONTROL_TOKEN_RE = re.compile('[A-Z]+(?=[A-Z][a-z]|\\d|$)|[A-Z]?[a-z]+|\\d+')
CONTROL_WORD_LABELS = {'Hys': 'Hybrid', 'MGUK': 'MGU-K', 'Toogle': 'Toggle', 'WindShield': 'Windshield'}
CONTROL_WORD_ABBREVIATIONS = {'Anti': 'Anti', 'Roll': 'Roll', 'Front': 'F', 'Rear': 'R', 'Brake': 'Brk', 'Bias': 'Bias', 'Migration': 'Mig', 'Traction': 'TC', 'Control': 'Ctrl', 'Cut': 'Cut', 'Fuel': 'Fuel', 'Mixture': 'Mix', 'Pit': 'Pit', 'Speed': 'Spd', 'Limiter': 'Lim', 'Toggle': 'Tgl', "Trigger": 'Trig', 'Windshield': 'WS', 'Wipers': 'Wip', 'Headlight': 'Light', 'Flash': 'Flash', 'Starter': 'Start', 'Hybrid': 'Hyb', 'Boost': 'Boost', 'Regen': 'Regen', 'Hold': 'Hold', 'Deploy': 'Dep', 'Fixed': 'Fix', 'Gain': 'Gain', 'Weight': 'W', 'Jacker': 'Jacker', 'Right': 'R', 'Entry': 'In', 'Exit': 'Out', 'Middle': 'Mid', 'Preload': 'Pre', 'Push': 'Push', 'Pass': 'Pass'}
PIT_COMMAND_CLEAR_TIRES = 'clear_tires'
PIT_COMMAND_CLEAR_WS = 'clear_ws'
APP_FOLDER = APP_DATA_FOLDER
CONFIG_FOLDER = str(dominant_control_data_dir('configs'))
CONFIG_FILE = os.path.join(CONFIG_FOLDER, 'config_v3.json')
PENDING_SCAN_FILE = os.path.join(CONFIG_FOLDER, 'pending_scan.flag')
LAPDIST_CLOSE_LOG_FILE = os.path.join(CONFIG_FOLDER, 'lapdist_close_log.txt')
ICON_CANDIDATES = ['DominantControl.ico', 'DominantControl.png', 'app.ico', 'app.png']
STARTUP_FOLDER = os.path.join(os.getenv('APPDATA') or os.path.expanduser('~'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
STARTUP_ENTRY_NAME = APP_STARTUP_ENTRY
WINDOWS_APP_USER_MODEL_ID = APP_USER_MODEL_ID

def _dedupe_texts(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result

def _control_words(var_name: str) -> List[str]:
    raw = str(var_name or '').strip()
    if raw.lower().startswith('dc') and len(raw) > 2:
        raw = raw[2:]
    raw = raw.replace('WindShield', 'Windshield').replace('windShield', 'windshield').replace('Toogle', 'Toggle')
    words: List[str] = []
    for part in re.split('[^0-9A-Za-z]+', raw):
        if not part:
            continue
        matches = CONTROL_TOKEN_RE.findall(part)
        for token in matches or [part]:
            words.append(CONTROL_WORD_LABELS.get(token, token))
    return words or [str(var_name or 'Control').strip() or 'Control']

def _compact_control_words(words: List[str]) -> str:
    compact: List[str] = []
    for word in words:
        compact_word = CONTROL_WORD_ABBREVIATIONS.get(word, word)
        if compact and compact[-1] == 'TC' and (compact_word == 'Ctrl'):
            continue
        compact.append(compact_word)
    return ' '.join(compact)

def _initial_control_name(words: List[str]) -> str:
    initials = []
    for word in words:
        clean = ''.join((ch for ch in word if ch.isalnum()))
        if clean and clean.lower() not in {"to", 'and'}:
            initials.append(clean[0].upper())
    return ''.join(initials)

def driver_control_name_candidates(var_name: str) -> List[str]:
    aliases = list(CONTROL_NAME_ALIASES.get(str(var_name or '').strip(), ()))
    words = _control_words(var_name)
    full = ' '.join(words)
    compact = _compact_control_words(words)
    initials = _initial_control_name(words) if len(full) > 4 else ''
    no_space = compact.replace(' ', '')
    return _dedupe_texts([*aliases, full, compact, no_space, initials])

def format_driver_control_name(var_name: str) -> str:
    return driver_control_name_candidates(var_name)[0]

def compact_driver_control_name(var_name: str) -> str:
    candidates = driver_control_name_candidates(var_name)
    for candidate in candidates[1:]:
        if 3 <= len(candidate) <= 8:
            return candidate
    for candidate in candidates[1:]:
        if candidate.isupper() and len(candidate) >= 2:
            return candidate
    return candidates[-1]

def resolve_resource_path(filename: str) -> Optional[str]:
    """Return the first existing path for a bundled or local resource."""
    possible_roots = [getattr(sys, '_MEIPASS', None), os.path.dirname(sys.argv[0]), os.path.abspath(os.path.dirname(__file__))]
    for root in possible_roots:
        if not root:
            continue
        candidate = os.path.join(root, filename)
        if os.path.exists(candidate):
            return candidate
    return None

def set_windows_app_user_model_id() -> None:
    """Set a stable Windows taskbar identity so the installed app shows its own icon."""
    if not sys.platform.startswith('win'):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except Exception as exc:
        print(f'[ICON] Could not set the Windows AppUserModelID: {exc}')

def apply_app_icon(root: tk.Tk) -> None:
    """Set the window icon to the packaged icon when available."""
    applied = False
    for icon_name in ICON_CANDIDATES:
        icon_path = resolve_resource_path(icon_name)
        if not icon_path:
            continue
        try:
            if icon_path.lower().endswith('.ico'):
                try:
                    root.iconbitmap(default=icon_path)
                except TypeError:
                    root.iconbitmap(icon_path)
                applied = True
            else:
                image = tk.PhotoImage(file=icon_path)
                root.iconphoto(True, image)
                root._icon_ref = image
                return
        except Exception as exc:
            print(f'[ICON] Could not load {icon_path}: {exc}')
    if applied:
        return
DEFAULT_OVERLAY_FEEDBACK = {'abs_hold_s': 0.35, 'tc_hold_s': 0.35, 'wheelspin_slip': 0.18, 'wheelspin_hold_s': 0.25, 'lockup_slip': 0.2, 'lockup_hold_s': 0.25, 'cooldown_s': 6.0}
BOT_PRESS_MS = 0
BOT_INTERVAL_MS = 0
LEGACY_BOT_PRESS_MS = 1
LEGACY_BOT_INTERVAL_MS = 1
CLAUDE_PRESS_MS = 14
CLAUDE_INTERVAL_MS = 6
CLAUDE_MIN_MS = 6
CLAUDE_TICK_MS = 1000.0 / 60.0
CLAUDE_STALL_PULSES = 3
CLAUDE_STALL_BACKOFF_S = 0.12
CLAUDE_STALL_BACKOFF_MAX_S = 0.9
CLAUDE_STALL_LONG_PRESS_MS = 28
GLOBAL_TIMING = {'profile': 'bot', 'press_min_ms': 60, 'press_max_ms': 80, 'interval_min_ms': 60, 'interval_max_ms': 90, 'random_enabled': False, 'random_range_ms': 10, 'boundary_press_ms': 6, 'boundary_interval_ms': 6}
TIMING_FORCE_BOT_VERSION = 2
BOUND_DISCOVERY_MAX_PULSES = 320
BOUND_DISCOVERY_STABLE_PULSES = 3
BOUND_DISCOVERY_SETTLE_S = 0.035
BOT_CORRECTION_SETTLE_S = 0.018
BOT_CORRECTION_STABILITY_WINDOW_S = 0.3
BOT_CORRECTION_RETRY_CONFIRM_S = 0.09
BOT_CORRECTION_STATUS_INTERVAL_S = 0.25
SURFACE_DC_PRECISION_PRESS_MS = 80
SURFACE_DC_PRECISION_INTERVAL_MS = 380
SURFACE_DC_PRECISION_SETTLE_S = 0.24
SURFACE_DC_PRECISION_RETRY_S = 0.35
SURFACE_DC_PRECISION_READ_SAMPLES = 3
SURFACE_DC_PRECISION_READ_DELAY_S = 0.06
SURFACE_DC_PRECISION_CONFIRMATIONS = 3
SURFACE_DC_PRECISION_CONFIRM_SAMPLES = 3
SURFACE_DC_PRECISION_CONFIRM_DELAY_S = 0.12
SURFACE_DC_PRECISION_STATUS_INTERVAL_S = 1.5
SURFACE_DC_ENTRY_SETTLE_S = 0.55
SURFACE_DC_ENTRY_RETRY_S = 0.7
SURFACE_DC_DECLARED_GRACE_S = 6.0
SURFACE_DC_ENTRY_GUARD_S = 4.0
SURFACE_DC_ENTRY_GUARD_INTERVAL_S = 0.45
_SURFACE_DC_PRECISION_PULSE_LOCK = threading.Lock()
IRSDK_TRACK_LOCATION_IN_PIT_STALL = 1
IRSDK_TRACK_LOCATION_APPROACHING_PITS = 2
IRSDK_TRACK_LOCATION_ON_TRACK = 3
PIT_LIMITER_LEARNING_MAX_SAMPLES = 7
PIT_LIMITER_LEARNING_FAST_POLL_MS = 20
PIT_LIMITER_LEARNING_BASE_LEAD_S = 0.18
PIT_LIMITER_LEARNING_MIN_LEAD_M = 3.0
PIT_LIMITER_LEARNING_MAX_LEAD_M = 18.0
PIT_LIMITER_LEARNING_MIN_ENTRY_SPEED_MPS = 1.0
PIT_LIMITER_LEARNING_APPROACH_WINDOW_S = 120.0
PIT_LIMITER_LEARNING_MAX_SAMPLE_DRIFT_PCT = 0.02
TTS_STATE = {'last_text': '', 'last_time': 0.0, 'cooldown_s': 1.2}
_TTS_ENGINE = None
_TTS_THREAD: Optional[threading.Thread] = None
_TTS_QUEUE: 'queue.Queue[str]' = queue.Queue()
_TTS_LOCK = threading.Lock()
TTS_OUTPUT_DEVICE_INDEX: Optional[int] = None
VOICE_TUNING_DEFAULTS = {'ambient_duration': 0.2, 'initial_timeout': 1.0, 'continuous_timeout': 0.8, 'phrase_time_limit': 1.2, 'energy_threshold': None, 'dynamic_energy': True}
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write('{}')

def restart_program():
    """Restart the application by closing and relaunching the process."""
    args = _build_launch_command()
    try:
        subprocess.Popen(args, cwd=os.getcwd(), env=os.environ.copy())
    except Exception as exc:
        print(f'[Restart] Could not start the new process: {exc}')
    try:
        root = tk._default_root
        if root is not None:
            root.quit()
            root.destroy()
    except Exception:
        pass
    os._exit(0)

def _build_launch_command() -> List[str]:
    """Build a command list for launching the app (supports frozen exe)."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, *sys.argv[1:]]
    script = os.path.abspath(sys.argv[0])
    return [sys.executable, script, *sys.argv[1:]]

def _startup_entry_path() -> Optional[str]:
    if not STARTUP_FOLDER:
        return None
    return os.path.join(STARTUP_FOLDER, STARTUP_ENTRY_NAME)

def _startup_entry_exists() -> bool:
    path = _startup_entry_path()
    return bool(path and os.path.exists(path))

def set_startup_entry(enabled: bool) -> bool:
    """Create or remove the Windows startup batch file."""
    if not sys.platform.startswith('win'):
        return False
    path = _startup_entry_path()
    if not path:
        return False
    try:
        if enabled:
            os.makedirs(STARTUP_FOLDER, exist_ok=True)
            command_line = subprocess.list2cmdline(_build_launch_command())
            content = f'@echo off\nstart "" {command_line}\n'
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(content)
        elif os.path.exists(path):
            os.remove(path)
        return True
    except Exception as exc:
        print(f'[Startup] Failed to update entry: {exc}')
        return False

def mark_pending_scan(*, silent: bool=False) -> None:
    """Persist a marker so the next launch triggers a rescan."""
    try:
        payload = {'rescan': True, 'silent': silent}
        with open(PENDING_SCAN_FILE, 'w', encoding='utf-8') as flag:
            json.dump(payload, flag)
    except Exception as exc:
        print(f'[Pending scan] Could not save the marker: {exc}')

def consume_pending_scan() -> Tuple[bool, bool]:
    """Return (has_marker, silent) for a pending rescan marker and clear it."""
    if not os.path.exists(PENDING_SCAN_FILE):
        return (False, False)
    silent = False
    try:
        with open(PENDING_SCAN_FILE, 'r', encoding='utf-8') as flag:
            try:
                payload = json.load(flag)
            except Exception:
                flag.seek(0)
                payload = flag.read().strip()
        if isinstance(payload, dict):
            silent = bool(payload.get('silent', False))
        elif isinstance(payload, str):
            silent = 'silent' in payload.lower()
    except Exception as exc:
        print(f'[Pending scan] Could not read the marker: {exc}')
    finally:
        try:
            os.remove(PENDING_SCAN_FILE)
        except Exception as exc:
            print(f'[Pending scan] Could not delete the marker: {exc}')
    return (True, silent)
IS_WINDOWS = os.name == 'nt' and hasattr(ctypes, 'windll')
WINMM_AVAILABLE = IS_WINDOWS and hasattr(ctypes.windll, 'winmm')
if IS_WINDOWS:
    SendInput = ctypes.windll.user32.SendInput
    MapVirtualKeyW = ctypes.windll.user32.MapVirtualKeyW
else:
    SendInput = None
    MapVirtualKeyW = None
    print("Warning: the Windows SendInput APIs are unavailable; sending commands is off.")
KEYEVENTF_EXTENDEDKEY = 1
KEYEVENTF_KEYUP = 2
KEYEVENTF_SCANCODE = 8
MAPVK_VK_TO_VSC_EX = 4
EXTENDED_SCAN_CODES = {28, 29, 53, 56, 71, 72, 73, 75, 77, 79, 80, 81, 82, 83}
PUL = ctypes.POINTER(ctypes.c_ulong)

class KeyBdInput(ctypes.Structure):
    """Keyboard input structure for SendInput."""
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort), ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', PUL)]

class HardwareInput(ctypes.Structure):
    """Hardware input structure for SendInput."""
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort), ('wParamH', ctypes.c_ushort)]

class MouseInput(ctypes.Structure):
    """Mouse input structure for SendInput."""
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', PUL)]

class Input_I(ctypes.Union):
    """Union of input types."""
    _fields_ = [('ki', KeyBdInput), ('mi', MouseInput), ('hi', HardwareInput)]

class Input(ctypes.Structure):
    """Input structure for SendInput."""
    _fields_ = [('type', ctypes.c_ulong), ('ii', Input_I)]
JOYERR_NOERROR = 0
JOY_RETURNBUTTONS = 128
MAXPNAMELEN = 32
MAX_JOYSTICKOEMVXDNAME = 260

class JOYCAPSW(ctypes.Structure):
    """Windows multimedia joystick capabilities."""
    _fields_ = [('wMid', ctypes.c_ushort), ('wPid', ctypes.c_ushort), ('szPname', ctypes.c_wchar * MAXPNAMELEN), ('wXmin', ctypes.c_uint), ('wXmax', ctypes.c_uint), ('wYmin', ctypes.c_uint), ('wYmax', ctypes.c_uint), ('wZmin', ctypes.c_uint), ('wZmax', ctypes.c_uint), ('wNumButtons', ctypes.c_uint), ('wPeriodMin', ctypes.c_uint), ('wPeriodMax', ctypes.c_uint), ('wRmin', ctypes.c_uint), ('wRmax', ctypes.c_uint), ('wUmin', ctypes.c_uint), ('wUmax', ctypes.c_uint), ('wVmin', ctypes.c_uint), ('wVmax', ctypes.c_uint), ('wCaps', ctypes.c_uint), ('wMaxAxes', ctypes.c_uint), ('wNumAxes', ctypes.c_uint), ('wMaxButtons', ctypes.c_uint), ('szRegKey', ctypes.c_wchar * MAXPNAMELEN), ('szOEMVxD', ctypes.c_wchar * MAX_JOYSTICKOEMVXDNAME)]

class JOYINFOEX(ctypes.Structure):
    """Windows multimedia joystick state snapshot."""
    _fields_ = [('dwSize', ctypes.c_uint), ('dwFlags', ctypes.c_uint), ('dwXpos', ctypes.c_uint), ('dwYpos', ctypes.c_uint), ('dwZpos', ctypes.c_uint), ('dwRpos', ctypes.c_uint), ('dwUpos', ctypes.c_uint), ('dwVpos', ctypes.c_uint), ('dwButtons', ctypes.c_uint), ('dwButtonNumber', ctypes.c_uint), ('dwPOV', ctypes.c_uint), ('dwReserved1', ctypes.c_uint), ('dwReserved2', ctypes.c_uint)]
if WINMM_AVAILABLE:
    joyGetNumDevs = ctypes.windll.winmm.joyGetNumDevs
    joyGetNumDevs.restype = ctypes.c_uint
    joyGetDevCapsW = ctypes.windll.winmm.joyGetDevCapsW
    joyGetDevCapsW.argtypes = [ctypes.c_uint, ctypes.POINTER(JOYCAPSW), ctypes.c_uint]
    joyGetDevCapsW.restype = ctypes.c_uint
    joyGetPosEx = ctypes.windll.winmm.joyGetPosEx
    joyGetPosEx.argtypes = [ctypes.c_uint, ctypes.POINTER(JOYINFOEX)]
    joyGetPosEx.restype = ctypes.c_uint
else:
    joyGetNumDevs = None
    joyGetDevCapsW = None
    joyGetPosEx = None

def _scan_code_flags(scan_code: int, key_up: bool=False, extended: Optional[bool]=None) -> Tuple[int, int]:
    scan_code = int(scan_code)
    encoded_extended: Optional[bool] = None
    if scan_code > 255:
        prefix = scan_code >> 8 & 255
        if prefix == 224:
            encoded_extended = True
        scan_code &= 255
    if extended is None:
        if encoded_extended is not None:
            extended = encoded_extended
        else:
            extended = scan_code in EXTENDED_SCAN_CODES
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= KEYEVENTF_KEYUP
    return (scan_code, flags)

def press_key(scan_code: int, extended: Optional[bool]=None):
    """
    Press a key using its scan code.

    Args:
        scan_code: The keyboard scan code to press
    """
    if SendInput is None:
        raise OSError('SendInput APIs are only available on Windows platforms.')
    scan_code, flags = _scan_code_flags(scan_code, extended=extended)
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

def release_key(scan_code: int, extended: Optional[bool]=None):
    """
    Release a key using its scan code.

    Args:
        scan_code: The keyboard scan code to release
    """
    if SendInput is None:
        raise OSError('SendInput APIs are only available on Windows platforms.')
    scan_code, flags = _scan_code_flags(scan_code, key_up=True, extended=extended)
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))
LEFT_SHIFT_SCAN_CODE = 42
LEFT_CTRL_SCAN_CODE = 29
LEFT_ALT_SCAN_CODE = 56
SHIFTED_SCANCODE_PREFIX = 'SHIFTSCAN:'
MODDED_SCANCODE_PREFIX = 'MODSCAN:'
SCANCODE_BINDING_PREFIX = 'SCANCODE:'
KEY_SCANCODE_PREFIX = 'KEYSCAN:'
SHIFTED_KEY_NAMES = {'+', '_', '~', '|', '{', '}', '?', ':', '"', '<', '>'}
SHIFT_BASE_KEY_NAMES = {'=', '-', '`', '\\', '[', ']', ';', "'", ',', '.', '/'}
NUMPAD_NAVIGATION_SCAN_CODES = {71, 72, 73, 75, 77, 79, 80, 81, 82, 83}
KEYPAD_EXTENDED_SCAN_CODES = {28, 53, 69}
EXTENDED_SCAN_CODE_NAMES = {'alt gr', 'apps', 'delete', 'down', 'end', 'home', 'insert', 'left', 'left windows', 'menu', 'num lock', 'page down', 'page up', 'print screen', 'right', 'right alt', 'right ctrl', 'right control', 'right windows', 'up'}
EXTENDED_SCAN_LABELS = {16: 'MEDIA PREV', 25: 'MEDIA NEXT', 28: 'NUMPAD ENTER', 29: 'RIGHT CTRL', 32: 'VOLUME MUTE', 33: 'LAUNCH APP2', 34: 'MEDIA PLAY/PAUSE', 36: 'MEDIA STOP', 46: 'VOLUME DOWN', 48: 'VOLUME UP', 50: 'BROWSER HOME', 53: 'NUMPAD /', 55: 'PRINT SCREEN', 56: 'RIGHT ALT', 69: 'NUM LOCK', 71: 'HOME', 72: 'UP', 73: 'PAGE UP', 75: 'LEFT', 77: 'RIGHT', 79: 'END', 80: 'DOWN', 81: 'PAGE DOWN', 82: 'INSERT', 83: 'DELETE', 91: 'LEFT WINDOWS', 92: 'RIGHT WINDOWS', 93: 'MENU', 101: 'BROWSER SEARCH', 102: 'BROWSER FAVORITES', 103: 'BROWSER REFRESH', 104: 'BROWSER STOP', 105: 'BROWSER FORWARD', 106: 'BROWSER BACK', 107: 'LAUNCH APP1', 108: 'LAUNCH MAIL', 109: 'MEDIA SELECT'}
UNSAFE_SYSTEM_EXTENDED_SCAN_CODES = {16, 25, 32, 33, 34, 36, 46, 48, 50, 101, 102, 103, 104, 105, 106, 107, 108, 109}
NON_EXTENDED_SCAN_LABELS = {1: 'ESC', 2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0', 12: '-', 13: '=', 14: 'BACKSPACE', 15: 'TAB', 16: 'Q', 17: 'W', 18: 'E', 19: 'R', 20: 'T', 21: 'Y', 22: 'U', 23: 'I', 24: 'O', 25: 'P', 26: '[', 27: ']', 28: 'ENTER', 29: 'CTRL', 30: 'A', 31: 'S', 32: 'D', 33: 'F', 34: 'G', 35: 'H', 36: 'J', 37: 'K', 38: 'L', 39: ';', 40: "'", 41: '`', 42: 'LEFT SHIFT', 43: '\\', 44: 'Z', 45: 'X', 46: 'C', 47: 'V', 48: 'B', 49: 'N', 50: 'M', 51: ',', 52: '.', 53: '/', 54: 'RIGHT SHIFT', 55: 'NUMPAD *', 56: 'ALT', 57: 'SPACE', 58: 'CAPS LOCK', 59: 'F1', 60: 'F2', 61: 'F3', 62: 'F4', 63: 'F5', 64: 'F6', 65: 'F7', 66: 'F8', 67: 'F9', 68: 'F10', 87: 'F11', 88: 'F12', 71: 'NUMPAD 7/HOME', 72: 'NUMPAD 8/UP', 73: 'NUMPAD 9/PAGE UP', 74: 'NUMPAD -', 75: 'NUMPAD 4/LEFT', 76: 'NUMPAD 5', 77: 'NUMPAD 6/RIGHT', 78: 'NUMPAD +', 79: 'NUMPAD 1/END', 80: 'NUMPAD 2/DOWN', 81: 'NUMPAD 3/PAGE DOWN', 82: 'NUMPAD 0/INSERT', 83: 'NUMPAD ./DELETE'}

def _coerce_scan_code(value: Any) -> Optional[int]:
    """Return a positive scan code integer or None."""
    try:
        scan_code = int(value)
    except Exception:
        return None
    return scan_code if scan_code > 0 else None

def _split_scan_code(scan_code: int) -> Tuple[int, Optional[bool]]:
    """Split an encoded scan code into base scan and explicit E0 state."""
    scan_code = int(scan_code)
    if scan_code <= 0:
        return (0, None)
    if scan_code > 255:
        prefix = scan_code >> 8 & 255
        if prefix == 224:
            return (scan_code & 255, True)
        return (scan_code & 255, None)
    return (scan_code, None)

def _format_scan_code_payload(scan_code: int, extended: bool=False) -> str:
    """Format a scan code payload for config strings."""
    base_scan, encoded_extended = _split_scan_code(scan_code)
    if encoded_extended is not None:
        extended = encoded_extended
    if base_scan <= 0:
        base_scan = 1
    return f'E0:{base_scan}' if extended else str(base_scan)

def _parse_scan_code_payload(payload: Any, *, default_extended: bool=False) -> Optional[Tuple[int, bool]]:
    """Parse decimal/hex scan-code payloads, optionally carrying E0."""
    text = str(payload or '').strip()
    if not text:
        return None
    extended = default_extended
    force_hex = False
    upper_text = text.upper()
    if upper_text.startswith('E0:'):
        extended = True
        text = text.split(':', 1)[1].strip()
        upper_text = text.upper()
    elif upper_text.startswith('E0') and len(upper_text) > 2:
        extended = True
        force_hex = True
        text = upper_text[2:].strip()
        upper_text = text.upper()
    try:
        if force_hex:
            scan_code = int(upper_text, 16)
        elif upper_text.startswith('0X'):
            scan_code = int(upper_text, 16)
        elif any((ch in 'ABCDEF' for ch in upper_text)):
            scan_code = int(upper_text, 16)
        else:
            scan_code = int(upper_text)
    except Exception:
        return None
    base_scan, encoded_extended = _split_scan_code(scan_code)
    if encoded_extended is not None:
        extended = encoded_extended
    if base_scan <= 0:
        return None
    return (base_scan, bool(extended))

def _normalize_prefixed_scan_code(text: str, prefix: str, *, default_extended: bool=False) -> Optional[str]:
    """Normalize SCANCODE-like config strings to a stable representation."""
    if not text.upper().startswith(prefix):
        return None
    payload = text[len(prefix):]
    parsed = _parse_scan_code_payload(payload, default_extended=default_extended)
    if not parsed:
        return None
    scan_code, extended = parsed
    return f'{prefix}{_format_scan_code_payload(scan_code, extended)}'

def _parse_prefixed_scan_code(binding: Any, prefix: str, *, default_extended: bool=False) -> Optional[Tuple[int, bool]]:
    """Parse a SCANCODE-like binding into base scan code and E0 state."""
    text = str(binding or '').strip()
    if not text.upper().startswith(prefix):
        return None
    return _parse_scan_code_payload(text[len(prefix):], default_extended=default_extended)

def _keyboard_event_uses_extended_scan_code(event: Any, scan_code: int) -> bool:
    """Infer whether a keyboard event came from an E0-prefixed key."""
    base_scan, encoded_extended = _split_scan_code(scan_code)
    if encoded_extended is not None:
        return encoded_extended
    name = str(getattr(event, 'name', '') or '').strip().lower()
    is_keypad = bool(getattr(event, 'is_keypad', False))
    if base_scan in NUMPAD_NAVIGATION_SCAN_CODES:
        return not is_keypad
    if base_scan in KEYPAD_EXTENDED_SCAN_CODES:
        return is_keypad or name in EXTENDED_SCAN_CODE_NAMES
    if base_scan == 29:
        return name in {'right ctrl', 'right control'}
    if base_scan == 56:
        return name in {'alt gr', 'right alt'}
    if base_scan in {91, 92}:
        return True
    return name in EXTENDED_SCAN_CODE_NAMES

def _keyboard_event_scan_code(event: Any) -> Optional[Tuple[int, bool]]:
    """Return the event scan code with explicit E0/non-E0 state."""
    scan_code = _coerce_scan_code(getattr(event, 'scan_code', None))
    if scan_code is None:
        return None
    name = str(getattr(event, 'name', '') or '').strip().lower()
    if name == 'alt gr':
        return (56, True)
    base_scan, encoded_extended = _split_scan_code(scan_code)
    if encoded_extended is not None:
        return (base_scan, encoded_extended)
    return (base_scan, _keyboard_event_uses_extended_scan_code(event, base_scan))

def _keyboard_event_scancode_binding(event: Any, prefix: str) -> Optional[str]:
    """Encode a keyboard event as SCANCODE/KEYSCAN binding text."""
    parsed = _keyboard_event_scan_code(event)
    if not parsed:
        return None
    scan_code, extended = parsed
    return f'{prefix}{_format_scan_code_payload(scan_code, extended)}'

def _normalize_key_scancode_code(code: Any) -> Optional[str]:
    """Normalize a KEYSCAN hotkey code or return None."""
    text = str(code or '').strip()
    return _normalize_prefixed_scan_code(text, KEY_SCANCODE_PREFIX, default_extended=False)

def _keyboard_event_input_code(event: Any) -> Optional[str]:
    """Return the canonical KEYSCAN code for a keyboard event."""
    return _keyboard_event_scancode_binding(event, KEY_SCANCODE_PREFIX)

def _scan_code_label(scan_code: int, extended: bool) -> str:
    """Human-friendly label for a scan code."""
    labels = EXTENDED_SCAN_LABELS if extended else NON_EXTENDED_SCAN_LABELS
    return labels.get(scan_code, f'SCAN {scan_code}')

def _keyboard_event_label(event: Any, scan_code: int, extended: bool) -> str:
    """Return the best display label for a captured keyboard event."""
    name = str(getattr(event, 'name', '') or '').strip()
    if name.lower() == 'alt gr':
        return 'ALT GR'
    label = _scan_code_label(scan_code, extended)
    if not label.startswith('SCAN '):
        return label
    return name.upper() if name else label

def _format_input_code_label(code: Any) -> str:
    """Format stored hotkey codes for buttons and warnings."""
    normalized = _normalize_key_scancode_code(code)
    if normalized:
        parsed = _parse_prefixed_scan_code(normalized, KEY_SCANCODE_PREFIX, default_extended=False)
        if parsed:
            scan_code, extended = parsed
            return f'KEY:{_scan_code_label(scan_code, extended)}'
    return str(code)

def _is_virtual_joystick_name(name: Any) -> bool:
    """Return True for joystick devices that should be treated as virtual output."""
    normalized = str(name or '').strip().lower()
    if not normalized:
        return False
    return 'vjoy' in normalized or 'vigem' in normalized or 'virtual joystick' in normalized or ('virtual gamepad' in normalized)

def _normalize_game_input_binding(value: Any) -> Any:
    """Normalize stored bindings to keyboard scan codes or binding strings."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, numbers.Integral):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    upper_text = text.upper()
    if upper_text.startswith('VJOY:'):
        return None
    normalized_scan = _normalize_prefixed_scan_code(text, SCANCODE_BINDING_PREFIX, default_extended=False)
    if normalized_scan:
        return normalized_scan
    if upper_text.startswith(MODDED_SCANCODE_PREFIX):
        normalized_modded = _normalize_modded_scancode_binding(text)
        if normalized_modded:
            return normalized_modded
        return text
    if upper_text.startswith(SHIFTED_SCANCODE_PREFIX):
        normalized_shifted = _normalize_prefixed_scan_code(text, SHIFTED_SCANCODE_PREFIX, default_extended=False)
        if normalized_shifted:
            return normalized_shifted
        return text
    try:
        return int(text)
    except Exception:
        return text

def _parse_scancode_binding(binding: Any) -> Optional[Tuple[int, bool]]:
    """Parse a binding string in the format SCANCODE:<scan_code>."""
    normalized = _normalize_game_input_binding(binding)
    if not isinstance(normalized, str):
        return None
    return _parse_prefixed_scan_code(normalized, SCANCODE_BINDING_PREFIX, default_extended=False)

def _parse_shifted_scancode_binding(binding: Any) -> Optional[Tuple[int, bool]]:
    """Parse a binding string in the format SHIFTSCAN:<scan_code>."""
    normalized = _normalize_game_input_binding(binding)
    if not isinstance(normalized, str):
        return None
    return _parse_prefixed_scan_code(normalized, SHIFTED_SCANCODE_PREFIX, default_extended=False)

def _parse_modded_scancode_binding(binding: Any) -> Optional[Tuple[int, int, bool]]:
    """Parse MODSCAN:<mask>:<scan> bindings."""
    text = str(binding or '').strip()
    if not text.upper().startswith(MODDED_SCANCODE_PREFIX):
        return None
    payload = text[len(MODDED_SCANCODE_PREFIX):]
    parts = payload.split(':', 1)
    if len(parts) != 2:
        return None
    try:
        modifier_mask = int(parts[0])
    except Exception:
        return None
    parsed = _parse_scan_code_payload(parts[1], default_extended=False)
    if not parsed:
        return None
    scan_code, extended = parsed
    return (modifier_mask & 7, scan_code, extended)

def _normalize_modded_scancode_binding(text: str) -> Optional[str]:
    """Normalize a MODSCAN binding to a stable representation."""
    if not str(text or '').upper().startswith(MODDED_SCANCODE_PREFIX):
        return None
    parsed = _parse_modded_scancode_binding(text)
    if not parsed:
        return None
    modifier_mask, scan_code, extended = parsed
    return f'{MODDED_SCANCODE_PREFIX}{modifier_mask}:{_format_scan_code_payload(scan_code, extended)}'

def _modded_scan_modifier_codes(modifier_mask: int) -> List[int]:
    """Return scan codes for the simplified Shift/Ctrl/Alt modifier mask."""
    codes: List[int] = []
    if modifier_mask & 1:
        codes.append(LEFT_SHIFT_SCAN_CODE)
    if modifier_mask & 2:
        codes.append(LEFT_CTRL_SCAN_CODE)
    if modifier_mask & 4:
        codes.append(LEFT_ALT_SCAN_CODE)
    return codes

def _unsafe_system_scan_reason(scan_code: int, extended: bool) -> Optional[str]:
    """Return a label when a scan code would trigger a Windows system action."""
    if extended and int(scan_code) in UNSAFE_SYSTEM_EXTENDED_SCAN_CODES:
        return _scan_code_label(scan_code, extended)
    return None

def _unsafe_system_binding_reason(binding: Any) -> Optional[str]:
    """Return a reason if a game binding should not be sent by the app."""
    normalized = _normalize_game_input_binding(binding)
    if normalized is None:
        return None
    modded_scan = _parse_modded_scancode_binding(normalized)
    if modded_scan is not None:
        _modifier_mask, scan_code, extended = modded_scan
        reason = _unsafe_system_scan_reason(scan_code, extended)
        return reason
    shifted_scan = _parse_shifted_scancode_binding(normalized)
    if shifted_scan is not None:
        scan_code, extended = shifted_scan
        return _unsafe_system_scan_reason(scan_code, extended)
    scan_binding = _parse_scancode_binding(normalized)
    if scan_binding is not None:
        scan_code, extended = scan_binding
        return _unsafe_system_scan_reason(scan_code, extended)
    return None

def _format_game_binding_label(binding: Any) -> str:
    """Return a readable label for a game-facing binding."""
    normalized = _normalize_game_input_binding(binding)
    if normalized is None:
        return ''
    modded_scan = _parse_modded_scancode_binding(normalized)
    if modded_scan is not None:
        modifier_mask, scan_code, extended = modded_scan
        parts: List[str] = []
        if modifier_mask & 1:
            parts.append('SHIFT')
        if modifier_mask & 2:
            parts.append('CTRL')
        if modifier_mask & 4:
            parts.append('ALT')
        parts.append(_scan_code_label(scan_code, extended))
        return '+'.join(parts)
    shifted_scan = _parse_shifted_scancode_binding(normalized)
    if shifted_scan is not None:
        scan_code, extended = shifted_scan
        return f'SHIFT+{_scan_code_label(scan_code, extended)}'
    scan_binding = _parse_scancode_binding(normalized)
    if scan_binding is not None:
        scan_code, extended = scan_binding
        return _scan_code_label(scan_code, extended)
    if isinstance(normalized, numbers.Integral):
        return _scan_code_label(int(normalized), False)
    if isinstance(normalized, str) and normalized.upper().startswith('KEY:'):
        return normalized.split(':', 1)[1].strip().upper()
    return str(normalized)

def _keyboard_binding_scan_identity(binding: Any) -> Optional[Tuple[int, bool]]:
    """Return the physical scan-code identity shared by input/output bindings.

    Global app hotkeys use ``KEYSCAN`` while keys sent to iRacing use
    ``SCANCODE``/``SHIFTSCAN``/``MODSCAN``.  Comparing their physical identity
    prevents an injected output key from recursively activating its own app
    hotkey.
    """
    if binding is None or isinstance(binding, bool):
        return None
    if isinstance(binding, numbers.Integral):
        return (int(binding), False)
    hotkey = _normalize_key_scancode_code(binding)
    if hotkey:
        return _parse_prefixed_scan_code(hotkey, KEY_SCANCODE_PREFIX, default_extended=False)
    text = str(binding or '').strip()
    if text.upper().startswith('KEY:'):
        key_name = text.split(':', 1)[1].strip().upper().replace('_', ' ')
        key_name = {'ESCAPE': 'ESC', 'RETURN': 'ENTER', 'CONTROL': 'CTRL', 'LEFT CONTROL': 'CTRL', 'LEFT CTRL': 'CTRL', 'LEFT ALT': 'ALT'}.get(key_name, key_name)
        for scan_code, label in EXTENDED_SCAN_LABELS.items():
            if key_name == label:
                return (scan_code, True)
        for scan_code, label in NON_EXTENDED_SCAN_LABELS.items():
            if key_name == label:
                return (scan_code, False)
    modded = _parse_modded_scancode_binding(binding)
    if modded is not None:
        _modifier_mask, scan_code, extended = modded
        return (scan_code, extended)
    shifted = _parse_shifted_scancode_binding(binding)
    if shifted is not None:
        return shifted
    return _parse_scancode_binding(binding)

def _game_binding_to_hotkey_code(binding: Any) -> Optional[str]:
    """Convert a game-facing keyboard binding to its global-hotkey identity."""
    identity = _keyboard_binding_scan_identity(binding)
    if identity is None:
        return None
    scan_code, extended = identity
    return f'{KEY_SCANCODE_PREFIX}{_format_scan_code_payload(scan_code, extended)}'

def _iracing_modifier_mask(raw_mask: int) -> int:
    """Convert iRacing modifier bits to the app Shift/Ctrl/Alt mask."""
    raw_mask = int(raw_mask)
    modifier_bits = raw_mask & 63 | raw_mask >> 16 & 63
    app_mask = 0
    if modifier_bits & IRACING_CONTROL_MOD_SHIFT:
        app_mask |= 1
    if modifier_bits & IRACING_CONTROL_MOD_CTRL:
        app_mask |= 2
    if modifier_bits & IRACING_CONTROL_MOD_ALT:
        app_mask |= 4
    return app_mask

def _iracing_code_override_scan(code: int) -> Optional[Tuple[int, bool]]:
    """Map iRacing/Windows key codes that are ambiguous or not VK-native."""
    code = int(code)
    if IRACING_INTERNAL_FKEY_BASE <= code <= IRACING_INTERNAL_FKEY_BASE + 11:
        offset = code - IRACING_INTERNAL_FKEY_BASE
        if offset < 10:
            return (59 + offset, False)
        return (87 if offset == 10 else 88, False)
    explicit: Dict[int, Tuple[int, bool]] = {8: (14, False), 9: (15, False), 12: (76, False), 13: (28, False), 16: (LEFT_SHIFT_SCAN_CODE, False), 17: (LEFT_CTRL_SCAN_CODE, False), 18: (LEFT_ALT_SCAN_CODE, False), 19: (69, False), 20: (58, False), 27: (1, False), 32: (57, False), 33: (73, True), 34: (81, True), 35: (79, True), 36: (71, True), 37: (75, True), 38: (72, True), 39: (77, True), 40: (80, True), 44: (55, True), 45: (82, True), 46: (83, True), 91: (91, True), 92: (92, True), 93: (93, True), 96: (82, False), 97: (79, False), 98: (80, False), 99: (81, False), 100: (75, False), 101: (76, False), 102: (77, False), 103: (71, False), 104: (72, False), 105: (73, False), 106: (55, False), 107: (78, False), 108: (28, True), 109: (74, False), 110: (83, False), 111: (53, True), 144: (69, True), 145: (70, False), 160: (LEFT_SHIFT_SCAN_CODE, False), 161: (54, False), 162: (LEFT_CTRL_SCAN_CODE, False), 163: (29, True), 164: (LEFT_ALT_SCAN_CODE, False), 165: (56, True), 166: (106, True), 167: (105, True), 168: (103, True), 169: (104, True), 170: (101, True), 171: (102, True), 172: (50, True), 173: (32, True), 174: (46, True), 175: (48, True), 176: (25, True), 177: (16, True), 178: (36, True), 179: (34, True), 180: (108, True), 181: (109, True), 182: (107, True), 183: (33, True), 186: (39, False), 187: (13, False), 188: (51, False), 189: (12, False), 190: (52, False), 191: (53, False), 192: (41, False), 219: (26, False), 220: (43, False), 221: (27, False), 222: (40, False), 226: (86, False)}
    if code in explicit:
        return explicit[code]
    if 48 <= code <= 57:
        return (code - 47, False)
    if 65 <= code <= 90:
        qwerty = {65: 30, 66: 48, 67: 46, 68: 32, 69: 18, 70: 33, 71: 34, 72: 35, 73: 23, 74: 36, 75: 37, 76: 38, 77: 50, 78: 49, 79: 24, 80: 25, 81: 16, 82: 19, 83: 31, 84: 20, 85: 22, 86: 47, 87: 17, 88: 45, 89: 21, 90: 44}
        return (qwerty[code], False)
    if 112 <= code <= 123:
        offset = code - 112
        if offset < 10:
            return (59 + offset, False)
        return (87 if offset == 10 else 88, False)
    return None

def _vk_to_scan_code(vk_code: int) -> Optional[Tuple[int, bool]]:
    """Convert an iRacing/Windows key code to a scan code for SendInput."""
    vk_code = int(vk_code)
    if vk_code <= 0:
        return None
    override = _iracing_code_override_scan(vk_code)
    if override:
        return override
    force_extended = {33, 34, 35, 36, 37, 38, 39, 40, 45, 46, 163, 165}
    force_plain = set(range(96, 106)) | {106, 107, 109, 110}
    scan_ex = 0
    if MapVirtualKeyW is not None:
        try:
            scan_ex = int(MapVirtualKeyW(vk_code, MAPVK_VK_TO_VSC_EX))
        except Exception:
            scan_ex = 0
    if scan_ex:
        prefix = scan_ex >> 8 & 255
        scan_code = scan_ex & 255
        extended = prefix == 224 or vk_code in force_extended
        if vk_code in force_plain:
            extended = False
        if scan_code > 0:
            return (scan_code, extended)
    fallback = {8: 14, 9: 15, 13: 28, 16: 42, 17: 29, 18: 56, 27: 1, 32: 57, 186: 39, 187: 13, 188: 51, 189: 12, 190: 52, 191: 53, 192: 41, 219: 26, 220: 43, 221: 27, 222: 40}
    if 65 <= vk_code <= 90:
        qwerty = {65: 30, 66: 48, 67: 46, 68: 32, 69: 18, 70: 33, 71: 34, 72: 35, 73: 23, 74: 36, 75: 37, 76: 38, 77: 50, 78: 49, 79: 24, 80: 25, 81: 16, 82: 19, 83: 31, 84: 20, 85: 22, 86: 47, 87: 17, 88: 45, 89: 21, 90: 44}
        return (qwerty.get(vk_code), False)
    if 48 <= vk_code <= 57:
        return (vk_code - 47, False)
    if 112 <= vk_code <= 123:
        return (59 + (vk_code - 112), False)
    if vk_code in fallback:
        return (fallback[vk_code], vk_code in force_extended)
    return None

def _iracing_vk_binding(vk_code: int, raw_modifier_mask: int) -> Optional[str]:
    """Return a game binding string from an iRacing controls.cfg keyboard entry."""
    parsed = _vk_to_scan_code(vk_code)
    if not parsed:
        return None
    scan_code, extended = parsed
    payload = _format_scan_code_payload(scan_code, extended)
    modifier_mask = _iracing_modifier_mask(raw_modifier_mask)
    if modifier_mask:
        return f'{MODDED_SCANCODE_PREFIX}{modifier_mask}:{payload}'
    return f'{SCANCODE_BINDING_PREFIX}{payload}'

def _parse_joy_button_code(code: Any) -> Optional[int]:
    """Extract the button index from a JOY:<device_id>:<button> code."""
    parts = str(code or '').strip().upper().split(':')
    if len(parts) != 3 or parts[0] != 'JOY':
        return None
    try:
        return int(parts[2])
    except Exception:
        return None

def _press_game_input(binding: Any) -> None:
    """Press a configured keyboard binding."""
    normalized = _normalize_game_input_binding(binding)
    if normalized is None:
        return
    unsafe_reason = _unsafe_system_binding_reason(normalized)
    if unsafe_reason:
        raise ValueError(f'System key blocked to avoid Windows actions: {unsafe_reason}')
    if isinstance(normalized, numbers.Integral):
        press_key(int(normalized))
        return
    modded_scan = _parse_modded_scancode_binding(normalized)
    if modded_scan is not None:
        modifier_mask, scan_code, extended = modded_scan
        for modifier_scan in _modded_scan_modifier_codes(modifier_mask):
            press_key(modifier_scan)
        press_key(scan_code, extended=extended)
        return
    scan_binding = _parse_scancode_binding(normalized)
    if scan_binding is not None:
        scan_code, extended = scan_binding
        press_key(scan_code, extended=extended)
        return
    shifted_scan = _parse_shifted_scancode_binding(normalized)
    if shifted_scan is not None:
        scan_code, extended = shifted_scan
        press_key(LEFT_SHIFT_SCAN_CODE)
        press_key(scan_code, extended=extended)
        return
    if isinstance(normalized, str) and normalized.upper().startswith('KEY:'):
        key_name = normalized.split(':', 1)[1].strip().lower()
        if key_name:
            keyboard.press(key_name)
            return
    raise ValueError(f'Unsupported game input command: {binding}')

def _release_game_input(binding: Any) -> None:
    """Release a configured keyboard binding."""
    normalized = _normalize_game_input_binding(binding)
    if normalized is None:
        return
    if isinstance(normalized, numbers.Integral):
        release_key(int(normalized))
        return
    modded_scan = _parse_modded_scancode_binding(normalized)
    if modded_scan is not None:
        modifier_mask, scan_code, extended = modded_scan
        release_key(scan_code, extended=extended)
        for modifier_scan in reversed(_modded_scan_modifier_codes(modifier_mask)):
            release_key(modifier_scan)
        return
    scan_binding = _parse_scancode_binding(normalized)
    if scan_binding is not None:
        scan_code, extended = scan_binding
        release_key(scan_code, extended=extended)
        return
    shifted_scan = _parse_shifted_scancode_binding(normalized)
    if shifted_scan is not None:
        scan_code, extended = shifted_scan
        release_key(scan_code, extended=extended)
        release_key(LEFT_SHIFT_SCAN_CODE)
        return
    if isinstance(normalized, str) and normalized.upper().startswith('KEY:'):
        key_name = normalized.split(':', 1)[1].strip().lower()
        if key_name:
            keyboard.release(key_name)
            return
    raise ValueError(f'Unsupported game input command: {binding}')

def _should_capture_shifted_scancode(key_name: Any) -> bool:
    """Return True when the current keyboard press should replay with Shift held."""
    normalized = str(key_name or '').strip()
    if normalized not in SHIFTED_KEY_NAMES and normalized not in SHIFT_BASE_KEY_NAMES:
        return False
    try:
        return keyboard.is_pressed('shift')
    except Exception:
        return False

def _normalize_timing_config(timing: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize timing configuration and ensure required keys exist.

    Args:
        timing: Raw timing configuration dictionary.

    Returns:
        A sanitized copy with validated bounds and known profiles.
    """
    normalized = dict(GLOBAL_TIMING)
    if not isinstance(timing, dict):
        return normalized
    normalized.update(timing)
    allowed_profiles = {'aggressive', 'casual', 'relaxed', 'custom', 'bot', 'bot_safe', 'claude'}
    if normalized.get('profile') not in allowed_profiles:
        normalized['profile'] = 'bot'
    for key in ['press_min_ms', 'press_max_ms', 'interval_min_ms', 'interval_max_ms', 'random_range_ms', 'boundary_press_ms', 'boundary_interval_ms']:
        try:
            normalized[key] = max(1, int(normalized.get(key, GLOBAL_TIMING[key])))
        except (TypeError, ValueError, KeyError):
            normalized[key] = GLOBAL_TIMING.get(key, 10)
    normalized['random_enabled'] = bool(normalized.get('random_enabled', False))
    return normalized

def _compute_timing(is_float: bool=False) -> Tuple[float, float]:
    """
    Compute press and interval timing based on global profile.
    
    Args:
        is_float: Whether this is for a float variable (gets extra delay)
        
    Returns:
        Tuple of (press_time_seconds, interval_time_seconds)
    """
    timing_cfg = _normalize_timing_config(GLOBAL_TIMING)
    profile = timing_cfg.get('profile', 'aggressive')
    if profile == 'aggressive':
        press_ms = 25
        interval_ms = 10
    elif profile == 'casual':
        press_ms = 80
        interval_ms = 100
    elif profile == 'relaxed':
        press_ms = 150
        interval_ms = 200
    elif profile == 'bot':
        press_ms = BOT_PRESS_MS
        interval_ms = BOT_INTERVAL_MS
    elif profile == 'bot_safe':
        press_ms = 12
        interval_ms = 6
    elif profile == 'claude':
        press_ms = CLAUDE_PRESS_MS
        interval_ms = CLAUDE_INTERVAL_MS
    else:
        p_min = timing_cfg.get('press_min_ms', 60)
        p_max = timing_cfg.get('press_max_ms', 80)
        i_min = timing_cfg.get('interval_min_ms', 60)
        i_max = timing_cfg.get('interval_max_ms', 90)
        press_ms = random.uniform(p_min, p_max)
        interval_ms = random.uniform(i_min, i_max)
        if timing_cfg.get('random_enabled', False):
            rng = timing_cfg.get('random_range_ms', 10)
            press_ms += random.uniform(-rng, rng)
            interval_ms += random.uniform(-rng, rng)
    if profile == 'bot':
        min_value = 0
    elif profile == 'bot_safe':
        min_value = 5
    elif profile == 'claude':
        min_value = CLAUDE_MIN_MS
    else:
        min_value = 10
    press_ms = max(min_value, press_ms)
    interval_ms = max(min_value, interval_ms)
    if is_float and profile not in ('bot', 'claude'):
        press_ms += 30
    return (press_ms / 1000.0, interval_ms / 1000.0)

def _binding_uses_vjoy(binding: Any) -> bool:
    """Return True for legacy vJoy-style bindings stored in config."""
    if binding is None:
        return False
    return str(binding).strip().upper().startswith('VJOY:')

def _sleep_after_output(binding: Any, keyboard_s: float, vjoy_s: float) -> None:
    """Sleep after a pulse, using shorter settle times for vJoy output."""
    delay = vjoy_s if _binding_uses_vjoy(binding) else keyboard_s
    if _binding_uses_vjoy(binding):
        delay = max(delay, VJOY_POST_PULSE_SETTLE_MS / 1000.0)
    if delay <= 0:
        return
    time.sleep(delay)

def _apply_vjoy_click_floor(binding: Any, press_s: float, interval_s: float) -> Tuple[float, float]:
    """Apply non-zero floors so virtual joystick pulses are visible to the game."""
    if not _binding_uses_vjoy(binding):
        return (press_s, interval_s)
    return (max(press_s, VJOY_CLICK_PRESS_FLOOR_MS / 1000.0), max(interval_s, VJOY_CLICK_INTERVAL_FLOOR_MS / 1000.0))

def _pause_joystick_input_for_vjoy(duration_s: float) -> None:
    """Temporarily pause joystick polling while a vJoy pulse is being emitted."""
    manager = globals().get('input_manager')
    if manager is None or duration_s <= 0:
        return
    try:
        manager.pause_joystick_polling(duration_s)
    except Exception:
        pass

def _normalize_var_tuple(entry: Tuple[Any, ...]) -> Tuple[str, bool, bool]:
    """Normalize a stored variable tuple to (name, is_float, is_boolean)."""
    if isinstance(entry, (list, tuple)):
        if len(entry) >= 3:
            name, is_float, is_boolean = entry[:3]
            return (str(name), bool(is_float), bool(is_boolean))
        if len(entry) == 2:
            name, is_float = entry
            return (str(name), bool(is_float), False)
    return (str(entry), False, False)

def _parse_optional_float(value: Any) -> Optional[float]:
    """Parse an optional numeric field, accepting comma or dot decimals."""
    text = str(value or '').strip().replace(',', '.')
    if not text:
        return None
    try:
        parsed = float(text)
    except Exception:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed

def _format_limit_value(value: Optional[float], is_float: bool) -> str:
    """Format a persisted/discovered control limit for the UI."""
    if value is None:
        return ''
    if is_float:
        text = f'{float(value):.6f}'.rstrip('0').rstrip('.')
        return text if text else '0'
    return str(int(round(float(value))))

def _boundary_pulse_timing_ms() -> Tuple[int, int]:
    """Return press/interval timings for BOT boundary (min/max) pulses."""
    timing_cfg = _normalize_timing_config(GLOBAL_TIMING)
    press_ms = max(1, int(timing_cfg.get('boundary_press_ms', 6)))
    interval_ms = max(1, int(timing_cfg.get('boundary_interval_ms', 6)))
    return (press_ms, interval_ms)

def _pulse_rate_hz(press_ms: int, interval_ms: int) -> float:
    """Compute approximate pulses-per-second based on press + interval timing."""
    total_ms = max(1, press_ms + interval_ms)
    return 1000.0 / total_ms

def _estimate_macro_duration_ms(steps: int, press_ms: float, interval_ms: float) -> float:
    """Estimate total duration for a fixed-step macro in milliseconds."""
    return max(0.0, steps) * max(0.0, press_ms + interval_ms)

def _bot_legacy_savings_ms(steps: int) -> Tuple[float, float, float]:
    """Return (legacy_ms, current_ms, savings_ms) for BOT timing."""
    legacy_ms = _estimate_macro_duration_ms(steps, LEGACY_BOT_PRESS_MS, LEGACY_BOT_INTERVAL_MS)
    current_ms = _estimate_macro_duration_ms(steps, BOT_PRESS_MS, BOT_INTERVAL_MS)
    return (legacy_ms, current_ms, max(0.0, legacy_ms - current_ms))

def click_pulse(binding: Any, is_float: bool=False):
    """
    Execute a single input pulse with timing.
    
    Args:
        binding: Keyboard scan code or VJOY:<device_id>:<button_id>
        is_float: Whether this is for a float variable
    """
    binding = _normalize_game_input_binding(binding)
    if binding is None:
        return
    try:
        t_press, t_interval = _compute_timing(is_float=is_float)
        t_press, t_interval = _apply_vjoy_click_floor(binding, t_press, t_interval)
        if _binding_uses_vjoy(binding):
            _pause_joystick_input_for_vjoy(t_press + t_interval + VJOY_POST_PULSE_SETTLE_MS / 1000.0)
        _press_game_input(binding)
        time.sleep(t_press)
        _release_game_input(binding)
        time.sleep(t_interval)
    except Exception as e:
        print(f'[pulse] Error: {e}')

def _direct_pulse(binding: Any, press_ms: int, interval_ms: int):
    """
    Execute a single input pulse with explicit timing overrides.

    Args:
        binding: Keyboard scan code or VJOY:<device_id>:<button_id>.
        press_ms: Duration to hold the key in milliseconds.
        interval_ms: Post-release interval in milliseconds.
    """
    binding = _normalize_game_input_binding(binding)
    if binding is None:
        return
    try:
        _press_game_input(binding)
        press_floor_ms = VJOY_DIRECT_PRESS_FLOOR_MS if _binding_uses_vjoy(binding) else 1
        interval_floor_ms = VJOY_DIRECT_INTERVAL_FLOOR_MS if _binding_uses_vjoy(binding) else 1
        effective_press_s = max(press_floor_ms, press_ms) / 1000.0
        effective_interval_s = max(interval_floor_ms, interval_ms) / 1000.0
        if _binding_uses_vjoy(binding):
            _pause_joystick_input_for_vjoy(effective_press_s + effective_interval_s + VJOY_POST_PULSE_SETTLE_MS / 1000.0)
        time.sleep(effective_press_s)
        _release_game_input(binding)
        time.sleep(effective_interval_s)
    except Exception as e:
        print(f'[direct pulse] Error: {e}')
VOICE_LANGUAGE_HINTS: tuple[str, ...] = ("english",)
VOICE_LANGUAGE_TAGS: tuple[str, ...] = (
    "en-us",
    "en_us",
    "enus",
    "en-gb",
    "en_gb",
    "engb",
)

def _select_voice_for_language(engine) -> Optional[str]:
    """Prefer a voice that speaks the application's language."""
    try:
        voices = engine.getProperty('voices') or []
    except Exception:
        return None
    fallback: Optional[str] = None
    for voice in voices:
        name = str(getattr(voice, 'name', '')).lower()
        vid = str(getattr(voice, 'id', '')).lower()
        languages = [str(lang).lower() for lang in getattr(voice, 'languages', [])]
        if any((tag in vid or tag in name for tag in VOICE_LANGUAGE_TAGS)):
            return getattr(voice, 'id', None)
        speaks_it = any((hint in name for hint in VOICE_LANGUAGE_HINTS)) or any((tag in lang for lang in languages for tag in VOICE_LANGUAGE_TAGS))
        if speaks_it and fallback is None:
            fallback = getattr(voice, 'id', None)
    return fallback

def _ensure_tts_engine():
    """Initialize and cache the shared TTS engine."""
    global _TTS_ENGINE, _TTS_THREAD
    if not HAS_TTS:
        return None
    with _TTS_LOCK:
        if _TTS_ENGINE is None:
            try:
                engine = pyttsx3.init()
                voice_id = _select_voice_for_language(engine)
                if voice_id:
                    engine.setProperty('voice', voice_id)
                engine.setProperty('rate', 185)
                _TTS_ENGINE = engine
            except Exception as exc:
                print(f'[TTS] Could not start the engine: {exc}')
                _TTS_ENGINE = None
        if _TTS_THREAD is None or not _TTS_THREAD.is_alive():
            _TTS_THREAD = threading.Thread(target=_tts_worker, daemon=True)
            _TTS_THREAD.start()
    return _TTS_ENGINE

def _play_wave_file(path: str, output_device: Optional[int]) -> bool:
    """Play a WAV file via PyAudio on the selected output device."""
    if not HAS_PYAUDIO:
        return False
    try:
        with wave.open(path, 'rb') as wf:
            pa = pyaudio.PyAudio()
            try:
                stream_params = dict(format=pa.get_format_from_width(wf.getsampwidth()), channels=wf.getnchannels(), rate=wf.getframerate(), output=True)
                if output_device is not None and output_device >= 0:
                    stream_params['output_device_index'] = int(output_device)
                stream = pa.open(**stream_params)
                try:
                    data = wf.readframes(1024)
                    while data:
                        stream.write(data)
                        data = wf.readframes(1024)
                finally:
                    stream.stop_stream()
                    stream.close()
            finally:
                pa.terminate()
    except Exception as exc:
        print(f'[TTS] Could not play the audio: {exc}')
        return False
    return True

def _tts_worker():
    """Background worker to serialize speech requests and reduce latency."""
    while True:
        text = _TTS_QUEUE.get()
        if text is None:
            return
        engine = _ensure_tts_engine()
        if not engine:
            _TTS_QUEUE.task_done()
            continue
        try:
            if TTS_OUTPUT_DEVICE_INDEX is not None and HAS_PYAUDIO:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp:
                    temp_path = tmp.name
                try:
                    engine.save_to_file(text, temp_path)
                    engine.runAndWait()
                    if not _play_wave_file(temp_path, TTS_OUTPUT_DEVICE_INDEX):
                        engine.say(text)
                        engine.runAndWait()
                finally:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
            else:
                engine.say(text)
                engine.runAndWait()
        except Exception as exc:
            print(f'[TTS] Playback failed: {exc}')
        finally:
            _TTS_QUEUE.task_done()

def speak_text(text: str):
    """Speak text using TTS with cooldown to prevent spam and reduce startup cost."""
    if not HAS_TTS or not text:
        return
    now = time.time()
    if text == TTS_STATE['last_text'] and now - TTS_STATE['last_time'] < TTS_STATE['cooldown_s']:
        return
    TTS_STATE['last_text'] = text
    TTS_STATE['last_time'] = now
    if not _ensure_tts_engine():
        return
    try:
        _TTS_QUEUE.put_nowait(text)
    except Exception:
        pass

class WinMMJoystickBackend:
    """Poll joystick buttons through WinMM to avoid SDL grabbing wheel FFB."""

    def __init__(self):
        self._last_buttons: Dict[int, int] = {}

    @staticmethod
    def _normalize_name(name: str) -> str:
        return ''.join((ch.lower() for ch in str(name) if ch.isalnum()))

    def _matches_allowed_name(self, name: str, allowed_names: Iterable[str]) -> bool:
        normalized = self._normalize_name(name)
        for allowed in allowed_names:
            allowed_norm = self._normalize_name(allowed)
            if not allowed_norm:
                continue
            if normalized == allowed_norm:
                return True
            if allowed_norm in normalized or normalized in allowed_norm:
                return True
        return False

    def list_devices(self) -> List[Tuple[int, str]]:
        """Return connected joystick devices known to the WinMM API."""
        if not WINMM_AVAILABLE or joyGetNumDevs is None or joyGetDevCapsW is None:
            return []
        devices: List[Tuple[int, str]] = []
        try:
            total = int(joyGetNumDevs())
        except Exception:
            return []
        for device_id in range(total):
            try:
                caps = JOYCAPSW()
                result = joyGetDevCapsW(device_id, ctypes.byref(caps), ctypes.sizeof(caps))
                if result != JOYERR_NOERROR:
                    continue
                name = str(caps.szPname).strip() or f'Joystick {device_id}'
                if _is_virtual_joystick_name(name):
                    continue
                devices.append((device_id, name))
            except Exception:
                continue
        return devices

    def _read_buttons(self, device_id: int) -> Optional[int]:
        """Return the raw button bitmask for a device."""
        if joyGetPosEx is None:
            return None
        try:
            info = JOYINFOEX()
            info.dwSize = ctypes.sizeof(JOYINFOEX)
            info.dwFlags = JOY_RETURNBUTTONS
            result = joyGetPosEx(device_id, ctypes.byref(info))
            if result != JOYERR_NOERROR:
                return None
            return int(info.dwButtons)
        except Exception:
            return None

    def connect_allowed_devices(self, allowed_names: Iterable[str]) -> List[Dict[str, Any]]:
        """Build the active device list from allowed names."""
        allowed = list(allowed_names)
        self._last_buttons.clear()
        devices: List[Dict[str, Any]] = []
        for device_id, name in self.list_devices():
            if allowed and (not self._matches_allowed_name(name, allowed)):
                continue
            initial_buttons = self._read_buttons(device_id)
            self._last_buttons[device_id] = int(initial_buttons or 0)
            devices.append({'id': device_id, 'name': name})
        return devices

    def poll_button_events(self, devices: Iterable[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
        """Return the JOY codes pressed and released since the last poll.

        Both edges come from a single read because consuming the button mask
        twice would swallow whichever edge polled second.
        """
        pressed: List[str] = []
        released: List[str] = []
        for device in devices:
            device_id = int(device.get('id', -1))
            if device_id < 0:
                continue
            buttons = self._read_buttons(device_id)
            if buttons is None:
                continue
            previous = self._last_buttons.get(device_id, buttons)
            for changed, sink in ((buttons & ~previous, pressed), (previous & ~buttons, released)):
                while changed:
                    lowest_bit = changed & -changed
                    button_idx = lowest_bit.bit_length() - 1
                    sink.append(f'JOY:{device_id}:{button_idx}')
                    changed &= changed - 1
            self._last_buttons[device_id] = buttons
        return (pressed, released)

    def poll_button_down_events(self, devices: Iterable[Dict[str, Any]]) -> List[str]:
        """Return one-shot JOY codes for newly pressed buttons."""
        return self.poll_button_events(devices)[0]

class InputManager:
    """
    Manages input from keyboard and joystick devices.
    
    Supports safe mode (keyboard only) and selective device enabling.
    """

    def __init__(self):
        self.joysticks: List[Any] = []
        self.listeners: Dict[str, Callable] = {}
        self.release_listeners: Dict[str, Callable] = {}
        self.active: bool = False
        self.allowed_devices: List[str] = []
        self.safe_mode: bool = False
        self._connected_allowed_key: Optional[Tuple[str, ...]] = None
        self._last_device_connect_attempt = 0.0
        self._input_thread: Optional[threading.Thread] = None
        self._poll_pause_lock = threading.Lock()
        self._polling_paused_until = 0.0
        self._input_watchdog = Watchdog('InputManager', interval_s=2.5, timeout_s=8.0, on_trip=self._restart_input_loop)
        self.preserve_game_ffb: bool = os.getenv('DOMINANTCONTROL_PRESERVE_FFB', '1') == '1'
        preferred_backend = os.getenv('DOMINANTCONTROL_JOYSTICK_BACKEND', 'winmm' if self.preserve_game_ffb else 'pygame').strip().lower()
        self._use_winmm_backend = preferred_backend == 'winmm' and WINMM_AVAILABLE
        self._winmm_backend = WinMMJoystickBackend() if self._use_winmm_backend else None
        if self._use_winmm_backend:
            print("[Input] Using WinMM to preserve the wheel's force feedback.")
            self._start_input_loop()
            return
        if HAS_PYGAME:
            try:
                self._apply_pygame_input_hints()
                pygame.init()
                pygame.joystick.init()
                if self.preserve_game_ffb:
                    print("[Input] Using pygame with SDL settings that are safe for FFB.")
                self._start_input_loop()
            except Exception as e:
                print(f'[Input] Error starting pygame: {e}')

    def _apply_pygame_input_hints(self) -> None:
        """Apply SDL environment hints before pygame joystick init."""
        if not self.preserve_game_ffb:
            return
        hint_pairs = {'SDL_JOYSTICK_HIDAPI': '0', 'SDL_XINPUT_ENABLED': '0', 'SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS': '1', 'SDL_JOYSTICK_RAWINPUT_CORRELATE_XINPUT': '0', 'SDL_HINT_JOYSTICK_HIDAPI': '0', 'SDL_HINT_XINPUT_ENABLED': '0', 'SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS': '1'}
        for name, value in hint_pairs.items():
            os.environ.setdefault(name, value)

    def _ensure_pygame_ready(self) -> bool:
        """Initialize pygame joystick support when that backend is active."""
        if self._use_winmm_backend or not HAS_PYGAME:
            return False
        try:
            self._apply_pygame_input_hints()
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            return True
        except Exception as exc:
            print(f'[Input] pygame support unavailable: {exc}')
            return False

    def set_safe_mode(self, enabled: bool):
        """
        Enable/disable safe mode (keyboard only).
        
        Args:
            enabled: True for keyboard only, False to enable joysticks
        """
        self.safe_mode = enabled
        if self.safe_mode:
            if not self._use_winmm_backend and HAS_PYGAME:
                try:
                    pygame.quit()
                except Exception:
                    pass
        elif self._use_winmm_backend:
            self._start_input_loop()
        elif HAS_PYGAME:
            try:
                if self._ensure_pygame_ready():
                    self._start_input_loop()
            except Exception as e:
                print(f'[Input] Error waking pygame back up: {e}')

    def get_all_devices(self) -> List[Tuple[int, str]]:
        """
        Get all available joystick devices.
        
        Returns:
            List of (device_id, device_name) tuples
        """
        if self.safe_mode:
            return []
        if self._use_winmm_backend and self._winmm_backend:
            return self._winmm_backend.list_devices()
        if not HAS_PYGAME:
            return []
        try:
            if not self._ensure_pygame_ready():
                return []
            devices = []
            count = pygame.joystick.get_count()
            for i in range(count):
                try:
                    j = pygame.joystick.Joystick(i)
                    if not j.get_init():
                        j.init()
                    name = j.get_name()
                    if _is_virtual_joystick_name(name):
                        continue
                    devices.append((i, name))
                except Exception:
                    devices.append((i, f'Device {i} (error)'))
            return devices
        except Exception as e:
            print(f'[Input] Error listing the devices: {e}')
            return []

    def connect_allowed_devices(self, allowed_names: List[str], *, force: bool=False):
        """
        Connect only devices in the allowed list.
        
        Args:
            allowed_names: List of device names to allow
        """
        self.allowed_devices = list(allowed_names)
        allowed_key = tuple(sorted((str(name) for name in self.allowed_devices)))
        now = time.time()
        if not force and allowed_key == self._connected_allowed_key and (self.joysticks or now - self._last_device_connect_attempt < 3.0):
            return
        self._connected_allowed_key = allowed_key
        self._last_device_connect_attempt = now
        self.joysticks.clear()
        if self.safe_mode:
            self._connected_allowed_key = None
            return
        if self._use_winmm_backend and self._winmm_backend:
            self.joysticks = self._winmm_backend.connect_allowed_devices(self.allowed_devices)
            if self.joysticks:
                counts: Dict[str, int] = {}
                for device in self.joysticks:
                    name = str(device.get('name', "Unknown"))
                    counts[name] = counts.get(name, 0) + 1
                summary = ', '.join((f'{name} x{count}' if count > 1 else name for name, count in counts.items()))
                print(f'[Input] Connected through WinMM: {summary}')
            return
        if not HAS_PYGAME:
            return
        try:
            if not self._ensure_pygame_ready():
                return
            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                name = j.get_name()
                if _is_virtual_joystick_name(name):
                    continue
                if self.allowed_devices and name not in self.allowed_devices:
                    continue
                try:
                    j.init()
                    self.joysticks.append(j)
                    print(f'[Input] Connected: {j.get_name()}')
                except Exception:
                    pass
        except Exception:
            pass

    def _start_input_loop(self, force: bool=False):
        """Start or restart the input loop thread with watchdog protection."""
        if not self._use_winmm_backend and (not HAS_PYGAME):
            return
        if not force and self._input_thread and self._input_thread.is_alive():
            return
        self._input_thread = threading.Thread(target=self._input_loop_with_watchdog, daemon=True, name='InputLoop')
        self._input_thread.start()
        self._input_watchdog.start()

    def _dispatch_from(self, table: Dict[str, Callable], code: str) -> bool:
        """Dispatch a joystick code, with a single-device fallback for old binds."""
        callback = table.get(code)
        if callback:
            _CALLBACK_DISPATCHER.submit(callback)
            return True
        button_idx = _parse_joy_button_code(code)
        if button_idx is None:
            return False
        if len(self.joysticks) == 1:
            callback = table.get(f'JOYANY:{button_idx}')
            if callback:
                _CALLBACK_DISPATCHER.submit(callback)
                return True
        return False

    def _dispatch_joystick_code(self, code: str) -> bool:
        """Dispatch a joystick button press."""
        return self._dispatch_from(self.listeners, code)

    def _dispatch_joystick_release(self, code: str) -> bool:
        """Dispatch a joystick button release to a held binding, if any."""
        if not self.release_listeners:
            return False
        return self._dispatch_from(self.release_listeners, code)

    def pause_joystick_polling(self, duration_s: float) -> None:
        """Pause joystick event polling until the requested deadline."""
        if duration_s <= 0:
            return
        deadline = time.time() + float(duration_s)
        with self._poll_pause_lock:
            if deadline > self._polling_paused_until:
                self._polling_paused_until = deadline

    def _joystick_polling_paused(self) -> bool:
        """Return True while joystick polling is temporarily suspended."""
        with self._poll_pause_lock:
            return time.time() < self._polling_paused_until

    def _restart_input_loop(self):
        """Attempt to restart the input loop if the watchdog detects a stall."""
        if self.safe_mode or (not self._use_winmm_backend and (not HAS_PYGAME)):
            return
        if self._input_thread and self._input_thread.is_alive():
            return
        print("[Input][Supervisor] Cycle stopped answering; restarting...")
        self._start_input_loop(force=True)

    def _input_loop_with_watchdog(self):
        """Background loop to capture joystick events and feed watchdog."""
        while True:
            try:
                if not self.safe_mode and self.active and self.listeners and (not self._joystick_polling_paused()):
                    if self._use_winmm_backend and self._winmm_backend:
                        pressed, released = self._winmm_backend.poll_button_events(self.joysticks)
                        for code in pressed:
                            self._dispatch_joystick_code(code)
                        for code in released:
                            self._dispatch_joystick_release(code)
                    elif HAS_PYGAME and pygame.get_init():
                        pygame.event.pump()
                        events = pygame.event.get()
                        for event in events:
                            if event.type == pygame.JOYBUTTONDOWN:
                                code = f'JOY:{event.joy}:{event.button}'
                                self._dispatch_joystick_code(code)
                            elif event.type == pygame.JOYBUTTONUP:
                                code = f'JOY:{event.joy}:{event.button}'
                                self._dispatch_joystick_release(code)
            except Exception:
                pass
            finally:
                self._input_watchdog.beat()
            if not self.active or not self.listeners:
                time.sleep(0.05)
            elif not self.joysticks:
                time.sleep(0.03)
            else:
                time.sleep(0.01)

    def capture_any_input(self, timeout: float=10.0) -> Optional[str]:
        """
        Capture any keyboard or joystick input.
        
        Args:
            timeout: Maximum time to wait for input in seconds
            
        Returns:
            Input code string (KEYSCAN, KEY:name, or JOY:id:button) or None
        """
        captured_code = None
        start = time.time()

        def key_hook(e):
            nonlocal captured_code
            if e.event_type == 'down':
                if e.name in {'esc', 'escape'}:
                    captured_code = 'CANCEL'
                else:
                    captured_code = _keyboard_event_input_code(e)
                    if not captured_code and e.name:
                        captured_code = f'KEY:{e.name.upper()}'
        try:
            hook = keyboard.hook(key_hook)
        except Exception:
            hook = None
        try:
            while time.time() - start < timeout:
                if captured_code:
                    break
                if not self.safe_mode:
                    if self._use_winmm_backend and self._winmm_backend:
                        try:
                            device_list = self.joysticks
                            if not device_list:
                                device_list = [{'id': device_id, 'name': name} for device_id, name in self._winmm_backend.list_devices()]
                            for code in self._winmm_backend.poll_button_down_events(device_list):
                                captured_code = code
                                break
                        except Exception:
                            pass
                    elif HAS_PYGAME and pygame.get_init():
                        try:
                            pygame.event.pump()
                            for joy in self.joysticks:
                                try:
                                    for b_idx in range(joy.get_numbuttons()):
                                        if joy.get_button(b_idx):
                                            captured_code = f'JOY:{joy.get_id()}:{b_idx}'
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

    def capture_game_action_binding(self, timeout: float=10.0) -> Tuple[Optional[Any], Optional[str]]:
        """
        Capture a keyboard binding for increase/decrease output.

        Game action output is keyboard-only. Joystick buttons remain available
        as hotkeys elsewhere in the app, but the actual game-facing
        increase/decrease key must be a keyboard key the game already knows.
        """
        while keyboard.is_pressed('enter'):
            time.sleep(0.05)
        result: Dict[str, Any] = {'binding': None, 'label': None}
        start = time.time()

        def key_hook(e):
            if result['binding'] is not None or e.event_type != 'down':
                return
            if e.name in {'esc', 'escape'}:
                result['binding'] = 'CANCEL'
                result['label'] = 'CANCEL'
            elif e.scan_code:
                parsed_scan = _keyboard_event_scan_code(e)
                if not parsed_scan:
                    return
                scan_code, extended = parsed_scan
                payload = _format_scan_code_payload(scan_code, extended)
                prefix = SHIFTED_SCANCODE_PREFIX if _should_capture_shifted_scancode(e.name) else SCANCODE_BINDING_PREFIX
                result['binding'] = f'{prefix}{payload}'
                result['label'] = _keyboard_event_label(e, scan_code, extended)
        try:
            hook = keyboard.hook(key_hook)
        except Exception:
            hook = None
        try:
            while time.time() - start < timeout:
                if result['binding'] is not None:
                    break
                time.sleep(0.02)
        finally:
            if hook:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass
        if result['binding'] == 'CANCEL':
            return (None, 'CANCEL')
        return (result['binding'], result['label'])

    def capture_keyboard_scancode(self, timeout: float=10.0) -> Tuple[Optional[int], Optional[str]]:
        """
        Capture a keyboard scan code with timeout and cancellation support.

        Returns:
            Tuple of (scan_code, key_name) or (None, None) if timeout/cancel
        """
        while keyboard.is_pressed('enter'):
            time.sleep(0.05)
        done = threading.Event()
        result: Dict[str, Optional[Any]] = {'scan': None, 'name': None}

        def on_event(e):
            if e.event_type == 'down':
                if e.name in {'esc', 'escape'}:
                    result['name'] = 'CANCEL'
                else:
                    parsed_scan = _keyboard_event_scan_code(e)
                    if parsed_scan:
                        scan_code, extended = parsed_scan
                        result['scan'] = 224 << 8 | scan_code if extended else scan_code
                        result['name'] = _keyboard_event_label(e, scan_code, extended)
                    else:
                        result['scan'] = e.scan_code
                        result['name'] = e.name
                done.set()
        hook = keyboard.hook(on_event, suppress=True)
        done.wait(timeout)
        keyboard.unhook(hook)
        if result['name'] == 'CANCEL':
            return (None, 'CANCEL')
        return (result['scan'], result['name'])
input_manager = InputManager()

class VoiceListener:
    """
    Lightweight voice trigger engine backed by Windows speech recognition.

    Uses the `speech_recognition` package with the default Windows recognizer
    (SAPI). Falls back to other recognizers if unavailable.
    """

    def __init__(self):
        self.available = VOICE_FEATURES_ENABLED and HAS_SPEECH
        self.recognizer = sr.Recognizer() if HAS_SPEECH else None
        self.callbacks: Dict[str, Callable] = {}
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self._noise_adjusted = False
        self.last_engine: Optional[str] = None
        self.engine = 'speech'
        self.vosk_model_path: str = ''
        self.vosk_model: Optional[Any] = None
        self._vosk_error: Optional[str] = None
        self.whisper_binary_path: str = ''
        self.whisper_model_path: str = ''
        self._whisper_error: Optional[str] = None
        self.device_index: Optional[int] = None
        self.ambient_duration = VOICE_TUNING_DEFAULTS['ambient_duration']
        self.initial_timeout = VOICE_TUNING_DEFAULTS['initial_timeout']
        self.continuous_timeout = VOICE_TUNING_DEFAULTS['continuous_timeout']
        self.phrase_time_limit = VOICE_TUNING_DEFAULTS['phrase_time_limit']
        self.energy_threshold: Optional[float] = VOICE_TUNING_DEFAULTS['energy_threshold']
        self.dynamic_energy = VOICE_TUNING_DEFAULTS['dynamic_energy']
        if self.recognizer:
            self._apply_recognizer_settings(self.recognizer)
        self._watchdog = Watchdog('VoiceListener', interval_s=2.0, timeout_s=7.0, on_trip=self._recover_listener)

    def set_phrases(self, phrases: Dict[str, Callable]):
        """Replace the phrase-to-callback map."""
        with self.lock:
            self.callbacks = {k.strip().lower(): v for k, v in phrases.items() if k}

    def set_enabled(self, enabled: bool):
        """Start or stop the listener based on user preference."""
        if not self.available:
            self.stop()
            return
        if enabled and self.callbacks:
            self.start()
        else:
            self.stop()

    def set_device_index(self, device_index: Optional[int]):
        """Update the microphone device index and restart listener if needed."""
        self.device_index = device_index
        if self.running:
            self.stop()
            self.start()

    def start(self):
        if not self.available:
            return
        if self.running and self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop_with_watchdog, daemon=True, name='VoiceListener')
        self.thread.start()
        self._watchdog.start()

    def stop(self):
        self.running = False
        self._watchdog.stop()

    def set_engine(self, engine: str, model_path: str='', whisper_binary: str='', whisper_model: str=''):
        """Configure which recognition engine to use."""
        engine = engine if engine in {'speech', 'vosk', 'whisper.cpp'} else 'speech'
        self.engine = engine
        self.vosk_model_path = model_path
        if engine == 'vosk':
            self._init_vosk_model(model_path)
        else:
            self.vosk_model = None
            self._vosk_error = None
        if engine == 'whisper.cpp':
            self.whisper_binary_path = whisper_binary
            self.whisper_model_path = whisper_model
            self._ensure_whisper_ready()
        else:
            self._whisper_error = None

    def _apply_recognizer_settings(self, recognizer):
        """Apply tuning values to a speech_recognition.Recognizer."""
        try:
            recognizer.dynamic_energy_threshold = self.dynamic_energy
            if self.energy_threshold is not None:
                recognizer.energy_threshold = self.energy_threshold
        except Exception:
            pass

    def update_tuning(self, tuning: Dict[str, Any]):
        """Update microphone/recognition tuning parameters."""

        def _safe_float(value: Any, default: float) -> float:
            try:
                return float(value)
            except Exception:
                return default
        self.ambient_duration = max(0.0, _safe_float(tuning.get('ambient_duration'), self.ambient_duration))
        self.initial_timeout = _safe_float(tuning.get('initial_timeout'), self.initial_timeout)
        self.continuous_timeout = _safe_float(tuning.get('continuous_timeout'), self.continuous_timeout)
        self.phrase_time_limit = _safe_float(tuning.get('phrase_time_limit'), self.phrase_time_limit)
        threshold_val = tuning.get('energy_threshold')
        try:
            self.energy_threshold = float(threshold_val) if threshold_val not in {None, ''} else None
        except Exception:
            self.energy_threshold = None
        self.dynamic_energy = bool(tuning.get('dynamic_energy', self.dynamic_energy))
        self._noise_adjusted = False
        if self.recognizer:
            self._apply_recognizer_settings(self.recognizer)
        elif HAS_SPEECH and sr is not None:
            self.recognizer = sr.Recognizer()
            self._apply_recognizer_settings(self.recognizer)

    def _recover_listener(self):
        """Restart the listener thread if it stops unexpectedly."""
        if not self.running:
            return
        if self.thread and self.thread.is_alive():
            return
        print("[Voice][Supervisor] The listener stopped answering; restarting...")
        self.start()

    def _listen_loop_with_watchdog(self):
        """Wrap the listener loop with heartbeat updates."""
        self._watchdog.beat()
        try:
            self._listen_loop()
        finally:
            self._watchdog.beat()

    def _init_vosk_model(self, model_path: str):
        """Load the Vosk model from disk if available."""
        if not HAS_VOSK or not model_path:
            self.vosk_model = None
            return
        if self.vosk_model_path == model_path and self.vosk_model is not None:
            return
        try:
            self.vosk_model = vosk.Model(model_path)
            self._vosk_error = None
        except Exception as exc:
            self.vosk_model = None
            self._vosk_error = str(exc)
            print(f'[Voice][Vosk] Could not load the model: {exc}')

    def _ensure_whisper_ready(self) -> bool:
        """Validate whisper.cpp prerequisites."""
        if not self.whisper_binary_path:
            self._whisper_error = "Select the whisper.cpp executable"
            return False
        if not os.path.exists(self.whisper_binary_path):
            self._whisper_error = "whisper.cpp executable not found"
            return False
        if not os.access(self.whisper_binary_path, os.X_OK):
            self._whisper_error = "whisper.cpp is not executable"
            return False
        if not self.whisper_model_path:
            self._whisper_error = "Select a ggml/gguf model (.bin/.gguf)"
            return False
        if not os.path.exists(self.whisper_model_path):
            self._whisper_error = "model file not found"
            return False
        self._whisper_error = None
        return True

    def _recognize_text(self, audio, recognizer=None) -> Optional[str]:
        """Try multiple engines to convert audio to text."""
        rec = recognizer or self.recognizer
        if not rec:
            return None
        if self.engine == 'whisper.cpp':
            text = self._transcribe_with_whisper(audio)
            if text:
                self.last_engine = 'whisper.cpp'
                return text
        if self.engine == 'vosk' and HAS_VOSK and self.vosk_model:
            try:
                raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                vosk_rec = vosk.KaldiRecognizer(self.vosk_model, 16000)
                if vosk_rec.AcceptWaveform(raw):
                    result_json = vosk_rec.Result()
                else:
                    result_json = vosk_rec.FinalResult()
                parsed = json.loads(result_json or '{}')
                text = (parsed.get('text') or '').strip()
                if text:
                    self.last_engine = 'vosk'
                    return text
            except Exception as exc:
                print(f'[Voice][Vosk] Recognition error: {exc}')
        engines: List[Tuple[str, Callable]] = []
        if hasattr(rec, 'recognize_sapi'):
            engines.append(('sapi', rec.recognize_sapi))
        if hasattr(rec, 'recognize_sphinx'):
            engines.append(('sphinx', rec.recognize_sphinx))
        if hasattr(rec, 'recognize_google'):
            engines.append(('google', rec.recognize_google))
        for name, engine in engines:
            try:
                result = engine(audio)
                self.last_engine = name
                return result
            except Exception:
                continue
        return None

    def _transcribe_with_whisper(self, audio) -> Optional[str]:
        """Run whisper.cpp as a subprocess over the captured audio."""
        if not self._ensure_whisper_ready():
            return None
        tmp_path = None
        try:
            wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp:
                tmp.write(wav_data)
                tmp_path = tmp.name
            cmd = [self.whisper_binary_path, '--model', self.whisper_model_path, '--file', tmp_path, '--output-json']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if result.returncode != 0:
                self._whisper_error = result.stderr.strip() or "whisper.cpp failed"
                return None
            text: Optional[str] = None
            for line in reversed(result.stdout.splitlines()):
                cleaned = line.strip()
                if not cleaned:
                    continue
                if cleaned.startswith('{'):
                    try:
                        parsed = json.loads(cleaned)
                        text = (parsed.get('text') or '').strip()
                        if text:
                            break
                    except Exception:
                        continue
                if 'text:' in cleaned.lower():
                    text = cleaned.split(':', 1)[-1].strip()
                    if text:
                        break
            if text:
                self._whisper_error = None
                return text
            self._whisper_error = "whisper.cpp returned no text"
            return None
        except Exception as exc:
            self._whisper_error = str(exc)
            print(f'[Voice][Whisper] Recognition error: {exc}')
            return None
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _listen_loop(self):
        if not self.recognizer:
            return
        try:
            with sr.Microphone(device_index=self.device_index) as source:
                self._apply_recognizer_settings(self.recognizer)
                if not self._noise_adjusted:
                    try:
                        self.recognizer.adjust_for_ambient_noise(source, duration=self.ambient_duration)
                        self._noise_adjusted = True
                    except Exception:
                        pass
                listen_timeout = self.initial_timeout if self.initial_timeout > 0 else None
                phrase_limit = self.phrase_time_limit if self.phrase_time_limit > 0 else None
                while self.running:
                    self._watchdog.beat()
                    try:
                        audio = self.recognizer.listen(source, timeout=listen_timeout, phrase_time_limit=phrase_limit)
                        listen_timeout = self.continuous_timeout if self.continuous_timeout > 0 else listen_timeout
                    except getattr(sr, 'WaitTimeoutError', Exception):
                        continue
                    except Exception:
                        continue
                    text = self._recognize_text(audio)
                    if not text:
                        continue
                    phrase = text.strip().lower()
                    if not phrase:
                        continue
                    with self.lock:
                        cb = self.callbacks.get(phrase)
                    if cb:
                        _CALLBACK_DISPATCHER.submit(cb)
        except Exception as exc:
            print(f'[Voice] Listening interrupted: {exc}')

    def capture_once(self, timeout: Optional[float]=None, phrase_time_limit: Optional[float]=None) -> Tuple[Optional[str], Optional[str]]:
        """Capture a single voice input for testing purposes."""
        if not self.available or sr is None:
            return (None, "Speech recognition unavailable.")
        recognizer = sr.Recognizer()
        self._apply_recognizer_settings(recognizer)
        try:
            with sr.Microphone(device_index=self.device_index) as source:
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=self.ambient_duration)
                except Exception:
                    pass
                audio = recognizer.listen(source, timeout=timeout if timeout is not None else self.initial_timeout if self.initial_timeout > 0 else None, phrase_time_limit=phrase_time_limit if phrase_time_limit is not None else self.phrase_time_limit if self.phrase_time_limit > 0 else None)
        except Exception as exc:
            return (None, str(exc))
        text = self._recognize_text(audio, recognizer=recognizer)
        if text is None:
            return ('', None)
        return (text.strip().lower(), None)
voice_listener = VoiceListener()
__all__ = [name for name in globals() if not name.startswith('__')]
