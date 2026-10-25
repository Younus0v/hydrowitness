"""What the simulated plant is really doing, worked out from the plant itself and never from what HydroWitness believes.

Two look-aheads on a copy of the simulated plant. Nothing here is available on a real site; it exists so the web demo
and the evaluation can say how far HydroWitness's answers were from the truth.

    minutes_to_line()            the plant carries on as it is (its own control logic runs, forced pumps stay forced)
    minutes_to_line_no_inflow()  every pump stops now. This is the case HydroWitness's countdown assumes when no person
                                 has confirmed that water is moving, so it is the fair truth to score the countdown against.
"""
from __future__ import annotations

import copy
from typing import Optional

from .tank_system import TankSystem

TRUTH_STEP_S = 300.0                 # the look-ahead steps this coarsely; it only has to say roughly how long
TRUTH_HORIZON_H = 24.0


def _clone(sys_: TankSystem) -> TankSystem:
    sim = copy.copy(sys_)
    sim.rng, sim.running, sim.forced, sim.cmd_log = copy.deepcopy(sys_.rng), dict(sys_.running), dict(sys_.forced), list(sys_.cmd_log)
    sim.dt = TRUTH_STEP_S
    return sim


def _run(sim: TankSystem, line_psi: float, horizon_h: float) -> Optional[float]:
    if sim.true_critical_psi() < line_psi:
        return 0.0
    for i in range(int(horizon_h * 3600 / TRUTH_STEP_S)):
        sim.step()
        if sim.true_critical_psi() < line_psi:
            return float(round((i + 1) * TRUTH_STEP_S / 60.0))
    return None


def minutes_to_line(sys_: TankSystem, line_psi: float, horizon_h: float = TRUTH_HORIZON_H) -> Optional[float]:
    """Simulated minutes until the farthest customer falls below the line if nothing changes. None: not within the horizon."""
    return _run(_clone(sys_), line_psi, horizon_h)


def minutes_to_line_no_inflow(sys_: TankSystem, line_psi: float, horizon_h: float = TRUTH_HORIZON_H) -> Optional[float]:
    """The same, with every pump stopped from now on (the plant's real demand, no inflow at all)."""
    sim = _clone(sys_)
    sim.forced = {pid: False for pid in sim.forced}
    for pid in sim.running:
        sim.running[pid] = False
    return _run(sim, line_psi, horizon_h)
