"""Loading and saving stop lists (CSV)."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

NAME_COLS = ("name", "stop", "label", "id", "package id", "package_id", "customer")
ADDRESS_COLS = ("address", "full_address", "location")
STREET_COLS = ("street", "address1", "street_address")
CITY_COLS = ("city", "town")
STATE_COLS = ("state", "st")
ZIP_COLS = ("zip", "zipcode", "zip_code", "postal_code", "postcode")
LAT_COLS = ("lat", "latitude")
LON_COLS = ("lon", "lng", "long", "longitude")

# Optional fleet-planning columns
DEMAND_COLS = ("demand", "qty", "quantity", "units", "packages", "load")
EARLIEST_COLS = ("earliest", "window_start", "tw_start", "open", "after")
LATEST_COLS = ("latest", "deadline", "due", "window_end", "tw_end", "close", "before")
READY_COLS = ("ready", "available", "available_at", "ready_at")
SERVICE_COLS = ("service", "service_min", "service_minutes", "dwell")
TRUCK_COLS = ("truck", "vehicle", "truck_id")
GROUP_COLS = ("group", "together", "group_id")

_OPEN_TIMES = {"", "eod", "none", "any", "anytime", "-", "n/a", "na"}
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$", re.IGNORECASE)


@dataclass
class Stop:
    name: str
    address: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    demand: float = 1.0
    earliest: Optional[int] = None  # minutes after midnight
    latest: Optional[int] = None    # minutes after midnight (deadline)
    ready: Optional[int] = None     # when the shipment is available at the depot
    service: Optional[float] = None  # minutes at the stop (None = fleet default)
    truck: Optional[int] = None     # must ride on this truck (1-based)
    group: Optional[str] = None     # stops sharing a group ride the same trip

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def constrained(self) -> bool:
        return any(v is not None for v in (self.earliest, self.latest, self.ready, self.truck, self.group))


class StopFileError(ValueError):
    pass


def parse_clock(value: str) -> Optional[int]:
    """Parse '9:05', '09:05', '9:05 AM', '10:30 pm', '9am' into minutes after midnight.

    Blank, 'EOD', or 'none' mean no time limit and return None.
    """
    v = value.strip().lower()
    if v in _OPEN_TIMES:
        return None
    m = _CLOCK.match(v.replace(" ", "") if v.endswith(("am", "pm", "a.m.", "p.m.")) else v)
    if not m:
        raise ValueError(f"not a time: {value!r}")
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError(f"not a time: {value!r}")
        h = h % 12 + (12 if ampm.startswith("p") else 0)
    if h > 23 or mins > 59:
        raise ValueError(f"not a time: {value!r}")
    return h * 60 + mins


def format_clock(minutes: Optional[float]) -> str:
    if minutes is None:
        return ""
    m = int(round(minutes))
    h, mm = divmod(m, 60)
    suffix = "AM" if h % 24 < 12 else "PM"
    return f"{(h % 12) or 12}:{mm:02d} {suffix}"


def _pick(row: Dict[str, str], candidates) -> str:
    for c in candidates:
        v = row.get(c)
        if v is not None and v.strip():
            return v.strip()
    return ""


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_stops(path: Path) -> List[Stop]:
    """Read stops from a CSV.

    Accepts either a single address column (``address``) or split columns
    (``street``, ``city``, ``state``, ``zip``). Rows that already have
    ``lat``/``lon`` columns skip geocoding. Header names are case-insensitive.

    Optional fleet columns: ``demand``, ``earliest``, ``deadline``/``latest``,
    ``ready``, ``service`` (minutes), ``truck`` (pin to truck N), ``group``.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise StopFileError(f"{path} has no header row.")
        headers = {h.strip().lower() for h in reader.fieldnames if h}
        has_address = headers & set(ADDRESS_COLS + STREET_COLS)
        has_coords = (headers & set(LAT_COLS)) and (headers & set(LON_COLS))
        if not has_address and not has_coords:
            raise StopFileError(
                f"{path} needs an 'address' column (or street/city/state/zip, or lat/lon). "
                f"Found columns: {', '.join(sorted(headers))}"
            )

        stops: List[Stop] = []
        for i, raw in enumerate(reader, start=1):
            row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
            line = i + 1  # header is line 1
            city, state_zip = _pick(row, CITY_COLS), " ".join(
                p for p in (_pick(row, STATE_COLS), _pick(row, ZIP_COLS)) if p)
            address = _pick(row, ADDRESS_COLS)
            if address and city and city.lower() not in address.lower():
                address = ", ".join(p for p in (address, city, state_zip) if p)
            if not address:
                address = ", ".join(p for p in (_pick(row, STREET_COLS), city, state_zip) if p)
            lat, lon = _to_float(_pick(row, LAT_COLS)), _to_float(_pick(row, LON_COLS))
            if not address and lat is None:
                continue  # blank line
            stop = Stop(name=_pick(row, NAME_COLS) or f"Stop {i}", address=address, lat=lat, lon=lon)
            try:
                _read_fleet_columns(row, stop)
            except ValueError as e:
                raise StopFileError(f"{path}, line {line}: {e}") from None
            stops.append(stop)

    if len(stops) < 2:
        raise StopFileError(f"{path} needs at least 2 stops (found {len(stops)}).")
    return stops


def _read_fleet_columns(row: Dict[str, str], stop: Stop) -> None:
    demand = _pick(row, DEMAND_COLS)
    if demand:
        d = _to_float(demand)
        if d is None or d < 0:
            raise ValueError(f"demand must be a non-negative number, got {demand!r}")
        stop.demand = d
    stop.earliest = parse_clock(_pick(row, EARLIEST_COLS))
    stop.latest = parse_clock(_pick(row, LATEST_COLS))
    stop.ready = parse_clock(_pick(row, READY_COLS))
    if stop.earliest is not None and stop.latest is not None and stop.earliest > stop.latest:
        raise ValueError("earliest time is after the deadline")
    service = _pick(row, SERVICE_COLS)
    if service:
        s = _to_float(service)
        if s is None or s < 0:
            raise ValueError(f"service minutes must be a non-negative number, got {service!r}")
        stop.service = s
    truck = _pick(row, TRUCK_COLS)
    if truck:
        t = _to_float(truck)
        if t is None or t < 1 or t != int(t):
            raise ValueError(f"truck must be a whole number starting at 1, got {truck!r}")
        stop.truck = int(t)
    stop.group = _pick(row, GROUP_COLS) or None


def save_route(path: Path, ordered: List[Stop], legs: List[float]) -> None:
    """Write stops in route order with per-leg and cumulative miles."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order", "name", "address", "lat", "lon", "leg_miles", "cumulative_miles"])
        total = 0.0
        for i, stop in enumerate(ordered):
            leg = legs[i - 1] if i > 0 else 0.0
            total += leg
            w.writerow([i, stop.name, stop.address, f"{stop.lat:.6f}", f"{stop.lon:.6f}",
                        f"{leg:.2f}", f"{total:.2f}"])
