# Statistics: what each count can and cannot rule out

Every interval below is an exact 95% interval (Clopper-Pearson for proportions, Poisson for rates). They cover **sampling luck inside the simulation only**. They do not cover the gap between a simulated plant and a real one, which is the bigger uncertainty. Re-run with `python3 tools/make_statistics.py`. Nothing here is a field result.

## 1. Time-to-Trouble clock against EPANET (validation V1)

A system counts as unsafe when the displayed worst case promised more time than EPANET says there was (by more than one 5-minute step).

| family | unsafe, with 95% CI |
|---|---|
| model_check | 0 of 30 (0.0%; 95% CI 0.0% to 11.6%) |
| nominal_off | 0 of 100 (0.0%; 95% CI 0.0% to 3.6%) |
| nominal_pump | 0 of 70 (0.0%; 95% CI 0.0% to 5.1%) |
| stress_margin | 0 of 40 (0.0%; 95% CI 0.0% to 8.8%) |
| stress_demand | 8 of 40 (20.0%; 95% CI 9.1% to 35.6%) |
| stress_pump | 0 of 30 (0.0%; 95% CI 0.0% to 11.6%) |
| **inside the clock's assumptions** (model_check, nominal_off, nominal_pump) | **0 of 200 (0.0%; 95% CI 0.0% to 1.8%)** |

Zero failures in the sample does not mean zero in the world: the upper end of the interval is what these systems cannot rule out. The demand-stress family fails by design and is the honest limit: when real demand is far above the configured peak, the countdown is too optimistic.

## 2. Attack detection (simulated plants, no operator walking to the pump)

| scenario | runs | HydroWitness warned | HydroWitness reached red | alarm panel | alarm panel + flat-line rule |
|---|---|---|---|---|---|
| frozen_screen | 20 | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) |
| forced_off_spoofed | 20 | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) |
| colorado_style | 20 | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) |
| replay | 20 | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) |
| hidden_valve_closure | 20 | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) | 0 of 20 (0.0%; 95% CI 0.0% to 16.8%) |
| power_loss_lookalike | 20 | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) | 20 of 20 (100.0%; 95% CI 83.2% to 100.0%) |
| **all six scenarios** | 120 | **120 of 120 (100.0%; 95% CI 97.0% to 100.0%)** | | 40 of 120 (33.3%; 95% CI 25.0% to 42.5%) | 80 of 120 (66.7%; 95% CI 57.5% to 75.0%) |

With 10 to 20 runs per scenario the intervals are wide: 20 of 20 only shows the true rate is probably above 83%. Seeds change the noise and message delays, not the plant or the attack script, so more seeds would add little evidence. More plants, pumps and attack variants would.

Witness-failure conditions (nodes jammed):

| condition | runs | warned | reached red |
|---|---|---|---|
| one_witness_jammed | 10 | 10 of 10 (100.0%; 95% CI 69.2% to 100.0%) | 0 of 10 (0.0%; 95% CI 0.0% to 30.8%) |
| both_witnesses_jammed | 10 | 10 of 10 (100.0%; 95% CI 69.2% to 100.0%) | 0 of 10 (0.0%; 95% CI 0.0% to 30.8%) |
| replay_witnesses_jammed | 10 | 10 of 10 (100.0%; 95% CI 69.2% to 100.0%) | 10 of 10 (100.0%; 95% CI 69.2% to 100.0%) |

## 3. False alarms on healthy simulated plants

| source | site-days | false amber | false red |
|---|---|---|---|
| evaluation: normal operation | 24 | 0 | 0 |
| drills: normal operation | 10 | 0 | 0 |
| long study (seeds 1000+) | 400 | 0 | 0 |
| **total** | **434** | **0** | **0** |

With 0 false amber and 0 false red in 434 simulated site-days, the 95% upper bounds are 0.008 false amber and 0.008 false red per site-day (about one every 118 and 118 site-days). To bound the rate below 0.01 per site-day with no events you need about 369 clean site-days. A real plant will add noise this simulation does not have, so the first real site-days matter more than more simulated ones.

## 4. Countdown never promising more time than the simulated truth

- Checkpoints scored: 2400. Checkpoints where the worst case was later than the truth: 0 of 2400 (0.0%; 95% CI 0.0% to 0.2%).
- Runs with a scored checkpoint: 242. Runs where the bound failed at least once: 0 of 242 (0.0%; 95% CI 0.0% to 1.5%).
- Checkpoints inside one run are strongly correlated, so the checkpoint interval is optimistic. The run-level interval is the one to quote.
