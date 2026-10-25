"""Next useful check: the one observation that would tell the operator the most right now, and why.

It does not guess and it does not score with hidden weights. It walks a short, fixed list of situations in a fixed
order of importance and picks the first that applies. Each suggestion says what to do, why it matters, what it
would change, and which reading to enter. Where the effect on the countdown can be worked out, it is worked out
by running the real clock again with the extra evidence; where it cannot (a person might see anything), it says so.

The order (most important first):
  1  no tank level at all                      nothing can be estimated until the tank is read
  2  the screen and a witness disagree         one person looking at the pump settles it
  3  the countdown is at 0 or its tank reading is old / model only (and, once the light is already red, the
     screen-versus-witness disagreement comes after the tank, because the countdown is what the operator needs)
  4  a Witness Node has gone quiet
  5  a pump seems to be running, no flow confirmed, and confirming it would change the countdown a lot
  6  no pressure gauge reading while the state is not green
  7  routine: the reading is aging while all is well
"""
from __future__ import annotations

from typing import Any, Optional

from .. import config as C
from ..schemas import SiteProfile
from .time_to_trouble import ClockResult, CreditedFlow, LevelReading, clock_text, compute_clock, format_age, format_minutes
from .quantities import CONFLICT


def _item(cid: str, title: str, who: str, what: str, why: str, effect: str, enter: Optional[dict],
          urgency: str = "high", priority: int = 5) -> dict:
    """priority: lower is more important (see the order in the module docstring). urgency is only a label."""
    return {"id": cid, "title": title, "who": who, "what": what, "why": why, "effect": effect,
            "enter": enter, "urgency": urgency, "priority": priority}


def choose(*, profile: SiteProfile, now: float, clock: ClockResult, trust_state: str, quantities: dict,
           nodes: list[dict], level: Optional[LevelReading], credits: list[CreditedFlow],
           cards: Optional[list[dict]] = None) -> dict[str, Any]:
    cands: list[dict] = []
    tank_enter = {"kind": "tank_level_ft", "pump_id": None}

    # 1. no tank level
    if clock.mode == "storage" and not clock.ok:
        cands.append(_item(
            "tank_missing", "Read the tank level", "Operator",
            "Read the tank sight glass (or the local level gauge) and enter the level in feet.",
            "There is no tank level from any source, so the countdown cannot be worked out. Nothing is guessed.",
            "You get a countdown the moment the reading is in.", tank_enter, "critical", 0))

    # 2. the screen and a witness disagree about a pump, and nobody has looked
    for it in quantities["pumps"]:
        if it["status"] == CONFLICT and not (it["operator"] and it["operator"]["counts"]):
            already_red = trust_state == "red"
            cands.append(_item(
                f"pump_look:{it['pump_id']}", f"Look at {it['pump_id']}", "Operator",
                f"Go to {it['pump_id']} (or ask someone who is there). Look at the pump and the Witness Node lights, "
                "and enter whether it is running.",
                it["status_text"] + " One person's own observation outweighs both the screen and the node.",
                ("The light is already red. Your look settles which of them is right and goes into the evidence log "
                 "as an operator's own observation."
                 if already_red else
                 "If you see something different from the screen, the state goes red. If you see what the screen says, "
                 "the node is the one that is wrong."),
                {"kind": "pump_running", "pump_id": it["pump_id"]}, "high" if already_red else "critical",
                3 if already_red else 1))

    # 3. the countdown is at zero, or its tank reading is old / only modelled
    if clock.ok and clock.mode == "storage":
        unc = clock.uncertainty_ft
        cost = clock.uncertainty_cost_min
        if clock.quality in ("old", "model_only"):
            gain = (f" The age of the reading costs about {format_minutes(cost)} of the worst case."
                    if cost is not None and cost >= 1 else "")
            cands.append(_item(
                "tank_refresh", "Read the tank again", "Operator",
                "Read the tank sight glass and enter the level in feet.",
                clock.quality_text,
                f"A fresh reading removes the {unc:.1f} ft of uncertainty and replaces a model with a measurement.{gain}",
                tank_enter, "high", 2))
        if clock.state == "empty" and clock.quality in ("fresh", "aging"):
            cands.append(_item(
                "pressure_gauge_zero", "Read a pressure gauge", "Operator",
                "Read a pressure gauge at the pump station, or at the farthest customer you can reach, and enter it in psi.",
                "The worst case has reached 0 minutes: at peak demand the farthest customer could already be at or below "
                f"{profile.min_pressure_psi:.0f} psi. The pressure shown is the screen's or the model's, not a measurement you can trust.",
                "A real gauge reading replaces the model. If it is above the line, you have time; if not, act now.",
                {"kind": "gauge_psi", "pump_id": None}, "critical", 2))

    # 4. a Witness Node has gone quiet
    for n in nodes:
        if n["silent"]:
            cands.append(_item(
                f"node_check:{n['node_id']}", f"Check Witness Node {n['node_id']}", "Operator",
                f"Go to {n['pump_id']} and check the node: power, Wi-Fi, and its lights. "
                "If it is fine, look at the pump and enter what you see.",
                f"Node {n['node_id']} has not reported recently, so {n['pump_id']} has only the screen to go on.",
                "Restores the independent witness for that pump, or tells you why it is silent.",
                {"kind": "pump_running", "pump_id": n["pump_id"]}, "high", 4))

    # 5. confirming flow would change the countdown a lot
    if trust_state != "green" and clock.ok and clock.mode == "storage" and level is not None:
        for it in quantities["pumps"]:
            if it["best_state"] != "RUNNING" or it["flow"]["state"] == "confirmed" or it["status"] == CONFLICT:
                continue
            pump = profile.pump(it["pump_id"])
            if pump is None:
                continue
            hypo = compute_clock(profile, now, level,
                                 credits + [CreditedFlow(pump.id, pump.rated_gpm * C.INFLOW_SAFETY_FACTOR, now,
                                                         "confirmed by an operator")])
            if hypo.worst_min is None:
                big = True
                after = "not falling"
            elif clock.worst_min is None:
                big = False
                after = ""
            else:
                big = (hypo.worst_min - clock.worst_min) >= max(15.0, 0.25 * max(clock.worst_min, 1.0))
                after = format_minutes(hypo.worst_min)
            if big:
                cands.append(_item(
                    f"flow_confirm:{pump.id}", f"Confirm water is moving from {pump.id}", "Operator",
                    f"Read {pump.id}'s discharge flow meter or its discharge pressure gauge. If water is really moving, "
                    "enter 'flow confirmed'.",
                    f"{pump.id} seems to be running, but the model gives it no credit: a motor can run against a closed "
                    "valve and deliver nothing.",
                    f"If confirmed, the worst case goes from {clock_text(clock, 'worst')} to {after}. If not confirmed, nothing changes.",
                    {"kind": "flow_confirmed", "pump_id": pump.id}, "medium", 5))

    # 6. no pressure gauge reading while the state is not green
    pr = quantities["pressure"]
    customers = next((c for c in (cards or []) if c.get("kind") == "customer"), None)
    if trust_state != "green" and pr["status"] in ("screen_only", "estimate", "unavailable") and clock.state != "empty":
        if customers is not None:
            cands.append(_item(
                "pressure_gauge", "Read a pressure gauge at a customer's tap", "Operator",
                "Read a pressure gauge at the farthest customer you can reach (or at a hydrant near the callers) and enter it in psi.",
                "Several customers report low pressure while the screen shows normal pressure. Only a gauge can say who is right.",
                "If the gauge differs from the screen by more than the allowed margin, the state goes red. If it agrees with "
                "the screen, the calls are probably local.", {"kind": "gauge_psi", "pump_id": None}, "high", 1))
        else:
            cands.append(_item(
                "pressure_gauge", "Read a pressure gauge", "Operator",
                "Read a pressure gauge at the pump station, or at the farthest customer you can reach, and enter it in psi.",
                "The pressure shown is the screen's or the model's. Nobody has measured it since this started.",
                "A real gauge reading is the strongest evidence about pressure. It replaces the model, and it contradicts the "
                "screen if they differ.", {"kind": "gauge_psi", "pump_id": None}, "medium", 6))

    # 7. routine: aging reading while all is well
    if trust_state == "green" and clock.ok and clock.quality == "aging":
        cands.append(_item(
            "routine_tank", "Read the tank on your next round", "Operator",
            "Read the tank sight glass on your normal round and enter it.",
            clock.quality_text, "Brings the countdown back to a fresh reading.", tank_enter, "routine", 9))

    if not cands:
        return {"primary": None, "alternatives": [],
                "text": "Nothing needs checking right now: the evidence agrees and is fresh. Keep to your normal rounds."}
    cands = [c for _, c in sorted(enumerate(cands), key=lambda ic: (ic[1]["priority"], ic[0]))]
    return {"primary": cands[0], "alternatives": cands[1:4], "text": cands[0]["title"] + ". " + cands[0]["why"]}
