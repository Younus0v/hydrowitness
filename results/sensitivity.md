# Sensitivity of the Time-to-Trouble clock

This answers two questions: how far off is the countdown when one setup number is wrong, and how often is it too optimistic when every setup number carries a plausible survey error. It uses the real engine. The error sizes are our assumptions, not measurements. Nothing here is a field result.

**Reference site:** the demo plant, tank read at 18 ft, 5 minutes ago, 06:00 local. The lowest tank level that still gives the farthest customer 20 psi is 11.2 ft, so only 6.8 ft of water is usable (28,788 gal per foot). Displayed worst case 590 min, expected case 1682 min.

## 1. One wrong number at a time

Positive 'displayed minus true' means the screen promised more time than the site had (the dangerous direction).

| input | if the real value is | true worst case (min) | displayed minus true (min) | direction |
|---|---|---|---|---|
| tank level reading | -1.0 ft from what was read | 494 | +96 | optimistic |
| tank level reading | -0.5 ft from what was read | 542 | +48 | optimistic |
| tank level reading | +0.5 ft from what was read | 638 | -48 | pessimistic |
| tank level reading | +1.0 ft from what was read | 686 | -96 | pessimistic |
| tank diameter | -5% | 532 | +58 | optimistic |
| tank diameter | -2% | 566 | +24 | optimistic |
| tank diameter | +2% | 614 | -24 | pessimistic |
| tank diameter | +5% | 651 | -61 | pessimistic |
| critical-point elevation | -10 ft from what was typed | 1550 | -960 | pessimistic |
| critical-point elevation | -5 ft from what was typed | 1070 | -480 | pessimistic |
| critical-point elevation | -2 ft from what was typed | 782 | -192 | pessimistic |
| critical-point elevation | +2 ft from what was typed | 398 | +192 | optimistic |
| critical-point elevation | +5 ft from what was typed | 110 | +480 | optimistic |
| critical-point elevation | +10 ft from what was typed | 0 | +590 | optimistic |
| tank floor elevation | -3 ft | 302 | +288 | optimistic |
| tank floor elevation | -1 ft | 494 | +96 | optimistic |
| tank floor elevation | +1 ft | 686 | -96 | pessimistic |
| tank floor elevation | +3 ft | 878 | -288 | pessimistic |
| friction (headloss) margin | -50% | 830 | -240 | pessimistic |
| friction (headloss) margin | -30% | 734 | -144 | pessimistic |
| friction (headloss) margin | +30% | 446 | +144 | optimistic |
| friction (headloss) margin | +50% | 350 | +240 | optimistic |
| minimum pressure the customers need | +2 psi (a higher line) | 147 | +443 | optimistic |
| minimum pressure the customers need | +5 psi (a higher line) | 0 | +590 | optimistic |
| real peak demand above the configured peak | +25% | 471 | +119 | optimistic |
| real peak demand above the configured peak | +50% | 392 | +198 | optimistic |
| real peak demand above the configured peak | +100% | 292 | +298 | optimistic |

**What this says.** Every foot of error in an elevation (or 2.31 ft of pressure line per psi) moves the worst case by about 96 minutes, which is 16% of the 590 displayed, because only 6.8 ft of water is usable. The inputs that matter most are the ones a person types once and nobody checks again: the farthest customer's elevation, the tank floor elevation, the pressure the customers need and the friction margin. That is why the setup wizard must ask for them carefully, why the dashboard states its assumptions on screen, and why we recommend a second-source check of every elevation before going live.

## 2. How old the tank reading is

| reading age | worst case (min) | level uncertainty subtracted (ft) | label |
|---|---|---|---|
| 5 min | 590 | 0.6 | fresh |
| 15 min | 561 | 0.8 | fresh |
| 1 h | 429 | 1.7 | aging |
| 3 h | 79 | 4.1 | old |
| 12 h | 0 | 14.9 | old |

The worst case already gives up time as a reading ages. That is deliberate: the screen should lean toward 'less time than you think'.

## 3. Crediting a pump

| case | worst case (min) | true worst case (min) | displayed minus true (min) |
|---|---|---|---|
| no pump credited | 590 |  |  |
| P1 credited at 0.8 x rated (200 gpm) | 1780 |  |  |
| P1 credited at 200 gpm; the pump delivers its full rated flow (250 gpm) | 1780 | 3564.8 | -1784.9 |
| P1 credited at 200 gpm; a worn pump delivers 75% of its rated flow (188 gpm) | 1780 | 1581.6 | 198.3 |
| P1 credited at 200 gpm; a worn pump delivers 50% of its rated flow (125 gpm) | 1780 | 1014.9 | 764.9 |

Crediting inflow is the one place the countdown can become optimistic by design, which is why it is credited only when a witness shows water moving, at 0.8 of rated flow.

## 4. Every setup number wrong at once (Monte Carlo)

4000 random draws per row, seed 20261114. Error sizes (standard deviations): level reading 0.3 ft, critical-point elevation 2.0 ft, tank floor elevation 1.0 ft, tank diameter 2%, friction margin 30% (lognormal). Truth is the closed-form time to the pressure line at a constant true draw.

| case | share of draws where the screen was too optimistic | when it was, median / 90th percentile too long (min) |
|---|---|---|
| typical setup errors; true draw 50-100% of the configured peak (the case the clock is built for) | 10.0% (95% CI 9.1% to 10.9%) | 136 / 385 |
| survey-grade setup (a quarter of the errors); draw 50-100% of peak | 0.7% (95% CI 0.5% to 1.0%) | 29 / 84 |
| typical setup errors, with a 1 ft setup allowance; draw 50-100% of peak | 6.3% (95% CI 5.6% to 7.2%) | 111 / 342 |
| typical setup errors, with a 2 ft setup allowance; draw 50-100% of peak | 3.5% (95% CI 3.0% to 4.1%) | 104 / 349 |
| typical setup errors, with a 4 ft setup allowance; draw 50-100% of peak | 1.0% (95% CI 0.7% to 1.4%) | 79 / 206 |
| half the setup errors; draw 50-100% of peak | 3.4% (95% CI 2.8% to 4.0%) | 59 / 174 |
| double the setup errors; draw 50-100% of peak | 24.8% (95% CI 23.4% to 26.1%) | 334 / 590 |
| typical setup errors; draw exactly at the configured peak (a boundary case) | 43.0% (95% CI 41.5% to 44.6%) | 164 / 433 |
| typical setup errors; draw 50-150% of peak (demand can exceed the peak) | 45.3% (95% CI 43.7% to 46.9%) | 302 / 590 |
| no setup errors at all; draw 50-150% of peak (the demand ceiling alone) | 44.2% (95% CI 42.6% to 45.7%) | 278 / 472 |

**The setup allowance.** Setup has an optional `setup_uncertainty_ft` setting (default 0, so nothing changes unless a site sets it). It subtracts that many feet from the worst case. With typical survey errors it brings the optimistic share from 10% down to 6.3% at 1 ft, 3.5% at 2 ft and 1.0% at 4 ft. The price is a shorter countdown: about 96 minutes per foot on this site. A site that has not had its elevations checked twice should set it.

**What this says.** In the case the clock is built for (true draw at or below the configured peak) typical survey errors alone leave the displayed worst case too optimistic in about 10% of sites, 3% if the setup numbers are twice as accurate, 1% if four times as accurate, and 25% if twice as sloppy. So the safety of the countdown depends on how carefully the site is surveyed. When the true draw can exceed the configured peak, the share rises to about 45%, and the demand ceiling alone accounts for 44%. The row at exactly the peak is a boundary case, not a typical day: no tank drains at its absolute peak for hours. These are the same limits we report as the failed demand-stress family in validation V1, and why the screen says to re-read the tank when something unusual happens.

**What would make it stronger:** a setup check that compares the typed elevations with a second source, a stated survey accuracy for each input, and a field comparison of the countdown against a real tank drawdown.
