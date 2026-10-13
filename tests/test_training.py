"""The V3 analysis script (training/train_classifier.py), checked on synthetic serial logs.

Synthetic signals only show that the script reads the node's log format, thins and splits the data without
leakage, and reports PASS / MARGINAL / FAIL sensibly. They say nothing about a real pump.
"""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "training"))

import node_reference as nr  # noqa: E402
import synth  # noqa: E402
import train_classifier as tc  # noqa: E402


def feature_rows(kind, sig, truth_by_block, mains=60):
    n = nr.ACC_N if kind == "acc" else nr.MIC_N
    prev, rows = None, []
    for i in range(sig.shape[-1] // n):
        if kind == "acc":
            b = nr.acc_block(sig[0, i * n:(i + 1) * n], sig[1, i * n:(i + 1) * n], sig[2, i * n:(i + 1) * n], mains, prev)
        else:
            b = nr.mic_block(sig[i * n:(i + 1) * n], mains, prev)
        if prev is not None:
            rows.append(((i + 1) * 512, truth_by_block[i], nr.window_features(kind, prev, b)))
        prev = b
    return rows


def make_log(path, strength_acc=1.0, strength_mic=1.0, seed=3, junk=True):
    rng = np.random.default_rng(seed)
    sched = [(False, 100), (True, 100), (False, 100), (True, 100)]
    acc, mic, _ = synth.stream(sched, rng, strength_acc=strength_acc, strength_mic=strength_mic)
    nblocks = int(400 / 0.512)
    labels = []
    for i in range(nblocks):
        t = (i + 1) * 0.512
        seg = int(t // 100)
        near_switch = (t % 100) < 4 or (t % 100) > 98          # the operator types the label a moment late / early
        labels.append("-" if near_switch else ("R" if sched[min(seg, 3)][0] else "S"))
    lines = []
    if junk:
        lines += ["I,Witness Node NB boot 4", "S,phase=1,state=R", "garbage \x00 line", "F,12,R,a,1,2,3"]   # damaged row too
    for kind, ch, sig in (("acc", "a", acc), ("mic", "m", mic)):
        for ms, lab, f in feature_rows(kind, sig, labels):
            lines.append(f"F,{ms},{lab},{ch}," + ",".join(f"{v:.4f}" for v in f))
    path.write_text("\n".join(lines) + "\n")


def test_parse_ignores_noise_and_counts_damaged_rows(tmp_path):
    p = tmp_path / "s.log"
    make_log(p)
    rows = tc.parse(p.read_text().splitlines())
    assert rows.bad == 1 and rows.unlabeled > 0
    assert set(rows.data) == {("a", "R"), ("a", "S"), ("m", "R"), ("m", "S")}


def test_a_node_that_hears_the_pump_passes(tmp_path):
    p = tmp_path / "s.log"
    make_log(p)
    rows = tc.parse(p.read_text().splitlines())
    reps = [tc.evaluate(ch, tc.usable_windows(rows.data[(ch, "R")], 5.0), tc.usable_windows(rows.data[(ch, "S")], 5.0)) for ch in "am"]
    assert all(r.enough and r.cv_verdict == "GOOD" and r.cv_worst_fold >= 0.9 for r in reps)
    assert tc.overall(reps)[0] == "PASS"
    assert all(r.separation > 3 for r in reps)


def test_a_node_that_hears_nothing_fails_instead_of_flattering_itself(tmp_path):
    p = tmp_path / "s.log"
    make_log(p, strength_acc=0.0, strength_mic=0.0)
    rows = tc.parse(p.read_text().splitlines())
    reps = [tc.evaluate(ch, tc.usable_windows(rows.data[(ch, "R")], 5.0), tc.usable_windows(rows.data[(ch, "S")], 5.0)) for ch in "am"]
    assert all(r.cv_verdict == "POOR" for r in reps)
    assert tc.overall(reps)[0] == "FAIL"


def test_one_good_channel_is_a_pass_with_a_warning_about_single_channel_confidence(tmp_path):
    p = tmp_path / "s.log"
    make_log(p, strength_acc=0.0)
    rows = tc.parse(p.read_text().splitlines())
    reps = [tc.evaluate(ch, tc.usable_windows(rows.data[(ch, "R")], 5.0), tc.usable_windows(rows.data[(ch, "S")], 5.0)) for ch in "am"]
    status, why = tc.overall(reps)
    assert status == "PASS" and "single-channel" in why


def test_too_little_data_is_reported_as_such(tmp_path):
    lines = [f"F,{i * 512},S,m," + ",".join(["1.0"] * nr.NFEAT) for i in range(8)]
    rows = tc.parse(lines)
    rep = tc.evaluate("m", tc.usable_windows(rows.data[("m", "S")], 0.0), np.zeros((0, nr.NFEAT)))
    assert not rep.enough
    assert tc.overall([rep])[0] == "NO DATA"


def test_thinning_never_keeps_two_overlapping_windows():
    rows = [(i * 512, np.zeros(nr.NFEAT)) for i in range(100)]
    kept = tc.usable_windows(rows, 5.0)
    first, last = 5_000, 99 * 512 - 5_000
    inside = [r for r in rows if first <= r[0] <= last]
    assert len(kept) == (len(inside) + 1) // 2


def test_command_line_exit_codes_and_json(tmp_path):
    good, bad = tmp_path / "good.log", tmp_path / "bad.log"
    make_log(good)
    make_log(bad, strength_acc=0.0, strength_mic=0.0)
    out = tmp_path / "r.json"
    run = lambda p, *extra: subprocess.run([sys.executable, os.path.join(ROOT, "training", "train_classifier.py"), str(p), *extra],
                                           capture_output=True, text=True)
    r = run(good, "--json", str(out))
    assert r.returncode == 0 and "V3 result: PASS" in r.stdout and out.exists()
    r = run(bad)
    assert r.returncode == 2 and "V3 result: FAIL" in r.stdout
