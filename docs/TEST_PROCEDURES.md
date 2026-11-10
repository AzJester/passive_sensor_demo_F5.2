# Physical test procedures

Revision B, 9 November 2026. None of these has been run. They are the work that turns "software verified" into bench and field evidence.

Three levels of evidence, kept apart in every report:

1. **Software verification.** Automated tests on synthetic and injected input. Done. See `VERIFICATION_REPORT.md`.
2. **Hardware bench testing (HB).** The real array on a bench or in a yard, with a loudspeaker. This section.
3. **Field validation (FT).** Outdoor background and real aircraft. This section.

For every test, write down: date, operator, unit serial, configuration SHA-256 (shown on the dashboard), firmware record name, air temperature, wind estimate, and the names of the recordings made. Commands assume a PowerShell window in the project folder with the virtual environment active (`.\.venv\Scripts\Activate.ps1`).

## Hardware bench tests

### HB-1 Stream identity and integrity

Purpose: show that Windows delivers six channels at 16 kHz without loss. Closes ACQ-01.

1. `python -m pasensor devices`. Note the index, name, and host API of the entry with six inputs.
2. `python -m pasensor record --seconds 60 --out-dir recordings\hb1 --label test=HB-1`
3. Open the `.json` sidecar. Check `channels` is 6, `sample_rate` is 16000, `frames` is 960000 within one block, and `gaps` is empty.
4. `python -m pasensor chaninfo --wav recordings\hb1\<file>.wav`

Pass: all of step 3, and no channel reads below -90 dBFS in a normally quiet room. Fail on only two channels: follow `firmware/PROVISIONING.md` section 8.

### HB-2 Channel identity and polarity

Purpose: tie each physical microphone port to a capture channel. Closes ACQ-07.

1. Number the four ports with stickers. Photograph the array from the port side with the USB socket visible.
2. `python -m pasensor chanmap --labels MIC1,MIC2,MIC3,MIC4 --out records\<unit>_chanmap.json`
3. For each prompt, scratch the case lightly with a fingernail right beside that one port for the whole three seconds. Keep the room quiet.
4. Read the result. Each microphone must map to a different channel with a margin of 3 dB or more.
5. If the suggested order differs from `device.raw_indices`, change the configuration to the suggested order and repeat.
6. Play steady broadband noise from the loudspeaker 2 m away, record 10 s, and run `chaninfo`. All four raw channels must be within 0.3 ms and within 6 dB of each other.
7. Polarity: run `python -m pasensor run --mode live` with that noise playing and read the audio card. A POLARITY SUSPECT flag at the top of the dashboard, or a bearing that is never available with strong broadband sound, means one channel may be inverted. Report it. The software has no polarity correction setting yet.

Pass: steps 4 and 6. Record the photo name and the JSON.

### HB-3 Supply current

Purpose: confirm the array runs from one USB port. Closes ENV-02.

Put an inline USB power meter between W1 and the host. Read voltage and current with the array idle, with the dashboard running, and with the LED ring at its default. Pass: current under 500 mA and voltage at the array above 4.75 V. Record all three readings. This is the only source for the array's power figure.

### HB-4 Timing consistency

Purpose: show the four raw channels share one sample clock with no per-channel delay. Closes ACQ-02.

1. Hang the loudspeaker on the array axis, directly above the centre of the array, 1.5 m up, facing down. Every microphone is then the same distance from it.
2. Play broadband noise at a moderate level. Record 60 s. Wait 10 minutes. Record 60 s again.
3. Put both files in a calibration manifest with any `true_azimuth_deg` and run `python -m pasensor calibrate`. Ignore the pass or fail. Read `pair_tdoa_median_us` and `pair_tdoa_std_us` for each file in the record.

Pass: every pair's median delay is within 62.5 microseconds of zero, every standard deviation is at most 30 microseconds, and the medians of the two files agree within 10 microseconds (no drift). A constant non-zero delay on one microphone means a per-channel offset. Report it. Do not hide it with the angle offset.

### HB-5 Stationary-source bearing calibration

Purpose: find the array's orientation and measure its bearing error. Closes BRG-06 and produces the calibration record that unlocks bearing display.

Setup: open ground, no wall within 5 m, array on its stand at working height, fully assembled as it will be used. Lay out a circle of radius 4 m around the stand with the tape. Mark at least eight points, every 45 degrees, and write down each point's azimuth in the array's intended frame (0 degrees along the direction you want to call +X, counterclockwise seen from above). State how you measured it.

1. At each point, set the loudspeaker on a stand at array height facing the array. Play broadband noise or pink noise. Not a tone.
2. `python -m pasensor record --seconds 20 --out-dir recordings\hb5 --label azimuth=<deg> --label distance_m=4`
3. Repeat at each point. Then repeat three points with the loudspeaker raised to about 30 degrees elevation.
4. Copy `records/calibration_manifest_template.json`, list every file with its true azimuth, and run:
   `python -m pasensor calibrate --manifest <manifest> --out records\<unit>_calibration.json --operator "<name>"`
5. Read the output. It reports the fitted offset, whether the sense is mirrored, median and 95th percentile error, and the share of frames with no bearing.
6. If it passes, copy the printed `calibration` block into `config\node.json`. Restart the sensor. The dashboard now shows bearings without the UNCALIBRATED badge.
7. Walk the loudspeaker to two new points you did not record. Check the live bearing against your measured azimuth.

Pass (proposed targets): median error at most 15 degrees, every position within 45 degrees, bearing available in at least half the frames, pair-delay spread at most 30 microseconds. A result of "mirror: true" is normal if the array frame is left-handed as mounted. A fail with large, inconsistent errors usually means a wrong channel order (go back to HB-2).

Repeat this test after any change to the mount, strap, foam cap, firmware, or channel routing. Do it once bare and once with the foam cap, and keep both records.

### HB-6 Self-noise, vibration, and host noise

Purpose: closes ENV-03 and checks the isolation pad.

1. In a quiet place, run the sensor and note the band level and ambient floor with the host at the far end of the cable.
2. Move the host to 0.5 m from the array. Note the level again. Then load the host (start a video export) so its fan runs. Note the level and whether a candidate appears.
3. Tap the stand firmly with a knuckle ten times. Watch the level trace.

Pass: at the working distance the host raises the band level by less than 1 dB and raises no candidate. Stand taps raise the level by less than 10 dB. If the fan raises a candidate at any distance, write that distance down. It is a real false-alert source.

### HB-7 Clock check

Purpose: closes MN-06 and the clock part of ACQ-05.

1. On each node computer, run `w32tm /stripchart /computer:time.windows.com /samples:5` and record the offsets.
2. Two-node clap test: place both arrays 1 m apart. Start both sensors with `--event-log`. Stand 5 m away on the perpendicular bisector and clap ten times, two seconds apart. Compare frame times of the level spikes in the two logs.

Pass: node clocks within 50 ms of each other. The clap spikes agree within one analysis step (128 ms).

### HB-8 Continuous run

Purpose: closes PRF-03.

Run `python -m pasensor run --mode live --event-log logs\soak.jsonl --log-file logs\soak.log` for 2 hours, then for 24 hours. Every few hours note from the dashboard: audio received, overflows and gaps, samples dropped in queue, restarts, processing load. Note the Python process memory from Task Manager at start and end.

Pass: no restarts, no queue drops, load under 50 percent, memory growth under 50 MB.

### HB-9 Fault behaviour on hardware

Purpose: closes REC-01 on the real device.

| Action | Expected on the dashboard |
|---|---|
| Unplug the USB cable while running | Within about 2 s: ACQUISITION FAULT, then RECONNECTING. No bearing, no candidate |
| Plug it back in | Receiving audio again within the retry interval. Restart counter up by one. Ambient floor kept |
| Clap hard next to the array or shout into it | Audio quality shows CLIPPING for those blocks. No bearing from them |
| Mute the device in Windows sound settings | SILENCE or a stall fault. No bearing |
| Start a heavy CPU load on the host | Load rises. If samples are lost the page shows it. No silent loss |
| Close the sensor window while the page is open | Page shows DASHBOARD LOST CONTACT within 2 s |

## Field tests

Before any field recording: tell the people on site that audio is being recorded, record only what the test needs, and follow `OPERATING.md` on retention.

### FT-1 Outdoor background collection

Purpose: build the negative class for training and the exposure for false-alert rates.

1. Choose sites that match intended use. For each, run sessions of at least 30 minutes at different times of day.
2. `python -m pasensor run --mode live --record-raw recordings\<site>\<date> --event-log logs\<site>_<date>.jsonl --label site=<site> --label wind=<estimate> --label notes="<what was audible>"`
3. Keep a written log with clock times of notable sounds: traffic, aircraft, helicopters, mowers, generators, speech, birds, wind gusts.
4. One session is one `session_id` for all later use. Never split a session across training and test.

Aim for at least ten sessions over at least three sites and three days before training anything.

### FT-2 Wind

Purpose: closes ENV-04.

On a breezy day, record 10 minutes with the foam cap and 10 minutes without, noting wind speed from a handheld meter or a nearby weather station each minute. Report the ambient floor against wind speed for both. State the wind speed at which the floor has risen 10 dB over calm.

### FT-3 False alerts on background

Purpose: closes DET-06 and BRG-07.

1. Set the thresholds and write them down before listening to the evaluation sessions.
2. Build an evaluation manifest (`records/evaluation_manifest_template.json`) from background sessions that were **not** used to choose thresholds or train a model. Leave `truth` empty.
3. `python -m pasensor evaluate --manifest <manifest> --out reports\ft3`

Report: false alert events per hour with exposure hours and the 95 percent interval, per site. Listen to every false alert and note its cause. Sample size: with zero alerts in T hours, the upper 95 percent bound is 3.7 divided by T per hour. Showing "under 1 per hour" needs at least 3.7 alert-free hours.

### FT-4 Controlled drone trials

Purpose: closes DET-07, DET-08, and gives the first real bearing errors against a moving source.

Prerequisites: a site where flying is permitted, a qualified pilot who follows the local aviation rules, the owner's consent to record the aircraft, a flight log with time and position (most aircraft record GPS), and the sensor clock checked against the log's clock (HB-7).

1. Complete HB-5 on site on the day. Record a 10 minute background session first.
2. Stationary hovers: at each of several measured ground distances, hover at a fixed height on a known azimuth for 60 s, then land or leave for 60 s. Start near and step outward. Stop stepping outward when two hovers in a row raise no candidate.
3. Passes: fly straight lines at constant height and speed past the array at several offsets.
4. Record the whole session with `--record-raw`, `--event-log`, and `--observation-log`.
5. From the flight log, write truth intervals (start, end, bearing track from the array to the aircraft) into an evaluation manifest. Count each hover or pass as one event.
6. `python -m pasensor evaluate --manifest <manifest> --out reports\ft4`

Report, by distance and by aircraft: event recall with interval, detection latency, bearing error median and 95th percentile, bearing unavailable fraction, lost-sample events, and processing load. Report misses as carefully as detections. A loudspeaker playing drone recordings is useful for repeatability and is not a substitute for a real aircraft.

Do not turn the largest distance with a detection into a "detection range". State recall at each distance tested, with the conditions.

### FT-5 Two-node trial

Purpose: closes MN-08.

1. Survey both node positions and headings. Write a site file from `config/site.example.json` with honest uncertainties: how well do you know each position and each heading?
2. Complete HB-5 on both nodes and HB-7 between them.
3. Fly the passes of FT-4 so the aircraft crosses the area where the two bearings meet at more than 20 degrees.
4. `python -m pasensor fuse --site <site.json> --observations <a.jsonl> <b.jsonl> --step 1`
5. Compare each estimate with the flight log position at the emission time.

Report: share of time with an estimate, share rejected and why, horizontal error against truth, and whether the truth fell inside the reported 95 percent ellipse about 95 percent of the time. If it did not, the stated uncertainties are too small. With two nodes, one reflected bearing gives a confident wrong position. Add a third node before drawing conclusions about accuracy.

## Record templates

In `records/`: unit record, firmware record, calibration manifest, evaluation manifest, dataset manifest, field session log.
