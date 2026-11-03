"""Build routekit/data/freight_truck.json from the FAF5 state database.

Source (public domain): Freight Analysis Framework, version 5.7.1, state-level
origin-destination data, 2018-2024 (Bureau of Transportation Statistics / FHWA).
https://faf.ornl.gov/faf5/Data/Download_Files/FAF5.7.1_State_2018-2024.zip

Usage:
    python scripts/build_freight.py FAF5.7.1_State_2018-2024.csv

Keeps truck shipments only (dms_mode == 1), counting the domestic truck leg of
imports and exports too, since that's where a truck picks the freight up.
Output units: tons are thousand tons; value is million 2017 dollars.

Output: {
  "years": [2018..2024],
  "states": {"17": "Illinois", ...},
  "commodities": {"07": "Other foodstuffs", ...},
  "out_tons": {"17": [t2018..t2024]},  "in_tons": {...},
  "out_by_commodity": {"17": {"07": tons_latest, ...}},
  "lanes": {"17>48": [tons_first, tons_latest, value_latest]},            # all commodities
  "commodity_lanes": {"07": {"17>48": tons_latest, ...}}                  # >= MIN_LANE thousand tons
}
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "routekit" / "data" / "freight_truck.json"
YEARS = list(range(2018, 2025))
MIN_LANE = 5.0  # thousand tons; smaller commodity lanes are dropped to keep the file small

STATES = {"01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California", "08": "Colorado",
          "09": "Connecticut", "10": "Delaware", "11": "Washington DC", "12": "Florida", "13": "Georgia",
          "15": "Hawaii", "16": "Idaho", "17": "Illinois", "18": "Indiana", "19": "Iowa", "20": "Kansas",
          "21": "Kentucky", "22": "Louisiana", "23": "Maine", "24": "Maryland", "25": "Massachusetts",
          "26": "Michigan", "27": "Minnesota", "28": "Mississippi", "29": "Missouri", "30": "Montana",
          "31": "Nebraska", "32": "Nevada", "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico",
          "36": "New York", "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma",
          "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina", "46": "South Dakota",
          "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont", "51": "Virginia", "53": "Washington",
          "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming"}
COMMODITIES = {"01": "Live animals/fish", "02": "Cereal grains", "03": "Other ag prods.", "04": "Animal feed",
               "05": "Meat/seafood", "06": "Milled grain prods.", "07": "Other foodstuffs",
               "08": "Alcoholic beverages", "09": "Tobacco prods.", "10": "Building stone", "11": "Natural sands",
               "12": "Gravel", "13": "Nonmetallic minerals", "14": "Metallic ores", "15": "Coal",
               "16": "Crude petroleum", "17": "Gasoline", "18": "Fuel oils",
               "19": "Natural gas and other fossil products", "20": "Basic chemicals", "21": "Pharmaceuticals",
               "22": "Fertilizers", "23": "Chemical prods.", "24": "Plastics/rubber", "25": "Logs",
               "26": "Wood prods.", "27": "Newsprint/paper", "28": "Paper articles", "29": "Printed prods.",
               "30": "Textiles/leather", "31": "Nonmetal min. prods.", "32": "Base metals",
               "33": "Articles-base metal", "34": "Machinery", "35": "Electronics", "36": "Motorized vehicles",
               "37": "Transport equip.", "38": "Precision instruments", "39": "Furniture",
               "40": "Misc. mfg. prods.", "41": "Waste/scrap", "43": "Mixed freight"}


def main(src: str) -> None:
    out_tons = defaultdict(lambda: [0.0] * len(YEARS))
    in_tons = defaultdict(lambda: [0.0] * len(YEARS))
    out_by_com = defaultdict(lambda: defaultdict(float))
    lanes = defaultdict(lambda: [0.0, 0.0, 0.0])
    com_lanes = defaultdict(lambda: defaultdict(float))
    rows = 0
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            if r["dms_mode"] != "1":
                continue
            rows += 1
            o, d, c = r["dms_origst"].zfill(2), r["dms_destst"].zfill(2), r["sctg2"].zfill(2)
            tons = [float(r[f"tons_{y}"] or 0) for y in YEARS]
            for i, t in enumerate(tons):
                out_tons[o][i] += t
                in_tons[d][i] += t
            out_by_com[o][c] += tons[-1]
            lane = lanes[f"{o}>{d}"]
            lane[0] += tons[0]
            lane[1] += tons[-1]
            lane[2] += float(r[f"value_{YEARS[-1]}"] or 0)
            com_lanes[c][f"{o}>{d}"] += tons[-1]

    def r3(x):
        return round(x, 3)

    data = {
        "source": "FAF5.7.1 State Database 2018-2024 (BTS/FHWA), truck mode; public domain",
        "units": {"tons": "thousand tons", "value": "million 2017 dollars"},
        "years": YEARS,
        "states": STATES,
        "commodities": COMMODITIES,
        "out_tons": {k: [r3(x) for x in v] for k, v in sorted(out_tons.items())},
        "in_tons": {k: [r3(x) for x in v] for k, v in sorted(in_tons.items())},
        "out_by_commodity": {k: {c: r3(t) for c, t in sorted(v.items()) if t >= 0.5}
                             for k, v in sorted(out_by_com.items())},
        "lanes": {k: [r3(a), r3(b), r3(c)] for k, (a, b, c) in sorted(lanes.items()) if b >= 0.5},
        "commodity_lanes": {c: {k: r3(t) for k, t in sorted(v.items()) if t >= MIN_LANE}
                            for c, v in sorted(com_lanes.items())},
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    n_com = sum(len(v) for v in data["commodity_lanes"].values())
    print(f"{rows} truck rows -> {OUT} ({len(data['lanes'])} lanes, {n_com} commodity lanes, "
          f"{OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
