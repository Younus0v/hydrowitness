/* HydroWitness drill replay: everything on screen is a pure function of one number, the time since the attack began.
 *
 * The amber/red times below are the medians of the saved drill results (results/v2_drills.json: simulated plant,
 * simulated Witness Node, five seeds per scenario). tests/test_site.py checks that they still match that file.
 * The tank and pump numbers on the SCADA screen and the shapes of the sensor traces are illustrations, not data.
 */
(function (root) {
  "use strict";

  var T_MIN = -20;   // seconds before the attack starts (everything agrees)
  var T_MAX = 130;   // seconds after it starts

  var HEADLINE = {   // the same words the real dashboard uses
    green: "Screen and witnesses agree",
    amber: "Check the screen against the pump",
    red: "Do not trust the screen"
  };

  var ACTION = {
    green: "Nothing to do.",
    amber: "Go and look at Pump 2 before you rely on the screen.",
    red: "Read the gauge and the sight glass yourself, then open the Lockout Playbook."
  };

  var SCENARIOS = {
    frozen: {
      id: "frozen",
      label: "Frozen screen",
      blurb: "An attacker locks the operator out. The screen keeps ticking, but its numbers stop moving. Then Pump 2 quietly stops.",
      screen: "frozen",
      pumpStopsAt: 45, amberAt: 65, redAt: 95, operatorAt: null,
      marks: [{ at: 0, text: "attack starts", row: 0 }, { at: 45, text: "pump stops", row: 0 }],
      reasons: [
        { at: 65, level: "amber", text: "Pump 2: the Witness Node says it stopped, but SCADA says it is running." },
        { at: 95, level: "red", text: "The disagreement kept going and SCADA's numbers have stopped changing. Treat the screen as untrusted." }
      ]
    },
    replay: {
      id: "replay",
      label: "Replayed data",
      blurb: "Recorded data from 30 minutes earlier is played back so the screen looks alive. Pump 2 is stopped underneath it.",
      screen: "replay",
      pumpStopsAt: 30, amberAt: null, redAt: 50, operatorAt: null,
      marks: [{ at: 0, text: "attack starts", row: 0 }, { at: 30, text: "pump stops", row: 0 }],
      reasons: [
        { at: 50, level: "red", text: "SCADA is repeating old data, and the Witness Node says Pump 2 stopped while SCADA says it is running. This goes straight to red." }
      ]
    },
    forged: {
      id: "forged",
      label: "Forged screen",
      blurb: "Pump 2 is forced off, and every number on the screen is faked to look normal. Nothing on the screen gives it away.",
      screen: "forged",
      pumpStopsAt: 0, amberAt: 20, redAt: 120, operatorAt: 120,
      marks: [{ at: 0, text: "attack starts, pump forced off", row: 0 }, { at: 120, text: "operator reads the pump", row: 1 }],
      reasons: [
        { at: 20, level: "amber", text: "Pump 2: the Witness Node says it stopped, but SCADA says it is running. One witness against the screen, so a person should look." },
        { at: 120, level: "red", text: "The operator confirmed Pump 2 is stopped. SCADA is wrong." }
      ]
    }
  };

  var ORDER = ["frozen", "replay", "forged"];

  function trustAt(sc, t) {
    if (sc.redAt !== null && t >= sc.redAt) return "red";      // red latches: once reached it stays
    if (sc.amberAt !== null && t >= sc.amberAt) return "amber";
    return "green";
  }

  function pumpRunning(sc, t) { return t < sc.pumpStopsAt; }

  function reasonsAt(sc, t) {
    return sc.reasons.filter(function (r) { return t >= r.at; });
  }

  /* Small deterministic noise in [0, 1): the same time always draws the same trace. */
  function noise(i, seed) {
    var x = Math.sin(i * 12.9898 + seed * 78.233) * 43758.5453;
    return x - Math.floor(x);
  }

  /* Tank level the SCADA screen shows, in feet. */
  function level(sc, t) {
    var base = 14.2;
    var live = function (u) { return base + 0.35 * Math.sin(u / 9) + 0.12 * Math.sin(u / 3.1); };
    if (sc.screen === "frozen") return live(Math.min(t, 0));                    // identical values after the freeze
    if (sc.screen === "replay") return t < 0 ? live(t) : live(t + 41.7);         // plausible, but old
    return live(t) + 0.04 * (noise(Math.round(t * 2), 5) - 0.5);                 // forged: noisy and self-consistent
  }

  /* Pump 2 flow the screen shows, in gpm. */
  function flow(sc, t) {
    if (sc.screen === "frozen") return 183;
    return Math.round(183 + 6 * Math.sin(t / 5) + 4 * (noise(Math.round(t * 2), 9) - 0.5));
  }

  /* What the pipes do: a pump that runs shakes and sounds loud; a stopped one does not.
     Returns a signed trace value in [-1, 1] for the sample at time t. */
  function vibration(sc, t) {
    var i = Math.round(t * 2);
    var amp = pumpRunning(sc, t) ? 0.62 + 0.18 * noise(i, 1) : 0.025;
    return amp * (noise(i, 2) * 2 - 1);
  }

  function sound(sc, t) {
    var i = Math.round(t * 2);
    var amp = pumpRunning(sc, t - 1) ? 0.55 + 0.2 * noise(i, 3) : 0.03;     // one second of run-down
    return amp * (noise(i, 4) * 2 - 1);
  }

  /* The time the SCADA screen's clock shows, as hh:mm:ss, ticking in every scenario. */
  function pollClock(t) {
    var s = 14 * 3600 + 2 * 60 + Math.floor(t - T_MIN);
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(Math.floor(s / 3600)) + ":" + p(Math.floor((s % 3600) / 60)) + ":" + p(s % 60);
  }

  function stateAt(id, t) {
    var sc = SCENARIOS[id];
    var trust = trustAt(sc, t);
    return {
      trust: trust,
      headline: HEADLINE[trust],
      action: ACTION[trust],
      reasons: reasonsAt(sc, t),
      pumpRunning: pumpRunning(sc, t)
    };
  }

  var api = {
    T_MIN: T_MIN, T_MAX: T_MAX, SCENARIOS: SCENARIOS, ORDER: ORDER, HEADLINE: HEADLINE, ACTION: ACTION,
    trustAt: trustAt, pumpRunning: pumpRunning, reasonsAt: reasonsAt, level: level, flow: flow,
    vibration: vibration, sound: sound, pollClock: pollClock, stateAt: stateAt
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HWDemo = api;
})(typeof window !== "undefined" ? window : this);
