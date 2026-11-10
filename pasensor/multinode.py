"""Bearing-only fusion across several calibrated nodes.

What this module is
    A position ESTIMATE from two or more bearings, with an uncertainty that
    includes bearing noise, each node's heading error, and each node's
    surveyed-position error. It rejects weak geometry instead of reporting it.

What it is not
    * Not proof of a location. Two rays always cross somewhere. With only two
      nodes there is no redundancy, so a wrong bearing (a reflection, another
      source) produces a confident wrong answer. Three or more nodes allow a
      consistency test.
    * Not raw inter-node TDOA. Time-difference localization between nodes
      needs the nodes' sample clocks aligned to a small fraction of a
      millisecond. These nodes are free-running USB devices on separate
      computers. That needs a separate synchronization design.

Coordinates
    Site frame: East and North in metres. Site azimuth is counterclockwise
    from East. A node's heading is the site azimuth of its array +X axis.

Sound travel time
    A node hears the source where it WAS, range/c seconds earlier. Nodes at
    different ranges therefore hear the same emission at different times.
    fuse_tracks() aligns observations on emission time. fuse() cannot; it
    reports the size of the motion error instead.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .geometry import local_to_site_azimuth, speed_of_sound, wrap180

SITE_SCHEMA = "pasensor.site.v1"
CHI2_95 = {1: 3.841, 2: 5.991, 3: 7.815, 4: 9.488, 5: 11.070, 6: 12.592, 7: 14.067, 8: 15.507, 9: 16.919, 10: 18.307}
K95 = math.sqrt(5.991)  # 95 percent scale for a two-dimensional Gaussian
LIMITATIONS = [
    "Bearing-only estimate. Not a validated location.",
    "The bearings are assumed to point at one source. That assumption is not verified.",
    "Reflections and competing sources give wrong bearings that this calculation cannot detect with two nodes.",
    "Uncertainty assumes Gaussian errors of the stated sizes. Use measured calibration errors, not guesses.",
    "Heading and node-position errors are fixed for a deployment. Averaging many fixes does not remove them.",
    "Height is ignored. A source well above the node plane is projected onto it.",
]


class FusionRejected(ValueError):
    """The inputs or the geometry do not support an estimate. code names the reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Node:
    node_id: str
    east_m: float
    north_m: float
    heading_deg: float
    position_sigma_m: float
    heading_sigma_deg: float
    bearing_sigma_deg: float
    face_up: bool = True
    clock_sigma_s: float = 0.05


@dataclass
class Site:
    nodes: dict
    temperature_c: float = 20.0
    min_crossing_deg: float = 20.0
    max_relative_error: float = 0.5
    max_range_m: float | None = None
    max_time_spread_s: float | None = None

    @property
    def c(self) -> float:
        return speed_of_sound(self.temperature_c)

    def max_baseline(self) -> float:
        nodes = list(self.nodes.values())
        return max((math.hypot(a.east_m - b.east_m, a.north_m - b.north_m) for a in nodes for b in nodes), default=0.0)


@dataclass
class Bearing:
    node_id: str
    site_azimuth_deg: float
    sigma_deg: float | None = None
    time_s: float | None = None
    event_id: str | None = None
    extra: dict = field(default_factory=dict)


def _finite(value, name, low=None, high=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise FusionRejected("BAD_INPUT", f"{name} must be a finite number")
    if (low is not None and value < low) or (high is not None and value > high):
        raise FusionRejected("BAD_INPUT", f"{name} must be between {low} and {high}")
    return float(value)


def site_from_mapping(raw: dict) -> Site:
    if not isinstance(raw, dict) or raw.get("schema") != SITE_SCHEMA:
        raise FusionRejected("BAD_INPUT", f"site file must be a JSON object with schema {SITE_SCHEMA}")
    nodes = {}
    for i, item in enumerate(raw.get("nodes", [])):
        name = item.get("node_id")
        if not isinstance(name, str) or not name.strip() or name in nodes:
            raise FusionRejected("BAD_INPUT", f"nodes[{i}].node_id must be a unique non-empty string")
        if not isinstance(item.get("face_up", True), bool):
            raise FusionRejected("BAD_INPUT", f"nodes[{i}].face_up must be true or false")
        nodes[name] = Node(
            name, _finite(item.get("east_m"), f"{name}.east_m"), _finite(item.get("north_m"), f"{name}.north_m"),
            _finite(item.get("heading_deg"), f"{name}.heading_deg"),
            _finite(item.get("position_sigma_m"), f"{name}.position_sigma_m", 0.0, 1000.0),
            _finite(item.get("heading_sigma_deg"), f"{name}.heading_sigma_deg", 0.0, 90.0),
            _finite(item.get("bearing_sigma_deg"), f"{name}.bearing_sigma_deg", 0.5, 90.0),
            item.get("face_up", True), _finite(item.get("clock_sigma_s", 0.05), f"{name}.clock_sigma_s", 0.0, 10.0))
    if len(nodes) < 2:
        raise FusionRejected("TOO_FEW_NODES", "a site needs at least two nodes")
    limits = raw.get("limits", {})
    optional = lambda key: None if limits.get(key) is None else _finite(limits[key], f"limits.{key}", 0.0)  # noqa: E731
    return Site(nodes, _finite(raw.get("temperature_c", 20.0), "temperature_c", -40, 60),
                _finite(limits.get("min_crossing_deg", 20.0), "limits.min_crossing_deg", 5.0, 90.0),
                _finite(limits.get("max_relative_error", 0.5), "limits.max_relative_error", 0.01, 10.0),
                optional("max_range_m"), optional("max_time_spread_s"))


def load_site(path) -> Site:
    try:
        return site_from_mapping(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as error:
        if isinstance(error, FusionRejected):
            raise
        raise FusionRejected("BAD_INPUT", f"cannot read site file {path}: {error}") from error


def geodetic_to_enu(lat_deg, lon_deg, height_m, lat0_deg, lon0_deg, height0_m):
    """WGS84 latitude, longitude, height to East-North-Up metres about an origin."""
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2.0 - f)

    def ecef(lat, lon, h):
        lat, lon = math.radians(lat), math.radians(lon)
        n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
        return np.array([(n + h) * math.cos(lat) * math.cos(lon), (n + h) * math.cos(lat) * math.sin(lon),
                         (n * (1.0 - e2) + h) * math.sin(lat)])

    d = ecef(lat_deg, lon_deg, height_m) - ecef(lat0_deg, lon0_deg, height0_m)
    lat0, lon0 = math.radians(lat0_deg), math.radians(lon0_deg)
    east = -math.sin(lon0) * d[0] + math.cos(lon0) * d[1]
    north = -math.sin(lat0) * math.cos(lon0) * d[0] - math.sin(lat0) * math.sin(lon0) * d[1] + math.cos(lat0) * d[2]
    up = math.cos(lat0) * math.cos(lon0) * d[0] + math.cos(lat0) * math.sin(lon0) * d[1] + math.sin(lat0) * d[2]
    return float(east), float(north), float(up)


def _solve(points: np.ndarray, angles: np.ndarray, sigma_b: np.ndarray, sigma_h: np.ndarray, sigma_p: np.ndarray):
    """Weighted least squares on angle residuals. Returns position, ranges, weights, Jacobian, residuals."""
    normals = np.stack((-np.sin(angles), np.cos(angles)), axis=1)
    w0 = 1.0 / np.maximum(sigma_b ** 2 + sigma_h ** 2, 1e-12)
    lhs = np.einsum("i,ij,ik->jk", w0, normals, normals)
    rhs = np.einsum("i,ij,ik,ik->j", w0, normals, normals, points)
    if abs(np.linalg.det(lhs)) < 1e-12:
        raise FusionRejected("POOR_CROSSING", "the bearings are parallel; they define no crossing point")
    x = np.linalg.solve(lhs, rhs)
    # The lines cross here. If that point is behind a node, the rays do not cross at all.
    if np.any(np.einsum("ij,ij->i", x[None, :] - points, np.stack((np.cos(angles), np.sin(angles)), axis=1)) <= 0):
        raise FusionRejected("BEHIND_NODE", "the bearing lines cross behind at least one node; the rays do not meet")
    for _ in range(25):
        d = x[None, :] - points
        ranges = np.maximum(np.linalg.norm(d, axis=1), 1e-6)
        predicted = np.arctan2(d[:, 1], d[:, 0])
        residual = np.radians(wrap180(np.degrees(angles - predicted)))
        jac = np.stack((-d[:, 1], d[:, 0]), axis=1) / (ranges ** 2)[:, None]
        variance = sigma_b ** 2 + sigma_h ** 2 + (sigma_p / ranges) ** 2
        weights = 1.0 / np.maximum(variance, 1e-12)
        normal_matrix = jac.T @ (weights[:, None] * jac)
        if np.linalg.cond(normal_matrix) > 1e12:
            raise FusionRejected("POOR_CROSSING", "the bearings are too close to parallel to locate a crossing")
        step = np.linalg.solve(normal_matrix, jac.T @ (weights * residual))
        x = x + step
        if float(np.linalg.norm(step)) < 1e-6:
            break
    else:
        raise FusionRejected("NO_CONVERGENCE", "the position solution did not converge")
    d = x[None, :] - points
    ranges = np.linalg.norm(d, axis=1)
    predicted = np.arctan2(d[:, 1], d[:, 0])
    residual = np.radians(wrap180(np.degrees(angles - predicted)))
    jac = np.stack((-d[:, 1], d[:, 0]), axis=1) / (np.maximum(ranges, 1e-6) ** 2)[:, None]
    return x, ranges, jac, residual


def _ellipse(cov: np.ndarray) -> dict:
    values, vectors = np.linalg.eigh(cov)
    major = vectors[:, 1]
    return {"semi_major_m": float(K95 * math.sqrt(max(values[1], 0.0))),
            "semi_minor_m": float(K95 * math.sqrt(max(values[0], 0.0))),
            "major_axis_site_azimuth_deg": float(math.degrees(math.atan2(major[1], major[0])) % 180.0)}


def fuse(bearings: list, site: Site, monte_carlo: int = 0, seed: int = 20261109,
         assumed_max_speed_mps: float | None = None) -> dict:
    """Estimate a position from one bearing per node. Raises FusionRejected."""
    seen = {}
    for b in bearings:
        if b.node_id not in site.nodes:
            raise FusionRejected("BAD_INPUT", f"node '{b.node_id}' is not in the site file")
        if b.node_id in seen:
            raise FusionRejected("BAD_INPUT", f"more than one bearing was given for node '{b.node_id}'")
        seen[b.node_id] = b
    if len(seen) < 2:
        raise FusionRejected("TOO_FEW_NODES", "position needs bearings from at least two different nodes")
    order = sorted(seen)
    nodes = [site.nodes[k] for k in order]
    points = np.array([[n.east_m, n.north_m] for n in nodes])
    angles = np.radians([_finite(seen[k].site_azimuth_deg, f"{k}.site_azimuth_deg") % 360.0 for k in order])
    sigma_b = np.radians([max(_finite(seen[k].sigma_deg if seen[k].sigma_deg is not None else n.bearing_sigma_deg,
                                      f"{k}.sigma_deg", 0.0, 90.0), 0.5) for k, n in zip(order, nodes)])
    sigma_h = np.radians([n.heading_sigma_deg for n in nodes])
    sigma_p = np.array([n.position_sigma_m for n in nodes])
    times = [seen[k].time_s for k in order]
    c = site.c
    time_spread = None
    if all(t is not None for t in times):
        time_spread = max(times) - min(times)
        allowed = site.max_time_spread_s
        if allowed is None:
            allowed = site.max_baseline() / c + 3.0 * max(n.clock_sigma_s for n in nodes) + 0.3
        if time_spread > allowed:
            raise FusionRejected("TIME_SPREAD", f"observation times differ by {time_spread:.2f} s; at most {allowed:.2f} s "
                                                "fits one emission heard at these nodes")
    x, ranges, jac, residual = _solve(points, angles, sigma_b, sigma_h, sigma_p)
    directions = np.stack((np.cos(angles), np.sin(angles)), axis=1)
    if np.any(np.einsum("ij,ij->i", x[None, :] - points, directions) <= 0):
        raise FusionRejected("BEHIND_NODE", "the crossing point lies behind at least one node's bearing")
    lines = (x[None, :] - points) / ranges[:, None]
    crossing = max(math.degrees(math.acos(min(abs(float(lines[i] @ lines[j])), 1.0)))
                   for i in range(len(order)) for j in range(i + 1, len(order)))
    if crossing < site.min_crossing_deg:
        raise FusionRejected("POOR_CROSSING", f"best crossing angle is {crossing:.1f} deg; the limit is {site.min_crossing_deg:.0f} deg")
    if site.max_range_m is not None and float(ranges.max()) > site.max_range_m:
        raise FusionRejected("OUT_OF_RANGE", f"the estimate is {ranges.max():.0f} m from a node; the site limit is {site.max_range_m:.0f} m")
    var_total = sigma_b ** 2 + sigma_h ** 2 + (sigma_p / ranges) ** 2
    weights = 1.0 / var_total
    cov_total = np.linalg.inv(jac.T @ (weights[:, None] * jac))
    gain = cov_total @ jac.T * weights[None, :]
    cov_noise = gain @ np.diag(sigma_b ** 2) @ gain.T
    cov_heading = gain @ np.diag(sigma_h ** 2) @ gain.T
    cov_position = gain @ np.diag((sigma_p / ranges) ** 2) @ gain.T
    ellipse = _ellipse(cov_total)
    if ellipse["semi_major_m"] > site.max_relative_error * float(np.median(ranges)):
        raise FusionRejected("POOR_GEOMETRY", f"95 percent semi-major axis {ellipse['semi_major_m']:.1f} m exceeds "
                                              f"{site.max_relative_error:.2f} of the median range {np.median(ranges):.1f} m")
    dof = len(order) - 2
    chi2 = float(np.sum(residual ** 2 / var_total))
    consistency = {"degrees_of_freedom": dof, "chi_square": chi2 if dof > 0 else None,
                   "limit_95": CHI2_95.get(dof, dof + 2.0 * math.sqrt(2.0 * dof)) if dof > 0 else None,
                   "checked": dof > 0,
                   "note": "No consistency check is possible with two nodes." if dof == 0 else "Residuals fit the stated errors."}
    if dof > 0 and chi2 > consistency["limit_95"]:
        raise FusionRejected("INCONSISTENT_BEARINGS", f"bearings disagree beyond their stated errors (chi-square {chi2:.1f}, "
                                                      f"limit {consistency['limit_95']:.1f}); they may not share one source")
    delays = ranges / c
    result = {
        "status": "bearing_only_estimate", "validated_location": False, "association_verified": False,
        "nodes": order, "position": {"east_m": float(x[0]), "north_m": float(x[1])},
        "covariance_m2": cov_total.tolist(), "ellipse_95": ellipse,
        "sigma_breakdown_m": {"bearing_noise": float(math.sqrt(np.trace(cov_noise))),
                              "node_heading": float(math.sqrt(np.trace(cov_heading))),
                              "node_position": float(math.sqrt(np.trace(cov_position))),
                              "total": float(math.sqrt(np.trace(cov_total)))},
        "range_m": {k: float(r) for k, r in zip(order, ranges)},
        "best_crossing_angle_deg": crossing, "consistency": consistency,
        "residual_deg": {k: float(math.degrees(r)) for k, r in zip(order, residual)},
        "sound_travel_s": {k: float(d) for k, d in zip(order, delays)},
        "time_spread_s": time_spread, "propagation_compensated": False,
        "coordinate_convention": "east and north in metres; azimuth counterclockwise from east",
        "limitations": list(LIMITATIONS),
    }
    spread = float(delays.max() - delays.min())
    result["motion_note"] = (f"Nodes hear this point {spread:.2f} s apart. A source moving at v m/s is displaced by up to "
                             f"about {spread:.2f} x v metres between those hearings. Use track fusion to align on emission time.")
    if assumed_max_speed_mps is not None:
        result["motion_error_bound_m"] = float(spread * assumed_max_speed_mps)
    if monte_carlo:
        result["monte_carlo"] = _monte_carlo(points, angles, sigma_b, sigma_h, sigma_p, x, int(monte_carlo), seed)
    return result


def _monte_carlo(points, angles, sigma_b, sigma_h, sigma_p, nominal, samples, seed) -> dict:
    """Empirical check of the linearized covariance. Failed draws count as unbounded."""
    if not 200 <= samples <= 100000:
        raise FusionRejected("BAD_INPUT", "monte_carlo must be between 200 and 100000")
    rng = np.random.default_rng(seed)
    count = len(angles)
    deviations = []
    for _ in range(samples):
        noisy_angles = angles + rng.normal(0, sigma_b) + rng.normal(0, sigma_h)
        noisy_points = points + rng.normal(0, 1.0, (count, 2)) * sigma_p[:, None]
        try:
            x, *_ = _solve(noisy_points, noisy_angles, sigma_b, sigma_h, sigma_p)
        except FusionRejected:
            continue
        directions = np.stack((np.cos(noisy_angles), np.sin(noisy_angles)), axis=1)
        if np.any(np.einsum("ij,ij->i", x[None, :] - noisy_points, directions) <= 0):
            continue
        deviations.append(float(np.linalg.norm(x - nominal)))
    deviations.sort()
    rank = math.ceil(0.95 * samples)
    return {"samples": samples, "seed": seed, "solved_fraction": len(deviations) / samples,
            "radius_95_m": deviations[rank - 1] if len(deviations) >= rank else None,
            "note": "Radius about the nominal estimate that holds 95 percent of all draws. "
                    "None means more than 5 percent of draws had no valid crossing."}


def _interpolate(track: list, t: float, max_gap_s: float):
    """Circular interpolation of (time, azimuth, sigma) samples at time t, or None."""
    before = [s for s in track if s[0] <= t]
    after = [s for s in track if s[0] >= t]
    if not before or not after:
        return None
    a, b = before[-1], after[0]
    if b[0] - a[0] > max_gap_s:
        return None
    fraction = 0.0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
    azimuth = (a[1] + fraction * float(wrap180(b[1] - a[1]))) % 360.0
    sigma = None if a[2] is None or b[2] is None else max(a[2], b[2])
    return azimuth, sigma


def fuse_tracks(tracks: dict, site: Site, emission_time_s: float, max_gap_s: float = 0.5, monte_carlo: int = 0) -> dict:
    """Fuse bearing tracks at one emission time, allowing for sound travel time.

    tracks maps node_id to a time-sorted list of (reception_time_s,
    site_azimuth_deg, sigma_deg or None). The solution is iterated: ranges give
    each node's reception time for the emission, the tracks are sampled at
    those times, and the position is solved again.
    """
    c = site.c
    delays = {k: 0.0 for k in tracks}
    result = None
    for _ in range(6):
        bearings = []
        for node_id, track in tracks.items():
            sample = _interpolate(track, emission_time_s + delays[node_id], max_gap_s)
            if sample is not None:
                bearings.append(Bearing(node_id, sample[0], sample[1]))
        if len(bearings) < 2:
            raise FusionRejected("TOO_FEW_NODES", "fewer than two nodes have a bearing near the required reception times")
        result = fuse(bearings, site)
        new = {k: result["range_m"][k] / c for k in result["nodes"]}
        change = max(abs(new[k] - delays.get(k, 0.0)) for k in new)
        delays.update(new)
        if change < 1e-4:
            break
    if monte_carlo:
        result = fuse(bearings, site, monte_carlo=monte_carlo)
    result.update(propagation_compensated=True, emission_time_s=emission_time_s,
                  reception_time_s={k: emission_time_s + delays[k] for k in result["nodes"]})
    result.pop("motion_note", None)
    return result


def associate_events(events_by_node: dict, site: Site, f0_tolerance: float = 0.12) -> list:
    """Group candidate events from different nodes that could be one source.

    Two events are compatible when their time intervals overlap after allowing
    for sound travel across the site and clock error, and their fundamental
    estimates agree within the tolerance (Doppler can shift them by several
    percent). This is a plausibility test. It does not prove a common source.
    """
    slack = site.max_baseline() / site.c + 3.0 * max(n.clock_sigma_s for n in site.nodes.values()) + 0.5
    items = [(node, e) for node, events in events_by_node.items() for e in events]

    def compatible(a, b) -> bool:
        if a[0] == b[0]:
            return False
        a0, a1 = a[1]["onset_time_s"], a[1].get("offset_time_s") or a[1]["onset_time_s"]
        b0, b1 = b[1]["onset_time_s"], b[1].get("offset_time_s") or b[1]["onset_time_s"]
        if a0 - slack > b1 or b0 - slack > a1:
            return False
        fa = (a[1].get("evidence") or {}).get("median_f0_hz")
        fb = (b[1].get("evidence") or {}).get("median_f0_hz")
        return fa is None or fb is None or abs(fa - fb) / max(fa, fb) <= f0_tolerance

    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if compatible(items[i], items[j]):
                parent[find(i)] = find(j)
    groups: dict = {}
    for i, (node, event) in enumerate(items):
        groups.setdefault(find(i), {}).setdefault(node, []).append(event["event_id"])
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        out.append({"node_events": members, "ambiguous": any(len(v) > 1 for v in members.values()),
                    "basis": "overlapping time intervals and agreeing fundamental estimates",
                    "time_slack_s": slack, "verified": False})
    return out


def observation_to_bearing(record: dict, site: Site, allow_synthetic: bool = False) -> Bearing:
    """Convert one pasensor.observation.v1 record into a site-frame Bearing."""
    node_id = record.get("node_id")
    if node_id not in site.nodes:
        raise FusionRejected("BAD_INPUT", f"observation from unknown node '{node_id}'")
    if record.get("synthetic") and not allow_synthetic:
        raise FusionRejected("SYNTHETIC", "the observation is simulated; pass allow_synthetic to fuse test data")
    if not record.get("calibrated") and not (record.get("synthetic") and allow_synthetic):
        raise FusionRejected("UNCALIBRATED", f"node '{node_id}' produced this bearing without an accepted calibration")
    node = site.nodes[node_id]
    azimuth = record.get("bearing_site_deg")
    if azimuth is None:
        azimuth = local_to_site_azimuth(_finite(record.get("bearing_local_deg"), "bearing_local_deg"),
                                        node.heading_deg, node.face_up)
    return Bearing(node_id, _finite(azimuth, "bearing_site_deg"), record.get("bearing_sigma_deg"),
                   record.get("time_s"), record.get("event_id"))
