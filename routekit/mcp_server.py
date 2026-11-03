"""A Model Context Protocol (MCP) server, so AI agents can use routekit as tools.

Run with ``routekit mcp``. It speaks JSON-RPC 2.0 over stdin/stdout (the MCP
stdio transport), with one JSON message per line. It's implemented directly,
with no SDK, so routekit keeps working on Python 3.9. Register it with an agent,
for example:

    claude mcp add routekit -- routekit mcp

Tools: plan_route, truck_locations, fema_disasters, geocode_address.
Nothing but protocol messages is ever written to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import json
import sys
import traceback
import warnings
from typing import Any, Callable, Dict, Optional

warnings.filterwarnings("ignore")

from routekit import __version__  # noqa: E402
from routekit import api  # noqa: E402

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "routekit plans delivery routes for U.S. addresses: one truck or a fleet (capacity, delivery windows, "
    "multiple trips, driver limits), on real roads or truck-legal roads with toll reporting, and can flag "
    "stops in FEMA disaster areas or under weather alerts. Typical dispatcher flow: truck_locations to see "
    "where trucks are → geocode_address to check any doubtful address → plan_route (start_at_truck to begin "
    "from a truck's live position) → report each truck's stop order and arrival times, and mention anything "
    "undelivered or skipped. Distances are straight-line unless roads/truck/avoid_tolls is set; say which.")

_STOP_HELP = ("One stop per string; the FIRST is the depot (start/end) unless depot or start_at_truck is given. "
              "Each string is an address ('233 S Wacker Dr, Chicago, IL'), optionally labeled ('Willis Tower: "
              "233 S Wacker Dr, Chicago, IL'), with an optional deadline ('… @ 10:30 AM') or window "
              "('… @ 9:00-11:00'), or coordinates ('41.88, -87.63').")

TOOLS = [
    {
        "name": "plan_route",
        "title": "Plan delivery routes",
        "description": "Optimize the order of stops for one truck or a fleet, and return the plan (stop order, "
                       "arrival times, miles) plus a text report. Give either 'stops' or 'csv_path'. Fleet "
                       "options (trucks, capacity, trips, drivers, start, deadlines) switch on fleet planning.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stops": {"type": "array", "items": {"type": "string"}, "description": _STOP_HELP},
                "csv_path": {"type": "string", "description": "Path to a CSV of stops (address or lat/lon "
                             "columns; optional demand, earliest, deadline, ready, truck, group, service)."},
                "depot": {"type": "string", "description": "Start/end address, if not the first stop."},
                "start_at_truck": {"type": "string", "description": "Name of a tracked truck: start from its live "
                                   "location (one-way). Requires Traccar to be configured."},
                "one_way": {"type": "boolean", "description": "Single truck: don't return to the depot."},
                "trucks": {"type": "integer", "minimum": 1},
                "capacity": {"type": "number", "description": "Max load per trip (in 'demand' units; 1 per stop by default)."},
                "trips": {"type": "integer", "minimum": 1, "description": "Max trips per truck."},
                "drivers": {"type": "integer", "minimum": 1, "description": "At most this many trucks out at once."},
                "reload_minutes": {"type": "number"},
                "start": {"type": "string", "description": "Departure time, e.g. '8:00' or '6:30 AM'."},
                "end": {"type": "string", "description": "Trucks must be back by this time."},
                "service_minutes": {"type": "number", "description": "Minutes spent at each stop."},
                "mph": {"type": "number", "description": "Average speed when not using roads (default 45)."},
                "roads": {"type": "boolean", "description": "Real road miles and drive times (car)."},
                "truck": {"type": "boolean", "description": "Truck-legal roads, with toll miles per leg."},
                "height_ft": {"type": "number"}, "weight_tons": {"type": "number"},
                "length_ft": {"type": "number"}, "width_ft": {"type": "number"},
                "hazmat": {"type": "boolean"},
                "avoid_tolls": {"type": "boolean", "description": "Stay off toll roads; reports what it costs."},
                "fema": {"type": "boolean", "description": "Flag stops in FEMA-declared disaster areas or under NWS alerts."},
                "fema_within_hours": {"type": "number", "description": "Declared-area stops must be reached within this many hours."},
                "fema_days": {"type": "integer", "description": "Declarations from the last N days (default 120)."},
                "html_path": {"type": "string", "description": "Also save an interactive browser map here."},
                "save_stops_path": {"type": "string", "description": "Save typed stops as a reusable CSV."},
                "offline": {"type": "boolean", "description": "No network: CSV coordinates or cache only."},
                "no_optimize": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "annotations": {"title": "Plan delivery routes", "readOnlyHint": False, "openWorldHint": True},
        "fn": lambda a: api.plan_route(**a),
    },
    {
        "name": "truck_locations",
        "title": "Where are my trucks?",
        "description": "Latest GPS position, speed, heading, and fix age for every tracked truck (Traccar). "
                       "Positions older than ~30 minutes may be stale; say so.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Where are my trucks?", "readOnlyHint": True, "openWorldHint": True},
        "fn": lambda a: api.truck_locations(),
    },
    {
        "name": "fema_disasters",
        "title": "FEMA disaster declarations",
        "description": "Open FEMA disaster declarations (OpenFEMA): by state(s), recent days, or one disaster "
                       "number. With stops_csv_path, saves the declared counties as relief stops (county "
                       "center points) ready for plan_route; give depot (a staging address) to put it first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "array", "items": {"type": "string"}, "description": "Postal codes, e.g. ['FL']."},
                "number": {"type": "integer", "description": "A FEMA disaster number, e.g. 4955."},
                "days": {"type": "integer", "default": 120},
                "stops_csv_path": {"type": "string"},
                "depot": {"type": "string", "description": "Staging-area address (with stops_csv_path)."},
            },
            "additionalProperties": False,
        },
        "annotations": {"title": "FEMA disaster declarations", "readOnlyHint": False, "openWorldHint": True},
        "fn": lambda a: api.disasters(**a),
    },
    {
        "name": "geocode_address",
        "title": "Check an address",
        "description": "Look up a U.S. street address (Census geocoder). Returns the matched address and "
                       "coordinates. Compare the matched address with the input: the geocoder can match a "
                       "nearby variant (e.g. N vs. S of a street).",
        "inputSchema": {"type": "object", "properties": {"address": {"type": "string"}},
                        "required": ["address"], "additionalProperties": False},
        "annotations": {"title": "Check an address", "readOnlyHint": True, "openWorldHint": True},
        "fn": lambda a: api.geocode(**a),
    },
]
_BY_NAME: Dict[str, Dict[str, Any]] = {t["name"]: t for t in TOOLS}


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code: int, message: str):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _call_tool(params: dict) -> dict:
    name = params.get("name")
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    args = params.get("arguments") or {}
    try:
        data = tool["fn"](args)
        failed = isinstance(data, dict) and data.get("ok") is False
    except (TypeError, ValueError) as e:  # bad arguments
        data, failed = {"ok": False, "error": str(e)}, True
    except Exception as e:  # never crash the server
        traceback.print_exc(file=sys.stderr)
        data, failed = {"ok": False, "error": f"{type(e).__name__}: {e}"}, True
    text = data.get("output") if isinstance(data, dict) and data.get("output") else json.dumps(data, indent=2)
    if isinstance(data, dict) and data.get("output") and data.get("plan"):
        text += "\n\n" + json.dumps({"plan": data["plan"], "undelivered": data.get("undelivered", [])}, indent=1)
    return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": failed}


def handle(msg: dict) -> Optional[dict]:
    """Handle one JSON-RPC message; returns the response, or None for notifications."""
    id_ = msg.get("id")
    method = msg.get("method")
    if id_ is None:  # notification (e.g. notifications/initialized): no reply
        return None
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(id_, {
            "protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "routekit", "title": "routekit", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]})
    if method == "tools/call":
        try:
            return _result(id_, _call_tool(params))
        except KeyError:
            return _error(id_, -32602, f"unknown tool: {params.get('name')}")
    return _error(id_, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    real_stdout = stdout
    sys.stdout = sys.stderr  # anything that print()s by accident goes to stderr, not the protocol stream
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                reply = _error(None, -32700, "parse error")
            else:
                if isinstance(msg, list):  # batches (older protocol versions)
                    replies = [r for r in (handle(m) for m in msg if isinstance(m, dict)) if r]
                    reply = replies or None
                elif isinstance(msg, dict):
                    reply = handle(msg)
                else:
                    reply = _error(None, -32600, "invalid request")
            if reply is not None:
                real_stdout.write(json.dumps(reply) + "\n")
                real_stdout.flush()
    finally:
        sys.stdout = real_stdout
    return 0
