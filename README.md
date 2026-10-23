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
| server: Time-to-Trouble clock, seven consistency rules, trust engine, node verification, evidence log, playbook, private alerts with a retry queue, API, dashboard with an offline Emergency view | built; 319 automated tests pass |
| simulated plant, simulated Witness Node, six attack drills, false-alarm runs | built; results below |
| clock against EPANET hydraulics (310 randomized systems) | done; results below |
| Witness Node signal processing, classifier, install calibration, button/LED/buzzer logic, message signing | built in portable C++; run against the Python reference and synthetic pumps on a normal computer |
| Witness Node Arduino sketch (I2S, I2C, Wi-Fi, flash) | written, compiles for the ESP32 (core 3.3.12), **never run on a real ESP32** |
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
python3 -m pytest tests -q                                   # 319 tests, about 110 s (needs g++ for the firmware tests)
HYDROWITNESS_TOKEN=choose-a-long-random-token uvicorn hydrowitness.api:app --port 8000
```

Open http://localhost:8000 and enter the token. With no site profile configured it runs the **demo site** (and
says so). Use **Drill Mode** to watch the six attack scenarios play out against the real engines. To run a real
site, see `docs/INTERFACES.md`. Re-run the validations:

```
python3 -m hydrowitness.sim.wntr_validation        # V1, about 35 s, writes results/v1_wntr_validation.*
python3 -m hydrowitness.sim.validation_report      # V2, about 70 s, writes results/v2_drills.*
```

## Results (simulated plant; read the limits)

**V1: the clock against EPANET** (`results/v1_wntr_validation.md`). Randomized tank-and-pipe systems, true time to
20 psi from Hazen-Williams hydraulics.

| | systems | worst case later than reality | expected case, median error |
|---|---|---|---|
| nothing random (checks the model itself) | 30 | 0 | 0% (p90 1%) |
| normal days, pumps off | 100 | 0 | about 0% bias, 8% typical |
| a witness shows a pump moving water | 70 | 0 | (stable cases) |
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

## Where things are

```
START_HERE_*.bat/.command, run_local.py   one-click start (see Quick start)
site/                    the website: six pages plus an in-browser demo of the real dashboard (static; see site/README.md for putting it on Vercel or Netlify)
tools/                   build_site.py writes the website's pages from tools/site_src/
hydrowitness/            the server: engine/ (clock, rules, trust, evidence checks), security/, evidence_log, playbook, alerts, api, static/ dashboard
hydrowitness/sim/        simulated plant and node, drills, V1 and V2 validation scripts
firmware/witness_node/   Arduino sketch plus portable C++ core; firmware/host_tests/ builds and tests it on a computer
training/                node_reference.py (the one definition of what the node computes), train_classifier.py (the V3 analysis), synth.py
tests/                   319 tests
results/                 saved validation output
docs/                    INTERFACES.md, WIRING.md, HARDWARE_STAGE.md
```

## Limits you should state out loud

* Synthetic signals and a simulated plant prove the software behaves as designed; they say nothing about how a
  real pump sounds. That is the next stage, and a FAIL there is a legitimate finding.
* A careful forgery of every SCADA value is caught by the physical witnesses, but the system only calls it red
  with a human in the loop. A hidden valve closure is amber, never red.
* The clock's worst case assumes demand stays under the configured peak.
* Alerts go only to a private, token-protected channel; there is no public alerting path by design.
* Not included: a SCADA adapter for any specific product, direct-pumping (no storage tank) sites, a node that
  buffers events while offline. See `docs/HARDWARE_STAGE.md`.

This is a student prototype for a competition, not a certified safety system. Do not rely on it as the only
protection for a real water system.
