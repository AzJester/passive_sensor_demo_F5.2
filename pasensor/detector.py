"""Candidate gate, bearing stability gate, and event records.

The gate marks a "rotor-like sound candidate". It is a heuristic built from
level and harmonic structure. Fans, engines, generators, and mowers can raise
it. It is not an identification and it has no measured error rates yet.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone

import numpy as np

from . import EVENT_SCHEMA
from .dsp import AUDIO_FAULTS
from .geometry import circular_mean_deg, circular_std_deg, wrap180

BLOCK_TEXT = {
    "AUDIO_FAULT": "audio block unusable",
    "OVERFLOW": "samples were lost",
    "FLOOR_NOT_READY": "still measuring the ambient floor",
    "LEVEL_BELOW_MARGIN": "level is not far enough above the ambient floor",
    "TOO_FEW_HARMONICS": "too few harmonics stand out",
    "HARMONIC_SCORE_LOW": "harmonic comb score is below the threshold",
}


class EventGate:
    """K-of-M persistence over frames that pass level and harmonic tests.

    The ambient floor is a low percentile of the band level over a rolling
    window, so brief loud sounds do not raise it and it follows slow changes
    in the background. A source that is present for most of the window will
    raise the floor and can mask itself. The dashboard shows the floor so an
    operator can see that happen.
    """

    def __init__(self, settings: dict, hop_seconds: float):
        self.s = settings
        self.hop = float(hop_seconds)
        self.levels: deque[float] = deque(maxlen=max(int(round(settings["floor_window_seconds"] / self.hop)), 1))
        self.needed = max(int(math.ceil(settings["background_seconds"] / self.hop)), 1)
        self.window: deque[bool] = deque(maxlen=int(settings["window_frames"]))
        self.active = False
        self.floor: float | None = None

    def reset_window(self) -> None:
        """Forget recent qualifying frames, for example after lost samples."""
        self.window.clear()

    def update(self, frame: dict, overflow: bool = False) -> dict:
        s = self.s
        fault = frame["audio"]["status"] in AUDIO_FAULTS
        level = float(frame["band_level_dbfs"])
        blocked = []
        if overflow:
            blocked.append("OVERFLOW")
        if fault:
            blocked.append("AUDIO_FAULT")
        usable = not blocked and math.isfinite(level)
        if usable:
            self.levels.append(level)
            if len(self.levels) >= self.needed:
                self.floor = float(np.percentile(np.fromiter(self.levels, dtype=float), s["floor_percentile"]))
        ready = self.floor is not None
        above = (level - self.floor) if ready and usable else None
        harmonic = frame["harmonic"]
        if usable:
            if not ready:
                blocked.append("FLOOR_NOT_READY")
            elif above < s["minimum_db_above_floor"]:
                blocked.append("LEVEL_BELOW_MARGIN")
            if harmonic["count"] < s["min_harmonics"]:
                blocked.append("TOO_FEW_HARMONICS")
            if harmonic["score"] < s["harmonic_score_threshold"]:
                blocked.append("HARMONIC_SCORE_LOW")
        qualifies = not blocked
        self.window.append(qualifies)
        hits = sum(self.window)
        was_active = self.active
        if not self.active and hits >= s["onset_frames"]:
            self.active = True
        elif self.active and hits <= s["release_frames"]:
            self.active = False
        return {
            "state": "BASELINING" if not ready else ("CANDIDATE" if self.active else "LISTENING"),
            "candidate": self.active, "onset": self.active and not was_active,
            "offset": was_active and not self.active, "qualifies": qualifies, "blocked_by": blocked,
            "floor_dbfs": self.floor, "floor_ready": ready, "level_above_floor_db": above,
            "floor_seconds": len(self.levels) * self.hop,
            "qualifying_frames": hits, "window_frames": len(self.window),
        }


class BearingTracker:
    """Release a bearing only when recent frame estimates agree with it.

    A single frame can produce a plausible direction from background noise.
    Requiring agreement across frames removes most of those. The released
    value is always the current frame's own estimate. Old values are never
    repeated: when the current frame has no estimate, nothing is released.
    """

    def __init__(self, settings: dict):
        self.window_frames = int(settings["window_frames"])
        self.min_frames = int(settings["min_frames"])
        self.max_spread = float(settings["max_spread_deg"])
        self.history: deque[float | None] = deque(maxlen=self.window_frames)

    def reset(self) -> None:
        self.history.clear()

    def update(self, azimuth_deg: float | None) -> dict:
        self.history.append(azimuth_deg)
        if azimuth_deg is None:
            return {"stable": False, "frames_agreeing": 0, "spread_deg": None}
        deviations = [abs(float(wrap180(a - azimuth_deg))) for a in self.history if a is not None]
        agreeing = [d for d in deviations if d <= self.max_spread]
        return {"stable": len(agreeing) >= self.min_frames, "frames_agreeing": len(agreeing),
                "spread_deg": max(agreeing) if agreeing else None}


def utc_iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventBuilder:
    """Accumulate evidence for one candidate from onset to offset."""

    def __init__(self, node_id: str, mode: str, config_sha256: str):
        self.node_id, self.mode, self.config_sha256 = node_id, mode, config_sha256
        self.sequence = 0
        self.current: dict | None = None

    def start(self, time_s: float, time_quality: str) -> dict:
        self.sequence += 1
        stamp = datetime.fromtimestamp(time_s, tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.current = {
            "schema": EVENT_SCHEMA, "record": "candidate", "event_id": f"{self.node_id}-{stamp}-{self.sequence:04d}",
            "node_id": self.node_id, "mode": self.mode, "config_sha256": self.config_sha256,
            "onset_time_s": time_s, "onset_utc": utc_iso(time_s), "time_quality": time_quality,
            "offset_time_s": None, "duration_s": None, "open": True, "frames": 0,
            "_bearings": [], "_f0": [], "_scores": [], "_above": [], "_identity": [],
            "label": "Heuristic rotor-like sound candidate. Not an identification.",
        }
        return self.current

    def add_frame(self, harmonic: dict, above_floor, bearing_deg, identity_score) -> None:
        event = self.current
        if event is None:
            return
        event["frames"] += 1
        if harmonic["f0_hz"] is not None:
            event["_f0"].append(harmonic["f0_hz"])
        event["_scores"].append(harmonic["score"])
        if above_floor is not None:
            event["_above"].append(above_floor)
        if bearing_deg is not None:
            event["_bearings"].append(bearing_deg)
        if identity_score is not None:
            event["_identity"].append(identity_score)

    def summary(self) -> dict | None:
        """Public view of the current event with the working lists reduced to statistics."""
        event = self.current
        if event is None:
            return None
        out = {k: v for k, v in event.items() if not k.startswith("_")}
        bearings = event["_bearings"]
        mean, _ = circular_mean_deg(bearings)
        out["evidence"] = {
            "median_f0_hz": float(np.median(event["_f0"])) if event["_f0"] else None,
            "max_harmonic_score": max(event["_scores"]) if event["_scores"] else None,
            "max_level_above_floor_db": max(event["_above"]) if event["_above"] else None,
        }
        out["bearing"] = {
            "frames_with_bearing": len(bearings),
            "unavailable_fraction": (1.0 - len(bearings) / event["frames"]) if event["frames"] else None,
            "mean_deg": mean, "first_deg": bearings[0] if bearings else None,
            "last_deg": bearings[-1] if bearings else None, "spread_deg": circular_std_deg(bearings),
        }
        out["identity"] = ({"available": True, "max_model_score": max(event["_identity"])}
                           if event["_identity"] else {"available": False})
        return out

    def finish(self, time_s: float) -> dict | None:
        if self.current is None:
            return None
        self.current.update(offset_time_s=time_s, offset_utc=utc_iso(time_s),
                            duration_s=max(time_s - self.current["onset_time_s"], 0.0), open=False)
        out = self.summary()
        self.current = None
        return out
