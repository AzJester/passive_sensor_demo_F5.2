# Changelog

## 0.2.3, Revision B, 2026-11-10

The sensor software, the drawing set, and the demonstration page are unchanged. This release puts the public demo address at the top of the README.

### Added

- `README.md`: the public demo address, at the top.
- `tests/test_package.py`: the README must carry the public demo address in its first lines.

### Changed

- `tools/make_release.py`: the `executive_brief` folder is not part of the release archive. The brief describes the package.
- The release archive now also holds `.gitattributes` and `.github/workflows/pages.yml`. Both came with the move to GitHub: the first keeps files byte for byte, and the second publishes the `demo` folder as the public demo page.

### Fixed

- `tests/test_dashboard.py`: five dashboard server tests failed with Windows error 10048 when the test PC had no free outgoing network port. The test client now picks its own source port in that case. What the tests check is unchanged.

## 0.2.2, Revision B, 2026-11-10

The sensor software and the drawing set are unchanged from 0.2.1. This release fixes the demonstration page on narrow screens.

### Fixed

- `demo/index.html`: on a phone-width screen the "Hardware in 3D" tab kept its two-column layout, so the 3D view and the parts table were squeezed side by side. An inline style overrode the narrow-screen rule. The layout is now a style rule, and the tab stacks into one column. The 3D view is shorter on a narrow screen, the Scene menu stays inside its panel, and a wide table scrolls inside its card.

### Added

- `tools/check_demo.py`: an eleventh check loads the page in 390 px wide frames and fails if either tab scrolls sideways or the 3D tab does not stack.
- `tests/test_package.py`: the demo page test now refuses an inline column layout.

## 0.2.1, Revision B, 2026-11-10

The sensor software behaves as it did in 0.2.0. This release adds the engineering drawing set and a demonstration page.

### Added

- Engineering drawing set for S&L Milestone Solutions in `hardware/drawings/`: four ANSI B sheets as vector PDF and SVG, and one combined PDF. SLM-PAS-M01 carrier plate, SLM-PAS-M02 foam parts, SLM-PAS-A01 sensor head assembly with parts list, SLM-PAS-E01 interconnection diagram. Revision A, status PRELIMINARY. Nothing on them has been built.
- `hardware/drafting.py`: a small drafting engine (sheet frame with zones, title and revision blocks, dimensions, leaders, balloons, hatching). It writes SVG and PDF from one display list and needs no third-party package.
- `demo/index.html`: a self-contained demonstration page with a simulated sensor and a 3D mock-up of the hardware. `tools/check_demo.py` checks it in a headless browser.
- Tests for the drawing set (title block content, text collisions, PDF structure, plate feature clearances) and for the demo page (no external resources, required labels, drawing dimensions).

### Changed

- `hardware/make_cad.py` now writes the drawing set. The numbered sheets replace the three sketches from 0.2.0 (`carrier_drawing.svg`, `stack.svg`, `wiring.svg`).
- Carrier plate: the two cable-tie holes moved clear of the strap, to X150 Y110 and X150 Y126. `carrier.scad` and `carrier.dxf` changed with them. Drawing the assembly to scale showed the old holes sat under the strap.
- `HARDWARE_BUILD.md`: each strap end now passes down through its slot and closes around the plate edge. The 0.2.0 text ran the strap under the plate centre, where the adapter plate and stand sit.

## 0.2.0, Revision B, 2026-11-09

Built from the Revision A package after an independent audit (`docs/AUDIT.md`).

### Fixed

- The dashboard page was missing from Revision A and could not load. A new dashboard is included.
- Acquisition faults ended the worker for good. The pipeline now reconnects with backoff and reports each fault.
- Capture, analysis, and file writing shared one thread. Capture now runs in the driver callback with a bounded queue. Analysis and writers have their own threads.
- Wrong bearings were released at low signal-to-noise. The estimator now weights by coherence, tests significance, cross-checks pair delays against a plane wave, and requires agreement across frames.
- Simulated bearings were marked calibrated. They are now marked simulated and never calibrated.
- Any text counted as a calibration record. A record must now exist, match the unit, come from physical recordings, and show a pass.
- Timestamps were publication times and replay was not repeatable. Blocks now carry capture time, and replay uses the recording clock.
- A stale status kept old measurements on screen. It now removes all of them.
- The harmonic score depended on a 5 Hz search grid.
- Level was computed before DC removal.

### Added

- Explicit modes: `run --mode live`, `replay`, or `simulated`, labelled everywhere.
- Device selection by name and host API, with format checks.
- Independent time-domain acoustic simulator and fault injection.
- Separate audio quality, detection evidence, bearing quality, and identity outputs.
- Rolling ambient floor, K-of-M candidate gate with reported reasons.
- Event records with onset, offset, evidence, and bearing statistics. Health records. Observation log for fusion.
- Opt-in recorder with sidecar metadata, gap list, hashes, rotation, and a disk-space floor. `purge` for retention.
- Commissioning tools: `devices`, `record`, `chanmap`, `chaninfo`, `calibrate`.
- `evaluate`: false alerts per hour, event recall, latency, bearing error, unavailable fraction, lost samples, load.
- Multi-node fusion with heading and position error, sound-travel alignment, geometry rejection, three-node consistency, and event association.
- Classifier feature extraction, leakage-safe training pipeline, model card, and JSON model format. No trained model.
- Strict configuration with unknown-key errors and conversion of Revision A files.
- Server hardening: Host header check, content security policy, read-only, fixed static table.
- Hardware package: BOM with dated prices, parametric carrier CAD (OpenSCAD, DXF, drawings), interconnect, power, assembly with inspection points, host and custom electronics assessments.
- Firmware provisioning guide checked against the vendor repository, with recovery steps.
- Requirements baseline with status, physical test procedures, record templates, limitations.

### Changed

- Default analysis band 300 to 3400 Hz (was 350 to 1800).
- Analysis step 128 ms with 256 ms blocks (was 256 ms, no overlap).
- Carrier plate redesigned with strap slots, tie holes, and an adapter zone.
- Firmware image named in the guide is now v2.1.1 (v2.1.0 is archived upstream).

### Kept

- `app.py --demo`, `--live`, `--wav` and `record.py` still work and forward to the new commands.
- `legacy/` holds the unchanged Revision A estimator and two-bearing utility with its 15 tests.

### Not done

- No hardware was connected. See `docs/LIMITATIONS.md` and `docs/VERIFICATION_REPORT.md`.

## Revision A, 2026-11-09

Original package as received. SHA-256 of the archive: `bdd2b7825e590423a0100a5ecb0447da4d84827df2a1af4bdbdd0ba02a88735a`.
