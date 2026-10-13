"""End to end: the real core against the simulated plant, one drill at a time."""
import pytest

from hydrowitness.demo import DEMO_SECRETS
from hydrowitness.schemas import ManualReading
from hydrowitness.security.nodesig import make_wire
from hydrowitness.sim.drills import SCENARIOS, DrillRunner, run_normal, run_scenario

SEEDS = [1, 2, 3, 4, 5]


# ------------------------------------------------------------------ normal operation
@pytest.mark.parametrize("seed", [1, 2])
def test_a_normal_day_raises_no_state_change_at_all(seed):
    out = run_normal(1.0, seed=seed)
    assert out["false_amber"] == 0 and out["false_red"] == 0, out["first_events"]


def test_normal_operation_shows_a_healthy_view():
    r = DrillRunner("frozen_screen", seed=1)          # warm-up only: attack hasn't started yet
    v = r.last_view
    assert v["trust"]["state"] == "green" and not v["trust"]["latched"]
    assert v["clock"]["ok"] and v["clock"]["worst_min"] < v["clock"]["expected_min"]
    assert all(n["state"] in ("RUNNING", "STOPPED") and not n["silent"] for n in v["nodes"])
    assert v["playbook"]["active"] is False


# ------------------------------------------------------------------ scenarios (no operator)
@pytest.mark.parametrize("seed", SEEDS)
def test_frozen_screen_goes_red_quickly(seed):
    m = run_scenario("frozen_screen", seed=seed, after_attack_s=300)
    assert m["false_alarms_before_attack"] == 0
    assert m["first_amber_s"] <= 120 and m["first_red_s"] <= 150 and m["final_state"] == "red"


@pytest.mark.parametrize("seed", SEEDS)
def test_colorado_style_goes_red_without_any_operator(seed):
    m = run_scenario("colorado_style", seed=seed, after_attack_s=300)
    assert m["first_amber_s"] <= 90 and m["first_red_s"] <= 150 and m["final_state"] == "red"


@pytest.mark.parametrize("seed", SEEDS)
def test_replay_is_caught_by_the_screen_alone(seed):
    m = run_scenario("replay", seed=seed, after_attack_s=300)
    assert m["first_red_s"] <= 150 and m["final_state"] == "red"


@pytest.mark.parametrize("seed", SEEDS)
def test_power_outage_goes_red_but_says_it_is_an_outage_candidate(seed):
    r = DrillRunner("power_loss_lookalike", seed=seed)
    r.advance(300)
    v = r.last_view
    assert v["trust"]["state"] == "red"
    assert any("backup power" in c["title"] for c in v["trust"]["info_cards"])


# -------------------------------------------------- the honest limits: no operator, no red
@pytest.mark.parametrize("seed", SEEDS)
def test_careful_forgery_alone_reaches_amber_but_not_red(seed):
    """A noisy, self-consistent forgery is caught by ONE witness (one node): amber. Red needs a second witness."""
    m = run_scenario("forced_off_spoofed", seed=seed, after_attack_s=600)
    assert m["first_amber_s"] is not None and m["first_amber_s"] <= 90
    assert m["first_red_s"] is None and m["final_state"] == "amber"


@pytest.mark.parametrize("seed", SEEDS)
def test_careful_forgery_goes_red_the_moment_an_operator_looks(seed):
    m = run_scenario("forced_off_spoofed", seed=seed, after_attack_s=300, operator_script=True)
    assert m["first_red_s"] == pytest.approx(120, abs=10) and m["final_state"] == "red"


@pytest.mark.parametrize("seed", SEEDS)
def test_hidden_valve_closure_is_only_visible_through_customers_and_stays_amber(seed):
    """The node cannot see a closed valve. Customers alone can raise amber, never red."""
    m = run_scenario("hidden_valve_closure", seed=seed, after_attack_s=600, operator_script=True)
    assert m["first_amber_s"] is not None and m["first_amber_s"] <= 200
    assert m["first_red_s"] is None and m["final_state"] == "amber"


# ---------------------------------------------------------------- what the core does about it
def test_red_starts_the_playbook_anchors_the_log_and_alerts():
    r = DrillRunner("colorado_style", seed=1)
    r.advance(200)
    v = r.last_view
    assert v["playbook"]["active"] and v["playbook"]["started_by"] == "automatic"
    assert any(a["level"] == "red" for a in v["alerts"])
    assert r.core.log.last_anchor_ts is not None and r.core.log.last_anchor_ts >= r.attack_t   # head hash sent out
    ok, bad_at, msg = r.core.log.verify()
    assert ok, msg


def test_the_clock_uses_the_last_trusted_level_when_the_screen_is_untrusted():
    r = DrillRunner("frozen_screen", seed=1)
    r.advance(300)
    v = r.last_view
    assert v["trust"]["state"] == "red"
    assert "last trusted SCADA level" in v["clock"]["basis"] and v["clock"]["needs_operator_reading"]
    assert v["clock"]["worst_min"] is not None and v["clock"]["worst_min"] < v["clock"]["expected_min"]


def test_an_operator_level_reading_replaces_the_model_and_clears_the_request():
    r = DrillRunner("colorado_style", seed=1)
    r.advance(200)
    before = r.last_view["clock"]
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="tank_level_ft", value=round(r.sys.level_ft, 2)))
    r.advance(10)
    after = r.last_view["clock"]
    assert before["needs_operator_reading"] and after["basis"].startswith("operator tank reading")
    assert not after["needs_operator_reading"]


def test_a_confirmed_flow_extends_the_clock_and_a_node_stopping_withdraws_the_credit():
    r = DrillRunner("replay", seed=2)             # P2 is running, then the attacker stops it at +30 s
    r.advance(20)
    base = r.last_view["clock"]["worst_min"]
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="flow_confirmed", value=True, pump_id="P2"))
    r.advance(5)
    assert r.last_view["clock"]["credited_pumps"] == ["P2"]
    assert r.last_view["clock"]["worst_min"] > base
    r.advance(60)                                       # P2 stops at +30 s: the node says STOPPED after the confirmation
    assert r.last_view["clock"]["credited_pumps"] == []


def test_ack_plus_clean_period_returns_to_green_after_the_drill():
    r = DrillRunner("frozen_screen", seed=1)
    r.advance(200)
    assert r.last_view["trust"]["state"] == "red"
    r.view.normal()
    r.sys.force_pump("P2", None)
    r.advance(60)
    assert r.last_view["trust"]["state"] == "red"       # latched
    assert r.core.acknowledge("Sam", r.sys.t)
    r.advance(r.profile.clean_period_s + 120)
    assert r.last_view["trust"]["state"] == "green"
    assert r.last_view["playbook"]["active"] is False


def test_playbook_steps_are_logged_with_who_and_when():
    r = DrillRunner("colorado_style", seed=1)
    r.advance(200)
    assert r.core.complete_step("c1", "Sam", r.sys.t)
    assert not r.core.complete_step("c1", "Sam", r.sys.t)
    rec = [x for x in r.core.log.records if x["type"] == "playbook_step"][-1]
    assert rec["source"] == "operator:Sam" and rec["content"]["step"] == "c1"


# --------------------------------------------------------------------- node security
def test_forged_node_message_is_rejected_and_surfaces_as_amber():
    r = DrillRunner("frozen_screen", seed=1)
    bad = make_wire(b"not-the-secret", n="NB", b=99, s=1, t_ms=int(r.sys.t * 1000), k="ev", st="R", c=990,
                    ca="R", cm="R", p=1, f="deadbeef")
    for _ in range(3):
        assert r.core.ingest_node(bad, r.sys.t) == {"accepted": False, "reason": "bad_signature"}
    v = r.core.evaluate(r.sys.t)
    assert v["trust"]["state"] == "amber" and "messages_rejected" in v["trust"]["flags"]
    assert any(rec["type"] == "node_message_rejected" for rec in r.core.log.records)


def test_replayed_valid_node_message_is_rejected():
    r = DrillRunner("frozen_screen", seed=1)
    w = make_wire(DEMO_SECRETS["NA"], n="NA", b=1, s=10_000_000, t_ms=int(r.sys.t * 1000), k="hb", st="S", c=900,
                  ca="S", cm="S", p=1, f="a1b2c3d4")
    assert r.core.ingest_node(w, r.sys.t)["accepted"]
    assert r.core.ingest_node(w, r.sys.t) == {"accepted": False, "reason": "replay"}


def test_a_node_that_goes_quiet_is_amber_within_a_minute():
    r = DrillRunner("frozen_screen", seed=1, operator_script=False)
    r.nodes["NB"].jam()                                  # the node is jammed: no more messages
    r.advance(90)
    v = r.last_view
    assert any(n["silent"] for n in v["nodes"] if n["node_id"] == "NB")
    assert v["trust"]["state"] in ("amber", "red")


def test_drills_are_deterministic():
    a = run_scenario("replay", seed=3, after_attack_s=200)
    b = run_scenario("replay", seed=3, after_attack_s=200)
    assert a == b


def test_all_six_scripted_scenarios_and_free_play_exist_and_are_described():
    scripted = {k for k, s in SCENARIOS.items() if s.events}
    assert scripted == {"frozen_screen", "forced_off_spoofed", "colorado_style", "replay",
                        "hidden_valve_closure", "power_loss_lookalike"}
    assert set(SCENARIOS) == scripted | {"free_play"}
    assert all(s.summary for s in SCENARIOS.values())


def test_free_play_starts_the_attack_clock_at_the_first_thing_you_break():
    r = DrillRunner("free_play", seed=1)
    r.advance(600)
    assert not r.attacked and r.metrics()["false_alarms_before_attack"] == 0
    r.inject("pump_off", "P2")
    r.inject("freeze")
    r.advance(240)
    m = r.metrics()
    assert r.attacked and m["first_amber_s"] is not None and m["first_amber_s"] <= 90
    with pytest.raises(KeyError):
        r.inject("explode")
    with pytest.raises(ValueError):
        r.inject("pump_off")                              # which pump?
