"""HydroWitness web API and dashboard server (build stage 4).

    uvicorn hydrowitness.api:app            (demo configuration)
    HYDROWITNESS_PROFILE=site.json HYDROWITNESS_SECRETS=secrets.json HYDROWITNESS_TOKEN=... uvicorn hydrowitness.api:app

Two cores run side by side:
  live   the real system: SCADA feed, signed Witness Node messages, operator entries.
  drill  an isolated simulation with its own in-memory log. A drill can never write to the live log,
         and the live system's state is always shown next to a drill, so a real alarm is never hidden.

READ-ONLY toward the plant. There is no endpoint, and no code path, that writes to a pump, valve or PLC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets as pysecrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .alerts import NtfySink
from .demo import DEMO_SECRETS, demo_profile
from .evidence_log import EvidenceLog
from .schemas import ManualKind, ManualReading, ScadaSnapshot, SiteProfile
from .security.nodesig import parse_secret
from .service import HydroWitnessCore
from .sim.drills import INJECT_ACTIONS, SCENARIOS, DrillRunner

log = logging.getLogger("hydrowitness.api")
STATIC = Path(__file__).parent / "static"
VERSION = "0.1.0"


# --------------------------------------------------------------------------- request bodies
class ManualIn(BaseModel):
    kind: ManualKind
    value: Optional[float | bool] = None
    pump_id: Optional[str] = None
    operator: str = Field(default="operator", max_length=40)
    address: Optional[str] = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=300)
    t: Optional[float] = None


class OperatorIn(BaseModel):
    operator: str = Field(default="operator", max_length=40)


class DrillStartIn(BaseModel):
    scenario: str
    seed: int = 1
    operator_script: bool = False
    speed: float = Field(default=5.0, gt=0, le=60)


class DrillInjectIn(BaseModel):
    action: str
    arg: Optional[str] = None


class DrillControlIn(BaseModel):
    speed: Optional[float] = Field(default=None, gt=0, le=60)
    running: Optional[bool] = None


# ----------------------------------------------------------------------------- app state
class AppState:
    def __init__(self, profile: SiteProfile, node_secrets: dict[str, bytes], *, log_path: Optional[str] = None,
                 alert_sinks: Optional[list] = None, demo_config: bool = False, deferred_alerts: bool = False):
        self.profile = profile
        self.node_secrets = node_secrets
        self.demo_config = demo_config
        self.sinks = alert_sinks if alert_sinks is not None else []
        self.deferred_alerts = deferred_alerts
        self.log_path = log_path
        self.live_log = EvidenceLog(log_path)
        self.live = self._new_live()
        self.drill: Optional[DrillRunner] = None
        self.drill_speed = 5.0
        self.drill_running = True
        self._debt = 0.0
        self.lock = threading.RLock()
        self.started = time.time()

    def _new_live(self) -> HydroWitnessCore:
        core = HydroWitnessCore(self.profile, self.node_secrets, log=self.live_log, alert_sinks=self.sinks, name="live",
                                deferred_alerts=self.deferred_alerts)
        core.alerts_enabled = False        # armed by the first real data, so a fresh start doesn't page anyone
        return core

    def reconfigure(self, profile: SiteProfile) -> None:
        with self.lock:
            self.profile = profile
            self.live = self._new_live()
            self.live_log.append("setup", "profile_changed", {"site": profile.site_name}, time.time())

    # ---- which core do operator actions go to
    @property
    def active(self) -> HydroWitnessCore:
        return self.drill.core if self.drill else self.live

    def tick(self, real_dt: float) -> None:
        with self.lock:
            if self.drill and self.drill_running:
                self._debt += real_dt * self.drill_speed
                n = int(self._debt // self.drill.dt)
                if n:
                    self._debt -= n * self.drill.dt
                    for _ in range(min(n, 400)):
                        self.drill.tick()
            if self.live.snaps or any(s.last_msg for s in self.live.registry.nodes.values()):
                self.live.evaluate()

    # ---- the state shown on the dashboard
    def state(self) -> dict:
        with self.lock:
            live_view = self.live.evaluate()
            if self.drill:
                d = self.drill
                view = d.core.evaluate(d.sys.t)
                m = d.metrics()
                view["drill"] = {
                    "scenario": d.scenario.id, "name": d.scenario.name, "summary": d.scenario.summary,
                    "speed": self.drill_speed, "running": self.drill_running,
                    "operator_script": d.operator_script, "attack_started": d.attacked,
                    "seconds_since_attack": m["seconds_since_attack"], "timeline": d.timeline[-30:][::-1],
                    "metrics": m,
                }
                source = "drill"
            else:
                view, source = live_view, "live"
            view["source"] = source
            view["live_summary"] = {
                "state": live_view["trust"]["state"], "flags": live_view["trust"]["flags"],
                "headline": (live_view["trust"]["cards"][0]["title"] if live_view["trust"]["cards"] else ""),
                "armed": self.live.alerts_enabled, "scada_age_s": (live_view["scada"] or {}).get("age_s"),
            }
            return view



def _load_config() -> tuple[SiteProfile, dict[str, bytes], bool]:
    prof_path, sec_path = os.environ.get("HYDROWITNESS_PROFILE"), os.environ.get("HYDROWITNESS_SECRETS")
    if prof_path and sec_path:
        profile = SiteProfile(**json.loads(Path(prof_path).read_text()))
        raw = json.loads(Path(sec_path).read_text())
        return profile, {k: parse_secret(v) for k, v in raw.items()}, False
    return demo_profile(), dict(DEMO_SECRETS), True


# ----------------------------------------------------------------------------- the app
def create_app(profile: Optional[SiteProfile] = None, node_secrets: Optional[dict[str, bytes]] = None, *,
               log_path: Optional[str] = None, token: Optional[str] = None, run_background: bool = True,
               alert_sinks: Optional[list] = None) -> FastAPI:
    if profile is None:
        profile, node_secrets, demo_config = _load_config()
    else:
        demo_config = False
        node_secrets = node_secrets or {}
    log_path = log_path or os.environ.get("HYDROWITNESS_LOG")
    token = token if token is not None else os.environ.get("HYDROWITNESS_TOKEN")
    sinks = list(alert_sinks) if alert_sinks is not None else []
    if alert_sinks is None and os.environ.get("NTFY_URL") and os.environ.get("NTFY_TOPIC") and os.environ.get("NTFY_TOKEN"):
        sinks.append(NtfySink(os.environ["NTFY_URL"], os.environ["NTFY_TOPIC"], os.environ["NTFY_TOKEN"]))
    st = AppState(profile, node_secrets, log_path=log_path, alert_sinks=sinks, demo_config=demo_config,
                  deferred_alerts=run_background)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        stop = threading.Event()
        if run_background:
            async def loop():
                last = time.monotonic()
                while True:
                    await asyncio.sleep(1.0)
                    now = time.monotonic()
                    dt, last = now - last, now
                    try:
                        st.tick(dt)
                    except Exception:           # the monitor must keep running whatever happens
                        log.exception("background tick failed")
            task = asyncio.create_task(loop())
            # Sending alerts is network work. It runs on its own thread so that a dead network (an internet
            # outage, a down ntfy server) can never stall the monitor or the dashboard.
            def deliver():
                while not stop.wait(0.5):
                    try:
                        st.live.alerts.deliver_pending(time.time())
                    except Exception:
                        log.exception("alert delivery failed")
            threading.Thread(target=deliver, name="alert-delivery", daemon=True).start()
        yield
        stop.set()
        if task:
            task.cancel()

    app = FastAPI(title="HydroWitness", version=VERSION, lifespan=lifespan)
    app.state.hw = st

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        if not token:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not pysecrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "missing or wrong access token")

    @app.middleware("http")
    async def headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ read
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": VERSION, "auth_required": bool(token), "demo_config": st.demo_config,
                "drill_active": st.drill is not None, "live_armed": st.live.alerts_enabled,
                "read_only": True}

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return st.state()

    @app.get("/api/profile", dependencies=[Depends(auth)])
    def get_profile():
        return st.profile.model_dump()

    @app.get("/api/log", dependencies=[Depends(auth)])
    def get_log(n: int = Query(100, ge=1, le=1000)):
        core = st.active
        return {"source": "drill" if st.drill else "live", "count": len(core.log.records),
                "head_hash": core.log.head_hash, "records": core.log.tail(n)[::-1]}

    @app.get("/api/log/verify", dependencies=[Depends(auth)])
    def verify_log():
        ok, bad_at, msg = st.active.log.verify()
        return {"ok": ok, "bad_at": bad_at, "message": msg, "records": len(st.active.log.records),
                "head_hash": st.active.log.head_hash}

    @app.get("/api/log/report", response_class=PlainTextResponse, dependencies=[Depends(auth)])
    def log_report():
        return st.active.log.report()

    @app.get("/api/log/export", dependencies=[Depends(auth)])
    def log_export():
        body = "\n".join(json.dumps(r, sort_keys=True) for r in st.active.log.records) + "\n"
        return PlainTextResponse(body, media_type="application/x-ndjson",
                                 headers={"Content-Disposition": 'attachment; filename="hydrowitness-evidence.jsonl"'})

    # ----------------------------------------------------------- live data in
    @app.post("/api/scada", dependencies=[Depends(auth)])
    def post_scada(body: dict[str, Any] = Body(...)):
        body = dict(body)
        body.setdefault("t", time.time())
        try:
            snap = ScadaSnapshot(**body)
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json()))
        st.live.alerts_enabled = True
        st.live.ingest_scada(snap)
        return {"ok": True}

    @app.post("/api/node")
    def post_node(wire: dict[str, Any] = Body(...)):
        """Witness Node messages are authenticated by their own HMAC, not by the dashboard token."""
        res = st.live.ingest_node(wire, time.time())
        if res["accepted"]:
            st.live.alerts_enabled = True
            res["trust"] = st.live.state          # lets the node's own LED and buzzer mirror the dashboard
        return JSONResponse(res, status_code=200 if res["accepted"] else 403)

    # --------------------------------------------------------- operator actions
    @app.post("/api/manual", dependencies=[Depends(auth)])
    def post_manual(m: ManualIn):
        core = st.active
        if m.kind in ("pump_running", "flow_confirmed") and not m.pump_id:
            raise HTTPException(422, "pump_id is required for this kind of reading")
        if m.kind in ("tank_level_ft", "gauge_psi") and m.value is None:
            raise HTTPException(422, "value is required for this kind of reading")
        if m.pump_id and core.profile.pump(m.pump_id) is None:
            raise HTTPException(422, f"unknown pump {m.pump_id}")
        t = core.clock() if m.t is None else m.t
        core.ingest_manual(ManualReading(t=t, kind=m.kind, value=m.value, pump_id=m.pump_id, operator=m.operator,
                                         address=m.address, note=m.note))
        return {"ok": True, "t": t}

    @app.post("/api/ack", dependencies=[Depends(auth)])
    def ack(body: OperatorIn = OperatorIn()):
        ok = st.active.acknowledge(body.operator)
        if not ok:
            raise HTTPException(409, "nothing to acknowledge: the state is green")
        return {"ok": True}

    @app.post("/api/playbook/start", dependencies=[Depends(auth)])
    def playbook_start(body: OperatorIn = OperatorIn()):
        if not st.active.start_playbook(body.operator):
            raise HTTPException(409, "the playbook is already running")
        return {"ok": True}

    @app.post("/api/playbook/step/{step_id}", dependencies=[Depends(auth)])
    def playbook_step(step_id: str, body: OperatorIn = OperatorIn()):
        if not st.active.complete_step(step_id, body.operator):
            raise HTTPException(404, "no such step, or it is already done")
        return {"ok": True}

    # ----------------------------------------------------------------- setup
    @app.put("/api/profile", dependencies=[Depends(auth)])
    def put_profile(body: dict[str, Any] = Body(...)):
        try:
            profile = SiteProfile(**body)
        except (ValidationError, ValueError) as exc:
            detail = json.loads(exc.json()) if isinstance(exc, ValidationError) else str(exc)
            raise HTTPException(422, detail)
        st.reconfigure(profile)
        return {"ok": True, "site": profile.site_name}

    # ----------------------------------------------------------------- drills
    @app.get("/api/drill/scenarios", dependencies=[Depends(auth)])
    def drill_scenarios():
        return {"scenarios": [{"id": s.id, "name": s.name, "summary": s.summary, "scheduled_events": len(s.events)}
                              for s in SCENARIOS.values()],
                "actions": INJECT_ACTIONS}

    @app.post("/api/drill/start", dependencies=[Depends(auth)])
    def drill_start(body: DrillStartIn):
        if body.scenario not in SCENARIOS:
            raise HTTPException(404, "unknown scenario")
        with st.lock:
            st.drill = DrillRunner(body.scenario, seed=body.seed, operator_script=body.operator_script)
            st.drill_speed, st.drill_running, st._debt = body.speed, True, 0.0
        return {"ok": True}

    @app.post("/api/drill/stop", dependencies=[Depends(auth)])
    def drill_stop():
        with st.lock:
            if st.drill is None:
                raise HTTPException(409, "no drill is running")
            st.drill = None
        return {"ok": True}

    @app.post("/api/drill/control", dependencies=[Depends(auth)])
    def drill_control(body: DrillControlIn):
        with st.lock:
            if st.drill is None:
                raise HTTPException(409, "no drill is running")
            if body.speed is not None:
                st.drill_speed = body.speed
            if body.running is not None:
                st.drill_running = body.running
        return {"ok": True, "speed": st.drill_speed, "running": st.drill_running}

    @app.post("/api/drill/step", dependencies=[Depends(auth)])
    def drill_step(seconds: float = Query(30.0, gt=0, le=1800)):
        with st.lock:
            if st.drill is None:
                raise HTTPException(409, "no drill is running")
            st.drill.advance(seconds)
        return {"ok": True}

    @app.post("/api/drill/inject", dependencies=[Depends(auth)])
    def drill_inject(body: DrillInjectIn):
        with st.lock:
            if st.drill is None:
                raise HTTPException(409, "no drill is running")
            try:
                label = st.drill.inject(body.action, body.arg)
            except KeyError:
                raise HTTPException(404, "unknown action")
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        return {"ok": True, "label": label}

    # ------------------------------------------------------------------ pages
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


_default_app: Optional[FastAPI] = None


def __getattr__(name: str):
    """`uvicorn hydrowitness.api:app` builds the default app on first use; importing this module builds nothing."""
    global _default_app
    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app
    raise AttributeError(name)
