"""The four evidence checks on the dashboard: what each one says, that they stay honest, and that the log check is cheap."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from hydrowitness import config as C
from hydrowitness.demo import DEMO_SECRETS, demo_profile
from hydrowitness.engine import evidence_checks as E
from hydrowitness.evidence_log import EvidenceLog
from hydrowitness.service import HydroWitnessCore
from hydrowitness.sim.drills import DrillRunner

ROOT = Path(__file__).resolve().parent.parent
NOW = 10_000.0


def _tr(cards=(), flags=(), stale=False):
    return NS(cards=list(cards), flags=list(flags), stale=stale)


def _card(kind="node", value=1.5, title="P1: screen and Witness Node disagree"):
    return {"kind": kind, "contribution": value, "title": title}


def _node(**kw):
    d = dict(node_id="NA", pump_id="P1", silent=False, state="RUNNING", confidence=0.9, acc_state="RUNNING",
             mic_state="RUNNING", mains_ok=True, last_seen_age_s=5.0)
    d.update(kw)
    return d


def _build(**kw):
    args = dict(now=NOW, scada_latest=NS(t=NOW - 3), tr=_tr(), nodes=[_node()], manual=[],
                log_ok=True, log_bad=None, log_msg="chain intact", log_count=12)
    args.update(kw)
    return E.build(**args)


def _by_id(result):
    return {c["id"]: c for c in result["checks"]}


# ----------------------------------------------------------------------------------- the shape
def test_all_four_pass_when_everything_agrees():
    r = _build()
    assert [c["id"] for c in r["checks"]] == ["integrity", "freshness", "agreement", "sensor"]
    assert all(c["status"] == "pass" for c in r["checks"])
    assert (r["passed"], r["total"], r["text"]) == (4, 4, "4 of 4 checks pass")


def test_it_is_a_count_not_an_invented_score():
    r = _build(log_ok=False, log_msg="record 3: previous-hash link is broken")
    assert set(r) == {"checks", "passed", "total", "text"}
    assert all(set(c) == {"id", "name", "status", "detail"} for c in r["checks"])
    assert all(c["status"] in ("pass", "warn", "fail", "na") for c in r["checks"])
    assert "/100" not in json.dumps(r) and "score" not in json.dumps(r)
    assert r["text"] == "3 of 4 checks pass"


# ------------------------------------------------------------------------------ record integrity
def test_a_broken_log_fails_and_says_where():
    c = _by_id(_build(log_ok=False, log_bad=3, log_msg="record 3: contents do not match its hash (record edited)"))["integrity"]
    assert c["status"] == "fail" and "record 3" in c["detail"]


def test_an_empty_log_is_not_called_a_chain_of_records():
    c = _by_id(_build(log_count=0, log_msg="log is empty"))["integrity"]
    assert c["status"] == "pass" and "No records yet" in c["detail"]
    assert "1 record," in _by_id(_build(log_count=1))["integrity"]["detail"]


# ---------------------------------------------------------------------------- evidence freshness
def test_freshness_warns_before_data_arrives_and_fails_when_stale():
    assert _by_id(_build(scada_latest=None))["freshness"]["status"] == "warn"
    old = _by_id(_build(scada_latest=NS(t=NOW - 400), tr=_tr(stale=True)))["freshness"]
    assert old["status"] == "fail" and "stale" in old["detail"] and "7 min" in old["detail"]
    assert _by_id(_build())["freshness"]["detail"] == "SCADA updated 3 s ago."


# ----------------------------------------------------------------------------- source agreement
def test_a_witness_that_disagrees_with_the_screen_fails():
    c = _by_id(_build(tr=_tr(cards=[_card(value=1.5)])))["agreement"]
    assert c["status"] == "fail" and c["detail"] == "P1: screen and Witness Node disagree."


def test_a_faint_disagreement_only_warns():
    assert _by_id(_build(tr=_tr(cards=[_card(value=0.4)])))["agreement"]["status"] == "warn"


def test_an_operator_who_contradicts_the_screen_always_fails():
    r = _build(tr=_tr(cards=[_card("operator", 0.2, "P1: the operator sees something different")], flags=["operator_contradiction"]))
    assert _by_id(r)["agreement"]["status"] == "fail"


def test_extra_disagreements_are_counted_not_listed():
    r = _build(tr=_tr(cards=[_card(value=1.5), _card("rule", 0.5, "Pressure jumped"), _card("customer", 0.3, "Calls")]))
    assert _by_id(r)["agreement"]["detail"] == "P1: screen and Witness Node disagree. 2 more sources also disagree."


def test_message_rejection_is_not_a_disagreement_between_sources():
    r = _build(tr=_tr(cards=[_card("integrity", 2.0, "Messages failed security checks")]))
    assert _by_id(r)["agreement"]["status"] == "pass"


def test_agreement_with_nothing_independent_is_a_warning_not_a_pass():
    c = _by_id(_build(nodes=[]))["agreement"]
    assert c["status"] == "warn" and "Only SCADA is reporting" in c["detail"]
    assert _by_id(_build(nodes=[_node(silent=True)]))["agreement"]["status"] == "warn"
    assert _by_id(_build(nodes=[_node(confidence=0.3)]))["agreement"]["status"] == "warn"


def test_a_recent_person_reading_counts_as_a_witness_and_an_old_one_does_not():
    fresh = NS(t=NOW - 60, kind="tank_level_ft", pump_id=None)
    old = NS(t=NOW - C.FRESHNESS_S - 1, kind="tank_level_ft", pump_id=None)
    call = NS(t=NOW - 60, kind="customer_call", pump_id=None)
    assert _by_id(_build(nodes=[], manual=[fresh]))["agreement"]["detail"] == "SCADA agrees with 1 independent reading."
    assert _by_id(_build(nodes=[], manual=[old]))["agreement"]["status"] == "warn"
    assert _by_id(_build(nodes=[], manual=[call]))["agreement"]["status"] == "warn"
    assert "2 independent readings" in _by_id(_build(manual=[fresh]))["agreement"]["detail"]


def test_with_no_screen_data_there_is_nothing_to_compare():
    assert _by_id(_build(scada_latest=None))["agreement"]["status"] == "warn"


# --------------------------------------------------------------------------------- sensor health
def test_no_node_means_not_set_up_and_is_left_out_of_the_count():
    r = _build(nodes=[])
    assert _by_id(r)["sensor"]["status"] == "na"
    assert (r["passed"], r["total"]) == (2, 3) and r["text"] == "2 of 3 checks pass (1 not set up)"


def test_a_silent_node_fails_and_says_for_how_long():
    c = _by_id(_build(nodes=[_node(silent=True, last_seen_age_s=185.0)]))["sensor"]
    assert c["status"] == "fail" and "has gone quiet (last heard 3 min ago)" in c["detail"]
    assert "has not reported yet" in _by_id(_build(nodes=[_node(silent=True, last_seen_age_s=None, state=None)]))["sensor"]["detail"]


def test_a_node_that_is_unsure_or_split_between_its_senses_warns():
    split = _by_id(_build(nodes=[_node(state="UNKNOWN", acc_state="RUNNING", mic_state="STOPPED")]))["sensor"]
    assert split["status"] == "warn" and "vibration and sound disagree" in split["detail"]
    assert _by_id(_build(nodes=[_node(state="UNKNOWN", acc_state="UNKNOWN", mic_state="UNKNOWN")]))["sensor"]["status"] == "warn"
    unsure = _by_id(_build(nodes=[_node(confidence=0.4)]))["sensor"]
    assert unsure["status"] == "warn" and "40% confident" in unsure["detail"]
    assert _by_id(_build(nodes=[_node(state=None, confidence=None)]))["sensor"]["status"] == "warn"


def test_backup_power_is_a_note_not_an_alarm_and_is_said_once():
    one = _by_id(_build(nodes=[_node(mains_ok=False)]))["sensor"]
    assert one["status"] == "warn" and one["detail"] == "Node NA is on backup power."
    two = _by_id(_build(nodes=[_node(mains_ok=False), _node(node_id="NB", mains_ok=False)]))["sensor"]
    assert two["detail"] == "Nodes NA, NB are on backup power."


def test_two_healthy_nodes_are_one_sentence():
    assert _by_id(_build(nodes=[_node(), _node(node_id="NB")]))["sensor"]["detail"] == "All 2 Witness Nodes are reporting clearly."


# -------------------------------------------------------------------------- inside the real core
def test_the_real_core_reports_the_checks_in_its_view_and_they_follow_an_attack():
    r = DrillRunner("frozen_screen", seed=1)
    v = r.last_view
    assert v["evidence_checks"]["text"] == "4 of 4 checks pass" and v["trust"]["state"] == "green"
    v = r.advance(300)                               # the scripted freeze starts right after the warm-up
    assert v["trust"]["state"] != "green"
    assert _by_id(v["evidence_checks"])["agreement"]["status"] in ("fail", "warn")
    assert v["evidence_checks"]["passed"] < 4


def test_a_tampered_log_shows_up_as_a_failed_integrity_check():
    log = EvidenceLog(recheck_s=0)          # check the whole chain on every call
    core = HydroWitnessCore(demo_profile(), DEMO_SECRETS, log=log, clock=lambda: NOW)
    for i in range(4):
        log.append("test", "note", {"i": i}, NOW + i)
    assert _by_id(core.evaluate(NOW)["evidence_checks"])["integrity"]["status"] == "pass"
    log.records[1]["content"] = {"i": 99}
    c = _by_id(core.evaluate(NOW)["evidence_checks"])["integrity"]
    assert c["status"] == "fail" and "record edited" in c["detail"]


# -------------------------------------------------------------------------- the cheap log check
def test_cached_verification_gives_the_same_answer_as_the_full_one():
    log = EvidenceLog()
    assert log.verify_cached() == log.verify() == (True, None, "log is empty")
    for i in range(6):
        log.append("t", "x", {"i": i}, float(i))
        assert log.verify_cached() == log.verify()
    assert log.verify_cached() == (True, None, "chain intact")


def test_cached_verification_checks_new_records_without_rereading_old_ones(monkeypatch):
    from hydrowitness import evidence_log as L
    log = EvidenceLog()
    for i in range(20):
        log.append("t", "x", {"i": i}, float(i))
    log.verify_cached()
    seen = []
    real = L.record_hash
    monkeypatch.setattr(L, "record_hash", lambda rec: (seen.append(rec["seq"]), real(rec))[1])
    log.append("t", "x", {"i": 20}, 20.0)          # append hashes the new record once itself
    seen.clear()
    assert log.verify_cached()[0] is True
    assert seen == [20]                              # only the record added since the last check was re-hashed
    seen.clear()
    log.verify_cached()
    assert seen == []                                # nothing new: nothing re-hashed


def test_cached_verification_still_finds_an_old_edit_at_the_next_full_check():
    log = EvidenceLog(recheck_s=3600)
    for i in range(5):
        log.append("t", "x", {"i": i}, float(i))
    assert log.verify_cached()[0] is True
    log.records[2]["content"] = {"i": 99}
    assert log.verify_cached()[0] is True            # between full checks, an old record is not re-read
    assert log.verify()[0] is False                  # the full check (the button, the command line) always finds it
    log.recheck_s = 0
    ok, bad, msg = log.verify_cached()
    assert (ok, bad) == (False, 2) and "record edited" in msg


def test_a_broken_chain_stays_broken_when_more_records_are_added():
    log = EvidenceLog(recheck_s=3600)
    for i in range(4):
        log.append("t", "x", {"i": i}, float(i))
    log.verify_cached()
    log.append("t", "x", {"i": 4}, 4.0)
    log.records[4]["content"] = {"i": 99}            # edit a record that has not been checked yet
    ok, bad, msg = log.verify_cached()
    assert (ok, bad) == (False, 4) and "record edited" in msg
    log.append("t", "x", {"i": 5}, 5.0)
    assert log.verify_cached()[:2] == (False, 4)      # more records do not repair it


def test_a_shorter_log_than_before_is_checked_again_from_the_start():
    log = EvidenceLog(recheck_s=3600)
    for i in range(5):
        log.append("t", "x", {"i": i}, float(i))
    log.verify_cached()
    del log.records[3:]
    assert log.verify_cached() == log.verify() == (True, None, "chain intact")


# ------------------------------------------------------------------------- the page and the demo
def test_the_dashboard_has_the_panel_and_copes_with_a_server_that_lacks_the_field():
    page = (ROOT / "hydrowitness" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="checks"' in page and "renderChecks(s)" in page
    assert "if (!ec || !Array.isArray(ec.checks)) { el.hidden = true; return; }" in page
    for word in ("Pass", "Check", "Fail", "Not set up"):
        assert f'"{word}"' in page                    # the status is words as well as a colour and a shape


def test_the_demo_recordings_carry_the_checks():
    idx = json.loads((ROOT / "site" / "demo" / "index.json").read_text())
    ec = idx["live"]["evidence_checks"]
    assert [c["id"] for c in ec["checks"]] == ["integrity", "freshness", "agreement", "sensor"]
    app = (ROOT / "site" / "demo" / "app.html").read_text(encoding="utf-8")
    assert 'id="checks"' in app
