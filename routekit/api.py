"""Structured, programmatic access to routekit, used by the MCP server.

Each function returns plain JSON-serializable data. Planning runs the same code
paths as the ``routekit`` command (with output captured), so agents and the CLI
always agree.
"""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from routekit import cli
from routekit.fema import KINDS, DisasterDataError, group_by_disaster, open_declarations
from routekit.geocode import GeocodeError, geocode_one
from routekit.tracking import TrackingError, provider_from_env

# plan_route option -> CLI flag, for options that take a value
_VALUE_FLAGS = {
    "depot": "--depot", "trucks": "--trucks", "capacity": "--capacity", "trips": "--trips",
    "drivers": "--drivers", "reload_minutes": "--reload", "start": "--start", "end": "--end",
    "service_minutes": "--service", "mph": "--mph", "height_ft": "--height", "weight_tons": "--weight",
    "length_ft": "--length", "width_ft": "--width", "fema_within_hours": "--fema-within",
    "fema_days": "--fema-days", "start_at_truck": "--start-at-truck", "html_path": "--html",
    "save_stops_path": "--save-stops", "osrm_url": "--osrm-url", "valhalla_url": "--valhalla-url",
}
# plan_route option -> CLI switch, for true/false options
_SWITCHES = {"roads": "--roads", "truck": "--truck", "hazmat": "--hazmat", "avoid_tolls": "--avoid-tolls",
             "fema": "--fema", "one_way": "--one-way", "no_optimize": "--no-optimize", "offline": "--offline"}


def _run_cli(argv: List[str]) -> Dict[str, Any]:
    buf = io.StringIO()
    console = Console(file=buf, width=110, color_system=None, force_terminal=False, record=False)
    try:
        code = cli.main(argv, console=console)
    except SystemExit as e:  # argparse errors
        code = int(e.code or 2)
    return {"exit_code": code, "output": buf.getvalue().rstrip()}


def plan_route(stops: Optional[List[str]] = None, csv_path: Optional[str] = None, **options) -> Dict[str, Any]:
    """Plan routes through `stops` (typed lines; first is the depot) or a CSV file.

    Returns the full text report (what the CLI prints), the plan rows (one per stop, in order,
    with truck/trip/arrival when it's a fleet plan), and any HTML map path.
    """
    if bool(stops) == bool(csv_path):
        raise ValueError("give either 'stops' (a list of addresses) or 'csv_path', not both")
    unknown = set(options) - set(_VALUE_FLAGS) - set(_SWITCHES)
    if unknown:
        raise ValueError(f"unknown option(s): {', '.join(sorted(unknown))}")
    with tempfile.TemporaryDirectory() as tmp:
        out_csv = Path(tmp) / "plan.csv"
        argv = ["plan"] + (list(stops) if stops else [str(csv_path)]) + ["--no-map", "-o", str(out_csv)]
        for key, flag in _VALUE_FLAGS.items():
            if options.get(key) is not None:
                argv += [flag, str(options[key])]
        for key, flag in _SWITCHES.items():
            if options.get(key):
                argv.append(flag)
        result = _run_cli(argv)
        rows = list(csv.DictReader(out_csv.open())) if out_csv.exists() else []
        # The plan is returned as data, so drop the CLI's note about the temporary CSV.
        result["output"] = "\n".join(line for line in result["output"].splitlines()
                                     if not (line.startswith(("Saved plan to", "Saved route to")) or tmp in line))
    result["plan"] = rows
    result["undelivered"] = [r["name"] for r in rows if r.get("arrives") == "NOT DELIVERED"]
    result["ok"] = result["exit_code"] == 0
    if options.get("html_path") and result["ok"]:
        result["html_path"] = str(Path(options["html_path"]).resolve())
    return result


def truck_locations() -> Dict[str, Any]:
    try:
        provider = provider_from_env()
        trucks = provider.trucks()
    except TrackingError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "provider": provider.name, "trucks": [t.to_dict() for t in trucks]}


def disasters(state: Optional[List[str]] = None, number: Optional[int] = None, days: int = 120,
              stops_csv_path: Optional[str] = None, depot: Optional[str] = None) -> Dict[str, Any]:
    if stops_csv_path:
        argv = ["disasters"] + ([str(number)] if number is not None else [])
        for st in state or []:
            argv += ["--state", st]
        argv += ["--days", str(days), "--stops", str(stops_csv_path)] + (["--depot", depot] if depot else [])
        result = _run_cli(argv)
        result["ok"] = result["exit_code"] == 0
        if result["ok"]:
            result["stops_csv_path"] = str(Path(stops_csv_path).resolve())
        return result
    try:
        decls = open_declarations(state, days=days, number=number)
    except DisasterDataError as e:
        return {"ok": False, "error": str(e)}
    out = []
    for code, ds in group_by_disaster(decls).items():
        d = ds[0]
        out.append({"disaster": code, "type": KINDS.get(d.kind, d.kind), "incident": d.incident,
                    "title": d.title.title(), "declared": d.declared.isoformat(),
                    "states": sorted({x.state for x in ds}), "programs": sorted({p for x in ds for p in x.programs}),
                    "areas": sorted(x.area for x in ds), "county_fips": sorted(x.fips for x in ds)})
    return {"ok": True, "declarations": out}


def geocode(address: str) -> Dict[str, Any]:
    try:
        hit = geocode_one(address)
    except GeocodeError as e:
        return {"ok": False, "error": str(e)}
    if hit is None:
        return {"ok": False, "error": "no match; include house number, street, city, state (and ZIP). "
                                      "Landmark names don't match."}
    lat, lon, matched = hit
    return {"ok": True, "lat": lat, "lon": lon, "matched_address": matched}
