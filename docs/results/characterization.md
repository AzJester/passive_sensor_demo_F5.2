# Synthetic characterization

**SYNTHETIC DATA ONLY.** These numbers describe software behavior on idealized simulated signals. They are not detection range, field accuracy, or false-alert rates. Real microphones, enclosures, wind, reflections, and real aircraft will differ.

Software 0.2.0. Python 3.14.7, NumPy 2.5.4, Windows 11, AMD64. Generated 2026-11-09 19:55:41 local time by `tools/characterize.py`.

## 1. Single-frame bearing, Revision B against Revision A

One 256 ms block per trial, 60 trials per row. One stationary rotor-like source at a random azimuth, elevation 5 to 45 degrees, 40 m away. SNR is source RMS over noise RMS across the full band. "Shown" is the share of frames where the estimator released a bearing. "Wrong by over 10 deg" is counted among shown bearings only. Revision B numbers are per frame, before the frame-to-frame stability gate that the pipeline adds.

| Noise field | SNR dB | Rev B shown | Rev B median error | Rev B 95th pct | Rev B wrong by over 10 deg | Rev A shown | Rev A median error | Rev A 95th pct | Rev A wrong by over 10 deg |
|---|---|---|---|---|---|---|---|---|---|
| uncorrelated | 20 | 100% | 0.1 | 0.3 | 0% | 97% | 0.7 | 1.8 | 0% |
| uncorrelated | 10 | 100% | 0.2 | 0.7 | 0% | 100% | 1.0 | 3.0 | 0% |
| uncorrelated | 5 | 100% | 0.5 | 1.2 | 0% | 100% | 2.1 | 4.6 | 0% |
| uncorrelated | 0 | 100% | 1.1 | 3.2 | 0% | 100% | 3.1 | 11.0 | 7% |
| uncorrelated | -5 | 93% | 1.8 | 5.2 | 0% | 100% | 6.2 | 21.6 | 32% |
| uncorrelated | -10 | 33% | 3.1 | 11.8 | 10% | 25% | 9.5 | 47.5 | 47% |
| diffuse | 10 | 100% | 0.2 | 0.8 | 0% | 100% | 2.5 | 6.5 | 0% |
| diffuse | 5 | 100% | 0.5 | 1.6 | 0% | 78% | 4.4 | 11.8 | 11% |
| diffuse | 0 | 85% | 1.2 | 3.8 | 0% | 8% | 10.6 | 17.2 | 60% |
| diffuse | -5 | 45% | 5.9 | 26.8 | 37% | 5% | 18.7 | 24.5 | 67% |

## 2. Ambient noise only, full pipeline

Two minutes per scene, no source. The pipeline includes the stability gate.

| Ambient scene (no source) | Audio | Frames | Frames with a bearing shown | Candidates |
|---|---|---|---|---|
| uncorrelated sensor noise | 120 s | 939 | 0 | 0 |
| diffuse noise field | 120 s | 939 | 0 | 0 |
| diffuse noise plus low-frequency wind noise | 120 s | 939 | 0 | 0 |

## 3. Candidate gate and bearing, full pipeline

Each run: 20 s of audio, ambient only for 8 s, then a rotor-like source for 8 s. Five runs per row with different fundamentals (140 to 232 Hz) and azimuths. Latency is candidate onset minus source start. Bearing error is measured against the source for every frame with a bearing while the source is on. When no candidate is raised the pipeline still reports the loudest coherent sound. At negative SNR that is the simulated ambient field, not the source, so the error against the source is large. The dashboard labels such a bearing as having no candidate.

| SNR dB (diffuse noise) | Runs | Runs with a candidate during the source | Median latency s | Median bearing error deg | Frames with bearing during the source | Candidates outside the source |
|---|---|---|---|---|---|---|
| 10 | 5 | 5 | 0.58 | 0.2 | 100% | 0 |
| 5 | 5 | 5 | 0.58 | 0.4 | 100% | 0 |
| 0 | 5 | 0 | n/a | 1.2 | 100% | 0 |
| -5 | 5 | 0 | n/a | 11.6 | 20% | 0 |
| -10 | 5 | 0 | n/a | 60.7 | 15% | 0 |

## 4. Processing time on this computer

- One analysis block (4096 frames, 4 microphones): median 9.9 ms, 99th percentile 16.3 ms, maximum 19.1 ms over 200 repeats.
- A new block arrives every 128 ms, so the median is 7.7 percent of real time on one core.
- Full pipeline over 60 s of synthetic audio: smoothed load 10.5 percent of real time, worst block 30.2 ms.

Total run time 264 s.
