"""A reduced, quick run of the V2 report so the script itself is tested. The full run is
`python3 -m hydrowitness.sim.validation_report` and its saved output is in results/."""
from hydrowitness.sim import validation_report as R


def test_report_covers_every_scenario_with_and_without_an_operator():
    d = R.drills(seeds=1, after_attack_s=300.0)
    assert len(d) == 12
    # the screen-lies scenarios reach red on their own; the careful forgery and the hidden valve do not (honest limits)
    assert d["frozen_screen|no operator"]["reached_red"] == 1
    assert d["colorado_style|no operator"]["reached_red"] == 1
    assert d["forced_off_spoofed|no operator"]["reached_red"] == 0 and d["forced_off_spoofed|no operator"]["reached_amber"] == 1
    assert d["hidden_valve_closure|no operator"]["reached_red"] == 0
    assert all(v["false_alarms_before_attack"] == 0 for v in d.values())


def test_markdown_states_the_limits_of_what_was_simulated():
    rep = {"drills": R.drills(seeds=1, after_attack_s=200.0), "normal_operation": {"site_days": 1, "false_amber": 0, "false_red": 0,
           "pump_state_changes": 2, "node_state_reports": 10}, "rule_false_episodes": {"site_days": 1, "false_rule_episodes": {}, "total": 0}}
    md = R.markdown(rep)
    assert "simulated plant, simulated node" in md and "| Frozen screen" in md
