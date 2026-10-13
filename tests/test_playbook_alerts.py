from hydrowitness.alerts import AlertManager, ListSink
from hydrowitness.playbook import DISCLAIMER, PHASES, Playbook

T = 1_800_000_000.0


def step_map(status):
    return {s["id"]: s for ph in status["phases"] for s in ph["steps"]}


# --------------------------------------------------------------------- playbook
def test_inactive_playbook_has_no_phases_but_still_carries_the_disclaimer():
    st = Playbook().status(T, None)
    assert st["active"] is False and st["phases"] == [] and st["disclaimer"] == DISCLAIMER
    assert "no regulatory advice" in DISCLAIMER


def test_start_is_idempotent_and_records_the_clock():
    pb = Playbook()
    assert pb.start(T, 600.0) is True
    assert pb.start(T + 10, 100.0) is False
    assert pb.start_worst_min == 600.0 and pb.started_t == T


def test_phases_come_in_the_documented_order():
    pb = Playbook()
    pb.start(T, 600.0)
    assert [p["name"] for p in pb.status(T, 600.0)["phases"]] == PHASES == ["Confirm", "Contain", "Prepare", "Recover"]


def test_confirm_steps_become_overdue_after_five_minutes():
    pb = Playbook()
    pb.start(T, 600.0)
    s = step_map(pb.status(T + 60, 600.0))
    assert s["c1"]["status"] == "due" and s["c1"]["target"] == "within 5 min"
    s = step_map(pb.status(T + 6 * 60, 590.0))
    assert s["c1"]["status"] == "overdue"


def test_contain_targets_are_a_share_of_the_starting_clock():
    pb = Playbook()
    pb.start(T, 600.0)
    s = step_map(pb.status(T + 30 * 60, 570.0))
    assert s["k1"]["target"] == "within 60 min" and s["k1"]["status"] == "due"
    assert step_map(pb.status(T + 61 * 60, 540.0))["k1"]["status"] == "overdue"


def test_prepare_steps_unlock_as_the_clock_runs_down():
    pb = Playbook()
    pb.start(T, 600.0)
    assert step_map(pb.status(T, 600.0))["p1"]["status"] == "upcoming"
    assert step_map(pb.status(T, 290.0))["p1"]["status"] == "due"                    # 48% left
    assert step_map(pb.status(T, 290.0))["p3"]["status"] == "upcoming"               # needs 25% left
    assert step_map(pb.status(T, 140.0))["p3"]["status"] == "due"


def test_recover_waits_until_contain_is_done():
    pb = Playbook()
    pb.start(T, 600.0)
    assert step_map(pb.status(T, 600.0))["r1"]["status"] == "upcoming"
    pb.complete("k1", "Sam", T + 1)
    pb.complete("k2", "Sam", T + 2)
    assert step_map(pb.status(T + 3, 600.0))["r1"]["status"] == "due"


def test_completing_a_step_records_who_and_when_and_only_once():
    pb = Playbook()
    pb.start(T, 600.0)
    done = pb.complete("c1", "Sam", T + 42)
    assert done.done_by == "Sam" and done.done_t == T + 42
    assert pb.complete("c1", "Lee", T + 99) is None            # already done
    assert pb.complete("nope", "Sam", T) is None
    s = step_map(pb.status(T + 50, 600.0))
    assert s["c1"]["status"] == "done" and s["c1"]["done_by"] == "Sam"


def test_current_phase_advances_when_a_phase_is_finished():
    pb = Playbook()
    pb.start(T, 600.0)
    assert pb.status(T, 600.0)["current_phase"] == "Confirm"
    for sid in ("c1", "c2", "c3"):
        pb.complete(sid, "Sam", T + 10)
    assert pb.status(T + 20, 600.0)["current_phase"] == "Contain"


def test_a_missing_start_clock_falls_back_to_eight_hours():
    pb = Playbook()
    pb.start(T, None)
    assert pb.start_worst_min == 480.0


# ----------------------------------------------------------------------- alerts
def test_alert_on_escalation_of_state_only():
    sink = ListSink()
    am = AlertManager([sink])
    am.on_state(T, "green", "amber", "Demo", "headline", ["boss", "backup"])
    am.on_state(T + 5, "amber", "amber", "Demo", "headline", ["boss", "backup"])
    am.on_state(T + 10, "amber", "green", "Demo", "headline", ["boss", "backup"])
    assert len(sink.sent) == 1 and sink.sent[0]["level"] == "amber" and sink.sent[0]["to"] == ["boss"]
    am.on_state(T + 20, "amber", "red", "Demo", "headline", ["boss", "backup"])
    assert [a["level"] for a in sink.sent] == ["amber", "red"]


def test_unacknowledged_alert_escalates_to_the_second_contact_after_five_minutes():
    sink = ListSink()
    am = AlertManager([sink])
    am.on_state(T, "green", "red", "Demo", "headline", ["boss", "backup"])
    am.tick(T + 299, True, "Demo", "headline", ["boss", "backup"])
    assert len(sink.sent) == 1
    am.tick(T + 301, True, "Demo", "headline", ["boss", "backup"])
    assert len(sink.sent) == 2 and sink.sent[1]["kind"] == "escalation" and sink.sent[1]["to"] == ["backup"]
    am.tick(T + 900, True, "Demo", "headline", ["boss", "backup"])
    assert len(sink.sent) == 2                              # escalates once


def test_acknowledging_stops_the_escalation():
    sink = ListSink()
    am = AlertManager([sink])
    am.on_state(T, "green", "red", "Demo", "headline", ["boss", "backup"])
    am.tick(T + 100, False, "Demo", "headline", ["boss", "backup"])          # acknowledged
    am.tick(T + 1000, False, "Demo", "headline", ["boss", "backup"])
    assert len(sink.sent) == 1


def test_a_sink_failure_is_not_possible_to_hide_in_history():
    am = AlertManager([])
    am.on_state(T, "green", "amber", "Demo", "headline", ["boss"])
    assert len(am.history) == 1
