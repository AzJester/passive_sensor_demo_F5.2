# From harmonic heuristic to trained classifier

Revision B. Code: `pasensor/classifier.py`, `pasensor/ml.py`, `pasensor/evaluate.py`.

## Where things stand

| Piece | State |
|---|---|
| Harmonic heuristic | Implemented, runs live, characterized on synthetic signals only |
| Feature extraction for a model | Implemented. The same code runs in the live pipeline and in training |
| Training, grouped splitting, leakage checks, model card | Implemented and tested end to end on synthetic data |
| Event-level evaluation (false alerts per hour, recall, latency) | Implemented and tested on synthetic sessions |
| Inference in the pipeline and on the dashboard | Implemented. Shows "Not assessed" when no model is installed |
| **Trained model** | **None.** No weights ship. No real drone audio was used |
| **Validation metrics on real audio** | **None exist** |

**The remaining dependency is data.** A usable classifier needs recordings from this array, at real sites, of real aircraft and real background. The public datasets below cannot stand in for that, for licence and for suitability reasons. Until that data exists, the sensor reports heuristic candidates and no identity.

## 1. Data collection

Collect with the sensor itself so the microphones, enclosure, sample rate, and processing match deployment. Procedures FT-1 (background) and FT-4 (aircraft) in `TEST_PROCEDURES.md`.

What to collect:

- **Positive sessions.** Several multirotor types and sizes, hovering and moving, at several distances, on several days and sites. Include take-off, landing, and throttle changes.
- **Hard negatives.** The things the heuristic already confuses: lawn mowers, leaf blowers, generators, air-conditioning units, compressors, motorbikes, helicopters, propeller aircraft, wind through structures.
- **Ordinary negatives.** Traffic, speech, birds, insects, rain on nearby surfaces, quiet nights.
- **Conditions.** Calm and windy, with and without the foam cap, day and night.

What to record with each session: the sidecar that the recorder writes (time, configuration hash, gaps), plus site, weather, aircraft type, and a ground-truth source for aircraft position and time (flight log).

Volume to plan for before a first model is worth evaluating: on the order of ten hours of background across at least three sites and ten sessions, and at least twenty separate flights. These are planning figures, not thresholds with evidence behind them.

Consent and privacy: recordings capture voices. Tell people on site. Keep recordings only as long as the work needs. See `OPERATING.md`.

## 2. Labelling

Label at the level of time intervals inside a session, not clips cut out of it.

- For aircraft sessions, take intervals from the flight log (motors on to motors off), then trim by ear where the aircraft is inaudible on the recording. Keep both versions: "present" and "audible".
- For background sessions, the whole session is one negative interval, with notes for notable sounds.
- Mark uncertain stretches as "unknown" and leave them out of both training and scoring.
- A second person should spot-check a sample of labels. Record disagreements.

Manifest format (`records/dataset_manifest_template.csv`): one row per labelled interval with `path, label, session_id, site, day, source, start_s, end_s, synthetic`. `session_id` is the original continuous recording. It is the unit that must never be split.

## 3. Features

`classifier.frame_features` and `window_features`:

- Per 256 ms block: 40 log-mel band energies from 60 Hz to 7.6 kHz, from the microphone-averaged power spectrum, **with the mean removed**. Removing the mean makes the feature describe spectral shape and not loudness. Loudness tracks distance and gain, and a model that learns "drones are the loud clips" has learned the dataset, not the sound.
- Five harmonic features from the heuristic: score, harmonic count, fundamental, harmonic power fraction, mean prominence.
- Per window of 8 blocks (about 1 s): mean and standard deviation of each of the 45 block features. 90 numbers per window.

The feature definition is stored inside every model file. The loader refuses a model whose definition differs from what the software computes.

## 4. Model selection

Start small and add capacity only when held-out results justify it.

| Stage | Model | Why | What it needs |
|---|---|---|---|
| 0 | Harmonic heuristic | Transparent baseline. Reported beside every model | Nothing |
| 1 | Logistic regression on window features (implemented) | Few parameters, hard to over-fit on little data, weights are readable, model file is plain JSON | A few hours of varied data |
| 2 | Gradient-boosted trees or a small multilayer network on the same features | Captures interactions between bands | More data, and a second library |
| 3 | Small convolutional network on one-second log-mel patches | Learns time-frequency patterns such as speed changes | Tens of hours across many sites and aircraft. Export to ONNX for inference |
| 4 | Pretrained audio embedding with a small head | Makes use of large general audio corpora | A check of the embedding's licence, and compute on the host |

Move up a stage only when the simpler model's errors on held-out sessions point to something the next stage can fix. Published high accuracies in this field mostly come from indoor or augmented clips split at clip level. One study on a real street scene reports an F-score near 0.80, which is a more useful expectation.

## 5. Training

```powershell
python -m pasensor train --manifest data\manifest.csv --out models\lr-001 `
    --group-by session_id --target-fpr 0.01 --provenance "who recorded what, where, under which licence"
```

What it does:

1. Assigns each **group** (by default each `session_id`) to train, validation, or test by a stable hash of its name. A session keeps its partition forever, even after more data is added.
2. Hashes every audio file and refuses to continue if identical audio sits in two partitions under different session names.
3. Extracts features with the live pipeline's code.
4. Fits normalization and the model on train only.
5. Picks the decision threshold on validation for a target window false-positive rate.
6. Scores the test partition once and writes `model.json` and `MODEL_CARD.md`.

Stricter grouping for honest generalization claims: `--group-by site` holds out whole sites. `--group-by site,day` holds out site-days. To claim the model works on an unseen aircraft type, hold out that type by putting it in `session_id` naming or splitting by hand into a separate evaluation manifest.

To install a model, set `classifier.model_path` in the node configuration. A model trained only on synthetic data is refused in live and replay modes.

## 6. Preventing evaluation leakage

Windows cut from one recording are near copies: same aircraft, same distance, same background, same minute. If some go to training and others to test, the test score measures memory. This is a documented, common error. The UrbanSound8K maintainers warn that reshuffling their folds inflates scores. A 2026 preprint on a drone audio set reports the detection rate at 1 percent false alarms falling from 0.80 to 0.75 once splits were grouped by session.

Safeguards in this project:

- Splitting is by group, in code, with no option to split by window.
- Duplicate audio across partitions stops training.
- Normalization constants and the threshold never see test data.
- The model card lists groups per partition and warns when the test partition has fewer than five groups in a class.
- The test partition is scored once per training run. If you tune anything after looking at test results, collect new test sessions.

Things the code cannot check, which you must: the same flight recorded by two nodes is one session. A loudspeaker replay of a recording is the same session as its source. Sessions from one afternoon at one site are closely related even with different names. Use `--group-by site,day` when in doubt.

## 7. Evaluation

Two levels, reported separately.

**Window level** (`train` output): area under the ROC curve, recall, and false-positive rate with 95 percent intervals, beside the harmonic heuristic on the same windows.

**Event level** (`python -m pasensor evaluate`): replay whole held-out sessions through the full pipeline and report false alert events per hour with exposure, event recall, detection latency, bearing error, bearing unavailable fraction, lost samples, and processing load. This is what matters to an operator. A window false-positive rate of 1 percent is still dozens of false windows per hour.

Report by condition: distance, aircraft type, site, wind. Report failures. State sample sizes. With zero false alerts in T hours the 95 percent upper bound is 3.7 / T per hour. With n of n events detected, the lower bound on recall is roughly 1 - 3/n.

A score becomes a "probability" only after calibration on held-out sessions and a reliability check. Until then the software labels it "uncalibrated model score".

## 8. Public datasets: what was checked on 9 November 2026

Licence terms change. Read the current licence yourself before using any of these. "Not verified" means the page did not state it or could not be read.

### Drone recordings

| Dataset | Source | Licence as stated | Content | Fit for this project |
|---|---|---|---|---|
| DroneAudioDataset (Al-Emadi and others) | github.com/saraalemadi/DroneAudioDataset | "Academic Research and Educational Use Licence". Changed 4 October 2026. Non-commercial academic use only, and it excludes defence, security, and surveillance uses including drone detection systems | Indoor propeller noise from two small drones, mixed with noise clips | **Do not use** for this product without written permission |
| Drone Audio Detection Samples (DADS) | huggingface.co/datasets/geronimobasso/drone-audio-detection-samples | Tagged MIT, but it repackages sets with stricter licences (including the one above) and tells users to check each | 180,320 mono 16 kHz files. Drone clips average 0.6 s. One split, no source-recording IDs | **Do not use.** Licence is not clean and session grouping is impossible, so leakage cannot be prevented |
| Drone-detection-dataset (Svanstrom and others) | github.com/DroneDetectionThesis/Drone-detection-dataset, doi 10.5281/zenodo.5500576 | CC0-1.0 on GitHub, "other-open" on Zenodo | 90 ten-second clips: 30 drone, 30 helicopter, 30 background. Outdoor, real flights to 200 m, three aircraft types | Usable. Only 15 minutes. Good as a small outside check, not for training |
| UAV Acoustic Localization Dataset (Western Sydney University, 2026) | doi 10.26183/07nm-4p35 | CC BY-SA 4.0 | 24-channel ground array at 48 kHz, open field, two sites, GPS logs, no-drone segments, one aircraft type | Best public fit for bearing work. Different array, so use it to test methods, not to train this unit's model. Share-alike applies |
| DroneNoise Database (University of Salford) | doi 10.17866/rd.salford.22133411 | CC BY 4.0 | Field overflights at one site on one day. Channels and rates not verified | Possible supplementary positives after checking format |
| MMAUD (NTU) | github.com/ntu-aris/MMAUD | CC BY-NC-SA 4.0 | Four microphone-array nodes, outdoor flights with surveyed truth, five aircraft types | Non-commercial only |
| DREGON (Inria), DroneAudioset (NeurIPS 2025), AIRA-UAS | dregon.inria.fr, huggingface.co/datasets/ahlab-drone-project/DroneAudioSet, aira.iimas.unam.mx | Academic only, MIT, not verified | Microphones mounted on or with the drone | **Wrong viewpoint.** Ego-noise at arm's length is not what a ground sensor hears |

### Background and negative sounds

| Dataset | Licence as stated | Note |
|---|---|---|
| ESC-50 | CC BY-NC (the ESC-10 subset is CC BY) | Non-commercial, except ESC-10 |
| UrbanSound8K | CC BY-NC 3.0 | Non-commercial. Use the predefined folds |
| FSD50K | Dataset CC BY. Individual clips are CC0, CC BY, CC BY-NC, or Sampling+ | Filter by per-clip licence using the dataset's metadata |
| AudioSet | Labels and features CC BY 4.0. Audio is not distributed | No UAV class. Its "Drone" label is the musical drone. "Aircraft engine" may include multirotors, so do not treat it as a clean negative |
| DEMAND | CC BY-SA 3.0 | 16-channel noise in real places, including outdoor. Share-alike |
| TAU Urban Acoustic Scenes 2019 | Non-commercial | |

Public background sets were made with other microphones. They can widen the negative class at stage 3 and above. They do not replace background recorded by this array at the deployment sites.

## 9. References

- Intaratep, Alexander, Devenport, Grace, Dropkin (2016). Experimental Study of Quadcopter Acoustics and Performance at Static Thrust Conditions. AIAA 2016-2873. Tones at blade-pass frequency, shaft rate, and their harmonics dominate up to about 6 kHz for a small quadcopter, with broadband noise above about 600 Hz.
- Tinney, Sirohi (2018). Multirotor Drone Noise at Static Thrust. AIAA Journal 56(7). doi 10.2514/1.J056827.
- Anwar, Kaleem, Jamalipour (2019). Machine Learning Inspired Sound-Based Amateur Drone Detection for Public Safety Applications. IEEE Transactions on Vehicular Technology 68(3). doi 10.1109/TVT.2019.2893615.
- Al-Emadi, Al-Ali, Al-Ali (2021). Audio-Based Drone Detection and Identification Using Deep Learning Techniques. Sensors 21(15):4953. Indoor, noise-augmented clips.
- Jeon and others (2017). Empirical Study of Drone Sound Detection in Real-Life Environment with Deep Neural Networks. arXiv 1701.05779. Real urban test set, best F-score about 0.80.
- Jekaterynczuk, Piotrowski (2025). Outdoor Microphone Range Tests and Spectral Analysis of UAV Acoustic Signatures for Array Development. Sensors 25(22):7057. Outdoor signal-to-noise is often limited by low-frequency wind and background.
- Grinstein and others (2024). Steered Response Power for Sound Source Localization: a tutorial review. EURASIP Journal on Audio, Speech, and Music Processing 2024:59. doi 10.1186/s13636-024-00377-z.
- Kapoor, Narayanan (2023). Leakage and the reproducibility crisis in machine-learning-based science. Patterns 4:100804.
- Salamon, Jacoby, Bello (2014). UrbanSound8K dataset notes on fold use. urbansounddataset.weebly.com.
- Shulman (2026). EchoHawk. arXiv 2606.29589. Preprint, not peer reviewed.

The fundamental search range of 70 to 500 Hz in the heuristic is an engineering assumption. No peer-reviewed source for typical blade-pass frequencies across aircraft sizes was verified for this document.
