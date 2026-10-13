"""Trust Engine: weights, thresholds, grace period, latching, caps."""
import pytest

from hydrowitness import config as C
from hydrowitness.engine.consistency import RuleResult
from hydrowitness.engine.trust import TrustEngine, TrustInputs
from hydrowitness.schemas import ManualReading, NodeMessage, ScadaSnapshot

NOW = 1_800_000_000.0


def snap(t=NOW, claims=None, level=22.0, pressure=26.8, demand=180.0):
    claims = {"P1": False, "P2": True} if claims is None else claims
    return ScadaSnapshot(t=t, tank_level_ft=level, pump_status=claims, demand_gpm=demand, pressure_psi=pressure,
                         pump_flow_gpm={k: (150.0 if v else 0.0) for k, v in claims.items()})


def nmsg(nid, state, *, conf=0.95, acc=None, mic=None, ts=NOW - 60, received=NOW, mains=True):
    acc = acc or {"RUNNING": "RUNNING", "STOPPED": "STOPPED"}.get(state, "UNKNOWN")
    mic = mic or acc
    return NodeMessage(node_id=nid, boot=1, seq=1, ts=ts, kind="hb", state=state, confidence=conf,
                       acc_state=acc, mic_state=mic, mains_ok=mains, fw_hash="a1b2c3d4", received_t=received)


def inputs(profile, *, now=NOW, scada="default", nodes=None, since=None, silent=None, claim_since=None,
           manual=None, rejected=None, rules=None):
    s = snap(t=now) if scada == "default" else scada
    nodes = nodes if nodes is not None else {"NA": nmsg("NA", "STOPPED", ts=now - 60, received=now),
                                             "NB": nmsg("NB", "RUNNING", ts=now - 60, received=now)}
    return TrustInputs(
        now=now, profile=profile, scada_latest=s, scada_near=lambda t: s, rules=rules or [],
        node_msgs=nodes, node_state_since=since or {n: (m.ts if m else None) for n, m in nodes.items()},
        node_silent=silent or {n: False for n in nodes}, claim_since=claim_since or {},
        manual=manual or [], rejected_recent=rejected or [])


def fired_rule(rule_id, severity):
    return RuleResult(rule_id, rule_id, severity, True, "x", since=NOW - 100)


# ------------------------------------------------------------------ the baseline
def test_everything_agrees_is_green(profile):
    r = TrustEngine(profile).evaluate(inputs(profile))
    assert r.state == "green" and r.score == 0 and r.cards == [] and r.flags == []


def test_one_node_with_both_channels_is_amber_not_red(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED")}      # screen says P2 RUNNING
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes))
    assert r.state == "amber"
    assert r.score == pytest.approx(C.W_NODE_BOTH * 0.95, abs=0.01)           # fresh x confidence
    assert r.cards[0]["kind"] == "node" and "Pump 2" not in r.cards[0]["title"]  # uses the pump id
    assert "P2" in r.cards[0]["title"]


def test_two_nodes_contradicting_is_red(profile):
    s = snap(claims={"P1": False, "P2": True})
    nodes = {"NA": nmsg("NA", "RUNNING"), "NB": nmsg("NB", "STOPPED")}
    r = TrustEngine(profile).evaluate(inputs(profile, scada=s, nodes=nodes))
    assert r.state == "red" and r.score >= C.RED_AT


def test_a_node_disagreement_inside_the_grace_period_is_ignored(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED", ts=NOW - 3)}   # node changed 3 s ago
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes, claim_since={"P2": NOW - 3}))
    assert r.state == "green"


def test_a_scada_change_inside_the_grace_period_is_ignored_too(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED", ts=NOW - 300)}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes, claim_since={"P2": NOW - 4}))
    assert r.state == "green"                                                  # SCADA just said RUNNING: command lag


def test_one_channel_node_alone_cannot_reach_amber(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED", conf=0.9, acc="STOPPED", mic="UNKNOWN")}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes))
    assert r.state == "green" and r.score == pytest.approx(C.W_NODE_ONE * 0.9, abs=0.01)


def test_one_channel_node_plus_a_fired_rule_is_amber(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED", conf=0.9, acc="STOPPED", mic="UNKNOWN")}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes, rules=[fired_rule("frozen_data", C.SEV_FROZEN)]))
    assert r.state == "amber"


def test_low_confidence_nodes_are_not_witnesses(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED", conf=0.4)}
    assert TrustEngine(profile).evaluate(inputs(profile, nodes=nodes)).state == "green"


def test_unknown_node_state_is_not_a_witness_but_channel_disagreement_is_shown(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"),
             "NB": nmsg("NB", "UNKNOWN", conf=0.3, acc="RUNNING", mic="STOPPED")}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes))
    assert r.state == "green"
    assert any(c["id"] == "chan:NB" for c in r.info_cards)


def test_a_node_on_backup_power_gets_an_info_card_but_no_blame(profile):
    nodes = {"NA": nmsg("NA", "STOPPED", mains=False), "NB": nmsg("NB", "RUNNING", mains=False)}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes))
    assert r.state == "green"
    assert {c["id"] for c in r.info_cards} == {"power:NA", "power:NB"}


def test_stale_node_witness_loses_weight(profile):
    # the node message is 600 s old: freshness 1 - 600/900 = 1/3
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED", received=NOW - 600)}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes))
    assert r.score == pytest.approx(2.0 * (1 / 3) * 0.95, abs=0.01)
    assert r.state == "green"


# ------------------------------------------------------------------- operators
def test_operator_who_sees_a_different_pump_state_forces_red(profile):
    man = [ManualReading(t=NOW - 10, kind="pump_running", value=False, pump_id="P2", operator="Sam")]
    r = TrustEngine(profile).evaluate(inputs(profile, manual=man))
    assert r.state == "red" and "operator_contradiction" in r.flags
    assert "Sam" in r.cards[0]["text"]


def test_operator_agreeing_with_the_screen_changes_nothing(profile):
    man = [ManualReading(t=NOW - 10, kind="pump_running", value=True, pump_id="P2")]
    assert TrustEngine(profile).evaluate(inputs(profile, manual=man)).state == "green"


def test_operator_level_reading_uses_the_tolerance(profile):
    ok = [ManualReading(t=NOW - 10, kind="tank_level_ft", value=22.5)]       # 0.5 ft off, tolerance 0.75
    bad = [ManualReading(t=NOW - 10, kind="tank_level_ft", value=20.0)]
    assert TrustEngine(profile).evaluate(inputs(profile, manual=ok)).state == "green"
    r = TrustEngine(profile).evaluate(inputs(profile, manual=bad))
    assert r.state == "red" and r.cards[0]["id"] == "op:level"


def test_operator_gauge_reading_uses_the_tolerance(profile):
    ok = [ManualReading(t=NOW - 10, kind="gauge_psi", value=24.0)]
    bad = [ManualReading(t=NOW - 10, kind="gauge_psi", value=12.0)]
    assert TrustEngine(profile).evaluate(inputs(profile, manual=ok)).state == "green"
    assert TrustEngine(profile).evaluate(inputs(profile, manual=bad)).state == "red"


def test_only_the_newest_operator_reading_counts(profile):
    man = [ManualReading(t=NOW - 100, kind="pump_running", value=False, pump_id="P2"),
           ManualReading(t=NOW - 10, kind="pump_running", value=True, pump_id="P2")]
    assert TrustEngine(profile).evaluate(inputs(profile, manual=man)).state == "green"


def test_old_operator_readings_fade_but_the_latch_keeps_the_state(profile):
    eng = TrustEngine(profile)
    man = [ManualReading(t=NOW - 10, kind="pump_running", value=False, pump_id="P2")]
    assert eng.evaluate(inputs(profile, manual=man)).state == "red"
    later = NOW + 1000                      # the reading is now older than the 15 minute freshness horizon
    r = eng.evaluate(inputs(profile, now=later, manual=man))
    assert r.raw_state == "green" and r.state == "red" and r.needs_ack


# ------------------------------------------------------------------- customers
def calls(n, *, same_address=False, t=NOW - 60):
    return [ManualReading(t=t + i, kind="customer_call", value=True, address="1 Oak St" if same_address else f"{i} Oak St")
            for i in range(n)]


def test_two_callers_are_not_enough(profile):
    assert TrustEngine(profile).evaluate(inputs(profile, manual=calls(2))).state == "green"


def test_three_distinct_addresses_make_amber_but_never_red(profile):
    r = TrustEngine(profile).evaluate(inputs(profile, manual=calls(3)))
    assert r.state == "amber"
    many = TrustEngine(profile).evaluate(inputs(profile, manual=calls(40)))
    assert many.state == "amber" and many.score <= C.W_CUSTOMER_CAP + 1e-9


def test_the_same_address_calling_repeatedly_counts_once(profile):
    assert TrustEngine(profile).evaluate(inputs(profile, manual=calls(10, same_address=True))).state == "green"


def test_calls_dont_contradict_a_screen_that_already_shows_low_pressure(profile):
    low = snap(pressure=21.0)
    assert TrustEngine(profile).evaluate(inputs(profile, scada=low, manual=calls(5))).state == "green"


def test_old_calls_fall_out_of_the_window(profile):
    old = calls(5, t=NOW - C.CUSTOMER_WINDOW_S - 100)
    assert TrustEngine(profile).evaluate(inputs(profile, manual=old)).state == "green"


# ------------------------------------------------------------- stale / silent / rules
def test_stale_scada_alone_is_amber(profile):
    r = TrustEngine(profile).evaluate(inputs(profile, scada=snap(t=NOW - 300), nodes={"NA": nmsg("NA", "STOPPED", ts=NOW - 600),
                                                                                    "NB": nmsg("NB", "RUNNING", ts=NOW - 600)}))
    assert r.state == "amber" and r.stale and "scada_stale" in r.flags


def test_stale_scada_plus_a_witness_that_disagrees_is_red(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED")}
    r = TrustEngine(profile).evaluate(inputs(profile, scada=snap(t=NOW - 300), nodes=nodes))
    assert r.state == "red"


def test_no_scada_data_at_all_is_amber_and_flagged(profile):
    r = TrustEngine(profile).evaluate(inputs(profile, scada=None))
    assert r.state == "amber" and "no_scada_data" in r.flags


def test_a_silent_node_is_amber(profile):
    r = TrustEngine(profile).evaluate(inputs(profile, silent={"NA": False, "NB": True}))
    assert r.state == "amber" and "node_silent:NB" in r.flags


def test_a_silent_node_is_not_used_as_a_witness(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED")}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes, silent={"NA": False, "NB": True}))
    assert r.score == 0 and r.state == "amber"          # amber only because it is silent


def test_rules_add_to_the_score(profile):
    nodes = {"NA": nmsg("NA", "STOPPED"), "NB": nmsg("NB", "STOPPED")}
    r = TrustEngine(profile).evaluate(inputs(profile, nodes=nodes, rules=[fired_rule("replay", C.SEV_REPLAY)]))
    assert r.state == "red" and r.score == pytest.approx(1.9 + 2.0, abs=0.01)


def test_rejected_node_messages_surface_as_amber_and_are_capped(profile):
    rej = [(NOW - 5, "NB", "bad_signature")] * 6
    r = TrustEngine(profile).evaluate(inputs(profile, rejected=rej))
    assert r.state == "amber" and "messages_rejected" in r.flags
    assert r.score == pytest.approx(C.INTEGRITY_CAP)


def test_rejected_messages_plus_stale_scada_do_not_force_red(profile):
    rej = [(NOW - 5, "NB", "bad_signature")] * 6
    nodes = {"NA": nmsg("NA", "STOPPED", ts=NOW - 600), "NB": nmsg("NB", "RUNNING", ts=NOW - 600)}
    r = TrustEngine(profile).evaluate(inputs(profile, scada=snap(t=NOW - 300), nodes=nodes, rejected=rej))
    assert r.state == "amber"          # integrity events are not a witness that SCADA is wrong about the plant


# --------------------------------------------------------------------- latching
def _amber_inputs(profile, now):
    nodes = {"NA": nmsg("NA", "STOPPED", ts=now - 60, received=now), "NB": nmsg("NB", "STOPPED", ts=now - 60, received=now)}
    return inputs(profile, now=now, nodes=nodes)


def test_state_never_returns_to_green_by_itself(profile):
    eng = TrustEngine(profile)
    assert eng.evaluate(_amber_inputs(profile, NOW)).state == "amber"
    for k in range(1, 6):
        r = eng.evaluate(inputs(profile, now=NOW + k * 1000))
        assert r.raw_state == "green" and r.state == "amber" and r.latched and r.needs_ack


def test_ack_alone_is_not_enough_the_clean_period_must_pass(profile):
    eng = TrustEngine(profile)
    eng.evaluate(_amber_inputs(profile, NOW))
    eng.evaluate(inputs(profile, now=NOW + 100))
    assert eng.acknowledge(NOW + 110)
    assert eng.evaluate(inputs(profile, now=NOW + 120)).state == "amber"
    r = eng.evaluate(inputs(profile, now=NOW + 100 + profile.clean_period_s - 1))
    assert r.state == "amber" and not r.needs_ack
    r = eng.evaluate(inputs(profile, now=NOW + 100 + profile.clean_period_s + 1))
    assert r.state == "green" and not r.latched


def test_a_clean_period_without_an_ack_is_not_enough(profile):
    eng = TrustEngine(profile)
    eng.evaluate(_amber_inputs(profile, NOW))
    eng.evaluate(inputs(profile, now=NOW + 100))
    assert eng.evaluate(inputs(profile, now=NOW + 100 + 3 * profile.clean_period_s)).state == "amber"


def test_a_new_problem_restarts_the_clean_period(profile):
    eng = TrustEngine(profile)
    eng.evaluate(_amber_inputs(profile, NOW))
    eng.evaluate(inputs(profile, now=NOW + 100))
    eng.acknowledge(NOW + 110)
    eng.evaluate(inputs(profile, now=NOW + 1000))
    eng.evaluate(_amber_inputs(profile, NOW + 1200))                    # trouble again
    r = eng.evaluate(inputs(profile, now=NOW + 1300))
    assert r.state == "amber"
    r = eng.evaluate(inputs(profile, now=NOW + 1300 + profile.clean_period_s - 5))
    assert r.state == "amber"                                           # the clean clock restarted at 1300


def test_red_stays_red_when_the_raw_state_drops_to_amber(profile):
    eng = TrustEngine(profile)
    man = [ManualReading(t=NOW - 10, kind="pump_running", value=False, pump_id="P2")]
    assert eng.evaluate(inputs(profile, manual=man)).state == "red"
    r = eng.evaluate(_amber_inputs(profile, NOW + 2000))
    assert r.raw_state == "amber" and r.state == "red"


def test_ack_needs_something_to_acknowledge(profile):
    assert TrustEngine(profile).acknowledge(NOW) is False
