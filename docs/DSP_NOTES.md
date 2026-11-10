# Signal processing: design, review findings, and what the numbers mean

Revision B. Code: `pasensor/dsp.py`, `pasensor/detector.py`, `pasensor/geometry.py`. Every number in this document comes from simulation unless it says otherwise.

## Four outputs that are never merged

| Output | Question it answers | Where | What it is not |
|---|---|---|---|
| Audio quality | Is this block usable at all? | `audio.status`, `audio.warnings` | Not a judgement about the sound |
| Detection evidence | Does the sound have a harmonic comb and stand above the ambient floor? | `detection.*` | Not a probability. Not an identification |
| Bearing quality | Is one direction a good, stable explanation of the arrival times? | `bearing.metrics`, `bearing.reasons` | Not accuracy in degrees |
| Identity | What does a trained model say the sound is? | `identity.*` | Absent until a model is trained on real data. "Not assessed" by default |

There is no combined "confidence". Each criterion is shown with its own value and threshold.

## Timing budget

16 kHz sampling. Analysis block 4096 frames (256 ms). A new block every 2048 frames (128 ms). A candidate needs 4 qualifying blocks among the last 6. The earliest onset is therefore about 0.5 s after the sound fills a block. Measured median on synthetic scenes: 0.58 s. Add capture buffering and the 250 ms page refresh for what an operator sees.

## Conventions and the delay sign

Local frame: +X and +Y in the microphone plane, +Z out of the port side. Azimuth is counterclockwise from +X seen from +Z. A bearing points from the array toward the sound.

A plane wave from unit direction u reaches a microphone at r at time t = -(r . u)/c. So t_i - t_j = (r_j - r_i) . u / c. The cross-spectrum X_i conj(X_j) has phase -2 pi f (t_i - t_j), and its inverse transform peaks at lag t_i - t_j. The steering table uses exactly that expression.

How the sign was verified: `pasensor/simulate.py` builds microphone signals in the time domain from distances alone. The test `test_arrival_order_by_direct_correlation` finds the lag by a sliding dot product, with no FFT and no estimator code, and checks that the microphone nearer the source leads by the expected number of samples. Only then is the estimator run on the same signals. The Revision A generator is also run through the new estimator as a cross-check.

## Bearing estimator

1. Remove DC. Split the block into 15 overlapping sub-frames of 512 samples (Hann window).
2. Average auto- and cross-spectra over the sub-frames. Compute magnitude-squared coherence for each of the six microphone pairs.
3. Weight each frequency bin by its coherence with the small-sample bias removed. Bins that hold only unrelated noise get little weight. Phase comes from the cross-spectrum normalized to unit magnitude (PHAT).
4. When the harmonic analysis finds six or more harmonics inside the band, steer on the bins at those harmonics only. This ties the bearing to the sound that raised the candidate.
5. Transform to the lag domain at 16 times oversampling (3.9 microsecond steps) and read each pair's value at the lag that each candidate direction predicts. Sum over pairs. Search azimuth in 2 degree steps and elevation from 0 to 90 degrees in 15 degree steps. Elevation is a nuisance parameter here, not an output.
6. Refine the azimuth peak with a parabola.
7. Independently, take each pair's own peak lag and fit one plane wave to the six lags by least squares. The fit residual and the fitted direction are a cross-check on step 5.
8. Release the frame's bearing only if every gate below passes.
9. In the pipeline, release it to the operator only if at least 3 of the last 5 frames produced bearings within 15 degrees of the current one. The value shown is always the current frame's own estimate. An old value is never repeated.

| Gate | Default | Reason given when it fails |
|---|---|---|
| Peak against the level expected from random phases | at least 8 | LOW_COHERENCE |
| Normalized peak | at least 0.4 | WEAK_PEAK |
| Peak above the median over azimuth | at least 0.15 | LOW_CONTRAST |
| Best peak 45 degrees or more away, relative to the main peak | at most 0.92 | COMPETING_PEAK |
| Effective bandwidth of the coherent sound | at least 120 Hz | NARROWBAND |
| Plane-wave fit residual | at most 25 microseconds | TDOA_INCONSISTENT |
| Agreement between the two methods | within 25 degrees | TDOA_INCONSISTENT |
| Fitted elevation | under 70 degrees | NEAR_OVERHEAD_OR_DIFFUSE |
| Agreement across frames | 3 of 5 within 15 degrees | UNSTABLE |

All thresholds are engineering choices tuned on simulation. They are configuration values under `processing.dsp` and `tracking`. Tune them on bench recordings, and record the change.

## Review findings

### Fractional delay

Delays between microphones are at most 272 microseconds (4.4 samples) across the diagonal. The lag-domain function is interpolated 16 times by zero-padding the spectrum, then linearly. On clean synthetic broadband input the pair delays match geometry within 4 microseconds and the bearing within 0.2 degrees.

### Frequency band and array spacing

The array is a 66 mm square, 93 mm across the diagonal. A single tone becomes ambiguous on one pair above c/(2d): 2.6 kHz for the sides and 1.8 kHz for the diagonals. Revision A used that to cap the band at 1.8 kHz. For a broadband or many-harmonic sound the cap is not needed: ambiguous lags fall in different places at different frequencies and do not add up, while the true lag does. Revision B uses 300 to 3400 Hz by default. In simulation sweeps during development the wider band gave equal or smaller errors in most conditions and kept bearings available at lower signal levels in diffuse noise. That comparison was made on an earlier setting of the estimator and was not repeated on the final one.

Two caveats. The lower edge matters because the array is small: at 300 Hz the largest phase difference between microphones is under 0.6 radian, so low frequencies carry little direction information and a lot of ambient noise. And the simulation has no enclosure. A 100 mm case scatters sound from about 1 kHz upward, which changes inter-microphone phase in ways only a bench measurement can show. The best band for the real unit is an open item (HB-5 can be repeated with different `band_hz`).

### Near field

For this aperture the far-field condition 2D^2/lambda is about 0.1 m at 3.4 kHz. Simulated point sources at 0.5, 1, and 2 m gave bearing errors under 0.2 degrees. Calibration with a loudspeaker at 4 m is well inside the valid region.

### Reflections

A reflection is a second coherent arrival. If it comes from another azimuth it can win, and the result is a stable, plausible, wrong bearing. Nothing in single-array processing can rule that out. A ground reflection has the same azimuth as the direct path and did not bias azimuth in simulation (coefficient 0.7, error under 0.1 degrees). Walls and vehicles are the real risk. Site the array away from them and treat any single bearing as "apparent".

### Narrowband interference and tones

A lone tone has one frequency, so its lag function is a cosine that fits many directions in a reverberant place. The estimator withholds it as NARROWBAND. The candidate gate separately requires at least four harmonics, so a loud whistle or alarm does not raise a candidate.

### Competing sources

Two equal broadband sources 120 degrees apart: the frame is withheld as COMPETING_PEAK in every simulated trial. When one source dominates, its bearing is reported. The estimator reports one direction per frame and does not separate sources.

### Bearing ambiguity that the hardware imposes

- **Up versus down.** A planar array cannot tell elevation +e from -e. Mounted face-up outdoors, sources are above, so this rarely matters.
- **Near overhead.** Azimuth is undefined at the zenith and poorly defined near it. Frames with fitted elevation above 70 degrees are withheld.
- **Diffuse noise looks overhead.** At low frequency, ambient noise is nearly identical at all four microphones: coherent, with zero delay on every pair. That is also the signature of a source straight overhead. Such frames are withheld under the same reason, and the stability gate removes the rest. In simulation, three ambient-only scenes of 120 s produced no bearing in 2817 frames.
- **Symmetries of the square.** Rotating the channel order by one position turns every bearing by 90 degrees and is perfectly self-consistent. No data test can catch it. The physical axis test (HB-5) does. Swapping two adjacent channels is not a symmetry: the six pair delays stop fitting one plane wave and the bearing is withheld at 10 of 12 tested azimuths.

### Temperature

Speed of sound is taken from the configured air temperature: 331.3 sqrt(1 + T/273.15) m/s. A wrong temperature scales every delay by the same factor. On a planar array that looks like a different elevation, not a different azimuth. Simulated scenes at -10 C and 40 C analysed with 20 C gave azimuth errors under 0.1 degrees. Temperature matters more for multi-node travel-time alignment than for single-array bearing. Wind and temperature gradients bend sound paths over distance and are not modelled.

### What happens with no usable result

The status always says why a bearing is absent. Audio faults give AUDIO_FAULT. Lost samples give OVERFLOW. An uncalibrated unit gives UNCALIBRATED. A stalled input gives STALE and removes every measurement. The dashboard shows "BEARING UNAVAILABLE" with the reasons in words.

When no candidate is active, a bearing can still appear. It is then the direction of the loudest coherent sound around, which may be a road or a compressor. The status marks it `subject: DOMINANT_SOUND` and the page says no candidate is active.

## Harmonic evidence and the candidate gate

1. Power spectrum of the whole block, averaged over microphones (3.9 Hz resolution).
2. Local noise floor: running median over 200 Hz.
3. A peak "stands out" when it is at least 9 dB above that floor.
4. Search fundamentals from 70 to 500 Hz in 1 Hz steps. For each, look for peaks at up to 16 harmonics below 4 kHz, with a tolerance of 1.5 percent of the harmonic frequency so that several rotors at slightly different speeds still match. Missing harmonics cost points, which keeps the search from choosing half the true fundamental.
5. Report the fundamental, the number of harmonics found, their mean prominence, and a score: (harmonics found / 8, capped at 1) times (mean prominence / 20 dB, capped at 1).

The candidate gate then requires, in 4 of the last 6 frames: band level at least 6 dB above the ambient floor, at least 4 harmonics, and score at least 0.25. The ambient floor is the 10th percentile of band level over the last 5 minutes, after at least 10 s of data.

What the heuristic cannot do:

- **It cannot tell a drone from a fan.** Any machine with a harmonic comb passes. A simulated steady 120 Hz machine scores above the threshold (test `test_steady_machine_hum_also_scores`).
- **It needs the sound to raise the band level.** In simulated diffuse noise the gate fired in 5 of 5 runs at 5 dB and in 0 of 5 at 0 dB, although the harmonic score was high and the bearing was correct at 0 dB. Lowering `minimum_db_above_floor` trades sensitivity for false alerts. That choice needs recorded data.
- **A source present at start-up is masked.** It sets the floor it would have to exceed.
- **A source present for most of five minutes raises the floor** and can drop out.

## Measured on synthetic signals

Full tables: `docs/results/characterization.md`. Highlights, single frame, rotor-like source:

| Condition | Revision B shown | Revision B wrong by over 10 degrees | Revision A shown | Revision A wrong by over 10 degrees |
|---|---|---|---|---|
| Uncorrelated noise, 0 dB | 100% | 0% | 100% | 7% |
| Uncorrelated noise, -5 dB | 93% | 0% | 100% | 32% |
| Diffuse noise, 5 dB | 100% | 0% | 78% | 11% |
| Diffuse noise, 0 dB | 85% | 0% | 8% | 60% |
| Diffuse noise, -5 dB | 45% | 37% | 5% | 67% |

The last row is the edge of usefulness for both. Revision B's stability gate removes much of what is left, but below about -5 dB in diffuse noise no bearing should be trusted.

## From heuristic to trained classifier

See `ML_WORKFLOW.md`. The feature code that a model would use already runs in the pipeline (`pasensor/classifier.py`), the training and evaluation tools exist, and no model ships.
