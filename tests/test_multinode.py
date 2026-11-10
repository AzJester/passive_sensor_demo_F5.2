"""Multi-node bearing fusion: geometry, uncertainty, sound travel time, association."""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from pasensor import multinode as M
from pasensor.recorder import JsonlWriter
from pasensor.simulate import PointSource, RotorSignal, Scene, static_position
from tests.helpers import ROOT, make_config, run_pipeline, sim_factory, step_gain


def site(nodes=None, **limits):
    nodes = nodes or [("A", 0.0, 0.0), ("B", 100.0, 0.0)]
    raw = {"schema": M.SITE_SCHEMA, "temperature_c": 20.0, "limits": limits,
           "nodes": [{"node_id": n, "east_m": e, "north_m": nn, "heading_deg": 0.0, "position_sigma_m": 0.5,
                      "heading_sigma_deg": 1.0, "bearing_sigma_deg": 2.0} for n, e, nn in nodes]}
    return M.site_from_mapping(raw)


def toward(node, point):
    return math.degrees(math.atan2(point[1] - node.north_m, point[0] - node.east_m)) % 360.0


def bearings_to(s, point, **offsets):
    return [M.Bearing(k, toward(n, point) + offsets.get(k, 0.0)) for k, n in s.nodes.items()]


class FusionTests(unittest.TestCase):
    def test_exact_crossing_and_honest_status(self):
        s = site()
        result = M.fuse(bearings_to(s, (50.0, 50.0)), s)
        self.assertAlmostEqual(result["position"]["east_m"], 50.0, places=5)
        self.assertAlmostEqual(result["position"]["north_m"], 50.0, places=5)
        self.assertAlmostEqual(result["best_crossing_angle_deg"], 90.0, places=4)
        self.assertEqual(result["status"], "bearing_only_estimate")
        self.assertFalse(result["validated_location"])
        self.assertFalse(result["association_verified"])
        self.assertFalse(result["consistency"]["checked"])            # two nodes: no redundancy
        self.assertIn("No consistency check", result["consistency"]["note"])
        self.assertTrue(any("Not a validated location" in line for line in result["limitations"]))
        json.dumps(result, allow_nan=False)

    def test_uncertainty_matches_closed_form_and_monte_carlo(self):
        s = site()
        result = M.fuse(bearings_to(s, (50.0, 50.0)), s, monte_carlo=6000)
        sigma_angle = math.radians(math.hypot(2.0, 1.0))
        per_axis = math.hypot(math.hypot(50.0, 50.0) * sigma_angle, 0.5)
        self.assertAlmostEqual(result["ellipse_95"]["semi_major_m"], M.K95 * per_axis, delta=0.02)
        self.assertAlmostEqual(result["ellipse_95"]["semi_minor_m"], M.K95 * per_axis, delta=0.02)
        mc = result["monte_carlo"]
        self.assertGreater(mc["solved_fraction"], 0.999)
        self.assertAlmostEqual(mc["radius_95_m"], M.K95 * per_axis, delta=0.06 * M.K95 * per_axis)

    def test_error_sources_are_separated_and_add_in_quadrature(self):
        s = site()
        parts = M.fuse(bearings_to(s, (50.0, 50.0)), s)["sigma_breakdown_m"]
        self.assertAlmostEqual(parts["total"] ** 2, parts["bearing_noise"] ** 2 + parts["node_heading"] ** 2
                               + parts["node_position"] ** 2, places=6)
        self.assertAlmostEqual(parts["bearing_noise"] / parts["node_heading"], 2.0, places=3)   # 2 deg versus 1 deg
        self.assertAlmostEqual(parts["node_position"], 0.5 * math.sqrt(2.0), places=3)
        for node in s.nodes.values():
            node.heading_sigma_deg = 5.0
        self.assertGreater(M.fuse(bearings_to(s, (50.0, 50.0)), s)["ellipse_95"]["semi_major_m"], 2.0 * M.K95 * parts["total"] / math.sqrt(2.0))

    def test_ellipse_stretches_along_range_for_shallow_crossing(self):
        s = site()
        result = M.fuse(bearings_to(s, (50.0, 120.0)), s)
        ellipse = result["ellipse_95"]
        self.assertGreater(ellipse["semi_major_m"], 1.5 * ellipse["semi_minor_m"])
        self.assertAlmostEqual(ellipse["major_axis_site_azimuth_deg"], 90.0, delta=1.0)        # north-south, along range

    def test_rejections(self):
        s = site()
        point = (50.0, 50.0)

        def code(bearings, the_site=s, **kwargs):
            with self.assertRaises(M.FusionRejected) as caught:
                M.fuse(bearings, the_site, **kwargs)
            return caught.exception.code

        self.assertEqual(code([M.Bearing("A", 45.0), M.Bearing("B", 45.0)]), "POOR_CROSSING")       # parallel
        self.assertEqual(code(bearings_to(s, (50.0, 400.0))), "POOR_CROSSING")                      # 14 degree crossing
        self.assertEqual(code([M.Bearing("A", 225.0), M.Bearing("B", 135.0)]), "BEHIND_NODE")
        self.assertEqual(code([M.Bearing("A", 45.0)]), "TOO_FEW_NODES")
        self.assertEqual(code([M.Bearing("A", 45.0), M.Bearing("A", 46.0)]), "BAD_INPUT")
        self.assertEqual(code([M.Bearing("A", 45.0), M.Bearing("Z", 135.0)]), "BAD_INPUT")
        self.assertEqual(code([M.Bearing("A", float("nan")), M.Bearing("B", 135.0)]), "BAD_INPUT")
        late = [M.Bearing("A", 45.0, time_s=10.0), M.Bearing("B", 135.0, time_s=12.0)]
        self.assertEqual(code(late), "TIME_SPREAD")
        wide = [M.Bearing(k, toward(n, point), sigma_deg=25.0) for k, n in s.nodes.items()]
        self.assertEqual(code(wide), "POOR_GEOMETRY")
        self.assertEqual(code(bearings_to(s, point), site(max_range_m=40.0)), "OUT_OF_RANGE")

    def test_sigma_has_a_floor_and_comes_from_the_site_by_default(self):
        s = site()
        tiny = [M.Bearing(k, toward(n, (50.0, 50.0)), sigma_deg=0.01) for k, n in s.nodes.items()]
        floor = [M.Bearing(k, toward(n, (50.0, 50.0)), sigma_deg=0.5) for k, n in s.nodes.items()]
        self.assertAlmostEqual(M.fuse(tiny, s)["ellipse_95"]["semi_major_m"], M.fuse(floor, s)["ellipse_95"]["semi_major_m"], places=6)

    def test_three_nodes_expose_an_inconsistent_bearing(self):
        s = site([("A", 0.0, 0.0), ("B", 100.0, 0.0), ("C", 50.0, 120.0)])
        good = M.fuse(bearings_to(s, (55.0, 45.0)), s)
        self.assertTrue(good["consistency"]["checked"])
        self.assertEqual(good["consistency"]["degrees_of_freedom"], 1)
        self.assertAlmostEqual(good["position"]["east_m"], 55.0, places=4)
        with self.assertRaises(M.FusionRejected) as caught:
            M.fuse(bearings_to(s, (55.0, 45.0), C=25.0), s)             # one node hears something else
        self.assertEqual(caught.exception.code, "INCONSISTENT_BEARINGS")
        two = site()
        skewed = M.fuse(bearings_to(two, (55.0, 45.0), B=25.0), two)   # the same error passes silently with two nodes
        self.assertGreater(math.hypot(skewed["position"]["east_m"] - 55.0, skewed["position"]["north_m"] - 45.0), 10.0)

    def test_site_file_validation(self):
        with self.assertRaises(M.FusionRejected):
            M.site_from_mapping({"schema": "wrong"})
        with self.assertRaises(M.FusionRejected):
            M.site_from_mapping({"schema": M.SITE_SCHEMA, "nodes": [{"node_id": "A", "east_m": 0, "north_m": 0, "heading_deg": 0,
                                                                     "position_sigma_m": 1, "heading_sigma_deg": 1, "bearing_sigma_deg": 3}]})
        example = M.load_site(ROOT / "config" / "site.example.json")
        self.assertGreaterEqual(len(example.nodes), 2)


class TrackTests(unittest.TestCase):
    def setUp(self):
        self.site = site([("A", 0.0, 0.0), ("B", 150.0, 0.0)])
        for node in self.site.nodes.values():
            node.bearing_sigma_deg, node.heading_sigma_deg, node.position_sigma_m = 0.5, 0.1, 0.05
        self.position = lambda t: np.array([-100.0 + 20.0 * t, 60.0])        # 20 m/s along east
        c = self.site.c
        self.tracks = {}
        for name, node in self.site.nodes.items():
            track = []
            for t_rx in np.arange(0.0, 12.0, 0.128):
                t_emit = t_rx
                for _ in range(8):                                             # solve t_emit + range/c = t_rx
                    t_emit = t_rx - np.linalg.norm(self.position(t_emit) - [node.east_m, node.north_m]) / c
                track.append((float(t_rx), toward(node, self.position(t_emit)), None))
            self.tracks[name] = track

    def test_emission_time_alignment_removes_motion_bias(self):
        truth = self.position(5.0)
        result = M.fuse_tracks(self.tracks, self.site, 5.0)
        error = math.hypot(result["position"]["east_m"] - truth[0], result["position"]["north_m"] - truth[1])
        self.assertLess(error, 0.3)
        self.assertTrue(result["propagation_compensated"])
        self.assertAlmostEqual(result["reception_time_s"]["A"] - 5.0, 60.0 / self.site.c, delta=0.01)
        self.assertGreater(result["reception_time_s"]["B"] - result["reception_time_s"]["A"], 0.25)
        # Using both bearings from one reception instant instead leaves a bias of several metres.
        same_instant = [M.Bearing(k, M._interpolate(t, 5.3, 0.5)[0]) for k, t in self.tracks.items()]
        naive = M.fuse(same_instant, self.site, assumed_max_speed_mps=20.0)
        candidates = [self.position(t) for t in np.arange(4.5, 5.6, 0.01)]
        naive_error = min(math.hypot(naive["position"]["east_m"] - p[0], naive["position"]["north_m"] - p[1]) for p in candidates)
        self.assertGreater(naive_error, 1.0)                                    # off the true path at every instant
        self.assertGreater(naive["motion_error_bound_m"], naive_error)
        self.assertIn("track fusion", naive["motion_note"])

    def test_gap_in_one_track_withholds_the_estimate(self):
        self.tracks["B"] = [s for s in self.tracks["B"] if not 4.5 < s[0] < 6.5]
        with self.assertRaises(M.FusionRejected) as caught:
            M.fuse_tracks(self.tracks, self.site, 5.0)
        self.assertEqual(caught.exception.code, "TOO_FEW_NODES")

    def test_interpolation_wraps_through_north(self):
        azimuth, _ = M._interpolate([(0.0, 350.0, None), (1.0, 10.0, None)], 0.5, 2.0)
        self.assertAlmostEqual(azimuth % 360.0, 0.0, places=6)


class AssociationTests(unittest.TestCase):
    def event(self, name, onset, offset, f0):
        return {"event_id": name, "onset_time_s": onset, "offset_time_s": offset, "evidence": {"median_f0_hz": f0}}

    def test_groups_by_time_and_fundamental_and_flags_ambiguity(self):
        s = site()
        groups = M.associate_events({"A": [self.event("a1", 10.0, 20.0, 160.0), self.event("a2", 100.0, 110.0, 200.0)],
                                     "B": [self.event("b1", 10.4, 20.3, 165.0), self.event("b2", 100.2, 109.0, 320.0)]}, s)
        self.assertEqual(len(groups), 1)                                        # a2 and b2 differ in fundamental
        self.assertEqual(groups[0]["node_events"], {"A": ["a1"], "B": ["b1"]})
        self.assertFalse(groups[0]["ambiguous"])
        self.assertFalse(groups[0]["verified"])
        crowded = M.associate_events({"A": [self.event("a1", 10.0, 20.0, 160.0), self.event("a3", 12.0, 18.0, 162.0)],
                                      "B": [self.event("b1", 10.4, 20.3, 165.0)]}, s)
        self.assertTrue(crowded[0]["ambiguous"])

    def test_distant_times_are_not_grouped(self):
        groups = M.associate_events({"A": [self.event("a1", 10.0, 12.0, 160.0)], "B": [self.event("b1", 30.0, 32.0, 160.0)]}, site())
        self.assertEqual(groups, [])


class FrameTests(unittest.TestCase):
    def test_geodetic_to_enu(self):
        east, north, up = M.geodetic_to_enu(33.4010, -112.0000, 350.0, 33.4000, -112.0000, 350.0)
        self.assertAlmostEqual(north, 110.9, delta=0.3)
        self.assertAlmostEqual(east, 0.0, delta=0.01)
        east, north, up = M.geodetic_to_enu(33.4000, -111.9990, 360.0, 33.4000, -112.0000, 350.0)
        self.assertAlmostEqual(east, 111.32 * math.cos(math.radians(33.4)), delta=0.3)
        self.assertAlmostEqual(up, 10.0, delta=0.01)

    def test_observation_conversion_and_gates(self):
        s = site()
        s.nodes["B"].heading_deg, s.nodes["B"].face_up = 90.0, False
        record = {"node_id": "A", "bearing_local_deg": 30.0, "bearing_site_deg": None, "calibrated": True, "time_s": 5.0}
        self.assertAlmostEqual(M.observation_to_bearing(record, s).site_azimuth_deg, 30.0)
        self.assertAlmostEqual(M.observation_to_bearing({**record, "node_id": "B"}, s).site_azimuth_deg, 60.0)   # face down mirrors
        self.assertAlmostEqual(M.observation_to_bearing({**record, "bearing_site_deg": 77.0}, s).site_azimuth_deg, 77.0)
        for bad, code in (({**record, "calibrated": False}, "UNCALIBRATED"), ({**record, "synthetic": True}, "SYNTHETIC"),
                          ({**record, "node_id": "Q"}, "BAD_INPUT")):
            with self.assertRaises(M.FusionRejected) as caught:
                M.observation_to_bearing(bad, s)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(M.observation_to_bearing({**record, "synthetic": True, "calibrated": False}, s, allow_synthetic=True).node_id, "A")


class EndToEndTwoNodeTests(unittest.TestCase):
    """Two simulated arrays hear one synthetic source; their observation logs are fused."""

    def test_acoustic_pipeline_to_position(self):
        source_site = np.array([30.0, 40.0, 15.0])
        layout = {"A": (0.0, 0.0, 0.0), "B": (80.0, 0.0, 90.0)}                # east, north, heading
        raw = {"schema": M.SITE_SCHEMA, "limits": {}, "nodes": [
            {"node_id": k, "east_m": e, "north_m": n, "heading_deg": h, "position_sigma_m": 0.3, "heading_sigma_deg": 1.0,
             "bearing_sigma_deg": 3.0} for k, (e, n, h) in layout.items()]}
        s = M.site_from_mapping(raw)
        with tempfile.TemporaryDirectory() as tmp:
            logs = []
            for index, (name, (east, north, heading)) in enumerate(layout.items()):
                offset = source_site[:2] - [east, north]
                local = (math.degrees(math.atan2(offset[1], offset[0])) - heading) % 360.0
                horizontal = float(np.linalg.norm(offset))
                slant = math.hypot(horizontal, source_site[2])
                rotor = PointSource(RotorSignal(f0_hz=170.0, seed=9), static_position(local, math.degrees(math.atan2(source_site[2], horizontal)), slant),
                                    level_at_1m=0.01 * slant, gain=step_gain(3.0))
                scene = Scene(sources=[rotor], sensor_noise_rms=0.0006, diffuse_noise_rms=0.0006, seed=20 + index)
                cfg = make_config(node_id=name)
                log = JsonlWriter(Path(tmp) / f"{name}.jsonl")
                run_pipeline(cfg, "SIMULATED", sim_factory(cfg, scene, limit_chunks=70), observation_log=log)
                log.close()
                logs.append(Path(tmp) / f"{name}.jsonl")
            records = [json.loads(line) for path in logs for line in path.read_text().splitlines()]
        self.assertTrue(all(r["synthetic"] and not r["calibrated"] for r in records))
        with self.assertRaises(M.FusionRejected):
            M.observation_to_bearing(records[0], s)                            # synthetic data needs an explicit opt-in
        tracks = {}
        for record in records:
            b = M.observation_to_bearing(record, s, allow_synthetic=True)
            tracks.setdefault(b.node_id, []).append((b.time_s, b.site_azimuth_deg, b.sigma_deg))
        self.assertEqual(sorted(tracks), ["A", "B"])
        start = max(t[0][0] for t in tracks.values())
        result = M.fuse_tracks({k: sorted(v) for k, v in tracks.items()}, s, start + 2.0)
        error = math.hypot(result["position"]["east_m"] - 30.0, result["position"]["north_m"] - 40.0)
        self.assertLess(error, 2.0)
        self.assertLess(error, result["ellipse_95"]["semi_major_m"])
        self.assertFalse(result["validated_location"])


class LegacyCompatibilityTests(unittest.TestCase):
    def test_revision_a_utility_agrees_on_the_crossing_point(self):
        spec = importlib.util.spec_from_file_location("triangulate", ROOT / "legacy" / "triangulate.py")
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        a = {"node_id": "A", "x_m": 0.0, "y_m": 0.0, "bearing_deg": 50.0, "bearing_sigma_deg": 2.0, "timestamp_s": 1000.0,
             "source_event_id": "e", "calibrated": True}
        b = {**a, "node_id": "B", "x_m": 100.0, "bearing_deg": 120.0}
        old = legacy.triangulate(a, b, reference_time=1001.0, samples=1000)["conditional_intersection"]
        new = M.fuse([M.Bearing("A", 50.0), M.Bearing("B", 120.0)], site())["position"]
        self.assertAlmostEqual(old["x_m"], new["east_m"], places=4)
        self.assertAlmostEqual(old["y_m"], new["north_m"], places=4)


def load_tests(loader, standard_tests, _pattern):
    """Also run the unchanged Revision A triangulation tests."""
    legacy_dir = str(ROOT / "legacy")
    sys.path.insert(0, legacy_dir)
    try:
        standard_tests.addTests(loader.loadTestsFromName("test_triangulate"))
    finally:
        sys.path.remove(legacy_dir)
    return standard_tests


if __name__ == "__main__":
    unittest.main()
