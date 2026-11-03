import random

import pytest

from routekit.solver import distance_matrix, haversine_miles, nearest_neighbor, route_length, two_opt


def test_haversine_known_distance():
    # Chicago to New York is ~711 miles great-circle.
    assert haversine_miles(41.8781, -87.6298, 40.7128, -74.0060) == pytest.approx(711, abs=5)


def test_haversine_zero():
    assert haversine_miles(10, 20, 10, 20) == 0


def _random_points(n, seed):
    rng = random.Random(seed)
    return [(rng.uniform(30, 45), rng.uniform(-120, -75)) for _ in range(n)]


@pytest.mark.parametrize("round_trip", [True, False])
@pytest.mark.parametrize("seed", range(5))
def test_routes_visit_every_stop_once(round_trip, seed):
    d = distance_matrix(_random_points(25, seed))
    for route in (nearest_neighbor(d, round_trip), two_opt(nearest_neighbor(d, round_trip), d, round_trip)):
        assert route[0] == 0
        body = route[1:-1] if round_trip else route[1:]
        assert sorted(body) == list(range(1, 25))
        if round_trip:
            assert route[-1] == 0


@pytest.mark.parametrize("round_trip", [True, False])
@pytest.mark.parametrize("seed", range(10))
def test_two_opt_never_worse(round_trip, seed):
    d = distance_matrix(_random_points(40, seed))
    nn = nearest_neighbor(d, round_trip)
    assert route_length(two_opt(nn, d, round_trip), d) <= route_length(nn, d) + 1e-9


def test_two_opt_untangles_a_crossing():
    # Square corners visited in a crossing order: 0 -> 2 -> 1 -> 3 -> 0.
    pts = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    d = distance_matrix(pts)
    crossed = [0, 2, 1, 3, 0]
    fixed = two_opt(crossed, d, round_trip=True)
    assert route_length(fixed, d) < route_length(crossed, d)
    assert sorted(fixed[1:-1]) == [1, 2, 3]


def test_two_opt_improves_on_average():
    gains = []
    for seed in range(10):
        d = distance_matrix(_random_points(60, seed))
        nn = nearest_neighbor(d)
        gains.append(1 - route_length(two_opt(nn, d), d) / route_length(nn, d))
    assert sum(gains) / len(gains) > 0.03
