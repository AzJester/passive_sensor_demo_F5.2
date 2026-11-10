"""Candidate gate, bearing stability gate, event records, and configuration rules."""
import json
import tempfile
import unittest
from pathlib import Path

from pasensor.config import ConfigError, DEFAULTS, load_config, validate
from pasensor.detector import BearingTracker, EventBuilder, EventGate
from tests.helpers import ROOT, make_config

HOP = 0.128


def frame(level=-60.0, score=0.0, count=0, status="OK", f0=None):
    return {"audio": {"status": status}, "band_level_dbfs": level,
            "harmonic": {"score": score, "count": count, "f0_hz": f0, "power_fraction": 0.0, "mean_prominence_db": 0.0}}


def settings(**changes):
    out = dict(DEFAULTS["detection"])
    out.update(background_seconds=1.0, **changes)
    return out


def primed_gate(**changes):
    gate = EventGate(settings(**changes), HOP)
    for _ in range(10):
        gate.update(frame(-70.0))
    return gate


class EventGateTests(unittest.TestCase):
    def test_baselining_until_enough_ambient(self):
        gate = EventGate(settings(), HOP)
        states = [gate.update(frame(-70.0))["state"] for _ in range(8)]
        self.assertEqual(states[:7], ["BASELINING"] * 7)
        self.assertEqual(states[7], "LISTENING")                # 8 hops is the first count that covers 1.0 s
        self.assertAlmostEqual(gate.floor, -70.0)

    def test_onset_needs_k_of_m_and_release_has_hysteresis(self):
        gate = primed_gate()
        loud = frame(-40.0, score=0.9, count=10)
        results = [gate.update(loud) for _ in range(4)]
        self.assertEqual([r["candidate"] for r in results], [False, False, False, True])
        self.assertTrue(results[3]["onset"])
        # One missed frame does not end the candidate.
        self.assertTrue(gate.update(frame(-70.0))["candidate"])
        ended = [gate.update(frame(-70.0)) for _ in range(6)]
        self.assertTrue(any(r["offset"] for r in ended))
        self.assertFalse(ended[-1]["candidate"])

    def test_each_criterion_blocks_and_is_reported(self):
        gate = primed_gate()
        self.assertIn("LEVEL_BELOW_MARGIN", gate.update(frame(-68.0, score=0.9, count=10))["blocked_by"])
        self.assertIn("TOO_FEW_HARMONICS", gate.update(frame(-40.0, score=0.9, count=2))["blocked_by"])
        self.assertIn("HARMONIC_SCORE_LOW", gate.update(frame(-40.0, score=0.1, count=10))["blocked_by"])
        self.assertEqual(gate.update(frame(-40.0, score=0.9, count=10))["blocked_by"], [])

    def test_loud_single_tone_never_becomes_candidate(self):
        gate = primed_gate()
        for _ in range(30):
            self.assertFalse(gate.update(frame(-25.0, score=0.12, count=1))["candidate"])

    def test_fault_and_overflow_frames_do_not_count_or_move_floor(self):
        gate = primed_gate()
        floor = gate.floor
        for _ in range(20):
            r = gate.update(frame(-10.0, score=0.9, count=10, status="CLIPPING"))
            self.assertFalse(r["qualifies"])
            self.assertIn("AUDIO_FAULT", r["blocked_by"])
        self.assertIn("OVERFLOW", gate.update(frame(-10.0, score=0.9, count=10), overflow=True)["blocked_by"])
        self.assertEqual(gate.floor, floor)

    def test_floor_ignores_short_loud_events_and_follows_slow_change(self):
        gate = EventGate(settings(floor_window_seconds=20.0), HOP)
        for _ in range(100):
            gate.update(frame(-70.0))
        for _ in range(8):                                       # one second burst
            gate.update(frame(-30.0))
        self.assertAlmostEqual(gate.floor, -70.0, delta=0.5)
        for _ in range(200):                                     # ambient rises and stays
            gate.update(frame(-55.0))
        self.assertAlmostEqual(gate.floor, -55.0, delta=0.5)

    def test_reset_window_clears_persistence(self):
        gate = primed_gate()
        for _ in range(3):
            gate.update(frame(-40.0, score=0.9, count=10))
        gate.reset_window()
        self.assertFalse(gate.update(frame(-40.0, score=0.9, count=10))["candidate"])


class BearingTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = BearingTracker({"window_frames": 5, "min_frames": 3, "max_spread_deg": 15.0})

    def test_needs_agreement_before_release(self):
        self.assertFalse(self.tracker.update(100.0)["stable"])
        self.assertFalse(self.tracker.update(102.0)["stable"])
        self.assertTrue(self.tracker.update(101.0)["stable"])

    def test_scattered_estimates_are_not_released(self):
        results = [self.tracker.update(a)["stable"] for a in (10.0, 200.0, 95.0, 310.0, 150.0, 40.0)]
        self.assertFalse(any(results))

    def test_missing_frame_releases_nothing(self):
        for a in (100.0, 101.0, 102.0):
            self.tracker.update(a)
        self.assertFalse(self.tracker.update(None)["stable"])    # never repeat an old value

    def test_follows_a_moving_source_and_wraps(self):
        angles = [350.0 + 3.0 * i for i in range(10)]            # crosses 0 degrees
        results = [self.tracker.update(a % 360.0)["stable"] for a in angles]
        self.assertEqual(results[:2], [False, False])
        self.assertTrue(all(results[2:]))


class EventBuilderTests(unittest.TestCase):
    def test_summary_statistics(self):
        builder = EventBuilder("N1", "SIMULATED", "abc")
        builder.start(1000.0, "synthetic")
        harmonic = {"f0_hz": 160.0, "score": 0.8}
        for bearing in (350.0, None, 10.0, None):
            builder.add_frame(harmonic, 12.0, bearing, None)
        open_view = builder.summary()
        self.assertTrue(open_view["open"])
        self.assertNotIn("_bearings", open_view)
        done = builder.finish(1004.0)
        self.assertEqual(done["duration_s"], 4.0)
        self.assertEqual(done["bearing"]["frames_with_bearing"], 2)
        self.assertAlmostEqual(done["bearing"]["unavailable_fraction"], 0.5)
        self.assertAlmostEqual(min(done["bearing"]["mean_deg"], 360 - done["bearing"]["mean_deg"]), 0.0, places=6)
        self.assertEqual(done["identity"], {"available": False})
        self.assertIn("Not an identification", done["label"])
        self.assertIsNone(builder.summary())
        json.dumps(done, allow_nan=False)


class ConfigTests(unittest.TestCase):
    def test_example_file_is_valid_and_uncalibrated(self):
        cfg = load_config(ROOT / "config" / "node.example.json")
        self.assertFalse(cfg.calibrated)
        self.assertEqual(cfg["device"]["raw_indices"], [2, 3, 4, 5])
        self.assertEqual(len(cfg.sha256), 64)

    def test_unknown_keys_are_errors(self):
        for raw in ({"node_idd": "x"}, {"detection": {"harmonic_treshold": 0.3}}, {"processing": {"dsp": {"bandhz": [1, 2]}}}):
            with self.assertRaises(ConfigError):
                validate(raw)

    def test_bad_values_are_errors(self):
        bad = [{"device": {"raw_indices": [2, 2, 4, 5]}}, {"device": {"raw_indices": [2, 3, 4, 6]}},
               {"device": {"raw_indices": [2, 3, 4]}}, {"array": {"temperature_c": 99}},
               {"processing": {"hop_frames": 3000}}, {"detection": {"onset_frames": 9}},
               {"calibration": {"geometry_validated": "yes"}}, {"health": {"restart_backoff_s": []}},
               {"array": {"geometry_m": [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]]}}, {"schema": "something-else"}]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                validate(raw)

    def test_revision_a_configuration_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(json.dumps({
                "node_id": "ACOUSTIC-01", "sample_rate": 16000, "capture_channels": 6, "raw_indices": [2, 3, 4, 5],
                "geometry_m": [[0.033, -0.033, 0], [0.033, 0.033, 0], [-0.033, 0.033, 0], [-0.033, -0.033, 0]],
                "geometry_validated": False, "calibration_record": "", "temperature_c": 20, "local_angle_offset_deg": 0,
                "background_seconds": 10, "minimum_db_above_background": 6, "harmonic_threshold": 0.25, "persistence_frames": 4}))
            cfg = load_config(path)
        self.assertTrue(cfg.upgraded_from_v1)
        self.assertEqual(cfg["detection"]["minimum_db_above_floor"], 6)

    def calibration_case(self, record_changes=None, config_changes=None, write_record=True):
        with tempfile.TemporaryDirectory() as tmp:
            record = {"schema": "pasensor.calibration-record.v1", "evidence_kind": "physical", "node_id": "ACOUSTIC-01",
                      "raw_indices": [2, 3, 4, 5], "geometry_m": DEFAULTS["array"]["geometry_m"],
                      "result": {"passed": True, "mirror": False, "local_angle_offset_deg": 37.0}}
            record.update(record_changes or {})
            if write_record:
                (Path(tmp) / "cal.json").write_text(json.dumps(record))
            calibration = {"geometry_validated": True, "record": "cal.json", "local_angle_offset_deg": 37.0,
                           "mirror": False, "bearing_sigma_deg": 6.0}
            calibration.update(config_changes or {})
            return validate({"calibration": calibration}, base_dir=Path(tmp))

    def test_calibration_requires_matching_physical_record(self):
        self.assertTrue(self.calibration_case().calibrated)
        failing = [dict(write_record=False), dict(record_changes={"evidence_kind": "synthetic"}),
                   dict(record_changes={"node_id": "OTHER"}), dict(record_changes={"raw_indices": [3, 2, 4, 5]}),
                   dict(record_changes={"result": {"passed": False, "mirror": False, "local_angle_offset_deg": 37.0}}),
                   dict(config_changes={"local_angle_offset_deg": 40.0}), dict(config_changes={"mirror": True}),
                   dict(config_changes={"bearing_sigma_deg": None}), dict(config_changes={"record": ""})]
        for case in failing:
            with self.subTest(case=case), self.assertRaises(ConfigError):
                self.calibration_case(**case)

    def test_config_hash_changes_with_content(self):
        self.assertNotEqual(make_config().sha256, make_config(detection={"min_harmonics": 5}).sha256)


if __name__ == "__main__":
    unittest.main()
