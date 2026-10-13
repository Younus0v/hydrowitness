"""A demo site used by tests, drills and the dashboard's default profile."""
from __future__ import annotations

import math

from .schemas import Pump, SiteProfile

DEMO_SECRETS = {"NA": b"demo-secret-node-A-change-me", "NB": b"demo-secret-node-B-change-me"}


def demo_profile() -> SiteProfile:
    typical = [round(170 + 50 * math.sin((h - 9) / 24 * 2 * math.pi), 1) for h in range(24)]
    return SiteProfile(
        site_name="Demo Valley Water",
        tank_diameter_ft=70.0,
        tank_height_ft=30.0,
        tank_floor_elev_ft=80.0,
        critical_point_elev_ft=40.0,
        min_pressure_psi=20.0,
        headloss_margin_ft=5.0,
        peak_demand_gpm=300.0,
        typical_demand_gpm_by_hour=typical,
        pressure_tap_elev_ft=40.0,       # transmitter on the main at the critical point's elevation
        pumps=[
            Pump(id="P1", name="Pump 1", rated_gpm=250.0, node_id="NA"),
            Pump(id="P2", name="Pump 2", rated_gpm=187.5, node_id="NB"),
        ],
        emergency_contacts=["manager (primary)", "second contact (escalation)"],
    )
