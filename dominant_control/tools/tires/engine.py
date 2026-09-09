#!/usr/bin/env python3
"""UI-independent stint tracking and tire-wear learning engine."""
from __future__ import annotations
import json
import os
import queue
import re
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import irsdk

from ...paths import tools_data_dir



def _get_appdata_dir() -> Path:
    return tools_data_dir()


APPDATA_DIR = _get_appdata_dir()
MODEL_PATH = str(APPDATA_DIR / "nishizumi_tirewear_model.json")
SETTINGS_PATH = str(APPDATA_DIR / "nishizumi_tirewear_settings.json")
TIRE_KEYS = ("lf", "rf", "lr", "rr")
WEAR_FIELDS = {
    "lf": ("LFwearL", "LFwearM", "LFwearR"),
    "rf": ("RFwearL", "RFwearM", "RFwearR"),
    "lr": ("LRwearL", "LRwearM", "LRwearR"),
    "rr": ("RRwearL", "RRwearM", "RRwearR"),
}
# Inner shoulder for each tire (carcass side). For left tires it's "R", for right tires it's "L".
INNER_WEAR_INDEX = {
    "lf": 2,
    "rf": 0,
    "lr": 2,
    "rr": 0,
}
PIT_TIRE_CHANGE_FLAGS = {
    "lf": 0x0001,
    "rf": 0x0002,
    "lr": 0x0004,
    "rr": 0x0008,
}


@dataclass
class TelemetrySnapshot:
    """Immutable snapshot of telemetry values consumed by the model worker."""

    session_time: float
    lap: int
    lap_dist_pct: float
    on_pit_road: bool
    speed_mps: float
    lat_accel: float
    long_accel: float
    steering: float
    track_temp: float
    air_temp: float
    humidity: float
    pit_sv_flags: int
    wear: Dict[str, float]
    track_name: str
    track_config: str
    car_path: str


class DataStorage:
    """Thread-safe JSON persistence for learned tire model samples."""

    def __init__(self, path: str = MODEL_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()
        self._ensure_model_file_exists()

    def _ensure_model_file_exists(self):
        """Create an empty model file so persistence is visible before first sample."""
        if os.path.exists(self.path):
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            # Non-fatal: learning can still proceed and save later.
            pass

    def _load(self) -> Dict[str, dict]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def get_samples(self, key: str) -> List[dict]:
        with self.lock:
            return list(self.data.get(key, {}).get("samples", []))

    def add_sample(self, key: str, sample: dict):
        with self.lock:
            if key not in self.data:
                self.data[key] = {"samples": []}
            self.data[key]["samples"].append(sample)


FEATURE_NAMES = [
    "bias",
    "T_avg",
    "T_end",
    "T_delta",
    "T_std",
    "Air_avg",
    "Air_delta",
    "Hum_avg",
    "Hum_delta",
    "E/lap",
    "T_avg×E/lap",
    "T_delta×E/lap",
]
_PHI_DIM = len(FEATURE_NAMES)


def _env_value(env: Optional[Dict[str, float]], name: str, legacy_name: Optional[str] = None, default: float = 0.0) -> float:
    if not env:
        return float(default)
    if name in env:
        return float(env.get(name, default))
    if legacy_name and legacy_name in env:
        return float(env.get(legacy_name, default))
    return float(default)


def _phi_from_env_context(env: Optional[Dict[str, float]], energy_per_lap: float) -> np.ndarray:
    t_avg = _env_value(env, "track_temp_avg", "track_temp", 0.0)
    t_end = _env_value(env, "track_temp_end", "track_temp", t_avg)
    t_start = _env_value(env, "track_temp_start", default=t_end)
    t_delta = _env_value(env, "track_temp_delta", default=t_end - t_start)
    t_std = max(0.0, _env_value(env, "track_temp_std", default=0.0))

    air_avg = _env_value(env, "air_temp_avg", "air_temp", 25.0)
    air_end = _env_value(env, "air_temp_end", "air_temp", air_avg)
    air_start = _env_value(env, "air_temp_start", default=air_end)
    air_delta = _env_value(env, "air_temp_delta", default=air_end - air_start)

    hum_avg = _env_value(env, "humidity_avg", "humidity", 50.0)
    hum_end = _env_value(env, "humidity_end", "humidity", hum_avg)
    hum_start = _env_value(env, "humidity_start", default=hum_end)
    hum_delta = _env_value(env, "humidity_delta", default=hum_end - hum_start)

    e_lap = float(energy_per_lap)
    return np.array(
        [
            1.0,
            t_avg,
            t_end,
            t_delta,
            t_std,
            air_avg,
            air_delta,
            hum_avg,
            hum_delta,
            e_lap,
            t_avg * e_lap,
            t_delta * e_lap,
        ],
        dtype=float,
    )


class RLSEstimator:
    """Single-target recursive least squares estimator."""

    def __init__(self, lam: float = 0.98, sigma0: float = 1e4):
        self.lam = float(lam)
        self.theta = np.zeros(_PHI_DIM, dtype=float)
        self.P = float(sigma0) * np.eye(_PHI_DIM, dtype=float)
        self.n_updates = 0
        self.mad_error = 1e-4

    def update(self, x: np.ndarray, y: float) -> float:
        px = self.P @ x
        gain = px / (self.lam + float(x @ px))
        error = float(y) - float(x @ self.theta)
        self.theta += gain * error
        self.P = (self.P - np.outer(gain, px)) / self.lam
        self.n_updates += 1
        self.mad_error = 0.95 * self.mad_error + 0.05 * abs(error)
        return error

    def predict(self, x: np.ndarray) -> float:
        return max(0.0, float(x @ self.theta))

    @property
    def confidence(self) -> float:
        return float(np.tanh(self.n_updates / 13.0))

    @property
    def uncertainty_trace(self) -> float:
        return float(np.trace(self.P))

    def to_dict(self) -> dict:
        return {
            "theta": self.theta.tolist(),
            "P": [r.tolist() for r in self.P],
            "n_updates": int(self.n_updates),
            "lam": float(self.lam),
            "mad_error": float(self.mad_error),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RLSEstimator":
        obj = cls(lam=float(data.get("lam", 0.98)))
        theta = np.array(data.get("theta", np.zeros(_PHI_DIM)), dtype=float)
        P = np.array(data.get("P", np.eye(_PHI_DIM) * 1e4), dtype=float)
        if theta.shape != (_PHI_DIM,) or P.shape != (_PHI_DIM, _PHI_DIM):
            return obj
        obj.theta = theta
        obj.P = P
        obj.n_updates = int(data.get("n_updates", 0))
        obj.mad_error = max(1e-6, float(data.get("mad_error", 1e-4)))
        return obj


class TireMLModel:
    """Persistent online tire wear model powered by RLS per tire."""

    def __init__(self, storage: DataStorage):
        self.storage = storage
        self._rls: Dict[str, RLSEstimator] = {t: RLSEstimator() for t in TIRE_KEYS}

    @staticmethod
    def _rls_key(dataset_key: str) -> str:
        return f"{dataset_key}::rls"

    def _reset_estimators(self):
        self._rls = {t: RLSEstimator() for t in TIRE_KEYS}

    def _sample_env_context(self, sample: dict) -> Dict[str, float]:
        return {
            "track_temp": float(sample.get("track_temp", sample.get("track_temp_avg", 0.0))),
            "air_temp": float(sample.get("air_temp", sample.get("air_temp_avg", 25.0))),
            "humidity": float(sample.get("humidity", sample.get("humidity_avg", 50.0))),
            "track_temp_avg": float(sample.get("track_temp_avg", sample.get("track_temp", 0.0))),
            "track_temp_start": float(sample.get("track_temp_start", sample.get("track_temp_avg", sample.get("track_temp", 0.0)))),
            "track_temp_end": float(sample.get("track_temp_end", sample.get("track_temp_avg", sample.get("track_temp", 0.0)))),
            "track_temp_delta": float(sample.get("track_temp_delta", 0.0)),
            "track_temp_std": float(sample.get("track_temp_std", 0.0)),
            "air_temp_avg": float(sample.get("air_temp_avg", sample.get("air_temp", 25.0))),
            "air_temp_start": float(sample.get("air_temp_start", sample.get("air_temp_avg", sample.get("air_temp", 25.0)))),
            "air_temp_end": float(sample.get("air_temp_end", sample.get("air_temp_avg", sample.get("air_temp", 25.0)))),
            "air_temp_delta": float(sample.get("air_temp_delta", 0.0)),
            "humidity_avg": float(sample.get("humidity_avg", sample.get("humidity", 50.0))),
            "humidity_start": float(sample.get("humidity_start", sample.get("humidity_avg", sample.get("humidity", 50.0)))),
            "humidity_end": float(sample.get("humidity_end", sample.get("humidity_avg", sample.get("humidity", 50.0)))),
            "humidity_delta": float(sample.get("humidity_delta", 0.0)),
        }

    def _phi_from_sample(self, sample: dict) -> np.ndarray:
        return _phi_from_env_context(self._sample_env_context(sample), float(sample.get("energy_per_lap", 0.0)))

    def _rebuild_rls_from_samples(self, dataset_key: str):
        self._reset_estimators()
        samples = self.storage.get_samples(dataset_key)
        for sample in samples:
            x = self._phi_from_sample(sample)
            for tire in TIRE_KEYS:
                if tire in sample:
                    self._rls[tire].update(x, float(sample[tire]))

    def load_rls(self, dataset_key: str):
        with self.storage.lock:
            raw = self.storage.data.get(self._rls_key(dataset_key), {})
        valid_rls = bool(raw)
        restored = {}
        for tire in TIRE_KEYS:
            est = RLSEstimator.from_dict(raw[tire]) if tire in raw else RLSEstimator()
            restored[tire] = est
            valid_rls = valid_rls and est.n_updates > 0

        if valid_rls:
            self._rls = restored
            return

        self._rebuild_rls_from_samples(dataset_key)
        if self.sample_count(dataset_key) > 0:
            self.save_rls(dataset_key)

    def save_rls(self, dataset_key: str):
        with self.storage.lock:
            self.storage.data[self._rls_key(dataset_key)] = {t: self._rls[t].to_dict() for t in TIRE_KEYS}
        self.storage.save()

    def add_stint_sample(self, dataset_key: str, sample: dict):
        self.storage.add_sample(dataset_key, sample)
        x = self._phi_from_sample(sample)
        for tire in TIRE_KEYS:
            self._rls[tire].update(x, float(sample[tire]))
        self.save_rls(dataset_key)

    def is_outlier(self, dataset_key: str, candidate: dict) -> bool:
        x = self._phi_from_sample(candidate)
        for tire in TIRE_KEYS:
            rls = self._rls[tire]
            if rls.n_updates < 4:
                continue
            pred = rls.predict(x)
            pred_var = max(1e-10, float(x @ rls.P @ x) + rls.mad_error * rls.mad_error)
            z = abs(float(candidate[tire]) - pred) / pred_var**0.5
            if z > 3.5:
                return True
        return False

    def get_rates(self, dataset_key: str, env_context: Dict[str, float], energy_per_lap: float) -> Tuple[Dict[str, float], float, int]:
        sample_count = self.sample_count(dataset_key)
        if self._rls["lf"].n_updates == 0:
            return {t: 0.0 for t in TIRE_KEYS}, 0.0, sample_count

        x = _phi_from_env_context(env_context, energy_per_lap)
        samples = self.storage.get_samples(dataset_key)
        rates = {}
        for tire in TIRE_KEYS:
            rls = self._rls[tire]
            conf = rls.confidence
            prior = float(np.median([float(s[tire]) for s in samples if tire in s])) if samples else 0.0
            rates[tire] = max(0.0, conf * rls.predict(x) + (1.0 - conf) * prior)
        return rates, self._rls["lf"].confidence, sample_count

    def sample_count(self, key: str) -> int:
        return len(self.storage.get_samples(key))

    def get_wear_per_lap_baseline(self, key: str, env_context: Dict[str, float]) -> Dict[str, float]:
        samples = self.storage.get_samples(key)
        if not samples:
            return {t: 0.0 for t in TIRE_KEYS}

        med_epl = float(np.median([float(s.get("energy_per_lap", 0.0)) for s in samples]))
        rates, _, _ = self.get_rates(key, env_context, med_epl)
        return {t: rates[t] * med_epl for t in TIRE_KEYS}

    def get_coefficients_report(self, dataset_key: str) -> str:
        lines = [f"=== Coefficients: {dataset_key} ==="]
        for tire in TIRE_KEYS:
            rls = self._rls[tire]
            lines.append(
                f"\n[{tire.upper()}] n={rls.n_updates}  conf={rls.confidence:.1%}  "
                f"P_trace={rls.uncertainty_trace:.2e}"
            )
            for name, coef in zip(FEATURE_NAMES, rls.theta):
                lines.append(f"  {name:20s}: {coef:+.6e}")
        return "\n".join(lines)


class StintTracker:
    """Detects stint boundaries and computes validated learning samples at pit-out.

    Driving/lap load is frozen when the car enters pit road, but tire wear is
    finalized from the latest values seen during the pit stop. Environmental
    context is accumulated over the whole stint so the model can learn start,
    average, end, delta, and variation instead of relying on a single snapshot.
    """

    MIN_LAPS = 2.0
    LAP_STD_THRESHOLD = 15.0
    PIT_WEAR_RESET_THRESHOLD = 5.0

    def __init__(self):
        self.in_stint = False
        self.prev_on_pit: Optional[bool] = None
        self.start_data: Dict[str, object] = {}

        self.current_energy = 0.0
        self.last_snapshot: Optional[TelemetrySnapshot] = None

        self.env_time_accum = 0.0
        self.env_integral = {"track_temp": 0.0, "air_temp": 0.0, "humidity": 0.0}
        self.env_sq_integral = {"track_temp": 0.0, "air_temp": 0.0, "humidity": 0.0}
        self.env_start = {"track_temp": 0.0, "air_temp": 0.0, "humidity": 0.0}

        self.last_lap: Optional[int] = None
        self.last_lap_cross_time: Optional[float] = None
        self.lap_times: List[float] = []
        self.min_speed_kmh = float("inf")

        self.stopped_in_pit = False
        self.pit_tire_change_live = {t: False for t in TIRE_KEYS}
        self.pit_tire_change_request = {t: False for t in TIRE_KEYS}
        self.pit_entry_wear: Optional[Dict[str, float]] = None
        self.pit_final_stint_wear: Optional[Dict[str, float]] = None
        self.pending_stint_end: Optional[Dict[str, object]] = None

    @staticmethod
    def make_dataset_key(s: TelemetrySnapshot) -> str:
        def clean(text: str, fallback: str) -> str:
            return (text or fallback).strip().lower().replace(" ", "_")

        track = clean(s.track_name, "unknown_track")
        config = clean(s.track_config, "default")
        car = clean(s.car_path, "unknown_car")
        return f"{track}+{config}+{car}"

    @staticmethod
    def laps_in_stint(current: TelemetrySnapshot, previous: TelemetrySnapshot) -> float:
        prev_progress = float(previous.lap) + float(previous.lap_dist_pct)
        curr_progress = float(current.lap) + float(current.lap_dist_pct)
        return max(0.0, curr_progress - prev_progress)

    def _reset_pit_state(self):
        self.stopped_in_pit = False
        self.pit_tire_change_live = {t: False for t in TIRE_KEYS}
        self.pit_tire_change_request = {t: False for t in TIRE_KEYS}
        self.pit_entry_wear = None
        self.pit_final_stint_wear = None
        self.pending_stint_end = None

    def _start_stint(self, snapshot: TelemetrySnapshot, speed_kmh: float, initial_wear: Optional[Dict[str, float]] = None):
        start_wear = dict(initial_wear or snapshot.wear)
        self.in_stint = True
        self.start_data = {
            "wear": start_wear,
            "session_time": snapshot.session_time,
            "lap": snapshot.lap,
            "lap_progress": float(snapshot.lap) + float(snapshot.lap_dist_pct),
            "energy": self.current_energy,
            "track_temp": snapshot.track_temp,
            "air_temp": snapshot.air_temp,
            "key": self.make_dataset_key(snapshot),
        }
        self.lap_times = []
        self.min_speed_kmh = speed_kmh
        self.env_time_accum = 0.0
        self.env_integral = {"track_temp": 0.0, "air_temp": 0.0, "humidity": 0.0}
        self.env_sq_integral = {"track_temp": 0.0, "air_temp": 0.0, "humidity": 0.0}
        self.env_start = {
            "track_temp": float(snapshot.track_temp),
            "air_temp": float(snapshot.air_temp),
            "humidity": float(snapshot.humidity),
        }
        self._reset_pit_state()

    def _infer_pit_tire_changes_from_wear(self, current_wear: Dict[str, float]):
        if not self.pit_entry_wear:
            return

        for tire in TIRE_KEYS:
            before = float(self.pit_entry_wear.get(tire, 0.0))
            now = float(current_wear.get(tire, before))
            if (now - before) >= self.PIT_WEAR_RESET_THRESHOLD:
                self.pit_tire_change_request[tire] = True

    def _integrate_stint_environment(self, previous: TelemetrySnapshot, current: TelemetrySnapshot):
        if not self.in_stint:
            return

        dt = max(0.0, float(current.session_time) - float(previous.session_time))
        if dt <= 1e-9:
            return

        for key in ("track_temp", "air_temp", "humidity"):
            prev_v = float(getattr(previous, key))
            curr_v = float(getattr(current, key))
            self.env_integral[key] += 0.5 * (prev_v + curr_v) * dt
            self.env_sq_integral[key] += 0.5 * (prev_v * prev_v + curr_v * curr_v) * dt
        self.env_time_accum += dt

    def _environment_summary(self, end_snapshot: Optional[TelemetrySnapshot] = None) -> Dict[str, float]:
        source = end_snapshot or self.last_snapshot
        if source is None:
            source = TelemetrySnapshot(0.0, 0, 0.0, False, 0.0, 0.0, 0.0, 0.0, 0.0, 25.0, 50.0, 0, {t: 100.0 for t in TIRE_KEYS}, "", "", "")

        end_vals = {
            "track_temp": float(source.track_temp),
            "air_temp": float(source.air_temp),
            "humidity": float(source.humidity),
        }
        start_vals = dict(self.env_start or end_vals)

        if self.env_time_accum > 1e-9:
            avg_vals = {k: float(v) / self.env_time_accum for k, v in self.env_integral.items()}
            mean_sq = {k: float(v) / self.env_time_accum for k, v in self.env_sq_integral.items()}
        else:
            avg_vals = dict(end_vals)
            mean_sq = {k: end_vals[k] * end_vals[k] for k in end_vals}

        std_vals = {k: float(max(0.0, mean_sq[k] - avg_vals[k] * avg_vals[k])) ** 0.5 for k in avg_vals}
        elapsed = max(self.env_time_accum, 1e-9)

        return {
            "track_temp": float(avg_vals["track_temp"]),
            "air_temp": float(avg_vals["air_temp"]),
            "humidity": float(avg_vals["humidity"]),
            "track_temp_avg": float(avg_vals["track_temp"]),
            "track_temp_start": float(start_vals["track_temp"]),
            "track_temp_end": float(end_vals["track_temp"]),
            "track_temp_delta": float(end_vals["track_temp"] - start_vals["track_temp"]),
            "track_temp_std": float(std_vals["track_temp"]),
            "track_temp_slope": float((end_vals["track_temp"] - start_vals["track_temp"]) / elapsed),
            "air_temp_avg": float(avg_vals["air_temp"]),
            "air_temp_start": float(start_vals["air_temp"]),
            "air_temp_end": float(end_vals["air_temp"]),
            "air_temp_delta": float(end_vals["air_temp"] - start_vals["air_temp"]),
            "air_temp_std": float(std_vals["air_temp"]),
            "air_temp_slope": float((end_vals["air_temp"] - start_vals["air_temp"]) / elapsed),
            "humidity_avg": float(avg_vals["humidity"]),
            "humidity_start": float(start_vals["humidity"]),
            "humidity_end": float(end_vals["humidity"]),
            "humidity_delta": float(end_vals["humidity"] - start_vals["humidity"]),
            "humidity_std": float(std_vals["humidity"]),
            "humidity_slope": float((end_vals["humidity"] - start_vals["humidity"]) / elapsed),
            "env_elapsed": float(self.env_time_accum),
        }

    def current_environment_context(self, snapshot: TelemetrySnapshot) -> Dict[str, float]:
        if not self.in_stint:
            return {
                "track_temp": float(snapshot.track_temp),
                "air_temp": float(snapshot.air_temp),
                "humidity": float(snapshot.humidity),
                "track_temp_avg": float(snapshot.track_temp),
                "track_temp_start": float(snapshot.track_temp),
                "track_temp_end": float(snapshot.track_temp),
                "track_temp_delta": 0.0,
                "track_temp_std": 0.0,
                "air_temp_avg": float(snapshot.air_temp),
                "air_temp_start": float(snapshot.air_temp),
                "air_temp_end": float(snapshot.air_temp),
                "air_temp_delta": 0.0,
                "air_temp_std": 0.0,
                "humidity_avg": float(snapshot.humidity),
                "humidity_start": float(snapshot.humidity),
                "humidity_end": float(snapshot.humidity),
                "humidity_delta": 0.0,
                "humidity_std": 0.0,
                "env_elapsed": 0.0,
            }
        return self._environment_summary(snapshot)

    def _capture_pit_service_state(self, snapshot: TelemetrySnapshot, speed_kmh: float):
        if self.pit_entry_wear is None:
            self.pit_entry_wear = dict(snapshot.wear)

        if self.pit_final_stint_wear is None:
            self.pit_final_stint_wear = dict(snapshot.wear)
        for tire in TIRE_KEYS:
            current = float(snapshot.wear[tire])
            self.pit_final_stint_wear[tire] = min(float(self.pit_final_stint_wear.get(tire, current)), current)

        flags = int(snapshot.pit_sv_flags)
        current_flags = {tire: bool(flags & bit) for tire, bit in PIT_TIRE_CHANGE_FLAGS.items()}
        self.pit_tire_change_live = current_flags

        just_stopped = (not self.stopped_in_pit) and speed_kmh < 1.0
        if just_stopped:
            self.stopped_in_pit = True
            self.pit_tire_change_request = dict(current_flags)
        elif self.stopped_in_pit:
            for tire in TIRE_KEYS:
                self.pit_tire_change_request[tire] = self.pit_tire_change_request[tire] or current_flags[tire]

        self._infer_pit_tire_changes_from_wear(snapshot.wear)

    def _build_stint_result(self, end_info: Dict[str, object], final_wear: Dict[str, float]) -> dict:
        start_lap_progress = float(self.start_data.get("lap_progress", float(end_info["lap_progress"])))
        end_lap_progress = float(end_info["lap_progress"])
        laps_progress = max(0.0, end_lap_progress - start_lap_progress)
        completed_laps = max(0, int(np.floor(laps_progress + 1e-6)))

        start_energy = float(self.start_data.get("energy", float(end_info["energy"])))
        end_energy = float(end_info["energy"])
        energy_used = max(1e-9, end_energy - start_energy)

        wear_start = dict(self.start_data.get("wear", final_wear))
        wear_end = dict(final_wear)
        wear_delta = {t: max(0.0, float(wear_start[t]) - float(wear_end[t])) for t in TIRE_KEYS}
        wear_per_lap = {t: wear_delta[t] / laps_progress if laps_progress > 1e-6 else 0.0 for t in TIRE_KEYS}
        wear_per_energy = {t: wear_delta[t] / energy_used for t in TIRE_KEYS}
        energy_per_lap = energy_used / laps_progress if laps_progress > 1e-6 else 0.0

        lap_times = list(end_info.get("lap_times", self.lap_times))
        lap_std = float(np.std(lap_times)) if len(lap_times) >= 2 else 0.0
        env = dict(end_info.get("env", {}))

        result = {
            "key": end_info.get("key", self.start_data.get("key")),
            "laps": completed_laps,
            "laps_progress": laps_progress,
            "lap_std": lap_std,
            "lap_times": lap_times,
            "min_speed_kmh": float(end_info.get("min_speed_kmh", self.min_speed_kmh)),
            "track_temp": float(env.get("track_temp", 0.0)),
            "air_temp": float(env.get("air_temp", 0.0)),
            "humidity": float(env.get("humidity", 50.0)),
            "energy_per_lap": float(energy_per_lap),
            "wear_per_lap": wear_per_lap,
            "wear_per_energy": wear_per_energy,
        }
        result.update(env)
        return result

    def _resolved_post_pit_wear(self, fallback_wear: Dict[str, float]) -> Dict[str, float]:
        post_pit_wear = dict(self.pit_final_stint_wear or fallback_wear)
        for tire in TIRE_KEYS:
            if self.pit_tire_change_request.get(tire, False):
                post_pit_wear[tire] = 100.0
        return post_pit_wear

    def update(self, snapshot: TelemetrySnapshot) -> Optional[dict]:
        speed_kmh = snapshot.speed_mps * 3.6

        if self.last_snapshot is not None:
            dt = max(0.0, snapshot.session_time - self.last_snapshot.session_time)
            self.current_energy += abs(snapshot.lat_accel) * snapshot.speed_mps * dt
            self._integrate_stint_environment(self.last_snapshot, snapshot)

        if self.in_stint:
            self.min_speed_kmh = min(self.min_speed_kmh, speed_kmh)

        if self.last_lap is None:
            self.last_lap = snapshot.lap
            self.last_lap_cross_time = snapshot.session_time
        elif snapshot.lap > self.last_lap:
            if self.last_lap_cross_time is not None:
                lap_time = snapshot.session_time - self.last_lap_cross_time
                if self.in_stint and 20.0 <= lap_time <= 500.0:
                    self.lap_times.append(lap_time)
            self.last_lap_cross_time = snapshot.session_time
            self.last_lap = snapshot.lap

        if self.prev_on_pit is None:
            self.prev_on_pit = snapshot.on_pit_road
            if snapshot.on_pit_road:
                self._capture_pit_service_state(snapshot, speed_kmh)
            else:
                self._start_stint(snapshot, speed_kmh)
            self.last_snapshot = snapshot
            return None

        result = None

        if snapshot.on_pit_road:
            self._capture_pit_service_state(snapshot, speed_kmh)

        if snapshot.on_pit_road and (not bool(self.prev_on_pit)) and self.in_stint:
            self.in_stint = False
            self.pending_stint_end = {
                "key": self.start_data.get("key", self.make_dataset_key(snapshot)),
                "session_time": snapshot.session_time,
                "lap": snapshot.lap,
                "lap_progress": float(snapshot.lap) + float(snapshot.lap_dist_pct),
                "energy": self.current_energy,
                "env": self._environment_summary(snapshot),
                "lap_times": list(self.lap_times),
                "min_speed_kmh": float(self.min_speed_kmh),
            }

        if (not snapshot.on_pit_road) and bool(self.prev_on_pit):
            final_stint_wear = dict(self.pit_final_stint_wear or snapshot.wear)
            post_pit_wear = self._resolved_post_pit_wear(final_stint_wear)
            if self.pending_stint_end is not None and self.start_data:
                result = self._build_stint_result(self.pending_stint_end, final_stint_wear)
            self._start_stint(snapshot, speed_kmh, initial_wear=post_pit_wear)

        self.prev_on_pit = snapshot.on_pit_road
        self.last_snapshot = snapshot
        return result

    def stint_is_valid(self, stint: dict) -> bool:
        laps_progress = float(stint.get("laps_progress", float(stint.get("laps", 0))))
        if laps_progress < self.MIN_LAPS:
            return False

        lap_times = list(stint.get("lap_times", []))
        if len(lap_times) > 2:
            lap_times = lap_times[1:-1]

        if len(lap_times) >= 2 and float(np.std(lap_times)) > self.LAP_STD_THRESHOLD:
            return False
        if float(stint.get("min_speed_kmh", 0.0)) < 20.0:
            return False
        return True

    def current_energy_per_lap(self, snapshot: TelemetrySnapshot) -> float:
        if not self.in_stint or not self.start_data:
            return 0.0

        start_energy = float(self.start_data.get("energy", self.current_energy))
        energy_used = max(0.0, self.current_energy - start_energy)
        start_lap_progress = float(self.start_data.get("lap_progress", float(snapshot.lap) + float(snapshot.lap_dist_pct)))
        current_lap_progress = float(snapshot.lap) + float(snapshot.lap_dist_pct)
        laps_progress = max(0.0, current_lap_progress - start_lap_progress)
        return energy_used / laps_progress if laps_progress > 1e-6 else 0.0

    def build_live_estimate(self, snapshot: TelemetrySnapshot, rates_per_energy: Dict[str, float], baseline_wear_per_lap: Dict[str, float]) -> Optional[dict]:
        if snapshot.on_pit_road or not self.in_stint or not self.start_data:
            return None

        start_energy = float(self.start_data.get("energy", self.current_energy))
        energy_used = max(0.0, self.current_energy - start_energy)
        initial_wear = self.start_data.get("wear", snapshot.wear)
        start_lap_progress = float(self.start_data.get("lap_progress", float(snapshot.lap) + float(snapshot.lap_dist_pct)))
        current_lap_progress = float(snapshot.lap) + float(snapshot.lap_dist_pct)
        laps_progress = max(0.0, current_lap_progress - start_lap_progress)

        estimated_tread = {}
        for tire in TIRE_KEYS:
            predicted_wear_energy = energy_used * float(rates_per_energy.get(tire, 0.0))
            predicted_wear_laps = laps_progress * float(baseline_wear_per_lap.get(tire, 0.0))
            predicted_wear = max(predicted_wear_energy, predicted_wear_laps)
            estimated_tread[tire] = float(np.clip(float(initial_wear[tire]) - predicted_wear, 0.0, 100.0))

        laps_done = max(0, snapshot.lap - int(self.start_data.get("lap", snapshot.lap)))
        return {
            "estimated_tread": estimated_tread,
            "energy_used": energy_used,
            "laps_done": laps_done,
            "laps_progress": laps_progress,
            "environment_context": self.current_environment_context(snapshot),
            "environment_average": self._environment_summary(snapshot),
        }


class TelemetryReader(threading.Thread):
    """60 Hz telemetry reader; reconnects automatically if sim/session restarts."""

    def __init__(
        self,
        out_queue: queue.Queue,
        stop_event: threading.Event,
        ir_client=None,
    ):
        super().__init__(daemon=True)
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.ir = ir_client if ir_client is not None else irsdk.IRSDK()
        self.last_meta = {"TrackName": "", "TrackConfigName": "", "CarPath": ""}

    @staticmethod
    def _safe_float(v, default=0.0) -> float:
        try:
            return float(v) if v is not None else float(default)
        except Exception:
            return float(default)

    @staticmethod
    def _safe_int(v, default=0) -> int:
        try:
            return int(v) if v is not None else int(default)
        except Exception:
            return int(default)

    def _connected(self) -> bool:
        try:
            if not getattr(self.ir, "is_initialized", False):
                self.ir.startup()
            return bool(getattr(self.ir, "is_connected", False))
        except Exception:
            return False

    def _parse_metadata(self) -> Dict[str, str]:
        def prettify(text: str) -> str:
            cleaned = re.sub(r"[_\-]+", " ", str(text or "")).strip()
            return cleaned.title()

        # Primary source (same approach used in the main app): direct iRacing blocks.
        try:
            weekend = self.ir["WeekendInfo"]
            if isinstance(weekend, dict):
                track_name = str(weekend.get("TrackDisplayName") or weekend.get("TrackName") or "").strip()
                track_config = str(weekend.get("TrackConfigName") or "").strip()
                if track_name:
                    self.last_meta["TrackName"] = prettify(track_name)
                if track_config:
                    self.last_meta["TrackConfigName"] = prettify(track_config)
        except Exception:
            pass

        try:
            driver_info = self.ir["DriverInfo"]
            if isinstance(driver_info, dict):
                drivers = driver_info.get("Drivers") or []
                idx = driver_info.get("DriverCarIdx")
                try:
                    idx = int(idx) if idx is not None else None
                except (TypeError, ValueError):
                    idx = None
                if idx is not None and 0 <= idx < len(drivers) and isinstance(drivers[idx], dict):
                    entry = drivers[idx]
                    car_path = str(
                        entry.get("CarScreenName")
                        or entry.get("CarScreenNameShort")
                        or entry.get("CarPath")
                        or ""
                    ).strip()
                    if car_path:
                        self.last_meta["CarPath"] = prettify(car_path)
        except Exception:
            pass

        if any(self.last_meta.values()):
            return self.last_meta

        # Prefer parsed SessionInfo dict and fallback to session YAML string.
        try:
            session_info = self.ir["SessionInfo"]
            if isinstance(session_info, dict):
                weekend = session_info.get("WeekendInfo") or {}
                driver_info = session_info.get("DriverInfo") or {}
                drivers = driver_info.get("Drivers") or []
                # PlayerCarIdx is the stable identifier for the local car.
                player_car_idx = self._safe_int(self.ir["PlayerCarIdx"], -1)

                track_name = str(weekend.get("TrackDisplayName") or weekend.get("TrackName") or "").strip()
                track_config = str(weekend.get("TrackConfigName") or "").strip()
                car_path = ""
                player_car = None
                for entry in drivers:
                    if not isinstance(entry, dict):
                        continue
                    if self._safe_int(entry.get("CarIdx"), -1) == player_car_idx:
                        player_car = entry
                        break

                # Fallback for older/odd payloads where list index may match the car idx.
                if player_car is None and 0 <= player_car_idx < len(drivers):
                    candidate = drivers[player_car_idx]
                    if isinstance(candidate, dict):
                        player_car = candidate

                if isinstance(player_car, dict):
                    car_path = str(
                        player_car.get("CarScreenName")
                        or player_car.get("CarScreenNameShort")
                        or player_car.get("CarPath")
                        or ""
                    ).strip()

                track_name = prettify(track_name)
                track_config = prettify(track_config)
                car_path = prettify(car_path)

                if track_name:
                    self.last_meta["TrackName"] = track_name
                if track_config:
                    self.last_meta["TrackConfigName"] = track_config
                if car_path:
                    self.last_meta["CarPath"] = car_path

                if track_name or track_config or car_path:
                    return self.last_meta
        except Exception:
            pass

        text = ""
        try:
            text = self.ir.session_info
        except Exception:
            pass

        if not text:
            return self.last_meta

        # YAML fallback parsing.
        m_track = re.search(r"^\s*TrackDisplayName:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        if m_track:
            self.last_meta["TrackName"] = m_track.group(1).strip().strip('"')
        else:
            m_track_name = re.search(r"^\s*TrackName:\s*(.+?)\s*$", text, flags=re.MULTILINE)
            if m_track_name:
                self.last_meta["TrackName"] = m_track_name.group(1).strip().strip('"')

        m_cfg = re.search(r"^\s*TrackConfigName:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        if m_cfg:
            self.last_meta["TrackConfigName"] = m_cfg.group(1).strip().strip('"')

        m_car = re.search(r"^\s*CarScreenName:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        if m_car:
            self.last_meta["CarPath"] = m_car.group(1).strip().strip('"')
        else:
            m_car_path = re.search(r"^\s*CarPath:\s*(.+?)\s*$", text, flags=re.MULTILINE)
            if m_car_path:
                self.last_meta["CarPath"] = m_car_path.group(1).strip().strip('"')

        self.last_meta["TrackName"] = prettify(self.last_meta.get("TrackName", ""))
        self.last_meta["TrackConfigName"] = prettify(self.last_meta.get("TrackConfigName", ""))
        self.last_meta["CarPath"] = prettify(self.last_meta.get("CarPath", ""))
        return self.last_meta

    @staticmethod
    def _normalize_wear_value(value: float) -> float:
        # Depending on sdk/version tire wear can be [0..1] fraction or [0..100] percent.
        v = max(0.0, float(value))
        return v * 100.0 if v <= 1.5 else v

    def run(self):
        tick_s = 1.0 / 60.0
        while not self.stop_event.is_set():
            try:
                if not self._connected():
                    # Push disconnected marker snapshot-less event through queue state.
                    self.out_queue.put_nowait((None, False))
                    time.sleep(1.0)
                    continue

                meta = self._parse_metadata()
                wear = {}
                for tire, fields in WEAR_FIELDS.items():
                    inner_index = INNER_WEAR_INDEX[tire]
                    inner_field = fields[inner_index]
                    wear[tire] = self._normalize_wear_value(self._safe_float(self.ir[inner_field], 100.0))

                snap = TelemetrySnapshot(
                    session_time=self._safe_float(self.ir["SessionTime"], 0.0),
                    lap=self._safe_int(self.ir["Lap"], 0),
                    lap_dist_pct=self._safe_float(self.ir["LapDistPct"], 0.0),
                    on_pit_road=bool(self.ir["OnPitRoad"]),
                    speed_mps=self._safe_float(self.ir["Speed"], 0.0),
                    lat_accel=self._safe_float(self.ir["LatAccel"], 0.0),
                    long_accel=self._safe_float(self.ir["LongAccel"], 0.0),
                    steering=self._safe_float(self.ir["SteeringWheelAngle"], 0.0),
                    track_temp=self._safe_float(self.ir["TrackTemp"], 0.0),
                    air_temp=self._safe_float(self.ir["AirTemp"], 0.0),
                    humidity=self._safe_float(self.ir["RelativeHumidity"], 0.0),
                    pit_sv_flags=self._safe_int(self.ir["PitSvFlags"], 0),
                    wear=wear,
                    track_name=meta.get("TrackName", ""),
                    track_config=meta.get("TrackConfigName", ""),
                    car_path=meta.get("CarPath", ""),
                )
                self.out_queue.put_nowait((snap, True))
                time.sleep(tick_s)
            except queue.Full:
                time.sleep(tick_s)
            except Exception:
                # Telemetry can disappear mid-session; keep looping.
                time.sleep(0.2)


class ModelWorker(threading.Thread):
    """Model thread that performs live estimation and incremental learning."""

    def __init__(self, in_queue: queue.Queue, state: dict, state_lock: threading.Lock, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.in_queue = in_queue
        self.state = state
        self.state_lock = state_lock
        self.stop_event = stop_event
        self.storage = DataStorage(MODEL_PATH)
        self.model = TireMLModel(self.storage)
        self._last_key = ""
        self.model_report = ""
        self.stints = StintTracker()
        self.smoothed_wear_per_lap = {t: 0.0 for t in TIRE_KEYS}

    def _update_state(self, **kwargs):
        with self.state_lock:
            self.state.update(kwargs)
            self.state["updated_at"] = time.time()

    def _consume_reset_request(self) -> bool:
        with self.state_lock:
            if not self.state.get("reset_requested", False):
                return False
            self.state["reset_requested"] = False
            return True

    def _reset_runtime_memory(self):
        self.storage.data = {}
        self.storage.save()
        self.model = TireMLModel(self.storage)
        self._last_key = ""
        self.stints = StintTracker()
        self.model_report = ""
        self.smoothed_wear_per_lap = {t: 0.0 for t in TIRE_KEYS}
        self._update_state(
            tread={t: 100.0 for t in TIRE_KEYS},
            wear_per_lap={t: 0.0 for t in TIRE_KEYS},
            key="",
            model_confidence=0.0,
            model_report="",
            sample_count=0,
            estimate_ready=False,
        )

    def run(self):
        while not self.stop_event.is_set():
            try:
                payload = self.in_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            snap, connected = payload
            self._update_state(connected=bool(connected))
            if self._consume_reset_request():
                self._reset_runtime_memory()
            if snap is None:
                continue

            key = StintTracker.make_dataset_key(snap)
            if key != self._last_key:
                self.model.load_rls(key)
                self._last_key = key
                self.model_report = self.model.get_coefficients_report(key)

            stint_end = self.stints.update(snap)
            live_energy_per_lap = self.stints.current_energy_per_lap(snap)
            env_context = self.stints.current_environment_context(snap)

            rates_energy, model_confidence, sample_count = self.model.get_rates(
                key,
                env_context,
                live_energy_per_lap,
            )
            baseline_wear_per_lap = self.model.get_wear_per_lap_baseline(
                key,
                env_context,
            )
            self._update_state(
                key=key,
                track_temp=snap.track_temp,
                air_temp=snap.air_temp,
                humidity=snap.humidity,
                env_track_temp=env_context["track_temp_avg"],
                env_air_temp=env_context["air_temp_avg"],
                env_humidity=env_context["humidity_avg"],
                env_track_temp_start=env_context["track_temp_start"],
                env_track_temp_end=env_context["track_temp_end"],
                env_track_temp_delta=env_context["track_temp_delta"],
                env_track_temp_std=env_context["track_temp_std"],
                env_air_temp_start=env_context["air_temp_start"],
                env_air_temp_end=env_context["air_temp_end"],
                env_air_temp_delta=env_context["air_temp_delta"],
                env_humidity_start=env_context["humidity_start"],
                env_humidity_end=env_context["humidity_end"],
                env_humidity_delta=env_context["humidity_delta"],
                env_humidity_std=env_context["humidity_std"],
                model_confidence=model_confidence,
                model_report=self.model_report,
                sample_count=sample_count,
                track_name=snap.track_name,
                track_config=snap.track_config,
                car_path=snap.car_path,
            )

            has_base_samples = sample_count >= 1
            self._update_state(estimate_ready=has_base_samples)

            live = self.stints.build_live_estimate(snap, rates_energy, baseline_wear_per_lap) if has_base_samples else None
            if live:
                laps_done = max(1e-6, live.get("laps_progress", float(live["laps_done"])))
                energy_per_lap_live = live["energy_used"] / laps_done
                current_wpl = {t: rates_energy[t] * energy_per_lap_live for t in TIRE_KEYS}
                for t in TIRE_KEYS:
                    current_wpl[t] = max(current_wpl[t], baseline_wear_per_lap.get(t, 0.0))

                # Exponential smoothing for stable wear rate estimate.
                for t in TIRE_KEYS:
                    self.smoothed_wear_per_lap[t] = 0.8 * self.smoothed_wear_per_lap[t] + 0.2 * current_wpl[t]

                self._update_state(
                    tread=dict(live["estimated_tread"]),
                    wear_per_lap=dict(self.smoothed_wear_per_lap),
                )
            elif not has_base_samples:
                self._update_state(wear_per_lap={t: 0.0 for t in TIRE_KEYS})

            if not stint_end:
                continue

            if not self.stints.stint_is_valid(stint_end):
                continue

            sample = {
                "track_temp": float(stint_end["track_temp"]),
                "air_temp": float(stint_end["air_temp"]),
                "humidity": float(stint_end.get("humidity", 50.0)),
                "track_temp_avg": float(stint_end.get("track_temp_avg", stint_end["track_temp"])),
                "track_temp_start": float(stint_end.get("track_temp_start", stint_end["track_temp"])),
                "track_temp_end": float(stint_end.get("track_temp_end", stint_end["track_temp"])),
                "track_temp_delta": float(stint_end.get("track_temp_delta", 0.0)),
                "track_temp_std": float(stint_end.get("track_temp_std", 0.0)),
                "air_temp_avg": float(stint_end.get("air_temp_avg", stint_end["air_temp"])),
                "air_temp_start": float(stint_end.get("air_temp_start", stint_end["air_temp"])),
                "air_temp_end": float(stint_end.get("air_temp_end", stint_end["air_temp"])),
                "air_temp_delta": float(stint_end.get("air_temp_delta", 0.0)),
                "humidity_avg": float(stint_end.get("humidity_avg", stint_end.get("humidity", 50.0))),
                "humidity_start": float(stint_end.get("humidity_start", stint_end.get("humidity", 50.0))),
                "humidity_end": float(stint_end.get("humidity_end", stint_end.get("humidity", 50.0))),
                "humidity_delta": float(stint_end.get("humidity_delta", 0.0)),
                "laps": int(stint_end["laps"]),
                "energy_per_lap": float(stint_end["energy_per_lap"]),
                "lf": float(stint_end["wear_per_energy"]["lf"]),
                "rf": float(stint_end["wear_per_energy"]["rf"]),
                "lr": float(stint_end["wear_per_energy"]["lr"]),
                "rr": float(stint_end["wear_per_energy"]["rr"]),
            }

            if self.model.is_outlier(str(stint_end["key"]), sample):
                continue

            self.model.add_stint_sample(str(stint_end["key"]), sample)
            self.model_report = self.model.get_coefficients_report(key)
            self._update_state(
                sample_count=self.model.sample_count(key),
                model_confidence=self.model._rls["lf"].confidence,
                model_report=self.model_report,
                estimate_ready=self.model.sample_count(key) >= 1,
            )
