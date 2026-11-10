"""Node configuration: defaults, strict validation, and the calibration gate.

Unknown keys are errors, so a typing mistake cannot silently fall back to a
default. A unit only counts as calibrated when its configuration points at a
calibration record whose contents match the configuration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from . import CONFIG_SCHEMA
from .dsp import DspSettings
from .geometry import ASSUMED_GEOMETRY_M, validate_geometry

DEFAULTS = {
    "schema": CONFIG_SCHEMA,
    "node_id": "ACOUSTIC-01",
    "device": {
        "name_contains": "XVF3800",
        "hostapi_contains": "WASAPI",
        "sample_rate": 16000,
        "capture_channels": 6,
        "raw_indices": [2, 3, 4, 5],
        "firmware_record": "",
    },
    "array": {
        "geometry_m": [list(p) for p in ASSUMED_GEOMETRY_M],
        "geometry_source": "OEM documentation value for AEC_MIC_ARRAY_GEO. Not read back from this unit.",
        "temperature_c": 20.0,
    },
    "calibration": {
        "geometry_validated": False,
        "record": "",
        "local_angle_offset_deg": 0.0,
        "mirror": False,
        "bearing_sigma_deg": None,
        "show_uncalibrated_bearing": False,
    },
    "processing": {"block_frames": 4096, "hop_frames": 2048, "dsp": {}},
    "detection": {
        "background_seconds": 10.0,
        "floor_window_seconds": 300.0,
        "floor_percentile": 10.0,
        "minimum_db_above_floor": 6.0,
        "harmonic_score_threshold": 0.25,
        "min_harmonics": 4,
        "window_frames": 6,
        "onset_frames": 4,
        "release_frames": 1,
    },
    "tracking": {"window_frames": 5, "min_frames": 3, "max_spread_deg": 15.0},
    "classifier": {"model_path": ""},
    "health": {
        "stale_after_s": 2.5,
        "stall_timeout_s": 2.0,
        "restart_backoff_s": [1.0, 2.0, 5.0, 10.0, 15.0],
        "queue_blocks": 64,
    },
    "recording": {"rotate_minutes": 10.0, "min_free_gb": 2.0},
    "site": {
        "east_m": None, "north_m": None, "heading_deg": None, "face_up": True,
        "position_sigma_m": None, "heading_sigma_deg": None,
    },
}


class ConfigError(ValueError):
    """The configuration file is missing, malformed, or inconsistent."""


def _merge(defaults: dict, given: dict, where: str) -> dict:
    out = copy.deepcopy(defaults)
    for key, value in given.items():
        if key not in defaults:
            raise ConfigError(f"unknown setting '{where}{key}'. Check the spelling against config/node.example.json.")
        if isinstance(defaults[key], dict) and key != "dsp":
            if not isinstance(value, dict):
                raise ConfigError(f"'{where}{key}' must be an object")
            out[key] = _merge(defaults[key], value, f"{where}{key}.")
        else:
            out[key] = copy.deepcopy(value)
    return out


def _number(value, name, low=None, high=None, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ConfigError(f"'{name}' must be a finite number")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ConfigError(f"'{name}' must be between {low} and {high}")
    return float(value)


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"'{name}' must be an integer between {low} and {high}")
    return value


def _upgrade_v1(raw: dict) -> dict:
    """Convert a Revision A flat configuration to the current layout."""
    out = {"node_id": raw.get("node_id", "ACOUSTIC-01")}
    out["device"] = {k: raw[k] for k in ("sample_rate", "capture_channels", "raw_indices") if k in raw}
    out["array"] = {"geometry_m": raw.get("geometry_m", DEFAULTS["array"]["geometry_m"]),
                    "temperature_c": raw.get("temperature_c", 20.0)}
    out["calibration"] = {"geometry_validated": raw.get("geometry_validated", False),
                          "record": raw.get("calibration_record", ""),
                          "local_angle_offset_deg": raw.get("local_angle_offset_deg", 0.0)}
    detection = {}
    if "background_seconds" in raw:
        detection["background_seconds"] = raw["background_seconds"]
    if "minimum_db_above_background" in raw:
        detection["minimum_db_above_floor"] = raw["minimum_db_above_background"]
    out["detection"] = detection
    return out


@dataclass
class NodeConfig:
    data: dict
    path: Path | None
    sha256: str
    calibration_record: dict | None
    upgraded_from_v1: bool = False

    def __getitem__(self, key):
        return self.data[key]

    @property
    def node_id(self) -> str:
        return self.data["node_id"]

    @property
    def sample_rate(self) -> int:
        return self.data["device"]["sample_rate"]

    @property
    def calibrated(self) -> bool:
        return bool(self.data["calibration"]["geometry_validated"])

    @property
    def hop_seconds(self) -> float:
        return self.data["processing"]["hop_frames"] / self.sample_rate

    def dsp_settings(self) -> DspSettings:
        return DspSettings.from_mapping(self.data["processing"]["dsp"])


def validate(raw: dict, base_dir: Path | None = None, path: Path | None = None) -> NodeConfig:
    if not isinstance(raw, dict):
        raise ConfigError("the configuration must be a JSON object")
    upgraded = False
    if "schema" not in raw and ("raw_indices" in raw or "capture_channels" in raw):
        raw, upgraded = _upgrade_v1(raw), True
    elif raw.get("schema", CONFIG_SCHEMA) != CONFIG_SCHEMA:
        raise ConfigError(f"unsupported configuration schema {raw.get('schema')!r}; expected {CONFIG_SCHEMA}")
    cfg = _merge(DEFAULTS, raw, "")
    if not isinstance(cfg["node_id"], str) or not cfg["node_id"].strip() or len(cfg["node_id"]) > 64:
        raise ConfigError("'node_id' must be a non-empty string of at most 64 characters")
    device = cfg["device"]
    _integer(device["sample_rate"], "device.sample_rate", 8000, 96000)
    channels = _integer(device["capture_channels"], "device.capture_channels", 3, 32)
    indices = device["raw_indices"]
    if (not isinstance(indices, list) or len(set(map(str, indices))) != len(indices)
            or any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < channels for i in indices)):
        raise ConfigError("'device.raw_indices' must list distinct zero-based channel numbers inside capture_channels")
    for key in ("name_contains", "hostapi_contains", "firmware_record"):
        if not isinstance(device[key], str):
            raise ConfigError(f"'device.{key}' must be a string")
    try:
        geometry = validate_geometry(cfg["array"]["geometry_m"])
    except (ValueError, TypeError) as error:
        raise ConfigError(f"'array.geometry_m': {error}") from error
    if len(geometry) != len(indices):
        raise ConfigError("'array.geometry_m' needs one position per entry in 'device.raw_indices'")
    _number(cfg["array"]["temperature_c"], "array.temperature_c", -40, 60)
    processing = cfg["processing"]
    block = _integer(processing["block_frames"], "processing.block_frames", 1024, 65536)
    hop = _integer(processing["hop_frames"], "processing.hop_frames", 256, 65536)
    if block % hop or hop > block:
        raise ConfigError("'processing.block_frames' must be a whole multiple of 'processing.hop_frames'")
    if not isinstance(processing["dsp"], dict):
        raise ConfigError("'processing.dsp' must be an object")
    try:
        settings = DspSettings.from_mapping(processing["dsp"])
    except (ValueError, TypeError) as error:
        raise ConfigError(f"'processing.dsp': {error}") from error
    if block < 2 * settings.subframe:
        raise ConfigError("'processing.block_frames' must be at least twice 'processing.dsp.subframe'")
    detection = cfg["detection"]
    _number(detection["background_seconds"], "detection.background_seconds", 1, 600)
    _number(detection["floor_window_seconds"], "detection.floor_window_seconds", 10, 3600)
    if detection["floor_window_seconds"] < detection["background_seconds"]:
        raise ConfigError("'detection.floor_window_seconds' must not be shorter than 'detection.background_seconds'")
    _number(detection["floor_percentile"], "detection.floor_percentile", 1, 50)
    _number(detection["minimum_db_above_floor"], "detection.minimum_db_above_floor", 0, 60)
    _number(detection["harmonic_score_threshold"], "detection.harmonic_score_threshold", 0, 1)
    _integer(detection["min_harmonics"], "detection.min_harmonics", 2, 16)
    window = _integer(detection["window_frames"], "detection.window_frames", 1, 100)
    onset = _integer(detection["onset_frames"], "detection.onset_frames", 1, window)
    _integer(detection["release_frames"], "detection.release_frames", 0, onset - 1 if onset > 1 else 0)
    tracking = cfg["tracking"]
    track_window = _integer(tracking["window_frames"], "tracking.window_frames", 1, 50)
    _integer(tracking["min_frames"], "tracking.min_frames", 1, track_window)
    _number(tracking["max_spread_deg"], "tracking.max_spread_deg", 1, 90)
    health = cfg["health"]
    _number(health["stale_after_s"], "health.stale_after_s", 0.5, 60)
    _number(health["stall_timeout_s"], "health.stall_timeout_s", 0.5, 60)
    _integer(health["queue_blocks"], "health.queue_blocks", 4, 4096)
    backoff = health["restart_backoff_s"]
    if not isinstance(backoff, list) or not backoff:
        raise ConfigError("'health.restart_backoff_s' must be a non-empty list of seconds")
    for i, value in enumerate(backoff):
        _number(value, f"health.restart_backoff_s[{i}]", 0.0, 600)
    _number(cfg["recording"]["rotate_minutes"], "recording.rotate_minutes", 0.1, 120)
    _number(cfg["recording"]["min_free_gb"], "recording.min_free_gb", 0, 10000)
    if not isinstance(cfg["classifier"]["model_path"], str):
        raise ConfigError("'classifier.model_path' must be a string")
    site = cfg["site"]
    for key in ("east_m", "north_m", "heading_deg"):
        _number(site[key], f"site.{key}", allow_none=True)
    _number(site["position_sigma_m"], "site.position_sigma_m", 0, 1000, allow_none=True)
    _number(site["heading_sigma_deg"], "site.heading_sigma_deg", 0, 180, allow_none=True)
    if not isinstance(site["face_up"], bool):
        raise ConfigError("'site.face_up' must be true or false")
    calibration = cfg["calibration"]
    for key in ("geometry_validated", "mirror", "show_uncalibrated_bearing"):
        if not isinstance(calibration[key], bool):
            raise ConfigError(f"'calibration.{key}' must be true or false")
    _number(calibration["local_angle_offset_deg"], "calibration.local_angle_offset_deg", -360, 360)
    _number(calibration["bearing_sigma_deg"], "calibration.bearing_sigma_deg", 0.1, 90, allow_none=True)
    if not isinstance(calibration["record"], str):
        raise ConfigError("'calibration.record' must be a string")
    record = _check_calibration(cfg, base_dir) if calibration["geometry_validated"] else None
    digest = hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return NodeConfig(cfg, path, digest, record, upgraded)


def _check_calibration(cfg: dict, base_dir: Path | None) -> dict:
    """Refuse to mark a unit calibrated unless the evidence file matches the configuration."""
    calibration = cfg["calibration"]
    name = calibration["record"].strip()
    if not name:
        raise ConfigError("calibration.geometry_validated is true but calibration.record is empty. "
                          "Run 'python -m pasensor calibrate' and reference the record it writes.")
    record_path = Path(name) if Path(name).is_absolute() or base_dir is None else base_dir / name
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read calibration record {record_path}: {error}") from error
    problems = []
    if record.get("schema") != "pasensor.calibration-record.v1":
        problems.append("it is not a pasensor calibration record")
    if record.get("evidence_kind") != "physical":
        problems.append("the record was not made from physical recordings (synthetic data cannot calibrate hardware)")
    if record.get("node_id") != cfg["node_id"]:
        problems.append("node_id differs")
    if record.get("raw_indices") != cfg["device"]["raw_indices"]:
        problems.append("raw_indices differ")
    try:
        same_geometry = all(abs(a - b) < 1e-6 for row_a, row_b in zip(record["geometry_m"], cfg["array"]["geometry_m"], strict=True)
                            for a, b in zip(row_a, row_b, strict=True))
    except (KeyError, TypeError, ValueError):
        same_geometry = False
    if not same_geometry:
        problems.append("geometry differs")
    result = record.get("result", {})
    if result.get("passed") is not True:
        problems.append("the record does not show a passed calibration")
    if bool(result.get("mirror")) != calibration["mirror"]:
        problems.append("mirror flag differs")
    offset = result.get("local_angle_offset_deg")
    if not isinstance(offset, (int, float)) or abs(((offset - calibration["local_angle_offset_deg"] + 180) % 360) - 180) > 0.05:
        problems.append("local_angle_offset_deg differs")
    if calibration["bearing_sigma_deg"] is None:
        problems.append("calibration.bearing_sigma_deg is not set")
    if problems:
        raise ConfigError(f"calibration record {record_path} does not support this configuration: " + "; ".join(problems))
    return record


def load_config(path) -> NodeConfig:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise ConfigError(f"cannot read configuration {path}: {error}") from error
    except ValueError as error:
        raise ConfigError(f"configuration {path} is not valid JSON: {error}") from error
    return validate(raw, base_dir=path.resolve().parent, path=path)


def default_config() -> NodeConfig:
    return validate({})
