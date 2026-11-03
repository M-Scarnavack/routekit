"""Terminal output: summary panel, map panel, and Rich tables."""

from __future__ import annotations

from typing import List, Optional

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from routekit.mapview import DEPOT_STYLE, MapScene, truck_style
from routekit.stops import Stop, format_clock
from routekit.vrp import Fleet, FleetSolution


def stops_table(ordered: List[Stop], legs: List[float], round_trip: bool, mph: float) -> Table:
    table = Table(title="Route", title_style="bold", header_style="bold", expand=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Stop", style="bold")
    table.add_column("Address", overflow="fold")
    table.add_column("Leg mi", justify="right")
    table.add_column("Total mi", justify="right")
    table.add_column("Drive", justify="right", style="dim")
    total = 0.0
    for i, s in enumerate(ordered):
        leg = legs[i - 1] if i > 0 else 0.0
        total += leg
        is_return = round_trip and i == len(ordered) - 1
        label = "■ " + s.name if i == 0 else ("↩ " + s.name if is_return else s.name)
        style = DEPOT_STYLE if (i == 0 or is_return) else None
        table.add_row(
            str(i) if not is_return else "",
            Text(label, style=style or ""),
            s.address or f"{s.lat:.4f}, {s.lon:.4f}",
            f"{leg:,.1f}" if i else "",
            f"{total:,.1f}" if i else "",
            _fmt_hours(total / mph) if i else "",
        )
    return table


def _fmt_hours(h: float) -> str:
    mins = int(round(h * 60))
    return f"{mins // 60}h {mins % 60:02d}m"


def summary_panel(n_stops: int, nn_miles: float, final_miles: float, optimized: bool,
                  round_trip: bool, mph: float, skipped: int) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("Stops", f"{n_stops} (+ depot)")
    t.add_row("Trip", "round trip back to depot" if round_trip else "one way")
    t.add_row("Nearest neighbor", f"{nn_miles:,.1f} mi")
    if optimized:
        saved = nn_miles - final_miles
        pct = (saved / nn_miles * 100) if nn_miles else 0.0
        t.add_row("After 2-opt", Text.assemble((f"{final_miles:,.1f} mi", "bold green"),
                                               (f"  (−{saved:,.1f} mi, −{pct:.1f}%)", "green")))
    t.add_row("Est. drive time", f"{_fmt_hours(final_miles / mph)} at {mph:g} mph")
    if skipped:
        t.add_row("Skipped", Text(f"{skipped} address(es) could not be located", style="red"))
    return Panel(t, title="[bold]routekit[/bold]", subtitle="straight-line miles", border_style="cyan",
                 expand=False)


def map_panel(scene: MapScene, revealed: Optional[int] = None) -> Panel:
    return Panel(Group(scene.frame(revealed), Text(""), scene.legend()),
                 title="Route map", border_style="cyan", expand=False)


def failures_table(failed: List[Stop]) -> Optional[Group]:
    if not failed:
        return None
    t = Table(title="Could not locate", title_style="bold red", header_style="bold")
    t.add_column("Stop")
    t.add_column("Address")
    for s in failed:
        t.add_row(s.name, s.address)
    tip = Text.from_markup(
        "[dim]These stops were skipped. The U.S. Census geocoder needs a house number and street "
        "(landmark names and PO boxes won't match). Fix the address, or add [bold]lat[/bold] and "
        "[bold]lon[/bold] columns for those rows.[/dim]")
    return Group(t, tip)


# -- fleet (multi-truck) output ---------------------------------------------

def _window(s: Stop) -> str:
    if s.earliest is not None and s.latest is not None:
        return f"{format_clock(s.earliest)}–{format_clock(s.latest)}"
    if s.latest is not None:
        return f"by {format_clock(s.latest)}"
    if s.earliest is not None:
        return f"after {format_clock(s.earliest)}"
    return ""


def fleet_summary_panel(sol: FleetSolution, fleet: Fleet, stops: List[Stop], optimized: bool,
                        skipped: int, balanced: bool) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    n = len(stops) - 1
    delivered = n - len(sol.unassigned)
    t.add_row("Stops", f"{n} (+ depot)")
    cap = f", up to {fleet.capacity:g} each" if fleet.capacity is not None else ""
    if balanced:
        cap += " (split evenly)"
    t.add_row("Trucks", f"{sol.trucks_used} of {fleet.trucks} used{cap}")
    t.add_row("Initial plan", f"{sol.initial_miles:,.1f} mi  [dim](cheapest feasible insertion)[/dim]")
    if optimized:
        saved = sol.initial_miles - sol.miles
        pct = (saved / sol.initial_miles * 100) if sol.initial_miles else 0.0
        t.add_row("After local search", Text.assemble((f"{sol.miles:,.1f} mi", "bold green"),
                                                      (f"  (−{saved:,.1f} mi, −{pct:.1f}%)", "green")))
    used = [tr for tr in sol.trips if tr.stops]
    if used:
        t.add_row("Last truck back", format_clock(max(tr.finish for tr in used)))
    missing = set(sol.unassigned)
    windows = sum(1 for i, s in enumerate(stops) if i and i not in missing
                  and (s.latest is not None or s.earliest is not None))
    if windows:
        t.add_row("Time windows", Text(f"all {windows} met", style="green"))
    if sol.unassigned:
        t.add_row("Not delivered", Text(f"{len(sol.unassigned)} stop(s) fit no truck under the rules", style="bold red"))
    else:
        t.add_row("Delivered", Text(f"all {delivered} stops", style="green"))
    if skipped:
        t.add_row("Skipped", Text(f"{skipped} address(es) could not be located", style="red"))
    return Panel(t, title="[bold]routekit · fleet[/bold]", subtitle="straight-line miles",
                 border_style="cyan", expand=False)


def trucks_table(sol: FleetSolution, fleet: Fleet) -> Table:
    t = Table(title="Trucks", title_style="bold", header_style="bold")
    for col, j in (("Truck", "left"), ("Stops", "right"), ("Load", "right"), ("Leaves", "right"),
                   ("Back", "right"), ("Miles", "right")):
        t.add_column(col, justify=j)
    for tr in sol.trips:
        if not tr.stops:
            continue
        load = f"{tr.load:g}" + (f"/{fleet.capacity:g}" if fleet.capacity is not None else "")
        t.add_row(Text(f"■ Truck {tr.truck}", style=truck_style(tr.truck - 1)), str(len(tr.stops)), load,
                  format_clock(tr.depart), format_clock(tr.finish), f"{tr.miles:,.1f}")
    return t


def fleet_stops_table(sol: FleetSolution, stops: List[Stop], d) -> Table:
    t = Table(title="Stops", title_style="bold", header_style="bold")
    for col, j in (("Truck", "left"), ("#", "right"), ("Stop", "left"), ("Arrives", "right"),
                   ("Window", "left"), ("Leg mi", "right")):
        t.add_column(col, justify=j)
    for tr in sol.trips:
        if not tr.stops:
            continue
        style = truck_style(tr.truck - 1)
        prev = 0
        for k, (n, arr) in enumerate(zip(tr.stops, tr.arrivals), start=1):
            s = stops[n]
            t.add_row(Text(f"Truck {tr.truck}", style=style), str(k), s.name, format_clock(arr),
                      _window(s), f"{d[prev][n]:,.1f}")
            prev = n
    return t


def unassigned_table(sol: FleetSolution, stops: List[Stop]) -> Optional[Group]:
    if not sol.unassigned:
        return None
    t = Table(title="Not delivered", title_style="bold red", header_style="bold")
    for col in ("Stop", "Window", "Ready", "Truck", "Group"):
        t.add_column(col)
    for i in sol.unassigned:
        s = stops[i]
        t.add_row(s.name, _window(s), format_clock(s.ready), str(s.truck or ""), s.group or "")
    tip = Text.from_markup("[dim]No truck could take these without breaking a rule: capacity, a deadline, "
                           "a ready time, a truck pin, or a group. Try more trucks (--trucks), more capacity "
                           "(--capacity), an earlier --start, or a faster --mph.[/dim]")
    return Group(t, tip)
