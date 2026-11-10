"""Per-frame analysis: audio quality, harmonic features, and SRP-PHAT bearing.

Four separate things come out of each frame. They are never merged into one
"confidence" number:

audio     Is the captured block usable at all (clipping, dead channel, ...)?
harmonic  Heuristic evidence that the sound has a harmonic comb, as rotors,
          fans, and engines do. A feature score from 0 to 1. Not a probability.
bearing   Apparent direction of the dominant coherent sound, with the reasons
          a bearing is withheld. Quality metrics, not accuracy.
(identity is produced elsewhere, only when a trained model is installed)

Delay sign (verified by tests that do not use this module's maths):
the cross-spectrum X_i * conj(X_j) has phase -2*pi*f*(t_i - t_j), so its
inverse transform peaks at lag t_i - t_j. For a far-field source in unit
direction u, t_i - t_j = (r_j - r_i) . u / c.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .geometry import (ASSUMED_GEOMETRY_M, is_planar, pairs, speed_of_sound, validate_geometry, wrap180)

AUDIO_FAULTS = ("NONFINITE", "CLIPPING", "SILENCE", "DEAD_CHANNEL", "DUPLICATE_CHANNEL")

REASON_TEXT = {
    "AUDIO_FAULT": "The audio block is not usable.",
    "LOW_COHERENCE": "The microphones do not share enough common sound.",
    "UNSTABLE": "Recent frames do not agree on one direction.",
    "OVERFLOW": "Samples were lost. This block is not used.",
    "UNCALIBRATED": "This unit has no accepted bearing calibration record.",
    "STALE": "No new audio result has arrived.",
    "NARROWBAND": "The coherent sound is a narrow tone. Tones give unreliable bearings.",
    "WEAK_PEAK": "No direction explains the arrival times well.",
    "LOW_CONTRAST": "The best direction barely stands out from the others.",
    "COMPETING_PEAK": "Two separated directions fit almost equally well.",
    "TDOA_INCONSISTENT": "Pairwise arrival times do not fit one plane wave.",
    "NEAR_OVERHEAD_OR_DIFFUSE": "Arrival times are near zero on every pair: overhead source or diffuse noise.",
}


@dataclass
class DspSettings:
    band_hz: tuple = (300.0, 3400.0)
    azimuth_step_deg: float = 2.0
    elevation_grid_deg: tuple = (0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0)
    subframe: int = 512
    oversample: int = 16
    coherence_weighting: str = "debias"
    harmonic_focus: bool = True
    harmonic_focus_min_tones: int = 6
    coherence_significance: float = 0.01
    min_srp_z: float = 8.0
    clip_level: float = 0.999
    level_imbalance_db: float = 6.0
    dead_channel_db: float = 30.0
    dc_offset_warning: float = 0.05
    min_srp_peak: float = 0.4
    min_contrast: float = 0.15
    max_second_peak_ratio: float = 0.92
    second_peak_separation_deg: float = 45.0
    min_coherent_bandwidth_hz: float = 120.0
    max_tdoa_residual_us: float = 25.0
    max_srp_ls_disagreement_deg: float = 25.0
    max_elevation_hint_deg: float = 70.0
    harmonic_band_hz: tuple = (60.0, 4000.0)
    f0_range_hz: tuple = (70.0, 500.0)
    max_harmonics: int = 16
    harmonic_prominence_db: float = 9.0
    harmonic_rel_tolerance: float = 0.015
    floor_window_hz: float = 200.0
    spectrum_points: int = 160
    spectrum_max_hz: float = 4000.0

    @classmethod
    def from_mapping(cls, mapping) -> "DspSettings":
        known = {f.name for f in fields(cls)}
        unknown = set(mapping) - known
        if unknown:
            raise ValueError(f"unknown processing settings: {sorted(unknown)}")
        values = {k: (tuple(v) if isinstance(v, list) else v) for k, v in mapping.items()}
        return cls(**values)


class Analyzer:
    """Analyze fixed-geometry multichannel blocks. One instance per stream."""

    def __init__(self, fs: float = 16000.0, geometry=None, temperature_c: float = 20.0,
                 settings: DspSettings | None = None):
        self.s = settings or DspSettings()
        if not math.isfinite(float(fs)) or float(fs) < 8000:
            raise ValueError("sample rate must be finite and at least 8000 Hz")
        self.fs = float(fs)
        self.geometry = validate_geometry(ASSUMED_GEOMETRY_M if geometry is None else geometry)
        self.planar = is_planar(self.geometry)
        self.c = speed_of_sound(temperature_c)
        low, high = map(float, self.s.band_hz)
        if not 0 < low < high < self.fs / 2:
            raise ValueError("band_hz must lie strictly inside 0..Nyquist")
        if not 0.5 <= float(self.s.azimuth_step_deg) <= 10:
            raise ValueError("azimuth_step_deg must be between 0.5 and 10")
        sub = int(self.s.subframe)
        if sub < 128 or sub & (sub - 1):
            raise ValueError("subframe must be a power of two of at least 128")
        f0_low, f0_high = map(float, self.s.f0_range_hz)
        hb_low, hb_high = map(float, self.s.harmonic_band_hz)
        if not (0 < hb_low <= f0_low < f0_high < hb_high < self.fs / 2):
            raise ValueError("f0_range_hz must lie inside harmonic_band_hz, inside Nyquist")
        self.azimuths = np.arange(0.0, 360.0, float(self.s.azimuth_step_deg))
        self.elevations = np.array(self.s.elevation_grid_deg, dtype=float)
        az, el = np.meshgrid(np.deg2rad(self.azimuths), np.deg2rad(self.elevations))
        directions = np.stack((np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)),
                              axis=-1).reshape(-1, 3)
        self.pairs = pairs(len(self.geometry))
        self.baselines = np.array([self.geometry[j] - self.geometry[i] for i, j in self.pairs])
        self.lags = self.baselines @ directions.T / self.c           # (pairs, directions): t_i - t_j
        self.pair_max_delay = np.linalg.norm(self.baselines, axis=1) / self.c
        self.max_delay = float(self.pair_max_delay.max())
        # sub-frame transform tables
        self._hann_sub = np.hanning(sub + 1)[:-1]
        self._freq_sub = np.fft.rfftfreq(sub, 1.0 / self.fs)
        self._mask = (self._freq_sub >= low) & (self._freq_sub <= high)
        self._band_bins = int(self._mask.sum())
        if self._band_bins < 8:
            raise ValueError("band_hz is too narrow for the chosen subframe")
        self._gcc_n = sub * int(self.s.oversample)
        self._lag_scale = self.fs * int(self.s.oversample)
        self._limit = int(math.ceil(self.max_delay * self._lag_scale)) + 2 * int(self.s.oversample)
        self._lag_axis = np.arange(-self._limit, self._limit + 1) / self._lag_scale
        self._harmonic_cache: dict[int, dict] = {}

    # ------------------------------------------------------------------ api
    def analyze(self, audio) -> dict:
        audio = np.asarray(audio, dtype=float)
        mics = len(self.geometry)
        sub = int(self.s.subframe)
        if audio.ndim != 2 or audio.shape[1] != mics or len(audio) < 2 * sub:
            raise ValueError(f"audio must have shape (at least {2 * sub} frames, {mics} microphones)")
        result = self._blank()
        if not np.isfinite(audio).all():
            result["audio"]["status"] = "NONFINITE"
            return result
        dc = audio.mean(axis=0)
        x = audio - dc
        per_mic_rms = np.sqrt(np.mean(x * x, axis=0))
        rms = float(np.sqrt(np.mean(x * x)))
        peak = float(np.max(np.abs(audio)))
        clipped = int(np.count_nonzero(np.abs(audio) >= self.s.clip_level))
        info = result["audio"]
        info.update(level_dbfs=_db20(rms), peak_dbfs=_db20(peak), clipped_samples=clipped,
                    per_mic_dbfs=[_db20(v) for v in per_mic_rms],
                    dc_offset_max=float(np.max(np.abs(dc))))
        median_rms = float(np.median(per_mic_rms))
        if clipped:
            info["status"] = "CLIPPING"
        elif rms < 1e-7:
            info["status"] = "SILENCE"
        elif float(per_mic_rms.min()) < 1e-7 or _db20(per_mic_rms.min() / max(median_rms, 1e-30)) < -self.s.dead_channel_db:
            info["status"] = "DEAD_CHANNEL"
        elif any(float(np.max(np.abs(x[:, i] - x[:, j]))) < 1e-9 for i, j in self.pairs):
            info["status"] = "DUPLICATE_CHANNEL"
        if info["status"] != "OK":
            return result
        if _db20(per_mic_rms.max() / per_mic_rms.min()) > self.s.level_imbalance_db:
            info["warnings"].append("LEVEL_IMBALANCE")
        if info["dc_offset_max"] > self.s.dc_offset_warning:
            info["warnings"].append("DC_OFFSET")
        result["harmonic"], result["spectrum"], psd = self._harmonic(x)
        result["_psd"] = psd
        self._spatial(x, result)
        return result

    # -------------------------------------------------------------- pieces
    def _blank(self) -> dict:
        return {
            "audio": {"status": "OK", "warnings": [], "level_dbfs": -160.0, "peak_dbfs": -160.0,
                      "per_mic_dbfs": [], "clipped_samples": 0, "dc_offset_max": 0.0},
            "band_level_dbfs": -160.0,
            "bearing": {"available": False, "azimuth_deg": None, "raw_azimuth_deg": None,
                        "reasons": ["AUDIO_FAULT"], "srp_peak": 0.0, "srp_z": 0.0, "srp_contrast": 0.0,
                        "second_peak_ratio": None, "coherence": 0.0, "coherent_bandwidth_hz": 0.0,
                        "tdoa_residual_us": None, "ls_azimuth_deg": None, "elevation_hint_deg": None,
                        "polarity_suspect_pairs": 0, "pair_tdoa_us": [], "basis": None},
            "harmonic": {"f0_hz": None, "count": 0, "considered": 0, "score": 0.0,
                         "mean_prominence_db": 0.0, "power_fraction": 0.0, "harmonic_freqs_hz": []},
            "spectrum": None,
        }

    def _spatial(self, x: np.ndarray, result: dict) -> None:
        s = self.s
        sub, hop = int(s.subframe), int(s.subframe) // 2
        frames = sliding_window_view(x, sub, axis=0)[::hop]          # (T, mics, sub)
        count = frames.shape[0]
        spec = np.fft.rfft(frames * self._hann_sub, axis=2)          # (T, mics, F)
        auto = np.mean(np.abs(spec) ** 2, axis=0)                    # (mics, F)
        mean_power = auto.mean(axis=0)
        window_power = float(np.mean(self._hann_sub ** 2))
        band_power = 2.0 * float(mean_power[self._mask].sum()) / (sub * sub * window_power)
        result["band_level_dbfs"] = _db10(band_power)
        cross = np.stack([np.mean(spec[:, i, :] * np.conj(spec[:, j, :]), axis=0) for i, j in self.pairs])
        magnitude = np.abs(cross)
        denom = np.stack([auto[i] * auto[j] for i, j in self.pairs])
        msc = magnitude ** 2 / np.maximum(denom, 1e-300)
        msc = np.clip(msc, 0.0, 1.0)
        bias = 1.0 / count
        debiased = np.clip((msc - bias) / (1.0 - bias), 0.0, 1.0)
        if s.coherence_weighting == "msc":
            weight = msc
        elif s.coherence_weighting == "debias":
            weight = debiased
        elif s.coherence_weighting == "ht":
            weight = np.minimum(msc / np.maximum(1.0 - msc, 0.05), 20.0)
        elif s.coherence_weighting == "threshold":
            # For independent noise averaged over `count` frames, P(msc > t) = (1 - t)**(count - 1).
            threshold = 1.0 - float(s.coherence_significance) ** (1.0 / max(count - 1, 1))
            weight = np.clip((msc - threshold) / (1.0 - threshold), 0.0, 1.0)
        elif s.coherence_weighting == "phat":
            weight = np.ones_like(msc)
        else:
            raise ValueError("unknown coherence_weighting")
        weight = weight * self._mask[None, :]
        debiased = debiased * self._mask[None, :]
        # When a harmonic comb is present, steer on the bins that carry it. This
        # ties the bearing to the sound that raised the candidate and keeps
        # unrelated broadband noise out of the direction estimate.
        basis = "ALL_COHERENT_BINS"
        comb = result["harmonic"]
        if s.harmonic_focus and comb["count"] >= s.harmonic_focus_min_tones:
            tones = np.array([f for f in comb["harmonic_freqs_hz"] if s.band_hz[0] <= f <= s.band_hz[1]])
            if len(tones) >= s.harmonic_focus_min_tones:
                half = np.maximum(self.fs / sub, s.harmonic_rel_tolerance * tones)
                focus = (np.abs(self._freq_sub[None, :] - tones[:, None]) <= half[:, None]).any(axis=0)
                weight = weight * focus[None, :]
                debiased = debiased * focus[None, :]
                basis = "HARMONIC_BINS"
        bearing = result["bearing"]
        bearing["reasons"] = []
        bearing["basis"] = basis
        pair_weight = weight.sum(axis=1)
        total = float(pair_weight.sum())
        coherence = float(debiased.sum()) / (len(self.pairs) * self._band_bins)
        bearing["coherence"] = coherence
        mean_weight = debiased.mean(axis=0)
        coherent_power = mean_weight * mean_power
        cp_sum, cp_sq = float(coherent_power.sum()), float((coherent_power ** 2).sum())
        df = self.fs / sub
        bearing["coherent_bandwidth_hz"] = (cp_sum * cp_sum / cp_sq * df) if cp_sq > 0 else 0.0
        if total <= 1e-9:
            bearing["reasons"] = ["LOW_COHERENCE"]
            return
        phat = np.where(magnitude > 1e-300, cross / np.maximum(magnitude, 1e-300), 0.0)
        gcc = np.fft.irfft(weight * phat, n=self._gcc_n, axis=1) * (self._gcc_n / 2.0)
        limit = self._limit
        segment = np.concatenate((gcc[:, -limit:], gcc[:, :limit + 1]), axis=1)
        srp = np.zeros(self.lags.shape[1])
        for p in range(len(self.pairs)):
            srp += np.interp(self.lags[p], self._lag_axis, segment[p])
        srp /= total
        # Expected standard deviation of the map if every bin had a random phase.
        noise_sigma = math.sqrt(float((weight ** 2).sum()) / 2.0) / total
        grid = srp.reshape(len(self.elevations), len(self.azimuths))
        profile = grid.max(axis=0)
        best = int(np.argmax(profile))
        peak = float(profile[best])
        step = float(s.azimuth_step_deg)
        y0, y1, y2 = profile[best - 1], profile[best], profile[(best + 1) % len(profile)]
        curvature = y0 - 2.0 * y1 + y2
        shift = float(np.clip(0.5 * (y0 - y2) / curvature, -0.5, 0.5)) if curvature < 0 else 0.0
        azimuth = float((self.azimuths[best] + shift * step) % 360.0)
        separation = np.abs(wrap180(self.azimuths - self.azimuths[best]))
        second = float(profile[separation >= s.second_peak_separation_deg].max())
        bearing.update(raw_azimuth_deg=azimuth, srp_peak=peak, srp_z=peak / max(noise_sigma, 1e-12),
                       srp_contrast=float(np.clip((peak - float(np.median(profile))) / max(peak, 1e-12), 0, 1)),
                       second_peak_ratio=(second / peak if peak > 1e-12 else None))
        # Independent cross-check: least-squares plane wave through per-pair peak lags.
        tau = np.zeros(len(self.pairs))
        suspect = 0
        for p in range(len(self.pairs)):
            reach = int(math.ceil(self.pair_max_delay[p] * self._lag_scale)) + int(s.oversample)
            lo, hi = limit - reach, limit + reach + 1
            window = segment[p, lo:hi]
            k = int(np.argmax(window))
            frac = 0.0
            if 0 < k < len(window) - 1:
                a, b, c2 = window[k - 1], window[k], window[k + 1]
                d = a - 2.0 * b + c2
                frac = float(np.clip(0.5 * (a - c2) / d, -0.5, 0.5)) if d < 0 else 0.0
            tau[p] = (lo - limit + k + frac) / self._lag_scale
            if -float(window.min()) > 1.25 * float(window[k]) and -float(window.min()) > 0.3 * pair_weight[p]:
                suspect += 1
        root_w = np.sqrt(np.maximum(pair_weight, 1e-12))
        design = self.baselines / self.c
        slowness, *_ = np.linalg.lstsq(design * root_w[:, None], tau * root_w, rcond=None)
        residual = tau - design @ slowness
        residual_us = float(np.sqrt(np.sum(pair_weight * residual ** 2) / total) * 1e6)
        horizontal = float(math.hypot(slowness[0], slowness[1]))
        ls_azimuth = float(math.degrees(math.atan2(slowness[1], slowness[0])) % 360.0) if horizontal > 1e-6 else None
        if self.planar:
            elevation_hint = float(math.degrees(math.acos(min(horizontal, 1.0))))
        else:
            elevation_hint = float(math.degrees(math.atan2(slowness[2], max(horizontal, 1e-12))))
        bearing.update(tdoa_residual_us=residual_us, ls_azimuth_deg=ls_azimuth,
                       elevation_hint_deg=elevation_hint, polarity_suspect_pairs=suspect,
                       pair_tdoa_us=[float(t * 1e6) for t in tau])
        reasons = []
        if bearing["srp_z"] < s.min_srp_z:
            reasons.append("LOW_COHERENCE")
        if bearing["coherent_bandwidth_hz"] < s.min_coherent_bandwidth_hz:
            reasons.append("NARROWBAND")
        if peak < s.min_srp_peak:
            reasons.append("WEAK_PEAK")
        if bearing["srp_contrast"] < s.min_contrast:
            reasons.append("LOW_CONTRAST")
        if bearing["second_peak_ratio"] is None or bearing["second_peak_ratio"] > s.max_second_peak_ratio:
            reasons.append("COMPETING_PEAK")
        disagreement = None if ls_azimuth is None else abs(float(wrap180(ls_azimuth - azimuth)))
        if abs(elevation_hint) >= s.max_elevation_hint_deg:
            reasons.append("NEAR_OVERHEAD_OR_DIFFUSE")
        elif residual_us > s.max_tdoa_residual_us or disagreement is None or disagreement > s.max_srp_ls_disagreement_deg:
            reasons.append("TDOA_INCONSISTENT")
        bearing["reasons"] = reasons
        bearing["available"] = not reasons
        bearing["azimuth_deg"] = azimuth if not reasons else None

    def _tables(self, n: int) -> dict:
        tables = self._harmonic_cache.get(n)
        if tables is not None:
            return tables
        s = self.s
        df = self.fs / n
        lo = int(math.ceil(s.harmonic_band_hz[0] / df))
        hi = int(math.floor(s.harmonic_band_hz[1] / df))
        step = max(df / 4.0, 0.5)
        f0 = np.arange(s.f0_range_hz[0], s.f0_range_hz[1] + step / 2, step)
        h = np.arange(1, int(s.max_harmonics) + 1)
        centre = f0[:, None] * h[None, :]
        half = np.ceil(s.harmonic_rel_tolerance * centre / df).astype(int) + 1
        centre_bin = np.rint(centre / df).astype(int) - lo
        valid = (centre_bin - half >= 0) & (centre_bin + half <= hi - lo)
        widths = np.unique(half[valid])
        width_index = np.searchsorted(widths, np.where(valid, half, widths[0]))
        floor_bins = int(round(s.floor_window_hz / df)) | 1
        points = int(s.spectrum_points)
        top = min(int(math.floor(s.spectrum_max_hz / df)), n // 2)
        edges = np.linspace(0, top, points + 1).astype(int)
        tables = dict(df=df, lo=lo, hi=hi, f0=f0, h=h, half=half, valid=valid, widths=widths,
                      width_index=width_index, centre_bin=np.where(valid, centre_bin, 0),
                      floor_bins=floor_bins, edges=edges, window=np.hanning(n + 1)[:-1])
        self._harmonic_cache[n] = tables
        return tables

    def harmonic_features(self, audio):
        """Harmonic features and power spectrum for any channel count (used for offline feature extraction)."""
        x = np.asarray(audio, dtype=float)
        if x.ndim == 1:
            x = x[:, None]
        return self._harmonic(x - x.mean(axis=0))

    def _harmonic(self, x: np.ndarray):
        s = self.s
        n = len(x)
        t = self._tables(n)
        power = np.mean(np.abs(np.fft.rfft(x * t["window"][:, None], axis=0)) ** 2, axis=1)
        band = power[t["lo"]: t["hi"] + 1]
        pad = t["floor_bins"] // 2
        floor = np.median(sliding_window_view(np.pad(band, pad, mode="reflect"), t["floor_bins"]), axis=1)
        floor = np.maximum(floor, 1e-30)
        prominence = 10.0 * np.log10(np.maximum(band, 1e-30) / floor)
        stack = np.empty((len(t["widths"]), len(band)))
        for row, half in enumerate(t["widths"]):
            padded = np.pad(prominence, int(half), mode="constant", constant_values=-np.inf)
            stack[row] = sliding_window_view(padded, 2 * int(half) + 1).max(axis=1)
        peak = np.where(t["valid"], stack[t["width_index"], t["centre_bin"]], -np.inf)
        present = peak >= s.harmonic_prominence_db
        considered = t["valid"].sum(axis=1)
        count = present.sum(axis=1)
        merit = np.where(present, np.minimum(peak, 30.0), 0.0).sum(axis=1) - 6.0 * (considered - count)
        best = int(np.argmax(merit))
        harmonic = {"f0_hz": None, "count": 0, "considered": int(considered[best]), "score": 0.0,
                    "mean_prominence_db": 0.0, "power_fraction": 0.0, "harmonic_freqs_hz": []}
        if count[best] > 0:
            df = t["df"]
            estimates, weights, freqs, tonal = [], [], [], 0.0
            excess = np.maximum(band - floor, 0.0)
            for k in np.flatnonzero(present[best]):
                centre, half = int(t["centre_bin"][best, k]), int(t["half"][best, k])
                window = slice(centre - half, centre + half + 1)
                local = int(np.argmax(prominence[window])) + centre - half
                frequency = (local + t["lo"]) * df
                estimates.append(frequency / (k + 1))
                weights.append(float(prominence[local]))
                freqs.append(round(float(frequency), 1))
                tonal += float(excess[window].sum())
            mean_prominence = float(np.mean(weights))
            harmonic.update(
                f0_hz=float(np.average(estimates, weights=weights)), count=int(count[best]),
                mean_prominence_db=mean_prominence, harmonic_freqs_hz=freqs,
                power_fraction=float(np.clip(tonal / max(float(band.sum()), 1e-30), 0, 1)),
                score=float(np.clip(count[best] / 8.0, 0, 1) * np.clip(mean_prominence / 20.0, 0, 1)))
        edges = t["edges"]
        reference = 10.0 * math.log10(n * n * float(np.mean(t["window"] ** 2)) / 2.0)
        pooled = [float(power[a:max(b, a + 1)].max()) for a, b in zip(edges[:-1], edges[1:])]
        spectrum = {"hz_per_point": float(edges[-1] * t["df"] / len(pooled)),
                    "db": [round(10.0 * math.log10(max(v, 1e-30)) - reference, 1) for v in pooled]}
        return harmonic, spectrum, (power, t["df"])


def _db20(value: float) -> float:
    return float(20.0 * math.log10(max(float(value), 1e-8)))


def _db10(value: float) -> float:
    return float(10.0 * math.log10(max(float(value), 1e-16)))
