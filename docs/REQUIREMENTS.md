# Requirements baseline and verification traceability

Revision B, 10 November 2026. Software version 0.2.3.

## How to read the status column

| Status | Meaning |
|---|---|
| **SW verified** | Implemented. An automated test passes. Shows the software does what the requirement says on synthetic or injected input. Says nothing about hardware |
| **Demonstrated (synthetic)** | A number was measured on simulated signals. It is software behavior, not performance |
| **Bench pending** | Implemented where software is involved. Needs the physical array on a bench. Procedure exists. Not done |
| **Field pending** | Needs outdoor recordings or drone trials. Procedure exists. Not done |
| **Target** | A proposed number to design and test against. Nothing has been measured. Revise it after first data |
| **Not started** | Defined, not implemented |

Verification methods: **T** automated test, **I** inspection, **A** analysis, **B** bench procedure, **F** field procedure. Test names refer to `tests/`. Procedure names refer to `docs/TEST_PROCEDURES.md`.

No requirement below states a detection range, an identification accuracy, or an environmental rating. None has been measured, so none is claimed.

## Audio acquisition and synchronization

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| ACQ-01 | Capture the four raw microphones in one six-channel 16 kHz stream from one device | Stream opens at 6 channels, 16000 Hz. A 60 s recording has 6 channels and no gaps | T `LiveSourceTests`, B HB-1 | SW verified with a stand-in device. Bench pending |
| ACQ-02 | Raw channels are sample-synchronous | Broadband source on the array axis: each pair delay within 1 sample (62.5 microseconds) of zero, standard deviation at most 30 microseconds over 60 s | B HB-4 | Target. Bench pending |
| ACQ-03 | Report every lost-sample event and never analyse across one | Injected driver overflow or queue drop: block excluded, counter rises, status shows OVERFLOW, analysis restarts on contiguous audio | T `test_overflow_block_is_excluded_and_reported`, `test_full_queue_drops_are_counted_and_flagged` | SW verified |
| ACQ-04 | Select the device by name and host API. Refuse unsuitable endpoints | Two-channel endpoint, wrong rate, missing device, or two matching devices each give a specific fault message | T `DeviceSelectionTests` | SW verified |
| ACQ-05 | Stamp each block with the time of its first sample and say how good that time is | Status and logs carry `frame_end_s` and `time_quality`. Replay uses the recording clock | T `test_chunks_are_contiguous_and_stamped_from_the_adc_clock`, `test_replay_is_reproducible` | SW verified. Clock accuracy on hardware: Bench pending (HB-7) |
| ACQ-06 | The capture thread does no analysis and no file access | Callback only copies into a bounded queue (64 blocks, 8.2 s) | I `acquisition.py`, T as ACQ-03 | SW verified |
| ACQ-07 | Channel order and polarity are confirmed per unit | Tap test maps each microphone to its channel with at least 3 dB margin. No pair shows inverted correlation | T `ChannelToolTests`, B HB-2 | Tool SW verified. Bench pending |

## Detection and false alerts

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| DET-01 | Raise a candidate only on level, harmonic count, and harmonic score together, sustained over frames | Level at least 6 dB over the ambient floor, at least 4 harmonics, score at least 0.25, in 4 of the last 6 frames. Each failed criterion is reported | T `EventGateTests` | SW verified |
| DET-02 | Call it a heuristic candidate. Never a detection, probability, or identification | Fixed wording in status, logs, and page. Automated wording check | T `test_candidate_wording_never_claims_identity_or_probability` | SW verified |
| DET-03 | Unusable audio never yields a candidate | Clipped, non-finite, silent, dead-channel, and duplicate-channel blocks are excluded | T `AudioQualityTests`, `test_bad_audio_blocks_give_no_candidate_or_bearing` | SW verified |
| DET-04 | Ambient floor follows slow change and ignores short events | One-second burst moves the floor less than 0.5 dB. A lasting 15 dB rise is followed | T `test_floor_ignores_short_loud_events_and_follows_slow_change` | SW verified |
| DET-05 | Algorithmic onset delay | At most 1.0 s from source start at adequate signal level | T `test_simulated_candidate_with_bearing_and_labels`, A | Demonstrated (synthetic): median 0.58 s at 5 and 10 dB |
| DET-06 | False alert rate at a deployment site | Report events per hour with exposure hours and a 95% interval. Proposed first target: at most 1 per hour | F FT-3 | Target. Field pending |
| DET-07 | Event recall for a consenting test aircraft | Report detected over total with a 95% interval at each tested distance. Proposed first target: at least 0.8 at the nearest test distance | F FT-4 | Target. Field pending |
| DET-08 | End-to-end latency on hardware | Report median and 90th percentile. Proposed target: at most 2 s | F FT-4 | Target. Field pending |

Measured limit to keep in view: on simulated diffuse noise the candidate gate fired in 5 of 5 runs at 5 dB and in 0 of 5 at 0 dB. The level criterion sets that limit. See `docs/LIMITATIONS.md`.

## Bearing estimation and ambiguity

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| BRG-01 | Fixed angle convention and correct delay sign | A source on +X gives 0 degrees, on +Y gives 90. Sign confirmed by a method independent of the estimator | T `SimulatorIndependenceTests`, `test_known_bearings_broadband_and_rotor` | SW verified |
| BRG-02 | Withhold the bearing when it is not trustworthy, and say why | Tone, competing sources, incoherent noise, overhead or diffuse arrival, inconsistent pair delays, or disagreement across frames each give a named reason and no bearing | T `BearingTests`, `BearingTrackerTests` | SW verified |
| BRG-03 | Never repeat an old bearing | When the current frame has no estimate, nothing is released. Stale status removes it | T `test_missing_frame_releases_nothing`, `test_stale_result_loses_bearing_and_candidate` | SW verified |
| BRG-04 | Hide live and replay bearings until a physical calibration record is accepted | Uncalibrated configuration shows no bearing. Synthetic records are refused | T `test_uncalibrated_live_bearing_is_withheld`, `test_synthetic_record_cannot_enable_bearing_but_a_physical_one_can` | SW verified |
| BRG-05 | Synthetic accuracy with adequate signal | Median error under 2 degrees at 5 dB over 30 trials | T `test_moderate_snr_accuracy` | Demonstrated (synthetic): 0.5 degrees median at 5 dB |
| BRG-06 | Stationary-source accuracy on the bench | Median absolute error at most 15 degrees, every position within 45 degrees, bearing available in at least half the frames | B HB-5 | Target carried from Revision A. Bench pending |
| BRG-07 | No bearing from ambient noise alone | Report frames with a bearing per hour of background | T `test_quiet_scene_raises_nothing`, F FT-3 | Demonstrated (synthetic): 0 of 2817 frames. Field pending |
| BRG-08 | Swapped or inverted channels are detectable | Swapped pair: bearing withheld at 10 of 12 test azimuths and calibration fails. Inverted channel: flagged | T `test_swapped_channels_are_detected`, `test_inverted_channel_is_flagged`, `test_swapped_channels_fail_calibration` | SW verified |
| BRG-09 | Report the share of each event with no bearing | `unavailable_fraction` in every event record | T `EventBuilderTests` | SW verified |

## Latency, processing load, and continuous operation

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| PRF-01 | Processing keeps up with real time with margin | Smoothed load at most 50% of real time on the host | T, A `tools/characterize.py` | Demonstrated (synthetic) on the development PC: 7.7% median block, 10.5% pipeline. Bench pending on the fielded host |
| PRF-02 | If processing falls behind, say so | Queue overflow is counted and shown as lost samples | T as ACQ-03 | SW verified |
| PRF-03 | Run continuously | 24 h without restart, no lost samples from software, memory growth under 50 MB | B HB-8 | Target. Not run |
| PRF-04 | Stated timing budget | Block 256 ms, step 128 ms, onset after 4 qualifying steps | I, A | Stated in `docs/DSP_NOTES.md` |

## Calibration and configuration management

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| CAL-01 | Strict configuration | Unknown keys and out-of-range values stop the program with the key name | T `ConfigTests` | SW verified |
| CAL-02 | Calibration is evidence-based | See BRG-04. Record holds file hashes, fit, errors, and criteria | T `CalibrationTests` | SW verified |
| CAL-03 | Every output names its configuration | Configuration SHA-256 in status, events, and recording sidecars | T `test_record_then_replay_round_trip_with_gap_and_rotation` | SW verified |
| CAL-04 | Per-unit records | Firmware, channel map, and calibration records exist for each physical unit | I `records/` templates | Templates provided. To be filled per unit |
| CAL-05 | Recalibrate after change | Any change to board, firmware, routing, geometry, mount, strap, or windscreen invalidates the record | I, procedure HB-5 | Procedural. The software enforces geometry and channel changes only |

## Environmental and power

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| ENV-01 | Prototype operating envelope | Dry weather, 0 to 35 C, sheltered from rain. No ingress rating is claimed | I | Stated limit. Not tested |
| ENV-02 | Array power | Supply current measured and within one USB 2.0 port (500 mA) | B HB-3 | Bench pending. The manufacturer publishes no figure |
| ENV-03 | Host noise | Host fan raises the array's band level by less than 1 dB at the chosen cable length | B HB-6 | Bench pending |
| ENV-04 | Wind | Report the wind speed at which the ambient floor rises 10 dB with the foam cap fitted | F FT-2 | Field pending |

## Data retention and privacy

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| DAT-01 | No raw audio is stored unless the operator asks | A run without `--record-raw` creates no audio file | T `test_no_audio_files_unless_recording_is_requested` | SW verified |
| DAT-02 | Recordings are traceable and never overwritten | Sidecar with SHA-256, gaps, configuration hash, labels. Existing names are refused | T as CAL-03 | SW verified |
| DAT-03 | Retention is deliberate | `purge` lists first and deletes only with `--confirm`. Proposed retention: 30 days unless a test plan says otherwise | T `RetentionTests` | SW verified. Period is a Target |
| DAT-04 | Nothing leaves the computer | Server binds to 127.0.0.1, refuses other Host headers, loads nothing external. No outbound connections in the code | T `ServerTests`, `DashboardSourceTests`, I | SW verified |
| DAT-05 | Recording is visible | Dashboard flag RAW RECORDING ON, console notice | T `test_overflow_clipping_and_recording_are_flagged` | SW verified |

## Operator interface and diagnostics

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| UI-01 | The operating mode is always visible and simulated output cannot pass as live | Mode badge on every page state. Simulated data is watermarked. Events keep their mode | T `OperatorViewTests`, browser check | SW verified |
| UI-02 | Stale input is obvious and removes current values | After 2.5 s with no result: STALE headline, no bearing, no candidate, no levels | T `SharedStateTests`, browser check | SW verified |
| UI-03 | Evidence is shown | Each criterion shows its value, its threshold, and met or not met | Browser check, I | Verified by inspection |
| UI-04 | No unmeasured quantities | No range rings, no position accuracy, no probability. Scores are labelled as heuristic | T `test_page_states_its_limits` | SW verified |
| UI-05 | Loss of the sensor process is obvious | Page reports lost contact within 2 s and clears the dial | Browser check | Verified by inspection |

## Recovery

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| REC-01 | Recover from device disconnection | Fault shown, retries at 1, 2, 5, 10, then every 15 s, resumes when the device returns | T `test_live_recovers_from_disconnect_and_stall`, `test_device_missing_at_start_then_appears`, B HB-9 | SW verified by injection. Bench pending |
| REC-02 | Detect stalled input | No audio for 2.0 s raises a fault and a restart | T `test_stall_and_disconnect_raise_faults` | SW verified |
| REC-03 | Survive a software fault | Restart after one fault. Stop and say so after 5 in 60 s | T `test_software_fault_restarts_then_gives_up_when_repeated` | SW verified |
| REC-04 | A replay is never restarted silently | Any fault ends the replay as FAILED | T `test_replay_fault_is_final` | SW verified |
| REC-05 | Faults are recorded | Health records in the event log | T `test_health_records_are_logged` | SW verified |

## Multi-node operation

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| MN-01 | Fusion uses only calibrated bearings and a surveyed site file | Uncalibrated or simulated observations are refused unless explicitly allowed for tests | T `test_observation_conversion_and_gates` | SW verified |
| MN-02 | Uncertainty includes bearing noise, heading error, and node-position error | Matches the closed form and a Monte Carlo run within 6% | T `test_uncertainty_matches_closed_form_and_monte_carlo` | SW verified |
| MN-03 | Reject weak geometry | Crossing under 20 degrees, crossing behind a node, error ellipse over half the range, or excess time spread: no estimate | T `test_rejections` | SW verified |
| MN-04 | Allow for sound travel time | Track fusion aligns on emission time. Position error under 0.3 m for a 20 m/s synthetic source | T `test_emission_time_alignment_removes_motion_bias` | SW verified |
| MN-05 | Never present a crossing as proof | Output says `validated_location: false`. Two nodes: no consistency check, stated. Three nodes: inconsistent bearings rejected | T `test_exact_crossing_and_honest_status`, `test_three_nodes_expose_an_inconsistent_bearing` | SW verified |
| MN-06 | Node clocks agree well enough for bearing fusion | Offset between node clocks at most 50 ms | B HB-7 | Target. Bench pending |
| MN-07 | Raw inter-node time-difference localization | Needs sample clocks aligned to about 0.1 ms | A `docs/MULTINODE.md` | Not started. Separate design |
| MN-08 | Field position error | Report against surveyed truth | F FT-5 | Field pending. No target yet |

## Classifier

| ID | Requirement | Acceptance criterion | Method | Status |
|---|---|---|---|---|
| CLS-01 | Windows from one recording session stay in one partition | Split by group. Duplicate audio across partitions is refused | T `test_whole_groups_go_to_one_partition`, `test_identical_audio_in_two_partitions_is_refused` | SW verified |
| CLS-02 | No untested model labels real audio | No model ships. A model trained only on synthetic data is refused in live and replay | T `test_synthetic_model_is_refused_for_real_audio` | SW verified |
| CLS-03 | Every model has a card | Data provenance, split, metrics with intervals, warnings | T `test_trained_model_is_valid_marked_synthetic_and_documented` | SW verified |
| CLS-04 | A trained model exists and is evaluated on held-out real sessions | Event recall and false alerts per hour reported on unseen sites and aircraft | F FT-3, FT-4 | Not started. Depends on data collection |
