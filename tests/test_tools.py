"""Commissioning, training, and evaluation tools, exercised on synthetic data only."""
import contextlib
import csv
import io
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from pasensor import classifier as C
from pasensor import ml
from pasensor.calibration import channel_map, channel_report, run_calibration, tap_analysis
from pasensor.cli import main
from pasensor.config import ConfigError, validate
from pasensor.evaluate import evaluate, poisson_rate_ci, report_markdown
from pasensor.simulate import static_clip
from tests.helpers import late_rotor, make_config, quiet_scene, rotor_scene, run_pipeline, sim_factory, six_channel, step_gain, write_wav


def quiet_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class ChannelToolTests(unittest.TestCase):
    def tap_recording(self, order):
        """Six channels of low noise with a loud burst on one capture channel per two-second segment."""
        rng = np.random.default_rng(5)
        audio = rng.normal(0, 0.001, (16000 * 8, 6))
        for segment, channel in enumerate(order):
            start = segment * 32000 + 8000
            burst = rng.normal(0, 0.05, 6000)
            audio[start:start + 6000, :] += 0.15 * burst[:, None]             # every microphone hears a little
            audio[start:start + 6000, channel] += burst                       # the tapped one hears it most
        return audio

    def test_tap_identifies_the_loudest_channel(self):
        result = tap_analysis(self.tap_recording([4, 2, 3, 5])[8000:14000])
        self.assertEqual(result["loudest_channel"], 4)
        self.assertTrue(result["confident"])
        weak = tap_analysis(np.random.default_rng(1).normal(0, 0.001, (6000, 6)))
        self.assertFalse(weak["confident"])

    def test_channel_map_confirms_or_contradicts_the_configuration(self):
        cfg = make_config()
        segments = [(0.5, 1.0), (2.5, 3.0), (4.5, 5.0), (6.5, 7.0)]
        labels = ["MIC1", "MIC2", "MIC3", "MIC4"]
        good = channel_map(self.tap_recording([2, 3, 4, 5]), 16000, segments, labels, cfg)
        self.assertTrue(good["matches_configuration"])
        self.assertEqual(good["problems"], [])
        swapped = channel_map(self.tap_recording([3, 2, 4, 5]), 16000, segments, labels, cfg)
        self.assertFalse(swapped["matches_configuration"])
        self.assertEqual(swapped["suggested_raw_indices"], [3, 2, 4, 5])
        processed = channel_map(self.tap_recording([0, 3, 4, 5]), 16000, segments, labels, cfg)
        self.assertTrue(any("not a configured raw channel" in p for p in processed["problems"]))

    def test_channel_report_exposes_a_delayed_processed_output(self):
        mics = static_clip(40, 10, 30.0, seconds=1.0, kind="noise", snr_db=30)
        audio = six_channel(mics)
        audio[:, 0] = np.roll(mics[:, 0], 64) * 3.0                            # a processed output: 4 ms late, louder
        report = channel_report(audio, 16000, 2)
        by_channel = {c["channel"]: c for c in report["channels"]}
        self.assertAlmostEqual(by_channel[0]["delay_ms_vs_reference"], 4.0, delta=0.3)
        for raw in (2, 3, 4, 5):
            self.assertLess(abs(by_channel[raw]["delay_ms_vs_reference"]), 0.3)
        self.assertGreater(by_channel[0]["rms_dbfs"], by_channel[2]["rms_dbfs"] + 6.0)


class CalibrationTests(unittest.TestCase):
    def make_set(self, tmp, truths, offset, mirror, sidecar=True):
        """Recordings from an array mounted with a rotation and, optionally, a mirrored channel sense."""
        sign = -1.0 if mirror else 1.0
        positions = []
        for truth in truths:
            raw_azimuth = (sign * (truth - offset)) % 360.0
            mics = static_clip(raw_azimuth, 5.0, 4.0, seconds=2.0, kind="noise", snr_db=25, seed=int(truth) + 1)
            wav = write_wav(Path(tmp) / f"az{int(truth):03d}.wav", six_channel(mics))
            if sidecar:
                wav.with_suffix(".json").write_text(json.dumps({"synthetic": True, "gaps": [], "start_time_s": 0.0}))
            positions.append({"wav": wav.name, "true_azimuth_deg": truth, "distance_m": 4.0})
        manifest = Path(tmp) / "positions.json"
        manifest.write_text(json.dumps({"operator": "unit test", "truth_method": "synthetic", "positions": positions}))
        return manifest

    def test_recovers_rotation_and_mirror(self):
        cfg = make_config()
        for mirror in (False, True):
            with self.subTest(mirror=mirror), tempfile.TemporaryDirectory() as tmp:
                record = run_calibration(self.make_set(tmp, range(0, 360, 45), 37.0, mirror), cfg)
                result = record["result"]
                self.assertTrue(result["passed"], result["problems"])
                self.assertEqual(result["mirror"], mirror)
                self.assertAlmostEqual(result["local_angle_offset_deg"], 37.0, delta=1.0)
                self.assertLess(result["median_abs_error_deg"], 2.0)
                self.assertGreaterEqual(result["bearing_sigma_deg"], 1.0)
                self.assertEqual(record["evidence_kind"], "synthetic")
                self.assertEqual(len(record["positions"][0]["sha256"]), 64)
                json.dumps(record, allow_nan=False)

    def test_synthetic_record_cannot_enable_bearing_but_a_physical_one_can(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = run_calibration(self.make_set(tmp, range(0, 360, 45), 37.0, False), make_config())
            accept = {"geometry_validated": True, "record": "record.json", "mirror": record["result"]["mirror"],
                      "local_angle_offset_deg": record["result"]["local_angle_offset_deg"],
                      "bearing_sigma_deg": record["result"]["bearing_sigma_deg"]}
            (Path(tmp) / "record.json").write_text(json.dumps(record))
            with self.assertRaises(ConfigError) as caught:
                validate({"calibration": accept}, base_dir=Path(tmp))
            self.assertIn("synthetic data cannot calibrate hardware", str(caught.exception))
            record["evidence_kind"] = "physical"                               # stands in for a real bench record
            (Path(tmp) / "record.json").write_text(json.dumps(record))
            self.assertTrue(validate({"calibration": accept}, base_dir=Path(tmp)).calibrated)

    def test_weak_evidence_does_not_pass(self):
        cfg = make_config()
        with tempfile.TemporaryDirectory() as tmp:
            few = run_calibration(self.make_set(tmp, (0, 45, 90), 10.0, False), cfg)["result"]
            self.assertFalse(few["passed"])
            self.assertTrue(any("positions" in p for p in few["problems"]))
        with tempfile.TemporaryDirectory() as tmp:
            bare = run_calibration(self.make_set(tmp, range(0, 360, 45), 10.0, False, sidecar=False), cfg)
            self.assertFalse(bare["result"]["passed"])
            self.assertEqual(bare["evidence_kind"], "unverified")

    def test_swapped_channels_fail_calibration(self):
        cfg = make_config(device={"raw_indices": [3, 2, 4, 5]})                 # wrong order for the geometry
        with tempfile.TemporaryDirectory() as tmp:
            result = run_calibration(self.make_set(tmp, range(0, 360, 45), 0.0, False), cfg)["result"]
        self.assertFalse(result["passed"])

    def test_command_line_writes_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.make_set(tmp, range(0, 360, 60), 20.0, False)
            code, out, _ = quiet_cli(["calibrate", "--manifest", str(manifest), "--out", str(Path(tmp) / "rec.json")])
            self.assertEqual(code, 0)
            self.assertIn("not made from physical recordings", out)
            self.assertTrue((Path(tmp) / "rec.json").exists())


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.manifest = ml.make_synthetic_dataset(Path(cls.tmp) / "data", sessions=28, seconds=6.0)
        cls.model = ml.train(cls.manifest, Path(cls.tmp) / "model", provenance="synthetic, generated by pasensor.simulate")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_whole_groups_go_to_one_partition(self):
        groups = [f"session-{i}" for i in range(300)]
        first = ml.grouped_split(groups)
        self.assertEqual(set(first), set(groups))
        self.assertEqual(first, ml.grouped_split(list(reversed(groups))))       # order does not matter
        extended = ml.grouped_split(groups + [f"new-{i}" for i in range(50)])
        self.assertTrue(all(extended[g] == first[g] for g in groups))           # adding data never moves old groups
        share = sum(1 for p in first.values() if p == "train") / 300
        self.assertAlmostEqual(share, 0.6, delta=0.08)
        rows = ml.load_manifest(self.manifest)
        assignment = self.model["training"]["split_assignment"]
        partitions_per_session = {}
        for row in rows:
            partitions_per_session.setdefault(row["session_id"], set()).add(assignment[row["session_id"]])
        self.assertTrue(all(len(parts) == 1 for parts in partitions_per_session.values()))

    def test_identical_audio_in_two_partitions_is_refused(self):
        rows = ml.load_manifest(self.manifest)
        assignment = ml.grouped_split([r["session_id"] for r in rows])
        train_row = next(r for r in rows if assignment[r["session_id"]] == "train")
        test_row = next(r for r in rows if assignment[r["session_id"]] == "test")
        copy = dict(train_row, session_id=test_row["session_id"])               # same file relabelled as another session
        copy.pop("sha256", None)
        with self.assertRaises(ml.DatasetError) as caught:
            ml.check_no_leakage(rows + [copy], assignment, ("session_id",))
        self.assertIn("two partitions", str(caught.exception))

    def test_manifest_errors_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.csv"
            for header, row in (("path,label,session_id", "missing.wav,drone,s1"), ("path,label,session_id", "x.wav,bird,s1"),
                                ("path,label,session_id", "x.wav,drone,")):
                path.write_text(header + "\n" + row + "\n")
                (Path(tmp) / "x.wav").write_bytes(b"")
                with self.assertRaises(ml.DatasetError):
                    ml.load_manifest(path)

    def test_trained_model_is_valid_marked_synthetic_and_documented(self):
        model = json.loads((Path(self.tmp) / "model" / "model.json").read_text())
        C.validate_model(model)
        training = model["training"]
        self.assertTrue(training["synthetic_only"])
        self.assertEqual(training["manifest_sha256"], ml.sha256_file(self.manifest))
        self.assertGreater(training["metrics"]["test"]["auc"], 0.9)             # the two synthetic classes are easy to separate
        self.assertIn("test_harmonic_heuristic_baseline", training["metrics"])
        self.assertTrue(any("Fewer than five test groups" in w for w in training["warnings"]))
        card = (Path(self.tmp) / "model" / "MODEL_CARD.md").read_text()
        self.assertIn("Synthetic data only: YES", card)
        self.assertIn("not a probability", card)

    def test_synthetic_model_is_refused_for_real_audio(self):
        cfg = make_config()
        path = Path(self.tmp) / "model" / "model.json"
        for mode in ("LIVE", "REPLAY"):
            with self.assertRaises(ValueError) as caught:
                C.load_classifier(path, cfg, mode)
            self.assertIn("must not label real audio", str(caught.exception))
        classifier = C.load_classifier(path, cfg, "SIMULATED")
        _final, state, _ = run_pipeline(cfg, "SIMULATED", sim_factory(cfg, late_rotor(), limit_chunks=70), classifier=classifier)
        scored = [s["identity"] for s in state.published if s.get("identity") and s["identity"].get("available")]
        self.assertTrue(scored)
        self.assertTrue(all(0.0 <= i["score"] <= 1.0 and i["score_kind"] == "uncalibrated model score" for i in scored))
        self.assertGreater(scored[-1]["score"], 0.5)                             # rotor-like synthetic sound
        self.assertTrue(state.events()[0]["identity"]["available"])

    def test_damaged_or_mismatched_model_files_are_rejected(self):
        good = json.loads((Path(self.tmp) / "model" / "model.json").read_text())
        for change in ({"schema": "other"}, {"kind": "pickle"}, {"weights": good["weights"][:-1]},
                       {"scale": [0.0] * len(good["scale"])}, {"feature_spec": {**good["feature_spec"], "n_mels": 64}},
                       {"bias": float("nan")}, {"training": None}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                C.validate_model({**good, **change})

    def test_features_ignore_loudness(self):
        from pasensor.dsp import Analyzer
        analyzer = Analyzer()
        clip = static_clip(40, 10, 30.0, kind="rotor", snr_db=25)
        harmonic, _s, (power, df) = analyzer.harmonic_features(clip)
        quiet = C.frame_features(power, df, harmonic)
        loud = C.frame_features(power * 100.0, df, harmonic)                     # 20 dB louder
        np.testing.assert_allclose(quiet[:40], loud[:40], atol=1e-9)
        self.assertEqual(len(quiet), C.FRAME_DIM)

    def test_resample_and_auc_helpers(self):
        t = np.arange(48000) / 48000.0
        x = np.sin(2 * np.pi * 440.0 * t)[:, None]
        y = ml.resample(x, 48000, 16000)
        self.assertEqual(len(y), 16000)
        expected = np.sin(2 * np.pi * 440.0 * np.arange(16000) / 16000.0)
        self.assertLess(float(np.max(np.abs(y[100:-100, 0] - expected[100:-100]))), 0.01)
        self.assertEqual(ml.roc_auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1])), 1.0)
        self.assertEqual(ml.roc_auc(np.array([0.5, 0.5, 0.5, 0.5]), np.array([0, 1, 0, 1])), 0.5)
        low, high = ml.wilson(8, 10)
        self.assertAlmostEqual(low, 0.490, delta=0.005)
        self.assertAlmostEqual(high, 0.943, delta=0.005)

    def test_training_command_reports_dataset_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = ml.make_synthetic_dataset(Path(tmp) / "tiny", sessions=2, seconds=5.0)
            code, _out, err = quiet_cli(["train", "--manifest", str(manifest), "--out", str(Path(tmp) / "m")])
            self.assertEqual(code, 2)
            self.assertIn("Dataset error", err)


class EvaluationTests(unittest.TestCase):
    def test_event_metrics_on_synthetic_sessions(self):
        cfg = make_config(calibration={"show_uncalibrated_bearing": True})
        with tempfile.TemporaryDirectory() as tmp:
            scene = rotor_scene(azimuth=120.0, gain=step_gain(5.0, 10.0))
            write_wav(Path(tmp) / "flight.wav", six_channel(scene.render(16000 * 14)))
            write_wav(Path(tmp) / "background.wav", six_channel(quiet_scene(seed=8).render(16000 * 12)))
            manifest = Path(tmp) / "eval.json"
            manifest.write_text(json.dumps({"schema": "pasensor.evaluation-manifest.v1", "guard_s": 1.0, "sessions": [
                {"wav": "flight.wav", "session_id": "syn-flight", "synthetic": True,
                 "truth": [{"start_s": 5.0, "end_s": 10.0, "label": "drone", "bearing_local_deg": 120.0}]},
                {"wav": "background.wav", "session_id": "syn-background", "synthetic": True, "truth": []}]}))
            report = evaluate(manifest, cfg)
            code, out, _ = quiet_cli(["evaluate", "--manifest", str(manifest), "--out", str(Path(tmp) / "report")])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "report.json").exists() and (Path(tmp) / "report.md").exists())
        self.assertTrue(report["synthetic_data"])
        self.assertIn("SYNTHETIC DATA", report["evidence_statement"])
        self.assertEqual((report["drone_events"], report["drone_events_detected"], report["false_alert_events"]), (1, 1, 0))
        self.assertEqual(report["event_recall"], 1.0)
        self.assertLess(report["event_recall_ci95"][0], 0.3)                    # one event proves very little
        self.assertAlmostEqual(report["background_exposure_hours"], (14.0 - 7.0 + 12.0) / 3600.0, delta=1e-4)
        self.assertEqual(report["false_alerts_per_hour"], 0.0)
        self.assertGreater(report["false_alerts_per_hour_ci95"][1], 500.0)      # 19 s of background bounds almost nothing
        self.assertLess(report["detection_latency_s"]["median"], 1.2)
        self.assertLess(report["bearing_error_deg"]["median"], 2.0)
        self.assertLess(report["bearing_unavailable_fraction"], 0.4)
        self.assertEqual(report["lost_sample_events"], {"gaps": 0, "overflows": 0})
        self.assertLess(report["processing"]["load_fraction"], 1.0)
        text = report_markdown(report)
        self.assertIn("SYNTHETIC DATA", text)
        self.assertIn("False alert events per hour", out)

    def test_rate_interval(self):
        low, high = poisson_rate_ci(0, 2.0)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 3.689 / 2.0, delta=0.01)
        low, high = poisson_rate_ci(5, 1.0)
        self.assertAlmostEqual(low, 1.62, delta=0.15)
        self.assertAlmostEqual(high, 11.67, delta=0.15)
        self.assertIsNone(poisson_rate_ci(1, 0.0))


class RetentionTests(unittest.TestCase):
    def test_purge_lists_first_and_deletes_only_on_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, foreign = Path(tmp) / "old.wav", Path(tmp) / "new.wav", Path(tmp) / "foreign.wav"
            for path in (old, new, foreign):
                path.write_bytes(b"RIFF")
            old.with_suffix(".json").write_text(json.dumps({"start_time_s": time.time() - 40 * 86400}))
            new.with_suffix(".json").write_text(json.dumps({"start_time_s": time.time() - 86400}))
            args = ["purge", "--dir", tmp, "--older-than-days", "30"]
            code, out, _ = quiet_cli(args)
            self.assertIn("would delete", out)
            self.assertIn("Nothing was deleted", out)
            self.assertTrue(old.exists())
            quiet_cli(args + ["--confirm"])
            self.assertFalse(old.exists() or old.with_suffix(".json").exists())
            self.assertTrue(new.exists() and foreign.exists())                  # recent files and files it did not record stay


class SyntheticDatasetTests(unittest.TestCase):
    def test_generated_manifest_is_marked_synthetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = ml.make_synthetic_dataset(tmp, sessions=2, seconds=3.0)
            with open(manifest, newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual({r["synthetic"] for r in rows}, {"true"})
        self.assertEqual({r["label"] for r in rows}, {"drone", "background"})


if __name__ == "__main__":
    unittest.main()
