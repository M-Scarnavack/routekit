"""Truck routing and tolls with Valhalla.

`Valhalla <https://valhalla.github.io/valhalla/>`_ is open-source routing on
OpenStreetMap data with a real truck profile: it honors height, weight, length,
and hazmat restrictions, and it can avoid toll roads. routekit uses the free
public server run by FOSSGIS (``valhalla1.openstreetmap.de``) by default; pass
``--valhalla-url`` (or set ``ROUTEKIT_VALHALLA_URL``) to use your own.

The public server allows 100 source×target pairs per matrix request and 10
waypoints per route, so larger problems are split into blocks. Results are
cached and requests are spaced a second apart.

Toll *dollar amounts* aren't in OpenStreetMap. routekit reports which legs use
toll roads, how many miles of each, and, with ``avoid_tolls``, how many extra
miles and minutes avoiding them costs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from routekit.http import HttpError, request_json
from routekit.roads import RoadError, _cached, _store, decode_polyline

DEFAULT_VALHALLA = "https://valhalla1.openstreetmap.de"
MATRIX_BLOCK = 10   # 10 × 10 = the public server's 100-pair limit
ROUTE_LIMIT = 10    # waypoints per /route request
FT = 0.3048
SHORT_TON = 0.90718474  # US ton -> metric tonne

LatLon = Tuple[float, float]


@dataclass
class Vehicle:
    """What's being driven. Truck dimensions are in feet and US tons; None = Valhalla's defaults
    (13.5 ft tall, 24 tons, 71 ft long: a typical tractor-trailer)."""

    truck: bool = False
    height_ft: Optional[float] = None
    weight_tons: Optional[float] = None
    length_ft: Optional[float] = None
    width_ft: Optional[float] = None
    hazmat: bool = False
    avoid_tolls: bool = False

    @property
    def costing(self) -> str:
        return "truck" if self.truck else "auto"

    def costing_options(self, avoid_tolls: Optional[bool] = None, matrix: bool = False) -> dict:
        """Valhalla costing options. Valhalla's matrix can't do a hard toll exclusion (it returns no
        route), so the matrix uses the strongest toll *avoidance* and routes use the hard exclusion."""
        avoid = self.avoid_tolls if avoid_tolls is None else avoid_tolls
        o: dict = ({"use_tolls": 0.0} if matrix else {"exclude_tolls": True}) if avoid else {}
        if self.truck:
            if self.height_ft is not None:
                o["height"] = round(self.height_ft * FT, 2)
            if self.weight_tons is not None:
                o["weight"] = round(self.weight_tons * SHORT_TON, 2)
            if self.length_ft is not None:
                o["length"] = round(self.length_ft * FT, 2)
            if self.width_ft is not None:
                o["width"] = round(self.width_ft * FT, 2)
            if self.hazmat:
                o["hazmat"] = True
        return {self.costing: o}

    def describe(self) -> str:
        if not self.truck:
            parts = ["car"]
        else:
            dims = [f"{self.height_ft:g} ft tall" if self.height_ft else "",
                    f"{self.weight_tons:g} tons" if self.weight_tons else "",
                    f"{self.length_ft:g} ft long" if self.length_ft else "",
                    "hazmat" if self.hazmat else ""]
            parts = ["truck"] + [d for d in dims if d]
        if self.avoid_tolls:
            parts.append("no tolls")
        return ", ".join(parts)


def valhalla_url(override: Optional[str] = None) -> str:
    return (override or os.environ.get("ROUTEKIT_VALHALLA_URL") or DEFAULT_VALHALLA).rstrip("/")


class MatrixRangeError(RoadError):
    """The server's distance table won't cover points this far apart (the public server caps it at 150 km)."""


def _post(session, url: str, body: dict) -> dict:
    try:
        data = request_json("POST", url, body=body, session=session)
    except HttpError as e:
        raise RoadError(str(e)) from None
    if "error" in data:
        if "max distance" in str(data["error"]).lower() and url.endswith("/sources_to_targets"):
            raise MatrixRangeError(f"Valhalla's distance table only covers points within about 93 miles of "
                                   f"each other on this server ({data['error']})")
        raise RoadError(f"Valhalla couldn't route these points: {data['error']}")
    return data


def _loc(p: LatLon) -> dict:
    return {"lat": round(p[0], 6), "lon": round(p[1], 6)}


def truck_matrix(points: Sequence[LatLon], vehicle: Vehicle, base_url: Optional[str] = None,
                 session=None, progress=None) -> Tuple[List[List[float]], List[List[float]]]:
    """Miles and drive minutes between every pair of points for this vehicle (directional)."""
    base = valhalla_url(base_url)
    opts = vehicle.costing_options(matrix=True)
    path, hit = _cached("valhalla-matrix", base, points, repr((vehicle.costing, opts)))
    if hit:
        return hit["miles"], hit["minutes"]
    n = len(points)
    miles = [[0.0] * n for _ in range(n)]
    minutes = [[0.0] * n for _ in range(n)]
    blocks = [list(range(i, min(i + MATRIX_BLOCK, n))) for i in range(0, n, MATRIX_BLOCK)]
    total, done = len(blocks) ** 2, 0
    for rows in blocks:
        for cols in blocks:
            data = _post(session, f"{base}/sources_to_targets", {
                "sources": [_loc(points[r]) for r in rows],
                "targets": [_loc(points[c]) for c in cols],
                "costing": vehicle.costing, "costing_options": opts, "units": "miles",
            })
            for a, row in enumerate(data["sources_to_targets"]):
                for b, cell in enumerate(row):
                    r, c = rows[a], cols[b]
                    if r == c:
                        continue
                    if cell.get("distance") is None or cell.get("time") is None:
                        raise RoadError(f"no {vehicle.costing} route between point {r + 1} and point {c + 1}"
                                        + (" (height/weight/length limits?)" if vehicle.truck else ""))
                    miles[r][c] = float(cell["distance"])
                    minutes[r][c] = float(cell["time"]) / 60.0
            done += 1
            if progress:
                progress(done, total)
    _store(path, {"miles": miles, "minutes": minutes})
    return miles, minutes


@dataclass
class Leg:
    shape: List[LatLon]
    miles: float
    minutes: float
    toll_miles: float

    @property
    def tolled(self) -> bool:
        return self.toll_miles > 0.005


def truck_route(waypoints: Sequence[LatLon], vehicle: Vehicle, base_url: Optional[str] = None,
                session=None, avoid_tolls: Optional[bool] = None) -> List[Leg]:
    """Each leg of a route through `waypoints`: road shape, miles, minutes, and toll miles."""
    base = valhalla_url(base_url)
    opts = vehicle.costing_options(avoid_tolls)
    path, hit = _cached("valhalla-route", base, waypoints, repr((vehicle.costing, opts)))
    if hit:
        return [Leg([tuple(p) for p in h["shape"]], h["miles"], h["minutes"], h["toll_miles"]) for h in hit]
    legs: List[Leg] = []
    for start in range(0, len(waypoints) - 1, ROUTE_LIMIT - 1):
        chunk = waypoints[start:start + ROUTE_LIMIT]
        body = {"locations": [_loc(p) for p in chunk], "costing": vehicle.costing,
                "costing_options": opts, "units": "miles", "directions_type": "maneuvers"}
        try:
            data = _post(session, f"{base}/route", body)
        except RoadError:
            if not (avoid_tolls if avoid_tolls is not None else vehicle.avoid_tolls):
                raise
            # No toll-free road exists somewhere: avoid tolls as much as possible instead,
            # and let the toll miles show what couldn't be avoided.
            data = _post(session, f"{base}/route",
                         {**body, "costing_options": vehicle.costing_options(avoid_tolls, matrix=True)})
        for leg in data["trip"]["legs"]:
            toll = sum(m.get("length", 0.0) for m in leg.get("maneuvers", []) if m.get("toll"))
            s = leg["summary"]
            legs.append(Leg(decode_polyline(leg["shape"], precision=6), float(s["length"]),
                            float(s["time"]) / 60.0, float(toll)))
    _store(path, [{"shape": l.shape, "miles": l.miles, "minutes": l.minutes, "toll_miles": l.toll_miles}
                  for l in legs])
    return legs
