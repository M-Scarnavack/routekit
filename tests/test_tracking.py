import json
from datetime import datetime, timedelta, timezone

import pytest

from routekit import cli, http, tracking
from routekit.tracking import TraccarProvider, TrackingError, compass


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


DEVICES = [{"id": 1, "name": "Truck 1", "uniqueId": "abc1", "status": "online"},
           {"id": 2, "name": "Truck 2", "uniqueId": "abc2", "status": "offline"},
           {"id": 3, "name": "Spare", "uniqueId": "abc3", "status": "unknown"}]  # never reported


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeTraccar:
    def __init__(self, ok_auth="Bearer good-token"):
        self.ok_auth = ok_auth
        self.headers = []

    def get(self, url, timeout=None, headers=None):
        self.headers.append(headers)
        if headers.get("Authorization") != self.ok_auth:
            return Resp(None, 401)
        if url.endswith("/api/devices"):
            return Resp(DEVICES)
        if url.endswith("/api/positions"):
            return Resp([
                {"deviceId": 1, "latitude": 41.8781, "longitude": -87.6298, "speed": 34.0, "course": 90.0,
                 "fixTime": _iso(2), "address": "Chicago, IL"},
                {"deviceId": 2, "latitude": 41.602, "longitude": -87.3372, "speed": 0.0, "course": 0.0,
                 "fixTime": _iso(95)},
            ])
        raise AssertionError(url)


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(http, "MIN_INTERVAL", 0.0)


def test_traccar_positions_and_units():
    trucks = TraccarProvider("https://t.test", token="good-token", session=FakeTraccar()).trucks()
    assert [t.name for t in trucks] == ["Truck 1", "Truck 2"]  # "Spare" has no position yet
    t1, t2 = trucks
    assert t1.speed_mph == pytest.approx(34 * 1.150779) and t1.moving and compass(t1.heading) == "E"
    assert t1.address == "Chicago, IL" and 1.5 < t1.age_minutes < 3
    assert not t2.moving and t2.status == "offline" and t2.age_minutes > 90
    assert t1.to_dict()["speed_mph"] == 39.1


def test_basic_auth_header():
    fake = FakeTraccar(ok_auth="Basic ZGlzcEBleC5jb206cHc=")  # disp@ex.com:pw
    TraccarProvider("https://t.test", user="disp@ex.com", password="pw", session=fake).trucks()
    assert fake.headers[0]["Authorization"] == "Basic ZGlzcEBleC5jb206cHc="


def test_bad_credentials_message():
    with pytest.raises(TrackingError, match="rejected the credentials"):
        TraccarProvider("https://t.test", token="wrong", session=FakeTraccar()).trucks()


def test_missing_configuration(monkeypatch):
    for v in ("ROUTEKIT_TRACCAR_URL", "ROUTEKIT_TRACCAR_TOKEN", "ROUTEKIT_TRACCAR_USER", "ROUTEKIT_TRACCAR_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(TrackingError, match="ROUTEKIT_TRACCAR_URL"):
        tracking.provider_from_env()
    monkeypatch.setenv("ROUTEKIT_TRACCAR_URL", "https://t.test")
    with pytest.raises(TrackingError, match="TOKEN"):
        tracking.provider_from_env()


def test_find_truck():
    p = TraccarProvider("https://t.test", token="good-token", session=FakeTraccar())
    assert p.find("truck 1").name == "Truck 1"
    assert p.find("abc2").name == "Truck 2"
    with pytest.raises(TrackingError, match="several trucks"):
        p.find("truck")
    with pytest.raises(TrackingError, match="Trucks: Truck 1, Truck 2"):
        p.find("Spare")


def test_compass():
    assert [compass(h) for h in (0, 44, 46, 180, 359)] == ["N", "NE", "NE", "S", "N"]
    assert compass(None) == ""


@pytest.fixture
def fake_env(monkeypatch):
    fake = FakeTraccar()
    monkeypatch.setattr(cli, "provider_from_env",
                        lambda: TraccarProvider("https://t.test", token="good-token", session=fake))


def test_cli_trucks_json(fake_env, capsys):
    assert cli.main(["trucks", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["provider"] == "Traccar" and [t["name"] for t in data["trucks"]] == ["Truck 1", "Truck 2"]


def test_cli_trucks_table_and_html(fake_env, capsys, tmp_path):
    out = tmp_path / "trucks.html"
    assert cli.main(["trucks", "--html", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Truck 1" in text and "39 mph" in text and "stopped" in text and "1.6 h ago" in text
    assert "Truck 2" in out.read_text()


def test_cli_plan_from_truck(fake_env, capsys):
    assert cli.main(["plan", "41.60, -87.34", "41.52, -88.08", "--start-at-truck", "Truck 1", "--no-map"]) == 0
    out = capsys.readouterr().out
    assert "✓ Truck 1:" in out and "■ Truck 1 (current" in out and "one way" in out


def test_cli_plan_from_truck_rejects_depot(fake_env, capsys):
    assert cli.main(["plan", "41.60, -87.34", "--start-at-truck", "Truck 1", "--depot", "x"]) == 2
