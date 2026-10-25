/* Web demo only: the same moment seen four ways.
 *
 *   1  what the plant's screen claims
 *   2  what the witnesses report
 *   3  what HydroWitness concludes
 *   4  what actually happened in the simulation
 *
 * Columns 1 to 3 are read from the dashboard's own state, so they are exactly what the real software produced.
 * Column 4 is the simulation's own record of itself, saved next to each frame by hydrowitness/sim/web_recordings.py.
 * A real plant has no column 4: HydroWitness never sees it, and neither does anything else on a real site.
 *
 * Needs nothing from the network. Hidden when no drill is running (there is no simulation to compare with).
 */
(function () {
  "use strict";
  var host = null, lastHtml = "";
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  function dur(sec) {
    if (sec == null) return "unknown";
    sec = Math.max(0, Math.round(sec));
    if (sec < 90) return sec + " s";
    var m = Math.round(sec / 60);
    if (m < 90) return m + " min";
    return Math.floor(m / 60) + " h " + (m % 60) + " min";
  }
  var word = function (v) { return v ? "running" : "stopped"; };

  var CSS = "#compare{margin:0 0 1.5rem}#compare h2{margin-bottom:.4rem}" +
    "#compare .cmp-note{color:var(--muted);font-size:.92rem;max-width:80ch;margin:0 0 .8rem}" +
    "#compare .cmp-grid{display:grid;gap:.8rem;grid-template-columns:minmax(0,1fr)}" +
    "@media(min-width:700px){#compare .cmp-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}" +
    "@media(min-width:1180px){#compare .cmp-grid{grid-template-columns:repeat(4,minmax(0,1fr))}}" +
    "#compare .cmp-col{background:var(--panel);border-top:5px solid var(--line-strong);padding:.75rem .9rem 1rem;min-width:0}" +
    "#compare .cmp-col.c1{border-top-color:var(--muted)}#compare .cmp-col.c2{border-top-color:var(--water)}" +
    "#compare .cmp-col.c3{border-top-color:var(--state,var(--line-strong))}#compare .cmp-col.c4{border-top-color:var(--drill)}" +
    "#compare h3{font-size:1rem;margin:0 0 .15rem}#compare .cmp-sub{color:var(--muted);font-size:.85rem;margin:0 0 .55rem}" +
    "#compare ul{list-style:none;margin:0;padding:0}#compare li{padding:.3rem 0;border-top:1px solid var(--line);font-size:.95rem}" +
    "#compare li:first-child{border-top:0}#compare .k{color:var(--muted);font-size:.85rem;display:block}" +
    "#compare .bad{color:var(--alarm);font-weight:700}#compare .ok{color:var(--ok);font-weight:700}" +
    "#compare .big{font-weight:800;font-size:1.25rem;letter-spacing:-.01em}" +
    "#compare .cmp-bottom{margin-top:.8rem;padding:.6rem .9rem;border-left:6px solid var(--drill);background:color-mix(in srgb,var(--drill) 12%,var(--panel));max-width:90ch}";

  function ensure() {
    if (host) return host;
    var main = document.querySelector("main"), layout = document.querySelector(".layout");
    if (!main || !layout) return null;
    var st = document.createElement("style"); st.id = "compare-style"; st.textContent = CSS; document.head.appendChild(st);
    host = document.createElement("section");
    host.id = "compare"; host.setAttribute("aria-labelledby", "h-cmp"); host.hidden = true;
    main.insertBefore(host, layout);
    return host;
  }

  function column(cls, title, sub, items) {
    return '<div class="cmp-col ' + cls + '" role="group" aria-label="' + esc(title) + '"><h3>' + esc(title) + '</h3><p class="cmp-sub">' + esc(sub) + "</p><ul>" +
      items.map(function (x) { return "<li>" + x + "</li>"; }).join("") + "</ul></div>";
  }

  function build(s, pumps) {
    var t = s.truth, sc = s.scada, ds = s.drill || {}, st = s.trust.state, c = s.clock;
    // 1 the screen
    var screen = [];
    if (!sc) screen.push("Nothing has arrived from the screen.");
    else {
      pumps.forEach(function (p) {
        var v = sc.pump_status ? sc.pump_status[p] : null;
        screen.push("<b>" + esc(p) + ":</b> " + (v == null ? "unknown" : word(v)));
      });
      screen.push("<b>Tank:</b> " + (sc.tank_level_ft == null ? "n/a" : sc.tank_level_ft.toFixed(1) + " ft"));
      screen.push("<b>Pressure:</b> " + (sc.pressure_psi == null ? "n/a" : sc.pressure_psi.toFixed(1) + " psi"));
      screen.push('<span class="k">Last update</span>' + dur(sc.age_s) + " ago" + (sc.stale ? " (stale)" : ""));
    }
    // 2 the witnesses
    var wit = [];
    (s.nodes || []).forEach(function (n) {
      var txt = n.silent ? "silent" + (n.last_seen_age_s == null ? "" : " for " + dur(n.last_seen_age_s)) :
        n.state == null ? "waiting for its first message" : String(n.state).toLowerCase() + " (" + Math.round((n.confidence || 0) * 100) + "% sure)";
      wit.push("<b>Witness Node " + esc(n.node_id) + " (" + esc(n.pump_id) + "):</b> " + esc(txt) + (n.mains_ok === false ? " <b>on backup power</b>" : ""));
    });
    var seen = (s.manual_recent || []).filter(function (r) { return r.kind !== "customer_call"; });
    var calls = (s.manual_recent || []).filter(function (r) { return r.kind === "customer_call"; });
    if (!seen.length) wit.push("<b>People:</b> no one has looked yet");
    seen.slice(0, 2).forEach(function (r) {
      var what = r.kind === "pump_running" ? "saw " + esc(r.pump_id) + " " + word(r.value) : r.kind === "tank_level_ft" ? "read the tank at " + esc(r.value) + " ft" :
        r.kind === "gauge_psi" ? "read a gauge at " + esc(r.value) + " psi" : "confirmed flow from " + esc(r.pump_id);
      wit.push("<b>" + esc(r.operator || "operator") + "</b> " + what + " (" + dur(s.now - r.t) + " ago)");
    });
    if (calls.length) wit.push("<b>Customers:</b> " + calls.length + " call" + (calls.length === 1 ? "" : "s") + " about low pressure");
    // 3 HydroWitness
    var label = { green: "Green: screen and witnesses agree", amber: "Amber: check the screen against the pump", red: "Red: do not trust the screen" }[st];
    var hw = ['<span class="big">' + esc(label) + "</span>"];
    if (s.trust.cards && s.trust.cards[0]) hw.push('<span class="k">Main reason</span>' + esc(s.trust.cards[0].title));
    hw.push('<span class="k">Countdown (worst / expected)</span>' + esc(c.worst_text) + " / " + esc(c.expected_text) + (c.quality_text ? '<span class="k">' + esc(String(c.quality_text).split(". ")[0]) + ".</span>" : ""));
    var nc = s.next_check && s.next_check.primary;
    hw.push('<span class="k">Next useful check</span>' + (nc ? esc(nc.title) : "nothing needed"));
    if (ds.attack_started) hw.push('<span class="k">Since the attack began</span>amber ' + (ds.metrics.first_amber_s == null ? "not reached" : "after " + ds.metrics.first_amber_s + " s") +
      ", red " + (ds.metrics.first_red_s == null ? "not reached" : "after " + ds.metrics.first_red_s + " s"));
    // 4 the simulation
    var tr = [];
    pumps.forEach(function (p) {
      var x = t.pumps[p];
      tr.push("<b>" + esc(p) + ":</b> " + word(x.running) + ' <span class="k" style="display:inline">(' + esc(x.why) + ")</span>");
    });
    tr.push("<b>Tank:</b> " + t.level_ft.toFixed(1) + " ft");
    if (t.pressure_modelled) {
      tr.push("<b>Pressure at the farthest customer:</b> " + t.psi_critical.toFixed(1) + " psi (the line is " + t.psi_line + ")");
      tr.push('<span class="k">If nothing changes</span>' + (t.minutes_to_line == null ? "pressure stays above the line for at least " + t.horizon_h + " h" :
        t.minutes_to_line === 0 ? "pressure is already below the line" : "pressure falls below the line in " + dur(t.minutes_to_line * 60)));
    } else {
      tr.push("<b>Water reaching customers:</b> about " + t.supply_pct + "% of normal (a valve is closed)");
      tr.push('<span class="k">Pressure beyond the valve</span>The simulation does not work out a number for this. The tank and the transmitter gauge stay normal, which is why the screen looks fine.');
    }
    if (t.screen_wrong.length) tr.push('<span class="k">What is wrong with the screen</span><span class="bad">' + t.screen_wrong.map(esc).join("<br>") + "</span>");
    else tr.push('<span class="ok">The screen is telling the truth.</span>');
    if (t.events.length) tr.push('<span class="k">What was done to the plant</span>' + t.events.slice(-4).map(function (e) { return "+" + Math.round(e.after_attack_s) + " s " + esc(e.label); }).join("<br>"));
    // the one-line reading
    var wrong = t.screen_wrong.length > 0, bottom;
    if (wrong && st === "green") bottom = "The screen is already wrong, and HydroWitness has not caught it yet.";
    else if (wrong) bottom = "The screen is wrong, and HydroWitness has caught it: " + st + (ds.metrics && ds.metrics["first_" + (st === "red" ? "red" : "amber") + "_s"] != null ? " after " + ds.metrics["first_" + (st === "red" ? "red" : "amber") + "_s"] + " s" : "") + ".";
    else if (st === "green") bottom = "The screen is telling the truth, and HydroWitness agrees: green.";
    else bottom = "The screen is not wrong, but HydroWitness is " + st + ". That would be a false alarm.";
    return '<h2 id="h-cmp">The same moment, four ways</h2>' +
      '<p class="cmp-note">Columns 1 to 3 are what the real software saw and said. Column 4 is the simulation\'s own record of what was really happening. It is a simulated plant, and a real plant gives no column 4: HydroWitness never sees it.</p>' +
      '<div class="cmp-grid">' +
      column("c1", "1. What the screen claims", "Claims only. HydroWitness does not assume they are true.", screen) +
      column("c2", "2. What the witnesses report", "Independent of the screen.", wit) +
      column("c3", "3. What HydroWitness concludes", "From the first two.", hw) +
      column("c4", "4. What actually happened", "Simulation truth (simulated).", tr) + "</div>" +
      '<p class="cmp-bottom" role="status"><b>' + esc(bottom) + "</b></p>";
  }

  function tick() {
    var el = ensure();
    if (!el) return;
    var s = (typeof S !== "undefined" && S) ? S.s : null;
    if (!s || !s.truth || !s.drill || !s.trust) { el.hidden = true; return; }
    var pumps = (s.nodes || []).map(function (n) { return n.pump_id; });
    Object.keys(s.truth.pumps).forEach(function (p) { if (pumps.indexOf(p) < 0) pumps.push(p); });
    pumps.sort();
    var html = build(s, pumps);
    el.hidden = false;
    if (html !== lastHtml) { el.innerHTML = html; lastHtml = html; }
  }
  setInterval(tick, 500);
  window.addEventListener("load", tick);
})();
