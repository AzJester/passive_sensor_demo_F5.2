# Passive acoustic UAS sensor prototype

Revision B, software version 0.2.2, 10 November 2026.

A sensor node that listens with a four-microphone USB array, flags rotor-like sound with a transparent heuristic, estimates the direction the sound comes from, and shows its evidence and its own health on a local dashboard. It processes everything on the computer it runs on and needs no network. It is passive: it listens, records only when told to, and transmits nothing.

## What it is and is not

| It does | It does not |
|---|---|
| Capture four synchronized microphones through one six-channel USB stream | Identify aircraft. The candidate is a heuristic for harmonic sound. Fans and engines also match |
| Raise a "rotor-like sound candidate" with the evidence shown | Give a probability or a confidence |
| Estimate the apparent bearing of the dominant coherent sound, and withhold it when it is not trustworthy | Measure range or position from one array |
| Report lost samples, clipping, dead channels, stalls, and disconnections, and recover from them | Hide a fault or keep showing an old value |
| Record raw audio and metadata when asked | Record anything by default |
| Estimate position offline from two or more calibrated nodes, with uncertainty | Prove a location. Two crossing bearings are an estimate |

**Verification status in one paragraph.** The software passes 177 automated tests (one more runs at packaging) on synthetic and injected input. The dashboard, the drawing set, and the demonstration page were checked in a browser. **No hardware was connected.** Six-channel capture on Windows, microphone order, timing, bearing accuracy, and all detection performance on real sound are unverified. See `docs/VERIFICATION_REPORT.md` and `docs/LIMITATIONS.md`.

## Look first, without installing anything

- Open `demo/index.html` in a browser. It shows a simulated sensor and a 3D mock-up of the hardware. Everything in it is simulated or drawn, and it says so.
- Open `hardware/drawings/SLM-PAS-DRAWING-SET.pdf` for the S&L Milestone Solutions engineering drawings. They are revision A, PRELIMINARY. Nothing has been built from them.

## Install on Windows

1. Install 64-bit Python 3.11 or later from https://www.python.org/downloads/windows/ with the "py launcher" option.
2. Put this folder somewhere with a **short path**, for example `C:\PAS`. Python on Windows fails on paths longer than 260 characters.
3. Double-click `Install.cmd`. Or in PowerShell, from this folder:

```powershell
.\install_windows.ps1
```

It creates `.venv`, installs NumPy and sounddevice, and runs the tests. If script execution is blocked, run the steps by hand:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -b
```

## Try it without hardware

Double-click `Start-Simulation.cmd`, or:

```powershell
.\.venv\Scripts\python.exe -m pasensor run --mode simulated
```

Open http://127.0.0.1:8765 . The badge reads **SIMULATED DATA - NOT REAL AUDIO** and every chart is watermarked. After ten seconds of ambient measurement a synthetic rotor-like source appears in bursts and moves around the array. A pure tone appears briefly at 44 s to show a bearing being withheld. Other scenes: `--scenario quiet`, `flyby`, `fan`.

## Run with the array

Do these once per unit, in order. Each is a procedure with pass criteria.

1. Buy the parts: `hardware/BOM.md`.
2. Provision and read back the firmware: `firmware/PROVISIONING.md`.
3. Prove six-channel capture and identify the channels: HB-1 and HB-2 in `docs/TEST_PROCEDURES.md`.
4. Build the mount: `hardware/HARDWARE_BUILD.md`.
5. Calibrate the bearing with a loudspeaker: HB-5. This writes the calibration record that unlocks bearing display.

Then:

```powershell
Copy-Item config\node.example.json config\node.json      # edit node_id and paste the calibration block
.\.venv\Scripts\python.exe -m pasensor devices
.\.venv\Scripts\python.exe -m pasensor run --mode live --event-log logs\events.jsonl
```

or double-click `Start-Live.cmd`. Add `--record-raw recordings\<name>` only when you mean to save audio.

Replay a recording:

```powershell
.\.venv\Scripts\python.exe -m pasensor run --mode replay --wav recordings\<file>.wav
```

Day-to-day use, the meaning of every dashboard element, logs, privacy, and troubleshooting: `docs/OPERATING.md`.

## Where things are

| Path | Content |
|---|---|
| `pasensor/` | The software. `python -m pasensor --help` lists commands |
| `pasensor/web/` | Dashboard page, script, and style sheet |
| `config/` | `node.example.json` (one node), `site.example.json` (multi-node site) |
| `tests/` | Automated tests. `Run-Tests.cmd` runs them |
| `tools/` | `characterize.py` (synthetic characterization), `check_demo.py` (demo page check), `make_release.py` (package and checksums) |
| `hardware/` | `BOM.md`, `bom.csv`, `HARDWARE_BUILD.md`, `drawings/` (S&L Milestone Solutions drawing set, PDF and SVG), `cad/` (OpenSCAD, DXF), `make_cad.py`, `drafting.py` |
| `demo/` | `index.html`: self-contained simulated sensor demo and 3D hardware mock-up. Open it in a browser. No installation needed |
| `firmware/` | `PROVISIONING.md` |
| `records/` | Templates for unit, firmware, calibration, evaluation, dataset, and field-session records |
| `docs/AUDIT.md` | What was wrong with Revision A and what was done |
| `docs/REQUIREMENTS.md` | Requirements with acceptance criteria, verification method, and status |
| `docs/DSP_NOTES.md` | Signal processing design and review |
| `docs/MULTINODE.md` | Multi-node fusion: definitions, limits, uncertainty |
| `docs/ML_WORKFLOW.md` | Data collection, labelling, training, evaluation, dataset licences |
| `docs/TEST_PROCEDURES.md` | Bench and field procedures |
| `docs/VERIFICATION_REPORT.md` | What was tested and what passed |
| `docs/results/` | Recorded test output, demo page check, and synthetic characterization |
| `docs/LIMITATIONS.md` | Known limits and open decisions |
| `docs/OPERATING.md` | Operator instructions |
| `legacy/` | Unchanged Revision A estimator and two-bearing utility, kept for comparison |
| `app.py`, `record.py` | Revision A command names, forwarded to the new commands |
| `CHANGELOG.md`, `VERSION`, `SHA256SUMS.txt` | Release identity and file checksums |

## Requirements

Windows 11, Python 3.11 or later, NumPy 2.x, sounddevice 0.5.x. Verified on Python 3.14.7 with NumPy 2.5.4 and sounddevice 0.5.6 (`requirements-lock.txt`). The firmware image and the manufacturer's control tool are not included. Get them from the manufacturer as `firmware/PROVISIONING.md` describes.

## Verify the package

```powershell
Get-Content SHA256SUMS.txt | ForEach-Object {
    $hash, $file = $_ -split '\s+', 2
    if ((Get-FileHash $file -Algorithm SHA256).Hash.ToLower() -ne $hash) { "MISMATCH $file" }
}
```

No output means every file matches.
