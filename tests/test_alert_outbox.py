"""Alerts that cannot be delivered are kept and retried, so an internet or ntfy outage never silently loses one."""
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from hydrowitness import config as C
from hydrowitness.alerts import AlertManager, ListSink, NtfySink
from hydrowitness.api import AppState
from hydrowitness.demo import DEMO_SECRETS, demo_profile
from hydrowitness.service import HydroWitnessCore

ROOT = Path(__file__).resolve().parent.parent
CONTACTS = ["boss", "backup"]


class Flaky:
    """A channel that is down until told otherwise. Records every message it was given."""

    def __init__(self, up=False):
        self.up, self.calls = up, []

    def __call__(self, alert):
        self.calls.append(alert)
        return self.up


def raise_always(alert):
    raise ConnectionError("no route to host")


def red(am, now=1000.0):
    am.on_state(now, "green", "red", "Test Site", "Pump 2 stopped", CONTACTS)
    return am.history[-1]


# ------------------------------------------------------------------------------- the outbox
def test_a_working_channel_delivers_at_once_and_says_so():
    sink = ListSink()
    am = AlertManager([sink])
    a = red(am)
    assert a["status"] == "sent" and a["sent_t"] == 1000.0 and a["tries"] == 1
    assert len(sink.sent) == 1 and am.delivery(1000.0)["channel"] == "ok"


def test_a_failed_alert_is_kept_waiting_not_dropped():
    down = Flaky()
    am = AlertManager([down])
    a = red(am)
    assert a["status"] == "waiting" and a["tries"] == 1 and len(down.calls) == 1
    d = am.delivery(1010.0)
    assert d["channel"] == "waiting" and d["waiting"] == 1
    assert d["items"][0]["title"] == "HydroWitness RED at Test Site" and d["items"][0]["age_s"] == 10.0


def test_a_sink_that_raises_counts_as_failed_and_never_breaks_the_monitor():
    am = AlertManager([raise_always])
    assert red(am)["status"] == "waiting"


def test_a_sink_that_returns_nothing_counts_as_delivered():
    got = []
    am = AlertManager([got.append])                  # plain callables, as the older code and tests use them
    assert red(am)["status"] == "sent" and len(got) == 1


def test_it_tries_again_with_growing_gaps_and_stops_at_the_cap():
    down = Flaky()
    am = AlertManager([down])
    a = red(am, 1000.0)
    waits = []
    t = 1000.0
    for _ in range(7):
        waits.append(a["next_try_t"] - t)
        assert am.deliver_pending(t + 1) == 0         # not yet
        t = a["next_try_t"]
        assert am.deliver_pending(t) == 1
    assert waits == [30.0, 60.0, 120.0, 240.0, 240.0, 240.0, 240.0] and a["tries"] == 8


def test_when_the_channel_comes_back_the_waiting_alert_goes_out_once_and_says_it_was_late():
    chan = Flaky()
    am = AlertManager([chan])
    a = red(am, 1000.0)
    chan.up = True
    am.deliver_pending(1000.0 + C.ALERT_RETRY_S)
    assert a["status"] == "sent" and a["tries"] == 2
    assert chan.calls[0]["message"] == "Pump 2 stopped"
    assert chan.calls[1]["message"] == "Pump 2 stopped (Delayed: first tried 30 s ago.)"
    assert am.deliver_pending(5000.0) == 0 and len(chan.calls) == 2         # never sent twice
    assert am.delivery(5000.0)["channel"] == "ok" and am.last_sent_t == 1030.0


def test_only_the_channel_that_failed_is_tried_again():
    good, flaky = ListSink(), Flaky()
    am = AlertManager([good, flaky])
    a = red(am)
    flaky.up = True
    am.deliver_pending(1100.0)
    assert a["status"] == "sent" and len(good.sent) == 1 and len(flaky.calls) == 2


def test_an_alert_older_than_a_day_stops_being_retried_and_is_counted_as_never_delivered():
    down = Flaky()
    am = AlertManager([down])
    a = red(am, 1000.0)
    n = len(down.calls)
    assert am.deliver_pending(1000.0 + C.ALERT_KEEP_S + 1) == 0
    assert a["status"] == "expired" and len(down.calls) == n
    d = am.delivery(1000.0 + C.ALERT_KEEP_S + 1)
    assert d["expired"] == 1 and d["waiting"] == 0


def test_with_no_channel_at_all_the_alert_is_marked_as_having_nowhere_to_go():
    am = AlertManager([])
    a = red(am)
    assert a["status"] == "no_channel" and am.delivery(1000.0)["channel"] == "none"
    assert am.deliver_pending(2000.0) == 0


def test_the_escalation_goes_through_the_same_outbox():
    down = Flaky()
    am = AlertManager([down], escalate_after_s=300)
    red(am, 1000.0)
    am.tick(1301.0, True, "Test Site", "Pump 2 stopped", CONTACTS)
    esc = am.history[-1]
    assert esc["kind"] == "escalation" and esc["to"] == ["backup"] and esc["status"] == "waiting"


def test_the_history_stays_small_and_the_retry_list_stays_in_step_with_it():
    down = Flaky()
    am = AlertManager([down])
    for i in range(230):
        red(am, 1000.0 + i)
    assert len(am.history) == 200 and len(am._todo) == 200


# ------------------------------------------------------------------ live server: sending is off the monitor's path
def test_in_deferred_mode_nothing_is_sent_until_the_delivery_thread_runs():
    sink = ListSink()
    am = AlertManager([sink], deferred=True)
    a = red(am, 1000.0)
    assert a["status"] == "waiting" and sink.sent == []
    am.tick(1001.0, True, "Test Site", "x", CONTACTS)               # the monitor's own tick does not send either
    assert sink.sent == []
    assert am.deliver_pending(1001.0) == 1 and len(sink.sent) == 1 and a["status"] == "sent"


def test_a_server_with_background_work_uses_deferred_delivery_and_a_test_server_does_not():
    assert AppState(demo_profile(), dict(DEMO_SECRETS), deferred_alerts=True).live.alerts.deferred is True
    assert AppState(demo_profile(), dict(DEMO_SECRETS)).live.alerts.deferred is False


# ---------------------------------------------------------------------------------- ntfy itself
def test_ntfy_counts_only_an_accepted_message_as_delivered(monkeypatch):
    import httpx
    sink = NtfySink("https://ntfy.example/", "topic", "tok")
    alert = {"title": "t", "message": "m", "level": "red"}
    seen = {}

    def ok(url, **kw):
        seen.update(url=url, **kw)
        return NS(status_code=200)

    monkeypatch.setattr(httpx, "post", ok)
    assert sink(alert) is True
    assert seen["url"] == "https://ntfy.example/topic" and seen["headers"]["Priority"] == "urgent"
    assert seen["headers"]["Authorization"] == "Bearer tok"

    monkeypatch.setattr(httpx, "post", lambda *a, **k: NS(status_code=401))      # wrong token: refused, not delivered
    assert sink(alert) is False

    def unreachable(*a, **k):
        raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr(httpx, "post", unreachable)                               # the internet is down
    assert sink(alert) is False


# ----------------------------------------------------------------------------- inside the core
def test_the_dashboard_view_carries_the_delivery_status():
    down = Flaky()
    core = HydroWitnessCore(demo_profile(), DEMO_SECRETS, clock=lambda: 5000.0, alert_sinks=[down])
    core.alerts_enabled = False                     # as the live app starts: nobody is paged before real data arrives
    v = core.evaluate(5000.0)
    assert v["alert_delivery"] == {"channel": "ok", "waiting": 0, "items": [], "last_sent_t": None, "expired": 0}
    core.alerts.on_state(5000.0, "green", "red", "Demo", "Pump 2 stopped", ["boss"])
    v = core.evaluate(5001.0)
    assert v["alert_delivery"]["channel"] == "waiting" and v["alert_delivery"]["waiting"] == 1
    assert v["alerts"][0]["status"] == "waiting"


def test_a_core_with_no_channel_says_so():
    core = HydroWitnessCore(demo_profile(), DEMO_SECRETS, clock=lambda: 5000.0)
    assert core.evaluate(5000.0)["alert_delivery"]["channel"] == "none"


# ----------------------------------------------------------------- the decision: private ntfy only
def test_there_is_no_sms_or_twilio_in_the_project():
    needle = "twi" + "lio"                              # spelled in two pieces so this file does not match itself
    hits = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if (not p.is_file() or p.suffix in (".gz", ".png", ".webp", ".woff2", ".pyc", ".ttf") or "__pycache__" in rel.parts
                or any(part.startswith(".") for part in rel.parts) or p == Path(__file__).resolve()):
            continue                                  # caches, .git and .venv are not the project
        try:
            if needle in p.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(str(p.relative_to(ROOT)))
        except OSError:
            pass
    assert not hits, hits


def test_the_only_code_that_talks_to_the_network_is_the_private_alert_channel():
    users = []
    for p in (ROOT / "hydrowitness").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if any(w in text for w in ("httpx", "requests", "urllib", "smtplib", "socket", "aiohttp", "http.client")):
            users.append(p.name)
    assert users == ["alerts.py"], users
