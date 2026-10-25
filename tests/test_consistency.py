"""The seven consistency rules: each fires on its own kind of lie and stays quiet on honest data."""
import pytest

from hydrowitness.engine.consistency import ConsistencyChecker, QuietTimeLearner
from hydrowitness.schemas import Command, ScadaSnapshot
from hydrowitness.sim.tank_system import ScadaView, TankSystem

T0 = 1_800_000_000.0


def fired(checker, now):
    return {r.rule_id: r for r in checker.evaluate(now) if r.fired}


def feed(chk, start, seconds, dt=5.0, *, level=lambda t: 22.0, status=None, flows=None, demand=180.0,
         pressure=None, commands=None, jitter=0.01, learn=True):
    """Feed synthetic snapshots. Tiny jitter keeps the frozen-data rule quiet unless a test wants it."""
    status = status or {"P1": False, "P2": True}
    flows = flows or {"P1": 0.0, "P2": 150.0}
    t, k = start, 0
    while t < start + seconds:
        lv = level(t - start) + (jitter if k % 2 else -jitter)
        pr = pressure if pressure is not None else (80 + lv - 40) / 2.31
        chk.ingest(ScadaSnapshot(t=t, tank_level_ft=round(lv, 3), pump_status=dict(status),
                                 pump_flow_gpm={p: round(f + (0.3 if k % 2 else -0.3), 2) if f else 0.0 for p, f in flows.items()},
                                 demand_gpm=round(demand + (0.4 if k % 3 else -0.4) + 0.001 * k, 3),
                                 pressure_psi=round(pr + (0.05 if k % 2 else -0.05) + 0.0007 * k, 3),
                                 commands=list(commands or [])), learn)
        chk.evaluate(t)            # the core evaluates on every tick; persistence timers live in evaluate()
        t += dt
        k += 1
    return t


# ------------------------------------------------------------ honest data is quiet
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_honest_simulated_data_fires_nothing_for_a_whole_day(profile, seed):
    sys = TankSystem(profile, seed=seed, dt=10.0)
    view = ScadaView(sys, seed=seed + 100)
    chk = ConsistencyChecker(profile)
    bad = []
    for i in range(int(86400 / 10)):
        sys.step()
        chk.ingest(view.snapshot(), True)
        if i % 6 == 0:
            f = fired(chk, sys.t)
            if f:
                bad.append((round((sys.t - T0) / 3600, 2), sorted(f)))
    assert bad == []


# ------------------------------------------------------------------- 1 mass balance
def test_mass_balance_fires_when_flows_say_the_tank_must_rise_but_it_doesnt(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 900, status={"P1": True, "P2": True}, flows={"P1": 240.0, "P2": 180.0}, demand=100.0)
    f = fired(chk, end)
    assert "mass_balance" in f and "Tank level changed" in f["mass_balance"].explanation


def test_mass_balance_stays_quiet_when_the_numbers_agree(profile):
    chk = ConsistencyChecker(profile)
    # demand 150, inflow 150 (P2 running): the level should stay flat
    end = feed(chk, T0, 900, demand=150.0)
    assert "mass_balance" not in fired(chk, end)


def test_mass_balance_needs_a_real_window_of_data(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 60, status={"P1": True, "P2": True}, flows={"P1": 240.0, "P2": 180.0}, demand=100.0)
    assert "mass_balance" not in fired(chk, end)


# --------------------------------------------------------------- 2 pump/flow/tank
def test_running_pump_with_no_flow_fires_after_30_seconds(profile):
    chk = ConsistencyChecker(profile)
    feed(chk, T0, 60)
    end = feed(chk, T0 + 60, 20, flows={"P1": 0.0, "P2": 0.0})
    assert "pump_flow_tank" not in fired(chk, end)
    end = feed(chk, end, 30, flows={"P1": 0.0, "P2": 0.0})
    f = fired(chk, end)
    assert "pump_flow_tank" in f and "P2 shown RUNNING" in f["pump_flow_tank"].explanation


def test_stopped_pump_reporting_flow_fires(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 90, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 120.0})
    f = fired(chk, end)
    assert "pump_flow_tank" in f and "STOPPED but reports" in f["pump_flow_tank"].explanation


def test_all_pumps_stopped_while_the_tank_rises_fires(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 330, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 0.0},
               level=lambda s: 22.0 + 0.004 * s)             # +0.24 ft/min
    f = fired(chk, end)
    assert "pump_flow_tank" in f and "tank level rose" in f["pump_flow_tank"].explanation


def test_a_normal_pump_stop_on_a_small_tank_is_not_a_tank_that_rises_while_stopped(profile):
    """Found by the long evaluation runs: at the moment the last pump stops, the previous 5 minutes still hold the rise from
    when it was running. That is not "all pumps shown stopped but the tank rose"."""
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 400, status={"P1": True, "P2": False}, flows={"P1": 250.0, "P2": 0.0},
               level=lambda s: 22.0 + 0.0005 * s)             # +0.03 ft/min, a small tank filling
    for k in range(60):                                       # the pump stops, the level stops rising, and for the next 5 minutes ...
        end = feed(chk, end, 5, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 0.0}, level=lambda s: 22.2)
        assert "pump_flow_tank" not in fired(chk, end - 5.0), f"fired {5 * (k + 1)} s after a normal stop"


def test_the_rising_tank_check_still_catches_a_screen_that_shows_stopped_for_minutes_while_the_tank_fills(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 400, status={"P1": True, "P2": False}, flows={"P1": 250.0, "P2": 0.0}, level=lambda s: 22.0 + 0.0005 * s)
    end = feed(chk, end, 330, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 0.0}, level=lambda s: 22.2 + 0.004 * s)
    assert "pump_flow_tank" in fired(chk, end)


def test_a_normal_pump_stop_on_a_small_tank_is_not_a_tank_that_rises_while_stopped(profile):
    """Found by the long evaluation runs: at the moment the last pump stops, the previous 5 minutes still hold the rise from
    when it was running. That is not "all pumps shown stopped but the tank rose"."""
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 400, status={"P1": True, "P2": False}, flows={"P1": 250.0, "P2": 0.0},
               level=lambda s: 22.0 + 0.0005 * s)             # +0.03 ft/min, a small tank filling
    for k in range(60):                                       # the pump stops, the level stops rising, and for the next 5 minutes ...
        end = feed(chk, end, 5, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 0.0}, level=lambda s: 22.2)
        assert "pump_flow_tank" not in fired(chk, end - 5.0), f"fired {5 * (k + 1)} s after a normal stop"


def test_the_rising_tank_check_still_catches_a_screen_that_shows_stopped_for_minutes_while_the_tank_fills(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 400, status={"P1": True, "P2": False}, flows={"P1": 250.0, "P2": 0.0}, level=lambda s: 22.0 + 0.0005 * s)
    end = feed(chk, end, 330, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 0.0}, level=lambda s: 22.2 + 0.004 * s)
    assert "pump_flow_tank" in fired(chk, end)


# ------------------------------------------------------------------ 3 static head
def test_pressure_that_ignores_the_tank_level_fires_after_a_minute(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 50, pressure=45.0)
    assert "static_head" not in fired(chk, end)
    end = feed(chk, end, 40, pressure=45.0)
    assert "static_head" in fired(chk, end)


def test_static_head_follows_the_tank_level(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 300, level=lambda s: 26.0 - 0.01 * s, demand=150.0)       # pressure derived from level
    assert "static_head" not in fired(chk, end)


# ----------------------------------------------------------------- 4 plausibility
def test_pump_flow_above_130_percent_of_rating_fires(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 30, flows={"P1": 0.0, "P2": 260.0})                         # P2 is rated 187.5 gpm
    f = fired(chk, end)
    assert "plausibility" in f and "130%" in f["plausibility"].explanation


def test_tank_level_jumping_faster_than_the_equipment_can_fires(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 200, level=lambda s: 22.0 if s < 150 else 27.0)
    assert "plausibility" in fired(chk, end)


# ------------------------------------------------------------ 5 command, response
def test_ignored_start_command_fires(profile):
    chk = ConsistencyChecker(profile)
    cmd = Command(t=T0 + 100, pump_id="P1", action="START")
    end = feed(chk, T0, 220, commands=[cmd])                                        # P1 stays STOPPED
    f = fired(chk, end)
    assert "command_response" in f and "commanded START" in f["command_response"].explanation


def test_obeyed_command_is_fine(profile):
    chk = ConsistencyChecker(profile)
    cmd = Command(t=T0 + 100, pump_id="P1", action="START")
    t = feed(chk, T0, 105)
    end = feed(chk, t, 120, status={"P1": True, "P2": True}, flows={"P1": 240.0, "P2": 180.0}, commands=[cmd], demand=420.0)
    assert "command_response" not in fired(chk, end)


# ----------------------------------------------------------------------- 6 frozen
def test_frozen_values_fire_after_the_learned_quiet_time(profile):
    sys = TankSystem(profile, seed=4, dt=5.0)
    view = ScadaView(sys, seed=5)
    chk = ConsistencyChecker(profile)
    for _ in range(720):
        sys.step()
        chk.ingest(view.snapshot(), True)
    view.freeze()
    for _ in range(10):                                  # 50 s
        sys.step()
        chk.ingest(view.snapshot(), False)
    assert "frozen_data" not in fired(chk, sys.t)
    for _ in range(20):                                  # 150 s in total
        sys.step()
        chk.ingest(view.snapshot(), False)
    f = fired(chk, sys.t)
    assert "frozen_data" in f and "tank_level_ft" in f["frozen_data"].tags


def test_quiet_time_learner_adapts_to_report_on_change_tags():
    q = QuietTimeLearner()
    for t, v in [(0, 1.0), (300, 1.0), (301, 2.0)]:       # stays identical for 300 s while trusted
        q.observe("tag", v, T0 + t, learn=True)
    assert q.allowed_s("tag") == pytest.approx(900.0)
    assert q.allowed_s("never_seen") == 90.0              # floor


def test_quiet_time_learner_does_not_learn_while_untrusted():
    q = QuietTimeLearner()
    for t, v in [(0, 1.0), (500, 1.0), (501, 2.0)]:
        q.observe("tag", v, T0 + t, learn=False)
    assert q.allowed_s("tag") == 90.0


def test_a_stopped_pump_with_zero_flow_is_not_frozen_data(profile):
    chk = ConsistencyChecker(profile)
    end = feed(chk, T0, 900, status={"P1": False, "P2": False}, flows={"P1": 0.0, "P2": 0.0},
               level=lambda s: 22.0 - 0.01 * s, demand=50.0)
    assert "frozen_data" not in fired(chk, end)


# ----------------------------------------------------------------------- 7 replay
def test_replayed_history_fires_quickly(profile):
    sys = TankSystem(profile, seed=7, dt=5.0)
    view = ScadaView(sys, seed=8)
    chk = ConsistencyChecker(profile)
    for _ in range(720):                                 # one hour of honest data
        sys.step()
        chk.ingest(view.snapshot(), True)
    view.replay(1800)
    hit = None
    for i in range(60):
        sys.step()
        chk.ingest(view.snapshot(), False)
        if "replay" in fired(chk, sys.t):
            hit = i * 5
            break
    assert hit is not None and hit <= 120


def test_honest_noisy_data_never_looks_like_a_replay(profile):
    sys = TankSystem(profile, seed=9, dt=5.0)
    view = ScadaView(sys, seed=10)
    chk = ConsistencyChecker(profile)
    for _ in range(int(6 * 3600 / 5)):
        sys.step()
        chk.ingest(view.snapshot(), True)
        assert "replay" not in fired(chk, sys.t)
