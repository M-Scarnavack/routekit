import pytest
import requests

from routekit import cli, http, roads
from routekit.roads import RoadError, decode_polyline, road_geometry, road_matrix, symmetric
from routekit.stops import Stop
from routekit.vrp import Fleet, FleetProblem


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))  # fresh cache per test
    monkeypatch.setattr(http, "MIN_INTERVAL", 0.0)        # no politeness delay in tests


def encode_polyline(points, precision=5):
    """Reference encoder (Google's algorithm) for building fake OSRM responses."""
    out, plat, plon, f = [], 0, 0, 10 ** precision
    for lat, lon in points:
        ilat, ilon = round(lat * f), round(lon * f)
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


STEP1 = [(38.5, -120.2), (40.7, -120.95)]
STEP2 = [(40.7, -120.95), (43.252, -126.453)]  # each OSRM step is encoded on its own


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class FakeOSRM:
    """Answers /table like OSRM: meters = 1000 × |i − j| + 7 if i > j (one-way streets)."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        from urllib.parse import parse_qsl, urlsplit
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query))
        url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        self.calls.append((url, params))
        coords = url.split("/driving/")[1].split(";")
        pts = [tuple(map(float, c.split(","))) for c in coords]  # (lon, lat)
        ids = [int(round(lon * 1000)) for lon, _ in pts]
        if "/table/" in url:
            src = [int(x) for x in params["sources"].split(";")]
            dst = [int(x) for x in params["destinations"].split(";")]
            dist = [[1000.0 * abs(ids[a] - ids[b]) + (7 if ids[a] > ids[b] else 0) for b in dst] for a in src]
            return FakeResponse({"code": "Ok", "distances": dist, "durations": [[x / 10 for x in r] for r in dist]})
        legs = [{"steps": [{"geometry": encode_polyline(STEP1)}, {"geometry": encode_polyline(STEP2)}]}
                for _ in pts[1:]]
        return FakeResponse({"code": "Ok", "routes": [{"legs": legs}]})

    def close(self):
        pass


def _pts(n):
    return [(41.0, i / 1000) for i in range(n)]  # lon encodes the index for FakeOSRM


def test_encode_decode_round_trip():
    pts = [(41.88445, -87.65452), (41.87892, -87.6366), (-33.5, 151.25)]
    assert decode_polyline(encode_polyline(pts)) == pytest.approx(pts)


def test_decode_polyline_reference_example():
    # Google's documented example
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == pytest.approx(
        [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])


def test_symmetric_averages_directions():
    assert symmetric([[0, 2], [4, 0]]) == [[0, 3], [3, 0]]


def test_road_matrix_converts_units_and_keeps_direction():
    fake = FakeOSRM()
    miles, minutes = road_matrix(_pts(3), "http://osrm.test", session=fake)
    assert miles[0][2] == pytest.approx(2000 / 1609.344)
    assert miles[2][0] == pytest.approx(2007 / 1609.344)  # one-way difference survives
    assert minutes[0][2] == pytest.approx(200 / 60)
    assert len(fake.calls) == 1


def test_road_matrix_splits_big_problems_into_blocks():
    fake = FakeOSRM()
    n = 130
    miles, _ = road_matrix(_pts(n), "http://osrm.test", session=fake)
    assert len(fake.calls) == 9  # 3 × 3 blocks of up to 50
    for url, params in fake.calls:
        assert len(url.split("/driving/")[1].split(";")) <= roads.TABLE_LIMIT
    assert miles[129][3] == pytest.approx((126000 + 7) / 1609.344)
    assert all(miles[i][i] == 0 for i in range(n))


def test_road_matrix_is_cached():
    fake = FakeOSRM()
    road_matrix(_pts(4), "http://osrm.test", session=fake)
    road_matrix(_pts(4), "http://osrm.test", session=fake)
    assert len(fake.calls) == 1


def test_road_geometry_splits_by_leg():
    legs = road_geometry(_pts(3), "http://osrm.test", session=FakeOSRM())
    assert len(legs) == 2
    assert legs[0][0] == pytest.approx((38.5, -120.2)) and legs[0][-1] == pytest.approx((43.252, -126.453))
    assert len(legs[0]) == 3  # second step's first point isn't duplicated


def test_osrm_error_message():
    class NoRoute(FakeOSRM):
        def get(self, url, **kw):
            return FakeResponse({"code": "NoRoute", "message": "Impossible route between points"})
    with pytest.raises(RoadError, match="Impossible route"):
        road_matrix(_pts(2), "http://osrm.test", session=NoRoute())


def test_old_ssl_without_curl_gets_a_helpful_message(monkeypatch):
    class OldSSL(FakeOSRM):
        def get(self, url, **kw):
            raise requests.exceptions.SSLError("handshake failure")
    monkeypatch.setattr(http.shutil, "which", lambda name: None)  # no curl to fall back on
    with pytest.raises(RoadError, match="TLS 1.3"):
        road_matrix(_pts(2), "https://osrm.test", session=OldSSL())


def test_old_ssl_falls_back_to_curl(monkeypatch):
    class OldSSL(FakeOSRM):
        def get(self, url, **kw):
            raise requests.exceptions.SSLError("handshake failure")
    seen = {}

    def fake_curl(method, url, body):
        seen["url"] = url
        return 200, '{"code": "Ok", "distances": [[0, 1609.344], [1609.344, 0]], "durations": [[0, 60], [60, 0]]}'
    monkeypatch.setattr(http, "_via_curl", fake_curl)
    miles, minutes = road_matrix(_pts(2), "https://osrm.test", session=OldSSL())
    assert miles[0][1] == pytest.approx(1.0) and minutes[0][1] == pytest.approx(1.0)
    assert seen["url"].startswith("https://osrm.test/table/v1/driving/")  # still HTTPS


def test_rate_limit_message():
    class Busy(FakeOSRM):
        def get(self, url, **kw):
            return FakeResponse({}, status=429)
    with pytest.raises(RoadError, match="rate-limiting"):
        road_matrix(_pts(2), "http://osrm.test", session=Busy())


def test_fleet_uses_real_drive_times():
    stops = [Stop("Depot", "", 41.0, -87.0), Stop("A", "", 41.0, -86.9)]
    d = [[0, 5], [5, 0]]
    minutes = [[0, 30], [25, 0]]  # 30 min out, 25 back: one-way streets
    p = FleetProblem(d, stops, Fleet(mph=60), minutes)
    plan = p.plan([1], 0)
    assert plan.arrivals == [8 * 60 + 30] and plan.finish == 8 * 60 + 55


def test_cli_roads(monkeypatch, capsys):
    calls = {}

    def fake_matrix(points, url, progress=None):
        calls["url"] = url
        n = len(points)
        return ([[abs(i - j) * 1.5 for j in range(n)] for i in range(n)],
                [[abs(i - j) * 4.0 for j in range(n)] for i in range(n)])

    monkeypatch.setattr(cli, "road_matrix", fake_matrix)
    monkeypatch.setattr(cli, "road_geometry", lambda pts, url: [[a, b] for a, b in zip(pts, pts[1:])])
    code = cli.main(["plan", "41.88, -87.65", "41.89, -87.64", "41.90, -87.63", "--roads",
                     "--osrm-url", "http://osrm.test", "--no-animate"])
    out = capsys.readouterr().out
    assert code == 0 and calls["url"] == "http://osrm.test"
    assert "road miles" in out and "on real roads" in out


def test_cli_roads_failure_is_clean(monkeypatch, capsys):
    def boom(*a, **k):
        raise RoadError("couldn't reach the OSRM server (offline)")
    monkeypatch.setattr(cli, "road_matrix", boom)
    assert cli.main(["plan", "41.88, -87.65", "41.89, -87.64", "--roads"]) == 1
    assert "Run without --roads" in capsys.readouterr().out
