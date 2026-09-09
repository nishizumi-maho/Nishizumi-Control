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
import urllib.error
import urllib.request
import webbrowser
from array import array
import tempfile
import wave
from collections import deque
from typing import Dict, List, Tuple, Optional, Any, Callable, Iterable, Deque, Iterator
from tkinter import font as tkfont

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
                print(f'[Watchdog:{self.name}] Recovery failed: {exc}')
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
IRSDK_VAR_TYPE_NUMERIC = {2, 3, 4, 5}
warnings.filterwarnings('ignore', message='pkg_resources is deprecated as an API.*')
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
        print("Warning: 'pygame' not installed. Joystick support disabled.")
else:
    HAS_PYGAME = False
sr = None
APP_NAME = 'DominantControl'
APP_VERSION = '12'
SURFACE_PRESET_KEYS = ('DRY', 'WET')
DEFAULT_SURFACE_PRESET = 'DRY'
WIPER_TOGGLE_VARS = {'dcToggleWindShieldWipers', 'dcToggleWindshieldWipers', 'dcToogleWindShieldWipers'}
HYBRID_BOOST_HOLD_VAR = 'dcHysBoostHold'
HYBRID_REGEN_HOLD_VAR = 'dcHysRegenHold'
HYBRID_HOLD_VARS = {HYBRID_BOOST_HOLD_VAR, HYBRID_REGEN_HOLD_VAR}
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
CONTROL_NAME_ALIASES: Dict[str, Tuple[str, ...]] = {'dcABS': ('ABS',), 'dcABias': ('Aero Bias', 'Aero', 'A'), 'dcAntiRollFront': ('Anti Roll Front', 'Anti Roll F', 'ARB Front', 'ARB F'), 'dcAntiRollRear': ('Anti Roll Rear', 'Anti Roll R', 'ARB Rear', 'ARB R'), 'dcBrakeBias': ('Brake Bias', 'Brk Bias', 'BB'), 'dcBrakeMigration': ('Brake Migration', 'Brake Mig', 'Brk Mig', 'BM'), 'dcDashPage': ('Dash Page', 'Dash', 'DP'), 'dcDiffEntry': ('Diff Entry', 'Diff In', 'D In'), 'dcDiffExit': ('Diff Exit', 'Diff Out', 'D Out'), 'dcDiffMiddle': ('Diff Middle', 'Diff Mid', 'D Mid'), 'dcDiffPreload': ('Diff Preload', 'Preload', 'D Pre'), 'dcFuelMixture': ('Fuel Mixture', 'Fuel Mix', 'Fuel'), 'dcHeadlightFlash': ('Headlight Flash', 'Flash', 'Light'), 'dcHysBoostHold': ('Hybrid Boost Hold', 'Boost Hold', 'Boost'), 'dcHysRegenHold': ('Hybrid Regen Hold', 'Regen Hold', 'Regen'), 'dcLowFuelAccept': ('Low Fuel Accept', 'Low Fuel', 'Fuel Ack'), 'dcMGUKDeployFixed': ('MGU-K Deploy', 'Deploy', 'MGU Dep'), 'dcMGUKRegenGain': ('MGU-K Regen', 'Regen Gain', 'MGU Reg'), 'dcPitSpeedLimiterToggle': ('Pit Speed Limiter', 'Pit Limiter', 'Pit Lim'), 'dcPushToPass': ('Push To Pass', 'P2P', 'P2P'), 'dcStarter': ('Starter', 'Start'), 'dcToggleWindShieldWipers': ('Wiper Toggle', 'Wiper Tgl', 'Wip Tgl'), 'dcToggleWindshieldWipers': ('Wiper Toggle', 'Wiper Tgl', 'Wip Tgl'), 'dcToogleWindShieldWipers': ('Wiper Toggle', 'Wiper Tgl', 'Wip Tgl'), 'dcTractionControl': ('Traction Control', 'TC', 'TC'), 'dcTractionControl2': ('Traction Control 2', 'TC 2', 'TC2'), 'dcTractionControlCut': ('Traction Cut', 'TC Cut', 'TCC'), 'dcTriggerWindShieldWipers': ('Wiper Trigger', 'Wiper Trig', 'Wip Trg'), 'dcTriggerWindshieldWipers': ('Wiper Trigger', 'Wiper Trig', 'Wip Trg'), 'dcWeightJackerRight': ('Weight Jacker Right', 'WJ Right', 'WJ R')}
CONTROL_TOKEN_RE = re.compile('[A-Z]+(?=[A-Z][a-z]|\\d|$)|[A-Z]?[a-z]+|\\d+')
CONTROL_WORD_LABELS = {'Hys': 'Hybrid', 'MGUK': 'MGU-K', 'Toogle': 'Toggle', 'WindShield': 'Windshield'}
CONTROL_WORD_ABBREVIATIONS = {'Anti': 'Anti', 'Roll': 'Roll', 'Front': 'F', 'Rear': 'R', 'Brake': 'Brk', 'Bias': 'Bias', 'Migration': 'Mig', 'Traction': 'TC', 'Control': 'Ctrl', 'Cut': 'Cut', 'Fuel': 'Fuel', 'Mixture': 'Mix', 'Pit': 'Pit', 'Speed': 'Spd', 'Limiter': 'Lim', 'Toggle': 'Tgl', 'Trigger': 'Trig', 'Windshield': 'WS', 'Wipers': 'Wip', 'Headlight': 'Light', 'Flash': 'Flash', 'Starter': 'Start', 'Hybrid': 'Hyb', 'Boost': 'Boost', 'Regen': 'Regen', 'Hold': 'Hold', 'Deploy': 'Dep', 'Fixed': 'Fix', 'Gain': 'Gain', 'Weight': 'W', 'Jacker': 'Jacker', 'Right': 'R', 'Entry': 'In', 'Exit': 'Out', 'Middle': 'Mid', 'Preload': 'Pre', 'Push': 'Push', 'Pass': 'Pass'}
PIT_COMMAND_CLEAR_TIRES = 'clear_tires'
PIT_COMMAND_CLEAR_WS = 'clear_ws'
APP_FOLDER = 'DominantControl'
BASE_PATH = os.getenv('APPDATA') or os.path.expanduser('~')
CONFIG_FOLDER = os.path.join(BASE_PATH, APP_FOLDER, 'configs')
os.makedirs(CONFIG_FOLDER, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_FOLDER, 'config_v3.json')
PENDING_SCAN_FILE = os.path.join(CONFIG_FOLDER, 'pending_scan.flag')
ICON_CANDIDATES = ['DominantControl.ico', 'DominantControl.png', '526409012-994813f4-dff8-4789-8ba9-3b886bb1e794.png', 'app.ico', 'app.png']
STARTUP_FOLDER = os.path.join(os.getenv('APPDATA') or os.path.expanduser('~'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
STARTUP_ENTRY_NAME = f'{APP_NAME}.bat'
GITHUB_RELEASE_OWNER = 'nishizumi-maho'
GITHUB_RELEASE_REPO = 'Nishizumi-Control'
GITHUB_RELEASES_API_LATEST = f'https://api.github.com/repos/{GITHUB_RELEASE_OWNER}/{GITHUB_RELEASE_REPO}/releases/latest'
GITHUB_RELEASES_PAGE_LATEST = f'https://github.com/{GITHUB_RELEASE_OWNER}/{GITHUB_RELEASE_REPO}/releases/latest'
GITHUB_API_VERSION = '2022-11-28'
GITHUB_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
WINDOWS_APP_USER_MODEL_ID = f'NishizumiControl.{APP_NAME}.v{APP_VERSION}'

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
        if clean and clean.lower() not in {'to', 'and'}:
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
        print(f'[ICON] Failed to set Windows AppUserModelID: {exc}')

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
            print(f'[ICON] Failed to load {icon_path}: {exc}')
    if applied:
        return

def _widget_contains(container: tk.Widget, widget: Optional[tk.Widget]) -> bool:
    """Return True when widget is container or one of its descendants."""
    while widget is not None:
        if widget == container:
            return True
        widget = getattr(widget, 'master', None)
    return False

def bind_mousewheel_scroll(container: tk.Widget, canvas: tk.Canvas, *, units_per_step: int=3) -> None:
    """Route mouse-wheel events to a canvas while the pointer is inside container."""

    def _wheel_units(event) -> int:
        event_num = getattr(event, 'num', None)
        if event_num == 4:
            return -units_per_step
        if event_num == 5:
            return units_per_step
        delta = int(getattr(event, 'delta', 0) or 0)
        if delta == 0:
            return 0
        steps = max(1, abs(delta) // 120)
        return -steps * units_per_step if delta > 0 else steps * units_per_step

    def _on_mousewheel(event):
        try:
            if not container.winfo_exists() or not canvas.winfo_exists():
                return None
            pointed_widget = container.winfo_containing(event.x_root, event.y_root)
            if not _widget_contains(container, pointed_widget):
                return None
            amount = _wheel_units(event)
            if amount == 0:
                return 'break'
            first, last = canvas.yview()
            if amount < 0 and first <= 0.0:
                return 'break'
            if amount > 0 and last >= 1.0:
                return 'break'
            canvas.yview_scroll(amount, 'units')
            return 'break'
        except Exception:
            return None
    handlers = getattr(container, '_mousewheel_scroll_handlers', [])
    handlers.append(_on_mousewheel)
    container._mousewheel_scroll_handlers = handlers
    container.bind_all('<MouseWheel>', _on_mousewheel, add='+')
    container.bind_all('<Button-4>', _on_mousewheel, add='+')
    container.bind_all('<Button-5>', _on_mousewheel, add='+')

def _parse_version_tuple(version: str) -> Tuple[int, ...]:
    """Convert a version string to a comparable tuple of integers."""
    cleaned = str(version or '').strip().lstrip('vV')
    digits = re.findall(r'\d+', cleaned)
    return tuple(int(part) for part in digits) if digits else (0,)
DEFAULT_OVERLAY_FEEDBACK = {'abs_hold_s': 0.35, 'tc_hold_s': 0.35, 'wheelspin_slip': 0.18, 'wheelspin_hold_s': 0.25, 'lockup_slip': 0.2, 'lockup_hold_s': 0.25, 'cooldown_s': 6.0}
BOT_PRESS_MS = 0
BOT_INTERVAL_MS = 0
LEGACY_BOT_PRESS_MS = 1
LEGACY_BOT_INTERVAL_MS = 1
GLOBAL_TIMING = {'profile': 'bot_safe', 'press_min_ms': 60, 'press_max_ms': 80, 'interval_min_ms': 60, 'interval_max_ms': 90, 'random_enabled': False, 'random_range_ms': 10, 'boundary_press_ms': 6, 'boundary_interval_ms': 6}
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write('{}')

def restart_program():
    """Restart the application by closing and relaunching the process."""
    args = _build_launch_command()
    try:
        subprocess.Popen(args, cwd=os.getcwd(), env=os.environ.copy())
    except Exception as exc:
        print(f'[Restart] Failed to spawn new process: {exc}')
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
        print(f'[PendingScan] Failed to persist marker: {exc}')

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
        print(f'[PendingScan] Failed to read marker: {exc}')
    finally:
        try:
            os.remove(PENDING_SCAN_FILE)
        except Exception as exc:
            print(f'[PendingScan] Failed to clear marker: {exc}')
    return (True, silent)
IS_WINDOWS = os.name == 'nt' and hasattr(ctypes, 'windll')
WINMM_AVAILABLE = IS_WINDOWS and hasattr(ctypes.windll, 'winmm')
if IS_WINDOWS:
    SendInput = ctypes.windll.user32.SendInput
else:
    SendInput = None
    print('Warning: Windows SendInput APIs unavailable; input injection disabled.')
KEYEVENTF_EXTENDEDKEY = 1
KEYEVENTF_KEYUP = 2
KEYEVENTF_SCANCODE = 8
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

def _scan_code_flags(scan_code: int, key_up: bool=False) -> Tuple[int, int]:
    scan_code = int(scan_code)
    extended = False
    if scan_code > 255:
        prefix = scan_code >> 8 & 255
        extended = prefix == 224
        scan_code &= 255
    else:
        extended = scan_code in EXTENDED_SCAN_CODES
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= KEYEVENTF_KEYUP
    return (scan_code, flags)

def press_key(scan_code: int):
    """
    Press a key using its scan code.

    Args:
        scan_code: The keyboard scan code to press
    """
    if SendInput is None:
        raise OSError('SendInput APIs are only available on Windows platforms.')
    scan_code, flags = _scan_code_flags(scan_code)
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

def release_key(scan_code: int):
    """
    Release a key using its scan code.

    Args:
        scan_code: The keyboard scan code to release
    """
    if SendInput is None:
        raise OSError('SendInput APIs are only available on Windows platforms.')
    scan_code, flags = _scan_code_flags(scan_code, key_up=True)
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))
LEFT_SHIFT_SCAN_CODE = 42
SHIFTED_SCANCODE_PREFIX = 'SHIFTSCAN:'
SHIFTED_KEY_NAMES = {'+', '_', '~', '|', '{', '}', '?', ':', '"', '<', '>'}
SHIFT_BASE_KEY_NAMES = {'=', '-', '`', '\\', '[', ']', ';', "'", ',', '.', '/'}

def _is_virtual_joystick_name(name: Any) -> bool:
    """Return True for joystick devices that should be treated as virtual output."""
    normalized = str(name or '').strip().lower()
    if not normalized:
        return False
    return 'vjoy' in normalized or 'vigem' in normalized or 'virtual joystick' in normalized or ('virtual gamepad' in normalized)

def _normalize_game_input_binding(value: Any) -> Any:
    """Normalize stored bindings to keyboard scan codes, shifted scan codes, or binding strings."""
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
    if upper_text.startswith(SHIFTED_SCANCODE_PREFIX):
        parts = upper_text.split(':')
        if len(parts) == 2:
            try:
                scan_code = max(1, int(parts[1]))
                return f'{SHIFTED_SCANCODE_PREFIX}{scan_code}'
            except Exception:
                return text
    try:
        return int(text)
    except Exception:
        return text

def _parse_shifted_scancode_binding(binding: Any) -> Optional[int]:
    """Parse a binding string in the format SHIFTSCAN:<scan_code>."""
    normalized = _normalize_game_input_binding(binding)
    if not isinstance(normalized, str):
        return None
    parts = normalized.upper().split(':')
    if len(parts) != 2 or parts[0] != SHIFTED_SCANCODE_PREFIX.rstrip(':'):
        return None
    try:
        return max(1, int(parts[1]))
    except Exception:
        return None

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
    if isinstance(normalized, numbers.Integral):
        press_key(int(normalized))
        return
    shifted_scan = _parse_shifted_scancode_binding(normalized)
    if shifted_scan is not None:
        press_key(LEFT_SHIFT_SCAN_CODE)
        press_key(shifted_scan)
        return
    if isinstance(normalized, str) and normalized.upper().startswith('KEY:'):
        key_name = normalized.split(':', 1)[1].strip().lower()
        if key_name:
            keyboard.press(key_name)
            return
    raise ValueError(f'Unsupported game input binding: {binding}')

def _release_game_input(binding: Any) -> None:
    """Release a configured keyboard binding."""
    normalized = _normalize_game_input_binding(binding)
    if normalized is None:
        return
    if isinstance(normalized, numbers.Integral):
        release_key(int(normalized))
        return
    shifted_scan = _parse_shifted_scancode_binding(normalized)
    if shifted_scan is not None:
        release_key(shifted_scan)
        release_key(LEFT_SHIFT_SCAN_CODE)
        return
    if isinstance(normalized, str) and normalized.upper().startswith('KEY:'):
        key_name = normalized.split(':', 1)[1].strip().lower()
        if key_name:
            keyboard.release(key_name)
            return
    raise ValueError(f'Unsupported game input binding: {binding}')

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
    allowed_profiles = {'aggressive', 'casual', 'relaxed', 'custom', 'bot', 'bot_safe'}
    if normalized.get('profile') not in allowed_profiles:
        normalized['profile'] = 'bot_safe'
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
    profile = timing_cfg.get('profile', 'bot_safe')
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
    else:
        min_value = 10
    press_ms = max(min_value, press_ms)
    interval_ms = max(min_value, interval_ms)
    if is_float and profile != 'bot':
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
        print(f'[click_pulse] Error: {e}')

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
        print(f'[_direct_pulse] Error: {e}')

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

    def poll_button_down_events(self, devices: Iterable[Dict[str, Any]]) -> List[str]:
        """Return one-shot JOY codes for newly pressed buttons."""
        events: List[str] = []
        for device in devices:
            device_id = int(device.get('id', -1))
            if device_id < 0:
                continue
            buttons = self._read_buttons(device_id)
            if buttons is None:
                continue
            previous = self._last_buttons.get(device_id, buttons)
            changed = buttons & ~previous
            while changed:
                lowest_bit = changed & -changed
                button_idx = lowest_bit.bit_length() - 1
                events.append(f'JOY:{device_id}:{button_idx}')
                changed &= changed - 1
            self._last_buttons[device_id] = buttons
        return events

class InputManager:
    """
    Manages input from keyboard and joystick devices.
    
    Supports safe mode (keyboard only) and selective device enabling.
    """

    def __init__(self):
        self.joysticks: List[Any] = []
        self.listeners: Dict[str, Callable] = {}
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
            print('[InputManager] Using WinMM joystick backend to preserve wheel FFB.')
            self._start_input_loop()
            return
        if HAS_PYGAME:
            try:
                self._apply_pygame_input_hints()
                pygame.init()
                pygame.joystick.init()
                if self.preserve_game_ffb:
                    print('[InputManager] Using pygame joystick backend with FFB-safe SDL hints.')
                self._start_input_loop()
            except Exception as e:
                print(f'[InputManager] Pygame init error: {e}')

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
            print(f'[InputManager] Pygame backend unavailable: {exc}')
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
                print(f'[InputManager] Error reactivating pygame: {e}')

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
                    devices.append((i, f'Device {i} (Error)'))
            return devices
        except Exception as e:
            print(f'[InputManager] Error getting devices: {e}')
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
                    name = str(device.get('name', 'Unknown'))
                    counts[name] = counts.get(name, 0) + 1
                summary = ', '.join((f'{name} x{count}' if count > 1 else name for name, count in counts.items()))
                print(f'[InputManager] Connected via WinMM: {summary}')
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
                    print(f'[InputManager] Connected: {j.get_name()}')
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

    def _dispatch_joystick_code(self, code: str) -> bool:
        """Dispatch a joystick code, with a single-device fallback for old binds."""
        callback = self.listeners.get(code)
        if callback:
            _CALLBACK_DISPATCHER.submit(callback)
            return True
        button_idx = _parse_joy_button_code(code)
        if button_idx is None:
            return False
        if len(self.joysticks) == 1:
            callback = self.listeners.get(f'JOYANY:{button_idx}')
            if callback:
                _CALLBACK_DISPATCHER.submit(callback)
                return True
        return False

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
        print('[InputManager][Watchdog] Input loop unresponsive, restarting...')
        self._start_input_loop(force=True)

    def _input_loop_with_watchdog(self):
        """Background loop to capture joystick events and feed watchdog."""
        while True:
            try:
                if not self.safe_mode and self.active and self.listeners and (not self._joystick_polling_paused()):
                    if self._use_winmm_backend and self._winmm_backend:
                        for code in self._winmm_backend.poll_button_down_events(self.joysticks):
                            self._dispatch_joystick_code(code)
                    elif HAS_PYGAME and pygame.get_init():
                        pygame.event.pump()
                        events = pygame.event.get()
                        for event in events:
                            if event.type == pygame.JOYBUTTONDOWN:
                                code = f'JOY:{event.joy}:{event.button}'
                                self._dispatch_joystick_code(code)
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
            Input code string (KEY:name or JOY:id:button) or None if timeout
        """
        captured_code = None
        start = time.time()

        def key_hook(e):
            nonlocal captured_code
            if e.event_type == 'down':
                if e.name in {'esc', 'escape'}:
                    captured_code = 'CANCEL'
                elif e.name:
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
                scan_code = int(e.scan_code)
                result['binding'] = f'{SHIFTED_SCANCODE_PREFIX}{scan_code}' if _should_capture_shifted_scancode(e.name) else scan_code
                result['label'] = (e.name or str(scan_code)).upper()
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

class DeviceSelector(tk.Toplevel):
    """
    Dialog for selecting which USB devices the application can use.
    """

    def __init__(self, parent, current_allowed: List[str], callback: Callable[[List[str]], None]):
        super().__init__(parent)
        self.title('Manage USB Devices')
        self.geometry('450x400')
        self.callback = callback
        tk.Label(self, text='Select which devices the application can use', font=('Arial', 10, 'bold'), pady=10).pack()
        tk.Label(self, text='Check/uncheck to allow/disallow device usage', fg='gray').pack()
        self.frame_list = tk.Frame(self)
        self.frame_list.pack(fill='both', expand=True, padx=10, pady=10)
        self.check_vars: Dict[str, tk.BooleanVar] = {}
        all_devices = input_manager.get_all_devices()
        for idx, name in all_devices:
            var = tk.BooleanVar()
            if current_allowed:
                var.set(name in current_allowed)
            else:
                var.set(False)
            chk = tk.Checkbutton(self.frame_list, text=name, variable=var, anchor='w')
            chk.pack(fill='x')
            self.check_vars[name] = var
        tk.Button(self, text='Save and Apply', command=self.save, bg='#90ee90', height=2).pack(fill='x', padx=10, pady=10)

    def save(self):
        """Save device selection and close dialog."""
        final_list = [name for name, var in self.check_vars.items() if var.get()]
        self.callback(final_list)
        self.destroy()

class OverlayWindow(tk.Toplevel):
    """
    Premium HUD overlay with modern glass-morphism design,
    real-time telemetry visualization, and smooth animations.
    """
    ICONS = {'brake': '◉', 'fuel': '◈', 'tc': '◎', 'abs': '◇', 'diff': '⬡', 'roll': '◫', 'weight': '◆', 'lap': '◐', 'speed': '▸', 'gear': '⬢', 'rpm': '◉', 'temp': '◈', 'default': '▪', 'status_ok': '●', 'status_warn': '◐', 'status_error': '○', 'status_scan': '◑'}
    THEMES = {'midnight': {'bg': '#0d1117', 'bg_secondary': '#161b22', 'fg': '#58a6ff', 'fg_secondary': '#8b949e', 'accent': '#238636', 'accent_glow': '#2ea043', 'warning': '#d29922', 'danger': '#f85149', 'success': '#3fb950', 'border': '#30363d', 'highlight': '#1f6feb'}, 'neon': {'bg': '#0a0a0f', 'bg_secondary': '#12121a', 'fg': '#00ffc8', 'fg_secondary': '#7a8899', 'accent': '#00aaff', 'accent_glow': '#00ddff', 'warning': '#ffcc00', 'danger': '#ff3366', 'success': '#00ff88', 'border': '#2a2a3a', 'highlight': '#6366f1'}, 'racing': {'bg': '#0c0c0c', 'bg_secondary': '#1a1a1a', 'fg': '#ff4444', 'fg_secondary': '#888888', 'accent': '#ff6b35', 'accent_glow': '#ff8c00', 'warning': '#ffd700', 'danger': '#dc143c', 'success': '#32cd32', 'border': '#333333', 'highlight': '#ff4500'}}

    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.wm_attributes('-topmost', True)
        self.wm_attributes('-alpha', 0.95)
        self.wm_attributes('-transparentcolor', '')
        self._pos_x = 50
        self._pos_y = 50
        apply_app_icon(self)
        self._color_cache: Dict[str, str] = {}
        self._pulse_state = 0
        self._last_status_color = None
        self.style_cfg = {'bg': '#0d1117', 'bg_secondary': '#161b22', 'fg': '#58a6ff', 'fg_secondary': '#8b949e', 'accent': '#238636', 'accent_glow': '#2ea043', 'warning': '#d29922', 'danger': '#f85149', 'success': '#3fb950', 'border': '#30363d', 'highlight': '#1f6feb', 'font_size': 11, 'opacity': 0.95, 'width': 340, 'height': 220, 'corner_radius': 12, 'show_graphs': True, 'compact_mode': False, 'theme': 'midnight', 'glow_enabled': True, 'animations_enabled': True}
        self.geometry(f"{self.style_cfg['width']}x{self.style_cfg['height']}+{self._pos_x}+{self._pos_y}")
        self.configure(bg=self.style_cfg['bg'])
        self.main_container = tk.Frame(self, bg=self.style_cfg['bg'], highlightthickness=1, highlightbackground=self.style_cfg['border'], highlightcolor=self.style_cfg['accent'])
        self.main_container.pack(fill='both', expand=True, padx=2, pady=2)
        self.frame_header = tk.Frame(self.main_container, bg=self.style_cfg['bg_secondary'], height=44)
        self.frame_header.pack(fill='x', pady=(0, 0))
        self.frame_header.pack_propagate(False)
        self.header_left = tk.Frame(self.frame_header, bg=self.style_cfg['bg_secondary'])
        self.header_left.pack(side='left', fill='y')
        self.status_canvas = tk.Canvas(self.header_left, width=24, height=24, bg=self.style_cfg['bg_secondary'], highlightthickness=0)
        self.status_canvas.pack(side='left', padx=(10, 6), pady=10)
        self._draw_status_led(self.style_cfg['success'])
        self.lbl_title = tk.Label(self.header_left, text='▸ DRIVER HUD', fg=self.style_cfg['fg'], bg=self.style_cfg['bg_secondary'], font=('Segoe UI Semibold', self.style_cfg['font_size'] + 1))
        self.lbl_title.pack(side='left', pady=10)
        self.header_right = tk.Frame(self.frame_header, bg=self.style_cfg['bg_secondary'])
        self.header_right.pack(side='right', fill='y')
        self.btn_compact = tk.Label(self.header_right, text='▾', fg=self.style_cfg['fg_secondary'], bg=self.style_cfg['bg_secondary'], font=('Segoe UI', 12), cursor='hand2', padx=8)
        self.btn_compact.pack(side='right', padx=(0, 8), pady=10)
        self.btn_compact.bind('<Button-1>', lambda e: self.toggle_compact_mode())
        self.btn_compact.bind('<Enter>', lambda e: self.btn_compact.config(fg=self.style_cfg['accent']))
        self.btn_compact.bind('<Leave>', lambda e: self.btn_compact.config(fg=self.style_cfg['fg_secondary']))
        self.separator1 = tk.Canvas(self.main_container, height=2, bg=self.style_cfg['bg'], highlightthickness=0)
        self.separator1.pack(fill='x', padx=0)
        self._draw_gradient_separator()
        self.frame_status = tk.Frame(self.main_container, bg=self.style_cfg['bg'])
        self.frame_status.pack(fill='x', pady=(6, 4))
        self.lbl_status = tk.Label(self.frame_status, text='● READY', fg=self.style_cfg['success'], bg=self.style_cfg['bg'], font=('JetBrains Mono', self.style_cfg['font_size'] - 1, 'bold'), anchor='w')
        self.lbl_status.pack(side='left', padx=12)
        self.content_frame = tk.Frame(self.main_container, bg=self.style_cfg['bg'])
        self.content_frame.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        self.canvas = tk.Canvas(self.content_frame, bg=self.style_cfg['bg'], highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.content_frame, orient='vertical', command=self.canvas.yview, width=6)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.frame_monitor = tk.Frame(self.canvas, bg=self.style_cfg['bg'])
        self.canvas_window = self.canvas.create_window((0, 0), window=self.frame_monitor, anchor='nw')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.scrollbar.pack(side='right', fill='y')
        self.frame_monitor.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.monitor_widgets: Dict[str, Dict[str, Any]] = {}
        self.value_history: Dict[str, Deque[float]] = {}
        self.max_history = 50
        self._bind_drag(self.frame_header)
        self._bind_drag(self.header_left)
        self._bind_drag(self.lbl_title)
        self._bind_drag(self.status_canvas)
        self.canvas.bind('<Enter>', self._bind_mousewheel)
        self.canvas.bind('<Leave>', self._unbind_mousewheel)

    def _draw_status_led(self, color: str):
        """Draw modern LED indicator with glow effect."""
        self.status_canvas.delete('all')
        if self.style_cfg.get('glow_enabled', True):
            glow_color = self._adjust_color(color, 0.3)
            self.status_canvas.create_oval(4, 4, 20, 20, fill='', outline=glow_color, width=2)
        self.status_led = self.status_canvas.create_oval(7, 7, 17, 17, fill=color, outline='')
        self.status_canvas.create_oval(9, 8, 13, 11, fill=self._lighten_color(color, 1.5), outline='')

    def _draw_gradient_separator(self):
        """Draw a gradient separator line."""
        self.separator1.delete('all')
        width = self.style_cfg.get('width', 340)
        colors = [self.style_cfg['bg'], self.style_cfg['border'], self.style_cfg['accent'], self.style_cfg['border'], self.style_cfg['bg']]
        segment_width = width // (len(colors) - 1)
        for i, color in enumerate(colors[:-1]):
            x1 = i * segment_width
            x2 = (i + 1) * segment_width
            self.separator1.create_line(x1, 1, x2, 1, fill=colors[i + 1], width=2)

    def toggle_compact_mode(self):
        """Toggle between normal and compact mode with smooth transition."""
        self.style_cfg['compact_mode'] = not self.style_cfg['compact_mode']
        if self.style_cfg['compact_mode']:
            self.btn_compact.config(text='▴')
            self.content_frame.pack_forget()
            self.geometry(f"{self.style_cfg['width']}x72+{self._pos_x}+{self._pos_y}")
        else:
            self.btn_compact.config(text='▾')
            self.content_frame.pack(fill='both', expand=True, padx=4, pady=(0, 6))
            height = max(220, self.style_cfg.get('height', 220))
            self.geometry(f"{self.style_cfg['width']}x{height}+{self._pos_x}+{self._pos_y}")

    def set_theme(self, theme_name: str):
        """Apply a predefined theme."""
        if theme_name in self.THEMES:
            theme = self.THEMES[theme_name]
            self.style_cfg.update(theme)
            self.style_cfg['theme'] = theme_name
            self.apply_style(self.style_cfg)

    def _adjust_color(self, color: str, alpha: float) -> str:
        """Adjust color brightness by alpha factor."""
        cache_key = f'adj_{color}_{alpha}'
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]
        try:
            color = color.lstrip('#')
            r, g, b = tuple((int(color[i:i + 2], 16) for i in (0, 2, 4)))
            bg_val = 13
            r = int(r * alpha + bg_val * (1 - alpha))
            g = int(g * alpha + bg_val * (1 - alpha))
            b = int(b * alpha + bg_val * (1 - alpha))
            result = f'#{r:02x}{g:02x}{b:02x}'
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return '#1a1a1a'

    def _bind_mousewheel(self, event):
        """Enable mouse wheel scrolling."""
        self.canvas.bind('<MouseWheel>', self._on_mousewheel)
        self.canvas.bind('<Button-4>', self._on_mousewheel_linux)
        self.canvas.bind('<Button-5>', self._on_mousewheel_linux)

    def _unbind_mousewheel(self, event):
        """Disable mouse wheel scrolling."""
        self.canvas.unbind('<MouseWheel>')
        self.canvas.unbind('<Button-4>')
        self.canvas.unbind('<Button-5>')

    def _on_mousewheel(self, event):
        """Handle mouse wheel scroll (Windows/Mac)."""
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _on_mousewheel_linux(self, event):
        """Handle mouse wheel scroll (Linux)."""
        if event.num == 4:
            self.canvas.yview_scroll(-1, 'units')
        elif event.num == 5:
            self.canvas.yview_scroll(1, 'units')

    def _bind_drag(self, widget):
        """Bind drag events to a widget."""
        widget.bind('<Button-1>', self._start_move)
        widget.bind('<B1-Motion>', self._do_move)

    def _start_move(self, event):
        """Start dragging."""
        self.x = event.x
        self.y = event.y

    def _do_move(self, event):
        """Handle drag motion with smooth update."""
        dx = event.x - self.x
        dy = event.y - self.y
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self._pos_x = x
        self._pos_y = y
        self.geometry(f'+{x}+{y}')

    def apply_style(self, style_dict: Dict[str, Any]):
        """Apply modern style configuration to the overlay."""
        self.style_cfg.update(style_dict)
        self._color_cache.clear()
        bg = self.style_cfg.get('bg', '#0d1117')
        bg_secondary = self.style_cfg.get('bg_secondary', '#161b22')
        fg = self.style_cfg.get('fg', '#58a6ff')
        fg_secondary = self.style_cfg.get('fg_secondary', '#8b949e')
        accent = self.style_cfg.get('accent', '#238636')
        border = self.style_cfg.get('border', '#30363d')
        success = self.style_cfg.get('success', '#3fb950')
        fs = self.style_cfg.get('font_size', 11)
        op = self.style_cfg.get('opacity', 0.95)
        width = int(self.style_cfg.get('width', 340))
        height = int(self.style_cfg.get('height', 220))
        self.wm_attributes('-alpha', op)
        if not self.style_cfg.get('compact_mode', False):
            self.geometry(f'{width}x{height}+{self._pos_x}+{self._pos_y}')
        self.configure(bg=bg)
        self.main_container.config(bg=bg, highlightbackground=border, highlightcolor=accent)
        self.frame_header.config(bg=bg_secondary)
        self.header_left.config(bg=bg_secondary)
        self.header_right.config(bg=bg_secondary)
        self.lbl_title.config(bg=bg_secondary, fg=fg, font=('Segoe UI Semibold', fs + 1))
        self.status_canvas.config(bg=bg_secondary)
        self.btn_compact.config(bg=bg_secondary, fg=fg_secondary)
        self.separator1.config(bg=bg)
        self._draw_gradient_separator()
        self.frame_status.config(bg=bg)
        self.lbl_status.config(bg=bg, font=('JetBrains Mono', fs - 1, 'bold'))
        self.content_frame.config(bg=bg)
        self.canvas.config(bg=bg)
        self.frame_monitor.config(bg=bg)
        self._draw_status_led(success)
        for var_name, widgets in self.monitor_widgets.items():
            widgets['row'].config(bg=bg)
            widgets['label'].config(bg=bg, fg=fg_secondary, font=('Segoe UI', fs))
            widgets['value'].config(bg=bg, fg=fg, font=('JetBrains Mono', fs, 'bold'))
            if 'bar_frame' in widgets:
                widgets['bar_frame'].config(bg=bg)
            if 'bar' in widgets and widgets['bar']:
                widgets['bar'].config(bg=self._darken_color(bg, 0.6))

    def _darken_color(self, color: str, factor: float=0.7) -> str:
        """Darken a hex color by factor."""
        cache_key = f'dark_{color}_{factor}'
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]
        try:
            color = color.lstrip('#')
            r, g, b = tuple((int(color[i:i + 2], 16) for i in (0, 2, 4)))
            r = int(r * factor)
            g = int(g * factor)
            b = int(b * factor)
            result = f'#{r:02x}{g:02x}{b:02x}'
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return '#0a0a0a'

    def _blend_colors(self, color1: str, color2: str, ratio: float) -> str:
        """Blend two colors together. ratio=0 is color1, ratio=1 is color2."""
        cache_key = f'blend_{color1}_{color2}_{ratio:.2f}'
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]
        try:
            c1 = color1.lstrip('#')
            c2 = color2.lstrip('#')
            r1, g1, b1 = tuple((int(c1[i:i + 2], 16) for i in (0, 2, 4)))
            r2, g2, b2 = tuple((int(c2[i:i + 2], 16) for i in (0, 2, 4)))
            r = int(r1 * (1 - ratio) + r2 * ratio)
            g = int(g1 * (1 - ratio) + g2 * ratio)
            b = int(b1 * (1 - ratio) + b2 * ratio)
            result = f'#{r:02x}{g:02x}{b:02x}'
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return color1

    def update_status_text(self, text: str, color: str='white'):
        """Update the status header text with modern styling."""
        color_map = {'red': self.style_cfg.get('danger', '#f85149'), 'green': self.style_cfg.get('success', '#3fb950'), 'orange': self.style_cfg.get('warning', '#d29922'), 'white': self.style_cfg.get('fg', '#58a6ff'), 'deepskyblue': self.style_cfg.get('highlight', '#1f6feb')}
        c = color_map.get(color, color)
        icon = self.ICONS['status_ok']
        text_lower = text.lower()
        if 'error' in text_lower or 'fail' in text_lower:
            icon = self.ICONS['status_error']
        elif 'ok' in text_lower or 'ready' in text_lower or 'connected' in text_lower:
            icon = self.ICONS['status_ok']
        elif 'warning' in text_lower or 'caution' in text_lower:
            icon = self.ICONS['status_warn']
        elif 'scan' in text_lower or 'search' in text_lower or 'wait' in text_lower:
            icon = self.ICONS['status_scan']
        try:
            display_text = text.upper()[:30]
            self.lbl_status.config(text=f'{icon} {display_text}', fg=c)
            self._draw_status_led(c)
            self._last_status_color = c
        except Exception:
            pass

    def rebuild_monitor(self, var_configs: Dict[str, Dict[str, Any]]):
        """Rebuild the monitor display with premium modern styling."""
        for widget_dict in self.monitor_widgets.values():
            widget_dict['row'].destroy()
        self.monitor_widgets.clear()
        visible_vars = [v for v, cfg in var_configs.items() if cfg.get('show', False)]
        if not visible_vars:
            return
        bg = self.style_cfg.get('bg', '#0d1117')
        fg = self.style_cfg.get('fg', '#58a6ff')
        fg_secondary = self.style_cfg.get('fg_secondary', '#8b949e')
        fs = self.style_cfg.get('font_size', 11)
        for idx, var_name in enumerate(visible_vars):
            cfg = var_configs.get(var_name, {})
            label_text = cfg.get('label') or format_driver_control_name(var_name)
            row_bg = bg if idx % 2 == 0 else self._lighten_color(bg, 1.08)
            row = tk.Frame(self.frame_monitor, bg=row_bg, pady=4)
            row.pack(fill='x', padx=2, pady=1)
            self._bind_drag(row)
            row.columnconfigure(0, weight=0, minsize=24)
            row.columnconfigure(1, weight=2, minsize=90)
            row.columnconfigure(2, weight=1, minsize=70)
            row.columnconfigure(3, weight=3, minsize=100)
            icon = self._get_var_icon(var_name)
            icon_color = self._get_icon_color(var_name)
            l_icon = tk.Label(row, text=icon, bg=row_bg, fg=icon_color, font=('Segoe UI', fs), anchor='center', width=2)
            l_icon.grid(row=0, column=0, sticky='w', padx=(6, 2))
            self._bind_drag(l_icon)
            l_name = tk.Label(row, text=label_text, bg=row_bg, fg=fg_secondary, font=('Segoe UI', fs), anchor='w')
            l_name.grid(row=0, column=1, sticky='w', padx=(0, 5))
            self._bind_drag(l_name)
            l_value = tk.Label(row, text='---', bg=row_bg, fg=fg, font=('JetBrains Mono', fs, 'bold'), anchor='e', width=7)
            l_value.grid(row=0, column=2, sticky='e', padx=(0, 8))
            self._bind_drag(l_value)
            bar_frame = None
            bar_canvas = None
            if self.style_cfg.get('show_graphs', True) and (not self.style_cfg.get('compact_mode', False)):
                bar_frame = tk.Frame(row, bg=row_bg, pady=2)
                bar_frame.grid(row=0, column=3, sticky='we', padx=(0, 8))
                bar_canvas = tk.Canvas(bar_frame, height=10, bg=self._darken_color(bg, 0.5), highlightthickness=1, highlightbackground=self._darken_color(bg, 0.7))
                bar_canvas.pack(fill='x', expand=True)
                self._bind_drag(bar_canvas)
                self._bind_drag(bar_frame)
            self.monitor_widgets[var_name] = {'row': row, 'icon': l_icon, 'label': l_name, 'value': l_value, 'bar_frame': bar_frame, 'bar': bar_canvas, 'last_value': None, 'row_bg': row_bg}
            if var_name not in self.value_history:
                self.value_history[var_name] = deque(maxlen=self.max_history)

    def _get_var_icon(self, var_name: str) -> str:
        """Get modern icon based on variable type."""
        name_lower = var_name.lower()
        if 'bias' in name_lower or 'brake' in name_lower:
            return self.ICONS['brake']
        elif 'fuel' in name_lower:
            return self.ICONS['fuel']
        elif 'tc' in name_lower or 'traction' in name_lower:
            return self.ICONS['tc']
        elif 'abs' in name_lower:
            return self.ICONS['abs']
        elif 'diff' in name_lower:
            return self.ICONS['diff']
        elif 'roll' in name_lower or 'arb' in name_lower:
            return self.ICONS['roll']
        elif 'weight' in name_lower or 'jacker' in name_lower:
            return self.ICONS['weight']
        elif 'lap' in name_lower or 'dist' in name_lower:
            return self.ICONS['lap']
        elif 'speed' in name_lower:
            return self.ICONS['speed']
        elif 'gear' in name_lower:
            return self.ICONS['gear']
        elif 'rpm' in name_lower:
            return self.ICONS['rpm']
        elif 'temp' in name_lower:
            return self.ICONS['temp']
        else:
            return self.ICONS['default']

    def _get_icon_color(self, var_name: str) -> str:
        """Get icon color based on variable type."""
        name_lower = var_name.lower()
        if 'bias' in name_lower or 'brake' in name_lower:
            return self.style_cfg.get('danger', '#f85149')
        elif 'fuel' in name_lower:
            return self.style_cfg.get('warning', '#d29922')
        elif 'tc' in name_lower or 'traction' in name_lower:
            return self.style_cfg.get('accent', '#238636')
        elif 'abs' in name_lower:
            return self.style_cfg.get('highlight', '#1f6feb')
        elif 'lap' in name_lower or 'dist' in name_lower:
            return self.style_cfg.get('fg', '#58a6ff')
        else:
            return self.style_cfg.get('fg_secondary', '#8b949e')

    def update_monitor_values(self, data_dict: Dict[str, Any]):
        """Update displayed telemetry values with modern visual bars."""
        for var_name, value in data_dict.items():
            if var_name not in self.monitor_widgets:
                continue
            widgets = self.monitor_widgets[var_name]
            last_value = widgets.get('last_value')
            if last_value == value and value is not None:
                continue
            widgets['last_value'] = value
            if value is None:
                text = '---'
                color = self.style_cfg.get('fg_secondary', '#8b949e')
            elif isinstance(value, float):
                if abs(value) >= 100:
                    text = f'{value:.1f}'
                elif abs(value) >= 10:
                    text = f'{value:.2f}'
                else:
                    text = f'{value:.3f}'
                color = self._get_value_color(value, var_name)
            else:
                text = str(value)
                color = self.style_cfg.get('fg', '#58a6ff')
            try:
                widgets['value'].config(text=text, fg=color)
            except Exception:
                pass
            if 'bar' in widgets and widgets['bar'] and (value is not None):
                try:
                    float_val = float(value) if isinstance(value, (int, float)) else 0.0
                    self._update_progress_bar(widgets['bar'], var_name, float_val)
                except Exception:
                    pass
            if value is not None and isinstance(value, (int, float)):
                if var_name not in self.value_history:
                    self.value_history[var_name] = deque(maxlen=self.max_history)
                self.value_history[var_name].append(float(value))

    def _get_value_color(self, value: float, var_name: str) -> str:
        """Get dynamic color based on value and variable type."""
        name_lower = var_name.lower()
        if 'fuel' in name_lower:
            if value < 0.15:
                return self.style_cfg.get('danger', '#f85149')
            elif value < 0.3:
                return self.style_cfg.get('warning', '#d29922')
            return self.style_cfg.get('fg', '#58a6ff')
        if value >= 0.95:
            return self.style_cfg.get('warning', '#d29922')
        if value <= 0.05:
            return self.style_cfg.get('danger', '#f85149')
        return self.style_cfg.get('fg', '#58a6ff')

    def _update_progress_bar(self, canvas: tk.Canvas, var_name: str, value: float):
        """Update progress bar with modern gradient and glow effects."""
        try:
            width = canvas.winfo_width()
            height = canvas.winfo_height()
            if width <= 1:
                width = 120
            if height <= 1:
                height = 10
            canvas.delete('all')
            bg_color = self._darken_color(self.style_cfg.get('bg', '#0d1117'), 0.5)
            canvas.create_rectangle(0, 0, width, height, fill=bg_color, outline='')
            norm_value = max(0.0, min(1.0, value))
            bar_width = max(0, (width - 2) * norm_value)
            if bar_width < 2:
                return
            if norm_value < 0.33:
                main_color = self.style_cfg.get('success', '#3fb950')
                glow_color = self.style_cfg.get('accent_glow', '#2ea043')
            elif norm_value < 0.66:
                main_color = self.style_cfg.get('highlight', '#1f6feb')
                glow_color = self.style_cfg.get('fg', '#58a6ff')
            elif norm_value < 0.85:
                main_color = self.style_cfg.get('warning', '#d29922')
                glow_color = self._lighten_color(main_color, 1.2)
            else:
                main_color = self.style_cfg.get('danger', '#f85149')
                glow_color = self._lighten_color(main_color, 1.3)
            canvas.create_rectangle(1, 1, bar_width + 1, height - 1, fill=main_color, outline='')
            highlight_height = max(2, height // 3)
            canvas.create_rectangle(1, 1, bar_width + 1, highlight_height, fill=self._lighten_color(main_color, 1.4), outline='')
            if self.style_cfg.get('glow_enabled', True) and bar_width > 4:
                glow_width = min(6, bar_width // 4)
                for i in range(glow_width):
                    alpha = 1.0 - i / glow_width
                    glow = self._blend_colors(glow_color, main_color, alpha * 0.5)
                    canvas.create_line(bar_width - i, 2, bar_width - i, height - 2, fill=glow, width=1)
            if bar_width > 8:
                canvas.create_line(bar_width, 1, bar_width, height - 1, fill=self._lighten_color(main_color, 1.6), width=1)
        except Exception:
            pass

    def _lighten_color(self, color: str, factor: float=1.3) -> str:
        """Lighten a hex color by factor."""
        cache_key = f'light_{color}_{factor}'
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]
        try:
            color = color.lstrip('#')
            r, g, b = tuple((int(color[i:i + 2], 16) for i in (0, 2, 4)))
            r = min(255, int(r * factor))
            g = min(255, int(g * factor))
            b = min(255, int(b * factor))
            result = f'#{r:02x}{g:02x}{b:02x}'
            self._color_cache[cache_key] = result
            return result
        except Exception:
            return '#ffffff'

    def clear_color_cache(self):
        """Clear the color calculation cache."""
        self._color_cache.clear()

class ScrollableFrame(tk.Frame):
    """
    Frame with vertical scrollbar.
    Use self.inner as the container for child widgets.
    """

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient='vertical', command=canvas.yview)
        self.inner = tk.Frame(canvas)
        self.inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        inner_window = canvas.create_window((0, 0), window=self.inner, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_canvas_configure(event):
            canvas.itemconfigure(inner_window, width=event.width)
        canvas.bind('<Configure>', _on_canvas_configure)
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        bind_mousewheel_scroll(self, canvas)

class OverlayConfigTab(tk.Frame):
    """
    Configuration tab for HUD overlay appearance and variable display.
    Modern styling with theme presets and advanced customization.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.var_rows: Dict[str, Dict[str, Any]] = {}
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill='both', expand=True)
        self.body = scroll_frame.inner
        header_frame = tk.Frame(self.body)
        header_frame.pack(anchor='w', pady=(8, 10), padx=5)
        tk.Label(header_frame, text='▸', font=('Segoe UI', 12), fg='#58a6ff').pack(side='left')
        tk.Label(header_frame, text='HUD / Overlay Configuration', font=('Segoe UI Semibold', 11)).pack(side='left', padx=(4, 0))
        theme_frame = tk.LabelFrame(self.body, text='● Theme Presets', font=('Segoe UI', 9, 'bold'))
        theme_frame.pack(fill='x', padx=5, pady=(0, 8))
        theme_inner = tk.Frame(theme_frame)
        theme_inner.pack(fill='x', padx=5, pady=8)
        tk.Label(theme_inner, text='Select Theme:', font=('Segoe UI', 9)).pack(side='left', padx=(0, 10))
        self.theme_var = tk.StringVar(value=self.app.overlay.style_cfg.get('theme', 'midnight'))
        themes = [('Midnight', 'midnight'), ('Neon', 'neon'), ('Racing', 'racing')]
        for label, value in themes:
            rb = tk.Radiobutton(theme_inner, text=label, variable=self.theme_var, value=value, command=self._on_theme_change, font=('Segoe UI', 9))
            rb.pack(side='left', padx=8)
        appearance_frame = tk.LabelFrame(self.body, text='● Appearance', font=('Segoe UI', 9, 'bold'))
        appearance_frame.pack(fill='x', padx=5, pady=5)
        tk.Label(appearance_frame, text='Background:', font=('Segoe UI', 9)).grid(row=0, column=0, padx=8, pady=6, sticky='w')
        self.lbl_bg_preview = tk.Label(appearance_frame, text='     ', bg=self.app.overlay.style_cfg.get('bg', '#0d1117'), relief='solid', width=4)
        self.lbl_bg_preview.grid(row=0, column=1, padx=5, pady=6)
        self.btn_bg = tk.Button(appearance_frame, text='Choose', command=self.pick_background_color, font=('Segoe UI', 8), width=8)
        self.btn_bg.grid(row=0, column=2, padx=5, pady=6, sticky='w')
        tk.Label(appearance_frame, text='Text Color:', font=('Segoe UI', 9)).grid(row=1, column=0, padx=8, pady=6, sticky='w')
        self.lbl_fg_preview = tk.Label(appearance_frame, text=' Aa ', fg=self.app.overlay.style_cfg.get('fg', '#58a6ff'), bg='#1a1a1a', relief='solid', font=('Segoe UI', 9, 'bold'))
        self.lbl_fg_preview.grid(row=1, column=1, padx=5, pady=6)
        self.btn_fg = tk.Button(appearance_frame, text='Choose', command=self.pick_text_color, font=('Segoe UI', 8), width=8)
        self.btn_fg.grid(row=1, column=2, padx=5, pady=6, sticky='w')
        tk.Label(appearance_frame, text='Font Size:', font=('Segoe UI', 9)).grid(row=2, column=0, padx=8, pady=4, sticky='w')
        self.scale_font = tk.Scale(appearance_frame, from_=8, to=18, orient='horizontal', length=150, showvalue=True)
        self.scale_font.set(self.app.overlay.style_cfg.get('font_size', 11))
        self.scale_font.grid(row=2, column=1, columnspan=2, padx=5, pady=4, sticky='w')
        tk.Label(appearance_frame, text='Opacity:', font=('Segoe UI', 9)).grid(row=3, column=0, padx=8, pady=4, sticky='w')
        self.scale_opacity = tk.Scale(appearance_frame, from_=0.5, to=1.0, resolution=0.05, orient='horizontal', length=150, showvalue=True)
        self.scale_opacity.set(self.app.overlay.style_cfg.get('opacity', 0.95))
        self.scale_opacity.grid(row=3, column=1, columnspan=2, padx=5, pady=4, sticky='w')
        size_frame = tk.LabelFrame(self.body, text='● Dimensions', font=('Segoe UI', 9, 'bold'))
        size_frame.pack(fill='x', padx=5, pady=5)
        tk.Label(size_frame, text='Width:', font=('Segoe UI', 9)).grid(row=0, column=0, padx=8, pady=4, sticky='w')
        self.scale_width = tk.Scale(size_frame, from_=200, to=600, orient='horizontal', length=180)
        self.scale_width.set(self.app.overlay.style_cfg.get('width', 340))
        self.scale_width.grid(row=0, column=1, padx=5, pady=4, sticky='w')
        tk.Label(size_frame, text='Height:', font=('Segoe UI', 9)).grid(row=1, column=0, padx=8, pady=4, sticky='w')
        self.scale_height = tk.Scale(size_frame, from_=120, to=500, orient='horizontal', length=180)
        self.scale_height.set(self.app.overlay.style_cfg.get('height', 220))
        self.scale_height.grid(row=1, column=1, padx=5, pady=4, sticky='w')
        effects_frame = tk.LabelFrame(self.body, text='● Visual Effects', font=('Segoe UI', 9, 'bold'))
        effects_frame.pack(fill='x', padx=5, pady=5)
        self.glow_var = tk.BooleanVar(value=self.app.overlay.style_cfg.get('glow_enabled', True))
        tk.Checkbutton(effects_frame, text='Enable glow effects on progress bars', variable=self.glow_var, command=self._on_effects_change, font=('Segoe UI', 9)).pack(anchor='w', padx=8, pady=4)
        self.graphs_var = tk.BooleanVar(value=self.app.overlay.style_cfg.get('show_graphs', True))
        tk.Checkbutton(effects_frame, text='Show progress bars', variable=self.graphs_var, command=self._on_effects_change, font=('Segoe UI', 9)).pack(anchor='w', padx=8, pady=4)
        tk.Button(self.body, text='▸ Apply Style', command=self.apply_style, font=('Segoe UI Semibold', 9), bg='#238636', fg='white', activebackground='#2ea043', activeforeground='white', relief='flat', padx=20, pady=6).pack(pady=(10, 5))
        for i in range(3):
            appearance_frame.columnconfigure(i, weight=1)
            size_frame.columnconfigure(i, weight=1)
        feedback_frame = tk.LabelFrame(self.body, text='● Assist Feedback (per car)', font=('Segoe UI', 9, 'bold'))
        feedback_frame.pack(fill='x', padx=5, pady=5)
        tk.Checkbutton(feedback_frame, text='Show ABS / TC / slip hints on the HUD', variable=self.app.show_overlay_feedback, command=self._on_feedback_toggle, font=('Segoe UI', 9)).grid(row=0, column=0, columnspan=2, sticky='w', padx=8, pady=(6, 8))
        self.feedback_vars = {'abs_hold_s': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['abs_hold_s']), 'tc_hold_s': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['tc_hold_s']), 'wheelspin_slip': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['wheelspin_slip']), 'wheelspin_hold_s': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['wheelspin_hold_s']), 'lockup_slip': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['lockup_slip']), 'lockup_hold_s': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['lockup_hold_s']), 'cooldown_s': tk.DoubleVar(value=DEFAULT_OVERLAY_FEEDBACK['cooldown_s'])}
        self.feedback_entries: Dict[str, tk.Entry] = {}
        feedback_rows = [('ABS active (s)', 'abs_hold_s'), ('TC active (s)', 'tc_hold_s'), ('Wheelspin slip', 'wheelspin_slip'), ('Wheelspin (s)', 'wheelspin_hold_s'), ('Lock-up slip', 'lockup_slip'), ('Lock-up (s)', 'lockup_hold_s'), ('Cooldown (s)', 'cooldown_s')]
        for idx, (label, key) in enumerate(feedback_rows, start=1):
            tk.Label(feedback_frame, text=label, font=('Segoe UI', 9)).grid(row=idx, column=0, padx=8, pady=2, sticky='w')
            entry = tk.Entry(feedback_frame, width=8, textvariable=self.feedback_vars[key], font=('Segoe UI', 9))
            entry.grid(row=idx, column=1, padx=5, pady=2, sticky='w')
            entry.bind('<FocusOut>', self._on_feedback_change)
            entry.bind('<KeyRelease>', self._on_feedback_change)
            self.feedback_entries[key] = entry
        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())
        variables_frame = tk.LabelFrame(self.body, text='● Variables to Display (per car)', font=('Segoe UI', 9, 'bold'))
        variables_frame.pack(fill='both', expand=True, padx=5, pady=5)
        header = tk.Frame(variables_frame)
        header.pack(fill='x', pady=(6, 4), padx=5)
        tk.Label(header, text='Show', width=6, anchor='w', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=2)
        tk.Label(header, text='Variable', width=22, anchor='w', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=2)
        tk.Label(header, text='HUD Label', width=18, anchor='w', font=('Segoe UI', 9, 'bold')).pack(side='left', padx=2)
        self.variables_list_frame = tk.Frame(variables_frame)
        self.variables_list_frame.pack(fill='both', expand=True, padx=5)
        note_frame = tk.Frame(self.body)
        note_frame.pack(anchor='w', padx=8, pady=(8, 12))
        tk.Label(note_frame, text='ℹ', font=('Segoe UI', 9), fg='#58a6ff').pack(side='left')
        tk.Label(note_frame, text=' Variables are saved per car. Theme applies globally.', fg='#8b949e', font=('Segoe UI', 8)).pack(side='left')

    def _on_theme_change(self):
        """Handle theme selection change."""
        theme_name = self.theme_var.get()
        if theme_name in OverlayWindow.THEMES:
            theme = OverlayWindow.THEMES[theme_name]
            self.app.overlay.style_cfg.update(theme)
            self.app.overlay.style_cfg['theme'] = theme_name
            self.lbl_bg_preview.config(bg=theme.get('bg', '#0d1117'))
            self.lbl_fg_preview.config(fg=theme.get('fg', '#58a6ff'))
            self.apply_style()

    def _on_effects_change(self):
        """Handle visual effects toggle."""
        self.app.overlay.style_cfg['glow_enabled'] = self.glow_var.get()
        self.app.overlay.style_cfg['show_graphs'] = self.graphs_var.get()
        self.apply_style()
        car = self.app.current_car or 'Generic Car'
        config = self.app.car_overlay_config.get(car, {})
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(config))

    def pick_background_color(self):
        """Open color picker for background color."""
        current = self.app.overlay.style_cfg.get('bg', '#0d1117')
        color = colorchooser.askcolor(title='Background Color', initialcolor=current)[1]
        if color:
            self.app.overlay.style_cfg['bg'] = color
            self.lbl_bg_preview.config(bg=color)
            self.apply_style()

    def pick_text_color(self):
        """Open color picker for text color."""
        current = self.app.overlay.style_cfg.get('fg', '#58a6ff')
        color = colorchooser.askcolor(title='Text Color', initialcolor=current)[1]
        if color:
            self.app.overlay.style_cfg['fg'] = color
            self.lbl_fg_preview.config(fg=color)
            self.apply_style()

    def apply_style(self):
        """Apply current style settings to overlay."""
        self.app.overlay.style_cfg['font_size'] = int(self.scale_font.get())
        self.app.overlay.style_cfg['opacity'] = float(self.scale_opacity.get())
        self.app.overlay.style_cfg['width'] = int(self.scale_width.get())
        self.app.overlay.style_cfg['height'] = int(self.scale_height.get())
        self.app.overlay.style_cfg['glow_enabled'] = self.glow_var.get()
        self.app.overlay.style_cfg['show_graphs'] = self.graphs_var.get()
        self.app.overlay.apply_style(self.app.overlay.style_cfg)
        self.app.save_config()

    @staticmethod
    def _default_overlay_label(var_name: str) -> str:
        """Return the default label for an overlay variable."""
        return format_driver_control_name(var_name)

    def load_for_car(self, car_name: str, var_list: List[Tuple[str, bool, bool]], overlay_config: Dict[str, Dict[str, Any]]):
        """
        Load HUD configuration for a specific car.
        
        Args:
            car_name: Name of the car
            var_list: List of (var_name, is_float, is_boolean) tuples
            overlay_config: Dict of var_name -> {"show": bool, "label": str}
        """
        self._load_feedback_for_car(car_name)
        for child in self.variables_list_frame.winfo_children():
            child.destroy()
        self.var_rows.clear()
        for entry in var_list:
            var_name, _is_float, _is_boolean = _normalize_var_tuple(entry)
            if var_name not in overlay_config:
                default_label = self._default_overlay_label(var_name)
                overlay_config[var_name] = {'show': False, 'label': default_label}
        for entry in var_list:
            var_name, _is_float, _is_boolean = _normalize_var_tuple(entry)
            config = overlay_config.get(var_name, {})
            row = tk.Frame(self.variables_list_frame)
            row.pack(fill='x', pady=2)
            show_var = tk.BooleanVar(value=config.get('show', False))
            checkbox = tk.Checkbutton(row, variable=show_var)
            checkbox.pack(side='left', padx=2)
            tk.Label(row, text=var_name, width=25, anchor='w').pack(side='left', padx=2)
            label_entry = tk.Entry(row, width=20)
            label_entry.pack(side='left', padx=2)
            label_entry.insert(0, config.get('label') or self._default_overlay_label(var_name))
            self.var_rows[var_name] = {'show_var': show_var, 'entry': label_entry}
            show_var.trace_add('write', lambda *_args, vn=var_name: self._on_overlay_row_change(vn))
            label_entry.bind('<KeyRelease>', lambda _event, vn=var_name: self._on_overlay_row_change(vn))
        self.app.car_overlay_config[car_name] = overlay_config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(overlay_config))
        self.app.save_config()

    def _on_feedback_change(self, *_args):
        """Persist feedback edits and save lazily."""
        car = self.app.current_car or 'Generic Car'
        self._collect_feedback_for_car(car)
        self.app.schedule_save()

    def _on_feedback_toggle(self):
        """Enable or disable assist hints and persist the preference."""
        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())
        self._on_feedback_change()

    def _set_feedback_fields_enabled(self, enabled: bool) -> None:
        """Toggle entry state for assist thresholds."""
        state = 'normal' if enabled else 'disabled'
        for entry in self.feedback_entries.values():
            try:
                entry.config(state=state)
            except Exception:
                continue

    def _on_overlay_row_change(self, var_name: str):
        """Apply live updates when overlay rows change."""
        car = self.app.current_car or 'Generic Car'
        config = self.app.car_overlay_config.get(car, {})
        row = self.var_rows.get(var_name)
        if not row:
            return
        show = row['show_var'].get()
        default_label = self._default_overlay_label(var_name)
        label = row['entry'].get().strip() or default_label
        config[var_name] = {'show': show, 'label': label}
        self.app.car_overlay_config[car] = config
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(config))
        self.app.schedule_save()

    def collect_for_car(self, car_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Collect current HUD configuration for a car.
        
        Args:
            car_name: Name of the car
            
        Returns:
            Dict of var_name -> {"show": bool, "label": str}
        """
        config = self.app.car_overlay_config.get(car_name, {})
        for var_name, row_config in self.var_rows.items():
            show = row_config['show_var'].get()
            default_label = self._default_overlay_label(var_name)
            label = row_config['entry'].get().strip() or default_label
            config[var_name] = {'show': show, 'label': label}
        self.app.car_overlay_config[car_name] = config
        self._collect_feedback_for_car(car_name)
        self.app.overlay.rebuild_monitor(self.app._overlay_display_config(config))
        return config

    def _load_feedback_for_car(self, car_name: str) -> None:
        """Load per-car feedback thresholds into the UI fields."""
        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(self.app.car_overlay_feedback.get(car_name, {}))
        for key, var in self.feedback_vars.items():
            try:
                var.set(float(cfg.get(key, DEFAULT_OVERLAY_FEEDBACK[key])))
            except Exception:
                var.set(DEFAULT_OVERLAY_FEEDBACK[key])
        self._set_feedback_fields_enabled(self.app.show_overlay_feedback.get())

    def _collect_feedback_for_car(self, car_name: str) -> Dict[str, float]:
        """Persist feedback thresholds from UI fields for a car."""
        cfg: Dict[str, float] = DEFAULT_OVERLAY_FEEDBACK.copy()
        for key, var in self.feedback_vars.items():
            try:
                cfg[key] = float(var.get())
            except Exception:
                cfg[key] = DEFAULT_OVERLAY_FEEDBACK[key]
        self.app.car_overlay_feedback[car_name] = cfg
        return cfg

class GenericController:
    """
    Controller for adjusting a single telemetry variable via key presses.
    """

    def __init__(self, ir_instance, var_name: str, is_float: bool=False, is_boolean: bool=False, allow_dual_keys: bool=False, status_callback: Optional[Callable[[str, str], None]]=None, app_ref=None):
        self.ir = ir_instance
        self.var_name = var_name
        self.is_float = is_float
        self.is_boolean = is_boolean
        self.allow_dual_keys = allow_dual_keys or not is_boolean
        self.running_action = False
        self.key_increase = None
        self.key_decrease = None
        self.update_status = status_callback
        self.app = app_ref
        self._target_lock = threading.Lock()
        self._requested_target: Optional[float] = None
        self._requested_nonce = 0
        self._next_nonce = 0
        self._clear_requested = False
        self._requested_direct_boundary = False
        self._worker_thread: Optional[threading.Thread] = None
        self._float_step: Optional[float] = None
        self.min_value: Optional[float] = None
        self.max_value: Optional[float] = None
        if self.var_name in WEIGHT_JACKER_VARS:
            self.min_value = -20.0
            self.max_value = 20.0
        self.boolean_pulse_only = False
        self.boolean_pulse_double = False
        self._momentary_armed = True
        self._momentary_last_request = 0.0
        self._momentary_release_timeout_s = 1.2

    def _is_momentary_pulse(self) -> bool:
        """Return True for driver controls that should be a one-shot pulse."""
        name = (self.var_name or '').lower()
        return 'tearoff' in name or 'tear_off' in name

    def _should_trigger_momentary(self, target: float) -> bool:
        """Debounce momentary pulses to avoid repeated firing loops."""
        now = time.time()
        if target <= 0:
            self._momentary_armed = True
            self._momentary_last_request = now
            return False
        if self._momentary_last_request > 0.0 and now - self._momentary_last_request > self._momentary_release_timeout_s:
            self._momentary_armed = True
        self._momentary_last_request = now
        if not self._momentary_armed:
            return False
        self._momentary_armed = False
        return True

    def read_telemetry(self, use_cache: bool=True, cache_ttl_s: Optional[float]=None) -> Optional[float]:
        """
        Read current value of the controlled variable.

        Args:
            use_cache: If True, return cached value when available.
            cache_ttl_s: Cache time-to-live in seconds (default 0.05).

        Returns:
            Current value or None if unavailable
        """
        if self.var_name == 'dcPushToPass' and self.app:
            status = self.app._read_push_to_pass_status()
            if status is not None:
                result = 1 if status else 0
                _TELEMETRY_CACHE.set(self.var_name, result)
                return result
        if not _TELEMETRY_CIRCUIT_BREAKER.can_execute(self.var_name):
            hit, cached = _TELEMETRY_CACHE.get(self.var_name, ttl_s=2.0)
            if hit:
                return cached
            return None
        if use_cache:
            hit, cached = _TELEMETRY_CACHE.get(self.var_name, ttl_s=cache_ttl_s)
            if hit and cached is not None:
                return cached
        on_ui_thread = threading.current_thread() is threading.main_thread()
        try:
            if not getattr(self.ir, 'is_initialized', False):
                if self.app:
                    self.app._start_sdk_warmup()
                if on_ui_thread:
                    return None
                try:
                    self.ir.startup()
                    time.sleep(0.1)
                except Exception:
                    _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                    return None
            max_retries = 1 if on_ui_thread else 4
            base_delay = 0.03
            for attempt in range(max_retries):
                try:
                    value = self.ir[self.var_name]
                    if value is None:
                        if on_ui_thread:
                            return None
                        if attempt < max_retries - 1:
                            delay = base_delay * 2 ** attempt
                            time.sleep(min(delay, 0.25))
                            continue
                        if self.app:
                            self.app._note_none_telemetry(self.var_name)
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                        return None
                    _TELEMETRY_CIRCUIT_BREAKER.record_success(self.var_name)
                    if self.app:
                        self.app._clear_none_telemetry(self.var_name)
                    if self.is_float:
                        result = float(value)
                    else:
                        result = int(round(value))
                    _TELEMETRY_CACHE.set(self.var_name, result)
                    return result
                except Exception as e:
                    if on_ui_thread:
                        if self.app:
                            self.app._start_sdk_warmup()
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                        return None
                    if attempt < max_retries - 1:
                        delay = base_delay * 2 ** attempt
                        try:
                            if hasattr(self.ir, 'shutdown'):
                                self.ir.shutdown()
                            time.sleep(min(delay, 0.25))
                            self.ir.startup()
                            time.sleep(0.1)
                        except Exception:
                            pass
                    else:
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
                        return None
        except Exception:
            _TELEMETRY_CIRCUIT_BREAKER.record_failure(self.var_name)
            return None
        return None

    def _resolve_target(self, target: float) -> float:
        """Align float targets to the nearest reachable increment when needed."""
        if not self.is_float:
            return target
        step = self._float_step
        current = self.read_telemetry()
        if step is None or step <= 0 or current is None:
            return target
        aligned = current + round((target - current) / step) * step
        if abs(aligned - target) >= 0.0005:
            if self.update_status:
                self.update_status(f'Rounded to {aligned:.3f}', 'orange')
            if self.app:
                short_name = compact_driver_control_name(self.var_name)
                self.app.notify_overlay_status(f'{short_name}: using {aligned:.3f} (nearest)', 'orange')
        return aligned

    def _apply_bounds(self, target: float) -> Tuple[float, bool]:
        """Clamp target to configured bounds and flag boundary requests."""
        min_value = self.min_value
        max_value = self.max_value
        if min_value is not None and max_value is not None and (max_value < min_value):
            min_value, max_value = (max_value, min_value)
        if min_value is not None and target <= min_value:
            return (min_value, True)
        if max_value is not None and target >= max_value:
            return (max_value, True)
        return (target, False)

    def _read_stable_int(self, initial_value: int, samples: int=3, delay: float=0.01) -> int:
        """Read multiple integer samples to reduce jitter near targets."""
        if samples <= 1:
            return initial_value
        values = [initial_value]
        for _ in range(samples - 1):
            time.sleep(delay)
            read_value = self.read_telemetry()
            if read_value is None:
                continue
            values.append(int(round(read_value)))
        values.sort()
        return values[len(values) // 2]

    def _read_stable_float(self, initial_value: float, samples: int=3, delay: float=0.01) -> float:
        """Read multiple float samples to reduce jitter near targets."""
        if samples <= 1:
            return initial_value
        values = [float(initial_value)]
        for _ in range(samples - 1):
            time.sleep(delay)
            read_value = self.read_telemetry()
            if read_value is None:
                continue
            values.append(float(read_value))
        values.sort()
        return values[len(values) // 2]

    def _estimate_pulses_remaining(self, diff: float) -> Optional[int]:
        """Estimate how many key pulses remain to close the diff."""
        if self.is_float:
            step = self._float_step if self._float_step else 0.001
            if step <= 0:
                return None
            return max(0, int(math.ceil(abs(diff) / step)))
        return max(0, int(abs(round(diff))))

    def _trigger_boolean(self, target: float) -> None:
        """Trigger a boolean control once without retry loops."""
        if self.running_action:
            return
        if not self.key_increase:
            if self.update_status:
                self.update_status('No key configured', 'red')
            if self.app:
                self.app.notify_overlay_status(f'{compact_driver_control_name(self.var_name)}: No key', 'red')
            return
        if not self.boolean_pulse_only:
            desired_state = 1 if target > 0 else 0
            current = self.read_telemetry()
            if current is None and desired_state == 0:
                return
            if current is not None:
                current_state = 1 if current > 0 else 0
                if current_state == desired_state:
                    if self.update_status:
                        state_text = 'ON' if desired_state else 'OFF'
                        self.update_status(f'Already {state_text}', 'gray')
                    return
        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        try:
            click_pulse(self.key_increase, is_float=False)
            if self.boolean_pulse_only and self.boolean_pulse_double:
                time.sleep(0.08)
                click_pulse(self.key_increase, is_float=False)
            if self.update_status:
                self.update_status('Triggered', 'green')
            if self.app:
                message = f'{short_name} Triggered'
                self.app.notify_overlay_status(message, 'green')
        finally:
            self.running_action = False

    def _trigger_momentary_pulse(self) -> None:
        """Send a one-shot pulse without consulting telemetry."""
        if self.running_action:
            return
        if not self.key_increase:
            if self.update_status:
                self.update_status('No key configured', 'red')
            if self.app:
                self.app.notify_overlay_status(f'{compact_driver_control_name(self.var_name)}: No key', 'red')
            return
        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        try:
            click_pulse(self.key_increase, is_float=False)
            if self.boolean_pulse_double:
                time.sleep(0.08)
                click_pulse(self.key_increase, is_float=False)
            if self.update_status:
                self.update_status('Triggered', 'green')
            if self.app:
                message = f'{short_name} Triggered'
                self.app.notify_overlay_status(message, 'green')
        finally:
            self.running_action = False

    def trigger_pulse(self) -> None:
        """Public helper to force a one-shot pulse regardless of telemetry."""
        self._trigger_momentary_pulse()

    def request_target(self, target: float):
        """Queue a target adjustment request, overriding any active target."""
        if self.app and (not self.app._commands_allowed()):
            return
        if self._is_momentary_pulse():
            if self._should_trigger_momentary(target):
                self._trigger_momentary_pulse()
            return
        if self.is_boolean and (not self.allow_dual_keys):
            self._trigger_boolean(target)
            return
        target, direct_boundary = self._apply_bounds(target)
        with self._target_lock:
            self._next_nonce += 1
            self._requested_target = target
            self._requested_nonce = self._next_nonce
            self._clear_requested = False
            self._requested_direct_boundary = direct_boundary
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(target=self._run_target_loop, daemon=True)
            self._worker_thread.start()

    def clear_target(self):
        """Clear any pending target requests and stop adjusting."""
        with self._target_lock:
            self._requested_target = None
            self._clear_requested = True

    def adjust_to_target(self, target: float):
        """
        Adjust variable to target value using discrete key presses.

        Args:
            target: Target value to reach
        """
        self.request_target(target)

    def _read_weight_jacker_int(self, samples: int=2) -> Optional[int]:
        """Return a stable integer Weight Jacker value when telemetry is ready."""
        values: List[int] = []
        for idx in range(max(1, samples)):
            current = self.read_telemetry()
            if current is not None:
                try:
                    values.append(int(round(float(current))))
                except Exception:
                    pass
            if idx < samples - 1:
                time.sleep(0.012)
        if not values:
            return None
        values.sort()
        return values[len(values) // 2]

    def _weight_jacker_request_changed(self, nonce: int) -> bool:
        with self._target_lock:
            return self._clear_requested or self._requested_nonce != nonce

    def _send_weight_jacker_pulses(self, key: Any, pulses: int, *, press_ms: int, interval_ms: int, nonce: int) -> bool:
        for _ in range(max(0, pulses)):
            if self._weight_jacker_request_changed(nonce):
                return False
            _direct_pulse(key, press_ms=press_ms, interval_ms=interval_ms)
        return True

    def _run_weight_jacker_direct_loop(self):
        """Send a fast burst, verify telemetry, then correct close to target."""
        if self.running_action:
            return
        if not self.key_increase or not self.key_decrease:
            if self.update_status:
                self.update_status('No keys configured', 'red')
            if self.app:
                self.app.notify_overlay_status(f'{compact_driver_control_name(self.var_name)}: No keys', 'red')
            return
        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        success = False
        cancelled = False
        cleared = False
        active_target: Optional[int] = None
        try:
            while True:
                with self._target_lock:
                    pending_target = self._requested_target
                    pending_nonce = self._requested_nonce
                    cleared = self._clear_requested
                if cleared or pending_target is None:
                    break
                if self.app and self.app.app_state != 'RUNNING':
                    cancelled = True
                    break
                if self.app and (not self.app._commands_allowed()):
                    cancelled = True
                    break
                current = self._read_weight_jacker_int(samples=2)
                if current is None:
                    time.sleep(0.02)
                    continue
                active_target = int(round(float(pending_target)))
                diff = active_target - current
                pulses = abs(diff)
                if pulses == 0:
                    with self._target_lock:
                        if self._requested_nonce == pending_nonce:
                            self._requested_target = None
                    success = True
                    break
                burst_pulses = pulses
                if pulses > WEIGHT_JACKER_BURST_GUARD + 2:
                    burst_pulses = pulses - WEIGHT_JACKER_BURST_GUARD
                key = self.key_increase if diff > 0 else self.key_decrease
                if self.update_status:
                    self.update_status(f'Burst ({burst_pulses}+verify)', 'orange')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} burst -> {active_target}', 'orange')
                if not self._send_weight_jacker_pulses(key, burst_pulses, press_ms=WEIGHT_JACKER_BURST_PRESS_MS, interval_ms=WEIGHT_JACKER_BURST_INTERVAL_MS, nonce=pending_nonce):
                    continue
                time.sleep(WEIGHT_JACKER_VERIFY_SETTLE_S)
                for pass_idx in range(WEIGHT_JACKER_MAX_CORRECTION_PASSES):
                    if self._weight_jacker_request_changed(pending_nonce):
                        break
                    current = self._read_weight_jacker_int(samples=2)
                    if current is None:
                        time.sleep(0.02)
                        continue
                    remaining = active_target - current
                    correction_pulses = abs(remaining)
                    if correction_pulses == 0:
                        break
                    key = self.key_increase if remaining > 0 else self.key_decrease
                    press_ms = WEIGHT_JACKER_FINE_PRESS_MS if correction_pulses <= 2 else WEIGHT_JACKER_BURST_PRESS_MS
                    interval_ms = WEIGHT_JACKER_FINE_INTERVAL_MS if correction_pulses <= 2 else WEIGHT_JACKER_BURST_INTERVAL_MS
                    if self.update_status:
                        self.update_status(f'Correction {pass_idx + 1} ({correction_pulses})', 'orange')
                    if not self._send_weight_jacker_pulses(key, correction_pulses, press_ms=press_ms, interval_ms=interval_ms, nonce=pending_nonce):
                        break
                    time.sleep(WEIGHT_JACKER_VERIFY_SETTLE_S if pass_idx < WEIGHT_JACKER_MAX_CORRECTION_PASSES - 1 else 0.02)
                final_value = self._read_weight_jacker_int(samples=3)
                final_ok = final_value == active_target
                with self._target_lock:
                    same_request = self._requested_nonce == pending_nonce
                    if same_request and final_ok:
                        self._requested_target = None
                success = final_ok
                if same_request and final_ok:
                    break
                if same_request:
                    time.sleep(0.02)
                    continue
        except Exception as exc:
            print(f'[WeightJackerDirect] Exception: {exc}')
        finally:
            if success:
                if self.update_status:
                    self.update_status('Ready', 'green')
                if self.app and active_target is not None:
                    self.app.notify_overlay_status(f'{short_name} sent ({active_target})', 'green')
            elif cancelled:
                if self.update_status:
                    self.update_status('Cancelled', 'red')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} Cancelled', 'red')
            elif cleared:
                if self.update_status:
                    self.update_status('Ready', 'green')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} Cleared', 'orange')
            else:
                if self.update_status:
                    self.update_status('Failed', 'red')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} Failed', 'red')
            self.running_action = False

    def _run_target_loop(self):
        if self.var_name in WEIGHT_JACKER_VARS:
            self._run_weight_jacker_direct_loop()
            return
        if self.running_action:
            return
        if not self.key_increase or not self.key_decrease:
            if self.update_status:
                self.update_status('No keys configured', 'red')
            if self.app:
                self.app.notify_overlay_status(f'{compact_driver_control_name(self.var_name)}: No keys', 'red')
            return
        self.running_action = True
        short_name = compact_driver_control_name(self.var_name)
        active_request: Optional[float] = None
        active_target: Optional[float] = None
        active_nonce: Optional[int] = None
        direct_boundary = False
        timeout_deadline: Optional[float] = None
        cancelled = False
        cleared = False
        success = False
        last_diff: Optional[float] = None
        last_value: Optional[float] = None
        off_by_one_streak = 0
        last_pulse_report_time = 0.0
        last_pulse_report_remaining: Optional[int] = None
        timing_profile = _normalize_timing_config(GLOBAL_TIMING).get('profile', 'bot_safe')
        is_bot_profile = timing_profile in {'bot', 'bot_safe'}
        is_bot_safe = timing_profile == 'bot_safe'
        try:
            while True:
                with self._target_lock:
                    pending_target = self._requested_target
                    pending_nonce = self._requested_nonce
                    cleared = self._clear_requested
                    pending_direct_boundary = self._requested_direct_boundary
                if cleared or pending_target is None:
                    break
                if pending_nonce != active_nonce:
                    active_nonce = pending_nonce
                    active_request = pending_target
                    direct_boundary = pending_direct_boundary
                    if direct_boundary:
                        active_target = pending_target
                    else:
                        active_target = self._resolve_target(pending_target)
                    if not self.is_float:
                        active_target = int(round(active_target))
                    keep_trying = bool(self.app and self.app.keep_trying_targets.get())
                    timeout_deadline = None if keep_trying else time.time() + 8
                    if self.update_status:
                        self.update_status('Adjusting...', 'orange')
                    if self.app:
                        self.app.notify_overlay_status(f'Adjusting {short_name} -> {active_target}', 'orange')
                if self.app and self.app.app_state != 'RUNNING':
                    cancelled = True
                    break
                if self.app and (not self.app._commands_allowed()):
                    cancelled = True
                    break
                keep_trying = bool(self.app and self.app.keep_trying_targets.get())
                if keep_trying:
                    timeout_deadline = None
                elif timeout_deadline is None:
                    timeout_deadline = time.time() + 8
                if not keep_trying and timeout_deadline and (time.time() > timeout_deadline):
                    break
                current = self.read_telemetry()
                if current is None:
                    time.sleep(0.05)
                    continue
                if self.is_float and last_value is not None:
                    delta = abs(float(current) - float(last_value))
                    if delta >= 0.0001:
                        if self._float_step is None or delta < self._float_step:
                            self._float_step = round(delta, 6)
                if active_target is None:
                    time.sleep(0.05)
                    continue
                fast_boundary = direct_boundary and (is_bot_profile or self.var_name in WEIGHT_JACKER_VARS)
                if not direct_boundary and is_bot_profile:
                    if self.is_float:
                        base_step = self._float_step if self._float_step else 0.001
                        close_threshold = max(0.001, base_step)
                        if abs(active_target - current) <= close_threshold * 2:
                            current = self._read_stable_float(float(current), samples=3, delay=0.02 if is_bot_safe else 0.012)
                    elif abs(active_target - current) <= 1:
                        stable_delay = 0.03 if is_bot_safe else 0.015
                        current = self._read_stable_int(int(round(current)), samples=3, delay=stable_delay)
                diff = active_target - current
                abs_diff = abs(diff)
                overshot = last_diff is not None and diff != 0 and (diff > 0 > last_diff or diff < 0 < last_diff)
                if fast_boundary:
                    pulses_remaining = self._estimate_pulses_remaining(diff)
                    if pulses_remaining is not None:
                        now = time.time()
                        if now - last_pulse_report_time >= 0.25 or pulses_remaining != last_pulse_report_remaining:
                            boundary_press_ms, boundary_interval_ms = _boundary_pulse_timing_ms()
                            rate_hz = _pulse_rate_hz(boundary_press_ms, boundary_interval_ms)
                            status = f'Adjusting... ({pulses_remaining} pulses, ~{rate_hz:.1f}/s)'
                            if self.update_status:
                                self.update_status(status, 'orange')
                            last_pulse_report_time = now
                            last_pulse_report_remaining = pulses_remaining
                if self.is_float:
                    tolerance = 0.001
                    if self._float_step and self._float_step > 0:
                        tolerance = max(tolerance, self._float_step / 2.0)
                    if abs_diff <= tolerance:
                        success = True
                elif diff == 0:
                    success = True
                if success:
                    with self._target_lock:
                        if self._requested_target == active_request:
                            self._requested_target = None
                    break
                key = self.key_increase if diff > 0 else self.key_decrease
                if not direct_boundary and is_bot_profile and (not self.is_float):
                    if abs_diff == 1:
                        if last_diff is not None and diff == last_diff:
                            off_by_one_streak += 1
                        else:
                            off_by_one_streak = 1
                    else:
                        off_by_one_streak = 0
                    if off_by_one_streak >= 2:
                        if is_bot_safe:
                            _direct_pulse(key, press_ms=10, interval_ms=12)
                            _sleep_after_output(key, 0.09, 0.03)
                        else:
                            _direct_pulse(key, press_ms=4, interval_ms=4)
                            _sleep_after_output(key, 0.03, 0.008)
                        last_diff = diff
                        last_value = current
                        continue
                base_step = self._float_step if self._float_step else 0.001
                close_threshold = max(0.001, base_step) if self.is_float else 1.0
                near_target = abs_diff <= close_threshold * 2
                if fast_boundary:
                    boundary_press_ms, boundary_interval_ms = _boundary_pulse_timing_ms()
                    _direct_pulse(key, press_ms=boundary_press_ms, interval_ms=boundary_interval_ms)
                elif not direct_boundary and is_bot_profile:
                    if abs_diff <= close_threshold * 2 or overshot:
                        if is_bot_safe:
                            _direct_pulse(key, press_ms=8, interval_ms=10)
                            _sleep_after_output(key, 0.09 if near_target else 0.07, 0.025 if near_target else 0.012)
                        else:
                            _direct_pulse(key, press_ms=3, interval_ms=3)
                            _sleep_after_output(key, 0.04 if near_target else 0.025, 0.01 if near_target else 0.004)
                    else:
                        click_pulse(key, self.is_float)
                        if abs_diff <= close_threshold * 4:
                            _sleep_after_output(key, 0.035 if near_target else 0.02, 0.01 if near_target else 0.005)
                        else:
                            _sleep_after_output(key, 0.025 if is_bot_safe else 0.01, 0.006 if is_bot_safe else 0.003)
                else:
                    click_pulse(key, self.is_float)
                    _sleep_after_output(key, 0.02, 0.005)
                last_diff = diff
                last_value = current
        except Exception as exc:
            print(f'[GenericController] Exception: {exc}')
        finally:
            if success:
                message = f'{short_name} OK ({active_target})'
                if self.update_status:
                    self.update_status('Ready', 'green')
                if self.app:
                    self.app.notify_overlay_status(message, 'green')
            elif cancelled:
                if self.update_status:
                    self.update_status('Cancelled', 'red')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} Cancelled', 'red')
            elif cleared:
                if self.update_status:
                    self.update_status('Ready', 'green')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} Cleared', 'orange')
            else:
                if self.update_status:
                    self.update_status('Failed', 'red')
                if self.app:
                    self.app.notify_overlay_status(f'{short_name} Failed', 'red')
            self.running_action = False

    def find_minimum_effective_timing(self, start_ms: int=1, max_ms: int=120, step_ms: int=1, settle_s: float=0.05, confirmation_attempts: int=2) -> Optional[int]:
        """
        Probe the minimal pulse timing that reliably updates telemetry.

        The probe fires fast pulses starting at ``start_ms`` and increments by
        ``step_ms`` until telemetry reflects a change. The first timing that
        consistently registers is returned.

        Args:
            start_ms: Initial press/interval duration in milliseconds.
            max_ms: Maximum duration to test in milliseconds.
            step_ms: Increment between attempts in milliseconds.
            settle_s: Delay after a pulse to allow telemetry to settle.
            confirmation_attempts: Number of retries per timing bucket.

        Returns:
            Suggested minimal working pulse duration in milliseconds, or None
            if no timing within bounds registers.
        """
        if self.app and (not self.app._commands_allowed()):
            raise ValueError('Commands are blocked while the car is off track.')
        if not self.key_increase or not self.key_decrease:
            raise ValueError('Increase/decrease keys must be configured before probing.')
        baseline = self.read_telemetry()
        if baseline is None:
            return None

        def _changed(old, new) -> bool:
            if old is None or new is None:
                return False
            if self.is_float:
                return abs(float(new) - float(old)) >= 0.0005
            return int(round(new)) != int(round(old))

        def _restore(target_value: float, timing_ms: int):
            """Attempt to revert telemetry back near baseline after a test."""
            for _ in range(5):
                current = self.read_telemetry()
                if current is None:
                    break
                if not _changed(target_value, current):
                    break
                direction = self.key_decrease if current > target_value else self.key_increase
                _direct_pulse(direction, timing_ms, timing_ms)
                time.sleep(settle_s)
        for delay_ms in range(max(1, start_ms), max_ms + 1, max(1, step_ms)):
            success_count = 0
            for _ in range(max(1, confirmation_attempts)):
                _direct_pulse(self.key_increase, delay_ms, delay_ms)
                time.sleep(settle_s)
                updated = self.read_telemetry()
                if _changed(baseline, updated):
                    success_count += 1
                else:
                    break
            _restore(baseline, delay_ms)
            if success_count >= confirmation_attempts:
                return delay_ms
        return None

class ScanValidator:
    """Validate that scanned telemetry variables are returning values."""

    def __init__(self, ir_instance, controllers: Dict[str, 'GenericController']):
        self.ir = ir_instance
        self.controllers = controllers
        self.validation_attempts = 0
        self.max_attempts = 3
        self.last_validation_time = 0.0
        self.validation_cooldown = 2.0

    def validate_scan(self) -> Tuple[bool, str]:
        """Return (success, message) for scanned telemetry validation."""
        now = time.time()
        if now - self.last_validation_time < self.validation_cooldown:
            return (True, 'Validation cooldown')
        self.last_validation_time = now
        if not self.controllers:
            return (False, 'No controllers found')
        valid_count = 0
        invalid_controllers = []
        for var_name, controller in self.controllers.items():
            value = controller.read_telemetry()
            if value is not None:
                valid_count += 1
            else:
                invalid_controllers.append(var_name)
        success_rate = valid_count / len(self.controllers)
        if success_rate >= 0.5:
            return (True, f'Validation OK ({valid_count}/{len(self.controllers)} working)')
        msg = f"Validation FAILED ({valid_count}/{len(self.controllers)} working). Broken: {', '.join(invalid_controllers[:3])}"
        return (False, msg)

    def reset(self) -> None:
        """Reset validation counters."""
        self.validation_attempts = 0

class ControlTab(tk.Frame):
    """
    Configuration tab for a single control variable.
    """

    def __init__(self, parent, controller: GenericController, label_name: str, app, *, create_default_rows: bool=True):
        super().__init__(parent)
        self.app = app
        self.controller = controller
        self.label_name = label_name
        self.controller.update_status = self.update_status_label
        self.controller.app = app
        self.preset_rows: List[Dict[str, Any]] = []
        self.is_pit_limiter = self.controller.var_name == 'dcPitSpeedLimiterToggle'
        self.is_push_to_pass = self.controller.var_name == 'dcPushToPass'
        self.is_hybrid_hold = False
        self.is_weight_jacker = self.controller.var_name in WEIGHT_JACKER_VARS
        self.is_fuel_mixture = self.controller.var_name == FUEL_MIXTURE_VAR
        self.is_wiper_toggle = self.controller.var_name in WIPER_TOGGLE_VARS
        self.is_tearoff = self._is_tearoff_control()
        self.is_simple_boolean = self.controller.is_boolean and (not self.is_push_to_pass) and (not self.is_hybrid_hold)
        self.pit_limiter_auto = tk.BooleanVar(value=True)
        self.pit_limiter_humanize = tk.BooleanVar(value=True)
        self.pit_limiter_force_on = tk.BooleanVar(value=True)
        self.pit_limiter_configured = tk.BooleanVar(value=True)
        self.pit_limiter_delay_min = tk.StringVar(value='0.08')
        self.pit_limiter_delay_max = tk.StringVar(value='0.26')
        self.pit_limiter_poll_hz_min = tk.StringVar(value='7.0')
        self.pit_limiter_poll_hz_max = tk.StringVar(value='10.0')
        self.chk_pit_limiter_auto: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_humanize: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_force_on: Optional[tk.Checkbutton] = None
        self.chk_pit_limiter_configured: Optional[tk.Checkbutton] = None
        self.entry_pit_limiter_delay_min: Optional[tk.Entry] = None
        self.entry_pit_limiter_delay_max: Optional[tk.Entry] = None
        self.entry_pit_limiter_poll_hz_min: Optional[tk.Entry] = None
        self.entry_pit_limiter_poll_hz_max: Optional[tk.Entry] = None
        self.pit_limiter_frame: Optional[tk.LabelFrame] = None
        self.p2p_chain_delay_min = tk.StringVar(value='0.12')
        self.p2p_chain_delay_max = tk.StringVar(value='0.25')
        self.entry_p2p_chain_delay_min: Optional[tk.Entry] = None
        self.entry_p2p_chain_delay_max: Optional[tk.Entry] = None
        self.fuel_yellow_enabled = tk.BooleanVar(value=True)
        self.fuel_green_enabled = tk.BooleanVar(value=True)
        self.fuel_yellow_delay_min = tk.StringVar(value='1.00')
        self.fuel_yellow_delay_max = tk.StringVar(value='2.00')
        self.fuel_green_delay_min = tk.StringVar(value='0.25')
        self.fuel_green_delay_max = tk.StringVar(value='0.70')
        self.fuel_mixture_frame: Optional[tk.LabelFrame] = None
        self.chk_fuel_yellow_enabled: Optional[tk.Checkbutton] = None
        self.chk_fuel_green_enabled: Optional[tk.Checkbutton] = None
        self.entry_fuel_yellow_delay_min: Optional[tk.Entry] = None
        self.entry_fuel_yellow_delay_max: Optional[tk.Entry] = None
        self.entry_fuel_green_delay_min: Optional[tk.Entry] = None
        self.entry_fuel_green_delay_max: Optional[tk.Entry] = None
        self.hybrid_hold_enabled = tk.BooleanVar(value=True)
        if self.controller.var_name == HYBRID_BOOST_HOLD_VAR:
            self.hybrid_stop_soc_min = tk.StringVar(value='0.000')
            self.hybrid_stop_soc_max = tk.StringVar(value='0.020')
        else:
            self.hybrid_stop_soc_min = tk.StringVar(value='0.990')
            self.hybrid_stop_soc_max = tk.StringVar(value='1.000')
        self.hybrid_max_hold_s = tk.StringVar(value='12.0')
        self.hybrid_frame: Optional[tk.LabelFrame] = None
        self.chk_hybrid_hold_enabled: Optional[tk.Checkbutton] = None
        self.entry_hybrid_stop_soc_min: Optional[tk.Entry] = None
        self.entry_hybrid_stop_soc_max: Optional[tk.Entry] = None
        self.entry_hybrid_max_hold_s: Optional[tk.Entry] = None
        self.wiper_auto = tk.BooleanVar(value=True)
        self.wiper_precip_on = tk.StringVar(value='0.04')
        self.wiper_precip_off = tk.StringVar(value='0.03')
        self.wiper_humanize_delay_min = tk.StringVar(value='0.0')
        self.wiper_humanize_delay_max = tk.StringVar(value='0.0')
        self.chk_wiper_auto: Optional[tk.Checkbutton] = None
        self.entry_wiper_precip_on: Optional[tk.Entry] = None
        self.entry_wiper_precip_off: Optional[tk.Entry] = None
        self.entry_wiper_delay_min: Optional[tk.Entry] = None
        self.entry_wiper_delay_max: Optional[tk.Entry] = None
        self.wiper_frame: Optional[tk.LabelFrame] = None
        self.lap_trigger_enabled = tk.BooleanVar(value=True)
        self.lap_trigger_interval = tk.StringVar(value='1')
        self.lap_trigger_count = tk.StringVar(value='1')
        self.lap_trigger_frame: Optional[tk.LabelFrame] = None
        self.chk_lap_trigger_enabled: Optional[tk.Checkbutton] = None
        self.entry_lap_trigger_interval: Optional[tk.Entry] = None
        self.entry_lap_trigger_count: Optional[tk.Entry] = None
        self.boolean_pulse_only = tk.BooleanVar(value=False)
        self.boolean_pulse_double = tk.BooleanVar(value=False)
        self.chk_boolean_pulse: Optional[tk.Checkbutton] = None
        self.chk_boolean_pulse_double: Optional[tk.Checkbutton] = None
        self.manual_pulse_frame: Optional[tk.LabelFrame] = None
        self.btn_manual_increase_bind: Optional[tk.Button] = None
        self.btn_manual_decrease_bind: Optional[tk.Button] = None
        self.manual_increase_bind: Optional[str] = None
        self.manual_decrease_bind: Optional[str] = None
        self._manual_increase_source_id = f'control-manual:{id(self)}:increase'
        self._manual_decrease_source_id = f'control-manual:{id(self)}:decrease'
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill='both', expand=True)
        body = scroll_frame.inner
        self.keys_frame = tk.LabelFrame(body, text=f'Keys ({label_name})', padx=5, pady=5)
        self.keys_frame.pack(fill='x', padx=5, pady=5)
        self.btn_increase = tk.Button(self.keys_frame, text='Set Increase (+)', command=lambda: self.bind_game_key('increase'))
        self.btn_decrease = tk.Button(self.keys_frame, text='Set Decrease (-)', command=lambda: self.bind_game_key('decrease'))
        self.uses_toggle_key = self.controller.is_boolean and (not self.controller.allow_dual_keys)
        if self.is_pit_limiter:
            self.uses_toggle_key = True
        if self.uses_toggle_key:
            self.btn_increase.config(text='Set Hold' if self.is_hybrid_hold else 'Set Toggle')
            self.btn_increase.pack(side='left', expand=True, fill='x', padx=2)
            self.btn_decrease.config(text='Not used', state='disabled')
        else:
            self.btn_increase.pack(side='left', expand=True, fill='x', padx=2)
            self.btn_decrease.pack(side='left', expand=True, fill='x', padx=2)
        if self.is_simple_boolean:
            self.chk_boolean_pulse = tk.Checkbutton(body, text='One-shot macro (press once, skip telemetry check)', variable=self.boolean_pulse_only, command=self._on_boolean_pulse_toggle)
            self.chk_boolean_pulse.pack(anchor='w', padx=10, pady=(0, 6))
            self.chk_boolean_pulse_double = tk.Checkbutton(body, text='Double-tap when one-shot is enabled (press on then off)', variable=self.boolean_pulse_double, command=self._on_boolean_pulse_double_toggle)
            self.chk_boolean_pulse_double.pack(anchor='w', padx=26, pady=(0, 6))
            self._update_boolean_pulse_double_state(editing=self.app.app_state == 'CONFIG')
        self.manual_pulse_frame = tk.LabelFrame(body, text='Ghost Hotkeys', padx=5, pady=5)
        self.manual_pulse_frame.pack(fill='x', padx=5, pady=(0, 5))
        tk.Label(self.manual_pulse_frame, text='Bind a joystick or keyboard hotkey here to send one pulse of the configured game key.', fg='gray', font=('Arial', 8), wraplength=760, justify='left').pack(anchor='w', pady=(0, 4))
        manual_bind_row = tk.Frame(self.manual_pulse_frame)
        manual_bind_row.pack(fill='x')
        self.btn_manual_increase_bind = tk.Button(manual_bind_row, text='Set Ghost +', command=lambda: self.bind_manual_pulse_hotkey('increase'))
        self.btn_manual_decrease_bind = tk.Button(manual_bind_row, text='Set Ghost -', command=lambda: self.bind_manual_pulse_hotkey('decrease'))
        if self.uses_toggle_key:
            self.btn_manual_increase_bind.config(text='Set Ghost Toggle')
            self.btn_manual_decrease_bind.config(text='Not used', state='disabled')
            self.btn_manual_increase_bind.pack(side='left', expand=True, fill='x', padx=2)
        else:
            self.btn_manual_increase_bind.pack(side='left', expand=True, fill='x', padx=2)
            self.btn_manual_decrease_bind.pack(side='left', expand=True, fill='x', padx=2)
        self._refresh_manual_pulse_bind_button('increase')
        self._refresh_manual_pulse_bind_button('decrease')
        self.lbl_monitor = tk.Label(body, text='Value: --', font=('Arial', 14, 'bold'))
        self.lbl_monitor.pack(pady=5)
        self.lbl_status = tk.Label(body, text='Idle', fg='gray')
        self.lbl_status.pack()
        self.presets_frame = tk.LabelFrame(body, text='Presets / Macros', padx=5, pady=5)
        self.presets_frame.pack(fill='both', expand=True, padx=5, pady=5)
        header = tk.Frame(self.presets_frame)
        header.pack(fill='x', padx=2, pady=(0, 2))
        self._configure_macro_grid(header)
        columns = self._macro_column_map()
        tk.Label(header, text='Macro value', anchor='w', font=('Arial', 8, 'bold')).grid(row=0, column=columns['value'], sticky='w', padx=5)
        if self.is_weight_jacker:
            tk.Label(header, text='Lap', anchor='w', font=('Arial', 8, 'bold')).grid(row=0, column=columns['lap'], sticky='w', padx=5)
        tk.Label(header, text='Keybinding', anchor='w', font=('Arial', 8, 'bold')).grid(row=0, column=columns['bind'], sticky='w', padx=5)
        self.presets_container = tk.Frame(self.presets_frame)
        self.presets_container.pack(fill='both', expand=True)
        self.btn_add_preset_row = tk.Button(self.presets_frame, text='Add Row (+)', command=self.add_preset_row, bg='#f0f0f0')
        self.btn_add_preset_row.pack(fill='x', padx=2, pady=(4, 0))
        self.p2p_frame = None
        if create_default_rows:
            for _ in range(4):
                self.add_preset_row()
        self.running = True
        self.after(500, self.monitor_loop)

    def set_discreet_mode(self, enabled: bool) -> None:
        """Show or hide automation-focused UI elements."""
        self._toggle_pack_widget(self.keys_frame, not enabled)
        self._toggle_pack_widget(self.manual_pulse_frame, not enabled)
        self._toggle_pack_widget(self.presets_frame, not enabled)
        self._toggle_pack_widget(self.p2p_frame, not enabled)
        self._toggle_pack_widget(self.fuel_mixture_frame, not enabled)
        self._toggle_pack_widget(self.hybrid_frame, not enabled)
        for row in self.preset_rows:
            self._apply_discreet_mode_to_row(row, enabled)

    def _apply_discreet_mode_to_row(self, row_data: Dict[str, Any], enabled: bool) -> None:
        pass

    def _macro_grid_columns(self) -> List[Tuple[str, int]]:
        columns: List[Tuple[str, int]] = [('value', 86)]
        if self.is_weight_jacker:
            columns.append(('lap', 62))
        columns.append(('bind', 126))
        columns.append(('delete', 34))
        return columns

    def _macro_column_map(self) -> Dict[str, int]:
        return {name: index for index, (name, _width) in enumerate(self._macro_grid_columns())}

    def _configure_macro_grid(self, frame: tk.Frame) -> None:
        columns = self._macro_grid_columns()
        for index, (_name, width) in enumerate(columns):
            frame.grid_columnconfigure(index, minsize=width, weight=0)
        frame.grid_columnconfigure(len(columns), weight=1)

    def _toggle_pack_widget(self, widget: Optional[tk.Widget], show: bool) -> None:
        if widget is None:
            return
        if show:
            if widget.winfo_manager() in {'pack', 'grid'}:
                return
            info = getattr(widget, '_pack_info', None)
            if info:
                widget.pack(**info)
                return
            info = getattr(widget, '_grid_info', None)
            if info:
                widget.grid(**info)
        else:
            manager = widget.winfo_manager()
            if manager == 'pack':
                info = widget.pack_info()
                if 'in' in info:
                    info['in_'] = info.pop('in')
                widget._pack_info = info
                widget.pack_forget()
            elif manager == 'grid':
                info = widget.grid_info()
                if 'in' in info:
                    info['in_'] = info.pop('in')
                widget._grid_info = info
                widget.grid_remove()

    def update_status_label(self, text: str, color: str):
        """Update status label."""
        if not self.app:
            return

        def _apply():
            try:
                if not self.lbl_status.winfo_exists():
                    return
                self.lbl_status.config(text=text, fg=color)
            except Exception:
                pass
        self.app.ui(_apply)

    def _bind_autosave_entry(self, entry: tk.Entry) -> None:
        """Attach auto-save handlers to entries."""
        entry.bind('<KeyRelease>', lambda _event: self.app.schedule_preset_save())
        entry.bind('<FocusOut>', lambda _event: self.app.schedule_preset_save())

    def run_bot_timing_probe(self):
        """Run a fast timing probe to suggest a stable BOT delay."""

        def _worker():
            try:
                suggested = self.controller.find_minimum_effective_timing()
            except ValueError as exc:
                error_msg = str(exc)
                self.after(0, lambda msg=error_msg: self.app._show_error('Keys Missing', msg))
                return
            if suggested is None:
                self.after(0, lambda: self.app._show_warning('Probe Result', 'No timing within 1-120 ms reliably updated telemetry.'))
            else:
                msg = f'Minimal stable pulse detected at ~{suggested} ms.\nApply this value to BOT/custom timings for reliable updates.'
                self.after(0, lambda: self.app._show_info('Probe Result', msg))
        threading.Thread(target=_worker, daemon=True).start()

    def _test_single_game_input(self, direction: str) -> None:
        """Fire one increase/decrease pulse for diagnostics."""
        binding = self.controller.key_increase if direction == 'increase' else self.controller.key_decrease
        label = 'Increase' if direction == 'increase' else 'Decrease'
        if binding is None:
            self.app._show_warning('Key Missing', f'{label} is not configured for this tab.')
            return

        def _worker():
            try:
                click_pulse(binding, self.controller.is_float)
                self.after(0, lambda: self.update_status_label(f'Tested {label}', 'green'))
            except Exception as exc:
                error_msg = str(exc)
                self.after(0, lambda msg=error_msg: self.app._show_warning('Test Failed', msg or f'Unable to test {label}.'))
        threading.Thread(target=_worker, daemon=True).start()

    def _manual_pulse_bind_default_text(self, direction: str) -> str:
        """Return the default label used by a ghost hotkey button."""
        if direction == 'increase':
            return 'Set Ghost Toggle' if self.uses_toggle_key else 'Set Ghost +'
        if self.uses_toggle_key:
            return 'Not used'
        return 'Set Ghost -'

    def _manual_pulse_bind_button(self, direction: str) -> Optional[tk.Button]:
        """Return the UI button associated with a ghost hotkey."""
        return self.btn_manual_increase_bind if direction == 'increase' else self.btn_manual_decrease_bind

    def _manual_pulse_bind_source_id(self, direction: str) -> str:
        """Return the conflict-detection source ID for a ghost hotkey."""
        return self._manual_increase_source_id if direction == 'increase' else self._manual_decrease_source_id

    def _manual_pulse_bind_value(self, direction: str) -> Optional[str]:
        """Return the configured ghost hotkey for the requested direction."""
        return self.manual_increase_bind if direction == 'increase' else self.manual_decrease_bind

    def _set_manual_pulse_bind_value(self, direction: str, code: Optional[str]) -> None:
        """Persist a ghost hotkey binding for one direction."""
        if direction == 'increase':
            self.manual_increase_bind = code
            if self.uses_toggle_key:
                self.manual_decrease_bind = None
        else:
            self.manual_decrease_bind = None if self.uses_toggle_key else code

    def _refresh_manual_pulse_bind_button(self, direction: str) -> None:
        """Refresh the ghost hotkey button label/color."""
        button = self._manual_pulse_bind_button(direction)
        if not button:
            return
        if direction == 'decrease' and self.uses_toggle_key:
            button.config(text='Not used', bg='#f0f0f0', state='disabled')
            return
        code = self._manual_pulse_bind_value(direction)
        if code:
            bg_color = '#90ee90' if 'JOY' in code else '#ADD8E6'
            button.config(text=code, bg=bg_color)
        else:
            button.config(text=self._manual_pulse_bind_default_text(direction), bg='#f0f0f0')

    def bind_manual_pulse_hotkey(self, direction: str) -> None:
        """Bind a joystick/keyboard hotkey that sends one game-key pulse."""
        if direction == 'decrease' and self.uses_toggle_key:
            return
        if self.app.app_state != 'CONFIG':
            self.app._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.app.focus_window()
        button = self._manual_pulse_bind_button(direction)
        if not button:
            return
        previous_bind = self._manual_pulse_bind_value(direction)
        button.config(text='...', bg='yellow')
        self.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self.app._find_hotkey_conflict(code, self._manual_pulse_bind_source_id(direction))
            if conflict:
                self.app._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self._set_manual_pulse_bind_value(direction, previous_bind)
                self._refresh_manual_pulse_bind_button(direction)
                return
            self._set_manual_pulse_bind_value(direction, code)
        elif code == 'CANCEL':
            self._set_manual_pulse_bind_value(direction, None)
        else:
            self._set_manual_pulse_bind_value(direction, previous_bind)
        self._refresh_manual_pulse_bind_button(direction)
        self.app.schedule_preset_save()

    def trigger_manual_pulse_hotkey(self, direction: str) -> None:
        """Send one pulse of the configured game key for a ghost hotkey."""
        binding = self.controller.key_increase if direction == 'increase' else self.controller.key_decrease
        label = 'toggle' if self.uses_toggle_key else direction
        if binding is None:
            self.update_status_label('No key configured', 'red')
            if self.app:
                self.app.notify_overlay_status(f'{self.label_name}: no {label} key configured', 'red')
            return
        if self.app and (not self.app._commands_allowed()):
            return
        try:
            click_pulse(binding, self.controller.is_float)
        except Exception as exc:
            print(f'[ControlTab] Ghost hotkey failed ({label}): {exc}')
            self.update_status_label('Ghost hotkey failed', 'red')

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = 'normal' if enabled else 'readonly'
        button_state = 'normal' if enabled else 'disabled'
        for row in self.preset_rows:
            try:
                if row.get('entry'):
                    row['entry'].config(state=state)
                if row.get('lap_number_entry'):
                    row['lap_number_entry'].config(state=state)
                delete_button = row.get('delete_button')
                if delete_button:
                    delete_button.config(state=button_state)
            except Exception:
                pass
        if self.btn_add_preset_row:
            self.btn_add_preset_row.config(state=button_state)
        if self.btn_manual_increase_bind:
            self.btn_manual_increase_bind.config(state=button_state)
        if self.btn_manual_decrease_bind:
            decrease_state = 'disabled' if self.uses_toggle_key else button_state
            self.btn_manual_decrease_bind.config(state=decrease_state)
        if self.is_simple_boolean:
            if self.chk_boolean_pulse:
                self.chk_boolean_pulse.config(state=button_state)
            self._update_boolean_pulse_double_state(editing=enabled)

    def bind_game_key(self, direction: str):
        """
        Bind a game key for increase/decrease.
        
        Args:
            direction: "increase" or "decrease"
        """
        if self.app.app_state != 'CONFIG':
            self.app._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.app.focus_window()
        btn = self.btn_increase if direction == 'increase' else self.btn_decrease
        if direction == 'increase':
            default_text = 'Set Hold' if self.is_hybrid_hold else 'Set Toggle' if self.uses_toggle_key else 'Set Increase (+)'
        else:
            default_text = 'Set Decrease (-)'
        original_text = btn['text']
        btn.config(text='PRESS KEYBOARD...', bg='yellow')
        self.update_idletasks()
        try:
            binding, key_name = input_manager.capture_game_action_binding()
        except RuntimeError as exc:
            btn.config(text=original_text, bg='#f0f0f0')
            self.app._show_warning('Joystick output unavailable', str(exc))
            return
        if key_name == 'CANCEL':
            if direction == 'increase':
                self.controller.key_increase = None
                if self.is_pit_limiter and self.uses_toggle_key:
                    self.controller.key_decrease = None
            else:
                self.controller.key_decrease = None
            btn.config(text=default_text, bg='#f0f0f0')
        elif binding is not None:
            if direction == 'increase':
                self.controller.key_increase = binding
                if self.is_pit_limiter and self.uses_toggle_key:
                    self.controller.key_decrease = binding
            else:
                self.controller.key_decrease = binding
            bg_color = '#90ee90' if isinstance(binding, str) else '#ADD8E6'
            btn.config(text=f'OK: {str(key_name).upper()}', bg=bg_color)
        else:
            btn.config(text=original_text, bg='#f0f0f0')
        self.app.schedule_preset_save()

    def _on_boolean_pulse_toggle(self) -> None:
        """Persist boolean one-shot toggle settings."""
        self.controller.boolean_pulse_only = bool(self.boolean_pulse_only.get())
        if not self.boolean_pulse_only.get():
            self.boolean_pulse_double.set(False)
            self.controller.boolean_pulse_double = False
        self._update_boolean_pulse_double_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_boolean_pulse_double_toggle(self) -> None:
        """Persist boolean double-tap settings."""
        self.controller.boolean_pulse_double = bool(self.boolean_pulse_double.get())
        self.app.schedule_preset_save()

    def _update_boolean_pulse_double_state(self, editing: bool=True) -> None:
        """Enable double-tap option only when one-shot is enabled."""
        if not self.chk_boolean_pulse_double:
            return
        if not editing:
            state = 'disabled'
        else:
            state = 'normal' if self.boolean_pulse_only.get() else 'disabled'
        self.chk_boolean_pulse_double.config(state=state)
        if not self.boolean_pulse_only.get():
            self.boolean_pulse_double.set(False)
            self.controller.boolean_pulse_double = False

    def _config_bind_button(self, button: tk.Button, data_store: Dict[str, Any]):
        """Configure binding button behavior."""

        def on_click():
            if self.app.app_state != 'CONFIG':
                self.app._show_info('Notice', 'Enter CONFIG mode first.')
                return
            self.app.focus_window()
            default_text = 'Set Bind'
            existing_bind = data_store.get('bind')
            button.config(text='...', bg='yellow')
            self.update_idletasks()
            code = input_manager.capture_any_input()
            if code and code != 'CANCEL':
                conflict = self.app._find_hotkey_conflict(code, data_store.get('source_id'))
                if conflict:
                    self.app._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                    if existing_bind:
                        bg_color = '#90ee90' if 'JOY' in existing_bind else '#ADD8E6'
                        button.config(text=existing_bind, bg=bg_color)
                    else:
                        button.config(text=default_text, bg='#f0f0f0')
                    return
                data_store['bind'] = code
                bg_color = '#90ee90' if 'JOY' in code else '#ADD8E6'
                button.config(text=code, bg=bg_color)
            elif code == 'CANCEL':
                data_store['bind'] = None
                button.config(text=default_text, bg='#f0f0f0')
            self.app.schedule_preset_save()
        button.config(command=on_click)

    def add_preset_row(self, existing: Optional[Dict[str, Any]]=None, is_reset: bool=False, pack_row: bool=True):
        """Add a preset row to the UI."""
        is_reset = False
        frame = tk.Frame(self.presets_container)
        if pack_row:
            frame.pack(fill='x', pady=2)
        self._configure_macro_grid(frame)
        columns = self._macro_column_map()
        row_index = len(self.preset_rows)
        value_entry = None
        value_var = None
        if self.is_tearoff or self.is_pit_limiter or self.is_hybrid_hold:
            value_var = tk.StringVar(value='ON')
            value_entry = ttk.Combobox(frame, width=8, textvariable=value_var, values=['ON', 'OFF'], state='readonly')
            value_entry.grid(row=0, column=columns['value'], sticky='ew', padx=5)
            value_entry.bind('<<ComboboxSelected>>', lambda _event: self.app.schedule_preset_save())
            if self.app.app_state != 'CONFIG':
                value_entry.config(state='disabled')
        else:
            value_entry = ttk.Entry(frame, width=8)
            value_entry.grid(row=0, column=columns['value'], sticky='ew', padx=5)
            self._bind_autosave_entry(value_entry)
            if self.app.app_state != 'CONFIG':
                value_entry.config(state='readonly')
        lap_number_entry = None
        if self.is_weight_jacker:
            lap_number_entry = ttk.Entry(frame, width=6)
            lap_number_entry.grid(row=0, column=columns['lap'], sticky='ew', padx=5)
            self._bind_autosave_entry(lap_number_entry)
            if self.app.app_state != 'CONFIG':
                lap_number_entry.config(state='readonly')
        p2p_chain_entry = None
        bind_button = tk.Button(frame, text='Set Bind', width=12)
        bind_button.grid(row=0, column=columns['bind'], sticky='ew', padx=5)
        row_data = {'frame': frame, 'entry': value_entry, 'value_var': value_var, 'lap_number_entry': lap_number_entry, 'bind': None, 'is_reset': is_reset, 'delete_button': None, 'source_id': f'control:{id(frame)}', 'p2p_chain_entry': p2p_chain_entry}
        self._config_bind_button(bind_button, row_data)
        if existing:
            entry_state = 'normal'
            if self.app.app_state != 'CONFIG':
                entry_state = 'disabled' if self.is_tearoff or self.is_pit_limiter or self.is_hybrid_hold else 'readonly'
            value_entry.config(state='readonly' if self.is_tearoff or self.is_pit_limiter or self.is_hybrid_hold else 'normal')
            if self.is_tearoff or self.is_pit_limiter or self.is_hybrid_hold:
                entry_value = self._tearoff_label_for_value(existing.get('val', ''))
                value_entry.set(entry_value)
            else:
                value_entry.delete(0, tk.END)
                value_entry.insert(0, existing.get('val', ''))
            if self.app.app_state != 'CONFIG':
                value_entry.config(state=entry_state)
            if lap_number_entry is not None:
                lap_number_entry.config(state='normal')
                lap_number_entry.delete(0, tk.END)
                lap_number_entry.insert(0, existing.get('lap_number', existing.get('lap', '')))
                if self.app.app_state != 'CONFIG':
                    lap_number_entry.config(state='readonly')
            row_data['bind'] = existing.get('bind')
            if row_data['bind'] and bind_button:
                bg_color = '#90ee90' if 'JOY' in row_data['bind'] else '#ADD8E6'
                bind_button.config(text=row_data['bind'], bg=bg_color)
        delete_button = tk.Button(frame, text='X', fg='red', width=2, command=lambda r=row_data: self.remove_row(r))
        delete_button.grid(row=0, column=columns['delete'], sticky='w', padx=5)
        if self.app.app_state != 'CONFIG':
            delete_button.config(state='disabled')
        row_data['delete_button'] = delete_button
        self.preset_rows.append(row_data)
        if self.app.discreet_mode.get():
            self._apply_discreet_mode_to_row(row_data, True)

    def remove_row(self, row_data: Dict[str, Any]):
        """Remove a preset row."""
        if self.app.app_state != 'CONFIG':
            self.app._show_info('Notice', 'Enter CONFIG mode first.')
            return
        row_data['frame'].destroy()
        if row_data in self.preset_rows:
            self.preset_rows.remove(row_data)
        self._refresh_pit_limiter_indices()
        self.app.schedule_preset_save()

    def monitor_loop(self):
        """Background loop to monitor current value."""
        if not self.running:
            return
        value = self.controller.read_telemetry()
        if value is None:
            text = '--'
        else:
            text = f'{value:.3f}' if self.controller.is_float else str(value)
        try:
            self.lbl_monitor.config(text=f'Current: {text}')
        except Exception:
            pass
        if self.running:
            self.after(500, self.monitor_loop)

    @staticmethod
    def _safe_entry_value(widget: Optional[tk.Widget]) -> str:
        """Return entry text safely even if widget was destroyed."""
        if widget is None:
            return ''
        try:
            if hasattr(widget, 'winfo_exists') and (not widget.winfo_exists()):
                return ''
            return widget.get()
        except Exception:
            return ''

    @staticmethod
    def _safe_widget_text(widget: Optional[tk.Widget], default: str='') -> str:
        """Return widget text safely even if widget was destroyed."""
        if widget is None:
            return default
        try:
            if hasattr(widget, 'winfo_exists') and (not widget.winfo_exists()):
                return default
            return widget.cget('text')
        except Exception:
            return default

    def _is_tearoff_control(self) -> bool:
        """Return True when this control is a tear-off visor toggle."""
        name = (self.controller.var_name or '').lower()
        return 'tearoff' in name or 'tear_off' in name

    @staticmethod
    def _tearoff_label_for_value(value: Any) -> str:
        """Normalize tear-off macro values to ON/OFF labels."""
        if value is None:
            return 'OFF'
        text = str(value).strip().lower()
        if text in {'1', 'on', 'true', 'yes'}:
            return 'ON'
        if text in {'0', 'off', 'false', 'no', ''}:
            return 'OFF'
        try:
            numeric = float(text)
            return 'ON' if numeric > 0 else 'OFF'
        except Exception:
            return 'OFF'

    @staticmethod
    def _tearoff_value_for_label(value: str) -> str:
        """Convert ON/OFF labels into stored tear-off values."""
        return '1' if str(value).strip().upper() == 'ON' else '0'

    def _preset_value_for_row(self, row: Dict[str, Any]) -> str:
        """Return normalized preset value for storage."""
        value = self._safe_entry_value(row.get('entry'))
        if self.is_tearoff or self.is_pit_limiter or self.is_hybrid_hold:
            return self._tearoff_value_for_label(value)
        return value

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        decrease_key = self.controller.key_decrease
        decrease_text = self._safe_widget_text(self.btn_decrease, '')
        if self.uses_toggle_key:
            decrease_key = None
            decrease_text = 'Not used'
        config = {'meta_var': self.controller.var_name, 'meta_float': self.controller.is_float, 'key_increase': self.controller.key_increase, 'key_increase_text': self._safe_widget_text(self.btn_increase, ''), 'key_decrease': decrease_key, 'key_decrease_text': decrease_text, 'ghost_increase_bind': self.manual_increase_bind, 'ghost_decrease_bind': None if self.uses_toggle_key else self.manual_decrease_bind, 'bool_pulse_only': self.boolean_pulse_only.get() if self.is_simple_boolean else False, 'bool_pulse_double': self.boolean_pulse_double.get() if self.is_simple_boolean else False}
        config['presets'] = [
            {
                'val': self._preset_value_for_row(row),
                'lap_number': self._safe_entry_value(row.get('lap_number_entry')).strip(),
                'bind': row['bind']
            }
            for row in self.preset_rows
        ]
        return config

    def apply_key_config(self, config: Dict[str, Any]) -> None:
        """Apply only increase/decrease key settings to the tab."""
        if not config:
            return
        if 'key_increase' in config:
            increase_key = config.get('key_increase')
            self.controller.key_increase = _normalize_game_input_binding(increase_key)
            self.btn_increase.config(text=config.get('key_increase_text', 'Set Hold' if self.is_hybrid_hold else 'Set Toggle' if self.uses_toggle_key else 'Set Increase (+)'))
        if 'key_decrease' in config and (not self.uses_toggle_key):
            decrease_key = config.get('key_decrease')
            self.controller.key_decrease = _normalize_game_input_binding(decrease_key)
            self.btn_decrease.config(text=config.get('key_decrease_text', 'Set Decrease (-)'))
        elif self.uses_toggle_key:
            self.controller.key_decrease = None
            self.btn_decrease.config(text='Not used', state='disabled')
            if self.is_pit_limiter and self.controller.key_increase is not None:
                self.controller.key_decrease = self.controller.key_increase
        if 'ghost_increase_bind' in config:
            self.manual_increase_bind = config.get('ghost_increase_bind')
        if self.uses_toggle_key:
            self.manual_decrease_bind = None
        elif 'ghost_decrease_bind' in config:
            self.manual_decrease_bind = config.get('ghost_decrease_bind')
        if 'ghost_increase_bind' in config or 'ghost_decrease_bind' in config:
            self._refresh_manual_pulse_bind_button('increase')
            self._refresh_manual_pulse_bind_button('decrease')

    def destroy(self):
        """Ensure monitoring loop stops when widget is destroyed."""
        self.running = False
        super().destroy()

    def set_config(self, config: Dict[str, Any]):
        """Load configuration."""
        if not config:
            return
        increase_key = config.get('key_increase')
        decrease_key = config.get('key_decrease')
        self.controller.key_increase = _normalize_game_input_binding(increase_key)
        if not self.uses_toggle_key:
            self.controller.key_decrease = _normalize_game_input_binding(decrease_key)
        else:
            self.controller.key_decrease = None
            if self.is_pit_limiter and self.controller.key_increase is not None:
                self.controller.key_decrease = self.controller.key_increase
        self.btn_increase.config(text=config.get('key_increase_text', 'Set Hold' if self.is_hybrid_hold else 'Set Toggle' if self.uses_toggle_key else 'Set Increase (+)'))
        if not self.uses_toggle_key:
            self.btn_decrease.config(text=config.get('key_decrease_text', 'Set Decrease (-)'))
        else:
            self.btn_decrease.config(text='Not used', state='disabled')
        self.manual_increase_bind = config.get('ghost_increase_bind')
        self.manual_decrease_bind = None if self.uses_toggle_key else config.get('ghost_decrease_bind')
        self._refresh_manual_pulse_bind_button('increase')
        self._refresh_manual_pulse_bind_button('decrease')
        if self.is_simple_boolean:
            pulse_only = bool(config.get('bool_pulse_only', False))
            self.boolean_pulse_only.set(pulse_only)
            self.controller.boolean_pulse_only = pulse_only
            pulse_double = bool(config.get('bool_pulse_double', False))
            self.boolean_pulse_double.set(pulse_double)
            self.controller.boolean_pulse_double = pulse_double
            self._update_boolean_pulse_double_state(editing=self.app.app_state == 'CONFIG')
        else:
            self.boolean_pulse_only.set(False)
            self.boolean_pulse_double.set(False)
            self.controller.boolean_pulse_only = False
            self.controller.boolean_pulse_double = False
        for row in list(self.preset_rows):
            row['frame'].destroy()
        self.preset_rows.clear()
        saved_presets = [preset for preset in config.get('presets', []) if not preset.get('is_reset')]
        bulk_load = len(saved_presets) > 20
        for preset in saved_presets:
            self.add_preset_row(existing=preset, is_reset=False, pack_row=not bulk_load)
        if not self.preset_rows:
            for _ in range(4):
                self.add_preset_row(pack_row=not bulk_load)
        if bulk_load:
            for row in self.preset_rows:
                frame = row.get('frame')
                if frame and frame.winfo_manager() != 'pack':
                    frame.pack(fill='x', pady=2)
        self._refresh_pit_limiter_indices()

    def _refresh_pit_limiter_indices(self) -> None:
        """No-op now that pit limiter targets derive from macro values."""
        return

    def _apply_pit_limiter_config(self, config: Dict[str, Any]) -> None:
        """Apply pit limiter automation settings from config."""
        if not self.is_pit_limiter:
            return
        pit_cfg = config.get('pit_limiter', {}) if isinstance(config, dict) else {}
        self.pit_limiter_auto.set(pit_cfg.get('auto_toggle', True))
        self.pit_limiter_humanize.set(pit_cfg.get('humanize', True))
        self.pit_limiter_force_on.set(pit_cfg.get('force_on', True))
        self.pit_limiter_configured.set(pit_cfg.get('configured', True))
        self.pit_limiter_delay_min.set(str(pit_cfg.get('delay_min', '0.08')))
        self.pit_limiter_delay_max.set(str(pit_cfg.get('delay_max', '0.26')))
        self.pit_limiter_poll_hz_min.set(str(pit_cfg.get('poll_hz_min', '7.0')))
        self.pit_limiter_poll_hz_max.set(str(pit_cfg.get('poll_hz_max', '10.0')))

    def _apply_p2p_chain_config(self, config: Dict[str, Any]) -> None:
        """Apply push-to-pass chaining settings from config."""
        if not self.is_push_to_pass:
            return
        p2p_cfg = config.get('p2p_chain', {}) if isinstance(config, dict) else {}
        self.p2p_chain_delay_min.set(str(p2p_cfg.get('delay_min', '0.12')))
        self.p2p_chain_delay_max.set(str(p2p_cfg.get('delay_max', '0.25')))
        self._update_pit_limiter_option_state(editing=self.app.app_state == 'CONFIG')

    def _apply_fuel_mixture_config(self, config: Dict[str, Any]) -> None:
        """Apply fuel mixture flag automation settings from config."""
        if not self.is_fuel_mixture:
            return
        fuel_cfg = config.get('fuel_mixture_auto', {}) if isinstance(config, dict) else {}
        self.fuel_yellow_enabled.set(fuel_cfg.get('yellow_enabled', True))
        self.fuel_green_enabled.set(fuel_cfg.get('green_enabled', True))
        self.fuel_yellow_delay_min.set(str(fuel_cfg.get('yellow_delay_min', '1.00')))
        self.fuel_yellow_delay_max.set(str(fuel_cfg.get('yellow_delay_max', '2.00')))
        self.fuel_green_delay_min.set(str(fuel_cfg.get('green_delay_min', '0.25')))
        self.fuel_green_delay_max.set(str(fuel_cfg.get('green_delay_max', '0.70')))
        self._update_fuel_mixture_option_state(editing=self.app.app_state == 'CONFIG')

    def _apply_hybrid_hold_config(self, config: Dict[str, Any]) -> None:
        """Apply hybrid hold/release automation settings from config."""
        if not self.is_hybrid_hold:
            return
        if self.controller.var_name == HYBRID_BOOST_HOLD_VAR:
            default_min, default_max = ('0.000', '0.020')
        else:
            default_min, default_max = ('0.990', '1.000')
        hold_cfg = config.get('hybrid_hold', {}) if isinstance(config, dict) else {}
        self.hybrid_hold_enabled.set(hold_cfg.get('enabled', True))
        self.hybrid_stop_soc_min.set(str(hold_cfg.get('stop_soc_min', default_min)))
        self.hybrid_stop_soc_max.set(str(hold_cfg.get('stop_soc_max', default_max)))
        self.hybrid_max_hold_s.set(str(hold_cfg.get('max_hold_s', '12.0')))
        self._update_hybrid_option_state(editing=self.app.app_state == 'CONFIG')

    def _apply_wiper_config(self, config: Dict[str, Any]) -> None:
        """Apply windshield wiper automation settings from config."""
        if not self.is_wiper_toggle:
            return
        wiper_cfg = config.get('wiper_auto', {}) if isinstance(config, dict) else {}
        self.wiper_auto.set(wiper_cfg.get('enabled', True))
        self.wiper_precip_on.set(str(wiper_cfg.get('precip_on', '0.04')))
        self.wiper_precip_off.set(str(wiper_cfg.get('precip_off', '0.03')))
        self.wiper_humanize_delay_min.set(str(wiper_cfg.get('humanize_delay_min', '0.0')))
        self.wiper_humanize_delay_max.set(str(wiper_cfg.get('humanize_delay_max', '0.0')))
        self._update_wiper_option_state(editing=self.app.app_state == 'CONFIG')

    def _apply_lap_trigger_config(self, config: Dict[str, Any]) -> None:
        """Apply random lap trigger settings from config."""
        if not (self.is_tearoff or self.is_wiper_toggle):
            return
        trigger_cfg = config.get('lap_trigger', {}) if isinstance(config, dict) else {}
        self.lap_trigger_enabled.set(trigger_cfg.get('enabled', True))
        self.lap_trigger_interval.set(str(trigger_cfg.get('interval', '1')))
        self.lap_trigger_count.set(str(trigger_cfg.get('count', '1')))
        self._update_lap_trigger_option_state(editing=self.app.app_state == 'CONFIG')

    def _on_pit_limiter_setting_change(self) -> None:
        """Persist pit limiter automation settings and refresh state."""
        self._update_pit_limiter_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_wiper_setting_change(self) -> None:
        """Persist windshield wiper automation settings and refresh state."""
        self._update_wiper_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_hybrid_hold_setting_change(self) -> None:
        """Persist hybrid hold settings and refresh widget state."""
        self._update_hybrid_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_fuel_mixture_setting_change(self) -> None:
        """Persist fuel mixture flag automation settings."""
        self._update_fuel_mixture_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _on_lap_trigger_setting_change(self) -> None:
        """Persist per-lap random trigger settings and refresh state."""
        self._update_lap_trigger_option_state(editing=self.app.app_state == 'CONFIG')
        self.app.schedule_preset_save()

    def _update_pit_limiter_option_state(self, editing: bool) -> None:
        """Enable or disable pit limiter option widgets."""
        if not self.is_pit_limiter:
            return
        enabled = self.pit_limiter_auto.get()
        humanize = self.pit_limiter_humanize.get()
        base_state = 'normal' if editing else 'disabled'
        if self.chk_pit_limiter_auto:
            self.chk_pit_limiter_auto.config(state=base_state)
        if self.chk_pit_limiter_configured:
            self.chk_pit_limiter_configured.config(state=base_state)
        if self.chk_pit_limiter_humanize:
            self.chk_pit_limiter_humanize.config(state=base_state)
        if self.chk_pit_limiter_force_on:
            self.chk_pit_limiter_force_on.config(state=base_state)
        entry_state = 'normal' if editing and enabled and humanize else 'disabled'
        if self.entry_pit_limiter_delay_min:
            self.entry_pit_limiter_delay_min.config(state=entry_state)
        if self.entry_pit_limiter_delay_max:
            self.entry_pit_limiter_delay_max.config(state=entry_state)
        poll_state = 'normal' if editing and enabled else 'disabled'
        if self.entry_pit_limiter_poll_hz_min:
            self.entry_pit_limiter_poll_hz_min.config(state=poll_state)
        if self.entry_pit_limiter_poll_hz_max:
            self.entry_pit_limiter_poll_hz_max.config(state=poll_state)

    def _update_fuel_mixture_option_state(self, editing: bool) -> None:
        """Enable or disable fuel mixture automation widgets."""
        if not self.is_fuel_mixture:
            return
        base_state = 'normal' if editing else 'disabled'
        yellow_state = 'normal' if editing and self.fuel_yellow_enabled.get() else 'disabled'
        green_state = 'normal' if editing and self.fuel_green_enabled.get() else 'disabled'
        if self.chk_fuel_yellow_enabled:
            self.chk_fuel_yellow_enabled.config(state=base_state)
        if self.chk_fuel_green_enabled:
            self.chk_fuel_green_enabled.config(state=base_state)
        if self.entry_fuel_yellow_delay_min:
            self.entry_fuel_yellow_delay_min.config(state=yellow_state)
        if self.entry_fuel_yellow_delay_max:
            self.entry_fuel_yellow_delay_max.config(state=yellow_state)
        if self.entry_fuel_green_delay_min:
            self.entry_fuel_green_delay_min.config(state=green_state)
        if self.entry_fuel_green_delay_max:
            self.entry_fuel_green_delay_max.config(state=green_state)

    def _update_hybrid_option_state(self, editing: bool) -> None:
        """Enable or disable hybrid hold option widgets."""
        if not self.is_hybrid_hold:
            return
        enabled = self.hybrid_hold_enabled.get()
        base_state = 'normal' if editing else 'disabled'
        entry_state = 'normal' if editing and enabled else 'disabled'
        if self.chk_hybrid_hold_enabled:
            self.chk_hybrid_hold_enabled.config(state=base_state)
        if self.entry_hybrid_stop_soc_min:
            self.entry_hybrid_stop_soc_min.config(state=entry_state)
        if self.entry_hybrid_stop_soc_max:
            self.entry_hybrid_stop_soc_max.config(state=entry_state)
        if self.entry_hybrid_max_hold_s:
            self.entry_hybrid_max_hold_s.config(state=entry_state)

    def _update_wiper_option_state(self, editing: bool) -> None:
        """Enable or disable windshield wiper option widgets."""
        if not self.is_wiper_toggle:
            return
        enabled = self.wiper_auto.get()
        base_state = 'normal' if editing else 'disabled'
        entry_state = 'normal' if editing and enabled else 'disabled'
        if self.chk_wiper_auto:
            self.chk_wiper_auto.config(state=base_state)
        if self.entry_wiper_precip_on:
            self.entry_wiper_precip_on.config(state=entry_state)
        if self.entry_wiper_precip_off:
            self.entry_wiper_precip_off.config(state=entry_state)
        if self.entry_wiper_delay_min:
            self.entry_wiper_delay_min.config(state=entry_state)
        if self.entry_wiper_delay_max:
            self.entry_wiper_delay_max.config(state=entry_state)

    def _update_lap_trigger_option_state(self, editing: bool) -> None:
        """Enable or disable random lap trigger option widgets."""
        if not (self.is_tearoff or self.is_wiper_toggle):
            return
        enabled = self.lap_trigger_enabled.get()
        base_state = 'normal' if editing else 'disabled'
        entry_state = 'normal' if editing and enabled else 'disabled'
        if self.chk_lap_trigger_enabled:
            self.chk_lap_trigger_enabled.config(state=base_state)
        if self.entry_lap_trigger_count:
            self.entry_lap_trigger_count.config(state=entry_state)
        if self.entry_lap_trigger_interval:
            self.entry_lap_trigger_interval.config(state=entry_state)

class ComboTab(tk.Frame):
    """
    Tab for creating combo macros that adjust multiple variables with one trigger.
    """

    def __init__(self, parent, controllers_dict: Dict[str, GenericController], app):
        super().__init__(parent)
        self.app = app
        self.controllers = controllers_dict
        self.var_names = list(self.controllers.keys())
        self.var_display_names = {name: compact_driver_control_name(name) for name in self.var_names}
        self.preset_rows: List[Dict[str, Any]] = []
        if self.var_names:
            self.column_width = max(8, min(12, max((len(self.var_display_names[name]) for name in self.var_names)) + 2))
        else:
            self.column_width = 8
        self.trigger_col_px = 132
        self.value_col_px = max(78, min(112, self.column_width * 9))
        self.delete_col_px = 38
        scroll_frame = ScrollableFrame(self)
        scroll_frame.pack(fill='both', expand=True)
        body = scroll_frame.inner
        tk.Label(body, text='⚡ Combo Adjustments (one trigger -> multiple variables)', fg='orange', font=('Arial', 10, 'bold')).pack(pady=5)
        header = tk.Frame(body)
        header.pack(fill='x', padx=5, pady=5)
        self._configure_combo_grid(header)
        tk.Label(header, text='Trigger', anchor='center', font=('Arial', 9, 'bold')).grid(row=0, column=0, sticky='ew', padx=2)
        for index, var_name in enumerate(self.var_names, start=1):
            tk.Label(header, text=self.var_display_names[var_name], anchor='center', font=('Arial', 8)).grid(row=0, column=index, sticky='ew', padx=2)
        self.presets_container = tk.Frame(body)
        self.presets_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.btn_add_combo_row = tk.Button(body, text='Add Row (+)', command=self.add_dynamic_row, bg='#f0f0f0')
        self.btn_add_combo_row.pack(fill='x', padx=5, pady=(0, 5))
        for _ in range(2):
            self.add_dynamic_row()

    def _configure_combo_grid(self, frame: tk.Frame) -> None:
        frame.grid_columnconfigure(0, minsize=self.trigger_col_px, weight=0)
        for index, _var_name in enumerate(self.var_names, start=1):
            frame.grid_columnconfigure(index, minsize=self.value_col_px, weight=0)
        frame.grid_columnconfigure(len(self.var_names) + 1, minsize=self.delete_col_px, weight=0)
        frame.grid_columnconfigure(len(self.var_names) + 2, weight=1)

    def set_discreet_mode(self, enabled: bool) -> None:
        """Show or hide automation-focused UI elements."""
        return

    def set_editing_state(self, enabled: bool):
        """Enable/disable editing based on app mode."""
        state = 'normal' if enabled else 'readonly'
        button_state = 'normal' if enabled else 'disabled'
        for row in self.preset_rows:
            for entry in row['entries'].values():
                try:
                    entry.config(state=state)
                except Exception:
                    pass
            bind_button = row.get('bind_button')
            if bind_button:
                try:
                    bind_button.config(state=button_state)
                except Exception:
                    pass
            delete_button = row.get('delete_button')
            if delete_button:
                try:
                    delete_button.config(state=button_state)
                except Exception:
                    pass
        if self.btn_add_combo_row:
            try:
                self.btn_add_combo_row.config(state=button_state)
            except Exception:
                pass

    def _bind_autosave_entry(self, entry: tk.Entry) -> None:
        """Attach auto-save handlers to entries."""
        entry.bind('<KeyRelease>', lambda _event: self.app.schedule_preset_save())
        entry.bind('<FocusOut>', lambda _event: self.app.schedule_preset_save())

    def _config_bind_button(self, button: tk.Button, data_store: Dict[str, Any]):
        """Configure binding button behavior."""

        def on_click():
            if self.app.app_state != 'CONFIG':
                self.app._show_info('Notice', 'Enter CONFIG mode first.')
                return
            self.app.focus_window()
            default_text = 'Set Bind'
            existing_bind = data_store.get('bind')
            button.config(text='...', bg='yellow')
            self.update_idletasks()
            code = input_manager.capture_any_input()
            if code and code != 'CANCEL':
                conflict = self.app._find_hotkey_conflict(code, data_store.get('source_id'))
                if conflict:
                    self.app._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                    if existing_bind:
                        bg_color = '#90ee90' if 'JOY' in existing_bind else '#ADD8E6'
                        button.config(text=existing_bind, bg=bg_color)
                    else:
                        button.config(text=default_text, bg='#f0f0f0')
                    return
                data_store['bind'] = code
                bg_color = '#90ee90' if 'JOY' in code else '#ADD8E6'
                button.config(text=code, bg=bg_color)
            elif code == 'CANCEL':
                data_store['bind'] = None
                button.config(text=default_text, bg='#f0f0f0')
            self.app.schedule_preset_save()
        button.config(command=on_click)

    def add_dynamic_row(self, existing: Optional[Dict[str, Any]]=None, is_reset: bool=False, pack_row: bool=True):
        """Add a combo preset row."""
        is_reset = False
        frame = tk.Frame(self.presets_container)
        if pack_row:
            frame.pack(fill='x', pady=2)
        self._configure_combo_grid(frame)
        bind_button = tk.Button(frame, text='Set Bind', fg='black')
        bind_button.grid(row=0, column=0, sticky='ew', padx=2)
        row_data = {'frame': frame, 'entries': {}, 'bind': None, 'bind_button': bind_button, 'is_reset': is_reset, 'delete_button': None, 'source_id': f'combo:{id(frame)}'}
        self._config_bind_button(bind_button, row_data)
        for index, var_name in enumerate(self.var_names, start=1):
            entry = ttk.Entry(frame, width=self.column_width, justify='center')
            entry.grid(row=0, column=index, sticky='ew', padx=2)
            if self.app.app_state != 'CONFIG':
                entry.config(state='readonly')
            row_data['entries'][var_name] = entry
            self._bind_autosave_entry(entry)
        if existing:
            values = existing.get('vals', {})
            for var_name, value in values.items():
                if var_name in row_data['entries']:
                    entry = row_data['entries'][var_name]
                    entry.config(state='normal')
                    entry.delete(0, tk.END)
                    entry.insert(0, value)
                    if self.app.app_state != 'CONFIG':
                        entry.config(state='readonly')
            row_data['bind'] = existing.get('bind')
            if row_data['bind']:
                bg_color = '#90ee90' if 'JOY' in row_data['bind'] else '#ADD8E6'
                bind_button.config(text=row_data['bind'], bg=bg_color)
        delete_button = tk.Button(frame, text='X', fg='red', command=lambda r=row_data: self.remove_row(r), width=2)
        delete_button.grid(row=0, column=len(self.var_names) + 1, sticky='w', padx=5)
        if self.app.app_state != 'CONFIG':
            bind_button.config(state='disabled')
            delete_button.config(state='disabled')
        row_data['delete_button'] = delete_button
        self.preset_rows.append(row_data)

    def remove_row(self, row_data: Dict[str, Any]):
        """Remove a preset row."""
        if self.app.app_state != 'CONFIG':
            self.app._show_info('Notice', 'Enter CONFIG mode first.')
            return
        row_data['frame'].destroy()
        if row_data in self.preset_rows:
            self.preset_rows.remove(row_data)
        self.app.schedule_preset_save()

    def get_config(self) -> Dict[str, Any]:
        """Get current combo configuration."""
        presets_data = []
        for row in self.preset_rows:
            values = {var_name: entry.get() for var_name, entry in row['entries'].items()}
            presets_data.append({'vals': values, 'bind': row['bind']})
        return {'presets': presets_data}

    def set_config(self, config: Dict[str, Any]):
        """Load combo configuration."""
        for row in list(self.preset_rows):
            row['frame'].destroy()
        self.preset_rows.clear()
        if not config:
            for _ in range(2):
                self.add_dynamic_row()
            return
        saved_presets = [preset for preset in config.get('presets', []) if not preset.get('is_reset')]
        bulk_load = len(saved_presets) > 20
        for preset in saved_presets:
            self.add_dynamic_row(existing=preset, is_reset=False, pack_row=not bulk_load)
        if bulk_load:
            for row in self.preset_rows:
                frame = row.get('frame')
                if frame and frame.winfo_manager() != 'pack':
                    frame.pack(fill='x', pady=2)
        if len(self.preset_rows) < 2:
            self.add_dynamic_row()

class GlobalTimingWindow(tk.Toplevel):
    """
    Window for configuring input timing profiles.
    """

    def __init__(self, parent, callback_save: Callable, popups_enabled: Optional[Callable[[], bool]]=None):
        super().__init__(parent)
        self.title('Timing Adjustments (Anti-Detection)')
        self.geometry('420x420')
        self.callback = callback_save
        self.popups_enabled = popups_enabled
        self._profile_initialized = False
        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=10, pady=10)
        timing_frame = tk.Frame(notebook)
        notebook.add(timing_frame, text='Timing')
        profiles_frame = tk.LabelFrame(timing_frame, text='Behavior Profiles')
        profiles_frame.pack(fill='x', padx=10, pady=5)
        self.var_profile = tk.StringVar(value=GLOBAL_TIMING.get('profile', 'bot_safe'))
        tk.Radiobutton(profiles_frame, text='🤖 BOT (experimental, near-zero delay)', variable=self.var_profile, value='bot', command=self._on_profile_change).pack(anchor='w', padx=5, pady=2)
        tk.Radiobutton(profiles_frame, text='🤖 BOT Stable (fast, more reliable)', variable=self.var_profile, value='bot_safe', command=self._on_profile_change).pack(anchor='w', padx=5, pady=2)
        tk.Radiobutton(profiles_frame, text='😈 Aggressive (fast, robotic)', variable=self.var_profile, value='aggressive', command=self._on_profile_change).pack(anchor='w', padx=5, pady=2)
        tk.Radiobutton(profiles_frame, text='🙂 Casual (more relaxed)', variable=self.var_profile, value='casual', command=self._on_profile_change).pack(anchor='w', padx=5, pady=2)
        tk.Radiobutton(profiles_frame, text='😎 Relaxed (well-spaced)', variable=self.var_profile, value='relaxed', command=self._on_profile_change).pack(anchor='w', padx=5, pady=2)
        tk.Radiobutton(profiles_frame, text='🛠 Custom (define values below)', variable=self.var_profile, value='custom', command=self._on_profile_change).pack(anchor='w', padx=5, pady=(2, 5))
        legacy_ms, current_ms, savings_ms = _bot_legacy_savings_ms(16)
        tk.Label(profiles_frame, text=f'BOT 16-step macro estimate: {current_ms:.0f} ms (was {legacy_ms:.0f} ms, saves {savings_ms:.0f} ms)', font=('Segoe UI', 9)).pack(anchor='w', padx=5, pady=(0, 4))
        boundary_frame = tk.LabelFrame(timing_frame, text='BOT Boundary Pulses (min/max clamps)')
        boundary_frame.pack(fill='x', padx=10, pady=(5, 10))
        tk.Label(boundary_frame, text='Press (ms):').grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.entry_boundary_press = tk.Entry(boundary_frame, width=8)
        self.entry_boundary_press.grid(row=0, column=1, padx=5, pady=2)
        self.entry_boundary_press.insert(0, str(GLOBAL_TIMING.get('boundary_press_ms', 6)))
        tk.Label(boundary_frame, text='Interval (ms):').grid(row=0, column=2, sticky='w', padx=5, pady=2)
        self.entry_boundary_interval = tk.Entry(boundary_frame, width=8)
        self.entry_boundary_interval.grid(row=0, column=3, padx=5, pady=2)
        self.entry_boundary_interval.insert(0, str(GLOBAL_TIMING.get('boundary_interval_ms', 6)))
        self.lbl_boundary_rate = tk.Label(boundary_frame, text='Approx. -- pulses/sec', font=('Segoe UI', 9))
        self.lbl_boundary_rate.grid(row=1, column=0, columnspan=4, sticky='w', padx=5, pady=(2, 4))
        for widget in (self.entry_boundary_press, self.entry_boundary_interval):
            widget.bind('<KeyRelease>', self._update_boundary_rate_label)
            widget.bind('<FocusOut>', self._update_boundary_rate_label)
        for i in range(4):
            boundary_frame.columnconfigure(i, weight=1)
        self.custom_frame = tk.LabelFrame(timing_frame, text='Custom Settings (this profile only)')
        self.custom_frame.pack(fill='x', padx=10, pady=10)
        tk.Label(self.custom_frame, text='Press Min (ms):').grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.entry_press_min = tk.Entry(self.custom_frame, width=8)
        self.entry_press_min.grid(row=0, column=1, padx=5, pady=2)
        self.entry_press_min.insert(0, str(GLOBAL_TIMING.get('press_min_ms', 60)))
        tk.Label(self.custom_frame, text='Press Max (ms):').grid(row=0, column=2, sticky='w', padx=5, pady=2)
        self.entry_press_max = tk.Entry(self.custom_frame, width=8)
        self.entry_press_max.grid(row=0, column=3, padx=5, pady=2)
        self.entry_press_max.insert(0, str(GLOBAL_TIMING.get('press_max_ms', 80)))
        tk.Label(self.custom_frame, text='Interval Min (ms):').grid(row=1, column=0, sticky='w', padx=5, pady=2)
        self.entry_interval_min = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_min.grid(row=1, column=1, padx=5, pady=2)
        self.entry_interval_min.insert(0, str(GLOBAL_TIMING.get('interval_min_ms', 60)))
        tk.Label(self.custom_frame, text='Interval Max (ms):').grid(row=1, column=2, sticky='w', padx=5, pady=2)
        self.entry_interval_max = tk.Entry(self.custom_frame, width=8)
        self.entry_interval_max.grid(row=1, column=3, padx=5, pady=2)
        self.entry_interval_max.insert(0, str(GLOBAL_TIMING.get('interval_max_ms', 90)))
        self.var_random = tk.BooleanVar(value=GLOBAL_TIMING.get('random_enabled', False))
        self.check_random = tk.Checkbutton(self.custom_frame, text='Randomize (humanize)', variable=self.var_random, command=self._toggle_random)
        self.check_random.grid(row=2, column=0, columnspan=4, sticky='w', padx=5, pady=(5, 2))
        tk.Label(self.custom_frame, text='Range (+/- ms):').grid(row=3, column=0, sticky='w', padx=5, pady=2)
        self.entry_random_range = tk.Entry(self.custom_frame, width=8)
        self.entry_random_range.grid(row=3, column=1, padx=5, pady=2)
        self.entry_random_range.insert(0, str(GLOBAL_TIMING.get('random_range_ms', 10)))
        for i in range(4):
            self.custom_frame.columnconfigure(i, weight=1)
        tk.Button(self, text='💾 SAVE', command=self.save_all, bg='#90ee90', height=2).pack(fill='x', padx=10, pady=10)
        self.update_idletasks()
        min_width = max(420, self.winfo_reqwidth())
        min_height = self.winfo_reqheight()
        self.minsize(min_width, min_height)
        self.geometry(f'{min_width}x{min_height}')
        self._on_profile_change()
        self._update_boundary_rate_label()
        self._profile_initialized = True

    def _on_profile_change(self):
        """Handle profile selection change."""
        profile = self.var_profile.get()
        state = 'normal' if profile == 'custom' else 'disabled'
        for widget in [self.entry_press_min, self.entry_press_max, self.entry_interval_min, self.entry_interval_max, self.check_random, self.entry_random_range]:
            widget.config(state=state)
        if self._profile_initialized and profile != 'custom':
            GLOBAL_TIMING['profile'] = profile
            self.callback(GLOBAL_TIMING)

    def _update_boundary_rate_label(self, _event: Optional[tk.Event]=None):
        """Update the boundary pulse rate preview."""
        try:
            press_ms = int(self.entry_boundary_press.get())
            interval_ms = int(self.entry_boundary_interval.get())
            rate_hz = _pulse_rate_hz(max(1, press_ms), max(1, interval_ms))
            label = f'Approx. {rate_hz:.1f} pulses/sec'
        except ValueError:
            label = 'Approx. -- pulses/sec'
        self.lbl_boundary_rate.config(text=label)

    def _toggle_random(self):
        """Handle randomization toggle."""
        state = 'normal' if self.var_random.get() and self.var_profile.get() == 'custom' else 'disabled'
        self.entry_random_range.config(state=state)

    def save_all(self):
        """Save timing configuration."""
        profile = self.var_profile.get()
        GLOBAL_TIMING['profile'] = profile
        try:
            GLOBAL_TIMING['boundary_press_ms'] = int(self.entry_boundary_press.get())
            GLOBAL_TIMING['boundary_interval_ms'] = int(self.entry_boundary_interval.get())
        except ValueError:
            if self.popups_enabled is None or self.popups_enabled():
                messagebox.showerror('Error', 'Please use numbers only for BOT boundary pulses.')
            return
        if profile == 'custom':
            try:
                GLOBAL_TIMING['press_min_ms'] = int(self.entry_press_min.get())
                GLOBAL_TIMING['press_max_ms'] = int(self.entry_press_max.get())
                GLOBAL_TIMING['interval_min_ms'] = int(self.entry_interval_min.get())
                GLOBAL_TIMING['interval_max_ms'] = int(self.entry_interval_max.get())
                GLOBAL_TIMING['random_enabled'] = self.var_random.get()
                GLOBAL_TIMING['random_range_ms'] = int(self.entry_random_range.get())
            except ValueError:
                if self.popups_enabled is None or self.popups_enabled():
                    messagebox.showerror('Error', 'Please use numbers only in Custom mode.')
                return
        self.callback(GLOBAL_TIMING)
        self.destroy()

class iRacingControlApp:
    """
    Main application for iRacing control management.
    
    Features:
    - Dynamic driver control adjustment
    - Multi-device input support
    - HUD overlay with telemetry
    - Per-car/track preset management
    - Macro/combo system
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f'{APP_NAME} v{APP_VERSION}')
        self.root.geometry('1180x820')
        self.root.minsize(980, 680)
        apply_app_icon(self.root)
        self._configure_styles()
        self.root.protocol('WM_DELETE_WINDOW', self._close_app)
        self.root.bind('<Unmap>', self._handle_minimize_event)
        self._uiq: 'queue.Queue[Tuple[Callable, tuple, dict]]' = queue.Queue()
        self.root.after(30, self._drain_ui_queue)
        try:
            self.ir = irsdk.IRSDK(parse_yaml_async=True)
        except TypeError:
            self.ir = irsdk.IRSDK()
        self.ir_lock = threading.Lock()
        self._sdk_warmup_lock = threading.Lock()
        self._sdk_warmup_thread: Optional[threading.Thread] = None
        self._sdk_last_warmup_attempt = 0.0
        self.app_state = 'RUNNING'
        self.controllers: Dict[str, GenericController] = {}
        self.tabs: Dict[str, ControlTab] = {}
        self.combo_tab: Optional[ComboTab] = None
        self.overlay_tab: Optional[OverlayConfigTab] = None
        self.combo_frame: Optional[tk.Frame] = None
        self.overlay_frame: Optional[tk.Frame] = None
        self.combo_tab_label = '⚡ Combos'
        self.overlay_tab_label = 'HUD / Overlay'
        self._control_tab_label_refresh_job: Optional[str] = None
        self._control_tab_font: Optional[tkfont.Font] = None
        self.saved_presets: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_config: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_feedback: Dict[str, Dict[str, float]] = {}
        self.show_overlay_feedback = tk.BooleanVar(value=True)
        self.p2p_overlay_default_off_migrated = False
        self._overlay_feedback_state = {'last_time': time.time(), 'abs_active': 0.0, 'tc_active': 0.0, 'spin_active': 0.0, 'lock_active': 0.0, 'last_alert': '', 'last_alert_time': 0.0}
        self.active_vars: List[Tuple[str, bool, bool]] = []
        self.overlay_extra_vars: List[Tuple[str, bool, bool]] = [('dcPushToPass', False, True)]
        self._automation_rng = random.Random()
        self.overlay_extra_vars: List[Tuple[str, bool, bool]] = [('dcPushToPass', False, True)]
        self.current_car = ''
        self.current_track = ''
        self.current_surface = DEFAULT_SURFACE_PRESET
        self.last_session_type = ''
        self.last_session_num: Optional[int] = None
        self.scans_since_restart = 0
        self.pending_scan_on_start = False
        self.skip_race_restart_once = False
        self.skip_session_scan_once = False
        self.skip_auto_scan_once = False
        self.skip_on_track_restart_once = False
        self._last_auto_pair: Tuple[str, str] = ('', '')
        self._session_scan_pending = False
        self._telemetry_active = False
        self._rescan_restart_pair: Tuple[str, str] = ('', '')
        self._last_weekend_key: Optional[Tuple[Any, ...]] = None
        self._last_session_id: Optional[Any] = None
        self._skip_next_auto_load = False
        self._last_successful_scan_pair: Tuple[str, str] = ('', '')
        self._last_successful_scan_time = 0.0
        self._last_detected_pair: Tuple[str, str] = ('', '')
        self._last_detected_pair_time = 0.0
        self._last_loaded_preset_signature: Tuple[str, str, str] = ('', '', '')
        self._last_loaded_preset_time = 0.0
        self._pending_scan_silent = False
        self._scan_in_progress = False
        self._on_track_restart_seen = False
        self._session_scan_debounce_ms = 250
        self._continuous_scan_job: Optional[str] = None
        self._continuous_scan_delay_ms = 700
        self.scan_validator: Optional[ScanValidator] = None
        self._validation_failures = 0
        self._max_validation_failures = 3
        self._last_on_track_state = False
        self._on_track_validation_pending = False
        self._recovery_in_progress = False
        self._recovery_attempt = 0
        self._max_recovery_attempts = 2
        self._none_scan_attempts = 0
        self._max_none_scan_attempts = 3
        self._none_telemetry_counts: Dict[str, int] = {}
        self._none_telemetry_threshold = 5
        self._none_telemetry_cooldown_s = 5.0
        self._none_telemetry_last_trigger = 0.0
        self._validation_in_progress = False
        self._latest_release_version: Optional[str] = None
        self._update_check_running = False
        self.auto_load_attempted: set = set()
        self.overlay = OverlayWindow(root)
        self.overlay.withdraw()
        self.overlay_visible = True
        self._overlay_visible_before_discreet: Optional[bool] = None
        self.use_keyboard_only = tk.BooleanVar(value=False)
        self.mic_combo: Optional[ttk.Combobox] = None
        self.auto_detect = tk.BooleanVar(value=True)
        self.auto_scan_on_change = tk.BooleanVar(value=True)
        self.auto_restart_on_rescan = tk.BooleanVar(value=False)
        self.auto_restart_on_race = tk.BooleanVar(value=False)
        self.auto_restart_on_track_ready = tk.BooleanVar(value=False)
        self.block_off_track_commands = tk.BooleanVar(value=True)
        self.keep_trying_targets = tk.BooleanVar(value=True)
        self.show_scan_popup = tk.BooleanVar(value=False)
        self.keep_scanning_until_valid = tk.BooleanVar(value=True)
        self.disable_popups = tk.BooleanVar(value=True)
        self.auto_save_presets = tk.BooleanVar(value=True)
        self.lock_preset_selection = tk.BooleanVar(value=True)
        self.start_with_windows = tk.BooleanVar(value=False)
        self.focus_on_start = tk.BooleanVar(value=False)
        self.show_getting_started = tk.BooleanVar(value=True)
        self.discreet_mode = tk.BooleanVar(value=False)
        self.clear_target_bind: Optional[str] = None
        self.btn_clear_target_bind: Optional[tk.Button] = None
        self.manual_rescan_bind: Optional[str] = None
        self.btn_manual_rescan_bind: Optional[tk.Button] = None
        self.surface_toggle_bind: Optional[str] = None
        self.btn_surface_toggle_bind: Optional[tk.Button] = None
        self.surface_dry_bind: Optional[str] = None
        self.btn_surface_dry_bind: Optional[tk.Button] = None
        self.surface_wet_bind: Optional[str] = None
        self.btn_surface_wet_bind: Optional[tk.Button] = None
        self.btn_discreet_mode: Optional[tk.Button] = None
        self.stability_frame: Optional[tk.LabelFrame] = None
        self.presets_frame: Optional[tk.LabelFrame] = None
        self.devices_frame: Optional[tk.LabelFrame] = None
        self.scan_frame: Optional[tk.LabelFrame] = None
        self._config_save_job: Optional[str] = None
        self._auto_save_job: Optional[str] = None
        self.getting_started_window: Optional[tk.Toplevel] = None
        self._p2p_chain_lock = threading.Lock()
        self._p2p_chain_threads: Dict[str, threading.Event] = {}
        self._hybrid_hold_lock = threading.Lock()
        self._hybrid_hold_states: Dict[str, Dict[str, Any]] = {}
        self._fuel_mixture_state: Dict[str, Any] = {'last_caution': None, 'last_flags': 0, 'pending_kind': None, 'pending_since': None, 'pending_delay': 0.0, 'pending_target': None, 'restart_armed_until': 0.0, 'cooldown_until': 0.0}
        self.getting_started_text = 'Quick start checklist\n1) Choose your car and track.\n2) Confirm your input devices.\n3) Scan driver controls.\n4) Visit Options to set up your hotkeys.\n\nUse CONFIG mode when changing bindings and RUNNING mode when driving.'
        self.load_config()
        self._apply_startup_preference(notify=False)
        self._create_menu()
        self._create_main_ui()
        self._apply_startup_focus_mode()
        self.root.after(100, self._start_sdk_warmup)
        if self.keep_scanning_until_valid.get():
            self.root.after(500, lambda: self.scan_driver_controls(silent_if_unavailable=True, allow_restart=False))
        self.root.after(300, self._maybe_show_getting_started)
        self.root.after(3000, self.schedule_update_check)
        self.update_safe_mode()
        self.root.after(2000, self.auto_preset_loop)
        self.update_overlay_loop()
        if self.overlay_visible:
            self.overlay.deiconify()
        input_manager.active = self.app_state == 'RUNNING'
        self.root.after(200, self._perform_pending_scan)

    def _apply_startup_preference(self, notify: bool=False) -> None:
        """Create or remove the startup entry based on current preference."""
        enabled = self.start_with_windows.get()
        success = set_startup_entry(enabled)
        if success:
            return
        current = _startup_entry_exists()
        self.start_with_windows.set(current)
        if notify:
            self._show_warning('Start with Windows', 'Unable to update the Windows startup entry. Please check permissions or try running as Administrator.')

    def _apply_startup_focus_mode(self) -> None:
        """Optionally keep the main window from stealing focus on startup."""
        if self.focus_on_start.get():
            return

        def _background_window() -> None:
            if not self.root.winfo_exists():
                return
            try:
                self.root.lower()
            except Exception:
                pass
        self.root.after(250, _background_window)

    def _on_startup_toggle(self) -> None:
        self._apply_startup_preference(notify=True)
        self.schedule_save()

    def _on_disable_popups_toggle(self) -> None:
        if self.disable_popups.get():
            if self.getting_started_window and self.getting_started_window.winfo_exists():
                self.getting_started_window.destroy()
                self.getting_started_window = None
        self.schedule_save()

    def _on_keep_scanning_toggle(self) -> None:
        if self.keep_scanning_until_valid.get():
            if not self._scan_in_progress:
                self.scan_driver_controls(silent_if_unavailable=True, allow_restart=False)
        else:
            self._cancel_continuous_scan_retry()
        self.schedule_save()

    def toggle_discreet_mode(self) -> None:
        """Toggle discreet mode visibility settings."""
        self.discreet_mode.set(not self.discreet_mode.get())
        self._apply_discreet_mode()
        self.schedule_save()

    def _apply_discreet_mode(self) -> None:
        """Show or hide automation/humanization UI based on discreet mode."""
        enabled = self.discreet_mode.get()
        if self.btn_discreet_mode:
            label = 'Layout: Simple' if enabled else 'Layout: Standard'
            color = '#f4b183' if enabled else '#f0f0f0'
            self.btn_discreet_mode.config(text=label, bg=color)
        if enabled:
            if self._overlay_visible_before_discreet is None:
                self._overlay_visible_before_discreet = self.overlay_visible
            self.overlay_visible = False
            try:
                if self.overlay.winfo_exists():
                    self.overlay.withdraw()
            except Exception:
                pass
        else:
            if self._overlay_visible_before_discreet is not None:
                self.overlay_visible = self._overlay_visible_before_discreet
                try:
                    if self.overlay_visible and self.overlay.winfo_exists():
                        self.overlay.deiconify()
                except Exception:
                    pass
            self._overlay_visible_before_discreet = None
        if self.stability_frame:
            if enabled:
                self.stability_frame.grid_remove()
            else:
                self.stability_frame.grid()
        self._toggle_pack_widget(self.presets_frame, not enabled)
        self._toggle_pack_widget(self.devices_frame, not enabled)
        self._toggle_pack_widget(self.scan_frame, not enabled)
        for tab in self.tabs.values():
            tab.set_discreet_mode(enabled)
        if self.combo_tab:
            self.combo_tab.set_discreet_mode(enabled)
        self._apply_discreet_tabs()
        self._refresh_overlay_discreet_state()

    def _tab_is_visible(self, frame: tk.Frame) -> bool:
        if hasattr(self, 'main_tabs') and frame in (getattr(self, 'hud_tab', None), getattr(self, 'options_tab', None), getattr(self, 'diagnostics_tab', None)):
            return str(frame) in self.main_tabs.tabs()
        return str(frame) in self.notebook.tabs()

    def _apply_discreet_tabs(self) -> None:
        """Keep public top-level pages available."""
        return

    def _schedule_control_tab_label_refresh(self, _event=None) -> None:
        """Debounce tab relabeling while the control notebook is resized."""
        if not getattr(self, 'root', None):
            return
        job = self._control_tab_label_refresh_job
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._control_tab_label_refresh_job = self.root.after(80, self._refresh_control_tab_labels)

    def _get_control_tab_font(self) -> tkfont.Font:
        if self._control_tab_font is None:
            self._control_tab_font = tkfont.Font(root=self.root, font=('Segoe UI Semibold', 9))
        return self._control_tab_font

    def _fit_control_tab_text(self, text: str, max_px: int) -> str:
        font = self._get_control_tab_font()
        if font.measure(text) <= max_px:
            return text
        if max_px <= font.measure('...'):
            return text[:1]
        base = text.strip()
        while len(base) > 1:
            base = base[:-1].rstrip()
            candidate = f'{base}...'
            if font.measure(candidate) <= max_px:
                return candidate
        return text[:1]

    def _select_control_tab_labels(self, var_names: List[str], available_width: int) -> List[str]:
        if not var_names:
            return []
        font = self._get_control_tab_font()
        candidates = [driver_control_name_candidates(name) for name in var_names]
        tab_padding_px = 22
        available_width = max(220, int(available_width) - 8)
        widths = [[font.measure(text) + tab_padding_px for text in items] for items in candidates]
        full_width = sum((item_widths[0] for item_widths in widths))
        if full_width <= available_width:
            return [items[0] for items in candidates]
        states: Dict[int, Tuple[int, int, List[str]]] = {0: (0, 0, [])}
        for items, item_widths in zip(candidates, widths):
            next_states: Dict[int, Tuple[int, int, List[str]]] = {}
            for used_width, (score, filled_width, labels) in states.items():
                for candidate_index, (text, width) in enumerate(zip(items, item_widths)):
                    new_width = used_width + width
                    if new_width > available_width:
                        continue
                    text_score = len(text.replace(' ', '')) * 12 + text.count(' ') * 4 - candidate_index * 10
                    candidate_state = (score + text_score, filled_width + width, labels + [text])
                    previous = next_states.get(new_width)
                    if previous is None or candidate_state[0] > previous[0] or (candidate_state[0] == previous[0] and candidate_state[1] > previous[1]):
                        next_states[new_width] = candidate_state
            states = next_states
            if not states:
                break
        if states:
            _width, (_score, _filled_width, labels) = max(states.items(), key=lambda item: (item[1][0], item[1][1]))
            return labels
        max_text_px = max(24, available_width // len(candidates) - tab_padding_px)
        return [self._fit_control_tab_text(items[-1], max_text_px) for items in candidates]

    def _control_notebook_width(self) -> int:
        notebook = getattr(self, 'notebook', self.root)
        notebook_width = notebook.winfo_width()
        if notebook_width > 80:
            return notebook_width
        root_width = self.root.winfo_width()
        if root_width > 80:
            return max(220, root_width - 40)
        try:
            geometry_width = int(str(self.root.geometry()).split('x', 1)[0])
            if geometry_width > 80:
                return max(220, geometry_width - 40)
        except Exception:
            pass
        req_width = notebook.winfo_reqwidth()
        if req_width > 80:
            return req_width
        return 900

    def _refresh_control_tab_labels(self) -> None:
        self._control_tab_label_refresh_job = None
        if not getattr(self, 'notebook', None):
            return
        tab_ids = list(self.notebook.tabs())
        if not tab_ids:
            return
        var_names = [var_name for var_name, _is_float, _is_boolean in self.active_vars][:len(tab_ids)]
        labels = self._select_control_tab_labels(var_names, self._control_notebook_width())
        for tab_id, label in zip(tab_ids, labels):
            try:
                self.notebook.tab(tab_id, text=label)
            except Exception:
                pass

    def _toggle_pack_widget(self, widget: Optional[tk.Widget], show: bool) -> None:
        if widget is None:
            return
        if show:
            if widget.winfo_manager() == 'pack':
                return
            info = getattr(widget, '_pack_info', None)
            if not info:
                return
            widget.pack(**info)
        else:
            if widget.winfo_manager() != 'pack':
                return
            info = widget.pack_info()
            if 'in' in info:
                info['in_'] = info.pop('in')
            widget._pack_info = info
            widget.pack_forget()

    def _overlay_display_config(self, config: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return config

    def _refresh_overlay_discreet_state(self) -> None:
        car = self.current_car or 'Generic Car'
        config = self.car_overlay_config.get(car, {})
        self.overlay.rebuild_monitor(self._overlay_display_config(config))

    def _p2p_chain_delay_range(self) -> Tuple[float, float]:
        """Return the sanitized delay range for push-to-pass chaining."""
        tab = self.tabs.get('dcPushToPass')
        if not tab or not getattr(tab, 'is_push_to_pass', False):
            return (0.0, 0.0)
        min_raw = str(tab.p2p_chain_delay_min.get()).strip()
        max_raw = str(tab.p2p_chain_delay_max.get()).strip()
        try:
            min_val = float(min_raw)
        except Exception:
            min_val = 0.0
        try:
            max_val = float(max_raw)
        except Exception:
            max_val = 0.0
        min_val = max(0.0, min_val)
        max_val = max(0.0, max_val)
        if max_val < min_val:
            min_val, max_val = (max_val, min_val)
        return (min_val, max_val)

    def _p2p_chain_delay(self) -> float:
        """Return a humanized delay between chained push-to-pass activations."""
        min_val, max_val = self._p2p_chain_delay_range()
        if max_val <= 0.0:
            return 0.0
        return self._bounded_lognormal(min_val, max_val)

    @staticmethod
    def _p2p_chain_count(raw_value: Any) -> int:
        """Return the number of extra P2P activations requested."""
        if raw_value is None:
            return 0
        if isinstance(raw_value, numbers.Real):
            count = int(raw_value)
        else:
            text = str(raw_value).strip()
            if not text:
                return 0
            try:
                count = int(float(text))
            except Exception:
                return 0
        return max(0, count)

    def _wait_for_p2p_cycle(self, stop_event: threading.Event, active_timeout_s: float=8.0, inactive_timeout_s: float=40.0) -> bool:
        """Wait until P2P becomes active and then ends."""
        start = time.time()
        active_seen = False
        while not stop_event.is_set():
            if self.app_state != 'RUNNING':
                return False
            status = self._read_push_to_pass_status()
            if status is None:
                time.sleep(0.05)
                continue
            if status:
                active_seen = True
            elif active_seen:
                return True
            elapsed = time.time() - start
            if not active_seen and elapsed > active_timeout_s:
                return False
            if active_seen and elapsed > inactive_timeout_s:
                return False
            time.sleep(0.05)
        return False

    def _start_p2p_chain(self, count: int, target: float, preset_index: Optional[int]) -> None:
        """Start a push-to-pass repeat sequence for the given preset."""
        return
        if count <= 0:
            return
        chain_key = f'p2p:{preset_index}' if preset_index is not None else 'p2p:global'
        with self._p2p_chain_lock:
            existing = self._p2p_chain_threads.get(chain_key)
            if existing:
                existing.set()
                self._p2p_chain_threads.pop(chain_key, None)
            stop_event = threading.Event()
            self._p2p_chain_threads[chain_key] = stop_event
        threading.Thread(target=self._run_p2p_chain, args=(count, target, chain_key, stop_event), daemon=True).start()

    def _run_p2p_chain(self, count: int, target: float, chain_key: str, stop_event: threading.Event) -> None:
        """Trigger repeated push-to-pass macros sequentially."""
        try:
            controller = self.controllers.get('dcPushToPass')
            if not controller:
                return
            for _ in range(count):
                if stop_event.is_set():
                    break
                if not self._wait_for_p2p_cycle(stop_event):
                    break
                delay = self._p2p_chain_delay()
                if delay > 0.0:
                    time.sleep(delay)
                if stop_event.is_set() or self.app_state != 'RUNNING':
                    break
                if not self._commands_allowed():
                    break
                controller.request_target(target)
        finally:
            with self._p2p_chain_lock:
                if self._p2p_chain_threads.get(chain_key) is stop_event:
                    self._p2p_chain_threads.pop(chain_key, None)

    def _pit_limiter_delay_range(self, pit_cfg: Dict[str, Any]) -> Tuple[float, float]:
        """Return the sanitized reaction delay range for pit limiter triggers."""
        min_raw = str(pit_cfg.get('delay_min', '')).strip()
        max_raw = str(pit_cfg.get('delay_max', '')).strip()
        try:
            min_val = float(min_raw)
        except Exception:
            min_val = 0.0
        try:
            max_val = float(max_raw)
        except Exception:
            max_val = 0.0
        min_val = max(0.0, min_val)
        max_val = max(0.0, max_val)
        if max_val < min_val:
            min_val, max_val = (max_val, min_val)
        return (min_val, max_val)

    def _pit_limiter_poll_hz_range(self, pit_cfg: Dict[str, Any]) -> Tuple[float, float]:
        """Return the sanitized polling Hz range for pit limiter automation."""
        min_raw = str(pit_cfg.get('poll_hz_min', '')).strip()
        max_raw = str(pit_cfg.get('poll_hz_max', '')).strip()
        try:
            min_val = float(min_raw)
        except Exception:
            min_val = 0.0
        try:
            max_val = float(max_raw)
        except Exception:
            max_val = 0.0
        min_val = max(0.0, min_val)
        max_val = max(0.0, max_val)
        if max_val < min_val:
            min_val, max_val = (max_val, min_val)
        if max_val == 0.0:
            min_val = max(min_val, 7.0)
            max_val = max(max_val, 10.0)
        return (min_val, max_val)

    def _pit_limiter_poll_interval_ms(self, pit_cfg: Dict[str, Any], state: Dict[str, Any]) -> int:
        """Return a humanized polling interval in milliseconds."""
        min_hz, max_hz = self._pit_limiter_poll_hz_range(pit_cfg)
        if max_hz <= 0.0:
            return 120
        base_hz = self._bounded_lognormal(min_hz, max_hz)
        now = time.time()
        drift = state.get('poll_drift_state', 0.0)
        drift_last = state.get('poll_drift_last_update', now)
        elapsed = max(0.0, now - drift_last)
        drift_decay = 0.65 ** max(1.0, elapsed * 2.0)
        drift = max(-0.8, min(0.8, drift))
        state['poll_drift_state'] = drift
        state['poll_drift_last_update'] = now
        jitter = state.get('poll_jitter_state', 0.0)
        jitter = max(-0.35, min(0.35, jitter))
        state['poll_jitter_state'] = jitter
        hz = base_hz + drift + jitter
        hz = max(min_hz, min(max_hz, hz))
        if hz <= 0.0:
            return 120
        interval_ms = int(round(1000.0 / hz))
        return max(10, min(1000, interval_ms))

    def _pit_limiter_targets(self, tab_config: Dict[str, Any], pit_cfg: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        """Resolve pit limiter on/off targets from preset rows."""
        presets = tab_config.get('presets', [])

        def _label_for_value(value: Any) -> Optional[str]:
            if value is None:
                return None
            text = str(value).strip().lower()
            if text in {'on', '1', 'true', 'yes'}:
                return 'ON'
            if text in {'off', '0', 'false', 'no', ''}:
                return 'OFF'
            try:
                numeric = float(text)
            except Exception:
                return None
            return 'ON' if numeric > 0 else 'OFF'

        def _target_from_value(value: Any, label: Optional[str]) -> Optional[float]:
            if label == 'ON':
                if str(value).strip().lower() in {'on', 'true', 'yes'}:
                    return 1.0
            if label == 'OFF':
                if str(value).strip().lower() in {'off', 'false', 'no', ''}:
                    return 0.0
            try:
                return float(str(value).strip())
            except Exception:
                return 1.0 if label == 'ON' else 0.0 if label == 'OFF' else None
        on_target = None
        off_target = None
        for preset in presets:
            label = _label_for_value(preset.get('val'))
            if label == 'ON' and on_target is None:
                on_target = _target_from_value(preset.get('val'), label)
            elif label == 'OFF' and off_target is None:
                off_target = _target_from_value(preset.get('val'), label)
            if on_target is not None and off_target is not None:
                break
        if on_target is None or off_target is None:

            def _target_from_index(idx: Any) -> Optional[float]:
                try:
                    idx_int = int(idx)
                except Exception:
                    return None
                if idx_int < 0 or idx_int >= len(presets):
                    return None
                val_str = str(presets[idx_int].get('val', '')).strip()
                if not val_str:
                    return None
                try:
                    return float(val_str)
                except Exception:
                    return None
            on_target = on_target if on_target is not None else _target_from_index(pit_cfg.get('on_index'))
            off_target = off_target if off_target is not None else _target_from_index(pit_cfg.get('off_index'))
        return (on_target, off_target)

    def _pit_limiter_humanized_delay(self, min_val: float, max_val: float, state: Dict[str, Any], entering: bool) -> float:
        """Return a nuanced, human-like delay for pit limiter triggers."""
        if max_val <= 0.0:
            return 0.0
        base = self._bounded_lognormal(min_val, max_val)
        now = time.time()
        drift = state.get('drift_state', 0.0)
        drift_last = state.get('drift_last_update', now)
        elapsed = max(0.0, now - drift_last)
        drift_decay = 0.55 ** max(1.0, elapsed * 2.5)
        drift = max(-0.12, min(0.18, drift))
        state['drift_state'] = drift
        state['drift_last_update'] = now
        jitter = state.get('jitter_state', 0.0)
        jitter = max(-0.05, min(0.07, jitter))
        state['jitter_state'] = jitter
        extra_pause = 0.0
        delay = base + drift + jitter + extra_pause
        return max(min_val, min(max_val, delay))

    def _reset_pit_limiter_state(self) -> None:
        """Reset pit limiter automation state."""
        state = self._pit_limiter_state
        state['last_on_pit'] = None
        state['pending_action'] = None
        state['pending_since'] = None
        state['pending_delay'] = 0.0
        state['last_action'] = None
        state['last_trigger_delay'] = None
        state['cooldown_until'] = 0.0
        state['scan_until'] = 0.0
        state['jitter_state'] = 0.0
        state['drift_state'] = 0.0
        state['drift_last_update'] = 0.0
        state['poll_jitter_state'] = 0.0
        state['poll_drift_state'] = 0.0
        state['poll_drift_last_update'] = 0.0

    def _pit_limiter_macro_loop(self) -> None:
        """Auto-toggle pit limiter based on OnPitRoad telemetry."""
        return
        interval_ms = 120
        try:
            controller = self.controllers.get('dcPitSpeedLimiterToggle')
            tab = self.tabs.get('dcPitSpeedLimiterToggle')
            if not controller or not tab or self.app_state != 'RUNNING' or (not self._commands_allowed()):
                interval_ms = 300
                self._reset_pit_limiter_state()
                return
            if not self._pit_limiter_track_ok():
                interval_ms = 300
                self._reset_pit_limiter_state()
                return
            tab_config = tab.get_config()
            pit_cfg = tab_config.get('pit_limiter', {})
            state = self._pit_limiter_state
            if not pit_cfg.get('auto_toggle'):
                interval_ms = 320
                self._reset_pit_limiter_state()
                return
            interval_ms = self._pit_limiter_poll_interval_ms(pit_cfg, state)
            on_target, off_target = self._pit_limiter_targets(tab_config, pit_cfg)
            if on_target is None or off_target is None:
                return
            on_pit = self._bool_from_keys(['OnPitRoad', 'PlayerCarOnPitRoad'])
            now = time.time()
            if state['last_on_pit'] is None:
                state['last_on_pit'] = on_pit
                if on_pit:
                    state['scan_until'] = now + 2.0
                return
            if on_pit != state['last_on_pit']:
                target = on_target if on_pit else off_target
                delay_min, delay_max = self._pit_limiter_delay_range(pit_cfg)
                if pit_cfg.get('humanize'):
                    delay = self._pit_limiter_humanized_delay(delay_min, delay_max, state, on_pit)
                else:
                    delay = max(0.0, delay_min)
                state['pending_action'] = target
                state['pending_since'] = now
                state['pending_delay'] = delay
                state['last_trigger_delay'] = delay
                state['last_on_pit'] = on_pit
                state['scan_until'] = now + 2.0 if on_pit else 0.0
            pending_action = state.get('pending_action')
            pending_since = state.get('pending_since')
            pending_delay = state.get('pending_delay', 0.0)
            if pending_action is not None and pending_since is not None:
                if now < state.get('cooldown_until', 0.0):
                    return
                if now - pending_since < pending_delay:
                    return
            if not on_pit:
                state['scan_until'] = 0.0
        finally:
            self.root.after(interval_ms, self._pit_limiter_macro_loop)

    def _wiper_controller(self) -> Tuple[Optional[str], Optional[GenericController], Optional[ControlTab]]:
        """Return the first available wiper controller/tab pair."""
        for var_name in WIPER_TOGGLE_VARS:
            controller = self.controllers.get(var_name)
            if controller:
                return (var_name, controller, self.tabs.get(var_name))
        return (None, None, None)

    def _wiper_tab(self) -> Optional[ControlTab]:
        """Return the first available wiper tab."""
        for var_name in WIPER_TOGGLE_VARS:
            tab = self.tabs.get(var_name)
            if tab:
                return tab
        return None

    @staticmethod
    def _wiper_alias_config(configs: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the first wiper config stored under any known alias."""
        for alias in WIPER_TOGGLE_VARS:
            cfg = configs.get(alias)
            if isinstance(cfg, dict):
                return cfg
        return None

    def _wiper_thresholds(self, wiper_cfg: Dict[str, Any]) -> Tuple[float, float]:
        """Return parsed precipitation thresholds (on, off)."""

        def _parse(value: Any, fallback: float) -> float:
            try:
                return float(value)
            except Exception:
                return fallback
        on_threshold = _parse(wiper_cfg.get('precip_on', 0.04), 0.04)
        off_threshold = _parse(wiper_cfg.get('precip_off', 0.03), 0.03)
        if on_threshold < 0.0:
            on_threshold = 0.0
        if off_threshold < 0.0:
            off_threshold = 0.0
        if on_threshold < off_threshold:
            on_threshold, off_threshold = (off_threshold, on_threshold)
        return (on_threshold, off_threshold)

    def _wiper_humanized_thresholds(self, on_threshold: float, off_threshold: float, state: Dict[str, Any]) -> Tuple[float, float]:
        """Return a humanized set of precipitation thresholds."""
        if off_threshold == 0.0:
            return (on_threshold, off_threshold)
        now = time.time()
        last_update = state.get('threshold_last_update', now)
        elapsed = max(0.0, now - last_update)
        decay = 0.6 ** max(1.0, elapsed * 2.0)
        drift_on = state.get('threshold_drift_on', 0.0) * decay
        drift_off = state.get('threshold_drift_off', 0.0) * decay
        drift_scale = max(0.0015, on_threshold * 0.08)
        drift_on = max(-0.018, min(0.018, drift_on))
        drift_off = max(-0.018, min(0.018, drift_off))
        state['threshold_drift_on'] = drift_on
        state['threshold_drift_off'] = drift_off
        jitter_on = state.get('threshold_jitter_on', 0.0)
        jitter_off = state.get('threshold_jitter_off', 0.0)
        jitter_on = max(-0.012, min(0.012, jitter_on))
        jitter_off = max(-0.012, min(0.012, jitter_off))
        state['threshold_jitter_on'] = jitter_on
        state['threshold_jitter_off'] = jitter_off
        state['threshold_last_update'] = now
        on_effective = on_threshold + drift_on + jitter_on + base_bias
        off_effective = off_threshold + drift_off + jitter_off - base_bias * 0.4
        on_effective = max(0.0, on_effective)
        off_effective = max(0.0, off_effective)
        if on_effective < off_effective:
            on_effective, off_effective = (off_effective, on_effective)
        return (on_effective, off_effective)

    def _wiper_action_delay(self, wiper_cfg: Dict[str, Any], off_threshold: float) -> float:
        """Return the delay before toggling wipers based on config."""

        def _parse(value: Any, fallback: float) -> float:
            try:
                return float(value)
            except Exception:
                return fallback
        min_delay = _parse(wiper_cfg.get('humanize_delay_min', 0.0), 0.0)
        max_delay = _parse(wiper_cfg.get('humanize_delay_max', min_delay), min_delay)
        min_delay = max(0.0, min_delay)
        max_delay = max(0.0, max_delay)
        if max_delay < min_delay:
            min_delay, max_delay = (max_delay, min_delay)
        return 0.0

    def _wiper_desired_state(self, precipitation: float, wiper_cfg: Dict[str, Any], state: Dict[str, Any]) -> Tuple[Optional[bool], str, float, float, float, float]:
        """Return desired wiper state from precipitation and thresholds."""
        on_threshold, off_threshold = self._wiper_thresholds(wiper_cfg)
        on_effective, off_effective = self._wiper_humanized_thresholds(on_threshold, off_threshold, state)
        if precipitation >= on_effective:
            return (True, 'on', on_threshold, off_threshold, on_effective, off_effective)
        if precipitation <= off_effective:
            return (False, 'off', on_threshold, off_threshold, on_effective, off_effective)
        return (None, 'hold', on_threshold, off_threshold, on_effective, off_effective)

    def _wiper_should_trigger(self, desired_state: bool, phase: str, state: Dict[str, Any]) -> bool:
        """Return True if a new wiper toggle should be scheduled."""
        return state.get('last_trigger_phase') != phase

    def _log_wiper_debug(self, precipitation: float, desired_state: Optional[bool], phase: str, on_threshold: float, off_threshold: float, on_effective: float, off_effective: float, state: Dict[str, Any], wiper_cfg: Dict[str, Any], *, reason: str, force: bool=False) -> None:
        """Print wiper debug telemetry and threshold data to the terminal."""
        return
        if not self.wiper_debug_enabled.get():
            return
        now = time.time()
        if not force and now - state.get('debug_last_log', 0.0) < 0.6:
            return
        pending = state.get('pending_action')
        cooldown_remaining = max(0.0, state.get('cooldown_until', 0.0) - now)
        desired_label = 'ON' if desired_state is True else 'OFF' if desired_state is False else 'HOLD'
        print(f"[Wipers Debug] reason={reason} precip={precipitation:.4f} thresholds(on={on_threshold:.4f}/off={off_threshold:.4f}) effective(on={on_effective:.4f}/off={off_effective:.4f}) phase={phase} desired={desired_label} pending={pending} cooldown={cooldown_remaining:.2f}s delay_range={wiper_cfg.get('humanize_delay_min', '0')}-{wiper_cfg.get('humanize_delay_max', '0')}")
        state['debug_last_log'] = now

    def _trigger_wiper_toggle(self, controller: GenericController, desired_state: bool) -> bool:
        """Press the wiper toggle hotkey once without telemetry checks."""
        key = controller.key_increase
        if not key:
            if controller.update_status:
                controller.update_status('No toggle key configured', 'red')
            if self.wiper_debug_enabled.get():
                print('[Wipers Debug] No wiper toggle key configured.')
            return False
        try:
            click_pulse(key, is_float=False)
        except Exception as exc:
            if self.wiper_debug_enabled.get():
                print(f'[Wipers Debug] Failed to press wiper toggle: {exc}')
            return False
        if controller.update_status:
            controller.update_status('Triggered', 'green')
        state_label = 'ON' if desired_state else 'OFF'
        self.notify_overlay_status(f'Wipers toggle ({state_label})', 'green')
        return True

    def _wiper_process_precipitation(self, controller: GenericController, wiper_cfg: Dict[str, Any], state: Dict[str, Any], precipitation: float) -> None:
        """Evaluate precipitation and schedule/trigger wiper toggles."""
        desired_state, phase, on_threshold, off_threshold, on_effective, off_effective = self._wiper_desired_state(precipitation, wiper_cfg, state)
        state['last_phase'] = phase
        self._log_wiper_debug(precipitation, desired_state, phase, on_threshold, off_threshold, on_effective, off_effective, state, wiper_cfg, reason='auto')
        now = time.time()
        pending_action = state.get('pending_action')
        if desired_state is None:
            if pending_action is not None:
                state['pending_action'] = None
            return
        if pending_action is not None and pending_action != desired_state:
            state['pending_action'] = None
            pending_action = None
        if pending_action is None:
            if not self._wiper_should_trigger(desired_state, phase, state):
                return
            if now < state.get('cooldown_until', 0.0):
                return
            delay = self._wiper_action_delay(wiper_cfg, off_threshold)
            state['pending_action'] = desired_state
            state['pending_since'] = now
            state['pending_delay'] = delay
            state['last_trigger_phase'] = phase
            pending_action = desired_state
        pending_since = state.get('pending_since', now)
        pending_delay = state.get('pending_delay', 0.0)
        if pending_since is None:
            pending_since = now
            state['pending_since'] = now
        if now < state.get('cooldown_until', 0.0):
            return
        if now - pending_since < pending_delay:
            return
        if self._trigger_wiper_toggle(controller, bool(pending_action)):
            state['pending_action'] = None
            state['last_desired'] = pending_action
            state['last_action'] = now
            state['cooldown_until'] = now + 0.35

    def _precipitation_amount(self) -> Optional[float]:
        """Return the current precipitation amount when available."""
        value = self._read_ir_value('Precipitation')
        if value is None:
            return None
        if isinstance(value, (list, tuple, array)):
            values = [self._safe_float(item, 0.0) for item in value]
            return max(values) if values else 0.0
        if isinstance(value, numbers.Real):
            return float(value)
        return None

    def _reset_wiper_state(self) -> None:
        """Reset windshield wiper automation state."""
        state = self._wiper_state
        state['startup_checked'] = False
        state['last_on_track'] = None
        state['cooldown_until'] = 0.0
        state['last_action'] = None
        state['last_desired'] = None
        state['last_phase'] = None
        state['last_trigger_phase'] = None
        state['pending_action'] = None
        state['pending_since'] = None
        state['pending_delay'] = 0.0
        state['threshold_drift_on'] = 0.0
        state['threshold_drift_off'] = 0.0
        state['threshold_jitter_on'] = 0.0
        state['threshold_jitter_off'] = 0.0
        state['threshold_last_update'] = 0.0
        state['debug_last_log'] = 0.0

    def _wiper_apply_snapshot(self, controller: GenericController, wiper_cfg: Dict[str, Any]) -> bool:
        """Apply a one-time wiper toggle decision from current precipitation."""
        precipitation = self._precipitation_amount()
        if precipitation is None:
            return False
        self._wiper_process_precipitation(controller, wiper_cfg, self._wiper_state, precipitation)
        return True

    def _wiper_macro_loop(self) -> None:
        """Auto-toggle windshield wipers based on precipitation telemetry."""
        return
        interval_ms = 200
        try:
            _name, controller, tab = self._wiper_controller()
            if not controller or not tab or self.app_state != 'RUNNING' or (not self._commands_allowed()):
                interval_ms = 400
                self._reset_wiper_state()
                return
            tab_config = tab.get_config()
            wiper_cfg = tab_config.get('wiper_auto', {})
            if not wiper_cfg.get('enabled'):
                interval_ms = 450
                self._reset_wiper_state()
                return
            state = self._wiper_state
            if self._wiper_apply_snapshot(controller, wiper_cfg):
                state['startup_checked'] = True
        finally:
            self.root.after(interval_ms, self._wiper_macro_loop)

    @staticmethod
    def _ir_flag_value(name: str, default: int) -> int:
        """Return an iRacing flag enum value with a fallback for old SDKs."""
        flags = getattr(irsdk, 'Flags', None)
        if flags is None:
            return default
        if hasattr(flags, name):
            try:
                return int(getattr(flags, name))
            except Exception:
                return default
        if isinstance(flags, dict):
            try:
                return int(flags.get(name, default))
            except Exception:
                return default
        return default

    def _read_session_flags(self) -> Optional[int]:
        """Read SessionFlags as an unsigned bitfield."""
        value = self._read_ir_value('SessionFlags', use_cache=False)
        if isinstance(value, numbers.Real):
            return int(value) & 4294967295
        if isinstance(value, (list, tuple, array)):
            for item in value:
                if isinstance(item, numbers.Real):
                    return int(item) & 4294967295
        return None

    def _fuel_flag_state(self, session_flags: int) -> Tuple[bool, bool]:
        """Return (true yellow/caution active, green active) from SessionFlags."""
        caution_mask = 0
        for name, fallback in (('yellow', 8), ('yellow_waving', 256), ('caution', 16384), ('caution_waving', 32768)):
            caution_mask |= self._ir_flag_value(name, fallback)
        green_mask = 0
        for name, fallback in (('green', 4), ('start_go', 2147483648)):
            green_mask |= self._ir_flag_value(name, fallback)
        caution_now = bool(session_flags & caution_mask)
        green_now = bool(session_flags & green_mask)
        return (caution_now, green_now)

    def _fuel_restart_cue_state(self, session_flags: int) -> bool:
        """Return True while iRacing is holding a restart cue."""
        restart_mask = 0
        for name, fallback in (('one_lap_to_green', 512), ('green_held', 1024)):
            restart_mask |= self._ir_flag_value(name, fallback)
        return bool(session_flags & restart_mask)

    def _fuel_mixture_delay_range(self, fuel_cfg: Dict[str, Any], prefix: str, default_min: float, default_max: float) -> Tuple[float, float]:
        """Return a sanitized delay range for fuel mixture automation."""
        min_delay = self._safe_float(fuel_cfg.get(f'{prefix}_delay_min', default_min), default_min)
        max_delay = self._safe_float(fuel_cfg.get(f'{prefix}_delay_max', default_max), default_max)
        min_delay = max(0.0, min_delay)
        max_delay = max(0.0, max_delay)
        if max_delay < min_delay:
            min_delay, max_delay = (max_delay, min_delay)
        return (min_delay, max_delay)

    def _fuel_mixture_delay(self, fuel_cfg: Dict[str, Any], prefix: str, default_min: float, default_max: float) -> float:
        """Sample a randomized fuel mixture reaction delay."""
        min_delay, max_delay = self._fuel_mixture_delay_range(fuel_cfg, prefix, default_min, default_max)
        if max_delay <= min_delay:
            return min_delay

    @staticmethod
    def _fuel_mixture_is_value(value: Any, expected: int) -> bool:
        """Return True when a fuel mixture telemetry value equals expected."""
        if value is None:
            return False
        try:
            return int(round(float(value))) == int(expected)
        except Exception:
            return False

    @staticmethod
    def _clear_fuel_mixture_pending(state: Dict[str, Any]) -> None:
        """Clear a pending fuel mixture flag action."""
        state['pending_kind'] = None
        state['pending_since'] = None
        state['pending_delay'] = 0.0
        state['pending_target'] = None

    def _reset_fuel_mixture_state(self) -> None:
        """Reset fuel mixture flag automation state."""
        state = self._fuel_mixture_state
        state['last_caution'] = None
        state['last_flags'] = 0
        state['restart_armed_until'] = 0.0
        state['cooldown_until'] = 0.0
        self._clear_fuel_mixture_pending(state)

    def _schedule_fuel_mixture_flag_action(self, kind: str, target: int, delay: float) -> None:
        """Schedule a fuel mixture adjustment after a humanized delay."""
        state = self._fuel_mixture_state
        state['pending_kind'] = kind
        state['pending_since'] = time.time()
        state['pending_delay'] = max(0.0, delay)
        state['pending_target'] = target
        label = 'yellow' if kind == 'yellow' else 'restart'
        self.notify_overlay_status(f'Fuel Mix {label} -> {target} in {delay:.2f}s', 'orange')

    def _process_fuel_mixture_pending(self, controller: GenericController, caution_now: bool, now: float) -> None:
        """Fire a scheduled fuel mixture action when its delay expires."""
        state = self._fuel_mixture_state
        pending_kind = state.get('pending_kind')
        if pending_kind is None:
            return
        if pending_kind == 'yellow' and (not caution_now):
            self._clear_fuel_mixture_pending(state)
            return
        if pending_kind == 'green' and caution_now:
            self._clear_fuel_mixture_pending(state)
            return
        pending_since = state.get('pending_since')
        pending_delay = float(state.get('pending_delay', 0.0) or 0.0)
        if pending_since is None or now - pending_since < pending_delay:
            return
        if now < state.get('cooldown_until', 0.0):
            return
        target = state.get('pending_target')
        if target is None:
            self._clear_fuel_mixture_pending(state)
            return
        current = controller.read_telemetry(use_cache=False)
        if pending_kind == 'green' and (not self._fuel_mixture_is_value(current, 8)):
            self._clear_fuel_mixture_pending(state)
            return
        if pending_kind == 'yellow' and self._fuel_mixture_is_value(current, target):
            self._clear_fuel_mixture_pending(state)
            return
        controller.request_target(float(target))
        state['cooldown_until'] = now + 0.35
        self._clear_fuel_mixture_pending(state)
        self.notify_overlay_status(f'Fuel Mix -> {int(target)}', 'green')

    def _fuel_mixture_flag_loop(self) -> None:
        """Auto-select fuel mixture from yellow/green SessionFlags changes."""
        return
        interval_ms = 150
        try:
            controller = self.controllers.get(FUEL_MIXTURE_VAR)
            tab = self.tabs.get(FUEL_MIXTURE_VAR)
            if not controller or not tab or self.app_state != 'RUNNING' or (not self._commands_allowed()):
                interval_ms = 350
                self._reset_fuel_mixture_state()
                return
            tab_config = tab.get_config()
            fuel_cfg = tab_config.get('fuel_mixture_auto', {})
            yellow_enabled = bool(fuel_cfg.get('yellow_enabled', True))
            green_enabled = bool(fuel_cfg.get('green_enabled', True))
            if not yellow_enabled and (not green_enabled):
                interval_ms = 400
                self._reset_fuel_mixture_state()
                return
            session_flags = self._read_session_flags()
            if session_flags is None:
                interval_ms = 250
                return
            caution_now, green_now = self._fuel_flag_state(session_flags)
            restart_cue_now = self._fuel_restart_cue_state(session_flags)
            state = self._fuel_mixture_state
            now = time.time()
            last_caution = state.get('last_caution')
            if last_caution is None:
                state['last_caution'] = caution_now
                state['last_flags'] = session_flags
                return
            if caution_now and state.get('pending_kind') == 'green':
                self._clear_fuel_mixture_pending(state)
                state['restart_armed_until'] = 0.0
            if not caution_now and state.get('pending_kind') == 'yellow':
                self._clear_fuel_mixture_pending(state)
            if bool(last_caution) and (not caution_now):
                state['restart_armed_until'] = now + 5.0
            elif restart_cue_now and state.get('restart_armed_until', 0.0) > 0.0:
                state['restart_armed_until'] = now + 5.0
            pending_kind = state.get('pending_kind')
            if yellow_enabled and caution_now and (not bool(last_caution)) and (pending_kind is None) and (now >= state.get('cooldown_until', 0.0)):
                current = controller.read_telemetry(use_cache=False)
                if not self._fuel_mixture_is_value(current, 8):
                    delay = self._fuel_mixture_delay(fuel_cfg, 'yellow', 1.0, 2.0)
                    self._schedule_fuel_mixture_flag_action('yellow', 8, delay)
            pending_kind = state.get('pending_kind')
            restart_green = green_now and (bool(last_caution) and (not caution_now) or now <= state.get('restart_armed_until', 0.0))
            if green_enabled and restart_green and (pending_kind is None) and (now >= state.get('cooldown_until', 0.0)):
                current = controller.read_telemetry(use_cache=False)
                if current is None:
                    pass
                elif self._fuel_mixture_is_value(current, 8):
                    delay = self._fuel_mixture_delay(fuel_cfg, 'green', 0.25, 0.7)
                    self._schedule_fuel_mixture_flag_action('green', 1, delay)
                    state['restart_armed_until'] = 0.0
                else:
                    state['restart_armed_until'] = 0.0
            state['last_caution'] = caution_now
            state['last_flags'] = session_flags
            self._process_fuel_mixture_pending(controller, caution_now, now)
        finally:
            self.root.after(interval_ms, self._fuel_mixture_flag_loop)

    @staticmethod
    def _resolve_broadcast_msg_id(name: str) -> Optional[int]:
        broadcast = getattr(irsdk, 'BroadcastMsg', None)
        if broadcast is None:
            return None
        if hasattr(broadcast, name):
            return getattr(broadcast, name)
        if isinstance(broadcast, dict):
            return broadcast.get(name)
        return None

    @staticmethod
    def _resolve_pit_command(command: str) -> Optional[int]:
        pit_enum = getattr(irsdk, 'PitCommandMode', None)
        if pit_enum is None:
            pit_enum = getattr(irsdk, 'PitCommand', None)
        if pit_enum is None:
            return None
        candidates = {command, command.upper(), command.lower(), command.replace(' ', ''), command.replace(' ', '_'), command.replace('-', '_')}
        for name in candidates:
            if hasattr(pit_enum, name):
                return getattr(pit_enum, name)
        if isinstance(pit_enum, dict):
            for name in candidates:
                if name in pit_enum:
                    return pit_enum[name]
        return None

    def _send_pit_command(self, command: str, value: int) -> bool:
        """Send a pit command broadcast via the iRacing SDK."""
        if not getattr(self.ir, 'is_initialized', False):
            try:
                self.ir.startup()
            except Exception:
                return False
        resolved = self._resolve_pit_command(command)
        if resolved is None:
            return False
        try:
            if hasattr(self.ir, 'pit_command'):
                self.ir.pit_command(resolved, value)
            elif hasattr(self.ir, 'broadcast_msg'):
                msg_id = self._resolve_broadcast_msg_id('pit_command')
                if msg_id is None:
                    return False
                self.ir.broadcast_msg(msg_id, resolved, value)
            else:
                return False
        except Exception:
            return False
        return True

    @staticmethod
    def _clean_series_field(value: Any) -> str:
        """Return a compact string for iRacing series metadata."""
        if value in (None, ''):
            return ''
        text = str(value).strip()
        if not text or text.lower() in {'none', 'null', 'nan'}:
            return ''
        return text

    def _resolve_turbo_pit_series_context(self) -> Tuple[Optional[str], str]:
        """Return the active series preference key and status text."""
        try:
            weekend = self.ir['WeekendInfo']
        except Exception:
            return (None, 'Series: no active iRacing session detected')
        if not isinstance(weekend, dict):
            return (None, 'Series: no active iRacing session detected')
        series_id = self._clean_series_field(weekend.get('SeriesID'))
        series_name = ''
        for key in ('SeriesName', 'SeriesDisplayName', 'SeriesShortName', 'Series', 'SeasonName'):
            series_name = self._clean_series_field(weekend.get(key))
            if series_name:
                break
        if series_id in {'0', '-1'} and (not series_name):
            return (series_id, f'No active iRacing series detected (SeriesID {series_id})')
        if series_name and series_id:
            return (series_id, f'Applies to series: {series_name} (SeriesID {series_id})')
        if series_name:
            return (None, f'Series: {series_name} (SeriesID unavailable; using default setting)')
        if series_id:
            return (series_id, f'Applies to SeriesID: {series_id}')
        return (None, 'Series: no active series detected in this session')

    def _resolve_series_id_key(self) -> Optional[str]:
        """Return the active iRacing series key for Turbo Pit preferences."""
        series_key, _status_text = self._resolve_turbo_pit_series_context()
        return series_key

    def _update_turbo_pit_series_status(self, status_text: str) -> None:
        """Refresh the Turbo Pit per-series status label."""
        try:
            if self.turbo_pit_series_var.get() != status_text:
                self.turbo_pit_series_var.set(status_text)
        except Exception:
            pass

    def _sync_turbo_pit_state_for_series(self) -> None:
        """Apply per-series Turbo Pit state when telemetry reports a series change."""
        return
        series_key, status_text = self._resolve_turbo_pit_series_context()
        self._update_turbo_pit_series_status(status_text)
        if series_key == self._active_turbo_pit_series_key:
            return
        self._active_turbo_pit_series_key = series_key
        if not series_key:
            return
        enabled = self.turbo_pit_enabled_by_series.get(series_key, True)
        self.turbo_pit_enabled.set(enabled)

    def _on_turbo_pit_toggle(self) -> None:
        """Persist Turbo Pit checkbox state for the currently active series."""
        return
        enabled = bool(self.turbo_pit_enabled.get())
        series_key, status_text = self._resolve_turbo_pit_series_context()
        self._update_turbo_pit_series_status(status_text)
        self._active_turbo_pit_series_key = series_key
        if series_key:
            self.turbo_pit_enabled_by_series[series_key] = enabled
        self.schedule_save()

    def _turbo_pit_clear(self) -> None:
        """Clear tires + windshield pit options via broadcast."""
        self._send_pit_command(PIT_COMMAND_CLEAR_TIRES, 0)
        self._send_pit_command(PIT_COMMAND_CLEAR_WS, 0)

    def _turbo_pit_loop(self) -> None:
        """Run Turbo Pit auto-clear when entering pit road."""
        return
        interval_ms = 300
        try:
            self._sync_turbo_pit_state_for_series()
            if not self.turbo_pit_enabled.get() or self.app_state != 'RUNNING' or (not self._commands_allowed()):
                interval_ms = 500
                self._turbo_pit_state['last_on_pit'] = None
                return
            on_pit = self._bool_from_keys(['OnPitRoad', 'PlayerCarOnPitRoad'])
            if on_pit is None:
                return
            last_on_pit = self._turbo_pit_state.get('last_on_pit')
            if last_on_pit is None:
                self._turbo_pit_state['last_on_pit'] = on_pit
                return
            if on_pit and (not last_on_pit):
                self._turbo_pit_clear()
            self._turbo_pit_state['last_on_pit'] = on_pit
        finally:
            self.root.after(interval_ms, self._turbo_pit_loop)

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
            if min_val <= sample <= max_val:
                return sample
        return max(min_val, min(max_val, sample))

    def ui(self, fn: Callable, *args, **kwargs):
        """Thread-safe UI dispatcher."""
        self._uiq.put((fn, args, kwargs))

    def _drain_ui_queue(self):
        handled = 0
        while True:
            try:
                fn, args, kwargs = self._uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f'[UI] Handler error: {exc}')
            finally:
                handled += 1
        next_interval = 20 if handled else 60
        self.root.after(next_interval, self._drain_ui_queue)

    def _popups_enabled(self) -> bool:
        return not self.disable_popups.get()

    def _show_info(self, title: str, message: str) -> None:
        if self._popups_enabled():
            messagebox.showinfo(title, message)

    def _show_warning(self, title: str, message: str) -> None:
        if self._popups_enabled():
            messagebox.showwarning(title, message)

    def _show_error(self, title: str, message: str) -> None:
        if self._popups_enabled():
            messagebox.showerror(title, message)

    def _ask_yes_no(self, title: str, message: str) -> bool:
        if not self._popups_enabled():
            print(f'[Popups Disabled] Auto-confirming Yes/No prompt: {title} - {message}')
            return True
        return messagebox.askyesno(title, message)

    def _ask_ok_cancel(self, title: str, message: str) -> bool:
        if not self._popups_enabled():
            print(f'[Popups Disabled] Auto-confirming OK/Cancel prompt: {title} - {message}')
            return True
        return messagebox.askokcancel(title, message)

    def schedule_update_check(self) -> None:
        """Kick off a background update check and schedule the next one."""
        self._check_for_updates_async(notify_when_current=False)
        delay_ms = int(GITHUB_UPDATE_CHECK_INTERVAL_SECONDS * 1000)
        self.root.after(delay_ms, self.schedule_update_check)

    def check_for_updates_now(self) -> None:
        """User-triggered update check."""
        self._check_for_updates_async(notify_when_current=True)

    def _check_for_updates_async(self, notify_when_current: bool) -> None:
        """Run GitHub latest-release check in a worker thread."""
        if self._update_check_running:
            if notify_when_current:
                messagebox.showinfo('Update Check', 'An update check is already running. Please wait.')
            return
        self._update_check_running = True

        def _worker():
            result = self._fetch_latest_release_version()
            self.ui(self._handle_update_result, result, notify_when_current)
        threading.Thread(target=_worker, name='github-release-check', daemon=True).start()

    def _fetch_latest_release_version(self) -> Dict[str, Any]:
        """Fetch and parse latest release metadata from GitHub."""
        request = urllib.request.Request(
            GITHUB_RELEASES_API_LATEST,
            headers={
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': GITHUB_API_VERSION,
                'User-Agent': f'{APP_NAME}/{APP_VERSION}',
            },
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            return {'ok': False, 'error': f'GitHub HTTP error: {exc.code}'}
        except urllib.error.URLError as exc:
            return {'ok': False, 'error': f'Network error: {exc.reason}'}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        version = str(payload.get('tag_name', '')).strip()
        html_url = str(payload.get('html_url', '')).strip() or GITHUB_RELEASES_PAGE_LATEST
        published = str(payload.get('published_at', '')).strip()
        if not version:
            return {'ok': False, 'error': 'No release tag_name found in GitHub response.'}
        return {'ok': True, 'version': version, 'html_url': html_url, 'published_at': published}

    def _handle_update_result(self, result: Dict[str, Any], notify_when_current: bool) -> None:
        """Update UI state after the worker completes."""
        self._update_check_running = False
        if not result.get('ok'):
            if notify_when_current:
                messagebox.showwarning('Update Check Failed', f"Could not check for updates.\n\n{result.get('error', 'Unknown error')}")
            return
        latest_version = str(result.get('version', '')).strip()
        self._latest_release_version = latest_version
        has_update = _parse_version_tuple(latest_version) > _parse_version_tuple(APP_VERSION)
        if has_update:
            published = result.get('published_at') or 'unknown date'
            answer = messagebox.askyesno(
                'Update Available',
                f'Current version: {APP_VERSION}\nLatest version: {latest_version}\nPublished: {published}\n\nOpen the latest release page now?',
            )
            if answer:
                webbrowser.open(result.get('html_url', GITHUB_RELEASES_PAGE_LATEST))
            return
        if notify_when_current:
            messagebox.showinfo(
                'No Updates Found',
                f'You are on the latest version.\n\nCurrent version: {APP_VERSION}\nLatest release: {latest_version}',
            )

    def _create_menu(self):
        """Create application menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        options_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label='Options', menu=options_menu)
        options_menu.add_command(label='Timing Adjustments', command=self.open_timing_window)
        options_menu.add_separator()
        options_menu.add_command(label='Show/Hide Overlay', command=self.toggle_overlay)
        options_menu.add_command(label='Restart Application', command=restart_program)
        options_menu.add_separator()
        options_menu.add_command(label='Check for Updates', command=self.check_for_updates_now)
        options_menu.add_command(label='Open Latest Release Page', command=lambda: webbrowser.open(GITHUB_RELEASES_PAGE_LATEST))
        options_menu.add_separator()
        options_menu.add_command(label='Restore Defaults (Delete Config)', command=self.restore_defaults)

    def _configure_styles(self) -> None:
        """Improve UI readability with consistent fonts and spacing."""
        base_font = ('Segoe UI', 10)
        heading_font = ('Segoe UI Semibold', 10)
        button_font = ('Segoe UI Semibold', 10)
        self.root.option_add('*Font', base_font)
        self.root.option_add('*Label.Font', base_font)
        self.root.option_add('*LabelFrame.Font', heading_font)
        self.root.option_add('*Button.Font', button_font)
        self.root.option_add('*Button.Padx', 10)
        self.root.option_add('*Button.Pady', 4)
        self.root.option_add('*Button.BorderWidth', 1)
        self.root.option_add('*Button.Relief', 'raised')
        self.root.option_add('*Checkbutton.Font', base_font)
        self.root.option_add('*Radiobutton.Font', base_font)
        self.root.option_add('*Entry.Font', base_font)
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TLabel', font=base_font)
        style.configure('TLabelFrame.Label', font=heading_font)
        style.configure('TButton', font=button_font, padding=(10, 6))
        style.configure('TCheckbutton', font=base_font, padding=(6, 2))
        style.configure('TRadiobutton', font=base_font, padding=(6, 2))
        style.configure('TNotebook.Tab', font=heading_font, padding=(10, 6))
        style.configure('Control.TNotebook.Tab', font=('Segoe UI Semibold', 9), padding=(7, 5))
        style.configure('TCombobox', padding=4)
        style.map('TCombobox', fieldbackground=[('disabled', '#e4e4e4')], foreground=[('disabled', '#7a7a7a')])
        style.configure('PresetLoad.TButton', font=button_font, padding=(10, 6), background='#e0e0e0', foreground='#000000')
        style.map('PresetLoad.TButton', background=[('disabled', '#c8c8c8'), ('!disabled', '#e0e0e0')], foreground=[('disabled', '#7a7a7a'), ('!disabled', '#000000')])

    def _create_main_ui(self):
        """Create main user interface."""
        header_frame = tk.Frame(self.root, pady=8)
        header_frame.pack(fill='x', padx=14)
        header_frame.columnconfigure(0, weight=1)
        title_box = tk.Frame(header_frame)
        title_box.grid(row=0, column=0, sticky='w')
        tk.Label(title_box, text='Dominant Control', font=('Segoe UI Semibold', 18)).pack(anchor='w')
        self.header_context_var = tk.StringVar(value='No car  /  No track')
        tk.Label(title_box, textvariable=self.header_context_var, fg='#5f6b7a', font=('Segoe UI', 10)).pack(anchor='w')
        mode_frame = tk.Frame(header_frame)
        mode_frame.grid(row=0, column=1, sticky='e')
        self.btn_mode = tk.Button(mode_frame, text='Mode: RUNNING', bg='#90ee90', command=self.toggle_mode, font=('Segoe UI Semibold', 10), width=28, height=2)
        self.btn_mode.pack(side='right')
        self.main_tabs = ttk.Notebook(self.root)
        self.main_tabs.pack(fill='both', expand=True, padx=14, pady=(0, 10))
        main_tab = ttk.Frame(self.main_tabs)
        controls_tab = ttk.Frame(self.main_tabs)
        combos_tab = ttk.Frame(self.main_tabs)
        hud_tab = ttk.Frame(self.main_tabs)
        options_tab = ttk.Frame(self.main_tabs)
        diagnostics_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(main_tab, text='Main')
        self.main_tabs.add(controls_tab, text='Controls')
        self.main_tabs.add(combos_tab, text='Combos')
        self.main_tabs.add(hud_tab, text='HUD')
        self.main_tabs.add(options_tab, text='Options')
        self.main_tabs.add(diagnostics_tab, text='Diagnostics')
        self.controls_tab = controls_tab
        self.combos_tab = combos_tab
        self.hud_tab = hud_tab
        self.options_tab = options_tab
        self.diagnostics_tab = diagnostics_tab
        self.main_tabs.bind('<<NotebookTabChanged>>', lambda _event: self._refresh_diagnostics_tab())
        setup_container = tk.Frame(main_tab)
        setup_container.pack(fill='both', expand=True, padx=5, pady=(5, 2))
        setup_container.columnconfigure(0, weight=1)
        setup_container.columnconfigure(1, weight=0)
        setup_container.rowconfigure(0, weight=1)
        steps_column = tk.Frame(setup_container)
        steps_column.grid(row=0, column=0, columnspan=2, sticky='nsew')
        steps_column.columnconfigure(0, weight=1)
        options_column = tk.Frame(setup_container)
        options_column.grid(row=0, column=1, sticky='nsew')
        options_column.columnconfigure(0, weight=1)
        options_column.grid_remove()
        self.presets_frame = tk.LabelFrame(steps_column, text='Step 1: Choose your car and track')
        self.presets_frame.pack(fill='x', pady=(0, 8))
        selector_frame = tk.Frame(self.presets_frame)
        selector_frame.pack(fill='x', padx=5, pady=2)
        selector_frame.columnconfigure(1, weight=2)
        selector_frame.columnconfigure(3, weight=2)
        tk.Label(selector_frame, text='Car:').grid(row=0, column=0, sticky='w')
        self.combo_car = ttk.Combobox(selector_frame, width=30)
        self.combo_car.grid(row=0, column=1, sticky='ew', padx=5)
        self.combo_car.bind('<<ComboboxSelected>>', self.on_car_selected)
        tk.Label(selector_frame, text='Track:').grid(row=0, column=2, sticky='w')
        self.combo_track = ttk.Combobox(selector_frame, width=30)
        self.combo_track.grid(row=0, column=3, sticky='ew', padx=5)
        self.combo_track.bind('<<ComboboxSelected>>', self.on_track_selected)
        tk.Label(selector_frame, text='Surface:').grid(row=0, column=4, sticky='w')
        self.combo_surface = ttk.Combobox(selector_frame, width=8, values=list(SURFACE_PRESET_KEYS), state='readonly')
        self.combo_surface.grid(row=0, column=5, sticky='ew', padx=(5, 0))
        self.combo_surface.set(DEFAULT_SURFACE_PRESET)
        self.combo_surface.bind('<<ComboboxSelected>>', self.on_surface_selected)
        actions_frame = tk.Frame(self.presets_frame)
        actions_frame.pack(fill='x', padx=5, pady=5)
        self.btn_load_preset = ttk.Button(actions_frame, text='Load', command=self.action_load_preset, style='PresetLoad.TButton')
        self.btn_load_preset.pack(side='left', expand=True, fill='x', padx=2)
        self.btn_save_preset = tk.Button(actions_frame, text='Save Current', command=self.action_save_preset, bg='#ADD8E6')
        self.btn_save_preset.pack(side='left', expand=True, fill='x', padx=2)
        self.btn_clear_preset = tk.Button(actions_frame, text='Clear', command=self.action_clear_preset, bg='#ffcccc')
        self.btn_clear_preset.pack(side='left', expand=True, fill='x', padx=2)
        preset_io_frame = tk.Frame(self.presets_frame)
        preset_io_frame.pack(fill='x', padx=5, pady=(0, 5))
        tk.Button(preset_io_frame, text='Import', command=self.action_import_preset, bg='#f0f0f0').pack(side='left', expand=True, fill='x', padx=2)
        tk.Button(preset_io_frame, text='Export', command=self.action_export_preset, bg='#f0f0f0').pack(side='left', expand=True, fill='x', padx=2)
        self.devices_frame = tk.LabelFrame(steps_column, text='Step 2: Confirm input devices (joystick/wheel)')
        self.devices_frame.pack(fill='x', pady=(0, 8))
        self.check_safe = tk.Checkbutton(self.devices_frame, text='Keyboard Only Mode (requires restart)', variable=self.use_keyboard_only, command=self.trigger_safe_mode_update)
        self.check_safe.pack(anchor='w', padx=8, pady=(6, 2))
        tk.Button(self.devices_frame, text='🎮 Manage Devices', command=self.open_device_manager, bg='#e0e0e0').pack(fill='x', padx=5, pady=5)
        self._update_preset_lock_state()
        self.scan_frame = tk.LabelFrame(steps_column, text='Step 3: Scan driver controls')
        self.scan_frame.pack(fill='x')
        self.btn_scan = tk.Button(self.scan_frame, text='Scan controls for the selected car', command=self.scan_driver_controls, bg='lightblue')
        self.btn_scan.pack(fill='x', padx=5, pady=5)
        tk.Label(self.scan_frame, text='Tip: Scan after changing devices or presets to keep bindings in sync.', fg='gray', font=('Arial', 9)).pack(fill='x', padx=8, pady=(0, 6))
        controls_container = tk.Frame(controls_tab)
        controls_container.pack(fill='both', expand=True, padx=5, pady=5)
        controls_header = tk.Frame(controls_container)
        controls_header.pack(fill='x', pady=(0, 6))
        tk.Label(controls_header, text='Driver Controls', font=('Segoe UI Semibold', 12)).pack(side='left')
        tk.Label(controls_header, text='Each scanned iRacing control gets its own page.', fg='gray').pack(side='left', padx=10)
        self.notebook = ttk.Notebook(controls_container, style='Control.TNotebook')
        self.notebook.pack(fill='both', expand=True, padx=0, pady=0)
        self.notebook.bind('<Configure>', self._schedule_control_tab_label_refresh)
        self.combo_page_container = tk.Frame(combos_tab)
        self.combo_page_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.overlay_page_container = tk.Frame(hud_tab)
        self.overlay_page_container.pack(fill='both', expand=True, padx=5, pady=5)
        diagnostics_container = tk.Frame(diagnostics_tab)
        diagnostics_container.pack(fill='both', expand=True, padx=10, pady=10)
        diagnostics_container.columnconfigure(0, weight=1)
        diagnostics_card = tk.LabelFrame(diagnostics_container, text='Runtime Diagnostics')
        diagnostics_card.grid(row=0, column=0, sticky='nsew')
        self.diagnostics_text = tk.Text(diagnostics_card, height=16, wrap='none', font=('Consolas', 10), relief='flat')
        self.diagnostics_text.pack(fill='both', expand=True, padx=8, pady=8)
        diagnostics_actions = tk.Frame(diagnostics_container)
        diagnostics_actions.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        tk.Button(diagnostics_actions, text='Refresh', command=self._refresh_diagnostics_tab).pack(side='left')
        tk.Button(diagnostics_actions, text='Save Config', command=self.save_config).pack(side='left', padx=6)
        options_canvas = tk.Canvas(options_tab, highlightthickness=0)
        options_scrollbar = ttk.Scrollbar(options_tab, orient='vertical', command=options_canvas.yview)
        options_canvas.configure(yscrollcommand=options_scrollbar.set)
        options_scrollbar.pack(side='right', fill='y')
        options_canvas.pack(side='left', fill='both', expand=True)
        options_container = tk.Frame(options_canvas)
        options_window = options_canvas.create_window((0, 0), window=options_container, anchor='nw')

        def _on_options_container_configure(_event):
            options_canvas.configure(scrollregion=options_canvas.bbox('all'))

        def _on_options_canvas_configure(event):
            options_canvas.itemconfigure(options_window, width=event.width)
        options_container.bind('<Configure>', _on_options_container_configure)
        options_canvas.bind('<Configure>', _on_options_canvas_configure)
        bind_mousewheel_scroll(options_tab, options_canvas)
        options_notebook = ttk.Notebook(options_container)
        options_notebook.pack(fill='both', expand=True, padx=5, pady=5)
        general_tab = ttk.Frame(options_notebook)
        options_notebook.add(general_tab, text='General Settings')
        privacy_tab = ttk.Frame(options_notebook)
        options_notebook.add(privacy_tab, text='Extras')
        general_container = tk.Frame(general_tab)
        general_container.pack(fill='both', expand=True, padx=5, pady=5)
        general_container.columnconfigure(0, weight=1)
        general_left = tk.LabelFrame(general_container, text='Presets & Devices')
        general_left.grid(row=0, column=0, sticky='nsew', pady=(0, 8))
        tk.Checkbutton(general_left, text='Auto-save preset edits (hotkeys/macros)', variable=self.auto_save_presets, command=self.schedule_save).pack(anchor='w', padx=8, pady=(8, 2))
        tk.Checkbutton(general_left, text='Lock car/track selection (auto-managed)', variable=self.lock_preset_selection, command=self._on_lock_preset_selection_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(general_left, text='Auto-detect car/track via iRacing', variable=self.auto_detect, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(general_left, text='Auto-scan when car/track changes', variable=self.auto_scan_on_change, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(general_left, text='Show getting started popup on startup', variable=self.show_getting_started, command=self.schedule_save).pack(anchor='w', padx=8, pady=(2, 8))
        self.stability_frame = tk.LabelFrame(general_container, text='Automation & Shortcuts')
        self.stability_frame.grid(row=1, column=0, sticky='nsew')
        tk.Button(self.stability_frame, text='Timing Adjustments', command=self.open_timing_window).pack(fill='x', padx=8, pady=(8, 6))
        tk.Checkbutton(self.stability_frame, text='Restart before rescanning controls (after the first scan)', variable=self.auto_restart_on_rescan, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Auto-restart and scan when joining a Race session', variable=self.auto_restart_on_race, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Restart + rescan when on-track telemetry goes true', variable=self.auto_restart_on_track_ready, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Block commands when IsOnTrackCar is false', variable=self.block_off_track_commands, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Show scan completion popup', variable=self.show_scan_popup, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Keep scanning until driver controls are detected', variable=self.keep_scanning_until_valid, command=self._on_keep_scanning_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Start with Windows', variable=self.start_with_windows, command=self._on_startup_toggle).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Focus app window on startup/restart', variable=self.focus_on_start, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Keep trying to reach hotkey targets (no timeout)', variable=self.keep_trying_targets, command=self.schedule_save).pack(anchor='w', padx=8, pady=2)
        tk.Checkbutton(self.stability_frame, text='Disable all pop-up notifications', variable=self.disable_popups, command=self._on_disable_popups_toggle).pack(anchor='w', padx=8, pady=2)
        clear_frame = tk.Frame(self.stability_frame)
        clear_frame.pack(fill='x', padx=8, pady=6)
        tk.Label(clear_frame, text='Clear target hotkey (optional):').pack(side='left')
        self.btn_clear_target_bind = tk.Button(clear_frame, text='Set Clear Hotkey', width=18, command=self._set_clear_target_bind)
        self.btn_clear_target_bind.pack(side='left', padx=6)
        rescan_frame = tk.Frame(self.stability_frame)
        rescan_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(rescan_frame, text='Manual rescan hotkey (restart + scan + load preset):').pack(side='left')
        self.btn_manual_rescan_bind = tk.Button(rescan_frame, text='Set Rescan Hotkey', width=18, command=self._set_manual_rescan_bind)
        self.btn_manual_rescan_bind.pack(side='left', padx=6)
        surface_frame = tk.Frame(self.stability_frame)
        surface_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(surface_frame, text='Dry/Wet toggle hotkey (switch preset surface):').pack(side='left')
        self.btn_surface_toggle_bind = tk.Button(surface_frame, text='Set Dry/Wet Hotkey', width=18, command=self._set_surface_toggle_bind)
        self.btn_surface_toggle_bind.pack(side='left', padx=6)
        surface_dry_frame = tk.Frame(self.stability_frame)
        surface_dry_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(surface_dry_frame, text='Dry-only hotkey (switch to DRY preset):').pack(side='left')
        self.btn_surface_dry_bind = tk.Button(surface_dry_frame, text='Set Dry Hotkey', width=18, command=self._set_surface_dry_bind)
        self.btn_surface_dry_bind.pack(side='left', padx=6)
        surface_wet_frame = tk.Frame(self.stability_frame)
        surface_wet_frame.pack(fill='x', padx=8, pady=(0, 8))
        tk.Label(surface_wet_frame, text='Wet-only hotkey (switch to WET preset):').pack(side='left')
        self.btn_surface_wet_bind = tk.Button(surface_wet_frame, text='Set Wet Hotkey', width=18, command=self._set_surface_wet_bind)
        self.btn_surface_wet_bind.pack(side='left', padx=6)
        privacy_container = tk.Frame(privacy_tab)
        privacy_container.pack(fill='both', expand=True, padx=10, pady=10)
        privacy_container.columnconfigure(0, weight=1)
        self.btn_discreet_mode = tk.Button(privacy_container, text='Layout: Standard', command=self.toggle_discreet_mode, bg='#f0f0f0', height=2)
        self.btn_discreet_mode.pack(fill='x')
        if not self.active_vars:
            self.active_vars = [('dcBrakeBias', True, False)]
        initial_tab_configs = None
        initial_combo_config = None
        initial_car = self.current_car.strip()
        initial_track = self.current_track.strip()
        if initial_car and initial_track and (initial_car in self.saved_presets) and (initial_track in self.saved_presets[initial_car]):
            entry = self._ensure_track_surface_presets(initial_car, initial_track)
            initial_surface = self._normalize_surface_label(self.current_surface or entry.get('active_surface'))
            preset_data = entry['surface_presets'].get(initial_surface, {})
            preset_vars = preset_data.get('active_vars')
            if preset_vars:
                self.active_vars = [_normalize_var_tuple(item) for item in preset_vars]
            initial_tab_configs = preset_data.get('tabs', {})
            initial_combo_config = preset_data.get('combo', {})
            self._last_loaded_preset_signature = (initial_car, initial_track, initial_surface)
            self._last_loaded_preset_time = time.time()
        self.rebuild_tabs(self.active_vars, tab_configs=initial_tab_configs, combo_config=initial_combo_config)
        if initial_tab_configs:
            self._apply_car_key_config(initial_car)
            self.register_current_listeners()
        self.update_preset_ui()
        self._refresh_clear_target_bind_button()
        self._refresh_manual_rescan_bind_button()
        self._refresh_surface_toggle_bind_button()
        self._refresh_surface_dry_bind_button()
        self._refresh_surface_wet_bind_button()
        self._update_header_context()
        self._refresh_diagnostics_tab()
        self._apply_discreet_mode()

    def _update_header_context(self) -> None:
        """Refresh the compact car/track context in the app header."""
        if not hasattr(self, 'header_context_var'):
            return
        car = self.current_car or 'No car'
        track = self.current_track or 'No track'
        self.header_context_var.set(f'{car}  /  {track}')

    def _refresh_diagnostics_tab(self) -> None:
        """Refresh the diagnostics page when it is visible or requested."""
        text_widget = getattr(self, 'diagnostics_text', None)
        if text_widget is None:
            return
        input_thread = getattr(input_manager, '_input_thread', None)
        try:
            ui_queue_size = self._uiq.qsize()
        except Exception:
            ui_queue_size = 0
        try:
            callback_pending = _CALLBACK_DISPATCHER.pending()
        except Exception:
            callback_pending = 0
        lines = [f'App state: {self.app_state}', f"SDK initialized: {getattr(self.ir, 'is_initialized', False)}", f"SDK connected: {getattr(self.ir, 'is_connected', None)}", f"Telemetry active: {getattr(self, '_telemetry_active', False)}", f"Scan in progress: {getattr(self, '_scan_in_progress', False)}", f'Controls: {len(self.controllers)}', f'Tk UI queue: {ui_queue_size}', f'Callback queue: {callback_pending}', f'Input thread alive: {bool(input_thread and input_thread.is_alive())}', f"Current car: {self.current_car or '-'}", f"Current track: {self.current_track or '-'}", f'Surface: {self.current_surface}', f'Config: {CONFIG_FILE}']
        text_widget.configure(state='normal')
        text_widget.delete('1.0', tk.END)
        text_widget.insert('1.0', '\n'.join(lines))
        text_widget.configure(state='disabled')

    def open_getting_started_window(self) -> None:
        """Open the getting started guide in a popup window."""
        if not self._popups_enabled():
            return
        if self.getting_started_window and self.getting_started_window.winfo_exists():
            self.getting_started_window.lift()
            return
        self.getting_started_window = tk.Toplevel(self.root)
        self.getting_started_window.title('Getting Started')
        self.getting_started_window.geometry('760x360')
        self.getting_started_window.transient(self.root)

        def _cleanup():
            if self.getting_started_window and self.getting_started_window.winfo_exists():
                self.getting_started_window.destroy()
            self.getting_started_window = None
        self.getting_started_window.protocol('WM_DELETE_WINDOW', _cleanup)
        container = tk.Frame(self.getting_started_window)
        container.pack(fill='both', expand=True, padx=12, pady=12)
        tk.Label(container, text=self.getting_started_text, wraplength=720, justify='left').pack(fill='x', pady=(0, 12))
        tk.Button(container, text='Close', command=_cleanup, bg='#e0e0e0').pack(anchor='e')

    def _maybe_show_getting_started(self) -> None:
        """Show the getting started popup once if enabled."""
        if not self.show_getting_started.get() or not self._popups_enabled():
            return
        self.open_getting_started_window()
        self.show_getting_started.set(False)
        self.schedule_save()

    def _on_output_selected(self, *_):
        self._apply_audio_preferences()
        self.schedule_save()

    def toggle_mode(self):
        """Toggle between RUNNING and CONFIG modes."""
        if self.app_state == 'RUNNING':
            self.app_state = 'CONFIG'
            self._stop_hybrid_hold()
            self.btn_mode.config(text='Mode: CONFIG (Click to Save & Run)', bg='orange')
            input_manager.active = False
            self._clear_keyboard_hotkeys()
        else:
            self.app_state = 'RUNNING'
            self.btn_mode.config(text='Mode: RUNNING', bg='#90ee90')
            input_manager.active = True
            self.register_current_listeners()
        editing = self.app_state == 'CONFIG'
        for tab in self.tabs.values():
            tab.set_editing_state(editing)
        if self.combo_tab:
            self.combo_tab.set_editing_state(editing)

    def focus_window(self):
        """Force focus to main window."""
        self.root.focus_force()

    def _close_app(self):
        """Close the application window and exit."""
        self._stop_hybrid_hold()
        if self._auto_save_job:
            try:
                self.root.after_cancel(self._auto_save_job)
            except Exception:
                pass
            self._auto_save_current_preset()
        self.save_config()
        self.root.destroy()

    def _minimize_to_tray(self, _event=None):
        """Minimize the window instead of closing it."""
        if self.root.state() != 'iconic':
            self.root.iconify()
        return 'break'

    def _handle_minimize_event(self, _event):
        if self.root.state() == 'iconic':
            self._minimize_to_tray()

    def _iter_hotkey_bindings(self) -> Iterator[Tuple[str, str, str]]:
        """Yield (code, label, source_id) for all hotkey bindings."""
        app_bindings = [('clear_target_bind', 'Clear target hotkey'), ('manual_rescan_bind', 'Manual rescan hotkey'), ('surface_toggle_bind', 'Dry/Wet toggle hotkey'), ('surface_dry_bind', 'Dry preset hotkey'), ('surface_wet_bind', 'Wet preset hotkey')]
        for attr_name, label in app_bindings:
            code = getattr(self, attr_name)
            if code:
                yield (code, label, f'app:{attr_name}')
        for tab in self.tabs.values():
            label_prefix = getattr(tab, 'label_name', 'Control')
            manual_increase_bind = getattr(tab, 'manual_increase_bind', None)
            if manual_increase_bind:
                yield (manual_increase_bind, f'{label_prefix} ghost +' if not getattr(tab, 'uses_toggle_key', False) else f'{label_prefix} ghost toggle', getattr(tab, '_manual_increase_source_id', f'control-manual:{id(tab)}:increase'))
            manual_decrease_bind = getattr(tab, 'manual_decrease_bind', None)
            if manual_decrease_bind:
                yield (manual_decrease_bind, f'{label_prefix} ghost -', getattr(tab, '_manual_decrease_source_id', f'control-manual:{id(tab)}:decrease'))
            for idx, row in enumerate(tab.preset_rows, start=1):
                code = row.get('bind')
                if code:
                    preset_label = f'{label_prefix} reset' if row.get('is_reset') else f'{label_prefix} preset {idx}'
                    source_id = row.get('source_id', f'control:{id(row)}')
                    yield (code, preset_label, source_id)
        if self.combo_tab:
            for idx, row in enumerate(self.combo_tab.preset_rows, start=1):
                code = row.get('bind')
                if code:
                    preset_label = 'Combo reset' if row.get('is_reset') else f'Combo preset {idx}'
                    source_id = row.get('source_id', f'combo:{id(row)}')
                    yield (code, preset_label, source_id)

    def _find_hotkey_conflict(self, code: str, current_source: Optional[str]) -> Optional[str]:
        """Return a conflicting label if the hotkey is already in use."""
        for existing_code, label, source_id in self._iter_hotkey_bindings():
            if existing_code == code and source_id != current_source:
                return label
        return None

    def _refresh_clear_target_bind_button(self):
        """Update the clear-target hotkey button text/color."""
        if not self.btn_clear_target_bind:
            return
        if self.clear_target_bind:
            bg_color = '#90ee90' if 'JOY' in self.clear_target_bind else '#ADD8E6'
            self.btn_clear_target_bind.config(text=self.clear_target_bind, bg=bg_color)
        else:
            self.btn_clear_target_bind.config(text='Set Clear Hotkey', bg='#f0f0f0')

    def _refresh_manual_rescan_bind_button(self):
        """Update the manual-rescan hotkey button text/color."""
        if not self.btn_manual_rescan_bind:
            return
        if self.manual_rescan_bind:
            bg_color = '#90ee90' if 'JOY' in self.manual_rescan_bind else '#ADD8E6'
            self.btn_manual_rescan_bind.config(text=self.manual_rescan_bind, bg=bg_color)
        else:
            self.btn_manual_rescan_bind.config(text='Set Rescan Hotkey', bg='#f0f0f0')

    def _refresh_surface_toggle_bind_button(self):
        """Update the Dry/Wet toggle hotkey button text/color."""
        if not self.btn_surface_toggle_bind:
            return
        if self.surface_toggle_bind:
            bg_color = '#90ee90' if 'JOY' in self.surface_toggle_bind else '#ADD8E6'
            self.btn_surface_toggle_bind.config(text=self.surface_toggle_bind, bg=bg_color)
        else:
            self.btn_surface_toggle_bind.config(text='Set Dry/Wet Hotkey', bg='#f0f0f0')

    def _refresh_surface_dry_bind_button(self):
        """Update the Dry-only hotkey button text/color."""
        if not self.btn_surface_dry_bind:
            return
        if self.surface_dry_bind:
            bg_color = '#90ee90' if 'JOY' in self.surface_dry_bind else '#ADD8E6'
            self.btn_surface_dry_bind.config(text=self.surface_dry_bind, bg=bg_color)
        else:
            self.btn_surface_dry_bind.config(text='Set Dry Hotkey', bg='#f0f0f0')

    def _refresh_surface_wet_bind_button(self):
        """Update the Wet-only hotkey button text/color."""
        if not self.btn_surface_wet_bind:
            return
        if self.surface_wet_bind:
            bg_color = '#90ee90' if 'JOY' in self.surface_wet_bind else '#ADD8E6'
            self.btn_surface_wet_bind.config(text=self.surface_wet_bind, bg=bg_color)
        else:
            self.btn_surface_wet_bind.config(text='Set Wet Hotkey', bg='#f0f0f0')

    def _refresh_wiper_debug_bind_button(self):
        """Update the wiper debug hotkey button text/color."""
        return
        if not self.btn_wiper_debug_bind:
            return
        if self.wiper_debug_bind:
            bg_color = '#90ee90' if 'JOY' in self.wiper_debug_bind else '#ADD8E6'
            self.btn_wiper_debug_bind.config(text=self.wiper_debug_bind, bg=bg_color)
        else:
            self.btn_wiper_debug_bind.config(text='Set Wiper Debug Hotkey', bg='#f0f0f0')

    def _set_clear_target_bind(self):
        """Capture an optional hotkey for clearing target attempts."""
        if self.app_state != 'CONFIG':
            self._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.focus_window()
        previous_bind = self.clear_target_bind
        if self.btn_clear_target_bind:
            self.btn_clear_target_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:clear_target_bind')
            if conflict:
                self._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self.clear_target_bind = previous_bind
                self._refresh_clear_target_bind_button()
                return
            self.clear_target_bind = code
        elif code == 'CANCEL':
            self.clear_target_bind = None
        self._refresh_clear_target_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_surface_toggle_bind(self):
        """Capture an optional hotkey for toggling DRY/WET presets."""
        if self.app_state != 'CONFIG':
            self._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.focus_window()
        previous_bind = self.surface_toggle_bind
        if self.btn_surface_toggle_bind:
            self.btn_surface_toggle_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:surface_toggle_bind')
            if conflict:
                self._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self.surface_toggle_bind = previous_bind
                self._refresh_surface_toggle_bind_button()
                return
            self.surface_toggle_bind = code
        elif code == 'CANCEL':
            self.surface_toggle_bind = None
        self._refresh_surface_toggle_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_surface_dry_bind(self):
        """Capture an optional hotkey for switching to DRY presets."""
        if self.app_state != 'CONFIG':
            self._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.focus_window()
        previous_bind = self.surface_dry_bind
        if self.btn_surface_dry_bind:
            self.btn_surface_dry_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:surface_dry_bind')
            if conflict:
                self._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self.surface_dry_bind = previous_bind
                self._refresh_surface_dry_bind_button()
                return
            self.surface_dry_bind = code
        elif code == 'CANCEL':
            self.surface_dry_bind = None
        self._refresh_surface_dry_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_surface_wet_bind(self):
        """Capture an optional hotkey for switching to WET presets."""
        if self.app_state != 'CONFIG':
            self._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.focus_window()
        previous_bind = self.surface_wet_bind
        if self.btn_surface_wet_bind:
            self.btn_surface_wet_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:surface_wet_bind')
            if conflict:
                self._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self.surface_wet_bind = previous_bind
                self._refresh_surface_wet_bind_button()
                return
            self.surface_wet_bind = code
        elif code == 'CANCEL':
            self.surface_wet_bind = None
        self._refresh_surface_wet_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def _set_wiper_debug_bind(self):
        """Capture an optional hotkey for wiper precipitation debug."""
        return
        if self.app_state != 'CONFIG':
            self._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.focus_window()
        previous_bind = self.wiper_debug_bind
        if self.btn_wiper_debug_bind:
            self.btn_wiper_debug_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:wiper_debug_bind')
            if conflict:
                self._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self.wiper_debug_bind = previous_bind
                self._refresh_wiper_debug_bind_button()
                return
            self.wiper_debug_bind = code
        elif code == 'CANCEL':
            self.wiper_debug_bind = None
        self._refresh_wiper_debug_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def clear_all_targets(self):
        """Stop all active target adjustments."""
        for controller in self.controllers.values():
            controller.clear_target()
        self._stop_hybrid_hold()
        self.notify_overlay_status('Targets cleared', 'orange')

    def _set_manual_rescan_bind(self):
        """Capture an optional hotkey for manual restart + rescan."""
        if self.app_state != 'CONFIG':
            self._show_info('Notice', 'Enter CONFIG mode first.')
            return
        self.focus_window()
        previous_bind = self.manual_rescan_bind
        if self.btn_manual_rescan_bind:
            self.btn_manual_rescan_bind.config(text='...', bg='yellow')
        self.root.update_idletasks()
        code = input_manager.capture_any_input()
        if code and code != 'CANCEL':
            conflict = self._find_hotkey_conflict(code, 'app:manual_rescan_bind')
            if conflict:
                self._show_warning('Hotkey already in use', f'{code} is already bound to {conflict}.')
                self.manual_rescan_bind = previous_bind
                self._refresh_manual_rescan_bind_button()
                return
            self.manual_rescan_bind = code
        elif code == 'CANCEL':
            self.manual_rescan_bind = None
        self._refresh_manual_rescan_bind_button()
        if self.app_state == 'RUNNING':
            self.register_current_listeners()
        self.schedule_save()

    def toggle_surface_preset(self):
        """Toggle between DRY/WET presets for the active track."""
        car = (self.current_car or self.combo_car.get().strip()).strip()
        track = (self.current_track or self.combo_track.get().strip()).strip()
        if not car or not track:
            return
        current = self._selected_surface()
        next_surface = 'WET' if current == 'DRY' else 'DRY'
        self._set_active_surface(car, track, next_surface, load=True, notify=True)

    def set_surface_preset(self, surface: str) -> None:
        """Switch to a specific surface preset for the active track."""
        car = (self.current_car or self.combo_car.get().strip()).strip()
        track = (self.current_track or self.combo_track.get().strip()).strip()
        if not car or not track:
            return
        surface_key = self._normalize_surface_label(surface)
        self._set_active_surface(car, track, surface_key, load=True, notify=True)

    def trigger_wiper_debug(self) -> None:
        """Log precipitation thresholds and apply the wiper toggle decision."""
        return
        if not self.wiper_debug_enabled.get():
            return
        _name, controller, tab = self._wiper_controller()
        if not controller or not tab:
            print('[Wipers Debug] No wiper controller/tab available.')
            return
        if not self._commands_allowed() or self.app_state != 'RUNNING':
            print('[Wipers Debug] Commands not allowed (off-track or not running).')
            return
        tab_config = tab.get_config()
        wiper_cfg = tab_config.get('wiper_auto', {})
        enabled = bool(wiper_cfg.get('enabled', False))
        precipitation = self._precipitation_amount()
        if precipitation is None:
            print('[Wipers Debug] Precipitation telemetry unavailable.')
            return
        desired_state, phase, on_threshold, off_threshold, on_effective, off_effective = self._wiper_desired_state(precipitation, wiper_cfg, self._wiper_state)
        self._log_wiper_debug(precipitation, desired_state, phase, on_threshold, off_threshold, on_effective, off_effective, self._wiper_state, wiper_cfg, reason='manual', force=True)
        if not enabled:
            print('[Wipers Debug] Wiper automation is disabled in this preset.')
            return
        if desired_state is None:
            print('[Wipers Debug] Precipitation within deadband; no toggle sent.')
            return
        if self._trigger_wiper_toggle(controller, desired_state):
            now = time.time()
            state = self._wiper_state
            state['last_desired'] = desired_state
            state['last_phase'] = phase
            state['last_trigger_phase'] = phase
            state['last_action'] = now
            state['cooldown_until'] = now + 0.35

    def manual_restart_scan(self):
        """Restart the app and trigger a scan + preset reload."""
        detected_car, detected_track = self._detect_current_car_track()
        restart_car = (detected_car or self.combo_car.get().strip() or self.current_car).strip()
        restart_track = (detected_track or self.combo_track.get().strip() or self.current_track).strip()
        if restart_car and restart_track:
            self._rescan_restart_pair = (restart_car, restart_track)
        self.pending_scan_on_start = True
        mark_pending_scan(silent=True)
        self.save_config()
        restart_program()

    def update_safe_mode(self):
        """Update safe mode settings."""
        input_manager.set_safe_mode(self.use_keyboard_only.get())
        if not self.use_keyboard_only.get():
            input_manager.connect_allowed_devices(input_manager.allowed_devices, force=True)

    def trigger_safe_mode_update(self):
        """Trigger safe mode update with restart."""
        new_value = self.use_keyboard_only.get()
        if self._ask_ok_cancel('Restart Required', 'Restart is required to apply Keyboard Only mode. Confirm?'):
            self.save_config()
            restart_program()
        else:
            self.use_keyboard_only.set(not new_value)
            self.save_config()
        self.update_safe_mode()

    def open_device_manager(self):
        """Open device management dialog."""
        if self.use_keyboard_only.get():
            self._show_info('Keyboard Mode', "Disable 'Keyboard Only Mode' to manage joystick devices.")
            return
        DeviceSelector(self.root, input_manager.allowed_devices, self.update_allowed_devices)

    def update_allowed_devices(self, new_list: List[str]):
        """Update list of allowed devices."""
        input_manager.allowed_devices = list(new_list)
        input_manager.connect_allowed_devices(input_manager.allowed_devices, force=True)
        self.save_config()

    def _normalize_surface_label(self, value: Optional[str]) -> str:
        """Return a valid surface preset label."""
        if not value:
            return DEFAULT_SURFACE_PRESET
        label = str(value).strip().upper()
        if label in SURFACE_PRESET_KEYS:
            return label
        return DEFAULT_SURFACE_PRESET

    def _default_surface_preset(self) -> Dict[str, Any]:
        """Return a blank preset payload for a surface."""
        return {'active_vars': list(self.active_vars), 'tabs': {}, 'combo': {}}

    def _strip_tab_key_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Return a tab config without per-car increase/decrease keys."""
        if not config:
            return {}
        stripped = dict(config)
        for key in ('key_increase', 'key_decrease', 'key_increase_text', 'key_decrease_text'):
            stripped.pop(key, None)
        return stripped

    def _collect_car_key_config(self, car: str) -> None:
        """Persist increase/decrease keys per car (shared across tracks)."""
        if not car:
            return
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        car_keys = self.saved_presets[car].get('_car_keys')
        if not isinstance(car_keys, dict):
            car_keys = {}
        for var_name, tab in self.tabs.items():
            car_keys[var_name] = {'key_increase': tab.controller.key_increase, 'key_decrease': tab.controller.key_decrease, 'key_increase_text': tab.btn_increase['text'], 'key_decrease_text': tab.btn_decrease['text'], 'ghost_increase_bind': tab.manual_increase_bind, 'ghost_decrease_bind': None if tab.uses_toggle_key else tab.manual_decrease_bind}
        self.saved_presets[car]['_car_keys'] = car_keys

    def _apply_car_key_config(self, car: str) -> None:
        """Apply stored per-car increase/decrease keys to tabs."""
        car_keys = self.saved_presets.get(car, {}).get('_car_keys')
        if not isinstance(car_keys, dict):
            return
        for var_name, tab in self.tabs.items():
            key_cfg = car_keys.get(var_name)
            if not isinstance(key_cfg, dict) and var_name in WIPER_TOGGLE_VARS:
                key_cfg = self._wiper_alias_config(car_keys)
            if isinstance(key_cfg, dict):
                tab.apply_key_config(key_cfg)

    def _ensure_track_surface_presets(self, car: str, track: str) -> Dict[str, Any]:
        """Ensure a track entry uses the surface preset structure."""
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        entry = self.saved_presets[car].get(track)
        if not isinstance(entry, dict):
            entry = {}
        if 'surface_presets' in entry:
            surface_presets = entry.get('surface_presets')
            if not isinstance(surface_presets, dict):
                surface_presets = {}
            normalized_presets: Dict[str, Any] = {}
            for key, value in surface_presets.items():
                normalized_presets[self._normalize_surface_label(key)] = value
            entry['surface_presets'] = normalized_presets
        else:
            legacy = entry if entry else self._default_surface_preset()
            entry = {'surface_presets': {DEFAULT_SURFACE_PRESET: legacy, 'WET': copy.deepcopy(legacy)}, 'active_surface': DEFAULT_SURFACE_PRESET}
        entry['active_surface'] = self._normalize_surface_label(entry.get('active_surface'))
        for surface in SURFACE_PRESET_KEYS:
            if surface not in entry['surface_presets']:
                entry['surface_presets'][surface] = self._default_surface_preset()
        self.saved_presets[car][track] = entry
        return entry

    def _get_surface_preset(self, car: str, track: str, surface: Optional[str]=None) -> Dict[str, Any]:
        """Return the preset payload for the selected surface."""
        entry = self._ensure_track_surface_presets(car, track)
        surface_key = self._normalize_surface_label(surface or entry.get('active_surface'))
        return entry['surface_presets'][surface_key]

    def _set_surface_selection(self, surface: str) -> None:
        """Update current surface selection and UI."""
        surface_key = self._normalize_surface_label(surface)
        self.current_surface = surface_key
        if getattr(self, 'combo_surface', None):
            self.combo_surface.set(surface_key)

    def _selected_surface(self) -> str:
        """Return the surface selected in the UI or current state."""
        if getattr(self, 'combo_surface', None):
            return self._normalize_surface_label(self.combo_surface.get())
        return self._normalize_surface_label(self.current_surface)

    def _update_surface_selector(self, car: str, track: str) -> None:
        """Sync surface selector with saved preset data."""
        if not car or not track:
            self._set_surface_selection(DEFAULT_SURFACE_PRESET)
            return
        entry = self._ensure_track_surface_presets(car, track)
        self._set_surface_selection(entry.get('active_surface', DEFAULT_SURFACE_PRESET))

    def update_preset_ui(self):
        """Update car/track combo boxes."""
        cars = sorted(list(self.saved_presets.keys()))
        self.combo_car['values'] = [c for c in cars if c]
        if self.current_car and self.current_car in cars:
            self.combo_car.set(self.current_car)
            self.on_car_selected(None)
            if self.current_track:
                self._update_surface_selector(self.current_car, self.current_track)
        self._update_header_context()
        self._update_preset_lock_state()

    def _on_lock_preset_selection_toggle(self) -> None:
        """Toggle manual preset selection lock."""
        self._update_preset_lock_state()
        self.schedule_save()

    def _update_preset_lock_state(self) -> None:
        """Enable or disable manual preset selection controls."""
        locked = self.lock_preset_selection.get()
        state = 'disabled' if locked else 'normal'
        self.combo_car.configure(state=state)
        self.combo_track.configure(state=state)
        if getattr(self, 'combo_surface', None):
            surface_state = 'disabled' if locked else 'readonly'
            self.combo_surface.configure(state=surface_state)
        load_state = tk.DISABLED if locked else tk.NORMAL
        self.btn_load_preset.configure(state=load_state)
        self.btn_save_preset.configure(state=tk.NORMAL)
        self.btn_clear_preset.configure(state=tk.NORMAL)

    def on_car_selected(self, _event):
        """Handle car selection."""
        car = self.combo_car.get()
        if car in self.saved_presets:
            tracks = sorted([t for t in self.saved_presets[car].keys() if t not in {'_overlay', '_overlay_feedback', '_car_keys'}])
            self.combo_track['values'] = tracks
        else:
            self.combo_track['values'] = []
        self.current_car = car
        if self.current_track:
            self._update_surface_selector(car, self.current_track)
        self._update_header_context()

    def on_track_selected(self, _event) -> None:
        """Handle track selection."""
        track = self.combo_track.get()
        self.current_track = track
        self._update_surface_selector(self.current_car, track)
        self._update_header_context()

    def on_surface_selected(self, _event) -> None:
        """Handle surface selection changes."""
        surface = self._selected_surface()
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        self.current_surface = surface
        if not car or not track:
            return
        self._set_active_surface(car, track, surface, load=True, notify=False)

    def auto_fill_ui(self, car: str, track: str):
        """Auto-fill car and track in UI."""
        self.current_car = car
        self.current_track = track
        self.combo_car.set(car)
        self.on_car_selected(None)
        self.combo_track.set(track)
        self._update_surface_selector(car, track)

    def _set_active_surface(self, car: str, track: str, surface: str, *, load: bool=True, notify: bool=False) -> None:
        """Set the active surface preset for a track."""
        surface_key = self._normalize_surface_label(surface)
        entry = self._ensure_track_surface_presets(car, track)
        entry['active_surface'] = surface_key
        self.saved_presets[car][track] = entry
        self._set_surface_selection(surface_key)
        if load:
            self.load_specific_preset(car, track, surface=surface_key)
        if notify:
            color = 'green' if surface_key == 'DRY' else 'deepskyblue'
            self.notify_overlay_status(f'Preset {surface_key}', color)
        self.schedule_save()

    def action_save_preset(self):
        """Save current configuration as preset."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        if not car or not track:
            self._show_warning('Error', 'Define Car and Track.')
            return
        self._save_preset_for_pair(car, track, show_message=True)

    def _save_preset_for_pair(self, car: str, track: str, show_message: bool=False) -> None:
        """Save preset data for a specific car/track pair."""
        surface = self._selected_surface()
        if self.overlay_tab:
            self.overlay_tab.collect_for_car(car)
        if car not in self.car_overlay_feedback:
            self.car_overlay_feedback[car] = DEFAULT_OVERLAY_FEEDBACK.copy()
        self._collect_car_key_config(car)
        current_data = {'active_vars': self.active_vars, 'tabs': {}, 'combo': self.combo_tab.get_config() if self.combo_tab else {}}
        for var_name, tab in self.tabs.items():
            current_data['tabs'][var_name] = self._strip_tab_key_config(tab.get_config())
        entry = self._ensure_track_surface_presets(car, track)
        entry['surface_presets'][surface] = current_data
        entry['active_surface'] = surface
        self.saved_presets[car][track] = entry
        self._set_surface_selection(surface)
        if car not in self.car_overlay_config:
            self.car_overlay_config[car] = {}
        self.saved_presets[car]['_overlay'] = self.car_overlay_config[car]
        self.saved_presets[car]['_overlay_feedback'] = self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy())
        self.save_config()
        self.auto_load_attempted.discard((car, track))
        if (car, track) == (self.current_car, self.current_track):
            self.register_current_listeners()
        self.update_preset_ui()
        if show_message:
            self._show_info('Saved', f'Preset saved for {car} @ {track} ({surface})')

    def load_specific_preset(self, car: str, track: str, *, surface: Optional[str]=None):
        """Load a specific car/track preset."""
        if car not in self.saved_presets or track not in self.saved_presets[car]:
            return
        entry = self._ensure_track_surface_presets(car, track)
        surface_key = self._normalize_surface_label(surface or entry.get('active_surface'))
        signature = (car.strip(), track.strip(), surface_key)
        if signature == self._last_loaded_preset_signature and time.time() - self._last_loaded_preset_time < 10.0:
            return
        self._last_loaded_preset_signature = signature
        self._last_loaded_preset_time = time.time()
        data = entry['surface_presets'].get(surface_key, self._default_surface_preset())
        entry['active_surface'] = surface_key
        self.saved_presets[car][track] = entry
        self._set_surface_selection(surface_key)
        tabs_data = data.get('tabs', {})
        combo_data = data.get('combo')
        configs_applied_in_rebuild = False
        active_vars = data.get('active_vars')
        if active_vars:
            normalized_vars = [_normalize_var_tuple(item) for item in active_vars]
            missing_tabs = any((name not in self.tabs for name, _f, _b in normalized_vars))
            if normalized_vars != self.active_vars or missing_tabs:
                self.rebuild_tabs(normalized_vars, tab_configs=tabs_data, combo_config=combo_data)
                configs_applied_in_rebuild = True
        wiper_config_applied = False
        if not configs_applied_in_rebuild:
            for var_name, config in tabs_data.items():
                if var_name in self.tabs:
                    self.tabs[var_name].set_config(config)
                    if var_name in WIPER_TOGGLE_VARS:
                        wiper_config_applied = True
        if not configs_applied_in_rebuild and (not wiper_config_applied):
            wiper_tab = self._wiper_tab()
            wiper_cfg = self._wiper_alias_config(tabs_data)
            if wiper_tab and wiper_cfg:
                try:
                    wiper_tab.set_config(wiper_cfg)
                except Exception:
                    pass
        if self.combo_tab and combo_data and (not configs_applied_in_rebuild):
            self.combo_tab.set_config(combo_data)
        self._apply_car_key_config(car)
        overlay_config = self.saved_presets[car].get('_overlay', {})
        self.car_overlay_config[car] = overlay_config
        self.car_overlay_feedback[car] = self.saved_presets[car].get('_overlay_feedback', self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy()))
        self.overlay_tab.load_for_car(car, self._overlay_var_list(), overlay_config)
        self.register_current_listeners()
        print(f'[Preset] Loaded {car} / {track} ({surface_key})')

    def action_load_preset(self):
        """Load selected preset."""
        car = self.combo_car.get()
        track = self.combo_track.get()
        surface = self._selected_surface()
        if not car or not track:
            return
        self.current_car = car
        self.current_track = track
        self._set_active_surface(car, track, surface, load=True, notify=False)

    def action_clear_preset(self):
        """Clear selected preset."""
        car = self.combo_car.get()
        track = self.combo_track.get()
        if not car or not track:
            return
        if car in self.saved_presets and track in self.saved_presets[car]:
            if not self._ask_yes_no('Confirm', f'Clear preset for {car} @ {track}?'):
                return
            del self.saved_presets[car][track]
            self.save_config()
            self.current_car = car
            self.current_track = track
            self._set_surface_selection(DEFAULT_SURFACE_PRESET)
            self.rebuild_tabs(list(self.active_vars))
            self._save_preset_for_pair(car, track, show_message=False)

    def _strip_preset_hotkeys(self, preset_data: Dict[str, Any]) -> Dict[str, Any]:
        """Return preset data without any hotkey bindings."""
        if not preset_data:
            return {}

        def _strip_tab(tab_cfg: Dict[str, Any]) -> Dict[str, Any]:
            clean: Dict[str, Any] = {}
            for key, value in tab_cfg.items():
                if key in {'key_increase', 'key_decrease', 'key_increase_text', 'key_decrease_text', 'ghost_increase_bind', 'ghost_decrease_bind'}:
                    continue
                if key == 'presets' and isinstance(value, list):
                    cleaned_presets = []
                    for preset in value:
                        cleaned = {preset_key: preset_val for preset_key, preset_val in preset.items() if preset_key != 'bind'}
                        cleaned_presets.append(cleaned)
                    clean['presets'] = cleaned_presets
                else:
                    clean[key] = value
            return clean
        tabs_data = {name: _strip_tab(tab_cfg) for name, tab_cfg in preset_data.get('tabs', {}).items()}
        combo_cfg = preset_data.get('combo', {})
        combo_presets = combo_cfg.get('presets', [])
        combo_clean = {}
        if combo_cfg:
            combo_clean = dict(combo_cfg)
            if isinstance(combo_presets, list):
                combo_clean['presets'] = [{preset_key: preset_val for preset_key, preset_val in preset.items() if preset_key != 'bind'} for preset in combo_presets]
        clean_data = dict(preset_data)
        clean_data['tabs'] = tabs_data
        clean_data['combo'] = combo_clean
        return clean_data

    def _preset_base_for_import(self, car: str, track: str) -> Dict[str, Any]:
        """Return base preset data used to preserve hotkeys during import."""
        surface = self._selected_surface()
        if car in self.saved_presets and track in self.saved_presets[car]:
            return self._get_surface_preset(car, track, surface)
        return {'active_vars': self.active_vars, 'tabs': {name: tab.get_config() for name, tab in self.tabs.items()}, 'combo': self.combo_tab.get_config() if self.combo_tab else {}}

    def _merge_tab_without_hotkeys(self, incoming_tab: Dict[str, Any], base_tab: Dict[str, Any]) -> Dict[str, Any]:
        """Merge tab data while preserving existing hotkeys."""
        merged = dict(incoming_tab)
        for key in ('key_increase', 'key_decrease', 'key_increase_text', 'key_decrease_text', 'ghost_increase_bind', 'ghost_decrease_bind'):
            if key in base_tab:
                merged[key] = base_tab[key]
        incoming_presets = incoming_tab.get('presets')
        if isinstance(incoming_presets, list):
            base_presets = base_tab.get('presets', [])
            merged_presets = []
            for idx, preset in enumerate(incoming_presets):
                preset_copy = dict(preset)
                base_bind = None
                if idx < len(base_presets):
                    base_bind = base_presets[idx].get('bind')
                preset_copy['bind'] = base_bind
                merged_presets.append(preset_copy)
            merged['presets'] = merged_presets
        return merged

    def _merge_combo_without_hotkeys(self, incoming_combo: Dict[str, Any], base_combo: Dict[str, Any]) -> Dict[str, Any]:
        """Merge combo data while preserving existing hotkeys."""
        merged = dict(incoming_combo)
        incoming_presets = incoming_combo.get('presets')
        if isinstance(incoming_presets, list):
            base_presets = base_combo.get('presets', [])
            merged_presets = []
            for idx, preset in enumerate(incoming_presets):
                preset_copy = dict(preset)
                base_bind = None
                if idx < len(base_presets):
                    base_bind = base_presets[idx].get('bind')
                preset_copy['bind'] = base_bind
                merged_presets.append(preset_copy)
            merged['presets'] = merged_presets
        return merged

    def _merge_preset_without_hotkeys(self, incoming: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
        """Merge a preset while keeping existing hotkey bindings intact."""
        incoming_clean = self._strip_preset_hotkeys(incoming)
        merged_tabs: Dict[str, Any] = {}
        base_tabs = base.get('tabs', {})
        for name, tab_cfg in incoming_clean.get('tabs', {}).items():
            base_tab = base_tabs.get(name, {})
            merged_tabs[name] = self._merge_tab_without_hotkeys(tab_cfg, base_tab)
        merged_combo = {}
        if 'combo' in incoming_clean:
            merged_combo = self._merge_combo_without_hotkeys(incoming_clean.get('combo', {}), base.get('combo', {}))
        return {'active_vars': incoming_clean.get('active_vars', base.get('active_vars', [])), 'tabs': merged_tabs, 'combo': merged_combo}

    def action_export_preset(self) -> None:
        """Export the selected preset without hotkey bindings."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        surface = self._selected_surface()
        if not car or not track:
            self._show_warning('Export Preset', 'Select a car and track first.')
            return
        if car not in self.saved_presets or track not in self.saved_presets[car]:
            self._show_warning('Export Preset', 'No saved preset found for the selected car/track.')
            return
        preset_data = self._strip_preset_hotkeys(self._get_surface_preset(car, track, surface))
        payload = {'version': 1, 'car': car, 'track': track, 'surface': surface, 'preset': preset_data}
        filename = filedialog.asksaveasfilename(title='Export Preset', defaultextension='.json', filetypes=[('Preset Export', '*.json'), ('All Files', '*.*')])
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            self._show_error('Export Preset', f'Failed to export preset: {exc}')
            return
        self._show_info('Export Preset', f'Preset exported to {filename}')

    def action_import_preset(self) -> None:
        """Import preset values without overwriting hotkeys."""
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        surface = self._selected_surface()
        if not car or not track:
            self._show_warning('Import Preset', 'Select a car and track first.')
            return
        filename = filedialog.askopenfilename(title='Import Preset', filetypes=[('Preset Export', '*.json'), ('All Files', '*.*')])
        if not filename:
            return
        try:
            with open(filename, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self._show_error('Import Preset', f'Failed to load preset file: {exc}')
            return
        incoming = data.get('preset') if isinstance(data, dict) else None
        if incoming is None and isinstance(data, dict) and ('tabs' in data):
            incoming = data
        if not isinstance(incoming, dict):
            self._show_error('Import Preset', 'Preset file is missing preset data.')
            return
        base = self._preset_base_for_import(car, track)
        merged = self._merge_preset_without_hotkeys(incoming, base)
        entry = self._ensure_track_surface_presets(car, track)
        entry['surface_presets'][surface] = merged
        entry['active_surface'] = surface
        self.saved_presets[car][track] = entry
        self.save_config()
        self.update_preset_ui()
        if (car, track) == (self.current_car, self.current_track):
            self.load_specific_preset(car, track, surface=surface)
        self._show_info('Import Preset', f'Preset imported for {car} @ {track} ({surface}).')

    def auto_preset_loop(self):
        """Background loop for auto-detecting car/track."""
        idle_delay_ms = 3000
        active_delay_ms = 2000
        if not (self.auto_detect.get() or self.auto_restart_on_race.get() or self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get() or self.auto_restart_on_track_ready.get() or self.keep_scanning_until_valid.get()):
            self.root.after(idle_delay_ms, self.auto_preset_loop)
            return
        try:
            if not self._ensure_sdk_connected():
                self._set_telemetry_active(False)
                self.root.after(700, self.auto_preset_loop)
                return
            session_type, session_num = self._get_session_state()
            if self._handle_session_change(session_type, session_num):
                return
            if not (self.auto_detect.get() or self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get() or self.auto_restart_on_track_ready.get() or self.keep_scanning_until_valid.get()):
                self.root.after(idle_delay_ms, self.auto_preset_loop)
                return
            driver_info = self.ir['DriverInfo']
            if not driver_info:
                self._set_telemetry_active(False)
                self.root.after(500, self.auto_preset_loop)
                return
            try:
                idx = int(driver_info.get('DriverCarIdx', -1))
            except Exception:
                idx = -1
            drivers = driver_info.get('Drivers', [])
            if not isinstance(drivers, list) or idx < 0 or idx >= len(drivers):
                self.root.after(500, self.auto_preset_loop)
                return
            driver_entry = drivers[idx] if isinstance(drivers[idx], dict) else {}
            raw_car = str(driver_entry.get('CarScreenName', '')).strip()
            if not raw_car:
                self.root.after(500, self.auto_preset_loop)
                return
            weekend = self.ir['WeekendInfo']
            if not weekend:
                self._set_telemetry_active(False)
                self.root.after(500, self.auto_preset_loop)
                return
            self._detect_session_change(weekend)
            self._handle_weekend_change(weekend)
            raw_track = str(weekend.get('TrackDisplayName', '')).strip()
            if not raw_track:
                self.root.after(500, self.auto_preset_loop)
                return
            telemetry_reconnected = self._set_telemetry_active(True)
            if self._maybe_restart_on_track_ready():
                return
            is_on_track = self._bool_from_keys(['IsOnTrack'])
            is_on_track_car = self._bool_from_keys(['IsOnTrackCar'])
            on_track_now = is_on_track and is_on_track_car
            if on_track_now and (not self._last_on_track_state):
                print('[AutoDetect] Driver entered car - validating scan...')
                self._on_track_validation_pending = True
                self.root.after(500, self._validate_and_recover)
            self._last_on_track_state = on_track_now
            car_clean = ''.join((c for c in raw_car if c.isalnum() or c in ' -_'))
            track_clean = ''.join((c for c in raw_track if c.isalnum() or c in ' -_'))
            if not car_clean.strip() or not track_clean.strip():
                self.root.after(3000, self.auto_preset_loop)
                return
            current_pair = (car_clean, track_clean)
            already_reported_pair = current_pair == self._last_detected_pair
            if current_pair != self._last_auto_pair:
                if already_reported_pair:
                    self._last_auto_pair = current_pair
                    self.current_car, self.current_track = (car_clean, track_clean)
                    self.root.after(active_delay_ms, self.auto_preset_loop)
                    return
                self._last_auto_pair = current_pair
                self._last_detected_pair = current_pair
                self._last_detected_pair_time = time.time()
                self.current_car, self.current_track = (car_clean, track_clean)
                print(f'[AutoDetect] {car_clean} @ {track_clean}')
                if self.auto_detect.get():
                    self.auto_fill_ui(car_clean, track_clean)
                if self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get():
                    self._schedule_session_scan()
                if telemetry_reconnected:
                    self._schedule_session_scan()
                if car_clean not in self.saved_presets:
                    self.saved_presets[car_clean] = {}
                if '_overlay' not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]['_overlay'] = self.car_overlay_config.get(car_clean, {})
                if '_overlay_feedback' not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean]['_overlay_feedback'] = self.car_overlay_feedback.get(car_clean, DEFAULT_OVERLAY_FEEDBACK.copy())
                if track_clean not in self.saved_presets[car_clean]:
                    self.saved_presets[car_clean][track_clean] = {'surface_presets': {'DRY': {'active_vars': None, 'tabs': {}, 'combo': {}}, 'WET': {'active_vars': None, 'tabs': {}, 'combo': {}}}, 'active_surface': DEFAULT_SURFACE_PRESET}
                self.save_config()
                if (car_clean, track_clean) not in self.auto_load_attempted:
                    self.auto_load_attempted.add((car_clean, track_clean))
                    if self._skip_next_auto_load:
                        self._skip_next_auto_load = False
                    else:
                        entry = self._ensure_track_surface_presets(car_clean, track_clean)
                        surface_key = entry.get('active_surface', DEFAULT_SURFACE_PRESET)
                        surface_data = entry['surface_presets'].get(surface_key, {})
                        if surface_data.get('active_vars'):
                            self.load_specific_preset(car_clean, track_clean, surface=surface_key)
            elif telemetry_reconnected:
                self._schedule_session_scan()
        except Exception as e:
            print(f'[AutoDetect] Error: {e}')
        self.root.after(active_delay_ms, self.auto_preset_loop)

    def _get_session_state(self) -> Tuple[str, Optional[int]]:
        """Return the current session type and session number if available."""
        try:
            session_info = self.ir['SessionInfo']
        except Exception:
            return ('', None)
        session_num = None
        try:
            session_num = int(self.ir['SessionNum'])
        except Exception:
            pass
        session_type = ''
        try:
            sessions = session_info.get('Sessions') if session_info else None
            if isinstance(sessions, list):
                if session_num is not None and 0 <= session_num < len(sessions):
                    session_type = sessions[session_num].get('SessionType', '')
                if not session_type:
                    for entry in sessions:
                        session_type = entry.get('SessionType', '')
                        if session_type:
                            break
        except Exception:
            pass
        return (session_type, session_num)

    def _handle_session_change(self, session_type: str, session_num: Optional[int]) -> bool:
        """Handle session transitions and restart if entering a race."""
        new_type = session_type or ''
        new_num = session_num
        if not new_type and new_num is None:
            self._mark_session_inactive()
            return False
        session_changed = new_type != self.last_session_type or (new_num is not None and new_num != self.last_session_num)
        if session_changed:
            self.last_session_type = new_type
            if new_num is not None:
                self.last_session_num = new_num
            if self.skip_race_restart_once and new_type == 'Race':
                self.skip_race_restart_once = False
                return False
            if self.skip_session_scan_once:
                self.skip_session_scan_once = False
                return False
            if not self._scan_recent_for_pair(window_s=60.0):
                self._last_auto_pair = ('', '')
                self.auto_load_attempted.clear()
            if self.auto_restart_on_race.get() and new_type == 'Race':
                self.pending_scan_on_start = True
                mark_pending_scan()
                self.save_config()
                restart_program()
                return True
            self._schedule_session_scan()
        return False

    def _maybe_restart_on_track_ready(self) -> bool:
        """Restart + rescan when on-track telemetry flips to true."""
        if not self.auto_restart_on_track_ready.get():
            self._on_track_restart_seen = False
            self.skip_on_track_restart_once = False
            return False
        is_on_track = self._bool_from_keys(['IsOnTrack'])
        is_on_track_car = self._bool_from_keys(['IsOnTrackCar'])
        on_track_now = is_on_track and is_on_track_car
        if self.skip_on_track_restart_once and on_track_now:
            self.skip_on_track_restart_once = False
            self._on_track_restart_seen = True
            return False
        if on_track_now and (not self._on_track_restart_seen):
            self._on_track_restart_seen = True
            self.manual_restart_scan()
            return True
        if not on_track_now:
            self._on_track_restart_seen = False
        return False

    def _validate_and_recover(self) -> None:
        """Validate scan results and attempt recovery when telemetry is invalid."""
        if self._recovery_in_progress or self._scan_in_progress or self._validation_in_progress:
            print('[Recovery] Scan already in progress, skipping validation')
            return
        if not self._on_track_validation_pending:
            return
        self._on_track_validation_pending = False
        self._validation_in_progress = True
        validator = ScanValidator(self.ir, dict(self.controllers))
        self.scan_validator = validator

        def _worker() -> None:
            try:
                success, message = validator.validate_scan()
            except Exception as exc:
                success = False
                message = f'Validation error: {exc}'
            self.root.after(0, lambda: self._finish_validate_and_recover(success, message))
        threading.Thread(target=_worker, name='ScanValidation', daemon=True).start()

    def _finish_validate_and_recover(self, success: bool, message: str) -> None:
        """Finish scan validation on the UI thread."""
        self._validation_in_progress = False
        print(f'[Validation] {message}')
        if success:
            self._validation_failures = 0
            self.notify_overlay_status('Scan OK', 'green')
            return
        self._validation_failures += 1
        print(f'[Recovery] Validation failed ({self._validation_failures}/{self._max_validation_failures})')
        if self._validation_failures >= self._max_validation_failures:
            self._show_warning('Scan Recovery', 'Unable to read telemetry after multiple attempts.\nRestarting application to fix connection...')
            self._recovery_restart_and_scan()
        else:
            self.notify_overlay_status(f'Scan issue detected - retrying ({self._validation_failures}/3)', 'orange')
            self._recovery_rescan()

    def _recovery_rescan(self) -> None:
        """Attempt a rescan without restarting the app."""
        if self._recovery_in_progress:
            return
        if self._scan_in_progress:
            self.root.after(500, self._recovery_rescan)
            return
        self._recovery_in_progress = True
        self._recovery_attempt += 1
        print(f'[Recovery] Attempting rescan (attempt {self._recovery_attempt})...')

        def on_complete() -> None:
            self._recovery_in_progress = False
            self.root.after(1000, self._validate_after_recovery)
        self.scan_driver_controls(silent_if_unavailable=True, allow_restart=False, on_complete=on_complete)

    def _validate_after_recovery(self) -> None:
        """Validate scan after a recovery attempt."""
        if self._scan_in_progress:
            self.root.after(500, self._validate_after_recovery)
            return
        if self._validation_in_progress:
            self.root.after(500, self._validate_after_recovery)
            return
        self._validation_in_progress = True
        validator = ScanValidator(self.ir, dict(self.controllers))
        self.scan_validator = validator

        def _worker() -> None:
            try:
                success, message = validator.validate_scan()
            except Exception as exc:
                success = False
                message = f'Validation error: {exc}'
            self.root.after(0, lambda: self._finish_validate_after_recovery(success, message))
        threading.Thread(target=_worker, name='RecoveryValidation', daemon=True).start()

    def _finish_validate_after_recovery(self, success: bool, message: str) -> None:
        """Finish post-rescan validation on the UI thread."""
        self._validation_in_progress = False
        print(f'[Recovery] Post-recovery validation: {message}')
        if success:
            self._validation_failures = 0
            self._recovery_attempt = 0
            self.notify_overlay_status('Recovery successful', 'green')
            return
        if self._recovery_attempt >= self._max_recovery_attempts:
            self._show_warning('Scan Recovery', 'Rescan failed. Restarting application...')
            self._recovery_restart_and_scan()
        else:
            print("[Recovery] Rescan didn't fix issue, trying again...")
            self.root.after(2000, self._recovery_rescan)

    def _recovery_restart_and_scan(self) -> None:
        """Restart the app and schedule a scan after reboot."""
        print('[Recovery] Initiating restart and scan...')
        detected_car, detected_track = self._detect_current_car_track()
        restart_car = (detected_car or self.combo_car.get().strip() or self.current_car).strip()
        restart_track = (detected_track or self.combo_track.get().strip() or self.current_track).strip()
        if restart_car and restart_track:
            self._rescan_restart_pair = (restart_car, restart_track)
        self.pending_scan_on_start = True
        self.skip_race_restart_once = True
        self.skip_session_scan_once = True
        self.skip_on_track_restart_once = True
        mark_pending_scan(silent=True)
        self.save_config()
        restart_program()

    def _handle_none_scan_result(self) -> bool:
        """Return True if a repeated None scan triggered a restart."""
        if not self._telemetry_active or not self._telemetry_ready_for_scan():
            self._none_scan_attempts = 0
            return False
        self._none_scan_attempts += 1
        print(f'[Recovery] Scan returned no telemetry values ({self._none_scan_attempts}/{self._max_none_scan_attempts}).')
        if self._none_scan_attempts < self._max_none_scan_attempts:
            return False
        self._none_scan_attempts = 0
        self._show_warning('Scan Recovery', 'Scan returned no telemetry values repeatedly.\nRestarting application to restore telemetry...')
        self._recovery_restart_and_scan()
        return True

    def _set_telemetry_active(self, active: bool) -> bool:
        """Track telemetry connection state and report reconnections."""
        if active == self._telemetry_active:
            return False
        self._telemetry_active = active
        if not active:
            self._mark_session_inactive()
            return False
        already_scanned = self._scan_recent_for_pair(window_s=20.0)
        if not already_scanned:
            self._last_auto_pair = ('', '')
            self.auto_load_attempted.clear()
            self._last_weekend_key = None
            self._skip_next_auto_load = False
        if self.keep_scanning_until_valid.get() and (not self.active_vars) and (not already_scanned):
            self._schedule_continuous_scan_retry()
        return True

    def _mark_session_inactive(self) -> None:
        """Reset session tracking when not connected to a session."""
        self.last_session_type = ''
        self.last_session_num = None
        self._last_auto_pair = ('', '')
        self._session_scan_pending = False
        self.auto_load_attempted.clear()
        self._telemetry_active = False
        self._last_weekend_key = None
        self._last_session_id = None
        self._skip_next_auto_load = False
        self._on_track_restart_seen = False

    def _scan_recent_for_pair(self, car: Optional[str]=None, track: Optional[str]=None, *, window_s: float=12.0) -> bool:
        """Return True when this car/track was scanned moments ago."""
        pair = ((car if car is not None else self.current_car).strip(), (track if track is not None else self.current_track).strip())
        if not pair[0] or not pair[1]:
            return False
        if pair != self._last_successful_scan_pair:
            return False
        return time.time() - self._last_successful_scan_time <= window_s

    def _sdk_connected_fast(self) -> bool:
        """Return True when the SDK looks ready without retry sleeps."""
        try:
            with self.ir_lock:
                if not getattr(self.ir, 'is_initialized', False):
                    return False
                if getattr(self.ir, 'is_connected', True) is False:
                    return False
                _ = self.ir['SessionNum']
            return True
        except Exception:
            return False

    def _prime_sdk_metadata(self) -> None:
        """Touch SDK metadata in a worker so later UI callbacks are cheap."""
        keys = ('SessionNum', 'DriverInfo', 'WeekendInfo', 'SessionInfo')
        try:
            with self.ir_lock:
                for key in keys:
                    try:
                        _ = self.ir[key]
                    except Exception:
                        pass
                try:
                    headers = getattr(self.ir, '_var_headers_dict', None)
                    if headers:
                        _ = len(headers)
                except Exception:
                    pass
        except Exception:
            pass

    def _start_sdk_warmup(self) -> None:
        """Start a throttled background connection attempt."""
        now = time.time()
        with self._sdk_warmup_lock:
            if now - self._sdk_last_warmup_attempt < 0.35:
                return
            if self._sdk_warmup_thread and self._sdk_warmup_thread.is_alive():
                return
            self._sdk_last_warmup_attempt = now

            def _warmup() -> None:
                if self._ensure_sdk_connected(blocking=True, start_warmup=False):
                    self._prime_sdk_metadata()
            self._sdk_warmup_thread = threading.Thread(target=_warmup, name='SDKWarmup', daemon=True)
            self._sdk_warmup_thread.start()

    def _ensure_sdk_connected(self, *, blocking: bool=False, start_warmup: bool=True) -> bool:
        """Ensure the shared SDK handle is initialized and connected."""
        if self._sdk_connected_fast():
            return True
        max_retries = 3 if blocking else 1
        base_delay = 0.06
        for attempt in range(max_retries):
            try:
                reconnected = False
                with self.ir_lock:
                    is_init = getattr(self.ir, 'is_initialized', False)
                    is_conn = getattr(self.ir, 'is_connected', True)
                    if not is_init:
                        reconnected = bool(self.ir.startup())
                    elif not is_conn:
                        try:
                            self.ir.shutdown()
                        except Exception:
                            pass
                        reconnected = bool(self.ir.startup())
                if reconnected:
                    self._refresh_controller_ir()
                    _TELEMETRY_CACHE.invalidate()
                    _TELEMETRY_CIRCUIT_BREAKER.reset()
                    if blocking or threading.current_thread() is not threading.main_thread():
                        self._prime_sdk_metadata()
                    elif start_warmup:
                        self._start_sdk_warmup()
                if self._sdk_connected_fast():
                    return True
                if not blocking:
                    break
                if attempt < max_retries - 1:
                    time.sleep(min(base_delay * 2 ** attempt, 0.25))
            except Exception:
                if not blocking or attempt >= max_retries - 1:
                    break
                time.sleep(min(base_delay * 2 ** attempt, 0.25))
        if start_warmup and (not blocking):
            self._start_sdk_warmup()
        return False

    def _detect_session_change(self, weekend: Optional[Dict[str, Any]]) -> bool:
        """Detect session changes to refresh controller SDK handles."""
        if not weekend:
            return False
        session_id = weekend.get('SessionID')
        if session_id is None:
            return False
        if session_id != self._last_session_id:
            self._last_session_id = session_id
            self._refresh_controller_ir()
            return True
        return False

    def _get_weekend_key(self, weekend: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
        """Return a stable identifier for the current weekend/session."""
        if not weekend:
            return None
        key_fields = (weekend.get('SessionID'), weekend.get('SubSessionID'), weekend.get('TrackID'), weekend.get('TrackDisplayName'))
        if all((field in (None, '') for field in key_fields)):
            return None
        return key_fields

    def _handle_weekend_change(self, weekend: Dict[str, Any]) -> None:
        """Reset auto-detect state when a new weekend/session loads."""
        weekend_key = self._get_weekend_key(weekend)
        if weekend_key is None or weekend_key == self._last_weekend_key:
            return
        self._last_weekend_key = weekend_key
        if self._scan_recent_for_pair(window_s=20.0):
            return
        self._last_auto_pair = ('', '')
        self.auto_load_attempted.clear()
        if self.auto_scan_on_change.get() or self.auto_restart_on_rescan.get():
            self._schedule_session_scan()

    def _telemetry_ready_for_scan(self) -> bool:
        """Return True when telemetry data is stable enough to scan."""
        try:
            if not getattr(self.ir, 'is_initialized', False):
                self._start_sdk_warmup()
                return False
            if getattr(self.ir, 'is_connected', True) is False:
                self._start_sdk_warmup()
                return False
            with self.ir_lock:
                driver_info = self.ir['DriverInfo']
                weekend = self.ir['WeekendInfo']
                session_info = self.ir['SessionInfo']
        except Exception:
            self._start_sdk_warmup()
            return False
        if not driver_info or not weekend or (not session_info):
            self._start_sdk_warmup()
            return False
        sessions = session_info.get('Sessions') if session_info else None
        if not sessions:
            return False
        return True

    def _note_none_telemetry(self, var_name: str) -> None:
        """Track None telemetry reads and trigger a rescan if persistent."""
        if not self._telemetry_active:
            return
        if self._scan_in_progress or self._recovery_in_progress:
            return
        if not self._telemetry_ready_for_scan():
            return
        now = time.time()
        if now - self._none_telemetry_last_trigger < self._none_telemetry_cooldown_s:
            return
        count = self._none_telemetry_counts.get(var_name, 0) + 1
        self._none_telemetry_counts[var_name] = count
        if count < self._none_telemetry_threshold:
            return
        self._none_telemetry_counts[var_name] = 0
        self._none_telemetry_last_trigger = now
        print(f'[Telemetry] {var_name} returned None; rescanning controls.')
        self.notify_overlay_status('Telemetry missing - rescanning', 'orange')
        try:
            self.root.after(0, self._recovery_rescan)
        except Exception:
            self._recovery_rescan()

    def _clear_none_telemetry(self, var_name: str) -> None:
        """Reset None telemetry streak for a variable."""
        if var_name in self._none_telemetry_counts:
            self._none_telemetry_counts[var_name] = 0

    def _cancel_continuous_scan_retry(self) -> None:
        if not self._continuous_scan_job:
            return
        try:
            self.root.after_cancel(self._continuous_scan_job)
        except Exception:
            pass
        self._continuous_scan_job = None

    def _schedule_continuous_scan_retry(self) -> None:
        if not self.keep_scanning_until_valid.get():
            self._cancel_continuous_scan_retry()
            return
        if self._continuous_scan_job:
            return

        def _retry() -> None:
            self._continuous_scan_job = None
            if not self.keep_scanning_until_valid.get():
                return
            if self.active_vars:
                return
            self.scan_driver_controls(silent_if_unavailable=True, allow_restart=False)
        self._continuous_scan_job = self.root.after(self._continuous_scan_delay_ms, _retry)

    def _schedule_session_scan(self) -> None:
        """Schedule a rescan and preset reload for a session change."""
        if self._session_scan_pending:
            return
        if self.active_vars and self._scan_recent_for_pair(window_s=60.0):
            return
        if self._scan_recent_for_pair(window_s=20.0):
            return
        if self.skip_auto_scan_once:
            self.skip_auto_scan_once = False
            return
        if self._scan_in_progress:
            self._session_scan_pending = True
            self._skip_next_auto_load = True
            self.root.after(self._session_scan_debounce_ms, self._auto_scan_and_load_preset)
            return
        self._session_scan_pending = True
        self._skip_next_auto_load = True
        self.root.after(self._session_scan_debounce_ms, self._auto_scan_and_load_preset)

    def _auto_scan_and_load_preset(self) -> None:
        """Scan controls and then reload the current car/track preset."""
        if not self._session_scan_pending:
            return
        if self._scan_in_progress:
            self.root.after(self._session_scan_debounce_ms, self._auto_scan_and_load_preset)
            return
        if not self._telemetry_ready_for_scan():
            self.root.after(self._session_scan_debounce_ms, self._auto_scan_and_load_preset)
            return
        if self._scan_recent_for_pair(window_s=20.0):
            self._session_scan_pending = False
            self._skip_next_auto_load = False
            return
        self._session_scan_pending = False
        self._skip_next_auto_load = False

        def _finish_auto_load() -> None:
            car = (self.combo_car.get().strip() or self.current_car).strip()
            track = (self.combo_track.get().strip() or self.current_track).strip()
            if car and track and (car in self.saved_presets):
                if track in self.saved_presets[car]:
                    entry = self._ensure_track_surface_presets(car, track)
                    surface_key = entry.get('active_surface', DEFAULT_SURFACE_PRESET)
                    self.load_specific_preset(car, track, surface=surface_key)
        self.scan_driver_controls(on_complete=_finish_auto_load)

    def scan_driver_controls(self, *, silent_if_unavailable: bool=False, allow_restart: bool=True, on_complete: Optional[Callable[[], None]]=None):
        """Scan for dc* driver control variables in current car."""
        try:
            if self._scan_in_progress:
                print('[Scan] Scan already in progress, skipping request')
                if on_complete:
                    self.root.after(0, on_complete)
                return
            if allow_restart and self.auto_restart_on_rescan.get() and (self.scans_since_restart >= 1):
                detected_car, detected_track = self._detect_current_car_track()
                restart_car = (detected_car or self.combo_car.get().strip() or self.current_car).strip()
                restart_track = (detected_track or self.combo_track.get().strip() or self.current_track).strip()
                restart_pair = (restart_car, restart_track)
                detected_pair = (detected_car, detected_track)
                suppress_restart = detected_car and detected_track and (detected_pair == self._rescan_restart_pair)
                if not suppress_restart:
                    self.pending_scan_on_start = True
                    if restart_car and restart_track:
                        self._rescan_restart_pair = restart_pair
                    mark_pending_scan()
                    self.save_config()
                    restart_program()
                    if on_complete:
                        self.root.after(0, on_complete)
                    return
            previous_pair = (self.current_car, self.current_track)
            fallback_tabs = {k: v.get_config() for k, v in self.tabs.items()}
            fallback_combo = self.combo_tab.get_config() if self.combo_tab else {}
            self._scan_in_progress = True
            if not silent_if_unavailable:
                self.notify_overlay_status('Scanning controls...', 'orange')

            def _worker():
                result = self._scan_driver_controls_worker()
                self.root.after(0, lambda: self._finish_scan_driver_controls(result, previous_pair, fallback_tabs, fallback_combo, silent_if_unavailable, on_complete))
            threading.Thread(target=_worker, name='DriverControlScan', daemon=True).start()
        except Exception as exc:
            print(f'[Scan] Unexpected error: {exc}')
            import traceback
            traceback.print_exc()
            if not silent_if_unavailable:
                self._show_error('Scan', f'Scanning failed with error: {exc}\n\nCheck console for details.')
            self._schedule_continuous_scan_retry()
            if on_complete:
                self.root.after(0, on_complete)
            self._scan_in_progress = False

    def _discover_driver_controls_from_headers(self) -> List[Tuple[str, bool, bool]]:
        """Return dc* controls from SDK headers without per-variable probing."""
        try:
            with self.ir_lock:
                headers = getattr(self.ir, '_var_headers_dict', None)
                if not headers:
                    return []
                found: List[Tuple[str, bool, bool]] = []
                for name, header in headers.items():
                    if not str(name).startswith('dc'):
                        continue
                    try:
                        var_type = int(getattr(header, 'type', -1))
                        count = int(getattr(header, 'count', 1))
                    except Exception:
                        continue
                    if count != 1:
                        continue
                    if var_type == IRSDK_VAR_TYPE_BOOL:
                        found.append((str(name), False, True))
                    elif var_type in IRSDK_VAR_TYPE_NUMERIC:
                        found.append((str(name), True, False))
            found.sort(key=lambda item: item[0])
            return found
        except Exception as exc:
            print(f'[Scan Worker] Header discovery failed: {exc}')
            return []

    def _scan_driver_controls_worker(self) -> Dict[str, Any]:
        """Worker thread for driver control scanning."""
        try:
            print('[Scan Worker] Starting scan...')
            if not self._ensure_sdk_connected(blocking=True):
                print('[Scan Worker] SDK not connected')
                return {'status': 'unavailable'}
            self._prime_sdk_metadata()
            weekend = None
            try:
                with self.ir_lock:
                    weekend = self.ir['WeekendInfo']
            except Exception as e:
                print(f'[Scan Worker] Failed to read WeekendInfo: {e}')
                weekend = None
            self._detect_session_change(weekend)
            found_vars = self._discover_driver_controls_from_headers()
            if found_vars:
                print(f'[Scan Worker] Header scan found {len(found_vars)} driver controls')
            else:
                candidates = ['dcBrakeBias', 'dcFuelMixture', 'dcTractionControl', 'dcTractionControl2', 'dcABS', 'dcAntiRollFront', 'dcAntiRollRear', 'dcHysBoostHold', 'dcHysRegenHold', 'dcMGUKDeployFixed', 'dcMGUKRegenGain', 'dcWeightJackerRight', 'dcDiffEntry', 'dcDiffExit']
                try:
                    with self.ir_lock:
                        names = getattr(self.ir, 'var_headers_names', None)
                    if names:
                        print(f'[Scan Worker] Found {len(names)} SDK variable names')
                        for key in names:
                            if str(key).startswith('dc'):
                                candidates.append(str(key))
                except Exception as e:
                    print(f'[Scan Worker] Error discovering SDK variables: {e}')
                candidates = sorted(list(set(candidates)))
                print(f'[Scan Worker] Testing {len(candidates)} candidate variables')
                if not candidates:
                    print('[Scan Worker] No candidate variables found')
                    return {'status': 'no_candidates'}
                try:
                    with self.ir_lock:
                        _ = self.ir['SessionNum']
                except Exception as e:
                    print(f'[Scan Worker] Warning: Could not read SessionNum: {e}')
                try:
                    for candidate in candidates:
                        try:
                            with self.ir_lock:
                                value = self.ir[candidate]
                        except Exception as e:
                            print(f'[Scan Worker] {candidate}: read failed ({type(e).__name__}: {e})')
                            continue
                        if value is None:
                            print(f'[Scan Worker] {candidate}: None value')
                            continue
                        if isinstance(value, bool):
                            found_vars.append((candidate, False, True))
                            print(f'[Scan Worker] {candidate}: FOUND (value={value})')
                            continue
                        if not isinstance(value, numbers.Real):
                            print(f'[Scan Worker] {candidate}: skipped (type={type(value).__name__})')
                            continue
                        found_vars.append((candidate, True, False))
                        print(f'[Scan Worker] {candidate}: FOUND (value={value})')
                except Exception as e:
                    print(f'[Scan Worker] Error reading variables: {e}')
                    import traceback
                    traceback.print_exc()
            print(f'[Scan Worker] Scan complete: found {len(found_vars)} variables')
            if not found_vars:
                return {'status': 'no_vars'}
            seen = set()
            clean_vars = []
            for entry in found_vars:
                name, is_float, is_boolean = _normalize_var_tuple(entry)
                if name in seen:
                    continue
                seen.add(name)
                clean_vars.append((name, is_float, is_boolean))
            clean_vars.sort(key=lambda x: x[0])
            detected_car, detected_track = self._detect_current_car_track()
            for _ in range(6):
                if detected_car and detected_track:
                    break
                time.sleep(0.05)
                detected_car, detected_track = self._detect_current_car_track()
            print(f'[Scan Worker] Detected: {detected_car} @ {detected_track}')
            return {'status': 'ok', 'vars': clean_vars, 'detected_car': detected_car, 'detected_track': detected_track}
        except Exception as exc:
            print(f'[Scan Worker] Fatal error: {exc}')
            import traceback
            traceback.print_exc()
            return {'status': 'error', 'error': str(exc)}

    def _finish_scan_driver_controls(self, result: Dict[str, Any], previous_pair: Tuple[str, str], fallback_tabs: Dict[str, Dict[str, Any]], fallback_combo: Dict[str, Any], silent_if_unavailable: bool, on_complete: Optional[Callable[[], None]]) -> None:
        """Finalize scan results on the main UI thread."""
        self._scan_in_progress = False
        status = result.get('status')
        if status != 'no_vars':
            self._none_scan_attempts = 0
        if status == 'unavailable':
            if not silent_if_unavailable:
                self._show_error('Error', 'Open iRacing (or enter a session).')
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return
        if status == 'no_candidates':
            self._show_warning('Scan', "SDK hasn't returned any variables yet.\nEnter the car (Drive), adjust controls, and try again.")
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return
        if status == 'no_vars':
            if self._handle_none_scan_result():
                if on_complete:
                    on_complete()
                return
            self._show_warning('Scan', "No numeric or boolean 'dc*' variables found.\nThe car may not have driver controls or you're not in Drive mode.")
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return
        if status != 'ok':
            print(f"[Scan] Unexpected error: {result.get('error')}")
            if not silent_if_unavailable:
                self._show_error('Scan', "Scanning failed. Please try again once you're in session.")
            self._schedule_continuous_scan_retry()
            if on_complete:
                on_complete()
            return
        clean_vars = result['vars']
        self._refresh_controller_ir()
        missing_tabs = any((name not in self.tabs for name, _f, _b in clean_vars))
        if clean_vars != self.active_vars or missing_tabs:
            self.active_vars = clean_vars
            self.rebuild_tabs(self.active_vars)
        else:
            self.active_vars = clean_vars
        detected_car = result.get('detected_car', '')
        detected_track = result.get('detected_track', '')
        car = detected_car or self.combo_car.get().strip() or self.current_car or 'Generic Car'
        track = detected_track or self.combo_track.get().strip() or self.current_track or 'Generic Track'
        self.current_car, self.current_track = (car, track)
        self.auto_fill_ui(car, track)
        scan_pair = (car.strip(), track.strip())
        self._last_successful_scan_pair = scan_pair
        self._last_successful_scan_time = time.time()
        self._last_auto_pair = scan_pair
        self.auto_load_attempted.add(scan_pair)
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        entry = self._ensure_track_surface_presets(car, track)
        surface_key = self._normalize_surface_label(entry.get('active_surface', self._selected_surface()))
        surface_data = entry['surface_presets'].setdefault(surface_key, self._default_surface_preset())
        surface_data['active_vars'] = self.active_vars
        entry['surface_presets'][surface_key] = surface_data
        entry['active_surface'] = surface_key
        self.saved_presets[car][track] = entry
        if '_overlay' not in self.saved_presets[car]:
            self.saved_presets[car]['_overlay'] = self.car_overlay_config.get(car, {})
        if '_overlay_feedback' not in self.saved_presets[car]:
            self.saved_presets[car]['_overlay_feedback'] = self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy())
        self.car_overlay_config[car] = self.saved_presets[car]['_overlay']
        self.car_overlay_feedback[car] = self.saved_presets[car]['_overlay_feedback']
        self.overlay_tab.load_for_car(car, self._overlay_var_list(), self.car_overlay_config[car])
        preset_data = entry['surface_presets'].get(surface_key, {})
        if preset_data.get('tabs') or preset_data.get('combo'):
            self.load_specific_preset(car, track, surface=surface_key)
        else:
            if (car, track) == previous_pair:
                self._apply_inline_config(fallback_tabs, fallback_combo)
            self._apply_car_key_config(car)
            self.register_current_listeners()
        self.update_preset_ui()
        self.save_config()
        self.scans_since_restart += 1
        self._cancel_continuous_scan_retry()
        if self.scan_validator:
            self.scan_validator.reset()
        self._validation_failures = 0
        self._recovery_attempt = 0
        if self.show_scan_popup.get():
            self._show_info('Scan', f"{len(clean_vars)} 'dc' controls configured for this car.")
        if on_complete:
            on_complete()

    def rebuild_tabs(self, vars_list: List[Tuple[str, bool, bool]], *, tab_configs: Optional[Dict[str, Dict[str, Any]]]=None, combo_config: Optional[Dict[str, Any]]=None):
        """Rebuild control tabs with new variable list."""
        tab_configs = tab_configs if isinstance(tab_configs, dict) else {}
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        for tab in self.tabs.values():
            try:
                tab.destroy()
            except Exception:
                pass
        for frame in (self.combo_frame, self.overlay_frame):
            if frame is not None:
                try:
                    frame.destroy()
                except Exception:
                    pass
        self.controllers.clear()
        self.tabs.clear()
        self.combo_frame = None
        self.overlay_frame = None
        self.active_vars = [_normalize_var_tuple(item) for item in vars_list]
        for var_name, is_float, is_boolean in self.active_vars:
            display_name = format_driver_control_name(var_name)
            allow_dual_keys = var_name == 'dcPitSpeedLimiterToggle'
            controller = GenericController(self.ir, var_name, is_float, is_boolean, allow_dual_keys, app_ref=self)
            self.controllers[var_name] = controller
            frame = tk.Frame(self.notebook)
            initial_config = tab_configs.get(var_name)
            tab_widget = ControlTab(frame, controller, display_name, self, create_default_rows=not bool(initial_config))
            if initial_config:
                tab_widget.set_config(initial_config)
            tab_widget.pack(fill='both', expand=True)
            self.notebook.add(frame, text=display_name)
            self.tabs[var_name] = tab_widget
        self.combo_frame = tk.Frame(self.combo_page_container)
        self.combo_tab = ComboTab(self.combo_frame, self.controllers, self)
        if combo_config:
            self.combo_tab.set_config(combo_config)
        self.combo_tab.pack(fill='both', expand=True)
        self.combo_frame.pack(fill='both', expand=True)
        self.overlay_frame = tk.Frame(self.overlay_page_container)
        self.overlay_tab = OverlayConfigTab(self.overlay_frame, self)
        self.overlay_tab.pack(fill='both', expand=True)
        self.overlay_frame.pack(fill='both', expand=True)
        self._apply_discreet_mode()
        self._refresh_control_tab_labels()
        self.root.after_idle(self._refresh_control_tab_labels)
        car = self.current_car or 'Generic Car'
        if car not in self.saved_presets:
            self.saved_presets[car] = {}
        if '_overlay' not in self.saved_presets[car]:
            self.saved_presets[car]['_overlay'] = self.car_overlay_config.get(car, {})
        if '_overlay_feedback' not in self.saved_presets[car]:
            self.saved_presets[car]['_overlay_feedback'] = self.car_overlay_feedback.get(car, DEFAULT_OVERLAY_FEEDBACK.copy())
        self.car_overlay_config[car] = self.saved_presets[car]['_overlay']
        self.car_overlay_feedback[car] = self.saved_presets[car]['_overlay_feedback']
        self.overlay_tab.load_for_car(car, self._overlay_var_list(), self.car_overlay_config[car])
        editing = self.app_state == 'CONFIG'
        for tab in self.tabs.values():
            tab.set_editing_state(editing)
        if self.combo_tab:
            self.combo_tab.set_editing_state(editing)
        self.register_current_listeners()

    def toggle_overlay(self):
        """Toggle HUD overlay visibility."""
        if self.discreet_mode.get():
            try:
                if self.overlay.winfo_exists():
                    self.overlay.withdraw()
            except Exception:
                pass
            self.overlay_visible = False
            return
        if self.overlay.winfo_viewable():
            self.overlay.withdraw()
            self.overlay_visible = False
        else:
            self.overlay.deiconify()
            self.overlay_visible = True
        self.schedule_save()

    def notify_overlay_status(self, text: str, color: str):
        """Update overlay status text temporarily."""
        self.ui(self.overlay.update_status_text, text, color)
        self.ui(self.root.after, 2000, lambda: self.overlay.update_status_text('HUD Ready', 'white'))

    def _overlay_var_list(self) -> List[Tuple[str, bool, bool]]:
        """Return the active variable list plus overlay-only telemetry rows."""
        overlay_vars = [_normalize_var_tuple(item) for item in self.active_vars]
        existing = {name for name, _is_float, _is_boolean in overlay_vars}
        for name, is_float, is_boolean in self.overlay_extra_vars:
            if name not in existing:
                overlay_vars.append((name, is_float, is_boolean))
        return overlay_vars

    def update_overlay_loop(self):
        """Background loop to update HUD values."""
        telemetry_ready = self.app_state == 'RUNNING'
        next_interval = 100
        if self.overlay_visible:
            if telemetry_ready:
                data = {}
                car = self.current_car or 'Generic Car'
                config = self.car_overlay_config.get(car, {})
                visible_controller_keys = [var_name for var_name in self.controllers.keys() if config.get(var_name, {}).get('show', False)]
                batch_keys = [key for key in visible_controller_keys if key != 'dcPushToPass']
                if batch_keys:
                    data.update(self._batch_read_ir_values(batch_keys))
                if 'dcPushToPass' in visible_controller_keys:
                    status = self._read_push_to_pass_status()
                    data['dcPushToPass'] = None if status is None else int(bool(status))
                extra_keys = []
                for var_name, var_config in config.items():
                    if not var_config.get('show', False):
                        continue
                    if var_name in data:
                        continue
                    if var_name in self.controllers:
                        continue
                    extra_keys.append(var_name)
                if extra_keys:
                    data.update(self._batch_read_ir_values(extra_keys))
                self.overlay.update_monitor_values(data)
                next_interval = 100
            else:
                self.overlay.update_monitor_values({})
                next_interval = 250
        if self.show_overlay_feedback.get() and telemetry_ready:
            self._update_overlay_feedback()
        else:
            self._overlay_feedback_state['last_time'] = time.time()
            if not self.overlay_visible:
                next_interval = 400 if not telemetry_ready else 180
        self.root.after(next_interval, self.update_overlay_loop)

    def _read_ir_value(self, key: str, use_cache: bool=True):
        """Safely read a telemetry key from the iRacing SDK."""
        if use_cache:
            hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=0.08)
            if hit:
                return cached
        if not _TELEMETRY_CIRCUIT_BREAKER.can_execute(key):
            hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=2.0)
            if hit:
                return cached
            return None
        on_ui_thread = threading.current_thread() is threading.main_thread()
        try:
            if not getattr(self.ir, 'is_initialized', False):
                self._start_sdk_warmup()
                if on_ui_thread:
                    return None
                self.ir.startup()
            value = self.ir[key]
            if value is not None:
                _TELEMETRY_CACHE.set(key, value)
            _TELEMETRY_CIRCUIT_BREAKER.record_success(key)
            return value
        except Exception:
            if on_ui_thread:
                self._start_sdk_warmup()
            _TELEMETRY_CIRCUIT_BREAKER.record_failure(key)
            return None

    def _batch_read_ir_values(self, keys: List[str]) -> Dict[str, Any]:
        """Read multiple telemetry keys efficiently in a single batch."""
        results: Dict[str, Any] = {}
        keys_to_read = []
        for key in keys:
            if not _TELEMETRY_CIRCUIT_BREAKER.can_execute(key):
                hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=2.0)
                results[key] = cached if hit else None
                continue
            hit, cached = _TELEMETRY_CACHE.get(key, ttl_s=0.08)
            if hit:
                results[key] = cached
            else:
                keys_to_read.append(key)
        if keys_to_read:
            try:
                if not getattr(self.ir, 'is_initialized', False):
                    self._start_sdk_warmup()
                    if threading.current_thread() is threading.main_thread():
                        for key in keys_to_read:
                            results[key] = None
                        return results
                    self.ir.startup()
                fresh_values = {}
                for key in keys_to_read:
                    try:
                        value = self.ir[key]
                        results[key] = value
                        if value is not None:
                            fresh_values[key] = value
                        _TELEMETRY_CIRCUIT_BREAKER.record_success(key)
                    except Exception:
                        results[key] = None
                        _TELEMETRY_CIRCUIT_BREAKER.record_failure(key)
                if fresh_values:
                    _TELEMETRY_CACHE.batch_set(fresh_values)
            except Exception:
                for key in keys_to_read:
                    results[key] = None
        return results

    @staticmethod
    def _safe_float(value: Any, default: float=0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _bool_from_keys(self, keys: List[str], use_cache: bool=True) -> bool:
        """Return True if any telemetry key resolves to a truthy value."""
        for key in keys:
            value = self._read_ir_value(key, use_cache=use_cache)
            if isinstance(value, (list, tuple, array)):
                if any((bool(v) for v in value)):
                    return True
            elif isinstance(value, numbers.Real):
                if float(value) != 0.0:
                    return True
            elif isinstance(value, bool) and value:
                return True
        return False

    def _read_push_to_pass_status(self) -> Optional[bool]:
        """Return current push-to-pass state from telemetry when available."""
        value = self._read_ir_value('P2P_Status')
        status = self._telemetry_value_bool(value, use_player_idx=True)
        if status is None:
            value = self._read_ir_value('dcPushToPass')
            status = self._telemetry_value_bool(value, use_player_idx=True)
        return status

    def _telemetry_value_bool(self, value: Any, use_player_idx: bool=False) -> Optional[bool]:
        """Normalize telemetry values into a boolean when possible."""
        if isinstance(value, (list, tuple, array)):
            if use_player_idx:
                idx_value = self._read_ir_value('PlayerCarIdx', use_cache=False)
                if isinstance(idx_value, numbers.Real):
                    idx = int(idx_value)
                    if 0 <= idx < len(value):
                        return bool(value[idx])
                return None
            return any((bool(v) for v in value))
        if isinstance(value, numbers.Real):
            return float(value) != 0.0
        if isinstance(value, bool):
            return value
        return None

    def _player_on_track_car(self) -> Optional[bool]:
        """Return True if the player's car is on track."""
        value = self._read_ir_value('IsOnTrackCar', use_cache=False)
        return self._telemetry_value_bool(value, use_player_idx=True)

    def _pit_limiter_track_ok(self) -> bool:
        """Return False when IsOnTrack or IsOnTrackCar is explicitly false."""
        on_track = self._telemetry_value_bool(self._read_ir_value('IsOnTrack', use_cache=False), use_player_idx=True)
        on_track_car = self._telemetry_value_bool(self._read_ir_value('IsOnTrackCar', use_cache=False), use_player_idx=True)
        if on_track is False or on_track_car is False:
            return False
        return True

    def _commands_allowed(self) -> bool:
        """Return True when command triggers should be allowed."""
        if not self.block_off_track_commands.get():
            return True
        on_track = self._player_on_track_car()
        return bool(on_track)

    def _push_overlay_alert(self, message: str, color: str, cfg: Dict[str, float], now: float) -> None:
        """Send rate-limited feedback to the overlay status area."""
        state = self._overlay_feedback_state
        cooldown = max(0.5, float(cfg.get('cooldown_s', 6.0)))
        if now - state.get('last_alert_time', 0.0) < cooldown and state.get('last_alert') == message:
            return
        self.notify_overlay_status(message, color)
        state['last_alert'] = message
        state['last_alert_time'] = now

    def _update_overlay_feedback(self):
        """Analyze telemetry and surface ABS/TC/wheelspin hints on the HUD."""
        car = self.current_car or 'Generic Car'
        cfg = DEFAULT_OVERLAY_FEEDBACK.copy()
        cfg.update(self.car_overlay_feedback.get(car, {}))
        state = self._overlay_feedback_state
        now = time.time()
        dt = max(0.0, now - state.get('last_time', now))
        state['last_time'] = now
        abs_keys = ['BrakeABSactive', 'BrakeABSActive', 'BrakeABSActiveLF', 'BrakeABSActiveRF', 'BrakeABSActiveLR', 'BrakeABSActiveRR']
        tc_keys = ['TractionControlActive', 'TractionControlEngaged', 'TCActive', 'TractionControlOn']
        slip_keys = ['WheelSlip', 'WheelSlipPct', 'WheelSlipRatio', 'TireSlip']
        snapshot = self._batch_read_ir_values(['Throttle', 'Brake', *abs_keys, *tc_keys, *slip_keys])

        def _truthy(keys: List[str]) -> bool:
            for key in keys:
                value = snapshot.get(key)
                if isinstance(value, (list, tuple, array)):
                    if any((bool(v) for v in value)):
                        return True
                elif isinstance(value, numbers.Real):
                    if bool(value):
                        return True
                elif value:
                    return True
            return False
        throttle = self._safe_float(snapshot.get('Throttle'), 0.0)
        brake = self._safe_float(snapshot.get('Brake'), 0.0)
        abs_active = _truthy(abs_keys)
        tc_active = _truthy(tc_keys)
        slips: List[float] = []
        for key in slip_keys:
            value = snapshot.get(key)
            if isinstance(value, (list, tuple, array)):
                slips.extend([self._safe_float(v, 0.0) for v in value])
        max_slip = max(slips) if slips else 0.0
        min_slip = min(slips) if slips else 0.0
        if abs_active and brake > 0.05:
            state['abs_active'] += dt
        else:
            state['abs_active'] = 0.0
        if tc_active and throttle > 0.2:
            state['tc_active'] += dt
        else:
            state['tc_active'] = 0.0
        if throttle > 0.2 and max_slip >= cfg['wheelspin_slip']:
            state['spin_active'] += dt
        else:
            state['spin_active'] = 0.0
        lock_threshold = -abs(cfg['lockup_slip'])
        if brake > 0.05 and slips and (min_slip <= lock_threshold):
            state['lock_active'] += dt
        else:
            state['lock_active'] = 0.0
        if state['abs_active'] >= cfg['abs_hold_s']:
            self._push_overlay_alert('ABS active too long: ease off the brake or lower ABS.', 'orange', cfg, now)
            state['abs_active'] = 0.0
        if state['tc_active'] >= cfg['tc_hold_s']:
            self._push_overlay_alert('TC constantly triggering: consider lowering TC or changing the map.', 'orange', cfg, now)
            state['tc_active'] = 0.0
        if state['spin_active'] >= cfg['wheelspin_hold_s']:
            self._push_overlay_alert('Wheelspin detected: raise TC or modulate the throttle.', 'orange', cfg, now)
            state['spin_active'] = 0.0
        if state['lock_active'] >= cfg['lockup_hold_s']:
            self._push_overlay_alert('Lock-up detected: increase ABS or ease pedal pressure.', 'orange', cfg, now)
            state['lock_active'] = 0.0

    def open_timing_window(self):
        """Open timing configuration window."""
        GlobalTimingWindow(self.root, self.save_timing_config, self._popups_enabled)

    def save_timing_config(self, new_timing: Dict[str, Any]):
        """Save timing configuration."""
        GLOBAL_TIMING.update(_normalize_timing_config(new_timing))
        self.save_config()

    def _perform_pending_scan(self):
        """Execute a deferred scan request set before restarting."""
        pending_scan, silent_scan = consume_pending_scan()
        if pending_scan:
            self.pending_scan_on_start = True
            self.skip_session_scan_once = True
            self.skip_auto_scan_once = True
            self.skip_on_track_restart_once = True
            self._pending_scan_silent = silent_scan
        if self.pending_scan_on_start:
            self.skip_race_restart_once = True
            self.pending_scan_on_start = False
            self.save_config()
            self.root.after(50, lambda: self.scan_driver_controls(silent_if_unavailable=self._pending_scan_silent, allow_restart=False))

    def _detect_current_car_track(self) -> Tuple[str, str]:
        """Detect current car/track names from the iRacing SDK, if available."""
        raw_car = ''
        raw_track = ''
        try:
            driver_info = self.ir['DriverInfo']
            if driver_info:
                idx = driver_info.get('DriverCarIdx')
                if idx is not None:
                    try:
                        idx = int(idx)
                    except (TypeError, ValueError):
                        idx = None
                if idx is not None:
                    raw_car = driver_info['Drivers'][idx]['CarScreenName']
        except Exception:
            pass
        try:
            weekend = self.ir['WeekendInfo']
            if weekend:
                raw_track = weekend.get('TrackDisplayName', '')
        except Exception:
            pass
        if not raw_car and (not raw_track):
            return ('', '')
        car_clean = ''.join((c for c in raw_car if c.isalnum() or c in ' -_')).strip()
        track_clean = ''.join((c for c in raw_track if c.isalnum() or c in ' -_')).strip()
        return (car_clean, track_clean)

    def schedule_save(self):
        """Schedule configuration save."""

        def _schedule():
            if self._config_save_job:
                try:
                    self.root.after_cancel(self._config_save_job)
                except Exception:
                    pass
            self._config_save_job = self.root.after(250, self._flush_scheduled_save)
        self.ui(_schedule)

    def _flush_scheduled_save(self) -> None:
        """Persist config after debounce to avoid excessive disk writes."""
        self._config_save_job = None
        self.save_config()

    def schedule_preset_save(self) -> None:
        """Auto-save current preset if the setting is enabled."""
        if not self.auto_save_presets.get():
            return
        if self.app_state != 'CONFIG':
            return
        if self._auto_save_job:
            self.root.after_cancel(self._auto_save_job)
        self._auto_save_job = self.root.after(400, self._auto_save_current_preset)

    def _auto_save_current_preset(self) -> None:
        """Persist the current preset without showing prompts."""
        self._auto_save_job = None
        car = self.combo_car.get().strip()
        track = self.combo_track.get().strip()
        if not car or not track:
            return
        self._save_preset_for_pair(car, track, show_message=False)

    def save_config(self):
        """Save configuration to disk."""
        if self._config_save_job:
            try:
                self.root.after_cancel(self._config_save_job)
            except Exception:
                pass
            self._config_save_job = None
        car = self.current_car or 'Generic Car'
        if self.overlay_tab:
            self.overlay_tab.collect_for_car(car)
        data = {'global_timing': GLOBAL_TIMING, 'hud_style': self.overlay.style_cfg, 'show_overlay_feedback': self.show_overlay_feedback.get(), 'overlay_visible': self.overlay_visible, 'p2p_overlay_default_off_migrated': self.p2p_overlay_default_off_migrated, 'use_keyboard_only': self.use_keyboard_only.get(), 'auto_detect': self.auto_detect.get(), 'auto_scan_on_change': self.auto_scan_on_change.get(), 'auto_restart_on_rescan': self.auto_restart_on_rescan.get(), 'auto_restart_on_race': self.auto_restart_on_race.get(), 'auto_restart_on_track_ready': self.auto_restart_on_track_ready.get(), 'block_off_track_commands': self.block_off_track_commands.get(), 'auto_save_presets': self.auto_save_presets.get(), 'lock_preset_selection': self.lock_preset_selection.get(), 'start_with_windows': self.start_with_windows.get(), 'focus_on_start': self.focus_on_start.get(), 'keep_trying_targets': self.keep_trying_targets.get(), 'show_scan_popup': self.show_scan_popup.get(), 'keep_scanning_until_valid': self.keep_scanning_until_valid.get(), 'disable_popups': self.disable_popups.get(), 'show_getting_started': self.show_getting_started.get(), 'discreet_mode': self.discreet_mode.get(), 'clear_target_bind': self.clear_target_bind, 'manual_rescan_bind': self.manual_rescan_bind, 'surface_toggle_bind': self.surface_toggle_bind, 'surface_dry_bind': self.surface_dry_bind, 'surface_wet_bind': self.surface_wet_bind, 'pending_scan_on_start': self.pending_scan_on_start, 'rescan_restart_pair': list(self._rescan_restart_pair), 'allowed_devices': input_manager.allowed_devices, 'saved_presets': self.saved_presets, 'car_overlay_config': self.car_overlay_config, 'car_overlay_feedback': self.car_overlay_feedback, 'active_vars': self.active_vars, 'current_car': self.current_car, 'current_track': self.current_track, 'current_surface': self.current_surface}
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f'[SAVE] Error saving config: {e}')

    def load_config(self):
        """Load configuration from disk."""
        global GLOBAL_TIMING
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return
        GLOBAL_TIMING = _normalize_timing_config(data.get('global_timing', GLOBAL_TIMING))
        style = data.get('hud_style')
        if style:
            self.overlay.style_cfg.update(style)
            self.overlay.apply_style(self.overlay.style_cfg)
        self.show_overlay_feedback.set(data.get('show_overlay_feedback', True))
        self.overlay_visible = data.get('overlay_visible', True)
        self.p2p_overlay_default_off_migrated = data.get('p2p_overlay_default_off_migrated', False)
        self.use_keyboard_only.set(data.get('use_keyboard_only', False))
        self.auto_detect.set(data.get('auto_detect', True))
        self.auto_scan_on_change.set(data.get('auto_scan_on_change', True))
        self.auto_restart_on_rescan.set(data.get('auto_restart_on_rescan', False))
        self.auto_restart_on_race.set(data.get('auto_restart_on_race', False))
        self.auto_restart_on_track_ready.set(data.get('auto_restart_on_track_ready', False))
        block_off_track = data.get('block_off_track_commands', True)
        if not isinstance(block_off_track, bool):
            block_off_track = True
        self.block_off_track_commands.set(block_off_track)
        self.auto_save_presets.set(data.get('auto_save_presets', True))
        self.lock_preset_selection.set(data.get('lock_preset_selection', True))
        self.start_with_windows.set(data.get('start_with_windows', _startup_entry_exists()))
        self.focus_on_start.set(data.get('focus_on_start', False))
        self.keep_trying_targets.set(data.get('keep_trying_targets', True))
        self.show_scan_popup.set(data.get('show_scan_popup', False))
        self.keep_scanning_until_valid.set(data.get('keep_scanning_until_valid', True))
        self.disable_popups.set(data.get('disable_popups', True))
        self.show_getting_started.set(data.get('show_getting_started', True))
        self.discreet_mode.set(data.get('discreet_mode', False))
        self.clear_target_bind = data.get('clear_target_bind')
        self.manual_rescan_bind = data.get('manual_rescan_bind')
        self.surface_toggle_bind = data.get('surface_toggle_bind')
        self.surface_dry_bind = data.get('surface_dry_bind')
        self.surface_wet_bind = data.get('surface_wet_bind')
        self.pending_scan_on_start = data.get('pending_scan_on_start', False)
        pair = data.get('rescan_restart_pair', ['', ''])
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            self._rescan_restart_pair = (pair[0], pair[1])
        input_manager.allowed_devices = data.get('allowed_devices', [])
        self.saved_presets = data.get('saved_presets', {})
        self.car_overlay_config = data.get('car_overlay_config', {})
        self.car_overlay_feedback = data.get('car_overlay_feedback', self.car_overlay_feedback)
        self.active_vars = [_normalize_var_tuple(item) for item in data.get('active_vars', [])]
        self.current_car = data.get('current_car', '')
        self.current_track = data.get('current_track', '')
        self.current_surface = self._normalize_surface_label(data.get('current_surface', DEFAULT_SURFACE_PRESET))
        self._normalize_saved_presets()
        if not self.p2p_overlay_default_off_migrated:
            self._disable_push_to_pass_overlay_default()
            self.p2p_overlay_default_off_migrated = True
            self.save_config()

    def _normalize_saved_presets(self) -> None:
        """Upgrade any saved presets to the surface-aware structure."""
        for car, track_map in list(self.saved_presets.items()):
            if not isinstance(track_map, dict):
                continue
            car_keys = track_map.get('_car_keys')
            if not isinstance(car_keys, dict):
                car_keys = {}
            for track in list(track_map.keys()):
                if track in {'_overlay', '_overlay_feedback', '_car_keys'}:
                    continue
                entry = self._ensure_track_surface_presets(car, track)
                for surface_data in entry.get('surface_presets', {}).values():
                    tabs_data = surface_data.get('tabs', {})
                    for var_name, tab_cfg in tabs_data.items():
                        if not isinstance(tab_cfg, dict):
                            continue
                        if var_name in car_keys:
                            existing = car_keys[var_name]
                            if isinstance(existing, dict):
                                for ghost_key in ('ghost_increase_bind', 'ghost_decrease_bind'):
                                    if ghost_key in tab_cfg and ghost_key not in existing:
                                        existing[ghost_key] = tab_cfg.get(ghost_key)
                            continue
                        if 'key_increase' in tab_cfg or 'key_decrease' in tab_cfg:
                            car_keys[var_name] = {'key_increase': tab_cfg.get('key_increase'), 'key_decrease': tab_cfg.get('key_decrease'), 'key_increase_text': tab_cfg.get('key_increase_text', 'Set Increase (+)'), 'key_decrease_text': tab_cfg.get('key_decrease_text', 'Set Decrease (-)'), 'ghost_increase_bind': tab_cfg.get('ghost_increase_bind'), 'ghost_decrease_bind': tab_cfg.get('ghost_decrease_bind')}
            if car_keys:
                track_map['_car_keys'] = car_keys

    def _disable_push_to_pass_overlay_default(self) -> None:
        """Turn off legacy Push To Pass HUD visibility created by the old default."""

        def _disable(overlay_config: Any) -> None:
            if not isinstance(overlay_config, dict):
                return
            p2p_config = overlay_config.get('dcPushToPass')
            if isinstance(p2p_config, dict):
                p2p_config['show'] = False

        for overlay_config in self.car_overlay_config.values():
            _disable(overlay_config)
        for track_map in self.saved_presets.values():
            if isinstance(track_map, dict):
                _disable(track_map.get('_overlay'))

    @staticmethod
    def _is_hybrid_hold_var(var_name: str) -> bool:
        """Return True for IR18 hybrid hold controls."""
        return var_name in HYBRID_HOLD_VARS

    @staticmethod
    def _parse_lap_filter(value: Any) -> Optional[int]:
        """Parse a row lap filter. Blank means every lap; Outlap maps to 0."""
        text = str(value or '').strip()
        if not text:
            return None
        lowered = text.lower().replace(' ', '').replace('-', '')
        if lowered in {'out', 'outlap', 'saida'}:
            return 0
        if lowered.startswith('lap'):
            lowered = lowered[3:]
        try:
            return int(round(float(lowered)))
        except Exception:
            return None

    def _read_current_sdk_lap(self) -> Optional[int]:
        """Read the SDK Lap value, falling back to LapCompleted + 1."""
        value = self._read_ir_value('Lap', use_cache=False)
        if isinstance(value, numbers.Real):
            return int(round(float(value)))
        completed = self._read_ir_value('LapCompleted', use_cache=False)
        if isinstance(completed, numbers.Real):
            return max(0, int(round(float(completed))) + 1)
        return None

    def _lap_filter_matches(self, filter_value: Any, sdk_lap: Optional[int]) -> bool:
        """Return True when a row lap filter matches the current SDK lap."""
        wanted_lap = self._parse_lap_filter(filter_value)
        if wanted_lap is None:
            return True
        if sdk_lap is None:
            return False
        return wanted_lap == sdk_lap

    def _read_lap_completed(self) -> Optional[int]:
        """Read LapCompleted as an integer when available."""
        completed = self._read_ir_value('LapCompleted', use_cache=False)
        if isinstance(completed, numbers.Real):
            return int(round(float(completed)))
        return None

    def _hybrid_hold_config(self, var_name: str) -> Dict[str, Any]:
        """Collect sanitized hybrid hold settings for a hybrid control tab."""
        if var_name == HYBRID_BOOST_HOLD_VAR:
            default_min, default_max = (0.0, 0.02)
        else:
            default_min, default_max = (0.99, 1.0)
        cfg = {'enabled': True, 'stop_soc_min': default_min, 'stop_soc_max': default_max, 'max_hold_s': 12.0}
        tab = self.tabs.get(var_name)
        if not tab or not getattr(tab, 'is_hybrid_hold', False):
            return cfg
        cfg['enabled'] = bool(tab.hybrid_hold_enabled.get())
        stop_min = max(0.0, min(1.0, self._safe_float(tab.hybrid_stop_soc_min.get(), default_min)))
        stop_max = max(0.0, min(1.0, self._safe_float(tab.hybrid_stop_soc_max.get(), default_max)))
        if stop_max < stop_min:
            stop_min, stop_max = (stop_max, stop_min)
        cfg['stop_soc_min'] = stop_min
        cfg['stop_soc_max'] = stop_max
        max_hold = self._safe_float(tab.hybrid_max_hold_s.get(), 12.0)
        cfg['max_hold_s'] = max(0.25, min(30.0, max_hold))
        return cfg

    def _read_hybrid_soc(self) -> Optional[float]:
        """Read hybrid state of charge as a 0.0-1.0 value when available."""
        value = self._read_ir_value('EnergyERSBatteryPct', use_cache=False)
        if isinstance(value, numbers.Real):
            return max(0.0, min(1.0, float(value)))
        energy = self._read_ir_value('EnergyERSBattery', use_cache=False)
        if isinstance(energy, numbers.Real):
            raw = float(energy)
            if 0.0 <= raw <= 1.0:
                return raw
            return max(0.0, min(1.0, raw / HYBRID_BATTERY_FULL_J))
        return None

    def _notify_hybrid_status(self, text: str, color: str) -> None:
        """Send hybrid status feedback without letting shutdown races matter."""
        try:
            self.notify_overlay_status(text, color)
        except Exception:
            pass

    def _start_hybrid_hold(self, controller: GenericController, var_name: str, preset_index: Optional[int], preset_config: Optional[Dict[str, Any]]) -> None:
        """Press and hold a hybrid button until SOC reaches the stop threshold."""
        return
        if self.app_state != 'RUNNING' or not self._commands_allowed():
            return
        binding = controller.key_increase
        label = 'Hybrid Deploy' if var_name == HYBRID_BOOST_HOLD_VAR else 'Hybrid Regen'
        if binding is None:
            self._notify_hybrid_status(f'{label}: no key configured', 'red')
            return
        cfg = self._hybrid_hold_config(var_name)
        direction = 'deploy' if var_name == HYBRID_BOOST_HOLD_VAR else 'regen'
        stop_soc = random.uniform(float(cfg['stop_soc_min']), float(cfg['stop_soc_max']))
        stop_event = threading.Event()
        state = {'var_name': var_name, 'binding': binding, 'stop_event': stop_event, 'stop_soc': stop_soc, 'max_hold_s': cfg['max_hold_s'], 'direction': direction, 'label': label, 'preset_index': preset_index, 'pressed': False}
        with self._hybrid_hold_lock:
            existing = self._hybrid_hold_states.get(var_name)
            if existing and (not existing.get('stop_event').is_set()):
                return
            self._hybrid_hold_states[var_name] = state
        thread = threading.Thread(target=self._hybrid_hold_worker, args=(state,), daemon=True)
        state['thread'] = thread
        thread.start()

    def _hybrid_hold_worker(self, state: Dict[str, Any]) -> None:
        """Worker that owns one hybrid press-hold-release lifecycle."""
        var_name = state['var_name']
        binding = state['binding']
        stop_event: threading.Event = state['stop_event']
        label = state.get('label', 'Hybrid')
        direction = state.get('direction', 'deploy')
        stop_soc = float(state.get('stop_soc', 0.0))
        max_hold_s = float(state.get('max_hold_s', 12.0))
        reason = 'stopped'
        try:
            _press_game_input(binding)
            state['pressed'] = True
            self._notify_hybrid_status(f'{label} hold -> {stop_soc:.1%}', 'orange')
            started_at = time.time()
            while not stop_event.wait(0.03):
                if self.app_state != 'RUNNING':
                    reason = 'mode changed'
                    break
                if not self._commands_allowed():
                    reason = 'commands blocked'
                    break
                soc = self._read_hybrid_soc()
                if soc is not None:
                    if direction == 'deploy' and soc <= stop_soc:
                        reason = f'SOC {soc:.0%}'
                        break
                    if direction == 'regen' and soc >= stop_soc:
                        reason = f'SOC {soc:.0%}'
                        break
                if time.time() - started_at >= max_hold_s:
                    reason = 'timeout'
                    break
        except Exception as exc:
            reason = 'failed'
            print(f'[Hybrid Hold] {label} failed: {exc}')
        finally:
            if state.get('pressed'):
                try:
                    _release_game_input(binding)
                except Exception as exc:
                    print(f'[Hybrid Hold] Release failed: {exc}')
                state['pressed'] = False
            with self._hybrid_hold_lock:
                if self._hybrid_hold_states.get(var_name) is state:
                    self._hybrid_hold_states.pop(var_name, None)
            color = 'red' if reason == 'failed' else 'green'
            self._notify_hybrid_status(f'{label} released ({reason})', color)

    def _stop_hybrid_hold(self, var_name: Optional[str]=None) -> None:
        """Request release of one or all active hybrid holds."""
        with self._hybrid_hold_lock:
            if var_name is None:
                states = list(self._hybrid_hold_states.values())
            else:
                state = self._hybrid_hold_states.get(var_name)
                states = [state] if state else []
        for state in states:
            stop_event = state.get('stop_event')
            if stop_event:
                stop_event.set()
            binding = state.get('binding')
            if state.get('pressed') and binding is not None:
                try:
                    _release_game_input(binding)
                except Exception:
                    pass
                state['pressed'] = False

    def _trigger_preset_action(self, controller: GenericController, target: float, var_name: str, preset_index: Optional[int], preset_config: Optional[Dict[str, Any]]) -> None:
        """Trigger a preset macro only from the explicit user binding."""
        if self.app_state != 'RUNNING':
            return
        controller.request_target(target)

    def _make_preset_action(self, controller: GenericController, target: float, var_name: str, preset_index: Optional[int], preset_config: Optional[Dict[str, Any]]):
        """Create an action that adjusts a single controller to a target."""
        return lambda: self._trigger_preset_action(controller, target, var_name, preset_index, preset_config)

    def _make_combo_action(self, values: Dict[str, str]):
        """Create an action that adjusts multiple controllers at once."""

        def combo_action():
            if self.app_state != 'RUNNING':
                return
            for var_name, val_str in values.items():
                if var_name in self.controllers and val_str:
                    try:
                        target = float(val_str)
                    except Exception:
                        continue
                    ctrl = self.controllers[var_name]
                    ctrl.request_target(target)
        return combo_action

    def register_current_listeners(self):
        """Register keyboard/joystick listeners based on current config."""
        self._clear_keyboard_hotkeys()
        input_manager.listeners.clear()
        if self.app_state != 'RUNNING':
            input_manager.active = False
            return
        if not input_manager.safe_mode:
            input_manager.connect_allowed_devices(input_manager.allowed_devices)

        def _register_input_binding(bind_code: Optional[str], action: Callable) -> None:
            """Register keyboard hotkeys or joystick listeners with compat aliases."""
            if not bind_code:
                return
            if bind_code.startswith('KEY:'):
                key_name = bind_code.split(':', 1)[1].lower()
                handle = keyboard.add_hotkey(key_name, action)
                self._hotkey_handles.append(handle)
                return
            input_manager.listeners[bind_code] = action
            button_idx = _parse_joy_button_code(bind_code)
            if button_idx is not None and len(input_manager.joysticks) == 1:
                input_manager.listeners.setdefault(f'JOYANY:{button_idx}', action)
        for var_name, tab in self.tabs.items():
            config = tab.get_config()
            controller = self.controllers[var_name]
            manual_increase_bind = config.get('ghost_increase_bind')
            if manual_increase_bind:
                _register_input_binding(manual_increase_bind, lambda tab=tab: tab.trigger_manual_pulse_hotkey('increase'))
            manual_decrease_bind = config.get('ghost_decrease_bind')
            if manual_decrease_bind:
                _register_input_binding(manual_decrease_bind, lambda tab=tab: tab.trigger_manual_pulse_hotkey('decrease'))
            for idx, preset in enumerate(config.get('presets', [])):
                bind = preset.get('bind')
                val_str = preset.get('val')
                if not val_str:
                    continue
                try:
                    target = float(val_str)
                except Exception:
                    continue
                action = self._make_preset_action(controller, target, var_name, idx, preset)
                _register_input_binding(bind, action)
        if self.combo_tab:
            combo_config = self.combo_tab.get_config()
            for preset in combo_config.get('presets', []):
                bind = preset.get('bind')
                values = preset.get('vals', {})
                action = self._make_combo_action(values)
                _register_input_binding(bind, action)
        if self.clear_target_bind:
            action = self.clear_all_targets
            _register_input_binding(self.clear_target_bind, action)
        if self.manual_rescan_bind:
            action = self.manual_restart_scan
            _register_input_binding(self.manual_rescan_bind, action)
        if self.surface_toggle_bind:
            action = self.toggle_surface_preset
            _register_input_binding(self.surface_toggle_bind, action)
        if self.surface_dry_bind:
            action = lambda: self.set_surface_preset('DRY')
            _register_input_binding(self.surface_dry_bind, action)
        if self.surface_wet_bind:
            action = lambda: self.set_surface_preset('WET')
            _register_input_binding(self.surface_wet_bind, action)
        input_manager.active = self.app_state == 'RUNNING'

    def _refresh_controller_ir(self):
        """Ensure all controllers use the latest IRSDK handle."""
        for controller in self.controllers.values():
            controller.ir = self.ir

    def _clear_keyboard_hotkeys(self):
        """Remove all keyboard hotkeys registered by the app."""
        if not hasattr(self, '_hotkey_handles'):
            self._hotkey_handles: List[Any] = []
        for handle in self._hotkey_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._hotkey_handles.clear()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

    def _apply_inline_config(self, tab_configs: Dict[str, Dict[str, Any]], combo_config: Dict[str, Any]):
        """Reapply unsaved tab/combo configuration after a rescan."""
        wiper_config_applied = False
        for var_name, config in tab_configs.items():
            if var_name in self.tabs:
                try:
                    self.tabs[var_name].set_config(config)
                    if var_name in WIPER_TOGGLE_VARS:
                        wiper_config_applied = True
                except Exception:
                    pass
        if not wiper_config_applied:
            wiper_tab = self._wiper_tab()
            wiper_cfg = self._wiper_alias_config(tab_configs)
            if wiper_tab and wiper_cfg:
                try:
                    wiper_tab.set_config(wiper_cfg)
                except Exception:
                    pass
        if self.combo_tab and combo_config:
            try:
                self.combo_tab.set_config(combo_config)
            except Exception:
                pass

    def restore_defaults(self):
        """Delete the configuration file and restart the app after confirmation."""
        if not self._ask_yes_no('Restore Defaults', 'This will delete your configuration file and restart the app. Continue?'):
            return
        try:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
        except Exception as exc:
            self._show_error('Error', f'Failed to delete config: {exc}')
            return
        self._show_info('Defaults Restored', 'Configuration reset. The application will restart now.')
        restart_program()

def main():
    """Main application entry point."""
    try:
        set_windows_app_user_model_id()
        root = tk.Tk()
        root.withdraw()
        iRacingControlApp(root)
        root.deiconify()
        root.mainloop()
    except Exception as e:
        print(f'Fatal Error: {e}')
        import traceback
        traceback.print_exc()
        input('Press Enter to close...')
if __name__ == '__main__':
    main()
