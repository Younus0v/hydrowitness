# Threat model

A safety net that can be turned against its owner is worse than no net. This page lists who might attack HydroWitness or the plant it watches, what we built against each, what is **not** covered, and which test or result backs each line. Items marked *not done* are limits, not secrets.

## What is being protected, and the one-way rule

HydroWitness is **read-only**: it never writes to the plant (no setpoints, no pump commands). The worst thing it can do by being wrong is show a wrong light or a wrong number to a person. So the threats that matter most are those that make it **look healthy when the plant is not** (a false green, a countdown that is too long) and those that **stop people from seeing it** (denial).

## Assets

| asset | why it matters |
|---|---|
| The trust light and the Time-to-Trouble clock | A false green or an optimistic clock could delay a human response |
| Site profile (elevations, pumps, demand, setup allowance) | A wrong number here shifts the clock; see `results/sensitivity.md` |
| Witness Node secrets (one per node) | Whoever holds one can speak as that node |
| Dashboard token | Gives read access and the ability to record operator readings |
| Evidence log | The record of what was seen and when, used after an incident |
| Private alert channel | Where warnings go |

## Attackers and what we did

Legend for status: **built and tested** (a test in `tests/` exercises it), **built, not independently reviewed**, **not done**.

### 1. An attacker who controls SCADA or the PLC (the main case)

They can freeze the screen, replay old values, force pumps off and make the display say they are on, hide an alarm, or cut remote access.

| attack | defence | status |
|---|---|---|
| Frozen screen | Data that stops changing, or stops arriving, is itself evidence; trust cannot be green once SCADA is silent beyond the stale time | built and tested (property test `test_silent_scada_is_never_green`, drills) |
| Replayed old data | Consistency rules compare the screen with independent witnesses and with physics; replay is caught in all 20 of 20 simulated runs | built; simulated only (`results/evaluation.md`) |
| Pumps forced off behind a careful forgery of every SCADA value | Witness Nodes (vibration and sound) disagree with the screen: amber within 30 s in simulation, red only when a person looks | built; simulated only; **real nodes untested on a real pump** |
| Valve closed out of sight | Customers' reports raise amber; by design they cannot raise red alone | built; amber only, never red (stated in README) |
| Attacker forges operator readings through SCADA | Operator readings enter only through the HydroWitness dashboard, not through SCADA | built |

**What we cannot claim:** if the attacker controls SCADA *and* the Witness Nodes *and* the people, there is nothing independent left. HydroWitness raises the cost of an attack; it does not make one impossible.

### 2. A network attacker against the HydroWitness server

| attack | defence | status |
|---|---|---|
| Reading or using the dashboard without the token | Bearer token on every dashboard and control route; comparison is constant-time (`secrets.compare_digest`) | built and tested |
| Guessing the token | The one-click starter makes a random token on the person's own computer; the server listens on 127.0.0.1 by default | built; Windows starter **not yet run on a Windows machine** (README) |
| Sending malformed or hostile requests | Input schemas with bounds; garbage bodies are refused with a 4xx; the server's own error text never echoes the hostile input | built and tested by property-based fuzzing (`tests/test_properties.py`); fuzzing found two real defects, both fixed |
| Flooding the server | Measured under 20x and 100x the realistic message rate on one machine (`results/soak.md`, once the soak test has been run for the record) | measured on a development computer only; **no rate limiting or firewalling is built in**; put it behind the plant's own network controls |
| Reading traffic on the wire | The server speaks plain HTTP on localhost. For anything beyond localhost, put it behind TLS (a reverse proxy) | **not built in**; the node firmware has a TLS option that has been compile-tested only |
| Known vulnerable dependencies | `pip-audit` run on the pinned requirements found none at the time of the run; static analysis (bandit, ruff) found no high-severity issue | checked once; CI re-runs the tests, not the audit. Re-run the audit before each release |

### 3. An attacker against the Witness Node messages

| attack | defence | status |
|---|---|---|
| Forged message | HMAC-SHA256 with a per-device secret over a canonical string; the check is constant-time | built and tested |
| Replayed message | `(boot count, sequence)` must be strictly newer than the last accepted; a stale timestamp (over 60 s from server time) is refused | built and tested |
| Unknown node | Rejected and remembered as evidence | built and tested |
| Silencing a node | A node that goes quiet for more than three heartbeats makes trust unable to be green, and the gap is logged | built and tested (jammed-witness runs: all warned) |
| Stealing a secret from a node | The secret sits in the ESP32's flash. **Flash encryption and secure boot are not enabled.** Someone with physical access and a cable could read it | **not done**; mitigated by one secret per node (revoking one does not reveal the others) |
| Replacing the node with a spoof that plays recorded pump sounds | A recording near the microphone would fool the sound channel; the vibration channel on the same box makes it harder; neither is tested against a determined attacker | **not tested** |

### 4. Tampering with the record

The evidence log is hash-chained (each entry's hash covers the previous), with periodic anchors, and `/api/log/verify` checks the whole chain. This detects editing or deleting an old entry **if someone has an earlier copy of the head hash**. An attacker with write access to the log file and no earlier copy can rewrite the whole chain. Anchoring the head hash somewhere the attacker cannot reach (a printout, a second computer, the operator's phone) is the fix, and the software supports it through an anchor sink; the choice of where is the utility's. Verified by tests and by the soak run (the log still verified after load).

### 5. Mistakes, not attacks (often the bigger risk)

| mistake | effect | defence |
|---|---|---|
| A wrong elevation, wrong pressure requirement or wrong friction margin typed at setup | Countdown off by about 96 minutes per foot (or 2.31 ft per psi) in the demo case; with realistic survey errors about 10% of sites would start with an optimistic worst case (`results/sensitivity.md`) | An optional **setup allowance** (`setup_uncertainty_ft`) that shortens the worst case; a recommendation to re-check every elevation from a second source; the dashboard prints its assumptions |
| Real demand far above the configured peak (main break, fire flow) | Clock too optimistic: 8 of 40 stressed EPANET systems | An operator's tank reading re-anchors the clock; the screen states the demand it assumes; stated as a limit |
| Person ignores the screen because of false alarms | System stops being used | False-alarm study (`results/long_normal.md`) and drills; the real rate on a real plant is **unknown** |
| Person trusts the screen too much | Delayed manual response | The screen always shows how old the reading is and what is assumed; the Lockout Playbook tells people to look at the real tank |

## Not in scope

HydroWitness does not protect SCADA itself, does not monitor water chemistry (the Oldsmar case), does not stop an attacker, and does not replace the eight actions in EPA's May 2024 enforcement alert. It is a second source of truth for the day the first one is wrong.

## What an outside reviewer should check first

1. The setup allowance default (0) and whether a deployment should require a nonzero value.
2. Whether the node secret handling is acceptable without flash encryption for the intended sites.
3. Whether the anchor of the evidence log is placed somewhere out of an attacker's reach.
4. The assumption that operator readings entered through the dashboard are independent of SCADA.
