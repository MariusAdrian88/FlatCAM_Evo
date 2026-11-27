"""Seed clearing must reach disconnected areas and clear around holes without cutting their copper."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    import camlib
    from shapely import MultiPolygon, Point, Polygon, box
    from shapely.ops import unary_union
finally:
    sys.argv = _saved_argv

from test_clearing_paths import make_geometry

PAD_CENTERS = [(x, y) for x in range(3, 29, 3) for y in range(3, 20, 3)]
SEED_BAND_MIN_COORDS = camlib.Geometry.SEED_BAND_MIN_COORDS


def band_limit(min_coords):
    """Sets Geometry.SEED_BAND_MIN_COORDS, the number of margin coordinates from which the margin is cut in bands."""
    return patch.object(camlib.Geometry, 'SEED_BAND_MIN_COORDS', min_coords)


def board(islands=False):
    """
    A board area of 30 x 21 mm with 54 round holes (the copper pads). With islands: the pads are rings and the area
    inside each ring is a separate polygon.
    """
    area = Polygon(box(0, 0, 30, 21).exterior.coords, [Point(c).buffer(0.7, quad_segs=16).exterior.coords
                                                       for c in PAD_CENTERS])
    if islands:
        area = MultiPolygon([area] + [Point(c).buffer(0.35, quad_segs=16) for c in PAD_CENTERS])
    return area


class TestSeedBands(unittest.TestCase):
    def setUp(self):
        self.geometry = make_geometry()

    def seed_paths(self, polygon, tooldia, steps, simplify_tol=0.0):
        return list(self.geometry.clear_polygon_seed(
            polygon, tooldia, steps, connect=False, contour=False, simplify_tol=simplify_tol).get_objects())

    def test_banded_clearing_keeps_holes_and_islands_within_simplification_tolerance(self):
        tolerance = 0.01
        for islands in (False, True):
            polygon = board(islands)
            with self.subTest(islands=islands), band_limit(0):
                paths = self.seed_paths(polygon, 0.4, 64, simplify_tol=tolerance)
                margin = polygon.buffer(-0.2, quad_segs=32)
                swept = unary_union([path.buffer(0.2, quad_segs=32) for path in paths])
                for component in camlib.flatten_shapely_geometry(margin):
                    self.assertLess(component.difference(swept).area, 1e-3 * component.area,
                                    'uncut pocket at %s' % (component.bounds,))
                # Simplification may move the path by the requested tolerance, but not beyond it into copper.
                self.assertLess(swept.difference(polygon.buffer(tolerance)).area, 1e-4)

    def test_rings_go_on_past_a_ring_that_misses_the_margin(self):
        # 2 areas 3 mm apart, and 2 pockets joined by a neck narrower than the tool: a ring in the gap misses the
        # margin, the later rings clear the other part.
        for polygon in (MultiPolygon([box(0, 0, 4, 4), box(7, 0, 11, 4)]).buffer(0),
                        unary_union([box(0, 0, 4, 4), box(6, 0, 10, 4), box(3.9, 1.8, 6.1, 2.2)])):
            for min_coords in (SEED_BAND_MIN_COORDS, 0):
                with self.subTest(parts=len(polygon.geoms) if hasattr(polygon, 'geoms') else 1,
                                  min_coords=min_coords), band_limit(min_coords):
                    paths = list(self.geometry.clear_polygon_seed(polygon, 1.0, 64, connect=False).get_objects())
                    reach = polygon.buffer(-0.5, quad_segs=32).buffer(0.5, quad_segs=32)
                    swept = unary_union([p.buffer(0.5, quad_segs=32) for p in paths])
                    self.assertLess(reach.difference(swept).area, 1e-4)

if __name__ == '__main__':
    unittest.main()
