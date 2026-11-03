"""Build routekit/data/us_counties.json from the Census Gazetteer counties file.

Source (public domain): U.S. Census Bureau 2024 Gazetteer, counties
https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip

Usage:
    python scripts/build_counties.py 2024_Gaz_counties_national.txt

Output: {"source": ..., "counties": {"12073": ["Leon County", "FL", 30.459, -84.278], ...}}
Each point is the Census "internal point", which is inside the county (a good stand-in
for a county-level relief destination when no specific site is known).
"""

import csv
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "routekit" / "data" / "us_counties.json"


def main(src: str) -> None:
    counties = {}
    with open(src, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        col = {name: i for i, name in enumerate(header)}
        for row in reader:
            row = [c.strip() for c in row]
            counties[row[col["GEOID"]]] = [row[col["NAME"]], row[col["USPS"]],
                                           round(float(row[col["INTPTLAT"]]), 5),
                                           round(float(row[col["INTPTLONG"]]), 5)]
    OUT.write_text(json.dumps({"source": "U.S. Census Bureau 2024 Gazetteer (public domain)",
                               "counties": dict(sorted(counties.items()))}, separators=(",", ":")))
    print(f"wrote {OUT} ({len(counties)} counties, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
