"""Hash-chained evidence log.

Every record holds a timestamp, source, type, content, the previous record's hash and its
own hash (SHA-256 over the record plus the previous hash). The log supports an advisory
decision and an investigation. It does not make decisions.

Tamper evidence: the chain breaks if any record is edited, removed or reordered. To also
catch whole-log replacement, the latest head hash is sent to an outside channel
(``anchor_sink``), once a day and at every red event.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Callable, Optional

GENESIS = "0" * 64


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def record_hash(rec: dict) -> str:
    body = {k: rec[k] for k in ("seq", "ts", "source", "type", "content", "prev_hash")}
    return hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()


class EvidenceLog:
    def __init__(self, path: Optional[str] = None, anchor_sink: Optional[Callable[[dict], None]] = None,
                 recheck_s: float = 600.0):
        self.path = path
        self.anchor_sink = anchor_sink
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self.last_anchor_ts: Optional[float] = None
        self.recheck_s = recheck_s
        self._vcache: Optional[dict] = None
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.records = [json.loads(line) for line in fh if line.strip()]

    @property
    def head_hash(self) -> str:
        return self.records[-1]["hash"] if self.records else GENESIS

    def append(self, source: str, type_: str, content: dict, ts: float) -> dict:
        with self._lock:
            rec = {
                "seq": len(self.records), "ts": float(ts), "source": source, "type": type_,
                "content": json.loads(_canon(content)), "prev_hash": self.head_hash,
            }
            rec["hash"] = record_hash(rec)
            self.records.append(rec)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
            return rec

    def verify(self) -> tuple[bool, Optional[int], str]:
        """Check the whole chain from the first record. O(n); this is what the dashboard button and the
        command-line check use."""
        return verify_records(self.records)

    def verify_cached(self) -> tuple[bool, Optional[int], str]:
        """The same answer as ``verify()``, cheap enough to call every second.

        The first call checks the whole chain. Later calls check only the records added since, linked
        to the last one already checked. The whole chain is checked again every ``recheck_s`` seconds
        (default ten minutes), so an edit to an old record in memory is still found, just not
        instantly. A broken chain stays reported as broken until that next full check.
        """
        now = time.monotonic()
        with self._lock:
            n = len(self.records)
            c = self._vcache
            if c is not None and n >= c["n"] and now - c["full_t"] < self.recheck_s:
                if not c["ok"]:
                    return False, c["bad"], c["msg"]
                if n == c["n"]:
                    return True, None, c["msg"]
                ok, bad, msg, prev = _verify_span(self.records, c["n"], c["prev"])
                if ok:
                    c.update(n=n, prev=prev, msg="chain intact")
                    return True, None, "chain intact"
                self._vcache = {"n": n, "prev": c["prev"], "ok": False, "bad": bad, "msg": msg, "full_t": c["full_t"]}
                return False, bad, msg
            ok, bad, msg, prev = _verify_span(self.records, 0, GENESIS)
            if ok and n == 0:
                msg = "log is empty"
            self._vcache = {"n": n, "prev": prev, "ok": ok, "bad": bad, "msg": msg, "full_t": now}
            return ok, bad, msg

    def anchor(self, ts: float, reason: str = "daily") -> dict:
        """Send the head hash to an outside channel (manager's email/phone, a second host...)."""
        a = {"ts": ts, "reason": reason, "head_hash": self.head_hash, "count": len(self.records)}
        self.last_anchor_ts = ts
        if self.anchor_sink:
            self.anchor_sink(a)
        return a

    def maybe_daily_anchor(self, ts: float) -> Optional[dict]:
        if self.last_anchor_ts is None or ts - self.last_anchor_ts >= 86400:
            return self.anchor(ts, "daily")
        return None

    def tail(self, n: int = 50) -> list[dict]:
        return self.records[-n:]

    def report(self) -> str:
        ok, bad, msg = self.verify()
        return (f"HydroWitness evidence log report\nrecords: {len(self.records)}\n"
                f"head hash: {self.head_hash}\nchain valid: {ok}\n{msg}\n")


def _verify_span(records: list[dict], start: int, prev: str) -> tuple[bool, Optional[int], str, str]:
    """Check records[start:] given the hash of the record before them. Returns (ok, bad index, message, last hash)."""
    for i in range(start, len(records)):
        rec = records[i]
        if rec.get("seq") != i:
            return False, i, f"record {i}: sequence number is {rec.get('seq')} (record removed or reordered)", prev
        if rec.get("prev_hash") != prev:
            return False, i, f"record {i}: previous-hash link is broken", prev
        if record_hash(rec) != rec.get("hash"):
            return False, i, f"record {i}: contents do not match its hash (record edited)", prev
        prev = rec["hash"]
    return True, None, "chain intact", prev


def verify_records(records: list[dict]) -> tuple[bool, Optional[int], str]:
    ok, bad, msg, _ = _verify_span(records, 0, GENESIS)
    if ok and not records:
        msg = "log is empty"
    return ok, bad, msg


def verify_file(path: str) -> tuple[bool, Optional[int], str]:
    with open(path, "r", encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    return verify_records(recs)


if __name__ == "__main__":  # python -m hydrowitness.evidence_log evidence.jsonl
    import sys

    ok, bad, msg = verify_file(sys.argv[1])
    print(("OK: " if ok else "BROKEN: ") + msg)
    raise SystemExit(0 if ok else 1)
