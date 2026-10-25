"""The in-browser demo (site/demo/): its recordings are a fresh run of the real engines, say what the saved results say,
and the dashboard page they drive is the real dashboard with only the intended changes."""
import gzip
import json
from pathlib import Path

import pytest

from hydrowitness.sim import web_recordings as W

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "site" / "demo"
V2 = json.loads((ROOT / "results" / "v2_drills.json").read_text())["drills"]
HAND_WRITTEN = {"shim.js", "compare.js"}          # written by hand, not generated; the tests below check what they must
DASH = (ROOT / "hydrowitness" / "static" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def built():
    return W.build_all()


def _on_disk():
    return {p.relative_to(DEMO).as_posix(): p.read_bytes() for p in DEMO.rglob("*") if p.is_file() and p.name not in HAND_WRITTEN}


def _content(rel, data):
    """What a file says, not how it happens to be stored: compressed bytes can differ between zlib builds (Windows and Linux
    Python) and a checkout may turn line endings into CRLF, neither of which changes what the demo shows."""
    return gzip.decompress(data) if rel.endswith(".gz") else data.replace(b"\r\n", b"\n")


def test_committed_demo_files_are_a_fresh_run_of_the_engines(built):
    disk = _on_disk()
    assert set(disk) == set(built)
    stale = sorted(k for k in built if _content(k, disk[k]) != _content(k, built[k]))
    assert not stale, f"run `python -m hydrowitness.sim.web_recordings`: {stale} are out of date"


def test_every_drill_has_a_recording_with_and_without_the_operator(built):
    ids = {s for s in W.SCENARIOS if s not in W.SKIP}
    assert ids == {"frozen_screen", "forced_off_spoofed", "colorado_style", "replay", "hidden_valve_closure", "power_loss_lookalike"}
    for sid in ids:
        for op in (False, True):
            assert f"rec/{W.rec_name(sid, op)}" in built


def _recording(built, sid, op):
    return json.loads(gzip.decompress(built[f"rec/{W.rec_name(sid, op)}"]))


@pytest.mark.parametrize("sid", sorted(set(W.SCENARIOS) - W.SKIP))
@pytest.mark.parametrize("op", [False, True])
def test_recorded_times_are_the_saved_drill_results(built, sid, op):
    rec = _recording(built, sid, op)
    frames = rec["frames"]
    assert rec["dt"] == W.DT and len(frames) == int(W.HORIZON_S // W.DT) + 1
    m = frames[-1]["drill"]["metrics"]
    saved = V2[f"{sid}|{'operator' if op else 'no operator'}"]
    assert m["first_amber_s"] == saved["amber_median_s"]
    assert m["first_red_s"] == saved["red_median_s"]
    # frames advance one step at a time, and red latches: it never goes back
    secs = [f["drill"]["seconds_since_attack"] for f in frames]
    assert secs == [i * W.DT for i in range(len(frames))]
    states = [f["trust"]["state"] for f in frames]
    rank = {"green": 0, "amber": 1, "red": 2}
    assert all(rank[b] >= rank[a] for a, b in zip(states, states[1:]))
    assert states[0] == "green" and m["false_alarms_before_attack"] == 0


def test_no_recording_contains_a_secret_or_a_token(built):
    from hydrowitness.demo import DEMO_SECRETS
    for rel, data in built.items():
        text = gzip.decompress(data).decode() if rel.endswith(".gz") else data.decode("utf-8")
        for secret in DEMO_SECRETS.values():
            secret = secret.decode() if isinstance(secret, bytes) else secret
            assert secret not in text, rel


def test_the_dashboard_page_is_the_real_one_plus_the_intended_additions(built):
    app = built["app.html"].decode("utf-8")
    assert '<script src="shim.js"></script>' in app and 'id="webdemo-note"' in app and 'id="webdemo-style"' in app
    assert '<script src="compare.js" defer></script>' in app
    stripped = app.replace('<script src="shim.js"></script>', "").replace('<script src="compare.js" defer></script>', "")
    assert W.NOTICE not in DASH and len(app) - len(DASH) < 2500          # additions only: two script tags, a style and a notice
    for must in ("Screen and witnesses agree", "Do not trust the screen", "/api/state", "/api/drill/start"):
        assert must in app
    assert W.SECOND_WITNESS_NEW in app and W.SECOND_WITNESS_OLD not in app
    assert stripped.count("<head>") == 1


def test_the_dashboard_still_has_the_anchors_the_demo_depends_on():
    assert DASH.count(W.SECOND_WITNESS_OLD) == 1
    for anchor in ('id="enter"', 'id="drill"', "attack-grid", 'data-act="pb-start"', 'data-act="pb-step"', 'data-act="ack"'):
        assert anchor in DASH, anchor


def test_the_shim_answers_only_from_recordings():
    shim = (DEMO / "shim.js").read_text(encoding="utf-8")
    assert "DecompressionStream" in shim and "web_demo: true" in shim and "read_only: true" in shim
    # nothing that would reach another site or change a plant
    assert "http://" not in shim and "https://" not in shim and "XMLHttpRequest" not in shim
    for route in ("/api/state", "/api/profile", "/api/drill/scenarios", "/api/drill/start", "/api/drill/step", "/api/drill/control", "/api/log/verify"):
        assert route in shim
    assert "NOT_HERE" in shim and "downloadable version" in shim


# ---- the comparison column: what the simulation says really happened -----------------------------------------------
@pytest.mark.parametrize("sid", sorted(set(W.SCENARIOS) - W.SKIP))
def test_every_frame_records_what_really_happened(built, sid):
    for f in _recording(built, sid, False)["frames"]:
        t = f["truth"]
        assert set(t["pumps"]) == {"P1", "P2"} and t["level_ft"] > 0
        assert isinstance(t["screen_wrong"], list) and t["psi_line"] == 20.0
        # the engines never see this: nothing in the truth column may be used to build the state they produce
        assert "truth" not in f["trust"] and "truth" not in f["clock"]


def test_the_screen_is_called_wrong_by_the_end_of_every_attack(built):
    for sid in W.SCENARIOS:
        if sid in W.SKIP:
            continue
        fr = _recording(built, sid, False)["frames"]
        assert fr[-1]["truth"]["screen_wrong"], f"{sid}: by the end of the run the screen is wrong, so the demo must say so"


def test_the_demo_does_not_claim_a_pressure_the_simulation_never_worked_out(built):
    for f in _recording(built, "hidden_valve_closure", False)["frames"]:
        t = f["truth"]
        if t["valve_closed"]:
            assert t["psi_critical"] is None and t["minutes_to_line"] is None and t["pressure_modelled"] is False
            assert t["supply_pct"] == 30


def test_truth_says_power_loss_is_power_loss(built):
    last = _recording(built, "power_loss_lookalike", False)["frames"][-1]["truth"]
    assert last["power_ok"] is False and all(not p["running"] for p in last["pumps"].values())
    assert any("Mains power" in x for x in last["screen_wrong"])


def test_compare_js_only_reads_the_dashboards_own_state():
    js = (DEMO / "compare.js").read_text(encoding="utf-8")
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "http://" not in js and "https://" not in js
    assert "simulated" in js.lower() and "never sees it" in js
