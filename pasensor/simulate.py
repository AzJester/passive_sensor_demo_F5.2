"""Independent time-domain acoustic scene simulator. SYNTHETIC DATA ONLY.

This module exists so the estimator can be tested against signals that were
NOT produced with the estimator's own maths. It works from physical geometry:
each microphone receives the source waveform delayed by (distance / c) and
scaled by (1 / distance). Tonal parts are evaluated in closed form at the
retarded time. Broadband parts use windowed-sinc interpolation in the time
domain. Nothing here uses the frequency-domain phase ramps that the Revision A
generator shared with its estimator.

What it cannot show: real USB timing, real microphone order or polarity,
enclosure diffraction, wind turbulence, real reflections, or real drone
sound. A pass on synthetic data is software evidence only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from .geometry import speed_of_sound, unit_vector, validate_geometry, ASSUMED_GEOMETRY_M

_CHUNK = 8192
_INDEX_OFFSET = 1 << 24


class NoiseSequence:
    """White Gaussian noise with random access by absolute sample index."""

    def __init__(self, seed: int, fir: np.ndarray | None = None):
        self.seed = int(seed)
        self.fir = None if fir is None else np.asarray(fir, dtype=float)
        self._cache: dict[int, np.ndarray] = {}

    def _chunk(self, k: int) -> np.ndarray:
        block = self._cache.get(k)
        if block is None:
            rng = np.random.default_rng([self.seed, k + _INDEX_OFFSET])
            block = rng.standard_normal(_CHUNK)
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[k] = block
        return block

    def _white(self, i0: int, i1: int) -> np.ndarray:
        first, last = i0 // _CHUNK, (i1 - 1) // _CHUNK
        data = np.concatenate([self._chunk(k) for k in range(first, last + 1)])
        return data[i0 - first * _CHUNK: i1 - first * _CHUNK]

    def values(self, i0: int, i1: int) -> np.ndarray:
        """Samples for integer indices i0 <= i < i1 (after the optional FIR)."""
        if self.fir is None:
            return self._white(i0, i1)
        half = len(self.fir) // 2
        return np.convolve(self._white(i0 - half, i1 + half), self.fir, mode="valid")


def bandpass_fir(fs: float, low_hz: float, high_hz: float, taps: int = 63) -> np.ndarray:
    """Windowed-sinc band-pass FIR with unit white-noise output RMS."""
    n = np.arange(taps) - (taps - 1) / 2
    h = (2 * high_hz / fs) * np.sinc(2 * high_hz / fs * n) - (2 * low_hz / fs) * np.sinc(2 * low_hz / fs * n)
    h *= np.hamming(taps)
    return h / math.sqrt(float(np.sum(h * h)))


def sinc_interpolate(fetch: Callable[[int, int], np.ndarray], positions: np.ndarray,
                     half_width: int = 16) -> np.ndarray:
    """Evaluate a sampled sequence at fractional sample positions."""
    positions = np.asarray(positions, dtype=float)
    out = np.empty(len(positions))
    k = np.arange(-half_width + 1, half_width + 1)
    for start in range(0, len(positions), 8192):
        p = positions[start:start + 8192]
        base = np.floor(p).astype(np.int64)
        frac = p - base
        i0 = int(base.min()) - half_width + 1
        i1 = int(base.max()) + half_width + 1
        data = fetch(i0, i1)
        idx = (base - i0)[:, None] + k[None, :]
        x = frac[:, None] - k[None, :]
        kernel = np.sinc(x) * (0.5 + 0.5 * np.cos(np.pi * np.clip(x / half_width, -1, 1)))
        out[start:start + 8192] = (data[idx] * kernel).sum(axis=1)
    return out


@dataclass
class RotorSignal:
    """Synthetic multirotor-like waveform: detuned harmonic combs plus broadband noise.

    Parameters are plausible, not measured. Unit RMS overall.
    """
    f0_hz: float = 160.0
    n_rotors: int = 4
    detune: float = 0.012
    harmonics: int = 16
    rolloff: float = 0.7
    wobble_hz: float = 0.4
    wobble_depth: float = 0.006
    broadband_ratio: float = 0.4
    fs: float = 16000.0
    seed: int = 1
    _noise: NoiseSequence = field(init=False, repr=False)

    def __post_init__(self):
        rng = np.random.default_rng([self.seed, 77])
        spread = rng.uniform(-1.0, 1.0, self.n_rotors) if self.n_rotors > 1 else np.zeros(1)
        self._f = self.f0_hz * (1.0 + self.detune * spread)
        self._phase = rng.uniform(0, 2 * np.pi, (self.n_rotors, self.harmonics))
        self._wobble_phase = rng.uniform(0, 2 * np.pi, self.n_rotors)
        self._amp = np.arange(1, self.harmonics + 1, dtype=float) ** (-self.rolloff)
        tonal_power = self.n_rotors * float(np.sum(self._amp ** 2)) / 2.0
        self._tonal_scale = 1.0 / math.sqrt(tonal_power * (1.0 + self.broadband_ratio ** 2))
        self._noise_scale = self.broadband_ratio / math.sqrt(1.0 + self.broadband_ratio ** 2)
        self._noise = NoiseSequence(self.seed * 7919 + 13, bandpass_fir(self.fs, 200.0, 0.4 * self.fs))

    def evaluate(self, tau: np.ndarray) -> np.ndarray:
        tau = np.asarray(tau, dtype=float)
        out = np.zeros_like(tau)
        nyquist_guard = 0.45 * self.fs
        for r in range(self.n_rotors):
            if self.wobble_hz > 0 and self.wobble_depth > 0:
                warped = tau + self.wobble_depth / (2 * np.pi * self.wobble_hz) * np.sin(
                    2 * np.pi * self.wobble_hz * tau + self._wobble_phase[r])
            else:
                warped = tau
            for h in range(1, self.harmonics + 1):
                if h * self._f[r] >= nyquist_guard:
                    break
                out += self._amp[h - 1] * np.sin(2 * np.pi * h * self._f[r] * warped + self._phase[r, h - 1])
        out *= self._tonal_scale
        if self.broadband_ratio > 0:
            out += self._noise_scale * sinc_interpolate(self._noise.values, tau * self.fs)
        return out


@dataclass
class NoiseSignal:
    """Band-limited noise point source, unit RMS."""
    low_hz: float = 200.0
    high_hz: float = 5000.0
    fs: float = 16000.0
    seed: int = 1
    _noise: NoiseSequence = field(init=False, repr=False)

    def __post_init__(self):
        self._noise = NoiseSequence(self.seed * 104729 + 5, bandpass_fir(self.fs, self.low_hz, self.high_hz))

    def evaluate(self, tau: np.ndarray) -> np.ndarray:
        return sinc_interpolate(self._noise.values, np.asarray(tau, dtype=float) * self.fs)


@dataclass
class ToneSignal:
    """Single sinusoid, unit RMS."""
    frequency_hz: float = 1000.0
    phase: float = 0.3

    def evaluate(self, tau: np.ndarray) -> np.ndarray:
        return math.sqrt(2.0) * np.sin(2 * np.pi * self.frequency_hz * np.asarray(tau, dtype=float) + self.phase)


def static_position(azimuth_deg: float, elevation_deg: float, distance_m: float):
    p = distance_m * unit_vector(azimuth_deg, elevation_deg)
    return lambda t: np.broadcast_to(p, (len(np.atleast_1d(t)), 3))


def linear_path(p0: Sequence[float], velocity: Sequence[float]):
    p0, v = np.asarray(p0, dtype=float), np.asarray(velocity, dtype=float)
    return lambda t: p0[None, :] + np.atleast_1d(t)[:, None] * v[None, :]


def circular_path(radius_m: float, height_m: float, period_s: float, start_deg: float = 0.0,
                  center: Sequence[float] = (0.0, 0.0)):
    def path(t):
        t = np.atleast_1d(t)
        angle = math.radians(start_deg) + 2 * np.pi * t / period_s
        return np.stack((center[0] + radius_m * np.cos(angle), center[1] + radius_m * np.sin(angle),
                         np.full_like(t, height_m, dtype=float)), axis=1)
    return path


@dataclass
class PointSource:
    signal: object
    path: Callable[[np.ndarray], np.ndarray]
    level_at_1m: float = 0.05
    gain: Callable[[np.ndarray], np.ndarray] | None = None
    ground_reflection: float = 0.0
    array_height_m: float = 1.5


class Scene:
    """Render synchronized microphone signals for point sources plus ambient noise.

    Positions are in the local array frame, in metres. Time starts at zero
    and advances with each call to render().
    """

    def __init__(self, fs: float = 16000.0, geometry=None, temperature_c: float = 20.0,
                 sources: Sequence[PointSource] = (), sensor_noise_rms: float = 0.0,
                 diffuse_noise_rms: float = 0.0, wind_noise_rms: float = 0.0, seed: int = 1):
        self.fs = float(fs)
        self.geometry = validate_geometry(ASSUMED_GEOMETRY_M if geometry is None else geometry)
        self.c = speed_of_sound(temperature_c)
        self.sources = list(sources)
        self.sensor_noise_rms = float(sensor_noise_rms)
        self.diffuse_noise_rms = float(diffuse_noise_rms)
        self.wind_noise_rms = float(wind_noise_rms)
        self.index = 0
        self._rng = np.random.default_rng([int(seed), 991])
        el = np.degrees(np.arcsin(self._rng.uniform(0.0, 1.0, 16)))
        az = self._rng.uniform(0.0, 360.0, 16)
        self._diffuse_dirs = np.array([unit_vector(a, e) for a, e in zip(az, el)])

    # -- point sources -----------------------------------------------------
    def _receive(self, source: PointSource, t: np.ndarray, mirror: bool) -> np.ndarray:
        out = np.zeros((len(t), len(self.geometry)))
        for m, r in enumerate(self.geometry):
            tau = t.copy()
            for _ in range(3):  # fixed-point solve of the retarded time
                p = np.array(source.path(tau), dtype=float)
                if mirror:
                    p[:, 2] = -p[:, 2] - 2.0 * source.array_height_m
                distance = np.linalg.norm(p - r[None, :], axis=1)
                tau = t - distance / self.c
            amplitude = source.level_at_1m / np.maximum(distance, 0.1)
            if source.gain is not None:
                amplitude = amplitude * source.gain(tau)
            if mirror:
                amplitude = amplitude * source.ground_reflection
            out[:, m] = amplitude * source.signal.evaluate(tau)
        return out

    # -- ambient -----------------------------------------------------------
    def _diffuse(self, n: int) -> np.ndarray:
        f = np.fft.rfftfreq(n, 1.0 / self.fs)
        shape = np.where(f >= 40.0, 1.0 / np.sqrt(np.maximum(f, 40.0)), 0.0)
        out = np.zeros((n, len(self.geometry)))
        for u in self._diffuse_dirs:
            spectrum = np.fft.rfft(self._rng.standard_normal(n)) * shape
            for m, r in enumerate(self.geometry):
                out[:, m] += np.fft.irfft(spectrum * np.exp(2j * np.pi * f * float(r @ u) / self.c), n=n)
        rms = float(np.sqrt(np.mean(out ** 2)))
        return out * (self.diffuse_noise_rms / max(rms, 1e-30))

    def _wind(self, n: int) -> np.ndarray:
        f = np.fft.rfftfreq(n, 1.0 / self.fs)
        shape = np.where(f >= 5.0, 1.0 / np.maximum(f, 5.0) ** 1.5, 0.0)
        out = np.stack([np.fft.irfft(np.fft.rfft(self._rng.standard_normal(n)) * shape, n=n)
                        for _ in range(len(self.geometry))], axis=1)
        rms = float(np.sqrt(np.mean(out ** 2)))
        return out * (self.wind_noise_rms / max(rms, 1e-30))

    # -- public ------------------------------------------------------------
    def render(self, n: int) -> np.ndarray:
        t = (self.index + np.arange(n)) / self.fs
        out = np.zeros((n, len(self.geometry)))
        for source in self.sources:
            out += self._receive(source, t, mirror=False)
            if source.ground_reflection > 0:
                out += self._receive(source, t, mirror=True)
        if self.sensor_noise_rms > 0:
            out += self._rng.normal(0.0, self.sensor_noise_rms, out.shape)
        if self.diffuse_noise_rms > 0:
            out += self._diffuse(n)
        if self.wind_noise_rms > 0:
            out += self._wind(n)
        self.index += n
        return out


def static_clip(azimuth_deg: float, elevation_deg: float = 0.0, distance_m: float = 30.0,
                seconds: float = 0.256, kind: str = "rotor", snr_db: float | None = 30.0,
                fs: float = 16000.0, geometry=None, temperature_c: float = 20.0, seed: int = 1,
                f0_hz: float = 160.0, received_rms: float = 0.03, ground_reflection: float = 0.0,
                tone_hz: float = 1000.0) -> np.ndarray:
    """One stationary source plus uncorrelated sensor noise at the requested SNR.

    snr_db is the ratio of received source RMS to per-microphone noise RMS
    over the full band. None means no added noise.
    """
    if kind == "rotor":
        signal = RotorSignal(f0_hz=f0_hz, fs=fs, seed=seed)
    elif kind == "noise":
        signal = NoiseSignal(fs=fs, seed=seed)
    elif kind == "tone":
        signal = ToneSignal(frequency_hz=tone_hz)
    else:
        raise ValueError("kind must be rotor, noise, or tone")
    source = PointSource(signal, static_position(azimuth_deg, elevation_deg, distance_m),
                         level_at_1m=received_rms * distance_m, ground_reflection=ground_reflection)
    noise = 0.0 if snr_db is None else received_rms / (10.0 ** (snr_db / 20.0))
    scene = Scene(fs=fs, geometry=geometry, temperature_c=temperature_c, sources=[source],
                  sensor_noise_rms=noise, seed=seed)
    return scene.render(int(round(seconds * fs)))


def brute_force_delay(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
    """Integer lag (samples) by which a lags b, found by direct correlation.

    Positive means the waveform reaches channel a later than channel b. This
    uses no FFT so tests can check delay sign without the estimator's code.
    """
    best, best_lag = -math.inf, 0
    core = slice(max_lag, len(a) - max_lag)
    for lag in range(-max_lag, max_lag + 1):
        value = float(np.dot(a[core], np.roll(b, lag)[core]))
        if value > best:
            best, best_lag = value, lag
    return best_lag


def duty_gain(start_s: float, on_s: float, off_s: float, ramp_s: float = 1.0):
    """Repeating on/off envelope with raised-cosine ramps."""
    def gain(t):
        t = np.asarray(t, dtype=float)
        phase = np.mod(t - start_s, on_s + off_s)
        g = np.clip(np.minimum(phase, on_s - phase) / ramp_s, 0.0, 1.0)
        g = np.where(t < start_s, 0.0, g)
        return 0.5 - 0.5 * np.cos(np.pi * g)
    return gain


SCENARIOS = {
    "demo": "Ambient noise, a rotor-like source circling the array in bursts, and a brief pure tone.",
    "quiet": "Ambient noise only.",
    "flyby": "One rotor-like source passing on a straight line.",
    "fan": "A steady single-rotor harmonic source. Shows that machinery also raises the heuristic.",
}


def build_scenario(name: str, fs: float, geometry, temperature_c: float, seed: int) -> Scene:
    """Named synthetic scenes for the simulated mode. All levels are arbitrary."""
    ambient = dict(sensor_noise_rms=0.0006, diffuse_noise_rms=0.0006, seed=seed)
    if name == "demo":
        rotor = PointSource(RotorSignal(f0_hz=165.0, fs=fs, seed=seed), circular_path(35.0, 15.0, 80.0, start_deg=20.0),
                            level_at_1m=0.4, gain=duty_gain(14.0, 26.0, 12.0))
        tone = PointSource(ToneSignal(1000.0), static_position(250.0, 5.0, 25.0), level_at_1m=0.1,
                           gain=duty_gain(44.0, 5.0, 71.0))
        return Scene(fs, geometry, temperature_c, [rotor, tone], **ambient)
    if name == "quiet":
        return Scene(fs, geometry, temperature_c, [], **ambient)
    if name == "flyby":
        rotor = PointSource(RotorSignal(f0_hz=190.0, fs=fs, seed=seed), linear_path((-150.0, 40.0, 25.0), (10.0, 0.0, 0.0)),
                            level_at_1m=0.5)
        return Scene(fs, geometry, temperature_c, [rotor], **ambient)
    if name == "fan":
        fan = PointSource(RotorSignal(f0_hz=120.0, n_rotors=1, detune=0.0, wobble_depth=0.0, broadband_ratio=0.2,
                                      fs=fs, seed=seed), static_position(100.0, 0.0, 15.0), level_at_1m=0.12,
                          gain=duty_gain(14.0, 40.0, 20.0))
        return Scene(fs, geometry, temperature_c, [fan], **ambient)
    raise ValueError(f"unknown scenario '{name}'. Choose one of: {', '.join(SCENARIOS)}")
