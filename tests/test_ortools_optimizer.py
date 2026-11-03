"""OR-Tools drill path optimization of CNCjob: integer distance matrix, time limit, route."""
import random
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("ortools")

from ortools.constraint_solver import pywrapcp  # noqa: E402

from appCommon.Common import GracefulException  # noqa: E402
from camlib import CNCjob  # noqa: E402


def make_cnc():
    # the optimizer methods use only self.app (log, abort_flag) and class members
    cnc = CNCjob.__new__(CNCjob)
    cnc.app = SimpleNamespace(log=Mock(), abort_flag=False)
    return cnc


def path_length(locs, route):
    return sum(((locs[a][0] - locs[b][0]) ** 2 + (locs[a][1] - locs[b][1]) ** 2) ** 0.5
               for a, b in zip(route, route[1:]))


# ---------------------------------------------------------------------------------------------------------------
# distance matrix
# ---------------------------------------------------------------------------------------------------------------

def test_distance_matrix_is_scaled_rounded_and_symmetric():
    locs = [(0.0, 0.0), (0.3, 0.4), (3.0, 4.0)]
    matrix = CNCjob.ortools_distance_matrix(locs)
    assert matrix == [[0, 500, 5000], [500, 0, 4500], [5000, 4500, 0]]
    assert all(type(v) is int for row in matrix for v in row)


# ---------------------------------------------------------------------------------------------------------------
# time limit
# ---------------------------------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('method', ['optimized_ortools_basic'])
def test_route_visits_every_location_once_from_the_first(method):
    random.seed(3)
    locs = [(random.uniform(0, 50), random.uniform(0, 50)) for _ in range(40)]
    route = getattr(make_cnc(), method)(locations=locs, opt_time=1)
    assert route[0] == 0
    assert sorted(route) == list(range(len(locs)))


@pytest.mark.parametrize('method', ['optimized_ortools_meta'])
def test_route_of_holes_less_than_1_mm_apart_is_not_random(method):
    # holes on a line, 0.3 mm apart, in shuffled order: the shortest open path goes along the line.
    # With distances truncated to whole units every arc cost 0 or 1 and the order was arbitrary.
    random.seed(5)
    xs = [i * 0.3 for i in range(30)]
    random.shuffle(xs)
    xs.remove(0.0)
    locs = [(0.0, 0.0)] + [(x, 0.0) for x in xs]
    route = getattr(make_cnc(), method)(locations=locs, opt_time=1)
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


# ---------------------------------------------------------------------------------------------------------------
# solve in the process pool: the OR-Tools search holds the GIL for its whole time limit
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def pool():
    from multiprocessing import get_context
    with get_context('spawn').Pool(1) as p:
        p.apply(time.sleep, (0,))     # the child is started before the timed part
        yield p


def _random_locs(count):
    random.seed(7)
    return [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(count)]


