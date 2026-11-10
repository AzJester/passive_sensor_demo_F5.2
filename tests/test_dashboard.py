"""What the operator is shown: status labels, stale handling, the local server, and the command line."""
import http.client
import json
import re
import subprocess
import sys
import threading
import unittest

import pasensor

from pasensor.server import WEB_ROOT, make_server
from pasensor.state import SharedState, operator_view
from tests.helpers import ROOT, late_rotor, make_config, run_pipeline, sim_factory


def status(**changes):
    base = {
        "state": "LISTENING", "mode": "LIVE", "age_s": 0.1,
        "acquisition": {"status": "OK", "last_fault": None},
        "audio": {"status": "OK", "warnings": []},
        "detection": {"candidate": False, "blocked_by": ["LEVEL_BELOW_MARGIN"]},
        "bearing": {"available": False, "bearing_local_deg": None, "compass_deg": None, "calibrated": False, "reasons": ["LOW_COHERENCE"]},
        "identity": {"available": False, "reason": "No trained classifier is installed."},
        "recording": {"enabled": False},
    }
    base.update(changes)
    return base


class OperatorViewTests(unittest.TestCase):
    def test_mode_is_always_labelled(self):
        self.assertEqual(operator_view(status(mode="LIVE"))["mode_label"], "LIVE AUDIO")
        self.assertEqual(operator_view(status(mode="REPLAY"))["mode_label"], "RECORDING REPLAY")
        self.assertIn("NOT REAL AUDIO", operator_view(status(mode="SIMULATED"))["mode_label"])

    def test_listening_explains_why_no_candidate(self):
        view = operator_view(status())
        self.assertEqual(view["severity"], "ok")
        self.assertEqual(view["candidate"]["label"], "No candidate")
        self.assertIn("ambient floor", view["candidate"]["caption"])
        self.assertFalse(view["bearing"]["show_needle"])
        self.assertEqual(view["bearing"]["label"], "BEARING UNAVAILABLE")
        self.assertTrue(view["bearing"]["reasons"][0].startswith("The microphones do not share"))
        self.assertEqual(view["bearing"]["badges"], ["UNCALIBRATED"])

    def test_candidate_wording_never_claims_identity_or_probability(self):
        view = operator_view(status(state="CANDIDATE", detection={"candidate": True, "blocked_by": []}))
        text = json.dumps(view).lower()
        self.assertEqual(view["severity"], "candidate")
        self.assertIn("heuristic", view["headline"].lower())
        self.assertIn("not an identification", text)
        for word in ("drone detected", "probability", "confidence", "confirmed", "threat", "range"):
            self.assertNotIn(word, text.replace("no range is measured", ""))
        self.assertEqual(view["identity"]["label"], "Not assessed")

    def test_bearing_shown_only_with_value_and_badges_tell_its_standing(self):
        bearing = {"available": True, "bearing_local_deg": 123.4, "compass_deg": None, "calibrated": True, "reasons": []}
        view = operator_view(status(bearing=bearing))
        self.assertTrue(view["bearing"]["show_needle"])
        self.assertEqual(view["bearing"]["label"], "123 deg local")
        self.assertEqual(view["bearing"]["badges"], [])
        self.assertIn("No range", view["bearing"]["caption"])
        self.assertIn("No candidate is active", view["bearing"]["caption"])     # a bearing without a candidate says so
        tied = operator_view(status(state="CANDIDATE", detection={"candidate": True, "blocked_by": []},
                                    bearing={**bearing, "subject": "CANDIDATE_HARMONICS"}))
        self.assertIn("candidate's harmonic sound", tied["bearing"]["caption"])
        simulated = operator_view(status(mode="SIMULATED", bearing={**bearing, "calibrated": False}))
        self.assertEqual(simulated["bearing"]["badges"], ["SIMULATED GEOMETRY"])
        uncalibrated = operator_view(status(bearing={**bearing, "calibrated": False}))
        self.assertEqual(uncalibrated["bearing"]["badges"], ["UNCALIBRATED"])
        compass = operator_view(status(bearing={**bearing, "compass_deg": 45.0}))
        self.assertIn("45 deg compass", compass["bearing"]["label"])

    def test_faults_take_over_the_headline_and_hide_results(self):
        cases = {
            "STALE": status(state="STALE", age_s=7.0),
            "FAULT": status(state="FAULT", acquisition={"status": "FAULT", "last_fault": "device unplugged"}, bearing=None, detection=None, audio=None),
            "RECONNECTING": status(state="RECONNECTING", acquisition={"status": "DISCONNECTED", "last_fault": "device unplugged"}, bearing=None, detection=None, audio=None),
            "FAILED": status(state="FAILED", bearing=None, detection=None, audio=None),
        }
        for name, value in cases.items():
            with self.subTest(state=name):
                view = operator_view(value)
                self.assertEqual(view["severity"], "fault")
                self.assertFalse(view["bearing"]["show_needle"])
                self.assertEqual(view["candidate"]["label"], "Not assessed")
        self.assertIn("STALE", operator_view(cases["STALE"])["flags"])
        self.assertIn("device unplugged", operator_view(cases["FAULT"])["headline"])

    def test_overflow_clipping_and_recording_are_flagged(self):
        view = operator_view(status(acquisition={"status": "OVERFLOW"}))
        self.assertEqual(view["severity"], "warning")
        self.assertIn("OVERFLOW", view["flags"])
        view = operator_view(status(audio={"status": "CLIPPING", "warnings": ["DC_OFFSET"]}))
        self.assertEqual(view["severity"], "warning")
        self.assertIn("clipping", view["headline"])
        self.assertEqual(view["flags"], ["CLIPPING", "DC_OFFSET"])
        self.assertIn("RAW RECORDING ON", operator_view(status(recording={"enabled": True}))["flags"])

    def test_model_score_is_labelled_as_a_score(self):
        view = operator_view(status(identity={"available": True, "score": 0.83, "score_kind": "uncalibrated model score", "model_id": "m1"}))
        self.assertEqual(view["identity"]["label"], "0.83 (uncalibrated model score)")


class SharedStateTests(unittest.TestCase):
    def test_stale_result_loses_bearing_and_candidate(self):
        now = [1000.0]
        state = SharedState(stale_after_s=2.5, clock=lambda: now[0])
        state.publish(status(state="CANDIDATE", detection={"candidate": True, "event_id": "e1", "blocked_by": []},
                             bearing={"available": True, "bearing_local_deg": 45.0, "bearing_site_deg": 50.0, "compass_deg": 40.0,
                                      "calibrated": True, "reasons": []}))
        fresh = state.snapshot()["status"]
        self.assertFalse(fresh["stale"])
        self.assertTrue(fresh["operator"]["bearing"]["show_needle"])
        now[0] += 3.0
        stale = state.snapshot()["status"]
        self.assertEqual(stale["state"], "STALE")
        self.assertEqual(stale["state_before_stale"], "CANDIDATE")
        self.assertIsNone(stale["bearing"]["bearing_local_deg"])
        self.assertIsNone(stale["bearing"]["compass_deg"])
        self.assertEqual(stale["bearing"]["reasons"], ["STALE"])
        self.assertFalse(stale["detection"]["candidate"])
        self.assertIsNone(stale["audio"])                         # old levels and evidence are removed too
        self.assertIsNone(stale["spectrum"])
        self.assertEqual(stale["acquisition"]["status"], "STALLED")
        self.assertEqual(stale["operator"]["candidate"]["label"], "Not assessed")
        self.assertFalse(stale["operator"]["bearing"]["show_needle"])
        self.assertIn("No new audio result", stale["operator"]["bearing"]["reasons"][0])

    def test_terminal_states_do_not_go_stale_and_snapshots_are_isolated(self):
        now = [0.0]
        state = SharedState(clock=lambda: now[0])
        state.publish({"state": "REPLAY_COMPLETE", "mode": "REPLAY"})
        now[0] += 100.0
        snap = state.snapshot()
        self.assertEqual(snap["status"]["state"], "REPLAY_COMPLETE")
        snap["status"]["state"] = "CHANGED"
        self.assertEqual(state.snapshot()["status"]["state"], "REPLAY_COMPLETE")

    def test_events_update_in_place(self):
        state = SharedState()
        state.add_event({"event_id": "a", "open": True})
        state.add_event({"event_id": "b", "open": True})
        state.add_event({"event_id": "a", "open": False})
        self.assertEqual([(e["event_id"], e["open"]) for e in state.events()], [("b", True), ("a", False)])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = make_config()
        _final, cls.state, _ = run_pipeline(cfg, "SIMULATED", sim_factory(cfg, late_rotor(), limit_chunks=50))
        cls.server = make_server(cls.state, {"node_id": "T"}, 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, method="GET", host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response, body

    def test_binds_to_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_status_is_strict_json_with_operator_view(self):
        response, body = self.request("/api/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(body, parse_constant=lambda c: self.fail(f"non-finite number {c} in JSON"))
        self.assertEqual(payload["status"]["mode"], "SIMULATED")
        self.assertIn("NOT REAL AUDIO", payload["status"]["operator"]["mode_label"])
        self.assertEqual(payload["events"][0]["mode"], "SIMULATED")
        history = json.loads(self.request("/api/history")[1])["history"]
        self.assertGreater(len(history), 40)
        self.assertEqual(json.loads(self.request("/api/info")[1]), {"node_id": "T"})
        self.assertIn("events", json.loads(self.request("/api/events")[1]))

    def test_security_headers(self):
        response, _ = self.request("/")
        self.assertEqual(response.status, 200)
        policy = response.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("unsafe", policy)
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_foreign_host_header_is_refused(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1"):
            response, _ = self.request("/api/status", host=host)
            self.assertEqual(response.status, 421)
        self.assertEqual(self.request("/api/status", host=f"localhost:{self.port}")[0].status, 200)

    def test_read_only_and_no_path_traversal(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(self.request("/api/status", method=method)[0].status, 405)
        for path in ("/../config/node.example.json", "/app.js/../../cli.py", "/static/x", "/api/unknown"):
            self.assertEqual(self.request(path)[0].status, 404)
        head, body = self.request("/app.css", method="HEAD")
        self.assertEqual((head.status, body), (200, b""))

    def test_static_files_are_served(self):
        for path, kind in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")):
            response, body = self.request(path)
            self.assertEqual(response.status, 200)
            self.assertTrue(response.getheader("Content-Type").startswith(kind))
            self.assertGreater(len(body), 500)


class DashboardSourceTests(unittest.TestCase):
    """Static checks on the page the operator loads."""

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    def test_page_loads_nothing_from_other_origins_and_has_no_inline_code(self):
        self.assertEqual(re.findall(r"""(?:src|href)=["'](?!/)[^"']+""", self.html), [])
        self.assertNotRegex(self.html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(self.html, r"<style|style=|\bon[a-z]+=")
        self.assertNotRegex(self.js, r"https?://(?!www\.w3\.org/2000/svg)")

    def test_script_never_injects_markup_or_evaluates_text(self):
        for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(risky, self.js)

    def test_page_states_its_limits(self):
        for phrase in ("heuristic score, not a probability", "direction only, no range", "identifies no aircraft"):
            self.assertIn(phrase, self.html)
        self.assertIn("DASHBOARD LOST CONTACT WITH THE SENSOR PROCESS", self.js)
        self.assertNotRegex(self.html + self.js, r"(?i)range ring|accuracy circle")

    def test_script_only_reads(self):
        self.assertNotRegex(self.js, r"""method:\s*["'](POST|PUT|DELETE|PATCH)""")
        self.assertEqual(sorted(set(re.findall(r'getJson\("([^"]+)"\)', self.js))), ["/api/history", "/api/info", "/api/status"])


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args, script=None, timeout=120):
        command = [sys.executable, str(ROOT / script)] if script else [sys.executable, "-m", "pasensor"]
        return subprocess.run(command + list(args), cwd=ROOT, capture_output=True, text=True, timeout=timeout)

    def test_version_and_help(self):
        self.assertIn(f"pasensor {pasensor.__version__}", self.run_cli("version").stdout)
        self.assertEqual(self.run_cli("run", "--help").returncode, 0)

    def test_mode_is_required_and_announced(self):
        self.assertNotEqual(self.run_cli("run").returncode, 0)
        done = self.run_cli("run", "--mode", "simulated", "--no-server", "--replay-speed", "max", "--duration", "3")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("MODE: SIMULATED DATA - NOT REAL AUDIO", done.stdout)
        summary = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["final_state"], "STOPPED")
        self.assertGreater(summary["frames_analysed"], 10)

    def test_argument_errors_exit_with_code_2(self):
        self.assertEqual(self.run_cli("run", "--mode", "replay", "--no-server").returncode, 2)
        self.assertEqual(self.run_cli("run", "--mode", "live", "--wav", "x.wav", "--no-server").returncode, 2)
        missing = self.run_cli("run", "--mode", "simulated", "--no-server", "--config", "does-not-exist.json")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("Configuration error", missing.stderr)

    def test_revision_a_entry_point_still_works(self):
        self.assertNotEqual(self.run_cli(script="app.py").returncode, 0)
        sys.path.insert(0, str(ROOT))
        try:
            import app
            self.assertEqual(app.translate(["--demo"]), ["run", "--mode", "simulated", "--port", "8765"])
            self.assertEqual(app.translate(["--live", "--device", "3", "--log", "e.jsonl"]),
                             ["run", "--mode", "live", "--port", "8765", "--device", "3", "--event-log", "e.jsonl"])
            self.assertEqual(app.translate(["--wav", "t.wav"])[:5], ["run", "--mode", "replay", "--port", "8765"])
        finally:
            sys.path.remove(str(ROOT))


if __name__ == "__main__":
    unittest.main()
