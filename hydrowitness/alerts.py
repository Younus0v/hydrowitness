"""Alerts with escalation (project document section 5.9).

Alerts use a PRIVATE channel: a self-hosted ntfy server with access control, or SMS. Public ntfy
topics are protected only by the topic name, so they are NOT used for real deployments.
If nobody acknowledges within 5 minutes, the alert escalates to a second contact.
"""
from __future__ import annotations

import json
import logging
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


class NtfySink:
    """POST to a self-hosted ntfy server. Needs a token; never point this at ntfy.sh with a guessable topic."""

    def __init__(self, base_url: str, topic: str, token: str, timeout: float = 3.0):
        self.url, self.topic, self.token, self.timeout = base_url.rstrip("/"), topic, token, timeout

    def __call__(self, alert: dict) -> None:
        try:
            import httpx

            httpx.post(f"{self.url}/{self.topic}", content=alert["message"].encode("utf-8"), timeout=self.timeout,
                       headers={"Authorization": f"Bearer {self.token}", "Title": alert["title"],
                                "Priority": "urgent" if alert["level"] == "red" else "high"})
        except Exception as exc:  # an alert failure must never take the monitor down
            log.warning("ntfy alert failed: %s", exc)


class AlertManager:
    def __init__(self, sinks: Optional[list[Callable[[dict], None]]] = None,
                 escalate_after_s: float = C.ESCALATE_AFTER_S):
        self.sinks = sinks or []
        self.escalate_after_s = escalate_after_s
        self._pending_since: Optional[float] = None
        self._escalated = False
        self.history: list[dict] = []

    def _send(self, alert: dict) -> None:
        self.history.append(alert)
        for s in self.sinks:
            s(alert)

    def on_state(self, now: float, prev: str, state: str, site: str, headline: str, contacts: list[str]) -> None:
        if ORDER[state] > ORDER[prev]:
            self._pending_since, self._escalated = now, False
            self._send({"t": now, "kind": "alert", "level": state, "to": contacts[:1],
                        "title": f"HydroWitness {state.upper()} at {site}", "message": headline})

    def tick(self, now: float, needs_ack: bool, site: str, headline: str, contacts: list[str]) -> None:
        if not needs_ack:
            self._pending_since = None
            return
        if (self._pending_since is not None and not self._escalated
                and now - self._pending_since >= self.escalate_after_s):
            self._escalated = True
            self._send({"t": now, "kind": "escalation", "level": "red", "to": contacts[1:2] or contacts[:1],
                        "title": f"ESCALATION: nobody has acknowledged HydroWitness at {site}",
                        "message": headline})
