/* The three-pane drill replay on the home page. Needs replay-logic.js (window.HWDemo) and the markup in index.html.
 * Everything drawn is a pure function of one number: seconds since the attack began. */
(function () {
  "use strict";
  var D = window.HWDemo;
  var root = document.getElementById("demo");
  if (!D || !root) return;
  var $ = function (s) { return root.querySelector(s); };
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SPEED = 10;                       // simulated seconds per real second
  var X0 = 4, XW = 392;                 // chart drawing area in viewBox units
  var VIB = { mid: 66, half: 38 }, SND = { mid: 176, half: 38 };
  var INK = "#0B2530", GRID = "#D5E2E0", GRID_MAJOR = "#B9CCC9";

  var els = {
    blurb: $("#blurb"), timing: $("#timing"), range: $("#t"), play: $("#play"), label: $("#tlabel"),
    clock: $("#h-clock"), level: $("#h-level"), flow: $("#h-flow"), hline: $("#h-line"),
    vib: $("#tr-vib"), snd: $("#tr-snd"), marks: $("#marks"), atk: $("#atk"), reading: $("#reading"),
    verdict: $("#verdict"), light: $("#light"), word: $("#vword"), head: $("#vhead"), act: $("#vact"), why: $("#why")
  };

  var LIGHTS = {
    green: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#17744A"/><path d="M13 25l8 8 15-17" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    amber: '<svg viewBox="0 0 48 48"><path d="M24 4L46 42H2z" fill="#F0B323" stroke="#3B2600" stroke-width="2.5" stroke-linejoin="round"/><path d="M24 17v13" stroke="#3B2600" stroke-width="5" stroke-linecap="round"/><circle cx="24" cy="36" r="2.8" fill="#3B2600"/></svg>',
    red: '<svg viewBox="0 0 48 48"><polygon points="15,2 33,2 46,15 46,33 33,46 15,46 2,33 2,15" fill="#B3261E"/><path d="M16 16l16 16M32 16L16 32" stroke="#fff" stroke-width="5" stroke-linecap="round"/></svg>'
  };
  var WORD = { green: "Green", amber: "Amber", red: "Red" };

  var id = "frozen", t = D.T_MIN, playing = false, last = 0, raf = 0, cache = {};

  function sx(time) { return X0 + (time - D.T_MIN) / (D.T_MAX - D.T_MIN) * XW; }
  function set(el, key, value, setter) {      // write to the page only when the value changed
    if (cache[key] === value) return;
    cache[key] = value; setter(el, value);
  }
  function text(el, key, value) { set(el, key, value, function (e, v) { e.textContent = v; }); }

  function buildGrid() {
    var g = "";
    for (var s = -20; s <= 130; s += 10) {
      var major = s % 30 === 0;
      g += '<line x1="' + sx(s).toFixed(1) + '" y1="20" x2="' + sx(s).toFixed(1) + '" y2="206" stroke="' + (major ? GRID_MAJOR : GRID) + '" stroke-width="1" vector-effect="non-scaling-stroke"/>';
      if (major && s >= 0) g += '<text class="tick" x="' + sx(s).toFixed(1) + '" y="221" text-anchor="middle">' + (s ? "+" : "") + s + ' s</text>';
    }
    [VIB.mid, SND.mid].forEach(function (y) {
      g += '<line x1="' + X0 + '" y1="' + y + '" x2="' + (X0 + XW) + '" y2="' + y + '" stroke="' + GRID_MAJOR + '" stroke-width="1" vector-effect="non-scaling-stroke"/>';
    });
    $("#grid").innerHTML = g;
    els.atk.setAttribute("x1", sx(0)); els.atk.setAttribute("x2", sx(0));
  }

  function trace(fn, band, sc, upto) {
    var pts = [];
    for (var u = D.T_MIN; u <= upto; u += 0.5) {
      pts.push(sx(u).toFixed(1) + "," + (band.mid - fn(sc, u) * band.half).toFixed(1));
    }
    return pts.join(" ");
  }

  function drawMarks(sc) {
    var out = "";
    sc.marks.forEach(function (m) {
      if (m.at > t && m.at !== 0) return;
      var x = sx(m.at), right = x > 230;
      var y = 30 + m.row * 14;
      if (m.at !== 0) out += '<line x1="' + x.toFixed(1) + '" y1="20" x2="' + x.toFixed(1) + '" y2="206" stroke="' + INK + '" stroke-width="1" vector-effect="non-scaling-stroke"/>';
      out += '<text x="' + (x + (right ? -4 : 4)).toFixed(1) + '" y="' + y + '" text-anchor="' + (right ? "end" : "start") + '" paint-order="stroke" stroke="#F4F8F7" stroke-width="3">' + m.text + '</text>';
    });
    els.marks.innerHTML = out;
  }

  function fmtT(v) {
    var s = Math.round(v);
    return v < 0 ? "Before the attack, " + s.toString().replace("-", "−") + " s" : "Attack +" + s + " s";
  }

  function render() {
    var sc = D.SCENARIOS[id], st = D.stateAt(id, t);
    var sec = Math.round(t * 2) / 2;

    els.range.value = t;
    text(els.label, "tl", fmtT(t));
    els.range.setAttribute("aria-valuetext", fmtT(t) + ", trust " + WORD[st.trust].toLowerCase());

    // what SCADA says
    text(els.clock, "clock", D.pollClock(t));
    text(els.level, "level", D.level(sc, t).toFixed(2) + " ft");
    text(els.flow, "flow", D.flow(sc, t) + " gpm");
    var pts = [];
    for (var u = Math.max(D.T_MIN, t - 60); u <= t; u += 1) {
      var yy = 26 - (D.level(sc, u) - 14.2) * 34;
      pts.push(((u - (t - 60)) / 60 * 200).toFixed(1) + "," + yy.toFixed(1));
    }
    els.hline.setAttribute("points", pts.join(" "));

    // what the pipes say
    els.vib.setAttribute("points", trace(D.vibration, VIB, sc, sec));
    els.snd.setAttribute("points", trace(D.sound, SND, sc, sec));
    drawMarks(sc);
    var running = D.pumpRunning(sc, t);
    text(els.reading, "reading", running ? "Pump 2 is shaking and loud: running." : "Pump 2 has gone still and quiet: stopped.");

    // what HydroWitness says
    set(els.verdict, "trust", st.trust, function (e, v) { e.setAttribute("data-state", v); els.light.innerHTML = LIGHTS[v]; });
    text(els.word, "word", WORD[st.trust]);
    text(els.head, "head", st.headline);
    text(els.act, "act", st.action);
    set(els.why, "why", st.reasons.map(function (r) { return r.text; }).join("|"), function (e) {
      e.innerHTML = "";
      st.reasons.forEach(function (r) { var li = document.createElement("li"); li.textContent = r.text; e.appendChild(li); });
    });
  }

  function pct(s) { return ((s - D.T_MIN) / (D.T_MAX - D.T_MIN) * 100).toFixed(2) + "%"; }

  function describe(sc) {
    els.blurb.textContent = sc.blurb;
    var line;
    if (sc.amberAt === null) line = "No amber step: straight to red " + sc.redAt + " s after the attack starts.";
    else line = "Amber " + sc.amberAt + " s after the attack starts, red at " + sc.redAt + " s.";
    if (sc.operatorAt !== null) line += " Red needs a person to read the pump.";
    els.timing.textContent = line;
    var a = sc.amberAt === null ? pct(sc.redAt) : pct(sc.amberAt), r = pct(sc.redAt);
    els.range.style.setProperty("--hist", "linear-gradient(90deg,#17744A 0 " + a + ",#F0B323 0 " + r + ",#B3261E 0)");
  }

  function frame(ts) {
    if (!playing) return;
    t = Math.min(D.T_MAX, t + (ts - last) / 1000 * SPEED);
    last = ts;
    render();
    if (t >= D.T_MAX) { stop(); return; }
    raf = requestAnimationFrame(frame);
  }
  function play() {
    if (playing) return;
    if (t >= D.T_MAX) t = D.T_MIN;
    playing = true; last = performance.now(); els.play.textContent = "Pause";
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    playing = false; cancelAnimationFrame(raf);
    els.play.textContent = t >= D.T_MAX ? "Replay" : "Play";
  }

  function choose(next, autoplay) {
    id = next; stop(); cache = {};
    root.querySelectorAll("[data-scen]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-scen") === id)); });
    describe(D.SCENARIOS[id]);
    t = D.T_MIN; render();
    if (autoplay && !reduce) play();
  }

  root.querySelectorAll("[data-scen]").forEach(function (b) {
    b.addEventListener("click", function () { choose(b.getAttribute("data-scen"), true); });
  });
  els.play.addEventListener("click", function () { if (playing) stop(); else play(); });
  els.range.addEventListener("input", function () { stop(); t = Number(els.range.value); els.play.textContent = t >= D.T_MAX ? "Replay" : "Play"; render(); });

  buildGrid();
  choose("frozen", false);
  if (reduce) { t = D.T_MAX; render(); els.play.textContent = "Replay"; }
  else if ("IntersectionObserver" in window) {      // it plays once, the first time it comes into view
    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) { io.disconnect(); play(); }
    }, { threshold: 0.4 });
    io.observe(root);
  }
})();
