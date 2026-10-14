"""The website: every number on it matches the saved results, it loads nothing from other sites, its files exist,
and the pages are exactly what tools/build_site.py produces from tools/site_src/."""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = ["index", "how-it-works", "technology", "demo", "team", "contact"]
HTML = {p: (SITE / f"{p}.html").read_text(encoding="utf-8") for p in PAGES}
ALL = "\n".join(HTML.values())
V2 = json.loads((ROOT / "results" / "v2_drills.json").read_text())
V1_MD = (ROOT / "results" / "v1_wntr_validation.md").read_text()


def _builder():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pages_are_exactly_what_the_generator_produces():
    built = _builder().build()
    assert set(built) == {f"{p}.html" for p in PAGES}
    stale = [f for f, text in built.items() if (SITE / f).read_text(encoding="utf-8") != text]
    assert not stale, f"run `python tools/build_site.py`: {stale} are out of date"


def test_every_local_file_a_page_or_the_stylesheet_uses_exists():
    for name, text in HTML.items():
        refs = set(re.findall(r'(?:src|href)="([^"#:]+)"', text))
        refs = {r.split("?")[0] for r in refs if not r.startswith(("mailto", "http", "data"))}
        for r in refs:
            assert (SITE / r).is_file(), f"{name}.html uses {r}"
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    for r in re.findall(r"url\(([^)\"']+)\)", css):
        if not r.startswith(("data:", "http")):
            assert (SITE / "assets" / r).is_file(), r
    assert {"assets/site.css", "assets/site.js", "assets/replay-logic.js", "assets/replay.js", "assets/demo.js",
            "assets/img/logo-mark.png", "assets/img/logo-full.webp", "assets/img/dash_red.webp",
            "demo/app.html"} <= set(re.findall(r'(?:src|href)="([^"#:?]+)', ALL))


def test_pages_load_nothing_from_other_sites():
    assert not re.search(r'<(?:script|img|link|iframe)[^>]+(?:src|href)="https?://', ALL)
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    assert "url(http" not in css and "@import" not in css and "url(//" not in css
    # the demo's dashboard page too: it must not reach out either
    app = (SITE / "demo" / "app.html").read_text(encoding="utf-8")
    assert not re.search(r'<(?:script|img|link|iframe)[^>]+(?:src|href)="https?://', app)


def test_the_only_outside_links_are_the_two_cited_sources():
    urls = set(re.findall(r'href="(https?://[^"]+)"', ALL))
    assert urls == {
        "https://waterworld.com/residential-commercial/news/14306876/hacker-altered-chemicals-in-oldsmar-florida-water-supply",
        "https://nextgov.com/cybersecurity/2024/05/more-70-surveyed-water-systems-failed-meet-epa-cyber-standards/396727",
    }


TEAM_EMAIL = "hydrowitness@gmail.com"      # the team's public address, chosen by the team


def test_no_personal_details_on_any_page():
    low = ALL.lower().replace(TEAM_EMAIL, "")
    assert "gmail" not in low and "dagoo" not in low       # no other address, no computer or account names
    assert "younus" not in low                             # no names at all: the Team page lists roles only
    assert HTML["team"].count('<li class="rv"><b>') == 5 and "Five jobs" in HTML["team"]
    # the Contact page uses the real team address, shown as text and as a link, with no placeholder left
    assert f'href="mailto:{TEAM_EMAIL}">{TEAM_EMAIL}</a>' in HTML["contact"] and "example" not in HTML["contact"]


def test_every_page_has_the_basics():
    for name, text in HTML.items():
        assert text.startswith("<!doctype html>") and '<html lang="en">' in text, name
        assert 'name="viewport"' in text and 'name="description"' in text, name
        assert len(re.findall(r"<h1[ >]", text)) == 1, name
        assert len(re.findall(r"<title>[^<]{8,}</title>", text)) == 1, name
        assert 'href="#main"' in text and 'id="main"' in text, name
        for img in re.findall(r"<img\b[^>]*>", text):
            assert "alt=" in img, f"{name}: {img[:60]}"
        assert text.count('aria-current="page"') == 1, name


def test_fonts_are_self_hosted_and_licensed():
    fonts = {p.name for p in (SITE / "assets" / "fonts").iterdir()}
    assert sum(n.endswith(".woff2") for n in fonts) >= 6
    assert {"LICENSE-manrope-OFL.txt", "LICENSE-plex-mono-OFL.txt"} <= fonts
    assert not (SITE / "fonts").exists() and not (SITE / "img").exists()      # the old layout is gone


def test_the_page_can_be_framed_only_by_itself():
    cfg = json.loads((SITE / "vercel.json").read_text())
    h = {x["key"]: x["value"] for rule in cfg["headers"] if rule["source"] == "/(.*)" for x in rule["headers"]}
    assert h["X-Frame-Options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in h["Content-Security-Policy"]
    assert "default-src 'self'" in h["Content-Security-Policy"] and "http" not in h["Content-Security-Policy"]
    # Netlify reads site/_headers; it must say exactly the same thing as vercel.json
    got, section = {}, None
    for line in (SITE / "_headers").read_text().splitlines():
        if not line.strip():
            continue
        if not line.startswith(" "):
            section = line.strip()
        elif section == "/*":
            key, _, value = line.strip().partition(": ")
            got[key] = value
    assert got == h
    assert "/assets/*" in (SITE / "_headers").read_text()


def _row(op_key):
    d = V2["drills"][op_key]
    return d["amber_median_s"], d["red_median_s"]


def test_results_table_matches_the_saved_drills():
    html = HTML["technology"]
    rows = re.findall(r"<tr><td>([^<]+)</td><td class=\"num\">([^<]+)</td><td class=\"num\">([^<]+)</td>"
                      r"<td class=\"hide-sm\"><span class=\"bar\" style=\"--a:([\d.]+);--r:([\d.]+)\"", html)
    assert len(rows) == 6
    expect = {
        "Frozen screen": "frozen_screen|no operator",
        "Replayed data": "replay|no operator",
        "Alarms off, remote access cut, pump cycles changed": "colorado_style|no operator",
        "Power outage that looks like an attack": "power_loss_lookalike|no operator",
        "Pumps forced off behind a careful forgery": "forced_off_spoofed|operator",
        "A valve closed out of sight": "hidden_valve_closure|no operator",
    }
    for name, amber_txt, red_txt, a, r in rows:
        amber, red = _row(expect[name])
        assert (amber_txt == "none") == (amber is None), name
        if amber is not None:
            assert amber_txt == f"{amber:g} s" and float(a) == amber / 2, name
        if red is not None:
            assert red_txt.startswith(f"{red:g} s") and float(r) == red / 2, name
        else:
            assert red_txt.startswith("never") and float(r) > 100, name
        if amber is None:
            assert float(a) == float(r), name                 # no amber band is drawn
    # the forgery only reaches red with an operator, and the page says so
    assert V2["drills"]["forced_off_spoofed|no operator"]["reached_red"] == 0
    assert "with an operator" in html


def test_other_claims_match_the_saved_results():
    n = V2["normal_operation"]
    assert (n["false_amber"], n["false_red"], n["pump_state_changes"], n["site_days"]) == (0, 0, 33, 10.0)
    assert "0 false amber and 0 false red over 10 site-days" in HTML["technology"] and "33 times" in HTML["technology"]
    assert "0 false alarms in 10 simulated site-days" in HTML["index"]
    assert "310 randomized systems" in V1_MD and "310 randomized systems" in HTML["technology"]
    assert "310 randomized EPANET systems" in HTML["index"]
    m = re.search(r"\| stress_demand \| (\d+) \| (\d+) \(", V1_MD)
    assert (m.group(2), m.group(1)) == ("8", "40") and "8 of 40 stress cases" in HTML["technology"]
    # the headline numbers in the facts strip are the saved medians
    assert f"red in {V2['drills']['frozen_screen|no operator']['red_median_s']:g} seconds" in HTML["index"]
    assert f"red in {V2['drills']['replay|no operator']['red_median_s']:g} seconds" in HTML["index"]


def test_the_test_count_on_the_page_is_the_readme_count():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    page = re.search(r"(\d+) automated tests pass", HTML["technology"]).group(1)
    assert f"{page} automated tests pass" in readme


def test_trust_points_on_the_page_are_the_engines_constants():
    from hydrowitness import config as C
    t = HTML["technology"]
    assert (C.AMBER_AT, C.RED_AT) == (1.0, 3.0) and "Amber starts at 1 point and red at 3" in t
    assert (C.W_OPERATOR, C.W_NODE_BOTH, C.W_NODE_ONE, C.W_CUSTOMER_EACH, C.W_CUSTOMER_CAP) == (3.0, 2.0, 1.0, 0.5, 1.5)
    for cell in ("3.0", "2.0", "1.0", "0.5, up to 1.5"):
        assert f'<td class="num">{cell}</td>' in t
    assert C.CUSTOMER_MIN_DISTINCT == 3 and "three or more" in t


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_replay_logic_matches_the_saved_drills():
    js = r"""
    const D = require(process.argv[1]);
    const out = {};
    for (const id of D.ORDER) {
      const sc = D.SCENARIOS[id], seq = [];
      for (let t = D.T_MIN; t <= D.T_MAX; t += 0.5) seq.push(D.trustAt(sc, t));
      const rank = { green: 0, amber: 1, red: 2 };
      const monotonic = seq.every((v, i) => i === 0 || rank[v] >= rank[seq[i - 1]]);   // red latches, never recovers
      const first = (c) => { for (let t = D.T_MIN; t <= D.T_MAX; t += 0.5) if (D.trustAt(sc, t) === c) return t; return null; };
      out[id] = { amber: first("amber"), red: first("red"), monotonic, start: seq[0],
                  headline: D.stateAt(id, D.T_MAX).headline, reasons: D.stateAt(id, D.T_MAX).reasons.length };
    }
    console.log(JSON.stringify(out));
    """
    res = subprocess.run(["node", "-e", js, str(SITE / "assets" / "replay-logic.js")], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    want = {"frozen": ("frozen_screen|no operator",), "replay": ("replay|no operator",), "forged": ("forced_off_spoofed|operator",)}
    for sid, (key,) in want.items():
        d = V2["drills"][key]
        assert got[sid]["amber"] == d["amber_median_s"], sid
        assert got[sid]["red"] == d["red_median_s"], sid
        assert got[sid]["monotonic"] and got[sid]["start"] == "green", sid
        assert got[sid]["headline"] == "Do not trust the screen" and got[sid]["reasons"] >= 1, sid


def test_headlines_are_the_dashboards_own_words():
    dash = (ROOT / "hydrowitness" / "static" / "index.html").read_text(encoding="utf-8")
    js = (SITE / "assets" / "replay-logic.js").read_text(encoding="utf-8")
    for words in ("Screen and witnesses agree", "Check the screen against the pump", "Do not trust the screen"):
        assert words in dash and words in js
        assert words.lower() in HTML["how-it-works"].lower()


def test_green_amber_red_colours_only_mean_trust():
    """The brand colour is blue. The state colours must not be used as decoration in the stylesheet."""
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    uses = [m.start() for m in re.finditer(r"var\(--(?:ok|warn|bad)(?:-tint|-ink)?\)", css)]
    for pos in uses:
        line = css[css.rfind("\n", 0, pos) + 1: css.find("\n", pos)]
        allowed = ("verdict", "--state", "--tint", "bar", "hist", ".pane", "border-top:8px")
        assert any(a in line for a in allowed) or line.startswith("  --"), line.strip()[:90]
