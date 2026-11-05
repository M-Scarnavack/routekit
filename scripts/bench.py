"""Benchmark routekit's fleet solver on the Solomon VRPTW instances.

The 56 Solomon instances (1987) are the standard test set for vehicle routing
with time windows: 100 customers each, with truck capacities, delivery windows,
and service times. Researchers have published best-known solutions for every
one, so they give an outside yardstick for how good routekit's plans are.

    python scripts/bench.py                    # all 56 instances
    python scripts/bench.py --quick            # one instance per class (6)
    python scripts/bench.py c101 r205 rc1      # instances, or whole classes by prefix
    python scripts/bench.py --json results.json
    python scripts/bench.py --quick --baseline benchmarks/baseline.json   # fail on a regression

For each instance it reports trucks used and total distance next to the best
known, the gap in percent, and the solve time. Every plan is re-checked from
scratch against the instance file (each customer served once, capacity, time
windows, back at the depot by closing time), rather than trusting the solver.

How Solomon maps onto routekit:

- distance: Euclidean, full precision (the convention behind the best-known table)
- travel time = distance, so the fleet drives at 60 "mph" (1 distance unit per minute)
- READY TIME / DUE DATE -> earliest / latest; SERVICE TIME -> service
- the depot's DUE DATE -> the end of the shift; up to 25 trucks of the given capacity

The best-known solutions minimize trucks first and distance second. routekit
minimizes distance with the trucks it's given, so it may use more trucks than
the best known; the report shows both numbers, and a negative distance gap with
extra trucks is not a better solution.

Instances and best-known values: SINTEF, https://www.sintef.no/projectweb/top/vrptw/
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routekit.stops import Stop  # noqa: E402
from routekit.vrp import Fleet, FleetSolution, solve_fleet  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "benchmarks" / "solomon"
QUICK = ["c101", "c201", "r101", "r201", "rc101", "rc201"]
CLASSES = ["c1", "c2", "r1", "r2", "rc1", "rc2"]
EPS = 1e-6


@dataclass
class Customer:
    id: int
    x: float
    y: float
    demand: float
    ready: float
    due: float
    service: float


@dataclass
class Instance:
    name: str
    vehicles: int
    capacity: float
    nodes: List[Customer]  # nodes[0] is the depot

    def dist(self, i: int, j: int) -> float:
        a, b = self.nodes[i], self.nodes[j]
        return math.hypot(a.x - b.x, a.y - b.y)


def load_instance(path: Path) -> Instance:
    """Parse a Solomon instance file."""
    lines = path.read_text().splitlines()
    name = lines[0].strip().lower()
    vehicles = capacity = None
    nodes: List[Customer] = []
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("NUMBER"):
            vehicles, capacity = (int(v) for v in lines[i + 1].split())
        parts = line.split()
        if len(parts) == 7 and all(p.replace(".", "", 1).isdigit() for p in parts):
            cid, x, y, q, r, due, s = (float(p) for p in parts)
            nodes.append(Customer(int(cid), x, y, q, r, due, s))
    if vehicles is None or not nodes or nodes[0].id != 0:
        raise ValueError(f"{path}: not a Solomon instance")
    if [n.id for n in nodes] != list(range(len(nodes))):
        raise ValueError(f"{path}: customers aren't numbered 0..n")
    return Instance(name, vehicles, float(capacity), nodes)


def load_best_known(path: Path = DATA / "best_known.csv") -> Dict[str, dict]:
    with open(path, newline="") as f:
        return {r["instance"]: {"vehicles": int(r["vehicles"]), "distance": float(r["distance"]),
                                "reference": r["reference"]} for r in csv.DictReader(f)}


def to_routekit(inst: Instance):
    """Stops, distance matrix, and fleet for routekit's solver."""
    depot = inst.nodes[0]
    stops = [Stop("depot", "", lat=depot.x, lon=depot.y, demand=0)]
    for c in inst.nodes[1:]:
        stops.append(Stop(f"customer {c.id}", "", lat=c.x, lon=c.y, demand=c.demand,
                          earliest=c.ready, latest=c.due, service=c.service))
    n = len(inst.nodes)
    d = [[inst.dist(i, j) for j in range(n)] for i in range(n)]
    fleet = Fleet(trucks=inst.vehicles, capacity=inst.capacity, start=depot.ready, end=depot.due, mph=60.0)
    return stops, d, fleet


def check(inst: Instance, sol: FleetSolution) -> List[str]:
    """Re-verify a plan from the instance data alone, without routekit's own bookkeeping."""
    problems = []
    seen: Dict[int, int] = {}
    depot = inst.nodes[0]
    for t in sol.used:
        load = sum(inst.nodes[n].demand for n in t.stops)
        if load > inst.capacity + EPS:
            problems.append(f"truck {t.truck}: load {load:g} > capacity {inst.capacity:g}")
        clock, prev = depot.ready, 0
        for n in t.stops:
            seen[n] = seen.get(n, 0) + 1
            c = inst.nodes[n]
            clock = max(clock + inst.dist(prev, n), c.ready)
            if clock > c.due + EPS:
                problems.append(f"customer {n} reached at {clock:.2f}, due {c.due:g}")
            clock += c.service
            prev = n
        clock += inst.dist(prev, 0)
        if clock > depot.due + EPS:
            problems.append(f"truck {t.truck} back at {clock:.2f}, depot closes {depot.due:g}")
    for n in range(1, len(inst.nodes)):
        if seen.get(n, 0) != 1:
            problems.append(f"customer {n} served {seen.get(n, 0)} times")
    return problems


def distance(inst: Instance, sol: FleetSolution) -> float:
    """Total distance recomputed from the instance (not the solver's own total)."""
    total = 0.0
    for t in sol.used:
        seq = [0] + t.stops + [0]
        total += sum(inst.dist(a, b) for a, b in zip(seq, seq[1:]))
    return total


def run(inst: Instance, best: Optional[dict]) -> dict:
    stops, d, fleet = to_routekit(inst)
    t0 = time.perf_counter()
    sol = solve_fleet(d, stops, fleet)
    seconds = time.perf_counter() - t0
    problems = check(inst, sol)
    dist = distance(inst, sol)
    row = {
        "instance": inst.name,
        "vehicles": sol.trucks_used,
        "distance": round(dist, 2),
        "seconds": round(seconds, 2),
        "valid": not problems,
        "problems": problems[:5],
    }
    if best:
        row["best_vehicles"] = best["vehicles"]
        row["best_distance"] = best["distance"]
        row["gap_pct"] = round(100.0 * (dist - best["distance"]) / best["distance"], 2)
    return row


def select(names: List[str], quick: bool) -> List[Path]:
    every = sorted(DATA.glob("*.txt"), key=lambda p: (p.stem.rstrip("0123456789"), p.stem))
    if quick:
        return [DATA / f"{n}.txt" for n in QUICK]
    if not names:
        return every
    picked = []
    for n in names:
        n = n.lower()
        exact = DATA / f"{n}.txt"
        if exact.exists():
            picked.append(exact)
        elif n in CLASSES:  # a class prefix such as "r1" (but "r1" must not match "rc1..")
            picked += [p for p in every if p.stem[:-2] == n]
        else:
            raise SystemExit(f"unknown instance or class: {n}  (classes: {', '.join(CLASSES)})")
    return picked


def summarize(rows: List[dict]) -> Dict[str, dict]:
    """Average gap, trucks, and time per class (C1, C2, R1, ...) and overall."""
    groups: Dict[str, List[dict]] = {}
    for r in rows:
        groups.setdefault(r["instance"][:-2].upper(), []).append(r)
    groups = {k: groups[k] for k in sorted(groups, key=lambda k: CLASSES.index(k.lower()))}
    if len(groups) > 1:
        groups["All"] = rows
    out = {}
    for k, rs in groups.items():
        with_best = [r for r in rs if "gap_pct" in r]
        out[k] = {
            "instances": len(rs),
            "valid": sum(r["valid"] for r in rs),
            "gap_pct": round(sum(r["gap_pct"] for r in with_best) / len(with_best), 2) if with_best else None,
            "vehicles": sum(r["vehicles"] for r in rs),
            "best_vehicles": sum(r.get("best_vehicles", 0) for r in rs),
            "seconds": round(sum(r["seconds"] for r in rs), 1),
        }
    return out


def compare(rows: List[dict], baseline_path: Path, tolerance: float) -> List[str]:
    """Regressions against a saved run: invalid plans, or distance up by more than `tolerance` %."""
    base = {r["instance"]: r for r in json.loads(baseline_path.read_text())["results"]}
    out = []
    for r in rows:
        b = base.get(r["instance"])
        if not r["valid"]:
            out.append(f"{r['instance']}: plan breaks a rule ({'; '.join(r['problems'])})")
        elif b is not None:
            change = 100.0 * (r["distance"] - b["distance"]) / b["distance"]
            if change > tolerance:
                out.append(f"{r['instance']}: distance {b['distance']:.2f} → {r['distance']:.2f} "
                           f"(+{change:.2f}%, tolerance {tolerance}%)")
            if r["vehicles"] > b["vehicles"]:
                out.append(f"{r['instance']}: trucks {b['vehicles']} → {r['vehicles']}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("instances", nargs="*", help="instance names (c101) or classes (r1, rc2); default: all")
    ap.add_argument("--quick", action="store_true", help="one instance per class: " + ", ".join(QUICK))
    ap.add_argument("--json", type=Path, help="save results to this JSON file")
    ap.add_argument("--baseline", type=Path, help="compare with a saved --json run; exit 1 on a regression")
    ap.add_argument("--tolerance", type=float, default=0.5,
                    help="allowed distance increase vs the baseline, in percent (default 0.5)")
    args = ap.parse_args(argv)

    from rich.console import Console
    from rich.table import Table

    console = Console()
    best = load_best_known()
    paths = select(args.instances, args.quick)

    table = Table(title="routekit on the Solomon VRPTW benchmark", header_style="bold")
    for col in ("Instance", "Trucks", "Best", "Distance", "Best known", "Gap", "Time", "Check"):
        table.add_column(col, justify="left" if col == "Instance" else "right")

    rows = []
    with console.status("") as status:
        for i, p in enumerate(paths, 1):
            status.update(f"Solving {p.stem} ({i}/{len(paths)})…")
            r = run(load_instance(p), best.get(p.stem))
            rows.append(r)
            gap = r.get("gap_pct")
            table.add_row(
                r["instance"], str(r["vehicles"]), str(r.get("best_vehicles", "")),
                f"{r['distance']:.2f}", f"{r['best_distance']:.2f}" if "best_distance" in r else "",
                "" if gap is None else f"{gap:+.1f}%", f"{r['seconds']:.1f}s",
                "[green]✓[/green]" if r["valid"] else "[red]✗[/red]")
    console.print(table)

    summary = summarize(rows)
    st = Table(title="By class", header_style="bold")
    for col in ("Class", "Instances", "Avg gap", "Trucks", "Best", "Time", "Valid"):
        st.add_column(col, justify="left" if col == "Class" else "right")
    for k, s in summary.items():
        st.add_row(k, str(s["instances"]), "" if s["gap_pct"] is None else f"{s['gap_pct']:+.1f}%",
                   str(s["vehicles"]), str(s["best_vehicles"]), f"{s['seconds']:.1f}s",
                   f"{s['valid']}/{s['instances']}")
    console.print(st)

    for r in rows:
        for prob in r["problems"]:
            console.print(f"[red]✗ {r['instance']}: {prob}[/red]")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "benchmark": "Solomon VRPTW, 100 customers",
            "python": platform.python_version(),
            "summary": summary,
            "results": rows,
        }, indent=2) + "\n")
        console.print(f"Saved {args.json}")

    failed = not all(r["valid"] for r in rows)
    if args.baseline:
        regressions = compare(rows, args.baseline, args.tolerance)
        if regressions:
            console.print("[bold red]Regressions vs baseline:[/bold red]")
            for line in regressions:
                console.print(f"  • {line}")
            failed = True
        else:
            console.print(f"[green]✓ No regressions vs {args.baseline}[/green]")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
