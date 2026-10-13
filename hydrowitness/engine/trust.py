"""Witness Matcher and Trust Engine (project document section 5.5).

Compares SCADA's claims with every available witness and turns the disagreements into a
green / amber / red state with plain-language evidence cards.

    S = sum( weight x freshness x confidence )  over witnesses that contradict SCADA

    green  : S < 1.0, SCADA data fresh, every node heartbeat present
    amber  : 1.0 <= S < 3.0, or SCADA stale, or a node has gone silent, or a message was rejected
    red    : S >= 3.0, or an operator's own reading contradicts SCADA,
             or SCADA is unreachable while any witness shows abnormal conditions
    latch  : amber/red clear only after an operator acknowledges AND a clean period passes.
             The state never returns to green on its own.

These weights and thresholds are defaults chosen before testing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import config as C
from ..schemas import ManualReading, NodeMessage, ScadaSnapshot, SiteProfile
from .consistency import RuleResult

ORDER = {"green": 0, "amber": 1, "red": 2}


def fmt_t(profile: SiteProfile, t: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(t + profile.utc_offset_hours * 3600.0))


@dataclass
class Contribution:
    id: str
    kind: str          # node | operator | customer | rule | integrity
    weight: float
    freshness: float
    confidence: float
    title: str
    text: str
    t: float

    @property
    def value(self) -> float:
        return self.weight * self.freshness * self.confidence

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["contribution"] = round(self.value, 3)
        return d


@dataclass
class TrustInputs:
    now: float
    profile: SiteProfile
    scada_latest: Optional[ScadaSnapshot]
    scada_near: Callable[[float], Optional[ScadaSnapshot]]
    rules: list[RuleResult]
    node_msgs: dict[str, Optional[NodeMessage]]
    node_state_since: dict[str, Optional[float]]
    node_silent: dict[str, bool]
    claim_since: dict[str, float]
    manual: list[ManualReading]
    rejected_recent: list[tuple]


@dataclass
class TrustResult:
    state: str
    raw_state: str
    score: float
    cards: list[dict]
    info_cards: list[dict]
    flags: list[str]
    stale: bool
    latched: bool
    needs_ack: bool
    clean_for_s: Optional[float]
    changed_t: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def freshness(age_s: float, horizon_s: float = C.FRESHNESS_S) -> float:
    return max(0.0, 1.0 - max(0.0, age_s) / horizon_s)


class TrustEngine:
    def __init__(self, profile: SiteProfile):
        self.profile = profile
        self.reset()

    def reset(self) -> None:
        self._latched = "green"
        self._latched_t = 0.0
        self._ack_t: Optional[float] = None
        self._clean_since: Optional[float] = None
        self._changed_t = 0.0
        self._last_state = "green"

    # --------------------------------------------------------------- operator ack
    def acknowledge(self, now: float) -> bool:
        if self._latched == "green":
            return False
        self._ack_t = now
        return True

    # -------------------------------------------------------------- contributions
    def _node_contributions(self, inp: TrustInputs) -> tuple[list[Contribution], list[dict]]:
        p, now = self.profile, inp.now
        out: list[Contribution] = []
        info: list[dict] = []
        claims = inp.scada_latest.pump_status if inp.scada_latest else {}
        for pump in p.pumps:
            nid = pump.node_id
            if not nid:
                continue
            msg = inp.node_msgs.get(nid)
            if msg is None or inp.node_silent.get(nid):
                continue
            if not msg.mains_ok:
                info.append({
                    "id": f"power:{nid}", "title": f"Node {nid} is on backup power",
                    "text": (f"Witness Node {nid} reports mains power lost at {pump.id}'s location. This fits an "
                             "ordinary power outage as well as an attack, so do not assume either."),
                    "t": msg.ts})
            if msg.state == "UNKNOWN" and msg.acc_state != "UNKNOWN" and msg.mic_state != "UNKNOWN" \
                    and msg.acc_state != msg.mic_state:
                info.append({
                    "id": f"chan:{nid}", "title": f"Node {nid} channels disagree",
                    "text": f"Node {nid}: vibration says {msg.acc_state}, sound says {msg.mic_state}. "
                            "The node is not guessing and is not counted as a witness.",
                    "t": msg.ts})
            claim = claims.get(pump.id)
            if claim is None or msg.state not in ("RUNNING", "STOPPED") or msg.confidence < C.NODE_MIN_CONFIDENCE:
                continue
            if (msg.state == "RUNNING") == bool(claim):
                continue
            node_since = inp.node_state_since.get(nid) or msg.ts
            claim_t = inp.claim_since.get(pump.id, float("-inf"))
            if now - max(node_since, claim_t) < C.GRACE_S:
                continue  # command lag, not a contradiction yet
            both = msg.both_channels_agree
            weight = C.W_NODE_BOTH if both else C.W_NODE_ONE
            fresh = freshness(now - msg.received_t)
            claim_txt = "RUNNING" if claim else "STOPPED"
            how = "hears and feels" if both else "reports (one channel only)"
            out.append(Contribution(
                id=f"node:{nid}:{pump.id}", kind="node", weight=weight, freshness=fresh, confidence=msg.confidence,
                title=f"{pump.id}: screen and Witness Node disagree",
                text=(f"SCADA says {pump.id} {claim_txt} (since {fmt_t(p, claim_t) if claim_t > 0 else 'earlier'}). "
                      f"Witness Node {nid} {how} {msg.state} since {fmt_t(p, node_since)}, "
                      f"confidence {msg.confidence:.2f}. Disagreement weight {weight:.1f}."),
                t=max(node_since, claim_t if claim_t > 0 else node_since)))
        return out, info

    def _operator_contributions(self, inp: TrustInputs) -> list[Contribution]:
        p, now = self.profile, inp.now
        out: list[Contribution] = []
        latest: dict[tuple, ManualReading] = {}
        for r in inp.manual:
            if r.kind in ("tank_level_ft", "gauge_psi", "pump_running"):
                key = (r.kind, r.pump_id)
                if key not in latest or r.t > latest[key].t:
                    latest[key] = r
        for (kind, pump_id), r in latest.items():
            fresh = freshness(now - r.t)
            if fresh <= 0:
                continue
            ref = inp.scada_near(r.t) or inp.scada_latest
            if ref is None:
                continue
            if kind == "tank_level_ft" and ref.tank_level_ft is not None and r.value is not None:
                if abs(float(r.value) - ref.tank_level_ft) > p.level_tolerance_ft:
                    out.append(Contribution(
                        id="op:level", kind="operator", weight=C.W_OPERATOR, freshness=fresh, confidence=1.0,
                        title="Tank level: the operator's reading disagrees with the screen",
                        text=(f"{r.operator} read the tank at {float(r.value):.2f} ft at {fmt_t(p, r.t)}; SCADA shows "
                              f"{ref.tank_level_ft:.2f} ft. Disagreement weight {C.W_OPERATOR:.1f}."), t=r.t))
            elif kind == "gauge_psi" and ref.pressure_psi is not None and r.value is not None:
                tol = max(p.pressure_tolerance_psi, 0.2 * float(r.value))
                if abs(float(r.value) - ref.pressure_psi) > tol:
                    out.append(Contribution(
                        id="op:gauge", kind="operator", weight=C.W_OPERATOR, freshness=fresh, confidence=1.0,
                        title="Pressure: the operator's gauge reading disagrees with the screen",
                        text=(f"{r.operator} read a gauge at {float(r.value):.1f} psi at {fmt_t(p, r.t)}; SCADA shows "
                              f"{ref.pressure_psi:.1f} psi. Disagreement weight {C.W_OPERATOR:.1f}."), t=r.t))
            elif kind == "pump_running" and pump_id and r.value is not None:
                claim = ref.pump_status.get(pump_id)
                if claim is not None and bool(r.value) != bool(claim):
                    out.append(Contribution(
                        id=f"op:pump:{pump_id}", kind="operator", weight=C.W_OPERATOR, freshness=fresh, confidence=1.0,
                        title=f"{pump_id}: the operator sees something different from the screen",
                        text=(f"{r.operator} saw {pump_id} {'RUNNING' if r.value else 'STOPPED'} at {fmt_t(p, r.t)}; "
                              f"SCADA says {'RUNNING' if claim else 'STOPPED'}. Disagreement weight {C.W_OPERATOR:.1f}."),
                        t=r.t))
        return out

    def _customer_contribution(self, inp: TrustInputs) -> Optional[Contribution]:
        calls = [r for r in inp.manual if r.kind == "customer_call" and inp.now - r.t <= C.CUSTOMER_WINDOW_S]
        if not calls:
            return None
        distinct = {(r.address or f"anon{int(r.t)}") for r in calls}
        if len(distinct) < C.CUSTOMER_MIN_DISTINCT:
            return None
        s = inp.scada_latest
        if s is not None and s.pressure_psi is not None and s.pressure_psi < self.profile.min_pressure_psi + 5:
            return None  # SCADA already admits low pressure, so the calls don't contradict it
        newest = max(r.t for r in calls)
        weight = min(C.W_CUSTOMER_EACH * len(distinct), C.W_CUSTOMER_CAP)
        return Contribution(
            id="customers", kind="customer", weight=weight,
            freshness=freshness(inp.now - newest, C.CUSTOMER_WINDOW_S), confidence=1.0,
            title="Customers report low pressure",
            text=(f"{len(distinct)} different addresses called about low pressure while the screen shows normal "
                  f"pressure. Customer reports are a weak signal (capped at {C.W_CUSTOMER_CAP:.1f}) and "
                  "can't turn the state red on their own."),
            t=newest)

    @staticmethod
    def _rule_contributions(inp: TrustInputs) -> list[Contribution]:
        out = []
        for r in inp.rules:
            if r.fired:
                out.append(Contribution(
                    id=f"rule:{r.rule_id}", kind="rule", weight=r.severity, freshness=1.0, confidence=1.0,
                    title=r.name, text=f"{r.explanation} Weight {r.severity:.1f}.", t=r.since or inp.now))
        return out

    def _integrity_contribution(self, inp: TrustInputs) -> Optional[Contribution]:
        rej = inp.rejected_recent
        if not rej:
            return None
        reasons = sorted({r[2] for r in rej})
        weight = min(C.INTEGRITY_EACH * len(rej), C.INTEGRITY_CAP)
        return Contribution(
            id="integrity", kind="integrity", weight=weight, freshness=1.0, confidence=1.0,
            title="Messages to the system were rejected",
            text=(f"{len(rej)} node message(s) failed security checks ({', '.join(reasons)}). They were not used. "
                  "Someone may be trying to spoof a node."),
            t=max(r[0] for r in rej))

    # ------------------------------------------------------------------ evaluate
    def evaluate(self, inp: TrustInputs) -> TrustResult:
        now = inp.now
        node_c, info = self._node_contributions(inp)
        op_c = self._operator_contributions(inp)
        cust = self._customer_contribution(inp)
        rule_c = self._rule_contributions(inp)
        integ = self._integrity_contribution(inp)

        witness_c = node_c + op_c + ([cust] if cust else [])
        all_c = witness_c + rule_c + ([integ] if integ else [])
        score = sum(c.value for c in all_c)
        witness_and_rules = sum(c.value for c in witness_c + rule_c)

        age = None if inp.scada_latest is None else now - inp.scada_latest.t
        stale = age is None or age > self.profile.stale_after_s
        silent = [nid for nid, s in inp.node_silent.items() if s]
        flags: list[str] = []
        if age is None:
            flags.append("no_scada_data")
        elif stale:
            flags.append("scada_stale")
        if silent:
            flags.append("node_silent:" + ",".join(silent))
        if integ:
            flags.append("messages_rejected")

        operator_contradiction = any(c.freshness > 0 for c in op_c)
        if operator_contradiction:
            flags.append("operator_contradiction")

        if operator_contradiction or score >= C.RED_AT or (stale and witness_and_rules >= C.AMBER_AT):
            raw = "red"
        elif score >= C.AMBER_AT or stale or silent:
            raw = "amber"
        else:
            raw = "green"

        # latch: escalate immediately, clear only after ack + clean period
        if raw == "green":
            if self._clean_since is None:
                self._clean_since = now
        else:
            self._clean_since = None
        if ORDER[raw] > ORDER[self._latched]:
            self._latched = raw
            self._latched_t = now
            self._ack_t = None
        clean_for = None if self._clean_since is None else now - self._clean_since
        if (self._latched != "green" and self._ack_t is not None and clean_for is not None
                and clean_for >= self.profile.clean_period_s):
            self._latched = "green"
            self._ack_t = None
        state = raw if ORDER[raw] >= ORDER[self._latched] else self._latched
        if state != self._last_state:
            self._changed_t = now
            self._last_state = state
        cards = [c.as_dict() for c in sorted(all_c, key=lambda c: -c.value)]
        return TrustResult(
            state=state, raw_state=raw, score=round(score, 3), cards=cards, info_cards=info, flags=flags,
            stale=stale, latched=self._latched != "green", needs_ack=self._latched != "green" and self._ack_t is None,
            clean_for_s=clean_for, changed_t=self._changed_t)
