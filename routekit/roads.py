"""Real road distances, drive times, and route shapes from an OSRM server.

`OSRM <https://project-osrm.org/>`_ is open-source routing on OpenStreetMap
data. By default routekit uses the free public demo server, which needs no
API key but is rate-limited and meant for light use; for heavy use, run your
own OSRM and pass ``--osrm-url`` (or set ``ROUTEKIT_OSRM_URL``).

- :func:`road_matrix` returns road miles and drive minutes between every pair of
  points. Big problems are split into blocks that fit the server's 100-point
  table limit.
- :func:`road_geometry` returns the road shape of each leg of a route, for
  drawing on the map.

Results are cached in ``~/.cache/routekit/roads/``, and requests are spaced at
least a second apart to respect the demo server's usage policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import requests

from routekit import __version__
from routekit.geocode import cache_path

DEFAULT_OSRM = "https://router.project-osrm.org"
METERS_PER_MILE = 1609.344
TABLE_LIMIT = 100       # coordinates per /table request (OSRM's default max-table-size)
ROUTE_LIMIT = 100       # waypoints per /route request
MIN_INTERVAL = 1.0      # seconds between requests (demo server policy: about 1 per second)
TIMEOUT = 60

LatLon = Tuple[float, float]
_last_request = 0.0


class RoadError(RuntimeError):
    pass


def osrm_url(override: Optional[str] = None) -> str:
    return (override or os.environ.get("ROUTEKIT_OSRM_URL") or DEFAULT_OSRM).rstrip("/")


def _cache_dir() -> Path:
    return cache_path().parent / "roads"


def _cached(kind: str, base: str, points: Sequence[LatLon], extra: str = ""):
    key = json.dumps([kind, base, [(round(a, 6), round(b, 6)) for a, b in points], extra])
    path = _cache_dir() / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")
    try:
        return path, json.loads(path.read_text())
    except (OSError, ValueError):
        return path, None


def _store(path: Path, value) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    except OSError:
        pass  # caching is best-effort


def _get(session: requests.Session, url: str, params: dict) -> dict:
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    try:
        resp = session.get(url, params=params, timeout=TIMEOUT,
                           headers={"User-Agent": f"routekit/{__version__} (+https://github.com/M-Scarnavack/routekit)"})
        _last_request = time.monotonic()
        if resp.status_code == 429:
            raise RoadError("the OSRM server is rate-limiting requests; wait a minute, or use your own "
                            "server with --osrm-url")
        data = resp.json()
    except requests.exceptions.SSLError as e:
        import ssl
        raise RoadError(
            f"couldn't open a secure connection to {urlsplit(url).netloc}. Your Python's SSL "
            f"library ({ssl.OPENSSL_VERSION}) is probably too old: the public OSRM server requires TLS 1.3, "
            "which macOS's built-in Python 3.9 can't do. Fix it by installing a current Python from "
            "python.org, or accept an unencrypted connection with "
            "--osrm-url http://router.project-osrm.org (your stop locations would be sent in the clear)"
        ) from e
    except requests.RequestException as e:
        raise RoadError(f"couldn't reach the OSRM server ({e})") from e
    except ValueError:
        raise RoadError(f"the OSRM server sent an unexpected response (HTTP {resp.status_code})") from None
    if data.get("code") != "Ok":
        msg = data.get("message") or data.get("code") or "unknown error"
        raise RoadError(f"OSRM couldn't route these points: {msg}")
    return data


def _coords(points: Sequence[LatLon]) -> str:
    return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)  # OSRM wants lon,lat


def road_matrix(points: Sequence[LatLon], base_url: Optional[str] = None,
                session: Optional[requests.Session] = None,
                progress=None) -> Tuple[List[List[float]], List[List[float]]]:
    """Road miles and drive minutes between every pair of points, as (miles, minutes).

    Entries are directional (one-way streets make A→B differ from B→A).
    """
    base = osrm_url(base_url)
    path, hit = _cached("table", base, points)
    if hit:
        return hit["miles"], hit["minutes"]
    n = len(points)
    miles = [[0.0] * n for _ in range(n)]
    minutes = [[0.0] * n for _ in range(n)]
    block = TABLE_LIMIT // 2 if n > TABLE_LIMIT else n
    blocks = [list(range(i, min(i + block, n))) for i in range(0, n, block)]
    total, done = len(blocks) ** 2, 0
    own = session is None
    session = session or requests.Session()
    try:
        for rows in blocks:
            for cols in blocks:
                idx = rows + [c for c in cols if c not in rows]
                pos = {p: k for k, p in enumerate(idx)}
                data = _get(session, f"{base}/table/v1/driving/{_coords([points[i] for i in idx])}", {
                    "annotations": "distance,duration",
                    "sources": ";".join(str(pos[r]) for r in rows),
                    "destinations": ";".join(str(pos[c]) for c in cols),
                })
                for a, r in enumerate(rows):
                    for b, c in enumerate(cols):
                        dist, dur = data["distances"][a][b], data["durations"][a][b]
                        if dist is None or dur is None:
                            raise RoadError(f"no road route between point {r + 1} and point {c + 1}")
                        miles[r][c] = dist / METERS_PER_MILE
                        minutes[r][c] = dur / 60.0
                done += 1
                if progress:
                    progress(done, total)
    finally:
        if own:
            session.close()
    _store(path, {"miles": miles, "minutes": minutes})
    return miles, minutes


def symmetric(m: Sequence[Sequence[float]]) -> List[List[float]]:
    """Average each pair of directions, so A→B equals B→A (2-opt assumes this)."""
    n = len(m)
    return [[(m[i][j] + m[j][i]) / 2 for j in range(n)] for i in range(n)]


def decode_polyline(text: str, precision: int = 5) -> List[LatLon]:
    """Decode a Google/OSRM encoded polyline into (lat, lon) points."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    while index < len(text):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(text[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def road_geometry(waypoints: Sequence[LatLon], base_url: Optional[str] = None,
                  session: Optional[requests.Session] = None) -> List[List[LatLon]]:
    """The road shape of each leg of a route through `waypoints`, in order.

    Returns one list of (lat, lon) points per leg (len(waypoints) - 1 legs).
    """
    base = osrm_url(base_url)
    path, hit = _cached("route", base, waypoints)
    if hit:
        return [[tuple(p) for p in leg] for leg in hit]
    legs: List[List[LatLon]] = []
    own = session is None
    session = session or requests.Session()
    try:
        for start in range(0, len(waypoints) - 1, ROUTE_LIMIT - 1):
            chunk = waypoints[start:start + ROUTE_LIMIT]
            data = _get(session, f"{base}/route/v1/driving/{_coords(chunk)}",
                        {"overview": "false", "steps": "true", "geometries": "polyline"})
            for leg in data["routes"][0]["legs"]:
                pts: List[LatLon] = []
                for step in leg["steps"]:
                    seg = decode_polyline(step["geometry"])
                    pts.extend(seg if not pts else seg[1:])
                legs.append(pts)
    finally:
        if own:
            session.close()
    _store(path, legs)
    return legs
