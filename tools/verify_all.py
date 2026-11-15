"""One command that re-checks everything we claim, and says plainly what it did and did not check.

    python3 tools/verify_all.py            # about 2 minutes: tests, reports up to date with their data, website matches the results
    python3 tools/verify_all.py --quick    # about 15 seconds: skips the test suite (reports and website only)
    python3 tools/verify_all.py --full     # about 25 minutes: also re-runs the simulations from scratch (V1, V2, evaluation, long study)
                                           # and then checks the written reports against the fresh numbers

What a pass means: the saved result files, the written statistics, the website text and the code agree with each other, and the
tests pass. It does NOT mean the software works on a real plant. Every number checked here comes from simulation.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def run(label: str, cmd: list[str], results: list, *, env_extra: dict | None = None) -> bool:
    print(f"\n=== {label}\n$ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    env = dict(os.environ, **(env_extra or {}))
    p = subprocess.run(cmd, cwd=ROOT, env=env)
    ok = p.returncode == 0
    results.append((label, ok, time.time() - t0))
    print(f"--- {'OK' if ok else 'FAILED'} ({time.time() - t0:.0f} s)", flush=True)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="skip the test suite")
    ap.add_argument("--full", action="store_true", help="also re-run the simulations (about 25 minutes)")
    ap.add_argument("--long-runs", type=int, default=80, help="runs in the long false-alarm study when --full is used")
    a = ap.parse_args(argv)
    if a.quick and a.full:
        ap.error("--quick and --full cannot be combined")
    results: list[tuple[str, bool, float]] = []
    notes: list[str] = []

    if not a.quick:
        cmd = [PY, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
        if have("xdist"):
            cmd += ["-n", "4"]
        else:
            notes.append("pytest-xdist is not installed, so the tests run on one process and are slower (pip install pytest-xdist).")
        if not have("wntr") or not have("six"):
            notes.append("wntr (or six) is not importable, so the EPANET validation tests were SKIPPED (pip install -r requirements.txt).")
        if not have("hypothesis"):
            notes.append("hypothesis is not installed, so the property-based tests were SKIPPED (pip install hypothesis).")
        run("tests", cmd, results)

    if a.full:
        run("V1: clock against EPANET (310 systems)", [PY, "-m", "hydrowitness.sim.wntr_validation"], results)
        run("V2: attack drills", [PY, "-m", "hydrowitness.sim.validation_report"], results)
        run("evaluation against a plain alarm panel", [PY, "-m", "hydrowitness.sim.evaluation", "--jobs", "2"], results)
        run("long false-alarm study", [PY, "tools/long_normal.py", "--runs", str(a.long_runs), "--days", "5", "--workers", "2"], results)
        run("regenerate statistics", [PY, "tools/make_statistics.py"], results)
        run("regenerate sensitivity analysis", [PY, "tools/sensitivity.py"], results)
        run("regenerate website", [PY, "tools/build_site.py"], results)
        notes.append("--full rewrote files in results/ and site/. Review them with `git diff` and commit them if the change is expected. "
                     "A result that got worse is a finding to report, not a file to revert.")

    run("website matches the saved results", [PY, "tools/build_site.py", "--check"], results)
    run("statistics match the saved results", [PY, "tools/make_statistics.py", "--check"], results)
    run("sensitivity analysis is current", [PY, "tools/sensitivity.py", "--check"], results)

    print("\n" + "=" * 60)
    for label, ok, secs in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}  ({secs:.0f} s)")
    for n in notes:
        print(f"NOTE  {n}")
    bad = [r for r in results if not r[1]]
    print("\nEverything checked agrees." if not bad else f"\n{len(bad)} check(s) failed.")
    print("Reminder: all of this is simulation and code consistency. It says nothing about a real plant or a real pump.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
