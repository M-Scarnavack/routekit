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
from routekit.entry import StopTextError, collect_interactive, offer_save, parse_lines, save_stops  # noqa: E402
from routekit.geocode import GeocodeError, geocode_stops  # noqa: E402
from routekit.mapview import MapScene  # noqa: E402
from rich.text import Text  # noqa: E402

from routekit.roads import RoadError, road_geometry, road_matrix, symmetric  # noqa: E402
from routekit.truck import Vehicle, truck_matrix, truck_route  # noqa: E402
from routekit.render import (failures_table, fleet_stops_table, fleet_summary_panel,  # noqa: E402
                             map_panel, stops_table, summary_panel, trucks_table, unassigned_table)
from routekit.solver import distance_matrix, nearest_neighbor, route_length, two_opt  # noqa: E402
from routekit.stops import Stop, StopFileError, format_clock, load_stops, parse_clock, save_route  # noqa: E402
from routekit.vrp import Fleet, FleetProblem, FleetSolution  # noqa: E402,F401


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="routekit",
        description="Plan delivery routes from a CSV of U.S. addresses, right in your terminal.",
    )
    p.add_argument("--version", action="version", version=f"routekit {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser(
        "plan", help="Optimize routes through a CSV of stops, or addresses you type.",
        description="Optimize routes. Give a CSV file, or type addresses: as arguments, piped in with '-', "
                    "or interactively (run with no stops). The first stop is the depot.",
        epilog='Examples:\n'
               '  routekit plan stops.csv\n'
               '  routekit plan "1100 W Randolph St, Chicago, IL" "233 S Wacker Dr, Chicago, IL @ 10:30 AM"\n'
               '  cat stops.txt | routekit plan -\n'
               '  routekit plan            (asks for addresses one at a time)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    plan.add_argument("inputs", nargs="*", metavar="STOP",
                      help="A CSV file, or addresses ('Name: address @ 10:30 AM' and 'lat, lon' also work), "
                           "or '-' to read one per line from stdin. Leave empty to type them interactively.")
    plan.add_argument("--roads", action="store_true",
                      help="Use real road miles and drive times (OSRM, OpenStreetMap data) instead of straight lines.")
    plan.add_argument("--osrm-url", metavar="URL",
                      help="OSRM server for --roads (default: the free public demo server, or $ROUTEKIT_OSRM_URL).")
    truck = plan.add_argument_group(
        "truck routing and tolls (Valhalla)",
        "Real road routing that respects truck restrictions and can avoid tolls, via the free public "
        "Valhalla server. Any of these options turns it on (no need for --roads).")
    truck.add_argument("--truck", action="store_true", help="Route as a truck (default size: 13.5 ft, 24 tons, 71 ft).")
    truck.add_argument("--height", type=float, metavar="FT", help="Truck height in feet (implies --truck).")
    truck.add_argument("--weight", type=float, metavar="TONS", help="Gross weight in US tons (implies --truck).")
    truck.add_argument("--length", type=float, metavar="FT", help="Truck length in feet (implies --truck).")
    truck.add_argument("--width", type=float, metavar="FT", help="Truck width in feet (implies --truck).")
    truck.add_argument("--hazmat", action="store_true", help="Carrying hazardous materials (implies --truck).")
    truck.add_argument("--avoid-tolls", action="store_true",
                       help="Never use toll roads, and report what avoiding them costs.")
    truck.add_argument("--valhalla-url", metavar="URL",
                       help="Valhalla server (default: the free public FOSSGIS server, or $ROUTEKIT_VALHALLA_URL).")
    plan.add_argument("--save-stops", type=Path, metavar="FILE",
                      help="Save the stops you typed to a CSV so you can rerun them later.")
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
    fleet.add_argument("--trips", type=int, default=1, metavar="N",
                       help="Max trips per truck: a truck can come back, reload, and go out again (default 1).")
    fleet.add_argument("--drivers", type=int, metavar="N",
                       help="Drivers available; at most this many trucks are out at once. "
                            "Drivers can switch trucks at the depot. Default: one per truck.")
    fleet.add_argument("--reload", type=float, default=0.0, metavar="MIN",
                       help="Minutes at the depot between a truck's trips (default 0).")
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


def gather_stops(args: argparse.Namespace, console: Console):
    """Stops from a CSV, typed arguments, stdin, or an interactive prompt. Returns (stops, typed)."""
    inputs = args.inputs
    source = "typed"
    if len(inputs) == 1 and inputs[0] != "-" and (inputs[0].lower().endswith(".csv") or Path(inputs[0]).is_file()):
        return load_stops(Path(inputs[0])), False
    if inputs == ["-"] or (not inputs and not sys.stdin.isatty()):
        source = "stdin"
        stops = parse_lines(sys.stdin.read().splitlines())
    elif not inputs:
        stops = collect_interactive(console, offline=args.offline)
        if len(stops) >= 2 and not args.save_stops:
            offer_save(console, stops)
    else:
        if "-" in inputs:
            raise StopTextError("'-' (read from stdin) can't be mixed with typed addresses")
        stops = parse_lines(inputs, unit="stop")
    if len(stops) + (1 if args.depot else 0) < 2:
        if source == "stdin":
            raise StopTextError("no stops given. Pass a CSV or addresses, pipe them in one per line, "
                                "or run 'routekit plan' in a terminal to type them interactively")
        raise StopTextError("need at least 2 stops: a depot (the first one) and somewhere to go")
    return stops, True


def plan(args: argparse.Namespace, console: Console) -> int:
    try:
        stops, _typed = gather_stops(args, console)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] no such file: {e.filename}")
        return 2
    except (OSError, StopFileError, StopTextError) as e:
        console.print(f"[red]Error:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")
        return 130
    try:
        start = parse_clock(args.start)
        end = parse_clock(args.end) if args.end else None
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 2
    for flag, value in (("--trucks", args.trucks), ("--trips", args.trips), ("--drivers", args.drivers)):
        if value is not None and value < 1:
            console.print(f"[red]Error:[/red] {flag} must be at least 1.")
            return 2
    fleet_mode = args.trucks > 1 or args.capacity is not None or args.end is not None \
        or args.service > 0 or args.trips > 1 or args.drivers is not None or args.reload > 0 \
        or any(s.constrained or s.service for s in stops)
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

    if args.save_stops:
        save_stops(args.save_stops, stops)
        console.print(f"[green]Saved {len(stops)} stops to {args.save_stops}[/green]")

    if failed and failed[0] is stops[0]:
        console.print(f"[red]Error:[/red] could not locate the depot: {stops[0].address!r}")
        return 1
    located = [s for s in stops if s.located]
    if len(located) < 2:
        console.print("[red]Error:[/red] fewer than 2 stops could be located.")
        if failed:
            console.print(failures_table(failed))
        return 1

    minutes = None
    distances = "straight-line miles"
    args.vehicle = vehicle_from(args)
    if args.vehicle is not None:
        try:
            with console.status("Getting truck road distances from Valhalla…") as status:
                miles_dir, minutes = truck_matrix(
                    [(s.lat, s.lon) for s in located], args.vehicle, args.valhalla_url,
                    progress=lambda k, n: status.update(f"Getting road distances from Valhalla… {k}/{n}"))
        except RoadError as e:
            console.print(f"[red]Error:[/red] {e}. Run without truck/toll options for straight-line distances.")
            return 1
        d = symmetric(miles_dir)
        distances = f"road miles · {args.vehicle.describe()} · OpenStreetMap via Valhalla"
    elif args.roads:
        try:
            with console.status("Getting road distances from OSRM…") as status:
                miles_dir, minutes = road_matrix(
                    [(s.lat, s.lon) for s in located], args.osrm_url,
                    progress=lambda k, n: status.update(f"Getting road distances from OSRM… {k}/{n}"))
        except RoadError as e:
            console.print(f"[red]Error:[/red] {e}. Run without --roads for straight-line distances.")
            return 1
        d = symmetric(miles_dir)  # optimize on the average of both directions
        distances = "road miles · OpenStreetMap via OSRM"
    else:
        d = distance_matrix([(s.lat, s.lon) for s in located])
    if fleet_mode:
        return plan_fleet(args, console, located, d, failed, start if start is not None else 8 * 60, end,
                          minutes, distances)

    round_trip = not args.one_way
    nn = nearest_neighbor(d, round_trip=round_trip)
    nn_miles = route_length(nn, d)
    route = nn if args.no_optimize else two_opt(nn, d, round_trip=round_trip)
    miles = route_length(route, d)

    ordered = [located[i] for i in route]
    legs = [d[a][b] for a, b in zip(route, route[1:])]
    leg_minutes = [minutes[a][b] for a, b in zip(route, route[1:])] if minutes else None

    legs_info = route_legs(console, args, [ordered])
    console.print(summary_panel(len(located) - 1, nn_miles, miles, not args.no_optimize,
                                round_trip, args.mph, len(failed),
                                sum(leg_minutes) if leg_minutes else None, distances,
                                extra_rows=legs_info.summary_rows() if legs_info else None))
    if not args.no_map:
        width = max(30, min(console.width - 4, 100))
        scene = MapScene([ordered], cols=width, basemap=not args.no_basemap, labels=not args.no_labels,
                         shapes=legs_info.shapes if legs_info else None)
        if args.no_animate or not console.is_terminal:
            console.print(map_panel(scene))
        else:
            animate(scene, console, n_stops=len(located))
    console.print(stops_table(ordered, legs, round_trip, args.mph, leg_minutes,
                              legs_info.tolls[0] if legs_info and legs_info.has_tolls_data else None))
    ft = failures_table(failed)
    if ft:
        console.print(ft)
    if args.output:
        save_route(args.output, ordered, legs)
        console.print(f"[green]Saved route to {args.output}[/green]")
    return 0


def vehicle_from(args) -> Optional[Vehicle]:
    """A Valhalla vehicle if any truck/toll option was given, else None."""
    dims = (args.height, args.weight, args.length, args.width)
    if not (args.truck or args.avoid_tolls or args.hazmat or any(v is not None for v in dims)):
        return None
    is_truck = args.truck or args.hazmat or any(v is not None for v in dims)
    return Vehicle(truck=is_truck, height_ft=args.height, weight_tons=args.weight, length_ft=args.length,
                   width_ft=args.width, hazmat=args.hazmat, avoid_tolls=args.avoid_tolls)


class RouteLegs:
    """Road shapes (and, with Valhalla, toll miles) for each leg of each route."""

    def __init__(self, shapes, tolls=None, avoid_delta=None):
        self.shapes = shapes            # [route][leg] -> [(lat, lon)]
        self.tolls = tolls              # [route][leg] -> toll miles, or None (OSRM)
        self.avoid_delta = avoid_delta  # (extra miles, extra minutes) from avoiding tolls

    @property
    def has_tolls_data(self) -> bool:
        return self.tolls is not None

    def summary_rows(self):
        rows = []
        if self.tolls is not None:
            toll_mi = sum(t for route in self.tolls for t in route)
            n = sum(1 for route in self.tolls for t in route if t > 0.005)
            if self.avoid_delta is not None:
                extra_mi, extra_min = self.avoid_delta
                if n:
                    rows.append(("Tolls", Text(f"couldn't avoid all of them: ≈{toll_mi:,.1f} mi on {n} leg(s) "
                                               "have no toll-free road", style="red")))
                elif extra_mi < 0.05 and extra_min < 0.5:
                    rows.append(("Tolls", Text("avoided, at no extra cost: this route has no tolls anyway", style="green")))
                else:
                    rows.append(("Tolls", Text.assemble(("avoided", "green"),
                                                        (f"  (+{extra_mi:,.1f} mi, +{extra_min:,.0f} min vs. "
                                                         "using toll roads, same stop order)", "yellow"))))
            elif n:
                rows.append(("Tolls", Text(f"≈{toll_mi:,.1f} mi on toll roads, {n} leg(s); add --avoid-tolls "
                                           "to compare", style="yellow")))
            else:
                rows.append(("Tolls", Text("none on this route", style="green")))
        return rows


def route_legs(console: Console, args, routes: List[List[Stop]]) -> Optional[RouteLegs]:
    """Fetch road legs for the map and toll report; None for straight lines (or on error)."""
    if getattr(args, "vehicle", None) is None and not args.roads:
        return None
    try:
        with console.status("Getting road shapes and tolls…" if getattr(args, "vehicle", None)
                            else "Getting road shapes for the map…"):
            if args.vehicle is None:
                return RouteLegs([road_geometry([(s.lat, s.lon) for s in r], args.osrm_url) for r in routes])
            pts = [[(s.lat, s.lon) for s in r] for r in routes]
            legs = [truck_route(p, args.vehicle, args.valhalla_url) for p in pts]
            delta = None
            if args.vehicle.avoid_tolls:
                with_tolls = [truck_route(p, args.vehicle, args.valhalla_url, avoid_tolls=False) for p in pts]
                delta = (sum(l.miles for r in legs for l in r) - sum(l.miles for r in with_tolls for l in r),
                         sum(l.minutes for r in legs for l in r) - sum(l.minutes for r in with_tolls for l in r))
            return RouteLegs([[l.shape for l in r] for r in legs], [[l.toll_miles for l in r] for r in legs], delta)
    except RoadError as e:
        console.print(f"[yellow]Note:[/yellow] drawing straight lines on the map ({e}).")
        return None


def plan_fleet(args, console: Console, located: List[Stop], d, failed: List[Stop],
               start: float, end: Optional[float], minutes=None, distances: str = "straight-line miles") -> int:
    capacity, balanced = args.capacity, False
    if capacity is None and args.trucks > 1 and args.trips == 1 and not any(s.constrained for s in located[1:]):
        capacity = math.ceil(sum(s.demand for s in located[1:]) / args.trucks)
        balanced = True
    if args.trips > 1 and capacity is None:
        console.print("[yellow]Note:[/yellow] --trips only matters with a --capacity (or time rules); "
                      "without one, every truck can carry everything in one trip.")
    drivers = args.drivers if args.drivers is not None and args.drivers < args.trucks else None
    fleet = Fleet(trucks=args.trucks, capacity=capacity, start=start, end=end, mph=args.mph, service=args.service,
                  trips=args.trips, drivers=drivers, reload=args.reload)
    problem = FleetProblem(d, located, fleet, minutes)
    with console.status("Planning trucks…"):
        sol = problem.solve(optimize=not args.no_optimize)

    used = sol.used
    by_truck: dict = {}
    for t in sorted(used, key=lambda t: (t.truck, t.trip)):
        by_truck.setdefault(t.truck, []).append(t)
    depot = located[0]
    truck_routes, names = [], []
    for truck, trips in by_truck.items():
        route = [depot]
        for t in trips:
            route += [located[n] for n in t.stops] + [depot]
        truck_routes.append(route)
        names.append(f"Truck {truck}" + (f" ({len(trips)} trips)" if len(trips) > 1 else ""))
    legs_info = route_legs(console, args, truck_routes) if used else None
    trip_tolls = None
    if legs_info and legs_info.has_tolls_data:
        trip_tolls = {}
        for (truck, trips), tolls in zip(by_truck.items(), legs_info.tolls):
            k = 0
            for t in trips:  # each trip is len(stops) + 1 legs of its truck's chained route
                trip_tolls[(t.truck, t.trip)] = sum(tolls[k:k + len(t.stops) + 1])
                k += len(t.stops) + 1
    console.print(fleet_summary_panel(sol, fleet, located, not args.no_optimize, len(failed), balanced, distances,
                                      extra_rows=legs_info.summary_rows() if legs_info else None))
    if not args.no_map and used:
        width = max(30, min(console.width - 4, 100))
        scene = MapScene(truck_routes, cols=width, basemap=not args.no_basemap, labels=not args.no_labels,
                         names=names, shapes=legs_info.shapes if legs_info else None)
        if args.no_animate or not console.is_terminal:
            console.print(map_panel(scene))
        else:
            animate(scene, console, n_stops=len(located))
    console.print(trucks_table(sol, fleet, trip_tolls))
    console.print(fleet_stops_table(sol, located, d, fleet))
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
        w.writerow(["truck", "trip", "driver", "order", "name", "address", "lat", "lon", "departs", "arrives",
                    "deadline", "leg_miles", "trip_miles"])
        for tr in sorted(sol.used, key=lambda t: (t.truck, t.trip)):
            prev, total = 0, 0.0
            for k, (n, arr) in enumerate(zip(tr.stops, tr.arrivals), start=1):
                s = located[n]
                total += d[prev][n]
                w.writerow([tr.truck, tr.trip, tr.driver or "", k, s.name, s.address, f"{s.lat:.6f}",
                            f"{s.lon:.6f}", format_clock(tr.depart), format_clock(arr), format_clock(s.latest),
                            f"{d[prev][n]:.2f}", f"{total:.2f}"])
                prev = n
        for n in sol.unassigned:
            s = located[n]
            w.writerow(["", "", "", "", s.name, s.address, f"{s.lat:.6f}", f"{s.lon:.6f}", "", "NOT DELIVERED",
                        format_clock(s.latest), "", ""])


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    if args.command == "plan":
        return plan(args, console)
    return 2


if __name__ == "__main__":
    sys.exit(main())
