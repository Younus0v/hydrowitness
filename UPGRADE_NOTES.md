# Upgrade notes: the nine-point improvement round

What changed, how to check it, and what is still not done. Everything runs locally; no Supabase, no cloud database, no new service.

Run `python3 -m pytest tests -q` (428 tests, about 3 minutes) and `python3 tools/build_site.py --check` after copying the files in.

## What changed

| # | plan item | where | state |
|---|---|---|---|
| 1 | Countdown that cannot lie about zero | `engine/time_to_trouble.py`, `playbook.py`, `service.py`, `static/index.html` | done |
| 2 | Evidence per quantity (pump operation, tank level, pressure, remaining time) | new `engine/quantities.py`, dashboard "Screen vs witnesses" | done |
| 3 | "Next useful check", and what changed after a reading | new `engine/next_check.py`, `service.py`, dashboard | done |
| 4 | Dashboard main view reorganised | `static/index.html` | done, checked in screenshots (desktop and 390 px phone) |
| 5 | Demo comparison: screen claim, witnesses, HydroWitness, simulation truth | `site/demo/compare.js`, `sim/web_recordings.py`, new `sim/truth.py` | done |
| 6 | Fair comparison with a plain alarm panel | new `sim/evaluation.py`, `results/evaluation.{json,md}` | done |
| 7 | Website numbers come from the saved results | `tools/build_site.py`, `tools/site_src/*.html` | done |
| 8 | Physical-measurement tools | new `training/measure.py`, `measurements/`, `results/measurements.{json,md}` | done; the only real recording so far is one bench boot log |
| 9 | Regression tests and packaging | `tests/test_honest_estimates.py`, `test_evaluation.py`, `test_measure.py`, `test_separation_and_gaps.py`, additions to four more | done |

### 1. The countdown
- Zero stays zero. If the worst case has reached 0 minutes the dashboard says so; it never turns 0 into "about 1 minute" or a blank.
- With no tank level from anywhere it says **"Estimate unavailable"** and shows no number.
- Every estimate shows how old its level reading is, how much of the worst case that age costs, and an assumptions list: which demand, which inflow (none is credited until a person confirms water is moving), the 72 h look-ahead, the friction margin. The dashboard, the alerts and the playbook read the same numbers.
- The Lockout Playbook's time targets use the same clock, and say when they fall back to a planning default.

### 2 and 3. Evidence and the next check
- For each of pump operation, tank level, pressure and remaining time the dashboard says whether it is independently confirmed, only the screen's word, an estimate, in conflict, or unavailable, and why.
- It lists what is missing or conflicting, and recommends **one next useful check** (who, what, why, what it will change). After a reading it shows what that reading changed, comparing against the state without it, so ordinary drift is not reported as a change.

### 6. The comparison with a plain alarm panel
- Two baselines, settings written in `results/evaluation.json`: an alarm panel that believes the screen (low tank below 15 ft, low pressure below 20 psi, no data for 120 s), and the same panel with a generous flat-line rule (readings identical for 300 s).
- 10 seeds on two tank sizes, 6 scenarios with and without an operator, plus 3 failure conditions where the witnesses are jammed, plus 24 site-days on a healthy plant. Repeat with `python -m hydrowitness.sim.evaluation` (about 3 minutes with 2 workers). Same seeds give identical results; a test checks that.
- Result in one line each: HydroWitness raised a warning in all 120 no-operator attack runs; the plain panel in 40 and the flat-line version in 80; 0 false warnings in 24 healthy site-days for all three; the worst-case countdown was never later than the simulated plant's truth; the expected-case countdown was never late and was early in 85 of 2,688 scored checks.
- Honest limits are printed in the report: the plant and the nodes are simulated, the nodes' error rates are assumptions, a closed valve is not scored (the simulation does not work out pressure beyond a valve), and the plain panel's late catch of the replay is a quirk of the simulator.

### A real bug the longer runs found
`consistency.py`, rule 2: at the moment the last pump stopped, "all pumps shown stopped but the tank is rising" looked back five minutes and still counted the rise from when the pump was running. On a small tank that raised a false amber (3 in the first 24 site-days). It now counts only the time every pump has been shown stopped. A regression test fails on the old rule and passes on the new one. No saved drill result changed.

### 8. Measurement tools
`training/measure.py` (`new`, `capture`, `train`, `heldout`, `report`): a session folder per recording with its purpose fixed up front (**bench**, **development**, **evaluation**), the equipment and mounting written down, the node's own lines kept untouched, and what the person did in a separate file. `train` refuses evaluation sessions; `heldout` fits on development sessions only. `results/measurements.md` says "No measurements from a real pump have been supplied" until labelled pump recordings exist. The first bench session is the boot log the hardware teammate sent on 25 Oct 2026.

## Hardware status after the boot log
The firmware has booted on a real ESP32-S board (node NB, boot 14, firmware 861255cb, 60 Hz, not calibrated). The accelerometer module answered `0x72`, not `0x68`: unresolved until the `S,` status line shows `acc_rate`. The README, firmware README, hardware stage guide and website no longer say "never run on a real ESP32"; the website's wording follows `results/measurements.json`, so it changes by itself once real pump recordings are added.

## Not done, and not tested
- No real pump, no real Wi-Fi link and no real Windows run of the new tools. `measure.py capture --port` needs `pip install pyserial` and was only tested with a stand-in.
- The firmware changed only in the 26 October follow-up (see the last section); the nine-point round itself did not touch it.
- The dashboard was checked visually at desktop and phone widths and by tests for its text and structure. It has not been tested with a screen reader.
- The baseline alarm thresholds (15 ft, 20 psi, 120 s, 300 s) are our choice, written down; a plant with different alarm limits would give different panel numbers.

## Follow-up, 26 October: what the first status lines and the video showed
The hardware teammate sent three status lines, a photo of the accelerometer and a 9 s video.
- **Accelerometer.** `acc_rate` 3001-3198 Hz against the 1000 Hz asked for, `acc_overflows` up to 2185, `i2c_errs` up to 289, only 94 epochs in the time of three status lines. The chip answers 0x72 and carries no visible marking. Our reading (not proved): it ignores the sample-rate divider and runs far above 1 kHz; the 3 kHz is the speed at which the I2C bus could empty the FIFO. The firmware now measures the real FIFO rate at boot, tries the 184 Hz low-pass setting (1 kHz base) if the first fails, and otherwise switches the accelerometer off and says so; `mpu` prints a probe; `status` ends with `heap_max`, `acc_cfg`, `acc_who`.
- **Calibration could not start.** In the video the button hold gave 3 beeps, 3 red flashes, then red/amber alternating. That pattern means "calibration failed or could not start". The old code asked the heap for one 31 kB block; the board had 68 kB free in total, so the request very likely failed on a fragmented heap. Calibration memory is now four 7.7 kB blocks, and the sketch prints a warning when memory is short. Two host tests cover it, and one of them fails on the old single block.
- **Not confirmed on the board.** The sketch type-checks against the stand-in headers and compiles for the ESP32 (esp32 core 3.3.12, all warnings on, no warnings; 1,003,300 bytes of program space, 76%). Both fixes need one flash and one test by the hardware teammate.
- Recorded as `measurements/2026-10-26-bench-status-NB` (bench). It proves nothing about a pump.

