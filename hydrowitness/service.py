"""HydroWitnessCore: wires the engines, node checks, evidence log, playbook and alerts together.

The core is clock-injectable (``clock``) so drills and tests can run faster than real time.
It is READ-ONLY toward the plant: no code path writes to a pump, valve or PLC.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional

from . import config as C
from .alerts import AlertManager
from .engine import evidence_checks, next_check, quantities
from .engine.consistency import ConsistencyChecker
from .engine.time_to_trouble import (ClockResult, CreditedFlow, LevelReading, clock_text, compute_clock,
                                     format_minutes, l_min_ft)
from .engine.trust import ORDER, TrustEngine, TrustInputs, fmt_t
from .evidence_log import EvidenceLog
from .playbook import Playbook
from .schemas import ManualReading, ScadaSnapshot, SiteProfile
from .security.nodesig import NodeRegistry


class HydroWitnessCore:
    def __init__(self, profile: SiteProfile, node_secrets: Optional[dict[str, bytes]] = None, *,
                 clock: Callable[[], float] = time.time, log: Optional[EvidenceLog] = None,
                 alert_sinks: Optional[list] = None, name: str = "live", deferred_alerts: bool = False):
        self.profile = profile
        self.name = name
        self.clock = clock
        self.lock = threading.RLock()
        self.registry = NodeRegistry(node_secrets or {}, heartbeat_interval_s=profile.heartbeat_interval_s)
        self.checker = ConsistencyChecker(profile)
        self.trust = TrustEngine(profile)
        self.log = log or EvidenceLog()
        self.playbook = Playbook()
        self.alerts = AlertManager(alert_sinks or [], deferred=deferred_alerts)
        self.manual: list[ManualReading] = []
        self.snaps: deque[ScadaSnapshot] = deque(maxlen=7200)
        self.claim_since: dict[str, float] = {}
        self._claims: dict[str, Optional[bool]] = {}
        self.green_levels: deque[tuple[float, float]] = deque(maxlen=4000)
        self.baseline: Optional[LevelReading] = None
        self.state = "green"
        self._last_digest_t = -1e18
        self._rej_logged: dict[str, float] = {}
        self.last_result = None
        self.alerts_enabled = True      # the live app turns this off until the first real data arrives
        self._new_readings: list[ManualReading] = []     # readings since the last evaluation, for "what changed"
        self._prev_summary: Optional[dict] = None
        self.last_update: Optional[dict] = None

    # ---------------------------------------------------------------- ingest
    def ingest_scada(self, snap: ScadaSnapshot) -> None:
        with self.lock:
            learn = self.state == "green"
            self.snaps.append(snap)
            self.checker.ingest(snap, learn)
            for pid, st in snap.pump_status.items():
                if st is None:
                    continue
                if pid in self._claims and self._claims[pid] != st:
                    self.claim_since[pid] = snap.t
                    self.log.append("scada", "claim_change", {"pump": pid, "now": "RUNNING" if st else "STOPPED"}, snap.t)
                elif pid not in self._claims:
                    self.claim_since[pid] = float("-inf")
                self._claims[pid] = st
            if self.state == "green" and snap.tank_level_ft is not None:
                self.green_levels.append((snap.t, snap.tank_level_ft))
            if snap.t - self._last_digest_t >= 300:
                self._last_digest_t = snap.t
                self.log.append("scada", "digest", {
                    "tank_level_ft": snap.tank_level_ft, "pump_status": snap.pump_status,
                    "demand_gpm": snap.demand_gpm, "pressure_psi": snap.pressure_psi}, snap.t)

    def ingest_node(self, wire: dict, now: Optional[float] = None) -> dict:
        with self.lock:
            now = self.clock() if now is None else now
            ok, reason, msg = self.registry.accept(wire, now)
            if ok and msg is not None:
                nid = msg.node_id
                events = self.registry.nodes[nid].events
                if events and events[-1][0] == msg.ts:
                    self.log.append(f"node:{nid}", "node_state", {
                        "state": msg.state, "confidence": msg.confidence, "acc": msg.acc_state,
                        "mic": msg.mic_state, "mains_ok": msg.mains_ok, "seq": msg.seq}, msg.ts)
            else:
                key = f"{wire.get('n', '?') if isinstance(wire, dict) else '?'}:{reason}"
                if now - self._rej_logged.get(key, -1e18) >= 10:
                    self._rej_logged[key] = now
                    self.log.append("security", "node_message_rejected", {"reason": reason, "key": key}, now)
            return {"accepted": ok, "reason": reason}

    def ingest_manual(self, reading: ManualReading) -> None:
        with self.lock:
            self.manual.append(reading)
            self._new_readings.append(reading)
            if len(self.manual) > 2000:
                self.manual = self.manual[-1000:]
            self.log.append(f"operator:{reading.operator}", "manual_reading", reading.model_dump(), reading.t)

    def acknowledge(self, operator: str = "operator", now: Optional[float] = None) -> bool:
        with self.lock:
            now = self.clock() if now is None else now
            ok = self.trust.acknowledge(now)
            if ok:
                self.log.append(f"operator:{operator}", "acknowledged", {"state": self.state}, now)
            return ok

    def complete_step(self, step_id: str, operator: str = "operator", now: Optional[float] = None) -> bool:
        with self.lock:
            now = self.clock() if now is None else now
            step = self.playbook.complete(step_id, operator, now)
            if step:
                self.log.append(f"operator:{operator}", "playbook_step", {"step": step.id, "text": step.text}, now)
            return step is not None

    def start_playbook(self, operator: str = "operator", now: Optional[float] = None) -> bool:
        with self.lock:
            now = self.clock() if now is None else now
            res = self.last_result or self.evaluate(now)
            ck = res["clock"]
            basis = "unavailable" if not ck.get("ok") else ("not_falling" if ck.get("worst_min") is None else None)
            started = self.playbook.start(now, ck.get("worst_min"), by=operator, basis=basis)
            if started:
                self.log.append(f"operator:{operator}", "playbook_started", {"manual": True}, now)
            return started

    # ----------------------------------------------------------------- lookups
    def _scada_near(self, t: float) -> Optional[ScadaSnapshot]:
        best, best_d = None, 120.0
        for s in reversed(self.snaps):
            d = abs(s.t - t)
            if d < best_d:
                best, best_d = s, d
            if s.t < t - 120:
                break
        return best

    def _credits(self, now: float) -> list[CreditedFlow]:
        out: list[CreditedFlow] = []
        latest: dict[str, ManualReading] = {}
        for r in self.manual:
            if r.kind == "flow_confirmed" and r.pump_id:
                if r.pump_id not in latest or r.t > latest[r.pump_id].t:
                    latest[r.pump_id] = r
        for pid, r in latest.items():
            pump = self.profile.pump(pid)
            if pump is None or not r.value or now - r.t > self.profile.flow_credit_ttl_s:
                continue
            if pump.node_id:
                msg = self.registry.latest(pump.node_id)
                since = self.registry.nodes[pump.node_id].state_since
                if msg and msg.state == "STOPPED" and msg.confidence >= 0.6 and since and since > r.t:
                    continue  # the pump stopped after it was confirmed: credit withdrawn
            out.append(CreditedFlow(pid, pump.rated_gpm * C.INFLOW_SAFETY_FACTOR, r.t, f"confirmed by {r.operator}"))
        return out

    def _choose_level(self, now: float, state: str) -> Optional[LevelReading]:
        op = None
        for r in self.manual:
            if r.kind == "tank_level_ft" and r.value is not None and (op is None or r.t > op.t):
                op = r
        op_level = LevelReading(float(op.value), op.t, "operator") if op and now - op.t <= 3 * 3600 else None
        latest = self.snaps[-1] if self.snaps else None
        scada = None
        if latest and latest.tank_level_ft is not None and now - latest.t <= self.profile.stale_after_s:
            scada = LevelReading(latest.tank_level_ft, latest.t, "scada")
        if state == "green":
            cands = [x for x in (scada, op_level) if x]
            return max(cands, key=lambda x: x.t) if cands else None
        if op_level:
            return op_level
        if self.baseline:
            return self.baseline
        if latest and latest.tank_level_ft is not None:
            return LevelReading(latest.tank_level_ft, latest.t, "scada_untrusted")
        return None

    def _set_baseline(self, res_cards: list[dict], rules) -> None:
        anchors = [r.since for r in rules if r.fired and r.since] + [c["t"] for c in res_cards if c.get("t")]
        anchor = min(anchors) if anchors else None
        pick = None
        for t, lv in reversed(self.green_levels):
            if anchor is None or t <= anchor:
                pick = (t, lv)
                break
        if pick is None and self.green_levels:
            pick = self.green_levels[-1]
        self.baseline = LevelReading(pick[1], pick[0], "scada_last_trusted") if pick else None

    # ----------------------------------------------------------------- evaluate
    def evaluate(self, now: Optional[float] = None) -> dict:
        with self.lock:
            now = self.clock() if now is None else now
            p = self.profile
            latest = self.snaps[-1] if self.snaps else None
            rules = self.checker.evaluate(now)
            node_ids = p.node_ids()
            inp = TrustInputs(
                now=now, profile=p, scada_latest=latest, scada_near=self._scada_near, rules=rules,
                node_msgs={n: self.registry.latest(n) for n in node_ids},
                node_state_since={n: self.registry.nodes[n].state_since for n in node_ids},
                node_silent={n: self.registry.silent(n, now) for n in node_ids},
                claim_since=dict(self.claim_since),
                manual=[r for r in self.manual if now - r.t <= max(C.FRESHNESS_S, C.CUSTOMER_WINDOW_S)],
                rejected_recent=self.registry.rejected_recent(now, C.INTEGRITY_WINDOW_S),
            )
            tr = self.trust.evaluate(inp)
            prev = self.state
            if tr.state != prev:
                if prev == "green":
                    self._set_baseline(tr.cards, rules)
                if tr.state == "green":
                    self.baseline = None
                self.state = tr.state
                self.log.append("trust", "trust_state", {
                    "from": prev, "to": tr.state, "score": tr.score, "flags": tr.flags,
                    "cards": [c["title"] for c in tr.cards[:6]]}, now)
                if tr.state == "red":
                    self.log.anchor(now, "red")
            self.log.maybe_daily_anchor(now)

            level = self._choose_level(now, self.state)
            credits = self._credits(now)
            clock = compute_clock(p, now, level, credits)

            if self.state != "green" and not self.playbook.active:
                self.playbook.start(now, clock.worst_min,
                                    basis="unavailable" if not clock.ok else ("not_falling" if clock.worst_min is None else None))
                self.log.append("playbook", "playbook_started", {"manual": False, "state": self.state}, now)
            if self.state == "green" and self.playbook.active and not tr.latched:
                self.playbook.reset()
                self.log.append("playbook", "playbook_closed", {}, now)

            headline = (tr.cards[0]["title"] if tr.cards else ", ".join(tr.flags) or "state changed")
            headline += f". Worst case: {clock_text(clock, 'worst')}."
            if self.alerts_enabled:
                self.alerts.on_state(now, prev, self.state, p.site_name, headline, p.emergency_contacts)
                self.alerts.tick(now, tr.needs_ack, p.site_name, headline, p.emergency_contacts)

            view = self._view(now, tr, clock, rules, level, credits)
            self.last_result = view
            return view

    # ------------------------------------------------------------------ what a new reading changed
    @staticmethod
    def _describe_reading(r: ManualReading) -> str:
        who = r.operator or "operator"
        if r.kind == "tank_level_ft":
            return f"{who} read the tank at {float(r.value):.1f} ft"
        if r.kind == "gauge_psi":
            return f"{who} read a gauge at {float(r.value):.1f} psi"
        if r.kind == "pump_running":
            return f"{who} saw {r.pump_id} {'RUNNING' if r.value else 'STOPPED'}"
        if r.kind == "flow_confirmed":
            return f"{who} {'confirmed water is moving from' if r.value else 'found no water moving from'} {r.pump_id}"
        if r.kind == "customer_call":
            return f"a customer at {r.address or 'an unknown address'} called about low pressure"
        return f"{who} entered a reading"

    def _clock_without(self, now: float, new: list[ManualReading], state: str) -> dict:
        """What the clock would say RIGHT NOW if the new readings had not arrived, with the light as it was before them.
        Comparing with this (not with the previous evaluation) keeps ordinary time passing out of "what changed"."""
        saved = self.manual
        self.manual = [r for r in saved if not any(r is n for n in new)]
        try:
            ck = compute_clock(self.profile, now, self._choose_level(now, state), self._credits(now))
        finally:
            self.manual = saved
        return {"worst_text": clock_text(ck, "worst"), "expected_text": clock_text(ck, "expected"),
                "level_source_text": ck.level_source_text, "credited_gpm": ck.credited_gpm}

    def _track_update(self, now: float, summary: dict) -> Optional[dict]:
        """When readings arrived since the last evaluation, say what they changed (or that they changed nothing)."""
        new, self._new_readings = self._new_readings, []
        prev, self._prev_summary = self._prev_summary, summary
        if new and prev is not None:
            base = self._clock_without(now, new, prev["state"])
            prev = {**prev, **base}
            changes = []
            counted = prev["worst_text"] != summary["worst_text"] or prev["expected_text"] != summary["expected_text"]
            for key, label in (("worst_text", "Worst case"), ("expected_text", "Expected case")):
                if prev[key] != summary[key]:
                    changes.append(f"{label}: {prev[key]} \u2192 {summary[key]}")
            if not counted:
                changes.append(f"The countdown did not change (worst case {summary['worst_text']})")
            if prev["level_source_text"] != summary["level_source_text"] and summary["level_source_text"]:
                changes.append(f"The tank level now comes from {summary['level_source_text']}")
            if prev["state"] != summary["state"]:
                changes.append(f"The light went from {prev['state']} to {summary['state']}")
            if prev["next_id"] != summary["next_id"]:
                changes.append("The next useful check is now: " + (summary["next_title"] or "nothing"))
            if prev["credited_gpm"] != summary["credited_gpm"]:
                changes.append(f"Credited inflow: {prev['credited_gpm']:.0f} gpm \u2192 {summary['credited_gpm']:.0f} gpm")
            what = "; ".join(self._describe_reading(r) for r in new[-3:])
            what = what[:1].upper() + what[1:]
            if counted:
                head = f"{what}. That changed the countdown: worst case {prev['worst_text']} \u2192 {summary['worst_text']}."
            elif len(changes) > 1:
                head = f"{what}. The countdown stayed the same, but other things changed."
            else:
                head = f"{what}. That did not change the estimate."
            self.last_update = {
                "t": now, "readings": [self._describe_reading(r) for r in new[-3:]], "headline": head,
                "changes": changes, "before": {k: prev[k] for k in ("worst_text", "expected_text", "state")},
                "after": {k: summary[k] for k in ("worst_text", "expected_text", "state")}}
        if self.last_update is not None and now - self.last_update["t"] > 900:
            self.last_update = None
        return self.last_update

    def _view(self, now: float, tr, clock: ClockResult, rules, level=None, credits=None) -> dict:
        p = self.profile
        latest = self.snaps[-1] if self.snaps else None
        nodes = []
        for pump in p.pumps:
            if not pump.node_id:
                continue
            st = self.registry.nodes[pump.node_id]
            m = st.last_msg
            nodes.append({
                "node_id": pump.node_id, "pump_id": pump.id, "silent": self.registry.silent(pump.node_id, now),
                "state": m.state if m else None, "confidence": m.confidence if m else None,
                "acc_state": m.acc_state if m else None, "mic_state": m.mic_state if m else None,
                "mains_ok": m.mains_ok if m else None, "fw_hash": m.fw_hash if m else None,
                "last_seen_age_s": None if st.last_seen_t is None else round(now - st.last_seen_t, 1),
                "state_since": st.state_since,
            })
        clock_d = clock.as_dict()
        clock_d["worst_text"] = clock_text(clock, "worst")
        clock_d["expected_text"] = clock_text(clock, "expected")
        clock_d["l_min_ft"] = clock.l_min_ft if clock.l_min_ft is not None else (l_min_ft(p) if p.mode == "storage" else None)
        age = None if latest is None else now - latest.t
        log_ok, log_bad, log_msg = self.log.verify_cached()
        checks = evidence_checks.build(
            now=now, scada_latest=latest, tr=tr, nodes=nodes, manual=self.manual,
            log_ok=log_ok, log_bad=log_bad, log_msg=log_msg, log_count=len(self.log.records))
        qty = quantities.build(profile=p, now=now, latest=latest, nodes=nodes, manual=self.manual,
                               credits=credits or [], clock=clock, scada_near=self._scada_near,
                               scada_stale=tr.stale, trust_state=tr.state)
        nxt = next_check.choose(profile=p, now=now, clock=clock, trust_state=tr.state, quantities=qty,
                                nodes=nodes, level=level, credits=credits or [], cards=tr.cards)
        update = self._track_update(now, {
            "worst_text": clock_d["worst_text"], "expected_text": clock_d["expected_text"],
            "level_source_text": clock.level_source_text, "state": tr.state,
            "credited_gpm": clock.credited_gpm,
            "next_id": nxt["primary"]["id"] if nxt["primary"] else None,
            "next_title": nxt["primary"]["title"] if nxt["primary"] else None})
        return {
            "mode": self.name, "now": now, "now_text": fmt_t(p, now), "site": p.site_name,
            "trust": {**tr.as_dict(), "thresholds": {"amber": C.AMBER_AT, "red": C.RED_AT}},
            "clock": clock_d,
            "scada": None if latest is None else {
                "age_s": round(age, 1), "stale": tr.stale, "tank_level_ft": latest.tank_level_ft,
                "pump_status": latest.pump_status, "pump_flow_gpm": latest.pump_flow_gpm,
                "demand_gpm": latest.demand_gpm, "pressure_psi": latest.pressure_psi},
            "nodes": nodes,
            "playbook": self.playbook.status(now, clock.worst_min if clock.ok else None),
            "rules": [r.as_dict() for r in rules],
            "manual_recent": [r.model_dump() for r in self.manual[-10:]][::-1],
            "timeline": [{"seq": r["seq"], "ts": r["ts"], "time": fmt_t(p, r["ts"]), "source": r["source"],
                          "type": r["type"], "content": r["content"]} for r in self.log.tail(40)][::-1],
            "log": {"count": len(self.log.records), "head_hash": self.log.head_hash},
            "evidence_checks": checks,
            "quantities": qty,
            "next_check": nxt,
            "last_update": update,
            "alerts": self.alerts.history[-10:][::-1],
            "alert_delivery": self.alerts.delivery(now),
        }
