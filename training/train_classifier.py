#!/usr/bin/env python3
"""Measure whether a real node can tell a real pump's ON from its OFF (validation V3).

Workflow (docs/HARDWARE_STAGE.md, stage 8)
  1. Mount the node on the real pump (or the nearest stand-in) exactly as it will be installed.
  2. Connect USB, open a serial terminal at 115200 baud and log everything to a file.
  3. Type `dump on`. With the pump OFF type `label S`; later, with the pump ON and steady, type `label R`.
     Change the label back to `label -` whenever the pump is starting, stopping or you are touching the box.
     Do this several times, on different days if you can (different noise, different pump warm-up).
  4. Run:  python3 training/train_classifier.py session1.log session2.log

The node does not use a classifier trained here: it learns its own on site during install calibration. This
script answers the question that decides whether the whole witness idea works on that equipment: do the
features separate ON from OFF on data the model has not seen, in time periods it was not trained on?

Rows it reads are the node's own feature rows:  F,<ms>,<label R|S|->,<channel a|m>,<15 numbers>
Everything else in the log is ignored.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import node_reference as nr  # noqa: E402

GAP_MS = 700                      # consecutive windows are 512 ms apart; more than this is a break in the recording
MIN_PER_CLASS = 10                # thinned windows needed in each class before any number is trusted
CHANNELS = {"a": "acc", "m": "mic"}


# ------------------------------------------------------------------------------------------- reading
@dataclass
class Rows:
    data: dict = field(default_factory=dict)         # (channel, label) -> list of (ms, features)
    bad: int = 0
    unlabeled: int = 0


def parse(lines: Iterable[str]) -> Rows:
    out = Rows()
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("F,"):
            continue
        f = ln.split(",")
        if len(f) != 4 + nr.NFEAT:
            out.bad += 1                              # a row cut short by a serial hiccup
            continue
        try:
            ms = int(f[1])
            x = np.array([float(v) for v in f[4:]], dtype=np.float64)
        except ValueError:
            out.bad += 1
            continue
        label, ch = f[2], f[3]
        if ch not in CHANNELS or not np.all(np.isfinite(x)):
            out.bad += 1
            continue
        if label not in ("R", "S"):
            out.unlabeled += 1
            continue
        out.data.setdefault((ch, label), []).append((ms, x))
    return out


def segments(rows: list) -> list[list]:
    """Runs of consecutive windows with no gap (a new run starts at every relabel, pause or restart)."""
    rows = sorted(rows, key=lambda r: r[0])
    segs, cur = [], []
    for r in rows:
        if cur and r[0] - cur[-1][0] > GAP_MS:
            segs.append(cur)
            cur = []
        cur.append(r)
    if cur:
        segs.append(cur)
    return segs


def usable_windows(rows: list, trim_s: float) -> np.ndarray:
    """Drop the transitions at both ends of each run (the label was typed late or early), then keep every second
    window so no two kept windows share a block of audio (adjacent windows overlap by half)."""
    keep = []
    for seg in segments(rows):
        t0, t1 = seg[0][0] + trim_s * 1000, seg[-1][0] - trim_s * 1000
        core = [r for r in seg if t0 <= r[0] <= t1]
        keep += core[::2]
    return np.array([x for _, x in keep]) if keep else np.zeros((0, nr.NFEAT))


# ---------------------------------------------------------------------------------------- evaluation
@dataclass
class ChannelReport:
    channel: str
    n_running: int
    n_stopped: int
    enough: bool
    node_style_accuracy: tuple = (0.0, 0.0, 0.0)      # running, stopped, min: the install-time check
    node_verdict: str = "POOR"
    cv_running: float = 0.0
    cv_stopped: float = 0.0
    cv_worst_fold: float = 0.0
    cv_quality: float = 0.0
    cv_verdict: str = "POOR"
    separation: float = 0.0                           # Mahalanobis distance between the two classes
    top_features: list = field(default_factory=list)


def verdict(q: float) -> str:
    return "GOOD" if q >= nr.GOOD_ACCURACY else "FAIR" if q >= nr.FAIR_ACCURACY else "POOR"


def evaluate(channel: str, R: np.ndarray, S: np.ndarray, folds: int = 5) -> ChannelReport:
    rep = ChannelReport(CHANNELS[channel], len(R), len(S), len(R) >= MIN_PER_CLASS and len(S) >= MIN_PER_CLASS)
    if not rep.enough:
        return rep
    cal = nr.calibrate(R, S)
    rep.node_style_accuracy = (cal.acc_r, cal.acc_s, cal.quality)
    rep.node_verdict = cal.verdict
    rep.separation = math.sqrt(cal.model.d2)
    contrib = (cal.model.mu_r - cal.model.mu_s) ** 2 / cal.model.var
    names = nr.FEATURES[CHANNELS[channel]]
    order = np.argsort(contrib)[::-1][:3]
    rep.top_features = [(names[i], float(contrib[i] / max(contrib.sum(), 1e-12))) for i in order]

    # Blocked cross-validation: each fold is a contiguous stretch of time, so a model is always tested on a
    # period it never saw (neighbouring windows are alike and would flatter a shuffled split).
    ra, rs = [], []
    r_idx, s_idx = np.array_split(np.arange(len(R)), folds), np.array_split(np.arange(len(S)), folds)
    for k in range(folds):
        tr_r = np.concatenate([r_idx[j] for j in range(folds) if j != k])
        tr_s = np.concatenate([s_idx[j] for j in range(folds) if j != k])
        m = nr.fit(R[tr_r], S[tr_s])
        ra.append(float(np.mean([m.z(x) > 0 for x in R[r_idx[k]]])))
        rs.append(float(np.mean([m.z(x) < 0 for x in S[s_idx[k]]])))
    rep.cv_running, rep.cv_stopped = float(np.mean(ra)), float(np.mean(rs))
    rep.cv_worst_fold = float(min(min(a, b) for a, b in zip(ra, rs)))
    rep.cv_quality = min(rep.cv_running, rep.cv_stopped)
    rep.cv_verdict = verdict(rep.cv_quality)
    return rep


def agreement(rows: Rows) -> dict:
    """With both channels logged at the same moments: how often do they agree, and how often do they both say
    the wrong thing at the same time (the case a second sensor cannot rescue)."""
    by = {}
    for (ch, label), lst in rows.data.items():
        for ms, x in lst:
            by.setdefault(ms, {})[ch] = (label, x)
    pairs = [(v["a"], v["m"]) for v in by.values() if "a" in v and "m" in v and v["a"][0] == v["m"][0]]
    if len(pairs) < 2 * MIN_PER_CLASS:
        return {}
    R = {ch: usable_windows(rows.data.get((ch, "R"), []), 5.0) for ch in "am"}
    S = {ch: usable_windows(rows.data.get((ch, "S"), []), 5.0) for ch in "am"}
    if any(len(R[c]) < MIN_PER_CLASS or len(S[c]) < MIN_PER_CLASS for c in "am"):
        return {}
    models = {c: nr.fit(R[c], S[c]) for c in "am"}
    both_right = contradict = both_wrong = unsure = 0
    for (la, xa), (_, xm) in pairs:
        za, zm = models["a"].z(xa), models["m"].z(xm)
        sa, sm = nr.z_to_state(za), nr.z_to_state(zm)
        truth = la
        if sa == "U" or sm == "U":
            unsure += 1
        elif sa == sm == truth:
            both_right += 1
        elif sa == sm:
            both_wrong += 1
        else:
            contradict += 1
    n = len(pairs)
    return {"windows": n, "both_right": both_right / n, "channels_contradict": contradict / n,
            "both_wrong": both_wrong / n, "at_least_one_unsure": unsure / n,
            "note": "fitted and scored on the same data: optimistic, use only to compare channels"}


# ------------------------------------------------------------------------------------------- report
def overall(reports: list[ChannelReport]) -> tuple[str, str]:
    ok = [r for r in reports if r.enough]
    if not ok:
        return "NO DATA", "Not enough labelled windows. Log at least ~30 s of each of pump OFF and pump ON per channel."
    best = max(ok, key=lambda r: r.cv_quality)
    if best.cv_verdict == "GOOD" and best.cv_worst_fold >= nr.FAIR_ACCURACY:
        both = sum(1 for r in ok if r.cv_verdict == "GOOD")
        return "PASS", ("Both channels separate ON from OFF on unseen data." if both == len(ok) and len(ok) == 2 else
                        f"The {best.channel} channel separates ON from OFF on unseen data; the other is weaker or missing, so "
                        "the node will report with single-channel confidence only.")
    if best.cv_verdict in ("GOOD", "FAIR"):
        return "MARGINAL", ("Separation is there but not reliable in every time period. Move the node (closer to the motor "
                            "end bell, on bare metal, away from the wall) and repeat, or log on another day.")
    return "FAIL", ("No channel separates ON from OFF reliably on this equipment as mounted. This is a finding, not a "
                    "bug: say so in the submission, and try a different mounting point before concluding anything.")


def format_report(rows: Rows, reports: list[ChannelReport], agree: dict) -> str:
    out = [f"Rows read: {sum(len(v) for v in rows.data.values())} labelled, {rows.unlabeled} unlabelled (ignored), "
           f"{rows.bad} damaged (ignored)", ""]
    for r in reports:
        out.append(f"{r.channel.upper()} channel: {r.n_running} running / {r.n_stopped} stopped usable windows")
        if not r.enough:
            out += [f"  not enough data (need {MIN_PER_CLASS} of each)", ""]
            continue
        a, b, q = r.node_style_accuracy
        out += [
            f"  install-style check (what the node itself would say): running {a:.0%}, stopped {b:.0%} -> {r.node_verdict}",
            f"  cross-validated on unseen stretches of time:           running {r.cv_running:.0%}, stopped {r.cv_stopped:.0%}"
            f" (worst fold {r.cv_worst_fold:.0%}) -> {r.cv_verdict}",
            f"  separation (Mahalanobis distance): {r.separation:.1f}",
            "  features doing the work: " + ", ".join(f"{n} ({s:.0%})" for n, s in r.top_features), ""]
    if agree:
        out += [f"Both channels logged together ({agree['windows']} windows):",
                f"  both right {agree['both_right']:.0%}, contradict each other {agree['channels_contradict']:.0%}, "
                f"both wrong {agree['both_wrong']:.0%}, at least one unsure {agree['at_least_one_unsure']:.0%}",
                f"  ({agree['note']})", ""]
    status, why = overall(reports)
    out += [f"V3 result: {status}", why]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", help="serial logs containing the node's F, rows")
    ap.add_argument("--trim-s", type=float, default=5.0, help="seconds dropped at both ends of every labelled run")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--json", help="also write the results here")
    args = ap.parse_args(argv)

    lines = []
    for p in args.logs:
        with open(p, errors="replace") as fh:
            lines += fh.readlines()
    rows = parse(lines)
    reports = []
    for ch in "am":
        R = usable_windows(rows.data.get((ch, "R"), []), args.trim_s)
        S = usable_windows(rows.data.get((ch, "S"), []), args.trim_s)
        reports.append(evaluate(ch, R, S, args.folds))
    agree = agreement(rows)
    print(format_report(rows, reports, agree))
    if args.json:
        status, why = overall(reports)
        with open(args.json, "w") as fh:
            json.dump({"result": status, "why": why, "channels": [r.__dict__ for r in reports], "agreement": agree}, fh, indent=2)
    return {"PASS": 0, "MARGINAL": 1, "FAIL": 2, "NO DATA": 3}[overall(reports)[0]]


if __name__ == "__main__":
    sys.exit(main())
