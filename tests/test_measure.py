"""training/measure.py: sessions say what they are, the report counts only what the recordings support, and a held-out
evaluation session can never be trained on. Synthetic logs only: they test the bookkeeping, not any pump."""
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "training"))

import measure as M  # noqa: E402

from .test_training import make_log  # noqa: E402

STATUS = ("S,phase=2,state=R,conf=900,acc=R,mic=R,verdict_acc=2,verdict_mic=2,offline=0,mains=1,acc_rate=1000.4,acc_overflows=0,"
          "i2c_errs=3,mic_errs=0,epochs=120,sent=118,dropped=0,rejected=0,heap=172000,rssi=-61,time=1")
BOOT = "I,Witness Node NB boot 14 firmware 861255cb mains 60 Hz, not calibrated: hold the button 3 s with the pump OFF"
WHO = "W,MPU6050 WHO_AM_I is 0x72, expected 0x68 (a clone? continuing)"


@pytest.fixture
def base(tmp_path):
    return tmp_path / "measurements"


def pump_session(base, sid, purpose, seed, **over):
    fields = dict(pump="0.5 hp sump pump", mounting="on the motor housing, bare metal", board="ESP32 dev board", date="2026-11-02")
    fields.update(over)
    d = M.new_session(sid, purpose, base=base, **fields)
    make_log(d / "serial.log", seed=seed)
    return d


# ---- sessions ------------------------------------------------------------------------------------------------------------
def test_a_session_is_a_folder_with_what_it_is_and_is_never_overwritten(base):
    d = M.new_session("2026-11-02-sump-1", "development", base=base, pump="sump pump", mounting="on the housing")
    assert sorted(p.name for p in d.iterdir()) == ["events.csv", "serial.log", "session.json"]
    meta = json.loads((d / "session.json").read_text())
    assert meta["purpose"] == "development" and meta["pump"] == "sump pump" and meta["schema"] == M.SCHEMA
    with pytest.raises(FileExistsError):
        M.new_session("2026-11-02-sump-1", "development", base=base)


@pytest.mark.parametrize("bad", ["", "a", "../escape", "has space", "x" * 80])
def test_session_ids_cannot_escape_the_folder(base, bad):
    with pytest.raises(ValueError):
        M.session_dir(bad, base)


def test_the_purpose_must_be_one_of_the_three(base):
    with pytest.raises(ValueError):
        M.new_session("2026-11-02-x", "practice", base=base)


def test_development_and_evaluation_sessions_need_a_pump_a_mounting_and_a_board(base):
    d = M.new_session("2026-11-02-sump-1", "evaluation", base=base)
    probs = M.problems(M.load_session(d))
    assert len(probs) == 3 and all("does not say" in p for p in probs)
    assert M.problems(M.load_session(M.new_session("2026-11-02-bench", "bench", base=base))) == []


# ---- capture -------------------------------------------------------------------------------------------------------------
def stamp():
    t = dt.datetime(2026, 11, 2, 9, 0, 0)
    return lambda: t


def test_capture_keeps_the_nodes_lines_exactly_and_logs_what_the_person_did(base):
    d = M.new_session("2026-11-02-sump-1", "development", base=base, pump="p", mounting="m", board="b")
    sent = []
    cap = M.Capture(d, send=sent.append, now=stamp())
    for ln in ["I,boot", "F,512,R,a,1,2\r\n", "", "S,phase=2"]:
        cap.on_line(ln)
    assert cap.on_command("r") and cap.on_command("S") and cap.on_command("-")
    assert cap.on_command("n pump started by hand at the breaker") and cap.on_command("what")
    assert cap.on_command("q") is False
    cap.close()
    assert (d / "serial.log").read_text() == "I,boot\nF,512,R,a,1,2\nS,phase=2\n"       # raw: train_classifier reads this file
    assert sent == ["label R", "label S", "label -"]
    ev = (d / "events.csv").read_text().splitlines()
    assert ev[0] == "host_time,kind,text" and "2026-11-02T09:00:00,capture_start," in ev
    assert "2026-11-02T09:00:00,note,pump started by hand at the breaker" in ev
    assert any(",unknown_input,what" in x for x in ev) and any(",capture_stop,3 lines" in x for x in ev)


def test_without_a_serial_port_a_label_is_recorded_as_not_sent(base):
    d = M.new_session("2026-11-02-sump-1", "development", base=base)
    cap = M.Capture(d, send=None, now=stamp())
    cap.on_command("r")
    cap.close()
    assert "label_not_sent" in (d / "events.csv").read_text()


def test_a_log_saved_from_a_terminal_can_be_imported(base, tmp_path):
    d = M.new_session("2026-11-02-bench", "bench", base=base)
    src = tmp_path / "monitor.txt"
    src.write_text(f"ets Jul 29 2019\n\n{BOOT}\n{WHO}\n")
    assert M.capture_file(d, src) == 3
    assert (d / "serial.log").read_text().splitlines()[1:] == [BOOT, WHO]


# ---- reading what the node said ------------------------------------------------------------------------------------------
def test_observations_read_the_boot_line_warnings_and_the_last_status():
    o = M.observations(["junk", BOOT, WHO, STATUS, "FATAL: neither sensor works"])
    assert o["boots"] == [{"node": "NB", "boot": 14, "firmware": "861255cb", "mains_hz": 60,
                           "calibration": "not calibrated: hold the button 3 s with the pump OFF"}]
    assert o["warnings"] == [WHO[2:]] and o["fatal"] == ["FATAL: neither sensor works"]
    assert o["status_lines"] == 1 and o["last_status"]["acc_rate"] == "1000.4" and o["last_status"]["i2c_errs"] == "3"


# ---- the report counts only what the recordings support --------------------------------------------------------------------
def test_with_no_sessions_the_report_says_no_measurements_were_supplied(base):
    rep = M.build_report(base)
    assert rep["sessions"] == [] and rep["counted"] == {"bench": 0, "development": 0, "evaluation": 0}
    assert rep["headline"].startswith("No measurements from a real pump have been supplied.")


def test_a_bench_boot_log_is_not_pump_evidence_but_is_reported_as_hardware_evidence(base, tmp_path):
    d = M.new_session("2026-10-25-bench-boot-NB", "bench", base=base, board="ESP32-S, 38 pins")
    (d / "serial.log").write_text(f"{BOOT}\n{WHO}\n")
    rep = M.build_report(base)
    assert rep["counted"]["bench"] == 1 and rep["counted"]["development"] == 0
    assert rep["sessions"][0]["counts_as_pump_evidence"] is False
    assert rep["headline"].startswith("No measurements from a real pump have been supplied.") and "booted on a real board" in rep["headline"]
    md = M.markdown(rep)
    assert "861255cb" in md and "WHO_AM_I is 0x72" in md and "says nothing about whether the node can tell" in md


def test_labelled_windows_without_a_described_pump_and_mounting_do_not_count(base):
    d = M.new_session("2026-11-02-sump-1", "development", base=base)          # nothing about the equipment written down
    make_log(d / "serial.log", seed=3)
    rep = M.build_report(base)
    assert rep["counted"]["development"] == 0 and rep["sessions"][0]["problems"]
    assert rep["headline"].startswith("No measurements from a real pump have been supplied.")


def test_development_recordings_alone_are_not_evidence_of_how_well_it_works(base):
    pump_session(base, "2026-11-02-sump-1", "development", 3)
    rep = M.build_report(base)
    assert rep["counted"]["development"] == 1 and rep["heldout"] is None
    assert "no held-out evaluation recordings" in rep["headline"]
    ch = rep["sessions"][0]["windows"]["channels"]["acc"]
    assert ch["running_windows"] > 10 and ch["stopped_windows"] > 10 and ch["running_runs"] >= 2


def test_held_out_scores_come_from_a_fit_that_never_saw_the_evaluation_recordings(base):
    pump_session(base, "2026-11-02-sump-1", "development", 3)
    pump_session(base, "2026-11-09-sump-2", "evaluation", 5)
    rep = M.build_report(base)
    h = rep["heldout"]
    assert rep["counted"] == {"bench": 0, "development": 1, "evaluation": 1}
    assert all(v["verdict"] != "NO DATA" for v in h.values()) and h["acc"]["running_correct"] > 0.9 and h["acc"]["stopped_correct"] > 0.9
    # change only the evaluation labels (swap running and stopped): the score must follow the evaluation data, not the fit
    ev = base / "2026-11-09-sump-2" / "serial.log"
    ev.write_text("\n".join(x.replace(",R,", ",X,").replace(",S,", ",R,").replace(",X,", ",S,") if x.startswith("F,") else x
                            for x in ev.read_text().splitlines()) + "\n")
    swapped = M.build_report(base)["heldout"]
    assert swapped["acc"]["running_correct"] < 0.1 and swapped["acc"]["stopped_correct"] < 0.1
    assert swapped["acc"]["development_running"] == h["acc"]["development_running"]       # the fit's inputs did not move


def test_too_little_evaluation_data_is_reported_as_no_data_not_as_a_score(base, tmp_path):
    dev, ev = tmp_path / "dev.log", tmp_path / "ev.log"
    make_log(dev, seed=3)
    make_log(ev, seed=5)
    h = M.heldout([dev.read_text().splitlines()], [ev.read_text().splitlines()[:120]])
    assert all(v["verdict"] == "NO DATA" for v in h.values()) and "running_correct" not in h["acc"]


def test_an_evaluation_session_with_no_labelled_windows_is_not_counted_at_all(base):
    pump_session(base, "2026-11-02-sump-1", "development", 3)
    d = pump_session(base, "2026-11-09-sump-2", "evaluation", 5)
    (d / "serial.log").write_text("\n".join((d / "serial.log").read_text().splitlines()[:30]) + "\n")
    rep = M.build_report(base)
    assert rep["counted"]["evaluation"] == 0 and rep["heldout"] is None
    assert "no held-out evaluation recordings" in rep["headline"]


def test_train_refuses_anything_that_is_not_a_development_session(base, monkeypatch, capsys):
    monkeypatch.setattr(M, "DIR", base)
    pump_session(base, "2026-11-02-sump-1", "development", 3)
    pump_session(base, "2026-11-09-sump-2", "evaluation", 5)
    M.new_session("2026-11-02-bench", "bench", base=base)
    assert M.main(["train", "2026-11-02-sump-1", "2026-11-09-sump-2"]) == 2
    assert "refusing: 2026-11-09-sump-2 is a evaluation session" in capsys.readouterr().out
    assert M.main(["train", "2026-11-02-bench"]) == 2
    assert M.main(["train", "2026-11-02-sump-1"]) in (0, 1, 2, 3)                          # development sessions are accepted


def test_the_command_line_creates_captures_and_reports(base, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(M, "DIR", base)
    monkeypatch.setattr(M, "RESULTS", tmp_path / "results")
    assert M.main(["new", "2026-11-02-bench", "--purpose", "bench", "--board", "ESP32-S"]) == 0
    src = tmp_path / "m.txt"
    src.write_text(BOOT + "\n")
    assert M.main(["capture", "2026-11-02-bench", "--from-file", str(src)]) == 0
    assert M.main(["capture", "2026-11-02-bench"]) == 2                                    # needs a port or a file
    assert M.main(["capture", "2026-11-02-nope", "--from-file", str(src)]) == 2
    assert M.main(["report"]) == 0
    assert (tmp_path / "results" / "measurements.md").read_text().startswith("# Real measurements")
    assert json.loads((tmp_path / "results" / "measurements.json").read_text())["counted"]["bench"] == 1


# ---- what is committed ---------------------------------------------------------------------------------------------------
def test_the_committed_report_is_the_committed_sessions_rendered():
    assert (ROOT / "results" / "measurements.md").read_text() == M.markdown(M.build_report())
    assert json.loads((ROOT / "results" / "measurements.json").read_text()) == json.loads(json.dumps(M.build_report(), default=str))


def test_the_committed_bench_boot_log_is_what_the_board_printed_and_claims_no_pump():
    rep = M.build_report()
    bench = next(s for s in rep["sessions"] if s["id"] == "2026-10-25-bench-boot-NB")
    assert bench["purpose"] == "bench" and bench["counts_as_pump_evidence"] is False
    assert bench["observations"]["boots"][0]["firmware"] == "861255cb" and bench["observations"]["boots"][0]["boot"] == 14
    assert bench["observations"]["fatal"] == [] and bench["windows"]["labelled"] == 0
    assert rep["counted"]["development"] == 0 and rep["counted"]["evaluation"] == 0
    assert rep["headline"].startswith("No measurements from a real pump have been supplied.")


def test_the_raw_serial_recordings_are_not_hidden_by_gitignore():
    """`*.log` is ignored so editor logs stay out of the repo; a session's serial.log is the evidence and must not be."""
    lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "*.log" in lines and "!measurements/**/serial.log" in lines
    assert lines.index("!measurements/**/serial.log") > lines.index("*.log")        # a later rule wins
