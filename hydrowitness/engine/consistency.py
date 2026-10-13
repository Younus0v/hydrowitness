"""Consistency Checker (project document section 5.4).

Seven rules, each returning a score and a one-sentence explanation. They look only at what
SCADA says about itself, so they catch sloppy or partial forgeries. A perfectly consistent
forgery gets past all of them: that gap is exactly what the independent witnesses are for.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .. import config as C
from ..schemas import GAL_PER_FT3, PSI_TO_FT, Command, ScadaSnapshot, SiteProfile


@dataclass
class RuleResult:
    rule_id: str
    name: str
    severity: float
    fired: bool
    explanation: str = ""
    since: Optional[float] = None
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class QuietTimeLearner:
    """Learns, per tag, how long a value may legitimately stay identical.

    Many sites report only on change, so a fixed limit would raise false alarms.
    allowed = max(floor, 3 x the longest identical run seen while everything was trusted).
    """

    def __init__(self, floor_s: float = 90.0, mult: float = 3.0):
        self.floor_s, self.mult = floor_s, mult
        self.max_run: dict[str, float] = {}
        self.run: dict[str, list] = {}  # tag -> [value, start_t, last_t]

    def observe(self, tag: str, value: Optional[float], t: float, learn: bool) -> None:
        if value is None:
            self.run.pop(tag, None)
            return
        v = round(float(value), 4)
        cur = self.run.get(tag)
        if cur is None:
            self.run[tag] = [v, t, t]
        elif cur[0] == v:
            cur[2] = t
        else:
            if learn:
                self.max_run[tag] = max(self.max_run.get(tag, 0.0), cur[2] - cur[1])
            self.run[tag] = [v, t, t]

    def current_run_s(self, tag: str) -> float:
        cur = self.run.get(tag)
        return 0.0 if cur is None else cur[2] - cur[1]

    def run_start(self, tag: str) -> Optional[float]:
        cur = self.run.get(tag)
        return None if cur is None else cur[1]

    def allowed_s(self, tag: str) -> float:
        return max(self.floor_s, self.mult * self.max_run.get(tag, 0.0))


class ConsistencyChecker:
    WINDOW_S = 3600.0

    def __init__(self, profile: SiteProfile, *, replay_k: int = 10):
        self.profile = profile
        self.snaps: deque[ScadaSnapshot] = deque()
        self.quiet = QuietTimeLearner()
        self.commands: dict[tuple, Command] = {}
        self.replay_k = replay_k
        self._tuples: list[tuple[float, tuple]] = []
        self._kgram_index: dict[tuple, int] = {}
        self._replay_last_t: Optional[float] = None
        self._replay_since: Optional[float] = None
        self._mb_since: Optional[float] = None
        self._pf_since: dict[str, float] = {}
        self._sh_since: Optional[float] = None

    # ------------------------------------------------------------------ ingest
    def ingest(self, snap: ScadaSnapshot, learn: bool) -> None:
        self.snaps.append(snap)
        while self.snaps and snap.t - self.snaps[0].t > self.WINDOW_S:
            self.snaps.popleft()
        for cmd in snap.commands:
            self.commands[(cmd.t, cmd.pump_id, cmd.action)] = cmd
        self._observe_quiet(snap, learn)
        self._observe_replay(snap)

    def _frozen_tags(self, snap: ScadaSnapshot) -> dict[str, Optional[float]]:
        tags: dict[str, Optional[float]] = {
            "tank_level_ft": snap.tank_level_ft,
            "pressure_psi": snap.pressure_psi,
            "demand_gpm": snap.demand_gpm,
        }
        for pid, flow in snap.pump_flow_gpm.items():
            if snap.pump_status.get(pid):
                tags[f"flow:{pid}"] = flow  # a stopped pump's zero flow is legitimately constant
        return tags

    def _observe_quiet(self, snap: ScadaSnapshot, learn: bool) -> None:
        tags = self._frozen_tags(snap)
        for tag, val in tags.items():
            self.quiet.observe(tag, val, snap.t, learn)
        for tag in [k for k in self.quiet.run if k.startswith("flow:") and k not in tags]:
            self.quiet.run.pop(tag, None)

    def _observe_replay(self, snap: ScadaSnapshot) -> None:
        vals = tuple(round(v, 3) for v in (snap.tank_level_ft, snap.pressure_psi, snap.demand_gpm) if v is not None)
        if len(vals) < 2:
            return
        if self._tuples and self._tuples[-1][1] == vals:
            return  # consecutive duplicates are the frozen-data rule's business
        self._tuples.append((snap.t, vals))
        n, k = len(self._tuples), self.replay_k
        if n >= k:
            key = tuple(x[1] for x in self._tuples[n - k:])
            first = self._kgram_index.get(key)
            if first is not None and first <= n - 1 - k:
                prev_last = self._replay_last_t
                self._replay_last_t = snap.t
                if prev_last is None or snap.t - prev_last > 300:
                    self._replay_since = self._tuples[n - k][0]
            else:
                self._kgram_index.setdefault(key, n - k)
        if n > 60000:
            drop = n - 30000
            self._tuples = self._tuples[drop:]
            self._kgram_index = {}
            for i in range(0, len(self._tuples) - k + 1):
                self._kgram_index.setdefault(tuple(x[1] for x in self._tuples[i:i + k]), i)

    # ---------------------------------------------------------------- evaluate
    def evaluate(self, now: float) -> list[RuleResult]:
        return [
            self._rule_mass_balance(now),
            self._rule_pump_flow_tank(now),
            self._rule_static_head(now),
            self._rule_plausibility(now),
            self._rule_command_response(now),
            self._rule_frozen(now),
            self._rule_replay(now),
        ]

    def _window(self, now: float, seconds: float) -> list[ScadaSnapshot]:
        return [s for s in self.snaps if now - s.t <= seconds]

    # 1 ------------------------------------------------------------------------
    def _rule_mass_balance(self, now: float) -> RuleResult:
        r = RuleResult("mass_balance", "Tank mass balance", C.SEV_MASS_BALANCE, False)
        win = [s for s in self._window(now, 600) if s.tank_level_ft is not None and s.demand_gpm is not None]
        if len(win) < 12 or win[-1].t - win[0].t < 240:
            self._mb_since = None
            return r
        n_end = max(3, min(12, len(win) // 4))
        a, b = win[:n_end], win[-n_end:]
        la = statistics.fmean(s.tank_level_ft for s in a)
        lb = statistics.fmean(s.tank_level_ft for s in b)
        ta, tb = statistics.fmean(s.t for s in a), statistics.fmean(s.t for s in b)
        dt_min = (tb - ta) / 60.0
        if dt_min <= 0:
            return r
        inflow = statistics.fmean(sum(s.pump_flow_gpm.values()) for s in win)
        demand = statistics.fmean(s.demand_gpm for s in win)
        dv_rep = (lb - la) * self.profile.tank_area_sqft * GAL_PER_FT3
        dv_exp = (inflow - demand) * dt_min
        resid = (dv_rep - dv_exp) / dt_min
        limit = max(0.2 * self.profile.peak_demand_gpm, 40.0)
        if abs(resid) > limit:
            self._mb_since = self._mb_since if self._mb_since is not None else now
            if now - self._mb_since >= 180:
                r.fired = True
                r.since = self._mb_since
                r.explanation = (f"Tank level changed by {lb - la:+.2f} ft but reported flows imply "
                                 f"{dv_exp / (self.profile.tank_area_sqft * GAL_PER_FT3):+.2f} ft "
                                 f"(unexplained {resid:+.0f} gpm).")
        else:
            self._mb_since = None
        return r

    # 2 ------------------------------------------------------------------------
    def _rule_pump_flow_tank(self, now: float) -> RuleResult:
        r = RuleResult("pump_flow_tank", "Pump, flow and tank agreement", C.SEV_PUMP_FLOW, False)
        if not self.snaps:
            return r
        s = self.snaps[-1]
        problems, since_list = [], []
        for pump in self.profile.pumps:
            st, fl = s.pump_status.get(pump.id), s.pump_flow_gpm.get(pump.id)
            if st is None or fl is None:
                self._pf_since.pop(pump.id, None)
                continue
            bad = None
            if st is False and fl > max(5.0, 0.05 * pump.rated_gpm):
                bad = f"{pump.id} shown STOPPED but reports {fl:.0f} gpm"
            elif st is True and fl < 0.1 * pump.rated_gpm:
                bad = f"{pump.id} shown RUNNING but reports {fl:.0f} gpm"
            if bad:
                start = self._pf_since.setdefault(pump.id, now)
                if now - start >= 30:
                    problems.append(bad)
                    since_list.append(start)
            else:
                self._pf_since.pop(pump.id, None)
        # all pumps stopped, zero inflow, yet the tank is rising
        stopped = [p for p in self.profile.pumps if s.pump_status.get(p.id) is False]
        if self.profile.pumps and len(stopped) == len(self.profile.pumps):
            win = self._window(now, 300)
            lv = [x for x in win if x.tank_level_ft is not None]
            if len(lv) >= 12 and lv[-1].t - lv[0].t >= 240:
                half = len(lv) // 4 or 1
                rise = statistics.fmean(x.tank_level_ft for x in lv[-half:]) - statistics.fmean(x.tank_level_ft for x in lv[:half])
                if rise / ((lv[-1].t - lv[0].t) / 60.0) > 0.02:
                    problems.append(f"all pumps shown STOPPED but tank level rose {rise:.2f} ft")
                    since_list.append(lv[0].t)
        if problems:
            r.fired, r.explanation, r.since = True, "; ".join(problems) + ".", min(since_list)
        return r

    # 3 ------------------------------------------------------------------------
    def _rule_static_head(self, now: float) -> RuleResult:
        r = RuleResult("static_head", "Level and pressure move together", C.SEV_STATIC_HEAD, False)
        tap = self.profile.pressure_tap_elev_ft
        if tap is None or not self.snaps:
            return r
        s = self.snaps[-1]
        if s.tank_level_ft is None or s.pressure_psi is None:
            self._sh_since = None
            return r
        expected = (self.profile.tank_floor_elev_ft + s.tank_level_ft - tap) / PSI_TO_FT
        tol = 3.0 + 0.1 * abs(expected) + self.profile.headloss_margin_ft / PSI_TO_FT
        if abs(s.pressure_psi - expected) > tol:
            self._sh_since = self._sh_since if self._sh_since is not None else now
            if now - self._sh_since >= 60:
                r.fired, r.since = True, self._sh_since
                r.explanation = (f"Pressure {s.pressure_psi:.1f} psi doesn't match tank level "
                                 f"{s.tank_level_ft:.1f} ft (expected about {expected:.1f} psi).")
        else:
            self._sh_since = None
        return r

    # 4 ------------------------------------------------------------------------
    def _rule_plausibility(self, now: float) -> RuleResult:
        r = RuleResult("plausibility", "Physically possible rates", C.SEV_PLAUSIBILITY, False)
        if not self.snaps:
            return r
        problems = []
        s = self.snaps[-1]
        for pump in self.profile.pumps:
            fl = s.pump_flow_gpm.get(pump.id)
            if fl is not None and fl > 1.3 * pump.rated_gpm:
                problems.append(f"{pump.id} reports {fl:.0f} gpm, above 130% of its rating")
        lv_now = [x.tank_level_ft for x in self._window(now, 30) if x.tank_level_ft is not None]
        lv_old = [x.tank_level_ft for x in self.snaps
                  if 120 <= now - x.t <= 150 and x.tank_level_ft is not None]
        if len(lv_now) >= 3 and len(lv_old) >= 3:
            rate = abs(statistics.fmean(lv_now) - statistics.fmean(lv_old)) / 2.0  # ft per minute over ~120 s
            max_gpm = sum(p.rated_gpm for p in self.profile.pumps) + 1.5 * self.profile.peak_demand_gpm
            max_rate = max_gpm / (self.profile.tank_area_sqft * GAL_PER_FT3)
            if rate > 1.2 * max_rate + 0.01:
                problems.append(f"tank level moving {rate:.2f} ft/min, faster than the equipment can")
        if problems:
            r.fired, r.explanation, r.since = True, "; ".join(problems) + ".", now
        return r

    # 5 ------------------------------------------------------------------------
    def _rule_command_response(self, now: float) -> RuleResult:
        r = RuleResult("command_response", "Command, then response", C.SEV_COMMAND_RESPONSE, False)
        delay = self.profile.command_response_s
        problems, since = [], []
        for cmd in self.commands.values():
            if not (delay <= now - cmd.t <= 600):
                continue
            after = [x for x in self.snaps if cmd.t + delay <= x.t <= cmd.t + delay + 60]
            if not after:
                continue
            st = after[0].pump_status.get(cmd.pump_id)
            if st is None:
                continue
            want = cmd.action == "START"
            if st != want:
                problems.append(f"{cmd.pump_id} was commanded {cmd.action} but SCADA still shows "
                                f"{'RUNNING' if st else 'STOPPED'} {int(delay)} s later")
                since.append(cmd.t + delay)
        if problems:
            r.fired, r.explanation, r.since = True, "; ".join(problems) + ".", min(since)
        return r

    # 6 ------------------------------------------------------------------------
    def _rule_frozen(self, now: float) -> RuleResult:
        r = RuleResult("frozen_data", "Frozen data", C.SEV_FROZEN, False)
        frozen, since = [], []
        for tag in list(self.quiet.run):
            run_s = self.quiet.current_run_s(tag)
            if run_s > self.quiet.allowed_s(tag):
                frozen.append((tag, run_s))
                since.append(self.quiet.run_start(tag))
        if frozen:
            r.fired = True
            r.tags = [t for t, _ in frozen]
            r.since = min(since)
            r.explanation = "No change, not even sensor noise: " + ", ".join(
                f"{t} for {int(s)} s" for t, s in frozen) + "."
        return r

    # 7 ------------------------------------------------------------------------
    def _rule_replay(self, now: float) -> RuleResult:
        r = RuleResult("replay", "Repeated identical sequence", C.SEV_REPLAY, False)
        if self._replay_last_t is not None and now - self._replay_last_t <= 120:
            r.fired = True
            r.since = self._replay_since
            r.explanation = (f"The last {self.replay_k} readings exactly repeat an earlier sequence: "
                             "real sensors don't repeat to the last digit.")
        return r
