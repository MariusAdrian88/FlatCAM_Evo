"""Vectorized bounds() of Geometry / Gerber / Excellon must equal the old recursive walkers, edge cases included."""
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from shapely import Point, Polygon, LineString, MultiPolygon, GeometryCollection, box  # noqa: E402

from camlib import Geometry, list_total_bounds  # noqa: E402
from appParsers.ParseExcellon import Excellon  # noqa: E402
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

INF = (np.inf, np.inf, -np.inf, -np.inf)


def old_rec(obj):
    """The old recursive walker (Geometry flavour)."""
    if type(obj) is list:
        r = list(INF)
        for k in obj:
            subs = [old_rec(v) for v in k.values()] if type(k) is dict else (
                [] if getattr(k, 'is_empty', False) else [old_rec(k)])
            for s in subs:
                r = [min(r[0], s[0]), min(r[1], s[1]), max(r[2], s[2]), max(r[3], s[3])]
        return tuple(r)
    return obj.bounds


class ListTotalBoundsTest(unittest.TestCase):
    def test_matches_old_walker(self):
        cases = [
            [], [[], []], [Polygon()], [GeometryCollection()],
            [box(0, 0, 1, 2)],
            [Polygon(), [Polygon(), box(0, 0, 1, 2)], {'a': [box(-1, -1, 0, 0)], 'b': box(5, 5, 6, 7)}],
            [LineString([(0, 0), (5, -2)]), [LineString([(1, 1), (2, 9)])]],
            [GeometryCollection([box(0, 0, 1, 1), Point(9, 9)]), Point(-3, 2)],
            [MultiPolygon([box(0, 0, 1, 1), box(4, 4, 5, 5)])],
        ]
        for case in cases:
            got = list_total_bounds(case)
            self.assertEqual(got, old_rec(case), case)
            self.assertTrue(all(type(v) is float for v in got))

class ObjectBoundsTest(unittest.TestCase):
    def geometry(self):
        g = Geometry(make_app())
        g.tools = {}
        return g

    def test_geometry_single(self):
        g = self.geometry()
        g.multigeo = False
        expect = {None: (0, 0, 0, 0)}
        g.solid_geometry = None
        self.assertEqual(g.bounds(), expect[None])
        g.solid_geometry = []
        self.assertEqual(g.bounds(), INF)
        g.solid_geometry = box(0, 0, 3, 4)
        self.assertEqual(g.bounds(), (0.0, 0.0, 3.0, 4.0))
        g.solid_geometry = [Polygon(), [box(0, 0, 1, 2)], {'k': [box(5, 5, 6, 7)]}]
        self.assertEqual(g.bounds(), (0.0, 0.0, 6.0, 7.0))
        g.solid_geometry = [[], None]
        with self.assertRaises(AttributeError):
            g.bounds()

    def test_excellon(self):
        e = Excellon(make_app())
        self.assertEqual(e.bounds(), (0, 0, 0, 0))
        e.solid_geometry = []
        e.tools = {1: {'solid_geometry': [Point(1, 1).buffer(1), Polygon()]},
                   2: {'solid_geometry': [[Point(5, 5).buffer(2)], {'x': [box(9, 9, 10, 10)]}]}}
        self.assertEqual(e.bounds(), (0.0, 0.0, 10.0, 10.0))
        e.tools = {1: {'solid_geometry': []}}
        self.assertEqual(e.bounds(), INF)
        e.tools = {1: {'solid_geometry': [Point(1, 1).buffer(1), None]}}
        with self.assertRaises(AttributeError):
            e.bounds()


if __name__ == '__main__':
    unittest.main()
