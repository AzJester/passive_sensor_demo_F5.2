# Hardware build package

Revision B, 9 November 2026.

**Status.** This is a build package for a first unit. No unit has been built from it. Dimensions of the purchased case, the strap fit, and the adapter plate hole spacing are not verified. Measure the parts when they arrive and correct the drawing before you cut.

The first build keeps the existing USB array. There is no documented reason to change it: it gives four microphones on one clock, it is in production, and the software already matches its channel layout. The reasons to move to custom electronics later are in section 9.

## 1. Drawings and CAD

Engineering drawings are in `drawings/`, issued under the S&L Milestone Solutions title block. Each sheet is ANSI B (11 x 17 in) with border zones, a revision block, third-angle projection, and metric dimensions. Every sheet exists as vector PDF and as SVG. `SLM-PAS-DRAWING-SET.pdf` holds all four.

| Drawing | Title | Content |
|---|---|---|
| SLM-PAS-M01 | Carrier plate | Detail drawing, scale 1:1. Front and right side views, ordinate dimensions from the lower left corner, hole and slot callouts, material and finish notes |
| SLM-PAS-M02 | Foam pad and windscreen cap | Detail drawing, scale 1:2. Pad views, cap top view, section A-A through the pocket |
| SLM-PAS-A01 | Sensor head assembly | Assembly drawing, scale 1:2. Top view, front view with the cap in section, detail C of the strap at a slot, item balloons, parts list |
| SLM-PAS-E01 | Interconnection diagram | Units A1 and A2, cable W1, wire list with connector pins, reference designations |

CAD files are in `cad/`:

| File | Content |
|---|---|
| `cad/carrier.scad` | Editable 3D model of the carrier plate (OpenSCAD) |
| `cad/carrier.dxf` | 2D cutting profile, millimetres, layer CUT. Layer REFERENCE_DO_NOT_CUT shows the case outline and adapter zone |

All of these come from `make_cad.py`, which draws through `drafting.py`. Change a dimension in `make_cad.py`, run `python hardware/make_cad.py`, and the model, the profile, and every sheet stay consistent.

Status of the drawings: **revision A, PRELIMINARY**. The DRAWN, CHECKED, and APPROVED name fields are blank for you to sign. Nothing has been cut or built from them.

How they were checked: an automated layout check (no overlapping text, nothing outside the sheet), the SVG sheets rendered in Microsoft Edge, and the PDF set rendered with the Windows PDF engine. The OpenSCAD file was **not** compiled, because OpenSCAD is not installed on the computer that produced this package. Open it in OpenSCAD once before relying on it. PDF text uses the standard Helvetica font. The SVG sheets ask for Aptos and fall back to Segoe UI or Arial.

A 3D mock-up of the same assembly is in `../demo/index.html` under the tab "Hardware in 3D". It uses the drawing dimensions and item numbers, shows the stack exploded, and shows the node deployed beside a host computer. It is a picture of the design, not of a built unit.

Drawing the assembly to scale exposed two faults in the earlier sketch, now corrected. The strap could not close under the plate, because the adapter plate and stand sit there. And the cable-tie holes sat under the strap. See 4.1 and step 9.

## 2. Electrical interconnection

One cable. See drawing SLM-PAS-E01, which also carries the wire list.

| Ref | From | To | Cable | Signals |
|---|---|---|---|---|
| W1 | A1 array, USB-C socket marked XMOS, next to the 3.5 mm jack | Host USB-A port | StarTech USB2AC2M, USB 2.0, 2 m, shielded | VBUS 5 V, D+, D-, GND, shield bonded to both shells. The cable carries the 56 kilohm CC pull-up that tells the array a legacy host is attached |
| P1 | Host mains adapter or battery | Host | As supplied with the host | Host power only |

Rules:

- Plug W1 straight into the computer. Do not use an unpowered hub.
- Keep the total USB length at 3 m or less unless you add an active extension and repeat bench test HB-1.
- Leave the speaker connector, the 3.5 mm jack, the I2S header, and any 5 V input unconnected. This build needs no soldering and no mains wiring.
- If the array has a second USB-C socket for a XIAO module, do not use it. Use the XMOS socket.

## 3. Power

| Load | Supply | Known figure | Source | Budget used |
|---|---|---|---|---|
| A1 array | 5 V from the host USB port | Not published by the manufacturer | Vendor pages and FAQ, checked 9 November 2026 | Must stay under 500 mA, the limit of a USB 2.0 port. Measure it (HB-3) |
| Host laptop | Its own adapter or battery | Per the host maker | | Not part of this design |
| Later: Raspberry Pi 5 host | 5 V 5 A USB-C supply | About 4 W typical bare board. About 2.7 W idle and 7 W under load in third-party tests | Raspberry Pi documentation, pidramble.com | 10 W average for host plus array until measured |

To cut array current, the firmware can switch off the LED ring supply (pin X0D33) and the speaker amplifier (X0D31, low is on) through the vendor host tool. Record the settings in the unit record if you change them.

Battery sizing example for a later standalone node: 10 W for 24 h is 240 Wh. Treat that as a planning figure, not a measurement.

## 4. Mechanical design

### 4.1 Carrier plate M01

A 160 x 160 mm plate of 3 mm clear polycarbonate.

| Feature | Dimension | Purpose |
|---|---|---|
| Four corner holes | Diameter 4.5, centres 15 from each edge | Spare mounting, guy lines, elastic loops that hold the foam cap |
| Two strap slots | 4 x 26, full-radius ends, centres 60 left and right of the plate centre, long axis along Y | One hook-and-loop strap over the case, running along the X axis. Each end wraps the 18 mm strip between slot and plate edge |
| Two cable-tie holes | Diameter 4.5 at (150, 110) and (150, 126) from the lower left corner | Strain relief for W1. Placed clear of the strap, which covers Y 70 to 90 at that edge |
| Adapter zone | 90 x 50 rectangle about the centre | Match-drill two diameter 6.6 holes to the adapter plate threads at assembly |
| Tolerance | Plus or minus 0.5 | |

Why the strap runs along X: the four microphones sit on the diagonals, 46.7 mm from the centre. A 20 mm strap along an axis passes between the microphone pairs and stays about 23 mm clear of every port.

### 4.2 Stack

From the ground up (drawing SLM-PAS-A01): light stand, adapter plate M1 screwed onto the stand stud, carrier plate bolted down onto M1 with two 1/4 in-20 screws from above, foam pad M6, array in its case with microphone ports up, one strap over the case, foam cap M5 over everything.

The strap does not go under the middle of the plate. Each end passes down through its slot, outward under the plate, up around the plate edge, and closes on itself on top (detail C of the assembly drawing). The two screw heads sit under the foam pad. A 1/2 in screw through the 3 mm plate reaches 9.7 mm into the 10 mm adapter plate, so it does not stand proud underneath.

### 4.3 Microphone port placement

- **Ports face the sky.** The ports are on the logo and LED side of the board. The connectors are on the other side.
- **Nothing solid above the port plane** within the foam cap. A hard surface near a port bends the sound field and changes the inter-microphone delays.
- **Height.** At least 1.5 m above the ground on the stand. Low mounting strengthens the ground reflection.
- **Clear space.** At least 3 m from walls, vehicles, and fences where the site allows. A reflection gives a real, repeatable, wrong bearing.
- **Level.** Within 2 degrees. Tilt maps directly into azimuth error for elevated sources.
- **Orientation.** Mark +X and +Y on the plate only after the axis test (HB-5). Do not trust the USB socket position as an axis reference until then.

### 4.4 Vibration isolation

The case rests on a 10 to 13 mm pad of open-cell foam and touches nothing rigid. The strap is soft and adds little stiffness. This decouples the array from stand vibration and cable knocks. It is a simple pad, not a tuned isolator, and its effect is not measured. Test HB-6 (tap the stand, watch the level) tells you whether it is enough. If it is not, hang the plate from the corner holes on elastic cord.

Route W1 in a loose loop from the case edge to the tie holes so cable movement does not tug the case.

### 4.5 Wind protection

A cap cut from 25 mm, 30 PPI reticulated foam covers the case top and sides (drawing SLM-PAS-M02). Cut a block 140 x 140 mm and hollow a pocket about 104 mm across and 12 mm deep so it slips over the case. Hold it with two elastic loops run diagonally between opposite corner holes.

- Record a bare reference set before fitting the cap, then repeat the calibration with the cap on. The cap is part of the acoustic path. Calibration is valid only for the configuration it was measured in.
- Foam gives modest protection. Expect wind noise to dominate above a light breeze. FT-2 measures where. A fur cover over the foam is the usual next step and is not specified here.
- The assembly is not weatherproof. Bring it in when rain is possible.

## 5. Tools

Fine-tooth saw or scoring knife for polycarbonate (or a laser or waterjet service using `carrier.dxf`), drill with 4.5 mm and 6.6 mm bits, 4 mm slot file or router for the strap slots, deburring tool, steel rule and square, calipers, Phillips screwdriver, sharp long blade for foam, spirit level, permanent marker.

## 6. Assembly, with inspection points

Stop at any failed inspection point and resolve it before going on.

| Step | Action | Inspect |
|---|---|---|
| 1 | Unpack A1. Read the label | SKU is 114993701. Case is undamaged. Four microphone ports are visible and clear on the logo side |
| 2 | Measure the case with calipers: diameter or width, height, and where the USB socket sits | Write the numbers in the unit record. If the case is wider than 110 mm, move the strap slots outward in `make_cad.py` and regenerate |
| 3 | Do firmware provisioning and bench test HB-1 **before** any mechanical work | Six channels at 16 kHz record cleanly. See `firmware/PROVISIONING.md` |
| 4 | Do the channel test HB-2 on the bare unit | Each microphone maps to a distinct raw channel with at least 3 dB margin |
| 5 | Cut the plate to drawing SLM-PAS-M01: 160 x 160, four corner holes, two tie holes, two slots | Dimensions within 0.5 mm, hole diameters within 0.2 mm. No cracks from drilling. All edges deburred |
| 6 | Place adapter plate M1 centred under the carrier. Mark two of its 1/4 in-20 holes through. Drill 6.6 mm | Both screws start by hand and pull the carrier flat. Screw tips do not stand proud below M1 where the stand seats |
| 7 | Screw M1 onto the stand stud. Bolt the carrier to M1 | No rock or play. Plate level within 2 degrees with the stand upright |
| 8 | Cut the foam pad to 110 x 110 (SLM-PAS-M02) and lay it centred | Pad covers the whole case footprint. If the case rocks on the screw heads, relieve the pad over them |
| 9 | Set the case on the pad, ports up. Lay the joined ties over the case along X. Pass each end down through its slot, outward under the plate, up around the plate edge, and close it on itself on top (SLM-PAS-A01, detail C) | Strap is snug, not crushing the pad flat. Strap edges are at least 15 mm from every port. Case does not touch the plate. No strap under the adapter plate |
| 10 | Plug in W1. Form a loose loop. Tie the cable at the two tie holes | Pulling the cable below the ties does not move the case |
| 11 | Weight the stand base | Stand does not tip when pushed at plate height |
| 12 | Record the bare reference set, then fit the foam cap and elastic loops | Cap sits evenly. No foam is forced into a port |
| 13 | Place the computer at the end of the cable, on the ground, fan side away | HB-6 passes |
| 14 | Photograph the assembly from above and from the side with the axis marks visible | Photos stored with the unit record |

## 7. Identifying each microphone and its channel

Full procedure: HB-2 in `docs/TEST_PROCEDURES.md`. In short:

1. Put four numbered stickers on the case next to the four ports. Use the board silkscreen names MIC1 to MIC4 if you can see them. Otherwise number them yourself and photograph the layout.
2. Run `python -m pasensor chanmap`. For each microphone in turn it asks you to scratch gently beside that port only, listens for three seconds, and reports which capture channel was loudest.
3. The tool compares the result with `device.raw_indices` in the configuration. Order matters: entry k of `raw_indices` must be the channel of the microphone at entry k of `array.geometry_m`.
4. Record a few seconds of steady broadband sound and run `python -m pasensor chaninfo --wav <file>`. The four raw channels should sit within about 0.3 ms of each other at similar levels. A channel that is several milliseconds late or much louder is a processed output, not a raw microphone.
5. Save both outputs in the unit record.

The tap test finds which channel belongs to which port. It cannot tell you where that port is in the array frame. The stationary-source test HB-5 does that, and its calibration record is what unlocks bearing display.

## 8. Assessment: a dedicated embedded host for a later standalone version

Not built. Prices seen 9 November 2026.

| Candidate | Price | Power | Cooling | Storage and ports | Notes |
|---|---|---|---|---|---|
| Raspberry Pi 5, 8 GB (SC1112) | 175.00 at PiShop.us, in stock. Official 27 W supply 12.95 (search excerpt) | About 2.7 W idle, about 7 W under load (third-party). Official typical 800 mA bare board | Idle about 65 C with no cooler. Throttles between 80 and 85 C under sustained heavy load | microSD, NVMe by HAT. Two USB 3 and two USB 2 ports | 8 GB is more memory than this needs. Memory prices have pushed Pi prices up several times since October 2025 |
| Raspberry Pi 4 B, 4 GB (SC0194) | 100.00 at PiShop.us, in stock | About 2.7 W idle, 6.4 W at four-core load (third-party) | Same throttle rule | microSD. Two USB 3 and two USB 2 | Lowest cost and power |
| MeLE Quieter 4C, N150, 16 GB and 512 GB | 529.99 on sale at the MeLE store. Lower trims from 329.99 | About 7 W idle, 12.5 W average loaded (review of the N100 unit) | Fanless by design | NVMe or SATA M.2, microSD. Three USB-A, one USB-C. 12 V input | Runs the same Windows stack as the prototype |
| OnLogic CL260 | From 940.00 | Not stated | Fanless, rated 0 to 50 C | M.2. Four USB-A, two USB-C. 12 to 24 V input | Only if you need an industrial rating or wide input voltage |

Points that decide it:

- **Compute.** One analysis block took a median of 9.9 ms on the development PC (Intel Core Ultra 7 155H), against a new block every 128 ms. A Raspberry Pi is several times slower per core. The load should still fit, and that must be measured on the board (requirement PRF-01), not assumed.
- **Cooling noise.** The host must be fanless or sit well away from the array. A fan is a steady harmonic source and is exactly what the candidate heuristic responds to.
- **Operating system.** Linux handles multichannel USB audio class 2 devices natively and may be the lower-risk host for six-channel capture. The software is plain Python with NumPy and PortAudio and should run there. It has been tested on Windows only.
- **Storage.** Raw six-channel recording uses about 690 MB per hour, 16.6 GB per day. Use an SSD or a high-endurance card and keep recording opt-in.
- **Enclosure.** Put the host in its own vented box at the foot of the stand, not under the array. A sealed box in direct sun is the main thermal risk.
- **Service.** Removable storage, a labelled power switch, network access for logs, and a way to read the dashboard from a phone or laptop. The dashboard is loopback-only today. Remote viewing needs an authenticated design first.

Suggested order: Raspberry Pi 4 B or Pi 5 with a passive heat-sink case for the first standalone trial. Choose the MeLE unit if keeping Windows matters more than cost and power.

## 9. Assessment: custom electronics

Not designed. Nothing in this package is fabrication-ready custom electronics.

| | Stock USB array (this build) | Custom array (future) |
|---|---|---|
| Aperture | 66 mm square. Bearing resolution is coarse at low frequency, where rotor fundamentals sit | Chosen for the task, for example 150 to 300 mm, for sharper bearings and better rejection of ambient noise |
| Microphones | 4 | 8 or more, allowing elevation and better handling of two sources |
| Sample rate and raw access | 16 kHz, raw channels through vendor firmware | 48 kHz raw, full control |
| Weather | Indoor consumer case | Ports with membranes, drainage, sealed electronics |
| Clock | Free-running USB clock | Can take a GPS-disciplined clock, which is what inter-node time-difference localization needs |
| Effort | Buy and configure | Schematic, PCB layout with acoustic ports, PDM or TDM clocking, USB audio firmware, enclosure, EMC, board bring-up, production test. A separate project measured in months |

Do the custom design only after the stock array has produced field data that shows what limits performance. Treat schematic, layout, fabrication outputs, embedded firmware, and board verification as their own scope with their own reviews.

## 10. Records to keep for each physical unit

Templates are in `records/`.

- `unit_record_template.md`: serial, purchase, measured case dimensions, firmware, channel map, photos, power measurement.
- `firmware_record_template.json`: image name, version readback, SHA-256, routing readback, geometry readback.
- Calibration record: written by `python -m pasensor calibrate`. Never edit it by hand.
- Configuration file `config/node.json` for the unit, with its SHA-256 as shown on the dashboard.
