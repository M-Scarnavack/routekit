import json

import pytest

from routekit import cli, http
from routekit.dat import DatClient, DatError, _find, place, to_stops


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(http, "MIN_INTERVAL", 0.0)


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeDAT:
    """Mimics DAT's published API shape: org token -> user token -> rates / search."""

    def __init__(self, good_password="pw"):
        self.good_password = good_password
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append(("POST", url, json, headers.get("Authorization")))
        if url.endswith("/access/v1/token/organization"):
            if json["password"] != self.good_password:
                return Resp({"message": "bad credentials"}, 401)
            return Resp({"accessToken": "ORG", "expiresWhen": "2099-01-01T00:00:00Z"})
        if url.endswith("/access/v1/token/user"):
            assert headers["Authorization"] == "Bearer ORG"
            return Resp({"accessToken": "USR", "expiresWhen": "2099-01-01T00:00:00Z"})
        assert headers["Authorization"] == "Bearer USR"
        if url.endswith("/linehaulrates/v1/lookups"):
            return Resp({"rateResponses": [{"response": {
                "rate": {"mileage": 716, "perMile": {"rateUsd": 2.31, "lowUsd": 2.02, "highUsd": 2.67},
                         "perTrip": {"rateUsd": 1654}, "averageFuelSurchargePerMileUsd": 0.58},
                "escalation": {"timeframe": "7_DAYS", "origin": {"name": "Chicago Mkt"},
                               "destination": {"name": "Atlanta Mkt"}}}}]})
        if url.endswith("/search/v3/queries"):
            return Resp({"queryId": "Q1"})
        raise AssertionError(url)

    def get(self, url, timeout=None, headers=None):
        self.calls.append(("GET", url, None, headers.get("Authorization")))
        assert "/search/v3/queryMatches/Q1" in url
        return Resp({"matches": [{
            "matchId": "M1", "origin": {"city": "Joliet", "stateProv": "IL", "latitude": 41.525, "longitude": -88.0817},
            "destination": {"city": "Dallas", "stateProv": "TX"}, "availability": {"earliestWhen": "2026-11-04T08:00:00Z"},
            "equipmentType": "V", "lengthFeet": 53, "weightPounds": 42000, "rateUsd": 2100, "tripLength": 925,
            "originDeadheadMiles": 38, "posterInfo": {"companyName": "Acme Logistics", "contact": {"phoneNumber": "555-0100"}}}]})


def _client(fake=None, password="pw"):
    return DatClient("svc@acme.com", password, "disp@acme.com", "https://id.test", "https://fr.test",
                     "https://an.test", session=fake or FakeDAT())


def test_place_parsing():
    assert place("Chicago, IL") == {"city": "Chicago", "stateProv": "IL"}
    assert place("60607") == {"postalCode": "60607"}
    with pytest.raises(DatError):
        place("Chicago")


def test_two_step_login_and_token_reuse():
    fake = FakeDAT()
    c = _client(fake)
    c.rate("Chicago, IL", "Atlanta, GA")
    c.rate("Chicago, IL", "Dallas, TX")
    logins = [u for m, u, *_ in fake.calls if "/access/" in u]
    assert logins == ["https://id.test/access/v1/token/organization", "https://id.test/access/v1/token/user"]


def test_rate_parsing():
    fake = FakeDAT()
    r = _client(fake).rate("Chicago, IL", "Atlanta, GA", "reefer", contract=True)
    assert (r.per_mile, r.low_per_mile, r.high_per_mile, r.per_trip, r.miles) == (2.31, 2.02, 2.67, 1654, 716)
    assert r.fuel_per_mile == 0.58 and r.timeframe == "7_DAYS" and r.escalation == "Chicago Mkt → Atlanta Mkt"
    body = [c for c in fake.calls if c[1].endswith("/lookups")][0][2][0]
    assert body["equipment"] == "REEFER" and body["rateType"] == "CONTRACT"
    assert body["origin"] == {"city": "Chicago", "stateOrProvince": "IL"}


def test_load_search_and_stops():
    fake = FakeDAT()
    loads = _client(fake).search_loads("Chicago, IL", radius=150, equip="van", pickup_from="2026-11-04")
    ld = loads[0]
    assert (ld.origin, ld.destination, ld.company, ld.phone) == ("Joliet, IL", "Dallas, TX", "Acme Logistics", "555-0100")
    assert ld.rate_per_mile == pytest.approx(2100 / 925) and ld.deadhead_miles == 38
    q = [c for c in fake.calls if c[1].endswith("/queries")][0][2]["criteria"]
    assert q["lane"]["equipment"]["types"] == ["V"] and q["lane"]["origin"]["area"]["radius"]["miles"] == 150
    assert to_stops(loads)[0]["lat"] == 41.525
    from routekit.dat import to_shipments
    row = to_shipments(loads)[0]
    assert row["pickup_lat"] == 41.525 and row["delivery"] == "Dallas, TX" and row["shipment"] == "M1"


def test_bad_credentials():
    with pytest.raises(DatError, match="rejected"):
        _client(password="wrong").check()


def test_missing_credentials(monkeypatch):
    for v in ("ROUTEKIT_DAT_SERVICE_USER", "ROUTEKIT_DAT_SERVICE_PASSWORD", "ROUTEKIT_DAT_USER"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(DatError, match="ROUTEKIT_DAT_USER"):
        DatClient.from_env()


def test_find_is_defensive():
    assert _find({"a": [{"b": {"rateUsd": 3}}]}, "rateUsd") == 3
    assert _find({"a": 1}, "missing") is None


def test_cli_dat_loads_json_and_stops(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(DatClient, "from_env", classmethod(lambda cls, session=None: _client()))
    stops = tmp_path / "loads.csv"
    assert cli.main(["dat", "loads", "--origin", "Chicago, IL", "--json", "--stops", str(stops)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["loads"][0]["company"] == "Acme Logistics"
    assert "\"Joliet, IL → Dallas, TX (Acme Logistics)\",M1," in stops.read_text()
    assert cli.main(["dat", "rate", "Chicago, IL", "Atlanta, GA"]) == 0
    assert "$2.31" in capsys.readouterr().out
