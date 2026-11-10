"""Array geometry, angle conventions, and speed of sound.

Conventions used everywhere in this package
-------------------------------------------
Local array frame
    +X and +Y lie in the microphone plane. +Z points out of the microphone
    port side of the board (up, for a face-up mount). Local azimuth is
    measured counterclockwise from +X when viewed from +Z. Elevation is
    measured from the array plane toward +Z.

Bearing
    A bearing points FROM the array TOWARD the apparent sound source.

Site frame
    East-North-Up in metres. "Site azimuth" is measured counterclockwise
    from East (a mathematical angle). "Compass bearing" is measured
    clockwise from true North. Use the conversion functions below. Never
    mix the two.

Arrival time
    A plane wave arriving from unit direction u reaches a microphone at
    position r at time t = -(r . u) / c relative to the array origin. A
    microphone closer to the source hears the wave earlier.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

# Geometry carried over from the Revision A package. It is an assumption
# until the firmware geometry readback and the physical axis test confirm it.
ASSUMED_GEOMETRY_M = (
    (0.033, -0.033, 0.0),
    (0.033, 0.033, 0.0),
    (-0.033, 0.033, 0.0),
    (-0.033, -0.033, 0.0),
)


def speed_of_sound(temperature_c: float) -> float:
    """Speed of sound in dry air, metres per second.

    Humidity raises the value by up to about 0.5 percent. Wind and
    temperature gradients bend the path and are not modelled.
    """
    t = float(temperature_c)
    if not math.isfinite(t) or not -40.0 <= t <= 60.0:
        raise ValueError("temperature_c must be between -40 and 60")
    return 331.3 * math.sqrt(1.0 + t / 273.15)


def validate_geometry(value) -> np.ndarray:
    """Return an (M, 3) float array or raise ValueError."""
    g = np.array(value, dtype=float, copy=True)
    if g.ndim != 2 or g.shape[1] != 3 or len(g) < 3 or not np.isfinite(g).all():
        raise ValueError("geometry must hold at least three finite XYZ positions in metres")
    if np.linalg.matrix_rank(g - g.mean(axis=0), tol=1e-6) < 2:
        raise ValueError("microphones on one line cannot resolve azimuth without ambiguity")
    spacing = [float(np.linalg.norm(g[i] - g[j])) for i, j in pairs(len(g))]
    if min(spacing) < 1e-3:
        raise ValueError("microphone positions must be at least 1 mm apart")
    if max(spacing) > 2.0:
        raise ValueError("geometry larger than 2 m is outside the far-field model used here")
    return g


def is_planar(geometry: np.ndarray) -> bool:
    g = np.asarray(geometry, dtype=float)
    return np.linalg.matrix_rank(g - g.mean(axis=0), tol=1e-6) < 3


def pairs(count: int) -> list[tuple[int, int]]:
    return list(itertools.combinations(range(count), 2))


def unit_vector(azimuth_deg: float, elevation_deg: float = 0.0) -> np.ndarray:
    az, el = math.radians(float(azimuth_deg)), math.radians(float(elevation_deg))
    return np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])


def expected_tdoa(geometry, azimuth_deg, elevation_deg, c) -> np.ndarray:
    """Far-field arrival-time difference t_i - t_j for every pair (i, j), seconds."""
    g = np.asarray(geometry, dtype=float)
    u = unit_vector(azimuth_deg, elevation_deg)
    return np.array([float((g[j] - g[i]) @ u) / c for i, j in pairs(len(g))])


def max_pair_spacing(geometry) -> float:
    g = np.asarray(geometry, dtype=float)
    return max(float(np.linalg.norm(g[i] - g[j])) for i, j in pairs(len(g)))


def min_pair_spacing(geometry) -> float:
    g = np.asarray(geometry, dtype=float)
    return min(float(np.linalg.norm(g[i] - g[j])) for i, j in pairs(len(g)))


def alias_frequency_hz(spacing_m: float, c: float) -> float:
    """Lowest frequency at which one pair can show a single-tone phase ambiguity."""
    return c / (2.0 * float(spacing_m))


def wrap360(angle_deg):
    return np.mod(angle_deg, 360.0)


def wrap180(angle_deg):
    """Wrap to the interval [-180, 180)."""
    return (np.asarray(angle_deg, dtype=float) + 180.0) % 360.0 - 180.0


def angular_difference(a_deg, b_deg):
    """Signed smallest difference a - b in degrees."""
    return wrap180(np.asarray(a_deg, dtype=float) - np.asarray(b_deg, dtype=float))


def circular_mean_deg(values, weights=None):
    """Return (mean angle in degrees, resultant length 0..1). Empty input gives (None, 0)."""
    v = np.asarray(list(values), dtype=float)
    if v.size == 0:
        return None, 0.0
    w = np.ones_like(v) if weights is None else np.asarray(list(weights), dtype=float)
    total = float(w.sum())
    if total <= 0:
        return None, 0.0
    s = float((w * np.sin(np.radians(v))).sum()) / total
    c = float((w * np.cos(np.radians(v))).sum()) / total
    length = math.hypot(s, c)
    if length < 1e-12:
        return None, 0.0
    return float(math.degrees(math.atan2(s, c)) % 360.0), float(min(length, 1.0))


def circular_std_deg(values) -> float | None:
    """Circular standard deviation in degrees, or None for fewer than two values."""
    v = list(values)
    if len(v) < 2:
        return None
    _, length = circular_mean_deg(v)
    if length <= 0.0:
        return 180.0
    return float(math.degrees(math.sqrt(max(-2.0 * math.log(length), 0.0))))


def local_to_site_azimuth(local_azimuth_deg: float, heading_deg: float, face_up: bool = True) -> float:
    """Convert a local bearing to a site azimuth (counterclockwise from East).

    heading_deg is the site azimuth of the array's local +X axis. A face-down
    mount mirrors the sense of rotation.
    """
    local = float(local_azimuth_deg)
    return float((heading_deg + (local if face_up else -local)) % 360.0)


def site_azimuth_to_compass(azimuth_deg: float) -> float:
    """Counterclockwise-from-East angle to clockwise-from-North compass bearing."""
    return float((90.0 - float(azimuth_deg)) % 360.0)


def compass_to_site_azimuth(compass_deg: float) -> float:
    return float((90.0 - float(compass_deg)) % 360.0)
