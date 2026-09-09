"""One pyirsdk connection shared by Dominant Control, Tools and Profiles."""

from __future__ import annotations

import copy
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import irsdk

from .events import EventBus


DEFAULT_FIELDS = {
    "AirTemp",
    "CarIdxClassPosition",
    "CarIdxLapCompleted",
    "CarIdxLapDistPct",
    "CarIdxLastLapTime",
    "CarIdxOnPitRoad",
    "CarIdxP2P_Status",
    "CarIdxPosition",
    "CarIdxTireCompound",
    "CarIdxTrackSurface",
    "DriverCarIdx",
    "DriverInfo",
    "DisplayUnits",
    "DRS_Status",
    "EnergyERSBattery",
    "EnergyERSBatteryPct",
    "EngineWarnings",
    "FuelLevel",
    "FuelLevelPct",
    "IsOnTrack",
    "IsOnTrackCar",
    "Lap",
    "LapBestLapTime",
    "LapDistPct",
    "LapLastLapTime",
    "LatAccel",
    "LongAccel",
    "ManualBoost",
    "ManualNoBoost",
    "OnPitRoad",
    "PaceCarIdx",
    "PitSvFlags",
    "PitSvFuel",
    "PitstopActive",
    "P2P_Status",
    "PlayerCarIdx",
    "PlayerCarPitSvStatus",
    "RelativeHumidity",
    "SessionFlags",
    "SessionInfo",
    "SessionLapsRemain",
    "SessionLapsRemainEx",
    "SessionNum",
    "SessionState",
    "SessionTime",
    "SessionTimeRemain",
    "SessionUniqueID",
    "Speed",
    "SteeringWheelAngle",
    "TrackTemp",
    "ThrottleRaw",
    "WeekendInfo",
    "WeatherDeclaredWet",
    "LFwearL",
    "LFwearM",
    "LFwearR",
    "RFwearL",
    "RFwearM",
    "RFwearR",
    "LRwearL",
    "LRwearM",
    "LRwearR",
    "RRwearL",
    "RRwearM",
    "RRwearR",
}


# Sections of the session string, not telemetry variables. pyirsdk parses them
# from YAML instead of reading a memory offset, and only when iRacing bumps the
# session counter — which is what makes them go stale on a session change.
SESSION_INFO_KEYS = frozenset({
    "CameraInfo",
    "CarSetup",
    "DriverInfo",
    "QualifyResultsInfo",
    "RadioInfo",
    "SessionInfo",
    "SplitTimeInfo",
    "WeekendInfo",
})

# Sections every live session publishes. Only these decide whether the session
# string is broken: the remaining ones are optional and legitimately absent in
# some session types.
SESSION_INFO_REQUIRED_KEYS = frozenset({
    "DriverInfo",
    "SessionInfo",
    "WeekendInfo",
})


@dataclass(frozen=True, slots=True)
class TelemetryStatus:
    initialized: bool
    connected: bool
    generation: int
    sampled_at: float
    error: str = ""


class TelemetryHub:
    """Own the SDK and publish coherent cached generations at a fixed cadence."""

    def __init__(
        self,
        *,
        update_hz: float = 60.0,
        sdk: Any | None = None,
        events: EventBus | None = None,
        simulator_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.events = events or EventBus()
        self.update_hz = max(5.0, min(120.0, float(update_hz)))
        if sdk is None:
            try:
                sdk = irsdk.IRSDK(parse_yaml_async=True)
            except TypeError:
                sdk = irsdk.IRSDK()
        self._sdk = sdk
        self._sdk_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._fields = set(DEFAULT_FIELDS)
        self._cache: dict[str, Any] = {}
        self._sampled_at = 0.0
        self._generation = 0
        self._error = ""
        self._initialized = False
        self._connected = False
        self._stop_event = threading.Event()
        self._reconnect_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._proxy = TelemetryProxy(self)
        self._last_published_connection: bool | None = None
        self._next_startup_attempt = 0.0
        self._startup_retry_seconds = 2.0
        self._startup_socket_timeout = 3.0
        self._simulator_probe = simulator_probe or self._default_simulator_probe
        # Session string recovery. See _recover_session_info_locked.
        self._session_info_hole_since = 0.0
        self._session_info_last_resync = 0.0
        self._session_info_resync_interval = 0.25
        self._session_info_hole_grace = 6.0
        self._session_info_last_reconnect = 0.0
        self._session_info_reconnect_interval = 15.0
        self._session_info_resyncs = 0
        self._session_info_reconnects = 0
        self._session_info_replacements = 0
        self._var_header_repairs = 0
        self._simulator_token: tuple[int, ...] | None = None
        self._simulator_token_checked = 0.0
        self._simulator_token_interval = 1.0

    @property
    def proxy(self) -> "TelemetryProxy":
        return self._proxy

    @property
    def raw_sdk(self) -> Any:
        return self._sdk

    @property
    def sdk_lock(self) -> threading.RLock:
        return self._sdk_lock

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="DominantControl-TelemetryHub",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

        # startup() is third-party code and can remain blocked while iRacing is
        # closed. Closing the GUI must never wait indefinitely for that lock.
        acquired = self._sdk_lock.acquire(timeout=min(max(timeout, 0.0), 0.20))
        if not acquired:
            return
        try:
            try:
                self._sdk.shutdown()
            except Exception:
                pass
        finally:
            self._sdk_lock.release()

    def reconnect(self) -> bool:
        """Schedule a reconnect on the owner thread without blocking callers."""
        self.start()
        with self._cache_lock:
            self._cache.clear()
            self._sampled_at = 0.0
            self._initialized = False
            self._connected = False
        self._reconnect_event.set()
        return False

    def ensure_started(self) -> bool:
        """Ensure the owner worker is running; never call SDK startup here."""
        self.start()
        with self._cache_lock:
            return self._initialized

    def register(self, fields: Iterable[str]) -> None:
        with self._cache_lock:
            self._fields.update(str(field) for field in fields if field)

    def read(self, key: str, default: Any = None, *, max_age: float = 0.12) -> Any:
        """Return a cached field immediately, registering it for the worker."""
        key = str(key)
        with self._cache_lock:
            self._fields.add(key)
            if key in self._cache and time.monotonic() - self._sampled_at <= max_age:
                return self._cache[key]
        return default

    def read_many(self, fields: Iterable[str]) -> tuple[dict[str, Any], TelemetryStatus]:
        names = tuple(dict.fromkeys(str(field) for field in fields if field))
        self.register(names)
        with self._cache_lock:
            values = {name: self._cache.get(name) for name in names}
            status = self.status_locked()
        return values, status

    def snapshot(self) -> tuple[dict[str, Any], TelemetryStatus]:
        with self._cache_lock:
            return dict(self._cache), self.status_locked()

    def status(self) -> TelemetryStatus:
        with self._cache_lock:
            return self.status_locked()

    def status_locked(self) -> TelemetryStatus:
        return TelemetryStatus(
            initialized=self._initialized,
            connected=self._connected,
            generation=self._generation,
            sampled_at=self._sampled_at,
            error=self._error,
        )

    def call_sdk(self, name: str, *args: Any, **kwargs: Any) -> Any:
        # Commands should fail fast while the SDK worker is busy connecting,
        # rather than freezing a Tk callback and the whole Windows window.
        if not self.status().connected:
            raise RuntimeError("iRacing telemetry is not connected")
        acquired = self._sdk_lock.acquire(timeout=0.10)
        if not acquired:
            raise TimeoutError("The iRacing SDK is still trying to connect")
        try:
            method = getattr(self._sdk, name)
            return method(*args, **kwargs)
        finally:
            self._sdk_lock.release()

    def _startup_locked(self) -> None:
        """Call the SDK's startup without letting it block the owner forever.

        pyirsdk asks the local iRacing server whether the simulator is running
        through ``urlopen`` with no timeout, from inside ``startup()``.  While
        the simulator loads a session that request can hang, and it hangs while
        this thread holds the SDK lock: every cached field then stops being
        refreshed and the whole application goes blind until the user restarts
        it.  A default socket timeout is the only way to bound a call that takes
        no timeout of its own.
        """
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self._startup_socket_timeout)
        try:
            self._sdk.startup()
        finally:
            socket.setdefaulttimeout(previous)

    def _ensure_started_locked(self, *, force: bool = False) -> bool:
        try:
            if not bool(getattr(self._sdk, "is_initialized", False)):
                now = time.monotonic()
                if not force and now < self._next_startup_attempt:
                    return False
                self._next_startup_attempt = now + self._startup_retry_seconds
                if not self._simulator_probe():
                    return False
                self._startup_locked()
            if bool(getattr(self._sdk, "is_initialized", False)):
                self._next_startup_attempt = 0.0
            return bool(getattr(self._sdk, "is_initialized", False))
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return False

    def _run(self) -> None:
        interval = 1.0 / self.update_hz
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_tick:
                self._stop_event.wait(next_tick - now)
                continue
            next_tick = max(next_tick + interval, now)
            try:
                self._sample_once()
            except Exception as exc:
                # This thread is the only writer of the shared cache. If it ever
                # died, every reader would silently receive nothing for the rest
                # of the session, which reads exactly like a frozen application.
                with self._cache_lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                self._stop_event.wait(0.05)

    def _sample_once(self) -> None:
        values: dict[str, Any] = {}
        error = ""
        initialized = False
        connected = False
        with self._cache_lock:
            fields = tuple(self._fields)

        with self._sdk_lock:
            try:
                if self._reconnect_event.is_set():
                    self._reconnect_event.clear()
                    try:
                        self._sdk.shutdown()
                    except Exception:
                        pass
                    self._next_startup_attempt = 0.0
                initialized = self._ensure_started_locked()
                connected = bool(initialized and getattr(self._sdk, "is_connected", False))
                if connected:
                    freeze = getattr(self._sdk, "freeze_var_buffer_latest", None)
                    unfreeze = getattr(self._sdk, "unfreeze_var_buffer_latest", None)
                    frozen = False
                    try:
                        self._repair_var_headers_locked()
                        if callable(freeze):
                            freeze()
                            frozen = True
                        for field in fields:
                            try:
                                values[field] = self._stabilize(self._sdk[field])
                            except Exception:
                                continue
                    finally:
                        if frozen and callable(unfreeze):
                            unfreeze()
                    self._recover_session_info_locked(fields, values)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

        with self._cache_lock:
            if values:
                self._cache.update(values)
            elif not connected:
                self._cache.clear()
            self._sampled_at = time.monotonic()
            self._generation += 1
            self._error = error
            self._initialized = initialized
            self._connected = connected
            generation = self._generation

        if connected != self._last_published_connection:
            self._last_published_connection = connected
            self.events.publish(
                "telemetry.connection",
                connected=connected,
                generation=generation,
                error=error,
            )
        self.events.publish(
            "telemetry.sample",
            connected=connected,
            generation=generation,
        )

    def _recover_session_info_locked(
        self,
        fields: tuple[str, ...],
        values: dict[str, Any],
    ) -> None:
        """Refill session string sections that pyirsdk left empty.

        In asynchronous mode pyirsdk marks a section as "already queued" before
        its worker thread runs.  When iRacing bumps the session counter while it
        is still rewriting the session string — exactly what happens when the
        driver leaves a session and joins another one — that worker finds no
        readable section, gives up, and nothing ever queues the section again
        because the marker is already up to date.  ``WeekendInfo`` and
        ``DriverInfo`` then stay empty for as long as the process lives, so the
        application keeps showing the previous car and track until the user
        restarts it.  Parsing the sections again in place recovers within one
        sample instead.
        """
        yaml_fields = tuple(name for name in fields if name in SESSION_INFO_KEYS)
        required = tuple(
            name for name in yaml_fields if name in SESSION_INFO_REQUIRED_KEYS
        )
        if not required:
            self._session_info_hole_since = 0.0
            return

        if self._session_info_is_from_a_finished_session():
            self._session_info_last_resync = time.monotonic()
            self._session_info_resyncs += 1
            self._session_info_replacements += 1
            for name, value in self._resync_session_info_locked(yaml_fields).items():
                if value:
                    values[name] = self._stabilize(value)

        if all(values.get(name) for name in required):
            self._session_info_hole_since = 0.0
            return

        now = time.monotonic()
        if not self._session_info_hole_since:
            self._session_info_hole_since = now

        if now - self._session_info_last_resync >= self._session_info_resync_interval:
            self._session_info_last_resync = now
            self._session_info_resyncs += 1
            for name, value in self._resync_session_info_locked(yaml_fields).items():
                if value:
                    values[name] = self._stabilize(value)

        if all(values.get(name) for name in required):
            self._session_info_hole_since = 0.0
            return

        # A hole that survives the synchronous re-read means this SDK handle is
        # no longer usable. Reconnecting restores it without asking the driver
        # to restart the application mid-session.
        if (
            now - self._session_info_hole_since < self._session_info_hole_grace
            or now - self._session_info_last_reconnect
            < self._session_info_reconnect_interval
        ):
            return

        self._session_info_last_reconnect = now
        self._session_info_hole_since = 0.0
        self._session_info_reconnects += 1
        self._reconnect_event.set()

    def _session_info_is_from_a_finished_session(self) -> bool:
        """Detect a session string that pyirsdk will never invalidate.

        pyirsdk only drops its parsed sections when the simulator's session
        counter is *higher* than the one it last saw::

            if self.last_session_info_update < self._header.session_info_update:

        Every session the driver joins runs a new simulator process whose
        counter restarts near zero, while the shared memory itself belongs to
        the long lived iRacing services and stays mapped.  The telemetry offsets
        therefore follow the new car immediately, but the counter went
        *backwards*, so that comparison stays false and ``WeekendInfo`` and
        ``DriverInfo`` keep describing the session the driver already left — for
        the rest of the process's life.  That is why only restarting the
        application used to fix it: a new handle starts counting from zero.
        """
        sdk = self._sdk
        try:
            current = int(getattr(sdk, "session_info_update", 0) or 0)
            remembered = int(getattr(sdk, "last_session_info_update", 0) or 0)
        except Exception:
            current = remembered = 0
        if current < remembered:
            return True

        # Both sessions can be sitting on the same counter value when the sample
        # lands, which hides the regression above. A new simulator process is
        # always a new session, so its identity settles the question.
        return self._simulator_identity_changed()

    def _simulator_identity_changed(self) -> bool:
        """Report a simulator process replacement, at most once per second."""
        now = time.monotonic()
        if now - self._simulator_token_checked < self._simulator_token_interval:
            return False
        self._simulator_token_checked = now

        token = self._simulator_process_token()
        if token is None:
            return False
        previous = self._simulator_token
        self._simulator_token = token
        if previous is None or token == previous:
            return False
        return bool(token)

    @staticmethod
    def _simulator_process_token() -> tuple[int, ...] | None:
        """Identify the running simulator processes, or None when unknown."""
        if os.name != "nt":
            return None
        try:
            import psutil

            pids = []
            for process in psutil.process_iter(("pid", "name")):
                try:
                    name = str(process.info.get("name") or "").lower()
                    pid = process.info.get("pid")
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
                if "iracingsim" in name and pid is not None:
                    pids.append(int(pid))
            return tuple(sorted(pids))
        except Exception:
            return None

    def _resync_session_info_locked(self, keys: tuple[str, ...]) -> dict[str, Any]:
        """Parse the requested session string sections again, in place.

        ``last_session_info_update`` is the counter pyirsdk compares against the
        session string header, so moving it back makes pyirsdk drop the cached
        sections; turning the asynchronous parser off for the read makes the
        result available to this very sample.  Every requested section is read,
        not only the empty ones, because dropping the cache invalidates them
        all.
        """
        sdk = self._sdk
        try:
            previous_async = bool(getattr(sdk, "parse_yaml_async", False))
            sdk.parse_yaml_async = False
            sdk.last_session_info_update = 0
        except Exception:
            return {}

        parsed: dict[str, Any] = {}
        try:
            for key in keys:
                try:
                    parsed[key] = sdk[key]
                except Exception:
                    continue
        finally:
            try:
                sdk.parse_yaml_async = previous_async
            except Exception:
                pass
        return parsed

    def _repair_var_headers_locked(self) -> None:
        """Drop pyirsdk's variable map when the session replaced it.

        Every car publishes its own set of ``dc*`` variables, at its own
        offsets.  pyirsdk builds that map once and keeps it until shutdown, so
        after a session change a stale map makes each read land on another
        variable's bytes.
        """
        sdk = self._sdk
        header = getattr(sdk, "_header", None)
        if header is None:
            return
        try:
            expected = int(getattr(header, "num_vars", 0) or 0)
        except Exception:
            return
        if expected <= 0:
            return

        cached = getattr(sdk, "_IRSDK__var_headers", None)
        if not isinstance(cached, list) or len(cached) == expected:
            return

        for attribute in (
            "_IRSDK__var_headers",
            "_IRSDK__var_headers_dict",
            "_IRSDK__var_headers_names",
        ):
            try:
                setattr(sdk, attribute, None)
            except Exception:
                continue
        self._var_header_repairs += 1

    @staticmethod
    def _stabilize(value: Any) -> Any:
        if isinstance(value, dict):
            return copy.deepcopy(value)
        if isinstance(value, list):
            return tuple(value)
        if isinstance(value, bytearray):
            return bytes(value)
        return value

    @staticmethod
    def _default_simulator_probe() -> bool:
        """Avoid entering pyirsdk.startup() before the simulator exists."""
        if os.name != "nt":
            return True
        try:
            import psutil

            for process in psutil.process_iter(("name",)):
                try:
                    name = str(process.info.get("name") or "").lower()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
                if "iracingsim" in name:
                    return True
            return False
        except Exception:
            # Process enumeration is only an optimization. If Windows denies
            # it, let pyirsdk perform its normal connection attempt.
            return True


class TelemetryProxy:
    """Compatibility surface matching the pyirsdk API used by legacy code."""

    # This handle is owned by the hub and shared by every feature. Consumers
    # that recover from a failed read by restarting their own SDK must check
    # this before doing so: tearing down a shared handle takes the telemetry
    # away from the whole application, not just from the caller.
    shared_owner = True

    def __init__(self, hub: TelemetryHub) -> None:
        object.__setattr__(self, "_hub", hub)

    def __getitem__(self, key: str) -> Any:
        return self._hub.read(key)

    @property
    def is_initialized(self) -> bool:
        return self._hub.status().initialized

    @property
    def is_connected(self) -> bool:
        return self._hub.status().connected

    def startup(self, *args: Any, **kwargs: Any) -> bool:
        return self._hub.ensure_started()

    def shutdown(self) -> None:
        self._hub.reconnect()

    def freeze_var_buffer_latest(self) -> None:
        return None

    def unfreeze_var_buffer_latest(self) -> None:
        return None

    def pit_command(self, *args: Any, **kwargs: Any) -> Any:
        return self._hub.call_sdk("pit_command", *args, **kwargs)

    def broadcast_msg(self, *args: Any, **kwargs: Any) -> Any:
        return self._hub.call_sdk("broadcast_msg", *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hub.raw_sdk, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_hub":
            object.__setattr__(self, name, value)
            return
        setattr(self._hub.raw_sdk, name, value)
