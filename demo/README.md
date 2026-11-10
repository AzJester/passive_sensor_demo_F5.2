# Demonstration page

S&L Milestone Solutions, passive acoustic sensor node. Open `index.html` in a current browser (Edge, Chrome, Firefox, or Safari). It is one file. It needs no installation and no internet connection, and it opens no microphone.

The page has two tabs.

## Simulated sensor

The page synthesizes four microphone signals in the browser at 16 kHz, using the array geometry from the configuration (66 mm square). It then runs a simplified JavaScript estimator on those signals and shows the result the way the real dashboard does.

| Control | What it does |
|---|---|
| Scene | Multirotor circling, multirotor flying past, a machine with a steady hum, a single tone, multirotor and machine together, ambient noise only |
| Distance, Ambient noise | Made-up settings. They change the simulated level so you can watch the gates open and close |
| Unplug the array, Stall the input, Lose samples, Overload | Inject a fault so you can see what the operator is told |

What to look for:

- The purple label "SIMULATED DATA - NOT REAL AUDIO" never leaves the screen, and the dial and spectrum carry watermarks.
- The candidate card shows each criterion, its value, its threshold, and whether it is met. The score is a heuristic, not a probability.
- The machine scene raises a candidate. This is deliberate. The heuristic matches any steady harmonic source.
- The single tone raises no candidate and gives no bearing, with the reasons listed.
- A stalled input removes every current value after 2.5 s.
- The bearing is a direction only. No range is shown anywhere.

## Hardware in 3D

A mock-up of the sensor head and its deployment, built from the same dimensions as the drawing set in `hardware/drawings/`. Item numbers match the parts list on drawing SLM-PAS-A01. Drag to turn, scroll to zoom, and use the Exploded slider to separate the stack.

## What this page is not

- It is not the product software. The Python package `pasensor` is the reference. The JavaScript estimator is shorter and simpler (pairwise delays and one plane-wave fit, no coherence weighting).
- It is not evidence of performance. Source levels, noise levels, and distances are invented. Nothing here predicts detection distance or bearing accuracy on real hardware.
- It is not a photograph. Nothing shown has been built. The appearance of the purchased array, its case height, and its USB socket position are not verified. The table and computer are stand-ins.

## Checking the page

`python tools/check_demo.py` runs the page's self-test in headless Edge or Chrome and checks the result. It also loads the page in 390 px wide frames to confirm that it fits a phone-width screen. Add `--screenshots DIR` to save pictures of the main views. The last recorded run is in `docs/results/demo_check.txt`.

Address parameters used by the check, which also work by hand: `?tab=3d`, `&view=head|top|under|site`, `&explode=0..100`, `&xray=0|1`, `&nocap=1`, `?scenario=orbit|flyby|fan|tone|two|quiet`, `&fast=SECONDS`, `&fault=unplug|stall|overflow|clip`, `?selftest=1`.
