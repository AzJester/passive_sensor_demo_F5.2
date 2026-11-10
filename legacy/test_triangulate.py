"""Deterministic tests for the optional bearing-only geometry demonstration."""

import copy
import math
import unittest

try:
    from .triangulate import GeometryError, triangulate
except ImportError:
    from triangulate import GeometryError, triangulate


def observations():
    a = {"node_id": "A", "x_m": 0.0, "y_m": 0.0,
         "bearing_deg": 45.0, "bearing_sigma_deg": 1.0,
         "timestamp_s": 1000.0, "source_event_id": "synthetic-event-1",
         "calibrated": True}
    b = {"node_id": "B", "x_m": 10.0, "y_m": 0.0,
         "bearing_deg": 135.0, "bearing_sigma_deg": 1.0,
         "timestamp_s": 1000.1, "source_event_id": "synthetic-event-1",
         "calibrated": True}
    return a, b


class TriangulationTests(unittest.TestCase):
    def compute(self, a, b, **kwargs):
        return triangulate(a, b, reference_time=1001.0, samples=1000, **kwargs)

    def test_exact_intersection_and_status(self):
        a, b = observations()
        result = self.compute(a, b)
        point = result["conditional_intersection"]
        self.assertAlmostEqual(point["x_m"], 5.0)
        self.assertAlmostEqual(point["y_m"], 5.0)
        self.assertAlmostEqual(result["baseline_m"], 10.0)
        self.assertAlmostEqual(result["acute_crossing_angle_deg"], 90.0)
        self.assertFalse(result["validated_location"])
        self.assertFalse(result["same_source_assumption_verified"])

    def test_parallel_rejected(self):
        a, b = observations()
        b["bearing_deg"] = a["bearing_deg"]
        with self.assertRaisesRegex(GeometryError, "crossing angle"):
            self.compute(a, b)

    def test_acute_crossing_rejected(self):
        a, b = observations()
        b["bearing_deg"] = 64.0
        with self.assertRaisesRegex(GeometryError, "crossing angle"):
            self.compute(a, b)

    def test_behind_node_rejected(self):
        a, b = observations()
        a["bearing_deg"] = 225.0
        with self.assertRaisesRegex(GeometryError, "behind"):
            self.compute(a, b)

    def test_stale_rejected(self):
        a, b = observations()
        a["timestamp_s"] = 998.9
        b["timestamp_s"] = 999.0
        with self.assertRaisesRegex(GeometryError, "age"):
            self.compute(a, b)

    def test_wrong_event_id_rejected(self):
        a, b = observations()
        b["source_event_id"] = "different-event"
        with self.assertRaisesRegex(GeometryError, "must match"):
            self.compute(a, b)

    def test_empty_event_id_rejected(self):
        a, b = observations()
        a["source_event_id"] = " "
        with self.assertRaisesRegex(GeometryError, "nonempty"):
            self.compute(a, b)

    def test_monte_carlo_repeatable_and_nonzero(self):
        a, b = observations()
        first = self.compute(a, b, seed=17)
        second = self.compute(a, b, seed=17)
        self.assertEqual(first, second)
        self.assertGreater(first["monte_carlo"]["empirical_90_percent_horizontal_radius_m"], 0.0)
        self.assertGreaterEqual(first["monte_carlo"]["geometry_acceptance_fraction"], 0.9)

    def test_reception_time_difference_rejected(self):
        a, b = observations()
        b["timestamp_s"] = 1000.3
        with self.assertRaisesRegex(GeometryError, "difference"):
            self.compute(a, b)

    def test_future_timestamp_rejected(self):
        a, b = observations()
        a["timestamp_s"] = 1001.1
        b["timestamp_s"] = 1001.2
        with self.assertRaisesRegex(GeometryError, "future"):
            self.compute(a, b)

    def test_short_baseline_rejected(self):
        a, b = observations()
        b["x_m"] = 0.5
        with self.assertRaisesRegex(GeometryError, "baseline"):
            self.compute(a, b)

    def test_calibration_and_sigma_gates(self):
        for key, value, message in (("calibrated", False, "calibrated"),
                                    ("bearing_sigma_deg", 0.5, "at least 1")):
            a, b = observations()
            a[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(GeometryError, message):
                self.compute(a, b)

    def test_nonfinite_and_boolean_numbers_rejected(self):
        for value in (math.nan, math.inf, True):
            a, b = observations()
            a["x_m"] = value
            with self.subTest(value=value), self.assertRaises(GeometryError):
                self.compute(a, b)

    def test_uncertain_geometry_rejected(self):
        a, b = observations()
        a["bearing_sigma_deg"] = 80.0
        b["bearing_sigma_deg"] = 80.0
        with self.assertRaisesRegex(GeometryError, "acceptance"):
            self.compute(a, b)

    def test_inputs_not_mutated(self):
        a, b = observations()
        original = copy.deepcopy((a, b))
        self.compute(a, b)
        self.assertEqual((a, b), original)


if __name__ == "__main__":
    unittest.main()
