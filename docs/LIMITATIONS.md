# Known limitations and unresolved engineering decisions

Revision B, software 0.2.2, 10 November 2026. Read this before relying on anything the sensor shows.

## What has not been verified at all

1. **No hardware was connected.** The array was not bought, flashed, mounted, or recorded. Every hardware statement comes from manufacturer documents.
2. **Six-channel capture on Windows is unproven.** The vendor documents two-channel recording on Windows and six-channel capture in general. Whether Windows offers all six channels to applications is the first thing to find out (HB-1).
3. **Live capture has only run against a stand-in for the audio driver.** The real PortAudio path, real overflow behaviour, and real timestamps are untested.
4. **No real audio of any kind was processed.** All signals were simulated.
5. **Nothing is known about detection of real aircraft.** No range, no recall, no false-alert rate, no bearing accuracy.
6. **The drawing set is preliminary.** The sheets in `hardware/drawings/` are revision A, status PRELIMINARY. Their author generated them, checked them by rule, and read them. No second person checked them, so the DRAWN, CHECKED, and APPROVED names are blank. No part was cut, fitted, or measured against them. The adapter plate hole pattern, the case diameter and height, and the USB socket position come from listings, not from parts in hand. The OpenSCAD file was not compiled.
7. **The demonstration page proves nothing about performance.** `demo/index.html` simulates sound with invented levels and runs a simplified estimator. Its 3D view is a mock-up from the drawing dimensions, not a picture of a built unit. It was checked in one browser, on a desktop screen and in a phone-width frame, not on a real phone.
8. **The software ran on one computer, one Python version (3.14.7), one NumPy version (2.5.4).** Linux and Raspberry Pi are untested.
9. **No long run.** The longest continuous run was a few minutes.

## Limits of the method

### Detection

- **The candidate is a heuristic for harmonic sound.** Fans, engines, generators, mowers, and helicopters raise it. It does not identify drones.
- **It needs about 5 dB of signal over ambient in the analysis band** (simulation: 5 of 5 runs at 5 dB, 0 of 5 at 0 dB). Faint sources that a person can hear may not raise it.
- **A source already sounding when the sensor starts is masked** until the ambient drops for a while. The floor is learned from what is heard first.
- **A source that stays for most of five minutes raises the floor** and can lose its candidate.
- **Only one fundamental is tracked per block.** Two aircraft are not separated.
- **The fundamental search covers 70 to 500 Hz.** Very small, fast rotors may sit above that.
- **No trained classifier exists.** Identity is "not assessed".

### Bearing

- **One direction per block, of the loudest coherent sound.** With no candidate active, that sound can be a road.
- **Reflections give wrong bearings that look right.** They are stable and pass every quality gate.
- **Small aperture.** The array is 66 mm across. Bearings are coarse for low-pitched sound and degrade quickly with noise. In simulated diffuse noise, bearings below about -5 dB are not trustworthy.
- **Azimuth only.** No elevation output, no range. Near-overhead sources give no azimuth.
- **The enclosure is not modelled.** The case scatters sound above about 1 kHz. Real errors will be larger than simulated ones.
- **Wind noise** is uncorrelated between microphones and buries the signal. Foam helps a little.
- **A channel order rotated by one position gives bearings off by exactly 90 degrees with no warning.** Only the physical axis test catches it.
- **No polarity correction setting.** An inverted microphone is flagged and must be fixed in hardware or reported.

### Multi-node

- **Two nodes cannot check themselves.** One bad bearing gives a confident wrong point.
- **Association is a plausibility test.** Two similar aircraft at once will be merged.
- **Clock quality on Windows is unmeasured.**
- **Height is ignored.**
- **Inter-node time-difference localization is not possible with these nodes.**

### Software

- **The dashboard has no access control** because it listens on the loopback address only. It cannot be viewed from another device.
- **The event list is kept in memory.** Use `--event-log` to keep it.
- **Windows path length.** Python on Windows fails on paths longer than 260 characters unless long paths are enabled. Install in a short folder such as `C:\PAS`. This was hit during development.
- **The analysis band, thresholds, and gate settings were tuned on simulation.**
- **Replay accepts integer PCM WAV only**, with the configured channel count and sample rate.

## Unresolved engineering decisions

| # | Decision | Options | What decides it |
|---|---|---|---|
| 1 | Is Windows a workable host for six-channel capture? | Keep Windows, or move the node to Linux | HB-1 on the real device |
| 2 | Analysis band for the real enclosure | 300 to 3400 Hz, narrower, or wider | HB-5 repeated at several bands |
| 3 | Level criterion in the candidate gate | Keep 6 dB over the floor, lower it, or replace it with a test on the harmonic score rising above its own recent history | Background and flight recordings (FT-3, FT-4) |
| 4 | Floor time constant and start-up behaviour | 5 minute window, longer, or a floor saved between runs | Background recordings |
| 5 | Show a bearing when no candidate is active? | Show it labelled, as now, or hide it outside commissioning | Operator feedback |
| 6 | Wind protection | Foam cap only, foam plus fur, or a larger basket | FT-2 |
| 7 | Mounting | Strap over the case, or a printed cradle once case dimensions are known | Measured case, HB-5 with and without the strap |
| 8 | Standalone host | Raspberry Pi 4 or 5, fanless x86, or stay on a laptop | Load measured on the board, thermal test in an enclosure |
| 9 | Remote viewing | Keep loopback only, or add an authenticated, encrypted service | Deployment need. Needs a security design first |
| 10 | Classifier | Stay with the heuristic, or train stage 1 | Enough real data (see `ML_WORKFLOW.md`) |
| 11 | Third node | Two nodes, or three for a consistency check | FT-5 results |
| 12 | Custom array | Stay with the stock array, or design a larger one | Field data showing aperture or weather as the limit |
| 13 | Retention period for raw recordings | 30 days proposed | Your data policy and local law |

## Legal and safety notes

This is not legal advice. Flying test aircraft is subject to aviation rules. Recording audio in places where people talk can be subject to privacy and recording laws. Check both for your location before field work. The sensor is passive: it listens, records only when told to, and transmits nothing.
