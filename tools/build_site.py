"""Build the website's pages from tools/site_src/ into site/.

    python tools/build_site.py            # rewrite site/*.html
    python tools/build_site.py --check    # exit 1 if site/*.html are not what the sources produce

Each page is a source file in tools/site_src/ made of a few `key: value` lines, a line with `---`, then the page body.
_layout.html wraps every page in the same header and footer, so the menu and the footer are written once.
The results table is filled in from results/v2_drills.json, the comparison with a plain alarm panel from results/evaluation.json,
and the hardware status from results/measurements.json, so none of those numbers or claims can drift from the saved results.
Standard library only.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "site_src"
SITE = ROOT / "site"
V2 = ROOT / "results" / "v2_drills.json"
EVALUATION = ROOT / "results" / "evaluation.json"
MEASUREMENTS = ROOT / "results" / "measurements.json"

# The team's public contact address. To change it, edit this line, then run `python tools/build_site.py`.
EMAIL = "hydrowitness@gmail.com"

PAGES = [  # file, menu label
    ("index.html", "Home"),
    ("how-it-works.html", "How it works"),
    ("technology.html", "Technology"),
    ("demo.html", "Demo"),
    ("team.html", "Team"),
    ("contact.html", "Contact us"),
]

# Rows of the results table: label, saved-results key. The bars run from 0 to 200 seconds.
ROWS = [
    ("Frozen screen", "frozen_screen|no operator"),
    ("Replayed data", "replay|no operator"),
    ("Alarms off, remote access cut, pump cycles changed", "colorado_style|no operator"),
    ("Power outage that looks like an attack", "power_loss_lookalike|no operator"),
    ("Pumps forced off behind a careful forgery", "forced_off_spoofed|operator"),
    ("A valve closed out of sight", "hidden_valve_closure|no operator"),
]


def results_table() -> str:
    d = json.loads(V2.read_text(encoding="utf-8"))["drills"]
    out = []
    for label, key in ROWS:
        r = d[key]
        amber, red = r["amber_median_s"], r["red_median_s"]
        a_txt = "none" if amber is None else f"{amber:g} s"
        if red is None:
            r_txt = "never, by design"
        elif key.endswith("|operator") and d[key.replace("|operator", "|no operator")]["red_median_s"] is None:
            r_txt = f"{red:g} s, with an operator"
        else:
            r_txt = f"{red:g} s"
        a_w = (red if amber is None else amber) / 2        # no amber step: the amber band has no width
        r_w = 999 if red is None else red / 2
        out.append(
            f'<tr><td>{html.escape(label)}</td><td class="num">{a_txt}</td><td class="num">{r_txt}</td>'
            f'<td class="hide-sm"><span class="bar" style="--a:{a_w:g};--r:{r_w:g}" aria-hidden="true"></span></td></tr>')
    return "\n          ".join(out)


# ---- the comparison with a plain alarm panel, written from results/evaluation.json --------------------------------------
EVAL_ROWS = [
    ("Frozen screen", "frozen_screen|no operator"),
    ("Replayed data", "replay|no operator"),
    ("Alarms off, remote access cut, pump cycles changed", "colorado_style|no operator"),
    ("Power outage that looks like an attack", "power_loss_lookalike|no operator"),
    ("Pumps forced off behind a careful forgery", "forced_off_spoofed|no operator"),
    ("A valve closed out of sight", "hidden_valve_closure|no operator"),
]


def _secs(v: float) -> str:
    return f"{v:g} s"


def _got(d: dict) -> str:
    """'20 of 20, median 65 s' or 'none of 20'."""
    if not d["detected"]:
        return f"none of {d['runs']}"
    return f"{d['detected']} of {d['runs']}, median {_secs(d['median_s'])}"


def _sum_countdown(*parts: dict) -> dict:
    keys = ("checkpoints", "unavailable", "not_modelled_valve", "scored", "worst_case_bound_held", "early", "close", "late")
    return {k: sum(p[k] for p in parts) for k in keys}


def evaluation_fields() -> dict[str, str]:
    res = json.loads(EVALUATION.read_text(encoding="utf-8"))
    s, c = res["summary"], res["config"]
    rows = []
    for label, key in EVAL_ROWS:
        d = s["attacks"][key]["detection"]
        red = d["hydrowitness_red"]
        red_txt = "no red" if not red["detected"] else f"red in {_secs(red['median_s'])}"
        rows.append(f'<tr><td>{html.escape(label)}</td><td class="num">{_got(d["hydrowitness_warned"])}<br><span class="fine">{red_txt}</span></td>'
                    f'<td class="num">{_got(d["alarm"])}</td><td class="num">{_got(d["alarm_flatline"])}</td></tr>')
    t = s["totals"]
    n_runs = t["attack_runs_without_operator"]
    hw_hit, al_hit, fl_hit = (n_runs - t[k] for k in ("hydrowitness_missed_without_operator", "alarm_missed_without_operator",
                                                      "alarm_flatline_missed_without_operator"))
    missed = (f"Across the {n_runs} runs where nobody walks to the pump, HydroWitness raised a warning in {hw_hit}. The plain alarm panel "
              f"raised an alarm in {al_hit}, and the panel with the flat-line check in {fl_hit}.")
    n = s["normal_operation"]
    hw_false = n["hydrowitness_false_amber"] + n["hydrowitness_false_red"]
    false = (f"On a healthy simulated plant, {n['site_days']:g} site-days across two tank sizes, with pumps really starting or stopping "
             f"{n['pump_state_changes']} times: HydroWitness raised {hw_false} false warnings, the plain alarm panel {n['alarm_false']}, and the panel with "
             f"the flat-line check {n['alarm_flatline_false']}.")
    k = _sum_countdown(t["countdown"], n["countdown"])
    bound = ("never later than the simulated plant's true time" if k["worst_case_bound_held"] == k["scored"]
             else f"later than the simulated plant's true time in {k['scored'] - k['worst_case_bound_held']} of them")
    late = ("never later than the true time" if not k["late"] else f"later than the true time in {k['late']}")
    countdown = (f"In {k['scored']:,} scored countdown checks, the worst-case figure was {bound}. The expected-case figure was {late}, "
                 f"{'and ' if not k['late'] else ''}early in {k['early']:,} (cautious, by design) and close in {k['close']:,}. "
                 f"It said \"estimate unavailable\" in {k['unavailable']} of {k['checkpoints']:,} checks. Closed-valve runs are not scored, "
                 f"because the simulation does not work out the pressure beyond a valve.")
    f = s["failure_conditions"]
    one, both, rep_ = f["one_witness_jammed"]["detection"], f["both_witnesses_jammed"]["detection"], f["replay_witnesses_jammed"]["detection"]
    nruns = both["runs"]
    failure = (f"When we jam the witnesses as well, HydroWitness still warns ({_got(both['hydrowitness_warned'])}), but only because a Witness Node "
               f"went silent. It cannot tell what the pumps are doing, so it reaches red in {both['hydrowitness_red']['detected']} of {nruns} runs with a "
               f"forged screen, and in {rep_['hydrowitness_red']['detected']} of {nruns} when the screen replays old data (the repeated sequence gives it away).")
    return {
        "EVAL_TABLE": "\n          ".join(rows), "EVAL_MISSED_TEXT": html.escape(missed), "EVAL_FALSE_TEXT": html.escape(false),
        "EVAL_COUNTDOWN_TEXT": html.escape(countdown, quote=False), "EVAL_FAILURE_TEXT": html.escape(failure),
        "EVAL_RUNS_PER_ROW": str(s["attacks"][EVAL_ROWS[0][1]]["detection"]["runs"]),
        "EVAL_SEEDS": str(len(c["seeds"])), "EVAL_HORIZON_MIN": f"{c['attack_horizon_s'] / 60:g}",
        "EVAL_LOW_LEVEL_FT": f"{c['alarm_panel']['low_level_ft']:g}", "EVAL_LOW_PSI": f"{c['alarm_panel']['low_pressure_psi']:g}",
        "EVAL_NO_DATA_S": f"{c['alarm_panel']['no_data_after_s']:g}", "EVAL_FLAT_S": f"{c['alarm_panel']['flat_line_after_s']:g}",
    }


def hardware_status() -> str:
    """What we can honestly say about real hardware, from results/measurements.json."""
    c = json.loads(MEASUREMENTS.read_text(encoding="utf-8"))["counted"]
    if c["development"] + c["evaluation"] == 0:
        if c["bench"]:
            return ("The firmware has booted on a real ESP32 development board. It has not yet been recorded on a real pump, so we have no "
                    "real measurement of how well it tells a pump's on from its off. Our team also reports an unrecorded trial on a pump "
                    "owned by someone outside the team, and says it worked. We do not count that as a result, because there is no recording of it.")
        return "The firmware has not yet run on a real board, and we have no real measurement of a pump."
    return (f"We have {c['development']} development and {c['evaluation']} evaluation recording(s) from a real pump. "
            "The measurement report lists exactly what they show, and what they do not.")


def parse(text: str) -> tuple[dict[str, str], str]:
    head, sep, body = text.partition("\n---\n")
    assert sep, "page source needs a line with --- after its settings"
    meta = {}
    for line in head.strip().splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body.strip("\n")


def nav(current: str) -> str:
    items = []
    for i, (file, label) in enumerate(PAGES):
        cur = ' aria-current="page"' if file == current else ""
        cls = ' class="menu-cta"' if i == len(PAGES) - 1 else ""
        items.append(f'<li><a{cls} href="{file}"{cur}>{label}</a></li>')
    return "\n        ".join(items)


def foot_links() -> str:
    return "\n        ".join(f'<li><a href="{f}">{l}</a></li>' for f, l in PAGES)


def build() -> dict[str, str]:
    layout = (SRC / "_layout.html").read_text(encoding="utf-8")
    EVAL, HW_STATUS = evaluation_fields(), hardware_status()
    out: dict[str, str] = {}
    for file, _ in PAGES:
        meta, body = parse((SRC / file).read_text(encoding="utf-8"))
        scripts = "".join(f'\n<script src="{s.strip()}"></script>' for s in meta.get("scripts", "").split(",") if s.strip())
        page = layout
        for key, value in {
            "TITLE": html.escape(meta["title"], quote=False),
            "DESCRIPTION": html.escape(meta["description"]),
            "NAV": nav(file),
            "FOOT_LINKS": foot_links(),
            "SCRIPTS": scripts,
            "BODY": body,
        }.items():
            page = page.replace("{{" + key + "}}", value)
        page = page.replace("{{RESULTS_TABLE}}", results_table()).replace("{{EMAIL}}", EMAIL)
        page = page.replace("{{HW_STATUS_TEXT}}", html.escape(HW_STATUS))
        for key, value in EVAL.items():
            page = page.replace("{{" + key + "}}", value)
        assert "{{" not in page, f"{file}: an unfilled placeholder is left"
        out[file] = page if page.endswith("\n") else page + "\n"
    return out


def main(argv: list[str]) -> int:
    pages = build()
    if "--check" in argv:
        stale = [f for f, t in pages.items() if not (SITE / f).is_file() or (SITE / f).read_text(encoding="utf-8") != t]
        if stale:
            print("out of date, run python tools/build_site.py:", ", ".join(stale))
            return 1
        print("site pages are up to date")
        return 0
    for f, t in pages.items():
        (SITE / f).write_text(t, encoding="utf-8", newline="\n")
        print(f"site/{f}  {len(t) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
