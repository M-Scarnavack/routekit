import json
import re

from routekit import cli
from routekit.htmlmap import MapOverlay, MapStop, MapTrack, road_path, write_html


def _data(page: str) -> dict:
    return json.loads(re.search(r"const DATA = (\{.*?\});\n", page, re.S).group(1))


def test_write_html_embeds_tracks_and_escapes(tmp_path):
    out = tmp_path / "map.html"
    evil = 'Dock </script><script>alert("x")</script>'
    write_html(out, "Test <plan>", "summary & more", (41.88, -87.65), "Depot",
               [MapTrack("Truck 1", "#123456", [(41.88, -87.65), (41.9, -87.6)],
                         [MapStop(41.9, -87.6, "1", evil, ["123 Main St"], flagged=True)])],
               overlays=[MapOverlay("Alerts", "#d62828", {"type": "FeatureCollection", "features": []})])
    page = out.read_text()
    assert page.count("</script>") == 2  # only Leaflet's tag and ours: the stop name can't close it
    assert "<title>Test &lt;plan&gt;</title>" in page and "summary &amp; more" in page
    data = _data(page)
    stop = data["tracks"][0]["stops"][0]
    assert stop["title"] == evil and stop["flagged"] is True
    assert data["overlays"][0]["name"] == "Alerts"
    assert "leaflet@1.9.4" in page  # pinned version


def test_road_path_joins_legs():
    pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]
    assert road_path(pts, None) == pts
    path = road_path(pts, [[(0.5, 0.2)], [(1.5, 0.8)]])
    assert path == [(0.0, 0.0), (0.5, 0.2), (1.0, 1.0), (1.5, 0.8), (2.0, 0.0)]


def test_cli_html_single_route(tmp_path, capsys):
    out = tmp_path / "route.html"
    assert cli.main(["plan", "Shop: 41.884, -87.654", "A: 41.879, -87.637", "B: 41.947, -87.656",
                     "--no-map", "--html", str(out)]) == 0
    data = _data(out.read_text())
    assert data["depot"]["name"] == "Shop"
    assert [s["title"] for s in data["tracks"][0]["stops"]] in (["A", "B"], ["B", "A"])
    assert len(data["tracks"][0]["path"]) == 4  # depot → 2 stops → depot
    assert "Saved interactive map" in capsys.readouterr().out


def test_cli_html_fleet(tmp_path):
    out = tmp_path / "fleet.html"
    stops = ["Shop: 41.884, -87.654", "41.879, -87.637", "41.947, -87.656", "41.80, -87.60", "41.82, -87.70"]
    assert cli.main(["plan", *stops, "--trucks", "2", "--no-map", "--html", str(out)]) == 0
    data = _data(out.read_text())
    assert len(data["tracks"]) == 2
    assert sum(len(t["stops"]) for t in data["tracks"]) == 4
    assert all(any(d.startswith("Arrives") for d in s["details"]) for t in data["tracks"] for s in t["stops"])
