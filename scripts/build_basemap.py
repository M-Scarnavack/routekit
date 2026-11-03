"""Build routekit/data/us_states.json from Natural Earth admin-1 boundaries.

Source (public domain): Natural Earth 1:50m Admin 1 – States, Provinces, v5.1.2
https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_admin_1_states_provinces.geojson

Usage:
    python scripts/build_basemap.py ne_50m_admin_1_states_provinces.geojson

Output format: {"source": ..., "states": {"IL": [[[lon, lat], ...], ...], ...}}
where each state maps to a list of rings (outer boundaries and holes alike;
routekit only draws outlines, so the distinction doesn't matter).
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "routekit" / "data" / "us_states.json"
PRECISION = 3  # ~100 m, far finer than a terminal cell


def rings(geometry):
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for poly in polys:
        yield from poly


def main(src: str) -> None:
    data = json.loads(Path(src).read_text())
    states = {}
    for feat in data["features"]:
        props = feat["properties"]
        if props.get("adm0_a3") != "USA":
            continue
        out_rings = []
        for ring in rings(feat["geometry"]):
            pts = []
            for lon, lat in ring:
                p = [round(lon, PRECISION), round(lat, PRECISION)]
                if not pts or pts[-1] != p:
                    pts.append(p)
            if len(pts) >= 3:
                out_rings.append(pts)
        states[props["postal"]] = out_rings
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "Natural Earth 1:50m Admin 1 v5.1.2 (public domain)",
        "states": dict(sorted(states.items())),
    }, separators=(",", ":")))
    n = sum(len(r) for rs in states.values() for r in rs)
    print(f"wrote {OUT} ({len(states)} states, {n} points, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
