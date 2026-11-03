"""Run: python tests/test_paint_geometry.py"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    import camlib
    from appPlugins.ToolPaint import PaintModel
    from appPlugins.ToolPaint.PaintGen import PaintGen  # noqa: F401 (test_paint_performance imports it from here)
    from shapely import LineString, Polygon, box
finally:
    sys.argv = _saved_argv


def storage_of(paths):
    storage = camlib.AppRTreeStorage()
    storage.get_points = lambda go: [go.coords[0], go.coords[-1]]
    for path in paths:
        storage.insert(path)
    return storage


class TestPaintGeometry(unittest.TestCase):
    def setUp(self):
        self.geometry = camlib.Geometry.__new__(camlib.Geometry)
        self.geometry.app = SimpleNamespace(abort_flag=False, log=Mock(), inform_no_echo=Mock(),
                                            app_units='MM', options={'geometry_circle_steps_mode': 'fixed',
                                                     'geometry_circle_steps': 64},
                                            proc_container=Mock())
        self.polygon = Polygon(box(0, 0, 6, 6).exterior.coords,
                               [box(2, 2, 4, 4).exterior.coords])

    def test_shrink_uses_configured_circle_accuracy_for_cumulative_offsets(self):
        from test_shrink_offsets import inset_layers, layer_paths
        for steps in (1, 16, 17, 64):
            with self.subTest(steps=steps):
                paths = list(camlib.Geometry.clear_polygon_shrink(
                    self.geometry, self.polygon, 1, steps, connect=False).get_objects())
                expected = layer_paths(inset_layers(self.polygon, 1, 0.15, steps, True, bounded=True))
                # Every inset is there, bit for bit; the rings that cover the gaps between insets are extra.
                self.assertTrue(all(any(a.equals_exact(b, 1e-12) for a in paths) for b in expected))

    def test_connect_reverses_nearest_end_and_lifts_for_long_walk(self):
        paths = [LineString([(1, 1), (2, 1)]), LineString([(4, 1), (3, 1)]),
                 LineString([(8, 1), (9, 1)])]
        result = camlib.Geometry.paint_connect(storage_of(paths), box(0, 0, 10, 3), 0.2, 64, max_walk=2)
        self.assertEqual([list(path.coords) for path in result.get_objects()],
                         [[(1, 1), (2, 1), (3, 1), (4, 1)], [(8, 1), (9, 1)]])

    def test_connect_lifts_at_a_hole(self):
        boundary = Polygon(box(0, 0, 8, 3).exterior.coords,
                           [box(2.5, 0.5, 3.5, 1.5).exterior.coords])
        paths = [LineString([(1, 1), (2, 1)]), LineString([(5, 1), (6, 1)])]
        result = camlib.Geometry.paint_connect(storage_of(paths), boundary, 0.2, 64)
        self.assertEqual([list(path.coords) for path in result.get_objects()],
                         [list(path.coords) for path in paths])

    def test_area_intersection_keeps_every_area_and_drops_touching_artifacts(self):
        left, right = box(0, 0, 4, 4), box(10, 0, 14, 4)
        painted = PaintModel.area_intersection(
            camlib.unary_union([left, right, box(4, 0, 6, 4)]),
            [box(-1, -1, 4, 5), box(9, -1, 15, 5), box(6, 1, 7, 2)])
        self.assertAlmostEqual(sum(part.area for part in painted), 32)
        self.assertEqual(PaintModel.area_intersection(left, box(4, 0, 8, 4)), [])
        self.assertEqual(PaintModel.area_intersection(left, box(4, 4, 8, 8)), [])

    def test_reference_area_counts_a_closed_line_outline_and_ignores_an_open_path(self):
        # A closed outline is an area, even when it arrives as a LineString or as split segments.
        rectangle = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]
        self.assertAlmostEqual(PaintModel.reference_area([LineString(rectangle)]).area, 16, places=4)
        split = [LineString([(0, 0), (4, 0)]), LineString([(4, 0), (4, 4), (0, 4), (0, 0)])]
        self.assertAlmostEqual(PaintModel.reference_area(split).area, 16, places=4)
        # An open path encloses nothing and must not become a hull.
        self.assertEqual(PaintModel.reference_area([LineString([(0, 0), (4, 0), (4, 4)])]).area, 0)

if __name__ == '__main__':
    unittest.main()
