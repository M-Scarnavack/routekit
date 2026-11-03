"""Braille-dot route maps with a state-outline basemap, stop labels, and animation frames.

Each terminal cell is a 2x4 grid of braille dots, so a 90x30 map has 180x120
"pixels". The map is built once as a :class:`MapScene`; :meth:`MapScene.frame`
renders it with only the first *n* dots of the route revealed, which is how the
animation draws the route leg by leg.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from rich.text import Text

from routekit.basemap import state_rings
from routekit.stops import Stop

# Bit for the braille dot at (x % 2, y % 4) within a cell.
_BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

BASE_STYLE = "#4a4a4a"
ROUTE_STYLE = "bold #3fd5ff"
HEAD_STYLE = "bold bright_white"
STOP_STYLE = "bold yellow"
DEPOT_STYLE = "bold magenta"
LABEL_STYLE = "bright_white"

MIN_BASEMAP_SPAN_DEG = 2.0  # below this (city scale) the 1:50m outlines are too coarse to be honest
MAX_LABELED_STOPS = 75

Dot = Tuple[int, int]


def bresenham(x0: int, y0: int, x1: int, y1: int) -> List[Dot]:
    """All dot coordinates on the line from (x0, y0) to (x1, y1), inclusive."""
    out = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        out.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


class Projection:
    """Equirectangular projection fitted to a set of points, preserving aspect ratio."""

    def __init__(self, latlons: Sequence[Tuple[float, float]], width: int, height: int, pad: Optional[int] = None):
        if pad is None:
            pad = max(3, int(min(width, height) * 0.05))  # ~5% margin so coastlines aren't clipped
        lats = [p[0] for p in latlons]
        lons = [p[1] for p in latlons]
        self.mid_cos = math.cos(math.radians((min(lats) + max(lats)) / 2))
        xs = [lon * self.mid_cos for lon in lons]
        self.min_x, self.max_y = min(xs), max(lats)
        span_x = max(max(xs) - self.min_x, 1e-6)
        span_y = max(self.max_y - min(lats), 1e-6)
        self.span_deg = max(span_x, span_y)
        self.scale = min((width - 1 - 2 * pad) / span_x, (height - 1 - 2 * pad) / span_y)
        self.off_x = (width - 1 - span_x * self.scale) / 2
        self.off_y = (height - 1 - span_y * self.scale) / 2

    def __call__(self, lat: float, lon: float) -> Dot:
        x = (lon * self.mid_cos - self.min_x) * self.scale + self.off_x
        y = (self.max_y - lat) * self.scale + self.off_y
        return int(round(x)), int(round(y))


def fit_rows(stops: Sequence[Stop], cols: int, min_rows: int = 8, max_rows: int = 30) -> int:
    """Pick a map height that matches the route's shape, so there's no dead space."""
    lats = [s.lat for s in stops]
    lons = [s.lon for s in stops]
    mid_cos = math.cos(math.radians((min(lats) + max(lats)) / 2))
    span_x = max((max(lons) - min(lons)) * mid_cos, 1e-6)
    span_y = max(max(lats) - min(lats), 1e-6)
    dots_high = (cols * 2) * span_y / span_x * 1.1 + 8  # + margin
    return int(min(max(math.ceil(dots_high / 4), min_rows), max_rows))


class MapScene:
    def __init__(self, ordered: List[Stop], cols: int = 80, rows: Optional[int] = None,
                 basemap: bool = True, labels: bool = True):
        self.cols = cols
        self.rows = rows or fit_rows(ordered, cols)
        self.width, self.height = self.cols * 2, self.rows * 4
        self.project = Projection([(s.lat, s.lon) for s in ordered], self.width, self.height)
        self.round_trip = len(ordered) > 2 and ordered[-1] is ordered[0]
        n_stops = len(ordered) - (1 if self.round_trip else 0)

        # Route: every dot along every leg, in driving order, plus where each stop is reached.
        pts = [self.project(s.lat, s.lon) for s in ordered]
        self.stop_cells: List[Tuple[int, int]] = [self._cell(p) for p in pts[:n_stops]]
        self.path: List[Dot] = [pts[0]]
        self.reached_at: List[int] = [0]  # index into path when stop i is reached
        for a, b in zip(pts, pts[1:]):
            self.path.extend(bresenham(*a, *b)[1:])
            self.reached_at.append(len(self.path) - 1)

        self.base_bits = self._basemap_bits() if basemap and self.project.span_deg >= MIN_BASEMAP_SPAN_DEG else None
        self.has_basemap = self.base_bits is not None
        self.labels = self._place_labels() if labels and n_stops - 1 <= MAX_LABELED_STOPS else {}

    @property
    def total_dots(self) -> int:
        return len(self.path)

    def _cell(self, dot: Dot) -> Tuple[int, int]:
        x, y = dot
        return min(max(y // 4, 0), self.rows - 1), min(max(x // 2, 0), self.cols - 1)

    def _basemap_bits(self) -> Optional[List[List[int]]]:
        rings = state_rings()
        if not rings:
            return None
        bits = [[0] * self.cols for _ in range(self.rows)]
        w, h = self.width, self.height
        any_drawn = False
        for ring in rings:
            prev = None
            for lon, lat in ring:
                p = self.project(lat, lon)
                if prev is not None:
                    (x0, y0), (x1, y1) = prev, p
                    # Cheap rejection of segments entirely off one side of the canvas.
                    if not ((x0 < 0 and x1 < 0) or (y0 < 0 and y1 < 0) or
                            (x0 >= w and x1 >= w) or (y0 >= h and y1 >= h)):
                        for x, y in bresenham(x0, y0, x1, y1):
                            if 0 <= x < w and 0 <= y < h:
                                bits[y // 4][x // 2] |= _BRAILLE_BITS[y % 4][x % 2]
                                any_drawn = True
                prev = p
        return bits if any_drawn else None

    def _place_labels(self) -> Dict[Tuple[int, int], str]:
        """Put each stop's order number beside it, wherever it doesn't collide."""
        taken = set(self.stop_cells)
        placed: Dict[Tuple[int, int], str] = {}
        for i, (r, c) in enumerate(self.stop_cells[1:], start=1):
            text = str(i)
            for start in (c + 1, c - len(text), None):
                if start is None:
                    break
                cells = [(r, start + k) for k in range(len(text))]
                if all(0 <= cc < self.cols and (rr, cc) not in taken for rr, cc in cells):
                    for k, cell in enumerate(cells):
                        placed[cell] = (text[k], i)
                        taken.add(cell)
                    break
        return placed

    def frame(self, revealed: Optional[int] = None) -> Text:
        """Render with the first `revealed` route dots drawn (None = the whole route)."""
        n = self.total_dots if revealed is None else max(1, min(revealed, self.total_dots))
        done = n >= self.total_dots

        route_bits = [[0] * self.cols for _ in range(self.rows)]
        w, h = self.width, self.height
        for x, y in self.path[:n]:
            if 0 <= x < w and 0 <= y < h:
                route_bits[y // 4][x // 2] |= _BRAILLE_BITS[y % 4][x % 2]

        marks: Dict[Tuple[int, int], Tuple[str, str]] = {}
        reached = sum(1 for at in self.reached_at[:len(self.stop_cells)] if at < n)
        if not done:
            marks[self._cell(self.path[n - 1])] = ("◆", HEAD_STYLE)
        for cell, (ch, stop_i) in self.labels.items():
            if stop_i < reached:
                marks[cell] = (ch, LABEL_STYLE)  # labels draw over the moving head
        for i in range(1, reached):
            marks[self.stop_cells[i]] = ("●", STOP_STYLE)  # stops draw over the moving head
        marks[self.stop_cells[0]] = ("■", DEPOT_STYLE)

        out = Text()
        for r in range(self.rows):
            run_style, run = None, []
            for c in range(self.cols):
                if (r, c) in marks:
                    ch, style = marks[(r, c)]
                else:
                    rb = route_bits[r][c]
                    bb = self.base_bits[r][c] if self.base_bits else 0
                    if rb:
                        ch, style = chr(0x2800 + (rb | bb)), ROUTE_STYLE
                    elif bb:
                        ch, style = chr(0x2800 + bb), BASE_STYLE
                    else:
                        ch, style = " ", None
                if style != run_style and run:
                    out.append("".join(run), style=run_style)
                    run = []
                run_style = style
                run.append(ch)
            if run:
                out.append("".join(run), style=run_style)
            if r < self.rows - 1:
                out.append("\n")
        return out

    def legend(self) -> Text:
        t = Text()
        t.append("■", style=DEPOT_STYLE).append(" depot   ")
        t.append("●", style=STOP_STYLE).append(" stop   ")
        if self.labels:
            t.append("12", style=LABEL_STYLE).append(" stop order   ")
        t.append("⠒⠒", style=ROUTE_STYLE).append(" route")
        if self.has_basemap:
            t.append("   ")
            t.append("⠒⠒", style=BASE_STYLE).append(" state lines")
        return t
