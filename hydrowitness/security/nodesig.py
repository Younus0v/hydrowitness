"""Witness Node message signing, verification, replay protection and heartbeat tracking.

The node signs a canonical ASCII string with a per-device secret (HMAC-SHA256). The
canonical string is built only from integers and short tokens so the ESP32 firmware and
this module produce byte-identical strings.
"""
from __future__ import annotations

import hashlib
import hmac
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from pydantic import ValidationError

from ..schemas import NodeMessage, NodeWire, message_from_wire


def parse_secret(text: str) -> bytes:
    """A node secret as written in the server's secrets file. The firmware's secrets.h holds 64 hex characters
    (32 random bytes) and signs with the decoded bytes, so a 64-hex-character value here is decoded the same way.
    Any other text (the demo secrets) is used as its UTF-8 bytes."""
    t = text.strip()
    if len(t) == 64 and all(c in "0123456789abcdefABCDEF" for c in t):
        return bytes.fromhex(t)
    return t.encode("utf-8")


def canonical_string(w: NodeWire | dict) -> str:
    g = (lambda k: w[k]) if isinstance(w, dict) else (lambda k: getattr(w, k))
    return "|".join(str(g(k)) for k in ("n", "b", "s", "t", "k", "st", "c", "ca", "cm", "p", "f"))


def sign(secret: bytes, w: NodeWire | dict) -> str:
    return hmac.new(secret, canonical_string(w).encode("ascii"), hashlib.sha256).hexdigest()


def make_wire(secret: bytes, *, n: str, b: int, s: int, t_ms: int, k: str, st: str, c: int,
              ca: str, cm: str, p: int, f: str) -> dict:
    body = dict(n=n, b=b, s=s, t=t_ms, k=k, st=st, c=c, ca=ca, cm=cm, p=p, f=f)
    body["sig"] = sign(secret, body)
    return body


@dataclass
class NodeState:
    last_boot: int = -1
    last_seq: int = -1
    last_msg: Optional[NodeMessage] = None
    last_seen_t: Optional[float] = None
    state_since: Optional[float] = None
    first_seen_t: Optional[float] = None
    events: deque = field(default_factory=lambda: deque(maxlen=200))  # accepted state changes


class NodeRegistry:
    def __init__(self, secrets: dict[str, bytes], *, max_skew_s: float = 60.0,
                 heartbeat_interval_s: float = 10.0):
        self.secrets = dict(secrets)
        self.max_skew_s = max_skew_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.nodes: dict[str, NodeState] = {nid: NodeState() for nid in secrets}
        self.rejected: deque[tuple[float, str, str]] = deque(maxlen=500)  # (t, node, reason)

    def _reject(self, now: float, node: str, reason: str):
        self.rejected.append((now, node, reason))
        return False, reason, None

    def accept(self, wire: dict, now: float):
        """Returns (accepted, reason, message). Every rejection is remembered as evidence."""
        try:
            w = NodeWire.model_validate(wire)
        except ValidationError:
            return self._reject(now, str(wire.get("n", "?"))[:16] if isinstance(wire, dict) else "?", "malformed")
        secret = self.secrets.get(w.n)
        if secret is None:
            return self._reject(now, w.n, "unknown_node")
        if not hmac.compare_digest(sign(secret, w), w.sig):
            return self._reject(now, w.n, "bad_signature")
        st = self.nodes[w.n]
        if (w.b, w.s) <= (st.last_boot, st.last_seq):
            return self._reject(now, w.n, "replay")
        if abs(now - w.t / 1000.0) > self.max_skew_s:
            return self._reject(now, w.n, "stale_timestamp")
        msg = message_from_wire(w, received_t=now)
        prev = st.last_msg
        st.last_boot, st.last_seq = w.b, w.s
        st.last_msg = msg
        st.last_seen_t = now
        if st.first_seen_t is None:
            st.first_seen_t = now
        if prev is None or prev.state != msg.state:
            st.state_since = msg.ts
            st.events.append((msg.ts, msg.state, msg.confidence))
        return True, "ok", msg

    def latest(self, node_id: str) -> Optional[NodeMessage]:
        st = self.nodes.get(node_id)
        return st.last_msg if st else None

    def silent(self, node_id: str, now: float) -> bool:
        """A node that has gone quiet is itself evidence: trust can't be green."""
        st = self.nodes.get(node_id)
        if st is None or st.last_seen_t is None:
            return True
        return (now - st.last_seen_t) > 3 * self.heartbeat_interval_s

    def rejected_recent(self, now: float, window_s: float) -> list[tuple[float, str, str]]:
        return [r for r in self.rejected if now - r[0] <= window_s]
