import pytest

from hydrowitness.engine.time_to_trouble import (CreditedFlow, LevelReading, compute_clock, expected_l_min_ft,
                                                 format_minutes, l_min_ft, usable_gal)
from hydrowitness.schemas import GAL_PER_FT3, SiteProfile, ResponseCurve

NOW = 1_800_000_000.0  # exactly on an hour boundary, so typical-demand maths is easy


def flat_profile(profile: SiteProfile, typical=180.0) -> SiteProfile:
    p = profile.model_copy(deep=True)
    p.typical_demand_gpm_by_hour = [typical] * 24
    return p


def level_for_usable(p: SiteProfile, gallons: float) -> float:
    return l_min_ft(p) + gallons / (p.tank_area_sqft * GAL_PER_FT3)


def test_l_min_matches_documented_formula(profile):
    # 20 psi = 46.2 ft of head; plus critical elevation 40, plus 5 ft margin, minus tank floor 80
    assert l_min_ft(profile) == pytest.approx(40 + 46.2 + 5 - 80)


def test_worked_example_from_project_document(profile):
    p = flat_profile(profile)
    lvl = level_for_usable(p, 120_000)
    assert usable_gal(p, lvl) == pytest.approx(120_000)
    r = compute_clock(p, NOW, LevelReading(lvl, NOW, "operator"))
    # level uncertainty (0.5 ft) is subtracted from the worst case, so allow for it
    unc_gal = 0.5 * p.tank_area_sqft * GAL_PER_FT3
    assert r.worst_min == pytest.approx((120_000 - unc_gal) / 300, rel=1e-6)
    # Expected case: 180 gpm is 60% of the 300 gpm peak, so only 0.6^1.852 of the 5 ft headloss margin applies.
    # That frees about 3 ft of tank (about 70,000 gal here): validation V1 showed the full margin made the
    # expected case ~36% pessimistic at ordinary demand.
    gpf = p.tank_area_sqft * GAL_PER_FT3
    freed_ft = l_min_ft(p) - expected_l_min_ft(p, 180.0)
    assert freed_ft == pytest.approx(5.0 * (1 - 0.6 ** 1.852), rel=1e-9)
    assert r.expected_min == pytest.approx((120_000 + freed_ft * gpf) / 180, rel=1e-6)
    assert r.expected_min > 120_000 / 180


def test_confirmed_inflow_extends_the_clock(profile):
    p = flat_profile(profile)
    lvl = level_for_usable(p, 120_000)
    base = compute_clock(p, NOW, LevelReading(lvl, NOW, "operator"))
    credit = CreditedFlow("P2", 150.0, NOW, "confirmed by test")
    r = compute_clock(p, NOW, LevelReading(lvl, NOW, "operator"), [credit])
    unc_gal = 0.5 * p.tank_area_sqft * GAL_PER_FT3
    assert r.worst_min == pytest.approx((120_000 - unc_gal) / 150, rel=1e-6)    # net peak outflow 150 gpm
    assert r.worst_min > 1.9 * base.worst_min
    assert r.credited_gpm == 150 and r.credited_pumps == ["P2"]


def test_no_credit_means_pumps_assumed_off(profile):
    p = flat_profile(profile)
    r = compute_clock(p, NOW, LevelReading(level_for_usable(p, 50_000), NOW, "scada"))
    assert r.credited_gpm == 0 and "No inflow credited" in r.notes[0]


def test_inflow_exceeding_peak_demand_means_not_falling(profile):
    p = flat_profile(profile)
    r = compute_clock(p, NOW, LevelReading(level_for_usable(p, 50_000), NOW, "scada"),
                      [CreditedFlow("P1", 320.0, NOW, "x")])
    assert r.ok and r.worst_min is None and format_minutes(r.worst_min) == "stable"


def test_clock_counts_down_as_data_ages(profile):
    p = flat_profile(profile)
    lvl = LevelReading(level_for_usable(p, 120_000), NOW, "operator")
    fresh = compute_clock(p, NOW, lvl)
    later = compute_clock(p, NOW + 600, lvl)    # ten minutes with no new data
    assert later.worst_min < fresh.worst_min - 9.9       # at least ten minutes gone (plus more uncertainty)
    assert later.uncertainty_ft > fresh.uncertainty_ft   # the range widens with age
    assert later.expected_min < fresh.expected_min


def test_untrusted_data_is_more_pessimistic_and_asks_for_a_reading(profile):
    p = flat_profile(profile)
    v = level_for_usable(p, 120_000)
    trusted = compute_clock(p, NOW, LevelReading(v, NOW, "scada"))
    model_only = compute_clock(p, NOW, LevelReading(v, NOW, "scada_last_trusted"))
    assert model_only.worst_min < trusted.worst_min
    assert model_only.needs_operator_reading and not trusted.needs_operator_reading


def test_at_or_below_l_min_is_zero(profile):
    p = flat_profile(profile)
    r = compute_clock(p, NOW, LevelReading(l_min_ft(p) - 0.2, NOW, "operator"))
    # below the peak-demand L_min: at a demand spike the pressure is already short, even though ordinary demand still has time
    assert r.worst_min == 0.0 and r.expected_min > 0.0
    r = compute_clock(p, NOW, LevelReading(expected_l_min_ft(p, 180.0) - 0.2, NOW, "operator"))
    assert r.worst_min == 0.0 and r.expected_min == 0.0


def test_expected_case_follows_hourly_demand(profile):
    p = profile.model_copy(deep=True)
    p.typical_demand_gpm_by_hour = [100.0] * 12 + [400.0] * 12
    p.peak_demand_gpm = 400.0
    p.headloss_margin_ft = 0.0      # no friction term, so the arithmetic below is exact (see the scaling test)
    lvl = level_for_usable(p, 60_000)
    t0 = NOW - 8 * 3600     # NOW falls in hour 8 UTC, so t0 is exactly local hour 0
    # 100 gpm for 12 h would use 72,000 gal, so a 60,000 gal tank outlasts the quiet hours
    quiet = compute_clock(p, t0, LevelReading(lvl, t0, "operator"))
    assert quiet.expected_min == pytest.approx(600.0, rel=1e-6)   # 60,000 gal / 100 gpm
    busy = compute_clock(p, t0 + 12 * 3600, LevelReading(lvl, t0 + 12 * 3600, "operator"))
    assert busy.expected_min == pytest.approx(150.0, rel=1e-6)    # 60,000 gal / 400 gpm
    # Starting at hour 8 the quiet demand lasts 4 more hours (24,000 gal), then 36,000 gal at 400 gpm
    mid = compute_clock(p, NOW, LevelReading(lvl, NOW, "operator"))
    assert mid.expected_min == pytest.approx(240 + 90, rel=1e-6)


def test_range_never_inverts(profile):
    p = flat_profile(profile, typical=290.0)
    r = compute_clock(p, NOW, LevelReading(level_for_usable(p, 100_000), NOW, "operator"))
    assert r.expected_min >= r.worst_min


def test_profile_rejects_typical_demand_above_peak(profile):
    data = profile.model_dump()
    data["typical_demand_gpm_by_hour"] = [350.0] * 24
    with pytest.raises(ValueError, match="peak_demand_gpm"):
        SiteProfile(**data)


def test_no_level_asks_for_a_reading(profile):
    r = compute_clock(profile, NOW, None)
    assert not r.ok and r.needs_operator_reading


def test_direct_mode_uses_response_curves(profile):
    p = profile.model_copy(deep=True)
    p.mode = "direct"
    p.peak_demand_gpm = 300
    p.typical_demand_gpm_by_hour = [150.0] * 24
    p.response_curves = [
        ResponseCurve(demand_gpm=150, minutes=[0, 10, 20, 30], psi=[60, 40, 22, 10]),
        ResponseCurve(demand_gpm=300, minutes=[0, 5, 10, 15], psi=[60, 35, 18, 5]),
    ]
    r = compute_clock(p, NOW, None)
    assert r.ok and r.mode == "direct"
    assert 5 < r.worst_min < 10                                   # crosses 20 psi between 5 and 10 min
    assert r.expected_min == pytest.approx(21.7, abs=0.1)        # typical-demand curve, linear interpolation
    p.response_curves = []
    assert not compute_clock(p, NOW, None).ok


def test_headloss_margin_scales_with_demand_in_the_expected_case_only(profile):
    p = flat_profile(profile, typical=150.0)
    assert expected_l_min_ft(p, p.peak_demand_gpm) == pytest.approx(l_min_ft(p))          # at peak: the full margin
    assert expected_l_min_ft(p, 0.0) == pytest.approx(l_min_ft(p) - p.headloss_margin_ft)  # no flow, no friction
    assert expected_l_min_ft(p, 500.0) == pytest.approx(l_min_ft(p))                      # never more than the entered margin
    lvl = LevelReading(level_for_usable(p, 100_000), NOW, "operator")
    r = compute_clock(p, NOW, lvl)
    unc_gal = 0.5 * p.tank_area_sqft * GAL_PER_FT3
    assert r.worst_min == pytest.approx((100_000 - unc_gal) / 300, rel=1e-6)              # the worst case still uses the full margin


def test_inflow_is_credited_only_from_when_a_witness_confirmed_it(profile):
    p = flat_profile(profile)
    lvl = LevelReading(level_for_usable(p, 200_000), NOW, "scada_last_trusted")
    now = NOW + 3600                                    # an hour of darkness
    gpf = p.tank_area_sqft * GAL_PER_FT3
    unc = 0.5 + 0.02 * 60 + 1.5
    just_now = compute_clock(p, now, lvl, [CreditedFlow("P1", 200.0, now, "witness")])
    from_start = compute_clock(p, now, lvl, [CreditedFlow("P1", 200.0, NOW, "witness")])
    # confirmed just now: the whole dark hour ran at peak with no inflow (300 gpm x 3600 s)
    assert just_now.worst_min == pytest.approx((200_000 - 300 * 60 - unc * gpf) / 100, rel=1e-6)
    # confirmed at the start of the dark hour: the inflow counted for the whole hour
    assert from_start.worst_min == pytest.approx((200_000 - 100 * 60 - unc * gpf) / 100, rel=1e-6)
    assert just_now.worst_min < from_start.worst_min


def test_the_clock_states_the_demand_it_assumes(profile):
    r = compute_clock(profile, NOW, LevelReading(l_min_ft(profile) + 10, NOW, "scada"))
    assert any("never exceeds the configured peak of 300 gpm" in n for n in r.notes)


def test_a_critical_point_far_below_the_tank_still_runs_out_when_the_tank_is_empty(profile):
    p = flat_profile(profile)
    p.critical_point_elev_ft = 0.0               # 80 ft below the tank floor: 20 psi needs only ~46 ft + margin
    assert l_min_ft(p) == 0.0                    # the pressure would hold to an empty tank, but an empty tank delivers nothing
    assert expected_l_min_ft(p, 180.0) == 0.0
    lvl = LevelReading(10.0, NOW, "operator")
    r = compute_clock(p, NOW, lvl)
    gpf = p.tank_area_sqft * GAL_PER_FT3
    assert r.worst_min == pytest.approx((10.0 - 0.5) * gpf / 300, rel=1e-6)
    assert r.expected_min == pytest.approx(10.0 * gpf / 180, rel=1e-6)
