"""OR-Tools drill paths: distances, complete routes, time limits and failure fallback."""

import random
import time
import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from camlib import CNCjob


# ==============================================================================================================
# test_ortools_optimizer: OR-Tools drill path optimization of CNCjob: integer distance matrix, time limit, route.
# ==============================================================================================================

pytest.importorskip("ortools")

def make_cnc():
    # the optimizer methods use only self.app (log, abort_flag) and class members
    cnc = CNCjob.__new__(CNCjob)
    cnc.app = SimpleNamespace(log=Mock(), abort_flag=False)
    return cnc

def path_length(locs, route):
    return sum(((locs[a][0] - locs[b][0]) ** 2 + (locs[a][1] - locs[b][1]) ** 2) ** 0.5
               for a, b in zip(route, route[1:]))

def test_route_visits_every_location_once_from_the_first():
    random.seed(3)
    locs = [(random.uniform(0, 50), random.uniform(0, 50)) for _ in range(40)]
    route = make_cnc().optimized_ortools_basic(locations=locs, opt_time=1)
    assert route[0] == 0
    assert sorted(route) == list(range(len(locs)))

def test_route_of_holes_less_than_1_mm_apart_is_not_random():
    # holes on a line, 0.3 mm apart, in shuffled order: the shortest open path goes along the line.
    # With distances truncated to whole units every arc cost 0 or 1 and the order was arbitrary.
    random.seed(5)
    xs = [i * 0.3 for i in range(30)]
    random.shuffle(xs)
    xs.remove(0.0)
    locs = [(0.0, 0.0)] + [(x, 0.0) for x in xs]
    route = make_cnc().optimized_ortools_meta(locations=locs, opt_time=1)
    assert path_length(locs, route) == pytest.approx(29 * 0.3, abs=1e-6)

def test_basic_stops_at_the_time_limit_on_a_panel():
    # 4 x 4 copies of a 32-hole board: 512 holes. Without a time limit the local search ran about 20 s.
    random.seed(1)
    board = [(random.uniform(0, 30), random.uniform(0, 20)) for _ in range(32)]
    locs = [(x + c * 32.0, y + r * 22.0) for r in range(4) for c in range(4) for (x, y) in board]
    t0 = time.perf_counter()
    route = make_cnc().optimized_ortools_basic(locations=locs, opt_time=1)
    elapsed = time.perf_counter() - t0
    assert sorted(route) == list(range(len(locs)))
    assert elapsed < 8.0

def test_solver_error_falls_back_to_the_greedy_route(monkeypatch):
    # e.g. protobuf fails to parse floats under a decimal-comma locale (issue 83): the job must still get a route
    from appCommon import RouteSolver

    def broken(*args, **kwargs):
        raise TypeError("Couldn't build proto file into descriptor pool")

    monkeypatch.setattr(RouteSolver, 'solve_route', broken)
    cnc = make_cnc()
    cnc.app.pool = None
    locs = [(0.0, 0.0), (10.0, 0.0), (1.0, 0.0), (5.0, 0.0)]
    route, objective = cnc._ortools_solve(locs, 0, True, 1)
    assert route == [0, 2, 3, 1]
    assert objective == pytest.approx(10.0)
    cnc.app.log.warning.assert_called()
