# OR-Tools route solver. Light on purpose: a pool child imports this module, so no camlib, Qt or app here.
import locale
import logging

import numpy as np

log = logging.getLogger('base')


def use_c_numeric_locale():
    """Sets LC_NUMERIC to "C" if the C locale has a decimal comma.

    Qt sets the C locale from the system on Unix. protobuf (in OR-Tools) then reads the default 0.5 of a parameter
    with a decimal comma and fails (issue 83). Each process that uses OR-Tools needs the reset: the app, a pool child
    and a test that made its own QApplication."""
    if locale.localeconv()['decimal_point'] != '.':
        locale.setlocale(locale.LC_NUMERIC, "C")

# above this many locations the distance matrix (n x n) is not built
MAX_MATRIX_POINTS = 2000

# integer arc costs: distances are multiplied by this before the rounding, so close holes do not get 0
DISTANCE_SCALE = 1000
# search time in seconds if the option is 0
DEFAULT_SEARCH_TIME = 3


def distance_matrix(locs, scale=DISTANCE_SCALE):
    """Euclidean distances of (x, y) tuples times scale, rounded to int; item [i][j] is the cost i -> j."""
    pts = np.asarray(locs, dtype=float).reshape(-1, 2)
    dx = pts[:, 0, None] - pts[None, :, 0]
    dy = pts[:, 1, None] - pts[None, :, 1]
    return np.rint(np.hypot(dx, dy) * scale).astype(np.int64).tolist()


def set_time_limit(search_parameters, opt_time):
    """Time limit in seconds (fractions kept); 0 or None selects DEFAULT_SEARCH_TIME."""
    seconds = float(opt_time) if opt_time else 0.0
    if seconds <= 0:
        seconds = DEFAULT_SEARCH_TIME
    search_parameters.time_limit.FromMilliseconds(int(seconds * 1000))


def greedy_route(locations, depot=0):
    """Nearest-neighbour order from the depot, numpy argmin over the remaining points; memory is O(n).

    Returns (route, objective): location indexes in route order and the path length."""
    pts = np.asarray(locations, dtype=float).reshape(-1, 2)
    count = len(pts)
    if count == 0:
        return [], 0.0
    remaining = np.ones(count, dtype=bool)
    route = [depot]
    remaining[depot] = False
    total = 0.0
    current = depot
    for _step in range(count - 1):
        dist = np.hypot(pts[:, 0] - pts[current, 0], pts[:, 1] - pts[current, 1])
        dist[~remaining] = np.inf
        current = int(np.argmin(dist))
        total += float(dist[current])
        remaining[current] = False
        route.append(current)
    return route, total


def solve_route(locations, depot=0, metaheuristic=False, opt_time=0):
    """Returns (route, objective): location indexes in route order and the cost / DISTANCE_SCALE, or (None, None).

    Above MAX_MATRIX_POINTS locations the n x n matrix would need too much memory: a greedy order is returned."""
    if len(locations) > MAX_MATRIX_POINTS:
        log.warning("%d locations: more than %d, the OR-Tools matrix is skipped, using a nearest-neighbour order." %
                    (len(locations), MAX_MATRIX_POINTS))
        return greedy_route(locations, depot)

    use_c_numeric_locale()
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    manager = pywrapcp.RoutingIndexManager(len(locations), 1, depot)
    routing = pywrapcp.RoutingModel(manager)
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    if metaheuristic:
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    else:
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    set_time_limit(search_parameters, opt_time)

    # the matrix is evaluated inside OR-Tools, without a Python call for each arc
    transit_index = routing.RegisterTransitMatrix(distance_matrix(locations))
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

    assignment = routing.SolveWithParameters(search_parameters)
    if not assignment:
        return None, None

    route = []
    node = routing.Start(0)
    while not routing.IsEnd(node):
        route.append(manager.IndexToNode(node))
        node = assignment.Value(routing.NextVar(node))
    return route, assignment.ObjectiveValue() / DISTANCE_SCALE
