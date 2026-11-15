"""Soak and load test of the real server (the same code `run_local.py` starts), with simulated SCADA, Witness Nodes and dashboards.

Why: a safety net that falls over, slows down or leaks memory under load is worse than none. This starts the server in a
separate process, drives it for a set time at several multiples of a realistic message rate, and reports

  * latency (median, 95th, 99th percentile, worst) for each kind of request,
  * errors (any answer other than the expected one, any connection failure),
  * the server's memory at the start, the end and the peak, and how fast the evidence log grows,
  * whether the tamper-evident evidence log still verifies at the end.

Realistic rate (1x): one SCADA update every 5 s, one heartbeat per node every 10 s (two nodes), one dashboard poll every 2 s.
The numbers depend on the computer. They describe this software on that computer, not on a real plant or a real network.

Run:  python3 tools/soak_test.py [--seconds 30] [--levels 1,20,100] [--out results]
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hydrowitness.demo import DEMO_SECRETS                 # noqa: E402
from hydrowitness.security.nodesig import make_wire        # noqa: E402

TOKEN = "soak-test-token-0123456789abcdef"
REALISTIC = {"scada_hz": 0.2, "node_hz": 0.1, "poll_hz": 0.5, "pollers": 1}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat: dict[str, list[float]] = {}
        self.codes: dict[str, dict[int, int]] = {}
        self.fail: dict[str, int] = {}

    def add(self, kind: str, secs: float, code: int):
        with self.lock:
            self.lat.setdefault(kind, []).append(secs * 1000.0)
            self.codes.setdefault(kind, {}).setdefault(code, 0)
            self.codes[kind][code] += 1

    def failure(self, kind: str):
        with self.lock:
            self.fail[kind] = self.fail.get(kind, 0) + 1


def worker(kind: str, port: int, hz: float, until: float, stats: Stats, make_request):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    period = 1.0 / hz
    # The server closes a connection that sits idle for more than a few seconds. A slow sender (a Witness Node sends every 10 s) really
    # opens a new connection for each message, so the test does the same; fast senders keep one connection open, as a browser would.
    fresh = period >= 3.0
    nxt = time.perf_counter()
    i = 0
    while time.perf_counter() < until:
        method, path, body, headers = make_request(i)
        i += 1
        if fresh:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        t0 = time.perf_counter()
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            resp.read()
            stats.add(kind, time.perf_counter() - t0, resp.status)
        except Exception:
            stats.failure(kind)
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        if fresh:
            conn.close()
        nxt += period
        delay = nxt - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            nxt = time.perf_counter()          # fell behind: do not try to catch up in a burst


def run_level(port: int, pid: int, mult: float, seconds: float, boot_base: int) -> dict:
    stats = Stats()
    until = time.perf_counter() + seconds
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

    def scada_req(i):
        lvl = 22.0 - (i % 40) * 0.01
        body = json.dumps({"tank_level_ft": lvl, "pump_status": {"P1": False, "P2": True},
                           "pump_flow_gpm": {"P1": 0.0, "P2": 180.0}, "demand_gpm": 180.0, "pressure_psi": 26.8, "t": time.time()})
        return "POST", "/api/scada", body, auth

    def node_req_for(nid, st):
        seqbox = [0]

        def req(i):
            seqbox[0] += 1
            w = make_wire(DEMO_SECRETS[nid], n=nid, b=boot_base, s=seqbox[0], t_ms=int(time.time() * 1000), k="hb", st=st, c=950,
                          ca=st, cm=st, p=1, f="a1b2c3d4")
            return "POST", "/api/node", json.dumps(w), {"Content-Type": "application/json"}
        return req

    def poll_req(i):
        return "GET", "/api/state", None, {"Authorization": f"Bearer {TOKEN}"}

    threads = [threading.Thread(target=worker, args=("scada", port, REALISTIC["scada_hz"] * mult, until, stats, scada_req)),
               threading.Thread(target=worker, args=("node_NA", port, REALISTIC["node_hz"] * mult, until, stats, node_req_for("NA", "S"))),
               threading.Thread(target=worker, args=("node_NB", port, REALISTIC["node_hz"] * mult, until, stats, node_req_for("NB", "R")))]
    n_pollers = max(1, int(round(REALISTIC["pollers"] * mult / 10)))
    threads += [threading.Thread(target=worker, args=("dashboard_poll", port, REALISTIC["poll_hz"] * min(mult, 10), until, stats, poll_req))
                for _ in range(n_pollers)]
    rss0, peak = rss_kb(pid), 0
    for t in threads:
        t.start()
    while time.perf_counter() < until:
        peak = max(peak, rss_kb(pid))
        time.sleep(0.5)
    for t in threads:
        t.join()
    rss1 = rss_kb(pid)
    out = {"multiplier": mult, "seconds": seconds, "dashboard_pollers": n_pollers, "rss_kb_start": rss0, "rss_kb_end": rss1, "rss_kb_peak": max(peak, rss1), "kinds": {}}
    for kind, lat in stats.lat.items():
        lat.sort()
        q = (lambda p, lat=lat: lat[min(len(lat) - 1, int(p * len(lat)))])
        out["kinds"][kind] = {"requests": len(lat), "median_ms": round(statistics.median(lat), 2), "p95_ms": round(q(0.95), 2),
                              "p99_ms": round(q(0.99), 2), "max_ms": round(lat[-1], 2), "status_codes": stats.codes[kind],
                              "connection_failures": stats.fail.get(kind, 0)}
    for kind, n in stats.fail.items():
        out["kinds"].setdefault(kind, {"requests": 0, "connection_failures": n})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=30.0, help="seconds per load level")
    ap.add_argument("--levels", default="1,20,100", help="multiples of the realistic message rate")
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args(argv)
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="hw_soak_")
    logf = Path(tmp) / "evidence.jsonl"
    env = dict(os.environ, HYDROWITNESS_TOKEN=TOKEN, HYDROWITNESS_LOG=str(logf), PYTHONPATH=str(ROOT))
    for k in ("HYDROWITNESS_PROFILE", "HYDROWITNESS_SECRETS", "NTFY_URL"):
        env.pop(k, None)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "hydrowitness.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                c.request("GET", "/api/health")
                if c.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("server did not start")
        levels = [float(x) for x in a.levels.split(",")]
        results = []
        for i, m in enumerate(levels):
            size_before = logf.stat().st_size if logf.exists() else 0
            r = run_level(port, srv.pid, m, a.seconds, boot_base=1000 + i)
            r["evidence_log_bytes_added"] = (logf.stat().st_size if logf.exists() else 0) - size_before
            results.append(r)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request("GET", "/api/log/verify", headers={"Authorization": f"Bearer {TOKEN}"})
        resp = c.getresponse()
        verify = json.loads(resp.read().decode() or "{}")
        c.request("GET", "/api/health")
        alive = c.getresponse().status == 200
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
    res = {"schema": 1, "machine": {"cpus": os.cpu_count(), "python": platform.python_version(), "platform": platform.platform()},
           "realistic_rate": REALISTIC, "levels": results, "evidence_log_verifies": verify, "server_alive_at_end": alive}
    md = markdown(res)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "soak.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    (out / "soak.md").write_text(md, encoding="utf-8")
    print(md)
    bad = any(k.get("connection_failures") for r in results for k in r["kinds"].values()) or not alive
    return 1 if bad else 0


def markdown(res: dict) -> str:
    m = res["machine"]
    L = ["# Soak and load test of the server", "",
         f"Run on a computer with {m['cpus']} CPU(s), Python {m['python']}, {m['platform']}. The server is the real one (uvicorn, the same app `run_local.py` starts), "
         "with simulated SCADA, two Witness Nodes and dashboard pages polling it. **These numbers describe this software on that computer. They say nothing about a real plant or a real network.**", "",
         "1x is a realistic rate: one SCADA update every 5 s, one heartbeat per node every 10 s, one dashboard poll every 2 s. Higher levels multiply it.", ""]
    for r in res["levels"]:
        L += [f"## {r['multiplier']:g}x for {r['seconds']:g} s ({r['dashboard_pollers']} dashboard poller(s))", "",
              "| request | count | median ms | 95th ms | 99th ms | worst ms | answers | connection failures |", "|---|---|---|---|---|---|---|---|"]
        for k, v in r["kinds"].items():
            L.append(f"| {k} | {v.get('requests', 0)} | {v.get('median_ms', '')} | {v.get('p95_ms', '')} | {v.get('p99_ms', '')} | {v.get('max_ms', '')} | "
                     f"{', '.join(f'{c}: {n}' for c, n in sorted(v.get('status_codes', {}).items()))} | {v.get('connection_failures', 0)} |")
        L += ["", f"Server memory: {r['rss_kb_start'] / 1024:.0f} MB at the start of the level, {r['rss_kb_end'] / 1024:.0f} MB at the end, {r['rss_kb_peak'] / 1024:.0f} MB peak. "
              f"Evidence log grew by {r['evidence_log_bytes_added'] / 1024:.0f} kB.", ""]
    v = res["evidence_log_verifies"]
    L += [f"After the load, the tamper-evident evidence log verified: `{json.dumps(v)[:200]}`. Server still answering: {res['server_alive_at_end']}.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
