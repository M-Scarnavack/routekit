"""The Solomon benchmark harness (scripts/bench.py): parsing, the independent checker, and a real solve."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "bench.py")
bench = importlib.util.module_from_spec(spec)
sys.modules["bench"] = bench  # dataclasses look their module up while the file loads
spec.loader.exec_module(bench)

from routekit.vrp import FleetSolution, TripPlan  # noqa: E402


def plan(*routes):
    trips = [TripPlan(k + 1, list(r), 0.0, 0.0, 0.0, [], 0.0) for k, r in enumerate(routes)]
    return FleetSolution(trips, [], 0.0, 0.0)


def test_all_56_instances_and_best_known_values_are_bundled():
    files = sorted(p.stem for p in bench.DATA.glob("*.txt"))
    assert len(files) == 56
    best = bench.load_best_known()
    assert sorted(best) == files
    assert best["c101"] == {"vehicles": 10, "distance": 828.94, "reference": "RT"}


def test_parse_instance():
    inst = bench.load_instance(bench.DATA / "c101.txt")
    assert (inst.name, inst.vehicles, inst.capacity, len(inst.nodes)) == ("c101", 25, 200.0, 101)
    depot, first = inst.nodes[0], inst.nodes[1]
    assert (depot.x, depot.y, depot.due) == (40, 50, 1236)
    assert (first.x, first.y, first.demand, first.ready, first.due, first.service) == (45, 68, 10, 912, 967, 90)
    assert inst.dist(0, 1) == pytest.approx((5 ** 2 + 18 ** 2) ** 0.5)


def test_select_classes_do_not_overlap():
    r1 = [p.stem for p in bench.select(["r1"], quick=False)]
    assert len(r1) == 12 and all(s.startswith("r1") for s in r1)  # not rc1xx
    assert len(bench.select(["rc2"], quick=False)) == 8
    assert [p.stem for p in bench.select([], quick=True)] == bench.QUICK
    with pytest.raises(SystemExit):
        bench.select(["x999"], quick=False)


def test_checker_catches_missing_late_and_overloaded():
    inst = bench.load_instance(bench.DATA / "c101.txt")
    n = len(inst.nodes)
    assert any("customer 5 served 0 times" in p for p in bench.check(inst, plan(range(1, 5), range(6, n))))
    assert any("served 2 times" in p for p in bench.check(inst, plan(range(1, n), [1])))
    problems = bench.check(inst, plan(range(1, n)))  # one truck for everything
    assert any("capacity" in p for p in problems)
    assert any("due" in p for p in problems)


def test_solve_c101_is_valid_and_near_best_known():
    inst = bench.load_instance(bench.DATA / "c101.txt")
    row = bench.run(inst, bench.load_best_known()["c101"])
    assert row["valid"], row["problems"]
    assert row["distance"] >= 828.94 - 0.01  # can't beat the optimum
    assert row["gap_pct"] < 25


def test_baseline_comparison_flags_regressions(tmp_path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"results": [{"instance": "c101", "vehicles": 12, "distance": 900.0}]}))
    ok = {"instance": "c101", "vehicles": 12, "distance": 902.0, "valid": True, "problems": []}
    assert bench.compare([ok], base, tolerance=0.5) == []
    worse = dict(ok, distance=910.0)
    assert "distance" in bench.compare([worse], base, tolerance=0.5)[0]
    more_trucks = dict(ok, vehicles=13)
    assert "trucks" in bench.compare([more_trucks], base, tolerance=0.5)[0]
    broken = dict(ok, valid=False, problems=["customer 3 served 0 times"])
    assert "breaks a rule" in bench.compare([broken], base, tolerance=0.5)[0]
