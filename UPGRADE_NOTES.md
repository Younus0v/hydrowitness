# Upgrade notes: the nine-point improvement round

What changed, how to check it, and what is still not done. Everything runs locally; no Supabase, no cloud database, no new service.

Run `python3 tools/verify_all.py` (tests, then that the saved reports and the website agree with the results; 480 tests, about 2 to 3 minutes) after copying the files in. The nine-point round below came first; the rigor round (November 2026) is at the end.

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
- **Calibration could not start.** In the video the button hold gave 3 beeps, 3 red flashes, then red/amber alternating. That pattern means "calibration failed or could not start". The cause was memory, but not for the reason first given: see the 28 October section.
- **Result on the board:** the accelerometer fix worked (28 Oct); the calibration change did not.
- Recorded as `measurements/2026-10-26-bench-status-NB` (bench). It proves nothing about a pump.

## Follow-up, 28 October: accelerometer fixed, calibration memory found
The teammate flashed the 26 Oct firmware and sent the `mpu` probe output, five status lines and a photo. Recorded as `measurements/2026-10-28-bench-mpu-probe-NB` (bench; the 26 Oct session's note carries a correction).
- **Accelerometer: fixed on this board.** The probe shows the first setting overflows the FIFO (the chip ignores the divider); the 184 Hz low-pass setting works. Five status lines: `acc_rate` 999.0 to 999.4 Hz, 0 overflows, 0 bus errors, epochs 435 to 700. The probe read 1.159 g with the board still, so this clone's scale is about 16 percent off; left alone because the node calibrates per unit.
- **Calibration: cause found.** The firmware printed "not enough memory" with 67,768 bytes "free" and a largest piece of 36,852. Two wrong readings followed: first that memory was a fragmentation problem (the 26 Oct four-block change), then that memory was not the cause at all. Both were mistakes of mine. The Arduino core's `ESP.getFreeHeap()` and `getMaxAllocHeap()` count all internal RAM, including instruction RAM that `malloc` cannot use. The built firmware has 37,940 bytes of free instruction RAM, which is the 36,852-byte "largest piece". The usable memory was about 30,916 bytes, shared with Wi-Fi, and the calibration asked for 30,720.
- **Fix (confirmed on the board).** The install recordings are stored as 16-bit half-precision numbers (`float_to_half`, `HalfRows` in `hw_model.h`): four blocks of 3,840 bytes, 15 kB in all, taken from the heap only while a calibration runs. If that much is not free at that moment (Wi-Fi busy), the node tries room for 96, then 64, then 48 windows per baseline (as little as 5.8 kB); fewer windows only make the calibration's own accuracy check a little noisier, and a test runs a full calibration with room for 48 and checks the node still follows the pump. Half precision keeps a relative error under 0.05 percent; tests check the conversion bit for bit against numpy for all 65,536 half values and for 60,000 random floats, and that calibrating from halves gives the same verdict and nearly the same model as from floats. `status` now ends with `mem8`, `mem8_max`, `mem8_min` (usable memory: free, largest piece, lowest ever) next to the old `heap*` figures, the boot prints one `I,memory:` line, and every 3 s button hold prints a line saying whether calibration started or was cancelled.
- **A mistake to avoid:** an intermediate firmware (not shipped to the repo) kept the recordings in 30 kB of fixed memory. That would have used up the usable memory and starved Wi-Fi. It was withdrawn the same day.
- Tests: 435 in all (428 before this follow-up, plus 3 in `tests/test_firmware_half.py`, 3 in `tests/test_firmware_node.py` for the button events and the crowded-heap cases, 1 in `tests/test_measure.py` that checks the raw serial logs are not git-ignored). The sketch compiles for the ESP32 (1,004,384 bytes of program space, 76%; static data 91,568 bytes, 27%). **Confirmed on the board the same day** (`measurements/2026-10-28-bench-calibration-start-NB`): holding the button started calibration with room for 128 windows and a second hold cancelled it; `mem8` was 31.7 to 32.0 kB free and its lowest ever 13.6 kB. Still not done: a calibration through both baselines, a pump or motor recording, the server link.

---

# Rigor round (November 2026): making the claims checkable

Aim: every number we say in public has a source anyone can re-run, an interval or limit beside it, and a place where its weaknesses are written down. Nothing here is a field result; all of it is simulation, tests and documents.

## What changed

| area | what | where |
|---|---|---|
| Exact statistics | Clopper-Pearson and Wilson intervals, exact Poisson upper bounds, tested against textbook values and for the 95% coverage guarantee | `hydrowitness/stats.py`, `tests/test_statistics.py` |
| Every count has an interval | One report built only from the saved results; `--check` fails if it is stale | `tools/make_statistics.py`, `results/statistics.{json,md}` |
| Sensitivity of the countdown to setup errors | One-at-a-time and Monte Carlo (4,000 draws per case) over an assumed error model; finding: about 1 site in 10 starts with an optimistic worst case under those assumptions | `tools/sensitivity.py`, `results/sensitivity.{json,md}` |
| An optional setup allowance | `setup_uncertainty_ft` (0 to 20 ft, default 0) shortens only the worst case; 2 ft cuts the optimistic share to 3.5%, 4 ft to 1.0% | `schemas.py`, `engine/time_to_trouble.py`, `docs/INTERFACES.md` |
| Long false-alarm study | 80 runs x 5 days = 400 healthy simulated site-days on seeds never used in tuning, 1,267 pump starts and stops: 0 false amber, 0 false red (upper bound 0.0092 per site-day). Resumable | `tools/long_normal.py`, `results/long_normal.{json,md}` |
| Fuzz and property tests | 12 Hypothesis tests: more water never means less time, a higher peak demand never gives more time, silence is never green, garbage requests never cause a server error | `tests/test_properties.py` |
| **Two real bugs found by the fuzzing, fixed** | (1) an absurdly large tank level crashed the countdown text with an OverflowError; (2) a request body with invalid text encoding caused an HTTP 500. Now a non-finite level is not a reading, a level above the tank height is capped at the height, and validation errors never echo the input | `time_to_trouble.py`, `api.py` |
| Load and soak test | The real server under 1x, 20x and 100x a realistic message rate: latency, errors, memory, and whether the evidence log still verifies | `tools/soak_test.py`, `results/soak.{json,md}` |
| Security scan | `bandit` and `ruff` found no high-severity issue; `pip-audit` found no known-vulnerable dependency at the time of the run. Small fixes: `raise ... from None` in four places | `api.py`, `training/measure.py` |
| One duplicate test removed | An exact-duplicate pair of tests in `tests/test_consistency.py` shadowed each other | `tests/test_consistency.py` |
| Intervals on real-pump scores | The held-out report now prints a 95% interval for every score and says that windows are not independent | `training/measure.py`, `tests/test_measure.py` |
| Website | A "How sure are we?" block whose sentences are generated from the saved statistics; the fuzz finding added to "Also tested"; the in-browser demo recordings regenerated because the clock now lists the setup allowance in its assumptions | `tools/build_site.py`, `tools/site_src/technology.html`, `site/demo/` |
| One command and CI | `tools/verify_all.py` (`--quick`, `--full`); a GitHub Actions workflow runs it on every push | `tools/verify_all.py`, `.github/workflows/ci.yml` |
| Evidence documents | Validation matrix, limitations register, threat model, FMEA of the Witness Node, pump-test protocol with a pass line fixed in advance, research basis with only verified citations | `docs/` |
| Industry kit (drafts) | Interview guide with consent script, an 8-question survey, a read-only pilot proposal, outreach emails, an empty outreach log | `docs/industry/` |

## Things worth knowing

* The first soak run showed one connection failure per Witness Node at 1x. The cause was the test client reusing a connection the server had closed after its idle timeout, not a server fault; a real node opens a new connection for each message. The test now does the same, and the second run had none.
* `hypothesis` and `pytest-xdist` are now in `requirements.txt`. Without hypothesis the property tests are skipped and say so.
* Regenerating the demo recordings changed all 13 files under `site/demo/`: the only difference is the new `setup_allowance_ft` field.
* The first GitHub run failed on Python 3.11 for two reasons, both found by running the same checks on 3.11 here. (1) The saved-report check compared floats as exact text, and 3.11 and 3.13 differ in the 16th digit (0.6915028921812354 against ...56); it now compares numbers with a tiny tolerance and everything else exactly (`stats.json_close`). (2) `wntr` imports `six` without declaring it, so on a clean install the EPANET validation tests were silently skipped; `six` is now in `requirements.txt`, and on a CI server a missing `wntr` fails loudly instead of skipping. The complete `tools/verify_all.py` now passes on both 3.11 and 3.13.
* Deep fuzz run: `HW_FUZZ_EXAMPLES=2500 python3 -m pytest tests/test_properties.py -q` passed after the fixes.

## Still not done (only the team can do these)

1. A **recorded** pump test, following `docs/PUMP_TEST_PROTOCOL.md`, on at least two pumps or on three different days.
2. Five or more operator interviews and the survey, using `docs/industry/`.
3. A utility willing to run the read-only pilot, in writing.
4. A licensed operator or engineer to review the clock's definition and the FMEA; an independent read of the code.
5. Letters of support in people's own words, after they have seen the work.
6. Parts receipts or quotes for the budget; permission in writing from the pump owner for any further trial.
7. Anything about cost and market: no customer has agreed to pay.
