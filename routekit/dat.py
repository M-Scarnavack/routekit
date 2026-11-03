"""DAT load board and rate connector (requires a DAT subscription and API access).

**Status: unverified against the live DAT API.** DAT's API documentation is
behind its developer portal (https://developer.dat.com; access by request through
DAT). This client follows DAT's publicly described API: a two-step login (an
*organization* token from a service account, exchanged for a *user* token), lane
rate lookups (RateView), and freight searches. Every host is configurable, and
responses are parsed defensively (the raw JSON is always returned too), so field
names can be adjusted once someone with portal access tests it.

Configuration (environment only; never commit credentials):

    ROUTEKIT_DAT_SERVICE_USER       service-account username (from DAT)
    ROUTEKIT_DAT_SERVICE_PASSWORD   service-account password
    ROUTEKIT_DAT_USER               the DAT user (email) the searches run as
    ROUTEKIT_DAT_IDENTITY_URL       default https://identity.api.dat.com
    ROUTEKIT_DAT_FREIGHT_URL        default https://freight.api.dat.com
    ROUTEKIT_DAT_ANALYTICS_URL      default https://analytics.api.dat.com

DAT's terms prohibit scraping its website; this uses only the API.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from routekit.http import HttpError, request_json

IDENTITY = "https://identity.api.dat.com"
FREIGHT = "https://freight.api.dat.com"
ANALYTICS = "https://analytics.api.dat.com"

# Equipment names for rate lookups and the one-letter codes load search uses.
EQUIPMENT = {"van": ("VAN", "V"), "reefer": ("REEFER", "R"), "flatbed": ("FLATBED", "F")}


class DatError(RuntimeError):
    pass


def place(text: str) -> Dict[str, str]:
    """'Chicago, IL' -> {"city": "Chicago", "stateProv": "IL"}; '60607' -> {"postalCode": "60607"}."""
    t = text.strip()
    if re.fullmatch(r"\d{5}", t):
        return {"postalCode": t}
    m = re.fullmatch(r"(.+?),\s*([A-Za-z]{2})", t)
    if not m:
        raise DatError(f"use 'City, ST' or a 5-digit ZIP for DAT places, not {text!r}")
    return {"city": m.group(1).strip(), "stateProv": m.group(2).upper()}


def equipment(name: str):
    try:
        return EQUIPMENT[name.lower()]
    except KeyError:
        raise DatError(f"equipment must be one of: {', '.join(EQUIPMENT)}") from None


def _find(obj: Any, *keys: str) -> Any:
    """First value found under any of `keys`, searching nested dicts and lists (defensive parsing)."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        for v in obj.values():
            hit = _find(v, *keys)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, *keys)
            if hit is not None:
                return hit
    return None


def _expiry(data: dict) -> float:
    when = data.get("expiresWhen") or data.get("expires_at")
    if when:
        try:
            return datetime.fromisoformat(str(when).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    secs = data.get("expiresIn") or data.get("expires_in")
    return time.time() + (float(secs) if secs else 15 * 60)


@dataclass
class LaneRate:
    origin: str
    destination: str
    equipment: str
    rate_type: str
    per_mile: Optional[float]
    low_per_mile: Optional[float]
    high_per_mile: Optional[float]
    per_trip: Optional[float]
    fuel_per_mile: Optional[float]
    miles: Optional[float]
    timeframe: Optional[str]
    escalation: Optional[str]
    raw: Any = field(repr=False, default=None)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "raw"}
        d["raw"] = self.raw
        return d


@dataclass
class Load:
    id: str
    origin: str
    destination: str
    origin_lat: Optional[float]
    origin_lon: Optional[float]
    pickup: Optional[str]
    equipment: Optional[str]
    length_ft: Optional[float]
    weight_lbs: Optional[float]
    rate_usd: Optional[float]
    trip_miles: Optional[float]
    deadhead_miles: Optional[float]
    company: Optional[str]
    phone: Optional[str]
    raw: Any = field(repr=False, default=None)
    dest_lat: Optional[float] = None
    dest_lon: Optional[float] = None

    @property
    def rate_per_mile(self) -> Optional[float]:
        if self.rate_usd and self.trip_miles:
            return self.rate_usd / self.trip_miles
        return None

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "raw"}
        d["rate_per_mile"] = self.rate_per_mile
        return d


def _place_text(p: Any) -> str:
    if not isinstance(p, dict):
        return str(p or "")
    city, st = p.get("city"), p.get("stateProv") or p.get("stateOrProvince") or p.get("state")
    return ", ".join(x for x in (city, st) if x) or p.get("postalCode", "")


class DatClient:
    def __init__(self, service_user: str, service_password: str, user: str, identity: str = IDENTITY,
                 freight: str = FREIGHT, analytics: str = ANALYTICS, session=None):
        missing = [n for n, v in (("ROUTEKIT_DAT_SERVICE_USER", service_user),
                                  ("ROUTEKIT_DAT_SERVICE_PASSWORD", service_password),
                                  ("ROUTEKIT_DAT_USER", user)) if not v]
        if missing:
            raise DatError("DAT needs API credentials (a DAT subscription with API access). Set: "
                           + ", ".join(missing))
        self.service_user, self.service_password, self.user = service_user, service_password, user
        self.identity, self.freight, self.analytics = identity.rstrip("/"), freight.rstrip("/"), analytics.rstrip("/")
        self.session = session
        self._org: Optional[tuple] = None   # (token, expires_at)
        self._usr: Optional[tuple] = None

    @classmethod
    def from_env(cls, session=None) -> "DatClient":
        e = os.environ.get
        return cls(e("ROUTEKIT_DAT_SERVICE_USER", ""), e("ROUTEKIT_DAT_SERVICE_PASSWORD", ""), e("ROUTEKIT_DAT_USER", ""),
                   e("ROUTEKIT_DAT_IDENTITY_URL", IDENTITY), e("ROUTEKIT_DAT_FREIGHT_URL", FREIGHT),
                   e("ROUTEKIT_DAT_ANALYTICS_URL", ANALYTICS), session)

    # -- auth ----------------------------------------------------------------

    def _call(self, method: str, url: str, body: Optional[Any] = None, token: Optional[str] = None,
              params: Optional[dict] = None) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            data = request_json(method, url, params=params, body=body, session=self.session, interval=0.2,
                                headers=headers)
        except HttpError as e:
            if e.status in (401, 403):
                raise DatError("DAT rejected the credentials or this account lacks API access for that product") from None
            raise DatError(f"DAT request failed: {e}") from None
        status = data.get("_http_status", 200) if isinstance(data, dict) else 200
        if status in (401, 403):
            raise DatError("DAT rejected the credentials or this account lacks API access for that product")
        if status >= 400:
            msg = _find(data, "message", "error", "detail") or f"HTTP {status}"
            raise DatError(f"DAT returned an error: {msg}")
        return data

    def _token(self) -> str:
        now = time.time()
        if self._usr and self._usr[1] - 60 > now:
            return self._usr[0]
        if not (self._org and self._org[1] - 60 > now):
            d = self._call("POST", f"{self.identity}/access/v1/token/organization",
                           {"username": self.service_user, "password": self.service_password})
            tok = d.get("accessToken") or d.get("access_token")
            if not tok:
                raise DatError("DAT didn't return an organization token")
            self._org = (tok, _expiry(d))
        d = self._call("POST", f"{self.identity}/access/v1/token/user", {"username": self.user}, token=self._org[0])
        tok = d.get("accessToken") or d.get("access_token")
        if not tok:
            raise DatError("DAT didn't return a user token")
        self._usr = (tok, _expiry(d))
        return tok

    def check(self) -> bool:
        self._token()
        return True

    # -- rates (RateView) ------------------------------------------------------

    def rate(self, origin: str, destination: str, equip: str = "van", contract: bool = False) -> LaneRate:
        rate_eq, _ = equipment(equip)
        rate_type = "CONTRACT" if contract else "SPOT"
        lane = {"origin": _rate_place(origin), "destination": _rate_place(destination), "rateType": rate_type,
                "equipment": rate_eq, "includeMyRate": False, "targetEscalation": {"escalationType": "BEST_FIT"}}
        data = self._call("POST", f"{self.analytics}/linehaulrates/v1/lookups", [lane], token=self._token())
        resp = _find(data, "rateResponses") or data
        rate = _find(resp, "rate") or {}
        per_mile = _find(rate, "perMile") or {}
        per_trip = _find(rate, "perTrip") or {}
        esc = _find(resp, "escalation") or {}
        return LaneRate(origin, destination, rate_eq, rate_type,
                        _num(_find(per_mile, "rateUsd")), _num(_find(per_mile, "lowUsd")),
                        _num(_find(per_mile, "highUsd")), _num(_find(per_trip, "rateUsd")),
                        _num(_find(rate, "averageFuelSurchargePerMileUsd")), _num(_find(rate, "mileage")),
                        _find(esc, "timeframe"),
                        " → ".join(x for x in (_find(_find(esc, "origin") or {}, "name"),
                                               _find(_find(esc, "destination") or {}, "name")) if x) or None,
                        raw=data)

    # -- load search -----------------------------------------------------------

    def search_loads(self, origin: str, radius: int = 100, equip: str = "van", destination: Optional[str] = None,
                     pickup_from: Optional[str] = None, limit: int = 50) -> List[Load]:
        _, code = equipment(equip)
        lane: Dict[str, Any] = {
            "assetType": "SHIPMENT",
            "equipment": {"types": [code]},
            "origin": {"place": place(origin), "area": {"radius": {"miles": int(radius)}}} if radius else
                      {"place": place(origin)},
            "destination": {"place": place(destination)} if destination else {"open": {}},
        }
        criteria: Dict[str, Any] = {"lane": lane, "maxAgeMinutes": 1440}
        if pickup_from:
            criteria["availability"] = {"earliestWhen": f"{pickup_from}T00:00:00.000Z"}
        token = self._token()
        q = self._call("POST", f"{self.freight}/search/v3/queries",
                       {"criteria": criteria, "delivery": {"notify": False}, "includeOpenDestinationTrucks": False},
                       token=token)
        query_id = _find(q, "queryId", "id")
        if not query_id:
            raise DatError("DAT didn't return a search id")
        res = self._call("GET", f"{self.freight}/search/v3/queryMatches/{query_id}", token=token,
                         params={"limit": int(limit)})
        matches = _find(res, "matches") or (res if isinstance(res, list) else [])
        return [_load(m) for m in matches if isinstance(m, dict)]


def _rate_place(text: str) -> Dict[str, str]:
    p = place(text)
    return {"postalCode": p["postalCode"]} if "postalCode" in p else {"city": p["city"], "stateOrProvince": p["stateProv"]}


def _num(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _load(m: dict) -> Load:
    o = _find(m, "origin") or {}
    d = _find(m, "destination") or {}
    avail = _find(m, "availability") or {}
    poster = _find(m, "posterInfo", "poster") or {}
    contact = _find(poster, "contact") or {}
    return Load(
        id=str(_find(m, "matchId", "postingId", "id") or ""),
        origin=_place_text(o), destination=_place_text(d) or "open",
        origin_lat=_num(_find(o, "latitude")), origin_lon=_num(_find(o, "longitude")),
        pickup=_find(avail, "earliestWhen", "earliest"),
        equipment=_find(m, "equipmentType", "type"),
        length_ft=_num(_find(m, "lengthFeet", "maximumLengthFeet")),
        weight_lbs=_num(_find(m, "weightPounds", "maximumWeightPounds")),
        rate_usd=_num(_find(m, "rateUsd", "amount")),
        trip_miles=_num(_find(m, "tripLength", "tripMiles", "tripLengthMiles")),
        deadhead_miles=_num(_find(m, "originDeadheadMiles", "deadheadMiles")),
        company=_find(poster, "companyName", "name"),
        phone=_find(contact, "phoneNumber", "phone") or _find(poster, "phone"),
        raw=m, dest_lat=_num(_find(d, "latitude")), dest_lon=_num(_find(d, "longitude")))


SHIPMENT_FIELDS = ["name", "shipment", "pickup", "pickup_lat", "pickup_lon", "delivery", "delivery_lat",
                   "delivery_lon", "demand"]


def to_shipments(loads: Iterable[Load]) -> List[dict]:
    """Loads as pickup → delivery shipments for 'routekit plan' (one CSV row per load).

    Uses DAT's coordinates when present; otherwise the 'City, ST' text, which the Census
    geocoder can't place without a street address (fill those in, or add coordinates).
    """
    rows = []
    for i, ld in enumerate(loads, start=1):
        sid = ld.id or f"load{i}"
        rows.append({"name": f"{ld.origin} → {ld.destination} ({ld.company or 'DAT'})", "shipment": sid,
                     "pickup": "" if ld.origin_lat is not None else ld.origin,
                     "pickup_lat": ld.origin_lat if ld.origin_lat is not None else "",
                     "pickup_lon": ld.origin_lon if ld.origin_lon is not None else "",
                     "delivery": "" if ld.dest_lat is not None else ld.destination,
                     "delivery_lat": ld.dest_lat if ld.dest_lat is not None else "",
                     "delivery_lon": ld.dest_lon if ld.dest_lon is not None else "",
                     "demand": 1})
    return rows


def to_stops(loads: Iterable[Load]) -> List[dict]:
    """Pickups as stop rows (name, address, lat, lon) for 'routekit plan'."""
    rows = []
    for ld in loads:
        rows.append({"name": f"Pickup {ld.id or ''} → {ld.destination}".strip(),
                     "address": "" if ld.origin_lat is not None else ld.origin,
                     "lat": ld.origin_lat if ld.origin_lat is not None else "",
                     "lon": ld.origin_lon if ld.origin_lon is not None else ""})
    return rows
