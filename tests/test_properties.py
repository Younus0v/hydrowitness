"""Property-based tests: rules that must hold for ANY input, not just the examples we thought of.

Ordinary tests check chosen cases. These let Hypothesis search for the case that breaks a rule, shrink it to the smallest example,
and report it. They cover three things an operator relies on without thinking about it:

  * the countdown behaves sensibly: more water never means less time, a higher peak demand never means more time,
    an older reading never makes the worst case longer, and no input produces a negative, NaN or inverted estimate;
  * the trust light never says "green" when its information has gone quiet;
  * the web interface never crashes (no 500 error) on garbage, whatever is posted.
"""
import math
import os

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("hypothesis", reason="pip install hypothesis to run the property-based tests (requirements.txt lists it)")
from hypothesis import HealthCheck, given, settings, strategies as st

from hydrowitness.api import create_app
from hydrowitness.demo import DEMO_SECRETS, demo_profile
from hydrowitness.engine.time_to_trouble import CreditedFlow, LevelReading, clock_text, compute_clock, format_minutes
from hydrowitness.schemas import ScadaSnapshot
from hydrowitness.service import HydroWitnessCore

NOW = 1_800_000_000.0
N = int(os.environ.get("HW_FUZZ_EXAMPLES", "150"))      # raise it for a deeper search: HW_FUZZ_EXAMPLES=3000 pytest tests/test_properties.py
SET = settings(max_examples=N, deadline=None, suppress_health_check=[HealthCheck.too_slow])
LEVELS = st.floats(min_value=0.0, max_value=30.0, allow_nan=False)


def worst_of(profile, level, age=300.0, credits=None):
    r = compute_clock(profile, NOW, LevelReading(level, NOW - age, "scada"), credits)
    return r, (math.inf if r.worst_min is None else r.worst_min)


# ------------------------------------------------------------------------------ the countdown
@SET
@given(a=LEVELS, b=LEVELS)
def test_more_water_never_means_less_time(a, b):
    lo, hi = sorted((a, b))
    p = demo_profile()
    assert worst_of(p, lo)[1] <= worst_of(p, hi)[1] + 1e-9


@SET
@given(peak_a=st.floats(min_value=230, max_value=2000), peak_b=st.floats(min_value=230, max_value=2000), level=LEVELS)
def test_a_higher_peak_demand_never_gives_more_time(peak_a, peak_b, level):
    lo, hi = sorted((peak_a, peak_b))
    p_lo, p_hi = demo_profile().model_copy(update={"peak_demand_gpm": lo}), demo_profile().model_copy(update={"peak_demand_gpm": hi})
    assert worst_of(p_hi, level)[1] <= worst_of(p_lo, level)[1] + 1e-9


@SET
@given(age_a=st.floats(min_value=0, max_value=7 * 86400), age_b=st.floats(min_value=0, max_value=7 * 86400), level=LEVELS)
def test_an_older_reading_never_lengthens_the_worst_case(age_a, age_b, level):
    young, old = sorted((age_a, age_b))
    p = demo_profile()
    assert worst_of(p, level, old)[1] <= worst_of(p, level, young)[1] + 1e-9


@SET
@given(allow_a=st.floats(min_value=0, max_value=20), allow_b=st.floats(min_value=0, max_value=20), level=LEVELS)
def test_a_bigger_setup_allowance_never_lengthens_the_worst_case(allow_a, allow_b, level):
    lo, hi = sorted((allow_a, allow_b))
    p_lo, p_hi = demo_profile().model_copy(update={"setup_uncertainty_ft": lo}), demo_profile().model_copy(update={"setup_uncertainty_ft": hi})
    assert worst_of(p_hi, level)[1] <= worst_of(p_lo, level)[1] + 1e-9


@SET
@given(level=LEVELS, age=st.floats(min_value=0, max_value=86400), hour=st.integers(0, 23),
       credit=st.one_of(st.none(), st.floats(min_value=0, max_value=1500)), source=st.sampled_from(["scada", "operator", "scada_last_trusted", "scada_untrusted"]))
def test_the_estimate_is_always_sane(level, age, hour, credit, source):
    now = NOW + hour * 3600.0
    credits = None if credit is None else [CreditedFlow("P1", credit, now - age, "witness")]
    r = compute_clock(demo_profile(), now, LevelReading(level, now - age, source), credits)
    assert r.ok
    for m in (r.worst_min, r.expected_min):
        assert m is None or (math.isfinite(m) and m >= 0.0)
    if r.worst_min is not None and r.expected_min is not None:
        assert r.expected_min + 1e-9 >= r.worst_min                      # the range never inverts
    assert r.state in ("empty", "falling", "not_falling")
    assert clock_text(r, "worst") and clock_text(r, "expected")
    assert r.uncertainty_ft >= 0.0


@SET
@given(a=st.floats(min_value=0, max_value=1500), b=st.floats(min_value=0, max_value=1500), level=LEVELS)
def test_crediting_more_inflow_never_shortens_the_worst_case(a, b, level):
    lo, hi = sorted((a, b))
    p = demo_profile()
    c = lambda g: [CreditedFlow("P1", g, NOW - 300.0, "witness")]
    assert worst_of(p, level, 300.0, c(hi))[1] + 1e-9 >= worst_of(p, level, 300.0, c(lo))[1]


@given(m=st.one_of(st.none(), st.floats(min_value=0, max_value=1e7)))
def test_formatting_minutes_never_fails(m):
    assert isinstance(format_minutes(m), str)


# --------------------------------------------------------------------------- the trust light
def _core(clock_box):
    return HydroWitnessCore(demo_profile(), dict(DEMO_SECRETS), clock=lambda: clock_box[0])


@SET
@given(gap=st.floats(min_value=121.0, max_value=10_000_000.0), level=st.floats(min_value=0.0, max_value=30.0),
       p1=st.booleans(), p2=st.booleans())
def test_silent_scada_is_never_green(gap, level, p1, p2):
    """After SCADA stops reporting for longer than the stale time, the light cannot be green, whatever SCADA last said."""
    box = [NOW]
    core = _core(box)
    core.ingest_scada(ScadaSnapshot(t=NOW, tank_level_ft=level, pump_status={"P1": p1, "P2": p2},
                                    pump_flow_gpm={"P1": 250.0 * p1, "P2": 187.5 * p2}, demand_gpm=180.0, pressure_psi=26.8))
    box[0] = NOW + gap
    assert core.evaluate(NOW + gap)["trust"]["state"] != "green"


@SET
@given(level=st.floats(allow_nan=True, allow_infinity=True),
       pressure=st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=True)),
       demand=st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=True)),
       flow=st.floats(allow_nan=True, allow_infinity=True), later=st.floats(min_value=0.0, max_value=86400.0))
def test_odd_scada_numbers_never_crash_the_core(level, pressure, demand, flow, later):
    box = [NOW]
    core = _core(box)
    try:
        snap = ScadaSnapshot(t=NOW, tank_level_ft=level, pump_status={"P1": True, "P2": False},
                             pump_flow_gpm={"P1": flow, "P2": 0.0}, demand_gpm=demand, pressure_psi=pressure)
    except ValueError:
        return                       # the schema refused it at the door, which is fine
    core.ingest_scada(snap)
    v = core.evaluate(NOW + later)
    assert v["trust"]["state"] in ("green", "amber", "red")


# ------------------------------------------------------------------------------ the web interface
_APP = create_app(demo_profile(), dict(DEMO_SECRETS), token=None, run_background=False)
_CLIENT = TestClient(_APP, raise_server_exceptions=False)

JSON = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20)),
    lambda kids: st.one_of(st.lists(kids, max_size=4), st.dictionaries(st.text(max_size=12), kids, max_size=5)),
    max_leaves=12)
OBJ = st.dictionaries(st.sampled_from(["tank_level_ft", "pump_status", "pump_flow_gpm", "demand_gpm", "pressure_psi", "commands", "t", "x",
                                       "n", "b", "s", "k", "st", "c", "sig", "level_ft", "pump_id", "running", "note", "operator",
                                       "site_name", "peak_demand_gpm", "tank_diameter_ft", "setup_uncertainty_ft", "mains_hz"]), JSON, max_size=8)


@settings(max_examples=max(1, N * 4 // 5), deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(path=st.sampled_from(["/api/scada", "/api/node", "/api/manual", "/api/ack", "/api/playbook/start", "/api/drill/start",
                             "/api/drill/inject", "/api/drill/control", "/api/playbook/step/x"]), body=OBJ)
def test_garbage_posts_never_cause_a_server_error(path, body):
    r = _CLIENT.post(path, json=body)
    assert r.status_code < 500, (path, body, r.status_code, r.text[:200])


@settings(max_examples=max(1, N * 2 // 5), deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(body=OBJ)
def test_garbage_profile_updates_never_cause_a_server_error(body):
    r = _CLIENT.put("/api/profile", json=body)
    assert r.status_code < 500, (body, r.status_code, r.text[:200])
    assert _CLIENT.get("/api/state").status_code == 200       # and the service still works afterwards


@settings(max_examples=max(1, N // 4), deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(raw=st.binary(max_size=200), ctype=st.sampled_from(["application/json", "text/plain", "application/x-www-form-urlencoded"]))
def test_raw_junk_bodies_never_cause_a_server_error(raw, ctype):
    for path in ("/api/scada", "/api/node", "/api/manual"):
        assert _CLIENT.post(path, content=raw, headers={"content-type": ctype}).status_code < 500
