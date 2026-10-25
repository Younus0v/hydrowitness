"""A fair comparison: HydroWitness against a plain alarm panel, on the simulated plant, with every setting written down.

The question: when the screen lies or goes quiet, does HydroWitness notice something a plain alarm panel would not,
what does it cost in false alarms, and how close is its countdown to what the simulated plant really does?

What is compared
  HydroWitness       the real engines (consistency rules, trust engine, witness nodes, countdown) as the drills run them.
  Alarm panel        what an ordinary SCADA alarm list does with the same screen data: it believes it. It raises an alarm when
                     the tank is low, when the pressure is low, or when no data has arrived for a while.
  Alarm + flat-line  the same panel with one extra, generous rule: also alarm when the readings stop changing at all.
                     A real plant is noisy, so identical readings for five minutes mean a frozen screen. It is here so the
                     comparison cannot be accused of handing the baseline a weak hand.

What is measured
  Missed events          attack runs where the system raised nothing at all within the horizon.
  Detection delay        seconds from the start of the attack to the first alarm or warning.
  False alarms per site-day   alarms on a healthy plant, no attack, counted per simulated site-day.
  Countdown error        HydroWitness's "time until the farthest customer falls below the line" against the simulated plant's
                         own answer for the same assumption (every pump stopped). Early: it said trouble sooner than it came.
                         Late: it said trouble would come later than it did, which is the dangerous direction. The alarm
                         panel has no countdown, so it is not scored on this.
  Unavailable-estimate rate   how often HydroWitness said "estimate unavailable" instead of giving a number.
  Failure conditions     runs built to break HydroWitness (witnesses jammed during an attack), reported as they came out.

What it does not show. The plant, the screen's lies and the Witness Nodes are all simulated, and the nodes' error rates are
assumptions (hydrowitness/sim/fake_node.py), not measurements of a real pump. The firmware has booted on a real ESP32 board but has not
yet been recorded against a real pump (docs/HARDWARE_STAGE.md), and nothing from a real pump is used here.
So these numbers show how the software behaves against the scripted scenarios. They are not a forecast for a real water
system. Everything is deterministic: the same seeds give byte-identical results.

Run:  python3 -m hydrowitness.sim.evaluation [--seeds 10] [--normal-days 2] [--normal-seeds 6] [--jobs 4] [--out results]
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import statistics
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Optional

from ..api import VERSION
from ..demo import DEMO_SECRETS, demo_profile
from .. import config as C
from ..schemas import ScadaSnapshot, SiteProfile
from ..service import HydroWitnessCore
from .drills import SCENARIOS, T0, DrillRunner
from .fake_node import FakeNode
from .tank_system import ScadaView, TankSystem
from .truth import TRUTH_STEP_S, minutes_to_line_no_inflow

SCHEMA = 1
SEEDS = tuple(range(1, 11))
HORIZON_S = 3600.0               # how long after the start of an attack a system is given to react
CHECK_EVERY_S = 300.0            # countdown checkpoints in an attack run
NORMAL_DAYS = 2.0
NORMAL_SEEDS = 6                 # per plant size
NORMAL_CHECK_EVERY_S = 7200.0    # countdown checkpoints in a normal run
LOW_LEVEL_ALARM_FT = 15.0        # midway between the pumps' start level (19 ft) and the pressure line (11.2 ft)
FLAT_LINE_S = 300.0
TOLERANCE_MIN = 15.0             # a countdown within this many minutes of the truth, or ...
TOLERANCE_FRAC = 0.10            # ... within this share of it, whichever is larger, is "close"
BOUND_SLACK_MIN = 5.0            # the truth is worked out in 5 minute steps
TRUTH_HORIZON_H = float(C.EXPECTED_HORIZON_H)      # the truth is looked up as far ahead as the countdown itself looks (72 h)
CAP_MIN = TRUTH_HORIZON_H * 60.0

PANELS = ("alarm", "alarm_flatline")
PANEL_NAMES = {"alarm": "Alarm panel", "alarm_flatline": "Alarm panel + flat-line check"}

# Attacks built to break HydroWitness: what if the witnesses are taken out too?
FAILURE_CONDITIONS = [
    {"id": "one_witness_jammed", "scenario": "forced_off_spoofed", "jam": ["NB"],
     "text": "Pump 2 is forced off behind a forged screen and the Witness Node on Pump 2 is jammed at the same moment. Nobody walks to the pump."},
    {"id": "both_witnesses_jammed", "scenario": "forced_off_spoofed", "jam": ["NA", "NB"],
     "text": "The same attack, and both Witness Nodes are jammed. Nobody walks to the pump."},
    {"id": "replay_witnesses_jammed", "scenario": "replay", "jam": ["NA", "NB"],
     "text": "The screen replays old data while Pump 2 is stopped, and both Witness Nodes are jammed."},
]


# ---- the plain alarm panel -------------------------------------------------------------------------------------------
class AlarmPanel:
    """What an ordinary alarm list does with the SCADA stream. It believes the screen."""

    def __init__(self, profile: SiteProfile, *, flat_line: bool, low_level_ft: float = LOW_LEVEL_ALARM_FT):
        self.low_level_ft = low_level_ft
        self.low_pressure_psi = profile.min_pressure_psi
        self.no_data_s = profile.stale_after_s
        self.flat_line_s = FLAT_LINE_S if flat_line else None
        self.last: Optional[ScadaSnapshot] = None
        self._last_t: Optional[float] = None
        self._sig: Optional[tuple] = None
        self._same_since: Optional[float] = None
        self._active: set[str] = set()
        self.events: list[tuple[float, str]] = []         # (time, kind) each time an alarm starts

    def step(self, now: float, snap: Optional[ScadaSnapshot]) -> None:
        if snap is not None:
            sig = (snap.tank_level_ft, snap.pressure_psi, snap.demand_gpm)
            if sig != self._sig:
                self._same_since = now
            self._sig, self.last, self._last_t = sig, snap, now
        active: set[str] = set()
        if self.last is not None:
            if self.last.tank_level_ft is not None and self.last.tank_level_ft < self.low_level_ft:
                active.add("low_level")
            if self.last.pressure_psi is not None and self.last.pressure_psi < self.low_pressure_psi:
                active.add("low_pressure")
            if self._last_t is not None and now - self._last_t > self.no_data_s:
                active.add("no_data")
            if self.flat_line_s is not None and snap is not None and self._same_since is not None \
                    and now - self._same_since >= self.flat_line_s:
                active.add("flat_line")
        for kind in sorted(active - self._active):
            self.events.append((now, kind))
        self._active = active

    def first_after(self, t: float) -> Optional[tuple[float, str]]:
        return next(((e - t, k) for e, k in self.events if e >= t), None)


def _panels(profile: SiteProfile) -> dict[str, AlarmPanel]:
    return {"alarm": AlarmPanel(profile, flat_line=False), "alarm_flatline": AlarmPanel(profile, flat_line=True)}


# ---- scoring one countdown checkpoint --------------------------------------------------------------------------------
def _cap(x: Optional[float]) -> float:
    return CAP_MIN if x is None else min(float(x), CAP_MIN)


def score_checkpoint(row: dict) -> dict:
    """One checkpoint: unavailable, not modelled (a closed valve: the simulation does not work out the pressure beyond it),
    skipped (a pump's flow was credited, so the no-inflow truth is the wrong yardstick), or scored with whether the worst case held and how far the expected case was from the truth."""
    if not row["ok"]:
        return {"kind": "unavailable"}
    if row.get("not_modelled"):
        return {"kind": "not_modelled"}
    if row["credited_gpm"] > 0:
        return {"kind": "skipped"}
    truth = _cap(row["truth_min"])
    err = _cap(row["expected_min"]) - truth
    tol = max(TOLERANCE_MIN, TOLERANCE_FRAC * truth)
    return {"kind": "scored", "bound_held": _cap(row["worst_min"]) <= truth + BOUND_SLACK_MIN, "error_min": round(err, 1),
            "class": "late" if err > tol else "early" if err < -tol else "close"}


def _checkpoint(view: dict, sys_: TankSystem, profile: SiteProfile, after_s: float) -> dict:
    c = view["clock"]
    row: dict[str, Any] = {"after_s": after_s, "ok": bool(c["ok"]), "credited_gpm": round(float(c["credited_gpm"]), 1)}
    if sys_.demand_scale < 0.99:
        row["not_modelled"] = True           # a valve is closed: only part of the water reaches customers, and pressure beyond it is not simulated
    else:
        row["truth_min"] = minutes_to_line_no_inflow(sys_, profile.min_pressure_psi, TRUTH_HORIZON_H)
    if c["ok"]:
        row["worst_min"] = None if c["worst_min"] is None else round(c["worst_min"], 1)
        row["expected_min"] = None if c["expected_min"] is None else round(c["expected_min"], 1)
    return row


# ---- one attack run --------------------------------------------------------------------------------------------------
def run_attack(scenario_id: str, seed: int, operator: bool, *, jam: tuple[str, ...] = (), horizon_s: float = HORIZON_S,
               check_every_s: float = CHECK_EVERY_S, small_tank: bool = False) -> dict:
    profile = _small_tank() if small_tank else demo_profile()
    panels = _panels(profile)

    def feed(now: float, snap: Optional[ScadaSnapshot]) -> None:
        for p in panels.values():
            p.step(now, snap)

    r = DrillRunner(scenario_id, seed=seed, operator_script=operator, on_scada=feed, profile=profile)
    for node_id in jam:
        r.inject("jam_node", node_id)
    checks, every = [], max(1, int(round(check_every_s / r.dt)))
    for i in range(1, int(round(horizon_s / r.dt)) + 1):
        view = r.tick()
        if i % every == 0:
            checks.append(_checkpoint(view, r.sys, profile, i * r.dt))
    m = r.metrics()
    warn = [x for x in (m["first_amber_s"], m["first_red_s"]) if x is not None]
    out: dict[str, Any] = {"scenario": scenario_id, "operator": operator, "seed": seed, "plant": "small" if small_tank else "demo", "jam": list(jam),
                           "hw_warn_s": min(warn) if warn else None, "hw_amber_s": m["first_amber_s"], "hw_red_s": m["first_red_s"],
                           "hw_false_before_attack": m["false_alarms_before_attack"], "final_state": m["final_state"]}
    for name, p in panels.items():
        hit = p.first_after(r.attack_t)
        out[f"{name}_s"] = None if hit is None else round(hit[0], 1)
        out[f"{name}_kind"] = None if hit is None else hit[1]
        out[f"{name}_false_before_attack"] = sum(1 for t, _ in p.events if t < r.attack_t)
    out["checks"] = checks
    return out


def _attack_task(args: tuple) -> dict:
    sid, seed, op, jam, horizon_s, check_every_s, small = args
    return run_attack(sid, seed, op, jam=tuple(jam), horizon_s=horizon_s, check_every_s=check_every_s, small_tank=small)


# ---- one normal run (no attack) --------------------------------------------------------------------------------------
def _small_tank() -> SiteProfile:
    return demo_profile().model_copy(update={"tank_diameter_ft": 30.0, "tank_area_sqft": math.pi * 15.0 ** 2})


def run_normal(days: float, seed: int, small_tank: bool, *, dt: float = 10.0, check_every_s: float = NORMAL_CHECK_EVERY_S) -> dict:
    """A healthy plant, honest screen, two nodes, full system. Every alarm or warning here is a false one."""
    p = _small_tank() if small_tank else demo_profile()
    sys_ = TankSystem(p, seed=seed, dt=dt, t0=T0)
    view = ScadaView(sys_, seed=seed + 1)
    nodes = [FakeNode(n, DEMO_SECRETS[n], pid, sys_, seed=seed + 2 + i, hb_s=10.0) for i, (n, pid) in enumerate((("NA", "P1"), ("NB", "P2")))]
    core = HydroWitnessCore(p, DEMO_SECRETS, clock=lambda: sys_.t, name="evaluation")
    panels = _panels(p)
    changes, last, checks = 0, dict(sys_.running), []
    every = max(1, int(round(check_every_s / dt)))
    for i in range(1, int(days * 86400 / dt) + 1):
        sys_.step()
        changes += sum(1 for k, v in sys_.running.items() if last[k] != v)
        last = dict(sys_.running)
        snap = view.snapshot()
        for pn in panels.values():
            pn.step(sys_.t, snap)
        core.ingest_scada(snap)
        for n in nodes:
            for w in n.step(sys_.t):
                core.ingest_node(w, sys_.t)
        v = core.evaluate(sys_.t)
        if i % every == 0:
            checks.append(_checkpoint(v, sys_, p, i * dt))
    recs = [x for x in core.log.records if x["type"] == "trust_state"]
    return {"seed": seed, "small_tank": small_tank, "days": days, "pump_state_changes": changes,
            "hw_false_amber": sum(1 for x in recs if x["content"]["to"] == "amber"),
            "hw_false_red": sum(1 for x in recs if x["content"]["to"] == "red"),
            "alarm_false": len(panels["alarm"].events), "alarm_flatline_false": len(panels["alarm_flatline"].events),
            "alarm_kinds": sorted({k for pn in panels.values() for _, k in pn.events}), "checks": checks}


def _normal_task(args: tuple) -> dict:
    days, seed, small = args
    return run_normal(days, seed, small)


# ---- summaries -------------------------------------------------------------------------------------------------------
def _stats(delays: list[float], n: int) -> dict:
    return {"detected": len(delays), "missed": n - len(delays), "runs": n,
            "median_s": None if not delays else statistics.median(delays), "max_s": None if not delays else max(delays)}


def summarize_detection(runs: list[dict]) -> dict:
    n = len(runs)
    out = {"runs": n,
           "hydrowitness_warned": _stats([r["hw_warn_s"] for r in runs if r["hw_warn_s"] is not None], n),
           "hydrowitness_red": _stats([r["hw_red_s"] for r in runs if r["hw_red_s"] is not None], n)}
    for name in PANELS:
        out[name] = _stats([r[f"{name}_s"] for r in runs if r[f"{name}_s"] is not None], n)
        out[name]["kinds"] = sorted({r[f"{name}_kind"] for r in runs if r[f"{name}_kind"]})
    out["false_before_attack"] = {"hydrowitness": sum(r["hw_false_before_attack"] for r in runs),
                                  **{name: sum(r[f"{name}_false_before_attack"] for r in runs) for name in PANELS}}
    return out


def summarize_countdown(runs: list[dict]) -> dict:
    rows = [score_checkpoint(c) for r in runs for c in r["checks"]]
    scored = [x for x in rows if x["kind"] == "scored"]
    errs = [x["error_min"] for x in scored]
    cls = {k: sum(1 for x in scored if x["class"] == k) for k in ("early", "close", "late")}
    return {"checkpoints": len(rows), "unavailable": sum(1 for x in rows if x["kind"] == "unavailable"),
            "not_modelled_valve": sum(1 for x in rows if x["kind"] == "not_modelled"),
            "skipped_pump_credited": sum(1 for x in rows if x["kind"] == "skipped"), "scored": len(scored),
            "worst_case_bound_held": sum(1 for x in scored if x["bound_held"]),
            "early": cls["early"], "close": cls["close"], "late": cls["late"],
            "median_error_min": None if not errs else round(statistics.median(errs), 1),
            "max_late_min": None if not errs else round(max(0.0, max(errs)), 1),
            "max_early_min": None if not errs else round(max(0.0, -min(errs)), 1)}


def summarize_normal(runs: list[dict]) -> dict:
    days = sum(r["days"] for r in runs)
    return {"runs": len(runs), "site_days": days, "pump_state_changes": sum(r["pump_state_changes"] for r in runs),
            "hydrowitness_false_amber": sum(r["hw_false_amber"] for r in runs),
            "hydrowitness_false_red": sum(r["hw_false_red"] for r in runs),
            "alarm_false": sum(r["alarm_false"] for r in runs), "alarm_flatline_false": sum(r["alarm_flatline_false"] for r in runs),
            "false_per_site_day": {"hydrowitness": round((sum(r["hw_false_amber"] + r["hw_false_red"] for r in runs)) / days, 3),
                                   "alarm": round(sum(r["alarm_false"] for r in runs) / days, 3),
                                   "alarm_flatline": round(sum(r["alarm_flatline_false"] for r in runs) / days, 3)},
            "countdown": summarize_countdown(runs),
            "countdown_by_plant": {name: summarize_countdown([r for r in runs if r["small_tank"] is small])
                                   for name, small in (("demo", False), ("small", True))}}


def _config(seeds, horizon_s, check_every_s, normal_days, normal_seeds) -> dict:
    p = demo_profile()
    node_defaults = {k: v.default for k, v in inspect.signature(FakeNode.__init__).parameters.items()
                     if k in ("delay_s", "p_acc_err", "p_mic_err", "hb_p_unsure_acc", "hb_p_unsure_mic", "hb_p_disagree")}
    return {
        "schema": SCHEMA, "software_version": VERSION, "seeds": list(seeds), "attack_horizon_s": horizon_s,
        "countdown_checkpoint_every_s": check_every_s, "step_s": 5.0, "warmup_s": 2400.0,
        "normal": {"days_per_run": normal_days, "seeds_per_plant": normal_seeds, "step_s": 10.0, "plants": ["demo tank (70 ft)", "small tank (30 ft)"]},
        "attack_plants": ["demo tank (70 ft)", "small tank (30 ft)"],
        "scenarios": [s for s in SCENARIOS if s != "free_play"],
        "alarm_panel": {"low_level_ft": LOW_LEVEL_ALARM_FT, "low_pressure_psi": p.min_pressure_psi, "no_data_after_s": p.stale_after_s,
                        "flat_line_after_s": FLAT_LINE_S, "note": "Both variants believe the screen. The flat-line variant adds one generous rule."},
        "countdown_scoring": {"truth": "the simulated plant with every pump stopped (the case the no-inflow countdown assumes)",
                              "truth_step_s": TRUTH_STEP_S, "truth_horizon_h": TRUTH_HORIZON_H, "close_within_min": TOLERANCE_MIN,
                              "close_within_share": TOLERANCE_FRAC, "worst_case_slack_min": BOUND_SLACK_MIN,
                              "skipped_when": "a pump's flow was credited",
                              "not_modelled_when": "a valve is closed: the simulation does not work out the pressure beyond it",
                              "built_in_earliness": "the simulated plant's real friction loss is 80% of the headloss margin typed into the profile, "
                                                    "so a countdown built on the typed margin comes out early by design"},
        "demo_plant": {"tank_diameter_ft": p.tank_diameter_ft, "peak_demand_gpm": p.peak_demand_gpm, "min_pressure_psi": p.min_pressure_psi,
                       "pumps_gpm": {x.id: x.rated_gpm for x in p.pumps}},
        "witness_node_error_model": node_defaults,
        "failure_conditions": [{"id": f["id"], "scenario": f["scenario"], "jammed_nodes": f["jam"], "text": f["text"]} for f in FAILURE_CONDITIONS],
        "limits": ["Simulated plant, simulated screen, simulated Witness Nodes.",
                   "The Witness Node error rates are assumptions, not measurements of a real pump.",
                   "The firmware has booted on a real ESP32 board, but no recording from a real pump is used here: this evaluation uses no real measurements.",
                   "These are results against scripted scenarios, not a forecast for a real water system."],
    }


def run(seeds=SEEDS, horizon_s: float = HORIZON_S, check_every_s: float = CHECK_EVERY_S, normal_days: float = NORMAL_DAYS,
        normal_seeds: int = NORMAL_SEEDS, jobs: int = 1, scenarios: Optional[list[str]] = None, failures: bool = True,
        normal: bool = True) -> dict:
    scen = [s for s in (scenarios or list(SCENARIOS)) if s != "free_play"]
    tasks = [(sid, seed, op, [], horizon_s, check_every_s, small) for sid in scen for op in (False, True) for small in (False, True) for seed in seeds]
    ftasks = [(f["scenario"], seed, False, f["jam"], horizon_s, check_every_s, False) for f in FAILURE_CONDITIONS for seed in seeds] if failures else []
    ntasks = [(normal_days, seed, small) for small in (False, True) for seed in range(1, normal_seeds + 1)] if normal else []

    def run_all(fn, items):
        if jobs > 1 and len(items) > 1:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                return list(pool.map(fn, items, chunksize=1))
        return [fn(x) for x in items]

    attack = run_all(_attack_task, tasks)
    fail = run_all(_attack_task, ftasks)
    norm = run_all(_normal_task, ntasks)

    summary: dict[str, Any] = {"attacks": {}, "failure_conditions": {}}
    for sid in scen:
        for op in (False, True):
            group = [r for r in attack if r["scenario"] == sid and r["operator"] is op]
            summary["attacks"][f"{sid}|{'operator' if op else 'no operator'}"] = {
                "scenario": sid, "name": SCENARIOS[sid].name, "operator": op,
                "detection": summarize_detection(group), "countdown": summarize_countdown(group)}
    for f in (FAILURE_CONDITIONS if failures else []):
        group = [r for r in fail if r["scenario"] == f["scenario"] and r["jam"] == f["jam"]]
        summary["failure_conditions"][f["id"]] = {"text": f["text"], "scenario": f["scenario"], "jammed_nodes": f["jam"],
                                                  "detection": summarize_detection(group), "countdown": summarize_countdown(group)}
    if normal:
        summary["normal_operation"] = summarize_normal(norm)
    all_attack = [r for r in attack]
    summary["totals"] = {
        "attack_runs": len(attack), "failure_runs": len(fail), "normal_runs": len(norm),
        "attack_runs_without_operator": sum(1 for r in attack if not r["operator"]),
        "hydrowitness_missed_without_operator": sum(1 for r in attack if not r["operator"] and r["hw_warn_s"] is None),
        "alarm_missed_without_operator": sum(1 for r in attack if not r["operator"] and r["alarm_s"] is None),
        "alarm_flatline_missed_without_operator": sum(1 for r in attack if not r["operator"] and r["alarm_flatline_s"] is None),
        "countdown": summarize_countdown(all_attack),
        "countdown_by_plant": {name: summarize_countdown([r for r in all_attack if r["plant"] == name]) for name in ("demo", "small")},
    }
    return {"config": _config(seeds, horizon_s, check_every_s, normal_days, normal_seeds),
            "summary": summary, "runs": {"attacks": attack, "failure_conditions": fail, "normal": norm}}


# ---- the human-readable report ---------------------------------------------------------------------------------------
def _t(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.0f} s"


def _det(d: dict) -> str:
    return f"{d['detected']}/{d['runs']}" + ("" if d["median_s"] is None else f" ({_t(d['median_s'])} / {_t(d['max_s'])})")


def _hours(m: Optional[float]) -> str:
    return "-" if m is None else f"{m / 60:.1f}"


def _countdown_cells(k: dict) -> str:
    return (f"{k['checkpoints']} | {k['unavailable']} | {k['not_modelled_valve']} | {k['scored']} | {k['worst_case_bound_held']}/{k['scored']} | "
            f"{k['early']} | {k['close']} | {k['late']} | {_hours(k['median_error_min'])} | {_hours(k['max_late_min'])}")


def markdown(res: dict) -> str:
    s, c = res["summary"], res["config"]
    L = ["# Evaluation: HydroWitness against a plain alarm panel (simulated plant, simulated nodes)", "",
         "Everything below is a simulation. The plant, the screen's lies and the Witness Nodes are simulated, the nodes' error rates are "
         "assumptions, and the firmware has so far only booted on a real board, not watched a real pump. **This evaluation uses no real "
         "measurements.** Same seeds, same numbers: regenerate with `python -m hydrowitness.sim.evaluation`.", "",
         f"Seeds {c['seeds'][0]} to {c['seeds'][-1]}, on two tank sizes (the demo tank and a small one that holds hours of water instead of days), "
         f"{c['attack_horizon_s'] / 60:.0f} minutes allowed after the start of each attack, software {c['software_version']}.", "",
         "## Did anything notice? (detected runs, with median / max delay after the attack began)", "",
         "| scenario | operator walks to the pump | HydroWitness warned | HydroWitness red | Alarm panel | Alarm panel + flat-line check |",
         "|---|---|---|---|---|---|"]
    for a in s["attacks"].values():
        d = a["detection"]
        L.append(f"| {a['name']} | {'yes' if a['operator'] else 'no'} | {_det(d['hydrowitness_warned'])} | {_det(d['hydrowitness_red'])} | "
                 f"{_det(d['alarm'])} | {_det(d['alarm_flatline'])} |")
    tot = s["totals"]
    L += ["", f"Across the {tot['attack_runs_without_operator']} runs where nobody walks to the pump, HydroWitness raised nothing in "
              f"{tot['hydrowitness_missed_without_operator']}, the alarm panel in {tot['alarm_missed_without_operator']}, "
              f"and the alarm panel with the flat-line check in {tot['alarm_flatline_missed_without_operator']}.", "",
          "The alarm panel's alarms in these runs were: " + ", ".join(sorted({k for a in s['attacks'].values() for k in a['detection']['alarm']['kinds']}) or ["none"])
          + ".", ""]
    notes = []
    rp = s["attacks"].get("replay|no operator")
    if rp and rp["detection"]["alarm_flatline"]["detected"]:
        notes.append(f"On the replay, the flat-line check fires at {_t(rp['detection']['alarm_flatline']['median_s'])} only because the simulated attacker's "
                     "30-minute recording runs out and the screen then freezes. A replay that kept going would not trigger it.")
    vv = s["attacks"].get("hidden_valve_closure|no operator")
    if vv:
        notes.append(f"On the closed valve, HydroWitness's warning ({_t(vv['detection']['hydrowitness_warned']['median_s'])}) comes only from the four "
                     "scripted customer calls. The alarm panel has no way to receive them, though a dispatcher would hear them anyway. Customer calls "
                     "alone can never turn the light red, so it stays amber, by design.")
    fa = s["attacks"].get("forced_off_spoofed|no operator")
    if fa:
        notes.append("On the careful forgery, HydroWitness warns because a Witness Node disagrees with the screen. It reaches red only when a person "
                     "has looked at the pump, which is why the table has an operator column.")
    L += ["Reading the table:"] + [f"- {x}" for x in notes] + [""]
    if "normal_operation" in s:
        n = s["normal_operation"]
        L += ["## False alarms on a healthy plant", "",
              f"{n['site_days']:.0f} simulated site-days with two nodes, an honest screen and {n['pump_state_changes']} real pump starts and stops "
              f"(two tank sizes).", "",
              "| | alarms or warnings | per site-day |", "|---|---|---|",
              f"| HydroWitness (amber or red) | {n['hydrowitness_false_amber'] + n['hydrowitness_false_red']} "
              f"({n['hydrowitness_false_amber']} amber, {n['hydrowitness_false_red']} red) | {n['false_per_site_day']['hydrowitness']} |",
              f"| Alarm panel | {n['alarm_false']} | {n['false_per_site_day']['alarm']} |",
              f"| Alarm panel + flat-line check | {n['alarm_flatline_false']} | {n['false_per_site_day']['alarm_flatline']} |", ""]
    cd = tot["countdown"]
    L += ["## How close was the countdown?", "",
          "HydroWitness's expected-case countdown against the simulated plant's own answer with every pump stopped (the case the countdown "
          f"assumes when no one has confirmed that water is moving), looked up to {c['countdown_scoring']['truth_horizon_h']:.0f} hours ahead. Within "
          f"{c['countdown_scoring']['close_within_min']:.0f} minutes or {c['countdown_scoring']['close_within_share'] * 100:.0f}% (whichever is larger) counts as close. "
          "**Early** means HydroWitness said trouble would come sooner than it did, which costs a little caution. "
          "**Late** means HydroWitness said trouble would come later than it did, the dangerous direction. The alarm panel gives no countdown, "
          "so it is not scored. A closed valve is **not modelled**: the simulation does not work out the pressure beyond it, so there is no true "
          "answer to score against. The simulated plant's real friction loss is 80% of the margin typed into the profile, so a countdown "
          "built on the typed margin comes out early by design.", "",
          "| scenario | operator | checkpoints | unavailable | not modelled | scored | worst case held | early | close | late | median error (h) | worst late (h) |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in s["attacks"].values():
        k = a["countdown"]
        L.append(f"| {a['name']} | {'yes' if a['operator'] else 'no'} | {_countdown_cells(k)} |")
    L.append(f"| **All attack runs** | | {_countdown_cells(cd)} |")
    def row(label, k):
        L.append(f"| **{label}** | | {_countdown_cells(k)} |")
    row("Attack runs, demo tank", tot["countdown_by_plant"]["demo"])
    row("Attack runs, small tank", tot["countdown_by_plant"]["small"])
    if "normal_operation" in s:
        row("Healthy plant, demo tank", s["normal_operation"]["countdown_by_plant"]["demo"])
        row("Healthy plant, small tank", s["normal_operation"]["countdown_by_plant"]["small"])
    if s["failure_conditions"]:
        L += ["", "## Failure conditions: attacks built to break HydroWitness", "",
              "| condition | HydroWitness warned | HydroWitness red | Alarm panel | Alarm panel + flat-line check |", "|---|---|---|---|---|"]
        for f in s["failure_conditions"].values():
            d = f["detection"]
            L.append(f"| {f['text']} | {_det(d['hydrowitness_warned'])} | {_det(d['hydrowitness_red'])} | {_det(d['alarm'])} | {_det(d['alarm_flatline'])} |")
    if s["failure_conditions"]:
        L += ["", "When the witnesses are jammed, HydroWitness's warning is \"a Witness Node went silent\", not \"the pump is off\". "
                  "It noticed it lost its witnesses, but it cannot tell what the pumps are doing, so it does not reach red unless something else "
                  "gives the attack away (here, the replayed screen's repeated sequence)."]
    L += ["", "## Settings", "", "```json", json.dumps({k: c[k] for k in ("alarm_panel", "countdown_scoring", "normal", "witness_node_error_model")}, indent=1), "```", "",
          "## Limits", ""] + [f"- {x}" for x in c["limits"]] + [""]
    return "\n".join(L)


def write(res: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "evaluation.json"), "w", newline="\n") as fh:
        json.dump(res, fh, indent=1)
        fh.write("\n")
    with open(os.path.join(out_dir, "evaluation.md"), "w", newline="\n") as fh:
        fh.write(markdown(res))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=len(SEEDS), help="seeds 1..N for every attack run")
    ap.add_argument("--normal-days", type=float, default=NORMAL_DAYS)
    ap.add_argument("--normal-seeds", type=int, default=NORMAL_SEEDS, help="seeds per plant size for the healthy-plant runs")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="worker processes (results do not depend on this)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    res = run(tuple(range(1, a.seeds + 1)), normal_days=a.normal_days, normal_seeds=a.normal_seeds, jobs=a.jobs)
    write(res, a.out)
    print(markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
