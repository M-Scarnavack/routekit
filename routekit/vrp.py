"""Multi-truck routing: capacities, delivery windows, ready times, and package rules.

This is a vehicle routing problem with time windows (VRPTW). routekit solves it
heuristically in two phases:

1. **Construction (feasible cheapest insertion).** Stops are taken tightest
   deadline first. Each one goes into whichever truck and position adds the
   fewest miles *without breaking any rule*. Grouped stops are inserted together
   into the same truck.
2. **Local search.** Repeat until nothing improves: 2-opt within each truck,
   *relocate* (move one stop to a better spot on another truck), and *swap*
   (exchange two stops between trucks). Every move is checked against every rule.

Rules a route must satisfy:

- total ``demand`` <= truck ``capacity``
- every stop reached by its ``latest`` deadline (waiting is allowed if early)
- a truck leaves the depot no earlier than the latest ``ready`` time of its load
- stops pinned with ``truck=N`` ride only truck N
- stops sharing a ``group`` ride the same truck
- optionally, every truck is back by the end of the shift

Node 0 is the depot. Routes are lists of stop indices *without* the depot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from routekit.stops import Stop

EPS = 1e-9
Matrix = List[List[float]]


@dataclass
class Fleet:
    trucks: int = 1
    capacity: Optional[float] = None
    start: float = 8 * 60          # minutes after midnight
    end: Optional[float] = None     # trucks must be back by this time
    mph: float = 45.0
    service: float = 0.0            # default minutes at each stop


@dataclass
class TripPlan:
    truck: int                      # 1-based
    stops: List[int]                # node indices, depot excluded
    miles: float
    load: float
    depart: float
    arrivals: List[float]           # arrival time at each stop
    finish: float                   # back at the depot


@dataclass
class FleetSolution:
    trips: List[TripPlan]
    unassigned: List[int]
    initial_miles: float
    miles: float
    moves: Dict[str, int] = field(default_factory=dict)

    @property
    def trucks_used(self) -> int:
        return sum(1 for t in self.trips if t.stops)


class FleetProblem:
    def __init__(self, d: Matrix, stops: Sequence[Stop], fleet: Fleet):
        """`stops[0]` is the depot; `d` is the miles matrix over the same indices."""
        self.d = d
        self.n = len(stops)
        self.fleet = fleet
        self.demand = [0.0] + [s.demand for s in stops[1:]]
        self.early = [None] + [s.earliest for s in stops[1:]]
        self.late = [None] + [s.latest for s in stops[1:]]
        self.ready = [None] + [s.ready for s in stops[1:]]
        self.service = [0.0] + [fleet.service if s.service is None else s.service for s in stops[1:]]
        self.pin = [None] + [s.truck for s in stops[1:]]
        groups: Dict[str, List[int]] = {}
        for i, s in enumerate(stops[1:], start=1):
            if s.group:
                groups.setdefault(s.group, []).append(i)
        self.group_of: Dict[int, List[int]] = {i: members for members in groups.values() for i in members}
        self.minutes_per_mile = 60.0 / fleet.mph

    # -- evaluation -------------------------------------------------------

    def miles(self, route: Sequence[int]) -> float:
        if not route:
            return 0.0
        d = self.d
        total = d[0][route[0]] + d[route[-1]][0]
        for a, b in zip(route, route[1:]):
            total += d[a][b]
        return total

    def plan(self, route: Sequence[int], truck: int) -> Optional[TripPlan]:
        """Schedule a route on truck `truck` (0-based). None if it breaks any rule."""
        f = self.fleet
        load = 0.0
        depart = f.start
        for n in route:
            pin = self.pin[n]
            if pin is not None and pin != truck + 1:
                return None
            load += self.demand[n]
            r = self.ready[n]
            if r is not None and r > depart:
                depart = r
        if f.capacity is not None and load > f.capacity + EPS:
            return None
        t, prev, arrivals = depart, 0, []
        d, mpm = self.d, self.minutes_per_mile
        for n in route:
            t += d[prev][n] * mpm
            e = self.early[n]
            if e is not None and t < e:
                t = e  # wait for the window to open
            late = self.late[n]
            if late is not None and t > late + EPS:
                return None
            arrivals.append(t)
            t += self.service[n]
            prev = n
        t += d[prev][0] * mpm
        if f.end is not None and route and t > f.end + EPS:
            return None
        return TripPlan(truck + 1, list(route), self.miles(route), load, depart, arrivals, t)

    def feasible(self, route: Sequence[int], truck: int) -> bool:
        return self.plan(route, truck) is not None

    # -- construction -----------------------------------------------------

    def _best_insert(self, route: List[int], node: int, truck: int):
        """Cheapest feasible position for `node` in `route`: (added_miles, position) or None."""
        d = self.d
        best = None
        seq = [0] + route + [0]
        for pos in range(len(route) + 1):
            a, b = seq[pos], seq[pos + 1]
            added = d[a][node] + d[node][b] - d[a][b]
            if best is not None and added >= best[0] - EPS:
                continue
            cand = route[:pos] + [node] + route[pos:]
            if self.feasible(cand, truck):
                best = (added, pos)
        return best

    def _insert_unit(self, route: List[int], unit: List[int], truck: int):
        r = list(route)
        for node in unit:
            ins = self._best_insert(r, node, truck)
            if ins is None:
                return None
            r.insert(ins[1], node)
        return r

    def construct(self) -> Tuple[List[List[int]], List[int]]:
        units: List[List[int]] = []
        seen = set()
        for i in range(1, self.n):
            if i in seen:
                continue
            unit = sorted(self.group_of.get(i, [i]))
            seen.update(unit)
            units.append(unit)

        def urgency(unit):
            deadlines = [self.late[i] for i in unit if self.late[i] is not None]
            pinned = any(self.pin[i] is not None for i in unit)
            return (min(deadlines) if deadlines else float("inf"),
                    0 if pinned else 1,
                    -len(unit),
                    -max(self.d[0][i] for i in unit))

        routes: List[List[int]] = [[] for _ in range(self.fleet.trucks)]
        unassigned: List[int] = []
        for unit in sorted(units, key=urgency):
            best = None
            for k in range(self.fleet.trucks):
                r = self._insert_unit(routes[k], unit, k)
                if r is None:
                    continue
                cost = self.miles(r) - self.miles(routes[k])
                if best is None or cost < best[0] - EPS:
                    best = (cost, k, r)
            if best is None:
                unassigned.extend(unit)
            else:
                routes[best[1]] = best[2]
        return routes, unassigned

    # -- local search -----------------------------------------------------

    def _two_opt(self, route: List[int], truck: int) -> Tuple[List[int], bool]:
        r, d = list(route), self.d
        improved_any = False
        improved = True
        while improved:
            improved = False
            seq = [0] + r + [0]
            for i in range(1, len(seq) - 2):
                for j in range(i + 1, len(seq) - 1):
                    a, b, c, e = seq[i - 1], seq[i], seq[j], seq[j + 1]
                    if d[a][c] + d[b][e] - d[a][b] - d[c][e] < -EPS:
                        cand = seq[1:i] + seq[i:j + 1][::-1] + seq[j + 1:-1]
                        if self.feasible(cand, truck):
                            r, improved, improved_any = cand, True, True
                            break
                if improved:
                    break
        return r, improved_any

    def _relocate(self, routes: List[List[int]]) -> bool:
        """Move one ungrouped stop to wherever it saves the most miles."""
        for a in range(len(routes)):
            for idx, node in enumerate(routes[a]):
                if node in self.group_of:
                    continue
                without = routes[a][:idx] + routes[a][idx + 1:]
                saved = self.miles(routes[a]) - self.miles(without)
                if without and not self.feasible(without, a):
                    continue  # removing a stop can't break rules today, but stay safe
                best = None
                for b in range(len(routes)):
                    base = without if b == a else routes[b]
                    ins = self._best_insert(base, node, b)
                    if ins is not None and (best is None or ins[0] < best[0]):
                        best = (ins[0], b, ins[1])
                if best and best[0] < saved - 1e-6:
                    _, b, pos = best
                    routes[a] = without
                    routes[b] = routes[b][:pos] + [node] + routes[b][pos:]
                    return True
        return False

    def _swap(self, routes: List[List[int]]) -> bool:
        """Exchange two ungrouped stops on different trucks if it saves miles."""
        k = len(routes)
        for a in range(k):
            for b in range(a + 1, k):
                ra, rb = routes[a], routes[b]
                base = self.miles(ra) + self.miles(rb)
                for i, x in enumerate(ra):
                    if x in self.group_of:
                        continue
                    for j, y in enumerate(rb):
                        if y in self.group_of:
                            continue
                        na = ra[:i] + [y] + ra[i + 1:]
                        nb = rb[:j] + [x] + rb[j + 1:]
                        if self.miles(na) + self.miles(nb) < base - 1e-6 and \
                                self.feasible(na, a) and self.feasible(nb, b):
                            routes[a], routes[b] = na, nb
                            return True
        return False

    def improve(self, routes: List[List[int]], max_rounds: int = 500) -> Dict[str, int]:
        moves = {"2-opt": 0, "relocate": 0, "swap": 0}
        use_swap = self.n <= 200  # swap is O(n²) evaluations per round
        for _ in range(max_rounds):
            changed = False
            for t in range(len(routes)):
                routes[t], did = self._two_opt(routes[t], t)
                if did:
                    moves["2-opt"] += 1
                    changed = True
            if self._relocate(routes):
                moves["relocate"] += 1
                continue
            if use_swap and self._swap(routes):
                moves["swap"] += 1
                continue
            if not changed:
                break
        return moves

    def retry_unassigned(self, routes: List[List[int]], unassigned: List[int]) -> List[int]:
        still = []
        for node in unassigned:
            if node in self.group_of:
                still.append(node)
                continue
            best = None
            for t in range(len(routes)):
                ins = self._best_insert(routes[t], node, t)
                if ins is not None and (best is None or ins[0] < best[0]):
                    best = (ins[0], t, ins[1])
            if best:
                routes[best[1]].insert(best[2], node)
            else:
                still.append(node)
        return still

    def solve(self) -> FleetSolution:
        routes, unassigned = self.construct()
        initial = sum(self.miles(r) for r in routes)
        moves = self.improve(routes)
        if unassigned:
            unassigned = self.retry_unassigned(routes, unassigned)
        trips = [self.plan(r, t) for t, r in enumerate(routes)]
        assert all(t is not None for t in trips), "local search produced an infeasible route"
        return FleetSolution(trips, sorted(unassigned), initial, sum(t.miles for t in trips), moves)


def solve_fleet(d: Matrix, stops: Sequence[Stop], fleet: Fleet) -> FleetSolution:
    return FleetProblem(d, stops, fleet).solve()
