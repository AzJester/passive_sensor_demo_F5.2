"""Dataset manifest, leakage-safe splitting, training, and window-level evaluation.

The rule that matters most: every window cut from one original recording
session goes to the same partition. Windows from one session are near
copies of each other. Splitting them across train and test makes a model look
far better than it is. This module assigns whole groups to partitions and
refuses to continue if any group, or any identical audio file, ends up on
both sides.

Manifest (CSV, one row per labelled segment):
    path,label,session_id,site,day,source,start_s,end_s,synthetic
    label is "drone" or "background". start_s and end_s are optional.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .classifier import FEATURE_SPEC, MODEL_SCHEMA, WINDOW_DIM, frame_features, sigmoid, window_features
from .dsp import Analyzer
from .recorder import sha256_file

LABELS = {"background": 0, "drone": 1}


class DatasetError(ValueError):
    pass


def read_wav(path) -> tuple[np.ndarray, int]:
    """Return (frames x channels float array, sample rate) for integer PCM WAV."""
    with wave.open(str(path), "rb") as w:
        if w.getcomptype() != "NONE" or w.getsampwidth() not in (2, 3, 4):
            raise DatasetError(f"{path}: only uncompressed 16, 24, or 32 bit integer PCM WAV is supported")
        width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2") / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4") / 2147483648.0
    else:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        data = np.where(value >= 1 << 23, value - (1 << 24), value) / 8388608.0
    return data.reshape(-1, channels), rate


def resample(x: np.ndarray, rate: int, target: int) -> np.ndarray:
    """Band-limited resampling by spectrum truncation or padding. Offline use only."""
    if rate == target:
        return x
    n = len(x)
    m = int(round(n * target / rate))
    spectrum = np.fft.rfft(x, axis=0)
    bins = m // 2 + 1
    out = np.zeros((bins, x.shape[1]), dtype=complex)
    keep = min(bins, len(spectrum))
    out[:keep] = spectrum[:keep]
    return np.fft.irfft(out, n=m, axis=0) * (m / n)


def load_manifest(path) -> list[dict]:
    path = Path(path)
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            label = (row.get("label") or "").strip().lower()
            if label not in LABELS:
                raise DatasetError(f"{path.name} line {number}: label must be 'drone' or 'background'")
            session = (row.get("session_id") or "").strip()
            if not session:
                raise DatasetError(f"{path.name} line {number}: session_id is required to keep sessions together")
            file = Path((row.get("path") or "").strip())
            if not file.is_absolute():
                file = path.parent / file
            if not file.exists():
                raise DatasetError(f"{path.name} line {number}: file not found: {file}")
            number_or_none = lambda key: float(row[key]) if (row.get(key) or "").strip() else None  # noqa: E731
            rows.append({"path": file, "label": label, "session_id": session, "site": (row.get("site") or "").strip(),
                         "day": (row.get("day") or "").strip(), "source": (row.get("source") or "").strip(),
                         "start_s": number_or_none("start_s"), "end_s": number_or_none("end_s"),
                         "synthetic": (row.get("synthetic") or "").strip().lower() in ("1", "true", "yes")})
    if not rows:
        raise DatasetError("the manifest has no rows")
    return rows


def group_key(row: dict, group_by: tuple) -> str:
    return "|".join(row[k] or "?" for k in group_by)


def grouped_split(groups: list[str], fractions=(0.6, 0.2, 0.2), seed: str = "pasensor-split-v1") -> dict:
    """Assign each distinct group to train, validation, or test by a stable hash.

    A group's partition depends only on its name and the seed, so adding new
    sessions later never moves an existing session across partitions.
    """
    if abs(sum(fractions) - 1.0) > 1e-9 or min(fractions) <= 0:
        raise DatasetError("split fractions must be positive and sum to 1")
    assignment = {}
    for group in sorted(set(groups)):
        digest = hashlib.sha256(f"{seed}:{group}".encode()).digest()
        u = int.from_bytes(digest[:8], "big") / 2 ** 64
        assignment[group] = "train" if u < fractions[0] else ("validation" if u < fractions[0] + fractions[1] else "test")
    return assignment


def check_no_leakage(rows: list[dict], assignment: dict, group_by: tuple) -> None:
    """Raise if one group or one identical file reaches more than one partition."""
    seen_file: dict = {}
    for row in rows:
        part = assignment[group_key(row, group_by)]
        digest = row.setdefault("sha256", sha256_file(row["path"]))
        if seen_file.setdefault(digest, part) != part:
            raise DatasetError(f"identical audio ({row['path'].name}) appears in two partitions under different groups. "
                               "Give copies of one recording the same session_id.")


def extract_windows(row: dict, raw_indices=None, capture_channels: int | None = None) -> np.ndarray:
    """Window features for one manifest row, computed exactly as the live pipeline does."""
    spec = FEATURE_SPEC
    data, rate = read_wav(row["path"])
    if capture_channels is not None and data.shape[1] == capture_channels and raw_indices:
        data = data[:, raw_indices]
    start = int((row["start_s"] or 0.0) * rate)
    end = int(row["end_s"] * rate) if row["end_s"] is not None else len(data)
    data = resample(data[start:end], rate, spec["sample_rate"])
    block, hop = spec["block_frames"], spec["hop_frames"]
    analyzer = _analyzer()
    frames = []
    for offset in range(0, len(data) - block + 1, hop):
        segment = data[offset:offset + block]
        if float(np.sqrt(np.mean(segment ** 2))) < 1e-7:
            continue
        harmonic, _spectrum, (power, df) = analyzer.harmonic_features(segment)
        frames.append(np.concatenate((frame_features(power, df, harmonic), [harmonic["score"], harmonic["count"]])))
    size = spec["window_frames"]
    windows = [np.concatenate((window_features(np.array(frames[i:i + size])[:, :-2]),
                               [np.mean([f[-2] for f in frames[i:i + size]]), np.min([f[-1] for f in frames[i:i + size]])]))
               for i in range(0, len(frames) - size + 1, size)]
    return np.array(windows) if windows else np.empty((0, WINDOW_DIM + 2))


_cached_analyzer = None


def _analyzer() -> Analyzer:
    global _cached_analyzer
    if _cached_analyzer is None:
        _cached_analyzer = Analyzer(fs=FEATURE_SPEC["sample_rate"])
    return _cached_analyzer


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0, iterations: int = 60):
    """L2-regularized logistic regression with balanced class weights, by Newton steps."""
    n, d = x.shape
    positives = max(float(y.sum()), 1.0)
    negatives = max(float(n - y.sum()), 1.0)
    weight = np.where(y == 1, n / (2.0 * positives), n / (2.0 * negatives))
    xb = np.hstack((x, np.ones((n, 1))))
    beta = np.zeros(d + 1)
    penalty = np.eye(d + 1) * l2
    penalty[-1, -1] = 0.0
    for _ in range(iterations):
        p = sigmoid(xb @ beta)
        gradient = xb.T @ (weight * (p - y)) + penalty @ beta
        hessian = (xb * (weight * p * (1.0 - p))[:, None]).T @ xb + penalty + 1e-8 * np.eye(d + 1)
        step = np.linalg.solve(hessian, gradient)
        beta -= step
        if float(np.max(np.abs(step))) < 1e-7:
            break
    return beta[:-1], float(beta[-1])


def roc_auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    positives, negatives = scores[labels == 1], scores[labels == 0]
    if len(positives) == 0 or len(negatives) == 0:
        return None
    order = np.argsort(np.concatenate((negatives, positives)), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    combined = np.concatenate((negatives, positives))
    for value in np.unique(combined):                     # average ranks over ties
        tie = combined == value
        ranks[tie] = ranks[tie].mean()
    return float((ranks[len(negatives):].sum() - len(positives) * (len(positives) + 1) / 2.0) / (len(positives) * len(negatives)))


def wilson(successes: int, trials: int) -> tuple | None:
    """Wilson 95 percent interval for a proportion."""
    if trials == 0:
        return None
    z = 1.959964
    p = successes / trials
    centre = (p + z * z / (2 * trials)) / (1 + z * z / trials)
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / (1 + z * z / trials)
    return (max(centre - half, 0.0), min(centre + half, 1.0))


def _rates(scores, labels, threshold) -> dict:
    predicted = scores >= threshold
    tp, fn = int(np.sum(predicted & (labels == 1))), int(np.sum(~predicted & (labels == 1)))
    fp, tn = int(np.sum(predicted & (labels == 0))), int(np.sum(~predicted & (labels == 0)))
    return {"windows": int(len(labels)), "true_positive": tp, "false_negative": fn, "false_positive": fp, "true_negative": tn,
            "window_recall": tp / (tp + fn) if tp + fn else None, "window_recall_ci95": wilson(tp, tp + fn),
            "window_false_positive_rate": fp / (fp + tn) if fp + tn else None,
            "window_false_positive_rate_ci95": wilson(fp, fp + tn)}


def train(manifest_path, out_dir, group_by=("session_id",), fractions=(0.6, 0.2, 0.2), target_fpr: float = 0.01,
          l2: float = 1.0, provenance: str = "", raw_indices=None, capture_channels: int | None = None,
          model_id: str | None = None) -> dict:
    """Train, pick a threshold on validation groups, and evaluate once on test groups."""
    rows = load_manifest(manifest_path)
    assignment = grouped_split([group_key(r, group_by) for r in rows], fractions)
    check_no_leakage(rows, assignment, group_by)
    parts = {name: {"x": [], "y": [], "h": [], "groups": set()} for name in ("train", "validation", "test")}
    for row in rows:
        windows = extract_windows(row, raw_indices, capture_channels)
        part = parts[assignment[group_key(row, group_by)]]
        part["groups"].add((group_key(row, group_by), row["label"]))
        if len(windows):
            part["x"].append(windows[:, :WINDOW_DIM])
            part["h"].append(windows[:, WINDOW_DIM:])
            part["y"].append(np.full(len(windows), LABELS[row["label"]]))
    data = {}
    for name, part in parts.items():
        if not part["x"]:
            raise DatasetError(f"the {name} partition has no usable windows. Add more sessions; do not move windows by hand.")
        data[name] = (np.vstack(part["x"]), np.concatenate(part["y"]), np.vstack(part["h"]))
        if len(np.unique(data[name][1])) < 2:
            raise DatasetError(f"the {name} partition lacks one of the two classes. Add more sessions of the missing class.")
    x_train, y_train, _ = data["train"]
    mean, scale = x_train.mean(axis=0), np.maximum(x_train.std(axis=0), 1e-6)
    weights, bias = fit_logistic((x_train - mean) / scale, y_train, l2)
    score = lambda x: sigmoid(((x - mean) / scale) @ weights + bias)  # noqa: E731
    x_val, y_val, _ = data["validation"]
    val_scores = score(x_val)
    negatives = np.sort(val_scores[y_val == 0])
    index = min(int(math.ceil((1.0 - target_fpr) * len(negatives))), len(negatives) - 1)
    threshold = float(min(max(negatives[index] + 1e-9, 0.0), 1.0))
    x_test, y_test, h_test = data["test"]
    test_scores = score(x_test)
    heuristic = ((h_test[:, 0] >= 0.25) & (h_test[:, 1] >= 4)).astype(float)
    group_counts = {name: {"drone": sum(1 for g in part["groups"] if g[1] == "drone"),
                           "background": sum(1 for g in part["groups"] if g[1] == "background")}
                    for name, part in parts.items()}
    warnings = []
    if min(group_counts["test"].values()) < 5:
        warnings.append("Fewer than five test groups in a class. The test metrics describe this small sample only.")
    synthetic = all(r["synthetic"] for r in rows)
    if any(r["synthetic"] for r in rows) and not synthetic:
        warnings.append("The manifest mixes synthetic and real rows. Report them separately.")
    metrics = {
        "validation": {"auc": roc_auc(val_scores, y_val), **_rates(val_scores, y_val, threshold)},
        "test": {"auc": roc_auc(test_scores, y_test), **_rates(test_scores, y_test, threshold)},
        "test_harmonic_heuristic_baseline": _rates(heuristic, y_test, 0.5),
        "note": "Window-level rates. They are not event recall or false alerts per hour; use 'pasensor evaluate' for those.",
    }
    manifest_hash = sha256_file(Path(manifest_path))
    model = {
        "schema": MODEL_SCHEMA, "kind": "logistic_regression",
        "model_id": model_id or f"logmel-lr-{manifest_hash[:10]}",
        "feature_spec": FEATURE_SPEC, "mean": mean.tolist(), "scale": scale.tolist(), "weights": weights.tolist(),
        "bias": bias, "decision_threshold": threshold, "calibration": None,
        "training": {
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "software_version": __version__,
            "manifest_sha256": manifest_hash, "group_by": list(group_by), "split_fractions": list(fractions),
            "split_assignment": assignment, "groups_per_partition": group_counts,
            "windows_per_partition": {k: int(len(v[1])) for k, v in data.items()},
            "target_validation_false_positive_rate": target_fpr, "l2": l2, "metrics": metrics, "warnings": warnings,
            "synthetic_only": synthetic,
            "data_provenance": provenance or "NOT STATED. Record where every recording came from and its licence.",
        },
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.json").write_text(json.dumps(model, indent=1), encoding="utf-8")
    (out / "MODEL_CARD.md").write_text(model_card(model), encoding="utf-8")
    return model


def model_card(model: dict) -> str:
    t = model["training"]
    m = t["metrics"]

    def line(name, r):
        ci = lambda v: "n/a" if v is None else f"{v[0]:.3f} to {v[1]:.3f}"  # noqa: E731
        num = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
        return (f"| {name} | {r['windows']} | {num(r.get('auc'))} | {num(r['window_recall'])} | {ci(r['window_recall_ci95'])} | "
                f"{num(r['window_false_positive_rate'])} | {ci(r['window_false_positive_rate_ci95'])} |")

    lines = [
        f"# Model card: {model['model_id']}", "",
        f"Created {t['created_utc']} with software {t['software_version']}.",
        f"Kind: logistic regression on feature set `{model['feature_spec']['name']}`.",
        f"Data provenance: {t['data_provenance']}",
        f"Synthetic data only: {'YES. This model must not label real audio.' if t['synthetic_only'] else 'no'}",
        f"Manifest SHA-256: `{t['manifest_sha256']}`. Groups are split by: {', '.join(t['group_by'])}.", "",
        "| Partition | Groups (drone / background) | Windows |", "|---|---|---|",
    ]
    for name in ("train", "validation", "test"):
        g = t["groups_per_partition"][name]
        lines.append(f"| {name} | {g['drone']} / {g['background']} | {t['windows_per_partition'][name]} |")
    lines += ["", f"Decision threshold {model['decision_threshold']:.4f}, chosen on validation groups for a target window "
              f"false-positive rate of {t['target_validation_false_positive_rate']}.", "",
              "| Evaluation | Windows | AUC | Recall | Recall 95% CI | False-positive rate | FPR 95% CI |", "|---|---|---|---|---|---|---|",
              line("Validation (threshold was tuned here)", m["validation"]), line("Test (held-out groups)", m["test"]),
              line("Test, harmonic heuristic baseline", m["test_harmonic_heuristic_baseline"]), "",
              m["note"], ""]
    lines += [f"Warning: {w}" for w in t["warnings"]]
    lines += ["", "The score is an uncalibrated model output, not a probability, until a calibration step is added "
              "and checked on held-out sessions."]
    return "\n".join(lines) + "\n"


def make_synthetic_dataset(out_dir, sessions: int = 12, seconds: float = 12.0, seed: int = 7) -> Path:
    """Write a small SYNTHETIC two-class dataset to exercise the pipeline. Not evidence of anything."""
    from .simulate import NoiseSignal, PointSource, RotorSignal, Scene, static_position
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rows = []
    fs = FEATURE_SPEC["sample_rate"]
    for index in range(sessions):
        label = "drone" if index % 2 == 0 else "background"
        azimuth, distance = float(rng.uniform(0, 360)), float(rng.uniform(20, 60))
        if label == "drone":
            signal = RotorSignal(f0_hz=float(rng.uniform(110, 260)), fs=fs, seed=seed + index)
        else:
            signal = NoiseSignal(low_hz=float(rng.uniform(100, 400)), high_hz=float(rng.uniform(1500, 5000)), fs=fs, seed=seed + index)
        source = PointSource(signal, static_position(azimuth, 15.0, distance), level_at_1m=0.01 * distance)
        scene = Scene(fs=fs, sources=[source], sensor_noise_rms=0.002, diffuse_noise_rms=0.002, seed=seed + index)
        audio = scene.render(int(seconds * fs))
        name = f"synthetic_{index:03d}_{label}.wav"
        with wave.open(str(out / name), "wb") as w:
            w.setnchannels(audio.shape[1])
            w.setsampwidth(2)
            w.setframerate(fs)
            w.writeframes(np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2").tobytes())
        rows.append({"path": name, "label": label, "session_id": f"synthetic-session-{index:03d}", "site": "synthetic",
                     "day": "synthetic", "source": "pasensor.simulate", "start_s": "", "end_s": "", "synthetic": "true"})
    manifest = out / "manifest.csv"
    with open(manifest, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return manifest
