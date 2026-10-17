"""The four plain-language evidence checks shown on the dashboard.

They answer one question for the person looking at the screen: "how much can I lean on the evidence
right now?" Each check reports pass, warn, fail, or na (not set up) and says why in one sentence.

This is a view of facts the trust engine already has. It does not add a number to the score, it does
not change the green/amber/red light, and it is deliberately NOT a 0-100 rating: a made-up percentage
would look more precise than the evidence is. The light stays the single verdict; these four rows
explain what sits behind it.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import config as C

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "na"

# What a person actually saw or did, as opposed to something a machine claimed.
_INDEPENDENT_KINDS = ("tank_level_ft", "gauge_psi", "pump_running", "flow_confirmed")


def _dur(sec: float) -> str:
    """Same wording as the dashboard's own duration formatter."""
    sec = max(0, round(sec))
    if sec < 90:
        return f"{sec} s"
    m = round(sec / 60)
    if m < 90:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min"


def _s(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _check(cid: str, name: str, status: str, detail: str) -> dict:
    return {"id": cid, "name": name, "status": status, "detail": detail}


def _integrity(ok: bool, bad: Optional[int], msg: str, count: int) -> dict:
    if not ok:
        return _check("integrity", "Record integrity", FAIL, f"The evidence log does not check out: {msg}.")
    if count == 0:
        return _check("integrity", "Record integrity", PASS, "No records yet, so nothing has been changed.")
    return _check("integrity", "Record integrity", PASS,
                  f"Chain intact: {count} {_s(count, 'record', 'records')}, none changed.")


def _freshness(now: float, scada_latest: Any, stale: bool) -> dict:
    if scada_latest is None:
        return _check("freshness", "Evidence freshness", WARN, "No SCADA data has arrived yet.")
    age = now - scada_latest.t
    if stale:
        return _check("freshness", "Evidence freshness", FAIL,
                      f"SCADA last updated {_dur(age)} ago, so the screen is stale.")
    return _check("freshness", "Evidence freshness", PASS, f"SCADA updated {_dur(age)} ago.")


def _usable(node: dict) -> bool:
    return (not node["silent"] and node["state"] in ("RUNNING", "STOPPED")
            and (node["confidence"] or 0.0) >= C.NODE_MIN_CONFIDENCE)


def _agreement(now: float, scada_latest: Any, tr: Any, nodes: list[dict], manual: list[Any]) -> dict:
    # Cards are what the trust engine counted against the screen (sorted by weight, biggest first).
    against = [c for c in tr.cards if c["kind"] in ("node", "operator", "customer", "rule")]
    if against:
        total = sum(c["contribution"] for c in against)
        hard = "operator_contradiction" in tr.flags or total >= C.AMBER_AT
        top = against[0]["title"].rstrip(".")
        more = len(against) - 1
        detail = f"{top}." + (f" {more} more {_s(more, 'source also disagrees', 'sources also disagree')}." if more else "")
        return _check("agreement", "Source agreement", FAIL if hard else WARN, detail)
    if scada_latest is None:
        return _check("agreement", "Source agreement", WARN, "Nothing to compare yet: no SCADA data.")
    seen = {(r.kind, r.pump_id) for r in manual
            if r.kind in _INDEPENDENT_KINDS and now - r.t <= C.FRESHNESS_S}
    n = sum(1 for nd in nodes if _usable(nd)) + len(seen)
    if n == 0:
        return _check("agreement", "Source agreement", WARN,
                      "Only SCADA is reporting. Nothing independent to compare it with.")
    return _check("agreement", "Source agreement", PASS,
                  f"SCADA agrees with {n} independent {_s(n, 'reading', 'readings')}.")


def _sensor(nodes: list[dict]) -> dict:
    if not nodes:
        return _check("sensor", "Sensor health", NA, "No Witness Node is set up for this site.")
    silent, soft, backup = [], [], []
    for nd in nodes:
        nid = nd["node_id"]
        if nd["silent"]:
            age = nd["last_seen_age_s"]
            silent.append(f"Node {nid} has not reported yet" if age is None
                          else f"Node {nid} has gone quiet (last heard {_dur(age)} ago)")
            continue
        if nd["state"] is None:
            soft.append(f"Node {nid} is waiting for its first reading")
        elif nd["state"] == "UNKNOWN":
            a, m = nd["acc_state"], nd["mic_state"]
            if a not in (None, "UNKNOWN") and m not in (None, "UNKNOWN") and a != m:
                soft.append(f"Node {nid}: vibration and sound disagree")
            else:
                soft.append(f"Node {nid} cannot tell if the pump is running")
        elif (nd["confidence"] or 0.0) < C.NODE_MIN_CONFIDENCE:
            soft.append(f"Node {nid} is unsure ({round((nd['confidence'] or 0) * 100)}% confident)")
        if nd["mains_ok"] is False:
            backup.append(nid)
    if backup:
        soft.append(f"Node {backup[0]} is on backup power" if len(backup) == 1
                    else f"Nodes {', '.join(backup)} are on backup power")
    if silent:
        return _check("sensor", "Sensor health", FAIL, ". ".join(silent[:2]) + ".")
    if soft:
        return _check("sensor", "Sensor health", WARN, ". ".join(soft[:2]) + ".")
    if len(nodes) == 1:
        nd = nodes[0]
        return _check("sensor", "Sensor health", PASS,
                      f"Node {nd['node_id']} is reporting clearly ({round(nd['confidence'] * 100)}% confident).")
    return _check("sensor", "Sensor health", PASS, f"All {len(nodes)} Witness Nodes are reporting clearly.")


def build(*, now: float, scada_latest: Any, tr: Any, nodes: list[dict], manual: list[Any],
          log_ok: bool, log_bad: Optional[int], log_msg: str, log_count: int) -> dict:
    """Return {"checks": [four rows], "passed": n, "total": n, "text": "3 of 4 checks pass"}."""
    checks = [
        _integrity(log_ok, log_bad, log_msg, log_count),
        _freshness(now, scada_latest, tr.stale),
        _agreement(now, scada_latest, tr, nodes, manual),
        _sensor(nodes),
    ]
    applicable = [c for c in checks if c["status"] != NA]
    passed = sum(1 for c in applicable if c["status"] == PASS)
    text = f"{passed} of {len(applicable)} checks pass"
    unset = len(checks) - len(applicable)
    if unset:
        text += f" ({unset} not set up)"
    return {"checks": checks, "passed": passed, "total": len(applicable), "text": text}
