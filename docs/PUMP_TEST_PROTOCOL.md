# Pump test protocol (written before the recordings, so the pass line cannot move afterwards)

**Purpose.** Answer one question with data a stranger can check: on real pump equipment, does the Witness Node tell **running** from **stopped** on time periods it was never tuned on?

**Why this exists.** Everything numeric in `results/` comes from a simulated plant and simulated nodes. A team member reports that a trial on a pump owner's real pump worked, but no recording was kept, so no figure from it is claimed anywhere (the website says "reported, unrecorded"). This protocol turns the next trial into evidence. A trial run under it replaces the unrecorded one in what we may claim.

Date this protocol was committed: see the git history of this file. **Do not edit the pass criteria after the first recording.** If a criterion turns out to be wrong, write a new version and say so in the report.

## 1. Before you go

* **Permission.** The pump owner agrees in writing (an email is enough) that the team may attach a small sensor box to the outside of the pump or motor, for a stated time, and may keep and publish the anonymised readings. The owner may say no to publishing the site name. A minor team member goes only with an adult who has agreed to supervise.
* **Safety.** The node is clipped or taped to the **outside** of the machine and is never connected to any wire of the plant, the pump's motor terminals or its controls. Nobody opens an electrical panel, climbs a tank or enters a confined space. The pump is started and stopped **only by its operator**, as they normally would. The team never starts or stops the pump.
* **No network contact.** The node runs on a USB battery or an outlet adapter and logs to a laptop. It is not joined to the plant's network. HydroWitness does not touch the plant's SCADA in this test.
* **Equipment record.** Write down, in `session.json`: the pump's make, size or horsepower, type (submersible, booster, centrifugal), the environment (indoors, outdoors, enclosure), the mounting (what surface, how attached, how far from the motor end), the board used, and the firmware hash printed at boot.

## 2. What to record

Use `training/measure.py` (see `docs/HARDWARE_STAGE.md`, stage 8). Every session has a purpose fixed **before** it is recorded.

| session kind | what it is for | how many |
|---|---|---|
| development | choose the mounting; look at the data; set up the node's calibration | at least 1 per pump |
| evaluation | scored only. The data is **not looked at** while choosing mounting or settings | at least 2 per pump, on **different days or different times of day** |

* Minimum for any claim: **2 different pumps** (or one pump and one motor of a different type), or one pump on **3 different days**. One pump on one afternoon is a demonstration, not a finding.
* In each session: at least **3 off/on cycles**, each state held **at least 5 minutes**, labelled with the `label S` and `label R` commands, with `label -` before and after every start, stop or touch of the box.
* The commands, once per session (`pip install pyserial` first; use your own port name, for example `COM5`):
  1. `python3 training/measure.py new 2026-11-15-pump-1 --purpose development --pump "make, hp, type" --mounting "where and how it is attached" --operator "your name" --board "ESP32-S" --accelerometer "MPU6050" --microphone "INMP441"`
  2. `python3 training/measure.py capture 2026-11-15-pump-1 --port COM5`
  3. While it runs, type then Enter: `s` when the pump is off and quiet, `r` when it is steady and running, `-` before every start, stop or touch of the box, `n <text>` for a note, `q` to finish. `dump on` is sent for you after 5 seconds; if no lines starting with `F` appear, type `dump on`.
  4. After the last session: `python3 training/measure.py report`.
* Record the operator's note of the real start and stop times in `events.csv` (a stopwatch is fine). The node's label commands are typed by a person watching the pump, not inferred from the node.

## 3. What counts as a pass (fixed in advance)

The held-out score is computed by `python3 training/measure.py report` from the evaluation sessions only, using a fit made from the development sessions only.

| result | rule |
|---|---|
| **PASS** | On the evaluation sessions, for at least one channel (vibration or sound), the lower of "running right" and "stopped right" is **at least 90%**, **and** the lower end of its exact 95% interval is **at least 80%**, **and** this holds on every evaluation session separately |
| **MARGINAL** | The lower of the two is between 70% and 90% on the combined evaluation data, or it passes on some sessions and not others |
| **FAIL** | Below 70%, or no channel separates. Reported as it is; a FAIL on one pump is a result, not a reason to drop the pump |
| **NO DATA** | Fewer than 10 usable windows per class in development or evaluation. Record longer |

The report also prints its own GOOD (97% or better), FAIR (90% or better) and POOR labels for each channel. Those are a separate scale. The PASS, MARGINAL or FAIL result for this protocol is decided by the table above from the printed percentages, intervals and counts, and is written into the trial report by hand.

Why two thresholds: with 20 windows a score of 95% has a 95% interval from about 75% to 99.9%. Requiring the interval's lower end to stay above 80% forces enough data before we say "works".

**The intervals treat each 5-second window as independent, and they are not.** Windows in the same run are strongly correlated. So the number to quote is also the count of separate runs, days and pumps. Write all three beside the percentage.

## 4. What the report must say, whatever the result

1. Pump(s), mounting, days and counts: separate runs, separate days, separate pumps.
2. Both channels' held-out scores with their intervals, from `results/measurements.md`.
3. The on-device calibration verdicts (`calibration: accelerometer ... , microphone ...`).
4. Anything that went wrong (a bad mounting, a noisy neighbour, a day the pump never started).
5. The sentence: "This is for this pump, this mounting and these days only."
6. A FAIL or MARGINAL is stated with the same prominence as a PASS.

## 5. What we may and may not claim afterwards

| result | allowed | not allowed |
|---|---|---|
| PASS on ≥2 pumps | "In a recorded test on N pumps over M days, the node's [channel] told running from stopped on held-out data: X% (95% CI a% to b%)" | "Works on pumps" in general; any claim about other pump types |
| PASS on 1 pump | "Recorded test on one pump" with the same numbers | Generalising |
| MARGINAL / FAIL | State it and what we changed to try again | Dropping the result |
| nothing recorded | "A trial was reported by the team; no data was kept" (current wording) | Any number from it |

## 6. After the test

Commit the session folders under `measurements/`, run `python3 training/measure.py report`, and commit `results/measurements.*`. A separate step, the real-node drills (stage 9 of `docs/HARDWARE_STAGE.md`), then tests the full system with the real node as witness on a bench pump the team controls.
