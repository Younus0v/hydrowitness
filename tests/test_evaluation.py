"""The evaluation generator (hydrowitness/sim/evaluation.py): the alarm-panel baseline does what it says, the scoring is
right, the same seeds give the same numbers, and the committed results/evaluation.* are what the generator produces."""
import json
from pathlib import Path

import pytest

from hydrowitness.demo import demo_profile
from hydrowitness.schemas import ScadaSnapshot
from hydrowitness.sim import evaluation as E

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
V2 = json.loads((RESULTS / "v2_drills.json").read_text())["drills"]


def snap(t, level=22.0, psi=26.0, demand=130.0):
    return ScadaSnapshot(t=t, tank_level_ft=level, pump_status={"P1": False, "P2": True}, pump_flow_gpm={"P1": 0.0, "P2": 180.0},
                         demand_gpm=demand, pressure_psi=psi, commands=[])


# ---- the alarm panel ---------------------------------------------------------------------------------------------------
def test_panel_alarms_on_a_low_tank_and_a_low_pressure_once_each():
    p = E.AlarmPanel(demo_profile(), flat_line=False)
    for t in range(0, 100, 10):
        p.step(float(t), snap(t, level=22.0 + t * 0.001, psi=26.0 + t * 0.001))
    assert p.events == []
    for t in range(100, 200, 10):                                    # the tank drops below 15 ft and stays there
        p.step(float(t), snap(t, level=14.0 - t * 0.001, psi=26.0 + t * 0.001))
    assert [k for _, k in p.events] == ["low_level"]                 # a rising edge, not one alarm per second
    p.step(300.0, snap(300, level=14.0, psi=19.0))
    assert [k for _, k in p.events] == ["low_level", "low_pressure"]


def test_panel_raises_no_data_after_the_stale_time_and_not_before():
    prof = demo_profile()
    p = E.AlarmPanel(prof, flat_line=False)
    p.step(0.0, snap(0, level=22.01))
    p.step(prof.stale_after_s - 1, None)
    assert p.events == []
    p.step(prof.stale_after_s + 1, None)
    assert [k for _, k in p.events] == ["no_data"]


def test_flat_line_check_exists_only_in_the_generous_variant_and_ignores_normal_noise():
    plain, flat = E.AlarmPanel(demo_profile(), flat_line=False), E.AlarmPanel(demo_profile(), flat_line=True)
    for i, t in enumerate(range(0, 900, 5)):                         # frozen: identical values, new timestamps
        for pn in (plain, flat):
            pn.step(float(t), snap(t))
    assert plain.events == [] and [k for _, k in flat.events] == ["flat_line"]
    assert flat.events[0][0] == E.FLAT_LINE_S
    noisy = E.AlarmPanel(demo_profile(), flat_line=True)
    for i, t in enumerate(range(0, 900, 5)):
        noisy.step(float(t), snap(t, level=22.0 + (i % 7) * 0.01))
    assert noisy.events == []


def test_the_panel_believes_a_forged_screen():
    p = E.AlarmPanel(demo_profile(), flat_line=True)
    for i, t in enumerate(range(0, 3600, 5)):                        # a careful forgery: noisy, plausible, wrong
        p.step(float(t), snap(t, level=22.0 + (i % 5) * 0.02, psi=26.0 + (i % 3) * 0.1, demand=130.0 + (i % 4)))
    assert p.events == [] and p.first_after(0.0) is None


# ---- scoring a countdown checkpoint ----------------------------------------------------------------------------------
def row(**kw):
    base = {"after_s": 300, "ok": True, "credited_gpm": 0.0, "worst_min": 600.0, "expected_min": 1500.0, "truth_min": 1500.0}
    base.update(kw)
    return base


def test_unavailable_not_modelled_and_credited_checkpoints_are_not_scored():
    assert E.score_checkpoint(row(ok=False))["kind"] == "unavailable"
    assert E.score_checkpoint(row(not_modelled=True, truth_min=None))["kind"] == "not_modelled"
    assert E.score_checkpoint(row(credited_gpm=120.0))["kind"] == "skipped"
    assert E.score_checkpoint(row())["kind"] == "scored"


def test_early_close_and_late_use_the_larger_of_fifteen_minutes_and_ten_percent():
    assert E.score_checkpoint(row(expected_min=1500.0 - 100))["class"] == "close"          # 6.7% of 1500
    assert E.score_checkpoint(row(expected_min=1500.0 - 200))["class"] == "early"          # 13%
    assert E.score_checkpoint(row(expected_min=1500.0 + 200))["class"] == "late"
    assert E.score_checkpoint(row(expected_min=10.0, truth_min=20.0))["class"] == "close"  # within 15 minutes
    assert E.score_checkpoint(row(expected_min=1500.0 + 200))["error_min"] == 200.0


def test_the_worst_case_must_not_be_later_than_the_truth():
    assert E.score_checkpoint(row(worst_min=600.0, truth_min=1500.0))["bound_held"] is True
    assert E.score_checkpoint(row(worst_min=1600.0, truth_min=1500.0))["bound_held"] is False
    assert E.score_checkpoint(row(worst_min=1503.0, truth_min=1500.0))["bound_held"] is True      # inside the 5 minute step of the truth


def test_no_trouble_coming_is_capped_the_same_way_on_both_sides():
    s = E.score_checkpoint(row(worst_min=None, expected_min=None, truth_min=None))
    assert s["bound_held"] is True and s["class"] == "close" and s["error_min"] == 0.0
    late = E.score_checkpoint(row(expected_min=None, truth_min=600.0))      # HydroWitness says "not falling", the plant is falling
    assert late["class"] == "late"


# ---- one run, and the same seeds give the same numbers --------------------------------------------------------------
SMALL = dict(seeds=(1,), horizon_s=600.0, check_every_s=300.0, scenarios=["forced_off_spoofed"], normal_days=0.05, normal_seeds=1, failures=False)


def test_the_same_seeds_give_identical_results_and_the_worker_count_does_not_matter():
    a = E.run(**SMALL, jobs=1)
    b = E.run(**SMALL, jobs=1)
    c = E.run(**SMALL, jobs=2)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True) == json.dumps(c, sort_keys=True)
    assert E.markdown(a) == E.markdown(b)


def test_a_different_seed_is_actually_a_different_run():
    a = E.run_attack("forced_off_spoofed", 1, False, horizon_s=300.0)
    b = E.run_attack("forced_off_spoofed", 2, False, horizon_s=300.0)
    assert a["checks"] != b["checks"]


@pytest.mark.parametrize("sid", ["frozen_screen", "forced_off_spoofed", "colorado_style", "replay", "power_loss_lookalike"])
def test_hydrowitness_times_here_are_the_saved_v2_drill_times(sid):
    """The evaluation drives the same drills, so its detection times are the ones saved in results/v2_drills.json."""
    r = E.run_attack(sid, 1, False, horizon_s=900.0, check_every_s=900.0)
    saved = V2[f"{sid}|no operator"]
    assert r["hw_amber_s"] == saved["amber_median_s"] and r["hw_red_s"] == saved["red_median_s"]


def test_a_run_records_what_the_panel_and_hydrowitness_did_and_nothing_about_the_truth_it_was_not_given():
    r = E.run_attack("frozen_screen", 1, False, horizon_s=600.0, check_every_s=300.0)
    assert r["hw_warn_s"] == 65.0 and r["hw_red_s"] == 95.0
    assert r["alarm_s"] is None                                       # the screen still updates its timestamp: a plain panel sees nothing
    assert r["alarm_flatline_s"] == E.FLAT_LINE_S and r["alarm_flatline_kind"] == "flat_line"
    assert r["hw_false_before_attack"] == 0 and r["alarm_false_before_attack"] == 0
    assert [c["after_s"] for c in r["checks"]] == [300.0, 600.0]


def test_a_jammed_witness_is_recorded_as_part_of_the_run():
    r = E.run_attack("forced_off_spoofed", 1, False, jam=("NB",), horizon_s=300.0, check_every_s=300.0)
    assert r["jam"] == ["NB"]


def test_a_closed_valve_is_not_scored_against_a_pressure_the_simulation_never_worked_out():
    r = E.run_attack("hidden_valve_closure", 1, False, horizon_s=600.0, check_every_s=300.0)
    assert all(c.get("not_modelled") and "truth_min" not in c for c in r["checks"])
    k = E.summarize_countdown([r])
    assert k["scored"] == 0 and k["not_modelled_valve"] == 2


def test_summaries_are_computed_from_the_runs_they_come_from():
    runs = [{"hw_warn_s": 20.0, "hw_red_s": None, "alarm_s": None, "alarm_flatline_s": 300.0, "alarm_kind": None, "alarm_flatline_kind": "flat_line",
             "hw_false_before_attack": 0, "alarm_false_before_attack": 0, "alarm_flatline_false_before_attack": 0, "checks": []},
            {"hw_warn_s": 40.0, "hw_red_s": 90.0, "alarm_s": 125.0, "alarm_flatline_s": 125.0, "alarm_kind": "no_data", "alarm_flatline_kind": "no_data",
             "hw_false_before_attack": 0, "alarm_false_before_attack": 0, "alarm_flatline_false_before_attack": 0, "checks": []},
            {"hw_warn_s": None, "hw_red_s": None, "alarm_s": None, "alarm_flatline_s": None, "alarm_kind": None, "alarm_flatline_kind": None,
             "hw_false_before_attack": 0, "alarm_false_before_attack": 0, "alarm_flatline_false_before_attack": 0, "checks": []}]
    d = E.summarize_detection(runs)
    assert d["hydrowitness_warned"] == {"detected": 2, "missed": 1, "runs": 3, "median_s": 30.0, "max_s": 40.0}
    assert d["hydrowitness_red"]["detected"] == 1 and d["alarm"]["detected"] == 1 and d["alarm_flatline"]["detected"] == 2
    assert d["alarm"]["kinds"] == ["no_data"] and d["alarm_flatline"]["kinds"] == ["flat_line", "no_data"]


# ---- the committed results ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def committed():
    return json.loads((RESULTS / "evaluation.json").read_text())


def test_the_markdown_report_is_the_json_report_rendered(committed):
    assert (RESULTS / "evaluation.md").read_text() == E.markdown(committed)


def test_committed_config_writes_down_every_setting_and_the_limits(committed):
    c = committed["config"]
    assert c["seeds"] == list(range(1, 11)) and c["attack_horizon_s"] == E.HORIZON_S
    assert c["alarm_panel"]["low_level_ft"] == E.LOW_LEVEL_ALARM_FT and c["alarm_panel"]["flat_line_after_s"] == E.FLAT_LINE_S
    assert set(c["witness_node_error_model"]) >= {"p_acc_err", "p_mic_err", "hb_p_disagree"}
    assert any("booted on a real ESP32 board" in x and "no real measurements" in x for x in c["limits"]) and any("assumptions" in x for x in c["limits"])
    md = (RESULTS / "evaluation.md").read_text()
    assert "uses no real measurements" in md and "simulated" in md.lower()
    assert "never run on a real" not in md and "never been run" not in md


def test_committed_summary_counts_add_up_from_its_own_runs(committed):
    runs = committed["runs"]["attacks"]
    s = committed["summary"]
    assert s["totals"]["attack_runs"] == len(runs) == 6 * 2 * 2 * len(committed["config"]["seeds"])
    for key, a in s["attacks"].items():
        group = [r for r in runs if r["scenario"] == a["scenario"] and r["operator"] is a["operator"]]
        assert a["detection"] == E.summarize_detection(group) and a["countdown"] == E.summarize_countdown(group), key
    assert s["totals"]["countdown"] == E.summarize_countdown(runs)


@pytest.mark.parametrize("pick", [("forced_off_spoofed", 1, False, "demo"), ("hidden_valve_closure", 2, True, "small"), ("replay", 3, False, "demo")])
def test_committed_runs_reproduce_exactly(committed, pick):
    """Re-run a few of the saved runs from their seeds and compare every field."""
    sid, seed, op, plant = pick
    c = committed["config"]
    saved = next(r for r in committed["runs"]["attacks"] if (r["scenario"], r["seed"], r["operator"], r["plant"]) == pick)
    again = E.run_attack(sid, seed, op, horizon_s=c["attack_horizon_s"], check_every_s=c["countdown_checkpoint_every_s"], small_tank=plant == "small")
    assert json.dumps(again, sort_keys=True) == json.dumps(saved, sort_keys=True)


def test_committed_failure_conditions_were_actually_run(committed):
    f = committed["summary"]["failure_conditions"]
    assert set(f) == {x["id"] for x in E.FAILURE_CONDITIONS}
    assert all(v["detection"]["runs"] == len(committed["config"]["seeds"]) for v in f.values())
