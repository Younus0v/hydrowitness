"""A small, seeded run of validation V1 so the harness itself stays honest. The full 310-scenario run is
`python3 -m hydrowitness.sim.wntr_validation` and its saved output is in results/."""
import pytest

pytest.importorskip("wntr")

from hydrowitness.sim import wntr_validation as V  # noqa: E402


@pytest.fixture(scope="module")
def result():
    return V.run(n=62, seed=3, quiet=True)


def test_every_family_ran_and_scenarios_are_recorded(result):
    assert set(result["summary"]) == set(V.FAMILIES)
    assert result["n"] == len(result["scenarios"]) >= 6


def test_in_normal_conditions_the_worst_case_is_never_later_than_hydraulics(result):
    for fam in ("model_check", "nominal_off", "nominal_pump"):
        s = result["summary"][fam]
        assert s["unsafe_count"] == 0, (fam, s)


def test_with_nothing_random_the_clock_matches_epanet(result):
    s = result["summary"]["model_check"]
    assert s["expected_median_abs_err"] is not None and s["expected_median_abs_err"] < 0.05


def test_the_stress_cases_are_reported_not_hidden(result):
    # beyond the configured peak the clock CAN be late: the harness must be able to see that, not average it away
    s = result["summary"]["stress_demand"]
    assert s["unsafe_count"] >= 1 and s["max_late_by_min"] > V.LATE_TOL_MIN


def test_markdown_report_has_one_row_per_family(result):
    md = V.markdown(result["summary"], result["n"], 3)
    for fam in V.FAMILIES:
        assert f"| {fam} |" in md
