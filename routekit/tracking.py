"""Live truck locations from a fleet tracking system.

There's no public API for an arbitrary truck's location (for good privacy
reasons). Location comes from the fleet's own tracking system. routekit
currently connects to **Traccar**, free open-source GPS tracking
(https://www.traccar.org): run your own Traccar server, or use Traccar's hosted
service, and put the free Traccar Client app on each driver's phone (or use any
of the hundreds of GPS devices Traccar supports).

Configure with environment variables (never put credentials in files you commit):

    ROUTEKIT_TRACCAR_URL       e.g. https://traccar.example.com
    ROUTEKIT_TRACCAR_TOKEN     an API token (Traccar → Settings → Preferences → Token), or
    ROUTEKIT_TRACCAR_USER / ROUTEKIT_TRACCAR_PASSWORD

Other providers (Samsara, Motive, Geotab) can be added as subclasses of
:class:`TrackingProvider` that return the same :class:`TruckPosition` records.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from routekit.http import HttpError, request_json

KNOTS_TO_MPH = 1.150779


class TrackingError(RuntimeError):
    pass


@dataclass
class TruckPosition:
    id: str
    name: str
    lat: float
    lon: float
    speed_mph: float
    heading: Optional[float]
    fix_time: Optional[datetime]   # when the GPS fix was taken (UTC)
    status: str                    # online / offline / unknown
    address: str = ""

    @property
    def age_minutes(self) -> Optional[float]:
        if self.fix_time is None:
            return None
        return (datetime.now(timezone.utc) - self.fix_time).total_seconds() / 60

    @property
    def moving(self) -> bool:
        return self.speed_mph >= 3

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "lat": self.lat, "lon": self.lon,
                "speed_mph": round(self.speed_mph, 1), "heading": self.heading,
                "fix_time": self.fix_time.isoformat() if self.fix_time else None,
                "age_minutes": round(self.age_minutes, 1) if self.age_minutes is not None else None,
                "status": self.status, "address": self.address}


class TrackingProvider:
    name = "tracking"

    def trucks(self) -> List[TruckPosition]:
        raise NotImplementedError

    def find(self, query: str) -> TruckPosition:
        """A truck by exact or case-insensitive name, id, or unique prefix of its name."""
        trucks = self.trucks()
        q = query.strip().lower()
        exact = [t for t in trucks if t.name.lower() == q or t.id.lower() == q]
        if exact:
            return exact[0]
        partial = [t for t in trucks if t.name.lower().startswith(q)]
        if len(partial) == 1:
            return partial[0]
        names = ", ".join(sorted(t.name for t in trucks)) or "none"
        if partial:
            raise TrackingError(f"{query!r} matches several trucks: {', '.join(t.name for t in partial)}")
        raise TrackingError(f"no truck named {query!r} with a known position. Trucks: {names}")


def _parse_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class TraccarProvider(TrackingProvider):
    name = "Traccar"

    def __init__(self, url: str, token: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None, session=None):
        if not url:
            raise TrackingError("no Traccar server configured")
        if not token and not (user and password):
            raise TrackingError("Traccar needs ROUTEKIT_TRACCAR_TOKEN, or ROUTEKIT_TRACCAR_USER and "
                                "ROUTEKIT_TRACCAR_PASSWORD")
        self.url = url.rstrip("/")
        if token:
            self._auth = f"Bearer {token}"
        else:
            self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.session = session

    @classmethod
    def from_env(cls, session=None) -> "TraccarProvider":
        return cls(os.environ.get("ROUTEKIT_TRACCAR_URL", ""), os.environ.get("ROUTEKIT_TRACCAR_TOKEN"),
                   os.environ.get("ROUTEKIT_TRACCAR_USER"), os.environ.get("ROUTEKIT_TRACCAR_PASSWORD"), session)

    def _get(self, path: str):
        try:
            data = request_json("GET", f"{self.url}{path}", session=self.session, interval=0,
                                headers={"Authorization": self._auth, "Accept": "application/json"})
        except HttpError as e:
            if e.status in (401, 403):
                raise TrackingError("Traccar rejected the credentials (check ROUTEKIT_TRACCAR_TOKEN or "
                                    "ROUTEKIT_TRACCAR_USER/PASSWORD)") from None
            raise TrackingError(f"couldn't reach Traccar: {e}") from None
        if isinstance(data, dict) and data.get("_http_status", 200) in (401, 403):
            raise TrackingError("Traccar rejected the credentials (check ROUTEKIT_TRACCAR_TOKEN or "
                                "ROUTEKIT_TRACCAR_USER/PASSWORD)")
        return data

    def trucks(self) -> List[TruckPosition]:
        devices = self._get("/api/devices")
        positions = self._get("/api/positions")
        if not isinstance(devices, list) or not isinstance(positions, list):
            raise TrackingError("unexpected response from Traccar")
        latest: Dict[int, dict] = {p["deviceId"]: p for p in positions}
        out = []
        for d in devices:
            p = latest.get(d["id"])
            if not p or d.get("disabled"):
                continue  # no position reported yet
            out.append(TruckPosition(
                id=str(d.get("uniqueId") or d["id"]), name=d.get("name") or str(d["id"]),
                lat=float(p["latitude"]), lon=float(p["longitude"]),
                speed_mph=float(p.get("speed") or 0) * KNOTS_TO_MPH,  # Traccar reports knots
                heading=p.get("course"), fix_time=_parse_time(p.get("fixTime")),
                status=d.get("status") or "unknown", address=p.get("address") or ""))
        return sorted(out, key=lambda t: t.name.lower())


def provider_from_env(session=None) -> TrackingProvider:
    if os.environ.get("ROUTEKIT_TRACCAR_URL"):
        return TraccarProvider.from_env(session)
    raise TrackingError("no tracking provider configured. Set ROUTEKIT_TRACCAR_URL and "
                        "ROUTEKIT_TRACCAR_TOKEN (or ROUTEKIT_TRACCAR_USER/PASSWORD)")


def compass(heading: Optional[float]) -> str:
    if heading is None:
        return ""
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((heading % 360) / 45 + 0.5) % 8]
