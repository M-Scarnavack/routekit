import pytest

from routekit import cli, http
from routekit.roads import RoadError
from routekit.truck import MATRIX_BLOCK, Vehicle, truck_matrix, truck_route
from tests.test_roads import encode_polyline


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(http, "MIN_INTERVAL", 0.0)


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeValhalla:
    """Points are (41.0, i/1000); distance i→j = |i−j| miles, +0.5 if i > j; 2 min per mile."""

    def __init__(self, exclude_fails=False):
        self.calls = []
        self.exclude_fails = exclude_fails

    @staticmethod
    def _i(loc):
        return int(round(loc["lon"] * 1000))

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        if url.endswith("/sources_to_targets"):
            rows = [[{"distance": abs(self._i(s) - self._i(t)) + (0.5 if self._i(s) > self._i(t) else 0),
                      "time": 120 * abs(self._i(s) - self._i(t))} for t in json["targets"]] for s in json["sources"]]
            return Resp({"sources_to_targets": rows})
        opts = json["costing_options"][json["costing"]]
        if self.exclude_fails and opts.get("exclude_tolls"):
            return Resp({"error": "No path could be found for input", "error_code": 442}, 400)
        legs = []
        for a, b in zip(json["locations"], json["locations"][1:]):
            ia, ib = self._i(a), self._i(b)
            tolled = not opts.get("exclude_tolls") and opts.get("use_tolls", 1) > 0 and ib % 2 == 0
            legs.append({"shape": encode_polyline([(a["lat"], a["lon"]), (b["lat"], b["lon"])], 6),
                         "summary": {"length": abs(ia - ib) + (1 if not tolled else 0), "time": 60 * abs(ia - ib)},
                         "maneuvers": [{"length": 0.4, "toll": tolled}, {"length": 0.6, "toll": tolled}]})
        return Resp({"trip": {"legs": legs}})


def _pts(n):
    return [(41.0, i / 1000) for i in range(n)]


def test_vehicle_options_convert_units():
    v = Vehicle(truck=True, height_ft=13.5, weight_tons=40, length_ft=53, hazmat=True)
    o = v.costing_options()["truck"]
    assert o["height"] == pytest.approx(4.11, abs=0.01)
    assert o["weight"] == pytest.approx(36.29, abs=0.01)
    assert o["length"] == pytest.approx(16.15, abs=0.01)
    assert o["hazmat"] is True
    assert v.costing == "truck"
    assert Vehicle().costing == "auto"


def test_toll_avoidance_is_soft_in_matrix_hard_in_routes():
    v = Vehicle(truck=True, avoid_tolls=True)
    assert v.costing_options(matrix=True) == {"truck": {"use_tolls": 0.0}}
    assert v.costing_options() == {"truck": {"exclude_tolls": True}}
    assert v.costing_options(avoid_tolls=False) == {"truck": {}}


def test_matrix_blocks_respect_100_pairs():
    fake = FakeValhalla()
    n = 23
    miles, minutes = truck_matrix(_pts(n), Vehicle(truck=True), "http://v.test", session=fake)
    assert len(fake.calls) == 9  # 3 × 3 blocks
    for _, body in fake.calls:
        assert len(body["sources"]) * len(body["targets"]) <= MATRIX_BLOCK ** 2
    assert miles[20][3] == pytest.approx(17.5) and miles[3][20] == pytest.approx(17)
    assert minutes[3][20] == pytest.approx(34)
    assert miles[5][5] == 0


def test_matrix_missing_pair_explains_truck_limits():
    class NoRoad(FakeValhalla):
        def post(self, url, json=None, **kw):
            r = super().post(url, json=json, **kw)
            r._d["sources_to_targets"][0][1] = {"distance": None, "time": None}
            return r
    with pytest.raises(RoadError, match="height/weight/length"):
        truck_matrix(_pts(3), Vehicle(truck=True), "http://v.test", session=NoRoad())


def test_route_reports_toll_miles_per_leg_and_chunks_waypoints():
    fake = FakeValhalla()
    legs = truck_route(_pts(14), Vehicle(truck=True), "http://v.test", session=fake)
    assert len(legs) == 13
    assert len(fake.calls) == 2  # 10 waypoints, then the remaining 5 (sharing one)
    assert [round(l.toll_miles, 3) for l in legs[:3]] == [0.0, 1.0, 0.0]  # legs into even points are tolled
    assert legs[1].tolled and not legs[0].tolled
    assert legs[0].shape[0] == pytest.approx((41.0, 0.0)) and legs[0].shape[-1] == pytest.approx((41.0, 0.001))


def test_route_avoiding_tolls_falls_back_when_no_toll_free_road():
    fake = FakeValhalla(exclude_fails=True)
    legs = truck_route(_pts(3), Vehicle(truck=True, avoid_tolls=True), "http://v.test", session=fake)
    assert len(legs) == 2
    opts = [body["costing_options"]["truck"] for _, body in fake.calls]
    assert opts == [{"exclude_tolls": True}, {"use_tolls": 0.0}]


def test_cli_truck_options_imply_truck_and_report_tolls(monkeypatch, capsys):
    seen = {}

    def fake_matrix(points, vehicle, url, progress=None):
        seen["vehicle"] = vehicle
        n = len(points)
        return [[abs(i - j) * 2.0 for j in range(n)] for i in range(n)], [[abs(i - j) * 3.0 for j in range(n)] for i in range(n)]

    from routekit.truck import Leg

    def fake_route(points, vehicle, url, avoid_tolls=None):
        avoiding = vehicle.avoid_tolls if avoid_tolls is None else avoid_tolls
        return [Leg([a, b], 2.0 + (1.5 if avoiding else 0), 3.0 + (5 if avoiding else 0), 0.0 if avoiding else 0.8)
                for a, b in zip(points, points[1:])]

    monkeypatch.setattr(cli, "truck_matrix", fake_matrix)
    monkeypatch.setattr(cli, "truck_route", fake_route)
    stops = ["41.88, -87.65", "41.89, -87.64", "41.90, -87.63"]

    assert cli.main(["plan", *stops, "--height", "14", "--hazmat", "--no-map"]) == 0
    out = capsys.readouterr().out
    v = seen["vehicle"]
    assert v.truck and v.hazmat and v.height_ft == 14 and not v.avoid_tolls
    assert "on toll roads" in out and "Toll mi" in out and "Valhalla" in out

    assert cli.main(["plan", *stops, "--avoid-tolls", "--no-map"]) == 0
    out = capsys.readouterr().out
    assert not seen["vehicle"].truck and seen["vehicle"].avoid_tolls  # a car can avoid tolls too
    assert "avoided" in out and "+4.5 mi" in out  # 3 legs × 1.5 extra miles


def test_cli_fleet_toll_column(monkeypatch, capsys):
    from routekit.truck import Leg
    monkeypatch.setattr(cli, "truck_matrix", lambda pts, v, url, progress=None: (
        [[abs(i - j) for j in range(len(pts))] for i in range(len(pts))],
        [[abs(i - j) * 2.0 for j in range(len(pts))] for i in range(len(pts))]))
    monkeypatch.setattr(cli, "truck_route", lambda pts, v, url, avoid_tolls=None: [
        Leg([a, b], 1.0, 2.0, 0.25) for a, b in zip(pts, pts[1:])])
    assert cli.main(["plan", "41.88, -87.65", "41.89, -87.64", "41.90, -87.63", "41.91, -87.62", "41.92, -87.61",
                     "--trucks", "2", "--truck", "--no-map"]) == 0
    out = capsys.readouterr().out
    assert "Toll mi" in out and "0.8" in out  # a 2-stop trip has 3 legs × 0.25
