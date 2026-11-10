"""Shared status between the processing thread and the dashboard server.

The snapshot adds the age of the newest result. When the result is too old
the snapshot replaces the state with STALE and removes the bearing and the
candidate flag, so the display can never show an old value as current.

operator_view() turns a status into the plain-language labels the dashboard
shows. Keeping that logic here, in Python, lets the test suite check what an
operator would see without driving a browser.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque

from .detector import BLOCK_TEXT
from .dsp import REASON_TEXT

TERMINAL_STATES = ("REPLAY_COMPLETE", "STOPPED", "FAILED")
MODE_LABEL = {
    "LIVE": "LIVE AUDIO",
    "REPLAY": "RECORDING REPLAY",
    "SIMULATED": "SIMULATED DATA - NOT REAL AUDIO",
}


class SharedState:
    def __init__(self, stale_after_s: float = 2.5, history_frames: int = 600, clock=time.time):
        self._lock = threading.Lock()
        self._clock = clock
        self.stale_after = float(stale_after_s)
        self._status = {"state": "STARTING", "mode": "", "seq": 0, "published_wall_s": clock()}
        self._history: deque = deque(maxlen=history_frames)
        self._events: deque = deque(maxlen=200)
        self._seq = 0

    def publish(self, status: dict, history_row: dict | None = None) -> None:
        with self._lock:
            self._seq += 1
            status = dict(status)
            status["seq"] = self._seq
            status["published_wall_s"] = self._clock()
            self._status = status
            if history_row is not None:
                self._history.append(history_row)

    def add_event(self, event: dict) -> None:
        """Insert or replace an event record (matched by event_id)."""
        with self._lock:
            for i, existing in enumerate(self._events):
                if existing.get("event_id") == event.get("event_id") and event.get("event_id"):
                    self._events[i] = event
                    return
            self._events.appendleft(event)

    def snapshot(self) -> dict:
        with self._lock:
            status = copy.deepcopy(self._status)
            events = copy.deepcopy(list(self._events))
        age = max(self._clock() - status.get("published_wall_s", 0.0), 0.0)
        status["age_s"] = age
        status["stale"] = False
        if status.get("state") not in TERMINAL_STATES and age > self.stale_after:
            status["stale"] = True
            status["state_before_stale"] = status.get("state")
            status["state"] = "STALE"
            # Nothing measured before the stall is current any more. Remove all of it, not only the bearing.
            calibrated = bool((status.get("bearing") or {}).get("calibrated"))
            status["bearing"] = {"available": False, "bearing_local_deg": None, "bearing_site_deg": None,
                                 "compass_deg": None, "calibrated": calibrated, "reasons": ["STALE"]}
            status["detection"] = {"candidate": False, "event_id": None, "blocked_by": []}
            status["audio"] = None
            status["spectrum"] = None
            if isinstance(status.get("acquisition"), dict):
                status["acquisition"]["status"] = "STALLED"
        status["operator"] = operator_view(status)
        return {"status": status, "events": events}

    def history(self) -> list:
        with self._lock:
            return list(self._history)

    def events(self) -> list:
        with self._lock:
            return copy.deepcopy(list(self._events))


def operator_view(status: dict) -> dict:
    """Labels for the dashboard. Pure function of one status dictionary."""
    state = status.get("state", "STARTING")
    mode = status.get("mode", "")
    acquisition = status.get("acquisition") or {}
    audio = status.get("audio") or {}
    detection = status.get("detection") or {}
    bearing = status.get("bearing") or {}
    identity = status.get("identity") or {}
    flags = []
    if state == "STALE":
        flags.append("STALE")
    if acquisition.get("status") in ("OVERFLOW", "DISCONNECTED", "STALLED", "FAULT"):
        flags.append(acquisition["status"])
    if audio.get("status") not in (None, "OK"):
        flags.append(audio["status"])
    flags.extend(audio.get("warnings") or [])
    if status.get("recording", {}).get("enabled"):
        flags.append("RAW RECORDING ON")

    live_result = state in ("BASELINING", "LISTENING", "CANDIDATE")
    if state == "STALE":
        headline, severity = f"INPUT STALE. No new audio result for {status.get('age_s', 0):.0f} s.", "fault"
    elif state in ("FAULT", "RECONNECTING", "FAILED"):
        text = acquisition.get("last_fault") or status.get("message") or "acquisition fault"
        prefix = "RECONNECTING" if state == "RECONNECTING" else ("STOPPED AFTER REPEATED FAULTS" if state == "FAILED" else "ACQUISITION FAULT")
        headline, severity = f"{prefix}. {text}", "fault"
    elif state == "REPLAY_COMPLETE":
        headline, severity = "Replay complete. No current data.", "notice"
    elif state == "STOPPED":
        headline, severity = "Stopped.", "notice"
    elif state == "STARTING":
        headline, severity = "Starting. Waiting for the first full audio block.", "notice"
    elif acquisition.get("status") == "OVERFLOW":
        headline, severity = "Samples were lost (overflow). This block is not analysed.", "warning"
    elif audio.get("status") not in (None, "OK"):
        headline, severity = f"AUDIO FAULT: {audio['status'].replace('_', ' ').lower()}. No detection or bearing from this block.", "warning"
    elif state == "CANDIDATE":
        headline, severity = "Rotor-like sound candidate (heuristic).", "candidate"
    elif state == "BASELINING":
        headline, severity = "Measuring the ambient floor. Candidates are not raised yet.", "notice"
    else:
        headline, severity = "Listening. No candidate.", "ok"

    if not live_result:
        candidate = {"active": False, "label": "Not assessed", "caption": "No current audio result."}
    elif detection.get("candidate"):
        candidate = {"active": True, "label": "ROTOR-LIKE SOUND CANDIDATE",
                     "caption": "Heuristic match on level and harmonic structure. Not an identification. "
                                "Fans, engines, and other machinery can also match."}
    else:
        blocked = [BLOCK_TEXT.get(code, code) for code in detection.get("blocked_by") or []]
        candidate = {"active": False, "label": "No candidate",
                     "caption": ("This frame does not qualify: " + "; ".join(blocked) + ".") if blocked
                     else "This frame qualifies. Waiting for enough qualifying frames."}

    badges = []
    if mode == "SIMULATED":
        badges.append("SIMULATED GEOMETRY")
    elif not bearing.get("calibrated"):
        badges.append("UNCALIBRATED")
    value = bearing.get("bearing_local_deg")
    if live_result and bearing.get("available") and value is not None:
        label = f"{value:.0f} deg local"
        caption = {
            "CANDIDATE_HARMONICS": "Apparent direction of the candidate's harmonic sound. No range is measured.",
            "CANDIDATE_BAND": "Apparent direction of the loudest coherent sound while the candidate is active. No range is measured.",
        }.get(bearing.get("subject"), "Apparent direction of the loudest coherent sound in the analysis band. "
                                      "No candidate is active, so this may be ordinary background sound. No range is measured.")
        if bearing.get("compass_deg") is not None:
            label += f" / {bearing['compass_deg']:.0f} deg compass"
        bearing_view = {"show_needle": True, "label": label, "caption": caption, "reasons": []}
    else:
        reasons = [REASON_TEXT.get(code, code) for code in bearing.get("reasons") or []]
        if not live_result and not reasons:
            reasons = ["No current audio result."]
        bearing_view = {"show_needle": False, "label": "BEARING UNAVAILABLE",
                        "caption": "No bearing is shown rather than an unreliable one.", "reasons": reasons}
    bearing_view["badges"] = badges

    if identity.get("available"):
        kind = identity.get("score_kind", "uncalibrated model score")
        identity_view = {"label": f"{identity.get('score', 0):.2f} ({kind})",
                         "caption": f"Model {identity.get('model_id', 'unknown')}. See the model card for its measured limits."}
    else:
        identity_view = {"label": "Not assessed",
                         "caption": identity.get("reason", "No trained classifier is installed. The software does not judge what the sound is.")}

    return {"mode_label": MODE_LABEL.get(mode, mode or "UNKNOWN MODE"), "headline": headline, "severity": severity,
            "flags": flags, "candidate": candidate, "bearing": bearing_view, "identity": identity_view}
