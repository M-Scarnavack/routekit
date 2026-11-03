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

# Pickup-and-delivery: one row per stop (shipment + type), or one row per shipment (pickup + delivery)
SHIPMENT_COLS = ("shipment", "shipment_id", "load_id", "order_id")  # not "load" (= demand) or "order" (stop #)
TYPE_COLS = ("type", "action", "stop_type", "kind")
PICKUP_COLS = ("pickup", "pickup_address", "origin", "from")
DELIVERY_COLS = ("delivery", "delivery_address", "dropoff", "destination", "to")
PICKUP_LAT, PICKUP_LON = ("pickup_lat", "origin_lat"), ("pickup_lon", "pickup_lng", "origin_lon", "origin_lng")
DELIVERY_LAT, DELIVERY_LON = ("delivery_lat", "destination_lat"), ("delivery_lon", "delivery_lng",
                                                                     "destination_lon", "destination_lng")
PICKUP_EARLIEST, PICKUP_LATEST = ("pickup_after", "pickup_earliest", "pickup_ready"), ("pickup_by", "pickup_latest")
DELIVERY_EARLIEST = ("deliver_after", "delivery_after", "delivery_earliest")
DELIVERY_LATEST = ("deliver_by", "delivery_by", "delivery_latest", "deadline", "latest", "due")
PICKUP_WORDS = {"pickup", "pick up", "pick-up", "p", "pu", "load", "origin"}
DELIVERY_WORDS = {"delivery", "deliver", "drop", "dropoff", "drop-off", "d", "del", "unload", "destination"}

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
    shipment: Optional[str] = None  # pickup-and-delivery: both halves share this id
    action: Optional[str] = None    # "pickup" or "delivery" (None = an ordinary drop from the depot)

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def constrained(self) -> bool:
        return any(v is not None for v in (self.earliest, self.latest, self.ready, self.truck, self.group,
                                           self.shipment))


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
        per_shipment = bool(headers & set(PICKUP_COLS + PICKUP_LAT)) and bool(headers & set(DELIVERY_COLS + DELIVERY_LAT))
        if not has_address and not has_coords and not per_shipment:
            raise StopFileError(
                f"{path} needs an 'address' column (or street/city/state/zip, or lat/lon, or pickup and "
                f"delivery columns for shipments). Found columns: {', '.join(sorted(headers))}"
            )

        stops: List[Stop] = []
        for i, raw in enumerate(reader, start=1):
            row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
            line = i + 1  # header is line 1
            if per_shipment and (_pick(row, PICKUP_COLS + PICKUP_LAT) or _pick(row, DELIVERY_COLS + DELIVERY_LAT)):
                try:
                    stops.extend(_shipment_row(row, i))
                except ValueError as e:
                    raise StopFileError(f"{path}, line {line}: {e}") from None
                continue
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

    try:
        check_shipments(stops)
    except ValueError as e:
        raise StopFileError(f"{path}: {e}") from None
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
    shipment = _pick(row, SHIPMENT_COLS)
    kind = _pick(row, TYPE_COLS).lower()
    if shipment:  # a 'type' column alone (e.g. "residential") means nothing to routekit
        if not kind:
            raise ValueError(f"shipment {shipment!r}: add a 'type' column saying pickup or delivery")
        if kind in PICKUP_WORDS:
            stop.action = "pickup"
        elif kind in DELIVERY_WORDS:
            stop.action = "delivery"
        else:
            raise ValueError(f"type must be pickup or delivery, got {kind!r}")
        stop.shipment = shipment


def _shipment_row(row: Dict[str, str], i: int) -> List[Stop]:
    """One CSV row describing a whole shipment -> its pickup and delivery stops."""
    name = _pick(row, NAME_COLS) or f"Shipment {i}"
    sid = _pick(row, SHIPMENT_COLS) or name
    p_addr, d_addr = _pick(row, PICKUP_COLS), _pick(row, DELIVERY_COLS)
    p_lat, p_lon = _to_float(_pick(row, PICKUP_LAT)), _to_float(_pick(row, PICKUP_LON))
    d_lat, d_lon = _to_float(_pick(row, DELIVERY_LAT)), _to_float(_pick(row, DELIVERY_LON))
    if not (p_addr or p_lat is not None) or not (d_addr or d_lat is not None):
        raise ValueError("a shipment needs both a pickup and a delivery (address or lat/lon)")
    demand = _pick(row, DEMAND_COLS)
    d = _to_float(demand) if demand else 1.0
    if d is None or d < 0:
        raise ValueError(f"demand must be a non-negative number, got {demand!r}")
    service = _to_float(_pick(row, SERVICE_COLS)) if _pick(row, SERVICE_COLS) else None
    pickup = Stop(f"{name} (pickup)", p_addr, p_lat, p_lon, demand=d, shipment=sid, action="pickup",
                  earliest=parse_clock(_pick(row, PICKUP_EARLIEST)), latest=parse_clock(_pick(row, PICKUP_LATEST)),
                  ready=parse_clock(_pick(row, READY_COLS)), service=service)
    delivery = Stop(f"{name} (delivery)", d_addr, d_lat, d_lon, demand=d, shipment=sid, action="delivery",
                    earliest=parse_clock(_pick(row, DELIVERY_EARLIEST)),
                    latest=parse_clock(_pick(row, DELIVERY_LATEST)), service=service)
    truck = _pick(row, TRUCK_COLS)
    if truck:
        t = _to_float(truck)
        if t is None or t < 1 or t != int(t):
            raise ValueError(f"truck must be a whole number starting at 1, got {truck!r}")
        pickup.truck = delivery.truck = int(t)
    for s in (pickup, delivery):
        if s.earliest is not None and s.latest is not None and s.earliest > s.latest:
            raise ValueError(f"{s.name}: earliest time is after the deadline")
    return [pickup, delivery]


def check_shipments(stops: List[Stop]) -> None:
    """Every shipment needs exactly one pickup and one delivery with the same demand."""
    by_id: Dict[str, List[Stop]] = {}
    for s in stops:
        if s.shipment is not None:
            by_id.setdefault(s.shipment, []).append(s)
    for sid, members in by_id.items():
        kinds = sorted(m.action for m in members)
        if kinds != ["delivery", "pickup"]:
            raise ValueError(f"shipment {sid!r} needs exactly one pickup and one delivery (found {', '.join(kinds)})")
        p, d = (members[0], members[1]) if members[0].action == "pickup" else (members[1], members[0])
        if p.demand != d.demand:
            raise ValueError(f"shipment {sid!r}: pickup and delivery demand differ ({p.demand:g} vs {d.demand:g})")
        if p.group or d.group:
            raise ValueError(f"shipment {sid!r}: shipments can't also be in a 'group'")


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
