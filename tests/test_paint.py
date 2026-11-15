"""Merged from: test_paint_geometry: Run: python tests/test_paint_geometry.py; test_paint_tool: Paint plugin controller: tool table, parameters copy, selection state.; test_paint_simplification: Paint output tolerance is stored in mm and converted once for inch geometry.; test_paint_performance: Geometry-preserving Paint optimizations; run with --benchmark for timings/counts.; test_shrink_offsets: Standard inset accuracy checks against high resolution offsets and the physical cutter sweep.
"""

import os
import sys
import unittest
import math
import pytest
import json
import tempfile
import contextlib
import io
import statistics
import time
import shapely
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
from pathlib import Path
from shapely import box, LineString, Polygon, Point, MultiPolygon, linestrings, buffer
from PyQt6 import QtWidgets
from appPlugins.ToolPaint.Paint import ToolPaint
from defaults import AppDefaults, AppOptions
from appPlugins.Common.clearing import regions
from appPlugins.ToolPaint import PaintModel
from shapely.ops import unary_union
from shapely.affinity import scale


# ==============================================================================================================
# test_paint_geometry: Run: python tests/test_paint_geometry.py
# ==============================================================================================================

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

    def test_crosshatch_tool_settings_reach_the_geometry(self):
        # The per-tool method and angle go through the same settings dict that the worker uses.
        area = box(0, 0, 12, 3)
        settings = PaintModel.tool_params(1, {1: {'tooldia': 1.0, 'data': {
            'tools_paint_overlap': 20, 'tools_paint_connect': False,
            'tools_paint_contour': False, 'tools_paint_offset': 0.0,
            'tools_paint_crosshatch_angle': 45.0}}})
        self.assertEqual(settings['angle'], 45.0)
        result = PaintModel.paint_polygon(
            area, 1.0, 5, settings['over'], settings['conn'], settings['cont'], False, 64,
            self.geometry.clear_polygon_shrink, self.geometry.clear_polygon_seed,
            self.geometry.clear_polygon_lines, crosshatch_angle=settings['angle'],
            clear_crosshatch=self.geometry.clear_polygon_crosshatch)
        angles = set()
        for path in result.get_objects():
            coords = list(path.coords)
            for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
                if math.hypot(x1 - x0, y1 - y0) > 1e-6:
                    angles.add(round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0, 6))
        self.assertEqual(angles, {45.0, 135.0})
        for bad in ('invalid', None, ''):
            data = {'tools_paint_overlap': 20, 'tools_paint_connect': False, 'tools_paint_contour': False,
                    'tools_paint_offset': 0.0, 'tools_paint_crosshatch_angle': bad}
            self.assertEqual(PaintModel.tool_params(1, {1: {'tooldia': 1.0, 'data': data}})['angle'], 0.0)

if __name__ == '__main__':
    unittest.main()


# ==============================================================================================================
# test_paint_tool: Paint plugin controller: tool table, parameters copy, selection state.
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

def _item(text='', row=0):
    item = MagicMock()
    item.text.return_value = str(text)
    item.row.return_value = row
    return item

def _stub(tools, **extra):
    """A ToolPaint stand-in: `tools` is {uid: diameter}; the table rows follow the dict order."""
    paint_tools = {uid: {'tooldia': dia, 'data': {'tools_mill_tool_shape': 0}, 'solid_geometry': []}
                   for uid, dia in tools.items()}
    uids = list(tools)
    table = MagicMock()
    table.rowCount.return_value = len(uids)
    cells = {}
    for row, uid in enumerate(uids):
        cells[(row, 1)] = _item(tools[uid], row)
        cells[(row, 3)] = _item(uid, row)
    table.item.side_effect = lambda r, c: cells[(r, c)]
    app = MagicMock()
    app.dec_format.side_effect = lambda v, d: float('%.*f' % (d, v))
    stub = SimpleNamespace(
        app=app, decimals=4, paint_tools=paint_tools, blockSignals=MagicMock(), build_ui=MagicMock(),
        clear_preview=MagicMock(), ui=SimpleNamespace(tools_table=table), cells=cells)
    for key, value in extra.items():
        setattr(stub, key, value)
    return stub

def _edit(stub, row, text):
    stub.cells[(row, 1)].text.return_value = text
    ToolPaint.on_tool_edit(stub, stub.cells[(row, 1)])

def test_tool_edit_rejects_a_duplicate_of_any_other_tool():
    stub = _stub({1: 2.0, 2: 1.0, 3: 0.5})
    _edit(stub, 0, '0.5')   # duplicates the last tool, not the first one checked
    assert stub.paint_tools[1]['tooldia'] == 2.0
    _edit(stub, 0, '1.0')   # duplicates a middle tool
    assert stub.paint_tools[1]['tooldia'] == 2.0
    _edit(stub, 0, '1.5')
    assert stub.paint_tools[1]['tooldia'] == 1.5
    stub.build_ui.assert_called()

def test_tool_edit_rejects_non_positive_and_non_finite_diameters():
    stub = _stub({1: 2.0, 2: 1.0})
    for text in ('0', '-1', 'nan', 'inf', 'abc'):
        _edit(stub, 0, text)
        assert stub.paint_tools[1]['tooldia'] == 2.0, text
    assert stub.blockSignals.call_args_list[-1].args == (False,)

def test_apply_to_all_copies_only_the_form_parameters_of_the_selected_row():
    stub = _stub({1: 2.0, 2: 1.0})
    stub.paint_tools[1]['data'].update(tools_paint_overlap=0.1, extra_key='one')
    stub.paint_tools[2]['data'].update(tools_paint_overlap=0.7, extra_key='two', tools_mill_tool_shape=3)
    stub.form_fields = {'tools_paint_overlap': MagicMock()}
    stub.ui.tools_table.selectedItems.return_value = [_item(row=1)]
    stub.ui.tools_table.currentRow.return_value = 0     # differs from the row the form shows
    ToolPaint.on_apply_param_to_all_clicked(stub)
    assert stub.paint_tools[1]['data']['tools_paint_overlap'] == 0.7
    assert stub.paint_tools[1]['data']['extra_key'] == 'one'
    assert stub.paint_tools[1]['data']['tools_mill_tool_shape'] == 0
    stub.build_ui.assert_called()

@pytest.fixture(scope="session")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

@pytest.fixture
def painter(qapp, tmp_path):
    import functools
    from copy import deepcopy
    from PyQt6.QtCore import QStringListModel
    from appMain import App
    from defaults import AppDefaults
    from test_plugin_geometry_bases import make_tool_app

    class Collection(QStringListModel):
        def __init__(self):
            super().__init__()
            self.objects, self.selected = {}, []

        def get_selected(self):
            return self.selected

        def get_active(self):
            return None

        def get_list(self):
            return list(self.objects.values())

        def get_names(self):
            return list(self.objects)

        def get_by_name(self, name):
            return self.objects.get(name)

    class Defaults(dict):
        report_usage = staticmethod(lambda *args: None)

    app = make_tool_app()
    app.collection = Collection()
    app.options = deepcopy(AppDefaults.factory_defaults)
    app.defaults = Defaults(AppDefaults.factory_defaults)
    app.dec_format = functools.partial(App.dec_format, app)
    app.tools_database_path.return_value = str(tmp_path / "missing_tools_db.json")
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.abort_flag = False
    app.plotcanvas.new_shape_collection.side_effect = lambda **kwargs: MagicMock()
    app.options["tools_paint_tooldia"] = "1.0"
    t = ToolPaint(app)
    t.decimals = 4
    t.set_tool_ui()
    t.build_ui()
    t.ui.tools_table.selectAll()
    board = SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 4, 4), box(10, 0, 14, 4)],
                            obj_options={'name': 'board'})
    ref = SimpleNamespace(kind='geometry', solid_geometry=[box(-1, -1, 5, 5)], obj_options={'name': 'ref'})
    for obj in (board, ref):
        obj.tools = {1: {'solid_geometry': obj.solid_geometry}}
    app.collection.objects.update(board=board, ref=ref)
    t.ui.obj_combo.currentText = lambda: 'board'
    t.ui.reference_combo.currentText = lambda: 'ref'
    return t

def _drawn_area(t):
    t.on_preview()
    store = t._preview_shapes
    if store is None:
        return 0.0
    area = sum(call.args[0].area for call in store.add.call_args_list)
    store.add.reset_mock()
    return area

def test_legacy_tool_angle_does_not_leak_from_previous_tool(painter):
    import json

    data = next(iter(painter.paint_tools.values()))['data']
    data.pop('tools_paint_crosshatch_angle', None)
    painter.ui.crosshatch_angle_entry.set_value(45.0)
    painter.storage_to_form(data)
    assert painter.ui.crosshatch_angle_entry.get_value() == 0.0
    painter.ui.crosshatch_angle_entry.set_value(135.0)
    painter.ui.crosshatch_angle_entry.returnPressed.emit()
    assert data['tools_paint_crosshatch_angle'] == 135.0
    painter.storage_to_form(json.loads(json.dumps(data)))
    assert painter.ui.crosshatch_angle_entry.get_value() == 135.0
    # the angle is editable for the Crosshatch method only
    for method, enabled in ((2, False), (5, True)):
        data['tools_paint_method'] = method
        painter.storage_to_form(data)
        assert painter.ui.crosshatch_angle_entry.isEnabled() is enabled

def test_selected_objects_do_not_need_the_source_combo(painter):
    # two Gerbers selected in the Project tab, no Geometry object: the source combo is empty
    first = SimpleNamespace(kind='gerber', solid_geometry=[box(0, 0, 2, 2)],
                            obj_options={'name': 'g1'}, tools={})
    second = SimpleNamespace(kind='gerber', solid_geometry=[box(3, 0, 5, 2)],
                             obj_options={'name': 'g2'}, tools={})
    painter.app.collection.objects.update(g1=first, g2=second)
    painter.app.collection.selected = [first, second]
    painter.app.collection.promise = MagicMock()
    painter.ui.obj_combo.currentText = lambda: ''
    painter.ui.multi_objects_cb.set_value(True)
    painter.ui.select_method_combo.set_value(0)     # All

    tasks = []
    painter.app.worker_task.emit.side_effect = tasks.append
    painter.gen.on_paint_button_click()
    assert len(tasks) == 2      # one paint job per selected object

    painter.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    assert _drawn_area(painter) == pytest.approx(8.0)
    assert not any('Object not found' in str(call.args[0]) for call in painter.app.inform.emit.call_args_list)


# ==============================================================================================================
# test_paint_simplification: Paint output tolerance is stored in mm and converted once for inch geometry.
# ==============================================================================================================

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
                cut = [regions.swept_area(result['tools'][1]['solid_geometry'], 1.0).intersection(copper).area
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


def test_normal_run_measures_the_stock_of_all_tools_and_not_only_of_the_last():
    # ascending order: the 0.4 tool clears the 0.5 wide branch, the 1.0 tool runs last and cannot enter it
    from test_ncc import square_paths

    area = unary_union([box(0, 0, 4, 4), box(4, 1.75, 6, 2.25)])
    small = square_paths(0.4) + [LineString([(3.8, y), (5.8, y)]) for y in (1.95, 2.05)]
    storage = {uid: {'tooldia': dia, 'data': {'tools_paint_overlap': 20, 'tools_paint_connect': False,
                                              'tools_paint_contour': True, 'tools_paint_offset': 0}}
               for uid, dia in ((1, 0.4), (2, 1.0))}
    paths = {0.4: small, 1.0: square_paths(1.0)}
    result = PaintModel.generate_paint(
        [area], storage, [1.0, 0.4], 1, 0.0, 4,
        lambda polygon, diameter, settings: SimpleNamespace(get_objects=lambda: paths[diameter]))
    assert result['last_diameter'] == 1.0 and all(tool['solid_geometry'] for tool in result['tools'].values())
    # the measure of PaintGen: no warning, the branch is cleared by the small tool
    from appPlugins.Common.clearing.job import stock_is_left
    assert stock_is_left(result['stock'], result.get('stock_diameter') or result['last_diameter']) is False


if __name__ == '__main__':
    unittest.main()


# ==============================================================================================================
# test_paint_performance: Geometry-preserving Paint optimizations; run with --benchmark for timings/counts.
# ==============================================================================================================

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
    setUp = TestPaintGeometry.setUp

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
        swept = regions.swept_area(paths, diameter, quad)
        self.assertTrue(swept.is_valid)
        self.assertLess(swept.symmetric_difference(expected).area, expected.length * tolerance)
        self.assertEqual(swept.difference(expected.buffer(2 * tolerance)).area, 0.0)
        with mock.patch.object(clipper_ops, 'pyclipr', None):
            self.assertEqual(regions.swept_area(paths, diameter, quad).symmetric_difference(expected).area, 0.0)
        self.assertTrue(regions.swept_area([], diameter, quad).is_empty)

    def test_job_with_stock_left_warns_but_still_publishes(self):
        from appPlugins.Common.clearing.job import incomplete_text
        # a 0.5 wide strip that the 1.0 tool cannot enter stays unpainted; the corners of a pad are not stock
        disc = Point(5, 5).buffer(5, quad_segs=32)
        board = unary_union([disc, box(10, 4.75, 14, 5.25)])
        for polygon, incomplete in ((board, True), (disc, False), (box(0, 0, 4, 3), False)):
            with self.subTest(incomplete=incomplete):
                worker, params = rest_worker(self.geometry, [1.0], method=0)
                output = SimpleNamespace(tools={}, obj_options={})
                result = worker._generate_paint_worker(output, worker.app, params, [1.0], [polygon], None, False)
                messages = [call.args[0] for call in worker.app.inform.emit.call_args_list]
                assert result != 'fail' and output.tools[1]['solid_geometry']
                assert ('[WARNING_NOTCL] %s' % incomplete_text() in messages) is incomplete
        # the same with the final cleanup: the message follows the rule of the stock, not any residue
        for polygon, message in ((board, True), (disc, False)):
            with self.subTest(final_cleanup=message):
                worker, params = rest_worker(self.geometry, [1.0], method=0)
                params.tools_storage[1]['data']['tools_paint_final_cleanup'] = True
                output = SimpleNamespace(tools={}, obj_options={})
                result = worker._generate_paint_worker(output, worker.app, params, [1.0], [polygon], None, False)
                messages = [call.args[0] for call in worker.app.inform.emit.call_args_list]
                assert result != 'fail'
                assert any('Final paint finished' in text for text in messages) is message

    def test_clearing_direction_reaches_the_rings_the_cleanup_and_the_output_flag(self):
        import numpy as np
        from test_clearing_engine import AREA, rings, role
        from test_clearing_paths import turn
        for direction, outer in (('climb', 1), ('conv', -1), ('none', None)):
            with self.subTest(direction=direction):
                worker, params = rest_worker(self.geometry, [1.0], method=0, overlap=0.5)
                params.tools_storage[1]['data'].update(tools_paint_clear_milling=direction, tools_paint_final_cleanup=True)
                output = SimpleNamespace(tools={}, obj_options={})
                self.assertNotEqual(worker._generate_paint_worker(output, worker.app, params, [1.0], [AREA], None, False),
                                    'fail')
                data = output.tools[1]['data']
                self.assertIs(data.get('tools_mill_keep_direction', False), outer is not None)
                made = [ring for ring in rings(output.tools[1]['solid_geometry']) if role(ring)]
                self.assertGreater(len(made), 3)
                if outer is not None:
                    for ring in made:
                        self.assertEqual(turn(ring), outer if role(ring) == 'outer' else -outer)
                else:
                    # no direction: the rings are as made (not one turn for every outer ring)
                    self.assertGreaterEqual(len({(role(ring), turn(ring)) for ring in made}), 3)

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
                swept = camlib.unary_union([regions.swept_area(tool['solid_geometry'], tool['tooldia'])
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
    case = TestPaintGeometry()
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


# ==============================================================================================================
# test_shrink_offsets: Standard inset accuracy checks against high resolution offsets and the physical cutter sweep.
# ==============================================================================================================

def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    return list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]

def inset_layers(polygon, diameter, overlap, steps, cumulative, bounded=False, tolerance=0.0):
    # Same full-circle to quadrant conversion as circle_quad_segs(). Keep this
    # numerical experiment independent of application/GUI imports.
    q = max(1, math.ceil(steps / 4))
    margins = polygon_parts(polygon.buffer(-diameter / 2, quad_segs=q))
    layers = [margins]
    step = diameter * (1 - overlap)
    chord_error = diameter * math.sin(math.pi / (8 * q)) ** 2
    if tolerance > 0:
        chord_error = min(chord_error, tolerance)
    for margin in margins:
        current = margin
        index = 1
        while True:
            distance = index * step
            inset_q = max(q, math.ceil(3 * math.pi / 8 * math.sqrt(distance / (2 * chord_error)))) if bounded else q
            current = (margin.buffer(-distance, quad_segs=inset_q) if cumulative
                       else current.buffer(-step, quad_segs=q))
            parts = [p for p in polygon_parts(current) if p.area > 0]
            if not parts:
                break
            layers.append(parts)
            if not cumulative:
                current = unary_union(parts) if len(parts) > 1 else parts[0]
            index += 1
    return layers

def layer_paths(layers):
    return [ring for layer in layers for part in layer for ring in [part.exterior, *part.interiors]]

def cutter_sweep(paths, radius):
    # Buffer two-point segments, not whole paths: GEOS otherwise simplifies the
    # input line before buffering it. Capsule unions model a physical round tool.
    segments = linestrings([[a, b] for path in paths for a, b in zip(path.coords, list(path.coords)[1:])])
    return unary_union(buffer(segments, radius, quad_segs=128))

class TestBoundedStandardOffsets(unittest.TestCase):
    def setUp(self):
        case = TestPaintGeometry()
        case.setUp()
        self.geometry = case.geometry
        self.geometry.app.options = {'global_tolerance': 0.005}
        self.geometry.app.units = 'MM'

    def paths(self, polygon, diameter, overlap=0.15, steps=64, **kwargs):
        result = self.geometry.clear_polygon_shrink(
            polygon, diameter, steps, overlap=overlap, connect=False, **kwargs)
        return list(result.get_objects()) if result else []

    def test_physical_sweeps_match_high_resolution_offsets_without_new_overcut(self):
        square_hole = Polygon(box(0, 0, 6, 6).exterior.coords, [box(2, 2, 4, 4).exterior.coords])
        circle_hole = Polygon(box(0, 0, 6, 6).exterior.coords,
                              [Point(3, 3).buffer(0.7, quad_segs=16).exterior.coords])
        for units, factor in (('MM', 1), ('IN', 1 / 25.4)):
            self.geometry.app.units = self.geometry.app.app_units = units
            tolerance = 0.005 if units == 'MM' else 0.005 / 20
            for polygon_mm in (square_hole, circle_hole):
                polygon = scale(polygon_mm, xfact=factor, yfact=factor, origin=(0, 0))
                for steps in (4, 64):
                    with self.subTest(units=units, steps=steps, holes=len(polygon.interiors)):
                        diameter = 0.4 * factor
                        q = max(1, math.ceil(steps / 4))
                        error = min(tolerance, diameter * math.sin(math.pi / (8 * q)) ** 2)
                        margin = polygon.buffer(-diameter / 2, quad_segs=q)
                        reference = []
                        index = 0
                        while True:
                            offset = margin.buffer(-index * diameter * 0.85, quad_segs=1024)
                            if offset.is_empty:
                                break
                            reference.extend(layer_paths([polygon_parts(offset)]))
                            index += 1
                        actual = self.paths(polygon, diameter, steps=steps)
                        first = layer_paths([polygon_parts(margin)])
                        self.assertEqual([p.wkb for p in actual[:len(first)]], [p.wkb for p in first])
                        self.assertTrue(all(margin.buffer(1e-11 * factor).covers(p) for p in actual))
                        # Each high-resolution inset has an actual path within the allowance; the extra rings that
                        # cover the gaps between the insets are inside the margin (above). The contour keeps the edge
                        # error (the first paths, above); an inner ring may be off by a quarter of the overlap width,
                        # from its arcs and from its simplification.
                        inner = max(error, diameter * 0.15 / 4)
                        allowance = 2 * inner + diameter * (1 - math.cos(math.pi / 4096)) + 1e-10 * factor
                        points = shapely.points(shapely.get_coordinates(unary_union(reference)))
                        self.assertLessEqual(float(shapely.distance(points, unary_union(actual)).max()), allowance)

    def test_round_cutter_clears_legacy_corner_slivers_without_extra_outside_cut(self):
        polygon = Polygon(box(0, 0, 6, 6).exterior.coords, [box(2, 2, 4, 4).exterior.coords])
        for steps in (16, 64):
            with self.subTest(steps=steps):
                old = cutter_sweep(layer_paths(inset_layers(polygon, 0.4, 0.15, steps, False)), 0.2)
                new = cutter_sweep(self.paths(polygon, 0.4, steps=steps), 0.2)
                kernel_error = 0.2 * (1 - math.cos(math.pi / 512))
                self.assertTrue(old.difference(new).buffer(-2 * kernel_error).is_empty)
                self.assertLess(new.difference(old).difference(polygon).area, 1e-12)
                self.assertGreater(new.difference(old).area, 0)

    def test_clipper_and_geos_insets_clear_the_same_concave_area(self):
        from unittest import mock
        from appCommon import clipper_ops
        if not clipper_ops.available():
            self.skipTest("pyclipr is not installed")
        n = 40
        star = Polygon([((5 if i % 2 else 2.5) * math.cos(math.pi * i / n), (5 if i % 2 else 2.5) *
                         math.sin(math.pi * i / n)) for i in range(2 * n)])
        fast = self.paths(star, 0.4)
        with mock.patch.object(clipper_ops, 'pyclipr', None):
            slow = self.paths(star, 0.4)
        self.assertTrue(fast and slow)
        margin = star.buffer(-0.2, quad_segs=16)
        self.assertTrue(all(margin.buffer(1e-9).covers(p) for p in fast))
        # the cut areas match within the inner ring allowance (a quarter of the overlap width per side)
        cut_fast, cut_slow = cutter_sweep(fast, 0.2), cutter_sweep(slow, 0.2)
        self.assertLess(cut_fast.symmetric_difference(cut_slow).area, 0.01 * cut_slow.area)
        self.assertTrue(cut_slow.difference(cut_fast).buffer(-0.4 * 0.15 / 2).is_empty)


# ==============================================================================================================
# Paint exclusion areas (Stage 4c): the physical domain, the allowed cutter centers, the strict boundary
# ==============================================================================================================
from appParsers.feature_access import fused_solid  # noqa: E402
from appPlugins.Common.clearing import regions  # noqa: E402
from test_clearing_golden import Plugin, qapp  # noqa: E402,F401  (the real-plugin harness of the golden tests)

# a round flash (1.6 mm, at 5,5) and a vertical trace (1 mm wide, x = 7, y 2..8)
EXCL_GBR = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,1.600000*%
%ADD11C,1.000000*%
G01*
D10*
X5000000Y5000000D03*
D11*
X7000000Y2000000D02*
X7000000Y8000000D01*
M02*
"""
STRICT = 1e-9


@pytest.fixture(scope='module')
def excl_board(qapp, tmp_path_factory):
    from unittest.mock import patch
    from helpers_objects import make_app, make_gerber
    path = tmp_path_factory.mktemp('excl') / 'board.gbr'
    path.write_text(EXCL_GBR)
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield make_gerber(make_app(), str(path), 'board')


def excluded_run(qapp, tmp_path, board, tools, exclusion, rest=False, tcl=None):
    """Paint all of the board with the real plugin; the exclusion zone is drawn into the panel. {dia: paths}."""
    plugin = Plugin(qapp, tmp_path, 'paint', board, list(tools), tool_data=tools)
    try:
        if exclusion is not None:
            board.add_exclusion_zones([exclusion])
            plugin.tool.ui.excl_cb.set_value(True)
        if tcl is None:
            result = plugin.tool.gen.paint_poly_all(
                board, tooldia=list(tools), outname='board_paint', plot=False, run_threaded=False, rest=rest,
                rest_offset=0.0)
            assert result != 'fail'
        else:
            tcl(plugin)
        return {row['tooldia']: [p for p in row['solid_geometry'] if p is not None and not p.is_empty]
                for row in plugin.result().tools.values()}
    finally:
        board.set_exclusion_zones([])       # the board is shared by the tests
        plugin.close()


def physical_domain_of(board, offset, exclusion):
    return unary_union(regions.physical_domain(regions.buffer_geometry(fused_solid(board), offset), exclusion))


def nearest_to(paths, exclusion):
    return min(shapely.distance(path, exclusion) for path in paths)


def swept_by(paths_by_dia):
    return unary_union([regions.swept_area(paths, dia, 32) for dia, paths in paths_by_dia.items()])


def uncut_strip(domain, swept, edge_x, side, band):
    """The width of the uncut strip that touches the straight exclusion edge x = edge_x (side -1: domain at x<edge)."""
    widths = []
    for part in regions.polygon_parts(domain.difference(swept).intersection(band)):
        if side > 0 and part.bounds[0] < edge_x + 1e-6:
            widths.append(part.bounds[2] - edge_x)
        elif side < 0 and part.bounds[2] > edge_x - 1e-6:
            widths.append(edge_x - part.bounds[0])
    return max(widths, default=0.0)


FLASH_AND_TRACE_CUT = box(5.4, 3.5, 7.0, 6.5)          # a straight edge in the flash (x = 5.4) and in the trace (x = 7)
EXCL_CASES = {
    'standard': (dict(method=0, overlap=20, connect=False, contour=True), False),
    'negative_offset': (dict(method=0, overlap=20, connect=False, contour=True, offset=-0.05), False),
    'rest_cleanup': (dict(method=0, overlap=20, connect=False, contour=True, final_cleanup=True), True),
}


@pytest.mark.parametrize('case', list(EXCL_CASES))
def test_paint_exclusion_cuts_the_free_copper_and_keeps_the_cutter_out(case, excl_board, qapp, tmp_path):
    """
    An exclusion over part of a flash and part of a trace (Standard, a negative offset, rest machining with the
    final cleanup). Engine residual (physical domain minus swept) is the normal edge stock; selection coverage
    (selection minus swept) is the excluded area plus that stock. The strict boundary holds and nothing is cleared
    twice at the straight exclusion edge.
    """
    row, rest = EXCL_CASES[case]
    tools = {0.2: row} if not rest else {0.4: dict(row, final_cleanup=False), 0.2: row}
    zone = FLASH_AND_TRACE_CUT
    paths = excluded_run(qapp, tmp_path, excl_board, tools, zone, rest=rest)
    offset = row.get('offset', 0.0)
    dia = min(paths)
    domain = physical_domain_of(excl_board, offset, zone)
    selection = unary_union(regions.buffer_geometry(fused_solid(excl_board), offset))
    assert selection.intersection(zone).area > 0.5 and not domain.intersects(zone.buffer(-1e-9))

    # strict boundary: the true distance of every path to the ORIGINAL zone, for every tool
    for tool_dia, tool_paths in paths.items():
        assert tool_paths and nearest_to(tool_paths, zone) >= tool_dia / 2 - STRICT, case

    swept = swept_by(paths)
    # engine residual: no stock that holds a disc of 10 % of the tool (corner residue only)
    residual = domain.difference(swept)
    assert residual.buffer(-0.1 * dia).is_empty
    # selection coverage: the selection minus swept is the zone (cut away) plus that same edge stock
    coverage = selection.difference(swept)
    assert coverage.difference(zone).buffer(-0.1 * dia).is_empty
    assert coverage.area == pytest.approx(selection.intersection(zone).area + residual.area, abs=1e-3)
    # the uncut strip at the straight edges (flash x = 5.4, trace x = 7) is the normal edge error, not a clearance
    if case != 'negative_offset':
        flash_strip = uncut_strip(domain, swept, 5.4, -1, box(4.0, 4.7, 5.4, 5.3))
        trace_strip = uncut_strip(domain, swept, 7.0, 1, box(7.0, 3.8, 7.6, 6.2))
        assert flash_strip <= 0.01 * dia and trace_strip <= 0.01 * dia, (flash_strip, trace_strip)


def test_paint_exclusion_on_the_selection_edge_keeps_the_cutter_inside_the_selection(excl_board, qapp, tmp_path):
    """
    A zone that ends on the centre line of a trace as wide as the tool: the strict boundary must not re-join the cut
    centre line around the zone through the stock outside the selection (the cutter would cut outside the copper).
    """
    dia, zone = 0.9, box(6.0, 4.9, 7.0, 5.1)            # the 1 mm trace at x = 7 leaves 0.05 mm of room per side
    selection = unary_union(regions.buffer_geometry(fused_solid(excl_board), 0.0))
    tools = {dia: dict(method=0, overlap=20, connect=False, contour=True)}
    from unittest.mock import patch
    from appPlugins.ToolPaint.PaintGen import PaintGen
    original = PaintGen._enforce_exclusion

    def enforce_on_the_centre_line(self, found, painted_object):
        # the kernels keep clear of the zone themselves: hand the strict boundary the one centre line that fails it
        for tool in found.values():
            tool['solid_geometry'] = [LineString([(7.0, 2.05), (7.0, 7.95)])]
        original(self, found, painted_object)

    with patch.object(PaintGen, '_enforce_exclusion', enforce_on_the_centre_line):
        paths = excluded_run(qapp, tmp_path, excl_board, tools, zone)[dia]
    assert paths and nearest_to(paths, zone) >= dia / 2 - STRICT
    # the normal edge error of the kernels is far below 1 um here
    for path in paths:
        assert selection.covers(path) and shapely.distance(path, selection.boundary) >= dia / 2 - 1e-3, path.wkt


def test_paint_exclusion_with_sharp_corners_shows_no_chord_error(excl_board, qapp, tmp_path):
    """A needle-sharp zone corner: the chord error of the round buffers must not bring the cutter nearer than r."""
    needle = Polygon([(5.3, 5.0), (7.0, 5.15), (7.0, 4.85)])
    paths = excluded_run(qapp, tmp_path, excl_board, {0.2: dict(method=0, overlap=20, connect=False, contour=True)},
                         needle)
    assert nearest_to(paths[0.2], needle) >= 0.1 - STRICT
    domain = physical_domain_of(excl_board, 0.0, needle)
    # engine residual: what the round cutter can reach (the domain opened by r) is cleared; the acute wedges of the
    # needle are out of reach for any cutter
    reachable = domain.buffer(-0.1, 32).buffer(0.1, 32)
    assert domain.difference(swept_by(paths)).intersection(reachable).buffer(-0.02).is_empty


def test_paint_exclusion_fragments_keep_the_cut_direction(excl_board, qapp, tmp_path):
    """
    Every outer ring (pieces cut or re-joined by the zone too) has the wall on the same side of the travel: all on
    one side for climb, all on the other for conv.
    """
    zone = Polygon([(5.3, 5.0), (7.0, 5.15), (7.0, 4.85)])
    wall = physical_domain_of(excl_board, 0.0, zone).boundary
    sides = {}
    for direction in ('climb', 'conv'):
        tools = {0.2: dict(method=0, overlap=20, connect=False, contour=True, clear_milling=direction)}
        found = set()
        for piece in excluded_run(qapp, tmp_path / direction, excl_board, tools, zone)[0.2]:
            coords = piece.coords
            if piece.length < 0.3:
                continue
            (x0, y0), (x1, y1) = coords[len(coords) // 2 - 1], coords[len(coords) // 2]
            middle = Point((x0 + x1) / 2, (y0 + y1) / 2)
            if not 0.09 < wall.distance(middle) < 0.12:
                continue
            wx, wy = shapely.ops.nearest_points(middle, wall)[1].coords[0]
            found.add(math.copysign(1, (x1 - x0) * (wy - middle.y) - (y1 - y0) * (wx - middle.x)))
        sides[direction] = found
    assert len(sides['climb']) == 1 and len(sides['conv']) == 1 and sides['climb'] != sides['conv'], sides


LASER_CASES = {
    # the zone covers the centre of the flash and leaves the ring of copper around it
    'flash_centre': (box(4.6, 4.6, 5.4, 5.4), lambda p: p.distance(Point(5, 5)) > 0.4),
    # the zone crosses the follow line of the trace and leaves copper on both sides
    'trace_crossed': (box(6.4, 4.5, 7.6, 5.5), lambda p: p.bounds[1] > 5.5 or p.bounds[3] < 4.5),
}


@pytest.mark.parametrize('case', list(LASER_CASES))
def test_paint_laser_lines_with_an_exclusion_keep_the_free_material(case, excl_board, qapp, tmp_path):
    """
    Laser lines find their elements before the zone is subtracted, then clip the center lines. Selection coverage:
    the copper outside the zone neighbourhood keeps its paths; none comes nearer than r; there is no swarm of
    fragments.
    """
    zone, on_free_copper = LASER_CASES[case]
    tools = {0.3: dict(method=3, overlap=20, connect=True, contour=True)}
    before = excluded_run(qapp, tmp_path / 'a', excl_board, tools, None)[0.3]
    paths = excluded_run(qapp, tmp_path / 'b', excl_board, tools, zone)[0.3]
    assert paths and nearest_to(paths, zone) >= 0.15 - STRICT
    assert any(on_free_copper(p) for p in paths)
    if case == 'trace_crossed':
        assert any(p.bounds[1] > 5.5 for p in paths) and any(p.bounds[3] < 4.5 for p in paths)
    assert len(paths) <= 2 * len(before) + 8
    # the clip adds no short fragments (the kernel itself may leave a degenerate path, with or without the zone)
    assert sum(p.length < 0.015 for p in paths) <= sum(p.length < 0.015 for p in before)


def test_paint_tcl_exclude_gives_the_paths_of_the_gui_zone(excl_board, qapp, tmp_path):
    """`paint -exclude <object>` draws the same paths as the same zone in the panel."""
    from tclCommands.TclCommandPaint import TclCommandPaint
    row = dict(method=0, overlap=20, connect=False, contour=True)
    gui = excluded_run(qapp, tmp_path / 'gui', excl_board, {0.2: row}, FLASH_AND_TRACE_CUT)

    def script(plugin):
        app = plugin.app
        app.paint_tool = plugin.tool
        app.collection.objects['keep_out'] = SimpleNamespace(
            kind='geometry', solid_geometry=[FLASH_AND_TRACE_CUT], obj_options={'name': 'keep_out'})
        TclCommandPaint(app).execute(
            {'name': 'board', 'tooldia': '0.2', 'method': 'standard', 'overlap': 20.0, 'offset': 0.0, 'connect': '0',
             'contour': '1', 'rest': '0', 'final_cleanup': '0', 'exclude': 'keep_out', 'outname': 'board_paint'}, [])

    tcl = excluded_run(qapp, tmp_path / 'tcl', excl_board, {0.2: row}, None, tcl=script)
    as_coords = lambda found: [[tuple(round(c, 6) for c in p.coords[0]), p.length] for p in found[0.2]]  # noqa: E731
    assert tcl[0.2] and as_coords(tcl) == as_coords(gui)


if __name__ == '__main__':
    unittest.main()


# ==============================================================================================================
# the zones and the selection of the shared controller (Paint and NCC)
# ==============================================================================================================

def _zone_source():
    from appObjects.AppObjectTemplate import FlatCAMObj
    obj = MagicMock(spec=FlatCAMObj)
    obj.deleted = False
    return obj


def test_closed_panel_does_not_show_the_zones_again_on_a_project_click(painter):
    obj = _zone_source()
    painter.app.collection.objects['board'] = obj
    painter.refresh_zone_view()
    obj.set_zones_panel_open.assert_called_with(True)
    painter.on_plugin_cleanup()
    obj.set_zones_panel_open.assert_called_with(False)
    obj.set_zones_panel_open.reset_mock()
    painter.refresh_zone_view()             # a click in the project tree
    obj.set_zones_panel_open.assert_not_called()


def test_a_plugin_panel_replaced_by_any_path_stops_the_zone_view(painter):
    from appTool import AppTool
    obj = _zone_source()
    painter.app.collection.objects['board'] = obj
    painter.refresh_zone_view()
    obj.set_zones_panel_open.reset_mock()
    painter.app.ui.plugin_scroll_area.takeWidget.return_value = painter
    AppTool.release_plugin_panel(painter.app)       # editor exit, Distance, project switch ...
    obj.set_zones_panel_open.assert_called_with(False)
    obj.set_zones_panel_open.reset_mock()
    painter.refresh_zone_view()
    obj.set_zones_panel_open.assert_not_called()


def test_add_area_without_a_source_object_does_not_start_the_selection(painter):
    painter.app.collection.objects.clear()
    painter.ui.obj_combo.currentText = lambda: ''
    painter.on_add_exclusion()
    assert not painter.selector.active


def test_paint_area_shape_preference_sets_the_exclusion_shape(painter):
    painter.app.options["tools_paint_area_shape"] = 'polygon'
    painter.set_tool_ui()
    assert painter.ui.excl_shape_radio.get_value() == 'polygon'


def test_polygon_selection_keeps_only_the_clicked_polygons():
    from appPlugins.Common.clearing.selection import AreaSelector, CLICK
    big, small = box(0, 0, 10, 10), box(2, 2, 4, 4)         # overlapping, not fused
    app = MagicMock()
    app.plotcanvas.translate_coords.side_effect = lambda xy: xy
    app.event_is_dragging = False
    sel = AreaSelector(MagicMock(app=app))
    got = []
    sel.mode, sel.commit, sel.faces = CLICK, lambda points, faces: got.append(faces), [big, small]
    click = lambda x, y: SimpleNamespace(pos=(x, y), button=1)
    sel.on_click_release(click(3, 3))       # inside both: the click picks the first one only
    sel.finish()
    assert len(got) == 1 and len(got[0]) == 1 and got[0][0] is big
