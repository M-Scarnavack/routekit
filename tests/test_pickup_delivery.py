import csv
import random

import pytest

from routekit import cli
from routekit.entry import StopTextError, parse_lines, parse_shipment_text
from routekit.solver import distance_matrix
from routekit.stops import Stop, StopFileError, load_stops
from routekit.vrp import Fleet, FleetProblem, solve_fleet


def _ship(sid, p, d, demand=1.0, **kw):
    return [Stop(f"{sid} (pickup)", "", *p, demand=demand, shipment=sid, action="pickup", **kw.get("pick", {})),
            Stop(f"{sid} (delivery)", "", *d, demand=demand, shipment=sid, action="delivery", **kw.get("drop", {}))]


def _problem(seed, n_ship=8, n_plain=6):
    rng = random.Random(seed)
    pt = lambda: (41.88 + rng.uniform(-0.2, 0.2), -87.63 + rng.uniform(-0.2, 0.2))  # noqa: E731
    stops = [Stop("Depot", "", 41.88, -87.63)]
    for i in range(n_plain):
        stops.append(Stop(f"P{i}", "", *pt(), demand=rng.choice([1, 2])))
    for i in range(n_ship):
        drop = {"latest": 8 * 60 + rng.randint(120, 360)} if rng.random() < 0.4 else {}
        pick = {"ready": 8 * 60 + rng.randint(0, 60)} if rng.random() < 0.3 else {}
        stops += _ship(f"S{i}", pt(), pt(), demand=rng.choice([1, 2, 3]), pick=pick, drop=drop)
    return stops, distance_matrix([(s.lat, s.lon) for s in stops])


def _verify(sol, stops, fleet):
    """Re-check pickup-and-delivery rules from scratch."""
    where = {}
    for t in sol.used:
        load = sum(stops[n].demand for n in t.stops if stops[n].action is None)  # leaves with depot freight
        assert load <= fleet.capacity + 1e-9
        seen = set()
        for k, (n, arr) in enumerate(zip(t.stops, t.arrivals)):
            s = stops[n]
            where[n] = (t.truck, t.trip, k)
            if s.action == "pickup":
                load += s.demand
                seen.add(s.shipment)
                if s.earliest is not None:
                    assert arr >= s.earliest - 1e-9
            elif s.action == "delivery":
                assert s.shipment in seen, f"{s.name} delivered before pickup"
                load -= s.demand
            else:
                load -= s.demand
            assert load <= fleet.capacity + 1e-9, "over capacity mid-route"
            if s.latest is not None:
                assert arr <= s.latest + 1e-6
    pickups = {s.shipment: i for i, s in enumerate(stops) if s.action == "pickup"}
    deliveries = {s.shipment: i for i, s in enumerate(stops) if s.action == "delivery"}
    for sid, p in pickups.items():
        d = deliveries[sid]
        if p in where or d in where:
            assert p in where and d in where, f"{sid} split: one half undelivered"
            assert where[p][:2] == where[d][:2], f"{sid} split across trips"
            assert where[p][2] < where[d][2]
        else:
            assert p in sol.unassigned and d in sol.unassigned
    assert sorted(list(where) + sol.unassigned) == list(range(1, len(stops)))


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("fleet", [Fleet(trucks=3, capacity=6, mph=25, service=3),
                                   Fleet(trucks=2, capacity=5, mph=25, trips=2, drivers=1)])
def test_random_pickup_delivery_plans_obey_every_rule(seed, fleet):
    stops, d = _problem(seed)
    sol = solve_fleet(d, stops, fleet)
    _verify(sol, stops, fleet)
    if len(sol.unassigned) == sol.initial_unassigned:
        assert sol.miles <= sol.initial_miles + 1e-9


def test_pickup_comes_before_delivery_even_when_backwards_is_shorter():
    depot = Stop("Depot", "", 41.88, -87.63)
    far, near = (41.98, -87.63), (41.89, -87.63)  # picking up far away, delivering near the depot
    stops = [depot] + _ship("X", far, near)
    sol = solve_fleet(distance_matrix([(s.lat, s.lon) for s in stops]), stops, Fleet())
    assert [stops[n].action for n in sol.used[0].stops] == ["pickup", "delivery"]


def test_load_is_tracked_along_the_route():
    # Capacity 2: deliver two depot boxes first, then a 2-unit pickup fits; the reverse wouldn't.
    depot = Stop("Depot", "", 41.88, -87.63)
    stops = [depot, Stop("A", "", 41.885, -87.63, demand=1), Stop("B", "", 41.89, -87.63, demand=1)] + \
        _ship("Big", (41.90, -87.63), (41.91, -87.63), demand=2)
    p = FleetProblem(distance_matrix([(s.lat, s.lon) for s in stops]), stops, Fleet(capacity=2))
    assert p.peak_load([1, 2, 3, 4]) == 2
    assert p.feasible([1, 2, 3, 4], 0)
    assert p.peak_load([3, 1, 2, 4]) == 4 and not p.feasible([3, 1, 2, 4], 0)
    assert p.peak_load([4, 3]) is None  # delivery before pickup
    assert p.peak_load([3]) is None     # pickup without its delivery
    sol = solve_fleet(p.d, stops, Fleet(capacity=2))
    assert not sol.unassigned and sol.used[0].load == 2


def test_pickup_ready_time_waits_at_pickup_not_depot():
    depot = Stop("Depot", "", 41.88, -87.63)
    stops = [depot, Stop("Early drop", "", 41.885, -87.63)] + \
        _ship("Late", (41.89, -87.63), (41.895, -87.63), pick={"ready": 10 * 60})
    sol = solve_fleet(distance_matrix([(s.lat, s.lon) for s in stops]), stops, Fleet(mph=30))
    t = sol.used[0]
    assert t.depart == 8 * 60  # the truck doesn't sit at the depot until 10:00
    pick_idx = [stops[n].action for n in t.stops].index("pickup")
    assert t.arrivals[pick_idx] >= 10 * 60


# -- input formats -----------------------------------------------------------

def test_typed_shipment():
    stops = parse_lines(["Yard: 41.88, -87.63", "Acme: 41.95, -87.65 @ 9:00-10:00 > 41.80, -87.60 @ 3:00 PM",
                         "41.90, -87.70"])
    yard, pu, de, plain = stops
    assert (pu.name, pu.action, pu.earliest, pu.latest) == ("Acme (pickup)", "pickup", 540, 600)
    assert (de.name, de.action, de.latest) == ("Acme (delivery)", "delivery", 900)
    assert pu.shipment == de.shipment and plain.action is None


def test_typed_shipment_errors():
    with pytest.raises(StopTextError, match="depot"):
        parse_lines(["41.95, -87.65 > 41.80, -87.60"])
    assert parse_lines(["41.95, -87.65 > 41.80, -87.60"], depot_given=True)[0].action == "pickup"
    with pytest.raises(StopTextError, match="one '>'"):
        parse_shipment_text("a > b > c", 1)


def test_csv_one_row_per_shipment(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("name,address,pickup_lat,pickup_lon,delivery_lat,delivery_lon,demand,pickup_after,deliver_by\n"
                 "Yard,,,,,,,,\n"
                 "Load 7,,41.95,-87.65,41.80,-87.60,3,9:00,3:00 PM\n")
    # the depot row needs coordinates too
    p.write_text("name,lat,lon,pickup_lat,pickup_lon,delivery_lat,delivery_lon,demand,pickup_after,deliver_by\n"
                 "Yard,41.88,-87.63,,,,,,,\n"
                 "Load 7,,,41.95,-87.65,41.80,-87.60,3,9:00,3:00 PM\n")
    yard, pu, de = load_stops(p)
    assert yard.action is None and (pu.action, pu.demand, pu.earliest) == ("pickup", 3, 540)
    assert (de.action, de.latest) == ("delivery", 900) and pu.shipment == de.shipment == "Load 7"


def test_csv_one_row_per_stop(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("name,lat,lon,shipment,type\nYard,41.88,-87.63,,\nA,41.95,-87.65,L1,pickup\nB,41.80,-87.60,L1,drop\n")
    _, a, b = load_stops(p)
    assert (a.action, b.action, a.shipment) == ("pickup", "delivery", "L1")


def test_csv_shipment_validation(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("name,lat,lon,shipment,type\nYard,41.88,-87.63,,\nA,41.95,-87.65,L1,pickup\n")
    with pytest.raises(StopFileError, match="exactly one pickup and one delivery"):
        load_stops(p)
    p.write_text("name,lat,lon,type\nYard,41.88,-87.63,\nA,41.95,-87.65,residential\n")
    assert all(s.action is None for s in load_stops(p))  # a stray 'type' column is ignored


def test_cli_shipments_end_to_end(tmp_path, capsys):
    out = tmp_path / "plan.csv"
    code = cli.main(["plan", "Yard: 41.88, -87.63", "Acme: 41.95, -87.65 > 41.80, -87.60 @ 3:00 PM",
                     "Beta: 41.83, -87.70 > 41.92, -87.62", "Drop: 41.90, -87.66", "--trucks", "2",
                     "--capacity", "2", "--no-map", "-o", str(out)])
    assert code == 0
    rows = list(csv.DictReader(out.open()))
    for sid in {r["shipment"] for r in rows if r["shipment"]}:
        legs = [r for r in rows if r["shipment"] == sid]
        assert [r["action"] for r in legs] == ["pickup", "delivery"] and legs[0]["truck"] == legs[1]["truck"]
        assert int(legs[0]["order"]) < int(legs[1]["order"])
    assert "Acme (pickup)" in capsys.readouterr().out


def test_cli_rejects_shipment_as_depot(tmp_path, capsys):
    p = tmp_path / "s.csv"
    p.write_text("name,pickup_lat,pickup_lon,delivery_lat,delivery_lon\nL1,41.95,-87.65,41.80,-87.60\n"
                 "L2,41.85,-87.65,41.90,-87.60\n")
    assert cli.main(["plan", str(p), "--no-map"]) == 2
    assert "--depot" in capsys.readouterr().out
