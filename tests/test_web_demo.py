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
DASH = (ROOT / "hydrowitness" / "static" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def built():
    return W.build_all()


def _on_disk():
    return {p.relative_to(DEMO).as_posix(): p.read_bytes() for p in DEMO.rglob("*") if p.is_file() and p.name != "shim.js"}


def test_committed_demo_files_are_a_fresh_run_of_the_engines(built):
    disk = _on_disk()
    assert set(disk) == set(built)
    stale = sorted(k for k in built if disk[k] != built[k])
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
    stripped = app.replace('<script src="shim.js"></script>', "")
    assert W.NOTICE not in DASH and len(app) - len(DASH) < 2500          # additions only: a script tag, a style and a notice
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
