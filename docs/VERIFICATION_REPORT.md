# Verification report

Revision B, software 0.2.3. The automated tests ran on 10 November 2026 for 0.2.3. The demo checks are the 0.2.2 results, because the demo page is unchanged. The public copy of the demo page was also loaded from its web address on 10 November 2026, and its self-test passed there. The drawing set is unchanged from 0.2.1, which the tests confirm byte for byte, so the drawings were not read by eye again. Sections 2 and 5 and the dashboard checks in section 4 are the 9 November 2026 results for software 0.2.0. They were not repeated because no file under `pasensor/` changed in 0.2.1, 0.2.2, or 0.2.3 except the version number.

## Summary

| Level | What was done | Result |
|---|---|---|
| Baseline reproduction | Revision A test suite and checksums | 34 of 34 tests passed. Checksums matched. Its dashboard file was missing |
| Software verification | 178 automated tests on synthetic and injected input | 177 passed, 0 failed, 1 skipped by design before packaging (see section 3) |
| Drawing set | Four sheets generated, checked by rule, and read by eye in two renderers | No text collisions. Two design faults found and corrected (section 4). Nothing was built from them |
| Demonstration page | Self-test, a phone-width layout check, and nine screenshots in headless Microsoft Edge | 11 of 11 checks passed. This checks the demo's logic on its own simulated scenes and nothing else |
| Operator interface | Dashboard driven in a browser in simulated mode | Candidate, stalled input, and lost contact displayed correctly after one fix |
| Synthetic characterization | Estimator and gate measured on simulated scenes | Recorded in `results/characterization.md` |
| Hardware bench testing | Nothing | **Not done. No hardware was available** |
| Field validation | Nothing | **Not done** |

Everything that passed is software evidence. It shows the program does what it is designed to do on idealized input. It does not show that the device works.

## 1. Environment

| Item | Value |
|---|---|
| Computer | Intel Core Ultra 7 155H, 16 GB RAM |
| Operating system | Windows 11 Home, build 10.0.26220, x64 |
| Python | 3.14.7 (CPython, 64-bit) |
| Packages | NumPy 2.5.4, sounddevice 0.5.6, cffi 2.1.1 (`requirements-lock.txt`) |
| Audio hardware | None used. No capture device was opened |
| Command | `python -m unittest discover -s tests -t . -v` |
| Raw output | `results/test_run.txt` |
| Browser for the drawing and demo checks | Microsoft Edge 158.0.4370.12, headless |

## 2. Baseline reproduction

The Revision A archive (SHA-256 `bdd2b782...8735a`) was extracted unchanged. All 21 files matched its `SHA256SUMS.txt`. `python -m unittest discover -v` ran 34 tests, all passed, in 0.98 s. Starting its application and requesting the dashboard page fails because `dashboard.html` is absent from the archive. Details: `AUDIT.md`.

## 3. Automated tests

| Test file | Tests | What it covers |
|---|---|---|
| `test_dashboard.py` | 24 | Operator labels, stale handling, local server and its security, page source checks, command line |
| `test_dsp.py` | 38 | Geometry and conventions, simulator self-checks, bearings, audio faults, harmonic features |
| `test_gate_config.py` | 18 | Candidate gate, bearing stability gate, event records, configuration and calibration gate |
| `test_multinode.py` | 17 | Fusion geometry and uncertainty, sound-travel alignment, association, frames, two simulated arrays end to end |
| `test_triangulate.py` | 15 | Unchanged Revision A two-bearing utility (run from `legacy/`) |
| `test_package.py` | 15 | Versions, documents, requirement IDs, BOM, CAD and drawing outputs, drawing title blocks, drawing text collisions, PDF structure, plate feature clearances, demo page checks, templates, no bundled binaries, checksums |
| `test_pipeline.py` | 30 | Device selection, live capture plumbing, recorder and replay, pipeline integration, fault injection and recovery |
| `test_tools.py` | 21 | Channel tools, bearing calibration, training and leakage checks, event-level evaluation, retention |
| **Total** | **178** | 177 passed, 0 failed, 1 skipped |

The one skipped test compares every file with `SHA256SUMS.txt`. That file is written by the packaging step, after this report, so the test skips here by design. The packaging step runs it and records the result beside the archive in `dist/release_check.txt`.

### Coverage against the verification list

| Asked for | Where | Examples |
|---|---|---|
| Independently generated synthetic acoustic cases | `test_dsp.py` with `pasensor/simulate.py` | Time-domain point sources with spherical spreading, detuned multi-rotor combs, diffuse and wind noise, ground reflection. The simulator is checked on its own by direct correlation before the estimator sees its output |
| Known bearing and delay-sign cases | `test_dsp.py` | 48 azimuth and elevation cases within 1 degree. Arrival order on four axes. Pair delays within 4 microseconds of geometry. Swapped, rotated, and inverted channels |
| Fault injection and recovery | `test_pipeline.py` | Driver overflow, queue overflow, unplug, stall, device missing at start, software exception, repeated exceptions, NaN, clipping, dead channel, inverted microphone |
| Recorded-audio replay | `test_pipeline.py`, `test_tools.py` | Record then replay with gap and file rotation. Two replays give identical events. 16 and 24 bit input. Wrong formats refused |
| Application integration | `test_pipeline.py`, `test_dashboard.py` | Simulated source through the full pipeline to events and status. Command line runs as a subprocess. Revision A entry points |
| Dashboard behaviour | `test_dashboard.py` and section 4 | Labels for every state, stale handling, server headers, Host check, read-only, static checks of the page source |
| Multi-node geometry and uncertainty | `test_multinode.py` | Exact crossing, closed-form and Monte Carlo uncertainty, nine rejection cases, three-node consistency, sound-travel alignment, association, two simulated arrays end to end, Revision A utility |

## 4. Checks made by hand in a browser

The dashboard was opened in a browser against the running program in simulated mode.

| Check | Observed |
|---|---|
| Page loads with no console errors | Yes |
| Mode badge and watermarks in simulated mode | "SIMULATED DATA - NOT REAL AUDIO", three watermarks, badge SIMULATED GEOMETRY |
| Candidate with evidence and bearing | Headline "Rotor-like sound candidate (heuristic)", all criteria shown with thresholds, needle at the simulated source azimuth, harmonics marked on the spectrum. Screenshot: `images/dashboard_simulated.jpg` |
| Source made to hang for 60 s | Headline "INPUT STALE. No new audio result for 41 s.", flag STALE, "BEARING UNAVAILABLE", no needle, no trail, candidate "Not assessed". Screenshot: `images/dashboard_stale.jpg` |
| Sensor process stopped with the page open | "DASHBOARD LOST CONTACT WITH THE SENSOR PROCESS. Nothing below is current.", dial cleared |

One defect was found this way: in the stale state the acquisition card still said "Receiving audio" and old evidence stayed visible, greyed. The status now removes every measurement when stale. An automated test covers it. The stale screenshot was taken before that fix and shows the old card text.

Not checked by hand: dark mode, narrow screens, and browsers other than the one built into the development tool.

### Drawing set (10 November 2026)

`hardware/make_cad.py` writes four ANSI B sheets (SLM-PAS-M01, M02, A01, E01) as SVG and PDF from one set of dimensions.

| Check | Observed |
|---|---|
| Files match the generator | Automated. All eleven generated files are byte-identical to a fresh run |
| Text collisions and text outside the sheet | Automated, from the bounding box of every text item. None on any sheet. The same rule found collisions while the sheets were being drawn, and each was cleared |
| Title block | Automated. Company name, drawing number, revision, PRELIMINARY status, and "DO NOT SCALE DRAWING" on every sheet. No classification or distribution marking |
| PDF structure | Automated. Page count, 17 x 11 in media box, every cross-reference entry, every stream length |
| SVG sheets read by eye | Each sheet rendered in headless Edge, whole and in enlarged regions, and read |
| PDF sheets read by eye | All four pages of the combined PDF rendered with the Windows PDF engine (Windows.Data.Pdf) and read. The four single-sheet PDFs come from the same code and were checked for structure only |
| Plate feature clearances | Automated. Tie holes clear the strap, the case, and the plate edge. Slots clear the case and the adapter plate. The pad fits between the slots. Microphones lie inside the case outline |

Drawing the assembly to scale found two design faults in the 0.2.0 package. The cable-tie holes sat under the strap. The strap was described as passing under the plate centre, where the adapter plate and stand are. Both are corrected on the sheets and in `hardware/HARDWARE_BUILD.md`.

Not done: no second person checked the drawings. The DRAWN, CHECKED, and APPROVED names are blank for that reason. No part was cut or fitted. The OpenSCAD file was not compiled. The PDF uses Helvetica because the PDF writer embeds no fonts. The SVG asks for Aptos.

### Demonstration page (10 November 2026)

`python tools/check_demo.py --screenshots docs/images/demo` ran `demo/index.html` in headless Edge. Output: `results/demo_check.txt`.

| Check | Observed |
|---|---|
| Script errors | None in either tab |
| Simulated multirotor at 40 m and 120 m | Candidate on every frame after onset. Mean bearing error 0.7 and 0.9 degrees against the simulated truth |
| Simulated multirotor too faint for the level gate | No candidate |
| Simulated machine hum | Candidate raised. This is the expected false alert, and the page says so |
| Single tone | No candidate, no bearing, reasons listed |
| Ambient only | No candidate, no bearing |
| Unplug and stall, read from screenshots | "ACQUISITION FAULT" and "INPUT STALE" headlines. Candidate "Not assessed", "BEARING UNAVAILABLE", no needle, spectrum removed |
| 3D mock-up, read from five screenshots | Sensor head, exploded stack, view from above, view from below, deployed view. Item numbers match drawing SLM-PAS-A01 |
| Cable W1 path in the mock-up | 1338 mm, inside the 2 m cable |
| Turning and zooming the 3D view | Scripted pointer events: a one-pointer drag turns the view and a two-pointer spread zooms it. Not tried on a real touch screen |
| Phone-width layout (added in 0.2.2) | The page loads in 390 px wide frames. Neither tab scrolls sideways, and the 3D view and its parts table stack in one column. A picture of those frames was also read. The same check fails on the 0.2.1 page |
| Page loads nothing external and opens no device | Automated in `test_package.py` |

These numbers describe a JavaScript simulation with invented source and noise levels. They are not evidence about the Python software or about hardware. Not checked: Chrome, Firefox, Safari, a real phone, touch input, and a computer without WebGL (the page shows a message there, which was not exercised).

### Installer and the real audio library

These ran on the development PC with no array attached. No microphone was opened.

| Check | Observed |
|---|---|
| `install_windows.ps1` in a clean copy of the 0.2.0 package | Created the environment, installed NumPy 2.5.4 and sounddevice 0.5.6, ran 173 tests (172 passed, 1 skipped), exit code 0, 127 s. Not repeated for 0.2.1, 0.2.2, or 0.2.3 |
| `python -m pasensor devices` through the real PortAudio library | Listed 11 input endpoints. None offers six inputs, as expected without the array |
| `python -m pasensor run --mode live` with no array | Reported "No input device name contains 'xvf3800'" with the inputs it saw, retried with backoff, opened no capture device, and stopped cleanly |
| `app.py --help` and a simulated run from the installed copy | Both worked |

The `.cmd` launchers were not executed. They contain only the commands above plus a browser start.

## 5. Synthetic characterization

Full tables: `results/characterization.md`. All synthetic.

- Single-frame bearing: Revision B released no bearing wrong by more than 10 degrees down to -5 dB in uncorrelated noise and down to 0 dB in diffuse noise. Revision A released 32 percent wrong at -5 dB uncorrelated and 60 percent wrong at 0 dB diffuse. At -5 dB diffuse, 37 percent of Revision B's single-frame bearings were wrong. That is the edge.
- Ambient only, 6 minutes over three noise scenes: 0 bearings shown, 0 candidates.
- Candidate gate in diffuse noise: 5 of 5 runs at 10 dB and at 5 dB, 0 of 5 at 0 dB and below. Median onset 0.58 s after the source started.
- Processing: median 9.9 ms per block against a 128 ms step.

## 6. The metrics the project must report

| Metric | Evidence available now | Value | What it is evidence of |
|---|---|---|---|
| False alert events per hour | Synthetic ambient scenes, 6 minutes | 0 events | Software only. Six minutes of simulated noise says nothing about a real site |
| Event recall | Synthetic sources in diffuse noise | 5 of 5 at 5 dB and above, 0 of 5 at 0 dB and below | Software only. Shows the gate's sensitivity limit |
| Detection latency | Synthetic | 0.58 s median, algorithmic | Software only |
| Bearing error | Synthetic | 0.2 to 0.4 degrees median at 5 to 10 dB with the stability gate | Software only. No enclosure, no reflections |
| Bearing unavailable fraction | Synthetic | 0 percent at 5 dB and above during the source | Software only |
| Dropped samples | Injected faults | Every injected loss was counted and excluded | Software only. Real loss rates are unknown |
| Processing load | Development PC | 7.7 percent median per block, 10.5 percent smoothed | Real measurement of software speed on this PC |
| Any of the above on real audio | None | None | Not measured |

## 7. Defects found and fixed while verifying Revision B

Recorded so the history is honest.

1. The first harmonic search returned a wrong fundamental for every input (a NaN in the scoring). Found by the first probe. Fixed.
2. A first coherence weighting withheld bearings for weak broadband sound that Revision A handled. Measured, then replaced.
3. Diffuse noise alone produced a bearing in about one frame in six before the stability gate was added.
4. A swapped channel pair passed at 15 degrees off its symmetry axis with a 15 degree error. The fit-residual limit was tightened from 45 to 25 microseconds.
5. Bearing lines that cross behind a node were reported as poor crossing, not as behind the node.
6. The stale display kept old values (section 4).
7. Two test files had names long enough to break the Windows path limit in a deep folder. They were renamed, and the limit is documented.
8. Five of my own test expectations were wrong and were corrected against measured behaviour, not by loosening the code: start-up masking, the Revision A harmonic score, overflow frame counts, one level comparison, and a timing-sensitive frame count.
9. Drawing the assembly to scale showed the cable-tie holes under the strap and a strap route that ran through the adapter plate (section 4). Both corrected in 0.2.1.
10. The first 3D mock-up failed to start in the browser (a shader setting that must match between two programs did not). The page's own error banner showed it. Fixed.
11. The first 3D deployed view ran the cable much further than the 2 m cable in the parts list allows. The stand was lowered and the path is now checked.
12. On a phone-width screen the 3D tab of the demo page did not stack into one column. An inline style overrode the narrow-screen rule. Found while preparing a hosted copy of the page. Fixed in 0.2.2, and the demo check now covers it.
13. Five dashboard server tests failed with Windows error 10048 while 0.2.3 was being verified. The test PC had used up its outgoing network ports, so the test client could not connect to the test server. This was a state of the PC, not a fault in the server. The test client now picks its own source port when that happens, and all five pass.

## 8. What remains unverified

See `LIMITATIONS.md` for the full list. In short: the hardware, six-channel capture on Windows, live capture through the real driver, microphone order and polarity, timing between channels, the mount, the firmware procedure, every number about real sound, clock agreement between nodes, long runs, other operating systems, the OpenSCAD file, the drawings against real parts, and the demo page in browsers other than Edge.

## 9. Reproduce

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe tools\characterize.py > docs\results\characterization.md
.\.venv\Scripts\python.exe -m pasensor run --mode simulated
.\.venv\Scripts\python.exe hardware\make_cad.py
.\.venv\Scripts\python.exe tools\check_demo.py
```

The full suite takes about five minutes on the development PC. Most of that is rendering synthetic audio.
