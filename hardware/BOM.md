# Bill of materials

Revision B. Prices are in US dollars, before tax and shipping, as seen on **9 November 2026**. "Page" means the price was read on the live product page that day. "Search excerpt" means it came from a same-day search result for that site and is less certain. Check every price and stock level again before you order. The same list is in `bom.csv`.

Nothing on this list has been bought or fitted for this revision. Fit notes say what to check when the parts arrive.

## Sensor head and mount (one node)

| Ref | Item | Manufacturer | Part number | Qty | Unit price | Source and how the price was seen | Purchase link | Fit note |
|---|---|---|---|---|---|---|---|---|
| A1 | USB four-microphone array with case | Seeed Studio | SKU 114993701 (reSpeaker XMOS XVF3800 with Case) | 1 | 59.90 | Seeed, page. China warehouse in stock, US warehouse on back order | https://www.seeedstudio.com/ReSpeaker-XVF3800-USB-4-Mic-Array-With-Case-p-6490.html | Buy this exact SKU. Mouser lists it as 713-114993701 at 59.90, on order |
| W1 | USB 2.0 cable, USB-C to USB-A, 2 m | StarTech | USB2AC2M | 1 | 20.49 | StarTech, page, in stock | https://www.startech.com/en-us/cables/usb2ac2m | For a USB-C only host use USB2CC1M (16.39) |
| P1 | Clear polycarbonate sheet, 12 x 12 x 1/8 in | McMaster-Carr | 8574K26 | 1 | 10.42 | McMaster-Carr, page | https://www.mcmaster.com/8574K26/ | 1/8 in is 3.2 mm. Drawing SLM-PAS-M01 says 3 mm nominal and accepts 3.2 |
| M1 | Tripod adapter plate, 90 x 50 x 10 mm, 1/4 in-20 threads | SmallRig | 4887 | 1 | 12.99 | B&H, page | https://www.bhphotovideo.com/c/product/1852171-REG/smallrig_4887_multifunctional_cheese_mount_plate.html | Hole spacing not verified. Match-drill the carrier to it |
| M2 | Screw, 1/4 in-20 x 1/2 in, 18-8 stainless, pack of 50 | McMaster-Carr | 91772A537 | 1 pack (2 used) | 14.80 | McMaster-Carr, page | https://www.mcmaster.com/91772A537/ | Check thread engagement in the adapter plate |
| M3 | Light stand, 1/4 in-20 stud, 34 to 96 in | Impact | LS-8AI | 1 | 32.95 | B&H, page | https://www.bhphotovideo.com/c/product/1258466-REG/impact_ls_8ai_air_cushioned_light_stand_black.html | The stand weighs about 1 kg. Weight its base outdoors |
| M4 | Hook-and-loop ties, 6 in, pack of 50 | StarTech | B506I-HOOK-LOOP-TIES | 1 pack | 19.46 | StarTech, page | https://www.startech.com/en-us/cables/b506i-hook-loop-ties | One tie is too short to go round the case and plate. Join three end to end |
| M5 | Reticulated polyurethane foam, 30 PPI, 1 in x 36 x 48 in (windscreen cap) | The Foam Factory | no SKU shown, "filter foam" | 1 | 24.99 | The Foam Factory, page | https://www.thefoamfactory.com/opencellfoam/filter.html | Cut a cap about 140 x 140 mm with a pocket for the case |
| M6 | Reticulated polyurethane foam, 30 PPI, 1/2 in x 36 x 48 in (isolation pad) | The Foam Factory | no SKU shown, "filter foam" | 1 | 12.99 | The Foam Factory, page | https://www.thefoamfactory.com/opencellfoam/filter.html | Cut a pad about 110 x 110 mm |
| | **Subtotal, required** | | | | **208.99** | | | |

## Optional parts

| Ref | Item | Manufacturer | Part number | Qty | Unit price | Source | Purchase link | Why |
|---|---|---|---|---|---|---|---|---|
| F1 | Snap-on ferrite, 6.6 mm bore, material 31 | Fair-Rite | 0431164281 | 1 | 2.83 | Digi-Key, page | https://www.digikey.com/en/products/detail/fair-rite-products-corp/0431164281/8594000 | Fit only if the bench test shows interference on the audio. Loose on a thin cable, so tie it in place |
| M7 | Screw M4 x 20, pan head, 18-8, pack of 100 | McMaster-Carr | 92000A228 | 1 pack | 14.76 | McMaster-Carr, page | https://www.mcmaster.com/92000A228/ | Corner holes: guy lines or windscreen retention |
| M8 | Locknut M4, nylon insert, 18-8, pack of 100 | McMaster-Carr | 93625A150 | 1 pack | 7.03 | McMaster-Carr, page | https://www.mcmaster.com/93625A150/ | With M7 |
| M9 | Washer M4, 18-8, pack of 100 | McMaster-Carr | 93475A230 | 1 pack | 3.93 | McMaster-Carr, page | https://www.mcmaster.com/93475A230/ | With M7 |
| M10 | Adhesive cable-tie mount, 1 x 1 in | Panduit | ABM2S-A-C | 2 | 1.07 | Gordon Electric Supply, search excerpt | https://www.gordonelectricsupply.com/p/Panduit-Abm2S-A-C-Cable-Tie-Mount-Adh-1Inxi1In-Wh-Pk100/6750617 | Extra strain relief on the stand |
| | Sandbag or other weight for the stand base | any | | 1 | not priced | | | Required outdoors. Not sourced here |
| | Small cable ties | any | | 4 | not priced | | | Strain relief at the plate tie holes |

Soft M4 rubber mounts were looked for and not found. The soft silicone grommet that suits a load this small (McMaster-Carr 1074N1, 17.29 each) takes an M3 screw and a 3.6 mm panel. The design uses a foam pad instead.

## Test equipment

| Item | Manufacturer | Model | Qty | Price | Source | Purchase link | Use |
|---|---|---|---|---|---|---|---|
| Battery loudspeaker | JBL | Go 5 (JBLGO5BLUAM) | 1 | 54.95 | B&H, page (the Go 4 page now leads to the Go 5) | https://www.bhphotovideo.com/c/product/1808908-REG/jbl_jblgo4bluam_go_4_portable_bluetooth.html | Broadband test source for HB-4 and HB-5 |
| Tape measure, 100 ft, open reel | Komelon | 6611 | 1 | 25.67 | Hanes Supply, search excerpt | https://www.hanessupply.com/hand-tools/testing-and-measuring/komelon-6611 | Source and node positions |
| Thermometer and hygrometer | ThermoPro | TP50 | 1 | 15.99 | TempPro store, search excerpt | https://temppro.com/products/tp50-digital-indoor-hygrometer-thermometer | Air temperature for the configuration |
| Sound level meter (optional) | Galaxy Audio | CM-130 | 1 | 74.99 | B&H, page (regular 89.99) | https://www.bhphotovideo.com/c/product/333306-REG/Galaxy_Audio_CM_130_CM_130_CHECK_MATE.html | Record source and ambient levels with each test |
| Inline USB power meter (optional) | your choice | not specified | 1 | not priced | | | Measure array supply current, HB-3. No part was verified |
| Marked protractor board or printed compass rose, stakes, string | | | | not priced | | | Lay out source azimuths around the array |
| | **Subtotal, test equipment listed with prices** | | | **171.60** | | | |

## Host computer

Use an existing Windows 11 PC with a free USB port for the first build. A later standalone host is assessed in `HARDWARE_BUILD.md`, section 8.

## What not to buy

- **SKU 114993700 and 114993702** (the versions with a XIAO ESP32S3). They ship with I2S firmware and do not appear as a USB microphone until reflashed.
- A second, different USB microphone to "add channels". Separate USB audio devices have separate clocks and cannot be combined for delay estimation.
