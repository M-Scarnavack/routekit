"""Typing stops instead of loading a CSV.

Each stop is one line of text:

    1100 W Randolph St, Chicago, IL 60607
    Willis Tower: 233 S Wacker Dr, Chicago, IL @ 10:30 AM
    Art Institute: 111 S Michigan Ave, Chicago, IL @ 9:00-11:00
    41.8789, -87.6359

- ``Label: address`` names the stop (otherwise the street part is used).
- ``@ TIME`` sets a deadline; ``@ TIME-TIME`` sets a delivery window.
- ``lat, lon`` skips geocoding.

The first stop is the depot unless ``--depot`` is given.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from routekit.geocode import GeocodeError, geocode_one
from routekit.stops import Stop, format_clock, parse_clock

_COORDS = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_WHEN = re.compile(r"\s@\s*(.+?)\s*$|^@\s*(.+?)\s*$")
_RANGE = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)
_LABEL = re.compile(r"^\s*([^:]{1,60}?)\s*:\s+(.+)$")


class StopTextError(ValueError):
    pass


def parse_stop_text(text: str, index: int = 0) -> Optional[Stop]:
    """Turn one typed line into a Stop. Returns None for blank lines and # comments."""
    line = text.strip()
    if not line or line.startswith("#"):
        return None

    earliest = latest = None
    m = _WHEN.search(line)
    if m:
        when = (m.group(1) or m.group(2)).strip()
        line = line[:m.start()].strip()
        parts = [p for p in _RANGE.split(when) if p]
        try:
            if len(parts) == 2:
                earliest, latest = parse_clock(parts[0]), parse_clock(parts[1])
            elif len(parts) == 1:
                latest = parse_clock(parts[0])
            else:
                raise ValueError(when)
        except ValueError:
            raise StopTextError(f"couldn't read the time {when!r}; try '@ 10:30 AM' or '@ 9:00-11:00'") from None
        if earliest is not None and latest is not None and earliest > latest:
            raise StopTextError(f"the window {when!r} ends before it starts")

    label = None
    if line.endswith(":"):
        raise StopTextError("there's a label but no address")
    lm = _LABEL.match(line)
    if lm and not _COORDS.match(line):
        label, line = lm.group(1).strip(), lm.group(2).strip()
    if not line:
        raise StopTextError("there's a label but no address")

    lat = lon = None
    cm = _COORDS.match(line)
    if cm:
        lat, lon = float(cm.group(1)), float(cm.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise StopTextError(f"{line!r} isn't a valid latitude, longitude")
        address = ""
    else:
        address = line

    name = label or (address.split(",")[0].strip() if address else f"{lat:.4f}, {lon:.4f}")
    if index == 0 and not label:
        name = f"Depot ({name})"
    return Stop(name=name, address=address, lat=lat, lon=lon, earliest=earliest, latest=latest)


def parse_lines(lines: Iterable[str], unit: str = "line") -> List[Stop]:
    stops: List[Stop] = []
    for n, raw in enumerate(lines, start=1):
        try:
            s = parse_stop_text(raw, len(stops))
        except StopTextError as e:
            raise StopTextError(f"{unit} {n}: {e}") from None
        if s:
            stops.append(s)
    return stops


HELP = (
    "Type one address per line, then press [bold]Enter on an empty line[/bold] when you're done.\n"
    "The first address is the [magenta]depot[/magenta], where trucks start and end.\n\n"
    "[dim]Optional:[/dim]  [cyan]Name: address[/cyan]   ·   [cyan]address @ 10:30 AM[/cyan] (deadline)   ·   "
    "[cyan]address @ 9:00-11:00[/cyan] (window)   ·   [cyan]41.88, -87.63[/cyan]\n"
    "[dim]Commands:[/dim]  [cyan]undo[/cyan] removes the last stop  ·  [cyan]list[/cyan] shows them  ·  "
    "Ctrl+C quits"
)


def collect_interactive(console: Console, offline: bool = False) -> List[Stop]:
    """Prompt for stops one at a time, confirming each address with the geocoder as it's typed."""
    try:
        import readline  # noqa: F401  (arrow keys and history in input())
    except ImportError:
        pass
    console.print(Panel(Text.from_markup(HELP), title="[bold]routekit · enter your stops[/bold]",
                        border_style="cyan", expand=False))
    stops: List[Stop] = []
    while True:
        prompt = "Depot" if not stops else f"Stop {len(stops)}"
        try:
            text = console.input(f"[bold cyan]{prompt}[/bold cyan] › ")
        except EOFError:
            console.print()
            break
        cmd = text.strip().lower()
        if cmd in ("", "done", "go"):
            if len(stops) >= 2:
                break
            console.print("[yellow]Add at least a depot and one stop first.[/yellow]")
            continue
        if cmd == "undo":
            if stops:
                gone = stops.pop()
                console.print(f"[dim]Removed {gone.name}.[/dim]")
            continue
        if cmd == "list":
            for i, s in enumerate(stops):
                console.print(f"  {i:>2}  {s.name}  [dim]{s.address or ''}[/dim]")
            continue
        try:
            stop = parse_stop_text(text, len(stops))
        except StopTextError as e:
            console.print(f"[red]✗[/red] {e}")
            continue
        if stop is None:
            continue
        if not stop.located:
            try:
                with console.status("Looking it up…"):
                    hit = geocode_one(stop.address, offline=offline)
            except GeocodeError as e:
                console.print(f"[red]✗ Couldn't reach the geocoder ({e}).[/red] "
                              "Check your connection, or type the stop as [cyan]lat, lon[/cyan].")
                continue
            if hit is None:
                console.print("[red]✗ Couldn't find that address.[/red] Include the house number, street, city, "
                              "and state, e.g. [cyan]233 S Wacker Dr, Chicago, IL[/cyan]. Landmark names won't "
                              "match. Or type [cyan]lat, lon[/cyan].")
                continue
            stop.lat, stop.lon, matched = hit
            found = f"[green]✓[/green] [dim]{matched}[/dim]"
        else:
            found = f"[green]✓[/green] [dim]{stop.lat:.5f}, {stop.lon:.5f}[/dim]"
        console.print(f"  {found}{window_note(stop)}")
        stops.append(stop)
    return stops


def window_note(stop: Stop) -> str:
    if stop.earliest is not None and stop.latest is not None:
        return f"  [yellow]{format_clock(stop.earliest)}–{format_clock(stop.latest)}[/yellow]"
    if stop.latest is not None:
        return f"  [yellow]by {format_clock(stop.latest)}[/yellow]"
    if stop.earliest is not None:
        return f"  [yellow]after {format_clock(stop.earliest)}[/yellow]"
    return ""


def offer_save(console: Console, stops: List[Stop]) -> None:
    """After an interactive session, offer to keep the stops as a CSV for next time."""
    try:
        name = console.input("[dim]Save these stops as a CSV for next time? File name, or Enter to skip:[/dim] ").strip()
    except EOFError:
        return
    if not name:
        return
    path = Path(name if name.lower().endswith(".csv") else name + ".csv")
    save_stops(path, stops)
    console.print(f"[green]Saved {len(stops)} stops to {path}[/green]  "
                  f"[dim](rerun with: routekit plan {path})[/dim]")


def save_stops(path: Path, stops: List[Stop]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "address", "lat", "lon", "earliest", "deadline"])
        for s in stops:
            w.writerow([s.name, s.address,
                        f"{s.lat:.6f}" if s.lat is not None else "", f"{s.lon:.6f}" if s.lon is not None else "",
                        format_clock(s.earliest), format_clock(s.latest)])
