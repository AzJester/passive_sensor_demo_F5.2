"""Channel identification and bearing calibration from recordings.

These tools turn physical test recordings into a calibration record. The
node configuration only counts as calibrated when it references a record
that was made from physical recordings and that passed.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .detector import BearingTracker
from .dsp import AUDIO_FAULTS, Analyzer
from .geometry import circular_mean_deg, circular_std_deg, wrap180
from .ml import read_wav
from .recorder import sha256_file

RECORD_SCHEMA = "pasensor.calibration-record.v1"
# Proposed acceptance targets for a bench prototype. They are not achieved results.
CRITERIA = {"median_abs_error_deg": 15.0, "max_position_error_deg": 45.0, "max_unavailable_fraction": 0.5,
            "max_pair_tdoa_std_us": 30.0, "min_positions": 4, "max_azimuth_gap_deg": 200.0}


def tap_analysis(segment: np.ndarray) -> dict:
    """Which channel is loudest during a tap or scratch next to one microphone port.

    A first difference emphasizes the high frequencies of a close tap, where
    the nearest microphone stands out most.
    """
    energy = np.mean(np.diff(np.asarray(segment, dtype=float), axis=0) ** 2, axis=0)
    level = 10.0 * np.log10(np.maximum(energy, 1e-20))
    order = np.argsort(level)[::-1]
    margin = float(level[order[0]] - level[order[1]])
    return {"loudest_channel": int(order[0]), "margin_db": margin, "level_db": [float(v) for v in level],
            "confident": margin >= 3.0}


def channel_map(samples: np.ndarray, fs: float, segments: list, labels: list, cfg) -> dict:
    """Map physical microphone labels to capture channels from tap segments (start_s, end_s)."""
    if len(segments) != len(labels) or len(labels) != len(cfg["device"]["raw_indices"]):
        raise ValueError("give one tap segment and one label per microphone, in geometry order")
    taps, problems = [], []
    for (start, end), label in zip(segments, labels):
        result = tap_analysis(samples[int(start * fs):int(end * fs)])
        result["label"] = label
        taps.append(result)
        if not result["confident"]:
            problems.append(f"{label}: the loudest channel leads by only {result['margin_db']:.1f} dB. Repeat the tap closer to the port.")
    suggested = [t["loudest_channel"] for t in taps]
    if len(set(suggested)) != len(suggested):
        problems.append("two microphones mapped to the same channel. Repeat the test.")
    configured = list(cfg["device"]["raw_indices"])
    for channel in suggested:
        if channel not in configured:
            problems.append(f"channel {channel} was loudest but is not a configured raw channel. Check the firmware routing.")
    return {"taps": taps, "suggested_raw_indices": suggested, "configured_raw_indices": configured,
            "matches_configuration": suggested == configured, "problems": problems,
            "note": "raw_indices must list channels in the same order as array.geometry_m."}


def channel_report(samples: np.ndarray, fs: float, reference: int) -> dict:
    """Level of every capture channel and its delay relative to one raw channel.

    Raw microphones on one board differ by a fraction of a millisecond.
    A processed output usually lags by several milliseconds and has a
    different level, which exposes a wrong channel selection.
    """
    x = np.asarray(samples, dtype=float)
    x = x - x.mean(axis=0)
    n = len(x)
    nfft = 1 << int(math.ceil(math.log2(2 * n)))
    spectrum = np.fft.rfft(x, n=nfft, axis=0)
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    band = (freqs >= 200.0) & (freqs <= min(6000.0, 0.45 * fs))
    limit = int(0.02 * fs)
    channels = []
    for ch in range(x.shape[1]):
        cross = spectrum[:, ch] * np.conj(spectrum[:, reference])
        cross = np.where(band & (np.abs(cross) > 0), cross / np.maximum(np.abs(cross), 1e-30), 0.0)
        gcc = np.fft.irfft(cross, n=nfft)
        window = np.concatenate((gcc[-limit:], gcc[:limit + 1]))
        lag = int(np.argmax(window)) - limit
        rms = float(np.sqrt(np.mean(x[:, ch] ** 2)))
        channels.append({"channel": ch, "rms_dbfs": 20.0 * math.log10(max(rms, 1e-8)),
                         "peak_dbfs": 20.0 * math.log10(max(float(np.max(np.abs(samples[:, ch]))), 1e-8)),
                         "delay_ms_vs_reference": 1000.0 * lag / fs,
                         "correlation_peak": float(window.max() / max(band.sum() / nfft * 2, 1e-12))})
    return {"reference_channel": reference, "channels": channels}


def analyse_position(wav_path, cfg) -> dict:
    """Frame bearings and pair delays for one stationary-source recording. Calibration is NOT applied."""
    samples, rate = read_wav(wav_path)
    device = cfg["device"]
    if rate != cfg.sample_rate or samples.shape[1] != device["capture_channels"]:
        raise ValueError(f"{Path(wav_path).name}: expected {device['capture_channels']} channels at {cfg.sample_rate} Hz")
    raw = samples[:, device["raw_indices"]]
    analyzer = Analyzer(cfg.sample_rate, cfg["array"]["geometry_m"], cfg["array"]["temperature_c"], cfg.dsp_settings())
    tracker = BearingTracker(cfg["tracking"])
    block, hop = cfg["processing"]["block_frames"], cfg["processing"]["hop_frames"]
    bearings, tdoa, frames, faults, polarity = [], [], 0, 0, 0
    for offset in range(0, len(raw) - block + 1, hop):
        result = analyzer.analyze(raw[offset:offset + block])
        frames += 1
        if result["audio"]["status"] in AUDIO_FAULTS:
            faults += 1
            tracker.update(None)
            continue
        b = result["bearing"]
        polarity = max(polarity, b["polarity_suspect_pairs"])
        if tracker.update(b["azimuth_deg"])["stable"]:
            bearings.append(b["azimuth_deg"])
            tdoa.append(b["pair_tdoa_us"])
    sidecar = Path(wav_path).with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    mean, _ = circular_mean_deg(bearings)
    tdoa_array = np.array(tdoa) if tdoa else np.empty((0, len(analyzer.pairs)))
    return {"file": Path(wav_path).name, "sha256": sha256_file(Path(wav_path)), "frames": frames,
            "frames_with_bearing": len(bearings), "audio_fault_frames": faults,
            "unavailable_fraction": 1.0 - len(bearings) / frames if frames else 1.0,
            "raw_mean_deg": mean, "raw_spread_deg": circular_std_deg(bearings),
            "pair_tdoa_median_us": [float(v) for v in np.median(tdoa_array, axis=0)] if len(tdoa_array) else [],
            "pair_tdoa_std_us": [float(v) for v in np.std(tdoa_array, axis=0)] if len(tdoa_array) else [],
            "polarity_suspect_pairs": polarity, "gaps": len(meta.get("gaps", [])),
            "synthetic": bool(meta.get("synthetic", False)), "has_sidecar": bool(meta), "_bearings": bearings}


def fit_calibration(positions: list) -> dict:
    """Find the mirror flag and angle offset that best map raw bearings onto the surveyed truth."""
    usable = [p for p in positions if p["raw_mean_deg"] is not None]
    best = None
    for mirror in (False, True):
        sign = -1.0 if mirror else 1.0
        offset, _ = circular_mean_deg([p["true_azimuth_deg"] - sign * p["raw_mean_deg"] for p in usable])
        if offset is None:
            continue
        residual = [float(wrap180(p["true_azimuth_deg"] - (sign * p["raw_mean_deg"] + offset))) for p in usable]
        rms = math.sqrt(sum(r * r for r in residual) / len(residual))
        if best is None or rms < best["rms"]:
            best = {"mirror": mirror, "offset": offset, "rms": rms, "sign": sign}
    return best


def run_calibration(manifest_path, cfg, operator: str = "", criteria: dict | None = None) -> dict:
    """Analyse surveyed stationary-source recordings and build a calibration record."""
    criteria = {**CRITERIA, **(criteria or {})}
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    positions = []
    for item in manifest["positions"]:
        wav = Path(item["wav"])
        if not wav.is_absolute():
            wav = manifest_path.parent / wav
        result = analyse_position(wav, cfg)
        result.update(true_azimuth_deg=float(item["true_azimuth_deg"]) % 360.0,
                      distance_m=item.get("distance_m"), elevation_deg=item.get("elevation_deg"))
        positions.append(result)
    problems = []
    usable = [p for p in positions if p["raw_mean_deg"] is not None]
    truths = sorted(p["true_azimuth_deg"] for p in usable)
    largest_gap = max(((b - a) % 360.0 for a, b in zip(truths, truths[1:] + truths[:1])), default=360.0) if len(truths) > 1 else 360.0
    if len(usable) < criteria["min_positions"]:
        problems.append(f"only {len(usable)} positions gave a bearing; {criteria['min_positions']} are needed")
    if largest_gap > criteria["max_azimuth_gap_deg"]:
        problems.append(f"source positions leave a {largest_gap:.0f} degree gap; spread them around the array")
    fit = fit_calibration(positions) if len(usable) >= 2 else None
    result = {"passed": False, "mirror": False, "local_angle_offset_deg": None, "bearing_sigma_deg": None,
              "median_abs_error_deg": None, "p95_abs_error_deg": None, "unavailable_fraction": None,
              "criteria": criteria, "problems": problems}
    if fit is not None:
        errors = []
        for p in usable:
            p["mean_error_deg"] = float(wrap180(fit["sign"] * p["raw_mean_deg"] + fit["offset"] - p["true_azimuth_deg"]))
            errors.extend(abs(float(wrap180(fit["sign"] * b + fit["offset"] - p["true_azimuth_deg"]))) for b in p["_bearings"])
            if abs(p["mean_error_deg"]) > criteria["max_position_error_deg"]:
                problems.append(f"{p['file']}: mean error {p['mean_error_deg']:.0f} degrees is outside the allowed sector")
        errors = np.array(errors)
        total_frames = sum(p["frames"] for p in positions)
        unavailable = 1.0 - sum(p["frames_with_bearing"] for p in positions) / total_frames if total_frames else 1.0
        result.update(mirror=fit["mirror"], local_angle_offset_deg=float(fit["offset"] % 360.0),
                      median_abs_error_deg=float(np.median(errors)), p95_abs_error_deg=float(np.percentile(errors, 95)),
                      bearing_sigma_deg=float(max(math.sqrt(float(np.mean(errors ** 2))), 1.0)),
                      unavailable_fraction=float(unavailable))
        if result["median_abs_error_deg"] > criteria["median_abs_error_deg"]:
            problems.append(f"median error {result['median_abs_error_deg']:.1f} degrees exceeds the target")
        if unavailable > criteria["max_unavailable_fraction"]:
            problems.append(f"bearing was unavailable in {100 * unavailable:.0f} percent of frames")
    for p in positions:
        if p["pair_tdoa_std_us"] and max(p["pair_tdoa_std_us"]) > criteria["max_pair_tdoa_std_us"]:
            problems.append(f"{p['file']}: pair delays vary by up to {max(p['pair_tdoa_std_us']):.0f} microseconds; timing is not repeatable")
        if p["gaps"]:
            problems.append(f"{p['file']}: the recording has {p['gaps']} discontinuities")
        if p["polarity_suspect_pairs"] >= 3:
            problems.append(f"{p['file']}: several pairs correlate negatively; check for an inverted microphone channel")
        p.pop("_bearings")
    synthetic = any(p["synthetic"] for p in positions)
    physical = all(p["has_sidecar"] for p in positions) and not synthetic
    if not all(p["has_sidecar"] for p in positions):
        problems.append("a recording has no metadata sidecar; record with 'python -m pasensor record'")
    result["passed"] = not problems
    return {
        "schema": RECORD_SCHEMA, "evidence_kind": "physical" if physical else ("synthetic" if synthetic else "unverified"),
        "node_id": cfg.node_id, "raw_indices": cfg["device"]["raw_indices"], "geometry_m": cfg["array"]["geometry_m"],
        "config_sha256_at_test": cfg.sha256, "firmware_record": cfg["device"]["firmware_record"],
        "temperature_c": cfg["array"]["temperature_c"], "operator": operator or manifest.get("operator", ""),
        "site_notes": manifest.get("notes", ""), "truth_method": manifest.get("truth_method", ""),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "software_version": __version__,
        "positions": positions, "result": result,
        "statement": "Targets are proposed bench criteria. A pass covers the tested positions, distance, mount, and windscreen only.",
    }
