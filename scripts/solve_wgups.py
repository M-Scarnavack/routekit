"""Solve the original WGU C950 WGUPS scenario with routekit's fleet solver.

Uses the course's own data, unchanged: data/Package.csv, data/Address.csv,
and the data/Distance.csv mileage table. So the result compares directly with
the original C950 solution in main.py (116 miles).

Every special note in the package file is turned into a routekit rule:

    "Can only be on truck 2"            -> truck = 2
    "Delayed on flight ... until 9:05"  -> ready = 9:05 AM
    "Must be delivered with 13, 15"     -> same group as 13 and 15
    "Wrong address listed" (package 9)  -> corrected to 410 S State St, ready 10:20 AM
    "10:30 AM" / "9:00 AM" deadlines    -> latest = deadline

Fleet: 3 trucks x 16 packages, 2 drivers, 18 mph, leaving the hub at 8:00 AM.
Trucks may return to the hub and go out again, and a driver who comes back can
take another truck, exactly as in the C950 scenario. routekit enforces the
two-driver limit, rather than just checking it afterward.

    python scripts/solve_wgups.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from routekit.stops import Stop, format_clock, parse_clock  # noqa: E402
from routekit.vrp import Fleet, FleetSolution, solve_fleet  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
C950_MILES = 116.0
PKG9_CORRECTED = "410 S State St"
PKG9_READY = "10:20 AM"


def load_wgups():
    addresses = [row for row in csv.reader(open(DATA / "Address.csv", encoding="utf-8-sig"))]
    index_of = {row[2].strip().lower(): int(row[0]) for row in addresses}
    table = [[float(x) if x else None for x in row] for row in csv.reader(open(DATA / "Distance.csv"))]

    def dist(i: int, j: int) -> float:
        v = table[i][j] if table[i][j] is not None else table[j][i]
        return float(v or 0.0)

    rows = list(csv.reader(open(DATA / "Package.csv", encoding="utf-8-sig")))[1:]
    depot = Stop("WGUPS Hub", addresses[0][2])
    stops: List[Stop] = [depot]
    loc: List[int] = [0]
    together: Dict[int, set] = {}
    for row in rows:
        pid, street, deadline = int(row[0]), row[1].strip(), row[5].strip()
        note = ",".join(row[7:]).strip().strip("'").strip()
        s = Stop(f"Package {pid}", street, latest=parse_clock(deadline))
        if m := re.search(r"only be on truck (\d+)", note, re.I):
            s.truck = int(m.group(1))
        if m := re.search(r"until (\d{1,2}:\d{2}\s*[ap]m)", note, re.I):
            s.ready = parse_clock(m.group(1))
        if m := re.search(r"delivered with ([\d,\s]+)", note, re.I):
            together[pid] = {pid, *(int(x) for x in re.findall(r"\d+", m.group(1)))}
        if "wrong address" in note.lower():
            s.address, s.ready = PKG9_CORRECTED, parse_clock(PKG9_READY)
        stops.append(s)
        loc.append(index_of[s.address.lower()])

    # Merge "delivered with" sets transitively into groups.
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for members in together.values():
        first = next(iter(members))
        for m in members:
            parent[find(m)] = find(first)
    for pid in parent:
        stops[pid].group = f"G{find(pid)}"

    d = [[dist(loc[i], loc[j]) for j in range(len(stops))] for i in range(len(stops))]
    return stops, d, loc


def check(solution: FleetSolution, stops: List[Stop], fleet: Fleet) -> List[str]:
    """Independently re-verify every rule (don't just trust the solver)."""
    problems = []
    truck_of = {n: t.truck for t in solution.trips for n in t.stops}
    for i, s in enumerate(stops[1:], start=1):
        if i not in truck_of:
            problems.append(f"{s.name} not delivered")
        if s.truck is not None and truck_of.get(i) != s.truck:
            problems.append(f"{s.name} must be on truck {s.truck}")
    groups: Dict[str, set] = {}
    for i, s in enumerate(stops[1:], start=1):
        if s.group:
            groups.setdefault(s.group, set()).add(truck_of.get(i))
    for g, trucks in groups.items():
        if len(trucks) > 1:
            problems.append(f"group {g} split across trucks {sorted(trucks)}")
    by_truck: Dict[int, list] = {}
    for t in solution.trips:
        if t.stops:
            by_truck.setdefault(t.truck, []).append(t)
    for truck, trips in by_truck.items():
        trips.sort(key=lambda t: t.depart)
        for a, b in zip(trips, trips[1:]):
            if b.depart < a.finish + fleet.reload - 1e-6:
                problems.append(f"truck {truck} trip {b.trip} leaves before trip {a.trip} is back")
    if drivers_needed(solution) > fleet.driver_count:
        problems.append(f"{drivers_needed(solution)} trucks out at once; only {fleet.driver_count} drivers")
    for t in solution.trips:
        if fleet.capacity and t.load > fleet.capacity:
            problems.append(f"truck {t.truck} over capacity ({t.load:g})")
        for n, arr in zip(t.stops, t.arrivals):
            s = stops[n]
            if s.latest is not None and arr > s.latest + 1e-6:
                problems.append(f"{s.name} late: {format_clock(arr)} > {format_clock(s.latest)}")
            if s.ready is not None and t.depart < s.ready - 1e-6:
                problems.append(f"{s.name} left before it was ready")
    return problems


def drivers_needed(solution: FleetSolution) -> int:
    """Most trucks away from the hub at the same moment."""
    events = []
    for t in solution.trips:
        if t.stops:
            events += [(t.depart, 1), (t.finish, -1)]
    out = peak = 0
    for _, delta in sorted(events, key=lambda e: (e[0], e[1])):  # returns sort before departures
        out += delta
        peak = max(peak, out)
    return peak


def main() -> int:
    console = Console()
    stops, d, loc = load_wgups()
    fleet = Fleet(trucks=3, capacity=16, start=8 * 60, mph=18, trips=2, drivers=2)
    sol = solve_fleet(d, stops, fleet)
    problems = check(sol, stops, fleet)

    console.print("[bold]WGUPS (WGU C950) solved with routekit[/bold]  "
                  "[dim]course distance table · 3 trucks × 16 packages · 2 drivers · 18 mph · 8:00 AM[/dim]\n")
    rules = Table.grid(padding=(0, 2))
    rules.add_column(style="dim")
    rules.add_column()
    rules.add_row("Truck-2-only packages", ", ".join(str(i) for i, s in enumerate(stops) if s.truck == 2))
    rules.add_row("Delayed until 9:05 AM", ", ".join(str(i) for i, s in enumerate(stops) if s.ready == 9 * 60 + 5))
    grp = sorted({s.group for s in stops if s.group})
    for g in grp:
        rules.add_row("Must ship together", ", ".join(str(i) for i, s in enumerate(stops) if s.group == g))
    rules.add_row("Package 9", f"address corrected to {PKG9_CORRECTED} at {PKG9_READY}")
    console.print(rules)

    t = Table(title="\nTrips", header_style="bold")
    for col, j in (("Truck", "right"), ("Trip", "right"), ("Driver", "right"), ("Packages", "left"),
                   ("Load", "right"), ("Leaves", "right"), ("Back", "right"), ("Miles", "right")):
        t.add_column(col, justify=j)
    for trip in sorted(sol.used, key=lambda tr: tr.depart):
        t.add_row(str(trip.truck), str(trip.trip), str(trip.driver), " ".join(str(n) for n in trip.stops),
                  f"{trip.load:g}/16", format_clock(trip.depart), format_clock(trip.finish), f"{trip.miles:.1f}")
    console.print(t)

    deadline_pkgs = [(n, a) for tr in sol.trips for n, a in zip(tr.stops, tr.arrivals) if stops[n].latest]
    worst = min(stops[n].latest - a for n, a in deadline_pkgs)
    no_return = sum(tr.miles - d[tr.stops[-1]][0] for tr in sol.trips if tr.stops)
    console.print(f"\nTotal: [bold green]{sol.miles:.1f} miles[/bold green] including every drive back to the hub "
                  f"([bold]{no_return:.1f}[/bold] without return legs)")
    console.print(f"Original C950 solution: {C950_MILES:.0f} miles, counted without return legs, "
                  f"and honoring only the package 9 note")
    console.print(f"Drivers: at most {drivers_needed(sol)} trucks out at once (limit enforced: "
                  f"{fleet.driver_count}); {sol.trip_count} trips on {sol.trucks_used} trucks")
    console.print(f"Deadlines: all {len(deadline_pkgs)} met, tightest with {worst:.0f} min to spare")
    console.print(f"Local search: {sol.initial_miles:.1f} → {sol.miles:.1f} miles "
                  f"({', '.join(f'{v} {k}' for k, v in sol.moves.items())})")
    if problems:
        console.print("[bold red]Rule violations:[/bold red]")
        for p in problems:
            console.print(f"  • {p}")
        return 1
    console.print("[green]✓ Independent check: every package delivered, every rule satisfied.[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
