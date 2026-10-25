"""What each thing on the screen rests on, one quantity at a time.

For pump operation, tank level, pressure and remaining time this says who is reporting it (the screen, a Witness
Node, a person, or the model), how old that report is, whether the sources agree, and what the report does NOT
prove. It is a view of facts the engines already have; it adds nothing to the trust score and changes no light.

Three rules it keeps on purpose:
  * a pump that sounds like it is running says nothing about water moving or pressure being adequate;
  * a number the model worked out is labelled as an estimate, never as a measurement;
  * a missing reading is shown as missing, not filled in.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .. import config as C
from ..schemas import PSI_TO_FT, ManualReading, ScadaSnapshot, SiteProfile
from .time_to_trouble import ClockResult, CreditedFlow, clock_text, format_age

INDEPENDENT, SCREEN_ONLY, ESTIMATE, UNAVAILABLE, CONFLICT, AGREE = (
    "independent", "screen_only", "estimate", "unavailable", "conflict", "agree")

RUNNING_NOT_FLOW = ("A pump that is running does not prove water is moving or that pressure is adequate: "
                    "it can run against a closed valve. Flow only counts once a person confirms it.")


def _latest(manual: list[ManualReading], kind: str, pump_id: Optional[str] = None) -> Optional[ManualReading]:
    best = None
    for r in manual:
        if r.kind != kind or (pump_id is not None and r.pump_id != pump_id) or r.value is None:
            continue
        if best is None or r.t > best.t:
            best = r
    return best


def _word(running: Optional[bool]) -> str:
    return "unknown" if running is None else ("RUNNING" if running else "STOPPED")


# ------------------------------------------------------------------------------------------------ pumps
def _pump_item(profile: SiteProfile, now: float, latest: Optional[ScadaSnapshot], nodes: list[dict],
               manual: list[ManualReading], credits: list[CreditedFlow], pump) -> dict:
    claim = latest.pump_status.get(pump.id) if latest else None
    screen_age = None if latest is None else round(now - latest.t, 1)
    screen = {"value": _word(claim), "age_s": screen_age}
    stale = screen_age is not None and screen_age > profile.stale_after_s
    says = f"The last screen report ({format_age(screen_age)} ago) said" if stale else "The screen says"
    only = "Only the last screen report" if stale else "Only the screen"

    node = next((n for n in nodes if n["pump_id"] == pump.id), None)
    node_info: Optional[dict] = None
    node_state: Optional[str] = None
    if node is not None:
        usable = (not node["silent"] and node["state"] in ("RUNNING", "STOPPED")
                  and (node["confidence"] or 0.0) >= C.NODE_MIN_CONFIDENCE)
        node_state = node["state"] if usable else None
        if node["silent"]:
            note = "Silent: it has not reported recently, so it is not a witness right now."
        elif node["state"] is None:
            note = "Waiting for its first reading."
        elif node["state"] not in ("RUNNING", "STOPPED"):
            note = "It cannot tell, so it is not counted."
        elif not usable:
            note = f"Only {round((node['confidence'] or 0) * 100)}% confident, so it is not counted."
        else:
            note = f"{round((node['confidence'] or 0) * 100)}% confident."
        node_info = {"node_id": node["node_id"], "value": node["state"] or "unknown", "usable": usable,
                     "confidence": node["confidence"], "age_s": node["last_seen_age_s"], "note": note,
                     "mains_ok": node["mains_ok"]}

    op = _latest(manual, "pump_running", pump.id)
    op_info: Optional[dict] = None
    op_state: Optional[str] = None
    if op is not None:
        counts = now - op.t <= C.FRESHNESS_S
        op_state = _word(bool(op.value)) if counts else None
        op_info = {"who": op.operator, "value": _word(bool(op.value)), "age_s": round(now - op.t, 1), "counts": counts,
                   "note": "" if counts else "Too old to count; look again."}

    cr = next((c for c in credits if c.pump_id == pump.id), None)
    flow = ({"state": "confirmed", "gpm": cr.gpm, "age_s": round(now - cr.confirmed_t, 1), "reason": cr.reason,
             "note": f"Credited {cr.gpm:.0f} gpm ({cr.reason})."}
            if cr else {"state": "not_confirmed", "gpm": 0.0, "age_s": None, "reason": "",
                        "note": "No flow confirmed: the model assumes this pump delivers nothing."})

    witnesses = [(f"Witness Node {node_info['node_id']}", node_state)] if node_state else []
    if op_state:
        witnesses.append((f"{op_info['who']}'s own look", op_state))
    claim_word = _word(claim) if claim is not None else None
    against = [w for w, st in witnesses if claim_word and st != claim_word]
    with_claim = [w for w, st in witnesses if claim_word and st == claim_word]
    if len({st for _, st in witnesses}) > 1:
        status, text = CONFLICT, "The witnesses disagree with each other: " + "; ".join(f"{w} says {st}" for w, st in witnesses) + "."
    elif against:
        status = CONFLICT
        text = f"{says} {claim_word}, but {' and '.join(against)} say{'s' if len(against) == 1 else ''} {witnesses[0][1]}."
    elif witnesses and claim is None:
        status, text = INDEPENDENT, f"Reported only by {witnesses[0][0]} ({witnesses[0][1]}); the screen gives no pump status."
    elif with_claim:
        status, text = AGREE, f"The screen and {' and '.join(with_claim)} agree: {claim_word}."
    elif claim is not None:
        status, text = SCREEN_ONLY, f"{only} reports this pump ({claim_word}). Nothing independent confirms it."
    else:
        status, text = UNAVAILABLE, "No report from the screen or any witness."
    best = witnesses[0][1] if witnesses else claim_word
    limit = RUNNING_NOT_FLOW if best == "RUNNING" else ""
    return {"pump_id": pump.id, "name": pump.name or pump.id, "status": status, "status_text": text,
            "screen": screen, "node": node_info, "operator": op_info, "flow": flow, "limit": limit,
            "best_state": best if best != "unknown" else None}


# ------------------------------------------------------------------------------------------------- tank
def _tank(profile: SiteProfile, now: float, latest: Optional[ScadaSnapshot], manual: list[ManualReading],
          clock: ClockResult, scada_near: Callable[[float], Optional[ScadaSnapshot]], scada_stale: bool) -> dict:
    rows: list[dict] = []
    if latest is not None and latest.tank_level_ft is not None:
        rows.append({"source": "Screen (SCADA)", "kind": "screen", "value": latest.tank_level_ft,
                     "age_s": round(now - latest.t, 1), "note": "Stale: the screen has not updated recently." if scada_stale else ""})
    op = _latest(manual, "tank_level_ft")
    conflict = False
    if op is not None:
        age = now - op.t
        ref = scada_near(op.t) or latest
        diff = None
        if ref is not None and ref.tank_level_ft is not None:
            diff = float(op.value) - ref.tank_level_ft
            conflict = abs(diff) > profile.level_tolerance_ft and age <= C.FRESHNESS_S
        rows.append({"source": f"{op.operator} (read the tank)", "kind": "operator", "value": float(op.value),
                     "age_s": round(age, 1),
                     "note": ("Disagrees with the screen by %.1f ft." % abs(diff)) if conflict else
                             ("" if age <= C.FRESHNESS_S else "Older than 15 minutes.")})
    src = clock.level_source
    if not clock.ok or clock.level_ft is None:
        status, text = UNAVAILABLE, "No tank level from anywhere. There is no countdown until someone reads the tank."
    elif conflict:
        status, text = CONFLICT, "The operator's reading and the screen disagree. The countdown uses the operator's reading."
    elif src == "operator":
        status, text = INDEPENDENT, "An operator's own reading of the tank."
    elif src == "scada":
        status, text = SCREEN_ONLY, "The screen's reading. Nothing independent has checked it recently."
    else:
        status, text = ESTIMATE, "Not measured now: " + clock.level_source_text + "."
    return {"title": "Tank level", "status": status, "status_text": text,
            "value_ft": clock.level_ft if clock.ok else None, "rows": rows,
            "limit": "The level is what the clock is built on; a wrong level makes every minute shown wrong."}


# --------------------------------------------------------------------------------------------- pressure
def _pressure(profile: SiteProfile, now: float, latest: Optional[ScadaSnapshot], manual: list[ManualReading],
              clock: ClockResult, scada_stale: bool = False) -> dict:
    rows: list[dict] = []
    g = _latest(manual, "gauge_psi")
    gauge_counts = g is not None and now - g.t <= C.FRESHNESS_S
    scada_psi = latest.pressure_psi if latest is not None else None
    if scada_psi is not None:
        rows.append({"source": "Screen (SCADA transmitter)", "kind": "screen", "value": scada_psi,
                     "age_s": round(now - latest.t, 1),
                     "note": ("Stale: the screen has not updated recently. " if scada_stale else "")
                             + "Measured at the transmitter, not at the farthest customer."})
    conflict = False
    if g is not None:
        age = now - g.t
        if scada_psi is not None:
            conflict = abs(float(g.value) - scada_psi) > max(profile.pressure_tolerance_psi, 0.2 * float(g.value)) and gauge_counts
        rows.append({"source": f"{g.operator} (read a gauge)", "kind": "operator", "value": float(g.value),
                     "age_s": round(age, 1),
                     "note": "Disagrees with the screen." if conflict else ("" if gauge_counts else "Older than 15 minutes.")})
    est = None
    if clock.ok and clock.worst_level_now_ft is not None:
        est = (profile.tank_floor_elev_ft + clock.worst_level_now_ft - profile.critical_point_elev_ft
               - profile.headloss_margin_ft) / PSI_TO_FT
        rows.append({"source": "Model (from the tank level)", "kind": "model", "value": round(est, 1), "age_s": None,
                     "note": "A cautious lower bound at the farthest customer at peak demand. Not a measurement."})
    if conflict:
        status, text = CONFLICT, "The gauge reading and the screen's pressure disagree."
    elif gauge_counts:
        status, text = INDEPENDENT, "A person read a gauge recently."
    elif scada_psi is not None and not scada_stale:
        status, text = SCREEN_ONLY, "The screen's pressure. No gauge reading to check it."
    elif est is not None:
        status, text = ESTIMATE, ("No current pressure measurement: the screen's last value is old. Only the model's estimate from the tank level."
                                  if scada_psi is not None else
                                  "No pressure measurement at all. Only the model's estimate from the tank level.")
    else:
        status, text = UNAVAILABLE, "No pressure reading and no tank level to estimate from."
    return {"title": "Pressure", "status": status, "status_text": text, "rows": rows,
            "model_psi": None if est is None else round(est, 1),
            "limit": ("The model's number is a lower bound from the tank level, not a pressure anyone measured. "
                      "Customers' own pressure can differ.")}


# ---------------------------------------------------------------------------------------- remaining time
def _remaining(profile: SiteProfile, clock: ClockResult) -> dict:
    if not clock.ok:
        return {"title": "Remaining time", "status": UNAVAILABLE, "status_text": "Estimate unavailable.",
                "inputs": [], "assumptions": clock.assumption_lines, "worst_text": clock_text(clock, "worst"),
                "expected_text": clock_text(clock, "expected"), "quality": clock.quality,
                "quality_text": clock.quality_text, "limit": "No estimate is shown rather than a guess."}
    inputs = [
        {"name": "Tank level", "text": f"{clock.level_ft:.1f} ft from {clock.level_source_text}",
         "age_s": clock.level_age_s},
        {"name": "Demand", "text": f"peak {profile.peak_demand_gpm:.0f} gpm (worst case); "
                                    f"{clock.assumptions.get('expected_demand_now_gpm', 0):.0f} gpm typical for this hour (expected)",
         "age_s": None},
        {"name": "Inflow", "text": (f"{clock.credited_gpm:.0f} gpm credited" if clock.credited_gpm else "none credited"),
         "age_s": None},
        {"name": "Looking ahead", "text": f"up to {int(clock.horizon_h)} h", "age_s": None},
    ]
    status = ESTIMATE
    return {"title": "Remaining time", "status": status,
            "status_text": "An estimate from the inputs below. It is not a measurement.",
            "worst_text": clock_text(clock, "worst"), "expected_text": clock_text(clock, "expected"),
            "inputs": inputs, "assumptions": clock.assumption_lines, "quality": clock.quality,
            "quality_text": clock.quality_text,
            "limit": ("Demand above the configured peak (a main break, fire flow) would drain the tank faster than "
                      "the worst case shown.")}


# ----------------------------------------------------------------------------------------------- gaps
def _gaps(pumps: list[dict], tank: dict, pressure: dict, clock: ClockResult, scada_stale: bool,
          trust_state: str, scada_age: Optional[float], nodes: list[dict]) -> list[dict]:
    g: list[dict] = []

    def add(kind: str, quantity: str, text: str) -> None:
        g.append({"kind": kind, "quantity": quantity, "text": text})

    for it in pumps:
        if it["status"] == CONFLICT:
            add("conflict", f"Pump {it['pump_id']}", it["status_text"])
    if tank["status"] == CONFLICT:
        add("conflict", "Tank level", tank["status_text"])
    if pressure["status"] == CONFLICT:
        add("conflict", "Pressure", pressure["status_text"])
    if tank["status"] == UNAVAILABLE:
        add("missing", "Tank level", "No tank level, so no countdown. Read the tank sight glass.")
    elif clock.quality in ("old", "model_only"):
        add("stale", "Tank level", clock.quality_text)
    if scada_stale:
        add("stale", "Screen data", "The screen has not updated for " + format_age(scada_age) + ".")
    for n in nodes:
        if n["silent"]:
            add("missing", f"Witness Node {n['node_id']}",
                f"Node {n['node_id']} is silent, so {n['pump_id']} has only the screen.")
    if trust_state != "green":
        if pressure["status"] in (SCREEN_ONLY, ESTIMATE, UNAVAILABLE):
            add("missing", "Pressure", "No gauge reading yet. The pressure shown is the screen's or the model's.")
        for it in pumps:
            op = it["operator"]
            if (op is None or not op["counts"]) and it["status"] in (SCREEN_ONLY, UNAVAILABLE):
                add("missing", f"Pump {it['pump_id']}",
                    f"Only the screen reports {it['pump_id']}, and no one has looked at it yet.")
    return g


def build(*, profile: SiteProfile, now: float, latest: Optional[ScadaSnapshot], nodes: list[dict],
          manual: list[ManualReading], credits: list[CreditedFlow], clock: ClockResult,
          scada_near: Callable[[float], Optional[ScadaSnapshot]], scada_stale: bool, trust_state: str) -> dict[str, Any]:
    pumps = [_pump_item(profile, now, latest, nodes, manual, credits, p) for p in profile.pumps]
    tank = _tank(profile, now, latest, manual, clock, scada_near, scada_stale)
    pressure = _pressure(profile, now, latest, manual, clock, scada_stale)
    remaining = _remaining(profile, clock)
    gaps = _gaps(pumps, tank, pressure, clock, scada_stale, trust_state,
                 None if latest is None else now - latest.t, nodes)
    return {"pumps": pumps, "tank": tank, "pressure": pressure, "remaining": remaining, "gaps": gaps}
