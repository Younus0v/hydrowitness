#!/usr/bin/env python3
"""Keep real measurements honest: record a session, say what it is, and report only what the recordings support.

    python3 training/measure.py new  2026-11-02-sump-1 --purpose development --pump "0.5 hp sump pump" --mounting "on the motor housing, bare metal"
    python3 training/measure.py capture 2026-11-02-sump-1 --port COM5          # needs: pip install pyserial
    python3 training/measure.py capture 2026-11-02-sump-1 --from-file monitor.txt      # or a log saved from any terminal
    python3 training/measure.py train   2026-11-02-sump-1 2026-11-03-sump-2    # development sessions only
    python3 training/measure.py report                                          # writes results/measurements.md and .json

Every session is a folder under measurements/ with
    session.json   what it is: purpose, board, sensors, pump, mounting. A development or evaluation session without a pump and
                   a mounting is incomplete and the report does not count it.
    serial.log     the node's own lines, exactly as received (this is what training/train_classifier.py reads)
    events.csv     host time, what happened: capture start and stop, the labels you sent, notes such as "pump started by hand"

A session has one purpose, fixed before it is recorded:
    bench        no pump. Boot logs, wiring checks, sensor checks. Proves things about the board, never about a pump.
    development  recordings you may study, train on, and use to choose the mounting and settings.
    evaluation   recordings you do NOT look at while tuning. They are only used to score what the development recordings
                 produced ("heldout"). `train` refuses them.

While capturing, type a line and press Enter:  r (pump is running: sends `label R`), s (pump is stopped: `label S`),
- (starting, stopping or touching the box: `label -`), n <text> (a note), q (quit).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import node_reference as nr  # noqa: E402
import train_classifier as tc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "measurements"
RESULTS = ROOT / "results"
PURPOSES = ("bench", "development", "evaluation")
SCHEMA = 1
LABEL_KEYS = {"r": "R", "s": "S", "-": "-"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,60}$")


# ---- sessions ------------------------------------------------------------------------------------------------------------
def session_dir(sid: str, base: Optional[Path] = None) -> Path:
    base = base or DIR
    if not ID_RE.match(sid):
        raise ValueError("a session id is 3 to 61 letters, digits, dots, dashes or underscores, e.g. 2026-11-02-sump-1")
    return base / sid


def new_session(sid: str, purpose: str, *, base: Optional[Path] = None, **fields) -> Path:
    if purpose not in PURPOSES:
        raise ValueError(f"purpose is one of {', '.join(PURPOSES)}")
    d = session_dir(sid, base)
    if d.exists():
        raise FileExistsError(f"{d} already exists: a recorded session is never overwritten")
    d.mkdir(parents=True)
    meta = {"schema": SCHEMA, "id": sid, "purpose": purpose, "date": fields.pop("date", None) or dt.date.today().isoformat(),
            "operator": "", "board": "", "accelerometer": "", "microphone": "", "buzzer": "", "pump": "", "mounting": "",
            "source": "recorded with training/measure.py", "notes": ""}
    for k, v in fields.items():
        if v is not None:
            meta[k] = v
    (d / "session.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
    (d / "events.csv").write_text("host_time,kind,text\n", encoding="utf-8", newline="\n")
    (d / "serial.log").write_text("", encoding="utf-8", newline="\n")
    return d


def load_session(d: Path) -> dict:
    meta = json.loads((d / "session.json").read_text(encoding="utf-8"))
    meta["_dir"] = d
    return meta


def sessions(base: Optional[Path] = None) -> list[dict]:
    base = base or DIR
    return [load_session(p.parent) for p in sorted(base.glob("*/session.json"))] if base.exists() else []


def problems(meta: dict) -> list[str]:
    """Why a session cannot be counted as evidence."""
    out = []
    if meta.get("purpose") not in PURPOSES:
        out.append("purpose is not one of bench, development, evaluation")
    if meta.get("purpose") in ("development", "evaluation"):
        for k, label in (("pump", "the pump or motor"), ("mounting", "how the node was mounted"), ("board", "the board")):
            if not str(meta.get(k, "")).strip():
                out.append(f"session.json does not say {label}")
    return out


# ---- capture -------------------------------------------------------------------------------------------------------------
class Capture:
    """Writes what the node says to serial.log, and what the person does to events.csv. No hardware needed to test it."""

    def __init__(self, d: Path, send: Optional[Callable[[str], None]] = None, now: Callable[[], dt.datetime] = dt.datetime.now):
        self.dir, self.send, self.now = d, send, now
        self.log = open(d / "serial.log", "a", encoding="utf-8", newline="\n")
        self.events = open(d / "events.csv", "a", encoding="utf-8", newline="")
        self._w = csv.writer(self.events, lineterminator="\n")
        self.lines = 0
        self.lock = threading.Lock()
        self.event("capture_start", "")

    def event(self, kind: str, text: str) -> None:
        with self.lock:
            self._w.writerow([self.now().isoformat(timespec="seconds"), kind, text])
            self.events.flush()

    def on_line(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        with self.lock:
            self.log.write(line + "\n")
            self.log.flush()
            self.lines += 1

    def on_command(self, cmd: str) -> bool:
        """One thing the person typed. Returns False when they asked to quit."""
        cmd = cmd.strip()
        if not cmd:
            return True
        if cmd.lower() in ("q", "quit"):
            return False
        key = cmd.lower()
        if key in LABEL_KEYS:
            if self.send is None:
                self.event("label_not_sent", f"{key}: no serial port, so the node was not told")
            else:
                self.send(f"label {LABEL_KEYS[key]}")
                self.event("label", LABEL_KEYS[key])
        elif key.startswith("n ") or key.startswith("note "):
            self.event("note", cmd.split(" ", 1)[1].strip())
        else:
            self.event("unknown_input", cmd)
        return True

    def close(self) -> None:
        self.event("capture_stop", f"{self.lines} lines")
        self.log.close()
        self.events.close()


def capture_file(d: Path, src: Path) -> int:
    """Take a log somebody saved from a terminal program."""
    cap = Capture(d)
    cap.event("imported", f"from {src.name}")
    for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
        cap.on_line(ln)
    n = cap.lines
    cap.close()
    return n


def capture_port(d: Path, port: str, baud: int = 115200) -> int:  # pragma: no cover - needs a board
    try:
        import serial  # type: ignore
    except ImportError:
        raise SystemExit("reading a serial port needs pyserial: pip install pyserial   (or save the Serial Monitor text and use --from-file)")
    ser = serial.Serial(port, baud, timeout=0.5)
    cap = Capture(d, send=lambda s: ser.write((s + "\n").encode()))
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            raw = ser.readline()
            if raw:
                cap.on_line(raw.decode("utf-8", errors="replace"))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    print(f"recording to {d / 'serial.log'}.  r = pump running, s = pump stopped, - = starting/stopping/touching, n <text> = note, q = quit")
    try:
        for cmd in sys.stdin:
            if not cap.on_command(cmd):
                break
    except KeyboardInterrupt:
        pass
    stop.set()
    t.join(timeout=2)
    n = cap.lines
    cap.close()
    ser.close()
    return n


# ---- reading a session ---------------------------------------------------------------------------------------------------
def read_log(d: Path) -> list[str]:
    p = d / "serial.log"
    return p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []


def observations(lines: Iterable[str]) -> dict:
    """What the node itself said about the board: boot line, warnings, the last status numbers."""
    obs: dict = {"boots": [], "warnings": [], "fatal": [], "status_lines": 0}
    last: dict = {}
    for ln in lines:
        ln = ln.strip()
        m = re.match(r"I,Witness Node (\S+) boot (\d+) firmware (\S+) mains (\d+) Hz, (.*)", ln)
        if m:
            obs["boots"].append({"node": m[1], "boot": int(m[2]), "firmware": m[3], "mains_hz": int(m[4]), "calibration": m[5]})
        elif ln.startswith("W,"):
            obs["warnings"].append(ln[2:])
        elif ln.startswith("FATAL"):
            obs["fatal"].append(ln)
        elif ln.startswith("S,"):
            obs["status_lines"] += 1
            for kv in ln[2:].split(","):
                k, _, v = kv.partition("=")
                if v:
                    last[k] = v
    if last:
        obs["last_status"] = last
    return obs


def windows(d: Path) -> dict:
    rows = tc.parse(read_log(d))
    out = {"labelled": sum(len(v) for v in rows.data.values()), "unlabelled": rows.unlabeled, "damaged": rows.bad, "channels": {}}
    for ch in "am":
        R, S = rows.data.get((ch, "R"), []), rows.data.get((ch, "S"), [])
        out["channels"][tc.CHANNELS[ch]] = {
            "running_windows": len(tc.usable_windows(R, 5.0)), "stopped_windows": len(tc.usable_windows(S, 5.0)),
            "running_runs": len(tc.segments(R)), "stopped_runs": len(tc.segments(S))}
    return out


# ---- held-out scoring ----------------------------------------------------------------------------------------------------
def heldout(dev_logs: list[list[str]], eval_logs: list[list[str]]) -> dict:
    """Fit on the development recordings only. Score on the evaluation recordings, which the fit never sees."""
    dev, ev = tc.parse([x for lg in dev_logs for x in lg]), tc.parse([x for lg in eval_logs for x in lg])
    out: dict = {}
    for ch in "am":
        name = tc.CHANNELS[ch]
        R, S = (tc.usable_windows(dev.data.get((ch, k), []), 5.0) for k in "RS")
        eR, eS = (tc.usable_windows(ev.data.get((ch, k), []), 5.0) for k in "RS")
        rec = {"development_running": len(R), "development_stopped": len(S), "evaluation_running": len(eR), "evaluation_stopped": len(eS)}
        if min(len(R), len(S)) < tc.MIN_PER_CLASS or min(len(eR), len(eS)) < tc.MIN_PER_CLASS:
            rec["verdict"] = "NO DATA"
        else:
            m = nr.fit(R, S)
            rec["running_correct"] = round(float(sum(m.z(x) > 0 for x in eR)) / len(eR), 3)
            rec["stopped_correct"] = round(float(sum(m.z(x) < 0 for x in eS)) / len(eS), 3)
            rec["verdict"] = tc.verdict(min(rec["running_correct"], rec["stopped_correct"]))
        out[name] = rec
    return out


# ---- the report ----------------------------------------------------------------------------------------------------------
def build_report(base: Optional[Path] = None) -> dict:
    sess = sessions(base)
    rows, counted = [], {"bench": 0, "development": 0, "evaluation": 0}
    for m in sess:
        d = m["_dir"]
        issue = problems(m)
        w = windows(d)
        has_pump_labels = any(c["running_windows"] and c["stopped_windows"] for c in w["channels"].values())
        rec = {"id": m["id"], "purpose": m["purpose"], "date": m.get("date"), "board": m.get("board", ""), "pump": m.get("pump", ""),
               "mounting": m.get("mounting", ""), "source": m.get("source", ""), "problems": issue, "observations": observations(read_log(d)),
               "windows": w, "counts_as_pump_evidence": bool(m["purpose"] in ("development", "evaluation") and not issue and has_pump_labels)}
        if rec["counts_as_pump_evidence"]:
            counted[m["purpose"]] += 1
        elif m["purpose"] == "bench" and not issue:
            counted["bench"] += 1
        rows.append(rec)
    dev = [r for r in rows if r["purpose"] == "development" and r["counts_as_pump_evidence"]]
    ev = [r for r in rows if r["purpose"] == "evaluation" and r["counts_as_pump_evidence"]]
    out: dict = {"schema": SCHEMA, "sessions": rows, "counted": counted, "heldout": None}
    if dev and ev:
        by_id = {m["id"]: m["_dir"] for m in sess}
        out["heldout"] = heldout([read_log(by_id[r["id"]]) for r in dev], [read_log(by_id[r["id"]]) for r in ev])
    out["headline"] = headline(counted, out["heldout"])
    return out


def headline(counted: dict, held: Optional[dict]) -> str:
    if counted["development"] + counted["evaluation"] == 0:
        text = ("No measurements from a real pump have been supplied. Every number in results/ comes from a simulated plant and simulated "
                "Witness Nodes.")
        if counted["bench"]:
            text += (f" The firmware has booted on a real board ({counted['bench']} bench log{'s' if counted['bench'] != 1 else ''}, no pump); "
                     "that is the only real-hardware evidence so far.")
        return text
    if not counted["evaluation"]:
        return ("Development recordings from a real pump exist, but no held-out evaluation recordings. They show what the node saw while it "
                "was being set up; they do not show how well it works on recordings it has not seen.")
    if held and all(v["verdict"] == "NO DATA" for v in held.values()):
        return "Development and evaluation recordings exist, but there are not yet enough labelled windows in both to score anything."
    return "Held-out evaluation recordings from a real pump exist; see the scores below. They are for this pump, this mounting and these days only."


def markdown(rep: dict) -> str:
    L = ["# Real measurements: what has been supplied", "", f"**{rep['headline']}**", "",
         "Sessions live in `measurements/`. Each has a purpose fixed before it was recorded: **bench** (no pump; proves things about the board "
         "only), **development** (may be studied and trained on), **evaluation** (never used to tune; only scored against what development "
         "produced). `python3 training/measure.py report` rewrites this file.", ""]
    if not rep["sessions"]:
        L += ["No sessions recorded yet.", ""]
    else:
        L += ["| session | purpose | date | board | pump | counts as pump evidence | problems |", "|---|---|---|---|---|---|---|"]
        for r in rep["sessions"]:
            L.append(f"| {r['id']} | {r['purpose']} | {r['date']} | {r['board'] or '-'} | {r['pump'] or '-'} | "
                     f"{'yes' if r['counts_as_pump_evidence'] else 'no'} | {'; '.join(r['problems']) or '-'} |")
        L.append("")
    for r in rep["sessions"]:
        o, w = r["observations"], r["windows"]
        L += [f"## {r['id']} ({r['purpose']})", ""]
        if r["source"]:
            L.append(f"Source: {r['source']}.")
        if o["boots"]:
            b = o["boots"][-1]
            L.append(f"Boot line: node {b['node']}, boot {b['boot']}, firmware {b['firmware']}, mains {b['mains_hz']} Hz, {b['calibration']}.")
        for x in o["warnings"]:
            L.append(f"- Node warning: {x}")
        for x in o["fatal"]:
            L.append(f"- **{x}**")
        if o.get("last_status"):
            s = o["last_status"]
            L.append("Last status line: " + ", ".join(f"{k}={s[k]}" for k in ("acc_rate", "acc_overflows", "i2c_errs", "mic_errs", "sent", "rejected") if k in s) + ".")
        if w["labelled"]:
            for ch, c in w["channels"].items():
                L.append(f"- {ch}: {c['running_windows']} running and {c['stopped_windows']} stopped usable windows "
                         f"({c['running_runs']} and {c['stopped_runs']} runs)")
        elif r["purpose"] != "bench":
            L.append("- No labelled windows in this log yet.")
        L.append("")
    if rep["heldout"]:
        L += ["## Held-out score (fit on development sessions, scored on evaluation sessions)", "",
              "| channel | development windows (running / stopped) | evaluation windows (running / stopped) | running right | stopped right | verdict |",
              "|---|---|---|---|---|---|"]
        for ch, v in rep["heldout"].items():
            L.append(f"| {ch} | {v['development_running']} / {v['development_stopped']} | {v['evaluation_running']} / {v['evaluation_stopped']} | "
                     f"{v.get('running_correct', '-')} | {v.get('stopped_correct', '-')} | {v['verdict']} |")
        L.append("")
    L += ["## What this does and does not support", "",
          "- A bench log shows the board boots and what its sensors say about themselves. It says nothing about whether the node can tell a "
          "pump's on from its off.",
          "- Only labelled recordings on a real pump or motor, with the equipment and mounting written down, count as pump evidence.",
          "- Evaluation recordings must not be used to choose a mounting or a setting. If they were, they are development recordings; say so.", ""]
    return "\n".join(L)


def write_report(base: Optional[Path] = None, out_dir: Optional[Path] = None) -> dict:
    out_dir = out_dir or RESULTS
    rep = build_report(base)
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = json.loads(json.dumps(rep, default=str))
    (out_dir / "measurements.json").write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8", newline="\n")
    (out_dir / "measurements.md").write_text(markdown(rep), encoding="utf-8", newline="\n")
    return rep


# ---- command line --------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="start a session folder")
    n.add_argument("id")
    n.add_argument("--purpose", required=True, choices=PURPOSES)
    for f in ("operator", "board", "accelerometer", "microphone", "buzzer", "pump", "mounting", "notes", "source", "date"):
        n.add_argument(f"--{f}")
    c = sub.add_parser("capture", help="record serial output into a session")
    c.add_argument("id")
    c.add_argument("--port")
    c.add_argument("--baud", type=int, default=115200)
    c.add_argument("--from-file")
    t = sub.add_parser("train", help="run training/train_classifier.py on development sessions only")
    t.add_argument("ids", nargs="+")
    sub.add_parser("report", help="write results/measurements.md and .json")
    h = sub.add_parser("heldout", help="print the held-out score")
    a = ap.parse_args(argv)

    if a.cmd == "new":
        fields = {k: getattr(a, k) for k in ("operator", "board", "accelerometer", "microphone", "buzzer", "pump", "mounting", "notes", "source", "date")}
        d = new_session(a.id, a.purpose, **fields)
        print(f"created {d}. Fill in what is missing in session.json before recording.")
        return 0
    if a.cmd == "capture":
        d = session_dir(a.id)
        if not (d / "session.json").exists():
            print(f"no session {a.id}: create it first with `new`")
            return 2
        if bool(a.port) == bool(a.from_file):
            print("give exactly one of --port or --from-file")
            return 2
        n_lines = capture_file(d, Path(a.from_file)) if a.from_file else capture_port(d, a.port, a.baud)
        print(f"{n_lines} lines saved to {d / 'serial.log'}")
        return 0
    if a.cmd == "train":
        logs = []
        for sid in a.ids:
            meta = load_session(session_dir(sid))
            if meta["purpose"] != "development":
                print(f"refusing: {sid} is a {meta['purpose']} session. Only development sessions may be used to train or tune.")
                return 2
            logs.append(str(meta["_dir"] / "serial.log"))
        return tc.main(logs)
    if a.cmd == "report":
        rep = write_report()
        print(markdown(rep))
        return 0
    if a.cmd == "heldout":
        print(json.dumps(build_report()["heldout"], indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
