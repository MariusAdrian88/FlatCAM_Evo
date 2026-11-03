"""appCommon.PathOrder: the order of the paths of a G-code job (nearest neighbour walk + travel improvement)."""
import math
import random
import unittest

import numpy as np
from shapely.geometry import LineString, LinearRing, MultiLineString, Point

from appCommon.PathOrder import order_paths, walk_nearest, improve_order, path_end_points


def reference_walk(points, start):
    """O(n^2) walk: the nearest end, the lowest id on a tie, then on from the other end of that path."""
    left = set(range(len(points) // 2))
    order = []
    cx, cy = start
    while left:
        best = min(((points[2 * p + s][0] - cx) ** 2 + (points[2 * p + s][1] - cy) ** 2, 2 * p + s)
                   for p in left for s in (0, 1))[1]
        order.append(best)
        left.discard(best >> 1)
        cx, cy = points[best ^ 1]
    return order


def travel(points, entries, start=(0.0, 0.0)):
    cx, cy = start
    total = 0.0
    for e in entries:
        total += math.hypot(points[e][0] - cx, points[e][1] - cy)
        cx, cy = points[e ^ 1]
    return total


def random_points(n, seed, grid=False):
    rnd = random.Random(seed)
    if grid:
        return np.array([[(i % 15) * 1.0, (i // 15) * 1.0] for i in range(2 * n)], dtype=float)
    return np.array([[rnd.uniform(0, 100), rnd.uniform(0, 60)] for __ in range(2 * n)])


class TestWalk(unittest.TestCase):

    def test_same_as_reference_walk(self):
        # 400 paths: the grid is made again while the walk goes on
        for seed in range(3):
            pts = random_points(400, seed)
            self.assertEqual(walk_nearest(pts, (0.0, 0.0)), reference_walk(pts, (0.0, 0.0)))
        # other start, also outside the box
        pts = random_points(300, 7)
        for start in ((50.0, 30.0), (-20.0, 500.0)):
            self.assertEqual(walk_nearest(pts, start), reference_walk(pts, start))

    def test_degenerate_inputs(self):
        self.assertEqual(walk_nearest(np.zeros((0, 2)), (0, 0)), [])
        self.assertEqual(walk_nearest(np.array([[3.0, 4.0], [5.0, 6.0]]), (0, 0)), [0])
        self.assertEqual(walk_nearest(np.array([[3.0, 4.0], [5.0, 6.0]]), (6, 6)), [1])
        same = np.ones((8, 2))
        self.assertEqual(walk_nearest(same, (0, 0)), [0, 2, 4, 6])
        line = np.array([[float(i), 0.0] for i in (4, 5, 1, 2, 9, 8)])
        self.assertEqual(walk_nearest(line, (0, 0)), reference_walk(line, (0, 0)))


class TestImprove(unittest.TestCase):

    def test_permutation_shorter_and_deterministic(self):
        for seed in range(3):
            pts = random_points(600, seed)
            walk = walk_nearest(pts, (0.0, 0.0))
            better = improve_order(pts, walk, (0.0, 0.0))
            self.assertEqual(sorted(e >> 1 for e in better), list(range(600)))
            self.assertLess(travel(pts, better), travel(pts, walk))
            self.assertEqual(better, improve_order(pts, walk, (0.0, 0.0)))

class TestOrderPaths(unittest.TestCase):

    def make_paths(self, n, seed):
        rnd = random.Random(seed)
        paths = []
        for i in range(n):
            x, y = rnd.uniform(0, 80), rnd.uniform(0, 50)
            if i % 3 == 0:
                paths.append(LinearRing([(x, y), (x + 2, y), (x + 2, y + 2), (x, y + 2)]))
            else:
                paths.append(LineString([(x, y), (x + rnd.uniform(-4, 4), y + rnd.uniform(-4, 4)),
                                         (x + rnd.uniform(-4, 4), y + rnd.uniform(-4, 4))]))
        return paths

    def test_every_path_once_and_reversal_rule(self):
        paths = self.make_paths(300, 1)
        result = order_paths(paths)
        self.assertEqual(len(result), len(paths))
        self.assertEqual(sorted(id(g) for __, g in result), sorted(id(g) for g in paths))
        for pt, geo in result:
            coords = list(geo.coords)
            self.assertIn(pt, (coords[0], coords[-1]))
            if coords[0] == coords[-1]:
                # a closed path is entered at its first point: it is not reversed
                self.assertEqual(pt, coords[0])

    def test_deterministic_and_improves(self):
        paths = self.make_paths(500, 2)
        a = order_paths(paths)
        b = order_paths(list(paths))
        self.assertEqual([(pt, id(g)) for pt, g in a], [(pt, id(g)) for pt, g in b])

        def length(res):
            cx, cy = 0.0, 0.0
            total = 0.0
            for pt, g in res:
                c = list(g.coords)
                total += math.hypot(pt[0] - cx, pt[1] - cy)
                cx, cy = c[-1] if pt == c[0] else c[0]
            return total
        self.assertLess(length(a), length(order_paths(paths, improve=False)))

    def test_empty_one_and_unusable(self):
        self.assertEqual(order_paths([]), [])
        self.assertEqual(order_paths([None]), [])
        one = LineString([(1, 1), (2, 2)])
        self.assertEqual(order_paths([one]), [((1.0, 1.0), one)])
        far = LineString([(10, 10), (0, 0.5)])
        # the nearest end is the last point: the path is cut in reverse
        self.assertEqual(order_paths([far])[0][0], (0.0, 0.5))
        # empty lines and multi part geometries are left out, as before
        mixed = [LineString(), MultiLineString([[(0, 0), (1, 1)]]), Point(5, 5), one, None]
        got = order_paths(mixed)
        self.assertEqual([g for __, g in got if g is one], [one])
        self.assertTrue(all(g is not mixed[0] and g is not mixed[1] for __, g in got))

if __name__ == '__main__':
    unittest.main()
