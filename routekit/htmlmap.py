"""Interactive HTML map export (``routekit plan ... --html map.html``).

Writes one self-contained HTML file: the plan's data is embedded as JSON, and
the page draws it with `Leaflet <https://leafletjs.com/>`_ on OpenStreetMap
tiles (both loaded from the web when the page is opened). Each truck gets its
own color and layer toggle, every stop has a popup, and optional overlays
(for example FEMA disaster areas and weather alerts) can be added.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

LatLon = Tuple[float, float]

# Same palette as the terminal map (routekit.mapview.TRUCK_STYLES), minus "bold".
TRUCK_COLORS = ("#1fb6e8", "#ff9f43", "#3fbf6a", "#ff6bcb", "#9b7ff0", "#e0b500", "#ff6b6b", "#2bb58a")
SINGLE_COLOR = "#1fb6e8"
ALERT_COLOR = "#d62828"

LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist"


@dataclass
class MapStop:
    lat: float
    lon: float
    label: str               # short text on the marker (stop number)
    title: str               # popup heading
    details: List[str] = field(default_factory=list)  # popup lines (plain text)
    flagged: bool = False    # highlight (e.g. inside a disaster area)


@dataclass
class MapTrack:
    name: str
    color: str
    path: List[LatLon]
    stops: List[MapStop]


@dataclass
class MapOverlay:
    """A GeoJSON FeatureCollection drawn under the routes, with a popup per feature."""
    name: str
    color: str
    geojson: dict


def straight_path(points: Sequence[LatLon]) -> List[LatLon]:
    return [tuple(p) for p in points]


def road_path(points: Sequence[LatLon], legs: Optional[Sequence[Sequence[LatLon]]]) -> List[LatLon]:
    """Stops joined by their road shapes (when known), else by straight lines."""
    if not legs:
        return straight_path(points)
    out: List[LatLon] = [tuple(points[0])]
    for k, leg in enumerate(legs):
        out.extend(tuple(p) for p in leg)
        out.append(tuple(points[k + 1]))
    return out


def write_html(path: Path, title: str, summary_text: str, depot: LatLon, depot_name: str,
               tracks: List[MapTrack], overlays: Optional[List[MapOverlay]] = None,
               footnote: str = "") -> None:
    data = {
        "title": title,
        "depot": {"lat": depot[0], "lon": depot[1], "name": depot_name},
        "tracks": [{
            "name": t.name, "color": t.color,
            "path": [[round(a, 6), round(b, 6)] for a, b in t.path],
            "stops": [{"lat": s.lat, "lon": s.lon, "label": s.label, "title": s.title,
                       "details": s.details, "flagged": s.flagged} for s in t.stops],
        } for t in tracks],
        "overlays": [{"name": o.name, "color": o.color, "geojson": o.geojson} for o in overlays or []],
    }
    # Keep "</script>" (or "<!--") in any stop name from ending the script block early.
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    page = TEMPLATE.replace("{{TITLE}}", html.escape(title)) \
                   .replace("{{SUMMARY}}", html.escape(summary_text)) \
                   .replace("{{FOOTNOTE}}", html.escape(footnote)) \
                   .replace("{{LEAFLET}}", LEAFLET) \
                   .replace("{{DATA}}", payload)
    Path(path).write_text(page, encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<link rel="stylesheet" href="{{LEAFLET}}/leaflet.css" crossorigin="">
<script src="{{LEAFLET}}/leaflet.js" crossorigin=""></script>
<style>
  :root { --bg: #ffffff; --fg: #1d1d1f; --muted: #6b6b70; --panel: rgba(255,255,255,.94); --border: #d8d8dc; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161618; --fg: #ececef; --muted: #9a9aa2; --panel: rgba(30,30,33,.94); --border: #3a3a3f; }
  }
  html, body { height: 100%; margin: 0; background: var(--bg); color: var(--fg);
               font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  #map { position: absolute; inset: 0; }
  .card { position: absolute; z-index: 1000; background: var(--panel); border: 1px solid var(--border);
          border-radius: 10px; box-shadow: 0 4px 18px rgba(0,0,0,.18); backdrop-filter: blur(6px); }
  #summary { top: 12px; left: 56px; max-width: min(460px, calc(100vw - 80px)); padding: 10px 14px; }
  #summary h1 { font-size: 15px; margin: 0 0 6px; }
  #summary pre { margin: 0; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; }
  #summary .foot { margin-top: 6px; color: var(--muted); font-size: 12px; }
  #summary button { float: right; border: 0; background: none; color: var(--muted); cursor: pointer; font-size: 13px; }
  #summary.collapsed pre, #summary.collapsed .foot { display: none; }
  .stop-pin { width: 22px; height: 22px; border-radius: 50%; color: #fff; font: 700 11px/22px sans-serif;
              text-align: center; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.4); }
  .stop-pin.flagged { outline: 3px solid #d62828; outline-offset: 1px; }
  .depot-pin { width: 18px; height: 18px; background: #7b3fa0; border: 2px solid #fff; border-radius: 3px;
               box-shadow: 0 1px 4px rgba(0,0,0,.4); }
  .leaflet-popup-content { margin: 10px 12px; }
  .leaflet-popup-content b { font-size: 14px; }
  .leaflet-popup-content .d { color: #444; margin-top: 2px; }
  .flag { color: #d62828; font-weight: 600; }
</style>
</head>
<body>
<div id="map"></div>
<div id="summary" class="card">
  <button onclick="this.parentElement.classList.toggle('collapsed')" title="Show or hide">▾</button>
  <h1>{{TITLE}}</h1>
  <pre>{{SUMMARY}}</pre>
  <div class="foot">{{FOOTNOTE}}</div>
</div>
<script>
const DATA = {{DATA}};
const map = L.map("map", { zoomControl: true });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · routekit'
}).addTo(map);

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

const layers = {};
const bounds = L.latLngBounds([[DATA.depot.lat, DATA.depot.lon]]);

for (const o of DATA.overlays) {
  const layer = L.geoJSON(o.geojson, {
    style: { color: o.color, weight: 2, fillColor: o.color, fillOpacity: 0.12 },
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 7, color: o.color, fillOpacity: 0.5 }),
    onEachFeature: (f, l) => { if (f.properties && f.properties.popup) l.bindPopup(f.properties.popup); }
  }).addTo(map);
  layers[`<span style="color:${o.color}">■</span> ${esc(o.name)}`] = layer;
}

for (const t of DATA.tracks) {
  const group = L.layerGroup().addTo(map);
  if (t.path.length > 1) {
    L.polyline(t.path, { color: "#fff", weight: 7, opacity: 0.85 }).addTo(group);
    L.polyline(t.path, { color: t.color, weight: 4, opacity: 0.95 }).addTo(group);
    t.path.forEach(p => bounds.extend(p));
  }
  for (const s of t.stops) {
    const icon = L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
      html: `<div class="stop-pin${s.flagged ? " flagged" : ""}" style="background:${t.color}">${esc(s.label)}</div>` });
    const body = s.details.map(d => `<div class="d${d.startsWith("⚠") ? " flag" : ""}">${esc(d)}</div>`).join("");
    L.marker([s.lat, s.lon], { icon, title: s.title }).bindPopup(`<b>${esc(s.title)}</b>${body}`).addTo(group);
    bounds.extend([s.lat, s.lon]);
  }
  layers[`<span style="color:${t.color}">■</span> ${esc(t.name)}`] = group;
}

L.marker([DATA.depot.lat, DATA.depot.lon], {
  icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: '<div class="depot-pin"></div>' }),
  title: DATA.depot.name, zIndexOffset: 1000
}).bindPopup(`<b>${esc(DATA.depot.name)}</b><div class="d">Depot: trucks start and end here</div>`).addTo(map);

if (Object.keys(layers).length > 1) L.control.layers(null, layers, { collapsed: false }).addTo(map);
map.fitBounds(bounds.pad(0.08));
</script>
</body>
</html>
"""
