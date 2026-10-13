"""The firmware's portable C++ core, compiled on this computer and compared with the Python reference.

This proves the signal processing, classifier, persistence logic and message signing are right. It does NOT
prove anything about the ESP32 glue (I2S, I2C, WiFi) or about real pumps: those need the hardware.
"""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "training"))

import node_reference as nr  # noqa: E402
import synth  # noqa: E402
from hydrowitness.security.nodesig import NodeRegistry, make_wire  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs a C++ compiler")


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("fw") / "core_cli")
    src = os.path.join(ROOT, "firmware", "host_tests", "core_cli.cpp")
    r = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-o", out, src],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def run(cli, args, stdin=""):
    r = subprocess.run([cli, *args], input=stdin, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout


def windows(kind, sig, mains=60):
    prev, feats = None, []
    n = nr.ACC_N if kind == "acc" else nr.MIC_N
    for i in range(sig.shape[-1] // n):
        if kind == "acc":
            b = nr.acc_block(sig[0, i * n:(i + 1) * n], sig[1, i * n:(i + 1) * n], sig[2, i * n:(i + 1) * n], mains, prev)
        else:
            b = nr.mic_block(sig[i * n:(i + 1) * n], mains, prev)
        if prev is not None:
            feats.append(nr.window_features(kind, prev, b))
        prev = b
    return np.array(feats)


def cli_features(cli, kind, sig, mains=60):
    n = nr.ACC_N if kind == "acc" else nr.MIC_N
    nblk = sig.shape[-1] // n
    rows = []
    for i in range(nblk):
        if kind == "acc":
            rows += [" ".join(f"{v:.9g}" for v in sig[a, i * n:(i + 1) * n]) for a in range(3)]
        else:
            rows.append(" ".join(f"{v:.9g}" for v in sig[i * n:(i + 1) * n]))
    out = run(cli, ["feat", kind, str(mains)], f"{nblk}\n" + "\n".join(rows) + "\n")
    return np.array([[float(v) for v in line.split()] for line in out.strip().splitlines()])


# ---------------------------------------------------------------------------------- generated tables
def test_the_c_bin_tables_are_the_ones_the_python_reference_generates():
    path = os.path.join(ROOT, "firmware", "witness_node", "hw_tables.h")
    assert open(path).read() == nr.c_header()


def test_band_layout_covers_the_documented_ranges():
    assert nr.ACC_KB[0] * nr.ACC_FS / nr.ACC_N == pytest.approx(20, abs=1.0)
    assert nr.ACC_KB[-1] * nr.ACC_FS / nr.ACC_N == pytest.approx(250, abs=1.0)
    assert nr.MIC_KB[0] * nr.MIC_FS / nr.MIC_N == pytest.approx(60, abs=1.0)
    assert nr.MIC_KB[-1] * nr.MIC_FS / nr.MIC_N == pytest.approx(8000, abs=1.0)
    assert all(b > a for a, b in zip(nr.ACC_KB, nr.ACC_KB[1:])) and all(b > a for a, b in zip(nr.MIC_KB, nr.MIC_KB[1:]))


# ------------------------------------------------------------------------------------ signing
@pytest.mark.parametrize("key,msg,expect", [
    # RFC 4231 test cases 1 and 2
    (bytes.fromhex("0b" * 20), b"Hi There", "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"),
    (b"Jefe", b"what do ya want for nothing?", "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"),
])
def test_hmac_matches_the_published_test_vectors(cli, key, msg, expect):
    assert run(cli, ["hmac", key.hex(), msg.decode()]).strip() == expect


def test_hmac_matches_python_for_random_keys_including_keys_longer_than_a_block(cli):
    rng = np.random.default_rng(3)
    for klen in (1, 16, 32, 63, 64, 65, 100):
        key = bytes(rng.integers(0, 256, klen, dtype=np.uint8))
        msg = "NB|3|17|1800000000123|hb|R|950|R|R|1|a1b2c3d4" + "x" * int(rng.integers(0, 80))
        want = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
        assert run(cli, ["hmac", key.hex(), msg]).strip() == want, klen


def test_firmware_json_equals_the_python_wire_and_the_server_accepts_it(cli):
    secret = b"a-device-secret-32-bytes-long!!!"
    for k, st, ca, cm, c, p in [("hb", "R", "R", "R", 951, 1), ("ev", "S", "S", "S", 912, 1),
                                ("hb", "U", "S", "R", 300, 0), ("ev", "R", "U", "R", 640, 1)]:
        t_ms = int(time.time() * 1000)
        fw = json.loads(run(cli, ["wire", secret.hex(), "NB", "7", "42", str(t_ms), k, st, str(c), ca, cm, str(p), "deadbeef"]))
        py = make_wire(secret, n="NB", b=7, s=42, t_ms=t_ms, k=k, st=st, c=c, ca=ca, cm=cm, p=p, f="deadbeef")
        assert fw == py
        reg = NodeRegistry({"NB": secret})
        ok, reason, msg = reg.accept(fw, t_ms / 1000.0)
        assert ok, reason
        assert msg.state == {"R": "RUNNING", "S": "STOPPED", "U": "UNKNOWN"}[st] and msg.mains_ok == bool(p)


# ---------------------------------------------------------------------------------- features
@pytest.mark.parametrize("kind,mains", [("acc", 60), ("acc", 50), ("mic", 60), ("mic", 50)])
def test_cpp_features_match_the_python_reference(cli, kind, mains):
    rng = np.random.default_rng(11)
    secs = 4
    if kind == "acc":
        sig = np.concatenate([synth.acc_signal(False, secs, rng, mains=mains), synth.acc_signal(True, secs, rng, mains=mains)], axis=1)
    else:
        sig = np.concatenate([synth.mic_signal(False, secs, rng, mains=mains), synth.mic_signal(True, secs, rng, mains=mains)])
    want, got = windows(kind, sig, mains), cli_features(cli, kind, sig, mains)
    assert got.shape == want.shape == (want.shape[0], nr.NFEAT) and want.shape[0] >= 14
    db = slice(0, 13)
    assert np.max(np.abs(got[:, db] - want[:, db])) < 0.02, np.max(np.abs(got[:, db] - want[:, db]))   # dB
    assert np.allclose(got[:, 13], want[:, 13], rtol=2e-3, atol=2e-4)                                   # spectral flux
    assert np.allclose(got[:, 14], want[:, 14], rtol=2e-3)                                              # crest factor


def test_a_running_pump_lights_up_the_right_features(cli):
    rng = np.random.default_rng(5)
    off = cli_features(cli, "mic", synth.mic_signal(False, 3, rng))
    on = cli_features(cli, "mic", synth.mic_signal(True, 3, rng))
    assert on[:, 0].mean() > off[:, 0].mean() + 6          # louder overall
    assert on[:, 11:13].mean() > off[:, 11:13].mean() + 6  # more energy at the mains harmonics


# -------------------------------------------------------------------------------- calibration
def calib_input(R, S, Q):
    f = lambda tag, X: "".join(tag + " " + " ".join(f"{v:.9g}" for v in row) + "\n" for row in X)
    return f("R", R) + f("S", S) + f("Q", Q)


def test_calibration_matches_the_python_reference(cli):
    rng = np.random.default_rng(2)
    R = rng.normal(0, 1, (100, nr.NFEAT)) * np.linspace(0.4, 1.5, nr.NFEAT) + np.linspace(2, 0.3, nr.NFEAT)
    S = rng.normal(0, 1, (80, nr.NFEAT)) * np.linspace(0.5, 1.2, nr.NFEAT) - 1
    Q = rng.normal(0, 1, (20, nr.NFEAT)) + 0.5
    ref = nr.calibrate(R, S)
    out = run(cli, ["calib"], calib_input(R, S, Q)).strip().splitlines()
    cal = out[0].split()
    assert float(cal[1]) == pytest.approx(ref.acc_r, abs=1e-6) and float(cal[2]) == pytest.approx(ref.acc_s, abs=1e-6)
    assert {"POOR": 0, "FAIR": 1, "GOOD": 2}[ref.verdict] == int(cal[4])
    assert float(cal[5]) == pytest.approx(ref.model.d2, rel=1e-3)
    assert np.allclose([float(v) for v in out[1].split()[1:]], ref.model.w, rtol=2e-3, atol=1e-5)
    assert np.allclose([float(v) for v in out[2].split()[1:]], ref.model.m, rtol=2e-3, atol=1e-5)
    z = [float(l.split()[1]) for l in out[3:]]
    assert np.allclose(z, [ref.model.z(q) for q in Q], rtol=5e-3, atol=2e-3)


def test_synthetic_pump_calibrates_good_and_the_class_means_score_plus_and_minus_one(cli):
    rng = np.random.default_rng(8)
    R = cli_features(cli, "mic", synth.mic_signal(True, 60, rng))
    S = cli_features(cli, "mic", synth.mic_signal(False, 60, rng))
    out = run(cli, ["calib"], calib_input(R, S, [R.mean(axis=0), S.mean(axis=0)])).strip().splitlines()
    assert int(out[0].split()[4]) == 2                                   # GOOD
    assert float(out[3].split()[1]) == pytest.approx(1.0, abs=0.02) and float(out[4].split()[1]) == pytest.approx(-1.0, abs=0.02)


def test_a_node_that_cannot_feel_the_pump_is_told_to_move(cli):
    """strength 0: the node hears and feels nothing. Calibration must say POOR, never GOOD."""
    rng = np.random.default_rng(9)
    for kind, gen in (("acc", synth.acc_signal), ("mic", synth.mic_signal)):
        R = cli_features(cli, kind, gen(True, 60, rng, strength=0.0))
        S = cli_features(cli, kind, gen(False, 60, rng, strength=0.0))
        verdict = int(run(cli, ["calib"], calib_input(R, S, [])).split()[4])
        assert verdict == 0, kind


# ----------------------------------------------------------------------------------- decisions
def decide(cli, rows):
    out = run(cli, ["decide"], "".join(f"{za} {zm} {ua} {um}\n" for za, zm, ua, um in rows))
    return [(l.split()[0], int(l.split()[1]), l.split()[2], l.split()[3], l.split()[4] == "1") for l in out.strip().splitlines()]


def test_decider_starts_unknown_and_both_channels_agreeing_gives_high_confidence(cli):
    res = decide(cli, [(-1.0, -1.0, 1, 1)] * 12)
    assert res[0][0] == "U" and all(r[0] == "U" for r in res[:4])          # needs the 5-block vote first
    events = [i for i, r in enumerate(res) if r[4]]
    assert len(events) == 1 and res[-1][0] == "S" and res[-1][2:4] == ("S", "S") and res[-1][1] >= 950


def test_a_state_change_takes_seconds_not_one_window(cli):
    rows = [(-1.0, -1.0, 1, 1)] * 20 + [(1.0, 1.0, 1, 1)] * 20
    res = decide(cli, rows)
    change = next(i for i in range(20, 40) if res[i][0] == "R")
    delay_blocks = change - 20
    assert 3 <= delay_blocks <= 7                                          # about 1.5 to 3.5 s at 0.512 s per block
    assert sum(r[4] for r in res) == 2                                     # exactly: U->S, then S->R


def test_isolated_bad_windows_do_not_flip_the_state(cli):
    rows = [(-1.0, -1.0, 1, 1)] * 20
    for i in (6, 9, 13, 16):
        rows[i] = (1.0, -1.0, 1, 1)                                        # one channel misreads for one window
    res = decide(cli, rows)
    assert [r[4] for r in res].count(True) == 1 and res[-1][0] == "S"


def test_contradicting_channels_are_reported_as_unknown_not_guessed(cli):
    rows = [(-1.0, -1.0, 1, 1)] * 12 + [(1.0, -1.0, 1, 1)] * 20             # accelerometer says RUNNING, mic says STOPPED
    res = decide(cli, rows)
    assert res[-1][0] == "U" and res[-1][1] == 300 and res[-1][2:4] == ("R", "S")


def test_one_usable_channel_gives_capped_confidence(cli):
    res = decide(cli, [(0.0, -1.0, 0, 1)] * 15)                             # accelerometer unusable
    assert res[-1][0] == "S" and 500 <= res[-1][1] <= 760 and res[-1][2] == "U" and res[-1][3] == "S"


def test_no_usable_channel_means_unknown_with_low_confidence(cli):
    res = decide(cli, [(0.0, 0.0, 0, 0)] * 15)
    assert res[-1][0] == "U" and res[-1][1] == 200 and not any(r[4] for r in res)


def test_weak_scores_inside_the_dead_band_do_not_count(cli):
    res = decide(cli, [(0.2, 0.2, 1, 1)] * 15)                              # |z| below the 0.35 threshold
    assert res[-1][0] == "U"


# ------------------------------------------------------------------------- end to end (synthetic)
def test_a_synthetic_day_start_stop_start_is_followed_with_no_false_events(cli):
    rng = np.random.default_rng(21)
    cal = {}
    for kind, gen in (("acc", synth.acc_signal), ("mic", synth.mic_signal)):
        R = windows(kind, gen(True, 100, rng))
        S = windows(kind, gen(False, 100, rng))
        cal[kind] = nr.calibrate(R, S)
        assert cal[kind].verdict == "GOOD"
    schedule = [(False, 40), (True, 60), (False, 50), (True, 40)]
    acc, mic, truth = synth.stream(schedule, rng)
    fa, fm = windows("acc", acc), windows("mic", mic)
    n = min(len(fa), len(fm))
    rows = [(cal["acc"].model.z(fa[i]), cal["mic"].model.z(fm[i]), 1, 1) for i in range(n)]
    res = decide(cli, rows)
    block_s = nr.MIC_N / nr.MIC_FS
    events = [(i, res[i][0]) for i in range(n) if res[i][4]]
    # settle, then three real changes at 40 s, 100 s and 150 s
    assert [s for _, s in events] == ["S", "R", "S", "R"], events
    for (i, _), truth_t in zip(events[1:], (40, 100, 150)):
        assert 0 < i * block_s + block_s - truth_t < 5.0, (i, truth_t)    # detected within 5 s of the real change


def test_a_marginal_accelerometer_still_works_through_the_microphone(cli):
    rng = np.random.default_rng(31)
    cal = {}
    for kind, gen, strength in (("acc", synth.acc_signal, 0.0), ("mic", synth.mic_signal, 1.0)):
        cal[kind] = nr.calibrate(windows(kind, gen(True, 100, rng, strength=strength), 60),
                                 windows(kind, gen(False, 100, rng, strength=strength), 60))
    assert cal["acc"].verdict == "POOR" and cal["mic"].verdict == "GOOD"
    acc, mic, _ = synth.stream([(False, 40), (True, 40)], rng, strength_acc=0.0)
    fm = windows("mic", mic)
    rows = [(0.0, cal["mic"].model.z(f), 0, 1) for f in fm]
    res = decide(cli, rows)
    assert res[-1][0] == "R" and res[-1][1] <= 760 and res[-1][2] == "U"
