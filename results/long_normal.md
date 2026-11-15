# Long false-alarm study (simulated healthy plants)

80 runs of 5 simulated days each, 400 site-days in all, on seeds 1000 and up (the evaluation uses seeds 1 to 10, the drills 1 to 5, so none of these were seen during tuning). Half the runs use the 70 ft demo tank, half a 30 ft tank, which cycles its pumps far more often. Honest screen, two Witness Nodes, full system. Every warning here is a false one.

| | count | per site-day | 95% upper bound per site-day |
|---|---|---|---|
| HydroWitness false amber | 0 | 0.0000 | 0.0092 |
| HydroWitness false red | 0 | 0.0000 | 0.0092 |
| Alarm panel, false alarms | 0 | 0.0000 | |
| Alarm panel with flat-line rule, false alarms | 0 | 0.0000 | |

Pump starts and stops the software had to follow without crying wolf: 1267.

How to read this: the upper bound is exact (Poisson, two-sided 95%). With zero events it equals 3.69 divided by the site-days. So 400 clean site-days cannot rule out a false amber about once every 108 site-days, and cannot rule out a false red about once every 108. More clean days tighten the bound; they never make it zero.

What this does not show: a real plant has noise, drift and equipment this simulation does not have. These are simulated days.
