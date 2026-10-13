# V1: Time-to-Trouble clock against EPANET hydraulics

310 randomized systems, seed 7. EPANET via WNTR, Hazen-Williams, 5-minute steps, 48-hour horizon.

Unsafe = the displayed worst case was later than the true time to 20 psi by more than one step (5 min), or the clock said "not falling" when it fell below 20 psi. Expected-case error is (displayed - true) / true: negative means the clock was more pessimistic than reality; positive means optimistic. Scored only where the true time is between 30 minutes and the 48-hour horizon.

| family | n | unsafe | worst late by (max) | median warning ahead of truth | expected: median error | median abs error | p90 abs error | optimistic by >10% |
|---|---|---|---|---|---|---|---|---|
| model_check | 30 | 0 (0.0%) | 0 min | 16.0 h | +0% | 0% | 1% | 0% |
| nominal_off | 100 | 0 (0.0%) | 0 min | 14.9 h | -0% | 8% | 25% | 26% |
| nominal_pump | 70 | 0 (0.0%) | 0 min | 38.1 h | - | - | - | - |
| stress_margin | 40 | 0 (0.0%) | 0 min | 13.2 h | +0% | 7% | 46% | 28% |
| stress_demand | 40 | 8 (20.0%) | 231 min | 3.6 h | +89% | 89% | 344% | 100% |
| stress_pump | 30 | 0 (0.0%) | 0 min | 36.9 h | - | - | - | - |
