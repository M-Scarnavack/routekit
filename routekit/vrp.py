"""Multi-truck routing: capacities, delivery windows, ready times, package rules,
multiple trips per truck, and a limited number of drivers.

This is a vehicle routing problem with time windows (VRPTW), extended with
multi-trip vehicles and a driver limit. routekit solves it heuristically:

1. **Construction (feasible cheapest insertion).** Stops are taken tightest
   deadline first. Each one goes into whichever trip and position adds the
   fewest miles *without breaking any rule*. Grouped stops go in together.
2. **Local search.** Repeat until nothing improves: 2-opt within each trip,
   *relocate* (move one stop to a better spot on any trip), and *swap*
   (exchange two stops between trips). Every move is checked against every rule.

Rules a plan must satisfy:

- each trip's total ``demand`` <= truck ``capacity`` (a truck reloads between trips)
- every stop reached by its ``latest`` deadline (waiting is allowed if early)
- a trip leaves no earlier than the latest ``ready`` time of its load
- a truck's next trip leaves only after its previous trip is back (+ ``reload`` minutes)
- no more than ``drivers`` trucks are out at once (drivers can switch trucks at the depot)
- stops pinned with ``truck=N`` ride only truck N; stops sharing a ``group`` ride the same trip
- optionally, every truck is back by the end of the shift

**Slots.** Each truck has ``trips`` slots, so there are ``trucks × trips``
routes; slot ``r`` belongs to truck ``r // trips``. With one trip per truck and a
driver for every truck, routes don't interact and each is checked on its own.
Otherwise the whole day is simulated for every candidate move (see
:meth:`FleetProblem.schedule`): whenever a truck is loaded and a driver is free,
the next trip leaves, most urgent first.

Node 0 is the depot. Routes are lists of stop indices *without* the depot.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from routekit.stops import Stop

EPS = 1e-9
INF = float("inf")
Matrix = List[List[float]]


@dataclass
class Fleet:
    trucks: int = 1
    capacity: Optional[float] = None
    start: float = 8 * 60          # minutes after midnight
    end: Optional[float] = None     # trucks must be back by this time
    mph: float = 45.0
    service: float = 0.0            # default minutes at each stop
    trips: int = 1                  # max trips per truck
    drivers: Optional[int] = None   # None = one driver per truck
    reload: float = 0.0             # minutes at the depot between a truck's trips

    @property
    def driver_count(self) -> int:
        return self.trucks if self.drivers is None else self.drivers


@dataclass
class TripPlan:
    truck: int                      # 1-based
    stops: List[int]                # node indices, depot excluded
    miles: float
    load: float
    depart: float
    arrivals: List[float]           # arrival time at each stop
    finish: float                   # back at the depot
    trip: int = 1                   # 1-based trip number for this truck
    driver: Optional[int] = None    # 1-based, when drivers are limited


@dataclass
class FleetSolution:
    trips: List[TripPlan]
    unassigned: List[int]
    initial_miles: float
    miles: float
    moves: Dict[str, int] = field(default_factory=dict)
    initial_unassigned: int = 0     # stops the initial plan couldn't place

    @property
    def used(self) -> List[TripPlan]:
        return [t for t in self.trips if t.stops]

    @property
    def trucks_used(self) -> int:
        return len({t.truck for t in self.used})

    @property
    def trip_count(self) -> int:
        return len(self.used)

    @property
    def drivers_needed(self) -> int:
        """Most trucks out at the same moment."""
        events = []
        for t in self.used:
            events += [(t.depart, 1), (t.finish, -1)]
        out = peak = 0
        for _, delta in sorted(events, key=lambda e: (e[0], e[1])):  # returns before departures
            out += delta
            peak = max(peak, out)
        return peak


class FleetProblem:
    def __init__(self, d: Matrix, stops: Sequence[Stop], fleet: Fleet):
        """`stops[0]` is the depot; `d` is the miles matrix over the same indices."""
        if fleet.trucks < 1 or fleet.trips < 1:
            raise ValueError("need at least one truck and one trip per truck")
        if fleet.drivers is not None and fleet.drivers < 1:
            raise ValueError("need at least one driver")
        self.d = d
        self.n = len(stops)
        self.fleet = fleet
        self.slots = fleet.trucks * fleet.trips
        self.coupled = fleet.trips > 1 or fleet.driver_count < fleet.trucks
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

    def truck_of(self, slot: int) -> int:
        """0-based truck index for a route slot."""
        return slot // self.fleet.trips

    # -- evaluating a single trip ----------------------------------------

    def miles(self, route: Sequence[int]) -> float:
        if not route:
            return 0.0
        d = self.d
        total = d[0][route[0]] + d[route[-1]][0]
        for a, b in zip(route, route[1:]):
            total += d[a][b]
        return total

    def _static_ok(self, route: Sequence[int], truck: int) -> bool:
        """Rules that don't depend on when the trip leaves: truck pins and capacity."""
        load = 0.0
        for n in route:
            pin = self.pin[n]
            if pin is not None and pin != truck + 1:
                return False
            load += self.demand[n]
        cap = self.fleet.capacity
        return cap is None or load <= cap + EPS

    def ready_time(self, route: Sequence[int]) -> float:
        t = self.fleet.start
        for n in route:
            r = self.ready[n]
            if r is not None and r > t:
                t = r
        return t

    def run(self, route: Sequence[int], truck: int, depart: float, trip: int = 1,
            driver: Optional[int] = None) -> Optional[TripPlan]:
        """Drive `route` leaving at `depart`. None if a deadline or the shift end is missed."""
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
        if self.fleet.end is not None and route and t > self.fleet.end + EPS:
            return None
        load = sum(self.demand[n] for n in route)
        return TripPlan(truck + 1, list(route), self.miles(route), load, depart, arrivals, t, trip, driver)

    def plan(self, route: Sequence[int], truck: int) -> Optional[TripPlan]:
        """Schedule one trip on `truck` (0-based), leaving as soon as its load is ready."""
        if not self._static_ok(route, truck):
            return None
        return self.run(route, truck, self.ready_time(route))

    def feasible(self, route: Sequence[int], truck: int) -> bool:
        return self.plan(route, truck) is not None

    # -- evaluating the whole day ------------------------------------------

    def schedule(self, routes: Sequence[Sequence[int]]) -> Optional[List[TripPlan]]:
        """Simulate every trip of every truck through the day. None if any rule breaks.

        Returns one TripPlan per slot (empty slots get an empty plan).
        """
        f = self.fleet
        for slot, route in enumerate(routes):
            if route and not self._static_ok(route, self.truck_of(slot)):
                return None

        # Each truck's non-empty trips, in slot order.
        queues: List[List[int]] = [[] for _ in range(f.trucks)]
        for slot, route in enumerate(routes):
            if route:
                queues[self.truck_of(slot)].append(slot)
        truck_free = [f.start] * f.trucks
        drivers = [(f.start, i) for i in range(f.driver_count)]
        heapq.heapify(drivers)
        plans: List[Optional[TripPlan]] = [None] * len(routes)
        trip_no = [0] * f.trucks
        remaining = sum(len(q) for q in queues)

        while remaining:
            # The next trip out: whichever truck's next load can leave soonest, most urgent first.
            best = None
            for k, q in enumerate(queues):
                if not q:
                    continue
                route = routes[q[0]]
                can_leave = max(truck_free[k], self.ready_time(route))
                urgency = min((self.late[n] for n in route if self.late[n] is not None), default=INF)
                key = (can_leave, urgency, k)
                if best is None or key < best[0]:
                    best = (key, k)
            (can_leave, _, _), k = best
            slot = queues[k].pop(0)
            free_at, driver = heapq.heappop(drivers)
            depart = max(can_leave, free_at)
            trip_no[k] += 1
            p = self.run(routes[slot], k, depart, trip_no[k],
                         driver + 1 if f.driver_count < f.trucks else None)
            if p is None:
                return None
            plans[slot] = p
            truck_free[k] = p.finish + f.reload
            heapq.heappush(drivers, (p.finish, driver))
            remaining -= 1

        for slot, route in enumerate(routes):
            if plans[slot] is None:
                plans[slot] = TripPlan(self.truck_of(slot) + 1, [], 0.0, 0.0, f.start, [], f.start, 0, None)
        return plans  # type: ignore[return-value]

    def _ok(self, routes: List[List[int]], changed: Sequence[int]) -> bool:
        """Is the plan still legal after changing the routes in `changed`?"""
        if self.coupled:
            return self.schedule(routes) is not None
        return all(self.feasible(routes[s], self.truck_of(s)) for s in changed)

    # -- construction -----------------------------------------------------

    def _best_insert(self, routes: List[List[int]], slot: int, node: int):
        """Cheapest legal position for `node` in `routes[slot]`: (added_miles, position) or None."""
        d = self.d
        route = routes[slot]
        best = None
        seq = [0] + route + [0]
        original = route
        for pos in range(len(route) + 1):
            a, b = seq[pos], seq[pos + 1]
            added = d[a][node] + d[node][b] - d[a][b]
            if best is not None and added >= best[0] - EPS:
                continue
            routes[slot] = route[:pos] + [node] + route[pos:]
            if self._ok(routes, (slot,)):
                best = (added, pos)
            routes[slot] = original
        return best

    def _insert_unit(self, routes: List[List[int]], unit: List[int], slot: int) -> Optional[List[int]]:
        original = routes[slot]
        for node in unit:
            ins = self._best_insert(routes, slot, node)
            if ins is None:
                routes[slot] = original
                return None
            routes[slot] = routes[slot][:ins[1]] + [node] + routes[slot][ins[1]:]
        result = routes[slot]
        routes[slot] = original
        return result

    def _candidate_slots(self, routes: List[List[int]]) -> List[int]:
        """Every slot with stops, plus each truck's first empty slot (no gaps in a truck's trips)."""
        out = []
        for k in range(self.fleet.trucks):
            base = k * self.fleet.trips
            for j in range(self.fleet.trips):
                out.append(base + j)
                if not routes[base + j]:
                    break
        return out

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
            return (min(deadlines) if deadlines else INF,
                    0 if pinned else 1,
                    -len(unit),
                    -max(self.d[0][i] for i in unit))

        routes: List[List[int]] = [[] for _ in range(self.slots)]
        unassigned: List[int] = []
        for unit in sorted(units, key=urgency):
            best = None
            for slot in self._candidate_slots(routes):
                r = self._insert_unit(routes, unit, slot)
                if r is None:
                    continue
                cost = self.miles(r) - self.miles(routes[slot])
                if best is None or cost < best[0] - EPS:
                    best = (cost, slot, r)
            if best is None:
                unassigned.extend(unit)
            else:
                routes[best[1]] = best[2]
        return routes, unassigned

    # -- local search -----------------------------------------------------

    def _two_opt(self, routes: List[List[int]], slot: int) -> bool:
        d = self.d
        improved_any = False
        improved = True
        while improved:
            improved = False
            seq = [0] + routes[slot] + [0]
            for i in range(1, len(seq) - 2):
                for j in range(i + 1, len(seq) - 1):
                    a, b, c, e = seq[i - 1], seq[i], seq[j], seq[j + 1]
                    if d[a][c] + d[b][e] - d[a][b] - d[c][e] < -EPS:
                        before = routes[slot]
                        routes[slot] = seq[1:i] + seq[i:j + 1][::-1] + seq[j + 1:-1]
                        if self._ok(routes, (slot,)):
                            improved = improved_any = True
                            break
                        routes[slot] = before
                if improved:
                    break
        return improved_any

    def _compact(self, routes: List[List[int]]) -> None:
        """Keep each truck's trips contiguous: no empty slot before a used one."""
        m = self.fleet.trips
        for k in range(self.fleet.trucks):
            block = routes[k * m:(k + 1) * m]
            used = [r for r in block if r]
            routes[k * m:(k + 1) * m] = used + [[] for _ in range(m - len(used))]

    def _relocate(self, routes: List[List[int]]) -> bool:
        """Move one ungrouped stop to wherever it saves the most miles."""
        for a in range(len(routes)):
            for idx, node in enumerate(routes[a]):
                if node in self.group_of:
                    continue
                original = routes[a]
                without = original[:idx] + original[idx + 1:]
                saved = self.miles(original) - self.miles(without)
                routes[a] = without
                if self.coupled and self.schedule(routes) is None:
                    routes[a] = original  # removing a stop changed the day's timing; skip
                    continue
                best = None
                for b in self._candidate_slots(routes):
                    ins = self._best_insert(routes, b, node)
                    if ins is not None and (best is None or ins[0] < best[0]):
                        best = (ins[0], b, ins[1])
                if best and best[0] < saved - 1e-6:
                    _, b, pos = best
                    routes[b] = routes[b][:pos] + [node] + routes[b][pos:]
                    self._compact(routes)
                    return True
                routes[a] = original
        return False

    def _swap(self, routes: List[List[int]]) -> bool:
        """Exchange two ungrouped stops on different trips if it saves miles."""
        k = len(routes)
        for a in range(k):
            for b in range(a + 1, k):
                ra, rb = routes[a], routes[b]
                if not ra or not rb:
                    continue
                base = self.miles(ra) + self.miles(rb)
                for i, x in enumerate(ra):
                    if x in self.group_of:
                        continue
                    for j, y in enumerate(rb):
                        if y in self.group_of:
                            continue
                        na = ra[:i] + [y] + ra[i + 1:]
                        nb = rb[:j] + [x] + rb[j + 1:]
                        if self.miles(na) + self.miles(nb) < base - 1e-6:
                            routes[a], routes[b] = na, nb
                            if self._ok(routes, (a, b)):
                                return True
                            routes[a], routes[b] = ra, rb
        return False

    def improve(self, routes: List[List[int]], max_rounds: int = 500) -> Dict[str, int]:
        moves = {"2-opt": 0, "relocate": 0, "swap": 0}
        use_swap = self.n <= 200  # swap is O(n²) evaluations per round
        for _ in range(max_rounds):
            changed = False
            for s in range(len(routes)):
                if routes[s] and self._two_opt(routes, s):
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
            for s in self._candidate_slots(routes):
                ins = self._best_insert(routes, s, node)
                if ins is not None and (best is None or ins[0] < best[0]):
                    best = (ins[0], s, ins[1])
            if best:
                routes[best[1]].insert(best[2], node)
            else:
                still.append(node)
        return still

    def finalize(self, routes: List[List[int]], unassigned: List[int], initial: float,
                 moves: Optional[Dict[str, int]] = None) -> FleetSolution:
        self._compact(routes)
        plans = self.schedule(routes) if self.coupled else [
            self.plan(r, self.truck_of(s)) if r else
            TripPlan(self.truck_of(s) + 1, [], 0.0, 0.0, self.fleet.start, [], self.fleet.start, 0)
            for s, r in enumerate(routes)]
        assert plans is not None and all(p is not None for p in plans), "solver produced an illegal plan"
        if not self.coupled:  # one trip per truck: number them
            for p in plans:
                p.trip = 1 if p.stops else 0
        return FleetSolution(plans, sorted(unassigned), initial, sum(p.miles for p in plans), moves or {})

    def solve(self, optimize: bool = True) -> FleetSolution:
        routes, unassigned = self.construct()
        if unassigned:
            unassigned = self.retry_unassigned(routes, unassigned)
        initial = sum(self.miles(r) for r in routes)
        initial_unassigned = len(unassigned)
        moves = self.improve(routes) if optimize else {}
        if unassigned and optimize:
            # Local search can free up room; delivering a stop beats saving miles.
            unassigned = self.retry_unassigned(routes, unassigned)
        sol = self.finalize(routes, unassigned, initial, moves)
        sol.initial_unassigned = initial_unassigned
        return sol


def solve_fleet(d: Matrix, stops: Sequence[Stop], fleet: Fleet) -> FleetSolution:
    return FleetProblem(d, stops, fleet).solve()
