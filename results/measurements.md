# Real measurements: what has been supplied

**No measurements from a real pump have been supplied. Every number in results/ comes from a simulated plant and simulated Witness Nodes. The firmware has booted on a real board (4 bench logs, no pump); that is the only real-hardware evidence so far.**

Sessions live in `measurements/`. Each has a purpose fixed before it was recorded: **bench** (no pump; proves things about the board only), **development** (may be studied and trained on), **evaluation** (never used to tune; only scored against what development produced). `python3 training/measure.py report` rewrites this file.

| session | purpose | date | board | pump | counts as pump evidence | problems |
|---|---|---|---|---|---|---|
| 2026-10-25-bench-boot-NB | bench | 2026-10-25 | ESP32-S development board, 38 pins (as described by the hardware teammate) | - | no | - |
| 2026-10-26-bench-status-NB | bench | 2026-10-26 | ESP32-S development board, 38 pins (as described by the hardware teammate), on a breadboard | - | no | - |
| 2026-10-28-bench-calibration-start-NB | bench | 2026-10-28 | ESP32-S development board on a breadboard, same as the earlier sessions | - | no | - |
| 2026-10-28-bench-mpu-probe-NB | bench | 2026-10-28 | ESP32-S development board (ESP-32S module), breadboard, as in the teammate's photo of 28 Oct | - | no | - |

## 2026-10-25-bench-boot-NB (bench)

Source: serial text pasted by the hardware teammate through the team lead; not captured by this tool.
Boot line: node NB, boot 14, firmware 861255cb, mains 60 Hz, not calibrated: hold the button 3 s with the pump OFF.
- Node warning: MPU6050 WHO_AM_I is 0x72, expected 0x68 (a clone? continuing)

## 2026-10-26-bench-status-NB (bench)

Source: three S, status lines pasted by the hardware teammate through the team lead (with the W, lines the firmware printed after each); one 9 s phone video of the board (not stored here) and one photo of the accelerometer module.
- Node warning: accelerometer sample rate is 3176.9 Hz, not 1000: the mains hum bands may sit slightly off (see README)
- Node warning: accelerometer sample rate is 3198.0 Hz, not 1000: the mains hum bands may sit slightly off (see README)
- Node warning: accelerometer sample rate is 3001.0 Hz, not 1000: the mains hum bands may sit slightly off (see README)
Last status line: acc_rate=3001.0, acc_overflows=2185, i2c_errs=289, mic_errs=0, sent=0, rejected=0.

## 2026-10-28-bench-calibration-start-NB (bench)

Source: serial text pasted by the hardware teammate through the team lead: five S, lines and two I,button lines from the 28 Oct final firmware.
Last status line: acc_rate=999.2, acc_overflows=0, i2c_errs=7, mic_errs=0, sent=0, rejected=0.

## 2026-10-28-bench-mpu-probe-NB (bench)

Source: serial text pasted by the hardware teammate through the team lead: the output of the mpu command and five S, lines, with the W, lines printed between them; one photo of the breadboard.
- Node warning: accelerometer sample rate is 3350.4 Hz, not 1000: the mains hum bands may sit slightly off (see README)
- Node warning: calibration could not start: not enough memory (free 67768 bytes, largest piece 36852). Restart the node and try again
- Node warning: calibration could not start: not enough memory (free 68752 bytes, largest piece 36852). Restart the node and try again
Last status line: acc_rate=999.3, acc_overflows=0, i2c_errs=0, mic_errs=0, sent=0, rejected=0.

## What this does and does not support

- A bench log shows the board boots and what its sensors say about themselves. It says nothing about whether the node can tell a pump's on from its off.
- Only labelled recordings on a real pump or motor, with the equipment and mounting written down, count as pump evidence.
- Evaluation recordings must not be used to choose a mounting or a setting. If they were, they are development recordings; say so.
