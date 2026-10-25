"""The countdown and the evidence behind it: zero stays zero, a missing estimate says so, age and assumptions are
stated, each quantity says who reports it, and "next useful check" and "what changed" are worked out from the real
engines. Nothing here changes the green/amber/red verdict."""
import pytest

from hydrowitness.demo import demo_profile
from hydrowitness.engine.time_to_trouble import (CreditedFlow, LevelReading, clock_text, compute_clock,
                                                 format_minutes, l_min_ft)
from hydrowitness.playbook import DEFAULT_START_MIN, Playbook
from hydrowitness.schemas import ManualReading
from hydrowitness.sim.drills import DrillRunner

NOW = 1_800_000_000.0


def steps(status):
    return {s["id"]: s for ph in status["phases"] for s in ph["steps"]}


def tank_read(r, value, who="Sam"):
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="tank_level_ft", value=value, operator=who))


# ------------------------------------------------------------------------------- zero stays zero
def test_a_countdown_of_zero_is_not_replaced_by_the_eight_hour_default():
    pb = Playbook()
    pb.start(NOW, 0.0)
    assert pb.start_worst_min == 0.0 and pb.start_basis == "empty"
    st = pb.status(NOW, 0.0)                         # used to divide by zero once 0 was allowed
    assert st["start_worst_min"] == 0.0 and "already at 0 minutes" in st["start_note"]
    s = steps(st)
    assert s["k1"]["target"] == "now" and s["k2"]["target"] == "now"      # was "within 48 min"
    assert s["p1"]["status"] == "due" and s["p3"]["target"] == "now"      # the 50% and 25% triggers are already met


def test_zero_in_the_real_clock_reaches_the_playbook_as_zero():
    p = demo_profile()
    r = compute_clock(p, NOW, LevelReading(l_min_ft(p) - 0.1, NOW, "operator"), [])
    assert r.ok and r.worst_min == 0.0 and r.state == "empty"
    assert clock_text(r, "worst") == "0 min" and format_minutes(r.worst_min) == "0 min"
    pb = Playbook()
    pb.start(NOW, r.worst_min)
    assert pb.start_worst_min == 0.0


def test_a_missing_estimate_uses_the_default_but_says_so():
    pb = Playbook()
    pb.start(NOW, None)
    assert pb.start_worst_min == DEFAULT_START_MIN and pb.start_basis == "unavailable"
    assert "no countdown estimate" in pb.status(NOW, None)["start_note"].lower()
    pb2 = Playbook()
    pb2.start(NOW, None, basis="not_falling")
    assert pb2.start_basis == "not_falling" and "not falling" in pb2.status(NOW, None)["start_note"]
    pb3 = Playbook()
    pb3.start(NOW, 600.0)
    assert pb3.status(NOW, 600.0)["start_note"] == "" and pb3.start_basis == "countdown"


def test_no_tank_reading_means_no_estimate_not_a_guess():
    p = demo_profile()
    r = compute_clock(p, NOW, None, [])
    assert not r.ok and r.state == "unavailable" and r.worst_min is None and r.expected_min is None
    assert clock_text(r, "worst") == clock_text(r, "expected") == "estimate unavailable"
    assert "stable" not in clock_text(r, "worst") and "Nothing is guessed" in r.assumption_lines[0]


def test_not_falling_is_worded_differently_from_unavailable():
    p = demo_profile()
    r = compute_clock(p, NOW, LevelReading(l_min_ft(p) + 5, NOW, "scada"), [CreditedFlow("P1", 320.0, NOW, "x")])
    assert r.state == "not_falling"
    assert clock_text(r, "worst") == "not falling" and clock_text(r, "expected") == "not falling within 72 h"


# -------------------------------------------------------------------- age, uncertainty and assumptions
def test_the_age_of_the_reading_is_labelled_and_costs_minutes():
    p = demo_profile()
    lvl = l_min_ft(p) + 12
    fresh = compute_clock(p, NOW, LevelReading(lvl, NOW, "operator"), [])
    aging = compute_clock(p, NOW, LevelReading(lvl, NOW - 1800, "operator"), [])
    old = compute_clock(p, NOW, LevelReading(lvl, NOW - 4 * 3600, "operator"), [])
    assert (fresh.quality, aging.quality, old.quality) == ("fresh", "aging", "old")
    assert fresh.uncertainty_ft < aging.uncertainty_ft < old.uncertainty_ft          # uncertainty grows with age
    assert fresh.worst_min > aging.worst_min > old.worst_min                        # and the worst case shrinks
    assert aging.uncertainty_cost_min > fresh.uncertainty_cost_min >= 0
    assert "30 min old" in aging.quality_text and "Read the tank again" in old.quality_text
    assert "4 h 00 min old" in old.quality_text


def test_a_modelled_level_is_labelled_model_only():
    p = demo_profile()
    r = compute_clock(p, NOW, LevelReading(l_min_ft(p) + 12, NOW - 600, "scada_last_trusted"), [])
    assert r.quality == "model_only" and r.needs_operator_reading
    assert r.quality_text.startswith("Model only") and "no current measurement" in r.quality_text


def test_every_estimate_states_its_demand_assumptions_and_how_far_ahead_it_looks():
    p = demo_profile()
    r = compute_clock(p, NOW, LevelReading(l_min_ft(p) + 12, NOW, "operator"), [])
    text = " ".join(r.assumption_lines)
    assert "300 gpm" in text and "72 h" in text and "none credited" in text
    assert r.assumptions["worst_demand_gpm"] == 300.0 and r.assumptions["horizon_h"] == 72
    assert "expected_demand_now_gpm" in r.assumptions and "level_uncertainty_ft" in r.assumptions


# ------------------------------------------------------- a running pump earns no credit on its own
def test_a_pump_that_sounds_like_it_is_running_earns_no_credit_until_a_person_confirms_flow():
    r = DrillRunner("frozen_screen", seed=1)
    v = r.last_view
    p2 = next(i for i in v["quantities"]["pumps"] if i["pump_id"] == "P2")
    assert p2["status"] == "agree" and p2["best_state"] == "RUNNING"        # the screen and the node both say running
    assert v["clock"]["credited_gpm"] == 0 and "none credited" in " ".join(v["clock"]["assumption_lines"])
    assert "does not prove water is moving" in p2["limit"] and p2["flow"]["state"] == "not_confirmed"
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="flow_confirmed", value=True, pump_id="P2", operator="Sam"))
    v = r.core.evaluate(r.sys.t)
    p2 = next(i for i in v["quantities"]["pumps"] if i["pump_id"] == "P2")
    assert p2["flow"]["state"] == "confirmed" and v["clock"]["credited_gpm"] > 0


# --------------------------------------------------------------- dashboard, alerts and playbook agree
def test_dashboard_alert_and_playbook_describe_the_same_situation():
    r = DrillRunner("colorado_style", seed=1)
    for _ in range(100):                              # stop on the very evaluation that turns red
        r.advance(5)
        if r.last_view["trust"]["state"] == "red":
            break
    v = r.last_view
    assert v["trust"]["state"] == "red"
    worst = v["clock"]["worst_text"]
    assert f"Worst case: {worst}." in v["alerts"][0]["message"]             # the alert says what the dashboard says
    assert v["playbook"]["active"] and v["playbook"]["start_basis"] == "countdown"
    assert v["quantities"]["remaining"]["worst_text"] == worst
    assert v["quantities"]["remaining"]["expected_text"] == v["clock"]["expected_text"]


def test_with_no_tank_reading_every_part_of_the_screen_says_unavailable():
    r = DrillRunner("frozen_screen", seed=1)
    r.core.snaps.clear()                              # nothing from the screen, no baseline, no operator reading
    r.core.baseline = None
    r.core.green_levels.clear()
    v = r.core.evaluate(r.sys.t)
    assert v["clock"]["worst_text"] == v["clock"]["expected_text"] == "estimate unavailable"
    assert v["quantities"]["tank"]["status"] == "unavailable" and v["quantities"]["tank"]["value_ft"] is None
    assert v["quantities"]["remaining"]["status"] == "unavailable"
    assert v["next_check"]["primary"]["id"] == "tank_missing"


# ------------------------------------------------------------- what each witness supports
def test_the_tank_level_says_whether_it_is_independent_screen_only_or_an_estimate():
    r = DrillRunner("frozen_screen", seed=1)
    assert r.last_view["quantities"]["tank"]["status"] == "screen_only"            # only the screen reports it
    r.advance(240)                                                                  # the screen freezes and the light goes red
    t = r.last_view["quantities"]["tank"]
    assert r.last_view["trust"]["state"] == "red" and t["status"] == "estimate"
    assert "Not measured now" in t["status_text"] and r.last_view["clock"]["quality"] == "model_only"
    tank_read(r, round(r.sys.level_ft, 2))
    v = r.core.evaluate(r.sys.t)
    assert v["quantities"]["tank"]["status"] == "independent" and v["clock"]["quality"] == "fresh"


def test_pressure_is_marked_as_a_model_estimate_until_someone_reads_a_gauge():
    r = DrillRunner("colorado_style", seed=1)
    r.advance(240)                                    # screen dark: its last pressure is old
    pr = r.last_view["quantities"]["pressure"]
    assert pr["status"] == "estimate" and pr["model_psi"] is not None
    model = next(x for x in pr["rows"] if x["kind"] == "model")
    assert "Not a measurement" in model["note"]
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="gauge_psi", value=24.0, operator="Sam"))
    assert r.core.evaluate(r.sys.t)["quantities"]["pressure"]["status"] == "independent"


def test_a_screen_witness_disagreement_is_a_listed_conflict_and_names_the_witness():
    r = DrillRunner("forced_off_spoofed", seed=1)
    r.advance(100)
    q = r.last_view["quantities"]
    p2 = next(i for i in q["pumps"] if i["pump_id"] == "P2")
    assert p2["status"] == "conflict" and "Witness Node NB" in p2["status_text"] and "STOPPED" in p2["status_text"]
    assert any(g["kind"] == "conflict" and g["quantity"] == "Pump P2" for g in q["gaps"])


def test_a_silent_node_is_listed_as_missing_information():
    r = DrillRunner("frozen_screen", seed=1)
    r.inject("jam_node", "NB")
    r.advance(120)
    gaps = r.last_view["quantities"]["gaps"]
    assert any(g["kind"] == "missing" and "Node NB is silent" in g["text"] for g in gaps)


# -------------------------------------------------------------------------- next useful check
def test_nothing_needs_checking_when_everything_agrees_and_is_fresh():
    r = DrillRunner("frozen_screen", seed=1)
    nc = r.last_view["next_check"]
    assert nc["primary"] is None and "Nothing needs checking" in nc["text"]


def test_a_screen_witness_conflict_points_at_the_pump_and_says_what_it_would_settle():
    r = DrillRunner("forced_off_spoofed", seed=1)
    r.advance(60)
    p = r.last_view["next_check"]["primary"]
    assert p["id"] == "pump_look:P2" and p["enter"] == {"kind": "pump_running", "pump_id": "P2"}
    assert "outweighs" in p["why"] and "goes red" in p["effect"]


def test_once_the_light_is_red_the_tank_reading_comes_before_the_pump_look():
    r = DrillRunner("frozen_screen", seed=1)
    r.advance(130)
    v = r.last_view
    assert v["trust"]["state"] == "red"
    assert v["next_check"]["primary"]["id"] == "tank_refresh"
    assert "pump_look:P2" in [a["id"] for a in v["next_check"]["alternatives"]]


def test_customer_calls_point_to_a_gauge_at_a_customer_tap():
    r = DrillRunner("hidden_valve_closure", seed=1)
    r.advance(200)
    p = r.last_view["next_check"]["primary"]
    assert r.last_view["trust"]["state"] == "amber" and p["id"] == "pressure_gauge"
    assert "customer" in p["title"].lower() and p["enter"]["kind"] == "gauge_psi"


def test_the_flow_check_effect_is_what_the_real_clock_gives_with_that_evidence():
    r = DrillRunner("hidden_valve_closure", seed=1)
    r.advance(200)
    v = r.last_view
    flow = next(a for a in [v["next_check"]["primary"]] + v["next_check"]["alternatives"] if a["id"] == "flow_confirm:P2")
    level = r.core._choose_level(r.sys.t, r.core.state)
    hypo = compute_clock(r.profile, r.sys.t, level, r.core._credits(r.sys.t)
                         + [CreditedFlow("P2", r.profile.pump("P2").rated_gpm * 0.8, r.sys.t, "x")])
    after = "not falling" if hypo.worst_min is None else format_minutes(hypo.worst_min)
    assert f"from {v['clock']['worst_text']} to {after}" in flow["effect"]
    assert "closed valve" in flow["why"]


# ----------------------------------------------------------------------------- what changed
def test_a_reading_that_changes_the_countdown_says_by_how_much():
    r = DrillRunner("colorado_style", seed=1)
    r.advance(240)
    tank_read(r, round(r.sys.level_ft, 2))
    v = r.core.evaluate(r.sys.t)
    lu = v["last_update"]
    assert lu is not None and "read the tank" in lu["headline"]
    assert lu["before"]["worst_text"] != lu["after"]["worst_text"] and lu["after"]["worst_text"] == v["clock"]["worst_text"]
    assert any(c.startswith("Worst case:") for c in lu["changes"]) and "changed the countdown" in lu["headline"]
    assert any("operator's own tank reading" in c for c in lu["changes"])


def test_time_passing_alone_is_never_reported_as_a_change():
    r = DrillRunner("frozen_screen", seed=1)
    r.advance(60)
    assert r.last_view["last_update"] is None
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="customer_call", value=True, address="1 A St", operator="d"))
    lu = r.core.evaluate(r.sys.t)["last_update"]
    assert lu is not None and "did not change the estimate" in lu["headline"]
    assert len(lu["changes"]) == 1 and lu["changes"][0].startswith("The countdown did not change")


def test_the_what_changed_notice_expires():
    r = DrillRunner("frozen_screen", seed=1)
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind="customer_call", value=True, address="1 A St", operator="d"))
    assert r.core.evaluate(r.sys.t)["last_update"] is not None
    r.advance(1000)
    assert r.last_view["last_update"] is None
