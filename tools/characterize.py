"""Characterize the estimator and the candidate gate on SYNTHETIC scenes.

Run:  python tools/characterize.py > docs/results/characterization.md

Everything here comes from the simulator in pasensor/simulate.py. The numbers
show how the software behaves on idealized signals. They are not detection
range, not field accuracy, and not false-alert rates for any real site.
"""
from __future__ import annotations

import importlib.util
import platform
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pasensor import __version__  # noqa: E402
from pasensor.acquisition import SimulatedSource, SourceEnded  # noqa: E402
from pasensor.config import validate  # noqa: E402
from pasensor.dsp import Analyzer  # noqa: E402
from pasensor.geometry import wrap180  # noqa: E402
from pasensor.pipeline import Pipeline  # noqa: E402
from pasensor.simulate import PointSource, RotorSignal, Scene, static_position  # noqa: E402
from pasensor.state import SharedState  # noqa: E402


def load_rev_a():
    spec = importlib.util.spec_from_file_location("dsp_rev_a", ROOT / "legacy" / "dsp_rev_a.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Analyzer()


def noise_args(kind: str, rms: float) -> dict:
    return {"sensor_noise_rms": rms} if kind == "uncorrelated" else {"diffuse_noise_rms": rms, "sensor_noise_rms": 0.05 * rms}


def frame_table(trials: int) -> list[str]:
    rev_a, rev_b = load_rev_a(), Analyzer()
    lines = ["| Noise field | SNR dB | Rev B shown | Rev B median error | Rev B 95th pct | Rev B wrong by over 10 deg | "
             "Rev A shown | Rev A median error | Rev A 95th pct | Rev A wrong by over 10 deg |", "|---|---|---|---|---|---|---|---|---|---|"]
    for kind, levels in (("uncorrelated", (20, 10, 5, 0, -5, -10)), ("diffuse", (10, 5, 0, -5))):
        for snr in levels:
            rng = np.random.default_rng(1000 + snr)
            errors = {"a": [], "b": []}
            for trial in range(trials):
                azimuth, elevation, rms = float(rng.uniform(0, 360)), float(rng.uniform(5, 45)), 0.02
                source = PointSource(RotorSignal(f0_hz=float(rng.uniform(110, 260)), seed=trial + 1),
                                     static_position(azimuth, elevation, 40.0), level_at_1m=rms * 40.0)
                x = Scene(sources=[source], seed=trial + 7, **noise_args(kind, rms / 10 ** (snr / 20))).render(4096)
                b = rev_b.analyze(x)["bearing"]["azimuth_deg"]
                a = rev_a.analyze(x)["bearing_deg"]
                errors["b"].append(np.nan if b is None else abs(float(wrap180(b - azimuth))))
                errors["a"].append(np.nan if a is None else abs(float(wrap180(a - azimuth))))
            cells = []
            for key in ("b", "a"):
                e = np.array(errors[key])
                ok = ~np.isnan(e)
                if ok.any():
                    cells += [f"{100 * ok.mean():.0f}%", f"{np.median(e[ok]):.1f}", f"{np.percentile(e[ok], 95):.1f}",
                              f"{100 * np.mean(e[ok] > 10):.0f}%"]
                else:
                    cells += ["0%", "n/a", "n/a", "n/a"]
            lines.append(f"| {kind} | {snr} | " + " | ".join(cells) + " |")
    return lines


def run_pipeline(scene, chunks: int, **sections):
    cfg = validate({"detection": {"background_seconds": 4.0}, **sections})
    count = {"n": 0}

    def factory():
        source = SimulatedSource(cfg["device"], cfg["array"]["geometry_m"], cfg["array"]["temperature_c"],
                                 cfg["processing"]["hop_frames"], realtime=False, start_time=0.0, scene=scene)
        inner = source.read

        def read():
            if count["n"] >= chunks:
                raise SourceEnded()
            count["n"] += 1
            return inner()
        source.read = read
        return source

    state = SharedState(history_frames=10 ** 6)
    pipeline = Pipeline(cfg, "SIMULATED", factory, state)
    started = time.perf_counter()
    pipeline.run(threading.Event())
    return state, pipeline, time.perf_counter() - started


def quiet_table() -> list[str]:
    lines = ["| Ambient scene (no source) | Audio | Frames | Frames with a bearing shown | Candidates |", "|---|---|---|---|---|"]
    for label, kwargs in (("uncorrelated sensor noise", {"sensor_noise_rms": 0.002}),
                          ("diffuse noise field", {"diffuse_noise_rms": 0.002, "sensor_noise_rms": 0.0002}),
                          ("diffuse noise plus low-frequency wind noise", {"diffuse_noise_rms": 0.002, "wind_noise_rms": 0.01, "sensor_noise_rms": 0.0002})):
        state, pipeline, _ = run_pipeline(Scene(sources=[], seed=5, **kwargs), 940)
        rows = state.history()
        lines.append(f"| {label} | {940 * 0.128:.0f} s | {len(rows)} | {sum(r['brg'] is not None for r in rows)} | {pipeline.stats['candidates']} |")
    return lines


def detection_table(seeds: int) -> list[str]:
    lines = ["| SNR dB (diffuse noise) | Runs | Runs with a candidate during the source | Median latency s | "
             "Median bearing error deg | Frames with bearing during the source | Candidates outside the source |",
             "|---|---|---|---|---|---|---|"]
    for snr in (10, 5, 0, -5, -10):
        detected, latency, errors, shown, frames, outside = 0, [], [], 0, 0, 0
        for seed in range(seeds):
            rms, azimuth = 0.01, 40.0 + 61.0 * seed
            gain = lambda t: ((np.asarray(t) >= 8.0) & (np.asarray(t) < 16.0)).astype(float)  # noqa: E731
            source = PointSource(RotorSignal(f0_hz=140.0 + 23.0 * seed, seed=seed + 1), static_position(azimuth, 20.0, 40.0),
                                 level_at_1m=rms * 40.0, gain=gain)
            scene = Scene(sources=[source], seed=seed + 11, **noise_args("diffuse", rms / 10 ** (snr / 20)))
            state, _pipeline, _ = run_pipeline(scene, 160)
            events = state.events()
            inside = [e for e in events if 7.5 <= e["onset_time_s"] <= 16.5]
            outside += len(events) - len(inside)
            if inside:
                detected += 1
                latency.append(min(e["onset_time_s"] for e in inside) - 8.0)
            for row in state.history():
                if 8.5 <= row["t"] <= 16.0:
                    frames += 1
                    if row["brg"] is not None:
                        shown += 1
                        errors.append(abs(float(wrap180(row["brg"] - azimuth))))
        lines.append(f"| {snr} | {seeds} | {detected} | {np.median(latency):.2f} | " if latency else f"| {snr} | {seeds} | {detected} | n/a | ")
        lines[-1] += (f"{np.median(errors):.1f} | " if errors else "n/a | ") + f"{100 * shown / max(frames, 1):.0f}% | {outside} |"
    return lines


def timing() -> list[str]:
    analyzer = Analyzer()
    x = Scene(sources=[PointSource(RotorSignal(seed=1), static_position(30, 20, 40), level_at_1m=0.8)], sensor_noise_rms=0.002).render(4096)
    times = []
    for _ in range(200):
        t0 = time.perf_counter()
        analyzer.analyze(x)
        times.append(1000 * (time.perf_counter() - t0))
    scene = Scene(sources=[PointSource(RotorSignal(seed=1), static_position(30, 20, 40), level_at_1m=0.8)], sensor_noise_rms=0.002, seed=2)
    _state, pipeline, _wall = run_pipeline(scene, 470)
    return [f"- One analysis block (4096 frames, 4 microphones): median {np.median(times):.1f} ms, 99th percentile {np.percentile(times, 99):.1f} ms, "
            f"maximum {max(times):.1f} ms over 200 repeats.",
            f"- A new block arrives every 128 ms, so the median is {100 * np.median(times) / 128:.1f} percent of real time on one core.",
            f"- Full pipeline over 60 s of synthetic audio: smoothed load {100 * pipeline._load:.1f} percent of real time, "
            f"worst block {pipeline._dsp_ms_max:.1f} ms."]


def main() -> None:
    started = time.time()
    print("# Synthetic characterization\n")
    print("**SYNTHETIC DATA ONLY.** These numbers describe software behavior on idealized simulated signals. They are not "
          "detection range, field accuracy, or false-alert rates. Real microphones, enclosures, wind, reflections, and real "
          "aircraft will differ.\n")
    print(f"Software {__version__}. Python {platform.python_version()}, NumPy {np.__version__}, {platform.system()} {platform.release()}, "
          f"{platform.machine()}. Generated {time.strftime('%Y-%m-%d %H:%M:%S')} local time by `tools/characterize.py`.\n")
    print("## 1. Single-frame bearing, Revision B against Revision A\n")
    print("One 256 ms block per trial, 60 trials per row. One stationary rotor-like source at a random azimuth, elevation 5 to 45 "
          "degrees, 40 m away. SNR is source RMS over noise RMS across the full band. \"Shown\" is the share of frames where the "
          "estimator released a bearing. \"Wrong by over 10 deg\" is counted among shown bearings only. Revision B numbers are "
          "per frame, before the frame-to-frame stability gate that the pipeline adds.\n")
    print("\n".join(frame_table(60)))
    print("\n## 2. Ambient noise only, full pipeline\n")
    print("Two minutes per scene, no source. The pipeline includes the stability gate.\n")
    print("\n".join(quiet_table()))
    print("\n## 3. Candidate gate and bearing, full pipeline\n")
    print("Each run: 20 s of audio, ambient only for 8 s, then a rotor-like source for 8 s. Five runs per row with different "
          "fundamentals (140 to 232 Hz) and azimuths. Latency is candidate onset minus source start. Bearing error is measured "
          "against the source for every frame with a bearing while the source is on. When no candidate is raised the pipeline "
          "still reports the loudest coherent sound. At negative SNR that is the simulated ambient field, not the source, so "
          "the error against the source is large. The dashboard labels such a bearing as having no candidate.\n")
    print("\n".join(detection_table(5)))
    print("\n## 4. Processing time on this computer\n")
    print("\n".join(timing()))
    print(f"\nTotal run time {time.time() - started:.0f} s.")


if __name__ == "__main__":
    main()
