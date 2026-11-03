import csv

import pytest

from routekit import geocode
from routekit.geocode import geocode_stops, parse_census_response
from routekit.stops import Stop, StopFileError, load_stops, save_route


def _write(tmp_path, text, name="stops.csv"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_load_single_address_column(tmp_path):
    stops = load_stops(_write(tmp_path, "Name,Address\nA,1 Main St Chicago IL\nB,2 Main St Chicago IL\n"))
    assert [s.name for s in stops] == ["A", "B"]
    assert stops[0].address == "1 Main St Chicago IL"
    assert not stops[0].located


def test_load_split_columns(tmp_path):
    stops = load_stops(_write(tmp_path, "street,city,state,zip\n1 Main St,Chicago,IL,60601\n2 Elm St,Evanston,IL,60201\n"))
    assert stops[0].address == "1 Main St, Chicago, IL 60601"
    assert stops[1].name == "Stop 2"


def test_load_lat_lon(tmp_path):
    stops = load_stops(_write(tmp_path, "name,lat,lon\nA,41.0,-87.0\nB,40.0,-86.0\n"))
    assert stops[0].located and stops[0].lat == 41.0


def test_load_rejects_missing_columns(tmp_path):
    with pytest.raises(StopFileError):
        load_stops(_write(tmp_path, "foo,bar\n1,2\n3,4\n"))


def test_load_rejects_single_stop(tmp_path):
    with pytest.raises(StopFileError):
        load_stops(_write(tmp_path, "address\n1 Main St\n"))


def test_parse_census_response():
    text = (
        '"0","233 S Wacker Dr, Chicago, IL","Match","Exact","233 S WACKER DR","-87.6366,41.8789","1","L"\n'
        '"1","nowhere","No_Match"\n'
    )
    res = parse_census_response(text)
    assert res["0"] == pytest.approx((41.8789, -87.6366))
    assert res["1"] is None


def test_geocode_uses_cache_and_skips_network(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls = []

    def fake_batch(addresses, session):
        calls.append(list(addresses))
        return {str(i): (41.0 + i, -87.0) for i in range(len(addresses))}

    monkeypatch.setattr(geocode, "_census_batch", fake_batch)
    stops = [Stop("A", "1 Main St"), Stop("B", "2 Main St")]
    assert geocode_stops(stops) == []
    assert len(calls) == 1 and all(s.located for s in stops)

    again = [Stop("A", "1 main st"), Stop("B", "2 MAIN ST")]
    assert geocode_stops(again, offline=True) == []
    assert len(calls) == 1  # served from cache


def test_save_route(tmp_path):
    a, b = Stop("A", "x", 41.0, -87.0), Stop("B", "y", 42.0, -88.0)
    out = tmp_path / "route.csv"
    save_route(out, [a, b, a], [10.0, 10.0])
    rows = list(csv.DictReader(out.open()))
    assert [r["name"] for r in rows] == ["A", "B", "A"]
    assert rows[-1]["cumulative_miles"] == "20.00"
