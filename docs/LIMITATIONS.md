# Limitations register

Everything we know HydroWitness does not do, or has not shown, in one place. If a judge, an operator or a reviewer finds something not on this list, add it, and say who found it. Status values: **open** (not done), **mitigated** (reduced, not removed), **by design** (we chose it).

| # | limitation | why it matters | status | where it is shown or measured |
|---|---|---|---|---|
| L1 | **Every performance number is from simulation.** A simulated plant and simulated nodes follow the noise model we wrote. A real plant has noise, drift and equipment we did not model. | The biggest uncertainty in the project. Confidence intervals cover sampling luck inside the simulation only. | open | `results/statistics.md` (header) |
| L2 | **No recorded real-pump test.** One trial on a pump owner's pump is reported by a team member, with no data kept. | We cannot say the Witness Node tells running from stopped on a real pump. | open; protocol written | `docs/PUMP_TEST_PROTOCOL.md`; website wording "reported, unrecorded" |
| L3 | **No real SCADA connection.** Data enters by a documented API, a spreadsheet or typed readings. No adapter for any specific SCADA product. | A pilot needs a feed the utility is willing to give. | open | README "What exists" |
| L4 | **Demand above the configured peak makes the clock optimistic.** 8 of 40 stressed EPANET systems (a main break or fire flow far above the stated peak). | The worst case assumes the peak demand typed at setup. | by design, stated on screen | `results/statistics.md` section 1 |
| L5 | **A wrong number typed at setup shifts the countdown.** About 96 minutes per foot of tank level error in the demo case, about 2.31 ft of head per psi of required pressure. With realistic survey errors about 1 site in 10 starts with an optimistic worst case. | The people most likely to have unchecked elevations are small systems. | mitigated (optional setup allowance, a second-source check recommended); not removed | `results/sensitivity.md`; `docs/INTERFACES.md` |
| L6 | **False-alarm rate on a real plant is unknown.** The simulated study bounds it only for simulated healthy plants. | An alarm that cries wolf gets ignored. | open | `results/long_normal.md`; `results/statistics.md` section 3 |
| L7 | **Hidden valve closures and customer reports can only raise amber, never red.** | By design, so one unverified caller cannot trigger a lockout. It also means a slow, hidden closure needs a person to look. | by design | README; `results/evaluation.md` |
| L8 | **Chemistry is out of scope.** We do not monitor chlorine, fluoride or any dosing. | Oldsmar 2021 was a dosing attack. HydroWitness would not have helped. | by design | `docs/THREAT_MODEL.md`; `docs/RESEARCH_BASIS.md` |
| L9 | **Needs a storage tank and a level source.** Direct-pressure or no-tank systems are not covered. | Limits the market to systems with elevated or ground storage. | by design | README |
| L10 | **Witness Node secrets are not protected against someone with physical access.** No flash encryption or secure boot. | An attacker with the board in hand could read the secret. | open | `docs/FMEA.md` row 15; `docs/THREAT_MODEL.md` |
| L11 | **No on-node buffer.** Events while the server is unreachable are lost. | A node cut off from the server cannot report; the server sees silence and cannot be green. | open | `docs/FMEA.md` row 12 |
| L12 | **No built-in TLS or rate limiting on the server.** Meant for localhost or behind the utility's own network controls. | Exposing it as is to the internet would be a mistake. | open | `docs/THREAT_MODEL.md` |
| L13 | **The Windows one-click starter has not been run on a Windows machine** (to our knowledge when this was written). | A first-time user may hit a problem we have not seen. | open | README |
| L14 | **No independent review.** Nobody outside the team has read the code, the threat model or the FMEA. No licensed operator or engineer has checked the clock's definition. | We may be wrong in ways we cannot see. | open; requests drafted | `docs/industry/OUTREACH.md` |
| L15 | **No operator interviews or letters of support exist yet.** | We cannot say what operators think. | open; kit drafted | `docs/industry/` |
| L16 | **Attack scenarios are ones we wrote.** The simulator plays attack patterns we thought of, with 20 runs each and the same plant and script per scenario. More seeds would add little; more plants and attack variants would. | A smart attacker may do something we did not think of. 100% of our scenarios caught is not 100% of attacks. | open | `results/statistics.md` section 2 |
| L17 | **We have not run HydroWitness on the public BATADAL data.** | The standard benchmark for attacks on water networks. Their data has no independent witnesses, so only part of our method applies. | open | `docs/RESEARCH_BASIS.md` |
| L18 | **Speed numbers are for one development computer.** Soak and load results describe this software on that machine, not on a plant's computer. | Slow hardware may behave differently. | open | `results/soak.md` |
| L19 | **Windows inside one recording are correlated.** The held-out score intervals treat each 5-second window as independent, so they are too narrow. | The count of separate runs, days and pumps is what matters. | mitigated (stated beside every interval) | `docs/PUMP_TEST_PROTOCOL.md`; `results/measurements.md` |
| L20 | **Cost and market numbers are estimates.** No customer has agreed to pay. | Pricing and market claims are hypotheses. | open | `docs/` pricing notes (team internal) |

## How an item leaves this list

An item leaves **open** only when a result exists that anyone can re-run or read: a recorded test, a review letter in the log, a CI run. We move it, we do not delete it, and we keep what it said before.
