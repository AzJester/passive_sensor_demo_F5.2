"""Opt-in raw audio recording and metadata logs, written off the processing thread.

Nothing in this module runs unless the operator asks for it on the command
line. Raw audio can contain speech and other private sound. Tell people on
site, record only what the test needs, and delete recordings you do not need.
"""
from __future__ import annotations

import hashlib
import json
import queue
import shutil
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import RECORDING_SCHEMA, __version__

PRIVACY_NOTICE = ("Raw audio recording is ON. Recordings can capture voices and other private sound. "
                  "Record only what the test needs and delete what you do not need.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class JsonlWriter:
    """Append JSON lines from a background thread so slow disks cannot stall analysis."""

    def __init__(self, path, max_pending: int = 2000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue(maxsize=max_pending)
        self.dropped = 0
        self.written = 0
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, name=f"jsonl-{self.path.name}", daemon=True)
        self._thread.start()

    def write(self, record: dict) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                while True:
                    record = self._queue.get()
                    if record is None:
                        return
                    handle.write(json.dumps(record, allow_nan=False, default=_json_default) + "\n")
                    handle.flush()
                    self.written += 1
        except (OSError, ValueError) as error:
            self.error = str(error)

    def close(self, timeout: float = 5.0) -> None:
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)


def _json_default(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class SessionRecorder:
    """Write all capture channels to rotating PCM16 WAV files with JSON sidecars."""

    def __init__(self, directory, cfg, mode: str, device_info: dict | None = None,
                 labels: dict | None = None, max_pending: int = 256):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cfg, self.mode = cfg, mode
        self.device_info = device_info or {}
        self.labels = labels or {}
        self.fs = int(cfg.sample_rate)
        self.channels = int(cfg["device"]["capture_channels"])
        self.rotate_frames = int(cfg["recording"]["rotate_minutes"] * 60 * self.fs)
        self.min_free_bytes = float(cfg["recording"]["min_free_gb"]) * 1e9
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._queue: queue.Queue = queue.Queue(maxsize=max_pending)
        self.dropped_chunks = 0
        self.files: list[str] = []
        self.frames_total = 0
        self.error: str | None = None
        self._wave = None
        self._meta: dict | None = None
        self._sequence = 0
        self._pending_gap = False
        self._last_space_check = 0.0
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()

    # called from the processing thread
    def write(self, chunk) -> None:
        if self.error:
            return
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            self.dropped_chunks += 1
            self._pending_gap = True

    def status(self) -> dict:
        return {"enabled": self.error is None, "directory": str(self.directory), "session_id": self.session_id,
                "seconds": self.frames_total / self.fs, "files": len(self.files),
                "dropped_chunks": self.dropped_chunks, "error": self.error}

    def close(self, timeout: float = 10.0) -> None:
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)

    # writer thread
    def _run(self) -> None:
        try:
            while True:
                chunk = self._queue.get()
                if chunk is None:
                    break
                self._write_chunk(chunk)
        except (OSError, wave.Error) as error:
            self.error = f"recording stopped: {error}"
        finally:
            try:
                self._finish_file()
            except (OSError, wave.Error) as error:
                self.error = self.error or f"could not finalize recording: {error}"

    def _write_chunk(self, chunk) -> None:
        now = time.monotonic()
        if now - self._last_space_check > 10.0:
            self._last_space_check = now
            if shutil.disk_usage(self.directory).free < self.min_free_bytes:
                raise OSError("free disk space fell below recording.min_free_gb")
        if self._wave is None:
            self._start_file(chunk)
        meta = self._meta
        if chunk.gap_before or chunk.overflow or self._pending_gap:
            meta["gaps"].append({"sample_index": meta["frames"],
                                 "kind": "overflow" if chunk.overflow else "gap"})
            self._pending_gap = False
        pcm = np.clip(np.rint(np.asarray(chunk.samples, dtype=np.float64) * 32768.0), -32768, 32767).astype("<i2")
        self._wave.writeframes(pcm.tobytes())
        meta["frames"] += len(pcm)
        self.frames_total += len(pcm)
        if meta["frames"] >= self.rotate_frames:
            self._finish_file()

    def _start_file(self, chunk) -> None:
        self._sequence += 1
        name = f"{self.cfg.node_id}_{self.session_id}_{self._sequence:03d}.wav"
        path = self.directory / name
        if path.exists():
            raise OSError(f"{name} already exists; recordings are never overwritten")
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(self.channels)
        self._wave.setsampwidth(2)
        self._wave.setframerate(self.fs)
        self._meta = {
            "schema": RECORDING_SCHEMA, "file": name, "session_id": self.session_id, "sequence": self._sequence,
            "node_id": self.cfg.node_id, "mode": self.mode,
            "synthetic": self.mode == "SIMULATED",
            "start_time_s": chunk.time_s,
            "start_utc": datetime.fromtimestamp(chunk.time_s, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "time_quality": chunk.time_quality, "sample_rate": self.fs, "channels": self.channels,
            "sample_format": "PCM_16", "frames": 0, "raw_indices": self.cfg["device"]["raw_indices"],
            "geometry_m": self.cfg["array"]["geometry_m"], "temperature_c": self.cfg["array"]["temperature_c"],
            "config_sha256": self.cfg.sha256, "firmware_record": self.cfg["device"]["firmware_record"],
            "software_version": __version__, "device": self.device_info, "gaps": [],
            "labels": dict(self.labels),
        }

    def _finish_file(self) -> None:
        if self._wave is None:
            return
        self._wave.close()
        self._wave = None
        meta, self._meta = self._meta, None
        path = self.directory / meta["file"]
        meta["duration_s"] = meta["frames"] / self.fs
        meta["sha256"] = sha256_file(path)
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=_json_default), encoding="utf-8")
        self.files.append(meta["file"])
