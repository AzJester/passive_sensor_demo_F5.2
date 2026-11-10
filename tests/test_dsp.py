"""Signal processing checks against the independent time-domain simulator.

Software evidence only. Synthetic microphones share a perfect clock and have
no enclosure, so these tests cannot show hardware timing or real accuracy.
"""
import importlib.util
import math
import unittest

import numpy as np

from pasensor import geometry as G
from pasensor.dsp import Analyzer, DspSettings
from pasensor.simulate import (NoiseSignal, PointSource, RotorSignal, Scene, ToneSignal, brute_force_delay,
                               sinc_interpolate, static_clip, static_position)
from tests.helpers import ROOT, angular_error

C20 = G.speed_of_sound(20.0)


def load_rev_a():
    spec = importlib.util.spec_from_file_location("dsp_rev_a", ROOT / "legacy" / "dsp_rev_a.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeometryTests(unittest.TestCase):
    def test_speed_of_sound(self):
        self.assertAlmostEqual(G.speed_of_sound(0.0), 331.3, places=3)
        self.assertAlmostEqual(G.speed_of_sound(20.0), 343.2, delta=0.3)
        with self.assertRaises(ValueError):
            G.speed_of_sound(float("nan"))

    def test_expected_tdoa_sign(self):
        # Source along +X: the +X microphones (0 and 1) hear it before the -X ones (2 and 3).
        tdoa = dict(zip(G.pairs(4), G.expected_tdoa(G.ASSUMED_GEOMETRY_M, 0.0, 0.0, C20)))
        self.assertLess(tdoa[(0, 3)], 0)                      # t0 - t3 < 0: mic 0 first
        self.assertAlmostEqual(tdoa[(0, 3)], -0.066 / C20, places=9)
        self.assertAlmostEqual(tdoa[(0, 1)], 0.0, places=12)

    def test_angle_conventions(self):
        self.assertAlmostEqual(G.site_azimuth_to_compass(0.0), 90.0)      # East
        self.assertAlmostEqual(G.site_azimuth_to_compass(90.0), 0.0)      # North
        self.assertAlmostEqual(G.compass_to_site_azimuth(G.site_azimuth_to_compass(217.0)), 217.0)
        self.assertAlmostEqual(G.local_to_site_azimuth(30.0, 100.0, face_up=True), 130.0)
        self.assertAlmostEqual(G.local_to_site_azimuth(30.0, 100.0, face_up=False), 70.0)

    def test_circular_statistics(self):
        mean, length = G.circular_mean_deg([350.0, 10.0])
        self.assertAlmostEqual(min(mean, 360.0 - mean), 0.0, places=6)
        self.assertGreater(length, 0.98)
        self.assertEqual(G.circular_mean_deg([]), (None, 0.0))
        self.assertIsNone(G.circular_std_deg([5.0]))
        self.assertLess(G.circular_std_deg([359.0, 1.0, 0.0]), 2.0)

    def test_invalid_geometry_rejected(self):
        for bad in ([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]],       # collinear
                    [[0, 0, 0], [0, 0, 0], [0.03, 0, 0], [0, 0.03, 0]],   # duplicate
                    [[0, 0, 0], [float("nan"), 0, 0], [0, 1, 0]]):
            with self.assertRaises(ValueError):
                G.validate_geometry(bad)


class SimulatorIndependenceTests(unittest.TestCase):
    """The simulator must be right on its own terms, checked without the estimator."""

    def test_arrival_order_by_direct_correlation(self):
        # brute_force_delay uses a plain sliding dot product, no FFT, no estimator code.
        for azimuth, early, late in ((0, 0, 3), (180, 3, 0), (90, 1, 0), (270, 0, 1)):
            x = static_clip(azimuth, 0.0, 30.0, seconds=0.4, kind="noise", snr_db=None, seed=4)
            lag = brute_force_delay(x[:, early], x[:, late], 12)
            expected = dict(zip(G.pairs(4), G.expected_tdoa(G.ASSUMED_GEOMETRY_M, azimuth, 0.0, C20)))
            key, sign = ((early, late), 1.0) if early < late else ((late, early), -1.0)
            self.assertLess(lag, 0, f"azimuth {azimuth}: microphone {early} must lead microphone {late}")
            self.assertAlmostEqual(lag, sign * expected[key] * 16000.0, delta=0.6)

    def test_sinc_interpolation_recovers_fractional_delay(self):
        fs, f = 16000.0, 1234.0
        tone = lambda i0, i1: np.sin(2 * np.pi * f * np.arange(i0, i1) / fs)  # noqa: E731
        positions = np.arange(200, 600) + 0.37
        got = sinc_interpolate(tone, positions)
        self.assertLess(float(np.max(np.abs(got - np.sin(2 * np.pi * f * positions / fs)))), 2e-3)

    def test_spherical_spreading(self):
        near = static_clip(40, 0, 10.0, kind="tone", snr_db=None, received_rms=0.02)
        self.assertAlmostEqual(float(np.sqrt(np.mean(near ** 2))), 0.02, delta=0.001)
        scene = lambda d: Scene(sources=[PointSource(ToneSignal(800.0), static_position(40, 0, d), level_at_1m=0.2)])  # noqa: E731
        r10 = float(np.sqrt(np.mean(scene(10.0).render(4096) ** 2)))
        r20 = float(np.sqrt(np.mean(scene(20.0).render(4096) ** 2)))
        self.assertAlmostEqual(r10 / r20, 2.0, delta=0.02)


class BearingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = Analyzer()

    def bearing(self, x):
        return self.analyzer.analyze(x)["bearing"]

    def test_known_bearings_broadband_and_rotor(self):
        for kind in ("noise", "rotor"):
            for azimuth in (0, 36, 90, 126, 180, 244, 270, 330):
                for elevation in (0, 30, 60):
                    with self.subTest(kind=kind, azimuth=azimuth, elevation=elevation):
                        b = self.bearing(static_clip(azimuth, elevation, 30.0, kind=kind, snr_db=30, seed=3))
                        self.assertTrue(b["available"], b["reasons"])
                        self.assertLessEqual(angular_error(b["azimuth_deg"], azimuth), 1.0)

    def test_rev_a_and_rev_b_agree_on_clean_signals(self):
        rev_a = load_rev_a().Analyzer()
        for azimuth in (15, 140, 265):
            x = static_clip(azimuth, 20, 30.0, kind="noise", snr_db=30, seed=8)
            self.assertLessEqual(angular_error(rev_a.analyze(x)["bearing_deg"], azimuth), 4.0)
            self.assertLessEqual(angular_error(self.bearing(x)["azimuth_deg"], azimuth), 1.0)

    def test_rev_a_generator_cross_check(self):
        # The Revision A frequency-domain generator and the new time-domain one must describe the same geometry.
        x = load_rev_a().synthesize_source(azimuth_deg=200, elevation_deg=10, seed=5)
        self.assertLessEqual(angular_error(self.bearing(x)["azimuth_deg"], 200), 2.0)

    def test_pair_delays_match_geometry(self):
        b = self.bearing(static_clip(63, 0, 30.0, kind="noise", snr_db=30))
        expected = G.expected_tdoa(G.ASSUMED_GEOMETRY_M, 63, 0, C20) * 1e6
        self.assertLess(float(np.max(np.abs(np.array(b["pair_tdoa_us"]) - expected))), 4.0)   # 1/16 sample
        self.assertLess(b["tdoa_residual_us"], 3.0)

    def test_near_field_source_keeps_bearing(self):
        for distance in (0.5, 1.0, 2.0):
            b = self.bearing(static_clip(77, 0, distance, kind="noise", snr_db=30))
            self.assertLessEqual(angular_error(b["azimuth_deg"], 77), 2.0, f"distance {distance} m")

    def test_temperature_error_does_not_bias_azimuth(self):
        # A wrong speed of sound scales every delay alike, which a planar array absorbs as elevation.
        for scene_temperature in (-10.0, 40.0):
            b = self.bearing(static_clip(133, 20, 30.0, kind="noise", snr_db=30, temperature_c=scene_temperature))
            self.assertLessEqual(angular_error(b["azimuth_deg"], 133), 2.0)

    def test_ground_reflection_keeps_azimuth(self):
        b = self.bearing(static_clip(100, 20, 40.0, kind="rotor", snr_db=25, ground_reflection=0.7))
        self.assertTrue(b["available"], b["reasons"])
        self.assertLessEqual(angular_error(b["azimuth_deg"], 100), 3.0)

    def test_swapped_channels_are_detected(self):
        withheld = 0
        for azimuth in range(15, 360, 30):                      # off the symmetry axes of the swap
            x = static_clip(azimuth, 10, 30.0, kind="noise", snr_db=30, seed=azimuth)[:, [1, 0, 2, 3]]
            b = self.bearing(x)
            withheld += not b["available"]
            if not b["available"]:
                self.assertTrue({"TDOA_INCONSISTENT", "COMPETING_PEAK", "WEAK_PEAK"} & set(b["reasons"]), b["reasons"])
            else:
                self.assertLessEqual(angular_error(b["azimuth_deg"], azimuth), 15.0)
        self.assertGreaterEqual(withheld, 10)

    def test_rotated_channel_order_is_a_consistent_offset(self):
        # A cyclic channel shift is a symmetry of the square. Data alone cannot reveal it; the axis test must.
        b = self.bearing(static_clip(100, 10, 30.0, kind="noise", snr_db=30)[:, [1, 2, 3, 0]])
        self.assertTrue(b["available"])
        self.assertLessEqual(angular_error(b["azimuth_deg"], 10.0), 1.0)

    def test_inverted_channel_is_flagged(self):
        x = static_clip(20, 10, 30.0, kind="noise", snr_db=30)
        x[:, 2] *= -1.0
        b = self.bearing(x)
        self.assertFalse(b["available"])
        self.assertGreaterEqual(b["polarity_suspect_pairs"], 3)

    def test_pure_tone_bearing_is_withheld(self):
        b = self.bearing(static_clip(23, 0, 30.0, kind="tone", snr_db=40))
        self.assertFalse(b["available"])
        self.assertIn("NARROWBAND", b["reasons"])

    def test_competing_sources_are_withheld_or_point_at_one_source(self):
        for trial in range(6):
            a1, a2 = 40.0 + 45.0 * trial, 160.0 + 45.0 * trial
            scene = Scene(sources=[PointSource(NoiseSignal(seed=trial + 1), static_position(a1, 10, 30), level_at_1m=0.6),
                                   PointSource(NoiseSignal(seed=trial + 60), static_position(a2, 10, 30), level_at_1m=0.6)],
                          sensor_noise_rms=0.0005, seed=trial)
            b = self.bearing(scene.render(4096))
            if b["available"]:
                self.assertLessEqual(min(angular_error(b["azimuth_deg"], a1), angular_error(b["azimuth_deg"], a2)), 15.0)

    def test_overhead_source_gives_no_azimuth(self):
        b = self.bearing(static_clip(50, 85, 30.0, kind="noise", snr_db=30))
        self.assertFalse(b["available"])
        self.assertIn("NEAR_OVERHEAD_OR_DIFFUSE", b["reasons"])

    def test_independent_noise_gives_no_bearing(self):
        rng = np.random.default_rng(123)
        available = sum(self.bearing(rng.normal(0, 0.01, (4096, 4)))["available"] for _ in range(60))
        self.assertEqual(available, 0)

    def test_low_snr_withholds_rather_than_misleads(self):
        rng = np.random.default_rng(11)
        wrong = shown = 0
        for trial in range(40):
            azimuth = float(rng.uniform(0, 360))
            x = static_clip(azimuth, 20, 40.0, kind="rotor", snr_db=-5.0, seed=trial + 1, f0_hz=float(rng.uniform(110, 260)))
            b = self.bearing(x)
            if b["available"]:
                shown += 1
                wrong += angular_error(b["azimuth_deg"], azimuth) > 10.0
        self.assertGreater(shown, 0)
        self.assertLessEqual(wrong / shown, 0.10)

    def test_moderate_snr_accuracy(self):
        rng = np.random.default_rng(12)
        errors = []
        for trial in range(30):
            azimuth = float(rng.uniform(0, 360))
            b = self.bearing(static_clip(azimuth, 20, 40.0, kind="rotor", snr_db=5.0, seed=trial + 1))
            self.assertTrue(b["available"], b["reasons"])
            errors.append(angular_error(b["azimuth_deg"], azimuth))
        self.assertLess(float(np.median(errors)), 2.0)
        self.assertLess(max(errors), 8.0)


class AudioQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = Analyzer()
        cls.clean = static_clip(40, 10, 30.0, kind="rotor", snr_db=30)

    def check(self, x, status):
        result = self.analyzer.analyze(x)
        self.assertEqual(result["audio"]["status"], status)
        self.assertFalse(result["bearing"]["available"])
        self.assertIsNone(result["bearing"]["azimuth_deg"])
        self.assertEqual(result["harmonic"]["score"], 0.0)

    def test_silence(self):
        self.check(np.zeros((4096, 4)), "SILENCE")

    def test_nonfinite(self):
        x = self.clean.copy()
        x[100, 2] = np.nan
        self.check(x, "NONFINITE")

    def test_clipping(self):
        x = self.clean.copy()
        x[42, 0] = 1.0
        self.check(x, "CLIPPING")

    def test_dead_channel(self):
        x = self.clean.copy()
        x[:, 2] = 0.0
        self.check(x, "DEAD_CHANNEL")
        x = self.clean.copy()
        x[:, 1] *= 0.001                                        # 60 dB down: a blocked or failed microphone
        self.check(x, "DEAD_CHANNEL")

    def test_duplicate_channel(self):
        x = self.clean.copy()
        x[:, 2] = x[:, 1]
        self.check(x, "DUPLICATE_CHANNEL")

    def test_level_imbalance_is_a_warning(self):
        x = self.clean.copy()
        x[:, 3] *= 0.3
        result = self.analyzer.analyze(x)
        self.assertEqual(result["audio"]["status"], "OK")
        self.assertIn("LEVEL_IMBALANCE", result["audio"]["warnings"])

    def test_wrong_shape_rejected(self):
        for x in (np.zeros(4096), np.zeros((4096, 6)), np.zeros((500, 4))):
            with self.assertRaises(ValueError):
                self.analyzer.analyze(x)

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            Analyzer(settings=DspSettings(band_hz=(300.0, 9000.0)))
        with self.assertRaises(ValueError):
            DspSettings.from_mapping({"bandhz": [300, 3000]})


class HarmonicFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = Analyzer()

    def harmonic(self, x):
        return self.analyzer.analyze(x)["harmonic"]

    def test_fundamental_estimate_off_any_grid(self):
        # Revision A searched fundamentals in 5 Hz steps and lost combs between the steps.
        for f0 in (95.0, 152.0, 157.5, 163.0, 240.0, 410.0):
            h = self.harmonic(static_clip(40, 20, 30.0, kind="rotor", snr_db=20, f0_hz=f0, seed=5))
            self.assertAlmostEqual(h["f0_hz"], f0, delta=0.012 * f0 + 1.0)
            self.assertGreaterEqual(h["count"], 8)
            self.assertGreater(h["score"], 0.6)

    def test_rev_a_score_depended_on_its_search_grid(self):
        # Revision A searched fundamentals in 5 Hz steps with one-bin windows. A steady comb between
        # two steps scored far lower than the same comb on a step. Revision B must not show that.
        def steady(f0):
            source = PointSource(RotorSignal(f0_hz=f0, n_rotors=1, detune=0.0, wobble_depth=0.0, seed=5),
                                 static_position(40, 20, 30), level_at_1m=0.9)
            return Scene(sources=[source], sensor_noise_rms=0.003, seed=5).render(4096)
        rev_a = load_rev_a().Analyzer()
        on_grid, off_grid = (rev_a.analyze(steady(f))["harmonic_score"] for f in (155.0, 157.5))
        self.assertLess(off_grid, 0.6 * on_grid)
        new = [self.harmonic(steady(f))["score"] for f in (155.0, 157.5)]
        self.assertLess(abs(new[0] - new[1]), 0.1)
        self.assertGreater(min(new), 0.8)

    def test_noise_has_no_comb(self):
        rng = np.random.default_rng(1)
        counts = [self.harmonic(rng.normal(0, 0.01, (4096, 4)))["count"] for _ in range(100)]
        self.assertLess(max(counts), 4)
        self.assertLess(self.harmonic(static_clip(0, 0, 30.0, kind="noise", snr_db=30))["count"], 4)

    def test_single_tone_is_not_a_comb(self):
        h = self.harmonic(static_clip(0, 0, 30.0, kind="tone", snr_db=40))
        self.assertLess(h["count"], 4)
        self.assertLess(h["score"], 0.25)

    def test_steady_machine_hum_also_scores(self):
        # A documented limit: any harmonic machine raises the heuristic.
        fan = PointSource(RotorSignal(f0_hz=120.0, n_rotors=1, detune=0.0, wobble_depth=0.0, broadband_ratio=0.2, seed=2),
                          static_position(100, 0, 15), level_at_1m=0.3)
        h = self.harmonic(Scene(sources=[fan], sensor_noise_rms=0.0005).render(4096))
        self.assertGreaterEqual(h["count"], 4)
        self.assertGreater(h["score"], 0.25)

    def test_spectrum_summary_shape(self):
        result = self.analyzer.analyze(static_clip(40, 20, 30.0, kind="rotor", snr_db=20))
        self.assertEqual(len(result["spectrum"]["db"]), 160)
        self.assertTrue(all(math.isfinite(v) for v in result["spectrum"]["db"]))


if __name__ == "__main__":
    unittest.main()
