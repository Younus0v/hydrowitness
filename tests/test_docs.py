"""The evidence documents: every file they point to exists, and the numbers they quote are the saved results."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["VALIDATION_MATRIX.md", "LIMITATIONS.md", "THREAT_MODEL.md", "FMEA.md", "PUMP_TEST_PROTOCOL.md", "RESEARCH_BASIS.md",
        "industry/README.md", "industry/INTERVIEW_GUIDE.md", "industry/SURVEY.md", "industry/PILOT_PROPOSAL.md", "industry/OUTREACH.md"]
STATS = json.loads((ROOT / "results" / "statistics.json").read_text(encoding="utf-8"))
PATH = re.compile(r"^(?:docs|results|tools|tests|training|hydrowitness|firmware|measurements|site)/[A-Za-z0-9_./-]*$")


@pytest.mark.parametrize("name", DOCS)
def test_every_file_a_document_points_to_exists(name):
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    missing = []
    for token in re.findall(r"`([^`\s]+)`", text):
        token = token.rstrip(".,;:")
        if PATH.match(token) and not (ROOT / token).exists():
            missing.append(token)
    assert not missing, f"{name} points to files that do not exist: {missing}"


def test_the_validation_matrix_quotes_the_saved_statistics():
    text = (ROOT / "docs" / "VALIDATION_MATRIX.md").read_text(encoding="utf-8")
    fa = STATS["false_alarms"]
    assert (fa["false_amber"], fa["false_red"]) == (0, 0) and f"{fa['site_days']:g} simulated healthy site-days" in text
    ins = STATS["v1_clock_vs_epanet"]["inside_assumptions"]["unsafe"]
    assert f"{ins['k']} unsafe of {ins['n']}" in text and f"{ins['hi']:.1%}" in text
    al = STATS["attacks"]["without_operator_all_scenarios"]
    assert f"{al['hydrowitness_warned']['k']} of {al['hydrowitness_warned']['n']} runs" in text
    assert f"{al['alarm_panel']['k']} of {al['runs']}" in text and f"{al['alarm_panel_flatline']['k']} of {al['runs']}" in text
    c = STATS["countdown"]
    assert f"{c['checkpoints_scored']:,} scored checkpoints" in text and f"{c['runs_with_a_scored_checkpoint']} runs" in text


def test_the_pump_trial_is_never_described_as_a_result():
    for name in ("VALIDATION_MATRIX.md", "LIMITATIONS.md", "FMEA.md", "PUMP_TEST_PROTOCOL.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8").lower()
        assert "no data" in text or "no recorded" in text or "not recorded" in text or "unrecorded" in text, name
    matrix = (ROOT / "docs" / "VALIDATION_MATRIX.md").read_text(encoding="utf-8")
    assert "Reported, not recorded" in matrix and "No data was kept" in matrix


def test_the_industry_kit_reports_no_results_and_protects_people():
    kit = "\n".join((ROOT / "docs" / "industry" / n).read_text(encoding="utf-8") for n in ("README.md", "INTERVIEW_GUIDE.md", "SURVEY.md", "OUTREACH.md"))
    assert "Nothing in this folder reports a result" in kit
    assert "Never ask for a password" in kit or "Never ask for a password, a network diagram" in kit
    assert "adult" in kit.lower() and "without your name" in kit
    log = (ROOT / "docs" / "industry" / "OUTREACH_LOG.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1, "the outreach log is a template: it must not contain invented contacts"


def test_every_claim_status_in_the_matrix_is_one_of_the_defined_words():
    text = (ROOT / "docs" / "VALIDATION_MATRIX.md").read_text(encoding="utf-8")
    words = {"Supported in simulation", "Shown by tests", "Measured on the bench", "Reported, not recorded", "Not tested"}
    for line in text.splitlines():
        if re.match(r"^\| [A-E]\d+ ", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            status = cells[3] if len(cells) > 3 else ""
            if not any(w.lower() in status.lower() for w in words | {"sourced", "checked once", "measured on one computer", "partly tested"}):
                raise AssertionError(f"unrecognised status in: {line[:100]}")
