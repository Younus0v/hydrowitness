"""Confidence intervals for every headline count in results/, written to results/statistics.json and results/statistics.md.

The reports in results/ give counts: "0 unsafe in 200 systems", "20 of 20 attacks warned". A count is not a guarantee. This tool
re-reads the committed result files and puts an exact 95% interval beside each count, so nobody (including us) reads "0 of 200"
as "never". It adds no new simulation; it only reads results/*.json. The long false-alarm study (tools/long_normal.py) is
included when results/long_normal.json exists.

Run:    python3 tools/make_statistics.py            writes results/statistics.json and .md
Check:  python3 tools/make_statistics.py --check    fails if the committed statistics are out of date with results/*.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hydrowitness import stats                     # noqa: E402
from hydrowitness.sim import evaluation as ev      # noqa: E402

RES = ROOT / "results"
INSIDE = ("model_check", "nominal_off", "nominal_pump")      # families that stay inside the clock's stated assumptions


def _ci(k: int, n: int) -> dict:
    lo, hi = stats.clopper_pearson(k, n)
    return {"k": k, "n": n, "rate": (k / n if n else None), "lo": lo, "hi": hi}


def _load(name: str):
    p = RES / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def build() -> dict:
    v1, evl, drills, longn = _load("v1_wntr_validation.json"), _load("evaluation.json"), _load("v2_drills.json"), _load("long_normal.json")
    out: dict = {"schema": 1, "level": 0.95, "method": "Clopper-Pearson exact (proportions); Poisson exact (rates)"}

    # 1. the clock against EPANET -----------------------------------------------------------------------------------------
    fam = {}
    for name, s in v1["summary"].items():
        fam[name] = {"unsafe": _ci(s["unsafe_count"], s["scenarios"]), "false_stable": _ci(s["false_stable"], s["scenarios"])}
    n_in = sum(v1["summary"][f]["scenarios"] for f in INSIDE)
    k_in = sum(v1["summary"][f]["unsafe_count"] for f in INSIDE)
    out["v1_clock_vs_epanet"] = {"families": fam, "inside_assumptions": {"families": list(INSIDE), "unsafe": _ci(k_in, n_in)}}

    # 2. detection of attacks ---------------------------------------------------------------------------------------------
    att = {}
    for key, s in evl["summary"]["attacks"].items():
        d = s["detection"]
        att[key] = {"scenario": s["scenario"], "operator": s["operator"], "runs": d["runs"],
                    "hydrowitness_warned": _ci(d["hydrowitness_warned"]["detected"], d["runs"]),
                    "hydrowitness_red": _ci(d["hydrowitness_red"]["detected"], d["runs"]),
                    "alarm_panel": _ci(d["alarm"]["detected"], d["runs"]),
                    "alarm_panel_flatline": _ci(d["alarm_flatline"]["detected"], d["runs"])}
    t = evl["summary"]["totals"]
    n0 = t["attack_runs_without_operator"]
    out["attacks"] = {
        "by_scenario": att,
        "without_operator_all_scenarios": {"runs": n0,
                                           "hydrowitness_warned": _ci(n0 - t["hydrowitness_missed_without_operator"], n0),
                                           "alarm_panel": _ci(n0 - t["alarm_missed_without_operator"], n0),
                                           "alarm_panel_flatline": _ci(n0 - t["alarm_flatline_missed_without_operator"], n0)}}
    out["failure_conditions"] = {k: {"runs": v["detection"]["runs"],
                                     "hydrowitness_warned": _ci(v["detection"]["hydrowitness_warned"]["detected"], v["detection"]["runs"]),
                                     "hydrowitness_red": _ci(v["detection"]["hydrowitness_red"]["detected"], v["detection"]["runs"])}
                                 for k, v in evl["summary"]["failure_conditions"].items()}

    # 3. false alarms: every clean simulated site-day we have ----------------------------------------------------------------
    sources = []
    nm = evl["summary"]["normal_operation"]
    sources.append(("evaluation: normal operation", nm["site_days"], nm["hydrowitness_false_amber"], nm["hydrowitness_false_red"]))
    dn = drills["normal_operation"]
    sources.append(("drills: normal operation", dn["site_days"], dn["false_amber"], dn["false_red"]))
    if longn:
        sources.append(("long study (seeds 1000+)", longn["site_days"], longn["hydrowitness_false_amber"], longn["hydrowitness_false_red"]))
    days = sum(s[1] for s in sources)
    fa, fr = sum(s[2] for s in sources), sum(s[3] for s in sources)
    out["false_alarms"] = {"sources": [{"name": s[0], "site_days": s[1], "false_amber": s[2], "false_red": s[3]} for s in sources],
                           "site_days": days, "false_amber": fa, "false_red": fr,
                           "upper_95_per_site_day": {"amber": stats.poisson_upper(fa, days), "red": stats.poisson_upper(fr, days)},
                           "site_days_needed_for_upper_bound_below_0.01_when_zero_events": round(stats.poisson_upper(0, 1.0) / 0.01)}

    # 4. countdown: the worst case must never promise more time than the truth --------------------------------------------------
    def runs_with_late(runs):
        scored_runs, bad = 0, 0
        for r in runs:
            sc = [ev.score_checkpoint(c) for c in r["checks"]]
            sc = [x for x in sc if x["kind"] == "scored"]
            if sc:
                scored_runs += 1
                bad += any(not x["bound_held"] for x in sc)
        return scored_runs, bad

    allruns = evl["runs"]["attacks"] + evl["runs"]["failure_conditions"] + evl["runs"]["normal"]
    n_runs, bad_runs = runs_with_late(allruns)
    tc = t["countdown"]
    out["countdown"] = {
        "checkpoints_scored": tc["scored"], "bound_failures_checkpoint": _ci(tc["scored"] - tc["worst_case_bound_held"], tc["scored"]),
        "runs_with_a_scored_checkpoint": n_runs, "runs_where_the_bound_failed": _ci(bad_runs, n_runs),
        "note": "Checkpoints inside one run are strongly correlated, so the checkpoint interval is optimistic. The run-level interval is the one to quote."}
    return out


def _r(c: dict) -> str:
    return f"{c['k']} of {c['n']} ({stats.pct(c['rate'])}; 95% CI {stats.pct(c['lo'])} to {stats.pct(c['hi'])})"


def markdown(s: dict) -> str:
    L: list[str] = []
    a = L.append
    a("# Statistics: what each count can and cannot rule out")
    a("")
    a("Every interval below is an exact 95% interval (Clopper-Pearson for proportions, Poisson for rates). They cover **sampling luck inside "
      "the simulation only**. They do not cover the gap between a simulated plant and a real one, which is the bigger uncertainty. "
      "Re-run with `python3 tools/make_statistics.py`. Nothing here is a field result.")
    a("")
    a("## 1. Time-to-Trouble clock against EPANET (validation V1)")
    a("")
    a("A system counts as unsafe when the displayed worst case promised more time than EPANET says there was (by more than one 5-minute step).")
    a("")
    a("| family | unsafe, with 95% CI |")
    a("|---|---|")
    for name, f in s["v1_clock_vs_epanet"]["families"].items():
        a(f"| {name} | {_r(f['unsafe'])} |")
    ins = s["v1_clock_vs_epanet"]["inside_assumptions"]
    a(f"| **inside the clock's assumptions** ({', '.join(ins['families'])}) | **{_r(ins['unsafe'])}** |")
    a("")
    a("Zero failures in the sample does not mean zero in the world: the upper end of the interval is what these systems cannot rule out. "
      "The demand-stress family fails by design and is the honest limit: when real demand is far above the configured peak, the countdown is too optimistic.")
    a("")
    a("## 2. Attack detection (simulated plants, no operator walking to the pump)")
    a("")
    a("| scenario | runs | HydroWitness warned | HydroWitness reached red | alarm panel | alarm panel + flat-line rule |")
    a("|---|---|---|---|---|---|")
    for key, r in s["attacks"]["by_scenario"].items():
        if r["operator"]:
            continue
        a(f"| {r['scenario']} | {r['runs']} | {_r(r['hydrowitness_warned'])} | {_r(r['hydrowitness_red'])} | {_r(r['alarm_panel'])} | {_r(r['alarm_panel_flatline'])} |")
    w = s["attacks"]["without_operator_all_scenarios"]
    a(f"| **all six scenarios** | {w['runs']} | **{_r(w['hydrowitness_warned'])}** | | {_r(w['alarm_panel'])} | {_r(w['alarm_panel_flatline'])} |")
    a("")
    a("With 10 to 20 runs per scenario the intervals are wide: 20 of 20 only shows the true rate is probably above 83%. "
      "Seeds change the noise and message delays, not the plant or the attack script, so more seeds would add little evidence. "
      "More plants, pumps and attack variants would.")
    a("")
    a("Witness-failure conditions (nodes jammed):")
    a("")
    a("| condition | runs | warned | reached red |")
    a("|---|---|---|---|")
    for k, f in s["failure_conditions"].items():
        a(f"| {k} | {f['runs']} | {_r(f['hydrowitness_warned'])} | {_r(f['hydrowitness_red'])} |")
    a("")
    fa = s["false_alarms"]
    a("## 3. False alarms on healthy simulated plants")
    a("")
    a("| source | site-days | false amber | false red |")
    a("|---|---|---|---|")
    for r in fa["sources"]:
        a(f"| {r['name']} | {r['site_days']:g} | {r['false_amber']} | {r['false_red']} |")
    a(f"| **total** | **{fa['site_days']:g}** | **{fa['false_amber']}** | **{fa['false_red']}** |")
    a("")
    ua, ur = fa["upper_95_per_site_day"]["amber"], fa["upper_95_per_site_day"]["red"]
    a(f"With {fa['false_amber']} false amber and {fa['false_red']} false red in {fa['site_days']:g} simulated site-days, the 95% upper bounds are "
      f"{ua:.3f} false amber and {ur:.3f} false red per site-day (about one every {1 / ua:.0f} and {1 / ur:.0f} site-days). "
      f"To bound the rate below 0.01 per site-day with no events you need about {fa['site_days_needed_for_upper_bound_below_0.01_when_zero_events']} clean site-days. "
      "A real plant will add noise this simulation does not have, so the first real site-days matter more than more simulated ones.")
    a("")
    c = s["countdown"]
    a("## 4. Countdown never promising more time than the simulated truth")
    a("")
    a(f"- Checkpoints scored: {c['checkpoints_scored']}. Checkpoints where the worst case was later than the truth: {_r(c['bound_failures_checkpoint'])}.")
    a(f"- Runs with a scored checkpoint: {c['runs_with_a_scored_checkpoint']}. Runs where the bound failed at least once: {_r(c['runs_where_the_bound_failed'])}.")
    a(f"- {c['note']}")
    a("")
    return "\n".join(L)


def main(argv=None) -> int:
    global RES
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if results/statistics.* is out of date")
    ap.add_argument("--results", default=str(RES))
    a = ap.parse_args(argv)
    RES = Path(a.results)
    s = build()
    js, md = json.dumps(s, indent=1, sort_keys=True) + "\n", markdown(s)
    if a.check:
        old_js = (RES / "statistics.json").read_text(encoding="utf-8") if (RES / "statistics.json").exists() else ""
        old_md = (RES / "statistics.md").read_text(encoding="utf-8") if (RES / "statistics.md").exists() else ""
        try:
            same_js = stats.json_close(json.loads(old_js), json.loads(js))
        except ValueError:
            same_js = False
        if not same_js or old_md != md:
            print("results/statistics.* is out of date. Run: python3 tools/make_statistics.py")
            return 1
        print("results/statistics.* is up to date")
        return 0
    (RES / "statistics.json").write_text(js, encoding="utf-8")
    (RES / "statistics.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
