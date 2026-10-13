"""A simulated Witness Node. Sends exactly the same signed wire messages the ESP32 firmware will send.

It has an error model (channel misreads, detection delay) so tests see realistic false alarms.
The error rates here are ASSUMPTIONS, not measurements: the real numbers come from
Section V3 of the validation plan, with the real node on a real pump.
"""
from __future__ import annotations

import numpy as np

from ..security.nodesig import make_wire
from .tank_system import TankSystem


class FakeNode:
    def __init__(self, node_id: str, secret: bytes, pump_id: str, system: TankSystem, *, seed: int = 7,
                 boot: int = 1, hb_s: float = 10.0, delay_s: tuple[float, float] = (1.5, 3.5),
                 p_acc_err: float = 0.01, p_mic_err: float = 0.02,
                 hb_p_unsure_acc: float = 0.004, hb_p_unsure_mic: float = 0.01, hb_p_disagree: float = 0.002):
        self.node_id, self.secret, self.pump_id, self.sys = node_id, secret, pump_id, system
        self.rng = np.random.default_rng(seed)
        self.boot, self.seq, self.hb_s = boot, 0, hb_s
        self.delay_s, self.p_acc_err, self.p_mic_err = delay_s, p_acc_err, p_mic_err
        # A heartbeat summarizes ten one-second windows by majority vote, so single-sample misreads never
        # reach the wire. A channel only looks unsure or contradicts the other if it stays that way for the
        # whole window. These three rates are ASSUMPTIONS to be replaced by real node measurements (V3).
        self.hb_p_unsure_acc, self.hb_p_unsure_mic, self.hb_p_disagree = hb_p_unsure_acc, hb_p_unsure_mic, hb_p_disagree
        self.mode = "normal"            # normal | jammed
        self.mains_ok = True
        self._reported = system.running[pump_id]
        self._pending: tuple[bool, float] | None = None
        self._last_hb = -1e18

    def jam(self) -> None:
        self.mode = "jammed"

    def lose_mains(self) -> None:
        self.mains_ok = False

    def _channels(self, truth: bool) -> tuple[str, str]:
        def ch(err: float) -> str:
            r = self.rng.random()
            if r < err:
                return "S" if truth else "R"     # a flat wrong answer
            if r < err * 2.5:
                return "U"
            return "R" if truth else "S"
        return ch(self.p_acc_err), ch(self.p_mic_err)

    def _hb_channels(self, truth: bool) -> tuple[str, str]:
        if self.rng.random() < self.hb_p_disagree:                      # one channel stuck on the wrong answer
            return ("S" if truth else "R"), ("R" if truth else "S")
        ca = "U" if self.rng.random() < self.hb_p_unsure_acc else ("R" if truth else "S")
        cm = "U" if self.rng.random() < self.hb_p_unsure_mic else ("R" if truth else "S")
        return ca, cm

    def _wire(self, now: float, kind: str, state: bool) -> dict:
        ca, cm = self._channels(state) if kind == "ev" else self._hb_channels(state)
        if ca == cm and ca in ("R", "S"):
            st, c = ca, int(self.rng.integers(900, 975))
        elif ca == "U" and cm == "U":
            st, c = "U", 200
        elif ca == "U" or cm == "U":
            st, c = (ca if cm == "U" else cm), int(self.rng.integers(600, 760))
        else:                                   # channels disagree: say so instead of guessing
            st, c = "U", 300
        self.seq += 1
        return make_wire(self.secret, n=self.node_id, b=self.boot, s=self.seq, t_ms=int(now * 1000), k=kind,
                         st=st, c=c, ca=ca, cm=cm, p=1 if self.mains_ok else 0, f="a1b2c3d4")

    def step(self, now: float) -> list[dict]:
        out: list[dict] = []
        if self.mode == "jammed":
            return out
        truth = self.sys.running[self.pump_id]
        if truth != self._reported and self._pending is None:
            self._pending = (truth, now + float(self.rng.uniform(*self.delay_s)))
        if self._pending is not None:
            if truth == self._reported:
                self._pending = None                      # it flipped back before the node decided
            elif now >= self._pending[1]:
                self._reported = self._pending[0]
                self._pending = None
                out.append(self._wire(now, "ev", self._reported))
                self._last_hb = now
        if now - self._last_hb >= self.hb_s:
            self._last_hb = now
            out.append(self._wire(now, "hb", self._reported))
        return out
