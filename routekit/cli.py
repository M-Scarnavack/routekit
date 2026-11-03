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

from routekit.fema import (KINDS, DisasterDataError, assess, counties, county_name,  # noqa: E402
                           group_by_disaster, open_declarations)
from routekit.htmlmap import (SINGLE_COLOR, TRUCK_COLORS, MapOverlay, MapStop, MapTrack,  # noqa: E402
                              road_path, write_html)
from routekit.render import _window  # noqa: E402
from routekit.roads import RoadError, road_geometry, road_matrix, symmetric  # noqa: E402
from routekit.tracking import TrackingError, compass, provider_from_env  # noqa: E402
from routekit.truck import MatrixRangeError, Vehicle, truck_matrix, truck_route  # noqa: E402
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
    relief = plan.add_argument_group(
        "disaster relief (FEMA + NWS)",
        "Flag stops in counties with open FEMA disaster declarations or active National Weather Service "
        "alerts (free U.S. government data, no key).")
    relief.add_argument("--fema", action="store_true", help="Flag stops in declared disaster areas or under weather alerts.")
    relief.add_argument("--fema-days", type=int, default=120, metavar="N",
                        help="Count declarations made in the last N days (default 120).")
    relief.add_argument("--fema-within", type=float, metavar="HOURS",
                        help="Priority: stops in declared areas must be reached within HOURS of the start (implies --fema).")
    plan.add_argument("--start-at-truck", metavar="NAME",
                      help="Start from a tracked truck's current location (Traccar) instead of a depot; "
                           "plans a one-way route.")
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
    plan.add_argument("--html", type=Path, metavar="FILE",
                      help="Also write an interactive map (open it in any browser, or share the file).")

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
    trk = sub.add_parser(
        "trucks", help="Where are my trucks? Live locations from your tracking system (Traccar).",
        description="Show each truck's latest GPS position from Traccar (free, open-source tracking). "
                    "Set ROUTEKIT_TRACCAR_URL and ROUTEKIT_TRACCAR_TOKEN (or _USER/_PASSWORD).")
    trk.add_argument("--json", action="store_true", help="Print machine-readable JSON (for scripts and agents).")
    trk.add_argument("--html", type=Path, metavar="FILE", help="Save a browser map of the trucks.")
    sub.add_parser("mcp", help="Run an MCP server so AI agents (Claude Code, Claude Desktop, ...) can use routekit as tools.",
                   description="Speak the Model Context Protocol over stdin/stdout. Register it with your agent, "
                               "e.g.: claude mcp add routekit -- routekit mcp")
    dis = sub.add_parser(
        "disasters", help="List open FEMA disaster declarations, and turn declared counties into relief stops.",
        description="List open FEMA disaster declarations (OpenFEMA, free). Give a disaster number to see its "
                    "counties, and --stops to save them as a CSV you can route with 'routekit plan'.")
    dis.add_argument("number", nargs="?", type=int, help="A FEMA disaster number, e.g. 4955.")
    dis.add_argument("--state", action="append", metavar="ST", help="Only these states (repeatable), e.g. --state FL.")
    dis.add_argument("--days", type=int, default=120, metavar="N", help="Declared in the last N days (default 120).")
    dis.add_argument("--stops", type=Path, metavar="FILE",
                     help="Save the declared counties as relief stops (each county's center) to a CSV.")
    dis.add_argument("--depot", metavar="ADDRESS", help="With --stops: put this staging area first, as the depot.")
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
    if args.start_at_truck:
        if args.depot:
            console.print("[red]Error:[/red] use --depot or --start-at-truck, not both.")
            return 2
        try:
            with console.status("Finding the truck…"):
                truck = provider_from_env().find(args.start_at_truck)
        except TrackingError as e:
            console.print(f"[red]Error:[/red] {e}")
            return 1
        age = truck.age_minutes
        console.print(f"[green]✓[/green] {truck.name}: {truck.lat:.5f}, {truck.lon:.5f}"
                      + (f" · {truck.speed_mph:.0f} mph {compass(truck.heading)}" if truck.moving else " · stopped")
                      + (f" · position {age:.0f} min old" if age is not None else ""))
        if age is not None and age > 30:
            console.print(f"[yellow]Note:[/yellow] that position is {age:.0f} minutes old.")
        stops.insert(0, Stop(name=f"{truck.name} (current location)", address="", lat=truck.lat, lon=truck.lon))
        args.one_way = True
        args.tracked_name = truck.name
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
    if fleet_mode and args.one_way and args.trips > 1:
        console.print("[red]Error:[/red] --one-way allows one trip per truck (a truck can't reload without "
                      "returning to the depot).")
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

    args.risk, args.priority = None, None
    if args.fema or args.fema_within is not None:
        try:
            with console.status("Checking FEMA disaster declarations and NWS weather alerts…"):
                risks, decls, alerts = assess([(s.lat, s.lon) for s in located], days=args.fema_days)
        except DisasterDataError as e:
            console.print(f"[red]Error:[/red] {e}. Run without --fema to skip disaster checks.")
            return 1
        args.risk = {id(s): r for s, r in zip(located, risks)}
        args.alerts = alerts
        if args.fema_within is not None:
            due = (start if start is not None else 8 * 60) + args.fema_within * 60
            count = 0
            for st in located[1:]:
                if args.risk[id(st)].declared:
                    st.latest = due if st.latest is None else min(st.latest, due)
                    count += 1
            args.priority = (count, due)
            fleet_mode = True

    minutes = None
    distances = "straight-line miles"
    args.vehicle = vehicle_from(args)
    args.planning_note = None
    if args.vehicle is not None:
        try:
            with console.status("Getting truck road distances from Valhalla…") as status:
                miles_dir, minutes = truck_matrix(
                    [(s.lat, s.lon) for s in located], args.vehicle, args.valhalla_url,
                    progress=lambda k, n: status.update(f"Getting road distances from Valhalla… {k}/{n}"))
            distances = f"road miles · {args.vehicle.describe()} · OpenStreetMap via Valhalla"
        except MatrixRangeError:
            # Long-haul: plan on OSRM's (car) road table, then truck-route each final leg with Valhalla.
            try:
                with console.status("Stops are far apart: getting the road distance table from OSRM…") as status:
                    miles_dir, minutes = road_matrix(
                        [(s.lat, s.lon) for s in located], args.osrm_url,
                        progress=lambda k, n: status.update(f"Getting road distances from OSRM… {k}/{n}"))
            except RoadError as e:
                console.print(f"[red]Error:[/red] {e}. Run without truck/toll options for straight-line distances.")
                return 1
            distances = f"road miles · planned on car roads (OSRM), legs routed as {args.vehicle.describe()} (Valhalla)"
            args.planning_note = ("Planning", Text(
                "stops are over ~93 mi apart, beyond the public Valhalla server's truck distance table, so stop "
                "order and times use car road data; the map, miles, and tolls below use truck routing",
                style="yellow"))
        except RoadError as e:
            console.print(f"[red]Error:[/red] {e}. Run without truck/toll options for straight-line distances.")
            return 1
        d = symmetric(miles_dir)
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
    panel = summary_panel(len(located) - 1, nn_miles, miles, not args.no_optimize,
                          round_trip, args.mph, len(failed),
                          sum(leg_minutes) if leg_minutes else None, distances,
                          extra_rows=(legs_info.summary_rows() if legs_info else []) + fema_rows(args, located)
                          + ([args.planning_note] if args.planning_note else []))
    console.print(panel)
    if not args.no_map:
        width = max(30, min(console.width - 4, 100))
        scene = MapScene([ordered], cols=width, basemap=not args.no_basemap, labels=not args.no_labels,
                         shapes=legs_info.shapes if legs_info else None, flagged=flagged_ids(args))
        if args.no_animate or not console.is_terminal:
            console.print(map_panel(scene))
        else:
            animate(scene, console, n_stops=len(located))
    console.print(stops_table(ordered, legs, round_trip, args.mph, leg_minutes,
                              legs_info.tolls[0] if legs_info and legs_info.has_tolls_data else None))
    print_flags(console, args, located)
    ft = failures_table(failed)
    if ft:
        console.print(ft)
    if args.output:
        save_route(args.output, ordered, legs)
        console.print(f"[green]Saved route to {args.output}[/green]")
    if args.html:
        stops_out, total, total_min = [], 0.0, 0.0
        n_points = len(ordered) - (1 if round_trip else 0)
        for i in range(1, n_points):
            total += legs[i - 1]
            total_min += leg_minutes[i - 1] if leg_minutes else legs[i - 1] / args.mph * 60
            s = ordered[i]
            stops_out.append(MapStop(s.lat, s.lon, str(i), s.name, [
                x for x in (s.address, f"Stop {i} of {n_points - 1}",
                            f"{total:,.1f} mi and {fmt_duration(total_min)} from the depot") if x]
                + risk_notes(args, s), flagged=is_flagged(args, s)))
        track = MapTrack("Route", SINGLE_COLOR,
                         road_path([(s.lat, s.lon) for s in ordered], legs_info.shapes[0] if legs_info else None),
                         stops_out)
        save_html(console, args, panel, ordered[0], [track], distances)
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
                  trips=args.trips, drivers=drivers, reload=args.reload, round_trip=not args.one_way)
    problem = FleetProblem(d, located, fleet, minutes)
    with console.status("Planning trucks…"):
        sol = problem.solve(optimize=not args.no_optimize)

    used = sol.used
    # A plan that starts from a tracked truck is about that truck: use its real name.
    display = {1: args.tracked_name} if getattr(args, "tracked_name", None) and fleet.trucks == 1 else {}
    by_truck: dict = {}
    for t in sorted(used, key=lambda t: (t.truck, t.trip)):
        by_truck.setdefault(t.truck, []).append(t)
    depot = located[0]
    truck_routes, names = [], []
    for truck, trips in by_truck.items():
        route = [depot]
        for t in trips:
            route += [located[n] for n in t.stops] + ([depot] if fleet.round_trip else [])
        truck_routes.append(route)
        names.append(display.get(truck, f"Truck {truck}") + (f" ({len(trips)} trips)" if len(trips) > 1 else ""))
    legs_info = route_legs(console, args, truck_routes) if used else None
    trip_tolls = None
    if legs_info and legs_info.has_tolls_data:
        trip_tolls = {}
        for (truck, trips), tolls in zip(by_truck.items(), legs_info.tolls):
            k = 0
            for t in trips:  # each trip is len(stops) (+ 1 return) legs of its truck's chained route
                n_legs = len(t.stops) + (1 if fleet.round_trip else 0)
                trip_tolls[(t.truck, t.trip)] = sum(tolls[k:k + n_legs])
                k += n_legs
    panel = fleet_summary_panel(sol, fleet, located, not args.no_optimize, len(failed), balanced, distances,
                                extra_rows=(legs_info.summary_rows() if legs_info else []) + fema_rows(args, located)
                          + ([args.planning_note] if args.planning_note else []))
    console.print(panel)
    if not args.no_map and used:
        width = max(30, min(console.width - 4, 100))
        scene = MapScene(truck_routes, cols=width, basemap=not args.no_basemap, labels=not args.no_labels,
                         names=names, shapes=legs_info.shapes if legs_info else None, flagged=flagged_ids(args))
        if args.no_animate or not console.is_terminal:
            console.print(map_panel(scene))
        else:
            animate(scene, console, n_stops=len(located))
    console.print(trucks_table(sol, fleet, trip_tolls, display))
    console.print(fleet_stops_table(sol, located, d, fleet, display))
    print_flags(console, args, located)
    for extra in (unassigned_table(sol, located), failures_table(failed)):
        if extra:
            console.print(extra)
    if args.output:
        save_fleet(args.output, sol, located, d)
        console.print(f"[green]Saved plan to {args.output}[/green]")
    if args.html and used:
        tracks = []
        for k, ((truck, trips), route) in enumerate(zip(by_truck.items(), truck_routes)):
            stops_out, number = [], 0
            for t in trips:
                for n, arr in zip(t.stops, t.arrivals):
                    number += 1
                    s = located[n]
                    window = _window(s)
                    stops_out.append(MapStop(s.lat, s.lon, str(number), s.name, [x for x in (
                        s.address,
                        f"Truck {truck}" + (f" · trip {t.trip}" if fleet.trips > 1 else "")
                        + (f" · driver {t.driver}" if t.driver else ""),
                        f"Arrives {format_clock(arr)}",
                        f"Window: {window}" if window else "") if x] + risk_notes(args, s),
                        flagged=is_flagged(args, s)))
            tracks.append(MapTrack(names[k], TRUCK_COLORS[(truck - 1) % len(TRUCK_COLORS)],
                                   road_path([(s.lat, s.lon) for s in route],
                                             legs_info.shapes[k] if legs_info else None), stops_out))
        save_html(console, args, panel, located[0], tracks, distances)
    return 0


def fmt_duration(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}h {m % 60:02d}m"


def save_html(console: Console, args, panel, depot: Stop, tracks, distances: str) -> None:
    import io
    rec = Console(record=True, width=64, file=io.StringIO(), color_system=None)
    rec.print(panel.renderable)  # the rows without the box, so long values wrap cleanly
    summary = "\n".join(line.rstrip() for line in rec.export_text().splitlines()).strip("\n")
    title = f"routekit · {Path(args.inputs[0]).name if len(args.inputs) == 1 else 'route plan'}"
    write_html(args.html, title, summary, (depot.lat, depot.lon), depot.name, tracks,
               overlays=fema_overlays(args),
               footnote=f"Distances: {distances}. Click a stop for details; toggle layers at top right."
                        + (" Red-ringed stops are in FEMA-declared areas or under NWS alerts."
                           if getattr(args, "risk", None) else ""))
    console.print(f"[green]Saved interactive map to {args.html}[/green]  [dim](open it in a browser)[/dim]")


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


# -- disaster relief helpers ----------------------------------------------------

def is_flagged(args, stop: Stop) -> bool:
    risk = getattr(args, "risk", None)
    return bool(risk and risk.get(id(stop)) and risk[id(stop)].flagged)


def flagged_ids(args):
    risk = getattr(args, "risk", None)
    return {k for k, r in risk.items() if r.flagged} if risk else None


def risk_notes(args, stop: Stop) -> List[str]:
    risk = getattr(args, "risk", None)
    r = risk.get(id(stop)) if risk else None
    return r.notes() if r else []


def fema_rows(args, located: List[Stop]):
    risk = getattr(args, "risk", None)
    if not risk:
        return []
    stops = located[1:]
    declared = [s for s in stops if risk[id(s)].declared]
    alerted = [s for s in stops if risk[id(s)].alerts]
    rows = []
    if declared:
        names = sorted({d.label for s in declared for d in risk[id(s)].declarations})
        rows.append(("FEMA", Text(f"{len(declared)} of {len(stops)} stops in declared disaster areas: "
                                  + "; ".join(names), style="bold red")))
    else:
        rows.append(("FEMA", Text(f"no stops in open declarations (last {args.fema_days} days)", style="green")))
    if alerted:
        events = sorted({a.event for s in alerted for a in risk[id(s)].alerts})
        rows.append(("Weather", Text(f"{len(alerted)} stop(s) under active NWS alerts: " + ", ".join(events),
                                     style="yellow")))
    else:
        rows.append(("Weather", Text("no active NWS alerts at any stop", style="green")))
    if args.priority:
        count, due = args.priority
        rows.append(("Priority", f"{count} declared-area stop(s) due by {format_clock(due)} "
                                 f"(within {args.fema_within:g} h)"))
    return rows


def print_flags(console: Console, args, located: List[Stop]) -> None:
    risk = getattr(args, "risk", None)
    if not risk:
        return
    flagged = [s for s in located[1:] if risk[id(s)].flagged]
    if not flagged:
        return
    from rich.table import Table
    t = Table(title="Disaster and weather flags", title_style="bold red", header_style="bold")
    for col in ("Stop", "County", "FEMA declaration", "NWS alerts"):
        t.add_column(col, overflow="fold")
    for s in flagged:
        r = risk[id(s)]
        decl = "; ".join(sorted({f"{d.label} ({', '.join(d.programs) or 'no programs listed'})"
                                 for d in r.declarations}))
        alerts = ", ".join(sorted({f"{a.event} ({a.severity})" for a in r.alerts}))
        t.add_row(s.name, county_name(r.county) if r.county else "", decl, alerts)
    console.print(t)
    console.print(Text.from_markup("[dim]FEMA: OpenFEMA disaster declarations. Weather: National Weather "
                                   "Service active alerts. Programs: IH = Individuals & Households, IA = Individual "
                                   "Assistance, PA = Public Assistance, HM = Hazard Mitigation.[/dim]"))


def fema_overlays(args):
    risk = getattr(args, "risk", None)
    if not risk:
        return []
    import html as _html
    overlays = []
    alerts = [a for a in getattr(args, "alerts", []) if a.geometry]
    if alerts:
        overlays.append(MapOverlay("NWS weather alerts", "#d62828", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": a.geometry, "properties": {
                "popup": f"<b>{_html.escape(a.event)}</b> ({_html.escape(a.severity)})<br>{_html.escape(a.headline)}"}}
            for a in alerts]}))
    declared = {}
    for r in risk.values():
        for d in r.declarations:
            declared.setdefault(d.fips, set()).add(d.label)
    pts = []
    for fips, labels in sorted(declared.items()):
        c = counties().get(fips)
        if c:
            pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c[3], c[2]]},
                        "properties": {"popup": f"<b>{_html.escape(c[0])}, {c[1]}</b><br>"
                                                + "<br>".join(_html.escape(l) for l in sorted(labels))}})
    if pts:
        overlays.append(MapOverlay("FEMA-declared counties (stops' counties)", "#7a1f1f",
                                   {"type": "FeatureCollection", "features": pts}))
    return overlays


def trucks(args, console: Console) -> int:
    import json as _json
    from rich.table import Table
    try:
        with console.status("Getting truck locations…"):
            provider = provider_from_env()
            fleet_now = provider.trucks()
    except TrackingError as e:
        if args.json:
            print(_json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        return 1
    if args.json:
        print(_json.dumps({"provider": provider.name, "trucks": [t.to_dict() for t in fleet_now]}, indent=2))
        return 0
    if not fleet_now:
        console.print("No trucks have reported a position yet.")
        return 0
    t = Table(title=f"Trucks ({provider.name})", title_style="bold", header_style="bold")
    for col, j in (("Truck", "left"), ("Status", "left"), ("Location", "left"), ("Speed", "right"),
                   ("Heading", "left"), ("Last fix", "right")):
        t.add_column(col, justify=j)
    for tr in fleet_now:
        age = tr.age_minutes
        stale = age is not None and age > 30
        t.add_row(Text(tr.name, style="bold"),
                  Text(tr.status, style="green" if tr.status == "online" else "dim"),
                  tr.address or f"{tr.lat:.5f}, {tr.lon:.5f}",
                  f"{tr.speed_mph:.0f} mph" if tr.moving else "stopped",
                  compass(tr.heading) if tr.moving else "",
                  Text(fmt_age(age), style="yellow" if stale else ""))
    console.print(t)
    if args.html:
        tracks = [MapTrack(tr.name, TRUCK_COLORS[i % len(TRUCK_COLORS)], [], [MapStop(
            tr.lat, tr.lon, str(i + 1), tr.name, [x for x in (
                tr.address, f"{tr.status} · " + (f"{tr.speed_mph:.0f} mph {compass(tr.heading)}" if tr.moving
                                                 else "stopped"),
                f"Last fix {fmt_age(tr.age_minutes)}") if x])]) for i, tr in enumerate(fleet_now)]
        first = fleet_now[0]
        write_html(args.html, "routekit · trucks", "\n".join(
            f"{tr.name:<16} {tr.status:<8} {fmt_age(tr.age_minutes)}" for tr in fleet_now),
            (first.lat, first.lon), first.name, tracks,
            footnote=f"Live positions from {provider.name}. Refresh by running 'routekit trucks --html' again.")
        console.print(f"[green]Saved map to {args.html}[/green]")
    return 0


def fmt_age(minutes: Optional[float]) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h ago"
    return f"{minutes / 1440:.0f} days ago"


def disasters(args, console: Console) -> int:
    from rich.table import Table
    try:
        with console.status("Getting FEMA disaster declarations…"):
            decls = open_declarations(args.state, days=args.days, number=args.number)
    except DisasterDataError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1
    if not decls:
        where = f" in {', '.join(s.upper() for s in args.state)}" if args.state else ""
        console.print(f"No open FEMA declarations{where} in the last {args.days} days." if args.number is None
                      else f"No FEMA disaster number {args.number}.")
        return 0
    groups = group_by_disaster(decls)
    t = Table(title="FEMA disaster declarations", title_style="bold", header_style="bold")
    for col, j in (("Disaster", "left"), ("Type", "left"), ("Incident", "left"), ("State", "left"),
                   ("Declared", "right"), ("Areas", "right"), ("Programs", "left")):
        t.add_column(col, justify=j)
    for code, ds in groups.items():
        d = ds[0]
        progs = sorted({p for x in ds for p in x.programs})
        t.add_row(Text(code, style="bold red"), KINDS.get(d.kind, d.kind), f"{d.incident}: {d.title.title()}",
                  ", ".join(sorted({x.state for x in ds})), f"{d.declared:%b} {d.declared.day}, {d.declared.year}",
                  str(len(ds)), ", ".join(progs))
    console.print(t)
    if args.number is not None or len(groups) == 1 or args.stops:
        for code, ds in groups.items():
            console.print(f"[bold]{code}[/bold] designated areas: " + ", ".join(sorted(x.area for x in ds)))
    if args.stops:
        depot_row = None
        if args.depot:
            from routekit.geocode import GeocodeError, geocode_one
            try:
                hit = geocode_one(args.depot)
            except GeocodeError as e:
                console.print(f"[red]Error:[/red] couldn't look up the staging area: {e}")
                return 1
            if hit is None:
                console.print(f"[red]Error:[/red] couldn't find the staging area {args.depot!r}. Include the house "
                              "number, street, city, state, and ZIP, or give 'lat, lon'.")
                return 1
            lat, lon, matched = hit
            console.print(f"[green]✓[/green] Staging area: [dim]{matched}[/dim]")
            depot_row = ["Staging area", args.depot, f"{lat:.6f}", f"{lon:.6f}", "depot"]
        rows, seen = [], set()
        for d in decls:
            targets = [d.fips] if not d.statewide else [f for f in counties() if f[:2] == d.fips[:2]]
            for f in targets:
                c = counties().get(f)
                if c and f not in seen:
                    seen.add(f)
                    rows.append((f"{c[0]}, {c[1]}", c[2], c[3], d.code))
        import csv as _csv
        with open(args.stops, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["name", "address", "lat", "lon", "note"])
            if depot_row:
                w.writerow(depot_row)
            for name, lat, lon, code in sorted(rows):
                w.writerow([name, "", f"{lat:.5f}", f"{lon:.5f}", code])
        console.print(f"[green]Saved {len(rows)} declared counties as relief stops to {args.stops}[/green]")
        console.print(Text.from_markup(
            "[dim]Each stop is the county's Census center point, a stand-in until you know the actual "
            "distribution sites. " + ("The staging area is the depot." if args.depot else
                                       "Add a staging area with --depot (it becomes the first row).") + "[/dim]"))
        console.print(f"[dim]Next: routekit plan {args.stops} --fema --truck --trucks 3 --html relief.html[/dim]")
    return 0


def main(argv: Optional[List[str]] = None, console: Optional[Console] = None) -> int:
    args = build_parser().parse_args(argv)
    console = console or Console()
    if args.command == "plan":
        return plan(args, console)
    if args.command == "disasters":
        return disasters(args, console)
    if args.command == "trucks":
        return trucks(args, console)
    if args.command == "mcp":
        from routekit.mcp_server import serve
        return serve()
    return 2


if __name__ == "__main__":
    sys.exit(main())
