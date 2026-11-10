"""Shared fixtures for the test suite. Everything here is synthetic."""
from __future__ import annotations

import copy
import threading
import wave
from pathlib import Path

import numpy as np

from pasensor.acquisition import SimulatedSource
from pasensor.config import validate
from pasensor.geometry import wrap180
from pasensor.pipeline import Pipeline
from pasensor.simulate import PointSource, RotorSignal, Scene, static_position
from pasensor.state import SharedState

ROOT = Path(__file__).resolve().parent.parent
FAST = {"detection": {"background_seconds": 2.0}}


def make_config(base_dir=None, **sections):
    """Default configuration with nested overrides, e.g. make_config(detection={"min_harmonics": 5})."""
    raw = copy.deepcopy(FAST)
    for name, value in sections.items():
        if isinstance(value, dict):
            raw.setdefault(name, {}).update(value)
        else:
            raw[name] = value
    return validate(raw, base_dir=base_dir)


def angular_error(a, b) -> float:
    return abs(float(wrap180(a - b)))


def rotor_scene(azimuth=75.0, elevation=15.0, distance=40.0, received_rms=0.01, noise=0.0006, gain=None, seed=3, f0=165.0):
    source = PointSource(RotorSignal(f0_hz=f0, seed=seed), static_position(azimuth, elevation, distance),
                         level_at_1m=received_rms * distance, gain=gain)
    return Scene(sources=[source], sensor_noise_rms=noise, diffuse_noise_rms=noise, seed=seed)


def quiet_scene(noise=0.0006, seed=3):
    return Scene(sources=[], sensor_noise_rms=noise, diffuse_noise_rms=noise, seed=seed)


def step_gain(start_s, end_s=1e9):
    return lambda t: ((np.asarray(t) >= start_s) & (np.asarray(t) < end_s)).astype(float)


class CollectingState(SharedState):
    """SharedState that also keeps every published status."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.published = []

    def publish(self, status, history_row=None):
        self.published.append(copy.deepcopy(status))
        super().publish(status, history_row)


def sim_factory(cfg, scene=None, scenario="demo", mode=None, limit_chunks=None, start_time=1_000_000.0):
    """Factory for a non-realtime simulated source. mode relabels it so LIVE and REPLAY policies can be tested."""
    from pasensor.acquisition import SourceEnded

    def factory():
        source = SimulatedSource(cfg["device"], cfg["array"]["geometry_m"], cfg["array"]["temperature_c"],
                                 cfg["processing"]["hop_frames"], scenario=scenario, realtime=False,
                                 start_time=start_time, scene=scene)
        if mode:
            source.mode = mode
        if limit_chunks is not None:
            inner_read, count = source.read, {"n": 0}

            def read():
                if count["n"] >= limit_chunks:
                    raise SourceEnded()
                count["n"] += 1
                return inner_read()
            source.read = read
        return source
    return factory


def run_pipeline(cfg, mode, factory, **kwargs):
    state = CollectingState(cfg["health"]["stale_after_s"], history_frames=100000)
    pipeline = Pipeline(cfg, mode, factory, state, **kwargs)
    final = pipeline.run(threading.Event())
    return final, state, pipeline


def six_channel(mics: np.ndarray, raw_indices=(2, 3, 4, 5), channels=6) -> np.ndarray:
    out = np.repeat(mics.mean(axis=1, keepdims=True), channels, axis=1)
    out[:, list(raw_indices)] = mics
    return out


def write_wav(path, samples: np.ndarray, fs: int = 16000) -> Path:
    path = Path(path)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(np.clip(np.rint(samples * 32768.0), -32768, 32767).astype("<i2").tobytes())
    return path


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.active = False
        self.time = 100.0
        self.closed = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True

    def feed(self, data, overflow=False, adc=0.0):
        status = type("Status", (), {"input_overflow": overflow})()
        info = type("Time", (), {"inputBufferAdcTime": adc})()
        self.callback(np.asarray(data, dtype=np.float32), len(data), info, status)


class FakeSoundDevice:
    """Stands in for the sounddevice module so device logic can be tested without hardware."""

    def __init__(self, devices, hostapis=("MME", "Windows WASAPI"), reject=False):
        self._devices, self._hostapis, self.reject = devices, hostapis, reject
        self.stream = None

    def query_devices(self):
        return [dict(d) for d in self._devices]

    def query_hostapis(self):
        return tuple({"name": name} for name in self._hostapis)

    def check_input_settings(self, **_kwargs):
        if self.reject:
            raise RuntimeError("Invalid sample rate")

    def InputStream(self, **kwargs):
        self.stream = FakeStream(**kwargs)
        return self.stream


def device(name, hostapi, channels, rate=16000.0):
    return {"name": name, "hostapi": hostapi, "max_input_channels": channels, "default_samplerate": rate}


def late_rotor(**kwargs):
    """A rotor-like source that starts at 3 s, after the 2 s ambient measurement used in these tests."""
    return rotor_scene(gain=step_gain(3.0), **kwargs)
