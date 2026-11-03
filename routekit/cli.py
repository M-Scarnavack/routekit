"""Command-line interface: ``routekit plan stops.csv``."""

from __future__ import annotations

import argparse
import math
import sys
import time
import warnings
from pathlib import Path
from typing import List, Optional

warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402

from routekit import __version__  # noqa: E402
from routekit.geocode import GeocodeError, geocode_stops  # noqa: E402
from routekit.mapview import MapScene  # noqa: E402
from routekit.render import (failures_table, fleet_stops_table, fleet_summary_panel,  # noqa: E402
                             map_panel, stops_table, summary_panel, trucks_table, unassigned_table)
from routekit.solver import distance_matrix, nearest_neighbor, route_length, two_opt  # noqa: E402
from routekit.stops import Stop, StopFileError, format_clock, load_stops, parse_clock, save_route  # noqa: E402
from routekit.vrp import Fleet, FleetProblem, FleetSolution  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="routekit",
        description="Plan delivery routes from a CSV of U.S. addresses, right in your terminal.",
    )
    p.add_argument("--version", action="version", version=f"routekit {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Optimize a route through every stop in a CSV.")
    plan.add_argument("csv", type=Path, help="CSV with an 'address' column (or street/city/state/zip, or lat/lon).")
    plan.add_argument("--depot", metavar="ADDRESS",
                      help="Start (and end) address. Default: the first row of the CSV.")
    plan.add_argument("--one-way", action="store_true", help="Don't return to the depot at the end.")
    plan.add_argument("--no-optimize", action="store_true", help="Skip 2-opt; show the nearest-neighbor route only.")
    plan.add_argument("--mph", type=float, default=45.0, help="Average speed for drive-time estimates (default 45).")
    plan.add_argument("--offline", action="store_true", help="Don't call the geocoder; use cached or CSV coordinates only.")
    plan.add_argument("--no-map", action="store_true", help="Skip the terminal map.")
    plan.add_argument("--no-animate", action="store_true", help="Draw the map instantly instead of animating the route.")
    plan.add_argument("--no-basemap", action="store_true", help="Hide the state-outline background.")
    plan.add_argument("--no-labels", action="store_true", help="Hide stop-order numbers on the map.")
    plan.add_argument("-o", "--output", type=Path, metavar="FILE", help="Write the ordered route to a CSV.")

    fleet = plan.add_argument_group(
        "fleet planning",
        "Use several trucks, capacities, and time windows. Also enabled automatically when the CSV has "
        "deadline/earliest/ready/truck/group columns.")
    fleet.add_argument("--trucks", type=int, default=1, metavar="N", help="Trucks available (default 1).")
    fleet.add_argument("--capacity", type=float, metavar="C",
                       help="Max load per truck, in the CSV's 'demand' units (each stop is 1 by default). "
                            "Without it, stops are split evenly across trucks.")
    fleet.add_argument("--start", default="8:00", metavar="TIME", help="When trucks leave the depot (default 8:00).")
    fleet.add_argument("--end", metavar="TIME", help="Trucks must be back by this time.")
    fleet.add_argument("--service", type=float, default=0.0, metavar="MIN",
                       help="Minutes spent at each stop (default 0; a 'service' column overrides).")
    return p


FPS = 30


def animation_frames(total_dots: int, n_stops: int, fps: int = FPS) -> List[int]:
    """How many route dots to reveal on each frame: about 2–6 seconds, longer for more stops."""
    seconds = min(6.0, 2.0 + 0.08 * n_stops)
    count = max(2, int(seconds * fps))
    step = max(1, math.ceil(total_dots / count))
    return list(range(1, total_dots, step)) + [total_dots]


def animate(scene: MapScene, console: Console, n_stops: int) -> None:
    with Live(map_panel(scene, 1), console=console, refresh_per_second=FPS, transient=False) as live:
        try:
            for revealed in animation_frames(scene.total_dots, n_stops):
                live.update(map_panel(scene, revealed))
                time.sleep(1 / FPS)
        except KeyboardInterrupt:
            pass  # Ctrl+C skips to the finished map
        live.update(map_panel(scene))


def plan(args: argparse.Namespace, console: Console) -> int:
    try:
        stops = load_stops(args.csv)
    except (OSError, StopFileError) as e:
        console.print(f"[red]Error:[/red] {e}")
        return 2
    try:
        start = parse_clock(args.start)
        end = parse_clock(args.end) if args.end else None
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 2
    if args.trucks < 1:
        console.print("[red]Error:[/red] --trucks must be at least 1.")
        return 2
    fleet_mode = args.trucks > 1 or args.capacity is not None or args.end is not None \
        or args.service > 0 or any(s.constrained or s.service for s in stops)
    if fleet_mode and args.one_way:
        console.print("[red]Error:[/red] --one-way isn't supported for fleet plans yet; trucks return to the depot.")
        return 2

    if args.depot:
        stops.insert(0, Stop(name="Depot", address=args.depot))

    try:
        if all(s.located for s in stops):
            failed = []
        else:
            with console.status("Geocoding addresses with the U.S. Census geocoder…"):
                failed = geocode_stops(stops, offline=args.offline)
    except GeocodeError as e:
        console.print(f"[red]Error:[/red] {e}\nTip: re-run with --offline to use cached results.")
        return 1

    if failed and failed[0] is stops[0]:
        console.print(f"[red]Error:[/red] could not locate the depot: {stops[0].address!r}")
        return 1
    located = [s for s in stops if s.located]
    if len(located) < 2:
        console.print("[red]Error:[/red] fewer than 2 stops could be located.")
        if failed:
            console.print(failures_table(failed))
        return 1

    d = distance_matrix([(s.lat, s.lon) for s in located])
    if fleet_mode:
        return plan_fleet(args, console, located, d, failed, start if start is not None else 8 * 60, end)

    round_trip = not args.one_way
    nn = nearest_neighbor(d, round_trip=round_trip)
    nn_miles = route_length(nn, d)
    route = nn if args.no_optimize else two_opt(nn, d, round_trip=round_trip)
    miles = route_length(route, d)

    ordered = [located[i] for i in route]
    legs = [d[a][b] for a, b in zip(route, route[1:])]

    console.print(summary_panel(len(located) - 1, nn_miles, miles, not args.no_optimize,
                                round_trip, args.mph, len(failed)))
    if not args.no_map:
        width = max(30, min(console.width - 4, 100))
        scene = MapScene(ordered, cols=width, basemap=not args.no_basemap, labels=not args.no_labels)
        if args.no_animate or not console.is_terminal:
            console.print(map_panel(scene))
        else:
            animate(scene, console, n_stops=len(located))
    console.print(stops_table(ordered, legs, round_trip, args.mph))
    ft = failures_table(failed)
    if ft:
        console.print(ft)
    if args.output:
        save_route(args.output, ordered, legs)
        console.print(f"[green]Saved route to {args.output}[/green]")
    return 0


def plan_fleet(args, console: Console, located: List[Stop], d, failed: List[Stop],
               start: float, end: Optional[float]) -> int:
    capacity, balanced = args.capacity, False
    if capacity is None and args.trucks > 1 and not any(s.constrained for s in located[1:]):
        capacity = math.ceil(sum(s.demand for s in located[1:]) / args.trucks)
        balanced = True
    fleet = Fleet(trucks=args.trucks, capacity=capacity, start=start, end=end, mph=args.mph, service=args.service)
    problem = FleetProblem(d, located, fleet)
    with console.status("Planning trucks…"):
        if args.no_optimize:
            routes, unassigned = problem.construct()
            trips = [problem.plan(r, t) for t, r in enumerate(routes)]
            initial = sum(t.miles for t in trips)
            sol = FleetSolution(trips, sorted(unassigned), initial, initial)
        else:
            sol = problem.solve()

    console.print(fleet_summary_panel(sol, fleet, located, not args.no_optimize, len(failed), balanced))
    used = [t for t in sol.trips if t.stops]
    if not args.no_map and used:
        width = max(30, min(console.width - 4, 100))
        depot = located[0]
        routes = [[depot] + [located[n] for n in t.stops] + [depot] for t in used]
        scene = MapScene(routes, cols=width, basemap=not args.no_basemap, labels=not args.no_labels,
                         names=[f"Truck {t.truck}" for t in used])
        if args.no_animate or not console.is_terminal:
            console.print(map_panel(scene))
        else:
            animate(scene, console, n_stops=len(located))
    console.print(trucks_table(sol, fleet))
    console.print(fleet_stops_table(sol, located, d))
    for extra in (unassigned_table(sol, located), failures_table(failed)):
        if extra:
            console.print(extra)
    if args.output:
        save_fleet(args.output, sol, located, d)
        console.print(f"[green]Saved plan to {args.output}[/green]")
    return 0


def save_fleet(path: Path, sol: FleetSolution, located: List[Stop], d) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["truck", "order", "name", "address", "lat", "lon", "arrives", "deadline",
                    "leg_miles", "truck_miles"])
        for tr in sol.trips:
            if not tr.stops:
                continue
            prev, total = 0, 0.0
            for k, (n, arr) in enumerate(zip(tr.stops, tr.arrivals), start=1):
                s = located[n]
                total += d[prev][n]
                w.writerow([tr.truck, k, s.name, s.address, f"{s.lat:.6f}", f"{s.lon:.6f}", format_clock(arr),
                            format_clock(s.latest), f"{d[prev][n]:.2f}", f"{total:.2f}"])
                prev = n
        for n in sol.unassigned:
            s = located[n]
            w.writerow(["", "", s.name, s.address, f"{s.lat:.6f}", f"{s.lon:.6f}", "NOT DELIVERED",
                        format_clock(s.latest), "", ""])


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    if args.command == "plan":
        return plan(args, console)
    return 2


if __name__ == "__main__":
    sys.exit(main())
