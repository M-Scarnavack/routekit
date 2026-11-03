import json

import pytest

from routekit import api, cli
from routekit.freight import (FreightDataError, data, fmt_tons, fmt_value, lanes, origins, resolve_commodity,
                              resolve_state, state_centers, state_codes)


def test_bundled_data_is_sane():
    d = data()
    assert d["years"] == list(range(2018, 2025))
    total = sum(v[-1] for v in d["out_tons"].values())
    assert 10e6 < total < 15e6  # 10–15 billion tons of truck freight a year (thousand-ton units)
    assert len(d["states"]) == 51 and len(d["commodities"]) == 42


def test_resolve_state_and_commodity():
    assert resolve_state("IL") == resolve_state("illinois") == resolve_state("17") == "17"
    assert resolve_commodity("7") == "07" and resolve_commodity("foodstuffs") == "07"
    with pytest.raises(FreightDataError, match="several"):
        resolve_commodity("prods")
    with pytest.raises(FreightDataError):
        resolve_state("Atlantis")


def test_origins_rank_and_filters_agree():
    rows = origins(top=5)
    assert rows[0].tons >= rows[1].tons >= rows[4].tons
    inter = origins(interstate=True, top=60)
    allf = {r.state: r.tons for r in origins(top=60)}
    assert all(r.tons < allf[r.state] for r in inter)  # interstate is a subset
    assert abs(sum(r.share for r in inter) - 1) < 1e-9


def test_lanes_from_and_to_a_state():
    il = lanes(origin="17", top=5)
    assert all(r.origin == "17" and r.dest != "17" for r in il)
    assert il[0].tons >= il[-1].tons and il[0].value
    into_tx = lanes(dest="48", top=3)
    assert all(r.dest == "48" for r in into_tx)
    food = lanes(origin="06", commodity="07", top=3)
    assert food and all(r.value is None for r in food)


def test_state_helpers_cover_every_state():
    assert set(state_codes()) >= set(data()["states"])
    lat, lon = state_centers()["17"]
    assert 37 < lat < 43 and -92 < lon < -87  # Illinois


def test_formatting():
    assert fmt_tons(1_708_300) == "1.71B tons" and fmt_tons(140_400) == "140.4M tons" and fmt_tons(8.5) == "8.5K tons" and fmt_tons(0.4) == "400 tons"
    assert fmt_value(83_323) == "$83.3B" and fmt_value(None) == ""


def test_cli_freight_json(capsys):
    assert cli.main(["freight", "--from", "IL", "--top", "3", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [l["from"] for l in out["lanes"]] == ["Illinois (IL)"] * 3


def test_cli_freight_html(tmp_path):
    out = tmp_path / "f.html"
    assert cli.main(["freight", "--interstate", "--top", "5", "--html", str(out)]) == 0
    page = out.read_text()
    assert "Tons originating" in page and "Top lanes" in page and '"view":' in page


def test_api_freight_demand():
    r = api.freight_demand(commodity="machinery", interstate=True, top=3)
    assert r["ok"] and r["commodity"] == "Machinery" and len(r["origins"]) == 3
    assert api.freight_demand(origin="Atlantis")["ok"] is False
