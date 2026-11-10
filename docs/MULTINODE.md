# Multi-node operation

Revision B. Code: `pasensor/multinode.py`. Command: `python -m pasensor fuse`. Verified on synthetic geometry and on two simulated arrays. Never run with physical nodes.

## Two different things

| | Single-array bearing | Multi-node position |
|---|---|---|
| Input | Four microphones, one clock, 66 mm apart | Bearings from two or more arrays tens of metres apart |
| Output | A direction | An estimated point with an uncertainty ellipse, or a rejection |
| Needs | Channel map and axis calibration | All of that for every node, plus surveyed positions, surveyed headings, and agreeing clocks |
| Main failure | Reflection or a second source gives a wrong direction | One wrong direction gives a confident wrong point |

The dashboard shows single-array bearing only. It never shows a position. Position is computed offline from observation logs.

**Two crossing rays are not proof of a location.** Any two non-parallel rays cross. With two nodes there is nothing to check the crossing against. The software says so in every two-node result (`consistency.checked: false`) and marks every result `validated_location: false`.

## Definitions

**Site frame.** East and North in metres from an origin you choose. Site azimuth is counterclockwise from East. Compass bearing is clockwise from true North: compass = 90 - azimuth. `geometry.site_azimuth_to_compass` converts. `multinode.geodetic_to_enu` turns surveyed latitude, longitude, and height into East, North, Up about the origin.

**Node position and its uncertainty.** `east_m`, `north_m`, and `position_sigma_m`, the one-sigma uncertainty of that survey. A tape from a marked corner might give 0.3 m. A phone GPS gives several metres and is not good enough at short range.

**Node heading and its uncertainty.** `heading_deg` is the site azimuth of the array's +X axis. `heading_sigma_deg` is how well you know it. Sighting along a straight edge to a surveyed landmark can give 1 to 2 degrees. A phone compass can be 10 degrees off near steel.

**Mount sense.** `face_up: true` for ports up. A face-down array reverses the sense of rotation.

**Bearing uncertainty.** `bearing_sigma_deg` for each node comes from that node's calibration record (the RMS error of HB-5). The software floors it at 0.5 degrees. The floor is a guard against division by nearly zero, not a claim of accuracy.

**Transformation.** site azimuth = heading + local bearing (face-up), or heading - local bearing (face-down). A node with `site.heading_deg` set writes `bearing_site_deg` into its observation log. Otherwise `fuse` applies the site file's heading.

Example site file: `config/site.example.json`.

## What a node logs

Run each node with `--observation-log <file>`. While a candidate is active and a calibrated bearing is available, each analysis step appends one record: node, event id, time of the block centre, time quality, local and site bearing, bearing sigma, estimated fundamental, harmonic score. Bearings from uncalibrated nodes are not logged. Simulated runs are logged with `synthetic: true` and are refused by `fuse` unless `--allow-synthetic` is given.

## Capture timestamps

Each record carries the capture time of the audio, not the time it was processed. In live mode the time comes from the audio driver's sample clock mapped onto the computer's clock (`time_quality: adc_clock`), or from arrival time if the driver gives none (`host_arrival`). Two limits follow:

- The mapping onto wall time is only as good as each computer's clock. Bearing fusion needs node clocks within about 50 ms of each other. Network time synchronization can do that. Check it (HB-7).
- Driver latency reporting on Windows is not verified for this device. HB-7's clap test measures the real offset between two nodes.

## Sound travel time and moving sources

Sound takes range / c to arrive: 0.29 s per 100 m. Each node hears the source where it was that long ago. Two nodes at different ranges hear the same instant of emission at different times.

If you pair bearings by reception time, the two rays point at two different past positions and cross at neither. `fuse_tracks` instead picks an emission time, estimates the ranges, looks up each node's bearing at emission time plus its own travel time, solves, and repeats until the ranges settle. In the test `test_emission_time_alignment_removes_motion_bias`, a synthetic source at 20 m/s is located within 0.3 m with alignment and is more than 1 m off the true path without it.

`fuse` on single observations cannot align. It reports the spread in travel times and, if you give an assumed top speed, the size of the possible error.

What alignment does not fix: wind (it shifts apparent bearings and travel times), temperature layering, and a source that changes speed quickly between hearings.

## Event association

Before fusing, something must decide that node A's candidate and node B's candidate are the same source. `associate_events` groups candidates whose time intervals overlap, allowing for travel time across the site, clock error, and half a second of slack, and whose estimated fundamentals agree within 12 percent (Doppler alone can shift them by several percent). It marks a group **ambiguous** when one node contributes more than one candidate, and it marks every group `verified: false`. It is a plausibility filter. Two different aircraft with similar rotors at the same time will pass it.

## Rejections

`fuse` returns no estimate, and says why, when:

| Code | Condition | Default |
|---|---|---|
| TOO_FEW_NODES | Fewer than two nodes have a usable bearing | |
| UNCALIBRATED, SYNTHETIC | An observation lacks calibration, or is simulated without opt-in | |
| TIME_SPREAD | Reception times differ by more than one emission could explain | baseline / c + 3 clock sigma + 0.3 s |
| BEHIND_NODE | The lines cross behind a node, so the rays do not meet | |
| POOR_CROSSING | The best crossing angle at the solution is too shallow | under 20 degrees |
| OUT_OF_RANGE | The solution is farther than a limit you set | off unless `max_range_m` is set |
| POOR_GEOMETRY | The 95 percent ellipse is too large to be useful | semi-major over 0.5 of the median range |
| INCONSISTENT_BEARINGS | Three or more bearings disagree beyond their stated errors | chi-square above the 95 percent point |

The limits are settings in the site file. They are guardrails, not a validated deployment design.

## Uncertainty

For each node the angular variance is: bearing sigma squared, plus heading sigma squared, plus (position sigma / range) squared. A weighted least-squares solve on the angle residuals gives the position and its covariance. The result reports:

- the 95 percent error ellipse (semi-major, semi-minor, orientation),
- how much of the error comes from bearing noise, heading, and node position,
- the range and sound travel time to each node,
- residuals and, for three or more nodes, a consistency statistic,
- optionally a Monte Carlo check that perturbs bearings, headings, and node positions together. Draws with no valid crossing count as unbounded.

The closed form and the Monte Carlo radius agree within 6 percent in the test geometry.

Read the ellipse with these limits in mind:

- It assumes Gaussian errors of the sizes you entered. If the calibration error or survey error is larger than stated, the ellipse is too small. FT-5 checks this against truth.
- Heading and position errors are fixed for a deployment. Averaging many fixes shrinks only the bearing-noise part.
- It says nothing about a wrong association or a reflected bearing. Those are not small Gaussian errors.
- It is horizontal. A source high above the nodes is projected onto the ground plane.

Worked example from the tests: two nodes 100 m apart, source 71 m from each at a 90 degree crossing, 2 degree bearing sigma, 1 degree heading sigma, 0.5 m position sigma. The 95 percent ellipse is a circle of radius 6.9 m.

## Bearing-only fusion versus raw inter-node time difference

| | Bearing-only fusion (implemented) | Inter-node time-difference localization (not implemented) |
|---|---|---|
| What is shared | One bearing per node per step | Raw audio or precise arrival times |
| Clock need | Tens of milliseconds, to pair observations | A timing error dt becomes a range-difference error of c dt. 1 ms is 0.34 m. Useful work needs about 0.1 ms or better |
| Can these nodes do it | Yes, with ordinary network time | No. Each array's sample clock runs free, and consumer USB audio clocks drift by tens of parts per million, about 1 ms every minute or less |
| What it would take | | A shared or disciplined sample clock per node (GPS pulse-per-second with timestamped capture, or a wired clock), calibrated capture latency, and a correlation processor. That is a separate hardware and firmware design. See `hardware/HARDWARE_BUILD.md`, section 9 |

Do not attempt time-difference localization by correlating recordings from two of these nodes. The result would look precise and be wrong.

## Legacy utility

`legacy/triangulate.py` is the unchanged Revision A two-bearing tool. Its 15 tests still pass and it agrees with `fuse` on the crossing point. It models bearing noise only.
