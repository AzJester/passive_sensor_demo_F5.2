"""Optional, bearing-only ray geometry demonstration.

Coordinates are metres east (x) and north (y). Bearings are mathematical
degrees counterclockwise from global east, not compass bearings. Each input
JSON object must contain node_id, x_m, y_m, bearing_deg, bearing_sigma_deg,
timestamp_s, source_event_id, and calibrated=true.

This module does not establish that observations came from the same acoustic
source, compensate acoustic travel time, or validate any drone location.
Reception-time proximity alone cannot resolve moving-source association.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import sys
import time
from typing import Any


MAX_RECEPTION_AGE_S = 2.0
MAX_INTER_NODE_TIME_DIFFERENCE_S = 0.25
MIN_BASELINE_M = 1.0
MIN_ACUTE_CROSSING_DEG = 20.0
MIN_MONTE_CARLO_ACCEPTANCE = 0.90
MAX_RADIUS_BASELINE_MULTIPLE = 5.0


class GeometryError(ValueError):
    """Input or geometry is unsuitable for this demonstration."""


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{name} must be a JSON number")
    try:
        result = float(value)
    except OverflowError as error:
        raise GeometryError(f"{name} must be finite") from error
    if not math.isfinite(result):
        raise GeometryError(f"{name} must be finite")
    return result


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeometryError(f"{name} must be a nonempty string")
    return value.strip()


def _observation(raw: dict[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GeometryError(f"{name} must be a JSON object")
    if raw.get("calibrated") is not True:
        raise GeometryError(f"{name}.calibrated must be true")
    result = {
        "node_id": _nonempty_string(raw.get("node_id"), f"{name}.node_id"),
        "source_event_id": _nonempty_string(
            raw.get("source_event_id"), f"{name}.source_event_id"
        ),
    }
    for key in ("x_m", "y_m", "bearing_deg", "bearing_sigma_deg", "timestamp_s"):
        result[key] = _number(raw.get(key), f"{name}.{key}")
    if result["bearing_sigma_deg"] < 1.0:
        raise GeometryError(f"{name}.bearing_sigma_deg must be at least 1 degree")
    result["bearing_deg"] %= 360.0
    return result


def _cross(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _intersection(
    a: dict[str, Any], b: dict[str, Any], angle_a: float, angle_b: float
) -> tuple[float, float, float]:
    # acos(abs(dot)) gives the acute angle of the two bearing lines.
    if not math.isfinite(angle_a) or not math.isfinite(angle_b):
        raise GeometryError("bearing angles must be finite")
    ra, rb = math.radians(angle_a % 360.0), math.radians(angle_b % 360.0)
    ua = (math.cos(ra), math.sin(ra))
    ub = (math.cos(rb), math.sin(rb))
    determinant = _cross(ua, ub)
    dot = min(1.0, max(-1.0, ua[0] * ub[0] + ua[1] * ub[1]))
    acute = math.degrees(math.acos(abs(dot)))
    if abs(determinant) < 1e-12 or acute < MIN_ACUTE_CROSSING_DEG:
        raise GeometryError("acute bearing crossing angle must be at least 20 degrees")
    delta = (b["x_m"] - a["x_m"], b["y_m"] - a["y_m"])
    distance_a = _cross(delta, ub) / determinant
    distance_b = _cross(delta, ua) / determinant
    if distance_a < 0.0 or distance_b < 0.0:
        raise GeometryError("bearing-ray intersection is behind at least one node")
    x = a["x_m"] + distance_a * ua[0]
    y = a["y_m"] + distance_a * ua[1]
    if not math.isfinite(x) or not math.isfinite(y):
        raise GeometryError("bearing-ray intersection is not finite")
    return x, y, acute


def triangulate(
    a: dict[str, Any],
    b: dict[str, Any],
    reference_time: float | None = None,
    samples: int = 4000,
    seed: int = 20261109,
) -> dict[str, Any]:
    """Return guarded geometry or raise GeometryError.

    The empirical radius is measured about the nominal intersection. Invalid
    Monte Carlo rays count as unbounded draws, rather than being discarded
    before the 90th percentile is computed. This is a sensitivity calculation
    under independent Gaussian angular errors, not calibrated accuracy.
    """
    a = _observation(a, "observation_a")
    b = _observation(b, "observation_b")
    reference = _number(time.time() if reference_time is None else reference_time,
                        "reference_time")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1000 <= samples <= 100000:
        raise GeometryError("samples must be an integer from 1000 to 100000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GeometryError("seed must be an integer")
    if a["node_id"] == b["node_id"]:
        raise GeometryError("observations must have different node_id values")
    if a["source_event_id"] != b["source_event_id"]:
        raise GeometryError("source_event_id values must match")
    ages = [reference - observation["timestamp_s"] for observation in (a, b)]
    if any(age < 0.0 for age in ages):
        raise GeometryError("observation timestamps must not be in the future")
    if any(age > MAX_RECEPTION_AGE_S for age in ages):
        raise GeometryError("observation reception age exceeds 2 seconds")
    time_difference = abs(a["timestamp_s"] - b["timestamp_s"])
    if time_difference > MAX_INTER_NODE_TIME_DIFFERENCE_S:
        raise GeometryError("inter-node reception-time difference exceeds 0.25 seconds")
    baseline = math.hypot(b["x_m"] - a["x_m"], b["y_m"] - a["y_m"])
    if not math.isfinite(baseline):
        raise GeometryError("node baseline must be finite")
    if baseline < MIN_BASELINE_M:
        raise GeometryError("node baseline must be at least 1 metre")
    x, y, acute = _intersection(a, b, a["bearing_deg"], b["bearing_deg"])

    rng = random.Random(seed)
    deviations = []
    for _ in range(samples):
        angle_a = rng.gauss(a["bearing_deg"], a["bearing_sigma_deg"])
        angle_b = rng.gauss(b["bearing_deg"], b["bearing_sigma_deg"])
        try:
            trial_x, trial_y, _ = _intersection(a, b, angle_a, angle_b)
        except GeometryError:
            continue
        deviations.append(math.hypot(trial_x - x, trial_y - y))
    accepted = len(deviations)
    acceptance = accepted / samples
    if acceptance < MIN_MONTE_CARLO_ACCEPTANCE:
        raise GeometryError(
            f"Monte Carlo geometry acceptance {acceptance:.3f} is below 0.90"
        )
    deviations.sort()
    # Nearest-rank percentile over ALL draws, with invalid draws at infinity.
    radius_rank = math.ceil(0.90 * samples)
    if accepted < radius_rank:
        raise GeometryError("90 percent of all Monte Carlo draws are not bounded")
    radius = deviations[radius_rank - 1]
    if radius > MAX_RADIUS_BASELINE_MULTIPLE * baseline:
        raise GeometryError("empirical 90 percent radius exceeds five times the baseline")

    return {
        "status": "geometry_only_not_validated",
        "validated_location": False,
        "same_source_assumption_verified": False,
        "source_event_id": a["source_event_id"],
        "node_ids": [a["node_id"], b["node_id"]],
        "coordinate_convention": "x east metres; y north metres; bearing CCW from global east",
        "reference_time_s": reference,
        "maximum_reception_age_s": max(ages),
        "inter_node_reception_time_difference_s": time_difference,
        "baseline_m": baseline,
        "acute_crossing_angle_deg": acute,
        "conditional_intersection": {"x_m": x, "y_m": y},
        "monte_carlo": {
            "samples": samples,
            "seed": seed,
            "accepted_samples": accepted,
            "geometry_acceptance_fraction": acceptance,
            "empirical_90_percent_horizontal_radius_m": radius,
            "radius_center": "nominal conditional intersection",
            "invalid_draw_treatment": "unbounded; included in percentile population",
            "error_model": "independent zero-mean Gaussian bearing errors using supplied sigma",
        },
        "limitations": [
            "Geometry demonstration only; no validated drone location or range accuracy.",
            "Matching source_event_id is an input assertion, not independent source association.",
            "Reception-time gating does not correct acoustic travel delay or moving-source association.",
            "Node position errors, heading biases, correlated errors, and atmospheric effects are not modeled.",
            "The empirical radius is angular-error sensitivity, not a calibrated confidence region.",
        ],
    }


def _load_json(path: str) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, ValueError) as error:
        raise GeometryError(f"cannot read observation JSON: {error}") from error
    if not isinstance(result, dict):
        raise GeometryError("each observation file must contain one JSON object")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation_a", help="first observation JSON file")
    parser.add_argument("observation_b", help="second observation JSON file")
    parser.add_argument("--reference-time", type=float, default=None,
                        help="Unix seconds for freshness checks; default current time")
    parser.add_argument("--samples", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=20261109)
    args = parser.parse_args(argv)
    try:
        result = triangulate(_load_json(args.observation_a), _load_json(args.observation_b),
                             reference_time=args.reference_time,
                             samples=args.samples, seed=args.seed)
    except GeometryError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
