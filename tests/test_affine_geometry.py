import os
import sys
import time
import unittest

import pytest
import shapely.affinity as affinity
from shapely.geometry import (Point, LineString, LinearRing, Polygon, MultiPolygon, MultiLineString,
                              GeometryCollection, MultiPoint)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camlib  # noqa: E402
from camlib import (affine_geometry, affine_geometry_many, affine_rotate_matrix, affine_scale_matrix,  # noqa: E402
                    affine_skew_matrix, affine_mirror_matrix, affine_translate_matrix, map_geometry)

ORIGIN = (3.5, -2.25)

CASES = [
    ('rotate', lambda g: affinity.rotate(g, 37.0, origin=ORIGIN), affine_rotate_matrix(37.0, ORIGIN)),
    ('rotate90', lambda g: affinity.rotate(g, 90.0, origin=ORIGIN), affine_rotate_matrix(90.0, ORIGIN)),
    ('scale', lambda g: affinity.scale(g, 1.7, 0.4, origin=ORIGIN), affine_scale_matrix(1.7, 0.4, ORIGIN)),
    ('skew', lambda g: affinity.skew(g, 12.0, -7.0, origin=ORIGIN), affine_skew_matrix(12.0, -7.0, ORIGIN)),
    ('mirror_x', lambda g: affinity.scale(g, 1.0, -1.0, origin=ORIGIN), affine_mirror_matrix('X', ORIGIN)),
    ('mirror_y', lambda g: affinity.scale(g, -1.0, 1.0, origin=ORIGIN), affine_mirror_matrix('Y', ORIGIN)),
    ('translate', lambda g: affinity.translate(g, 4.0, -9.5), affine_translate_matrix(4.0, -9.5)),
]


def make_geoms():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (4, 2), (4, 4), (2, 4)]])
    return {
        'point': Point(1, 2),
        'line': LineString([(0, 0), (1, 1), (5, 2)]),
        'ring': LinearRing([(0, 0), (1, 0), (1, 1), (0, 0)]),
        'poly_holes': poly,
        'multipoly': MultiPolygon([poly, Polygon([(20, 20), (21, 20), (21, 21)])]),
        'multiline': MultiLineString([[(0, 0), (1, 1)], [(5, 5), (6, 7)]]),
        'multipoint': MultiPoint([(0, 0), (3, 4)]),
        'collection': GeometryCollection([Point(1, 1), LineString([(0, 0), (2, 2)]), poly]),
        'line3d': LineString([(0, 0, 5), (1, 1, 6), (5, 2, 7)]),
        'empty': Polygon(),
    }


class TestAffineGeometry(unittest.TestCase):

    def assert_same(self, got, exp):
        if exp is None:
            self.assertIsNone(got)
            return
        if isinstance(exp, list):
            self.assertIsInstance(got, list)
            self.assertEqual(len(got), len(exp))
            for g, e in zip(got, exp):
                self.assert_same(g, e)
            return
        if not hasattr(exp, 'geom_type'):
            self.assertIs(got, exp)
            return
        self.assertEqual(got.geom_type, exp.geom_type)
        self.assertEqual(got.has_z, exp.has_z)
        if exp.is_empty:
            self.assertTrue(got.is_empty)
        else:
            self.assertTrue(got.equals_exact(exp, 1e-9), '%s != %s' % (got.wkt, exp.wkt))
            if exp.has_z:
                self.assertEqual(got.wkt.count(' '), exp.wkt.count(' '))

    def test_single_geometries(self):
        for name, ref, matrix in CASES:
            for gname, g in make_geoms().items():
                with self.subTest(op=name, geom=gname):
                    self.assert_same(affine_geometry(g, matrix), ref(g))

    def test_nested_and_mixed_lists(self):
        g = make_geoms()
        obj = [g['poly_holes'], [g['line'], None, [g['point'], g['multipoly']]], None, 7, g['collection'], []]
        for name, ref, matrix in CASES:
            with self.subTest(op=name):
                self.assert_same(affine_geometry(obj, matrix), map_geometry(obj, ref))

if __name__ == '__main__':
    unittest.main()
