# Real measurements: what has been supplied

**No measurements from a real pump have been supplied. Every number in results/ comes from a simulated plant and simulated Witness Nodes. The firmware has booted on a real board (1 bench log, no pump); that is the only real-hardware evidence so far.**

Sessions live in `measurements/`. Each has a purpose fixed before it was recorded: **bench** (no pump; proves things about the board only), **development** (may be studied and trained on), **evaluation** (never used to tune; only scored against what development produced). `python3 training/measure.py report` rewrites this file.

| session | purpose | date | board | pump | counts as pump evidence | problems |
|---|---|---|---|---|---|---|
| 2026-10-25-bench-boot-NB | bench | 2026-10-25 | ESP32-S development board, 38 pins (as described by the hardware teammate) | - | no | - |

## 2026-10-25-bench-boot-NB (bench)

Source: serial text pasted by the hardware teammate through the team lead; not captured by this tool.
Boot line: node NB, boot 14, firmware 861255cb, mains 60 Hz, not calibrated: hold the button 3 s with the pump OFF.
- Node warning: MPU6050 WHO_AM_I is 0x72, expected 0x68 (a clone? continuing)

## What this does and does not support

- A bench log shows the board boots and what its sensors say about themselves. It says nothing about whether the node can tell a pump's on from its off.
- Only labelled recordings on a real pump or motor, with the equipment and mounting written down, count as pump evidence.
- Evaluation recordings must not be used to choose a mounting or a setting. If they were, they are development recordings; say so.
