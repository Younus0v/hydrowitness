"""Validation V2 (attack drills) and the false-alarm check, run against the simulated plant.

Everything here uses the real engines (consistency rules, trust engine, node verification, playbook) with a
SIMULATED plant and a SIMULATED Witness Node. It shows that the software does what it was designed to do under
the six scripted scenarios, and how quickly. It does not show how real pumps sound or how real SCADA behaves:
those are the hardware stage (V3) and a pilot.

Run:  python3 -m hydrowitness.sim.validation_report [--seeds 5] [--normal-days 4] [--rule-days 20] [--out results]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from collections import Counter

from ..demo import demo_profile
from ..engine.consistency import ConsistencyChecker
from .drills import SCENARIOS, run_normal, run_scenario
from .tank_system import ScadaView, TankSystem


def drills(seeds: int, after_attack_s: float = 900.0) -> dict:
    out = {}
    for sid, sc in SCENARIOS.items():
        if sid == "free_play":
            continue
        for op in (False, True):
            runs = [run_scenario(sid, seed=s, operator_script=op, after_attack_s=after_attack_s) for s in range(1, seeds + 1)]
            amber = [r["first_amber_s"] for r in runs if r["first_amber_s"] is not None]
            red = [r["first_red_s"] for r in runs if r["first_red_s"] is not None]
            out[f"{sid}|{'operator' if op else 'no operator'}"] = {
                "scenario": sid, "name": sc.name, "operator_script": op, "seeds": seeds,
                "reached_amber": len(amber), "reached_red": len(red),
                "amber_median_s": statistics.median(amber) if amber else None, "amber_max_s": max(amber) if amber else None,
                "red_median_s": statistics.median(red) if red else None, "red_max_s": max(red) if red else None,
                "final_states": dict(Counter(r["final_state"] for r in runs)),
                "false_alarms_before_attack": sum(r["false_alarms_before_attack"] for r in runs),
                "info_cards": sorted({c for r in runs for c in r["info_cards"]}),
            }
    return out


def normal_operation(days: float, seeds: int) -> dict:
    """Full system with two nodes, healthy plant. Two tank sizes: the demo tank (pumps rarely switch) and a
    small tank whose pumps start and stop about five times a day, which exercises the node-versus-SCADA checks."""
    small = demo_profile().model_copy(update={"tank_diameter_ft": 30.0, "tank_area_sqft": math.pi * 15.0 ** 2})
    runs = [run_normal(days, seed=s) for s in range(1, seeds + 1)] + \
           [run_normal(days, seed=s, profile=small) for s in range(1, seeds + 3)]
    return {"site_days": days * len(runs), "false_amber": sum(r["false_amber"] for r in runs),
            "false_red": sum(r["false_red"] for r in runs), "pump_state_changes": sum(r["pump_state_changes"] for r in runs),
            "node_state_reports": sum(r["node_state_reports"] for r in runs)}


def rule_false_episodes(days: float, seeds: int = 4) -> dict:
    """The seven consistency rules alone, on a healthy plant with realistic sensor noise."""
    p = demo_profile()
    tot, prev_total = Counter(), 0
    for seed in range(1, seeds + 1):
        s = TankSystem(p, seed=seed, dt=10.0)
        v = ScadaView(s, seed=seed + 50)
        chk = ConsistencyChecker(p)
        prev = set()
        for _ in range(int(days * 86400 / 10)):
            s.step()
            chk.ingest(v.snapshot(), True)
            fired = {r.rule_id for r in chk.evaluate(s.t) if r.fired}
            for rid in fired - prev:
                tot[rid] += 1
            prev = fired
    return {"site_days": days * seeds, "false_rule_episodes": dict(tot), "total": sum(tot.values())}


def markdown(report: dict) -> str:
    t = lambda v: "-" if v is None else f"{v:.0f} s"
    lines = ["# V2: attack drills and false alarms (simulated plant, simulated node)", "",
             "| scenario | operator walks to the pump | reached amber | reached red | amber (median / max) | red (median / max) | final states | false alarms before attack |",
             "|---|---|---|---|---|---|---|---|"]
    for d in report["drills"].values():
        lines.append(f"| {d['name']} | {'yes' if d['operator_script'] else 'no'} | {d['reached_amber']}/{d['seeds']} | "
                     f"{d['reached_red']}/{d['seeds']} | {t(d['amber_median_s'])} / {t(d['amber_max_s'])} | "
                     f"{t(d['red_median_s'])} / {t(d['red_max_s'])} | {d['final_states']} | {d['false_alarms_before_attack']} |")
    n, r = report["normal_operation"], report["rule_false_episodes"]
    lines += ["", f"Normal operation, full system with two nodes: {n['site_days']:.0f} site-days, {n['false_amber']} false amber, "
              f"{n['false_red']} false red, while the pumps really started or stopped {n['pump_state_changes']} times. The nodes sent "
              f"{n['node_state_reports']} state-change messages, most of them brief 'unsure' blips from their modelled sensor noise.",
              f"Consistency rules alone: {r['site_days']:.0f} site-days, {r['total']} false rule episodes {r['false_rule_episodes'] or ''}.", ""]
    return "\n".join(lines)


def run(seeds: int = 5, normal_days: float = 1.0, normal_seeds: int = 4, rule_days: float = 5.0, out_dir: str | None = None) -> dict:
    report = {"drills": drills(seeds), "normal_operation": normal_operation(normal_days, normal_seeds),
              "rule_false_episodes": rule_false_episodes(rule_days)}
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "v2_drills.json"), "w") as fh:
            json.dump(report, fh, indent=1, default=str)
        with open(os.path.join(out_dir, "v2_drills.md"), "w") as fh:
            fh.write(markdown(report))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--normal-days", type=float, default=1.0, help="days per seed of full-system normal operation")
    ap.add_argument("--normal-seeds", type=int, default=4)
    ap.add_argument("--rule-days", type=float, default=5.0, help="days per seed of rules-only normal operation (x4 seeds)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    report = run(a.seeds, a.normal_days, a.normal_seeds, a.rule_days, a.out)
    print(markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
