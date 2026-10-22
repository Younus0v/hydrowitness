"""Alerts with escalation (project document section 5.9).

Alerts use one PRIVATE channel: a self-hosted ntfy server with access control. There is no SMS, no phone
number and no third-party messaging service. Public ntfy topics are protected only by the topic name, so
they are NOT used for real deployments.
If nobody acknowledges within 5 minutes, the alert escalates to a second contact.

An alert that cannot be delivered (the channel is down, the internet is out, the token was refused) is not
lost. It stays in the outbox as "waiting" and is tried again, with longer gaps each time, for a day. The
dashboard shows what is waiting so that a person can phone the contacts instead.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from . import config as C

log = logging.getLogger("hydrowitness.alerts")
ORDER = {"green": 0, "amber": 1, "red": 2}


class ListSink:
    """Collects alerts in memory (tests, drills, the dashboard's alert list)."""

    def __init__(self):
        self.sent: list[dict] = []

    def __call__(self, alert: dict) -> None:
        self.sent.append(alert)


def _ago(sec: float) -> str:
    sec = max(0, round(sec))
    if sec < 90:
        return f"{sec} s"
    m = round(sec / 60)
    if m < 90:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min"


class NtfySink:
    """POST to a self-hosted ntfy server. Needs a token; never point this at ntfy.sh with a guessable topic.

    Returns True only if the server accepted the message, False if it could not be reached or refused it,
    so that the alert can be tried again later."""

    def __init__(self, base_url: str, topic: str, token: str, timeout: float = 3.0):
        self.url, self.topic, self.token, self.timeout = base_url.rstrip("/"), topic, token, timeout

    def __call__(self, alert: dict) -> bool:
        try:
            import httpx

            r = httpx.post(f"{self.url}/{self.topic}", content=alert["message"].encode("utf-8"), timeout=self.timeout,
                           headers={"Authorization": f"Bearer {self.token}", "Title": alert["title"],
                                    "Priority": "urgent" if alert["level"] == "red" else "high"})
        except Exception as exc:  # an alert failure must never take the monitor down
            log.warning("ntfy alert failed: %s", exc)
            return False
        if r.status_code >= 400:
            log.warning("ntfy refused the alert: HTTP %s", r.status_code)
            return False
        return True


class AlertManager:
    """Decides when to alert and keeps an outbox of what could not be delivered.

    A sink is any callable that takes the alert dict. It counts as failed if it returns False or raises;
    anything else (including None) counts as delivered. ``deferred=True`` is for the live server: sending
    then happens in ``deliver_pending`` on a background thread, so a dead network can never stall the
    monitor. Drills and tests leave it off and send at once."""

    def __init__(self, sinks: Optional[list[Callable[[dict], Optional[bool]]]] = None,
                 escalate_after_s: float = C.ESCALATE_AFTER_S, *, deferred: bool = False):
        self.sinks = sinks or []
        self.escalate_after_s = escalate_after_s
        self.deferred = deferred
        self._pending_since: Optional[float] = None
        self._escalated = False
        self.history: list[dict] = []
        self.last_sent_t: Optional[float] = None
        self._lock = threading.Lock()
        self._seq = 0
        self._todo: dict[int, list[int]] = {}      # alert id -> the sinks that have not accepted it yet

    # ------------------------------------------------------------------ the outbox
    def _send(self, alert: dict) -> None:
        with self._lock:
            self._seq += 1
            alert = {**alert, "id": self._seq, "status": "waiting" if self.sinks else "no_channel",
                     "tries": 0, "next_try_t": alert["t"], "sent_t": None}
            self.history.append(alert)
            if self.sinks:
                self._todo[alert["id"]] = list(range(len(self.sinks)))
            if len(self.history) > 200:
                del self.history[:-200]
                keep = {a["id"] for a in self.history}
                self._todo = {k: v for k, v in self._todo.items() if k in keep}
        if self.sinks and not self.deferred:
            self._attempt(alert, alert["t"])

    def _attempt(self, alert: dict, now: float) -> None:
        with self._lock:
            todo = list(self._todo.get(alert["id"], []))
            tries = alert["tries"]
        if not todo:
            return
        out = alert
        if tries:
            out = {**alert, "message": f"{alert['message']} (Delayed: first tried {_ago(now - alert['t'])} ago.)"}
        accepted = []
        for i in todo:                                # network work happens here, with no lock held
            try:
                ok = self.sinks[i](out)
            except Exception as exc:
                log.warning("alert sink failed: %s", exc)
                ok = False
            if ok is not False:
                accepted.append(i)
        with self._lock:
            alert["tries"] = tries + 1
            left = [i for i in self._todo.get(alert["id"], []) if i not in accepted]
            if left:
                self._todo[alert["id"]] = left
                alert["next_try_t"] = now + min(C.ALERT_RETRY_MAX_S, C.ALERT_RETRY_S * 2 ** tries)
            else:
                self._todo.pop(alert["id"], None)
                alert["status"], alert["sent_t"] = "sent", now
                self.last_sent_t = now

    def deliver_pending(self, now: float) -> int:
        """Try every waiting alert whose time has come; give up on ones older than a day. Returns how many it tried."""
        with self._lock:
            due = []
            for a in self.history:
                if a["status"] != "waiting":
                    continue
                if now - a["t"] > C.ALERT_KEEP_S:
                    a["status"] = "expired"
                    self._todo.pop(a["id"], None)
                elif now >= a["next_try_t"]:
                    due.append(a)
        for a in due:
            self._attempt(a, now)
        return len(due)

    def delivery(self, now: float) -> dict:
        """What the dashboard shows: is the private channel working, and what is still waiting to go out."""
        with self._lock:
            waiting = [a for a in self.history if a["status"] == "waiting"]
            items = [{"id": a["id"], "t": a["t"], "kind": a["kind"], "level": a["level"], "title": a["title"],
                      "to": a["to"], "tries": a["tries"], "age_s": round(now - a["t"], 1)}
                     for a in waiting[-5:][::-1]]
            return {"channel": "none" if not self.sinks else ("waiting" if waiting else "ok"),
                    "waiting": len(waiting), "items": items, "last_sent_t": self.last_sent_t,
                    "expired": sum(1 for a in self.history if a["status"] == "expired")}

    # ------------------------------------------------------------------ when to alert
    def on_state(self, now: float, prev: str, state: str, site: str, headline: str, contacts: list[str]) -> None:
        if ORDER[state] > ORDER[prev]:
            self._pending_since, self._escalated = now, False
            self._send({"t": now, "kind": "alert", "level": state, "to": contacts[:1],
                        "title": f"HydroWitness {state.upper()} at {site}", "message": headline})

    def tick(self, now: float, needs_ack: bool, site: str, headline: str, contacts: list[str]) -> None:
        if not self.deferred:
            self.deliver_pending(now)
        if not needs_ack:
            self._pending_since = None
            return
        if (self._pending_since is not None and not self._escalated
                and now - self._pending_since >= self.escalate_after_s):
            self._escalated = True
            self._send({"t": now, "kind": "escalation", "level": "red", "to": contacts[1:2] or contacts[:1],
                        "title": f"ESCALATION: nobody has acknowledged HydroWitness at {site}",
                        "message": headline})
