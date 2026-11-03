"""Route construction (nearest neighbor) and improvement (2-opt).

Routes are lists of indices into a distance matrix. Index 0 is the depot and
is always first. For a round trip the route also ends at 0.
"""

from __future__ import annotations

import math
from typing import List, Sequence

Matrix = List[List[float]]

EARTH_RADIUS_MI = 3958.7613
EPS = 1e-9


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle ("as the crow flies") distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(a)))


def distance_matrix(points: Sequence[Sequence[float]]) -> Matrix:
    n = len(points)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_miles(points[i][0], points[i][1], points[j][0], points[j][1])
            m[i][j] = m[j][i] = d
    return m


def route_length(route: Sequence[int], d: Matrix) -> float:
    return sum(d[a][b] for a, b in zip(route, route[1:]))


def nearest_neighbor(d: Matrix, round_trip: bool = True) -> List[int]:
    """Greedy tour: from the depot, always drive to the closest unvisited stop. O(n^2)."""
    n = len(d)
    unvisited = set(range(1, n))
    route = [0]
    while unvisited:
        here = route[-1]
        nxt = min(unvisited, key=lambda j: (d[here][j], j))
        route.append(nxt)
        unvisited.remove(nxt)
    if round_trip:
        route.append(0)
    return route


def two_opt(route: List[int], d: Matrix, round_trip: bool = True, max_passes: int = 1000) -> List[int]:
    """Improve a route by reversing segments until no reversal shortens it.

    Each reversal removes two edges and reconnects the route the other way,
    which untangles any place where the route crosses itself. The depot stays
    first; for a round trip it also stays last. For a one-way route the end
    point is free to change. Never returns a longer route than it was given.
    """
    r = list(route)
    n = len(r)
    last = n - 1 if round_trip else n  # exclusive upper bound for segment end
    for _ in range(max_passes):
        improved = False
        for i in range(1, n - 1):
            a, b = r[i - 1], r[i]
            for j in range(i + 1, last + 1):
                c = r[j - 1]
                if j < n:
                    e = r[j]
                    delta = d[a][c] + d[b][e] - d[a][b] - d[c][e]
                else:  # reversing the open tail of a one-way route
                    delta = d[a][c] - d[a][b]
                if delta < -EPS:
                    r[i:j] = reversed(r[i:j])
                    b = r[i]
                    improved = True
        if not improved:
            break
    return r
