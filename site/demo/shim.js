/* Web demo backend.
 *
 * The page next to this file is the real HydroWitness dashboard. It normally talks to a Python server through /api/...
 * Here those calls are answered from recordings of the real engines running each drill on the simulated plant
 * (see hydrowitness/sim/web_recordings.py). No server, no network except the static files in this folder.
 *
 * Only what a recording can answer is supported: choosing a drill, pausing, skipping, changing speed, checking the
 * evidence log. Anything that would change the plant or add a reading gets a plain explanation instead.
 *
 * Address options:  app.html?scenario=forced_off_spoofed&op=1     (op=1 plays the scripted operator)
 *                   app.html?scenario=none                        (start from the empty screen and pick a drill)
 */
(function () {
  "use strict";
  var real = window.fetch.bind(window);
  var BASE = new URL(".", document.currentScript.src).href;
  var memo = {};
  var run = null;                                   // { rec, elapsed, speed, running, wall }
  var q = new URLSearchParams(location.search);

  var NOT_HERE = "Breaking things yourself and entering your own readings need the downloadable version. " +
                 "Here you can pick a drill, tick the scripted operator, pause, skip and change the speed.";

  function load(rel) {
    if (!memo[rel]) memo[rel] = (async function () {
      var res = await real(BASE + rel);
      if (!res.ok) throw new Error("Could not load " + rel);
      if (rel.slice(-3) !== ".gz") return res.json();
      if (typeof DecompressionStream === "undefined") throw new Error("This browser is too old to open the recordings.");
      return JSON.parse(await new Response(res.body.pipeThrough(new DecompressionStream("gzip"))).text());
    })();
    return memo[rel];
  }

  function reply(obj, status) {
    return new Response(JSON.stringify(obj), { status: status || 200, headers: { "Content-Type": "application/json" } });
  }
  function fail(detail, status) { return reply({ detail: detail }, status || 400); }

  async function startRun(scenario, operator, speed) {
    var rec = await load("rec/" + scenario + "-" + (operator ? "op" : "noop") + ".json.gz");
    run = { rec: rec, elapsed: 0, speed: speed || 5, running: true, wall: performance.now() };
  }

  function tick() {
    var now = performance.now();
    if (run.running) run.elapsed += (now - run.wall) / 1000 * run.speed;
    run.wall = now;
  }

  function current() {
    tick();
    var n = run.rec.frames.length, i = Math.min(Math.floor(run.elapsed / run.rec.dt), n - 1);
    var f = JSON.parse(JSON.stringify(run.rec.frames[i]));
    f.drill.speed = run.speed;
    f.drill.running = run.running && i < n - 1;     // the recording ends after 12 simulated minutes
    return f;
  }

  var ready = (async function () {
    var idx = await load("index.json");
    var first = q.get("scenario") || "frozen_screen";
    if (first !== "none" && idx.scenarios.some(function (s) { return s.id === first; })) {
      await startRun(first, q.get("op") === "1", 5);
    }
    return idx;
  })();

  window.fetch = async function (input, init) {
    var url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.pathname.indexOf("/api/") !== 0) return real(input, init);
    var method = ((init && init.method) || "GET").toUpperCase();
    var body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    try {
      var idx = await ready, path = url.pathname;
      if (method === "GET") {
        if (path === "/api/state") return reply(run ? current() : idx.live);
        if (path === "/api/profile") return reply(idx.profile);
        if (path === "/api/drill/scenarios") return reply({ scenarios: idx.scenarios, actions: {} });
        if (path === "/api/log/verify") return reply((run ? current() : idx.live)._verify);
        if (path === "/api/health") {
          return reply({ ok: true, version: idx.version, auth_required: false, demo_config: true, drill_active: !!run,
                         live_armed: false, read_only: true, web_demo: true });
        }
        if (path === "/api/log/export") return fail("Downloading the log needs the downloadable version.");
        return fail("Not found", 404);
      }
      if (path === "/api/drill/start") {
        if (!idx.scenarios.some(function (s) { return s.id === body.scenario; })) return fail("unknown scenario", 404);
        await startRun(body.scenario, !!body.operator_script, body.speed);
        return reply({ ok: true });
      }
      if (path === "/api/drill/stop") {
        if (!run) return fail("no drill is running", 409);
        run = null;
        return reply({ ok: true });
      }
      if (path === "/api/drill/control") {
        if (!run) return fail("no drill is running", 409);
        tick();
        if (body.speed != null) run.speed = Number(body.speed);
        if (body.running != null) run.running = !!body.running;
        return reply({ ok: true, speed: run.speed, running: run.running });
      }
      if (path === "/api/drill/step") {
        if (!run) return fail("no drill is running", 409);
        tick();
        run.elapsed += Number(url.searchParams.get("seconds") || 30);
        return reply({ ok: true });
      }
      return fail(NOT_HERE);                        // inject, manual readings, playbook steps, acknowledge, setup
    } catch (e) {
      return fail(e && e.message ? e.message : "The web demo hit a problem.", 500);
    }
  };
})();
