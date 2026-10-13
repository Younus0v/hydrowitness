"""Time-to-Trouble engine (project document section 5.3).

Storage mode: minutes until tank level falls to L_min, the lowest level at which the
critical service point still has the minimum pressure.

    L_min  = critical elevation + (min_psi * 2.31 ft/psi) + headloss margin - tank floor elevation
    V_use  = tank area * (level - L_min) * 7.48 gal/ft3
    worst  : demand at the site's peak, pumps off unless a witness shows water is moving
    expected: typical demand by hour of day, integrated forward. The headloss margin is entered ONCE, as the
             friction loss from the tank to the critical point at the configured peak demand. At lower demand
             there is less friction (it grows with flow^1.852), so the expected case scales the margin down
             with the hour's demand. Validation V1 showed that using the peak-demand margin at every hour made
             the expected case about 36% too pessimistic; the worst case keeps the full margin.

Inflow is credited ONLY when a witness shows water is actually moving. A running motor alone
earns no credit: a pump can run against a closed valve and deliver nothing.

Every estimate states what it is based on and how old that data is. As data ages the worst
case gets more pessimistic (level uncertainty grows), so the displayed range widens.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .. import config as C
from ..schemas import GAL_PER_FT3, PSI_TO_FT, SiteProfile


@dataclass
class LevelReading:
    value_ft: float
    t: float
    source: str  # "scada", "operator", "scada_last_trusted", "scada_untrusted"


@dataclass
class CreditedFlow:
    pump_id: str
    gpm: float          # already includes the 0.8 safety factor
    confirmed_t: float
    reason: str


@dataclass
class ClockResult:
    mode: str
    ok: bool
    basis: str
    level_ft: Optional[float] = None
    level_age_s: Optional[float] = None
    l_min_ft: Optional[float] = None
    usable_gal: Optional[float] = None
    worst_min: Optional[float] = None         # None with ok=True means "not falling"
    expected_min: Optional[float] = None
    credited_gpm: float = 0.0
    credited_pumps: list[str] = field(default_factory=list)
    uncertainty_ft: float = 0.0
    needs_operator_reading: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# ------------------------------------------------------------------ small helpers


def l_min_ft(profile: SiteProfile) -> float:
    """Lowest tank level that still gives the critical point its minimum pressure. Never below 0: a critical
    point far below the tank floor can have pressure to spare, but an empty tank delivers nothing. (Found by
    validation V1: without this floor the clock waited for a tank level below the bottom of the tank.)"""
    head_needed = profile.min_pressure_psi * PSI_TO_FT + profile.headloss_margin_ft
    return max(0.0, profile.critical_point_elev_ft + head_needed - profile.tank_floor_elev_ft)


def tank_area(profile: SiteProfile) -> float:
    return float(profile.tank_area_sqft)


def gal_per_ft_of_level(profile: SiteProfile) -> float:
    return tank_area(profile) * GAL_PER_FT3


def usable_gal(profile: SiteProfile, level_ft: float) -> float:
    return max(0.0, (level_ft - l_min_ft(profile)) * gal_per_ft_of_level(profile))


def local_hour(profile: SiteProfile, t: float) -> int:
    return int(((t / 3600.0) + profile.utc_offset_hours) % 24)


def typical_demand(profile: SiteProfile, t: float) -> float:
    return float(profile.typical_demand_gpm_by_hour[local_hour(profile, t)])


def level_uncertainty_ft(age_s: float, source: str) -> float:
    unc = C.LEVEL_BASE_UNCERTAINTY_FT + C.LEVEL_DRIFT_FT_PER_MIN * max(0.0, age_s) / 60.0
    if source in ("scada_untrusted", "scada_last_trusted"):
        unc += C.UNTRUSTED_EXTRA_UNCERTAINTY_FT
    return unc


def credited_inflow(profile: SiteProfile, pump_id: str, ttl_ok: bool = True) -> float:
    p = profile.pump(pump_id)
    return 0.0 if (p is None or not ttl_ok) else p.rated_gpm * C.INFLOW_SAFETY_FACTOR


def expected_l_min_ft(profile: SiteProfile, demand_gpm: float) -> float:
    """L_min for the expected case: the headloss margin scaled to this hour's demand."""
    ratio = min(max(demand_gpm / profile.peak_demand_gpm, 0.0), 1.0) if profile.peak_demand_gpm > 0 else 1.0
    margin = profile.headloss_margin_ft * ratio ** C.HEADLOSS_EXPONENT
    return max(0.0, profile.critical_point_elev_ft + profile.min_pressure_psi * PSI_TO_FT + margin - profile.tank_floor_elev_ft)


def _cap(profile: SiteProfile, level_ft: float) -> float:
    return min(level_ft, profile.tank_height_ft) if profile.tank_height_ft else level_ft


def _integrate_expected(profile: SiteProfile, t0: float, level_ft: float, q_in: float,
                        horizon_h: float = C.EXPECTED_HORIZON_H) -> Optional[float]:
    """Minutes until the level reaches the expected-case L_min, stepping through the typical hourly demand.

    Returns None if the tank is not falling to it within the horizon.
    """
    gpf = gal_per_ft_of_level(profile)
    t = t0
    lvl = level_ft
    end = t0 + horizon_h * 3600.0
    while t < end:
        demand = typical_demand(profile, t)
        lmin = expected_l_min_ft(profile, demand)
        if lvl <= lmin:
            return (t - t0) / 60.0
        next_hour = (math.floor(t / 3600.0) + 1) * 3600.0
        dt_min = (min(next_hour, end) - t) / 60.0
        net = demand - q_in
        if net > 1e-9:
            drop_ft = net * dt_min / gpf
            if lvl - drop_ft <= lmin:
                return (t - t0) / 60.0 + (lvl - lmin) / (net / gpf)
            lvl -= drop_ft
        else:
            lvl = _cap(profile, lvl + (-net) * dt_min / gpf)
        t = min(next_hour, end)
    return None


# ------------------------------------------------------------------------ the clock


def compute_clock(profile: SiteProfile, now: float, level: Optional[LevelReading],
                  credits: Optional[list[CreditedFlow]] = None) -> ClockResult:
    credits = credits or []
    if profile.mode == "direct":
        return _compute_direct(profile, now, credits)

    lmin = l_min_ft(profile)
    if level is None:
        return ClockResult(mode="storage", ok=False, l_min_ft=lmin, needs_operator_reading=True,
                           basis="No tank level available. Read the tank sight glass and enter it.")

    age = max(0.0, now - level.t)
    unc = level_uncertainty_ft(age, level.source)
    q_in = sum(c.gpm for c in credits)
    gpf = gal_per_ft_of_level(profile)

    # Inflow is credited only from the moment a witness confirmed it. Before that, during the dark interval,
    # the pumps are assumed off (worst case) so a late confirmation cannot make the past look better.
    inflow_from = now
    if credits:
        inflow_from = min(max(min(c.confirmed_t for c in credits), level.t), now)
    dark_no_credit_s = max(0.0, inflow_from - level.t)
    dark_credit_s = max(0.0, now - inflow_from)

    # Worst case: peak demand, credited inflow only, level uncertainty subtracted.
    net_w = profile.peak_demand_gpm - q_in
    drop_w_ft = (profile.peak_demand_gpm * dark_no_credit_s + max(net_w, 0.0) * dark_credit_s) / 60.0 / gpf
    level_w = level.value_ft - drop_w_ft - unc
    usable_w = max(0.0, (level_w - lmin) * gpf)
    if net_w <= 0:
        worst: Optional[float] = None
    elif usable_w <= 0:
        worst = 0.0
    else:
        worst = usable_w / net_w

    # Expected case: typical hourly demand rolled forward from the reading, then integrated on, with the
    # headloss margin scaled to each hour's demand.
    lvl_now = _roll_expected_level(profile, level.t, now, level.value_ft, q_in, inflow_from)
    expected = _integrate_expected(profile, now, lvl_now, q_in)
    if worst is not None and expected is not None:
        expected = max(expected, worst)   # the range never inverts

    basis = {
        "scada": "SCADA level (trusted)",
        "operator": "operator tank reading",
        "scada_last_trusted": "last trusted SCADA level, rolled forward (model only)",
        "scada_untrusted": "SCADA level that can't be trusted (model only)",
    }.get(level.source, level.source)
    notes = []
    if credits:
        notes.append("Inflow credited: " + ", ".join(f"{c.pump_id} {c.gpm:.0f} gpm ({c.reason})" for c in credits))
    else:
        notes.append("No inflow credited: pumps assumed off until a witness shows water moving.")
    notes.append(f"Worst case assumes demand never exceeds the configured peak of {profile.peak_demand_gpm:.0f} gpm. "
                 "A main break or fire flow would drain the tank faster: re-read the tank to re-anchor this clock.")
    return ClockResult(
        mode="storage", ok=True, basis=f"{basis}, {int(age)} s old", level_ft=level.value_ft,
        level_age_s=age, l_min_ft=lmin, usable_gal=usable_w, worst_min=worst, expected_min=expected,
        credited_gpm=q_in, credited_pumps=[c.pump_id for c in credits], uncertainty_ft=unc,
        needs_operator_reading=level.source in ("scada_last_trusted", "scada_untrusted"), notes=notes,
    )


def _roll_expected_level(profile: SiteProfile, t0: float, now: float, level0: float, q_in: float,
                         inflow_from: float) -> float:
    """Roll the expected-case tank level forward from the reading time to now. Inflow counts only from the
    time a witness confirmed it."""
    gpf = gal_per_ft_of_level(profile)
    lvl = level0
    t = t0
    while t < now:
        nxt = min((math.floor(t / 3600.0) + 1) * 3600.0, now)
        if inflow_from > t:
            nxt = min(nxt, inflow_from)
        q = q_in if t >= inflow_from else 0.0
        lvl = _cap(profile, lvl - (typical_demand(profile, t) - q) * (nxt - t) / 60.0 / gpf)
        t = nxt
    return lvl


# ------------------------------------------------------------------- direct pumping


def _crossing_minutes(curve_min: list[float], curve_psi: list[float], limit_psi: float) -> Optional[float]:
    for i in range(1, len(curve_min)):
        a, b = curve_psi[i - 1], curve_psi[i]
        if a >= limit_psi > b:
            frac = (a - limit_psi) / (a - b) if a != b else 0.0
            return curve_min[i - 1] + frac * (curve_min[i] - curve_min[i - 1])
    if curve_psi and curve_psi[0] < limit_psi:
        return 0.0
    return None


def _compute_direct(profile: SiteProfile, now: float, credits: list[CreditedFlow]) -> ClockResult:
    """No storage: the clock comes from precomputed WNTR response curves."""
    curves = sorted(profile.response_curves, key=lambda c: c.demand_gpm)
    if not curves:
        return ClockResult(mode="direct", ok=False,
                           basis="Direct-pumping site: response curves are not loaded for this site.",
                           notes=["Direct-pumping mode needs response curves computed for this site with WNTR/EPANET. "
                                  "That tool is not part of this build, so direct-pumping sites are not supported yet."])
    worst_curve = next((c for c in curves if c.demand_gpm >= profile.peak_demand_gpm), curves[-1])
    typ = typical_demand(profile, now)
    exp_curve = next((c for c in curves if c.demand_gpm >= typ), curves[-1])
    worst = _crossing_minutes(worst_curve.minutes, worst_curve.psi, profile.min_pressure_psi)
    expected = _crossing_minutes(exp_curve.minutes, exp_curve.psi, profile.min_pressure_psi)
    if worst is not None and expected is not None:
        expected = max(expected, worst)
    return ClockResult(
        mode="direct", ok=True, worst_min=worst, expected_min=expected,
        basis=f"precomputed response curves (peak {worst_curve.demand_gpm:.0f} gpm / typical {exp_curve.demand_gpm:.0f} gpm)",
        notes=["Direct-pumping systems have minutes, not hours. Confirm on site."],
    )


def format_minutes(m: Optional[float]) -> str:
    if m is None:
        return "stable"
    h, mm = divmod(int(round(m)), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"
