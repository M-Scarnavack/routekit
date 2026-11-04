"""Live dispatcher dashboard: ``routekit watch``.

Serves a self-updating browser map of every tracked truck (from the same tracking
provider ``routekit trucks`` uses) and flags breakdowns.

What gets flagged:

* **critical**: driver pressed SOS; device reported fault / accident / tow / fuel
  leak / power cut; engine fault codes (OBD DTCs); or stopped longer than
  ``breakdown_min``.
* **warning**: stopped longer than ``stop_min``; no GPS for ``offline_min``; low
  battery, temperature, GPS jamming, or driver-fatigue alarms.

Alarm alerts *latch*: a tracker usually sends an alarm on a single position report,
so the alert stays up until a dispatcher acknowledges it. Condition alerts (stopped,
no signal) clear themselves when the condition ends.

The server listens on 127.0.0.1 only: the page shows drivers' live locations.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Callable, Dict, List, Optional

from routekit.tracking import TrackingError, TrackingProvider, TruckPosition

MOVING_MPH = 3

CRITICAL_ALARMS = {
    "sos": "SOS pressed by driver",
    "fault": "Vehicle fault reported",
    "accident": "Accident detected",
    "tow": "Truck is being towed",
    "fuelLeak": "Fuel leak detected",
    "powerCut": "Tracker power cut",
}
WARNING_ALARMS = {
    "lowBattery": "Low battery",
    "lowPower": "Low power",
    "temperature": "Temperature alarm",
    "highRpm": "High engine RPM",
    "gpsAntennaCut": "GPS antenna cut",
    "jamming": "GPS jamming",
    "fatigueDriving": "Driver fatigue",
}
STATE_RANK = {"breakdown": 0, "warning": 1, "offline": 2, "stopped": 3, "moving": 4}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Monitor:
    """Polls a tracking provider and keeps the alert list."""

    def __init__(self, provider: TrackingProvider, stop_min: float = 20, breakdown_min: float = 45,
                 offline_min: float = 10, cache_s: float = 5, clock: Optional[Callable[[], datetime]] = None):
        self.provider = provider
        self.stop_min, self.breakdown_min, self.offline_min = stop_min, breakdown_min, offline_min
        self.cache_s = cache_s
        self.clock = clock or getattr(provider, "now", None) or _utcnow
        self.lock = threading.Lock()
        self.last_moving: Dict[str, datetime] = {}
        self.alerts: Dict[str, dict] = {}
        self.raw: List[TruckPosition] = []
        self.snapshot: Optional[dict] = None
        self.fetched_at = 0.0
        self.error: Optional[str] = None

    # -- evaluation

    def evaluate(self, positions: List[TruckPosition], now: datetime):
        active = set()
        trucks = []
        for p in positions:
            attrs = p.attributes or {}
            age = (now - p.fix_time).total_seconds() / 60 if p.fix_time else None
            moving = p.speed_mph >= MOVING_MPH or attrs.get("motion") is True
            if p.fix_time is not None and (moving or p.id not in self.last_moving):
                self.last_moving[p.id] = p.fix_time if moving else now
            stopped = (None if moving or p.fix_time is None
                       else max(0.0, (now - self.last_moving[p.id]).total_seconds() / 60))
            offline = age is not None and age >= self.offline_min

            def flag(kind, severity, message, latch=False, p=p):
                aid = f"{p.id}:{kind}"
                active.add(aid)
                a = self.alerts.get(aid)
                if a is None:
                    self.alerts[aid] = {"id": aid, "truck_id": p.id, "truck": p.name, "kind": kind,
                                        "severity": severity, "message": message, "latch": latch,
                                        "since": now.isoformat(), "acked": False}
                else:
                    a["message"] = message

            alarm = attrs.get("alarm")
            for name in str(alarm).split(",") if alarm else []:
                if name in CRITICAL_ALARMS:
                    flag("alarm-" + name, "critical", CRITICAL_ALARMS[name], latch=True)
                elif name in WARNING_ALARMS:
                    flag("alarm-" + name, "warning", WARNING_ALARMS[name], latch=True)
            if attrs.get("dtcs"):
                flag("dtc", "critical", f"Engine fault code {attrs['dtcs']}")
            if offline:
                flag("offline", "warning", f"No GPS signal for {age:.0f} min")
            elif stopped is not None and stopped >= self.breakdown_min:
                flag("long-stop", "critical", f"Possible breakdown: stopped {stopped:.0f} min")
            elif stopped is not None and stopped >= self.stop_min:
                flag("stop", "warning", f"Stopped {stopped:.0f} min")

            odo = attrs.get("odometer")
            trucks.append({
                "id": p.id, "name": p.name, "phone": p.phone, "lat": p.lat, "lon": p.lon,
                "speed_mph": round(p.speed_mph, 1), "heading": p.heading, "address": p.address,
                "fix_time": p.fix_time.isoformat() if p.fix_time else None,
                "age_min": round(age, 1) if age is not None else None,
                "stopped_min": round(stopped, 1) if stopped is not None else None,
                "moving": moving, "offline": offline,
                "ignition": attrs.get("ignition"), "battery": attrs.get("batteryLevel"),
                "fuel": attrs.get("fuel"),
                "odometer_mi": round(odo / 1609.34) if isinstance(odo, (int, float)) else None,
            })

        for aid in list(self.alerts):
            a = self.alerts[aid]
            if aid not in active and not (a["latch"] and not a["acked"]):
                del self.alerts[aid]

        sev_rank = {"critical": 0, "warning": 1}
        alerts = sorted(self.alerts.values(), key=lambda a: (a["acked"], sev_rank[a["severity"]], a["since"]))
        for t in trucks:
            mine = [a for a in alerts if a["truck_id"] == t["id"]]
            sev = [a["severity"] for a in mine if not a["acked"]] or [a["severity"] for a in mine]
            t["alerts"] = [a["id"] for a in mine]
            t["state"] = ("breakdown" if "critical" in sev else "warning" if "warning" in sev
                          else "offline" if t["offline"] else "moving" if t["moving"] else "stopped")
        trucks.sort(key=lambda t: (STATE_RANK[t["state"]], t["name"].lower()))
        return trucks, alerts

    # -- public

    def get(self) -> dict:
        with self.lock:
            if self.snapshot is None or time.time() - self.fetched_at >= self.cache_s:
                try:
                    self.raw = self.provider.trucks()
                    self.error = None
                except TrackingError as e:
                    self.error = str(e)
                except Exception as e:  # keep the dashboard up; show the problem on the page
                    self.error = f"error reading {self.provider.name}: {e}"
                now = self.clock()
                trucks, alerts = self.evaluate(self.raw, now)
                self.snapshot = {"source": self.provider.name, "now": now.isoformat(), "error": self.error,
                                 "trucks": trucks, "alerts": alerts,
                                 "thresholds": {"stop_min": self.stop_min, "breakdown_min": self.breakdown_min,
                                                "offline_min": self.offline_min}}
                self.fetched_at = time.time()
            return self.snapshot

    def ack(self, alert_id: str) -> bool:
        with self.lock:
            a = self.alerts.get(alert_id)
            if not a:
                return False
            a["acked"] = True
            self.fetched_at = 0.0
            return True


# ---------------------------------------------------------------- demo fleet

def _miles(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


class DemoProvider(TrackingProvider):
    """Five simulated trucks around Chicago on a 20x clock, so a scripted day (a
    delivery stop, a breakdown, a dead zone, an SOS) plays out in a few minutes."""

    name = "Demo (simulated trucks)"
    SPEEDUP = 20
    CENTER = (41.88, -87.68)
    # name, scripted events in simulated minutes
    FLEET = [
        ("Truck 101", []),
        ("Truck 102", [("breakdown", 15)]),
        ("Truck 103", [("signal_lost", 20, 50)]),
        ("Truck 104", [("stop", 10, 35)]),
        ("Truck 105", [("sos", 70)]),
    ]

    def __init__(self, seed: int = 7):
        rnd = random.Random(seed)
        self.t0 = time.time()
        self.start = _utcnow()
        self._trucks = []
        for i, (name, events) in enumerate(self.FLEET):
            pts = []
            for _ in range(6):
                ang, r = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.03, 0.16)
                pts.append((self.CENTER[0] + r * math.sin(ang), self.CENTER[1] + 1.3 * r * math.cos(ang)))
            self._trucks.append({"id": str(101 + i), "name": name, "route": pts, "mph": rnd.uniform(28, 42),
                                 "events": events, "dist": rnd.uniform(0, 5), "last_t": 0.0, "frozen": None})

    def sim_minutes(self) -> float:
        return (time.time() - self.t0) * self.SPEEDUP / 60

    def now(self) -> datetime:
        return self.start + timedelta(minutes=self.sim_minutes())

    @staticmethod
    def _point_at(route, dist):
        legs = [(route[i], route[(i + 1) % len(route)]) for i in range(len(route))]
        dist %= sum(_miles(a, b) for a, b in legs)
        for a, b in legs:
            leg = _miles(a, b)
            if dist <= leg:
                f = dist / leg if leg else 0
                heading = (math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) + 360) % 360
                return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, heading
            dist -= leg
        return route[0][0], route[0][1], 0.0

    def trucks(self) -> List[TruckPosition]:
        m, now = self.sim_minutes(), self.now()
        out = []
        for t in self._trucks:
            attrs, moving, silent = {"ignition": True}, True, False
            for ev in t["events"]:
                kind, at = ev[0], ev[1]
                if kind == "stop" and at <= m < ev[2]:
                    moving = False
                elif kind == "breakdown" and m >= at:
                    moving = False
                    attrs["dtcs"] = "P0217"  # engine over-temperature
                    if m < at + 2:
                        attrs["alarm"] = "fault"
                elif kind == "sos" and at <= m < at + 2:
                    attrs["alarm"] = "sos"
                elif kind == "signal_lost" and at <= m < ev[2]:
                    silent = True
            if moving:
                t["dist"] += t["mph"] * (m - t["last_t"]) / 60
            t["last_t"] = m
            if silent and t["frozen"] is not None:
                out.append(t["frozen"])
                continue
            lat, lon, heading = self._point_at(t["route"], t["dist"])
            pos = TruckPosition(id=t["id"], name=t["name"], lat=lat, lon=lon,
                                speed_mph=t["mph"] if moving else 0.0, heading=heading, fix_time=now,
                                status="online", attributes=attrs)
            t["frozen"] = pos if silent else None
            out.append(pos)
        return sorted(out, key=lambda p: p.name.lower())


# ---------------------------------------------------------------- web server

def dashboard_html() -> bytes:
    return resources.files("routekit").joinpath("data/watch.html").read_bytes()


def make_server(monitor: Monitor, host: str = "127.0.0.1", port: int = 8765, poll_s: float = 15
                ) -> ThreadingHTTPServer:
    page = dashboard_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path == "/api/fleet":
                self._json(200, dict(monitor.get(), poll_s=poll_s))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/ack":
                return self._send(404, b"not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                aid = json.loads(self.rfile.read(n) or b"{}").get("id", "")
            except (ValueError, AttributeError):
                return self._json(400, {"ok": False})
            self._json(200, {"ok": monitor.ack(str(aid))})

    return ThreadingHTTPServer((host, port), Handler)
