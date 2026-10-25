"""Regression checks for the improvement round: who may read the new fields, what the screen says when messages are missing,
and that demo or simulated data can never be mistaken for, or leak into, a real site's data."""
import json
import re
import time
from pathlib import Path

import pytest

from hydrowitness.api import create_app
from hydrowitness.demo import DEMO_SECRETS, demo_profile
from hydrowitness.sim import evaluation as E
from hydrowitness.sim import web_recordings as W
from hydrowitness.sim.drills import DrillRunner
from tests.test_api import client, node_wire, scada

ROOT = Path(__file__).resolve().parent.parent
NEW_FIELDS = ("quantities", "next_check", "last_update")


# ---- who may read the new fields -------------------------------------------------------------------------------------------
def test_the_new_fields_need_the_token_like_everything_else():
    c, _ = client(token="s3cret")
    assert c.get("/api/state").status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = c.get("/api/state", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and all(k in ok.json() for k in NEW_FIELDS)


def test_no_new_route_was_added_and_none_can_write_to_the_plant():
    c, _ = client()
    paths = set(c.app.openapi()["paths"])
    assert not any(w in p for p in paths for w in ("pump/", "valve", "plc", "write", "command", "setpoint", "truth", "evaluation", "measure"))


def test_the_new_fields_hold_no_secret_and_no_token():
    c, _ = client(token="s3cret")
    c.post("/api/scada", json=scada(), headers={"Authorization": "Bearer s3cret"})
    blob = c.get("/api/state", headers={"Authorization": "Bearer s3cret"}).text
    assert "s3cret" not in blob and "demo-secret" not in blob and "plain-demo-secret" not in blob


# ---- what the screen says when messages are missing ------------------------------------------------------------------------
def test_with_nothing_received_every_quantity_is_unavailable_and_nothing_is_guessed():
    c, _ = client()
    v = c.get("/api/state").json()
    q = v["quantities"]
    assert q["tank"]["status"] == q["pressure"]["status"] == q["remaining"]["status"] == "unavailable"
    assert all(p["status"] == "unavailable" for p in q["pumps"])
    assert q["remaining"]["worst_text"] == "estimate unavailable" and q["tank"]["value_ft"] is None and q["pressure"]["model_psi"] is None
    assert v["clock"]["ok"] is False and v["next_check"]["primary"]["id"] == "tank_missing"
    assert v["last_update"] is None


def test_a_screen_with_no_witness_is_labelled_screen_only_and_the_next_check_asks_for_a_person():
    c, _ = client()
    c.post("/api/scada", json=scada())
    v = c.get("/api/state").json()
    assert {p["status"] for p in v["quantities"]["pumps"]} == {"screen_only"}
    assert v["quantities"]["tank"]["status"] not in ("independent", "agree")
    kinds = {g["kind"] for g in v["quantities"]["gaps"]}
    assert "missing" in kinds and any("Node NB is silent" in g["text"] for g in v["quantities"]["gaps"])
    assert v["next_check"]["primary"]["who"] in ("Operator", "Dispatcher")


def test_a_message_from_one_node_makes_only_its_pump_independent():
    c, _ = client()
    c.post("/api/scada", json=scada())
    assert c.post("/api/node", json=node_wire(DEMO_SECRETS["NB"], st="R")).status_code == 200
    by_pump = {p["pump_id"]: p["status"] for p in c.get("/api/state").json()["quantities"]["pumps"]}
    assert by_pump["P2"] in ("independent", "agree") and by_pump["P1"] == "screen_only"


def test_a_message_that_is_unsigned_or_unknown_changes_nothing():
    c, _ = client()
    c.post("/api/scada", json=scada())
    before = c.get("/api/state").json()["quantities"]["pumps"]
    bad = node_wire(b"not-the-secret", st="R")
    assert c.post("/api/node", json=bad).status_code in (400, 401, 403, 422)
    assert c.get("/api/state").json()["quantities"]["pumps"] == before


def test_a_reading_for_a_pump_that_does_not_exist_is_refused_not_stored_and_cannot_break_the_view():
    c, _ = client()
    c.post("/api/scada", json=scada())
    r = c.post("/api/manual", json={"kind": "pump_running", "value": True, "pump_id": "P9", "operator": "x"})
    assert r.status_code in (400, 404, 409, 422)
    assert c.get("/api/state").status_code == 200


def test_the_clock_never_shows_a_number_without_a_tank_level_and_zero_stays_zero():
    c, _ = client()
    v = c.get("/api/state").json()["clock"]
    assert v["ok"] is False and v["worst_min"] is None and v["state"] == "unavailable"
    low = demo_profile().model_copy(update={"peak_demand_gpm": 3000.0, "tank_height_ft": 30.0})
    app = create_app(low, dict(DEMO_SECRETS), run_background=False)
    from fastapi.testclient import TestClient
    cc = TestClient(app)
    cc.post("/api/scada", json=scada(tank_level_ft=11.5, pump_flow_gpm={"P1": 0.0, "P2": 0.0}, pump_status={"P1": False, "P2": False}))
    clock = cc.get("/api/state").json()["clock"]
    if clock["state"] == "empty":
        assert clock["worst_min"] == 0 and clock["worst_text"] != "estimate unavailable"


# ---- demo and simulated data stay apart from a real site -------------------------------------------------------------------
def test_a_real_profile_is_never_reported_as_the_demo(tmp_path, monkeypatch):
    prof = demo_profile().model_copy(update={"site_name": "Real Town Water"})
    pf, sf = tmp_path / "p.json", tmp_path / "s.json"
    pf.write_text(json.dumps(prof.model_dump()))
    sf.write_text(json.dumps({"NA": "00" * 32, "NB": "11" * 32}))
    monkeypatch.setenv("HYDROWITNESS_PROFILE", str(pf))
    monkeypatch.setenv("HYDROWITNESS_SECRETS", str(sf))
    from fastapi.testclient import TestClient
    c = TestClient(create_app(run_background=False))
    h = c.get("/api/health").json()
    assert h["demo_config"] is False
    blob = c.get("/api/state").text + c.get("/api/profile").text
    assert "Real Town Water" in blob and "Demo Valley" not in blob and "plain-demo-secret" not in blob


def test_no_configuration_means_demo_and_says_so(monkeypatch):
    monkeypatch.delenv("HYDROWITNESS_PROFILE", raising=False)
    monkeypatch.delenv("HYDROWITNESS_SECRETS", raising=False)
    from fastapi.testclient import TestClient
    assert TestClient(create_app(run_background=False)).get("/api/health").json()["demo_config"] is True


def test_the_real_servers_state_never_carries_the_simulations_truth_even_during_a_drill():
    c, st = client()
    assert c.post("/api/drill/start", json={"scenario": "forced_off_spoofed", "speed": 5, "operator_script": False}).status_code == 200
    st.drill.advance(120)
    v = c.get("/api/state").json()
    assert v["source"] == "drill" and "drill" in v
    assert "truth" not in v and "truth" not in json.dumps(v["trust"]) and "truth" not in json.dumps(v["clock"])


def test_a_drill_does_not_write_to_the_live_log_or_the_live_quantities():
    c, st = client()
    c.post("/api/scada", json=scada())
    c.get("/api/state")                                   # the live side settles first (its own silent-node warning is a live event)
    before = (len(st.live.log.records), st.live.log.head_hash)
    c.post("/api/drill/start", json={"scenario": "frozen_screen", "speed": 5, "operator_script": True})
    st.drill.advance(300)
    c.get("/api/state")
    assert (len(st.live.log.records), st.live.log.head_hash) == before
    assert st.drill.core.log is not st.live.log


def test_only_the_demo_page_reads_the_simulations_truth_and_the_real_dashboard_does_not():
    dash = (ROOT / "hydrowitness" / "static" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\.truth\b|\"truth\"|'truth'|truth\.", dash)
    assert "s.truth" in (ROOT / "site" / "demo" / "compare.js").read_text(encoding="utf-8")
    engine_src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "hydrowitness" / "engine").glob("*.py"))
    service_src = (ROOT / "hydrowitness" / "service.py").read_text(encoding="utf-8")
    assert "sim.truth" not in engine_src + service_src and "TankSystem" not in engine_src + service_src, \
        "the engines must never read the simulated plant"


def test_every_demo_frame_is_labelled_as_a_drill_and_the_index_as_demo_data():
    rec = json.loads(__import__("gzip").decompress(W.build_all()["rec/" + W.rec_name("frozen_screen", False)]))
    assert all(f["source"] == "drill" and f["drill"]["running"] is not None and "truth" in f for f in rec["frames"])
    idx = json.loads(W.build_all()["index.json"])
    assert idx["live"]["source"] == "live" and "truth" not in idx["live"] and idx["profile"]["site_name"] == "Demo Valley Water"


def test_the_evaluation_uses_no_real_measurements_and_says_so():
    src = Path(E.__file__).read_text(encoding="utf-8")
    assert "measurements/" not in src and "import measure" not in src and "training" not in src
    res = json.loads((ROOT / "results" / "evaluation.json").read_text())
    assert "uses no real measurements" in (ROOT / "results" / "evaluation.md").read_text()
    assert any("no real measurements" in x for x in res["config"]["limits"])


def test_real_recordings_cannot_be_mistaken_for_simulated_ones_in_the_measurement_report():
    rep = json.loads((ROOT / "results" / "measurements.json").read_text())
    assert all(s["purpose"] == "bench" for s in rep["sessions"]) or rep["counted"]["development"] + rep["counted"]["evaluation"] > 0
    assert not (ROOT / "results" / "evaluation.json").read_text().count("measurements/")
