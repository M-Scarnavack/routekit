"""Freight demand: where truck freight originates and where it goes (FAF5).

Built from the Freight Analysis Framework (FAF5.7.1, Bureau of Transportation
Statistics / FHWA), the federal government's estimate of all freight moved in the
U.S. by origin state, destination state, commodity, and mode. routekit bundles a
compact truck-only summary (see scripts/build_freight.py).

This is **annual tonnage**, the long-run shape of where loads pick up and where
they go. It is *not* live load-board postings (those need a load board such as
DAT; see :mod:`routekit.dat`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from routekit.fema import counties

DATA_FILE = Path(__file__).resolve().parent / "data" / "freight_truck.json"


class FreightDataError(ValueError):
    pass


@lru_cache(maxsize=1)
def data() -> dict:
    try:
        return json.loads(DATA_FILE.read_text())
    except (OSError, ValueError) as e:
        raise FreightDataError(f"freight data missing ({e})") from None


@lru_cache(maxsize=1)
def state_codes() -> Dict[str, str]:
    """{FIPS: postal code}, e.g. {"17": "IL"}, from the bundled county data."""
    return {f[:2]: c[1] for f, c in counties().items()}


@lru_cache(maxsize=1)
def state_centers() -> Dict[str, Tuple[float, float]]:
    """Rough center of each state (mean of its county center points)."""
    sums: Dict[str, List[float]] = {}
    for f, (_, _, lat, lon) in counties().items():
        s = sums.setdefault(f[:2], [0.0, 0.0, 0])
        s[0] += lat
        s[1] += lon
        s[2] += 1
    return {k: (v[0] / v[2], v[1] / v[2]) for k, v in sums.items()}


def resolve_state(text: str) -> str:
    """'IL', 'Illinois', or '17' -> '17'."""
    t = text.strip().lower()
    names = data()["states"]
    codes = state_codes()
    for fips, name in names.items():
        if t in (fips, name.lower(), codes.get(fips, "").lower()):
            return fips
    raise FreightDataError(f"unknown state {text!r}; use a postal code like IL or a name like Illinois")


def resolve_commodity(text: str) -> str:
    """'07', '7', or a (partial) name like 'food' -> '07'."""
    t = text.strip().lower()
    coms = data()["commodities"]
    if t.isdigit() and t.zfill(2) in coms:
        return t.zfill(2)
    hits = [c for c, name in coms.items() if t in name.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise FreightDataError(f"unknown commodity {text!r}. Options: " + ", ".join(
            f"{c} {n}" for c, n in coms.items()))
    raise FreightDataError(f"{text!r} matches several commodities: " + ", ".join(
        f"{c} {coms[c]}" for c in hits))


def state_label(fips: str) -> str:
    return f"{data()['states'].get(fips, fips)} ({state_codes().get(fips, '?')})"


@dataclass
class OriginRow:
    state: str            # FIPS
    tons: float           # thousand tons, latest year (all or interstate)
    share: float          # of the U.S. total shown
    change: Optional[float]  # vs. the first year (fraction), all freight
    top_commodity: str


def origins(interstate: bool = False, commodity: Optional[str] = None, top: int = 15) -> List[OriginRow]:
    """States ranked by truck freight originating there in the latest year.

    Tonnage, change since the first year, and top commodity all follow the same filters
    (interstate only, one commodity).
    """
    d = data()
    totals: Dict[str, float] = {}
    firsts: Dict[str, float] = {}
    if commodity:
        for lane, tons in d["commodity_lanes"].get(commodity, {}).items():
            o, dest = lane.split(">")
            if interstate and o == dest:
                continue
            totals[o] = totals.get(o, 0.0) + tons
    else:
        for lane, (first, latest, _) in d["lanes"].items():
            o, dest = lane.split(">")
            if interstate and o == dest:
                continue
            totals[o] = totals.get(o, 0.0) + latest
            firsts[o] = firsts.get(o, 0.0) + first
    by_com = _commodity_totals(interstate)
    grand = sum(totals.values()) or 1.0
    rows = []
    for st, tons in sorted(totals.items(), key=lambda kv: -kv[1])[:top]:
        change = (tons / firsts[st] - 1) if not commodity and firsts.get(st) else None
        coms = by_com.get(st, {})
        top_c = max(coms, key=coms.get) if coms else ""
        rows.append(OriginRow(st, tons, tons / grand, change, d["commodities"].get(top_c, "")))
    return rows


@lru_cache(maxsize=2)
def _commodity_totals(interstate: bool) -> Dict[str, Dict[str, float]]:
    """{origin: {commodity: tons}} for the latest year (lanes of at least 5,000 tons)."""
    out: Dict[str, Dict[str, float]] = {}
    for com, lanes_ in data()["commodity_lanes"].items():
        for lane, tons in lanes_.items():
            o, dest = lane.split(">")
            if interstate and o == dest:
                continue
            out.setdefault(o, {})
            out[o][com] = out[o].get(com, 0.0) + tons
    return out


@dataclass
class LaneRow:
    origin: str
    dest: str
    tons: float              # thousand tons, latest year
    value: Optional[float]   # million 2017 $, latest year (all-commodity lanes only)
    change: Optional[float]  # vs. first year (all-commodity lanes only)


def lanes(origin: Optional[str] = None, dest: Optional[str] = None, commodity: Optional[str] = None,
          interstate: bool = True, top: int = 15) -> List[LaneRow]:
    """Biggest truck lanes, optionally from one state, into one state, and/or for one commodity."""
    d = data()
    out = []
    src = d["commodity_lanes"].get(commodity, {}) if commodity else d["lanes"]
    for lane, v in src.items():
        o, de = lane.split(">")
        if (origin and o != origin) or (dest and de != dest) or (interstate and o == de):
            continue
        if commodity:
            out.append(LaneRow(o, de, v, None, None))
        else:
            first, latest, value = v
            out.append(LaneRow(o, de, latest, value, (latest / first - 1) if first else None))
    return sorted(out, key=lambda r: -r.tons)[:top]


def fmt_tons(thousand_tons: float) -> str:
    t = thousand_tons * 1000
    if t >= 1e9:
        return f"{t / 1e9:,.2f}B tons"
    if t >= 1e6:
        return f"{t / 1e6:,.1f}M tons"
    if t >= 1e3:
        return f"{t / 1e3:,.1f}K tons" if t < 1e4 else f"{t / 1e3:,.0f}K tons"
    return f"{t:,.0f} tons"


def fmt_value(million: Optional[float]) -> str:
    if million is None:
        return ""
    return f"${million / 1000:,.1f}B" if million >= 1000 else f"${million:,.0f}M"


def state_geojson(values: Dict[str, float], label) -> dict:
    """State polygons (bundled outlines) shaded by `values` (FIPS -> number), for the HTML map."""
    from routekit.basemap import DATA_FILE as STATES_FILE
    shapes = json.loads(STATES_FILE.read_text())["states"]
    by_postal = {v: k for k, v in state_codes().items()}
    top = max(values.values(), default=0) or 1.0
    feats = []
    for postal, rings in shapes.items():
        fips = by_postal.get(postal)
        v = values.get(fips, 0.0) if fips else 0.0
        share = v / top
        feats.append({"type": "Feature",
                      "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]},
                      "properties": {"fill": _heat(share), "fillOpacity": 0.15 + 0.6 * (share ** 0.6) if v else 0.04,
                                     "popup": label(fips, v) if fips else postal}})
    return {"type": "FeatureCollection", "features": feats}


def _heat(x: float) -> str:
    """Yellow (low) -> red (high)."""
    x = max(0.0, min(1.0, x)) ** 0.6
    r, g, b = 255, int(214 - 180 * x), int(102 - 80 * x)
    return f"#{r:02x}{g:02x}{b:02x}"


def lane_geojson(rows: List[LaneRow], label) -> dict:
    centers = state_centers()
    top = max((r.tons for r in rows), default=1.0) or 1.0
    feats = []
    for r in rows:
        a, b = centers.get(r.origin), centers.get(r.dest)
        if not a or not b:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[a[1], a[0]], [b[1], b[0]]]},
                      "properties": {"weight": 2 + 8 * (r.tons / top), "popup": label(r)}})
    return {"type": "FeatureCollection", "features": feats}
