from __future__ import annotations
import atexit
from .foundation import *
from .ui.dialogs import *
from .ui.hud import *
from .ui.control_tabs import *
from .ui.lapdist_capture import LapDistCaptureCoordinator
from .core import SecondThrottleAutomation, SecondThrottleEngine, SecondThrottlePedalTakeover, TelemetryHub, default_second_throttle_macro, default_second_throttle_pit_macro
from .integration import IntegrationCoordinator
from .edition import enforce_public_state
from .app_mixins.ui import UiMixin
from .app_mixins.automation import AutomationMixin
from .app_mixins.lifecycle import LifecycleMixin
from .app_mixins.bindings import BindingsMixin
from .app_mixins.ghost_keys import GhostKeysMixin
from .app_mixins.presets import PresetsMixin
from .app_mixins.telemetry import TelemetryMixin
from .app_mixins.updates import UpdatesMixin

class iRacingControlApp(UiMixin, AutomationMixin, LifecycleMixin, BindingsMixin, GhostKeysMixin, PresetsMixin, TelemetryMixin, UpdatesMixin):

    def __init__(self, root: tk.Tk, telemetry_hub: TelemetryHub | None=None):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry('1180x820')
        self.root.minsize(980, 680)
        apply_app_icon(self.root)
        self._configure_styles()
        self.root.protocol('WM_DELETE_WINDOW', self._close_app)
        self.root.bind('<Unmap>', self._handle_minimize_event)
        self._uiq: 'queue.Queue[Tuple[Callable, tuple, dict]]' = queue.Queue()
        self.root.after(30, self._drain_ui_queue)
        self.telemetry_hub = telemetry_hub or TelemetryHub()
        self.telemetry_hub.start()
        self.ir = self.telemetry_hub.proxy
        self.ir_lock = threading.RLock()
        self.integration: IntegrationCoordinator | None = None
        self._sdk_warmup_lock = threading.Lock()
        self._sdk_warmup_thread: Optional[threading.Thread] = None
        self._sdk_last_warmup_attempt = 0.0
        self.app_state = 'RUNNING'
        self.controllers: Dict[str, GenericController] = {}
        self.tabs: Dict[str, ControlTab] = {}
        self.combo_tab: Optional[ComboTab] = None
        self._init_ghost_keys_state()
        self.overlay_tab: Optional[OverlayConfigTab] = None
        self.combo_frame: Optional[tk.Frame] = None
        self.overlay_frame: Optional[tk.Frame] = None
        self.combo_tab_label = '⚡ Combos'
        self.overlay_tab_label = "HUD / Overlay"
        self.voice_window: Optional[tk.Toplevel] = None
        self._control_tab_label_refresh_job: Optional[str] = None
        self._control_tab_font: Optional[tkfont.Font] = None
        self._hud_lapdist_default_off_version = 0
        self._auto_sync_iracing_controls_default_off_version = 0
        self._timing_force_bot_version = 0
        self._config_migration_save_needed = False
        self.saved_presets: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_config: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.car_overlay_feedback: Dict[str, Dict[str, float]] = {}
        self.show_overlay_feedback = tk.BooleanVar(value=True)
        self._overlay_feedback_state = {'last_time': time.time(), 'abs_active': 0.0, 'tc_active': 0.0, 'spin_active': 0.0, 'lock_active': 0.0, 'last_alert': '', 'last_alert_time': 0.0}
        self.active_vars: List[Tuple[str, bool, bool]] = []
        self.overlay_extra_vars: List[Tuple[str, bool, bool]] = [('dcPushToPass', False, True), ('LapDistPct', True, False)]
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
        self._scan_started_at = 0.0
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
        self.auto_load_attempted: set = set()
        self.overlay = OverlayWindow(root)
        self.overlay.withdraw()
        self.overlay_visible = True
        self.hud_visible_var = tk.BooleanVar(value=True)
        self._overlay_visible_before_discreet: Optional[bool] = None
        self.lapdist_capture = LapDistCaptureCoordinator(self)
        self.use_keyboard_only = tk.BooleanVar(value=False)
        self.use_tts = tk.BooleanVar(value=False)
        self.use_voice = tk.BooleanVar(value=VOICE_FEATURES_ENABLED)
        self.voice_engine = tk.StringVar(value='speech')
        self.vosk_model_path = tk.StringVar(value='')
        self.whisper_binary_path = tk.StringVar(value='')
        self.whisper_model_path = tk.StringVar(value='')
        self.microphone_device = tk.IntVar(value=-1)
        self.audio_output_device = tk.IntVar(value=-1)
        self.vosk_status_var = tk.StringVar(value='')
        self.whisper_status_var = tk.StringVar(value='')
        self.voice_engine_combo: Optional[ttk.Combobox] = None
        self.btn_vosk_model: Optional[tk.Button] = None
        self.btn_whisper_binary: Optional[tk.Button] = None
        self.btn_whisper_model: Optional[tk.Button] = None
        self.mic_combo: Optional[ttk.Combobox] = None
        self.audio_output_combo: Optional[ttk.Combobox] = None
        self.voice_ambient_duration = tk.DoubleVar(value=VOICE_TUNING_DEFAULTS['ambient_duration'])
        self.voice_initial_timeout = tk.DoubleVar(value=VOICE_TUNING_DEFAULTS['initial_timeout'])
        self.voice_continuous_timeout = tk.DoubleVar(value=VOICE_TUNING_DEFAULTS['continuous_timeout'])
        self.voice_phrase_time_limit = tk.DoubleVar(value=VOICE_TUNING_DEFAULTS['phrase_time_limit'])
        self.voice_energy_threshold = tk.StringVar(value='')
        self.voice_dynamic_energy = tk.BooleanVar(value=VOICE_TUNING_DEFAULTS['dynamic_energy'])
        self.auto_detect = tk.BooleanVar(value=True)
        self.auto_scan_on_change = tk.BooleanVar(value=True)
        self.auto_restart_on_rescan = tk.BooleanVar(value=False)
        self.auto_restart_on_race = tk.BooleanVar(value=False)
        self.auto_restart_on_track_ready = tk.BooleanVar(value=False)
        self.auto_sync_iracing_controls = tk.BooleanVar(value=False)
        self.block_off_track_commands = tk.BooleanVar(value=False)
        self.keep_trying_targets = tk.BooleanVar(value=True)
        self.show_scan_popup = tk.BooleanVar(value=False)
        self.keep_scanning_until_valid = tk.BooleanVar(value=True)
        self.disable_popups = tk.BooleanVar(value=True)
        self.auto_save_presets = tk.BooleanVar(value=True)
        self.lock_preset_selection = tk.BooleanVar(value=True)
        self.start_with_windows = tk.BooleanVar(value=True)
        self.focus_on_start = tk.BooleanVar(value=False)
        self.show_getting_started = tk.BooleanVar(value=True)
        self.discreet_mode = tk.BooleanVar(value=False)
        self.use_lapdist_macros = tk.BooleanVar(value=True)
        self.lapdist_humanize = tk.BooleanVar(value=True)
        self.lapdist_training_mode = tk.BooleanVar(value=False)
        self.lapdist_humanize_delay_min = tk.StringVar(value='0.05')
        self.lapdist_humanize_delay_max = tk.StringVar(value='0.18')
        self.lapdist_min_spacing = tk.StringVar(value='0.25')
        self.lapdist_cooldown_sensitivity = tk.StringVar(value='0.35')
        self.lapdist_show_close_log = tk.BooleanVar(value=False)
        self.lapdist_learning_enabled = tk.BooleanVar(value=True)
        self.lapdist_learning_persist = tk.BooleanVar(value=False)
        self.turbo_pit_enabled = tk.BooleanVar(value=True)
        self.turbo_pit_enabled_by_series: Dict[str, bool] = {}
        self._active_turbo_pit_series_key: Optional[str] = None
        self.turbo_pit_status_var = tk.StringVar(value="ON")
        self.turbo_pit_action_var = tk.StringVar(value="Turn Turbo Pit off")
        self.turbo_pit_hint_var = tk.StringVar(value="Automatic clearing runs when you enter the pit lane.")
        self.turbo_pit_series_var = tk.StringVar(value="Series: no active iRacing session detected")
        self.turbo_pit_toggle_bind: Optional[str] = None
        self.btn_turbo_pit_bind: Optional[tk.Button] = None
        self.second_throttle_output_bind: Optional[Any] = None
        self.second_throttle_output_label = ''
        self.second_throttle_toggle_bind: Optional[str] = None
        self.second_throttle_percent = tk.StringVar(value='100')
        self.second_throttle_macros: List[Dict[str, Any]] = [default_second_throttle_macro()]
        self.second_throttle_pit_macro: Dict[str, Any] = default_second_throttle_pit_macro()
        self.second_throttle_active_macro_id: Optional[str] = None
        self.second_throttle_active_macro_name = ''
        self.second_throttle_active_source = ''
        self.second_throttle_automation = SecondThrottleAutomation()
        self.second_throttle_pedal_takeover = SecondThrottlePedalTakeover()
        self.second_throttle_status_var = tk.StringVar(value="OFF")
        self.second_throttle_detail_var = tk.StringVar(value="Set the iRacing key and create your macros.")
        self.second_throttle_action_var = tk.StringVar(value="Turn on now")
        self.second_throttle_panel: Optional[Any] = None
        self.second_throttle_engine = SecondThrottleEngine(_press_game_input, _release_game_input, state_callback=self._on_second_throttle_state, error_callback=self._on_second_throttle_error)
        atexit.register(self.second_throttle_engine.shutdown)
        self.wiper_debug_enabled = tk.BooleanVar(value=False)
        self.clear_target_bind: Optional[str] = None
        self.btn_clear_target_bind: Optional[tk.Button] = None
        self.manual_rescan_bind: Optional[str] = None
        self.btn_manual_rescan_bind: Optional[tk.Button] = None
        self.lapdist_toggle_bind: Optional[str] = None
        self.btn_lapdist_toggle_bind: Optional[tk.Button] = None
        self.lapdist_capture_bind: Optional[str] = None
        self.btn_lapdist_capture_bind: Optional[tk.Button] = None
        self.surface_toggle_bind: Optional[str] = None
        self.btn_surface_toggle_bind: Optional[tk.Button] = None
        self.surface_dry_bind: Optional[str] = None
        self.btn_surface_dry_bind: Optional[tk.Button] = None
        self.surface_wet_bind: Optional[str] = None
        self.btn_surface_wet_bind: Optional[tk.Button] = None
        self.surface_dc_profiles_enabled = tk.BooleanVar(value=True)
        self.surface_dc_auto_declared_wet = tk.BooleanVar(value=True)
        self.surface_dc_endurance_enabled = tk.BooleanVar(value=True)
        self.surface_dc_endurance_mode = tk.StringVar(value='AUTO')
        self.surface_dc_profiles: Dict[str, Dict[str, Any]] = {}
        self.surface_dc_dry_bind: Optional[str] = None
        self.surface_dc_wet_bind: Optional[str] = None
        self.btn_surface_dc_dry_bind: Optional[tk.Button] = None
        self.btn_surface_dc_wet_bind: Optional[tk.Button] = None
        self.btn_surface_dc_capture_dry: Optional[tk.Button] = None
        self.btn_surface_dc_capture_wet: Optional[tk.Button] = None
        self.btn_surface_dc_apply_dry: Optional[tk.Button] = None
        self.btn_surface_dc_apply_wet: Optional[tk.Button] = None
        self.surface_dc_rows_frame: Optional[tk.Frame] = None
        self.surface_dc_rows: Dict[str, Dict[str, Any]] = {}
        self._surface_dc_rows_car = ''
        self._surface_dc_edit_widgets: List[tk.Widget] = []
        self.surface_dc_declared_status_var = tk.StringVar(value="Declared condition: waiting for iRacing")
        self.surface_dc_active_status_var = tk.StringVar(value="Active profile: none")
        self.surface_dc_values_summary_var = tk.StringVar(value="DRY: 0 values  •  WET: 0 values")
        self.surface_dc_endurance_status_var = tk.StringVar(value="Car entry: waiting for the driver")
        self.wiper_debug_bind: Optional[str] = None
        self.btn_wiper_debug_bind: Optional[tk.Button] = None
        self.bounds_discovery_delay_s = tk.StringVar(value='5.0')
        self.entry_bounds_discovery_delay: Optional[tk.Entry] = None
        self.btn_discover_all_bounds_header: Optional[tk.Button] = None
        self.btn_discreet_mode: Optional[tk.Button] = None
        self.stability_frame: Optional[tk.LabelFrame] = None
        self.lapdist_frame: Optional[tk.LabelFrame] = None
        self.presets_frame: Optional[tk.LabelFrame] = None
        self.devices_frame: Optional[tk.LabelFrame] = None
        self.scan_frame: Optional[tk.LabelFrame] = None
        self.turbo_pit_frame: Optional[tk.LabelFrame] = None
        self.turbo_pit_status_card: Optional[tk.Frame] = None
        self.lbl_turbo_pit_status: Optional[tk.Label] = None
        self.lbl_turbo_pit_hint: Optional[tk.Label] = None
        self.lbl_turbo_pit_series: Optional[tk.Label] = None
        self.btn_turbo_pit_toggle: Optional[tk.Button] = None
        self.entry_lapdist_delay_min: Optional[tk.Entry] = None
        self.entry_lapdist_delay_max: Optional[tk.Entry] = None
        self.entry_lapdist_min_spacing: Optional[tk.Entry] = None
        self.entry_lapdist_cooldown: Optional[tk.Entry] = None
        self.voice_phrase_map: Dict[str, Callable] = {}
        self._voice_traces_attached = False
        self._config_save_job: Optional[str] = None
        self._auto_save_job: Optional[str] = None
        self.getting_started_window: Optional[tk.Toplevel] = None
        self.lapdist_log_window: Optional[tk.Toplevel] = None
        self.lapdist_log_text: Optional[tk.Text] = None
        self.lapdist_overlay_window: Optional[LapDistOverlayWindow] = None
        self.lapdist_overlay_visible = False
        self.btn_lapdist_overlay: Optional[tk.Button] = None
        self.btn_lapdist_overlay_header: Optional[tk.Button] = None
        self.btn_lapdist_overlay_main: Optional[tk.Button] = None
        self.btn_sync_iracing_controls_header: Optional[tk.Button] = None
        self._lapdist_last_pct: Optional[float] = None
        self._lapdist_last_sdk_lap: Optional[int] = None
        self._lapdist_last_completed_lap: Optional[int] = None
        self._lapdist_run_lap_offset: Optional[int] = None
        self._lapdist_effective_lap: Optional[int] = None
        self._lapdist_last_reset_notice = 0.0
        self._lapdist_samples: Deque[Tuple[float, float]] = deque(maxlen=8)
        self._lapdist_last_slope = 0.0
        self._lapdist_lap_index = 0
        self._lapdist_macro_state: Dict[Tuple[str, int], Dict[str, Any]] = {}
        self._lapdist_clear_lap_index: Optional[int] = None
        self._lapdist_activation_history: Dict[str, Deque[float]] = {}
        self._lapdist_auto_state: Dict[str, Dict[str, Any]] = {}
        self._lapdist_rng_seed = self._seed_lapdist_rng()
        self._lapdist_rng = random.Random(self._lapdist_rng_seed)
        self._lapdist_rng_last_perturb = time.time()
        self._lapdist_hotkey_enabled = True
        self._lapdist_struggle_log: List[Dict[str, Any]] = []
        self._lapdist_learning_data: Dict[str, Dict[str, Any]] = {}
        self._lapdist_learning_step = 0.01
        self._pit_limiter_learning_data: Dict[str, Dict[str, Any]] = {}
        self._p2p_chain_lock = threading.Lock()
        self._p2p_chain_threads: Dict[str, threading.Event] = {}
        self._hybrid_hold_lock = threading.Lock()
        self._hybrid_hold_states: Dict[str, Dict[str, Any]] = {}
        self._pit_limiter_state: Dict[str, Any] = {'last_on_pit': None, 'last_entry_signal': None, 'pending_action': None, 'pending_since': None, 'pending_delay': 0.0, 'pending_reason': None, 'last_action': None, 'auto_on_active': False, 'emergency_recheck_after': 0.0, 'last_trigger_delay': None, 'approach_blocked_after_exit': False, 'approach_block_until': 0.0, 'cooldown_until': 0.0, 'scan_until': 0.0, 'jitter_state': 0.0, 'drift_state': 0.0, 'drift_last_update': 0.0, 'poll_jitter_state': 0.0, 'poll_drift_state': 0.0, 'poll_drift_last_update': 0.0, 'track_key': None, 'approach_seen_at': 0.0, 'learned_distance_m': None, 'learned_lead_m': None}
        self._wiper_state: Dict[str, Any] = {'startup_checked': False, 'last_on_track': None, 'cooldown_until': 0.0, 'last_action': None, 'last_desired': None, 'last_phase': None, 'last_trigger_phase': None, 'pending_action': None, 'pending_phase': None, 'pending_since': None, 'pending_delay': 0.0, 'threshold_drift_on': 0.0, 'threshold_drift_off': 0.0, 'threshold_jitter_on': 0.0, 'threshold_jitter_off': 0.0, 'threshold_last_update': 0.0, 'debug_last_log': 0.0}
        self._turbo_pit_state: Dict[str, Any] = {'last_on_pit': None}
        self._fuel_mixture_state: Dict[str, Any] = {'last_caution': None, 'last_flags': 0, 'pending_kind': None, 'pending_since': None, 'pending_delay': 0.0, 'pending_target': None, 'restart_armed_until': 0.0, 'cooldown_until': 0.0}
        self._surface_dc_state: Dict[str, Any] = {'session_token': None, 'last_declared_wet': None, 'last_in_car': None, 'last_on_pit': None, 'active_profile': None, 'active_profile_car': '', 'pending_profile': None, 'last_apply_time': 0.0, 'last_apply_reason': '', 'last_ui_refresh': 0.0, 'last_enabled': False, 'force_reapply': False, 'endurance_active': False, 'desired_targets': {}, 'desired_profile': None, 'desired_allow_pit_service': False, 'desired_reason': '', 'precision_started_at': 0.0, 'last_precision_supervision': 0.0, 'stint_id': 0, 'stint_started_at': 0.0, 'stint_reason': '', 'applied_signature': None, 'stint_profile': None, 'stint_profile_source': '', 'next_attempt': 0.0, 'waiting_reason': '', 'entry_guard_profile': None, 'entry_guard_until': 0.0, 'entry_guard_last_check': 0.0}
        self._iracing_controls_watch_state: Dict[str, Any] = {}
        self._iracing_controls_import_job: Optional[str] = None
        self.getting_started_text = "Quick checklist\n1) Pick your car and track.\n2) Confirm your input devices.\n3) Scan the driver controls.\n4) Open Options to set up your hotkeys.\n\nIf the car/track selectors are greyed out, iRacing manages them for you once you join a session. Join a session first to work with presets, or turn off “Lock car/track selection” on the Options tab.\n\nUse CONFIG mode to change hotkeys and RUNNING mode to drive."
        self.load_config()
        enforce_public_state(self)
        self._sync_second_throttle_engine()
        if not VOICE_FEATURES_ENABLED:
            self.use_voice.set(False)
        self._apply_startup_preference(notify=False)
        self._create_menu()
        self._create_main_ui()
        self.integration = IntegrationCoordinator(self, self.telemetry_hub)
        self.integration.attach(self.main_tabs)
        self.integration.start()
        self._update_voice_controls()
        self._apply_startup_focus_mode()
        if self._config_migration_save_needed:
            self.save_config()
            self._config_migration_save_needed = False
        self.root.after(100, self._start_sdk_warmup)
        if self.keep_scanning_until_valid.get():
            self.root.after(1200, self._kick_initial_scan)
        self.root.after(300, self._maybe_show_getting_started)
        self.update_safe_mode()
        self.root.after(2000, self.auto_preset_loop)
        self.update_overlay_loop()
        self.root.after(210, self._second_throttle_automation_loop)
        self.root.after(180, self._wiper_macro_loop)
        self.root.after(240, self._turbo_pit_loop)
        self.root.after(320, self._surface_dc_profile_loop)
        self.root.after(1400, self._iracing_controls_auto_sync_loop)
        if UPDATE_CHECK_AVAILABLE:
            self.root.after(4000, self.schedule_update_check)
        if self.overlay_visible:
            self.overlay.deiconify()
        input_manager.active = self.app_state == 'RUNNING'
        self.root.after(200, self._perform_pending_scan)
__all__ = ['iRacingControlApp']
