"""Terminal output: summary panel, map panel, and Rich tables."""

from __future__ import annotations

from typing import List, Optional

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from routekit.mapview import DEPOT_STYLE, MapScene
from routekit.stops import Stop


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


def failures_table(failed: List[Stop]) -> Optional[Table]:
    if not failed:
        return None
    t = Table(title="Could not locate", title_style="bold red", header_style="bold")
    t.add_column("Stop")
    t.add_column("Address")
    for s in failed:
        t.add_row(s.name, s.address)
    return t
