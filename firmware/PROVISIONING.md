# Firmware provisioning: reSpeaker XVF3800 USB 4-Mic Array

Revision B, 9 November 2026.

**Status.** Every fact below was read from the manufacturer's repository, wiki, or XMOS documentation on 9 November 2026. None of these steps has been run on a physical unit for this package. Where the vendor material is silent, this document says so.

**OEM firmware only.** The array runs the manufacturer's firmware. This project contains no custom embedded firmware and no firmware binaries. The repository states no licence and the maintainer calls the firmware source proprietary, so the images are not redistributed here. Get them from the source below.

## 1. Sources

| What | Where |
|---|---|
| Firmware images and guides | https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY (folder `xmos_firmwares/`) |
| DFU guide | `xmos_firmwares/dfu_guide.md` in that repository |
| Windows control tool | `host_control/win32/xvf_host.exe` with `command_map.dll` and `device_usb.dll`. Copy the folder. No installer |
| Python control tool | `python_control/xvf_host.py`. Needs `pip install pyusb` and libusb |
| Board guide | https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/ |
| Command reference | XMOS XVF3800 user guide, control command appendix (document XM-014888-PC) |
| Flashing tool | dfu-util 0.11, https://dfu-util.sourceforge.net/ |

Download the repository as a ZIP or clone it. The wiki warns against saving a single firmware file with the browser's "Save as", which can save a web page instead of the binary.

## 2. Required image

| Item | Value |
|---|---|
| Hardware | reSpeaker XVF3800 USB 4-Mic Array, USB ID `2886:001a` |
| Image | `xmos_firmwares/usb/respeaker_xvf3800_usb_dfu_firmware_v2.1.1_16k6ch.bin` |
| Why | It is the 16 kHz, six-channel USB build. Channels 1 and 2 carry processed outputs. Channels 3 to 6 carry the four microphones |
| Do not use | The plain `v2.1.1.bin` or `_48k2ch.bin` builds (two channels). Any I2S image. Any image for a different board |

Notes:

- The repository showed v2.1.1 as current on 9 November 2026, committed 29 September 2026. The vendor wiki still names v2.1.0. The older `v2.1.0_16k6ch` image is in `old_firmwares/`.
- The `AUDIO_MGR_OP_CH3` to `CH6` routing commands first appear in v2.1.0. Do not use an older image.
- A 48 kHz six-channel build exists only as a beta attached to an issue, with reported level and click problems. Do not use it.
- Boards sold with a XIAO ESP32S3 ship with I2S firmware. They will not appear as a USB microphone until flashed with a USB image.

## 3. Record before you change anything

Fill in `records/firmware_record_template.json` as you go.

```powershell
# identify the unit and its running firmware
.\xvf_host.exe VERSION
.\xvf_host.exe BLD_MSG
dfu-util -l

# hash the image you are about to flash, and note the repository commit
Get-FileHash .\respeaker_xvf3800_usb_dfu_firmware_v2.1.1_16k6ch.bin -Algorithm SHA256
git -C <path to the cloned repository> rev-parse HEAD
```

The manufacturer publishes no reference hashes that this audit could find. Your own recorded hash is what ties a unit to an image.

## 4. Flash

1. Close every program that uses the microphone.
2. Run `dfu-util -l`. You should see the device with two alternates: `alt=1` named "reSpeaker DFU Upgrade" and `alt=0` named "reSpeaker DFU Factory".
3. If dfu-util reports `LIBUSB_ERROR_NOT_SUPPORTED`, Windows has no usable driver on the DFU interface. The vendor guide uses Zadig to assign WinUSB. **Assign it to the DFU or control interface only.** Do not replace the driver of the audio interface or of the whole composite device, or the microphone will vanish from Windows. Unplug and replug afterwards.
4. Flash the upgrade partition:

```powershell
dfu-util -R -e -a 1 -D .\respeaker_xvf3800_usb_dfu_firmware_v2.1.1_16k6ch.bin
```

5. Wait for the device to re-enumerate. If no sound device appears, uninstall the device in Device Manager and replug it (vendor guide, section 4).

Safe mode is not needed when the running firmware already shows the DFU Upgrade alternate. To force it: unplug, hold the Mute button, plug in. The red LED blinks. The board then runs the factory-partition firmware, which supports DFU.

## 5. Read back and verify

```powershell
.\xvf_host.exe VERSION                       # expect 2.1.1
python xvf_host.py AEC_MIC_ARRAY_GEO         # 12 numbers: X Y Z in metres for microphones 0 to 3
python xvf_host.py AUDIO_MGR_OP_L
python xvf_host.py AUDIO_MGR_OP_R
foreach ($n in 3..6) { python xvf_host.py "AUDIO_MGR_OP_CH$n" }
```

| Readback | Expected | If different |
|---|---|---|
| Geometry | `0.033 -0.033 0`, `0.033 0.033 0`, `-0.033 0.033 0`, `-0.033 -0.033 0` (documented output) | Copy the values you read into `array.geometry_m`, in the same order, and note it in the unit record |
| `AUDIO_MGR_OP_CH3` to `CH6` | `(1, 0)`, `(1, 1)`, `(1, 2)`, `(1, 3)`: category 1 is raw microphones, source 0 to 3 | Set them, section 6 |
| `AUDIO_MGR_OP_L`, `_R` | `(8, 0)` and `(7, 3)` per the maintainer's table for v2.1.0. Not documented for v2.1.1 | Record what you read. The sensor ignores these two channels |

Category 1 is the microphone signal before gain, with no added system delay and no enhancement. That is what delay estimation needs. Expect these channels to be quieter than the processed outputs. Categories 3 and 11 add gain or delay. Do not use them without repeating the timing test.

Then verify the stream from the host side:

```powershell
python -m pasensor devices                    # the array must show 6 input channels
python -m pasensor record --seconds 10 --out-dir recordings\bringup
python -m pasensor chaninfo --wav recordings\bringup\<file>.wav
```

Pass: `devices` lists an endpoint with six inputs. The recording has six channels at 16000 Hz and no discontinuities. `chaninfo` shows the four raw channels within about 0.3 ms of each other. Whether Windows exposes all six channels is not stated anywhere in the vendor material. This is the first real risk to retire. If Windows offers only two channels, see section 8.

## 6. Change routing only if the readback is wrong

```powershell
foreach ($mic in 0..3) {
    $cmd = "AUDIO_MGR_OP_CH" + ($mic + 3)
    python xvf_host.py $cmd --values 1 $mic
    python xvf_host.py $cmd
}
```

## 7. Persistence

If the readback already matches after a power cycle, **do not save anything**. The default routing of the six-channel image is what the sensor needs.

Save only if you had to change a setting and it must survive power loss:

```powershell
.\xvf_host.exe save_configuration 1
```

Then power-cycle and repeat section 5.

- `SAVE_CONFIGURATION` stopped the board enumerating on firmware 2.0.8 and older (vendor issue 8). The maintainer reports it fixed from 2.0.9. Confirm `VERSION` shows 2.1.1 first.
- It saves every tunable setting, not only routing. Record the full before and after state.
- To return to defaults: `.\xvf_host.exe clear_configuration 1`, then reboot the board.

## 8. Recovery

| Symptom | Action |
|---|---|
| Sound device missing after a flash | Device Manager: uninstall the device, replug |
| Only two channels offered by Windows | Confirm the `_16k6ch` image with `VERSION` and `BLD_MSG`. Check Sound settings, the device's Advanced tab, default format. Run `python -m pasensor devices` and try the entry under another host API with `--device <index>`. If it still shows two, test the same board on a Linux host to separate a board problem from a Windows one |
| Audio broken after a driver change with Zadig | Remove the WinUSB assignment: uninstall the device with "delete the driver software", replug. The vendor wiki reports this needs every reSpeaker driver entry removed |
| Board does not enumerate at all | Enter safe mode (hold Mute while plugging in). Flash the required image again to `alt 1` |
| Still dead | Vendor issue 8: in safe mode flash `xmos_firmwares/recover/4mb_all_ff.bin` to `alt 1`. It stops near 96 percent with an out-of-range error, which the vendor says is expected. Then flash the required image. Some "V1.1" boards are reported not to recover this way (vendor issue 32, still open). There is no downloadable factory image |

## 9. Firmware acceptance record

A unit is provisioned when its record shows all of these:

- [ ] Hardware SKU and serial
- [ ] Repository commit and image file name
- [ ] SHA-256 of the image, computed by you
- [ ] `VERSION` and `BLD_MSG` before and after
- [ ] Geometry readback
- [ ] Routing readback for L, R, CH3 to CH6
- [ ] Whether `save_configuration` was used, and why
- [ ] Windows device name, host API, and index that gave six channels
- [ ] Bring-up recording file name and its SHA-256
- [ ] `chaninfo` output
- [ ] Readback repeated after a power cycle, with date and operator

Put the record's file name in `device.firmware_record` in the node configuration. It is then copied into every recording's sidecar.

## 10. The firmware's own direction output

The firmware computes its own direction of arrival for speech (`DOA_VALUE`, `AEC_AZIMUTH_VALUES`). The vendor image puts 0 degrees at the USB-C edge. That is a different reference from this software's local azimuth, which is measured from the array +X axis. Do not compare the two numbers until the axis test has related them. The sensor does not use the firmware's value.
