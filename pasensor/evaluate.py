"""Event-level evaluation of the full pipeline on labelled recordings.

Each session is replayed through the same code that runs live, as fast as the
computer allows. The report gives false alert events per hour, event recall,
detection latency, bearing error, unavailable fraction, lost samples, and
processing load. Results on synthetic sessions check the software only.

Manifest (JSON):
    {"schema": "pasensor.evaluation-manifest.v1", "guard_s": 1.0,
     "sessions": [{"wav": "a.wav", "session_id": "s1",
                   "truth": [{"start_s": 10, "end_s": 40, "label": "drone",
                              "bearing_local_deg": 120}]}]}
    A session with an empty truth list is background only. bearing_local_deg
    may be replaced by "bearing_track": [[seconds, degrees], ...].
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import numpy as np

from .acquisition import ReplaySource
from .geometry import wrap180
from .ml import wilson
from .pipeline import Pipeline
from .state import SharedState

MANIFEST_SCHEMA = "pasensor.evaluation-manifest.v1"


def _chi2_quantile(p: float, dof: float) -> float:
    """Wilson-Hilferty approximation to the chi-square quantile."""
    z = {0.025: -1.959964, 0.975: 1.959964}[p]
    return dof * (1.0 - 2.0 / (9.0 * dof) + z * math.sqrt(2.0 / (9.0 * dof))) ** 3


def poisson_rate_ci(count: int, exposure_hours: float) -> tuple | None:
    """Approximate 95 percent interval for an event rate per hour."""
    if exposure_hours <= 0:
        return None
    lower = 0.0 if count == 0 else 0.5 * _chi2_quantile(0.025, 2.0 * count) / exposure_hours
    upper = (-math.log(0.025) if count == 0 else 0.5 * _chi2_quantile(0.975, 2.0 * count + 2.0)) / exposure_hours
    return (max(lower, 0.0), upper)


def _truth_bearing(interval: dict, t: float):
    track = interval.get("bearing_track")
    if track:
        times = [p[0] for p in track]
        if t < times[0] or t > times[-1]:
            return None
        i = int(np.searchsorted(times, t))
        if i == 0:
            return track[0][1]
        (t0, a0), (t1, a1) = track[i - 1], track[i]
        fraction = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        return (a0 + fraction * float(wrap180(a1 - a0))) % 360.0
    return interval.get("bearing_local_deg")


def run_session(wav: Path, cfg, classifier=None) -> dict:
    """Replay one recording through the pipeline and return frames, events, and counters."""
    holder = {}

    def factory():
        holder["source"] = ReplaySource(wav, cfg["device"], cfg["processing"]["hop_frames"], realtime=False)
        return holder["source"]

    state = SharedState(cfg["health"]["stale_after_s"], history_frames=10_000_000)
    pipeline = Pipeline(cfg, "REPLAY", factory, state, classifier=classifier)
    started = time.perf_counter()
    final = pipeline.run(threading.Event())
    elapsed = time.perf_counter() - started
    source = holder["source"]
    origin = source._start_time
    frames = [{**row, "t": row["t"] - origin} for row in state.history()]
    events = []
    for event in reversed(state.events()):
        events.append({**event, "onset_s": event["onset_time_s"] - origin,
                       "offset_s": None if event.get("offset_time_s") is None else event["offset_time_s"] - origin})
    return {"final_state": final, "frames": frames, "events": events, "duration_s": source.total_frames / source.fs,
            "gaps": pipeline.stats["gap_count"], "overflows": pipeline.stats["overflow_count"],
            "frames_analysed": pipeline.stats["frames_analysed"], "wall_s": elapsed,
            "dsp_ms_max": pipeline._dsp_ms_max, "synthetic": bool((source.sidecar or {}).get("synthetic", False)),
            "has_sidecar": source.sidecar is not None}


def evaluate(manifest_path, cfg, classifier=None) -> dict:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"evaluation manifest must have schema {MANIFEST_SCHEMA}")
    guard = float(manifest.get("guard_s", 1.0))
    totals = {"background_s": 0.0, "false_alerts": 0, "drone_intervals": 0, "detected": 0, "latency": [],
              "bearing_errors": [], "drone_frames": 0, "drone_frames_with_bearing": 0, "audio_s": 0.0, "wall_s": 0.0,
              "gaps": 0, "overflows": 0, "dsp_ms_max": 0.0}
    sessions, synthetic, failed = [], False, []
    for item in manifest["sessions"]:
        wav = Path(item["wav"])
        if not wav.is_absolute():
            wav = manifest_path.parent / wav
        run = run_session(wav, cfg, classifier)
        if run["final_state"] != "REPLAY_COMPLETE":
            failed.append(item.get("session_id", wav.name))
        synthetic = synthetic or run["synthetic"] or bool(item.get("synthetic"))
        intervals = [t for t in item.get("truth", []) if t.get("label") == "drone"]
        covered = sum(min(t["end_s"] + guard, run["duration_s"]) - max(t["start_s"] - guard, 0.0) for t in intervals)
        in_drone = lambda t: any(i["start_s"] - guard <= t <= i["end_s"] + guard for i in intervals)  # noqa: E731
        false_alerts = [e for e in run["events"] if not in_drone(e["onset_s"])]
        detected = 0
        for interval in intervals:
            hits = [e for e in run["events"] if e["onset_s"] <= interval["end_s"] + guard
                    and (e["offset_s"] is None or e["offset_s"] >= interval["start_s"] - guard)]
            if hits:
                detected += 1
                totals["latency"].append(max(min(e["onset_s"] for e in hits) - interval["start_s"], 0.0))
            for frame in run["frames"]:
                if interval["start_s"] <= frame["t"] <= interval["end_s"]:
                    totals["drone_frames"] += 1
                    if frame["brg"] is not None:
                        totals["drone_frames_with_bearing"] += 1
                        truth = _truth_bearing(interval, frame["t"])
                        if truth is not None:
                            totals["bearing_errors"].append(abs(float(wrap180(frame["brg"] - truth))))
        totals["background_s"] += max(run["duration_s"] - covered, 0.0)
        totals["false_alerts"] += len(false_alerts)
        totals["drone_intervals"] += len(intervals)
        totals["detected"] += detected
        for key in ("gaps", "overflows"):
            totals[key] += run[key]
        totals["audio_s"] += run["duration_s"]
        totals["wall_s"] += run["wall_s"]
        totals["dsp_ms_max"] = max(totals["dsp_ms_max"], run["dsp_ms_max"])
        sessions.append({"session_id": item.get("session_id", wav.name), "file": wav.name, "duration_s": run["duration_s"],
                         "candidates": len(run["events"]), "false_alerts": len(false_alerts),
                         "drone_intervals": len(intervals), "detected": detected, "gaps": run["gaps"],
                         "final_state": run["final_state"]})
    hours = totals["background_s"] / 3600.0
    errors = np.array(totals["bearing_errors"])
    latency = np.array(totals["latency"])
    return {
        "schema": "pasensor.evaluation-report.v1", "node_id": cfg.node_id, "config_sha256": cfg.sha256,
        "calibrated": cfg.calibrated, "synthetic_data": synthetic,
        "evidence_statement": ("SYNTHETIC DATA. This run checks the software only. It is not performance evidence."
                               if synthetic else "Recorded data. Results apply to these sessions, sites, and conditions only."),
        "sessions": sessions, "failed_sessions": failed,
        "false_alert_events": totals["false_alerts"], "background_exposure_hours": hours,
        "false_alerts_per_hour": totals["false_alerts"] / hours if hours > 0 else None,
        "false_alerts_per_hour_ci95": poisson_rate_ci(totals["false_alerts"], hours),
        "drone_events": totals["drone_intervals"], "drone_events_detected": totals["detected"],
        "event_recall": totals["detected"] / totals["drone_intervals"] if totals["drone_intervals"] else None,
        "event_recall_ci95": wilson(totals["detected"], totals["drone_intervals"]),
        "detection_latency_s": {"median": float(np.median(latency)), "p90": float(np.percentile(latency, 90)),
                                "definition": "first candidate onset minus labelled interval start"} if len(latency) else None,
        "bearing_error_deg": {"frames": int(len(errors)), "median": float(np.median(errors)),
                              "p95": float(np.percentile(errors, 95))} if len(errors) else None,
        "bearing_unavailable_fraction": (1.0 - totals["drone_frames_with_bearing"] / totals["drone_frames"]
                                         if totals["drone_frames"] else None),
        "lost_sample_events": {"gaps": totals["gaps"], "overflows": totals["overflows"]},
        "processing": {"audio_seconds": totals["audio_s"], "wall_seconds": totals["wall_s"],
                       "load_fraction": totals["wall_s"] / totals["audio_s"] if totals["audio_s"] else None,
                       "dsp_ms_max": totals["dsp_ms_max"],
                       "note": "Whole-pipeline wall time divided by audio time on this computer, including file reading."},
    }


def report_markdown(report: dict) -> str:
    def num(value, digits=2, unit=""):
        return "n/a" if value is None else f"{value:.{digits}f}{unit}"

    def interval(value, digits=2):
        return "n/a" if value is None else f"{value[0]:.{digits}f} to {value[1]:.{digits}f}"

    latency, bearing = report["detection_latency_s"], report["bearing_error_deg"]
    lines = [
        "# Evaluation report", "", f"**{report['evidence_statement']}**", "",
        f"Node `{report['node_id']}`, configuration `{report['config_sha256'][:12]}`, "
        f"bearing calibration {'accepted' if report['calibrated'] else 'not done'}.", "",
        "| Metric | Value | 95% interval |", "|---|---|---|",
        f"| False alert events per hour | {num(report['false_alerts_per_hour'])} ({report['false_alert_events']} events in "
        f"{num(report['background_exposure_hours'], 3)} h) | {interval(report['false_alerts_per_hour_ci95'])} |",
        f"| Event recall | {num(report['event_recall'])} ({report['drone_events_detected']} of {report['drone_events']}) | "
        f"{interval(report['event_recall_ci95'])} |",
        f"| Detection latency, median | {num(latency['median'] if latency else None, 2, ' s')} | |",
        f"| Detection latency, 90th percentile | {num(latency['p90'] if latency else None, 2, ' s')} | |",
        f"| Bearing error, median | {num(bearing['median'] if bearing else None, 1, ' deg')} | |",
        f"| Bearing error, 95th percentile | {num(bearing['p95'] if bearing else None, 1, ' deg')} | |",
        f"| Bearing unavailable fraction | {num(report['bearing_unavailable_fraction'])} | |",
        f"| Lost-sample events | {report['lost_sample_events']['gaps']} gaps, {report['lost_sample_events']['overflows']} overflows | |",
        f"| Processing load | {num(report['processing']['load_fraction'], 3)} of real time, "
        f"{num(report['processing']['dsp_ms_max'], 1, ' ms')} worst block | |", "",
        "| Session | Duration | Candidates | False alerts | Drone events | Detected | Gaps |", "|---|---|---|---|---|---|---|",
    ]
    for s in report["sessions"]:
        lines.append(f"| {s['session_id']} | {s['duration_s']:.0f} s | {s['candidates']} | {s['false_alerts']} | "
                     f"{s['drone_intervals']} | {s['detected']} | {s['gaps']} |")
    if report["failed_sessions"]:
        lines += ["", "Sessions that did not replay to the end: " + ", ".join(report["failed_sessions"])]
    return "\n".join(lines) + "\n"
