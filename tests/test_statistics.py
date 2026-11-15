"""The statistics helpers, the committed statistics and sensitivity reports, and the setup allowance."""
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from hydrowitness import stats
from hydrowitness.demo import demo_profile
from hydrowitness.engine.time_to_trouble import LevelReading, compute_clock
from hydrowitness.schemas import GAL_PER_FT3

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------- the interval maths
def test_clopper_pearson_matches_textbook_values():
    assert stats.clopper_pearson(0, 10)[1] == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-9)        # 0.3085
    assert stats.clopper_pearson(0, 200)[1] == pytest.approx(0.018275, abs=1e-5)
    lo, hi = stats.clopper_pearson(5, 10)
    assert (lo, hi) == (pytest.approx(0.187086, abs=1e-5), pytest.approx(0.812914, abs=1e-5))
    lo, hi = stats.clopper_pearson(20, 20)
    assert hi == 1.0 and lo == pytest.approx(0.025 ** (1 / 20), abs=1e-9)                              # 0.8316
    assert stats.clopper_pearson(0, 0) == (0.0, 1.0)


def test_the_exact_interval_really_covers_at_least_95_percent():
    """The guarantee that makes Clopper-Pearson 'exact': whatever the true rate, the interval contains it in at least 95% of repeated samples."""
    for n, p in ((10, 0.05), (30, 0.1), (60, 0.5), (200, 0.01), (200, 0.4)):
        cover = 0.0
        for k in range(n + 1):
            lo, hi = stats.clopper_pearson(k, n)
            if lo <= p <= hi:
                cover += math.exp(stats._log_pmf(k, n, p))
        assert cover >= 0.95 - 1e-9, (n, p, cover)


def test_the_interval_always_contains_the_observed_rate():
    for n in (1, 7, 60):
        for k in range(n + 1):
            lo, hi = stats.clopper_pearson(k, n)
            assert 0 <= lo <= k / n <= hi <= 1


def test_poisson_upper_bound():
    assert stats.poisson_upper(0, 24) == pytest.approx(-math.log(0.025) / 24, rel=1e-9)               # 3.689 / 24
    assert stats.poisson_upper(5, 10) == pytest.approx(1.1668, abs=1e-3)                               # exact upper limit for 5 events is 11.668
    assert stats.poisson_upper(0, 0) == math.inf


def test_the_wording_helper():
    assert stats.show(0, 200) == "0 of 200 (0.0%, 95% CI 0.0% to 1.8%)"


# ---------------------------------------------------------------------- the committed reports are current
def _run(script: str, *args: str):
    return subprocess.run([sys.executable, str(ROOT / "tools" / script), *args], capture_output=True, text=True, cwd=ROOT)


def test_the_statistics_report_is_up_to_date_with_the_results():
    r = _run("make_statistics.py", "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_sensitivity_report_is_up_to_date():
    r = _run("sensitivity.py", "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_statistics_state_the_limits_of_what_they_show():
    text = (ROOT / "results" / "statistics.md").read_text(encoding="utf-8")
    assert "sampling luck inside the simulation only" in text and "Nothing here is a field result" in text
    s = json.loads((ROOT / "results" / "statistics.json").read_text(encoding="utf-8"))
    ins = s["v1_clock_vs_epanet"]["inside_assumptions"]["unsafe"]
    assert ins["n"] == 200 and ins["hi"] > 0.01                      # "0 of 200" must come with its upper bound, never alone
    assert s["false_alarms"]["upper_95_per_site_day"]["amber"] > 0


def test_the_sensitivity_report_says_its_assumptions_are_assumptions():
    text = (ROOT / "results" / "sensitivity.md").read_text(encoding="utf-8")
    assert "our assumptions, not measurements" in text and "Nothing here is a field result" in text


# ------------------------------------------------------------------------------- the setup allowance
NOW = 1_800_000_000.0


def test_the_setup_allowance_defaults_to_nothing_and_shortens_the_worst_case_by_the_right_amount():
    p0 = demo_profile()
    assert p0.setup_uncertainty_ft == 0.0
    p2 = p0.model_copy(update={"setup_uncertainty_ft": 2.0})
    lvl = LevelReading(20.0, NOW - 60, "scada")
    r0, r2 = compute_clock(p0, NOW, lvl), compute_clock(p2, NOW, lvl)
    gpf = p0.tank_area_sqft * GAL_PER_FT3
    assert r0.worst_min - r2.worst_min == pytest.approx(2.0 * gpf / p0.peak_demand_gpm, rel=1e-9)
    assert r2.expected_min == pytest.approx(r0.expected_min)               # only the worst case carries the allowance
    assert any("Setup allowance" in line for line in r2.assumption_lines) and not any("Setup allowance" in line for line in r0.assumption_lines)


def test_the_setup_allowance_is_validated():
    from hydrowitness.schemas import SiteProfile
    with pytest.raises(ValueError):
        SiteProfile(**{**demo_profile().model_dump(), "setup_uncertainty_ft": -1.0})
    with pytest.raises(ValueError):
        SiteProfile(**{**demo_profile().model_dump(), "setup_uncertainty_ft": 50.0})


def test_an_absurd_or_missing_number_is_not_a_reading():
    p = demo_profile()
    for bad in (float("nan"), float("inf"), -float("inf")):
        r = compute_clock(p, NOW, LevelReading(bad, NOW, "scada"))
        assert not r.ok and r.needs_operator_reading
    capped = compute_clock(p, NOW, LevelReading(1e9, NOW, "scada"))
    full = compute_clock(p, NOW, LevelReading(p.tank_height_ft, NOW, "scada"))
    assert capped.ok and capped.worst_min == pytest.approx(full.worst_min)    # a tank cannot hold more than its height
