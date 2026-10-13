"""A small tank-fed water system with attack injection (build stage 3).

TankSystem holds the PHYSICAL truth. ScadaView is the screen layer: it turns the truth into
what SCADA reports, and can be put in attack modes (frozen, replay, spoof, dark) so we can
test what HydroWitness does when the screen lies.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Optional

import numpy as np

from ..engine.time_to_trouble import typical_demand
from ..schemas import GAL_PER_FT3, PSI_TO_FT, Command, ScadaSnapshot, SiteProfile

DEFAULT_CONTROL = {          # PLC auto-control: start at or below, stop at or above (ft)
    "P1": (20.0, 24.5),
    "P2": (19.0, 23.5),
}


class TankSystem:
    def __init__(self, profile: SiteProfile, *, seed: int = 0, t0: float = 1_800_000_000.0 - 7 * 3600,
                 dt: float = 5.0, level0: float = 22.0, initial_running=("P2",),
                 control: Optional[dict] = None):
        self.p = profile
        self.rng = np.random.default_rng(seed)
        self.t = t0                   # default start is 01:00 local (UTC) in the demo profile
        self.dt = dt
        self.level_ft = level0
        self.running = {pump.id: pump.id in initial_running for pump in profile.pumps}
        self.forced: dict[str, Optional[bool]] = {pump.id: None for pump in profile.pumps}
        self.control = control or DEFAULT_CONTROL
        self.demand_scale = 1.0
        self._x = 0.0
        self.demand_gpm = typical_demand(profile, t0)
        self.cmd_log: list[Command] = []
        self.power_ok = True

    # ---- attack / physical interventions
    def force_pump(self, pump_id: str, state: Optional[bool]) -> None:
        """Physically force a pump on/off (None gives control back to the PLC logic)."""
        self.forced[pump_id] = state

    # ---- stepping
    def _autocontrol(self) -> None:
        for pump in self.p.pumps:
            pid = pump.id
            lo, hi = self.control.get(pid, (19.0, 23.5))
            want = self.running[pid]
            if self.forced[pid] is not None:
                want = self.forced[pid]
            elif not self.power_ok:
                want = False
            elif self.running[pid] and self.level_ft >= hi:
                want = False
            elif not self.running[pid] and self.level_ft <= lo:
                want = True
            if want != self.running[pid]:
                self.running[pid] = want
                if self.forced[pid] is None and self.power_ok:
                    self.cmd_log.append(Command(t=self.t, pump_id=pid, action="START" if want else "STOP"))

    def step(self) -> None:
        self.t += self.dt
        phi = math.exp(-self.dt / 900.0)
        self._x = phi * self._x + self.rng.normal(0, 0.012)
        self.demand_gpm = max(5.0, typical_demand(self.p, self.t) * (1.0 + self._x) * self.demand_scale)
        self._autocontrol()
        inflow = sum(pp.rated_gpm * 0.97 for pp in self.p.pumps if self.running[pp.id])
        gpf = self.p.tank_area_sqft * GAL_PER_FT3
        self.level_ft += (inflow - self.demand_gpm) * (self.dt / 60.0) / gpf
        self.level_ft = float(np.clip(self.level_ft, 0.0, self.p.tank_height_ft or 1e9))
        self.cmd_log = [c for c in self.cmd_log if self.t - c.t < 900]

    def true_inflow_gpm(self) -> dict[str, float]:
        return {pp.id: (pp.rated_gpm * 0.97 if self.running[pp.id] else 0.0) for pp in self.p.pumps}

    def true_pressure_psi(self) -> float:
        tap = self.p.pressure_tap_elev_ft if self.p.pressure_tap_elev_ft is not None else self.p.critical_point_elev_ft
        return max(0.0, (self.p.tank_floor_elev_ft + self.level_ft - tap) / PSI_TO_FT)

    def true_critical_psi(self, demand_gpm: Optional[float] = None) -> float:
        """Pressure at the critical customer. Real headloss is below the typed margin (the margin is conservative)."""
        q = self.demand_gpm if demand_gpm is None else demand_gpm
        loss = 0.8 * self.p.headloss_margin_ft * (q / self.p.peak_demand_gpm) ** 2
        return (self.p.tank_floor_elev_ft + self.level_ft - self.p.critical_point_elev_ft - loss) / PSI_TO_FT


class ScadaView:
    """What the operator's screen shows."""

    def __init__(self, system: TankSystem, *, seed: int = 1, level_noise: float = 0.015):
        self.sys = system
        self.rng = np.random.default_rng(seed)
        self.level_noise = level_noise
        self.mode = "normal"
        self.history: deque[tuple[float, ScadaSnapshot]] = deque()
        self._frozen: Optional[ScadaSnapshot] = None
        self._replay_offset = 1800.0
        self._spoof: dict = {}

    # ---- modes
    def freeze(self) -> None:
        self.mode = "frozen"
        self._frozen = self.history[-1][1] if self.history else None

    def dark(self) -> None:
        self.mode = "dark"

    def replay(self, offset_s: float = 1800.0) -> None:
        self.mode, self._replay_offset = "replay", offset_s

    def spoof(self, claim: dict[str, bool], level_ft: Optional[float] = None) -> None:
        """A careful forgery: self-consistent, noisy, plausible. Only outside witnesses can catch it."""
        recent = [s for _, s in list(self.history)[-12:]]          # a careful forger copies a smoothed value,
        lv = [s.tank_level_ft for s in recent if s.tank_level_ft is not None]       # not one noisy sample
        dm = [s.demand_gpm for s in recent if s.demand_gpm]
        self.mode = "spoof"
        self._spoof = {"claim": dict(claim),
                       "level": level_ft if level_ft is not None else (float(np.mean(lv)) if lv else self.sys.level_ft),
                       "demand": float(np.mean(dm)) if dm else self.sys.demand_gpm}

    def normal(self) -> None:
        self.mode = "normal"

    # ---- snapshots
    def _honest(self) -> ScadaSnapshot:
        s = self.sys
        flows = {pid: (max(0.0, f + self.rng.normal(0, 1.5)) if s.running[pid] else 0.0)
                 for pid, f in s.true_inflow_gpm().items()}
        return ScadaSnapshot(
            t=s.t,
            tank_level_ft=round(s.level_ft + self.rng.normal(0, self.level_noise), 2),
            pump_status={pid: bool(v) for pid, v in s.running.items()},
            pump_flow_gpm={pid: round(v, 1) for pid, v in flows.items()},
            demand_gpm=round(s.demand_gpm * (1 + self.rng.normal(0, 0.01)), 1),
            pressure_psi=round(s.true_pressure_psi() + self.rng.normal(0, 0.3), 1),
            commands=list(s.cmd_log))

    def snapshot(self) -> Optional[ScadaSnapshot]:
        s = self.sys
        honest = self._honest()
        if self.mode == "normal":
            self.history.append((s.t, honest))
            while self.history and s.t - self.history[0][0] > 86400 + 60:
                self.history.popleft()
            return honest
        if self.mode == "dark":
            return None
        if self.mode == "frozen" and self._frozen is not None:
            return self._frozen.model_copy(update={"t": s.t})          # identical values, new timestamp
        if self.mode == "replay":
            target = s.t - self._replay_offset
            past = min(self.history, key=lambda x: abs(x[0] - target))[1] if self.history else honest
            return past.model_copy(update={"t": s.t})
        if self.mode == "spoof":
            sp = self._spoof
            dem = round(sp["demand"] * (1 + self.rng.normal(0, 0.012)), 1)
            claim = sp["claim"]
            n_run = max(1, sum(1 for v in claim.values() if v))
            flows = {pid: (round(max(1.0, dem / n_run + self.rng.normal(0, 1.5)), 1) if claim.get(pid) else 0.0)
                     for pid in claim}
            level = round(sp["level"] + self.rng.normal(0, self.level_noise), 2)
            tap = s.p.pressure_tap_elev_ft or s.p.critical_point_elev_ft
            press = round((s.p.tank_floor_elev_ft + sp["level"] - tap) / PSI_TO_FT + self.rng.normal(0, 0.3), 1)
            return ScadaSnapshot(t=s.t, tank_level_ft=level, pump_status=dict(claim), pump_flow_gpm=flows,
                                 demand_gpm=dem, pressure_psi=press, commands=[])
        return honest
