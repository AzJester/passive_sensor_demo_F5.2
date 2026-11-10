"""Processing pipeline: source supervision, analysis, gating, and publication.

One thread runs this loop. Audio capture runs on the driver's own thread and
file writing runs on writer threads, so slow analysis or a slow disk cannot
disturb capture timing. If analysis falls behind, the capture queue fills,
chunks are dropped, and the loss is reported as a gap. It is never hidden.

Fault policy
    LIVE       any acquisition fault closes the device and retries with backoff.
    REPLAY     a fault ends the run. A replay must be reproducible, so it is
               never restarted behind the operator's back.
    SIMULATED  software faults restart the scene; used for recovery tests.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone

import numpy as np

from . import EVENT_SCHEMA, OBSERVATION_SCHEMA, STATUS_SCHEMA, __version__
from .acquisition import SourceEnded, SourceFault
from .detector import BearingTracker, EventBuilder, EventGate, utc_iso
from .dsp import AUDIO_FAULTS, Analyzer
from .geometry import local_to_site_azimuth, site_azimuth_to_compass

log = logging.getLogger("pasensor.pipeline")


class Pipeline:
    def __init__(self, cfg, mode: str, source_factory, state, *, event_log=None, observation_log=None,
                 recorder=None, classifier=None, max_software_faults: int = 5):
        if mode not in ("LIVE", "REPLAY", "SIMULATED"):
            raise ValueError("mode must be LIVE, REPLAY, or SIMULATED")
        self.cfg, self.mode, self.source_factory, self.state = cfg, mode, source_factory, state
        self.event_log, self.observation_log, self.recorder, self.classifier = event_log, observation_log, recorder, classifier
        self.max_software_faults = int(max_software_faults)
        self.fs = cfg.sample_rate
        self.block = cfg["processing"]["block_frames"]
        self.hop = cfg["processing"]["hop_frames"]
        self.raw_indices = list(cfg["device"]["raw_indices"])
        self.settings = cfg.dsp_settings()
        self.analyzer = Analyzer(self.fs, cfg["array"]["geometry_m"], cfg["array"]["temperature_c"], self.settings)
        self.gate = EventGate(cfg["detection"], cfg.hop_seconds)
        self.tracker = BearingTracker(cfg["tracking"])
        self.events = EventBuilder(cfg.node_id, mode, cfg.sha256)
        self._ring = np.zeros((self.block, len(self.raw_indices)))
        self._valid = 0
        self._analysed = False
        self._last_event_push = 0.0
        self._load = 0.0
        self._dsp_ms = 0.0
        self._dsp_ms_max = 0.0
        self._opened_at = time.monotonic()
        self._last_health_log = 0.0
        self._last_data_time = None
        self.source = None
        self.stats = {"frames_total": 0, "frames_analysed": 0, "overflow_count": 0, "gap_count": 0, "opens": 0,
                      "fault_count": 0, "software_faults": 0, "last_fault": None, "last_fault_kind": None,
                      "last_fault_utc": None, "candidates": 0}

    # ------------------------------------------------------------------ run
    def run(self, stop: threading.Event) -> str:
        backoff = self.cfg["health"]["restart_backoff_s"]
        attempt = 0
        recent_software_faults: deque = deque(maxlen=self.max_software_faults)
        final = "STOPPED"
        self._publish_idle("STARTING", "Opening the audio source.")
        while not stop.is_set():
            source = None
            try:
                source = self.source_factory()
                source.open()
                self.source = source
                self.stats["opens"] += 1
                self._opened_at = time.monotonic()
                self._reset_stream()
                while not stop.is_set():
                    chunk = source.read()
                    attempt = 0
                    self._process(chunk)
                final = "STOPPED"
                break
            except SourceEnded:
                final = "REPLAY_COMPLETE"
                break
            except SourceFault as fault:
                self._fault(fault.kind, str(fault))
                if self.mode != "LIVE":
                    final = "FAILED"
                    break
            except Exception as error:  # software failure inside analysis or a source
                log.error("software fault: %s", traceback.format_exc())
                self.stats["software_faults"] += 1
                self._fault("SOFTWARE_FAULT", f"{type(error).__name__}: {error}")
                now = time.monotonic()
                recent_software_faults.append(now)
                repeated = (len(recent_software_faults) == self.max_software_faults
                            and now - recent_software_faults[0] < 60.0)
                if self.mode == "REPLAY" or repeated:
                    final = "FAILED"
                    break
            finally:
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        log.warning("error while closing the source: %s", traceback.format_exc())
            delay = float(backoff[min(attempt, len(backoff) - 1)])
            attempt += 1
            self._publish_idle("RECONNECTING", f"Retrying in {delay:.0f} s (attempt {attempt}).")
            if stop.wait(delay):
                break
        self._end_event("run ended")
        self._publish_idle(final, {"REPLAY_COMPLETE": "The recording has ended.", "FAILED": "Stopped after a fault.",
                                   "STOPPED": "Stopped by the operator."}[final])
        return final

    # -------------------------------------------------------------- helpers
    def _reset_stream(self) -> None:
        self._valid = 0
        self.gate.reset_window()
        self.tracker.reset()

    def _health(self, kind: str, message: str) -> None:
        log.warning("%s: %s", kind, message)
        if self.event_log is not None:
            self.event_log.write({"schema": EVENT_SCHEMA, "record": "health", "kind": kind, "message": message,
                                  "node_id": self.cfg.node_id, "mode": self.mode,
                                  "wall_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds")})

    def _fault(self, kind: str, message: str) -> None:
        self.stats["fault_count"] += 1
        self.stats.update(last_fault=message, last_fault_kind=kind,
                          last_fault_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._end_event(f"ended by fault {kind}")
        self._health(kind, message)
        self._publish_idle("FAULT", message)

    def _end_event(self, note: str) -> None:
        if self.events.current is None:
            return
        finished = self.events.finish(self._last_data_time or time.time())
        finished["end_note"] = note
        self.gate.active = False
        self.state.add_event(finished)
        if self.event_log is not None:
            self.event_log.write(finished)

    def _acquisition(self, status: str) -> dict:
        source = {}
        if self.source is not None:
            try:
                source = self.source.describe()
            except Exception:
                source = {}
        s = self.stats
        return {"status": status, "source": source, "sample_rate": self.fs,
                "capture_channels": self.cfg["device"]["capture_channels"], "raw_indices": self.raw_indices,
                "frames_total": s["frames_total"], "frames_analysed": s["frames_analysed"],
                "overflow_count": s["overflow_count"], "gap_count": s["gap_count"],
                "queue_dropped_frames": source.get("queue_dropped_frames", 0),
                "restarts": max(s["opens"] - 1, 0), "fault_count": s["fault_count"],
                "software_faults": s["software_faults"], "last_fault": s["last_fault"],
                "last_fault_kind": s["last_fault_kind"], "last_fault_utc": s["last_fault_utc"],
                "uptime_s": time.monotonic() - self._opened_at}

    def _base(self, state: str) -> dict:
        cal = self.cfg["calibration"]
        return {"schema": STATUS_SCHEMA, "software_version": __version__, "node_id": self.cfg.node_id,
                "mode": self.mode, "state": state,
                "config": {"sha256": self.cfg.sha256, "path": str(self.cfg.path) if self.cfg.path else None,
                           "calibrated": self.cfg.calibrated, "calibration_record": cal["record"] or None},
                "recording": self.recorder.status() if self.recorder is not None else {"enabled": False},
                "processing": {"load_fraction": self._load, "dsp_ms": self._dsp_ms, "dsp_ms_max": self._dsp_ms_max,
                               "hop_ms": 1000.0 * self.hop / self.fs, "block_ms": 1000.0 * self.block / self.fs}}

    def _publish_idle(self, state: str, message: str) -> None:
        status = self._base(state)
        kind = {"FAULT": "FAULT", "RECONNECTING": "DISCONNECTED", "FAILED": "FAULT"}.get(state, "IDLE")
        status.update(message=message, acquisition=self._acquisition(kind), audio=None, detection=None,
                      bearing=None, identity=None, spectrum=None)
        self.state.publish(status)

    # -------------------------------------------------------------- process
    def _process(self, chunk) -> None:
        self.stats["frames_total"] += len(chunk.samples)
        if self.recorder is not None:
            self.recorder.write(chunk)
        lost = bool(chunk.overflow or chunk.gap_before)
        if chunk.overflow:
            self.stats["overflow_count"] += 1
        elif chunk.gap_before:
            self.stats["gap_count"] += 1
        end_time = chunk.time_s + self.hop / self.fs
        if lost:
            self._reset_stream()
            self._end_event("ended by lost samples")
            now = time.monotonic()
            if now - self._last_health_log > 1.0:
                self._last_health_log = now
                self._health("OVERFLOW" if chunk.overflow else "GAP",
                             f"samples lost before sample index {chunk.index}")
        raw = np.asarray(chunk.samples, dtype=np.float64)[:, self.raw_indices]
        self._ring = np.concatenate((self._ring[self.hop:], raw))
        self._valid = min(self._valid + self.hop, self.block)
        self._last_data_time = end_time
        times = {"frame_end_s": end_time, "frame_end_utc": utc_iso(end_time),
                 "frame_center_s": end_time - 0.5 * self.block / self.fs, "time_quality": chunk.time_quality}
        if self._valid < self.block:
            waiting = "BASELINING" if self.gate.floor is None else "LISTENING"
            status = self._base(waiting if self._analysed else "STARTING")
            status.update(times)
            reason = "OVERFLOW" if lost else None
            status.update(
                acquisition=self._acquisition("OVERFLOW" if lost else "OK"), audio=None, spectrum=None,
                detection={"candidate": False, "event_id": None, "blocked_by": [reason] if reason else [],
                           "floor_dbfs": self.gate.floor, "floor_ready": self.gate.floor is not None},
                bearing={"available": False, "bearing_local_deg": None, "bearing_site_deg": None, "compass_deg": None,
                         "calibrated": self.cfg.calibrated and self.mode != "SIMULATED",
                         "reasons": [reason] if reason else []},
                identity={"available": False, "reason": "No trained classifier is installed."},
                message="Waiting for a full audio block." if not lost else "Samples were lost. Refilling the analysis block.")
            self.state.publish(status)
            return

        started = time.perf_counter()
        frame = self.analyzer.analyze(self._ring)
        psd = frame.pop("_psd", None)
        if frame["bearing"]["polarity_suspect_pairs"] >= 3:
            # One inverted microphone makes three of the six pairs correlate negatively.
            frame["audio"]["warnings"].append("POLARITY_SUSPECT")
        gate = self.gate.update(frame)
        identity = {"available": False, "reason": "No trained classifier is installed. The software does not judge what the sound is."}
        identity_score = None
        if self.classifier is not None:
            identity = self.classifier.update(psd, frame)
            identity_score = identity.get("score") if identity.get("available") else None
        bearing = self._bearing(frame["bearing"], gate["candidate"])
        self._events(gate, frame, bearing, identity_score, times)
        elapsed = time.perf_counter() - started
        self._dsp_ms = 1000.0 * elapsed
        self._dsp_ms_max = max(self._dsp_ms_max, self._dsp_ms)
        self._load += 0.1 * (elapsed / (self.hop / self.fs) - self._load)
        self.stats["frames_analysed"] += 1
        self._analysed = True

        audio_fault = frame["audio"]["status"] in AUDIO_FAULTS
        status = self._base(gate["state"])
        status.update(times)
        det = self.cfg["detection"]
        harmonic = frame["harmonic"]
        status.update(
            acquisition=self._acquisition("OK"),
            audio={**frame["audio"], "band_level_dbfs": frame["band_level_dbfs"]},
            detection={
                "candidate": gate["candidate"], "event_id": self.events.current["event_id"] if self.events.current else None,
                "qualifies": gate["qualifies"], "blocked_by": gate["blocked_by"],
                "floor_dbfs": gate["floor_dbfs"], "floor_ready": gate["floor_ready"], "floor_seconds": gate["floor_seconds"],
                "level_above_floor_db": gate["level_above_floor_db"],
                "harmonic_score": harmonic["score"], "harmonic_count": harmonic["count"],
                "harmonics_considered": harmonic["considered"], "f0_hz": harmonic["f0_hz"],
                "mean_prominence_db": harmonic["mean_prominence_db"], "harmonic_power_fraction": harmonic["power_fraction"],
                "harmonic_freqs_hz": harmonic["harmonic_freqs_hz"],
                "qualifying_frames": gate["qualifying_frames"], "window_frames": gate["window_frames"],
                "thresholds": {"minimum_db_above_floor": det["minimum_db_above_floor"],
                               "harmonic_score": det["harmonic_score_threshold"], "min_harmonics": det["min_harmonics"],
                               "onset_frames": det["onset_frames"], "window_frames": det["window_frames"]},
                "label": "Heuristic rotor-like sound candidate. Not an identification.",
            },
            bearing=bearing, identity=identity, spectrum=frame["spectrum"],
            message="Audio fault in this block." if audio_fault else "")
        row = {"t": times["frame_end_s"], "level": frame["band_level_dbfs"], "floor": gate["floor_dbfs"],
               "score": harmonic["score"], "count": harmonic["count"], "cand": gate["candidate"],
               "brg": bearing["bearing_local_deg"], "ok": not audio_fault}
        self.state.publish(status, row)

    def _bearing(self, b: dict, candidate: bool) -> dict:
        cal, site = self.cfg["calibration"], self.cfg["site"]
        simulated = self.mode == "SIMULATED"
        calibrated = self.cfg.calibrated and not simulated
        raw = b["azimuth_deg"]
        track = self.tracker.update(raw)
        reasons = list(b["reasons"])
        local = None
        if raw is not None:
            if not track["stable"]:
                reasons.append("UNSTABLE")
            elif simulated:
                local = raw
            elif calibrated or cal["show_uncalibrated_bearing"]:
                local = ((-raw if cal["mirror"] else raw) + cal["local_angle_offset_deg"]) % 360.0
            else:
                reasons.append("UNCALIBRATED")
        site_azimuth = compass = None
        if local is not None and calibrated and site["heading_deg"] is not None:
            site_azimuth = local_to_site_azimuth(local, site["heading_deg"], site["face_up"])
            compass = site_azimuth_to_compass(site_azimuth)
        s = self.settings
        return {
            "available": local is not None, "bearing_local_deg": local, "bearing_site_deg": site_azimuth,
            "compass_deg": compass, "calibrated": calibrated, "simulated_geometry": simulated,
            "calibration_record": cal["record"] if calibrated else None,
            "sigma_deg": cal["bearing_sigma_deg"] if calibrated else None,
            "reasons": [] if local is not None else reasons, "basis": b.get("basis"),
            # What the bearing refers to. Without a candidate it is only the loudest coherent sound around.
            "subject": ("CANDIDATE_HARMONICS" if candidate and b.get("basis") == "HARMONIC_BINS"
                        else ("CANDIDATE_BAND" if candidate else "DOMINANT_SOUND")),
            "metrics": {"srp_peak": b["srp_peak"], "srp_z": b["srp_z"], "srp_contrast": b["srp_contrast"],
                        "second_peak_ratio": b["second_peak_ratio"], "coherence": b["coherence"],
                        "coherent_bandwidth_hz": b["coherent_bandwidth_hz"], "tdoa_residual_us": b["tdoa_residual_us"],
                        "elevation_hint_deg": b["elevation_hint_deg"], "polarity_suspect_pairs": b["polarity_suspect_pairs"],
                        "frames_agreeing": track["frames_agreeing"]},
            "thresholds": {"srp_peak": s.min_srp_peak, "srp_z": s.min_srp_z, "srp_contrast": s.min_contrast,
                           "second_peak_ratio": s.max_second_peak_ratio,
                           "coherent_bandwidth_hz": s.min_coherent_bandwidth_hz,
                           "tdoa_residual_us": s.max_tdoa_residual_us, "frames_agreeing": self.tracker.min_frames},
        }

    def _events(self, gate: dict, frame: dict, bearing: dict, identity_score, times: dict) -> None:
        if gate["onset"]:
            self.stats["candidates"] += 1
            self.events.start(times["frame_center_s"], times["time_quality"])
            self._last_event_push = 0.0
        if gate["candidate"] and self.events.current is not None:
            usable = bearing["bearing_local_deg"] if (bearing["calibrated"] or bearing["simulated_geometry"]) else None
            self.events.add_frame(frame["harmonic"], gate["level_above_floor_db"], usable, identity_score)
            if gate["onset"] or times["frame_end_s"] - self._last_event_push >= 1.0:
                self._last_event_push = times["frame_end_s"]
                self.state.add_event(self.events.summary())
            if gate["onset"] and self.event_log is not None:
                onset = self.events.summary()
                onset["record"] = "candidate_onset"
                self.event_log.write(onset)
            if usable is not None and self.observation_log is not None:
                self.observation_log.write({
                    "schema": OBSERVATION_SCHEMA, "node_id": self.cfg.node_id, "mode": self.mode,
                    "event_id": self.events.current["event_id"], "time_s": times["frame_center_s"],
                    "time_quality": times["time_quality"], "bearing_local_deg": usable,
                    "bearing_site_deg": bearing["bearing_site_deg"], "bearing_sigma_deg": bearing["sigma_deg"],
                    "f0_hz": frame["harmonic"]["f0_hz"], "harmonic_score": frame["harmonic"]["score"],
                    "calibrated": bearing["calibrated"], "synthetic": self.mode == "SIMULATED"})
        if gate["offset"] and self.events.current is not None:
            finished = self.events.finish(times["frame_end_s"])
            self.state.add_event(finished)
            if self.event_log is not None:
                self.event_log.write(finished)
