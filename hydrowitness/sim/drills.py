"""Drill Mode (project document section 5.8): the real engines, the real playbook, scripted attacks.

Each scenario runs against the simulated plant. The simulation clock is injected into the
core, so a drill can run faster than real time. Drill cores keep their own in-memory evidence
log, so drills never pollute the live log.

Scenarios (1-3 are the Must set, 4-6 the Should set):
  1 frozen_screen           PLC lockout: the screen stops updating
  2 forced_off_spoofed      pumps forced off while a careful forgery shows them running
  3 colorado_style          alarms off, remote access cut, pump cycles changed
  4 replay                  the screen replays data recorded 30 minutes earlier
  5 hidden_valve_closure    a valve closes out of sight; only customers notice
  6 power_loss_lookalike    a power outage that looks like an attack
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..demo import DEMO_SECRETS, demo_profile
from ..schemas import ManualReading, ScadaSnapshot, SiteProfile
from ..service import HydroWitnessCore
from .fake_node import FakeNode
from .tank_system import ScadaView, TankSystem

T0 = 1_800_000_000.0 - 7 * 3600     # 01:00 local in the demo profile


@dataclass
class Event:
    at_s: float                       # seconds after the attack starts
    label: str
    fn: Callable[["DrillRunner"], None]
    operator: bool = False            # only runs when a scripted operator is enabled


@dataclass
class Scenario:
    id: str
    name: str
    summary: str
    events: list[Event] = field(default_factory=list)
    expect_red_without_operator: bool = True


def _manual(r: "DrillRunner", kind: str, value, pump_id=None, address=None, operator="drill operator"):
    r.core.ingest_manual(ManualReading(t=r.sys.t, kind=kind, value=value, pump_id=pump_id,
                                       operator=operator, address=address))


def _scenarios() -> dict[str, Scenario]:
    s: dict[str, Scenario] = {}

    s["frozen_screen"] = Scenario(
        "frozen_screen", "Frozen screen (PLC lockout)",
        "Attackers lock the operator out and the screen stops updating. Then the pump quietly stops.",
        [Event(0, "Screen freezes (identical values, new timestamps)", lambda r: r.view.freeze()),
         Event(45, "Pump 2 stops under attacker-changed logic", lambda r: r.sys.force_pump("P2", False)),
         Event(180, "Operator walks to Pump 2 and sees it stopped",
               lambda r: _manual(r, "pump_running", False, "P2"), operator=True)])

    s["forced_off_spoofed"] = Scenario(
        "forced_off_spoofed", "Pumps forced off behind a careful forgery",
        "The screen looks alive, noisy and self-consistent, and says Pump 2 is running. It was forced off.",
        [Event(0, "Forgery starts and Pump 2 is forced off",
               lambda r: (r.view.spoof({"P1": False, "P2": True}), r.sys.force_pump("P2", False))),
         Event(120, "Operator walks to Pump 2 and sees it stopped",
               lambda r: _manual(r, "pump_running", False, "P2"), operator=True)],
        expect_red_without_operator=False)

    s["colorado_style"] = Scenario(
        "colorado_style", "Alarms off, remote access cut, pump cycles changed",
        "Modeled on the late-August Colorado reports: the screen goes dark while pumps are cycled.",
        [Event(0, "Remote access cut: no more SCADA data", lambda r: r.view.dark()),
         Event(30, "Pump 2 switched off", lambda r: r.sys.force_pump("P2", False)),
         Event(60, "Pump 1 switched on", lambda r: r.sys.force_pump("P1", True)),
         Event(180, "Pump 1 switched off", lambda r: r.sys.force_pump("P1", False)),
         Event(240, "Pump 1 switched on again", lambda r: r.sys.force_pump("P1", True)),
         Event(600, "Operator drives out and reads the tank",
               lambda r: _manual(r, "tank_level_ft", round(r.sys.level_ft, 2)), operator=True)])

    s["replay"] = Scenario(
        "replay", "Replayed data",
        "The screen replays recorded normal data from 30 minutes earlier while Pump 2 is stopped.",
        [Event(0, "Replay starts", lambda r: r.view.replay(1800)),
         Event(30, "Pump 2 stopped", lambda r: r.sys.force_pump("P2", False)),
         Event(120, "Operator walks to Pump 2 and sees it stopped",
               lambda r: _manual(r, "pump_running", False, "P2"), operator=True)])

    s["hidden_valve_closure"] = Scenario(
        "hidden_valve_closure", "Valve closed out of sight",
        "A main valve is closed and the screen is forged. Only customers notice. The node can't see this.",
        [Event(0, "Forgery starts and a valve closes", lambda r: (
            r.view.spoof({pid: v for pid, v in r.sys.running.items()}), setattr(r.sys, "demand_scale", 0.3))),
         Event(120, "Customer call 1", lambda r: _manual(r, "customer_call", True, address="12 Oak St", operator="dispatcher")),
         Event(150, "Customer call 2", lambda r: _manual(r, "customer_call", True, address="48 Elm Ave", operator="dispatcher")),
         Event(180, "Customer call 3", lambda r: _manual(r, "customer_call", True, address="7 Mill Rd", operator="dispatcher")),
         Event(210, "Customer call 4", lambda r: _manual(r, "customer_call", True, address="3 Hill Ln", operator="dispatcher")),
         Event(400, "Operator checks the gauge at the transmitter (it reads normal)",
               lambda r: _manual(r, "gauge_psi", round(r.sys.true_pressure_psi(), 1)), operator=True)],
        expect_red_without_operator=False)

    s["power_loss_lookalike"] = Scenario(
        "power_loss_lookalike", "Power outage that looks like an attack",
        "Mains power fails: pumps stop, SCADA goes dark, the node runs on backup power. Attack or outage?",
        [Event(0, "Mains power fails", lambda r: (setattr(r.sys, "power_ok", False), r.view.dark(),
                                                  [n.lose_mains() for n in r.nodes.values()])),
         Event(300, "Operator reads the sight glass and sees Pump 2 stopped",
               lambda r: _manual(r, "pump_running", False, "P2"), operator=True)])
    s["free_play"] = Scenario(
        "free_play", "Free play (you trigger the attacks)",
        "A healthy plant. Use the attack buttons to break it in any order and watch HydroWitness react.",
        [], expect_red_without_operator=False)
    return s


SCENARIOS = _scenarios()

# actions the presenter can trigger by hand in free play (and in any other drill)
INJECT_ACTIONS = {
    "freeze": "Freeze the screen", "dark": "Cut remote access (screen goes dark)",
    "replay": "Replay data from 30 minutes ago", "spoof": "Forge the screen (hold current claims)",
    "normal": "Restore the real screen", "pump_off": "Force a pump off", "pump_on": "Force a pump on",
    "pump_auto": "Give a pump back to the PLC", "jam_node": "Jam a Witness Node (it goes silent)",
    "power_loss": "Mains power loss", "power_back": "Mains power back", "valve_close": "Close a valve out of sight",
}


class DrillRunner:
    def __init__(self, scenario_id: str, *, seed: int = 1, dt: float = 5.0, warmup_s: float = 2400.0,
                 operator_script: bool = False, profile: Optional[SiteProfile] = None,
                 on_scada: Optional[Callable[[float, Optional[ScadaSnapshot]], None]] = None):
        if scenario_id not in SCENARIOS:
            raise KeyError(scenario_id)
        self.scenario = SCENARIOS[scenario_id]
        self.profile = profile or demo_profile()
        self.dt = dt
        self.sys = TankSystem(self.profile, seed=seed, dt=dt, t0=T0)
        self.view = ScadaView(self.sys, seed=seed + 1)
        self.nodes = {
            "NA": FakeNode("NA", DEMO_SECRETS["NA"], "P1", self.sys, seed=seed + 2, hb_s=dt if dt > 10 else 10.0),
            "NB": FakeNode("NB", DEMO_SECRETS["NB"], "P2", self.sys, seed=seed + 3, hb_s=dt if dt > 10 else 10.0),
        }
        self.core = HydroWitnessCore(self.profile, DEMO_SECRETS, clock=lambda: self.sys.t, name="drill")
        self.operator_script = operator_script
        self.attack_t = float("inf") if scenario_id == "free_play" else T0 + warmup_s
        self._done: set[int] = set()
        self.timeline: list[dict] = []
        self.last_view: dict = {}
        self.elapsed = 0.0
        self.warmup_s = warmup_s
        self.on_scada = on_scada          # sees what the screen shows each step (None when it shows nothing); used by evaluation.py
        self.fast_forward(warmup_s)

    # ---- running
    def tick(self) -> dict:
        self.sys.step()
        now = self.sys.t
        snap = self.view.snapshot()
        if self.on_scada is not None:
            self.on_scada(now, snap)
        if snap is not None:
            self.core.ingest_scada(snap)
        for node in self.nodes.values():
            for wire in node.step(now):
                self.core.ingest_node(wire, now)
        for i, ev in enumerate(self.scenario.events):
            if i not in self._done and now >= self.attack_t + ev.at_s:
                self._done.add(i)
                if ev.operator and not self.operator_script:
                    continue
                ev.fn(self)
                self.timeline.append({"t": now, "after_attack_s": round(now - self.attack_t, 1), "label": ev.label})
        self.last_view = self.core.evaluate(now)
        self.elapsed += self.dt
        return self.last_view

    def fast_forward(self, seconds: float) -> dict:
        n = int(round(seconds / self.dt))
        v = {}
        for _ in range(n):
            v = self.tick()
        return v

    def advance(self, seconds: float) -> dict:
        return self.fast_forward(seconds)

    def inject(self, action: str, arg: Optional[str] = None) -> str:
        """Do something to the plant by hand. In free play the first injection starts the attack clock."""
        if action not in INJECT_ACTIONS:
            raise KeyError(action)
        if self.attack_t == float("inf"):          # free play: the clock starts at the first thing you break
            self.attack_t = self.sys.t
        pid = arg if arg in self.sys.running else None
        if action == "freeze":
            self.view.freeze()
        elif action == "dark":
            self.view.dark()
        elif action == "replay":
            self.view.replay(1800)
        elif action == "spoof":
            self.view.spoof({p: bool(v) for p, v in self.sys.running.items()})
        elif action == "normal":
            self.view.normal()
        elif action in ("pump_off", "pump_on", "pump_auto"):
            if pid is None:
                raise ValueError("choose a pump")
            self.sys.force_pump(pid, {"pump_off": False, "pump_on": True, "pump_auto": None}[action])
        elif action == "jam_node":
            node = self.nodes.get(arg or "")
            if node is None:
                raise ValueError("choose a node")
            node.jam()
        elif action == "power_loss":
            self.sys.power_ok = False
            for n in self.nodes.values():
                n.lose_mains()
        elif action == "power_back":
            self.sys.power_ok = True
            for n in self.nodes.values():
                n.mains_ok = True
        elif action == "valve_close":
            self.sys.demand_scale = 0.3
        label = INJECT_ACTIONS[action] + (f" ({arg})" if arg else "")
        self.timeline.append({"t": self.sys.t, "after_attack_s": round(self.sys.t - self.attack_t, 1), "label": label})
        return label

    @property
    def attacked(self) -> bool:
        return self.sys.t >= self.attack_t

    # ---- results
    def metrics(self) -> dict:
        recs = [r for r in self.core.log.records if r["type"] == "trust_state"]
        before = [r for r in recs if r["ts"] < self.attack_t]
        after = [r for r in recs if r["ts"] >= self.attack_t]

        def first(level):
            hit = next((r for r in after if r["content"]["to"] == level), None)
            return None if hit is None else round(hit["ts"] - self.attack_t, 1)

        acts = [r for r in self.core.log.records
                if r["type"] in ("manual_reading", "playbook_step") and r["ts"] >= self.attack_t]
        steps = [r for r in self.core.log.records if r["type"] == "playbook_step"]
        v = self.last_view or {}
        return {
            "scenario": self.scenario.id, "operator_script": self.operator_script,
            "attack_started": self.attacked, "seconds_since_attack": round(max(0.0, self.sys.t - self.attack_t), 1),
            "false_alarms_before_attack": len(before),
            "first_amber_s": first("amber"), "first_red_s": first("red"),
            "final_state": v.get("trust", {}).get("state"),
            "first_operator_action_s": None if not acts else round(acts[0]["ts"] - self.attack_t, 1),
            "playbook_steps_done": len(steps),
            "worst_case_min": v.get("clock", {}).get("worst_text"),
            "info_cards": [c["title"] for c in v.get("trust", {}).get("info_cards", [])],
        }


def run_scenario(scenario_id: str, *, seed: int = 1, after_attack_s: float = 900.0,
                 operator_script: bool = False, dt: float = 5.0) -> dict:
    r = DrillRunner(scenario_id, seed=seed, operator_script=operator_script, dt=dt)
    r.advance(after_attack_s)
    return r.metrics()


def run_normal(days: float, *, seed: int = 1, dt: float = 10.0, profile: Optional[SiteProfile] = None) -> dict:
    """Normal operation only: counts false ambers and reds."""
    p = profile or demo_profile()
    sys = TankSystem(p, seed=seed, dt=dt, t0=T0)
    view = ScadaView(sys, seed=seed + 1)
    nodes = [FakeNode(n, DEMO_SECRETS[n], pid, sys, seed=seed + 2 + i, hb_s=10.0)
             for i, (n, pid) in enumerate((("NA", "P1"), ("NB", "P2")))]
    core = HydroWitnessCore(p, DEMO_SECRETS, clock=lambda: sys.t, name="normal")
    steps = int(days * 86400 / dt)
    truth_changes, last = 0, dict(sys.running)
    for _ in range(steps):
        sys.step()
        truth_changes += sum(1 for k, v in sys.running.items() if last[k] != v)
        last = dict(sys.running)
        core.ingest_scada(view.snapshot())
        for n in nodes:
            for w in n.step(sys.t):
                core.ingest_node(w, sys.t)
        core.evaluate(sys.t)
    recs = [r for r in core.log.records if r["type"] == "trust_state"]
    amber = [r for r in recs if r["content"]["to"] == "amber"]
    red = [r for r in recs if r["content"]["to"] == "red"]
    return {"days": days, "false_amber": len(amber), "false_red": len(red),
            "first_events": [(round((r["ts"] - T0) / 3600, 2), r["content"]["to"], r["content"]["cards"],
                              r["content"]["flags"]) for r in recs[:5]],
            "pump_state_changes": truth_changes,
            "node_state_reports": sum(1 for r in core.log.records if r["type"] == "node_state")}
