"""Paint output tolerance is stored in mm and converted once for inch geometry."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_paint_performance import rest_worker
from test_paint_geometry import PaintGen, storage_of
from defaults import AppDefaults, AppOptions
from shapely import LineString, box


class TestPaintSimplification(unittest.TestCase):
    def test_normal_and_rest_use_configured_tolerance_in_geometry_units(self):
        for units, scale in (('MM', 1), ('IN', 1 / 25.4)):
            for tolerance in (0, 0.001, 0.02):
                for rest in (False, True):
                    with self.subTest(units=units, tolerance=tolerance, rest=rest):
                        app = SimpleNamespace(abort_flag=False, log=Mock(), inform=Mock(),
                                              proc_container=Mock(), units=units,
                                              options={'tools_paint_simplification': tolerance})
                        worker, initial = rest_worker(SimpleNamespace(app=app), [scale])
                        worker.obj_name = 'paint'
                        worker.paint_tools = initial.tools_storage
                        params = worker._create_params(0, 2, rest, 'paint', None)
                        self.assertAlmostEqual(params.simplification_value, tolerance * scale)
                        path = LineString([(scale, scale), (2 * scale, 1.01 * scale),
                                           (3 * scale, scale)])
                        worker._process_polygon = Mock(side_effect=lambda *args: storage_of([path]))
                        output = SimpleNamespace(tools={}, obj_options={})
                        generate = worker._generate_rest_paint_worker if rest else worker._generate_paint_worker
                        result = generate(output, app, params, [scale], [box(0, 0, 6 * scale, 6 * scale)],
                                          None, False)
                        self.assertNotEqual(result, 'fail')
                        actual = output.tools[1]['solid_geometry'][0]
                        expected = path.simplify(tolerance * scale) if tolerance else path
                        self.assertEqual(actual.wkb, expected.wkb)
                        self.assertEqual(len(actual.coords), 2 if tolerance == 0.02 else 3)

    def test_simplified_paths_do_not_cut_into_the_copper_of_a_hole(self):
        # The contour around the hole of the area (copper) is concave: a chord of the simplification cuts the copper.
        from test_clearing_paths import make_geometry
        from appPlugins.ToolPaint import PaintModel
        geometry = make_geometry()
        geometry.app.units, geometry.app.options = 'MM', {'global_tolerance': 0}
        copper = box(2, 2, 4, 4)
        area = box(0, 0, 6, 6).difference(copper)
        storage = {1: {'tooldia': 1.0, 'data': {'tools_paint_overlap': 20, 'tools_paint_connect': False,
                                               'tools_paint_contour': True, 'tools_paint_offset': 0}}}
        for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines'):
            with self.subTest(method=method):
                def process(polygon, diameter, settings):
                    return getattr(geometry, method)(polygon, diameter, 32, overlap=settings['over'], connect=False)
                raw = PaintModel.generate_paint([area], storage, [1.0], 0, 0.0, 4, process)
                simple = PaintModel.generate_paint([area], storage, [1.0], 0, 0.01, 4, process)
                cut = [PaintModel.swept_area(result['tools'][1]['solid_geometry'], 1.0).intersection(copper).area
                       for result in (raw, simple)]
                # the copper cut of the original paths is the chord error of 32 steps; the simplification adds none
                self.assertLess(cut[1], cut[0] + 1e-6)

    def test_simplified_paths_keep_the_clearing_error(self):
        # A simplification of 1 or 2 um must not cut deeper past the outline than the Clearing Error (0.5 um), or
        # than the error of the circle steps if the Clearing Error is 0 (0.085 um at 256 steps).
        from test_clearing_paths import make_geometry
        from appPlugins.ToolPaint import PaintModel
        from shapely import Point
        import camlib
        geometry = make_geometry()
        area = box(0, 0, 6, 6).difference(Point(3, 3).buffer(1.0, quad_segs=64))
        storage = {1: {'tooldia': 1.0, 'data': {'tools_paint_overlap': 20, 'tools_paint_connect': False,
                                               'tools_paint_contour': True, 'tools_paint_offset': 0}}}
        for error_um, steps, allowed in ((0.5, 64, 0.0005), (0, 256, camlib.circle_error(0.5, 64))):
            geometry.app.units, geometry.app.options = 'MM', {'global_tolerance': 0, 'tools_clearing_error': error_um}

            def process(polygon, diameter, settings):
                return geometry.clear_polygon_shrink(polygon, diameter, steps, overlap=settings['over'],
                                                     connect=False)
            for tolerance in (0.001, 0.002):
                with self.subTest(error=error_um, tolerance=tolerance):
                    result = PaintModel.generate_paint([area], storage, [1.0], 0, tolerance, 4, process,
                                                       edge_error=camlib.clearing_error(geometry.app),
                                                       steps_at=lambda diameter: steps)
                    past = max(0.5 - area.boundary.distance(path) for path in result['tools'][1]['solid_geometry'])
                    self.assertLessEqual(past, allowed + 1e-9)

if __name__ == '__main__':
    unittest.main()
