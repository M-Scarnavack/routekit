"""Disaster relief: FEMA disaster declarations and National Weather Service alerts.

All sources are free U.S. government services with no API key:

- **OpenFEMA** disaster declarations: which counties are in an open major disaster
  (DR), emergency (EM), or fire management (FM) declaration, and which assistance
  programs are on.  https://www.fema.gov/about/openfema/api
- **NWS** active weather alerts, with the counties they cover.  https://www.weather.gov/documentation/services-web-api
- **Census geocoder** to find the county for each stop's coordinates.
- **Census Gazetteer** county internal points (bundled), so a list of declared
  counties can become a list of relief stops.

Counties are identified by 5-digit FIPS codes (state + county, e.g. ``12073``
for Leon County, FL). A statewide or tribal declaration with county code
``000`` matches every county in that state.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from routekit.geocode import cache_path
from routekit.http import HttpError, request_json

FEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
NWS_URL = "https://api.weather.gov/alerts/active"
CENSUS_COUNTY_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
COUNTIES_FILE = Path(__file__).resolve().parent / "data" / "us_counties.json"

KINDS = {"DR": "major disaster", "EM": "emergency", "FM": "fire management"}
PROGRAMS = (("ihProgramDeclared", "IH"), ("iaProgramDeclared", "IA"), ("paProgramDeclared", "PA"),
            ("hmProgramDeclared", "HM"))
SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}


class DisasterDataError(RuntimeError):
    pass


@dataclass
class Declaration:
    number: int
    kind: str                 # DR, EM, FM
    incident: str             # e.g. Hurricane
    title: str                # e.g. HURRICANE ISAIAS
    declared: date
    state: str                # postal code
    fips: str                 # 5-digit county FIPS, or state + "000" for statewide
    area: str                 # e.g. "Bay (County)"
    programs: List[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        return f"{self.kind}-{self.number}"

    @property
    def label(self) -> str:
        return f"{self.code} {self.title.title()}"

    @property
    def statewide(self) -> bool:
        return self.fips.endswith("000")

    def covers(self, county_fips: str) -> bool:
        return self.fips == county_fips or (self.statewide and county_fips[:2] == self.fips[:2])


@dataclass
class Alert:
    event: str                # e.g. Flood Warning
    severity: str
    headline: str
    counties: Set[str]        # 5-digit FIPS
    ends: Optional[str]
    geometry: Optional[dict]


def _today() -> date:
    return date.today()


def open_declarations(states: Optional[Iterable[str]] = None, days: int = 120, number: Optional[int] = None,
                      session=None) -> List[Declaration]:
    """Open (not closed out) declarations: one disaster `number`, or those declared in the last
    `days` days in `states` (all states if None). One entry per designated area."""
    if number is not None:
        flt = f"disasterNumber eq {int(number)}"
    else:
        since = (_today() - timedelta(days=days)).isoformat()
        flt = f"declarationDate ge '{since}T00:00:00.000Z' and disasterCloseoutDate eq null"
        states = sorted({s.upper() for s in states or []})
        if states:
            flt = "(" + " or ".join(f"state eq '{s}'" for s in states) + ") and " + flt
    try:
        data = request_json("GET", FEMA_URL, session=session, interval=0.2, params={
            "$filter": flt, "$top": 10000, "$orderby": "declarationDate desc",
            "$select": "disasterNumber,declarationType,incidentType,declarationTitle,declarationDate,state,"
                       "fipsStateCode,fipsCountyCode,designatedArea,disasterCloseoutDate,"
                       + ",".join(k for k, _ in PROGRAMS)})
    except HttpError as e:
        raise DisasterDataError(f"couldn't get FEMA declarations: {e}") from None
    out = []
    for r in data.get("DisasterDeclarationsSummaries", []):
        if number is not None and r.get("disasterCloseoutDate"):
            pass  # an explicitly requested disaster is shown even if closed
        out.append(Declaration(
            number=int(r["disasterNumber"]), kind=r["declarationType"], incident=r["incidentType"],
            title=r["declarationTitle"], declared=datetime.fromisoformat(r["declarationDate"][:10]).date(),
            state=r["state"], fips=f"{r['fipsStateCode']}{r['fipsCountyCode']}", area=r["designatedArea"],
            programs=[short for key, short in PROGRAMS if r.get(key)]))
    return out


def active_alerts(states: Iterable[str], session=None) -> List[Alert]:
    states = sorted({s.upper() for s in states})
    if not states:
        return []
    try:
        data = request_json("GET", NWS_URL, session=session, interval=0.2,
                            params={"area": ",".join(states), "status": "actual", "message_type": "alert"},
                            headers={"Accept": "application/geo+json"})
    except HttpError as e:
        raise DisasterDataError(f"couldn't get NWS alerts: {e}") from None
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        same = p.get("geocode", {}).get("SAME", []) or []
        out.append(Alert(event=p.get("event", "Alert"), severity=p.get("severity", "Unknown"),
                         headline=p.get("headline") or p.get("event", ""),
                         counties={c[-5:] for c in same if len(c) == 6},  # "012073" -> "12073"
                         ends=p.get("ends") or p.get("expires"), geometry=f.get("geometry")))
    return out


# -- counties --------------------------------------------------------------

@lru_cache(maxsize=1)
def counties() -> Dict[str, Tuple[str, str, float, float]]:
    """{fips: (name, state, lat, lon)} from the bundled Census Gazetteer."""
    try:
        return {k: tuple(v) for k, v in json.loads(COUNTIES_FILE.read_text())["counties"].items()}
    except (OSError, ValueError):
        return {}


def county_name(fips: str) -> str:
    c = counties().get(fips)
    return f"{c[0]}, {c[1]}" if c else fips


def _county_cache() -> Path:
    return cache_path().parent / "counties.json"


def county_fips(lat: float, lon: float, session=None) -> Optional[str]:
    """The county containing a point (Census geocoder), or None outside the U.S."""
    try:
        data = request_json("GET", CENSUS_COUNTY_URL, session=session, interval=0, params={
            "x": f"{lon:.6f}", "y": f"{lat:.6f}", "benchmark": "Public_AR_Current",
            "vintage": "Current_Current", "layers": "Counties", "format": "json"})
    except HttpError as e:
        raise DisasterDataError(f"couldn't look up counties: {e}") from None
    found = data.get("result", {}).get("geographies", {}).get("Counties", [])
    return found[0]["GEOID"] if found else None


def counties_for(points: Sequence[Tuple[float, float]], session=None) -> List[Optional[str]]:
    """County FIPS for each point, using a local cache and parallel lookups for the rest."""
    path = _county_cache()
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    keys = [f"{lat:.5f},{lon:.5f}" for lat, lon in points]
    missing = sorted({k for k in keys if k not in cache})
    if missing:
        def look(k):
            lat, lon = map(float, k.split(","))
            return k, county_fips(lat, lon, session)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for k, fips in pool.map(look, missing):
                cache[k] = fips
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache))
        except OSError:
            pass
    return [cache.get(k) for k in keys]


# -- matching stops to disasters ---------------------------------------------

@dataclass
class StopRisk:
    county: Optional[str]
    declarations: List[Declaration]
    alerts: List[Alert]

    @property
    def declared(self) -> bool:
        return bool(self.declarations)

    @property
    def flagged(self) -> bool:
        return bool(self.declarations or self.alerts)

    def notes(self) -> List[str]:
        out = []
        seen = set()
        for d in self.declarations:
            if d.code not in seen:
                seen.add(d.code)
                progs = f"; programs: {', '.join(d.programs)}" if d.programs else ""
                out.append(f"⚠ FEMA {d.label} ({KINDS.get(d.kind, d.kind)}, declared "
                           f"{d.declared:%b} {d.declared.day}, {d.declared.year}{progs})")
        for a in sorted(self.alerts, key=lambda a: SEVERITY_RANK.get(a.severity, 9)):
            out.append(f"⚠ NWS {a.event} ({a.severity})")
        return out


def assess(points: Sequence[Tuple[float, float]], days: int = 120, session=None):
    """County, open declarations, and active alerts for each point.

    Returns (risks, declarations, alerts), where the last two cover every state the points are in.
    """
    fips = counties_for(points, session)
    states = sorted({counties()[f][1] for f in fips if f and f in counties()})
    decls = open_declarations(states, days=days, session=session) if states else []
    alerts = active_alerts(states, session=session) if states else []
    risks = []
    for f in fips:
        if not f:
            risks.append(StopRisk(None, [], []))
            continue
        risks.append(StopRisk(f, [d for d in decls if d.covers(f)], [a for a in alerts if f in a.counties]))
    return risks, decls, alerts


def group_by_disaster(decls: Iterable[Declaration]) -> Dict[str, List[Declaration]]:
    groups: Dict[str, List[Declaration]] = {}
    for d in decls:
        groups.setdefault(d.code, []).append(d)
    return groups
