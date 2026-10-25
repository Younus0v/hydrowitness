"""Record the real engines running every drill, so the website can replay the real dashboard with no server.

Nothing here is a mock-up: each recording is the sequence of `/api/state` responses the real server produced while the
real DrillRunner played a scenario against the simulated plant, one frame per 5 simulated seconds. The website's
`demo/shim.js` answers the dashboard's `/api/...` calls from these frames, and `demo/app.html` is the dashboard page
itself with one script tag and a notice added.

    python -m hydrowitness.sim.web_recordings          # writes site/demo/ (about 10 s)

tests/test_web_demo.py rebuilds everything in memory and fails if the committed files are out of date.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi.encoders import jsonable_encoder

from ..api import AppState, VERSION
from ..demo import DEMO_SECRETS, demo_profile
from .drills import DrillRunner, SCENARIOS
from .truth import TRUTH_HORIZON_H, minutes_to_line

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "site" / "demo"
DASHBOARD = ROOT / "hydrowitness" / "static" / "index.html"

DT = 5.0                    # simulated seconds per frame (the drill's own step)
HORIZON_S = 720.0           # 12 simulated minutes after the attack starts covers every scripted event
SEED = 1
FIXED_WALL_CLOCK = 1_800_000_000.0      # 2027-01-15, so the idle frame is the same on every run
SKIP = {"free_play"}        # needs a person to break things, so it has no recording

NOTICE = (
    '<div id="webdemo-note" style="padding:.55rem 1rem;font:500 .9rem/1.4 system-ui,sans-serif;'
    'background:Canvas;color:CanvasText;border-bottom:1px solid GrayText">'
    "<b>Web demo.</b> This page replays recorded runs of the real HydroWitness engines on a simulated plant. "
    "Pick a drill on the right, and tick the box first if you want a scripted operator to check the pump. "
    "Breaking things yourself and entering your own readings need the downloadable version."
    "</div>"
)


# Controls a recording cannot honour are hidden, so nothing on the page looks clickable and then refuses.
STYLE = ("<style id=\"webdemo-style\">#enter,#drill .row + h3,#drill .row + h3 + .row,#drill .attack-grid,"
         "[data-act=\"pb-start\"],[data-act=\"pb-step\"],[data-act=\"ack\"],[data-act=\"enter-reading\"],"
         "[data-act=\"focus-level\"]{display:none !important}</style>")

SECOND_WITNESS_OLD = "Enter what you see in the panel above to add one."
SECOND_WITNESS_NEW = "Start this drill again with the scripted operator ticked to add one."


def _frame(st: AppState) -> dict[str, Any]:
    f = jsonable_encoder(st.state())
    ok, bad_at, message = st.active.log.verify()
    f["_verify"] = {"ok": ok, "bad_at": bad_at, "message": message, "records": len(st.active.log.records),
                    "head_hash": st.active.log.head_hash}
    return f


# ---- what really happened in the simulation ------------------------------------------------------------------
# The engines never see any of this. It is recorded next to each frame so the web demo can set what the screen claimed
# and what the witnesses reported against the plant's own state. A real plant gives no such column.
TRUTH_EVERY = 6                      # recompute the look-ahead every 6th frame (30 simulated seconds)
MODE_TEXT = {
    "frozen": "The screen is frozen: the same values repeat with new timestamps.",
    "dark": "The screen is dark: remote access was cut, so nothing new arrives.",
    "replay": "The screen is replaying data recorded 30 minutes earlier.",
    "spoof": "The screen is forged: its values are made up and look normal.",
}


def truth(d: DrillRunner, scada: Optional[dict], look_ahead: Optional[float]) -> dict[str, Any]:
    s, p = d.sys, d.profile
    pumps: dict[str, dict] = {}
    for pump in p.pumps:
        f = s.forced[pump.id]
        why = ("forced off" if f is False else "forced on" if f is True else
               "no power" if not s.power_ok else "the plant's own control logic")
        effective = f if f is not None else (False if not s.power_ok else bool(s.running[pump.id]))   # what it is doing from the next step on
        pumps[pump.id] = {"running": bool(effective), "why": why}
    wrong: list[str] = []
    if scada is not None:
        for pid, claim in (scada.get("pump_status") or {}).items():
            if claim is not None and pid in pumps and bool(claim) != pumps[pid]["running"]:
                wrong.append(f"{pid} is {'running' if pumps[pid]['running'] else 'stopped'}, but the screen says {'running' if claim else 'stopped'}.")
        lv = scada.get("tank_level_ft")
        if lv is not None and abs(lv - s.level_ft) > p.level_tolerance_ft:
            wrong.append(f"The tank is at {s.level_ft:.1f} ft, but the screen says {lv:.1f} ft.")
    if d.view.mode in MODE_TEXT:
        wrong.append(MODE_TEXT[d.view.mode])
    if not s.power_ok:
        wrong.append("Mains power has failed, so the pumps have stopped.")
    if s.demand_scale < 0.99:
        wrong.append("A valve is closed out of sight. Only part of the water reaches customers, and nothing on the screen shows it.")
    valve = s.demand_scale < 0.99
    return {
        "mode": d.view.mode, "pumps": pumps, "level_ft": round(s.level_ft, 2),
        # The simulation closes a valve by cutting the demand that gets through; it does not work out the pressure
        # beyond the valve. So with a valve closed there is no honest number, and none is given.
        "psi_critical": None if valve else round(s.true_critical_psi(), 1), "psi_line": p.min_pressure_psi,
        "pressure_modelled": not valve, "supply_pct": round(s.demand_scale * 100),
        "power_ok": s.power_ok, "valve_closed": valve, "demand_gpm": round(s.demand_gpm),
        "minutes_to_line": None if valve else look_ahead, "horizon_h": TRUTH_HORIZON_H,
        "events": [{"after_attack_s": x["after_attack_s"], "label": x["label"]} for x in d.timeline
                   if x["after_attack_s"] >= 0],
        "screen_wrong": wrong,
    }


def new_state() -> AppState:
    return AppState(demo_profile(), dict(DEMO_SECRETS), demo_config=True)


def record(scenario_id: str, operator_script: bool, *, seed: int = SEED, horizon_s: float = HORIZON_S) -> dict[str, Any]:
    st = new_state()
    st.drill = DrillRunner(scenario_id, seed=seed, operator_script=operator_script)
    frames: list[dict[str, Any]] = []
    ahead: Optional[float] = None
    for i in range(int(horizon_s // DT) + 1):
        if i:
            st.drill.advance(DT)
        f = _frame(st)
        if i % TRUTH_EVERY == 0 and st.drill.sys.demand_scale >= 0.99:
            ahead = minutes_to_line(st.drill.sys, st.drill.profile.min_pressure_psi)
        f["truth"] = truth(st.drill, f.get("scada"), ahead)
        frames.append(f)
    return {"v": 1, "dt": DT, "scenario": scenario_id, "operator_script": operator_script, "frames": frames}


def index() -> dict[str, Any]:
    st = new_state()
    st.live.clock = lambda: FIXED_WALL_CLOCK        # the idle "no data yet" view would otherwise read the wall clock
    live = _frame(st)
    return {
        "v": 1, "version": VERSION,
        "profile": jsonable_encoder(st.profile.model_dump()),
        "scenarios": [{"id": s.id, "name": s.name, "summary": s.summary, "scheduled_events": len(s.events)}
                      for s in SCENARIOS.values() if s.id not in SKIP],
        "live": live,
    }


def app_html() -> str:
    """The dashboard page as the server serves it, plus the shim and a notice. Nothing else changes."""
    src = DASHBOARD.read_text(encoding="utf-8")
    assert src.count("<head>") == 1 and len(re.findall(r"<body[^>]*>", src)) == 1, \
        "dashboard markup changed: update web_recordings.app_html"
    assert src.count(SECOND_WITNESS_OLD) == 1, "dashboard wording changed: update web_recordings"
    src = src.replace(SECOND_WITNESS_OLD, SECOND_WITNESS_NEW)
    src = src.replace("<head>", '<head>\n<script src="shim.js"></script>\n<script src="compare.js" defer></script>\n' + STYLE, 1)
    return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + "\n" + NOTICE, src, count=1)


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)        # mtime 0: the same input gives the same bytes


def rec_name(scenario_id: str, operator_script: bool) -> str:
    return f"{scenario_id}-{'op' if operator_script else 'noop'}.json.gz"


def build_all() -> dict[str, bytes]:
    """Every generated file, as {path relative to site/demo: bytes}."""
    files: dict[str, bytes] = {"index.json": dumps(index()), "app.html": app_html().encode("utf-8")}
    for sid in SCENARIOS:
        if sid in SKIP:
            continue
        for op in (False, True):
            files["rec/" + rec_name(sid, op)] = gz(dumps(record(sid, op)))
    return files


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rec").mkdir(exist_ok=True)
    files = build_all()
    for rel, data in files.items():
        p = OUT / rel
        p.write_bytes(data)
        print(f"{rel:46s}{len(data) / 1024:8.0f} KB")
    print(f"{len(files)} files, {sum(map(len, files.values())) / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
