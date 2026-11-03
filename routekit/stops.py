"""Loading and saving stop lists (CSV)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

NAME_COLS = ("name", "stop", "label", "id", "customer")
ADDRESS_COLS = ("address", "full_address", "location")
STREET_COLS = ("street", "address1", "street_address")
CITY_COLS = ("city", "town")
STATE_COLS = ("state", "st")
ZIP_COLS = ("zip", "zipcode", "zip_code", "postal_code", "postcode")
LAT_COLS = ("lat", "latitude")
LON_COLS = ("lon", "lng", "long", "longitude")


@dataclass
class Stop:
    name: str
    address: str
    lat: Optional[float] = None
    lon: Optional[float] = None

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None


class StopFileError(ValueError):
    pass


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
            address = _pick(row, ADDRESS_COLS)
            if not address:
                parts = [_pick(row, STREET_COLS), _pick(row, CITY_COLS)]
                state_zip = " ".join(p for p in (_pick(row, STATE_COLS), _pick(row, ZIP_COLS)) if p)
                address = ", ".join(p for p in parts + [state_zip] if p)
            lat, lon = _to_float(_pick(row, LAT_COLS)), _to_float(_pick(row, LON_COLS))
            if not address and lat is None:
                continue  # blank line
            name = _pick(row, NAME_COLS) or f"Stop {i}"
            stops.append(Stop(name=name, address=address, lat=lat, lon=lon))

    if len(stops) < 2:
        raise StopFileError(f"{path} needs at least 2 stops (found {len(stops)}).")
    return stops


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
