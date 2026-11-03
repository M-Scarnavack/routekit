"""Bundled U.S. state outlines (Natural Earth 1:50m, public domain)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

DATA_FILE = Path(__file__).resolve().parent / "data" / "us_states.json"

Ring = List[Tuple[float, float]]  # (lon, lat) pairs


@lru_cache(maxsize=1)
def state_rings() -> List[Ring]:
    """Every state boundary ring as (lon, lat) points. Empty list if the data file is missing."""
    try:
        data = json.loads(DATA_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [[(lon, lat) for lon, lat in ring] for rings in data["states"].values() for ring in rings]
