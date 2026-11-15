"""Sensitivity and error-propagation study for the Time-to-Trouble clock.

Two questions an engineer will ask before trusting a countdown:

  1. If one number typed into setup is wrong by a plausible amount, how many minutes is the displayed worst case off by,
     and in which direction? (Direction matters: a countdown that is too LONG is the dangerous one.)
  2. If ALL the setup numbers carry plausible survey errors at once, how often does the displayed worst case promise more
     time than the site really has?

Method. A reference site (the demo plant) with a fresh tank reading is evaluated with the real engine (`compute_clock`). For each
input we build a "true" site in which only that input differs from what was typed, evaluate the same engine on it, and report
displayed minus true. Positive means optimistic. The Monte Carlo draws every input error at once from the distributions in
ERROR_MODEL below (seeded, so the result is reproducible) and compares the displayed worst case with the true minutes to the pressure line,
computed in closed form (usable water divided by the true drawdown, with the friction margin scaled by flow^1.852).

Limits, stated up front: the error sizes are our assumptions about what a survey or sensor could get wrong, not measurements.
The truth here uses the same equations as the clock, so this isolates the effect of wrong INPUTS. Whether the equations match
real hydraulics is validation V1 (results/v1_wntr_validation.md), against EPANET.

Run:    python3 tools/sensitivity.py            writes results/sensitivity.json and .md
Check:  python3 tools/sensitivity.py --check    fails if the committed files are out of date
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hydrowitness import config as C                                   # noqa: E402
from hydrowitness import stats                                          # noqa: E402
from hydrowitness.demo import demo_profile                              # noqa: E402
from hydrowitness.engine.time_to_trouble import LevelReading, compute_clock, gal_per_ft_of_level, l_min_ft  # noqa: E402
from hydrowitness.schemas import GAL_PER_FT3, PSI_TO_FT, SiteProfile    # noqa: E402

RES = ROOT / "results"
NOW = 6 * 3600.0           # 06:00 local on the demo site
LEVEL_FT = 18.0            # the tank reading
READ_AGE_S = 300.0         # five minutes old

# Standard deviations of plausible input errors (assumptions, see the module note). Normal unless stated.
ERROR_MODEL = {
    "level_reading_ft": 0.3,            # sensor or sight-glass reading error
    "critical_elevation_ft": 2.0,       # elevation of the farthest or highest customer, from a survey or a map
    "tank_floor_elevation_ft": 1.0,
    "tank_diameter_rel": 0.02,
    "headloss_margin_rel": 0.30,        # lognormal sigma: the friction margin is the least certain number
}
N_SAMPLES = 4000
SEED = 20261114


def make(**over) -> SiteProfile:
    d = demo_profile().model_dump()
    d.update(over)
    if "tank_diameter_ft" in over:
        d["tank_area_sqft"] = None
    return SiteProfile(**d)


def worst(profile: SiteProfile, level_ft: float = LEVEL_FT, age_s: float = READ_AGE_S, credits=None) -> float:
    r = compute_clock(profile, NOW, LevelReading(level_ft, NOW - age_s, "scada"), credits)
    return float("inf") if r.worst_min is None else r.worst_min


def expected(profile: SiteProfile, level_ft: float = LEVEL_FT) -> float:
    r = compute_clock(profile, NOW, LevelReading(level_ft, NOW - READ_AGE_S, "scada"))
    return float("inf") if r.expected_min is None else r.expected_min


def one_at_a_time() -> dict:
    base = make()
    shown = worst(base)
    rows = []

    def add(name: str, what: str, true_worst: float):
        err = shown - true_worst
        rows.append({"input": name, "if_the_real_value_is": what, "true_worst_min": round(true_worst, 1),
                     "displayed_minus_true_min": round(err, 1),
                     "relative_to_displayed": round(err / shown, 3), "direction": "optimistic" if err > 0.5 else "pessimistic" if err < -0.5 else "negligible"})

    for d in (-1.0, -0.5, 0.5, 1.0):
        add("tank level reading", f"{d:+.1f} ft from what was read", worst(base, LEVEL_FT + d))
    d0 = base.tank_diameter_ft
    for rel in (-0.05, -0.02, 0.02, 0.05):
        add("tank diameter", f"{rel:+.0%}", worst(make(tank_diameter_ft=d0 * (1 + rel))))
    for d in (-10, -5, -2, 2, 5, 10):
        add("critical-point elevation", f"{d:+d} ft from what was typed", worst(make(critical_point_elev_ft=base.critical_point_elev_ft + d)))
    for d in (-3, -1, 1, 3):
        add("tank floor elevation", f"{d:+d} ft", worst(make(tank_floor_elev_ft=base.tank_floor_elev_ft + d)))
    for rel in (-0.5, -0.3, 0.3, 0.5):
        add("friction (headloss) margin", f"{rel:+.0%}", worst(make(headloss_margin_ft=base.headloss_margin_ft * (1 + rel))))
    for d in (2, 5):
        add("minimum pressure the customers need", f"{d:+d} psi (a higher line)", worst(make(min_pressure_psi=base.min_pressure_psi + d)))
    for rel in (0.25, 0.5, 1.0):
        add("real peak demand above the configured peak", f"{rel:+.0%}", worst(make(peak_demand_gpm=base.peak_demand_gpm * (1 + rel))))
    ranked = sorted(rows, key=lambda r: -abs(r["displayed_minus_true_min"]))
    return {"peak_gpm": base.peak_demand_gpm, "reference": {"level_ft": LEVEL_FT, "reading_age_s": READ_AGE_S, "local_hour": 6,
                          "l_min_ft": round(l_min_ft(base), 2), "usable_depth_ft": round(LEVEL_FT - l_min_ft(base), 2),
                          "worst_min": round(shown, 1), "expected_min": round(expected(base), 1),
                          "gallons_per_ft_of_level": round(gal_per_ft_of_level(base), 0)},
            "rows": rows, "ranked_by_size": [(r["input"], r["if_the_real_value_is"], r["displayed_minus_true_min"]) for r in ranked[:8]]}


def staleness() -> list[dict]:
    base = make()
    out = []
    for label, age in (("5 min", 300), ("15 min", 900), ("1 h", 3600), ("3 h", 10800), ("12 h", 43200)):
        r = compute_clock(base, NOW, LevelReading(LEVEL_FT, NOW - age, "scada"))
        out.append({"reading_age": label, "worst_min": None if r.worst_min is None else round(r.worst_min, 1),
                    "level_uncertainty_ft": round(r.uncertainty_ft, 2), "quality": r.quality})
    return out


def pump_credit() -> list[dict]:
    base = make()
    from hydrowitness.engine.time_to_trouble import CreditedFlow
    rows = []
    for label, gpm in (("no pump credited", None), ("P1 credited at 0.8 x rated (200 gpm)", 200.0)):
        cr = None if gpm is None else [CreditedFlow("P1", gpm, NOW - READ_AGE_S, "witness")]
        rows.append({"case": label, "worst_min": round(worst(base, credits=cr), 1)})
    for frac in (1.0, 0.75, 0.5):
        true_gpm = 250.0 * frac
        cr = [CreditedFlow("P1", 200.0, NOW - READ_AGE_S, "witness")]
        # the clock credits 200 gpm (0.8 x rated); the pump really delivers true_gpm
        shown = worst(base, credits=cr)
        true_w = worst(base, credits=[CreditedFlow("P1", true_gpm, NOW - READ_AGE_S, "witness")])
        label = "the pump delivers its full rated flow" if frac == 1.0 else f"a worn pump delivers {frac:.0%} of its rated flow"
        rows.append({"case": f"P1 credited at 200 gpm; {label} ({true_gpm:.0f} gpm)",
                     "worst_min": round(shown, 1), "true_worst_min": round(true_w, 1), "displayed_minus_true_min": round(shown - true_w, 1)})
    return rows


def _true_minutes(level, crit, floor, diam, margin_at_cfg_peak, min_psi, demand, cfg_peak) -> float:
    area = math.pi * (diam / 2.0) ** 2
    margin = margin_at_cfg_peak * (demand / cfg_peak) ** C.HEADLOSS_EXPONENT
    lmin = max(0.0, crit + min_psi * PSI_TO_FT + margin - floor)
    usable = max(0.0, (level - lmin) * area * GAL_PER_FT3)
    return usable / demand


def monte_carlo(scale: float = 1.0, demand_lo: float = 1.0, demand_hi: float = 1.0, n: int = N_SAMPLES, seed: int = SEED,
                allowance_ft: float = 0.0) -> dict:
    """Draw setup errors at once. demand is the true constant draw as a fraction of the configured peak (uniform lo..hi)."""
    rng = random.Random(seed)
    base = make()
    shown = worst(make(setup_uncertainty_ft=allowance_ft))
    bad, optimistic_by = 0, []
    for _ in range(n):
        level = LEVEL_FT + rng.gauss(0, ERROR_MODEL["level_reading_ft"] * scale)
        crit = base.critical_point_elev_ft + rng.gauss(0, ERROR_MODEL["critical_elevation_ft"] * scale)
        floor = base.tank_floor_elev_ft + rng.gauss(0, ERROR_MODEL["tank_floor_elevation_ft"] * scale)
        diam = base.tank_diameter_ft * (1 + rng.gauss(0, ERROR_MODEL["tank_diameter_rel"] * scale))
        margin = base.headloss_margin_ft * math.exp(rng.gauss(0, ERROR_MODEL["headloss_margin_rel"] * scale))
        demand = base.peak_demand_gpm * rng.uniform(demand_lo, demand_hi)
        truth = _true_minutes(level, crit, floor, diam, margin, base.min_pressure_psi, demand, base.peak_demand_gpm)
        if shown > truth:
            bad += 1
            optimistic_by.append(shown - truth)
    lo, hi = stats.clopper_pearson(bad, n)
    optimistic_by.sort()
    p90 = optimistic_by[int(0.9 * (len(optimistic_by) - 1))] if optimistic_by else 0.0
    return {"samples": n, "error_scale": scale, "setup_allowance_ft": allowance_ft, "true_demand_vs_peak": [demand_lo, demand_hi], "displayed_worst_min": round(shown, 1),
            "optimistic": {"k": bad, "n": n, "rate": bad / n, "lo": lo, "hi": hi},
            "when_optimistic_median_min": round(optimistic_by[len(optimistic_by) // 2], 1) if optimistic_by else 0.0,
            "when_optimistic_p90_min": round(p90, 1)}


def build() -> dict:
    return {
        "schema": 1, "error_model": ERROR_MODEL, "seed": SEED, "samples": N_SAMPLES,
        "one_at_a_time": one_at_a_time(), "reading_age": staleness(), "pump_credit": pump_credit(),
        "monte_carlo": [
            {"label": "typical setup errors; true draw 50-100% of the configured peak (the case the clock is built for)", **monte_carlo(1.0, 0.5, 1.0)},
            {"label": "survey-grade setup (a quarter of the errors); draw 50-100% of peak", **monte_carlo(0.25, 0.5, 1.0)},
            {"label": "typical setup errors, with a 1 ft setup allowance; draw 50-100% of peak", **monte_carlo(1.0, 0.5, 1.0, allowance_ft=1.0)},
            {"label": "typical setup errors, with a 2 ft setup allowance; draw 50-100% of peak", **monte_carlo(1.0, 0.5, 1.0, allowance_ft=2.0)},
            {"label": "typical setup errors, with a 4 ft setup allowance; draw 50-100% of peak", **monte_carlo(1.0, 0.5, 1.0, allowance_ft=4.0)},
            {"label": "half the setup errors; draw 50-100% of peak", **monte_carlo(0.5, 0.5, 1.0)},
            {"label": "double the setup errors; draw 50-100% of peak", **monte_carlo(2.0, 0.5, 1.0)},
            {"label": "typical setup errors; draw exactly at the configured peak (a boundary case)", **monte_carlo(1.0, 1.0, 1.0)},
            {"label": "typical setup errors; draw 50-150% of peak (demand can exceed the peak)", **monte_carlo(1.0, 0.5, 1.5)},
            {"label": "no setup errors at all; draw 50-150% of peak (the demand ceiling alone)", **monte_carlo(0.0, 0.5, 1.5)},
        ]}


def _m(c: dict) -> str:
    return f"{stats.pct(c['rate'])} (95% CI {stats.pct(c['lo'])} to {stats.pct(c['hi'])})"


def markdown(s: dict) -> str:
    ref = s["one_at_a_time"]["reference"]
    L: list[str] = []
    a = L.append
    a("# Sensitivity of the Time-to-Trouble clock")
    a("")
    a("This answers two questions: how far off is the countdown when one setup number is wrong, and how often is it too optimistic when every setup number carries a plausible survey error. "
      "It uses the real engine. The error sizes are our assumptions, not measurements. Nothing here is a field result.")
    a("")
    a(f"**Reference site:** the demo plant, tank read at {ref['level_ft']:g} ft, {ref['reading_age_s'] / 60:g} minutes ago, 06:00 local. "
      f"The lowest tank level that still gives the farthest customer 20 psi is {ref['l_min_ft']} ft, so only {ref['usable_depth_ft']} ft of water is usable "
      f"({ref['gallons_per_ft_of_level']:,.0f} gal per foot). Displayed worst case {ref['worst_min']:.0f} min, expected case {ref['expected_min']:.0f} min.")
    a("")
    a("## 1. One wrong number at a time")
    a("")
    a("Positive 'displayed minus true' means the screen promised more time than the site had (the dangerous direction).")
    a("")
    a("| input | if the real value is | true worst case (min) | displayed minus true (min) | direction |")
    a("|---|---|---|---|---|")
    for r in s["one_at_a_time"]["rows"]:
        a(f"| {r['input']} | {r['if_the_real_value_is']} | {r['true_worst_min']:.0f} | {r['displayed_minus_true_min']:+.0f} | {r['direction']} |")
    a("")
    per_ft = ref["gallons_per_ft_of_level"] / s["one_at_a_time"]["peak_gpm"]
    a(f"**What this says.** Every foot of error in an elevation (or 2.31 ft of pressure line per psi) moves the worst case by about {per_ft:.0f} minutes, "
      f"which is {per_ft / ref['worst_min']:.0%} of the {ref['worst_min']:.0f} displayed, because only {ref['usable_depth_ft']} ft of water is usable. "
      "The inputs that matter most are the ones a person types once and nobody checks again: the farthest customer's elevation, the tank floor elevation, the pressure the customers need and the friction margin. "
      "That is why the setup wizard must ask for them carefully, why the dashboard states its assumptions on screen, and why we recommend a second-source check of every elevation before going live.")
    a("")
    a("## 2. How old the tank reading is")
    a("")
    a("| reading age | worst case (min) | level uncertainty subtracted (ft) | label |")
    a("|---|---|---|---|")
    for r in s["reading_age"]:
        a(f"| {r['reading_age']} | {r['worst_min']:.0f} | {r['level_uncertainty_ft']} | {r['quality']} |")
    a("")
    a("The worst case already gives up time as a reading ages. That is deliberate: the screen should lean toward 'less time than you think'.")
    a("")
    a("## 3. Crediting a pump")
    a("")
    a("| case | worst case (min) | true worst case (min) | displayed minus true (min) |")
    a("|---|---|---|---|")
    for r in s["pump_credit"]:
        a(f"| {r['case']} | {r['worst_min']:.0f} | {r.get('true_worst_min', '')} | {r.get('displayed_minus_true_min', '')} |")
    a("")
    a("Crediting inflow is the one place the countdown can become optimistic by design, which is why it is credited only when a witness shows water moving, at 0.8 of rated flow.")
    a("")
    a("## 4. Every setup number wrong at once (Monte Carlo)")
    a("")
    a(f"{s['samples']} random draws per row, seed {s['seed']}. Error sizes (standard deviations): level reading {s['error_model']['level_reading_ft']} ft, critical-point elevation "
      f"{s['error_model']['critical_elevation_ft']} ft, tank floor elevation {s['error_model']['tank_floor_elevation_ft']} ft, tank diameter {s['error_model']['tank_diameter_rel']:.0%}, "
      f"friction margin {s['error_model']['headloss_margin_rel']:.0%} (lognormal). Truth is the closed-form time to the pressure line at a constant true draw.")
    a("")
    a("| case | share of draws where the screen was too optimistic | when it was, median / 90th percentile too long (min) |")
    a("|---|---|---|")
    for v in s["monte_carlo"]:
        a(f"| {v['label']} | {_m(v['optimistic'])} | {v['when_optimistic_median_min']:.0f} / {v['when_optimistic_p90_min']:.0f} |")
    a("")
    mc = {i: v for i, v in enumerate(s["monte_carlo"])}
    typ, grade, a1, a2, a4, half, dbl, edge, over, ceil_ = (mc[i]["optimistic"]["rate"] for i in range(10))
    a(f"**The setup allowance.** Setup has an optional `setup_uncertainty_ft` setting (default 0, so nothing changes unless a site sets it). "
      f"It subtracts that many feet from the worst case. With typical survey errors it brings the optimistic share from {typ:.0%} down to {a1:.1%} at 1 ft, {a2:.1%} at 2 ft and {a4:.1%} at 4 ft. "
      f"The price is a shorter countdown: about {ref['gallons_per_ft_of_level'] / s['one_at_a_time']['peak_gpm']:.0f} minutes per foot on this site. A site that has not had its elevations checked twice should set it.")
    a("")
    a(f"**What this says.** In the case the clock is built for (true draw at or below the configured peak) typical survey errors alone leave the displayed worst case too optimistic in about {typ:.0%} of sites, "
      f"{half:.0%} if the setup numbers are twice as accurate, {grade:.0%} if four times as accurate, and {dbl:.0%} if twice as sloppy. So the safety of the countdown depends on how carefully the site is surveyed. "
      f"When the true draw can exceed the configured peak, the share rises to about {over:.0%}, and the demand ceiling alone accounts for {ceil_:.0%}. "
      "The row at exactly the peak is a boundary case, not a typical day: no tank drains at its absolute peak for hours. "
      "These are the same limits we report as the failed demand-stress family in validation V1, and why the screen says to re-read the tank when something unusual happens.")
    a("")
    a("**What would make it stronger:** a setup check that compares the typed elevations with a second source, a stated survey accuracy for each input, and a field comparison of the countdown against a real tank drawdown.")
    a("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    s = build()
    js, md = json.dumps(s, indent=1, sort_keys=True) + "\n", markdown(s)
    if a.check:
        ok = (RES / "sensitivity.json").exists() and (RES / "sensitivity.md").exists()
        if ok:
            # floats may differ in their last digits between Python versions; everything else must match exactly
            ok = stats.json_close(json.loads((RES / "sensitivity.json").read_text(encoding="utf-8")), json.loads(js)) and \
                (RES / "sensitivity.md").read_text(encoding="utf-8") == md
        print("results/sensitivity.* is up to date" if ok else "results/sensitivity.* is out of date. Run: python3 tools/sensitivity.py")
        return 0 if ok else 1
    (RES / "sensitivity.json").write_text(js, encoding="utf-8")
    (RES / "sensitivity.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
