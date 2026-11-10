# Baseline audit: Revision A package

Audited 9 November 2026. Input: `Passive_Acoustic_UAS_Sensor_Prototype (1).zip`, SHA-256 `bdd2b7825e590423a0100a5ecb0447da4d84827df2a1af4bdbdd0ba02a88735a`, 22 files.

## What was reproduced

| Check | Result |
|---|---|
| File checksums against the package's own `SHA256SUMS.txt` | All 21 listed files match |
| `python -m unittest discover -v` on Python 3.14.7, NumPy 2.5.4, Windows 11 | 34 tests ran, 34 passed, 0.98 s |
| Dashboard page | **Could not load.** `app.py` serves `dashboard.html`, the README lists it, and the file is not in the package |
| Physical hardware, firmware, Windows six-channel capture | Not reproducible. No hardware was available then or now |

The Revision A record said its tests passed on Python 3.12.14. They also pass on 3.14.7. The same record noted that the HTTP page could not be fetched in its test environment. The missing file explains why that would have failed anywhere.

## What the baseline had right

These were checked and kept.

- **Delay sign.** For a source toward unit vector u, t_i - t_j = (r_j - r_i) . u / c, and the cross-spectrum X_i conj(X_j) peaks at lag t_i - t_j. Confirmed with a new simulator and a plain sliding-correlation check that share no code with the estimator.
- **Raw channel selection `[2, 3, 4, 5]`.** It matches the maintainer's default routing table for the 16 kHz six-channel image (GitHub issue 38 of the vendor repository).
- **Geometry.** The square of side 66 mm matches the vendor's documented `AEC_MIC_ARRAY_GEO` output and the vendor 2D drawing. It is still an assumption for any individual unit until read back.
- **Loopback-only server, no raw audio saved by default, bearing hidden until calibration.**
- **The `AUDIO_MGR_OP_CH3` to `CH6` commands and the `_16k6ch` firmware naming.** Both exist in the vendor repository.

## Prioritized issue list

Priority 1 blocks use or can mislead an operator. Priority 2 weakens results or evidence. Priority 3 is a documentation or design gap.

Status key: **Fixed** means changed and covered by an automated test. **Mitigated** means improved but a stated limit remains. **Open** means it needs hardware or data.

| ID | P | Finding in Revision A | Evidence | Status in Revision B |
|---|---|---|---|---|
| A-01 | 1 | `dashboard.html` is missing. A request for `/` raises inside the handler and returns nothing | File list; `app.py` line 211 | Fixed. New dashboard in `pasensor/web/`. Server tests fetch every page |
| A-02 | 1 | Any acquisition error ends the worker thread for good. No reconnect, no retry | `run_worker` catches the exception, publishes AUDIO FAULT, and returns | Fixed. Supervised restart with backoff. Fault-injection tests cover unplug, stall, missing device, and software faults |
| A-03 | 1 | Capture, analysis, and log writing share one thread through a blocking read | `live_blocks` and `run_worker` | Fixed. Driver callback copies into a bounded queue. Analysis and file writing run on other threads |
| A-04 | 1 | Wrong bearings are released at low signal-to-noise. Measured on synthetic frames: 32% of shown bearings were off by more than 10 degrees at -5 dB in uncorrelated noise, and 60% at 0 dB in diffuse noise | `docs/results/characterization.md`, section 1 | Fixed for the tested range. Revision B shows 0% wrong in both of those rows and withholds more often. It still fails below about -5 dB in diffuse noise |
| A-05 | 1 | Simulated mode marked its bearing `calibrated: true` | `validated = args.demo or cfg[...]` | Fixed. Simulated bearings carry `simulated_geometry` and are never calibrated |
| A-06 | 1 | The calibration gate accepted any non-empty text as a "calibration record" | `load_config` | Fixed. The record must exist, name this node, match channels and geometry, come from physical recordings, and show a pass |
| A-07 | 1 | Timestamps were the time of publication, not of capture. Replay stamped the wall clock, so two replays of one file differed | `run_worker` | Fixed. Every block carries the time of its first sample and a time-quality label. Replay uses the recording's clock and is repeatable (tested) |
| A-08 | 1 | The ambient baseline was measured once at start and never updated | `EventGate` | Mitigated. The floor is now a rolling low percentile. A source already sounding at start-up still sets the floor and masks itself. That limit has its own test and is shown to the operator |
| A-09 | 1 | Candidates required the bearing health to be "OK". Detection depended on localization, and a narrowband flag stood in for a tone test | `EventGate.update` | Fixed. Audio quality, harmonic evidence, bearing quality, and identity are separate. Tones are rejected by counting harmonics |
| A-10 | 2 | The harmonic score depended on where the fundamental fell on a 5 Hz search grid. A steady comb scored 0.62 on the grid and 0.24 at 157.5 Hz, under the 0.25 threshold | Test `test_rev_a_score_depended_on_its_search_grid` | Fixed. Fine search grid, tolerance that grows with harmonic number, noise-floor-relative peaks |
| A-11 | 2 | The test signal generator and the estimator used the same frequency-domain phase-ramp maths, so a shared sign error could pass | `dsp.py` | Fixed. Independent time-domain simulator, checked on its own by direct correlation |
| A-12 | 2 | Device chosen by index only. Indices change when USB devices come and go | `--device` | Fixed. Match by name and host API. Two-channel endpoints and duplicates are refused with a clear message |
| A-13 | 2 | Diffuse low-frequency noise looks like a zero-delay, coherent arrival on a small planar array, the same as a source overhead. The elevation search stopped at 75 degrees | Analysis and simulation | Fixed. Search runs to 90 degrees, such frames are withheld as overhead or diffuse, and a stability gate needs agreement across frames |
| A-14 | 2 | The log held candidate onsets only. No end time, duration, or health record. `record.py` wrote no metadata and deleted the whole file on one overflow | `app.py`, `record.py` | Fixed. Events have onset, offset, evidence, and bearing statistics. Health records are logged. Recordings get a sidecar with hash, gaps, and configuration |
| A-15 | 2 | Unknown configuration keys were ignored, so a typing error silently used a default | `load_config` | Fixed. Unknown keys are errors. Revision A files are converted |
| A-16 | 2 | No Host header check (DNS rebinding could read the status API), no content security policy, handler errors unhandled | `make_handler` | Fixed. See `docs/OPERATING.md`, security section |
| A-17 | 2 | Two-bearing utility ignored node heading and position error, handled two nodes only, and could not align for sound travel time | `triangulate.py` and its own limitation list | Fixed in `pasensor/multinode.py`. The original utility is kept unchanged in `legacy/` |
| A-18 | 2 | Staleness cleared the bearing and candidate but left other old values in place | `State.snapshot` | Fixed. A stale snapshot removes every measurement. Found again and fixed in Revision B during the browser check |
| A-19 | 2 | Level was computed before removing DC offset | `Analyzer.analyze` | Fixed. A DC warning was added |
| A-20 | 3 | Firmware instructions were out of date or untested: the named image is archived (current is v2.1.1), XIAO variants ship with I2S firmware, `SAVE_CONFIGURATION` broke enumeration on firmware 2.0.8 and older, no recovery steps | Vendor repository, checked 9 November 2026 | Fixed in `firmware/PROVISIONING.md`. Still unexecuted on hardware |
| A-21 | 3 | The 350 to 1800 Hz band was justified by half-wavelength spacing. A broadband time-delay method is not bound by that limit. The real limits are enclosure diffraction and noise | `ENGINEERING_PLAN.md` | Mitigated. Default is now 300 to 3400 Hz and is configurable. The best band for the real enclosure needs bench data |
| A-22 | 3 | The carrier plate had four holes and no way to hold the array or meet a stand | `carrier.scad` | Mitigated. New plate with strap slots, tie holes, and an adapter zone. It is a proposal: not cut, not fitted |
| A-23 | 3 | Requirements had no measurable criteria and no status | `ENGINEERING_PLAN.md` | Fixed. `docs/REQUIREMENTS.md` |
| A-24 | 3 | No bill of materials with part numbers or prices, no power figures | README table | Mitigated. `hardware/BOM.md`. Array supply current is unpublished and must be measured |

## Unsupported assumptions carried by the baseline

None of these can be closed from a desk. Each has a procedure in `docs/TEST_PROCEDURES.md`.

1. Windows exposes all six channels of the array to applications at 16 kHz. The vendor documents this only for two channels.
2. The four "raw" channels are sample-synchronous with each other and carry no per-channel processing delay.
3. Geometry index 0 to 3 corresponds to silkscreen MIC1 to MIC4, and those sit where the vendor image shows them.
4. All four microphones have the same polarity.
5. The local +X axis has a known relation to the USB connector edge.
6. The case, a strap, and a foam cover do not change inter-microphone delays enough to matter in the chosen band.
7. Timestamps from the Windows audio stack are good enough to compare two nodes.

## Terminology corrected

| Revision A wording | Problem | Revision B wording |
|---|---|---|
| `localization_score` shown next to a bearing | Reads like confidence | Separate named metrics, each with its threshold and a met or not met mark |
| "ROTOR LIKE CANDIDATE" as a state | Acceptable, kept | "Rotor-like sound candidate (heuristic). Not an identification." |
| `calibrated: true` in demo | False | `simulated_geometry: true`, badge SIMULATED GEOMETRY |
| "conditional intersection" with a Monte Carlo radius | Radius ignored most error sources | "bearing-only estimate" with a full error breakdown and `validated_location: false` |
