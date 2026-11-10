"""Feature extraction and inference for an optional trained sound classifier.

No trained model ships with this package. Until one is trained on real,
legitimately obtained recordings and evaluated on held-out sessions, the
sensor reports "identity not assessed".

Models are plain JSON (weights and normalization constants). The loader never
unpickles anything, so a model file cannot run code.
"""
from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path

import numpy as np

MODEL_SCHEMA = "pasensor.model.v1"
FEATURE_SPEC = {
    "name": "logmel-stats-v1",
    "sample_rate": 16000,
    "block_frames": 4096,
    "hop_frames": 2048,
    "n_mels": 40,
    "fmin_hz": 60.0,
    "fmax_hz": 7600.0,
    "window_frames": 8,
    "level_normalized": True,
    "harmonic_features": ["score", "count_over_16", "f0_over_500", "power_fraction", "mean_prominence_over_30"],
}
FRAME_DIM = FEATURE_SPEC["n_mels"] + len(FEATURE_SPEC["harmonic_features"])
WINDOW_DIM = 2 * FRAME_DIM

_filter_cache: dict = {}


def mel_filterbank(n_bins: int, df: float, n_mels: int, fmin: float, fmax: float) -> np.ndarray:
    key = (n_bins, round(df, 6), n_mels, fmin, fmax)
    bank = _filter_cache.get(key)
    if bank is None:
        mel = lambda f: 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)  # noqa: E731
        inverse = lambda m: 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)  # noqa: E731
        edges = inverse(np.linspace(mel(fmin), mel(fmax), n_mels + 2))
        freqs = np.arange(n_bins) * df
        bank = np.zeros((n_mels, n_bins))
        for i in range(n_mels):
            left, centre, right = edges[i], edges[i + 1], edges[i + 2]
            bank[i] = np.clip(np.minimum((freqs - left) / max(centre - left, 1e-9),
                                         (right - freqs) / max(right - centre, 1e-9)), 0.0, None)
            total = bank[i].sum()
            if total > 0:
                bank[i] /= total
        _filter_cache[key] = bank
    return bank


def frame_features(power: np.ndarray, df: float, harmonic: dict) -> np.ndarray:
    """One feature vector per analysis block.

    The log-mel part has its mean removed, so the features describe spectral
    shape and not loudness. Loudness depends on range and gain and would let a
    model cheat on a dataset where drones happen to be recorded louder.
    """
    spec = FEATURE_SPEC
    bank = mel_filterbank(len(power), df, spec["n_mels"], spec["fmin_hz"], spec["fmax_hz"])
    logmel = 10.0 * np.log10(np.maximum(bank @ power, 1e-30))
    logmel = logmel - logmel.mean()
    extra = np.array([harmonic["score"], harmonic["count"] / 16.0, (harmonic["f0_hz"] or 0.0) / 500.0,
                      harmonic["power_fraction"], harmonic["mean_prominence_db"] / 30.0])
    return np.concatenate((logmel, extra))


def window_features(frames: np.ndarray) -> np.ndarray:
    """Mean and standard deviation of frame features over one window."""
    frames = np.asarray(frames, dtype=float)
    return np.concatenate((frames.mean(axis=0), frames.std(axis=0)))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))


class WindowClassifier:
    """Logistic model on window features, applied to a sliding window of frames."""

    def __init__(self, model: dict):
        self.model = model
        self.mean = np.array(model["mean"], dtype=float)
        self.scale = np.array(model["scale"], dtype=float)
        self.weights = np.array(model["weights"], dtype=float)
        self.bias = float(model["bias"])
        self.threshold = float(model["decision_threshold"])
        self.calibration = model.get("calibration")
        self.frames: deque = deque(maxlen=int(model["feature_spec"]["window_frames"]))

    def score(self, features: np.ndarray) -> float:
        z = float(((features - self.mean) / self.scale) @ self.weights + self.bias)
        if self.calibration:
            z = self.calibration["a"] * z + self.calibration["b"]
        return float(sigmoid(z))

    def update(self, psd, frame: dict) -> dict:
        if psd is None or frame["audio"]["status"] != "OK":
            self.frames.clear()
            return {"available": False, "model_id": self.model["model_id"],
                    "reason": "The current audio block is not usable, so the model was not run."}
        power, df = psd
        self.frames.append(frame_features(power, df, frame["harmonic"]))
        if len(self.frames) < self.frames.maxlen:
            return {"available": False, "model_id": self.model["model_id"],
                    "reason": "Collecting a full analysis window for the model."}
        value = self.score(window_features(np.array(self.frames)))
        return {"available": True, "model_id": self.model["model_id"], "score": value,
                "score_kind": ("calibrated probability on the model's evaluation data" if self.calibration
                               else "uncalibrated model score"),
                "above_threshold": value >= self.threshold, "decision_threshold": self.threshold}


def validate_model(model: dict) -> None:
    if not isinstance(model, dict) or model.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"not a {MODEL_SCHEMA} model file")
    if model.get("kind") != "logistic_regression":
        raise ValueError("unsupported model kind")
    if model.get("feature_spec") != FEATURE_SPEC:
        raise ValueError("the model was trained with a different feature definition than this software computes")
    for key in ("mean", "scale", "weights"):
        values = model.get(key)
        if not isinstance(values, list) or len(values) != WINDOW_DIM or not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError(f"model field '{key}' must hold {WINDOW_DIM} finite numbers")
    if any(v <= 0 for v in model["scale"]):
        raise ValueError("model scale values must be positive")
    for key in ("bias", "decision_threshold"):
        if not isinstance(model.get(key), (int, float)) or not math.isfinite(model[key]):
            raise ValueError(f"model field '{key}' must be a finite number")
    if not isinstance(model.get("model_id"), str) or not model["model_id"]:
        raise ValueError("model_id is missing")
    if not isinstance(model.get("training"), dict):
        raise ValueError("the model carries no training record")


def load_classifier(path, cfg, mode: str = "LIVE") -> WindowClassifier:
    model = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_model(model)
    if model["training"].get("synthetic_only", True) and mode != "SIMULATED":
        raise ValueError("this model was trained only on synthetic data. It must not label real audio.")
    spec = model["feature_spec"]
    if (cfg.sample_rate != spec["sample_rate"] or cfg["processing"]["block_frames"] != spec["block_frames"]
            or cfg["processing"]["hop_frames"] != spec["hop_frames"]):
        raise ValueError("the node's sample rate or block settings differ from the model's feature definition")
    return WindowClassifier(model)
