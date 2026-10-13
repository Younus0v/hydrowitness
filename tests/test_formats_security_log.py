import json

import pytest

from hydrowitness.evidence_log import EvidenceLog, GENESIS, verify_records
from hydrowitness.schemas import NodeWire
from hydrowitness.security.nodesig import NodeRegistry, canonical_string, make_wire, sign

NOW = 1_800_000_000.0


def wire(secret, *, b=1, s=1, t=NOW, st="R", ca="R", cm="R", c=940, k="hb", p=1, n="NA"):
    return make_wire(secret, n=n, b=b, s=s, t_ms=int(t * 1000), k=k, st=st, c=c, ca=ca, cm=cm, p=p, f="a1b2c3d4")


def test_canonical_string_is_fixed_format():
    w = NodeWire(n="NA", b=1, s=2, t=1234, k="hb", st="R", c=940, ca="R", cm="S", p=1, f="abcd", sig="0" * 64)
    assert canonical_string(w) == "NA|1|2|1234|hb|R|940|R|S|1|abcd"


def test_hmac_known_vector_for_firmware_parity():
    # The firmware test (docs/HARDWARE_STAGE.md) must produce exactly this digest.
    d = {"n": "NA", "b": 1, "s": 2, "t": 1234, "k": "hb", "st": "R", "c": 940, "ca": "R", "cm": "S", "p": 1, "f": "abcd"}
    assert sign(b"test-secret", d) == "91af1357657ce7801fa3aeb314281d8e8cdf301b58d373741e7b3e8f799e0584"


def test_accepts_valid_message(secrets):
    reg = NodeRegistry(secrets)
    ok, reason, msg = reg.accept(wire(secrets["NA"]), NOW)
    assert ok and reason == "ok"
    assert msg.state == "RUNNING" and msg.both_channels_agree and msg.mains_ok


def test_rejects_bad_signature_and_tampering(secrets):
    reg = NodeRegistry(secrets)
    w = wire(secrets["NA"])
    w["st"] = "S"  # attacker flips the state but can't re-sign
    assert reg.accept(w, NOW)[1] == "bad_signature"
    assert reg.accept(wire(b"wrong-secret"), NOW)[1] == "bad_signature"
    assert reg.accept(wire(secrets["NA"], n="ZZ"), NOW)[1] == "unknown_node"
    assert reg.accept({"garbage": 1}, NOW)[1] == "malformed"
    assert len(reg.rejected) == 4


def test_replay_and_ordering(secrets):
    reg = NodeRegistry(secrets)
    assert reg.accept(wire(secrets["NA"], s=5), NOW)[0]
    assert reg.accept(wire(secrets["NA"], s=5), NOW + 1)[1] == "replay"       # same message again
    assert reg.accept(wire(secrets["NA"], s=4), NOW + 1)[1] == "replay"       # older message
    assert reg.accept(wire(secrets["NA"], s=6, t=NOW + 2), NOW + 2)[0]
    # a reboot increments the boot counter, so seq may restart
    assert reg.accept(wire(secrets["NA"], b=2, s=0, t=NOW + 3), NOW + 3)[0]
    assert reg.accept(wire(secrets["NA"], b=1, s=99, t=NOW + 4), NOW + 4)[1] == "replay"


def test_stale_timestamp_rejected(secrets):
    reg = NodeRegistry(secrets)
    assert reg.accept(wire(secrets["NA"], t=NOW - 600), NOW)[1] == "stale_timestamp"


def test_silent_node(secrets):
    reg = NodeRegistry(secrets, heartbeat_interval_s=10)
    assert reg.silent("NA", NOW)                      # never heard from: silent
    reg.accept(wire(secrets["NA"]), NOW)
    assert not reg.silent("NA", NOW + 20)
    assert reg.silent("NA", NOW + 31)                 # three missed heartbeats


def test_state_since_tracks_changes(secrets):
    reg = NodeRegistry(secrets)
    reg.accept(wire(secrets["NA"], s=1, st="R"), NOW)
    reg.accept(wire(secrets["NA"], s=2, t=NOW + 5, st="R"), NOW + 5)
    assert reg.nodes["NA"].state_since == pytest.approx(NOW)
    reg.accept(wire(secrets["NA"], s=3, t=NOW + 10, st="S", ca="S", cm="S"), NOW + 10)
    assert reg.nodes["NA"].state_since == pytest.approx(NOW + 10)


# ----------------------------------------------------------------- evidence log


def test_chain_valid_and_links():
    log = EvidenceLog()
    for i in range(5):
        log.append("test", "event", {"i": i}, NOW + i)
    ok, bad, _ = log.verify()
    assert ok and bad is None
    assert log.records[0]["prev_hash"] == GENESIS
    assert log.records[3]["prev_hash"] == log.records[2]["hash"]


@pytest.mark.parametrize("attack", ["edit", "delete", "reorder", "truncate_middle"])
def test_tampering_is_detected(attack):
    log = EvidenceLog()
    for i in range(6):
        log.append("test", "event", {"i": i}, NOW + i)
    recs = json.loads(json.dumps(log.records))
    if attack == "edit":
        recs[2]["content"]["i"] = 999
    elif attack == "delete":
        del recs[2]
    elif attack == "reorder":
        recs[2], recs[3] = recs[3], recs[2]
    else:
        recs = recs[:2] + recs[4:]
    ok, bad, msg = verify_records(recs)
    assert not ok and bad is not None and "record" in msg


def test_file_persistence_and_anchor(tmp_path):
    anchors = []
    path = str(tmp_path / "log.jsonl")
    log = EvidenceLog(path, anchor_sink=anchors.append)
    log.append("a", "x", {"v": 1}, NOW)
    log.append("a", "x", {"v": 2}, NOW + 1)
    log.anchor(NOW + 2, "red")
    assert anchors[0]["head_hash"] == log.head_hash and anchors[0]["count"] == 2
    reloaded = EvidenceLog(path)
    assert reloaded.head_hash == log.head_hash and reloaded.verify()[0]
    assert log.maybe_daily_anchor(NOW + 3) is None          # anchored 1 s ago
    assert log.maybe_daily_anchor(NOW + 2 + 86401) is not None
