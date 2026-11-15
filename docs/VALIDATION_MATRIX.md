# Validation matrix: every claim, its evidence, and how far the evidence goes

Read this before quoting any number from the website, the brief or the video. If a claim you want to make is not here, or its status is not green, do not make it.

**Status words**

* **Supported in simulation**: a result exists that anyone can re-run (`python3 tools/verify_all.py --full`), on a simulated plant and simulated Witness Nodes. It does not show the same on a real plant.
* **Shown by tests**: automated tests check that the software does what the claim says.
* **Measured on the bench**: seen on real hardware on a desk, with no pump.
* **Reported, not recorded**: someone on the team says it happened; no data exists. We make no numeric claim from it.
* **Not tested**: nothing exists yet.

Intervals are exact 95% intervals from `results/statistics.md`. They cover sampling luck inside the simulation only.

## A. The countdown (Time-to-Trouble clock)

| # | claim | evidence | status | what it does not show |
|---|---|---|---|---|
| A1 | The worst-case countdown is not later than the true time, under the clock's stated assumptions | V1 against EPANET: 0 unsafe of 200 randomized systems inside the assumptions (95% interval 0.0% to 1.8%). `results/v1_wntr_validation.md`, `results/statistics.md` §1 | Supported in simulation | EPANET is itself a model. 200 systems are not every system. Real tanks, valves and pipes have things we did not model |
| A2 | It can be late when real demand is far above the configured peak | 8 of 40 stressed systems (20%; 95% interval 9.1% to 35.6%) | Supported in simulation; **a stated limit, shown on screen** | How often real demand exceeds the peak at a real site |
| A3 | Across all simulated runs, the worst case was never later than the simulated truth | 0 of 2,400 scored checkpoints; 0 of 242 runs (95% interval 0.0% to 1.5%). Checkpoints in one run are correlated, so quote the run-level figure | Supported in simulation | Same as A1 |
| A4 | The expected-case countdown is close to the truth on normal days | V1: about 0% bias, 8% typical error with pumps off | Supported in simulation | Real demand patterns |
| A5 | Absurd or missing inputs never produce a nonsense estimate | Property tests: the estimate is always finite, never negative and never inverted; more water never means less time; higher demand never means more; an older reading never lengthens the worst case; a non-finite level is not a reading. Fuzzing found and fixed an OverflowError | Shown by tests | Inputs outside the test strategies |
| A6 | A wrong elevation or friction margin typed at setup shifts the countdown, and we know by how much | `results/sensitivity.md`: about 96 minutes per foot in the demo case; with the setup errors we assumed, about 10% of sites start with an optimistic worst case (95% interval 9.1% to 10.9%); a 2 ft allowance gives 3.5%, 4 ft gives 1.0% | Supported in simulation | **The error sizes are our assumptions, not measurements of how accurate real surveys are** |
| A7 | A real tank drawdown matches the countdown | Nothing | **Not tested** | Everything about real tanks |

## B. Detecting a lying screen

| # | claim | evidence | status | what it does not show |
|---|---|---|---|---|
| B1 | HydroWitness raises a warning on all six simulated attack scenarios when no operator walks to the pump | 120 of 120 runs (95% interval 97.0% to 100%). `results/statistics.md` §2 | Supported in simulation | One plant and one script per scenario, 20 noise seeds each, so the runs are less independent than the count. Attacks we did not write are not in it |
| B2 | A plain alarm panel misses most of them | Panel: 40 of 120 (33.3%). With a flat-line rule: 80 of 120 (66.7%) | Supported in simulation | A real alarm panel may be set up differently; we gave it generous settings |
| B3 | A careful forgery of every SCADA value is caught as amber, and red only with a person looking | Forged-off pumps: 20 of 20 warned, 0 of 20 red without an operator | Supported in simulation | Real node noise (the nodes' error rates are our assumptions) |
| B4 | A hidden valve closure is noticed by customers' reports, as amber only | 20 of 20 amber; never red, by design | Supported in simulation; **a stated limit** | |
| B5 | A jammed Witness Node does not make HydroWitness quietly green | 10 of 10 warned in each jamming condition | Supported in simulation | |
| B6 | Healthy plants do not produce false amber or red | **0 false amber, 0 false red in 434 simulated healthy site-days** (400 of them in a long study on seeds 1000 to 1079, half on a 30 ft tank, 1,267 pump starts and stops). 95% upper bound 0.0092 per site-day for the long study alone (about one per 108 site-days), 0.0085 combined | Supported in simulation | **A real plant has noise, drift and equipment this simulation lacks.** A clean simulated run says the software does not invent trouble in our noise model. It cannot say a real plant would stay quiet |
| B7 | The seven consistency rules fire on what they should and stay quiet otherwise | Dedicated tests per rule; drills; property test that silent SCADA is never green | Shown by tests | |
| B8 | Detection works on public benchmark data (BATADAL) | Not run | **Not tested** | `docs/RESEARCH_BASIS.md`, `docs/LIMITATIONS.md` L17 |

## C. The Witness Node

| # | claim | evidence | status | what it does not show |
|---|---|---|---|---|
| C1 | The node's signal processing matches a Python reference and recognises synthetic pumps | Portable C++ core and host tests against `training/node_reference.py` and synthetic pump signals; V3 gate (held-out time periods, worst fold 90% or better) | Shown by tests | Synthetic signals say nothing about how a real pump sounds |
| C2 | The firmware boots on a real ESP32 and the sensors answer | Four bench sessions in `measurements/` (boot, accelerometer probe, calibration start); accelerometer sampled at about 999 Hz with 0 overflows and 0 bus errors in the later sessions; button, LEDs and buzzer worked | Measured on the bench | No pump. No microphone with a sound source. Wi-Fi to the server not tested |
| C3 | **The node tells a running pump from a stopped one on a real pump** | A team member reports a trial on a pump owner's pump that worked and that the owner thanked them. **No data was kept.** | **Reported, not recorded.** The website says "reported, unrecorded" and gives no figure | Everything. This is the most important open item. `docs/PUMP_TEST_PROTOCOL.md` is the plan |
| C4 | A node message cannot be forged or replayed | HMAC-SHA256 per device, boot count and sequence, 60 s skew limit; tests of each | Shown by tests | A node secret can be read from a board in hand (no flash encryption): `docs/THREAT_MODEL.md` |
| C5 | Failures of the node are understood | `docs/FMEA.md`, 22 rows. Seen so far: clone accelerometer, FIFO overflow, calibration memory, all fixed | Measured on the bench (failures); our own judgement (ratings, not reviewed) | Field failures |

## D. The software as a product

| # | claim | evidence | status | what it does not show |
|---|---|---|---|---|
| D1 | The server does not crash on malformed or hostile input | Property-based fuzzing of 9 routes plus raw junk bodies: 150 examples per test by default, 2,500 in a deep run. Two real bugs found and fixed | Shown by tests | A determined attacker with more time and a better fuzzer |
| D2 | It stays responsive under load | `results/soak.md`: the real server at 1x, 20x and 100x a realistic message rate for 30 s each on a 2-CPU development machine: no failed request; 95th percentile latency 36 ms for dashboard polls at 100x; memory 59 MB to 64 MB; the evidence log verified afterwards | Measured on one computer | 90 s is too short to rule out a slow memory leak. A plant's own computer will differ |
| D3 | The evidence log is tamper-evident | Hash chain; tests that an edit, deletion or reordering breaks it and says where; verified after the soak run | Shown by tests | Replacing the whole log with no earlier head hash is not detected; anchoring is the utility's job |
| D4 | No known-vulnerable dependency; no high-severity static-analysis finding | `pip-audit`, `bandit`, `ruff` run during this round | Checked once | Re-run before each release; CI runs tests, not the audit |
| D5 | The website's numbers cannot drift from the saved results | `tools/build_site.py --check` and `tests/test_site.py` rebuild the pages from `results/*.json` | Shown by tests | |
| D6 | Alerts go only to a private channel and queue when it is down | Tests; the channel-down drill | Shown by tests | A real ntfy server |
| D7 | The one-click starter works | Mac and Linux run from a fresh copy. **Not run on Windows to our knowledge** | Partly tested | |
| D8 | It works with a real SCADA system | Nothing; no adapter for any product | **Not tested** | `docs/LIMITATIONS.md` L3 |

## E. The need and the market

| # | claim | evidence | status | what it does not show |
|---|---|---|---|---|
| E1 | Cyberattacks on small water systems are real | Aliquippa (Nov 2023), Muleshoe (Jan 2024), Colorado (Aug 2026), Oldsmar (2021); EPA's May 2024 alert: over 70% of systems inspected since Sept 2023 in violation of basic requirements. `docs/RESEARCH_BASIS.md` | Sourced | That HydroWitness would have helped in any of them |
| E2 | Smallest systems have no legal push to assess cyber risk | AWIA §2013 applies above 3,300 people | Sourced | |
| E3 | Operators want this | **No interviews, surveys or letters exist** | **Not tested** | `docs/industry/` is the plan |
| E4 | A utility will pilot it | None has agreed | **Not tested** | |
| E5 | Prices and market size | Internal projections | **Not tested.** Say "plan", never "result" | |

## How to use this table in the Innovation Brief and the video

1. Lead with C3 honestly: say what was done and that no data was kept, or do the recorded test first.
2. Put B6 and A1 next to their intervals and their limits, never as "0" alone.
3. Show A2, B4 and the limitations register on purpose. A project that states its own weak points is believed more on its strong ones.
4. Do not claim anything marked Not tested. If a judge asks, the answer is "not yet, and here is the plan."
