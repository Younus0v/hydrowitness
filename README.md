# HydroWitness

*When the screen lies, the pipes still tell the truth.* (Static Head Labs, Conrad Challenge, Cyber-Security and Technology)

HydroWitness is a read-only safety net for small water systems. It does two things when SCADA can't be trusted:

1. **Time-to-Trouble clock.** It estimates how many minutes until the pressure at the system's critical point
   falls below 20 psi, from the tank level and a physical model, even when the SCADA screen is dark or frozen.
2. **Trust indicator.** It compares what SCADA *claims* with independent witnesses (an operator's own readings,
   customer calls, and a cheap **Witness Node** that listens to the pump's vibration and sound) and shows green,
   amber or red, with the reasons, a hash-chained evidence log, a Lockout Playbook for what to do next, and a
   Drill Mode to practise.

It never writes to the plant. The software is about 95% of the project; the only hardware is the Witness Node
(ESP32, MPU6050 accelerometer, INMP441 microphone, button, three LEDs, buzzer), and it has no pressure sensor.

## Quick start (no typing)

1. Install Python 3.11 or newer from https://www.python.org/downloads/ (on Windows tick **Add python.exe to PATH**).
2. Unzip this folder, then **double-click `START_HERE_Windows.bat`** (Windows) or **`START_HERE_Mac_Linux.command`**
   (Mac; if it won't open, right-click, Open). The first run sets itself up in about a minute and needs internet.
3. Your browser opens on the dashboard, already signed in. Press **Drill Mode** to watch the attack scenarios.
   Close the black window to stop it.

It runs only on your own computer (127.0.0.1) with a random token kept in `.hydrowitness_token`, and runs the
demo site until you give it a real site profile. The Windows script has not been run on a Windows machine yet; the
Mac/Linux one was run from a fresh copy and the server, token check and sign-in were verified.

## What exists and how well it is tested

| part | status |
|---|---|
| server: Time-to-Trouble clock, seven consistency rules, trust engine, node verification, evidence log, playbook, private alerts with a retry queue, API, dashboard with an offline Emergency view | built; 480 automated tests pass, including property-based fuzz tests |
| simulated plant, simulated Witness Node, six attack drills, false-alarm runs | built; results below |
| clock against EPANET hydraulics (310 randomized systems) | done; results below |
| exact confidence intervals, a setup-error sensitivity analysis, a long false-alarm study (400 simulated site-days) | done; `results/statistics.md`, `results/sensitivity.md`, `results/long_normal.md` |
| Witness Node signal processing, classifier, install calibration, button/LED/buzzer logic, message signing | built in portable C++; run against the Python reference and synthetic pumps on a normal computer |
| Witness Node Arduino sketch (I2S, I2C, Wi-Fi, flash) | written, compiles for the ESP32 (core 3.3.12), and **has booted on a real ESP32-S board** (bench boot log in `measurements/`). Sensor data, Wi-Fi to the server and a real pump are **not yet verified**; the accelerometer module answered `0x72` instead of `0x68`, which is being checked |
| real pump: does the node tell on from off? | **not measured** (needs the parts: `docs/HARDWARE_STAGE.md`) |
| in-browser demo (site/demo/): the real dashboard replaying recorded drills, no server | built; regenerate with `python -m hydrowitness.sim.web_recordings`, tested against the saved drill results |
| real SCADA connection, real systems | not done |

## See it in a browser, no install

The website's **Demo** page (`site/demo/`) runs the real dashboard from recordings of the real engines playing each
drill on the simulated plant. You can pick a drill, tick the scripted operator, pause, skip and change the speed.
Breaking things yourself and typing in your own readings need the downloadable version (Quick start above).
To host it, see `site/README.md` (Vercel with Root Directory `site`, or Netlify with Publish directory `site`).

## Run it

```
pip install -r requirements.txt
python3 -m pytest tests -q -n 4                              # 480 tests, about 1 to 3 minutes (needs g++ for the firmware tests)
python3 tools/verify_all.py                                  # one command: tests, and that the reports and website match the saved results
HYDROWITNESS_TOKEN=choose-a-long-random-token uvicorn hydrowitness.api:app --port 8000
```

Open http://localhost:8000 and enter the token. With no site profile configured it runs the **demo site** (and
says so). Use **Drill Mode** to watch the six attack scenarios play out against the real engines. To run a real
site, see `docs/INTERFACES.md`. Re-run the validations:

```
python3 -m hydrowitness.sim.wntr_validation        # V1, about 35 s, writes results/v1_wntr_validation.*
python3 -m hydrowitness.sim.validation_report      # V2, about 70 s, writes results/v2_drills.*
python3 -m hydrowitness.sim.evaluation --jobs 2    # HydroWitness against a plain alarm panel, about 3 min, writes results/evaluation.*
python3 training/measure.py report                 # what real measurements exist (none from a pump yet), writes results/measurements.*
python3 tools/long_normal.py --runs 80 --days 5    # long false-alarm study, about 15 min, resumable, writes results/long_normal.*
python3 tools/make_statistics.py                   # exact confidence intervals for every count, writes results/statistics.*
python3 tools/sensitivity.py                       # what a wrong setup number does to the countdown, writes results/sensitivity.*
python3 tools/soak_test.py                         # load and soak test of the real server, writes results/soak.*
```

## Results (simulated plant; read the limits)

**V1: the clock against EPANET** (`results/v1_wntr_validation.md`). Randomized tank-and-pipe systems, true time to
20 psi from Hazen-Williams hydraulics.

| | systems | worst case later than reality | expected case, median error |
|---|---|---|---|
| nothing random (checks the model itself) | 30 | 0 | 0% (p90 1%) |
| normal days, pumps off | 100 | 0 | about 0% bias, 8% typical |
| a witness shows a pump moving water | 70 | 0 | (stable cases) |
| that pump delivers 5-18% less than the clock assumes (worn impeller) | 30 | 0 | (stable cases) |
| headloss margin set 30-70% too low | 40 | 0 | |
| real demand 1.3-2.0x the configured peak | 40 | **8 (20%)** | clock too optimistic |

The worst case is typically hours earlier than reality in normal conditions: it is designed to be safe, not tight.
**When real demand exceeds the configured peak (a main break, fire flow) the clock can be late**; the screen states
the demand it assumes, and an operator tank reading re-anchors it. Two engine fixes came out of this run: the
expected case now scales friction with demand (it was about 36% too pessimistic), and the tank-empty level is a
floor.

**V2: attack drills** (`results/v2_drills.md`), five random seeds each, simulated nodes:

| scenario | result |
|---|---|
| frozen screen (PLC lockout) | amber in 65 s, red in 95 s |
| alarms off, remote access cut, pump cycles changed | amber 50 s, red 80 s, no operator needed |
| replayed old data | red in 50 s |
| power outage that looks like an attack | red in about 2 minutes with a "backup power" explanation card |
| pumps forced off behind a **careful forgery** | amber within 30 s; **red only when an operator looks** (about 2 minutes) |
| a valve closed out of sight | amber (customers) in 3 minutes; **never red** (customer reports can't make red alone, by design) |
| healthy plant | 0 false alarms in 10 site-days with 33 real pump starts and stops; 0 false rule episodes in 20 site-days |

## How sure are we, and what is still missing

Every count above has an exact 95% interval in `results/statistics.md` (for example, 0 unsafe in 200 EPANET systems inside the clock's assumptions means the true rate is below 1.8%, not zero). The long study found 0 false amber and 0 false red in 400 further simulated site-days on seeds the tuning never saw, which rules out a false warning more often than about once in 108 site-days, and no further. A wrong typed elevation moves the countdown: under the setup errors we assumed, about 1 site in 10 would start with an optimistic worst case, and the optional `setup_uncertainty_ft` allowance cuts that (`results/sensitivity.md`). Fuzz testing found two real bugs, both fixed. **All of this is simulation.**

| document | what it is |
|---|---|
| `docs/VALIDATION_MATRIX.md` | every public claim, the evidence for it, and its status (supported in simulation, shown by tests, not tested) |
| `docs/LIMITATIONS.md` | everything we know it does not do or has not shown |
| `docs/THREAT_MODEL.md` | who might attack it, what we built against each, what is not covered |
| `docs/FMEA.md` | how the Witness Node can fail, what then happens, what we did |
| `docs/PUMP_TEST_PROTOCOL.md` | the pass line for a real pump test, written before the recordings |
| `docs/RESEARCH_BASIS.md` | the sources we cite, what each says, and what we have not verified |
| `docs/industry/` | drafts for operator interviews, a survey, a read-only pilot proposal and outreach (no results yet) |

## Where things are

```
START_HERE_*.bat/.command, run_local.py   one-click start (see Quick start)
site/                    the website: six pages plus an in-browser demo of the real dashboard (static; see site/README.md for putting it on Vercel or Netlify)
tools/                   build_site.py writes the website's pages from tools/site_src/
hydrowitness/            the server: engine/ (clock, rules, trust, evidence checks), security/, evidence_log, playbook, alerts, api, static/ dashboard
hydrowitness/sim/        simulated plant and node, drills, V1 and V2 validation scripts, evaluation.py (comparison with a plain alarm panel)
firmware/witness_node/   Arduino sketch plus portable C++ core; firmware/host_tests/ builds and tests it on a computer
training/                node_reference.py (the one definition of what the node computes), train_classifier.py (the V3 analysis), synth.py,
                         measure.py (record and report real measurement sessions: bench, development, evaluation)
measurements/            one folder per real recording session; today only a bench boot log, no pump
tests/                   480 tests, including tests/test_properties.py (Hypothesis fuzzing; HW_FUZZ_EXAMPLES=3000 for a deep run)
results/                 saved validation, evaluation, statistics, sensitivity, long-study and measurement output
docs/                    INTERFACES.md, WIRING.md, HARDWARE_STAGE.md, and the evidence documents listed above
.github/workflows/       CI: runs tools/verify_all.py on every push
```

## Limits you should state out loud

* Synthetic signals and a simulated plant prove the software behaves as designed; they say nothing about how a
  real pump sounds. That is the next stage, and a FAIL there is a legitimate finding.
* A careful forgery of every SCADA value is caught by the physical witnesses, but the system only calls it red
  with a human in the loop. A hidden valve closure is amber, never red.
* The clock's worst case assumes demand stays under the configured peak, and trusts the elevations typed at setup (see `setup_uncertainty_ft`).
* The full list of what we have not shown is `docs/LIMITATIONS.md`.
* Alerts go only to a private, token-protected channel; there is no public alerting path by design.
* Not included: a SCADA adapter for any specific product, direct-pumping (no storage tank) sites, a node that
  buffers events while offline. See `docs/HARDWARE_STAGE.md`.

This is a student prototype for a competition, not a certified safety system. Do not rely on it as the only
protection for a real water system.

## License

HydroWitness is open source under the Apache License 2.0 (see `LICENSE` and `NOTICE`), Copyright 2026 Static Head Labs. It covers the software, the firmware and the wiring documents in this repository. The license comes with no warranty, and this is a student prototype, not a certified safety system.
