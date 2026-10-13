"""Shared data formats for HydroWitness (build stage 1).

Everything the software, the simulator and the Witness Node firmware agree on
lives here. Times are epoch seconds (floats) everywhere except on the node wire
format, where integers are used so the HMAC string is identical in Python and C++.
"""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PSI_TO_FT = 2.31          # 1 psi of water = 2.31 ft of head
GAL_PER_FT3 = 7.48052


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------- site


class Pump(_Model):
    id: str
    name: str = ""
    rated_gpm: float = Field(gt=0)
    node_id: Optional[str] = None  # the Witness Node assigned to this pump (one node per pump)


class ResponseCurve(_Model):
    """Direct-pumping mode: pressure at the critical point after the pumps are lost.

    Precomputed per site with WNTR/EPANET; the app only interpolates it.
    """

    demand_gpm: float
    scenario: str = "all_pumps_off"
    minutes: list[float]
    psi: list[float]


class SiteProfile(_Model):
    """Everything the setup wizard collects."""

    site_name: str = "Demo system"
    mode: Literal["storage", "direct"] = "storage"
    utc_offset_hours: float = 0.0
    tank_diameter_ft: Optional[float] = None
    tank_area_sqft: Optional[float] = None
    tank_height_ft: Optional[float] = None
    tank_floor_elev_ft: float = 0.0
    critical_point_elev_ft: float = 0.0
    min_pressure_psi: float = 20.0
    headloss_margin_ft: float = 5.0
    peak_demand_gpm: float = 300.0
    typical_demand_gpm_by_hour: list[float] = Field(default_factory=lambda: [180.0] * 24, min_length=24, max_length=24)
    pumps: list[Pump] = Field(default_factory=list)
    mains_hz: Literal[50, 60] = 60
    emergency_contacts: list[str] = Field(default_factory=list)
    pressure_tap_elev_ft: Optional[float] = None  # elevation of the SCADA pressure transmitter
    level_tolerance_ft: float = 0.75
    pressure_tolerance_psi: float = 5.0
    flow_credit_ttl_s: float = 900.0
    stale_after_s: float = 120.0
    heartbeat_interval_s: float = 10.0
    clean_period_s: float = 1800.0
    command_response_s: float = 30.0
    response_curves: list[ResponseCurve] = Field(default_factory=list)

    @model_validator(mode="after")
    def _area(self) -> "SiteProfile":
        if self.tank_area_sqft is None and self.tank_diameter_ft:
            self.tank_area_sqft = math.pi * (self.tank_diameter_ft / 2.0) ** 2
        if self.mode == "storage" and not self.tank_area_sqft:
            raise ValueError("storage mode needs tank_diameter_ft or tank_area_sqft")
        if max(self.typical_demand_gpm_by_hour) > self.peak_demand_gpm:
            raise ValueError(
                f"peak_demand_gpm ({self.peak_demand_gpm}) must be at least the largest typical hourly demand "
                f"({max(self.typical_demand_gpm_by_hour)}). Check the setup numbers.")
        return self

    def pump(self, pump_id: str) -> Optional[Pump]:
        return next((p for p in self.pumps if p.id == pump_id), None)

    def node_ids(self) -> list[str]:
        return sorted({p.node_id for p in self.pumps if p.node_id})


# -------------------------------------------------------------------------- SCADA


class Command(_Model):
    t: float
    pump_id: str
    action: Literal["START", "STOP"]


class ScadaSnapshot(_Model):
    """What SCADA currently claims. Read-only: HydroWitness never writes back."""

    t: float
    tank_level_ft: Optional[float] = None
    pump_status: dict[str, Optional[bool]] = Field(default_factory=dict)
    pump_flow_gpm: dict[str, float] = Field(default_factory=dict)
    demand_gpm: Optional[float] = None
    pressure_psi: Optional[float] = None
    commands: list[Command] = Field(default_factory=list)


# ---------------------------------------------------------------------- node wire

NODE_STATE_NAMES = {"R": "RUNNING", "S": "STOPPED", "T": "TRANSITION", "U": "UNKNOWN"}


class NodeWire(_Model):
    """Exactly what the ESP32 sends (all integers/short strings, see docs/INTERFACES.md)."""

    n: str = Field(min_length=1, max_length=16)         # node id
    b: int = Field(ge=0)                                 # boot counter (persisted in flash)
    s: int = Field(ge=0)                                 # sequence number within this boot
    t: int = Field(ge=0)                                 # NTP time, milliseconds since epoch
    k: Literal["hb", "ev"]                               # heartbeat or event
    st: Literal["R", "S", "T", "U"]                      # combined state
    c: int = Field(ge=0, le=1000)                        # confidence in permille
    ca: Literal["R", "S", "U"]                           # accelerometer channel state
    cm: Literal["R", "S", "U"]                           # microphone channel state
    p: Literal[0, 1]                                     # 1 = mains power OK, 0 = on backup
    f: str = Field(min_length=1, max_length=16)         # firmware hash (short hex)
    sig: str = Field(min_length=64, max_length=64)      # HMAC-SHA256 hex


class NodeMessage(_Model):
    """A verified node message, in friendly units."""

    node_id: str
    boot: int
    seq: int
    ts: float
    kind: Literal["hb", "ev"]
    state: Literal["RUNNING", "STOPPED", "TRANSITION", "UNKNOWN"]
    confidence: float
    acc_state: Literal["RUNNING", "STOPPED", "UNKNOWN"]
    mic_state: Literal["RUNNING", "STOPPED", "UNKNOWN"]
    mains_ok: bool
    fw_hash: str
    received_t: float

    @property
    def both_channels_agree(self) -> bool:
        return self.acc_state == self.mic_state and self.acc_state in ("RUNNING", "STOPPED")

    @property
    def one_channel_only(self) -> bool:
        known = [s for s in (self.acc_state, self.mic_state) if s != "UNKNOWN"]
        return len(known) == 1


def message_from_wire(w: NodeWire, received_t: float) -> NodeMessage:
    chan = {"R": "RUNNING", "S": "STOPPED", "U": "UNKNOWN"}
    return NodeMessage(
        node_id=w.n, boot=w.b, seq=w.s, ts=w.t / 1000.0, kind=w.k,
        state=NODE_STATE_NAMES[w.st], confidence=w.c / 1000.0,
        acc_state=chan[w.ca], mic_state=chan[w.cm], mains_ok=bool(w.p),
        fw_hash=w.f, received_t=received_t,
    )


# ----------------------------------------------------------------- human witnesses

ManualKind = Literal["tank_level_ft", "gauge_psi", "pump_running", "flow_confirmed", "customer_call"]


class ManualReading(_Model):
    """Something a person observed: the strongest witnesses we have."""

    t: float
    kind: ManualKind
    value: Optional[float | bool] = None
    pump_id: Optional[str] = None
    operator: str = "operator"
    address: Optional[str] = None  # for customer calls: de-duplicates by address
    note: str = ""
