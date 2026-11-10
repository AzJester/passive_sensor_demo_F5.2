"""Passive acoustic bearing estimator and transparent spectral heuristics.

Prototype, not a trained drone classifier. Audio columns must be simultaneous
raw microphones whose positions match geometry, in metres. Azimuth is measured
counterclockwise from local +x; it points FROM the array TO the sound source.
No range, altitude, aircraft identity, or probability is inferred.
"""
from __future__ import annotations

import itertools
import math
import numpy as np


DEFAULT_GEOMETRY = np.array([
    [0.033, -0.033, 0.0], [0.033, 0.033, 0.0],
    [-0.033, 0.033, 0.0], [-0.033, -0.033, 0.0],
], dtype=float)


def _geometry(value):
    g = np.array(DEFAULT_GEOMETRY if value is None else value, dtype=float, copy=True)
    if g.ndim != 2 or g.shape[1] != 3 or len(g) < 3 or not np.isfinite(g).all():
        raise ValueError("geometry must contain at least three finite XYZ positions in metres")
    if np.linalg.matrix_rank(g - g.mean(axis=0)) < 2:
        raise ValueError("geometry must span at least two dimensions for unambiguous azimuth")
    if min(np.linalg.norm(g[i] - g[j]) for i, j in itertools.combinations(range(len(g)), 2)) < 1e-4:
        raise ValueError("microphone positions must be distinct")
    return g


def _blank(health, dbfs=-160.0):
    return {"dbfs": float(dbfs), "bearing_deg": None,
            "localization_score": 0.0, "ambiguity": True,
            "harmonic_score": 0.0, "health": health,
            "peak_score": 0.0, "effective_bandwidth_hz": 0.0,
            "coherence_score": 0.0}


class Analyzer:
    """Cache SRP-PHAT steering lags for a microphone geometry.

    Default localization band 350..1800 Hz is deliberately conservative for the
    default array's 93.3 mm maximum pair spacing. Elevation is marginalized on
    a 0..75 degree grid; this is a nuisance parameter, not an elevation output.
    Score is normalized peak-to-median contrast, NOT confidence/probability.
    Narrowband and competing-peak cases suppress the reported bearing.
    """

    def __init__(self, fs=16000, geometry=None, temperature_c=20.0,
                 band_hz=(350.0, 1800.0), azimuth_step_deg=2.0):
        if not math.isfinite(float(fs)) or float(fs) < 8000:
            raise ValueError("sample rate must be finite and at least 8000 Hz")
        if not math.isfinite(float(temperature_c)) or not -30 <= temperature_c <= 60:
            raise ValueError("temperature must be between -30 and 60 Celsius")
        low, high = map(float, band_hz)
        if not (0 < low < high < float(fs) / 2):
            raise ValueError("band must lie strictly within Nyquist")
        if not 0.5 <= float(azimuth_step_deg) <= 10:
            raise ValueError("azimuth step must be between 0.5 and 10 degrees")
        self.fs = int(fs)
        self.geometry = _geometry(geometry)
        self.c = 331.3 + 0.606 * float(temperature_c)
        self.band = (low, high)
        self.azimuths = np.arange(0.0, 360.0, float(azimuth_step_deg))
        self.elevations = np.arange(0.0, 76.0, 15.0)
        az, el = np.meshgrid(np.deg2rad(self.azimuths), np.deg2rad(self.elevations))
        self.directions = np.stack((np.cos(el)*np.cos(az),
                                    np.cos(el)*np.sin(az), np.sin(el)), axis=-1).reshape(-1, 3)
        self.pairs = list(itertools.combinations(range(len(self.geometry)), 2))
        # IFFT(X_i conj(X_j)) peaks at t_i - t_j. For a source toward +u,
        # t_i - t_j = (r_j-r_i).u/c, NOT the opposite sign.
        self.lags = np.array([self.directions @ (self.geometry[j] - self.geometry[i]) / self.c
                              for i, j in self.pairs])
        self.max_delay = max(np.linalg.norm(self.geometry[j] - self.geometry[i]) / self.c
                             for i, j in self.pairs)

    def analyze(self, audio4):
        audio = np.asarray(audio4, dtype=float)
        if audio.ndim != 2 or audio.shape[1] != len(self.geometry) or len(audio) < 256:
            raise ValueError("audio must have shape (at least 256 frames, number of microphones)")
        if not np.isfinite(audio).all():
            return _blank("NONFINITE_AUDIO")
        rms = float(np.sqrt(np.mean(audio * audio)))
        dbfs = 20.0 * math.log10(max(rms, 1e-8))
        if np.max(np.abs(audio)) >= 0.999:
            return _blank("CLIPPING", dbfs)
        if rms < 1e-7:
            return _blank("SILENCE", dbfs)
        audio = audio - audio.mean(axis=0, keepdims=True)
        per_mic_rms = np.sqrt(np.mean(audio * audio, axis=0))
        if np.min(per_mic_rms) < 1e-7:
            return _blank("MISSING_CHANNEL", dbfs)
        if any(np.max(np.abs(audio[:, i] - audio[:, j])) < 1e-10 for i, j in self.pairs):
            return _blank("DUPLICATE_CHANNEL", dbfs)
        n = len(audio)
        nfft = 1 << int(math.ceil(math.log2(2 * n)))
        x = np.fft.rfft(audio * np.hanning(n)[:, None], n=nfft, axis=0)
        frequencies = np.fft.rfftfreq(nfft, 1 / self.fs)
        mask = (frequencies >= self.band[0]) & (frequencies <= self.band[1])
        power = np.mean(np.abs(x)**2, axis=1)
        p = power[mask]
        if p.sum() <= 1e-16:
            return _blank("NO_INBAND_ENERGY", dbfs)
        distribution = p / p.sum()
        effective_bw = float(np.exp(-np.sum(distribution * np.log(np.maximum(distribution, 1e-30))))
                             * self.fs / nfft)
        harmonic = self._harmonic_score(power, frequencies)
        oversample = 8
        gcc_n = nfft * oversample
        limit = int(math.ceil(self.max_delay * self.fs * oversample)) + 2
        lag_axis = np.arange(-limit, limit + 1) / (self.fs * oversample)
        score = np.zeros(len(self.directions))
        active_pairs = 0
        pair_coherence = []
        for pair_index, (i, j) in enumerate(self.pairs):
            cross = x[:, i] * np.conj(x[:, j])
            unweighted = np.fft.irfft(np.where(mask, cross, 0), n=nfft)
            ei = float(np.fft.irfft(np.where(mask, np.abs(x[:, i])**2, 0), n=nfft)[0])
            ej = float(np.fft.irfft(np.where(mask, np.abs(x[:, j])**2, 0), n=nfft)[0])
            coarse_limit = int(math.ceil(self.max_delay * self.fs)) + 1
            physical = np.concatenate((unweighted[-coarse_limit:], unweighted[:coarse_limit+1]))
            pair_coherence.append(float(np.max(np.abs(physical))) / max(math.sqrt(ei*ej), 1e-30))
            magnitude = np.abs(cross)
            floor = max(float(magnitude[mask].max()) * 1e-6, 1e-20)
            phat = np.zeros_like(cross)
            useful = mask & (magnitude > floor)
            phat[useful] = cross[useful] / magnitude[useful]
            gcc = np.fft.irfft(phat, n=gcc_n)
            segment = np.concatenate((gcc[-limit:], gcc[:limit+1]))
            scale = float(np.max(np.abs(segment)))
            if scale > 1e-12:
                score += np.interp(self.lags[pair_index], lag_axis, segment) / scale
                active_pairs += 1
        if active_pairs != len(self.pairs):
            return _blank("INSUFFICIENT_COHERENCE", dbfs)
        coherence = float(np.clip(np.mean(pair_coherence), 0, 1))
        score /= active_pairs
        # Marginalize nuisance elevation instead of forcing every source into
        # the array plane, which would bias airborne-source azimuth estimates.
        profile = score.reshape(len(self.elevations), len(self.azimuths)).max(axis=0)
        best = int(np.argmax(profile))
        peak = float(profile[best])
        contrast = float(np.clip((peak - np.median(profile)) / max(abs(peak), 1e-12), 0, 1))
        distance = np.abs((self.azimuths - self.azimuths[best] + 180) % 360 - 180)
        second = float(np.max(profile[distance >= 35]))
        # These prototype thresholds are engineering heuristics. They need
        # calibration against measured source/background data, not relabeling
        # as validated detection probabilities.
        ambiguous = (effective_bw < 80.0 or coherence < 0.20 or peak < 0.40
                     or contrast < 0.15 or second > 0.985 * peak)
        return {"dbfs": dbfs,
                "bearing_deg": None if ambiguous else float(self.azimuths[best]),
                "localization_score": contrast, "ambiguity": bool(ambiguous),
                "harmonic_score": harmonic,
                "health": ("NARROWBAND" if effective_bw < 80 else
                           "INSUFFICIENT_COHERENCE" if coherence < 0.20 else "OK"),
                "peak_score": peak, "effective_bandwidth_hz": effective_bw,
                "coherence_score": coherence}

    def _harmonic_score(self, power, frequencies):
        """Maximum energy fraction in a trial harmonic comb, not a classifier.

        Comb fundamentals span 70..350 Hz. Six harmonics are measured with
        +/- one FFT-bin neighborhoods. Fans and machinery can score highly.
        """
        keep = (frequencies >= 70) & (frequencies <= min(3500, self.fs/2 - 1))
        total = float(power[keep].sum())
        if total <= 1e-16:
            return 0.0
        resolution = self.fs / ((len(frequencies)-1)*2)
        best = 0.0
        for f0 in np.arange(70, 351, 5):
            bins = set()
            for harmonic in range(1, 7):
                index = int(round(f0 * harmonic / resolution))
                bins.update(k for k in range(index-1, index+2) if 0 <= k < len(power) and keep[k])
            best = max(best, float(power[list(bins)].sum()) / total)
        return float(np.clip(best, 0, 1))


def analyze(audio4, fs=16000, geometry=None, temperature_c=20.0):
    return Analyzer(fs, geometry, temperature_c).analyze(audio4)


def synthesize_source(azimuth_deg=0.0, elevation_deg=0.0, fs=16000,
                      seconds=0.256, geometry=None, seed=1, rotor=False):
    """Deterministic far-field synthetic audio, NOT measured drone audio.

    Fractional delays are applied in frequency domain. The periodic sequence
    has a shared clock by construction; it cannot test real USB synchronization,
    microphone mapping, room reflections, wind, or hardware calibration.
    """
    g = _geometry(geometry)
    n = int(round(float(seconds) * fs))
    if n < 256:
        raise ValueError("synthetic duration must provide at least 256 frames")
    az, el = math.radians(float(azimuth_deg)), math.radians(float(elevation_deg))
    u = np.array([math.cos(el)*math.cos(az), math.cos(el)*math.sin(az), math.sin(el)])
    rng = np.random.default_rng(seed)
    frequencies = np.fft.rfftfreq(n, 1/fs)
    spectrum = np.fft.rfft(rng.normal(size=n))
    spectrum[(frequencies < 250) | (frequencies > 3500)] = 0
    base = np.fft.irfft(spectrum, n=n)
    if rotor:
        time = np.arange(n) / fs
        base = 0.65 * base
        for k in range(1, 10):
            base += (1.0 / math.sqrt(k)) * np.sin(2*np.pi*155*k*time + 0.17*k)
    spectrum = np.fft.rfft(base)
    offsets = g @ u / 343.42
    channels = np.stack([np.fft.irfft(spectrum * np.exp(2j*np.pi*frequencies*offset), n=n)
                         for offset in offsets], axis=1)
    channels *= 0.075 / max(float(np.sqrt(np.mean(channels**2))), 1e-12)
    channels += rng.normal(scale=0.0015, size=channels.shape)
    return channels
