"""Validation V1: does the Time-to-Trouble clock agree with real hydraulics?

For hundreds of randomized small water systems we build a pumped-storage network in WNTR (the EPANET
hydraulic solver), let the tank drain under demand the clock does NOT know exactly, and compare:

  true time      minutes from "now" until the pressure at the critical service point first falls below
                 20 psi, as computed by EPANET with Hazen-Williams headloss, pump curves and a tank
  worst case     what the clock displays as the earliest it could happen
  expected case  what the clock displays as the most likely time

What counts as a failure is chosen BEFORE running, in the direction that can hurt people:
  * the worst case is later than the true time by more than one hydraulic time step (5 min), i.e. the
    screen promised more time than there was;
  * the clock says "not falling" when EPANET shows the pressure crossing 20 psi within the horizon.
Accuracy of the expected case is reported but is not a safety failure.

Families
  model_check        pumps off and NOTHING random: demand exactly the typical pattern, margin exactly measured,
                     level read exactly. Isolates the clock's structural error from forecast error.
  nominal_off        pumps off, demand within the configured peak, margin measured at peak demand
  nominal_pump       a witness confirms a pump moving water; the clock credits 0.8 x rated flow
  stress_margin      the configured headloss margin is 30-70% of the real one (a badly set-up site)
  stress_demand      demand 1.3-2.0x the usual day, beyond the configured peak (heat wave, main break, fire)
  stress_pump        a credited pump delivers less than the clock assumes (worn impeller)
The stress families are NOT expected to pass. They exist to show, with numbers, where the clock stops being
trustworthy and what the product must say about it.

Run:  python3 -m hydrowitness.sim.wntr_validation [--n 310] [--seed 7] [--out results]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import warnings
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..engine.time_to_trouble import CreditedFlow, LevelReading, compute_clock, credited_inflow
from ..schemas import Pump, SiteProfile

FT, GPM, PSI_PER_M = 0.3048, 6.30902e-5, 1.42233
HORIZON_H = 48.0
STEP_S = 300
LATE_TOL_MIN = STEP_S / 60.0
MIN_PSI = 20.0

# Typical diurnal shape of a small system, mean 1.0 (morning and evening peaks, quiet night).
_SHAPE_RAW = np.array([0.50, 0.45, 0.42, 0.42, 0.50, 0.75, 1.15, 1.40, 1.30, 1.10, 1.00, 0.95,
                       0.95, 0.90, 0.88, 0.92, 1.05, 1.25, 1.30, 1.15, 0.95, 0.80, 0.65, 0.55])
SHAPE = _SHAPE_RAW / _SHAPE_RAW.mean()

FAMILIES = {"model_check": 30, "nominal_off": 100, "nominal_pump": 70, "stress_margin": 40, "stress_demand": 40, "stress_pump": 30}


@dataclass
class Scenario:
    sid: int
    family: str
    diameter_ft: float
    height_ft: float
    floor_ft: float
    crit_ft: float
    m1: tuple          # (length ft, diameter in, C)
    m2: tuple
    mean_gpm: float
    peak_gpm: float
    margin_cfg_ft: float
    margin_true_ft: float
    age_s: float
    start_hour: int
    level0_ft: float
    level0_read_ft: float
    typical: list
    true_gpm_by_hour: list
    pump_rated_gpm: float
    pump_on_from: str          # "none", "t0", "now"
    pump_head_scale: float


# ------------------------------------------------------------------------------------------ network
def _network(sc: Scenario, duration_s: float):
    import wntr

    wn = wntr.network.WaterNetworkModel()
    t = wn.options.time
    t.duration, t.hydraulic_timestep, t.report_timestep = duration_s, STEP_S, STEP_S
    t.pattern_timestep, t.pattern_start = 3600, 0
    wn.options.hydraulic.headloss = "H-W"
    wn.options.hydraulic.demand_model = "DDA"

    hours = int(math.ceil(duration_s / 3600.0)) + 2
    pattern = [sc.true_gpm_by_hour[(sc.start_hour + k) % 24] for k in range(hours)]
    wn.add_pattern("dem", pattern)                      # multiplier = total demand in gpm; base demands are shares of 1 gpm
    wn.add_junction("N1", base_demand=0.0, elevation=sc.floor_ft * FT)
    wn.add_junction("N2", base_demand=0.4 * GPM, demand_pattern="dem", elevation=(sc.crit_ft + 15) * FT)
    wn.add_junction("NC", base_demand=0.6 * GPM, demand_pattern="dem", elevation=sc.crit_ft * FT)
    wn.add_tank("T1", elevation=sc.floor_ft * FT, init_level=sc.level0_ft * FT, min_level=0.0,
                max_level=sc.height_ft * FT, diameter=sc.diameter_ft * FT)
    wn.add_pipe("PT", "N1", "T1", length=50 * FT, diameter=16 * 0.0254, roughness=130)
    wn.add_pipe("M1", "N1", "N2", length=sc.m1[0] * FT, diameter=sc.m1[1] * 0.0254, roughness=sc.m1[2])
    wn.add_pipe("M2", "N2", "NC", length=sc.m2[0] * FT, diameter=sc.m2[1] * 0.0254, roughness=sc.m2[2])
    if sc.pump_on_from != "none" or sc.family.startswith("nominal_pump") or sc.family == "stress_pump":
        qd = sc.pump_rated_gpm * GPM
        hd = (sc.floor_ft + 0.5 * sc.height_ft + 25.0) * FT * sc.pump_head_scale
        wn.add_curve("pc", "HEAD", [(0.0, 1.25 * hd), (qd, hd), (1.8 * qd, 0.5 * hd)])
        wn.add_reservoir("R0", base_head=0.0)
        wn.add_junction("NP", elevation=0.0)
        wn.add_pipe("P0", "R0", "NP", length=10.0, diameter=0.3, roughness=130)
        pump = wn.add_pump("PU1", "NP", "N1", pump_type="HEAD", pump_parameter="pc")
        pump = wn.get_link("PU1")
        if sc.pump_on_from == "t0":
            pump.initial_status = "Open"
        else:
            from wntr.network.controls import Control, ControlAction, SimTimeCondition

            pump.initial_status = "Closed"
            if sc.pump_on_from == "now":
                wn.add_control("on", Control(SimTimeCondition(wn, "=", sc.age_s), ControlAction(pump, "status", 1)))
    return wn


def _run(sc: Scenario, duration_s: float, tmp: str):
    import wntr

    wn = _network(sc, duration_s)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=os.path.join(tmp, "s"))
    p = res.node["pressure"]["NC"] * PSI_PER_M
    return p.index.values.astype(float), p.values.astype(float)


def steady_margin_ft(sc: Scenario, tmp: str) -> float:
    """Headloss from the tank to the critical point at the configured peak demand, from EPANET (full tank,
    pumps off). This is what a careful operator would enter as the headloss margin."""
    import wntr

    probe = Scenario(**{**sc.__dict__, "pump_on_from": "none", "family": "nominal_off", "age_s": 0.0,
                        "true_gpm_by_hour": [sc.peak_gpm] * 24, "level0_ft": sc.height_ft})
    wn = _network(probe, 3600.0)
    wn.options.time.duration = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=os.path.join(tmp, "m"))
    p_psi = float(res.node["pressure"]["NC"].iloc[0] * PSI_PER_M)
    return (sc.floor_ft + sc.height_ft - sc.crit_ft) - p_psi * 2.31


# ---------------------------------------------------------------------------------------- scenarios
def make_scenario(sid: int, family: str, rng: np.random.Generator, tmp: str) -> Scenario:
    while True:
        diameter = float(rng.uniform(25, 70))
        height = float(rng.uniform(20, 40))
        floor = float(rng.uniform(60, 160))
        mean = float(rng.uniform(40, 400))
        typical = [round(mean * s, 2) for s in SHAPE]
        peak = float(max(typical) * rng.uniform(1.1, 1.5))
        m1 = (float(rng.uniform(500, 4000)), int(rng.choice([6, 8, 10, 12])), float(rng.uniform(90, 140)))
        m2 = (float(rng.uniform(300, 3000)), int(rng.choice([4, 6, 8])), float(rng.uniform(90, 140)))
        proto = Scenario(sid, family, diameter, height, floor, 0.0, m1, m2, mean, peak, 0.0, 0.0, 0.0, 0, 0.0, 0.0, typical,
                         typical, 0.0, "none", 1.0)
        proto.crit_ft = 0.0
        margin = steady_margin_ft(proto, tmp)                     # independent of the critical elevation
        if not (0.0 < margin < 0.6 * (height + 40)):
            continue
        # place the critical point so the full tank gives 28-55 psi there at peak demand
        p_full = float(rng.uniform(28, 55))
        crit = floor + height - margin - p_full * 2.31
        l_min = crit + MIN_PSI * 2.31 + margin - floor
        if l_min < 2.0 or l_min > height - 6.0:
            continue
        break

    ideal = family == "model_check"
    cfg_margin = margin * (1.0 if ideal else rng.uniform(1.0, 1.15) if family != "stress_margin" else rng.uniform(0.3, 0.7))
    l_min_cfg = crit + MIN_PSI * 2.31 + cfg_margin - floor
    level0 = float(rng.uniform(l_min_cfg + 1.5, height - 0.5))
    if family == "stress_margin":
        level0 = float(rng.uniform(max(l_min_cfg, l_min) + 1.5, height - 0.5))
    age = float(rng.uniform(0, 7200))
    start_hour = int(rng.integers(0, 24))

    day = 1.0 if ideal else float(rng.lognormal(0, 0.10))
    if family == "stress_demand":
        day *= float(rng.uniform(1.3, 2.0))
    true = []
    for h in range(24):
        v = typical[h] * day * (1.0 if ideal else float(rng.lognormal(0, 0.05)))
        true.append(v if family == "stress_demand" else min(v, peak))
    pump_rated = float(rng.uniform(0.7, 1.6) * mean)
    pump_on, scale = "none", 1.0
    if family in ("nominal_pump", "stress_pump"):
        pump_on = "now" if rng.random() < 0.5 else "t0"
        scale = float(rng.uniform(0.82, 0.95)) if family == "stress_pump" else 1.0
    read_noise = 0.0 if ideal else float(rng.normal(0, 0.25))
    return Scenario(sid, family, diameter, height, floor, crit, m1, m2, mean, peak, float(cfg_margin), float(margin), age,
                    start_hour, level0, level0 + read_noise, typical, true, pump_rated, pump_on, scale)


def profile_for(sc: Scenario) -> SiteProfile:
    return SiteProfile(site_name=f"V1-{sc.sid}", tank_diameter_ft=sc.diameter_ft, tank_height_ft=sc.height_ft,
                       tank_floor_elev_ft=sc.floor_ft, critical_point_elev_ft=sc.crit_ft, min_pressure_psi=MIN_PSI,
                       headloss_margin_ft=sc.margin_cfg_ft, peak_demand_gpm=sc.peak_gpm,
                       typical_demand_gpm_by_hour=sc.typical, pumps=[Pump(id="P1", rated_gpm=sc.pump_rated_gpm)])


# ------------------------------------------------------------------------------------------ one run
def evaluate(sc: Scenario, tmp: str) -> dict:
    prof = profile_for(sc)
    t0 = 86400.0 * 20 + sc.start_hour * 3600.0
    now = t0 + sc.age_s
    credits = []
    if sc.pump_on_from != "none":
        credits = [CreditedFlow("P1", credited_inflow(prof, "P1"), now - 10.0, "witness: flow confirmed")]
    clock = compute_clock(prof, now, LevelReading(sc.level0_read_ft, t0, "scada"), credits)
    inf = float("inf")
    W = clock.worst_min if clock.worst_min is not None else inf
    E = clock.expected_min if clock.expected_min is not None else inf

    duration = (sc.age_s + HORIZON_H * 3600.0)
    times, psi = _run(sc, duration, tmp)
    after = times >= sc.age_s - 1e-6
    ts, ps = times[after], psi[after]
    T = inf
    if ps[0] < MIN_PSI:
        T = 0.0
    else:
        idx = np.nonzero(ps < MIN_PSI)[0]
        if len(idx):
            k = int(idx[0])
            f = (ps[k - 1] - MIN_PSI) / max(ps[k - 1] - ps[k], 1e-9)
            T = (ts[k - 1] + f * (ts[k] - ts[k - 1]) - sc.age_s) / 60.0
    horizon_min = HORIZON_H * 60.0
    Tc, Wc, Ec = min(T, horizon_min), min(W, horizon_min), min(E, horizon_min)
    late = Wc - Tc
    return {
        "id": sc.sid, "family": sc.family, "age_min": sc.age_s / 60.0, "mean_gpm": sc.mean_gpm, "peak_gpm": sc.peak_gpm,
        "true_demand_peak_gpm": max(sc.true_gpm_by_hour), "margin_cfg_ft": sc.margin_cfg_ft, "margin_true_ft": sc.margin_true_ft,
        "pump": sc.pump_on_from, "pump_head_scale": sc.pump_head_scale,
        "worst_min": None if W == inf else W, "expected_min": None if E == inf else E, "true_min": None if T == inf else T,
        "worst_late_by_min": late, "unsafe": bool(late > LATE_TOL_MIN),
        "false_stable": bool(clock.worst_min is None and T < horizon_min),
        "expected_rel_err": (None if (Tc >= horizon_min or Tc < 30.0) else (Ec - Tc) / Tc),
    }


# ---------------------------------------------------------------------------------------- reporting
def summarize(rows: list[dict]) -> dict:
    out = {}
    for fam in FAMILIES:
        r = [x for x in rows if x["family"] == fam]
        if not r:
            continue
        unsafe = [x for x in r if x["unsafe"] or x["false_stable"]]
        errs = [abs(x["expected_rel_err"]) for x in r if x["expected_rel_err"] is not None]
        signed = [x["expected_rel_err"] for x in r if x["expected_rel_err"] is not None]
        late = [x["worst_late_by_min"] for x in r if x["worst_late_by_min"] > 0]
        # how much earlier than the truth the worst case warns, where either one is inside the horizon
        margins = [-x["worst_late_by_min"] for x in r
                   if (x["true_min"] is not None and x["true_min"] < HORIZON_H * 60)
                   or (x["worst_min"] is not None and x["worst_min"] < HORIZON_H * 60)]
        out[fam] = {
            "scenarios": len(r),
            "unsafe_count": len(unsafe),
            "unsafe_rate": len(unsafe) / len(r),
            "max_late_by_min": max([x["worst_late_by_min"] for x in r] + [0.0]),
            "median_late_by_min_when_late": float(np.median(late)) if late else 0.0,
            "false_stable": sum(1 for x in r if x["false_stable"]),
            "expected_median_abs_err": float(np.median(errs)) if errs else None,
            "expected_p90_abs_err": float(np.percentile(errs, 90)) if errs else None,
            "expected_median_signed_err": float(np.median(signed)) if signed else None,
            "expected_optimistic_gt10pct": (sum(1 for e in signed if e > 0.10) / len(signed)) if signed else None,
            "scored_for_accuracy": len(errs),
            "true_crosses_within_horizon": sum(1 for x in r if x["true_min"] is not None and x["true_min"] < HORIZON_H * 60),
            "median_safety_margin_min": (float(np.median(margins)) if margins else None),
        }
    return out


def markdown(summary: dict, n: int, seed: int) -> str:
    lines = ["# V1: Time-to-Trouble clock against EPANET hydraulics", "",
             f"{n} randomized systems, seed {seed}. EPANET via WNTR, Hazen-Williams, 5-minute steps, 48-hour horizon.", "",
             "Unsafe = the displayed worst case was later than the true time to 20 psi by more than one step (5 min), or the clock "
             "said \"not falling\" when it fell below 20 psi. Expected-case error is (displayed - true) / true: negative means the "
             "clock was more pessimistic than reality; positive means optimistic. Scored only where the true time is between "
             "30 minutes and the 48-hour horizon.", "",
             "| family | n | unsafe | worst late by (max) | median warning ahead of truth | expected: median error | median abs error | p90 abs error | optimistic by >10% |",
             "|---|---|---|---|---|---|---|---|---|"]
    f = lambda v: "-" if v is None else f"{v:+.0%}"
    g = lambda v: "-" if v is None else f"{v:.0%}"
    h = lambda v: "-" if v is None else f"{v / 60:.1f} h"
    for fam, s in summary.items():
        lines.append(f"| {fam} | {s['scenarios']} | {s['unsafe_count']} ({s['unsafe_rate']:.1%}) | {s['max_late_by_min']:.0f} min | "
                     f"{h(s['median_safety_margin_min'])} | {f(s['expected_median_signed_err'])} | {g(s['expected_median_abs_err'])} | "
                     f"{g(s['expected_p90_abs_err'])} | {g(s['expected_optimistic_gt10pct'])} |")
    return "\n".join(lines) + "\n"


def run(n: int = sum(FAMILIES.values()), seed: int = 7, out_dir: Optional[str] = None, quiet: bool = False) -> dict:
    rng = np.random.default_rng(seed)
    counts = {k: max(1, round(v * n / sum(FAMILIES.values()))) for k, v in FAMILIES.items()}
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        sid = 0
        for fam, cnt in counts.items():
            for _ in range(cnt):
                sid += 1
                sc = make_scenario(sid, fam, rng, tmp)
                rows.append(evaluate(sc, tmp))
                if not quiet and sid % 50 == 0:
                    print(f"  {sid} scenarios done", flush=True)
    summary = summarize(rows)
    result = {"n": len(rows), "seed": seed, "summary": summary, "scenarios": rows}
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "v1_wntr_validation.json"), "w") as fh:
            json.dump(result, fh, indent=1)
        with open(os.path.join(out_dir, "v1_wntr_validation.md"), "w") as fh:
            fh.write(markdown(summary, len(rows), seed))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=sum(FAMILIES.values()))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    res = run(a.n, a.seed, a.out)
    print(markdown(res["summary"], res["n"], a.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
