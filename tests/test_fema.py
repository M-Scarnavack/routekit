import csv
from datetime import date

import pytest

from routekit import cli, fema
from routekit.fema import Declaration, StopRisk, assess, open_declarations

DECLS = [
    {"disasterNumber": 4955, "declarationType": "DR", "incidentType": "Hurricane", "declarationTitle": "HURRICANE ISAIAS",
     "declarationDate": "2026-10-27T00:00:00.000Z", "state": "FL", "fipsStateCode": "12", "fipsCountyCode": "005",
     "designatedArea": "Bay (County)", "ihProgramDeclared": True, "paProgramDeclared": True},
    {"disasterNumber": 4955, "declarationType": "DR", "incidentType": "Hurricane", "declarationTitle": "HURRICANE ISAIAS",
     "declarationDate": "2026-10-27T00:00:00.000Z", "state": "FL", "fipsStateCode": "12", "fipsCountyCode": "033",
     "designatedArea": "Escambia (County)", "paProgramDeclared": True},
    {"disasterNumber": 3657, "declarationType": "EM", "incidentType": "Hurricane", "declarationTitle": "HURRICANE ISAIAS",
     "declarationDate": "2026-10-09T00:00:00.000Z", "state": "GA", "fipsStateCode": "13", "fipsCountyCode": "000",
     "designatedArea": "Statewide"},
]
ALERTS = [{"properties": {"event": "Flood Warning", "severity": "Severe", "headline": "Flood Warning for Escambia",
                          "geocode": {"SAME": ["012033"]}, "ends": None},
           "geometry": {"type": "Polygon", "coordinates": [[[-87.4, 30.4], [-87.2, 30.4], [-87.2, 30.6], [-87.4, 30.4]]]}}]
COUNTY_AT = {(30.16, -85.53): "12005", (30.61, -87.34): "12033", (30.44, -84.28): "12073", (33.75, -84.39): "13121"}


@pytest.fixture
def fake_gov(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls = []

    def fake_request(method, url, params=None, body=None, session=None, interval=None, headers=None):
        calls.append((url, params))
        if url == fema.FEMA_URL:
            rows = DECLS
            if "disasterNumber eq" in params["$filter"]:
                n = int(params["$filter"].split("eq")[1])
                rows = [r for r in DECLS if r["disasterNumber"] == n]
            return {"DisasterDeclarationsSummaries": rows}
        if url == fema.NWS_URL:
            return {"features": ALERTS}
        if url == fema.CENSUS_COUNTY_URL:
            key = (round(float(params["y"]), 2), round(float(params["x"]), 2))
            g = COUNTY_AT.get(key)
            return {"result": {"geographies": {"Counties": [{"GEOID": g}] if g else []}}}
        raise AssertionError(url)

    monkeypatch.setattr(fema, "request_json", fake_request)
    monkeypatch.setattr(fema, "_today", lambda: date(2026, 11, 2))
    return calls


def test_open_declarations_query_and_parse(fake_gov):
    decls = open_declarations(["fl", "GA"], days=120)
    url, params = fake_gov[0]
    assert "(state eq 'FL' or state eq 'GA')" in params["$filter"]
    assert "declarationDate ge '2026-07-05" in params["$filter"]
    assert "disasterCloseoutDate eq null" in params["$filter"]
    d = decls[0]
    assert (d.code, d.label, d.fips, d.programs) == ("DR-4955", "DR-4955 Hurricane Isaias", "12005", ["IH", "PA"])


def test_statewide_declaration_covers_every_county_in_state():
    d = Declaration(3657, "EM", "Hurricane", "X", date(2026, 10, 9), "GA", "13000", "Statewide")
    assert d.statewide and d.covers("13121") and not d.covers("12005")


def test_assess_flags_declared_counties_and_alerts(fake_gov):
    risks, decls, alerts = assess([(30.16, -85.53), (30.61, -87.34), (30.44, -84.28), (33.75, -84.39), (0.0, 0.0)])
    bay, escambia, leon, fulton, nowhere = risks
    assert bay.declared and not bay.alerts
    assert escambia.declared and [a.event for a in escambia.alerts] == ["Flood Warning"]
    assert not leon.flagged                                       # Leon wasn't declared in this data
    assert fulton.declared and fulton.declarations[0].code == "EM-3657"  # statewide GA emergency
    assert nowhere.county is None and not nowhere.flagged
    assert any("⚠ FEMA DR-4955" in n for n in escambia.notes())
    assert any("⚠ NWS Flood Warning (Severe)" == n for n in escambia.notes())


def test_county_lookups_are_cached(fake_gov):
    fema.counties_for([(30.16, -85.53)])
    fema.counties_for([(30.16, -85.53)])
    assert sum(1 for url, _ in fake_gov if url == fema.CENSUS_COUNTY_URL) == 1


def test_bundled_counties():
    c = fema.counties()
    assert len(c) > 3000
    assert c["12073"][:2] == ("Leon County", "FL")


def test_cli_plan_fema_priority(fake_gov, capsys):
    code = cli.main(["plan", "Depot: 30.44, -84.28", "Bay: 30.16, -85.53", "Pensacola: 30.61, -87.34",
                     "--fema", "--fema-within", "6", "--start", "6:00", "--no-map"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 of 2 stops in declared disaster areas" in out
    assert "due by 12:00 PM" in out
    assert "Disaster and weather flags" in out and "Flood Warning" in out


def test_cli_disasters_writes_relief_stops(fake_gov, tmp_path, capsys, monkeypatch):
    out = tmp_path / "relief.csv"
    import routekit.geocode as geocode
    monkeypatch.setattr(geocode, "geocode_one", lambda a, offline=False: (30.497, -84.246, "3300 CAPITAL CIR NE"))
    assert cli.main(["disasters", "4955", "--stops", str(out), "--depot", "3300 Capital Cir NE, Tallahassee, FL"]) == 0
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["name"] == "Staging area" and rows[0]["lat"] == "30.497000"
    assert [r["name"] for r in rows[1:]] == ["Bay County, FL", "Escambia County, FL"]
    assert all(r["note"] == "DR-4955" for r in rows[1:])
    assert "Saved 2 declared counties" in capsys.readouterr().out


def test_cli_disasters_none(fake_gov, capsys, monkeypatch):
    monkeypatch.setattr(fema, "open_declarations", lambda *a, **k: [])
    monkeypatch.setattr(cli, "open_declarations", lambda *a, **k: [])
    assert cli.main(["disasters", "--state", "VT"]) == 0
    assert "No open FEMA declarations in VT" in capsys.readouterr().out


def test_fema_html_overlay(fake_gov, tmp_path):
    out = tmp_path / "m.html"
    assert cli.main(["plan", "Depot: 30.44, -84.28", "Pensacola: 30.61, -87.34", "Bay: 30.16, -85.53",
                     "--fema", "--no-map", "--html", str(out)]) == 0
    page = out.read_text()
    assert "NWS weather alerts" in page and "FEMA-declared counties" in page
    assert '"flagged":true' in page
