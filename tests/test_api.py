import json
import time

import pytest
from fastapi.testclient import TestClient

from hydrowitness.api import create_app
from hydrowitness.demo import DEMO_SECRETS, demo_profile
from hydrowitness.security.nodesig import make_wire


def client(token=None, **kw):
    app = create_app(demo_profile(), dict(DEMO_SECRETS), token=token, run_background=False, **kw)
    return TestClient(app), app.state.hw


def scada(**over):
    body = {"tank_level_ft": 22.0, "pump_status": {"P1": False, "P2": True}, "pump_flow_gpm": {"P1": 0.0, "P2": 180.0},
            "demand_gpm": 180.0, "pressure_psi": 26.8}
    body.update(over)
    return body


def node_wire(secret, nid="NB", seq=1, st="S", boot=1):
    return make_wire(secret, n=nid, b=boot, s=seq, t_ms=int(time.time() * 1000), k="hb", st=st, c=950,
                     ca=st, cm=st, p=1, f="a1b2c3d4")


# ------------------------------------------------------------------------------ basics
def test_page_health_and_profile():
    c, _ = client()
    assert c.get("/").status_code == 200 and "HydroWitness" in c.get("/").text
    h = c.get("/api/health").json()
    assert h["ok"] and h["read_only"] and h["auth_required"] is False
    assert c.get("/api/profile").json()["site_name"] == "Demo Valley Water"
    assert c.get("/api/health").headers["x-content-type-options"] == "nosniff"


def test_the_dashboard_makes_no_external_requests():
    c, _ = client()
    page = c.get("/").text
    assert "https://" not in page and "//cdn" not in page and page.count("http://") == page.count("http://www.w3.org")
    assert "<script src" not in page and "@import" not in page and 'rel="stylesheet"' not in page


def test_no_endpoint_writes_to_the_plant():
    c, st = client()
    paths = [p for p in c.app.openapi()["paths"]]
    assert not any(w in p for p in paths for w in ("pump/", "valve", "plc", "write", "command", "setpoint"))


# ---------------------------------------------------------------------------- live data
def test_fresh_server_is_not_armed_and_does_not_page_anyone():
    sink = []
    c, st = client(alert_sinks=[sink.append])
    v = c.get("/api/state").json()
    assert v["source"] == "live" and v["live_summary"]["armed"] is False
    assert sink == []


def test_scada_feed_arms_the_system_and_a_healthy_feed_is_green():
    c, st = client()
    for _ in range(3):
        assert c.post("/api/scada", json=scada()).json() == {"ok": True}
    v = c.get("/api/state").json()
    assert st.live.alerts_enabled
    assert v["scada"]["pump_status"] == {"P1": False, "P2": True}


def test_bad_scada_body_is_a_422_not_a_crash():
    c, _ = client()
    assert c.post("/api/scada", json={"tank_level_ft": "deep"}).status_code == 422


def test_node_messages_are_accepted_when_signed_and_rejected_when_not():
    c, st = client()
    ok = c.post("/api/node", json=node_wire(DEMO_SECRETS["NB"], seq=1))
    assert ok.status_code == 200 and ok.json()["accepted"]
    assert ok.json()["trust"] in ("green", "amber", "red")    # accepted nodes learn the state, so their LEDs can mirror it
    bad = c.post("/api/node", json=node_wire(b"wrong", seq=2))
    assert bad.status_code == 403 and bad.json()["reason"] == "bad_signature"
    assert "trust" not in bad.json()                          # a rejected sender learns nothing about the site"
    replay = c.post("/api/node", json=node_wire(DEMO_SECRETS["NB"], seq=1))
    assert replay.status_code == 403 and replay.json()["reason"] == "replay"


def test_end_to_end_a_node_contradicting_the_screen_raises_amber_and_alerts():
    sink = []
    c, st = client(alert_sinks=[sink.append])
    c.post("/api/scada", json=scada())                                  # screen says P2 running
    c.post("/api/node", json=node_wire(DEMO_SECRETS["NB"], seq=1, st="S"))   # node hears it stopped
    t0 = time.time()
    st.live.evaluate(t0 + 20)
    st.live.ingest_scada(type(st.live.snaps[-1])(**{**st.live.snaps[-1].model_dump(), "t": t0 + 20}))
    st.live.ingest_node(node_wire(DEMO_SECRETS["NB"], seq=2, st="S"), t0 + 20)
    v = st.live.evaluate(t0 + 21)
    assert v["trust"]["state"] in ("amber", "red") and sink and sink[0]["level"] in ("amber", "red")


# ---------------------------------------------------------------------- operator actions
def test_manual_readings_are_validated_and_logged():
    c, st = client()
    c.post("/api/scada", json=scada())
    assert c.post("/api/manual", json={"kind": "tank_level_ft", "value": 21.8, "operator": "Sam"}).json()["ok"]
    assert c.post("/api/manual", json={"kind": "pump_running", "value": False}).status_code == 422       # needs a pump
    assert c.post("/api/manual", json={"kind": "pump_running", "value": False, "pump_id": "P9"}).status_code == 422
    assert c.post("/api/manual", json={"kind": "tank_level_ft"}).status_code == 422                      # needs a value
    recs = c.get("/api/log?n=50").json()["records"]
    assert any(r["type"] == "manual_reading" and r["source"] == "operator:Sam" for r in recs)


def test_operator_who_sees_a_stopped_pump_turns_the_state_red():
    c, _ = client()
    c.post("/api/scada", json=scada())
    c.post("/api/manual", json={"kind": "pump_running", "value": False, "pump_id": "P2", "operator": "Sam"})
    v = c.get("/api/state").json()
    assert v["trust"]["state"] == "red" and v["playbook"]["active"]
    assert c.post("/api/ack", json={"operator": "Sam"}).json()["ok"]
    assert c.post("/api/playbook/step/c1", json={"operator": "Sam"}).json()["ok"]
    assert c.post("/api/playbook/step/c1", json={"operator": "Sam"}).status_code == 404            # already done
    assert c.post("/api/playbook/start", json={}).status_code == 409                                  # already running


def test_ack_when_green_is_a_409():
    c, _ = client()
    c.post("/api/scada", json=scada())
    assert c.post("/api/ack", json={}).status_code == 409


def test_log_verifies_exports_and_detects_tampering():
    c, st = client()
    c.post("/api/scada", json=scada())
    c.post("/api/manual", json={"kind": "tank_level_ft", "value": 22.0})
    assert c.get("/api/log/verify").json()["ok"]
    export = c.get("/api/log/export")
    assert export.headers["content-type"].startswith("application/x-ndjson")
    assert len(export.text.strip().splitlines()) == len(st.live.log.records)
    st.live.log.records[0]["content"]["tampered"] = True
    bad = c.get("/api/log/verify").json()
    assert bad["ok"] is False and bad["bad_at"] == 0
    assert "chain valid: False" in c.get("/api/log/report").text


def test_profile_can_be_replaced_and_is_validated():
    c, st = client()
    body = demo_profile().model_dump()
    body["site_name"] = "Maple Ridge"
    assert c.put("/api/profile", json=body).json() == {"ok": True, "site": "Maple Ridge"}
    assert c.get("/api/profile").json()["site_name"] == "Maple Ridge"
    body["peak_demand_gpm"] = 10                                                  # below the typical hourly demand
    assert c.put("/api/profile", json=body).status_code == 422


# ----------------------------------------------------------------------------- drills
def test_drill_is_isolated_from_the_live_log_and_live_state_stays_visible():
    c, st = client()
    c.post("/api/scada", json=scada())
    live_records = len(st.live.log.records)
    assert c.post("/api/drill/start", json={"scenario": "colorado_style", "speed": 5}).json()["ok"]
    assert c.post("/api/drill/step?seconds=300").json()["ok"]
    v = c.get("/api/state").json()
    assert v["source"] == "drill" and v["drill"]["name"] and v["trust"]["state"] == "red"
    assert v["live_summary"]["state"] in ("green", "amber") and v["live_summary"]["armed"]
    assert len(st.live.log.records) >= live_records
    assert not any(r["type"] in ("playbook_started", "trust_state") and r["ts"] < time.time() - 3600
                   for r in st.live.log.records)                        # nothing from the drill's simulated 2020-era clock
    assert not any("drill" in r["source"] for r in st.live.log.records)
    # operator actions go to the drill while it runs
    c.post("/api/manual", json={"kind": "tank_level_ft", "value": 20.0, "operator": "Trainee"})
    assert any(r["source"] == "operator:Trainee" for r in st.drill.core.log.records)
    assert not any(r["source"] == "operator:Trainee" for r in st.live.log.records)
    assert c.post("/api/drill/stop").json()["ok"]
    assert c.get("/api/state").json()["source"] == "live"


def test_free_play_attacks_can_be_injected_by_hand():
    c, st = client()
    c.post("/api/drill/start", json={"scenario": "free_play"})
    v = c.get("/api/state").json()
    assert v["trust"]["state"] == "green" and v["drill"]["attack_started"] is False
    assert c.post("/api/drill/inject", json={"action": "pump_off", "arg": "P2"}).json()["ok"]
    assert c.post("/api/drill/inject", json={"action": "freeze"}).json()["ok"]
    c.post("/api/drill/step?seconds=240")
    v = c.get("/api/state").json()
    assert v["drill"]["attack_started"] and v["trust"]["state"] in ("amber", "red")
    assert v["drill"]["metrics"]["first_amber_s"] is not None
    assert c.post("/api/drill/inject", json={"action": "pump_off"}).status_code == 422            # needs a pump
    assert c.post("/api/drill/inject", json={"action": "explode"}).status_code == 404


def test_drill_controls_and_errors():
    c, _ = client()
    assert c.post("/api/drill/stop").status_code == 409
    assert c.post("/api/drill/step").status_code == 409
    assert c.post("/api/drill/start", json={"scenario": "nope"}).status_code == 404
    c.post("/api/drill/start", json={"scenario": "frozen_screen"})
    r = c.post("/api/drill/control", json={"speed": 30, "running": False}).json()
    assert r == {"ok": True, "speed": 30, "running": False}
    assert c.post("/api/drill/control", json={"speed": 500}).status_code == 422
    scen = c.get("/api/drill/scenarios").json()
    assert {s["id"] for s in scen["scenarios"]} >= {"frozen_screen", "free_play"} and "freeze" in scen["actions"]


def test_drill_time_advances_with_the_background_tick():
    c, st = client()
    c.post("/api/drill/start", json={"scenario": "free_play", "speed": 30})
    t0 = st.drill.sys.t
    st.tick(2.0)                                      # two real seconds at 30x is 60 simulated seconds
    assert st.drill.sys.t - t0 == pytest.approx(60, abs=5)
    c.post("/api/drill/control", json={"running": False})
    t1 = st.drill.sys.t
    st.tick(5.0)
    assert st.drill.sys.t == t1


# ------------------------------------------------------------------------------- auth
def test_token_protects_everything_except_health_node_and_the_page():
    c, _ = client(token="s3cret")
    assert c.get("/api/health").status_code == 200 and c.get("/").status_code == 200
    for method, path in [("get", "/api/state"), ("get", "/api/log"), ("get", "/api/log/export"), ("get", "/api/profile"),
                         ("post", "/api/scada"), ("post", "/api/manual"), ("post", "/api/ack"),
                         ("post", "/api/drill/start"), ("put", "/api/profile")]:
        assert getattr(c, method)(path, **({} if method == "get" else {"json": {}})).status_code == 401, path
    assert c.get("/api/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    # node messages authenticate themselves with their own HMAC
    assert c.post("/api/node", json=node_wire(DEMO_SECRETS["NB"])).status_code == 200


def test_secrets_never_appear_in_any_response():
    c, _ = client()
    c.post("/api/scada", json=scada())
    blob = json.dumps([c.get(p).text for p in ("/api/state", "/api/profile", "/api/log", "/api/health")])
    assert "demo-secret" not in blob


def test_a_node_secret_written_as_hex_in_the_server_file_matches_what_the_firmware_signs_with(tmp_path, monkeypatch):
    """The firmware's secrets.h holds 64 hex characters and signs with the decoded 32 bytes. The server's secrets
    file holds the same 64 characters. If the server used their ASCII bytes instead, no real node would ever be
    accepted. This is the first-contact failure this test exists to prevent."""
    import json
    from hydrowitness.api import _load_config
    from hydrowitness.demo import demo_profile
    from hydrowitness.security.nodesig import NodeRegistry, parse_secret

    hex_secret = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    prof, sec = tmp_path / "profile.json", tmp_path / "secrets.json"
    prof.write_text(demo_profile().model_dump_json())
    sec.write_text(json.dumps({"NB": hex_secret, "NA": "plain-demo-secret"}))
    monkeypatch.setenv("HYDROWITNESS_PROFILE", str(prof))
    monkeypatch.setenv("HYDROWITNESS_SECRETS", str(sec))
    profile, secrets, demo = _load_config()
    assert not demo
    assert secrets["NB"] == bytes.fromhex(hex_secret) and len(secrets["NB"]) == 32
    assert secrets["NA"] == b"plain-demo-secret"                      # non-hex text still works (demo secrets)
    reg = NodeRegistry(secrets)
    wire = make_wire(bytes.fromhex(hex_secret), n="NB", b=1, s=1, t_ms=1_800_000_000_000, k="hb", st="S", c=900, ca="S", cm="S",
                     p=1, f="abcd1234")
    ok, reason, _ = reg.accept(wire, 1_800_000_000.5)
    assert ok, reason
    assert parse_secret(" " + hex_secret.upper() + "\n") == bytes.fromhex(hex_secret)


def test_dashboard_states_the_demand_the_worst_case_assumes():
    c, _ = client()
    page = c.get("/").text
    assert "peak_demand_gpm" in page and "fire flow" in page
