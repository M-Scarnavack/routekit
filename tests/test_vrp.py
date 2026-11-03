import random
import subprocess
import sys
from pathlib import Path

import pytest

from routekit.mapview import MapScene
from routekit.solver import distance_matrix
from routekit.stops import Stop, StopFileError, format_clock, load_stops, parse_clock
from routekit.vrp import Fleet, FleetProblem, solve_fleet

ROOT = Path(__file__).resolve().parent.parent


# -- time parsing -------------------------------------------------------------

@pytest.mark.parametrize("text,minutes", [
    ("9:05", 545), ("09:05", 545), ("9:05 AM", 545), ("9:05am", 545), ("10:30 AM", 630),
    ("12:00 PM", 720), ("12:15 am", 15), ("1:00 pm", 780), ("17:45", 1065), ("9am", 540), ("9 a.m.", 540),
])
def test_parse_clock(text, minutes):
    assert parse_clock(text) == minutes


@pytest.mark.parametrize("text", ["", "EOD", "eod", "none", "-"])
def test_parse_clock_open(text):
    assert parse_clock(text) is None


@pytest.mark.parametrize("text", ["25:00", "9:75", "13:00 pm", "noon-ish", "9.30"])
def test_parse_clock_rejects(text):
    with pytest.raises(ValueError):
        parse_clock(text)


def test_format_clock():
    assert format_clock(545) == "9:05 AM"
    assert format_clock(780) == "1:00 PM"
    assert format_clock(0) == "12:00 AM"


def test_load_fleet_columns(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("name,lat,lon,demand,earliest,deadline,ready,truck,group,service\n"
                 "Depot,41,-87,,,,,,,\n"
                 "A,41.1,-87.1,3,9:00,10:30 AM,9:05,2,g1,5\n")
    a = load_stops(p)[1]
    assert (a.demand, a.earliest, a.latest, a.ready, a.truck, a.group, a.service) == (3, 540, 630, 545, 2, "g1", 5)
    assert a.constrained


def test_load_combines_address_and_city(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("Package ID,Address,City,State,Zip,Deadline\n1,195 W Oakland Ave,Salt Lake City,UT,84115,10:30 AM\n"
                 "2,2530 S 500 E,Salt Lake City,UT,84106,EOD\n")
    stops = load_stops(p)
    assert stops[0].name == "1"
    assert stops[0].address == "195 W Oakland Ave, Salt Lake City, UT 84115"
    assert stops[0].latest == 630 and stops[1].latest is None


def test_load_reports_bad_time_with_line(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("name,lat,lon,deadline\nA,41,-87,\nB,41,-87,lunchtime\n")
    with pytest.raises(StopFileError, match="line 3"):
        load_stops(p)


# -- solver -------------------------------------------------------------------

def _random_problem(seed, n=30, trucks=4):
    rng = random.Random(seed)
    stops = [Stop("Depot", "", 41.88, -87.63)]
    for i in range(1, n):
        s = Stop(f"S{i}", "", 41.88 + rng.uniform(-0.15, 0.15), -87.63 + rng.uniform(-0.15, 0.15),
                 demand=rng.choice([1, 1, 2, 3]))
        r = rng.random()
        if r < 0.3:
            s.latest = 8 * 60 + rng.randint(60, 240)
        elif r < 0.4:
            s.earliest = 8 * 60 + rng.randint(30, 120)
        if rng.random() < 0.1:
            s.ready = 8 * 60 + rng.randint(15, 60)
        if rng.random() < 0.08:
            s.truck = rng.randint(1, trucks)
        stops.append(s)
    for g, members in enumerate((rng.sample(range(1, n), 3), rng.sample(range(1, n), 2))):
        for m in members:
            if stops[m].truck is None:
                stops[m].group = f"g{g}"
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    fleet = Fleet(trucks=trucks, capacity=16, start=8 * 60, mph=25, service=4)
    return stops, d, fleet


def _verify(sol, stops, d, fleet):
    """Re-check every rule from scratch."""
    seen = []
    truck_of = {}
    for trip in sol.trips:
        seen += trip.stops
        for n in trip.stops:
            truck_of[n] = trip.truck
        assert sum(stops[n].demand for n in trip.stops) <= fleet.capacity + 1e-9
        depart = max([fleet.start] + [stops[n].ready for n in trip.stops if stops[n].ready is not None])
        assert trip.depart == pytest.approx(depart)
        t, prev = depart, 0
        for n, arr in zip(trip.stops, trip.arrivals):
            t += d[prev][n] * 60 / fleet.mph
            if stops[n].earliest is not None:
                t = max(t, stops[n].earliest)
            assert arr == pytest.approx(t)
            if stops[n].latest is not None:
                assert arr <= stops[n].latest + 1e-6
            t += fleet.service if stops[n].service is None else stops[n].service
            prev = n
        if trip.stops:
            assert trip.finish == pytest.approx(t + d[prev][0] * 60 / fleet.mph)
    assert sorted(seen + sol.unassigned) == list(range(1, len(stops)))  # each stop exactly once
    for n, tr in truck_of.items():
        if stops[n].truck is not None:
            assert tr == stops[n].truck
    groups = {}
    for n, tr in truck_of.items():
        if stops[n].group:
            groups.setdefault(stops[n].group, set()).add(tr)
    assert all(len(v) == 1 for v in groups.values())
    assert sol.miles == pytest.approx(sum(t.miles for t in sol.trips))


@pytest.mark.parametrize("seed", range(12))
def test_fleet_solutions_obey_every_rule(seed):
    stops, d, fleet = _random_problem(seed)
    sol = solve_fleet(d, stops, fleet)
    _verify(sol, stops, d, fleet)
    assert sol.miles <= sol.initial_miles + 1e-9  # local search never makes it worse


def test_infeasible_stop_is_reported_not_dropped_silently():
    stops = [Stop("Depot", "", 41.88, -87.63), Stop("Near", "", 41.89, -87.63),
             Stop("Impossible", "", 42.88, -87.63, latest=8 * 60 + 5)]  # 69 miles away, due in 5 min
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    sol = solve_fleet(d, stops, Fleet(trucks=2, mph=30))
    assert sol.unassigned == [2]
    assert [n for t in sol.trips for n in t.stops] == [1]


def test_capacity_forces_a_second_truck():
    stops = [Stop("Depot", "", 41.88, -87.63)] + [Stop(f"S{i}", "", 41.88 + i * 0.01, -87.63, demand=5)
                                                  for i in range(1, 5)]
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    sol = solve_fleet(d, stops, Fleet(trucks=3, capacity=10))
    assert sol.trucks_used == 2 and not sol.unassigned


def test_ready_time_delays_departure():
    stops = [Stop("Depot", "", 41.88, -87.63), Stop("Late freight", "", 41.9, -87.63, ready=9 * 60 + 5)]
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    sol = solve_fleet(d, stops, Fleet(trucks=1))
    assert sol.trips[0].depart == 545


def test_plan_rejects_wrong_truck():
    stops = [Stop("Depot", "", 41.88, -87.63), Stop("Pinned", "", 41.9, -87.63, truck=2)]
    p = FleetProblem(distance_matrix([(s.lat, s.lon) for s in stops]), stops, Fleet(trucks=2))
    assert p.plan([1], truck=0) is None and p.plan([1], truck=1) is not None


# -- WGUPS (the original C950 scenario) ------------------------------------------

def test_wgups_script_satisfies_every_c950_rule():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "solve_wgups.py")],
                         capture_output=True, text=True, cwd=ROOT, env={"COLUMNS": "120", "PATH": ""})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "every rule satisfied" in out.stdout
    assert "limit enforced: 2" in out.stdout


# -- fleet map ----------------------------------------------------------------

def test_fleet_map_draws_every_truck():
    depot = Stop("Depot", "", 41.88, -87.63)
    a = [depot, Stop("A1", "", 41.95, -87.65), Stop("A2", "", 41.92, -87.60), depot]
    b = [depot, Stop("B1", "", 41.80, -87.60), depot]
    scene = MapScene([a, b], cols=60)
    assert scene.fleet and len(scene.tracks) == 2
    text = scene.frame().plain
    assert text.count("■") == 1 and text.count("●") == 3
    legend = scene.legend().plain
    assert "Truck 1" in legend and "Truck 2" in legend
    mid = scene.frame(scene.total_dots // 2).plain
    assert mid.count("◆") >= 1


# -- multiple trips and driver limits --------------------------------------------

def _verify_day(sol, stops, d, fleet):
    """Re-check the rules that tie trips together across the day."""
    used = sol.used
    seen = sorted(n for t in used for n in t.stops)
    assert sorted(seen + sol.unassigned) == list(range(1, len(stops)))
    by_truck, by_driver = {}, {}
    for t in used:
        assert t.load <= fleet.capacity + 1e-9
        ready = max([fleet.start] + [stops[n].ready for n in t.stops if stops[n].ready is not None])
        assert t.depart >= ready - 1e-9
        clock, prev = t.depart, 0
        for n, arr in zip(t.stops, t.arrivals):
            clock += d[prev][n] * 60 / fleet.mph
            if stops[n].earliest is not None:
                clock = max(clock, stops[n].earliest)
            assert arr == pytest.approx(clock)
            if stops[n].latest is not None:
                assert arr <= stops[n].latest + 1e-6
            clock += fleet.service if stops[n].service is None else stops[n].service
            prev = n
        assert t.finish == pytest.approx(clock + d[prev][0] * 60 / fleet.mph)
        by_truck.setdefault(t.truck, []).append(t)
        if t.driver is not None:
            by_driver.setdefault(t.driver, []).append(t)
        for n in t.stops:
            if stops[n].truck is not None:
                assert t.truck == stops[n].truck
    for trips in by_truck.values():
        trips.sort(key=lambda t: t.depart)
        assert len(trips) <= fleet.trips
        assert [t.trip for t in trips] == list(range(1, len(trips) + 1))
        for a, b in zip(trips, trips[1:]):
            assert b.depart >= a.finish + fleet.reload - 1e-6  # truck back (and reloaded) first
    for trips in by_driver.values():
        trips.sort(key=lambda t: t.depart)
        for a, b in zip(trips, trips[1:]):
            assert b.depart >= a.finish - 1e-6  # a driver drives one truck at a time
    assert sol.drivers_needed <= fleet.driver_count
    groups = {}
    for t in used:
        for n in t.stops:
            if stops[n].group:
                groups.setdefault(stops[n].group, set()).add((t.truck, t.trip))
    assert all(len(v) == 1 for v in groups.values())


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("trucks,trips,drivers,reload", [(3, 2, 2, 0), (4, 3, 2, 15), (2, 3, 1, 10)])
def test_multi_trip_driver_limited_plans_obey_every_rule(seed, trucks, trips, drivers, reload):
    stops, d, _ = _random_problem(seed, n=24, trucks=trucks)
    fleet = Fleet(trucks=trucks, capacity=8, start=8 * 60, mph=25, service=4,
                  trips=trips, drivers=drivers, reload=reload)
    sol = solve_fleet(d, stops, fleet)
    _verify_day(sol, stops, d, fleet)
    assert len(sol.unassigned) <= sol.initial_unassigned
    if len(sol.unassigned) == sol.initial_unassigned:
        assert sol.miles <= sol.initial_miles + 1e-9  # same stops delivered: never worse


def test_extra_trips_deliver_what_one_trip_cannot():
    stops = [Stop("Depot", "", 41.88, -87.63)] + [Stop(f"S{i}", "", 41.88 + i * 0.01, -87.63, demand=5)
                                                  for i in range(1, 5)]
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    one = solve_fleet(d, stops, Fleet(trucks=1, capacity=10))
    two = solve_fleet(d, stops, Fleet(trucks=1, capacity=10, trips=2))
    assert len(one.unassigned) == 2
    assert not two.unassigned and two.trip_count == 2
    first, second = sorted(two.used, key=lambda t: t.depart)
    assert second.depart >= first.finish


def test_single_driver_never_runs_two_trucks_at_once():
    stops = [Stop("Depot", "", 41.88, -87.63)] + [Stop(f"S{i}", "", 41.88 + 0.02 * i, -87.63 + 0.01 * i)
                                                  for i in range(1, 7)]
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    fleet = Fleet(trucks=3, capacity=2, drivers=1, trips=2, mph=20)
    sol = solve_fleet(d, stops, fleet)
    assert not sol.unassigned
    assert sol.drivers_needed == 1
    _verify_day(sol, stops, d, fleet)


def test_reload_time_delays_next_trip():
    stops = [Stop("Depot", "", 41.88, -87.63), Stop("A", "", 41.9, -87.63, demand=1),
             Stop("B", "", 41.86, -87.63, demand=1)]
    d = distance_matrix([(s.lat, s.lon) for s in stops])
    sol = solve_fleet(d, stops, Fleet(trucks=1, capacity=1, trips=2, reload=30))
    first, second = sorted(sol.used, key=lambda t: t.depart)
    assert second.depart == pytest.approx(first.finish + 30)


def test_cli_trips_and_drivers(tmp_path, capsys):
    from routekit import cli
    out = tmp_path / "plan.csv"
    code = cli.main(["plan", str(ROOT / "examples" / "chicago_fleet.csv"), "--trucks", "2", "--drivers", "1",
                     "--capacity", "10", "--trips", "3", "--reload", "10", "--mph", "20", "--no-map",
                     "-o", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "Drivers" in text and "trip 2" in text
    import csv as _csv
    rows = list(_csv.DictReader(out.open()))
    assert {"truck", "trip", "driver", "departs"} <= set(rows[0])
