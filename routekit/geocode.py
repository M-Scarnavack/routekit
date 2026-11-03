"""Geocoding via the free U.S. Census Bureau batch geocoder, with a local cache.

The Census geocoder needs no API key, covers U.S. street addresses, and accepts
up to 10,000 addresses per request. Results are cached in
``~/.cache/routekit/geocode.json`` so repeat runs work offline.
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from routekit.stops import Stop

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_SIZE = 1000  # Census allows 10k, but smaller batches fail faster and show progress
TIMEOUT = 120

LatLon = Tuple[float, float]


class GeocodeError(RuntimeError):
    pass


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "routekit" / "geocode.json"


def _key(address: str) -> str:
    return " ".join(address.upper().replace(",", " ").split())


def _load_cache(path: Path) -> Dict[str, Optional[List[float]]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, cache: Dict[str, Optional[List[float]]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=0, sort_keys=True))
        tmp.replace(path)
    except OSError:
        pass  # caching is best-effort


def parse_census_response(text: str) -> Dict[str, Optional[LatLon]]:
    """Parse the Census batch CSV response into {id: (lat, lon) or None}."""
    results: Dict[str, Optional[LatLon]] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        rid = row[0]
        if len(row) >= 6 and row[2] == "Match" and row[5]:
            lon_s, lat_s = row[5].split(",")  # Census returns "lon,lat"
            results[rid] = (float(lat_s), float(lon_s))
        else:
            results[rid] = None
    return results


def _census_batch(addresses: List[str], session: requests.Session) -> Dict[str, Optional[LatLon]]:
    buf = io.StringIO()
    w = csv.writer(buf)
    for i, addr in enumerate(addresses):
        w.writerow([i, addr, "", "", ""])
    try:
        resp = session.post(
            CENSUS_URL,
            files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
            data={"benchmark": "Public_AR_Current"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise GeocodeError(f"Census geocoder request failed: {e}") from e
    return parse_census_response(resp.text)


def geocode_stops(
    stops: List[Stop],
    offline: bool = False,
    use_cache: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[Stop]:
    """Fill in lat/lon for stops that lack them. Returns the stops that could not be located."""
    path = cache_path()
    cache = _load_cache(path) if use_cache else {}

    pending: List[Stop] = []
    for s in stops:
        if s.located:
            continue
        hit = cache.get(_key(s.address))
        if hit:
            s.lat, s.lon = hit
        elif _key(s.address) in cache and hit is None and offline:
            continue  # known miss
        else:
            pending.append(s)

    if pending and not offline:
        unique = sorted({s.address for s in pending})
        found: Dict[str, Optional[LatLon]] = {}
        with requests.Session() as session:
            for start in range(0, len(unique), BATCH_SIZE):
                chunk = unique[start:start + BATCH_SIZE]
                res = _census_batch(chunk, session)
                for i, addr in enumerate(chunk):
                    found[addr] = res.get(str(i))
                if progress:
                    progress(min(start + BATCH_SIZE, len(unique)), len(unique))
        for addr, ll in found.items():
            cache[_key(addr)] = list(ll) if ll else None
        for s in pending:
            ll = found.get(s.address)
            if ll:
                s.lat, s.lon = ll
        if use_cache:
            _save_cache(path, cache)

    return [s for s in stops if not s.located]


ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def geocode_one(address: str, offline: bool = False,
                session: Optional[requests.Session] = None) -> Optional[Tuple[float, float, str]]:
    """Look up one address right away: (lat, lon, matched address) or None.

    Used to confirm addresses as they're typed. Shares the batch geocoder's cache.
    """
    path = cache_path()
    cache = _load_cache(path)
    key = _key(address)
    hit = cache.get(key)
    if hit:
        return hit[0], hit[1], address
    if offline:
        return None
    try:
        resp = (session or requests).get(
            ONELINE_URL,
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=30,
        )
        resp.raise_for_status()
        matches = resp.json()["result"]["addressMatches"]
    except (requests.RequestException, ValueError, KeyError) as e:
        raise GeocodeError(f"Census geocoder request failed: {e}") from e
    if not matches:
        return None
    m = matches[0]
    lat, lon = float(m["coordinates"]["y"]), float(m["coordinates"]["x"])
    cache[key] = [lat, lon]
    _save_cache(path, cache)
    return lat, lon, m.get("matchedAddress", address)
