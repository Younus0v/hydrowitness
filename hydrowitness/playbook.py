"""Lockout Playbook (project document section 5.6).

Starts automatically at amber or red (an operator can also start it by hand). Every step has
text, an owner role, a time target expressed against the countdown clock, and a done button
that logs who and when.

This is a decision aid and a TEMPLATE. It gives no regulatory advice. The utility's own
emergency response plan and state rules come first. Review it with your coach, a real
operator and the state drinking-water program before relying on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PHASES = ["Confirm", "Contain", "Prepare", "Recover"]
DISCLAIMER = ("Template only. Follow your utility's own emergency response plan and your state's rules. "
              "This checklist gives no regulatory advice.")


@dataclass
class Step:
    id: str
    phase: str
    text: str
    owner: str
    target_min: Optional[float] = None        # absolute target, minutes after the playbook starts
    target_share: Optional[float] = None      # target as a share of the starting worst-case clock
    trigger_share: Optional[float] = None     # becomes due when the clock falls to this share left
    done_by: Optional[str] = None
    done_t: Optional[float] = None


def default_steps() -> list[Step]:
    return [
        Step("c1", "Confirm", "Read the physical pressure gauge and the tank sight glass at the site and enter both readings.",
             "Operator", target_min=5),
        Step("c2", "Confirm", "Check the Witness Node lights at the pump and compare them with what the screen says.",
             "Operator", target_min=5),
        Step("c3", "Confirm", "Call a second person and tell them what you see.", "Operator", target_min=5),
        Step("k1", "Contain", "Take internet-exposed controllers and remote access off the network, following the utility's own plan.",
             "Manager", target_share=0.10),
        Step("k2", "Contain", "Switch to manual operation where it is safe to do so.", "Operator", target_share=0.10),
        Step("p1", "Prepare", "Send a crew to the pump station and alert the manager.", "Manager", trigger_share=0.50),
        Step("p2", "Prepare", "Contact the state drinking-water program, as your plan requires.", "Manager", trigger_share=0.50),
        Step("p3", "Prepare", "Prepare the precautionary notice, as your plan requires.", "Manager", trigger_share=0.25),
        Step("r1", "Recover", "Verify equipment state physically before restoring remote control.", "Operator"),
        Step("r2", "Recover", "Record the sequence: export the evidence log and verify its chain.", "Manager"),
    ]


class Playbook:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.started_t: Optional[float] = None
        self.start_worst_min: Optional[float] = None
        self.started_by = ""
        self.steps = default_steps()

    def start(self, now: float, start_worst_min: Optional[float], by: str = "automatic") -> bool:
        if self.active:
            return False
        self.reset()
        self.active, self.started_t, self.started_by = True, now, by
        self.start_worst_min = start_worst_min if start_worst_min else 480.0
        return True

    def complete(self, step_id: str, operator: str, now: float) -> Optional[Step]:
        for s in self.steps:
            if s.id == step_id and s.done_t is None:
                s.done_by, s.done_t = operator, now
                return s
        return None

    def _phase_done(self, phase: str) -> bool:
        return all(s.done_t is not None for s in self.steps if s.phase == phase)

    def status(self, now: float, worst_now_min: Optional[float]) -> dict:
        if not self.active:
            return {"active": False, "disclaimer": DISCLAIMER, "phases": []}
        start = self.start_worst_min or 480.0
        elapsed_min = (now - self.started_t) / 60.0
        share_left = None if worst_now_min is None else max(0.0, min(1.0, worst_now_min / start))
        phases = []
        for ph in PHASES:
            steps = []
            for s in (x for x in self.steps if x.phase == ph):
                if s.done_t is not None:
                    st = "done"
                elif s.target_min is not None:
                    st = "overdue" if elapsed_min > s.target_min else "due"
                elif s.target_share is not None:
                    st = "overdue" if elapsed_min > s.target_share * start else "due"
                elif s.trigger_share is not None:
                    st = "due" if (share_left is not None and share_left <= s.trigger_share) else "upcoming"
                else:  # Recover
                    st = "due" if self._phase_done("Contain") else "upcoming"
                steps.append({
                    "id": s.id, "text": s.text, "owner": s.owner, "status": st,
                    "done_by": s.done_by, "done_t": s.done_t,
                    "target": (f"within {s.target_min:.0f} min" if s.target_min is not None else
                               f"within {s.target_share * start:.0f} min" if s.target_share is not None else
                               f"when {int(s.trigger_share * 100)}% of the clock is left" if s.trigger_share is not None else ""),
                })
            phases.append({"name": ph, "steps": steps, "done": all(x["status"] == "done" for x in steps)})
        current = next((p["name"] for p in phases if not p["done"]), "Recover")
        return {"active": True, "started_t": self.started_t, "started_by": self.started_by,
                "start_worst_min": start, "elapsed_min": round(elapsed_min, 1), "current_phase": current,
                "phases": phases, "disclaimer": DISCLAIMER}
