"""Geometry-preserving Paint optimizations; run with --benchmark for timings/counts."""
import contextlib
import io
import json
import statistics
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import test_paint_geometry as paint_cases
from appPlugins.ToolPaint import PaintModel
from test_paint_geometry import camlib, PaintGen, storage_of
from shapely import Polygon, Point, box


def rest_worker(geometry, diameters, method=2, overlap=0.15, connect=False):
    worker = PaintGen.__new__(PaintGen)
    worker.app = geometry.app
    worker.app.inform = Mock()
    worker.units = 'MM'
    worker.decimals = 4
    worker.circle_steps = 64
    worker.parent_tool = geometry
    worker.ui = SimpleNamespace(rest_offset_entry=SimpleNamespace(get_value=lambda: 0))
    worker._cleanup_progressive_plotting = Mock()
    worker._validate_output_geometry = lambda output_obj: bool(output_obj.tools)
    params = SimpleNamespace(
        paint_method=method, prog_plot=False, simplification_value=0,
        output_object_name='benchmark', tools_storage={
            i: {'tooldia': dia, 'solid_geometry': [], 'data': {
                'tools_paint_overlap': overlap * 100, 'tools_paint_connect': connect,
                'tools_paint_contour': True, 'tools_paint_offset': 0}}
            for i, dia in enumerate(diameters, 1)})
    return worker, params


def run_rest(worker, params, polygons, diameters):
    output = SimpleNamespace(tools={}, obj_options={})
    # The legacy worker prints every residual polygon.
    with contextlib.redirect_stdout(io.StringIO()):
        result = worker._generate_rest_paint_worker(
            output, worker.app, params, list(diameters), polygons, None, False)
    assert result != 'fail'
    return [path for tool in output.tools.values() for path in tool['solid_geometry']]


class TestPaintPerformance(unittest.TestCase):
    setUp = paint_cases.TestPaintGeometry.setUp

    def test_swept_area_with_clipper_matches_shapely_and_is_not_larger(self):
        import numpy as np
        import shapely
        from unittest import mock
        from appCommon import clipper_ops
        rng = np.random.default_rng(3)
        paths = [shapely.LineString(rng.uniform(0, 20, (4, 2))) for _ in range(60)]
        paths.append(shapely.LinearRing([(1, 1), (5, 1), (5, 4), (1, 1)]))
        diameter, quad = 0.4, 8
        expected = shapely.union_all(shapely.buffer(paths, diameter / 2, quad_segs=quad))
        tolerance = clipper_ops.arc_tolerance(diameter / 2, quad)
        swept = PaintModel.swept_area(paths, diameter, quad)
        self.assertTrue(swept.is_valid)
        self.assertLess(swept.symmetric_difference(expected).area, expected.length * tolerance)
        self.assertEqual(swept.difference(expected.buffer(2 * tolerance)).area, 0.0)
        with mock.patch.object(clipper_ops, 'pyclipr', None):
            self.assertEqual(PaintModel.swept_area(paths, diameter, quad).symmetric_difference(expected).area, 0.0)
        self.assertTrue(PaintModel.swept_area([], diameter, quad).is_empty)

    def test_rest_tool_clears_the_stock_that_is_narrower_than_it(self):
        # The 1.0 tool leaves the corners of the area and strips around the hole; the 0.4 tool crosses the cut area
        # to reach them and stays inside the area.
        from test_clearing_paths import make_geometry
        geometry = make_geometry()
        geometry.app.units, geometry.app.options = 'MM', {'global_tolerance': 0}
        area = box(0, 0, 10.6, 10).difference(box(4, 4, 6, 6))
        storage = {i: {'tooldia': dia, 'data': {'tools_paint_overlap': 20, 'tools_paint_connect': False,
                                                'tools_paint_contour': True, 'tools_paint_offset': 0}}
                   for i, dia in ((1, 1.0), (2, 0.4))}
        for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines'):
            with self.subTest(method=method):
                def process(polygon, diameter, settings):
                    return getattr(geometry, method)(polygon, diameter, 64, overlap=settings['over'], connect=False)
                tools = PaintModel.generate_rest_paint([area], storage, [1.0, 0.4], 0.0, 4, process)['tools']
                swept = camlib.unary_union([PaintModel.swept_area(tool['solid_geometry'], tool['tooldia'])
                                            for tool in tools.values()])
                reach = area.buffer(-0.2, quad_segs=32).buffer(0.2, quad_segs=32)
                self.assertLess(reach.difference(swept).area, 1e-3)
                self.assertLess(swept.difference(area).area, 1e-4)

    def test_rest_tool_a_little_smaller_removes_the_stock_thicker_than_the_error(self):
        # 1.00 then 0.99 mm: the stock at the corners is about 2.1 um thick, more than a Clearing Error of 2 um and
        # more than the error of the circle steps (Clearing Error 0).
        from test_clearing_paths import make_geometry
        geometry = make_geometry()
        geometry.app.units, geometry.app.options = 'MM', {'global_tolerance': 0}
        area = box(0, 0, 6, 6).difference(box(2, 2, 4, 4))
        storage = {i: {'tooldia': dia, 'data': {'tools_paint_overlap': 20, 'tools_paint_connect': False,
                                                'tools_paint_contour': True, 'tools_paint_offset': 0}}
                   for i, dia in ((1, 1.0), (2, 0.99))}
        for edge_error, steps in ((0.002, 64), (None, 64), (None, 256)):
            with self.subTest(edge_error=edge_error, steps=steps):
                def process(polygon, diameter, settings):
                    return geometry.clear_polygon_shrink(polygon, diameter, steps, overlap=settings['over'],
                                                         connect=False)
                tools = PaintModel.generate_rest_paint([area], storage, [1.0, 0.99], 0.0, 4, process,
                                                       edge_error=edge_error, steps_at=lambda diameter: steps)['tools']
                # the sweep and the reach at the same resolution: no slivers of the measure itself
                swept = camlib.unary_union([path.buffer(tool['tooldia'] / 2, quad_segs=64)
                                            for tool in tools.values() for path in tool['solid_geometry']])
                left = area.buffer(-0.495, quad_segs=64).buffer(0.495, quad_segs=64).difference(swept)
                self.assertTrue(tools[2]['solid_geometry'])
                self.assertTrue(left.buffer(-0.001).is_empty)

def benchmark():
    case = paint_cases.TestPaintGeometry()
    case.setUp()
    geometry = case.geometry
    geometry.app.options = {'global_tolerance': 0.005}
    geometry.app.units = 'MM'
    holes = [Point(x, y).buffer(0.7, quad_segs=16).exterior.coords
             for x in range(3, 29, 3) for y in range(3, 20, 3)]
    board = Polygon(box(0, 0, 30, 21).exterior.coords, holes)

    def measure(name, operation):
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            paths = operation()
            samples.append(time.perf_counter() - start)
        print(json.dumps({'case': name, 'seconds_median': statistics.median(samples),
                          'paths': len(paths), 'segments': sum(len(p.coords) - 1 for p in paths)}))

    for method, handler in enumerate((geometry.clear_polygon_shrink, geometry.clear_polygon_seed,
                                      geometry.clear_polygon_lines)):
        for dia, overlap in ((0.4, 0.15), (0.2, 0.6)):
            label = f'method={method},dia={dia},overlap={overlap}'
            measure('generation,' + label, lambda: list(handler(
                board, dia, 64, overlap=overlap, connect=False).get_objects()))
            raw = list(handler(board, dia, 64, overlap=overlap, connect=False).get_objects())
            measure('connection,' + label, lambda: list(camlib.Geometry.paint_connect(
                storage_of(raw), board, dia, 64).get_objects()))
    for diameters in ([0.4], [0.8, 0.4]):
        def operation():
            worker, params = rest_worker(geometry, diameters)
            return run_rest(worker, params, [board], diameters)
        measure('rest,' + str(diameters), operation)


if __name__ == '__main__':
    import sys
    if '--benchmark' in sys.argv:
        benchmark()
    else:
        import unittest
        unittest.main()
