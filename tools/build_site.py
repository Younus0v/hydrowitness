"""Build the website's pages from tools/site_src/ into site/.

    python tools/build_site.py            # rewrite site/*.html
    python tools/build_site.py --check    # exit 1 if site/*.html are not what the sources produce

Each page is a source file in tools/site_src/ made of a few `key: value` lines, a line with `---`, then the page body.
_layout.html wraps every page in the same header and footer, so the menu and the footer are written once.
The results table is filled in from results/v2_drills.json, so it can't drift from the saved results.
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
