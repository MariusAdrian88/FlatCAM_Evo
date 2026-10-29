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
    from appPlugins.ToolPaint.PaintGen import PaintGen
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
                self.assertEqual(len(paths), len(expected))
                self.assertTrue(all(a.equals_exact(b, 1e-12) for a, b in zip(paths, expected)))

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

    def test_final_cleanup_adds_cleanup_paths_only_when_flagged(self):
        standard, cleanup_path = LineString([(1, 1), (2, 1)]), LineString([(3, 3), (3.5, 3)])
        residual = box(5, 5, 5.2, 5.2)
        data = {'tools_paint_overlap': 15, 'tools_paint_connect': False,
                'tools_paint_contour': True, 'tools_paint_offset': 0}

        def process(polygon, diameter, settings):
            return SimpleNamespace(get_objects=lambda: [standard])

        def cleanup(domain, diameter, previous, generate_paths, boundary_first):
            return [cleanup_path], residual

        for flag in (True, False):
            with self.subTest(flag=flag):
                storage = {1: {'tooldia': 1.0, 'data': {**data, 'tools_paint_final_cleanup': flag},
                               'solid_geometry': []}}
                output_tools = {1: {'tooldia': 1.0, 'data': dict(data), 'solid_geometry': [standard]}}
                result = PaintModel.final_cleanup([self.polygon], storage, output_tools, [1.0], 4,
                                                  process, cleanup)
                if flag:
                    self.assertEqual([list(path.coords) for path in result['output_tools'][1]['solid_geometry']],
                                     [list(standard.coords), list(cleanup_path.coords)])
                    self.assertEqual(result['output_tools'][1]['paint_cleanup_start'], 1)
                    self.assertIs(result['residual'], residual)
                    self.assertIn(('cleanup', None), result['warnings'])
                else:
                    self.assertIsNone(result['residual'])
                    self.assertEqual(result['warnings'], [])
                    self.assertEqual([list(path.coords) for path in result['output_tools'][1]['solid_geometry']],
                                     [list(standard.coords)])
                # The caller's tool dicts are never changed.
                self.assertEqual(len(output_tools[1]['solid_geometry']), 1)
                self.assertNotIn('paint_cleanup_start', output_tools[1])
                self.assertNotIn('paint_cleanup_start', storage[1])

    def test_laser_lines_takes_only_traces_inside_the_polygon(self):
        inside, outside = LineString([(1, 1), (5, 1)]), LineString([(20, 1), (25, 1)])
        apertures = {'10': {'size': 0.0, 'type': 'C', 'geometry': [
            {'follow': inside}, {'follow': outside}, {'solid': inside.buffer(0.1)}]}}
        filled = []

        def fill(line, size, **kwargs):
            filled.append(line)
            return SimpleNamespace(get_objects=lambda: [line])

        for index in (None, PaintModel.aperture_index(apertures)):
            filled.clear()
            result = PaintModel.laser_lines(box(0, 0, 6, 6), 0.2, 0.15, False, True, False, 64, 64,
                                            apertures, None, None, fill, index=index)
            self.assertEqual([line.wkt for line in filled], [inside.wkt])
            self.assertEqual(len(list(result.get_objects())), 1)
        empty = PaintModel.laser_lines(box(40, 40, 46, 46), 0.2, 0.15, False, True, False, 64, 64,
                                       apertures, None, None, fill)
        self.assertEqual(list(empty.get_objects()), [])

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

    def _job_worker(self, abort=False, new_object_result='fail'):
        worker = PaintGen.__new__(PaintGen)
        worker.app = SimpleNamespace(abort_flag=abort, log=Mock(), inform=Mock(), proc_container=Mock(),
                                     collection=Mock(), units='MM', options={})
        worker.obj_name = 'src'
        worker.decimals = 4
        worker.parent_tool = SimpleNamespace(paint_tools={}, new_object=Mock(return_value=new_object_result))
        worker.ui = None
        return worker

    def _messages(self, worker):
        return ' '.join(str(call.args[0]) for call in worker.app.inform.emit.call_args_list)

    def test_check_validity_stops_when_no_tool_fits_inside_and_only_warns_when_some_do(self):
        # Paint cuts inside the polygons: a tool is valid if it fits inside them (the 0.3 strip is too narrow
        # for 0.5), for Geometry and Gerber sources alike.
        selected = [box(0, 0, 4, 4), box(5, 0, 5.3, 4)]
        for kind, checked, tools, runs, text in (('geometry', True, [5.0], False, 'no tool fits'),
                                                 ('gerber', True, [0.5], True, 'too narrow'),
                                                 ('geometry', True, [0.2, 5.0], True, 'fit inside no polygon'),
                                                 ('geometry', False, [5.0], True, '')):
            with self.subTest(kind=kind, checked=checked, tools=tools):
                worker = self._job_worker(new_object_result='ok')
                worker.ui = SimpleNamespace(valid_cb=SimpleNamespace(get_value=lambda c=checked: c))
                source = SimpleNamespace(kind=kind, tools={})
                result = worker.paint_handler(source, selected, tooldia=tools, run_threaded=False)
                self.assertEqual(worker.parent_tool.new_object.called, runs)
                self.assertEqual(result == 'fail', not runs)
                self.assertIn(text, self._messages(worker))


if __name__ == '__main__':
    unittest.main()
