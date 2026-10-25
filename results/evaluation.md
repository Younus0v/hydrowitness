# Evaluation: HydroWitness against a plain alarm panel (simulated plant, simulated nodes)

Everything below is a simulation. The plant, the screen's lies and the Witness Nodes are simulated, the nodes' error rates are assumptions, and the firmware has so far only booted on a real board, not watched a real pump. **This evaluation uses no real measurements.** Same seeds, same numbers: regenerate with `python -m hydrowitness.sim.evaluation`.

Seeds 1 to 10, on two tank sizes (the demo tank and a small one that holds hours of water instead of days), 60 minutes allowed after the start of each attack, software 0.1.0.

## Did anything notice? (detected runs, with median / max delay after the attack began)

| scenario | operator walks to the pump | HydroWitness warned | HydroWitness red | Alarm panel | Alarm panel + flat-line check |
|---|---|---|---|---|---|
| Frozen screen (PLC lockout) | no | 20/20 (65 s / 65 s) | 20/20 (95 s / 95 s) | 0/20 | 20/20 (300 s / 300 s) |
| Frozen screen (PLC lockout) | yes | 20/20 (65 s / 65 s) | 20/20 (95 s / 95 s) | 0/20 | 20/20 (300 s / 300 s) |
| Pumps forced off behind a careful forgery | no | 20/20 (20 s / 30 s) | 0/20 | 0/20 | 0/20 |
| Pumps forced off behind a careful forgery | yes | 20/20 (20 s / 30 s) | 20/20 (120 s / 120 s) | 0/20 | 0/20 |
| Alarms off, remote access cut, pump cycles changed | no | 20/20 (50 s / 50 s) | 20/20 (80 s / 80 s) | 20/20 (125 s / 125 s) | 20/20 (125 s / 125 s) |
| Alarms off, remote access cut, pump cycles changed | yes | 20/20 (50 s / 50 s) | 20/20 (80 s / 80 s) | 20/20 (125 s / 125 s) | 20/20 (125 s / 125 s) |
| Replayed data | no | 20/20 (50 s / 50 s) | 20/20 (50 s / 50 s) | 0/20 | 20/20 (2100 s / 2100 s) |
| Replayed data | yes | 20/20 (50 s / 50 s) | 20/20 (50 s / 50 s) | 0/20 | 20/20 (2100 s / 2100 s) |
| Valve closed out of sight | no | 20/20 (180 s / 180 s) | 0/20 | 0/20 | 0/20 |
| Valve closed out of sight | yes | 20/20 (180 s / 180 s) | 0/20 | 0/20 | 0/20 |
| Power outage that looks like an attack | no | 20/20 (20 s / 30 s) | 20/20 (125 s / 125 s) | 20/20 (125 s / 125 s) | 20/20 (125 s / 125 s) |
| Power outage that looks like an attack | yes | 20/20 (20 s / 30 s) | 20/20 (125 s / 125 s) | 20/20 (125 s / 125 s) | 20/20 (125 s / 125 s) |

Across the 120 runs where nobody walks to the pump, HydroWitness raised nothing in 0, the alarm panel in 80, and the alarm panel with the flat-line check in 40.

The alarm panel's alarms in these runs were: no_data.

Reading the table:
- On the replay, the flat-line check fires at 2100 s only because the simulated attacker's 30-minute recording runs out and the screen then freezes. A replay that kept going would not trigger it.
- On the closed valve, HydroWitness's warning (180 s) comes only from the four scripted customer calls. The alarm panel has no way to receive them, though a dispatcher would hear them anyway. Customer calls alone can never turn the light red, so it stays amber, by design.
- On the careful forgery, HydroWitness warns because a Witness Node disagrees with the screen. It reaches red only when a person has looked at the pump, which is why the table has an operator column.

## False alarms on a healthy plant

24 simulated site-days with two nodes, an honest screen and 67 real pump starts and stops (two tank sizes).

| | alarms or warnings | per site-day |
|---|---|---|
| HydroWitness (amber or red) | 0 (0 amber, 0 red) | 0.0 |
| Alarm panel | 0 | 0.0 |
| Alarm panel + flat-line check | 0 | 0.0 |

## How close was the countdown?

HydroWitness's expected-case countdown against the simulated plant's own answer with every pump stopped (the case the countdown assumes when no one has confirmed that water is moving), looked up to 72 hours ahead. Within 15 minutes or 10% (whichever is larger) counts as close. **Early** means HydroWitness said trouble would come sooner than it did, which costs a little caution. **Late** means HydroWitness said trouble would come later than it did, the dangerous direction. The alarm panel gives no countdown, so it is not scored. A closed valve is **not modelled**: the simulation does not work out the pressure beyond it, so there is no true answer to score against. The simulated plant's real friction loss is 80% of the margin typed into the profile, so a countdown built on the typed margin comes out early by design.

| scenario | operator | checkpoints | unavailable | not modelled | scored | worst case held | early | close | late | median error (h) | worst late (h) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Frozen screen (PLC lockout) | no | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.7 | 0.0 |
| Frozen screen (PLC lockout) | yes | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.7 | 0.0 |
| Pumps forced off behind a careful forgery | no | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.7 | 0.0 |
| Pumps forced off behind a careful forgery | yes | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.7 | 0.0 |
| Alarms off, remote access cut, pump cycles changed | no | 240 | 0 | 0 | 240 | 240/240 | 50 | 190 | 0 | -1.4 | 0.0 |
| Alarms off, remote access cut, pump cycles changed | yes | 240 | 0 | 0 | 240 | 240/240 | 35 | 205 | 0 | -1.2 | 0.0 |
| Replayed data | no | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.9 | 0.0 |
| Replayed data | yes | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.9 | 0.0 |
| Valve closed out of sight | no | 240 | 0 | 240 | 0 | 0/0 | 0 | 0 | 0 | - | - |
| Valve closed out of sight | yes | 240 | 0 | 240 | 0 | 0/0 | 0 | 0 | 0 | - | - |
| Power outage that looks like an attack | no | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.7 | 0.0 |
| Power outage that looks like an attack | yes | 240 | 0 | 0 | 240 | 240/240 | 0 | 240 | 0 | -0.7 | 0.0 |
| **All attack runs** | | 2880 | 0 | 480 | 2400 | 2400/2400 | 85 | 2315 | 0 | -1.2 | 0.0 |
| **Attack runs, demo tank** | | 1440 | 0 | 240 | 1200 | 1200/1200 | 0 | 1200 | 0 | -1.6 | 0.0 |
| **Attack runs, small tank** | | 1440 | 0 | 240 | 1200 | 1200/1200 | 85 | 1115 | 0 | -0.3 | 0.0 |
| **Healthy plant, demo tank** | | 144 | 0 | 0 | 144 | 144/144 | 0 | 144 | 0 | -1.0 | 0.0 |
| **Healthy plant, small tank** | | 144 | 0 | 0 | 144 | 144/144 | 0 | 144 | 0 | -0.3 | 0.0 |

## Failure conditions: attacks built to break HydroWitness

| condition | HydroWitness warned | HydroWitness red | Alarm panel | Alarm panel + flat-line check |
|---|---|---|---|---|
| Pump 2 is forced off behind a forged screen and the Witness Node on Pump 2 is jammed at the same moment. Nobody walks to the pump. | 10/10 (30 s / 30 s) | 0/10 | 0/10 | 0/10 |
| The same attack, and both Witness Nodes are jammed. Nobody walks to the pump. | 10/10 (30 s / 30 s) | 0/10 | 0/10 | 0/10 |
| The screen replays old data while Pump 2 is stopped, and both Witness Nodes are jammed. | 10/10 (30 s / 30 s) | 10/10 (200 s / 205 s) | 0/10 | 10/10 (2100 s / 2100 s) |

When the witnesses are jammed, HydroWitness's warning is "a Witness Node went silent", not "the pump is off". It noticed it lost its witnesses, but it cannot tell what the pumps are doing, so it does not reach red unless something else gives the attack away (here, the replayed screen's repeated sequence).

## Settings

```json
{
 "alarm_panel": {
  "low_level_ft": 15.0,
  "low_pressure_psi": 20.0,
  "no_data_after_s": 120.0,
  "flat_line_after_s": 300.0,
  "note": "Both variants believe the screen. The flat-line variant adds one generous rule."
 },
 "countdown_scoring": {
  "truth": "the simulated plant with every pump stopped (the case the no-inflow countdown assumes)",
  "truth_step_s": 300.0,
  "truth_horizon_h": 72.0,
  "close_within_min": 15.0,
  "close_within_share": 0.1,
  "worst_case_slack_min": 5.0,
  "skipped_when": "a pump's flow was credited",
  "not_modelled_when": "a valve is closed: the simulation does not work out the pressure beyond it",
  "built_in_earliness": "the simulated plant's real friction loss is 80% of the headloss margin typed into the profile, so a countdown built on the typed margin comes out early by design"
 },
 "normal": {
  "days_per_run": 2.0,
  "seeds_per_plant": 6,
  "step_s": 10.0,
  "plants": [
   "demo tank (70 ft)",
   "small tank (30 ft)"
  ]
 },
 "witness_node_error_model": {
  "delay_s": [
   1.5,
   3.5
  ],
  "p_acc_err": 0.01,
  "p_mic_err": 0.02,
  "hb_p_unsure_acc": 0.004,
  "hb_p_unsure_mic": 0.01,
  "hb_p_disagree": 0.002
 }
}
```

## Limits

- Simulated plant, simulated screen, simulated Witness Nodes.
- The Witness Node error rates are assumptions, not measurements of a real pump.
- The firmware has booted on a real ESP32 board, but no recording from a real pump is used here: this evaluation uses no real measurements.
- These are results against scripted scenarios, not a forecast for a real water system.
