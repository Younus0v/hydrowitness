"""Long false-alarm study: healthy simulated plants, honest screens, many days, seeds the tuning never saw.

The main evaluation (results/evaluation.md) covers 24 simulated site-days of normal operation. That is too few to say how
often HydroWitness would cry wolf. A system that warns falsely once a week gets ignored, so this is one of the claims that
most needs numbers. This script runs many more healthy site-days on seeds 1000 and up (the evaluation uses 1 to 10, the
drills 1 to 5), and reports the counts with an exact 95% upper bound on the false-alarm rate.

Everything is simulated. A clean run here says the software does not invent trouble on a simulated plant with the noise
model written in the evaluation config. It says nothing yet about a real plant.

Run:  python3 tools/long_normal.py [--runs 80] [--days 5] [--workers 2] [--out results]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hydrowitness import stats                       # noqa: E402
from hydrowitness.sim import evaluation as ev        # noqa: E402

FIRST_SEED = 1000


def _task(args):
    days, seed, small = args
    r = ev.run_normal(days, seed, small)
    r.pop("checks", None)
    return r


def run(runs: int, days: float, workers: int, partial: Path | None = None) -> dict:
    """Runs are saved one by one to `partial` (a .jsonl file), so an interrupted study resumes where it stopped."""
    tasks = [(days, FIRST_SEED + i, i % 2 == 1) for i in range(runs)]
    done: dict[int, dict] = {}
    if partial is not None and partial.exists():
        for line in partial.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["seed"]] = r
    todo = [t for t in tasks if t[1] not in done]
    t0 = time.time()

    def keep(r):
        done[r["seed"]] = r
        if partial is not None:
            with partial.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r) + "\n")

    if workers > 1 and todo:
        with mp.Pool(workers) as pool:
            for r in pool.imap_unordered(_task, todo, chunksize=1):
                keep(r)
    else:
        for t in todo:
            keep(_task(t))
    rows = [done[t[1]] for t in tasks]
    site_days = sum(r["days"] for r in rows)
    fa, fr = sum(r["hw_false_amber"] for r in rows), sum(r["hw_false_red"] for r in rows)
    alarm, flat = sum(r["alarm_false"] for r in rows), sum(r["alarm_flatline_false"] for r in rows)
    changes = sum(r["pump_state_changes"] for r in rows)
    return {
        "schema": 1, "runs": runs, "days_per_run": days, "first_seed": FIRST_SEED, "site_days": site_days,
        "plants": {"demo_tank_70ft": sum(1 for r in rows if not r["small_tank"]), "small_tank_30ft": sum(1 for r in rows if r["small_tank"])},
        "pump_state_changes": changes,
        "hydrowitness_false_amber": fa, "hydrowitness_false_red": fr,
        "alarm_panel_false": alarm, "alarm_panel_flatline_false": flat,
        "rate_upper_95_per_site_day": {
            "hydrowitness_amber": stats.poisson_upper(fa, site_days),
            "hydrowitness_red": stats.poisson_upper(fr, site_days),
        },
        "wall_seconds": round(time.time() - t0, 1),
        "runs_detail": rows,
    }


def markdown(res: dict) -> str:
    sd = res["site_days"]
    ua = res["rate_upper_95_per_site_day"]["hydrowitness_amber"]
    ur = res["rate_upper_95_per_site_day"]["hydrowitness_red"]
    return "\n".join([
        "# Long false-alarm study (simulated healthy plants)",
        "",
        f"{res['runs']} runs of {res['days_per_run']:g} simulated days each, {sd:g} site-days in all, on seeds {res['first_seed']} and up "
        "(the evaluation uses seeds 1 to 10, the drills 1 to 5, so none of these were seen during tuning). Half the runs use the 70 ft demo tank, "
        "half a 30 ft tank, which cycles its pumps far more often. Honest screen, two Witness Nodes, full system. Every warning here is a false one.",
        "",
        "| | count | per site-day | 95% upper bound per site-day |",
        "|---|---|---|---|",
        f"| HydroWitness false amber | {res['hydrowitness_false_amber']} | {res['hydrowitness_false_amber'] / sd:.4f} | {ua:.4f} |",
        f"| HydroWitness false red | {res['hydrowitness_false_red']} | {res['hydrowitness_false_red'] / sd:.4f} | {ur:.4f} |",
        f"| Alarm panel, false alarms | {res['alarm_panel_false']} | {res['alarm_panel_false'] / sd:.4f} | |",
        f"| Alarm panel with flat-line rule, false alarms | {res['alarm_panel_flatline_false']} | {res['alarm_panel_flatline_false'] / sd:.4f} | |",
        "",
        f"Pump starts and stops the software had to follow without crying wolf: {res['pump_state_changes']}.",
        "",
        "How to read this: the upper bound is exact (Poisson, two-sided 95%). With zero events it equals 3.69 divided by the site-days. "
        f"So {sd:g} clean site-days cannot rule out a false amber about once every {1 / ua:.0f} site-days, and cannot rule out a false red about once every {1 / ur:.0f}. "
        "More clean days tighten the bound; they never make it zero.",
        "",
        "What this does not show: a real plant has noise, drift and equipment this simulation does not have. These are simulated days.",
        "",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=80)
    ap.add_argument("--days", type=float, default=5.0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = run(a.runs, a.days, a.workers, out / "long_normal.partial.jsonl")
    (out / "long_normal.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    (out / "long_normal.md").write_text(markdown(res), encoding="utf-8")
    (out / "long_normal.partial.jsonl").unlink(missing_ok=True)
    print(markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
