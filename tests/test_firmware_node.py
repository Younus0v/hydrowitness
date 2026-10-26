"""The Witness Node's application logic (hw_node.h) run end to end on this computer.

witness_node.ino hands raw blocks to this logic and drives the LEDs and buzzer from it, so this test plays the
part of the hardware: synthetic pump signals in, button presses in, LED/buzzer states out. It proves the
install calibration flow, the persistence, the dead-sensor handling and the operator controls behave as written.

It does NOT prove anything about the ESP32 glue (I2S, I2C, WiFi, flash) or about real pumps. Synthetic signals
are not evidence of real separability; that is validation V3 with a real node.
"""
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "training"))

import synth  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs a C++ compiler")

UNCAL, RUN, CAL_OFF, CAL_WAIT_ON, CAL_ON = 0, 1, 2, 3, 4

# The standard operator story, in ms: pump off, long press at 10 s, calibration of the stopped baseline, the
# operator starts the pump at 150 s and presses at 165 s, then it runs, stops at 330 s and restarts at 380 s.
SCHEDULE = [(False, 150), (True, 180), (False, 50), (True, 40)]
STANDARD_SCRIPT = "10000 down\n13500 up\n165000 down\n165200 up\n"


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("fwnode") / "node_cli")
    src = os.path.join(ROOT, "firmware", "host_tests", "node_cli.cpp")
    r = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-o", out, src],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def signal(seed=1, schedule=SCHEDULE, strength_acc=1.0, strength_mic=1.0, kill_mic_after_s=None):
    rng = np.random.default_rng(seed)
    acc, mic, _ = synth.stream(schedule, rng, strength_acc=strength_acc, strength_mic=strength_mic)
    if kill_mic_after_s is not None:
        mic = mic.copy()
        mic[int(kill_mic_after_s * synth.MIC_FS):] = 0.0               # an unplugged microphone reads digital silence
    return acc, mic


class Run:
    def __init__(self, lines):
        self.E, self.O, self.P, self.D, self.M, self.X = [], [], [], [], [], None
        for ln in lines:
            f = ln.split()
            if f[0] == "E":
                self.E.append(dict(t=int(f[1]), phase=int(f[2]), st=f[3], c=int(f[4]), ca=f[5], cm=f[6], event=int(f[7]),
                                   wa=int(f[8]), wm=int(f[9]), za=float(f[10]), zm=float(f[11])))
            elif f[0] == "O":
                self.O.append((int(f[1]), *[bool(int(v)) for v in f[2:6]]))
            elif f[0] == "P":
                self.P.append((int(f[1]), int(f[2])))
            elif f[0] == "D":
                self.D.append(dict(t=int(f[1]), va=int(f[2]), vm=int(f[3]), qa=float(f[4]), qm=float(f[5]),
                                   mu_a=float(f[6]), mu_m=float(f[7])))
            elif f[0] == "M":
                self.M.append(int(f[1]))
            elif f[0] == "X":
                self.X = tuple(int(v) for v in f[1:])

    def phases(self):
        return [p for _, p in self.P]

    def events(self):
        return [(e["t"], e["st"]) for e in self.E if e["event"]]

    def out_at(self, t):
        cur = (False, False, False, False)
        for ts, g, a, r, b in self.O:
            if ts > t:
                break
            cur = (g, a, r, b)
        return cur

    def sampled(self, t0, t1, step=10):
        return [self.out_at(t) for t in range(t0, t1, step)]

    def rising_edges(self, idx, t0, t1):
        s = [x[idx] for x in self.sampled(t0 - 10, t1)]               # one sample before, so a flash that starts at t0 counts
        return sum(1 for i in range(1, len(s)) if s[i] and not s[i - 1])


def simulate(cli, tmp_path, acc, mic, script, mains=60, max_alloc=None):
    a = tmp_path / "acc.f32"
    m = tmp_path / "mic.f32"
    s = tmp_path / "script.txt"
    np.concatenate([acc[0], acc[1], acc[2]]).astype(np.float32).tofile(a)
    mic.astype(np.float32).tofile(m)
    s.write_text(script)
    env = dict(os.environ)
    if max_alloc is not None:
        env["HW_MAX_ALLOC"] = str(max_alloc)
    r = subprocess.run([cli, str(mains), str(a), str(m), str(s)], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    return Run(r.stdout.strip().splitlines())


@pytest.fixture(scope="module")
def standard(cli, tmp_path_factory):
    acc, mic = signal()
    return simulate(cli, tmp_path_factory.mktemp("std"), acc, mic, STANDARD_SCRIPT)


# --------------------------------------------------------------------------------- calibration flow
def test_install_flow_walks_through_the_phases_and_finishes_good(standard):
    assert standard.phases() == [CAL_OFF, CAL_WAIT_ON, CAL_ON, RUN]
    t = [ts for ts, _ in standard.P]
    assert 13_000 <= t[0] <= 13_100                              # three seconds after the press started
    assert 138_000 <= t[1] <= 139_000                            # 5 s skipped + 120 s recorded
    assert 165_000 <= t[2] <= 165_300                            # the operator's short press
    assert 290_000 <= t[3] <= 291_000
    assert len(standard.D) == 1
    d = standard.D[0]
    assert d["va"] == 2 and d["vm"] == 2                         # GOOD on both channels
    assert d["qa"] >= 0.97 and d["qm"] >= 0.97


def test_after_install_it_follows_the_pump_and_flags_nothing_else(standard):
    ev = standard.events()
    assert [s for _, s in ev] == ["R", "S", "R"], ev
    t_on1, t_off, t_on2 = 150_000, 330_000, 380_000
    # the pump actually ran from 150 s but the node was not armed until ~290 s, so the first event is the
    # first window after arming: nothing is reported while calibrating
    assert ev[0][0] > 290_000 and ev[0][0] - 290_304 <= 6_000
    assert 0 < ev[1][0] - t_off <= 6_000
    assert 0 < ev[2][0] - t_on2 <= 6_000
    steady = [e for e in standard.E if 300_000 <= e["t"] < 328_000]
    assert steady and all(e["st"] == "R" and e["c"] >= 850 and e["ca"] == "R" and e["cm"] == "R" for e in steady)


def test_nothing_is_reported_while_calibrating(standard):
    cal = [e for e in standard.E if e["phase"] in (CAL_OFF, CAL_WAIT_ON, CAL_ON)]
    assert cal and all(e["st"] == "U" and e["c"] == 200 and not e["event"] for e in cal)


def test_flash_image_round_trip_restores_a_working_node(standard):
    assert standard.X == (RUN, 1, 1)                             # rebuilt from the exported models, no recalibration


def test_a_second_long_press_cancels_and_changes_nothing(cli, tmp_path):
    acc, mic = signal(schedule=[(False, 60)])
    r = simulate(cli, tmp_path, acc, mic, "10000 down\n13500 up\n40000 down\n43500 up\n")
    assert r.phases() == [CAL_OFF, UNCAL]
    assert r.D == []                                             # no model was stored


def test_calibration_that_cannot_separate_the_pump_is_refused(cli, tmp_path):
    acc, mic = signal(strength_acc=0.0, strength_mic=0.0)        # a node bolted somewhere it can feel nothing
    r = simulate(cli, tmp_path, acc, mic, STANDARD_SCRIPT)
    assert r.phases() == [CAL_OFF, CAL_WAIT_ON, CAL_ON, UNCAL]
    d = r.D[0]
    assert d["va"] == 0 and d["vm"] == 0
    assert r.X == (UNCAL, 0, 0)
    assert r.events() == []                                      # a node that can't tell never claims to
    t0 = d["t"]
    assert r.rising_edges(2, t0, t0 + 1_300) == 3                # three red flashes
    assert any(g[3] for g in r.sampled(t0, t0 + 800))            # and beeps
    alt = r.sampled(t0 + 1_300, t0 + 9_800)
    assert any(a[2] and not a[1] for a in alt) and any(a[1] and not a[2] for a in alt)   # then red/amber alternate: move me
    after = r.sampled(t0 + 11_000, t0 + 20_000)
    assert all(not g[0] and not g[2] for g in after) and any(g[1] for g in after)        # needs calibration: amber only


def test_one_dead_channel_still_works_but_with_capped_confidence(cli, tmp_path):
    acc, mic = signal(strength_acc=0.0)                          # the accelerometer feels nothing, the microphone does
    r = simulate(cli, tmp_path, acc, mic, STANDARD_SCRIPT)
    d = r.D[0]
    assert d["va"] == 0 and d["vm"] >= 1
    assert [s for _, s in r.events()] == ["R", "S", "R"]
    steady = [e for e in r.E if 300_000 <= e["t"] < 328_000]
    assert steady and all(e["st"] == "R" and 500 <= e["c"] <= 760 and e["ca"] == "U" and e["cm"] == "R" for e in steady)
    t0 = d["t"]
    assert r.rising_edges(1, t0, t0 + 1_500) == 3                # amber flashes: works, but not at full strength


# -------------------------------------------------------------------------------- faults in service
def test_an_unplugged_microphone_degrades_to_one_channel_without_a_false_event(cli, tmp_path):
    acc, mic = signal(kill_mic_after_s=300)
    r = simulate(cli, tmp_path, acc, mic, STANDARD_SCRIPT)
    evs = r.events()
    assert evs[0][1] == "R"
    late = [e for e in r.E if 306_000 <= e["t"] < 328_000]
    assert late and all(e["cm"] == "U" and e["st"] == "R" and 500 <= e["c"] <= 760 for e in late)
    assert not any(300_000 < t < 328_000 for t, _ in evs)         # losing the mic is not a pump event


# ---------------------------------------------------------------------------------- operator controls
def test_confirming_a_strong_reading_adapts_and_an_unsure_one_is_refused(cli, tmp_path):
    acc, mic = signal()
    script = STANDARD_SCRIPT + "292000 down\n292200 up\n300000 down\n300200 up\n"
    r = simulate(cli, tmp_path, acc, mic, script)
    assert len(r.D) == 2                                         # calibration, then ONE confirmation
    assert abs(r.D[1]["mu_m"] - r.D[0]["mu_m"]) > 0              # the stored baseline moved
    assert abs(r.D[1]["mu_m"] - r.D[0]["mu_m"]) < 0.05 * 20      # ...but only a small step (alpha = 0.02)
    # the press at 292 s came before the node had a state: two amber blinks, nothing learned
    assert r.rising_edges(1, 292_200, 293_100) == 2
    # the press at 300 s: all three LEDs light together, for a moment
    assert any(g[0] and g[1] and g[2] for g in r.sampled(300_200, 300_600))


def test_alarm_sounds_while_the_server_says_red_and_a_press_mutes_it_without_training(cli, tmp_path):
    acc, mic = signal()
    script = STANDARD_SCRIPT + "295000 trust r\n318000 down\n318200 up\n"
    r = simulate(cli, tmp_path, acc, mic, script)
    before = r.sampled(300_000, 318_000)
    assert any(x[3] for x in before)                             # the alarm beeps
    assert len(r.D) == 1                                         # silencing is not a confirmation: no learning
    muted = r.sampled(318_500, 329_000)
    assert not any(x[3] for x in muted)                          # silent for the next ten minutes


def test_a_lost_server_link_is_visible_without_hiding_the_pump_state(cli, tmp_path):
    acc, mic = signal()
    r = simulate(cli, tmp_path, acc, mic, STANDARD_SCRIPT + "300000 link down\n")
    offline = r.sampled(345_000, 370_000)                        # pump stopped at 330 s: the node says STOPPED
    assert all(x[2] for x in offline)                            # red (stopped) stays lit throughout
    assert any(x[1] for x in offline) and not all(x[1] for x in offline)   # amber blinks briefly on top
    online = r.sampled(305_000, 318_000)
    assert all(x[0] and not x[1] and not x[2] for x in online)   # link still fresh and the pump running: green only


def test_steady_display_matches_the_state(standard):
    assert standard.out_at(310_000)[:3] == (True, False, False)  # running: green
    assert standard.out_at(350_000)[:3] == (False, False, True)  # stopped: red
    # the beginning, before any calibration: amber blinks, nothing else
    first = standard.sampled(0, 9_000)
    assert all(not x[0] and not x[2] for x in first) and any(x[1] for x in first)


def test_install_led_patterns_say_what_to_do(standard):
    off = standard.sampled(14_000, 138_000, 50)
    assert all(not x[0] and not x[1] for x in off) and any(x[2] for x in off) and not all(x[2] for x in off)   # red blinks
    wait = standard.sampled(139_500, 165_000, 50)
    assert all(not x[1] and not x[2] for x in wait) and any(x[0] for x in wait)                                # green blinks: start the pump
    on = standard.sampled(166_500, 290_000, 50)
    assert all(not x[1] and not x[2] for x in on) and any(x[0] for x in on)                                    # green blinks fast
    t = standard.D[0]["t"]
    assert standard.rising_edges(0, t, t + 1_500) == 3            # GOOD: three green flashes
    assert any(x[3] for x in standard.sampled(t, t + 700))        # and one long beep


# -------------------------------------------------------------------------------------- the sketch itself
@pytest.mark.parametrize("tls,mains", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_sketch_type_checks_against_arduino_stand_ins(tmp_path, tls, mains):
    """Catches typos, wrong types, printf mistakes and #include-order bugs in witness_node.ino for every config
    combination. The stand-ins (firmware/host_tests/stubs) are written from the published ESP32 API, so this
    cannot catch a mismatch with the real core: only a real build can. See firmware/README.md.

    One trap a real build found: the Arduino IDE writes its own declaration of every function in a .ino without
    attributes, so a function marked [[noreturn]] in the sketch fails to compile there ("declared [[noreturn]] but its
    first declaration was not"). Nothing here would notice, so check for it directly."""
    src = os.path.join(ROOT, "firmware", "witness_node")
    ino_text = open(os.path.join(src, "witness_node.ino")).read()
    assert not re.search(r"^\s*\[\[noreturn\]\]", ino_text, re.M), "the Arduino IDE rejects [[noreturn]] on a sketch function"
    for name in os.listdir(src):
        if name.endswith((".h", ".ino")) and name != "secrets.example.h":
            shutil.copy(os.path.join(src, name), tmp_path / name)
    cfg = (tmp_path / "config.h").read_text()
    cfg = cfg.replace("#define USE_TLS            0", f"#define USE_TLS            {tls}")
    cfg = cfg.replace("#define HAVE_MAINS_SENSE   0", f"#define HAVE_MAINS_SENSE   {mains}")
    (tmp_path / "config.h").write_text(cfg)
    stubs = os.path.join(ROOT, "firmware", "host_tests", "stubs")
    r = subprocess.run(["g++", "-std=gnu++20", "-fsyntax-only", "-Wall", "-Wextra", "-Wformat=2", "-Werror", "-isystem", stubs,
                        "-I", str(tmp_path), "-x", "c++", str(tmp_path / "witness_node.ino")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_example_secret_is_not_shipped_as_a_real_one():
    """secrets.h must never be committed; the example must be an obvious placeholder the sketch refuses to run with."""
    src = os.path.join(ROOT, "firmware", "witness_node")
    assert not os.path.exists(os.path.join(src, "secrets.h"))
    assert "secrets.h" in open(os.path.join(src, ".gitignore")).read()
    ino = open(os.path.join(src, "witness_node.ino")).read()
    assert "still has the example secret" in ino


# ------------------------------------------------------------------------- calibration memory on a crowded heap
SHORT_RUN = [(False, 30)]
LONG_PRESS = "10000 down\n13500 up\n"


def test_calibration_starts_when_no_single_piece_of_memory_is_bigger_than_8_kb(cli, tmp_path):
    """The first real board had 68 kB free but could not give one 31 kB block, so holding the button showed the
    'move me' pattern instead of starting calibration. Four blocks of 7.7 kB each fit."""
    acc, mic = signal(schedule=SHORT_RUN)
    r = simulate(cli, tmp_path, acc, mic, LONG_PRESS, max_alloc=8192)
    assert r.M == [] and CAL_OFF in r.phases()


def test_with_too_little_memory_the_node_says_so_and_changes_nothing(cli, tmp_path):
    acc, mic = signal(schedule=SHORT_RUN)
    r = simulate(cli, tmp_path, acc, mic, LONG_PRESS, max_alloc=4096)
    assert len(r.M) == 1 and 13_000 <= r.M[0] <= 14_000
    assert r.phases() == []                                       # still waiting for calibration, no phase change
    assert r.rising_edges(2, 13_000, 14_300) == 3                 # the red 'cannot do it' flashes
