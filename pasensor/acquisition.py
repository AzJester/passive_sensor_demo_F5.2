"""Audio sources. Live capture, WAV replay, and simulation are separate classes.

Every source hands the pipeline fixed-size chunks of the full device frame
(all capture channels), each tagged with the time of its first sample, how
good that time is, and whether samples were lost before it. A source never
hides a fault: it raises SourceFault and the pipeline decides what to do.

Live capture uses a PortAudio callback that only copies data into a bounded
queue. No analysis, logging, or file access happens on the audio thread.
"""
from __future__ import annotations

import json
import queue
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class SourceFault(Exception):
    """Acquisition failed. kind is a short code such as DISCONNECTED or STALLED."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class SourceEnded(Exception):
    """A finite source (a recording) reached its end."""


@dataclass
class AudioChunk:
    samples: np.ndarray            # (hop_frames, capture_channels), float
    index: int                     # sample index of the first frame since the source opened
    time_s: float                  # UNIX time of the first frame
    time_quality: str              # adc_clock | host_arrival | recording_clock | file_relative | synthetic
    overflow: bool = False         # the driver reported lost input before this chunk
    gap_before: bool = False       # samples are missing immediately before this chunk


# --------------------------------------------------------------------------- devices
def load_sounddevice():
    try:
        import sounddevice
    except (ImportError, OSError) as error:
        raise SourceFault("BACKEND", "Live capture needs the 'sounddevice' package. Run install_windows.ps1.") from error
    return sounddevice


def list_input_devices(sd) -> list[dict]:
    apis = [api["name"] for api in sd.query_hostapis()]
    out = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            out.append({"index": index, "name": device["name"], "hostapi": apis[device["hostapi"]],
                        "max_input_channels": int(device["max_input_channels"]),
                        "default_samplerate": float(device["default_samplerate"])})
    return out


def resolve_device(sd, device_cfg: dict, override=None) -> dict:
    """Pick exactly one capture device or raise SourceFault with a useful list.

    Device indices change when USB devices come and go, so the default is to
    match by name and host API. An explicit index is honoured as given.
    """
    devices = list_input_devices(sd)
    channels = device_cfg["capture_channels"]
    if isinstance(override, int) or (isinstance(override, str) and override.strip().isdigit()):
        index = int(override)
        match = [d for d in devices if d["index"] == index]
        if not match:
            raise SourceFault("NOT_FOUND", f"No input device has index {index}. Run 'python -m pasensor devices'.")
        chosen = match
    else:
        name = (override if isinstance(override, str) and override.strip() else device_cfg["name_contains"]).lower()
        named = [d for d in devices if name in d["name"].lower()]
        if not named:
            seen = "; ".join(f"[{d['index']}] {d['name']} ({d['hostapi']}, {d['max_input_channels']} ch)" for d in devices) or "none"
            raise SourceFault("NOT_FOUND", f"No input device name contains '{name}'. Inputs seen: {seen}")
        api = device_cfg["hostapi_contains"].lower()
        chosen = [d for d in named if api and api in d["hostapi"].lower()] or named
        wide = [d for d in chosen if d["max_input_channels"] >= channels]
        if not wide:
            listing = "; ".join(f"[{d['index']}] {d['name']} ({d['hostapi']}, {d['max_input_channels']} ch)" for d in named)
            raise SourceFault("FORMAT", f"The device was found but no endpoint offers {channels} input channels: {listing}. "
                                        "Check the firmware profile and the Windows default format for the device.")
        chosen = wide
        if len(chosen) > 1:
            listing = "; ".join(f"[{d['index']}] {d['name']} ({d['hostapi']})" for d in chosen)
            raise SourceFault("AMBIGUOUS", f"More than one device matches: {listing}. Pass --device with one index.")
    device = chosen[0]
    if device["max_input_channels"] < channels:
        raise SourceFault("FORMAT", f"Device [{device['index']}] {device['name']} offers {device['max_input_channels']} "
                                    f"input channels; this profile needs {channels}.")
    try:
        sd.check_input_settings(device=device["index"], channels=channels, dtype="float32",
                                samplerate=device_cfg["sample_rate"])
    except Exception as error:  # PortAudioError or ValueError, depending on the backend
        raise SourceFault("FORMAT", f"Device [{device['index']}] {device['name']} rejected {channels} channels at "
                                    f"{device_cfg['sample_rate']} Hz: {error}") from error
    return device


# --------------------------------------------------------------------------- live
class LiveSource:
    mode = "LIVE"

    def __init__(self, device_cfg: dict, hop_frames: int, override=None, queue_blocks: int = 64,
                 stall_timeout_s: float = 2.0, sd=None):
        self.device_cfg, self.hop, self.override = device_cfg, int(hop_frames), override
        self.fs = float(device_cfg["sample_rate"])
        self.channels = int(device_cfg["capture_channels"])
        self.stall_timeout = float(stall_timeout_s)
        self._sd = sd
        self._queue: queue.Queue = queue.Queue(maxsize=int(queue_blocks))
        self._stream = None
        self._offset = None
        self._gap_pending = False
        self._buffer = np.empty((0, self.channels), dtype=np.float32)
        self._buffer_time = 0.0
        self._buffer_quality = "host_arrival"
        self._flag_overflow = False
        self._flag_gap = False
        self._index = 0
        self.device = None
        self.stats = {"callbacks": 0, "overflow_events": 0, "queue_dropped_frames": 0}

    def open(self) -> None:
        sd = self._sd or load_sounddevice()
        self._sd = sd
        self.device = resolve_device(sd, self.device_cfg, self.override)
        try:
            self._stream = sd.InputStream(device=self.device["index"], channels=self.channels, samplerate=self.fs,
                                          dtype="float32", blocksize=self.hop, callback=self._callback)
            self._offset = time.time() - float(self._stream.time)
            self._stream.start()
        except SourceFault:
            raise
        except Exception as error:
            raise SourceFault("DEVICE_ERROR", f"Could not open [{self.device['index']}] {self.device['name']}: {error}") from error

    def _callback(self, indata, frames, time_info, status) -> None:
        # Audio thread: copy and return. Nothing slow may happen here.
        self.stats["callbacks"] += 1
        overflow = bool(getattr(status, "input_overflow", False))
        adc = float(getattr(time_info, "inputBufferAdcTime", 0.0) or 0.0)
        if adc > 0.0 and self._offset is not None:
            stamp, quality = adc + self._offset, "adc_clock"
        else:
            stamp, quality = time.time() - frames / self.fs, "host_arrival"
        gap, self._gap_pending = self._gap_pending, False
        try:
            self._queue.put_nowait((indata.copy(), stamp, quality, overflow, gap))
        except queue.Full:
            self.stats["queue_dropped_frames"] += int(frames)
            self._gap_pending = True

    def read(self) -> AudioChunk:
        deadline = time.monotonic() + self.stall_timeout
        while len(self._buffer) < self.hop:
            try:
                data, stamp, quality, overflow, gap = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stream is None or not self._stream.active:
                    raise SourceFault("DISCONNECTED", "The audio stream stopped. The device may be unplugged.")
                if time.monotonic() > deadline:
                    raise SourceFault("STALLED", f"No audio arrived for {self.stall_timeout:.1f} s.")
                continue
            if overflow:
                self.stats["overflow_events"] += 1
            if overflow or gap:
                # Samples are missing in front of this buffer. Start a fresh chunk.
                self._buffer = np.empty((0, self.channels), dtype=np.float32)
                self._flag_gap = True
                self._flag_overflow = self._flag_overflow or overflow
            if len(self._buffer) == 0:
                self._buffer_time, self._buffer_quality = stamp, quality
            self._buffer = np.concatenate((self._buffer, data)) if len(self._buffer) else data
        samples, self._buffer = self._buffer[:self.hop], self._buffer[self.hop:]
        chunk = AudioChunk(samples, self._index, self._buffer_time, self._buffer_quality,
                           overflow=self._flag_overflow, gap_before=self._flag_gap)
        self._buffer_time += self.hop / self.fs
        self._index += self.hop
        self._flag_overflow = self._flag_gap = False
        return chunk

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            for action in (stream.stop, stream.close):
                try:
                    action()
                except Exception:
                    pass

    def describe(self) -> dict:
        info = {"kind": "live", "sample_rate": self.fs, "capture_channels": self.channels,
                "queue_depth": self._queue.qsize(), **self.stats}
        if self.device:
            info.update(device=self.device["name"], device_index=self.device["index"], hostapi=self.device["hostapi"])
        return info


# --------------------------------------------------------------------------- replay
class ReplaySource:
    """Replay a PCM WAV made by the recorder. Timestamps come from the recording, not the wall clock."""
    mode = "REPLAY"

    def __init__(self, path, device_cfg: dict, hop_frames: int, realtime: bool = True):
        self.path, self.device_cfg, self.hop, self.realtime = Path(path), device_cfg, int(hop_frames), bool(realtime)
        self.fs = float(device_cfg["sample_rate"])
        self.channels = int(device_cfg["capture_channels"])
        self._wave = None
        self._index = 0
        self._start_time = 0.0
        self._quality = "file_relative"
        self._gaps: list[int] = []
        self._wall_start = 0.0
        self.sidecar = None
        self.total_frames = 0

    def open(self) -> None:
        try:
            self._wave = wave.open(str(self.path), "rb")
        except (OSError, wave.Error, EOFError) as error:
            raise SourceFault("FILE", f"Cannot open {self.path}: {error}") from error
        w = self._wave
        if w.getcomptype() != "NONE" or w.getsampwidth() not in (2, 3, 4):
            raise SourceFault("FORMAT", "Replay accepts uncompressed integer PCM WAV (16, 24, or 32 bit).")
        if w.getframerate() != int(self.fs) or w.getnchannels() != self.channels:
            raise SourceFault("FORMAT", f"{self.path.name} is {w.getnchannels()} channels at {w.getframerate()} Hz; "
                                        f"the configuration expects {self.channels} channels at {int(self.fs)} Hz.")
        self.total_frames = w.getnframes()
        sidecar = self.path.with_suffix(".json")
        if sidecar.exists():
            try:
                self.sidecar = json.loads(sidecar.read_text(encoding="utf-8"))
                self._start_time = float(self.sidecar["start_time_s"])
                self._quality = "recording_clock"
                self._gaps = sorted(int(g["sample_index"]) for g in self.sidecar.get("gaps", []))
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise SourceFault("FILE", f"Sidecar {sidecar.name} is unreadable: {error}") from error
        self._wall_start = time.monotonic()

    def read(self) -> AudioChunk:
        w = self._wave
        raw = w.readframes(self.hop)
        width = w.getsampwidth()
        if len(raw) < self.hop * width * self.channels:
            raise SourceEnded()
        if width == 2:
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif width == 4:
            samples = (np.frombuffer(raw, dtype="<i4") / 2147483648.0).astype(np.float32)
        else:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            value = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            samples = (np.where(value >= 1 << 23, value - (1 << 24), value) / 8388608.0).astype(np.float32)
        samples = samples.reshape(-1, self.channels)
        gap = any(self._index <= g < self._index + self.hop for g in self._gaps)
        chunk = AudioChunk(samples, self._index, self._start_time + self._index / self.fs, self._quality,
                           overflow=False, gap_before=gap)
        self._index += self.hop
        if self.realtime:
            delay = self._wall_start + self._index / self.fs - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return chunk

    def close(self) -> None:
        if self._wave is not None:
            self._wave.close()
            self._wave = None

    def describe(self) -> dict:
        return {"kind": "replay", "file": self.path.name, "sample_rate": self.fs, "capture_channels": self.channels,
                "total_seconds": self.total_frames / self.fs, "position_seconds": self._index / self.fs,
                "has_sidecar": self.sidecar is not None, "realtime": self.realtime}


# --------------------------------------------------------------------------- simulation
class SimulatedSource:
    """Synthetic scene rendered into the device channel layout. Never real audio."""
    mode = "SIMULATED"

    def __init__(self, device_cfg: dict, geometry, temperature_c: float, hop_frames: int, scenario: str = "demo",
                 seed: int = 20261109, realtime: bool = True, start_time: float | None = None, scene=None):
        self.device_cfg, self.hop, self.realtime = device_cfg, int(hop_frames), bool(realtime)
        self.fs = float(device_cfg["sample_rate"])
        self.channels = int(device_cfg["capture_channels"])
        self.raw_indices = list(device_cfg["raw_indices"])
        self.geometry, self.temperature_c = geometry, temperature_c
        self.scenario, self.seed = scenario, int(seed)
        self._start_time = start_time
        self._scene = scene
        self._index = 0
        self._wall_start = 0.0

    def open(self) -> None:
        from .simulate import build_scenario
        if self._scene is None:
            self._scene = build_scenario(self.scenario, self.fs, self.geometry, self.temperature_c, self.seed)
        if self._start_time is None:
            self._start_time = time.time()
        self._wall_start = time.monotonic()

    def read(self) -> AudioChunk:
        mics = self._scene.render(self.hop)
        samples = np.empty((self.hop, self.channels), dtype=np.float32)
        samples[:] = mics.mean(axis=1, keepdims=True)      # stand-in for the processed channels
        samples[:, self.raw_indices] = mics
        chunk = AudioChunk(samples, self._index, self._start_time + self._index / self.fs, "synthetic")
        self._index += self.hop
        if self.realtime:
            delay = self._wall_start + self._index / self.fs - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return chunk

    def close(self) -> None:
        pass

    def describe(self) -> dict:
        return {"kind": "simulated", "scenario": self.scenario, "seed": self.seed, "sample_rate": self.fs,
                "capture_channels": self.channels, "position_seconds": self._index / self.fs}


# --------------------------------------------------------------------------- fault injection
def fault_injecting_factory(inner_factory, plan: dict):
    """Wrap a source factory so tests can inject acquisition faults.

    plan maps a read number (counted across restarts) to an action:
    "overflow", "stall", "disconnect", "crash", "nan", "clip", "dead:<channel>",
    or "hang:<seconds>".
    The key "open_failures" gives how many open() calls fail before one succeeds.
    """
    state = {"reads": 0, "open_failures": int(plan.get("open_failures", 0))}

    class Injected:
        def __init__(self):
            self.inner = inner_factory()
            self.mode = self.inner.mode

        def open(self):
            if state["open_failures"] > 0:
                state["open_failures"] -= 1
                raise SourceFault("NOT_FOUND", "injected: device not present")
            self.inner.open()

        def read(self):
            number, state["reads"] = state["reads"], state["reads"] + 1
            action = plan.get(number)
            if action == "stall":
                raise SourceFault("STALLED", "injected: no audio arrived")
            if action == "disconnect":
                raise SourceFault("DISCONNECTED", "injected: device unplugged")
            if action == "crash":
                raise RuntimeError("injected software fault")
            if action is not None and action.startswith("hang:"):
                time.sleep(float(action.split(":")[1]))          # the source goes quiet without failing
                action = None
            chunk = self.inner.read()
            if action is None:
                return chunk
            chunk.samples = np.array(chunk.samples, copy=True)
            if action == "overflow":
                chunk.overflow = chunk.gap_before = True
            elif action == "nan":
                chunk.samples[7, :] = np.nan
            elif action == "clip":
                chunk.samples[:16, :] = 1.0
            elif action.startswith("dead:"):
                chunk.samples[:, int(action.split(":")[1])] = 0.0
            return chunk

        def close(self):
            self.inner.close()

        def describe(self):
            return self.inner.describe()

    return Injected
