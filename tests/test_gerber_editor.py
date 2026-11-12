"""Gerber Editor tests: pick, history, selection, apertures, drawing, vertices, bends, transform, move, corner and segment drag."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, Point, box
from appEditors.appGerberEditor import AppGerberEditor, EraserEditorGrb, ImportEditorGrb, SelectEditorGrb
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appHandlers.appIO import appIO
from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
import inspect
from unittest.mock import Mock
import pytest
from PyQt6 import QtCore
from appEditors.appGerberEditor import RegionEditorGrb, TrackEditorGrb
from appEditors.editor_corner import corner_point
import math
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import unary_union
from appEditors.appGerberEditor import AppGerberEditor, MarkEditorGrb, PadArrayEditorGrb, PadEditorGrb
from appEditors.grb_plugins.GrbBufferPlugin import BufferEditorTool
from appEditors.grb_plugins.GrbCommon import DrawToolShape, gerber_steps
from appEditors.grb_plugins.GrbTrackPlugin import GrbTrackEditorUI
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402
from PyQt6 import QtWidgets
from shapely.geometry import Point, Polygon, box
from appEditors.appGerberEditor import SimplifyEditorGrb
import sys
import numpy as np
from shapely.affinity import rotate, scale, translate
from shapely.geometry import Point, box
from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins import GrbTransformationPlugin
from appEditors.grb_plugins.GrbTransformationPlugin import TransformEditorTool, queue_transform
from shapely.affinity import translate
from appEditors.appGerberEditor import (
    AppGerberEditor,
    CopyEditorGrb,
    MoveEditorGrb,
    ImportEditorGrb,
    PoligonizeEditorGrb,
    TrackEditorGrb,
)
from test_editor_save_modes import gerber_stand_in, gerber_storage
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR, export_gerber_text  # noqa: F401,E402
from shapely import LineString, Point
from shapely.geometry import Polygon
from appEditors.grb_plugins import GrbSelectionPlugin as selection_module
from shapely import Point, box
from appEditors.grb_plugins import GrbAperturePlugin as aperture_module
from shapely.affinity import rotate, translate
from shapely.geometry import box, MultiPolygon
from appEditors.grb_plugins.GrbDrawingPlugin import rectangle_region, text_region
from appParsers.ParseFont import ParseFont
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, _headless_qt, make_gerber, NATIVE_GBR, reparse_gerber,
)
from camlib import gerber_quad_segs
from appEditors import appGerberEditor as parity_module
from shapely.geometry import LineString, box
from appEditors.grb_plugins.GrbVertices import vertex_plan
from tests.helpers_objects import _headless_qt  # noqa: F401,E402
from shapely import LineString, Point, Polygon, box
from appEditors.grb_plugins import GrbAutoPlugin as auto_module
from pathlib import Path
from shapely import LineString, MultiLineString, MultiPolygon, Point, Polygon
from tests.helpers_objects import _headless_qt, make_app, make_gerber  # noqa: F401,E402
from shapely.geometry import box, MultiPolygon, LineString
from appEditors.appGerberEditor import ImportEditorGrb
from unittest.mock import MagicMock, Mock
from appEditors.appGeoEditor import AppGeoEditor, FCMove, DrawToolShape as GeoShape
from appEditors.appGerberEditor import MoveEditorGrb, CopyEditorGrb
from appEditors.grb_plugins.GrbCommon import DrawToolShape as GrbShape
from shapely.geometry import LineString, Polygon, MultiLineString, box
from appEditors.segment_ops import segment_geometries
from appEditors.appGeoEditor import AppGeoEditor
from tests.test_geo_editor import drag_editor
from tests.test_geo_editor import FakeCollection_vertex_grips as FakeCollection


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'resources')


GRID = 2.54


CONTROL = QtCore.Qt.KeyboardModifier.ControlModifier


NO_MOD = QtCore.Qt.KeyboardModifier.NoModifier


_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeCanvas:
    """Only what the editor asks the canvas for."""

    def __init__(self, pixel=0.05):
        self.pixel = pixel
        self.graph_event_connect = MagicMock()
        self.graph_event_disconnect = MagicMock()

    def pixel_size(self):
        return self.pixel

    @staticmethod
    def translate_coords(pos):
        return pos[0], pos[1]


def snap(x, y):
    return round(x / GRID) * GRID, round(y / GRID) * GRID


def trace(x0, y0, x1, y1, width=0.1, with_follow=True):
    line = LineString([(x0, y0), (x1, y1)])
    geo = {'solid': line.buffer(width / 2, cap_style='flat')}
    if with_follow:
        geo['follow'] = line
    return DrawToolShape(geo)


def pad(cx, cy, radius, with_follow=True):
    geo = {'solid': Point(cx, cy).buffer(radius, 32)}
    if with_follow:
        geo['follow'] = Point(cx, cy)
    return DrawToolShape(geo)


def aperture(shapes, kind='C'):
    return {'type': kind, 'size': 0.1, 'geometry': list(shapes)}


def make_editor(storage=None, pixel=0.05, grid=False):
    app = MagicMock()
    app.options = {'global_mselect_key': 'Control', 'gerber_circle_steps_mode': 'fixed',
                   'gerber_circle_steps': 64}
    app.decimals = 4
    app.app_units = 'MM'
    app.resource_location = ASSETS
    app.selection_type = None
    app.grid_on = grid
    app.grid_status = lambda: app.grid_on
    app.geo_editor.snap = snap
    app.plotcanvas = MagicMock()

    calls = {'row': 0, 'sel': 0}
    original_row = AppGerberEditor.on_row_selected
    original_sel = AppGerberEditor.on_table_selection

    def row_spy(self, row, col):
        calls['row'] += 1
        return original_row(self, row, col)

    def sel_spy(self, *args):
        calls['sel'] += 1
        return original_sel(self)

    with patch.object(AppGerberEditor, 'on_row_selected', row_spy), \
            patch.object(AppGerberEditor, 'on_table_selection', sel_spy), \
            patch('appEditors.appGerberEditor.ShapeCollection', create=True):
        editor = AppGerberEditor(app)

    editor.calls = calls
    editor.canvas = FakeCanvas(pixel)
    editor.plot_all = MagicMock()
    editor.build_ui = MagicMock()
    editor.select_tool = MagicMock()
    set_storage(editor, storage or {})
    return editor


def set_storage(editor, storage):
    editor.storage_dict = storage
    editor.touch_geometry()
    table = editor.ui.apertures_table
    with QtCore.QSignalBlocker(table):
        table.setRowCount(len(storage))
        for row, key in enumerate(storage):
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(key)))


def run_transform(tool, kind, params):
    """The Transformation plugin path of the UI (queue_transform), with the worker job run on the spot."""
    if queue_transform(tool, kind, params) == 'fail':
        return 'fail'
    job = tool.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()


def make_select(editor):
    tool = SelectEditorGrb(editor)
    editor.active_tool = tool
    return tool


def event(x, y, button=1, dragging=0):
    return SimpleNamespace(pos=(x, y), button=button, is_dragging=dragging)


def press(editor, x, y):
    editor.on_canvas_click(event(x, y))


def move(editor, x, y):
    editor.on_canvas_move(event(x, y, dragging=1))


def release(editor, x, y):
    editor.on_canvas_click_release(event(x, y))


def click(editor, x, y):
    press(editor, x, y)
    release(editor, x, y)


def modifiers(value):
    return patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=value)


class PickCase(unittest.TestCase):
    def tearDown(self):
        # nothing is kept between the tests
        pass


class TestHitRule(PickCase):
    def test_thin_trace_hit_inside_tolerance(self):
        trc = trace(0, 0, 10, 0)
        ed = make_editor({10: aperture([trc])})
        make_select(ed)
        click(ed, 5, 0.25)          # 0.2 off the edge: 4 px
        self.assertEqual(ed.selected, [trc])
        click(ed, 5, 0.45)          # 0.4 off the edge: 8 px
        self.assertEqual(ed.selected, [])

    def test_overlap_picks_small_pad(self):
        for order in ((10, 11), (11, 10)):
            big, small = pad(0, 0, 1.0), pad(0.1, 0, 0.2)
            ed = make_editor({order[0]: aperture([big]), order[1]: aperture([small])})
            make_select(ed)
            click(ed, 0.1, 0.05)
            self.assertEqual(ed.selected, [small], order)


class TestIndexFreshness(PickCase):
    def setUp(self):
        self.a = pad(0, 0, 1.0)
        self.b = pad(10, 0, 1.0)
        self.ed = make_editor({10: aperture([self.a, self.b])}, pixel=0.01)
        make_select(self.ed)
        self.assertIsNotNone(self.ed.pick_at((0, 0)))

    def brute(self, point):
        return [shape for shape in sum((v['geometry'] for v in self.ed.storage_dict.values()), [])
                if shape.geo['solid'].intersects(Point(point))]


class TestModifiers(PickCase):
    def test_ctrl_adds(self):
        a, b = pad(0, 0, 1.0), pad(10, 0, 1.0)
        ed = make_editor({10: aperture([a, b])})
        make_select(ed)
        with modifiers(CONTROL):
            click(ed, 0, 0)
            click(ed, 10, 0)
            self.assertEqual(ed.selected, [a, b])
            click(ed, 0, 0)
            self.assertEqual(ed.selected, [b])
        click(ed, 0, 0)
        self.assertEqual(ed.selected, [a])


class TestOverlapAndCycle(PickCase):

    def test_repeated_click_cycles_through_the_shapes(self):
        big, mid, small = pad(0, 0, 3.0), pad(0, 0, 2.0), pad(0, 0, 1.0)
        ed = make_editor({10: aperture([big, mid, small])})
        make_select(ed)
        order = []
        for __ in range(4):
            click(ed, 0.1, 0.1)
            self.assertEqual(len(ed.selected), 1)
            order.append(ed.selected[0])
        # small first (smallest area), then mid, then big, then small again
        self.assertEqual(order, [small, mid, big, small])
        ed.app.inform.emit.assert_called_with(
            'Shape 1 / 3. Click again at the same place to select the next one.')


class TestClickOrDrag(PickCase):
    def setUp(self):
        self.shape = pad(0, 0, 1.0)
        self.ed = make_editor({10: aperture([self.shape])})
        self.tool = make_select(self.ed)
        self.ed.draw_selection_area_handler = MagicMock()
        self.release_spy = MagicMock(side_effect=self.tool.click_release)
        self.tool.click_release = self.release_spy


    def test_six_pixel_move_on_empty_canvas_is_area_drag(self):
        # Starting on a shape now moves it; an area selection starts on empty canvas.
        press(self.ed, 2, 0)
        move(self.ed, 2.3, 0)
        release(self.ed, 2.3, 0)
        self.ed.draw_selection_area_handler.assert_called_once()
        self.release_spy.assert_not_called()


class TestBulkSelection(PickCase):
    def setUp(self):
        self.a = DrawToolShape({'solid': box(0, 0, 1, 1)})
        self.b = DrawToolShape({'solid': box(1, 0, 3, 1)})
        self.far = DrawToolShape({'solid': box(10, 0, 11, 1)})
        self.ed = make_editor({11: aperture([self.far]), 10: aperture([self.a, self.b])})
        make_select(self.ed)
        self.ed.plot_selection = MagicMock()

    def test_rectangle_contains_and_intersects(self):
        self.ed.draw_selection_area_handler((-0.5, -0.5), (2, 2), True)
        self.assertEqual(self.ed.selected, [self.a])
        self.ed.selected = []
        self.ed.draw_selection_area_handler((2, 2), (-0.5, -0.5), False)
        self.assertEqual(self.ed.selected, [self.a, self.b])
        self.assertEqual(self.ed.calls, {'row': 0, 'sel': 0})
        self.ed.plot_all.assert_not_called()
        self.assertEqual(float(self.ed.ui.area_entry.get_value()), 3)


class TestEraserMake(PickCase):
    def eraser(self, ed, cx, cy, size=1.0):
        shape = DrawToolShape({'solid': box(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)})
        ed.selected = [shape]
        tool = EraserEditorGrb(ed)
        tool.origin = (cx, cy)
        tool.destination = (cx, cy)
        ed.active_tool = tool
        return tool

    def test_eraser_make_keeps_follow(self):
        trc = trace(0, 0, 10, 0, 0.4)
        ed = make_editor({10: aperture([trc])})
        self.eraser(ed, 5, 0).make()
        shapes = ed.storage_dict[0]['geometry']
        self.assertEqual(len(shapes), 1)
        follow = shapes[0].geo['follow']
        self.assertIn(follow.geom_type, ('LineString', 'MultiLineString'))
        self.assertAlmostEqual(follow.length, 9.0, places=3)
        self.assertTrue(follow.within(LineString([(0, 0), (10, 0)]).buffer(1e-6)))


class TestEditorOperations(PickCase):


    def test_scale_resolves_aperture_keys_and_scales_each_row_once(self):
        for key in (10, '10'):
            with self.subTest(key=key):
                shape = pad(0, 0, 1)
                ed = make_editor({key: aperture([shape])})
                ed.selected = [shape]
                ed.sync_aperture_rows({key})
                table = ed.ui.apertures_table
                with QtCore.QSignalBlocker(table):
                    table.setItem(0, 2, QtWidgets.QTableWidgetItem('C'))
                    table.selectRow(0)
                ed.ui.scale_factor_entry.set_value(2)
                area = shape.geo['solid'].area
                ed.on_scale()
                job = ed.app.worker_task.emit.call_args.args[0]
                job['fcn'](*job['params'])
                QtWidgets.QApplication.processEvents()
                scaled = ed.storage_dict[key]['geometry'][0]
                self.assertAlmostEqual(scaled.geo['solid'].area, area * 4)
                self.assertIn(ed.selected[0], ed.storage_dict[key]['geometry'])
                ed.app.log.error.assert_not_called()


    def test_import_merges_only_matching_aperture_types(self):
        for existing_type, imported_type in (('R', 'O'), ('O', 'R'), ('R', 'R'), ('O', 'O')):
            with self.subTest(existing=existing_type, imported=imported_type):
                draw_app = MagicMock()
                draw_app.storage_dict = {
                    10: {'type': existing_type, 'size': 2.2, 'width': 2, 'height': 1, 'geometry': []}
                }
                imported = pad(5, 5, 1)
                tool = SimpleNamespace(app=draw_app.app, draw_app=draw_app, sel_storage=[{
                    'type': imported_type, 'size': 2.2, 'width': 2, 'height': 1, 'geometry': [imported]
                }])
                ImportEditorGrb.import_shapes(tool)
                destination = 10 if existing_type == imported_type else 11
                self.assertEqual(draw_app.storage_dict[destination]['type'], imported_type)
                self.assertIn(imported, draw_app.storage_dict[destination]['geometry'])


class RecordingShapes:
    """The canvas collection: it keeps what is added. The real one draws only Polygon, LineString, LinearRing."""

    def __init__(self):
        self.added, self.last = {}, 0

    def add(self, shape=None, **kwargs):
        self.last += 1
        self.added[self.last] = shape
        return self.last

    def remove(self, key):
        self.added.pop(key, None)

    def clear(self, update=False):
        self.added.clear()

    def redraw(self, *args, **kwargs):
        pass


def editor_save(app, source, tools, loaded_as_regions=False, native_load=False):
    """The real build_edited_gerber() (the worker step of the save) on a stand-in editor, fed with a storage built
    from ``tools`` (what the editor holds after the load). Returns the detached edited Gerber object."""
    storage = {}
    for ap, tool in tools.items():
        storage[ap] = {k: v for k, v in tool.items() if k != 'geometry'}
        storage[ap]['geometry'] = [SimpleNamespace(geo=dict(rec)) for rec in tool['geometry']]
    app.app_units = 'MM'
    app.f_handlers = appIO(app)
    app.custom_signal = MagicMock()
    app.main_thread = QtCore.QThread.currentThread()
    stand_in = SimpleNamespace(app=app, gerber_obj=source, gerber_obj_options=source.obj_options, results=[1],
                               loaded_as_regions=loaded_as_regions, native_load=native_load)
    AppGerberEditor.build_edited_gerber(stand_in, 'edited', storage)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'obj' in result, result
    return result['obj']


def test_corner_point_equal_to_an_endpoint_is_dropped():
    assert corner_point(3, (0.0, 0.0), (0.0, 4.0)) is None
    assert corner_point(4, (0.0, 0.0), (4.0, 0.0)) is None
    assert corner_point(3, (0.0, 0.0), (4.0, 0.0)) is None
    assert corner_point(1, (0.0, 0.0), (4.0, 1e-12)) is None


# ---------------------------------------------------------------------------------------------------------------------
# Gerber Region and Track
# ---------------------------------------------------------------------------------------------------------------------
def grb_tool(cls, mode, points, snap=True):
    tool = cls.__new__(cls)
    tool.dont_execute = False
    ui = SimpleNamespace(grid_snap_btn=SimpleNamespace(isChecked=lambda: snap),
                         grid_gap_x_entry=SimpleNamespace(get_value=lambda: 1.0),
                         grid_gap_y_entry=SimpleNamespace(get_value=lambda: 1.0))
    tool.draw_app = SimpleNamespace(bend_mode=mode, app=SimpleNamespace(ui=ui, inform=Mock()),
                                    in_action=False, last_length=0, snap_x=0.0, snap_y=0.0,
                                    tool_shape=Mock(), draw_utility_geometry=Mock())
    tool.points = list(points)
    tool.quad_segs = 2
    tool.buf_val = 0.001
    tool.gridx_size = tool.gridy_size = 1.0
    tool.temp_points = []
    tool.inter_point = None
    if cls is RegionEditorGrb:
        tool.reg_tool = SimpleNamespace(length=0)
        tool.ui = SimpleNamespace(x_entry=Mock(), y_entry=Mock())
    return tool


def region_click(tool, target):
    tool.utility_geometry(data=target)
    tool.click(target)


@pytest.mark.parametrize('mode', [1])
def test_region_diagonal_gives_no_extra_vertex(mode):
    tool = grb_tool(RegionEditorGrb, mode, [(0.0, 0.0)])
    # a stale corner from a previous mouse position must not survive
    tool.utility_geometry(data=(5.0, 2.0))
    assert tool.inter_point is not None
    tool.utility_geometry(data=(3.0, 3.0))
    assert tool.inter_point is None
    assert tool.temp_points == [(0.0, 0.0), (3.0, 3.0)]
    tool.click((3.0, 3.0))
    assert tool.points == [(0.0, 0.0), (3.0, 3.0)]


def test_region_modes_work_without_grid_snap_on_off_grid_points():
    tool = grb_tool(RegionEditorGrb, 1, [(0.25, 0.25)], snap=False)
    region_click(tool, (4.5, 2.125))
    assert tool.points[0] == (0.25, 0.25)
    assert len(tool.points) == 3
    corner = tool.points[1]
    assert corner[1] == pytest.approx(0.25)
    assert abs(4.5 - corner[0]) == pytest.approx(abs(2.125 - corner[1]))


def test_region_backspace_pops_exactly_what_the_click_added():
    tool = grb_tool(RegionEditorGrb, 1, [])
    tool.click((0.0, 0.0))                       # 1 point
    region_click(tool, (5.0, 2.0))               # corner + point
    region_click(tool, (8.0, 2.0))               # aligned: 1 point
    region_click(tool, (10.0, 4.0))              # diagonal from (8, 2): 1 point
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0), (8.0, 2.0), (10.0, 4.0)]
    assert tool.click_sizes == [1, 2, 1, 1]
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0), (8.0, 2.0)]
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0)]
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0)]


def track_tool_with_storage(mode):
    tool = grb_tool(TrackEditorGrb, mode, [])
    storage = []
    tool.draw_app.storage_dict = {10: {'geometry': storage}}
    tool.draw_app.last_aperture_selected = 10
    tool.draw_app.add_gerber_shape = lambda shape, store: store.append(shape)
    tool.draw_app.plot_all = Mock()
    tool.track_tool = SimpleNamespace(length=0)
    tool.ui = SimpleNamespace(x_entry=Mock(), y_entry=Mock())
    tool.click_shapes = []
    return tool, storage


def track_click(tool, target):
    tool.utility_geometry(data=target)
    tool.click(target)


def test_track_backspace_removes_the_last_point_and_its_segment():
    tool, storage = track_tool_with_storage(1)
    track_click(tool, (0.0, 0.0))
    track_click(tool, (5.0, 2.0))
    track_click(tool, (8.0, 2.0))
    assert len(storage) == 3
    kept = storage[:2]

    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0), (5.0, 2.0)]
    assert storage == kept

    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [] and storage == [] and tool.temp_points == []
    # nothing left to remove: no error
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert storage == []


def snap_editor(shapes):
    editor = make_editor({10: aperture(shapes)}, grid=True)
    editor.app.call_source = 'grb_editor'
    editor.app.grb_editor = editor
    editor.app.geo_editor.app = editor.app
    editor.app.geo_editor.editor_options = dict(
        grid_snap=False, corner_snap=False, global_snap_max=0.05,
        global_gridx=1.0, global_gridy=1.0)
    editor.app.ui.grid_gap_link_cb.isChecked.return_value = False
    editor.canvas = SimpleNamespace(translate_coords_2=lambda p: (p[0] * 100, p[1] * 100),
                                   translate_coords=lambda p: (p[0] / 100, p[1] / 100))
    return editor


def test_follow_corners_flash_centers_multipart_and_grid_competition():
    editor = snap_editor([
        DrawToolShape({'follow': MultiLineString([[(0, 0), (10, 0)], [(10, 0), (10, 10)]])}),
        DrawToolShape({'follow': Point(20, 20)}),
        DrawToolShape({'solid': box(30, 30, 32, 32)})])
    options = editor.app.geo_editor.editor_options
    options['corner_snap'] = True
    assert editor.snap(10.01, 0.01) == (10, 0)
    assert editor.snap(20.01, 20) == (20, 20)
    assert editor.snap(30.01, 30) == (30.01, 30)
    options.update(grid_snap=True, global_gridx=0.1, global_gridy=0.2)
    assert editor.snap(0.14, 0.14) == pytest.approx((0.1, 0.2))
    editor.app.ui.grid_gap_link_cb.isChecked.return_value = True
    assert editor.snap(0.14, 0.14) == pytest.approx((0.1, 0.1))


@pytest.mark.parametrize('grid', [True])
def test_track_pad_snap_preview_click_and_return_to_global_grid(grid):
    center = (0.23, 0.37)
    pad = DrawToolShape({'solid': Point(center).buffer(0.8), 'follow': Point(center)})
    destination = (7.23, 3.37)
    other_pad = DrawToolShape({'solid': Point(destination).buffer(0.8), 'follow': Point(destination)})
    track = DrawToolShape({'solid': box(3, 0, 5, 1), 'follow': LineString([(3, 0.5), (5, 0.5)])})
    editor = snap_editor([pad, other_pad, track])
    editor.app.grid_on = grid
    editor.app.geo_editor.editor_options['grid_snap'] = True
    tool, _ = track_tool_with_storage(1)
    tool.name = 'track'
    tool.complete = False
    tool.draw_app = editor
    tool.app = editor.app
    panel = QtWidgets.QWidget()
    tool.ui = GrbTrackEditorUI(QtWidgets.QVBoxLayout(panel),
                              SimpleNamespace(app=editor.app), 'Track')
    editor.active_tool = tool
    editor.last_aperture_selected = 10
    editor.app.ui.grid_gap_x_entry.get_value.return_value = 1.0
    editor.app.ui.grid_gap_y_entry.get_value.return_value = 1.0
    tool.ui.snap_to_pad_cb.setChecked(True)
    # The mouse is over the pad, but much nearer a grid point than its center.
    raw = (0.01, 0.01)
    event = SimpleNamespace(pos=tuple(v * 100 for v in raw), button=1, is_dragging=False)
    editor.on_canvas_move(event)
    assert (editor.snap_x, editor.snap_y) == center
    editor.on_canvas_click(event)
    assert tool.points == [center]
    assert editor.storage_dict[10]['geometry'][-1].geo['follow'].equals(Point(center))
    # A click without an intervening mouse move must commit the new endpoint, not stale preview geometry.
    event.pos = (460, 220)
    editor.on_canvas_click(event)
    endpoint = (5.0, 2.0) if grid else (4.6, 2.2)
    assert tool.points[-1] == endpoint
    follow = editor.storage_dict[10]['geometry'][-1].geo['follow']
    assert tuple(follow.coords[0]) == center
    assert tuple(follow.coords[-1]) == endpoint
    assert editor.snap(3.1, 0.1) == ((3.0, 0.0) if grid else (3.1, 0.1))
    event.pos = (701, 301)
    editor.on_canvas_move(event)
    assert (editor.snap_x, editor.snap_y) == destination
    editor.on_canvas_click(event)
    assert tuple(editor.storage_dict[10]['geometry'][-1].geo['follow'].coords[-1]) == destination
    tool.ui.snap_to_pad_cb.setChecked(False)
    assert editor.snap(*raw) == ((0.0, 0.0) if grid else raw)
    assert editor.app.geo_editor.editor_options['global_gridx'] == 1.0
    panel.deleteLater()


def range_editor():
    line = LineString([(0, 0), (3, 0), (3, 4)])
    first = DrawToolShape({'solid': box(0, 0, 2, 3), 'follow': line})
    second = DrawToolShape({'solid': box(0, 0, 4, 3), 'follow': Point(1, 1)})
    missing = DrawToolShape({'solid': box(0, 0, 2, 3)})
    editor = make_editor({10: aperture([first, missing]), '11': aperture([second])})
    editor.ui.size_area_chk.setChecked(True)
    editor.ui.size_area_min_entry.setText('6')
    editor.ui.size_area_max_entry.setText('6')
    editor.ui.size_length_chk.setChecked(True)
    editor.ui.size_length_min_entry.setText('7')
    editor.ui.size_length_max_entry.setText('7')
    return editor, first, second, missing


def test_size_ranges_are_inclusive_and_follow_length_is_not_solid_perimeter():
    editor, first, second, missing = range_editor()
    editor.ui.size_select_btn.click()
    assert editor.selected == [first]
    editor.selected = []
    editor.ui.size_length_chk.setChecked(False)
    editor.ui.size_length_min_entry.setText('invalid disabled value')
    assert editor.on_select_by_size() == [first, missing]
    editor.selected = [second]
    # Select searches the whole board, not only the current selection
    assert editor.on_select_by_size() == [first, missing]


@pytest.mark.parametrize('field,value', [
    ('size_area_min_entry', '7')])
def test_invalid_ranges_do_not_mutate_selection_geometry_or_history(field, value):
    editor, first, second, missing = range_editor()
    editor.selected = [first, second]
    getattr(editor.ui, field).setText(value)
    revision = editor.geometry_revision
    editor.ui.size_delete_btn.click()
    assert editor.selected == [first, second]
    assert editor.storage_dict[10]['geometry'] == [first, missing]
    assert editor.geometry_revision == revision
    assert not editor.history.undo_stack


def test_filtered_delete_is_one_step_with_original_selection_and_aperture_ownership():
    editor, first, second, missing = range_editor()
    editor.selected = [first, second]
    editor.ui.size_delete_btn.click()
    assert editor.storage_dict[10]['geometry'] == [missing]
    assert editor.storage_dict['11']['geometry'] == [second]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first, missing]
    assert editor.selected == [first, second]
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'] == [missing]


def scene():
    first = DrawToolShape({'solid': box(0, 0, 1, 1), 'follow': Point(0.5, 0.5)})
    second = DrawToolShape({'solid': box(10, 0, 11, 1), 'follow': Point(10.5, 0.5)})
    editor = make_editor({10: aperture([first]), 11: aperture([second])})
    editor.history.reset()
    return editor, first, second


def size_scene():
    small = DrawToolShape({'solid': box(0, 0, 2, 3)})
    wide = DrawToolShape({'solid': box(0, 0, 4, 3)})
    editor = make_editor({10: aperture([small]), '11': aperture([wide])})
    editor.history.reset()
    editor.ui.size_width_chk.setChecked(True)
    editor.ui.size_width_entry.setText('2')
    editor.ui.size_height_chk.setChecked(True)
    editor.ui.size_height_entry.setText('3')
    editor.ui.size_tolerance_entry.setText('0')
    editor.plot_selection = MagicMock()
    return editor, small, wide


def select_row(editor, row):
    table = editor.ui.apertures_table
    with QtCore.QSignalBlocker(table):
        table.selectRow(row)


def reconnect_item_changed(editor):
    table = editor.ui.apertures_table
    try:
        table.itemChanged.disconnect()
    except TypeError:
        pass
    table.itemChanged.connect(editor.on_tool_edit)
    return table


def run_queued_job(editor):
    job = editor.app.worker_task.emit.call_args.args[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(job['fcn'], *job['params']).result(timeout=10)
    QtWidgets.QApplication.processEvents()


def test_pure_move_of_round_follow_r_pad_does_not_mark_regions():
    first = pad(0, 0, 1)
    editor = make_editor({10: {'type': 'R', 'size': 1.0, 'geometry': [first]}})
    editor.history.reset()
    editor.selected = [first]
    tool = SimpleNamespace(draw_app=editor, app=editor.app)
    queue_transform(tool, 'offset', ['X', 2])
    run_queued_job(editor)
    assert first.geo['follow'].equals(Point(2, 0))
    assert len(editor.history.undo_stack) == 1
    assert editor.loaded_as_regions is False


def test_delete_by_size_acts_on_selection_when_any_else_on_whole_board():
    editor, small, wide = size_scene()
    editor.selected = [wide]
    # the selected shape does not match, so nothing is deleted and the board is not searched
    assert editor.on_delete_by_size() == []
    assert small in editor.storage_dict[10]['geometry']
    assert editor.history.undo_stack == []

    editor.selected = []
    assert editor.on_delete_by_size() == [small]
    assert small not in editor.storage_dict[10]['geometry']
    assert wide in editor.storage_dict['11']['geometry']


def test_intersection_of_disjoint_shapes_fails_and_keeps_sources():
    editor, first, second = scene()
    editor.selected = [first, second]
    before_first = editor.storage_dict[10]['geometry'][:]
    before_second = editor.storage_dict[11]['geometry'][:]
    assert editor.apply_region_operation('intersection') == 'fail'
    assert editor.storage_dict[10]['geometry'] == before_first
    assert editor.storage_dict[11]['geometry'] == before_second
    assert editor.selected == [first, second]
    assert editor.loaded_as_regions is False
    assert editor.history.undo_stack == []


@pytest.mark.parametrize('kind, cut', [('R', False), ('R', True), ('R', 'shallow'), ('O', False), ('O', 'shallow')])
def test_dimension_edit_resizes_only_plain_flashes(kind, cut):
    meta = {'type': kind, 'size': math.hypot(2.0, 1.0), 'width': 2.0, 'height': 1.0}
    editor = make_editor({})
    plain = box(-1, -0.5, 1, 0.5) if kind == 'R' else \
        Gerber.create_flash_geometry(editor, Point(0, 0), meta, gerber_steps(editor.app, 0.5))
    cutter = box(0.998, -0.1, 1.1, 0.1) if cut == 'shallow' else box(0.5, -1, 2, 1)
    solid = plain.difference(cutter) if cut else plain
    flash = DrawToolShape({'solid': solid, 'follow': Point(0, 0)})
    editor = make_editor({10: dict(meta, geometry=[flash])})
    editor.history.reset()
    editor.tid2apcode = {1: 10}
    editor.oldapcode_newapcode = {10: 10}
    table = editor.ui.apertures_table
    table.setRowCount(1)
    with QtCore.QSignalBlocker(table):
        table.setItem(0, 1, QtWidgets.QTableWidgetItem('10'))
        table.setItem(0, 4, QtWidgets.QTableWidgetItem('4, 2'))
        table.setCurrentCell(0, 4)
    reconnect_item_changed(editor)
    editor.on_tool_edit()
    (shape,) = editor.storage_dict[10]['geometry']
    if cut:
        assert shape.geo['solid'].equals(solid)
        assert editor.history.undo_stack == []
    else:
        assert shape.geo['solid'].bounds == pytest.approx((-2, -1, 2, 1), abs=1e-3)
        assert len(editor.history.undo_stack) == 1
        editor.app.options['gerber_circle_steps_mode'] = 'fixed'
        editor.app.options['gerber_circle_steps'] = 128
        with QtCore.QSignalBlocker(table):
            table.item(0, 4).setText('6, 3')
            table.setCurrentCell(0, 4)
        editor.on_tool_edit()
        (shape,) = editor.storage_dict[10]['geometry']
        assert shape.geo['solid'].bounds == pytest.approx((-3, -1.5, 3, 1.5), abs=1e-3)
        expected = box(-3, -1.5, 3, 1.5) if kind == 'R' else \
            Gerber.create_flash_geometry(editor, Point(0, 0), editor.storage_dict[10], 128)
        assert shape.geo['solid'].equals(expected)
        assert len(editor.history.undo_stack) == 2


@pytest.mark.parametrize('kind', ['scale'])
def test_scale_and_buffer_without_selected_shape_fail_without_history_step(kind):
    editor, first, second = scene()
    editor.selected = []
    select_row(editor, 0)
    editor.ui.scale_factor_entry.set_value(1.2)
    if kind == 'scale':
        result = editor.on_scale()
    else:
        result = BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), 0.1, 1)
    assert result == 'fail'
    assert editor.history.undo_stack == []
    assert first.geo['solid'].equals(box(0, 0, 1, 1))


@pytest.mark.parametrize('factor_value', [0])
def test_buffer_factor_at_or_below_zero_is_rejected(factor_value):
    editor, first, second = scene()
    editor.selected = [first]
    tool = SimpleNamespace(draw_app=editor, app=editor.app)
    assert queue_transform(tool, 'buffer', [factor_value, 1, True]) == 'fail'
    editor.app.worker_task.emit.assert_not_called()
    assert editor.history.undo_stack == []


def obround_editor(width, height):
    meta = {'type': 'O', 'size': math.hypot(width, height), 'width': width, 'height': height}
    editor = make_editor({12: dict(meta, geometry=[])})
    editor.gerber_obj = SimpleNamespace(obj_options={'name': 'board'})
    editor.last_aperture_selected = 12
    editor.history.reset()
    return editor, meta


def flash_of(editor, meta, x, y):
    steps = gerber_steps(editor.app, min(meta['width'], meta['height']) / 2)
    return Gerber.create_flash_geometry(editor, Point(x, y), meta, steps)


@pytest.mark.parametrize('width, height', [(1.7, 1.0)])
def test_pad_and_pad_array_place_obround_pads(width, height):
    editor, meta = obround_editor(width, height)
    tool = PadEditorGrb.__new__(PadEditorGrb)
    storage = editor.storage_dict[12]['geometry']
    tool.draw_app, tool.app, tool.storage_obj, tool.points = editor, editor.app, storage, (5.0, 5.0)
    tool.make()
    editor.active_tool = tool
    editor.on_grb_shape_complete(editor.current_storage)

    array = PadArrayEditorGrb.__new__(PadArrayEditorGrb)
    array.draw_app, array.app, array.storage_obj, array.points = editor, editor.app, storage, [0.0, 0.0]
    array.ui = MagicMock()
    array.array_type, array.pad_array_dir, array.pad_array_size, array.pad_pitch = 'linear', 'X', 2, 2.54
    array.make()
    editor.active_tool = array
    editor.on_grb_shape_complete(editor.current_storage)

    shapes = editor.storage_dict[12]['geometry']
    assert [shape.geo['follow'].coords[0] for shape in shapes] == [(5.0, 5.0), (0.0, 0.0), (2.54, 0.0)]
    for shape in shapes:
        x, y = shape.geo['follow'].coords[0]
        assert shape.geo['solid'].is_valid
        assert shape.geo['solid'].hausdorff_distance(flash_of(editor, meta, x, y)) < 1e-6


def test_dimension_edit_changes_r_tracks_by_half_the_change():
    # a 1 x 1 aperture drew a copper track and a clear track along X: both are 1 wide
    track = DrawToolShape({'solid': box(-0.5, -0.5, 10.5, 0.5), 'follow': LineString([(0, 0), (10, 0)])})
    clear = DrawToolShape({'clear': box(-0.5, 4.5, 10.5, 5.5), 'follow': LineString([(0, 5), (10, 5)])})
    editor = make_editor({10: {'type': 'R', 'size': math.hypot(1, 1), 'width': 1.0, 'height': 1.0,
                               'geometry': [track, clear]}})
    editor.history.reset()
    editor.tid2apcode = {1: 10}
    editor.oldapcode_newapcode = {10: 10}
    table = editor.ui.apertures_table
    with QtCore.QSignalBlocker(table):
        table.setItem(0, 4, QtWidgets.QTableWidgetItem('1.2, 1.2'))
        table.setCurrentCell(0, 4)
    reconnect_item_changed(editor)
    editor.on_tool_edit()
    new_track, new_clear = editor.storage_dict[10]['geometry']
    # the sweep of a 1.2 x 1.2 aperture along the same paths
    assert new_track.geo['solid'].equals(box(-0.6, -0.6, 10.6, 0.6))
    assert new_clear.geo['clear'].equals(box(-0.6, 4.4, 10.6, 5.6))
    assert len(editor.history.undo_stack) == 1


def mark(editor, upper, lower):
    editor.ui.ma_upper_threshold_entry.set_value(upper)
    editor.ui.ma_lower_threshold_entry.set_value(lower)
    tool = MarkEditorGrb(editor)
    tool.on_markarea_click()
    return tool


def test_mark_area_delete_removes_only_the_shapes_marked_now():
    editor, small, wide = size_scene()   # areas 6 and 12
    mark(editor, 13, 0).on_markarea_clear()   # both shapes marked, then Clear
    MarkEditorGrb(editor).on_markarea_delete()   # the tool opened again, nothing marked: Delete removes nothing
    assert editor.storage_dict[10]['geometry'] == [small]
    assert editor.storage_dict['11']['geometry'] == [wide]

    mark(editor, 13, 7).on_markarea_delete()   # only the wide shape is marked
    assert editor.storage_dict[10]['geometry'] == [small]
    assert editor.storage_dict['11']['geometry'] == []


def size_editor():
    small = DrawToolShape({'solid': box(0, 0, 2, 3)})
    wide = DrawToolShape({'solid': box(0, 0, 4, 3)})
    empty = DrawToolShape({'solid': Polygon()})
    follow = DrawToolShape({'follow': Point(0, 0)})
    editor = make_editor({10: aperture([small, empty]), '11': aperture([wide, follow])})
    editor.ui.size_width_chk.setChecked(True)
    editor.ui.size_width_entry.setText('2')
    editor.ui.size_height_entry.setText('3')
    editor.ui.size_tolerance_entry.setText('0')
    editor.plot_selection = MagicMock()
    return editor, small, wide


def test_size_filters_combine_and_select_searches_whole_board():
    editor, small, wide = size_editor()
    editor.selected = [wide]
    # Select ignores the current selection, so repeating it never only narrows
    assert editor.on_select_by_size() == [small]
    editor.ui.size_width_entry.setText('4,05')
    editor.ui.size_tolerance_entry.setText('0,1')
    editor.ui.size_height_chk.setChecked(True)
    assert editor.on_select_by_size() == [wide]
    editor.selected = [wide, small]
    editor.ui.size_height_entry.setText('8')
    assert editor.on_select_by_size() == []


@pytest.mark.parametrize('field,value', [
    ('size_width_entry', 'nan'),
])
def test_invalid_size_input_preserves_selection(field, value):
    editor, small, wide = size_editor()
    editor.selected = [wide]
    getattr(editor.ui, field).setText(value)
    assert editor.on_select_by_size() == 'fail'
    assert editor.selected == [wide]
    editor.plot_selection.assert_not_called()


def simplify_tool(editor, tolerance=0.1):
    tool = SimplifyEditorGrb.__new__(SimplifyEditorGrb)
    tool.app = editor.app
    tool.draw_app = editor
    tool.ui = SimpleNamespace(geo_tol_entry=MagicMock(), geo_coords_entry=MagicMock(),
                              geo_vertex_entry=MagicMock())
    tool.ui.geo_tol_entry.get_value.return_value = tolerance
    return tool


def run_job(editor):
    job = editor.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()


def test_simplification_failure_does_not_delete_any_selected_shape():
    first = DrawToolShape({'solid': box(0, 0, 2, 2)})
    bad = MagicMock()
    bad.simplify.side_effect = ValueError('bad geometry')
    second = DrawToolShape({'solid': bad})
    editor = make_editor({10: aperture([first, second])})
    editor.selected = [first, second]
    revision = editor.geometry_revision
    simplify_tool(editor).on_simplification_click()
    run_job(editor)
    assert editor.storage_dict[10]['geometry'] == [first, second]
    assert editor.selected == [first, second]
    assert editor.geometry_revision == revision
    assert not editor.is_modified
    editor.plot_all.assert_not_called()
    editor.app.inform.emit.assert_called()


@pytest.mark.parametrize('tolerance', [-1])
def test_simplification_rejects_invalid_tolerance(tolerance):
    editor = make_editor({10: aperture([DrawToolShape({'solid': box(0, 0, 1, 1)})])})
    editor.selected = list(editor.storage_dict[10]['geometry'])
    simplify_tool(editor, tolerance).on_simplification_click()
    editor.app.worker_task.emit.assert_not_called()


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


KEYS = ('solid', 'follow', 'clear')


class FakeShapes:
    """Stand-in of the shape collection: records the calls."""

    def __init__(self, transform_ok=True):
        self.last_key = 0
        self.added = []
        self.removed = []
        self.transformed = []
        self.redraws = 0
        self.transform_ok = transform_ok

    def add(self, **kwargs):
        self.last_key += 1
        self.added.append((self.last_key, kwargs))
        return self.last_key

    def remove(self, key, update=False):
        self.removed.append(key)

    def clear(self, update=False):
        pass

    def redraw(self, *args, **kwargs):
        self.redraws += 1

    def transform_keys(self, keys, matrix, update=True):
        self.transformed.append((list(keys), tuple(matrix)))
        return self.transform_ok


class Host:
    """Stand-in of the tool: the path the UI uses (queue_transform -> history job -> result applied on the GUI
    thread), run synchronously on the real editor and a fake app."""

    def __init__(self, draw_app):
        self.app = draw_app.app
        self.draw_app = draw_app
        self.messages = []
        self.app.inform.emit.side_effect = self.messages.append

    def run(self, kind, params):
        if queue_transform(self, kind, params) == 'fail':
            return 'fail'
        job = self.app.worker_task.emit.call_args.args[0]
        job['fcn'](*job['params'])
        QtWidgets.QApplication.processEvents()

    def on_rotate_action(self, val, point):
        return self.run('rotate', [val, point])

    def on_flip(self, axis, point):
        return self.run('flip', [axis, point])

    def on_skew(self, axis, xval, yval, point):
        return self.run('skew', [axis, xval, yval, point])

    def on_scale(self, axis, xfactor, yfactor, point):
        return self.run('scale', [axis, xfactor, yfactor, point])

    def on_offset(self, axis, num):
        return self.run('offset', [axis, num])

    def on_buffer_action(self, value, join, factor=None):
        return self.run('buffer', [value, join] if factor is None else [value, join, factor])


def full_shape(cx, cy):
    geo = {'solid': Point(cx, cy).buffer(0.5, 8), 'follow': Point(cx, cy), 'clear': Point(cx, cy).buffer(0.7, 8)}
    return DrawToolShape(geo)


def build(count=5, real_plot=False, transform_ok=True):
    shapes = [full_shape(i * 2.0, (i % 3) * 1.5) for i in range(count)]
    shapes.append(trace(-3, -1, 4, 2))
    editor = make_editor({10: aperture(shapes[:3]), 11: aperture(shapes[3:])})
    editor.app.options.update({'global_draw_color': '#0000FFFF', 'global_sel_draw_color': '#FF0000FF',
                               'global_theme': 'default'})
    editor.shapes = FakeShapes(transform_ok)
    editor.tolerance = None
    editor.plot_count = 0
    if real_plot:
        real = AppGerberEditor.plot_all.__get__(editor)

        def counted():
            editor.plot_count += 1
            real()
        editor.plot_all = counted
    else:
        def counted():
            editor.plot_count += 1
        editor.plot_all = counted
    editor.selected = list(shapes[:4]) + [shapes[-1]]
    host = Host(editor)
    return host, editor, shapes


def snapshot(shapes):
    return [{k: v for k, v in s.geo.items()} for s in shapes]


def reference(shapes_geo, fn):
    return [{k: fn(v) for k, v in geo.items() if k in KEYS} for geo in shapes_geo]


def same_geo(test, a, b):
    test.assertEqual(a.geom_type, b.geom_type)
    test.assertTrue(np.allclose(np.asarray(a.bounds), np.asarray(b.bounds), atol=1e-9))
    test.assertTrue(np.allclose(np.array(a.exterior.coords if hasattr(a, 'exterior') else a.coords),
                                np.array(b.exterior.coords if hasattr(b, 'exterior') else b.coords), atol=1e-9))


class TransformCase(unittest.TestCase):
    def check(self, action, ref_fn, selected_only=True):
        host, editor, shapes = build()
        before = snapshot(shapes)
        selected = list(editor.selected)
        action(host)
        sel_ids = {id(s) for s in selected}
        for shape, old in zip(shapes, before):
            if id(shape) in sel_ids:
                for key, expected in reference([old], ref_fn)[0].items():
                    same_geo(self, shape.geo[key], expected)
            else:
                for key, value in old.items():
                    self.assertIs(shape.geo[key], value)
        self.assertEqual(editor.plot_count, 1)
        self.assertEqual(editor.selected, selected)
        self.assertEqual(host.messages, [])
        return host, editor, shapes

    def test_rotate(self):
        self.check(lambda h: h.on_rotate_action(33.0, (1.5, -2.0)), lambda g: rotate(g, -33.0, origin=(1.5, -2.0)))

    def test_flip_x_and_y(self):
        self.check(lambda h: h.on_flip('X', (1.0, 2.0)), lambda g: scale(g, 1, -1, origin=(1.0, 2.0)))
        self.check(lambda h: h.on_flip('Y', (1.0, 2.0)), lambda g: scale(g, -1, 1, origin=(1.0, 2.0)))

    def test_offset(self):
        self.check(lambda h: h.on_offset('X', 3.25), lambda g: translate(g, 3.25, 0))
        self.check(lambda h: h.on_offset('Y', -1.5), lambda g: translate(g, 0, -1.5))

    def test_buffer_distance(self):
        host, editor, shapes = build()
        before = snapshot(shapes)
        selected = list(editor.selected)
        host.on_buffer_action(0.2, 'round')
        for shape, old in zip(shapes, before):
            if shape in selected:
                same_geo(self, shape.geo['solid'], old['solid'].buffer(0.2, quad_segs=16, join_style='round'))
                self.assertIs(shape.geo['follow'], old['follow'])
                if 'clear' in old:
                    same_geo(self, shape.geo['clear'], old['clear'].buffer(0.2, quad_segs=16, join_style='round'))
        self.assertEqual(editor.plot_count, 1)
        self.assertEqual(editor.selected, selected)
        self.assertEqual(host.messages, [])


@pytest.mark.parametrize('kind,params', [
    ('rotate', [float('inf'), (0, 0)]),
])
def test_invalid_transform_preserves_selection_and_history(kind, params):
    host, editor, shapes = build()
    before, selected = snapshot(shapes), list(editor.selected)
    assert queue_transform(host, kind, params) == 'fail'
    assert snapshot(shapes) == before
    assert editor.selected == selected
    assert not editor.history.undo_stack
    assert editor.history.pending is None


def test_scaling_eroded_pad_keeps_empty_solid_and_finite_follow():
    host, editor, shapes = build()
    editor.selected = [shapes[0]]
    host.on_buffer_action(-2, 1)
    host.on_buffer_action(1.2, 1, True)
    assert shapes[0].geo['solid'].is_empty
    assert shapes[0].geo['follow'].equals(Point(0, 0))


@pytest.mark.parametrize('kind,params', [
    ('offset', ['X', sys.float_info.max]),
])
def test_unrepresentable_worker_output_preserves_board_and_history(kind, params):
    host, editor, shapes = build()
    editor.selected = [shapes[-1]]
    host.on_offset('X', 1)
    assert editor.undo()
    before = snapshot(shapes)
    state = editor.history.state()
    revision = editor.geometry_revision
    undo, redo = list(editor.history.undo_stack), list(editor.history.redo_stack)
    host.messages.clear()
    results = []
    editor.geometry_result.disconnect()
    editor.geometry_result.connect(results.append)
    assert queue_transform(host, kind, params) != 'fail'
    job = editor.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()
    # Don't pass corrupt geometry into GEOS history/export checks while reproducing the failure.
    assert 'error' in results[0]
    editor.history.apply_result(results[0])
    assert snapshot(shapes) == before
    assert editor.history.state() == state
    assert editor.geometry_revision == revision
    assert editor.history.undo_stack == undo and editor.history.redo_stack == redo
    assert host.messages and not any(message.startswith('[success]') for message in host.messages)


def transformation_tool(editor):
    from defaults import AppDefaults
    editor.app.defaults = AppDefaults()
    return TransformEditorTool(editor.app, editor)


def test_stale_queued_result_never_reports_success():
    host, editor, shapes = build()
    tool = transformation_tool(editor)
    before = snapshot(shapes)
    tool.on_rotate(val=20, ref=0)
    job = editor.app.worker_task.emit.call_args.args[0]
    editor.touch_geometry()
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()
    assert snapshot(shapes) == before
    assert not editor.history.undo_stack
    assert host.messages and not any(message.startswith('[success]') for message in host.messages)


def test_delete_is_one_step_and_restores_identity_selection_and_order():
    editor, first, second = scene()
    editor.selected = [first]
    editor.delete_selected()
    assert len(editor.history.undo_stack) == 1
    assert editor.storage_dict[10]['geometry'] == []
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.selected == [first]
    assert list(editor.storage_dict) == [10, 11]
    assert not editor.is_modified
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'] == []
    assert editor.storage_dict[11]['geometry'] == [second]


def test_nested_mutations_group_and_capture_immutable_geometry_values():
    editor, first, second = scene()
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    old = dict(first.geo)
    with editor.history.operation('Move then copy') as accepted:
        assert accepted
        first.geo['solid'] = translate(first.geo['solid'], xoff=2)
        first.geo['follow'] = translate(first.geo['follow'], xoff=2)
        editor.touch_geometry()
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), 0.1, 1)
        added = DrawToolShape({'solid': box(5, 5, 6, 6)})
        editor.add_gerber_shape(added, editor.storage_dict[10]['geometry'])
    assert len(editor.history.undo_stack) == 1
    assert editor.history.undo_stack[0]['label'] == 'Move then copy'
    assert editor.undo()
    assert first.geo == old
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'] == [first, added]
    assert first.geo['follow'].equals(Point(2.5, 0.5))
    assert first.geo['solid'].bounds == pytest.approx((1.9, -0.1, 3.1, 1.1))


def test_transaction_rolls_back_geometry_apertures_flags_and_selection_on_exception():
    editor, first, second = scene()
    editor.selected = [first]
    old = dict(first.geo)
    with pytest.raises(ValueError):
        with editor.history.operation('Broken'):
            first.geo['solid'] = box(1, 1, 2, 2)
            editor.storage_dict[10]['geometry'].clear()
            editor.storage_dict[99] = {'type': 'REG', 'size': 0, 'geometry': []}
            editor.loaded_as_regions = True
            editor.selected = []
            raise ValueError('computation failed')
    assert first.geo == old
    assert editor.storage_dict[10]['geometry'] == [first]
    assert list(editor.storage_dict) == [10, 11]
    assert editor.selected == [first]
    assert not getattr(editor, 'loaded_as_regions', False)
    assert editor.history.undo_stack == []


def test_branching_limits_and_non_mutating_selection():
    editor, first, second = scene()
    editor.app.options['global_history_steps'] = 2
    for index in range(3):
        editor.add_gerber_shape(DrawToolShape({'solid': box(index, 3, index + 1, 4)}),
                                editor.storage_dict[10]['geometry'])
    assert len(editor.history.undo_stack) == 2
    assert editor.history.baseline_evicted
    assert editor.undo()
    editor.selected = [second]
    editor.plot_selection()
    assert len(editor.history.redo_stack) == 1
    editor.delete_selected()
    assert editor.history.redo_stack == []
    assert not editor.redo()


def snapshot_gerber_editor_history(editor):
    return {key: ({name: value for name, value in aperture.items() if name != 'geometry'},
                  [(shape, dict(shape.geo)) for shape in aperture.get('geometry', ())])
            for key, aperture in editor.storage_dict.items()}


@pytest.mark.parametrize('family', ['move', 'erase', 'buffer'])
def test_mutation_family_round_trip_is_one_step(family):
    first, second = pad(0, 0, 1), pad(10, 0, 1)
    editor = make_editor({10: {'type': 'C', 'size': 2.0, 'geometry': [first, second]}})
    editor.gerber_obj = SimpleNamespace(obj_options={'name': 'board'})
    editor.history.reset()
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    editor.is_modified = False
    before = snapshot_gerber_editor_history(editor)
    before_regions = getattr(editor, 'loaded_as_regions', False)
    if family in ('pad', 'array', 'region', 'disc', 'semidisc'):
        geometry = pad(4, 4, 1)
        editor.active_tool = SimpleNamespace(name=family, geometry=geometry)
        shapes = [geometry, pad(6, 4, 1)] if family == 'array' else geometry
        editor.on_grb_shape_complete(editor.storage_dict[10]['geometry'], specific_shape=shapes)
    elif family == 'track':
        tool = TrackEditorGrb.__new__(TrackEditorGrb)
        tool.draw_app, tool.app = editor, editor.app
        tool.points, tool.temp_points, tool.click_shapes = [], [(0, 0), (3, 0), (3, 3)], []
        tool.buf_val, tool.quad_segs = 1, 16
        tool.track_tool = SimpleNamespace(length=1)
        editor.snap_x, editor.snap_y = 3, 3
        tool.ui = SimpleNamespace(x_entry=MagicMock(), y_entry=MagicMock())
        tool.click((3, 3))
    elif family in ('move', 'copy'):
        tool = (MoveEditorGrb if family == 'move' else CopyEditorGrb)(editor)
        editor.active_tool = tool
        tool.origin, tool.destination = (0, 0), (0, 5)
        tool.make()
    elif family == 'erase':
        editor = make_editor({10: {'type': 'C', 'size': 0.4, 'geometry': [trace(0, 0, 10, 0, 0.4)]}})
        tool = TestEraserMake().eraser(editor, 5, 0)
        before = snapshot_gerber_editor_history(editor)
        tool.make()
    elif family == 'polygonize':
        tool = PoligonizeEditorGrb(editor)
        tool.make()
    elif family == 'mark_delete':
        editor.geo_to_delete = [first]
        editor.delete_marked_polygons()
    elif family == 'buffer':
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), 0.1, 1)
        run_job(editor)
    elif family == 'scale':
        editor.ui.scale_factor_entry.set_value(1.2)
        editor.on_scale()
    elif family == 'transform':
        Host(editor).on_offset('X', 2)
    elif family == 'simplify':
        simplify_tool(editor).on_simplification_click()
        run_job(editor)
    else:
        tool = SimpleNamespace(draw_app=editor, app=editor.app, sel_storage=[{
            'type': 'REG', 'size': 0, 'geometry': [DrawToolShape({'solid': box(5, 5, 6, 6)})]}])
        ImportEditorGrb.import_shapes(tool)
    after = snapshot_gerber_editor_history(editor)
    after_regions = getattr(editor, 'loaded_as_regions', False)
    assert after != before
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot_gerber_editor_history(editor) == before
    assert getattr(editor, 'loaded_as_regions', False) == before_regions
    assert editor.redo()
    assert snapshot_gerber_editor_history(editor) == after
    assert getattr(editor, 'loaded_as_regions', False) == after_regions


def test_track_backspace_is_its_own_reversible_committed_action():
    first = trace(0, 0, 3, 0, 0.4)
    editor = make_editor({10: {'type': 'C', 'size': 0.4, 'geometry': [first]}})
    tool = TrackEditorGrb.__new__(TrackEditorGrb)
    tool.draw_app, tool.app = editor, editor.app
    tool.points, tool.temp_points = [(0, 0), (3, 0)], [(0, 0), (3, 0)]
    tool.click_shapes = [(first, editor.storage_dict[10]['geometry'])]
    tool.utility_geometry = lambda data: None
    editor.draw_utility_geometry = MagicMock()
    tool.on_key('Backspace')
    assert editor.storage_dict[10]['geometry'] == []
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]


@pytest.mark.parametrize('column,text', [(1, '12')])
def test_aperture_table_edits_restore_metadata_records_selection_and_aliases(column, text):
    editor, first, second = scene()
    editor.tid2apcode = {1: 10, 2: 11}
    editor.oldapcode_newapcode = {10: 10, 11: 11}
    editor.selected = [first]
    editor.last_aperture_selected = 10
    before = snapshot_gerber_editor_history(editor)
    table = editor.ui.apertures_table
    with QtCore.QSignalBlocker(table):
        table.setItem(0, column, QtWidgets.QTableWidgetItem(text))
        table.setCurrentCell(0, column)
    table.itemChanged.connect(editor.on_tool_edit)
    editor.on_tool_edit()
    after = snapshot_gerber_editor_history(editor)
    assert after != before
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot_gerber_editor_history(editor) == before
    assert editor.selected == [first]
    assert editor.oldapcode_newapcode == {10: 10, 11: 11}
    assert editor.last_aperture_selected == 10
    assert editor.redo()
    assert snapshot_gerber_editor_history(editor) == after
    if column == 1:
        assert editor.last_aperture_selected == 12
    else:
        assert editor.storage_dict[10]['size'] == 0.5


@pytest.mark.parametrize('distance,standalone,keep_other', [(-2, True, False)])
def test_buffer_undo_redo_save_reparse_preserves_material_and_export_mode(app, distance, standalone, keep_other):

    source = make_gerber(app, NATIVE_GBR, name='board')
    first = DrawToolShape({'solid': Point(4, 4).buffer(1, 16), 'follow': Point(4, 4)})
    other = DrawToolShape({'solid': Point(10, 4).buffer(1, 16), 'follow': Point(10, 4)})
    editor = make_editor({10: {'type': 'C', 'size': 2, 'geometry': [first, other] if keep_other else [first]}})
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    if standalone:
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), distance, 1)
        if editor.history.pending is not None:
            run_job(editor)
    else:
        Host(editor).on_buffer_action(distance, 1)
    for mode in ('buffered', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = getattr(editor, 'loaded_as_regions', False)
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        saved = result['obj']
        # a changed record that no longer matches its aperture is written as a region; the untouched records
        # keep their native apertures, so the object is no longer forced into the whole-board region export
        assert saved.export_as_regions is False
        if mode == 'undo':
            assert any(isinstance(rec.get('follow'), Point) for tool in saved.tools.values()
                       if tool.get('type') in ('C', 'R', 'O') for rec in tool.get('geometry', ()))
        else:
            assert not any(isinstance(rec.get('follow'), Point) for tool in saved.tools.values()
                           for rec in tool.get('geometry', ()))
        expected = unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                                for shape in aperture.get('geometry', ())])
        for text in (saved.source_file, export_gerber_text(app, saved)):
            parsed = Gerber(app, steps_per_circle=64)
            # The importer refuses empty boards by design; it must still contain no resurrected copper.
            assert parsed.parse_lines(text.splitlines()) == ('fail' if expected.is_empty else None)
            actual = unary_union(parsed.solid_geometry)
            if expected.is_empty:
                assert actual.is_empty
            else:
                assert actual.hausdorff_distance(expected) < 0.001


def test_save_empty_solid_keeps_clear_data_without_flashing_stale_follow(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    clear = Point(2, 2).buffer(0.2)
    erased = DrawToolShape({'solid': Polygon(), 'follow': Point(2, 2), 'clear': clear})
    other = DrawToolShape({'solid': Point(10, 2).buffer(1, 16), 'follow': Point(10, 2)})
    storage = {10: {'type': 'C', 'size': 2, 'geometry': [erased, other]}}
    stand_in = gerber_stand_in(app, source, storage)
    AppGerberEditor.build_edited_gerber(stand_in, 'board', storage)
    saved = app.custom_signal.emit.call_args.args[0]['obj']
    assert saved.tools[10]['geometry'][0]['clear'].equals(clear)
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(saved.source_file.splitlines()) is None
    actual = unary_union(parsed.solid_geometry)
    assert not actual.contains(Point(2, 2))
    assert actual.hausdorff_distance(other.geo['solid']) < 0.001


def test_save_as_new_detached_build_commits_reparseable_copy_without_changing_source(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    tools_before, solid_before, source_before = source.tools, source.solid_geometry, source.source_file
    editor = gerber_stand_in(app, source, gerber_storage(source))
    editor.deactivate_grb_editor = MagicMock()
    app.connect_custom_signal = MagicMock()
    app.worker_task = MagicMock()
    created = []

    def new_object(kind, name, initialize):
        obj = type(source)(name, app)
        initialize(obj, app)
        created.append(obj)
        return obj

    app.app_obj.new_object.side_effect = new_object
    AppGerberEditor.update_fcgerber(editor)
    job = app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    app.connect_custom_signal.call_args.args[0](app.custom_signal.emit.call_args.args[0])
    assert len(created) == 1
    saved = created[0]
    assert saved.obj_options['name'] == 'board_edit'
    assert saved.history_uid != source.history_uid
    assert source.tools is tools_before and source.solid_geometry is solid_before
    assert source.source_file == source_before
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(saved.source_file.splitlines()) is None
    assert parsed.solid_geometry
    editor.deactivate_grb_editor.assert_called_once()


def test_save_keeps_the_object_attributes_of_unchanged_and_moved_records_but_not_of_new_ones(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    source.to_attributes = {1: {'.N': ['GND']}, 2: {'.N': ['VCC']}}
    source.file_attributes = {'.FileFunction': ['Copper']}
    records = [rec for tool in source.tools.values() for rec in tool['geometry']]
    for number, rec in enumerate(records):
        rec['to'] = 1 + number % 2
        storage = gerber_storage(source)
        first = next(iter(storage))
        new_flash = source.create_flash_geometry(Point(30, 30), source.tools[first])
        storage[first]['geometry'].append(SimpleNamespace(geo={'solid': new_flash, 'follow': Point(30, 30)}))
    AppGerberEditor.build_edited_gerber(gerber_stand_in(app, source, storage), 'board', storage)
    saved = app.custom_signal.emit.call_args.args[0]['obj']
    assert saved.to_attributes == source.to_attributes
    assert saved.file_attributes == source.file_attributes
    saved_to = [[rec.get('to') for rec in saved.tools[ap]['geometry']] for ap in storage]
    expected = [[rec.get('to') for rec in source.tools[ap]['geometry']] for ap in storage]
    expected[0].append(None)
    assert saved_to == expected


@pytest.mark.parametrize('family', ['move'])
def test_move_and_copy_keep_the_to_id_and_undo_restores_it(family):
    editor, first, second = scene()
    first.geo['to'] = 3
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    tool =(MoveEditorGrb if family == 'move' else CopyEditorGrb)(editor)
    editor.active_tool = tool
    tool.origin, tool.destination = (0, 0), (0, 5)
    tool.make()
    shapes = editor.storage_dict[10]['geometry']
    assert len(shapes) == (1 if family == 'move' else 2)
    assert shapes[-1].geo['to'] == 3
    assert shapes[-1].geo['follow'].equals(Point(0.5, 5.5))
    assert 'to' not in second.geo
    assert editor.undo()
    assert [shape.geo.get('to') for shape in editor.storage_dict[10]['geometry']] == [3]


def test_region_union_keeps_the_to_id_only_when_all_the_merged_records_have_it():
    for ids, expected in (((4, 4), 4), ((4, 5), None), ((4, None), None)):
        editor, first, second = scene()
        second.geo['solid'] = box(0.5, 0, 11, 1)
        editor.selected = [first, second]
        for shape, to in zip((first, second), ids):
            if to is not None:
                shape.geo['to'] = to
        assert editor.apply_region_operation('union') != 'fail'
        merged = [shape for aperture in editor.storage_dict.values() for shape in aperture['geometry']]
        assert [shape.geo.get('to') for shape in merged] == [expected]


def track(coords, width=2):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / 2, 16)})


def flash_at(x, y, diameter=2):
    follow = Point(x, y)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(diameter / 2, 16)})


def region(coords):
    poly = Polygon(coords)
    return DrawToolShape({'follow': LineString(poly.exterior.coords), 'solid': poly})


def editor_for(storage, selected=()):
    editor = make_editor(storage)
    editor.loaded_as_regions = False
    editor.selected = list(selected)
    return editor


def footprint(editor):
    return unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                        for shape in aperture['geometry']])


def centers(editor):
    return sorted((round(shape.geo['follow'].x, 6), round(shape.geo['follow'].y, 6))
                  for aperture in editor.storage_dict.values() for shape in aperture['geometry'])


def ids(shapes):
    return sorted(id(shape) for shape in shapes)


def has(editor, shape):
    return any(picked is shape for picked in editor.selected)


def storage_ids(editor):
    return {key: ids(aperture['geometry']) for key, aperture in editor.storage_dict.items()}


def test_invert_and_filter_change_the_selection_without_an_undo_step():
    a, b, c = flash_at(0, 0), flash_at(5, 0), flash_at(10, 0)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a, b, c]}}, selected=[a])
    selection_module.invert_selection(editor)
    assert ids(editor.selected) == ids([b, c])
    pad, line, reg = flash_at(0, 0), track([(0, 5), (10, 5)]), region([(0, 0), (4, 0), (4, 4)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [pad, line]},
                         'REG': {'type': 'REG', 'size': 0, 'geometry': [reg]}},
                        selected=[pad, line, reg])
    selection_module.filter_selection(editor, 'track')
    assert ids(editor.selected) == ids([line])
    editor.selected = []
    selection_module.filter_selection(editor, 'pad')
    assert ids(editor.selected) == ids([pad])
    assert not editor.history.undo_stack


def test_chain_follows_connected_tracks_and_skips_the_far_one():
    t1, t2, t3 = track([(0, 0), (5, 0)]), track([(5, 0), (5, 5)]), track([(5, 5), (9, 5)])
    far = track([(20, 20), (25, 20)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [t1, t2, t3, far]}})
    selection_module.select_chain(editor, (2, 0.1), 0.5)
    assert ids(editor.selected) == ids([t1, t2, t3])
    editor.selected = [far]
    selection_module.select_chain(editor, (2, 0.1), 0.5, add=True)
    assert ids(editor.selected) == ids([far, t1, t2, t3])


@pytest.mark.parametrize('branching, far_selected', [(False, False)])
def test_chain_stops_at_a_junction_unless_branching(branching, far_selected):
    a = track([(0, 0), (5, 0)])
    b = track([(5, 0), (5, 5)])
    c = track([(5, 0), (9, -3)])
    d = track([(5, 5), (9, 5)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a, b, c, d]}})
    selection_module.select_chain(editor, (2, 0.1), 0.5, branching=branching)
    assert has(editor, a)
    assert has(editor, d) is far_selected


def test_step_repeat_makes_the_grid_and_undo_restores_the_source():
    a = flash_at(0, 0)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a]}}, selected=[a])
    assert selection_module.apply_step_repeat(editor, 2, 3, 10, 5) != 'fail'
    assert centers(editor) == sorted((x, y) for x in (0, 10, 20) for y in (0, 5))
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert ids(editor.storage_dict[10]['geometry']) == ids([a])
    assert editor.redo()
    assert len(centers(editor)) == 6
    assert selection_module.apply_step_repeat(editor, 1, 1, 10, 5) == 'fail'
    assert len(centers(editor)) == 6
    assert len(editor.history.undo_stack) == 1


def test_transcode_flash_takes_the_target_shape_at_the_same_center():
    a = flash_at(3, 4)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a]},
                         11: {'type': 'R', 'size': 0, 'width': 1, 'height': 3, 'geometry': []}},
                        selected=[a])
    assert selection_module.apply_transcode(editor, 11) != 'fail'
    assert editor.storage_dict[10]['geometry'] == []
    (moved,) = editor.storage_dict[11]['geometry']
    assert moved.geo['solid'].bounds == pytest.approx((2.5, 2.5, 3.5, 5.5))
    assert len(editor.history.undo_stack) == 1


def test_transcode_refuses_regions_and_tracks_to_non_circular_apertures():
    for source in ('region', 'track'):
        rect = {11: {'type': 'R', 'size': 0, 'width': 1, 'height': 3, 'geometry': []}}
        if source == 'region':
            shape = region([(0, 0), (4, 0), (4, 4)])
            storage = {0: {'type': 'REG', 'size': 0, 'geometry': [shape]}, **rect}
        else:
            shape = track([(0, 0), (10, 0)])
            storage = {10: {'type': 'C', 'size': 2, 'geometry': [shape]}, **rect}
        editor = editor_for(storage, selected=[shape])
        before = storage_ids(editor)
        assert selection_module.apply_transcode(editor, 11) == 'fail', source
        assert storage_ids(editor) == before
        assert not editor.history.undo_stack


def test_snap_to_grid_moves_flash_centers_and_track_vertices():
    f = flash_at(1.04, 2.96)
    t = track([(0.02, 0.01), (5.04, 0.02)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [f, t]}}, selected=[f, t])
    assert selection_module.apply_snap_to_grid(editor, 0.1, 0.1) != 'fail'
    geometry = editor.storage_dict[10]['geometry']
    (flash,) = [s for s in geometry if s.geo['follow'].geom_type == 'Point']
    (line,) = [s for s in geometry if s.geo['follow'].geom_type == 'LineString']
    assert (flash.geo['follow'].x, flash.geo['follow'].y) == pytest.approx((1.0, 3.0))
    assert (flash.geo['solid'].centroid.x, flash.geo['solid'].centroid.y) == pytest.approx((1.0, 3.0))
    assert [v for xy in line.geo['follow'].coords for v in xy] == pytest.approx([0.0, 0.0, 5.0, 0.0])


def assert_saved_as_flashes(app, editor):
    """The edited Gerber keeps the flashes in R or O apertures: no composed regions, a Point follow after reparse."""
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for aperture in parsed.tools.values()
               if aperture.get('type') in ('R', 'O') for record in aperture['geometry'])


def test_transcoded_flash_saves_as_a_rectangular_flash(app):
    flash = flash_at(5, 5)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [flash]},
                         11: {'type': 'R', 'size': 2.5, 'width': 2, 'height': 1.5, 'geometry': []}},
                        selected=[flash])
    assert selection_module.apply_transcode(editor, 11) != 'fail'
    assert not editor.loaded_as_regions
    assert_saved_as_flashes(app, editor)


def test_database_autoloads_only_checked_apertures_with_converted_dimensions(tmp_path):
    from appCommon.ToolDatabase import write_database, write_apertures
    from defaults import AppDefaults
    path = tmp_path / 'tools.sqlite3'
    write_database(path, {})
    write_apertures(path, {
        10: {'type': 'C', 'size': 0.1, 'units': 'IN', 'autoload': True},
        11: {'type': 'R', 'width': 0.1, 'height': 0.2, 'units': 'IN', 'autoload': True},
        12: {'type': 'O', 'width': 1, 'height': 2, 'units': 'MM', 'autoload': False},
    }, expected={})
    editor = make_editor()
    editor.app.options.update(AppDefaults.factory_defaults)
    editor.app.tools_database_path.return_value = str(path)
    editor.gerber_obj = SimpleNamespace(tools={}, solid_geometry=[], obj_options={'name': 'new_grb'})
    editor.build_ui = AppGerberEditor.build_ui.__get__(editor)
    pending = editor.history.reserve()
    editor.apply_load_result({'pending': pending, 'storage': {}, 'regions': False})
    assert set(editor.storage_dict) == {10, 11}
    assert editor.ui.apertures_table.rowCount() == 2
    assert [editor.ui.apertures_table.item(row, 1).text() for row in range(2)] == ['10', '11']
    assert editor.storage_dict[10] == {'type': 'C', 'size': pytest.approx(2.54), 'geometry': []}
    assert editor.storage_dict[11]['width'] == pytest.approx(2.54)
    assert editor.storage_dict[11]['height'] == pytest.approx(5.08)
    assert editor.storage_dict[11]['size'] == pytest.approx(math.hypot(2.54, 5.08))


def flash(x, y, diameter):
    center = Point(x, y)
    return DrawToolShape({'follow': center, 'solid': center.buffer(diameter / 2, 16)})


def rect_flash(x, y, width, height):
    return DrawToolShape({'follow': Point(x, y), 'solid': box(x - width / 2, y - height / 2,
                                                              x + width / 2, y + height / 2)})


def region_gerber_editor_apertures(x):
    polygon = box(x, 0, x + 2, 2)
    return DrawToolShape({'follow': polygon.exterior, 'solid': polygon})


def test_delete_unused_removes_empty_circle_and_keeps_empty_region():
    editor = make_editor({
        10: {'type': 'C', 'size': 2, 'geometry': [flash(0, 0, 2)]},
        11: {'type': 'C', 'size': 1, 'geometry': []},
        0: {'type': 'REG', 'size': 0, 'geometry': []},
    })
    assert aperture_module.apply_delete_unused(editor) == [11]
    assert set(editor.storage_dict) == {10, 0}
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert 11 in editor.storage_dict
    assert editor.redo()
    assert 11 not in editor.storage_dict


def test_renumber_follows_rank_order_and_moves_records_with_their_aperture():
    rect = rect_flash(0, 0, 1, 2)
    round_flash = flash(5, 0, 1)
    editor = make_editor({
        15: {'type': 'C', 'size': 1, 'geometry': [round_flash]},
        12: {'type': 'R', 'size': 0, 'width': 1, 'height': 2, 'geometry': [rect]},
        0: {'type': 'REG', 'size': 0, 'geometry': [region_gerber_editor_apertures(20)]},
    })
    assert aperture_module.apply_renumber(editor, start=10) == {12: 10, 15: 11}
    assert set(editor.storage_dict) == {10, 11, 0}
    assert editor.storage_dict[10]['type'] == 'R'
    assert editor.storage_dict[10]['geometry'] == [rect]
    assert editor.storage_dict[11]['type'] == 'C'
    assert editor.storage_dict[11]['geometry'] == [round_flash]
    assert editor.storage_dict[0]['type'] == 'REG'
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert set(editor.storage_dict) == {0, 12, 15}
    assert editor.storage_dict[12]['type'] == 'R'


def test_merge_duplicates_moves_records_into_lowest_code_and_keeps_other_sizes():
    first, second = flash(0, 0, 0.5), flash(5, 0, 0.5)
    other = flash(9, 0, 0.6)
    editor = make_editor({
        10: {'type': 'C', 'size': 0.5, 'geometry': [first]},
        14: {'type': 'C', 'size': 0.5, 'geometry': [second]},
        16: {'type': 'C', 'size': 0.6, 'geometry': [other]},
    })
    assert aperture_module.apply_merge_duplicates(editor) == {14: 10}
    assert set(editor.storage_dict) == {10, 16}
    assert editor.storage_dict[10]['geometry'] == [first, second]
    assert editor.storage_dict[16]['geometry'] == [other]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert set(editor.storage_dict) == {10, 14, 16}
    assert len(editor.storage_dict[10]['geometry']) == 1


def test_round_sizes_rounds_mm_size_rebuilds_flash_and_leaves_exact_codes_alone():
    off_grid = flash(0, 0, 0.5004)
    exact = flash(5, 0, 0.5)
    editor = make_editor({
        10: {'type': 'C', 'size': 0.5004, 'geometry': [off_grid]},
        11: {'type': 'C', 'size': 0.5, 'geometry': [exact]},
    })
    assert aperture_module.apply_round_sizes(editor) == [10]
    assert editor.storage_dict[10]['size'] == pytest.approx(0.5)
    minx, miny, maxx, maxy = editor.storage_dict[10]['geometry'][0].geo['solid'].bounds
    assert maxx - minx == pytest.approx(0.5, abs=1e-3)
    assert editor.storage_dict[11]['geometry'][0] is exact
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['size'] == pytest.approx(0.5004)


@pytest.mark.parametrize('kind', ['R'])
def test_rounded_rectangular_and_oblong_flashes_save_as_flashes(app, kind):
    from appEditors.grb_plugins.GrbSelectionPlugin import flash_geo
    probe = make_editor({10: {'type': 'C', 'size': 2, 'geometry': []}})
    width, height = 1.23456, 2.34567
    item = {'type': kind, 'size': math.hypot(width, height), 'width': width, 'height': height, 'geometry': []}
    item['geometry'].append(DrawToolShape(flash_geo(probe, item, Point(0, 0))))
    editor = make_editor({12: item})
    editor.loaded_as_regions = False
    assert aperture_module.apply_round_sizes(editor) == [12]
    assert not editor.loaded_as_regions
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for tool in parsed.tools.values()
               if tool.get('type') == kind for record in tool['geometry'])


@pytest.mark.parametrize('values', [(0, 0, 0, 2, 'c', 's', 0, 0, 16),
                                   (0, 0, 4, 2, 'c', 'r', 2, 0, 16)])
def test_rectangle_rejects_invalid_input(values):
    with pytest.raises(ValueError):
        rectangle_region(*values)


def font_parser(editor):
    parser = ParseFont(editor.app)
    parser.get_fonts_by_types()
    assert parser.regular_f, 'A usable installed font is required for the real outline test.'
    return parser, 'Arial' if 'Arial' in parser.regular_f else next(iter(parser.regular_f))


def test_text_holes_units_and_transform():
    editor = make_editor()
    parser, family = font_parser(editor)
    mm = text_region(parser, 'Oi', family, 'regular', 12, 'MM', 0, 0, 0)
    inch = text_region(parser, 'Oi', family, 'regular', 12, 'IN', 0, 0, 0)
    assert mm.is_valid and not mm.is_empty
    parts = list(mm.geoms) if isinstance(mm, MultiPolygon) else [mm]
    assert sum(len(p.interiors) for p in parts) >= 1
    assert len(parts) >= 2
    assert mm.area / inch.area == pytest.approx(25.4 ** 2)
    moved = text_region(parser, 'Oi', family, 'regular', 12, 'MM', 10, 20, 90)
    assert moved.equals_exact(translate(rotate(mm, 90, origin=(0, 0)), 10, 20), 1e-10)
    for text, size, x in [('', 12, 0), ('O', 0, 0), ('O', float('nan'), 0), ('O', 12, float('inf'))]:
        with pytest.raises(ValueError):
            text_region(parser, text, family, 'regular', size, 'MM', x, 0, 0)


def test_region_commit_preserves_owners_and_undo():
    existing = pad(20, 20, 1)
    editor = make_editor({10: aperture([existing])})
    polygon = box(0, 0, 5, 5).difference(box(1, 1, 4, 4))
    added = editor.add_region_geometry(MultiPolygon([polygon, box(2, 2, 3, 3)]))
    assert len(added) == 2
    assert editor.storage_dict[10]['geometry'] == [existing]
    assert editor.storage_dict[editor.last_aperture_selected]['type'] == 'REG'
    assert editor.loaded_as_regions
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict) == [10]
    assert editor.redo()
    assert unary_union([s.geo['solid'] for s in editor.selected]).equals(unary_union([polygon, box(2, 2, 3, 3)]))


def skip_without_fonts():
    # the offscreen Qt platform has an empty font database: the font combo box cannot select a family
    if QtWidgets.QFontComboBox().count() == 0:
        pytest.skip('no fonts in the Qt font database (offscreen platform)')


def test_copper_pour_clearance_parts_and_history():
    from appEditors.grb_plugins.GrbDrawingPlugin import copper_pour
    from appEditors.grb_plugins.GrbCommon import DrawToolShape
    obstacles = [box(4, -1, 5, 11), box(1, 1, 2, 2), box(-1, 3, -0.1, 4), box(50, 50, 51, 51)]
    shapes = [DrawToolShape({'solid': p}) for p in obstacles]
    editor = make_editor({10: aperture(shapes)})
    outline = box(0, 0, 10, 10)
    expected = outline
    for obstacle in obstacles:
        expected = expected.difference(obstacle.buffer(0.3, quad_segs=16))
    result = copper_pour(editor, outline, 0.3, 16)
    assert result.equals(expected)
    assert isinstance(result, MultiPolygon)
    assert sum(len(p.interiors) for p in result.geoms) == 1
    editor.add_region_geometry(result)
    assert editor.storage_dict[10]['geometry'] == shapes
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict) == [10]
    assert editor.redo()
    assert unary_union([s.geo['solid'] for s in editor.selected]).equals(expected)


def test_text_and_rectangle_native_export_reparse(app, tmp_path):
    editor = make_editor()
    parser, family = font_parser(editor)
    editor.add_region_geometry(text_region(parser, 'Oi', family, 'regular', 12, 'MM', 0, 0, 20))
    editor.add_region_geometry(rectangle_region(20, 20, 4, 2, 'c', 'r', 0.5, 30, 16))
    tools = {key: {**value, 'geometry': [shape.geo for shape in value['geometry']]}
             for key, value in editor.storage_dict.items()}
    expected = unary_union([shape.geo['solid'] for value in editor.storage_dict.values() for shape in value['geometry']])
    source = make_gerber(app, NATIVE_GBR)
    edited = editor_save(app, source, tools, loaded_as_regions=editor.loaded_as_regions)
    again = reparse_gerber(app, edited.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)


def opened_panel(editor, kind='rectangle'):
    editor.on_region_drawing(kind)
    panel = editor.region_drawing_panel
    assert panel is not None
    return panel


class JumpHolder(QtCore.QObject):
    """Stand-in of the app jump signal (no arguments)."""
    signal = QtCore.pyqtSignal()


@pytest.mark.parametrize('same_owner', [False])
def test_difference_keeps_cutters_and_order_with_history(same_owner):
    target = DrawToolShape({'solid': MultiPolygon([box(0, 0, 10, 10), box(12, 0, 13, 1)])})
    cutter = DrawToolShape({'solid': box(2, 2, 8, 8)})
    unrelated = pad(20, 20, 1)
    storage = {10: aperture([unrelated, target, cutter] if same_owner else [unrelated, target])}
    if not same_owner:
        storage[11] = aperture([cutter])
    editor = make_editor(storage)
    editor.selected = [target, cutter]
    original = cutter.geo
    result = editor.apply_region_operation('difference_keep')
    assert result != 'fail'
    assert unary_union([s.geo['solid'] for s in result]).equals(target.geo['solid'].difference(cutter.geo['solid']))
    assert sum(len(s.geo['solid'].interiors) for s in result) == 1
    assert editor.storage_dict[10]['geometry'] == ([unrelated, cutter] if same_owner else [unrelated])
    assert editor.storage_dict[10 if same_owner else 11]['geometry'][-1] is cutter
    assert cutter.geo is original
    assert editor.selected == result + [cutter]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.selected == [target, cutter]
    assert editor.storage_dict[10]['geometry'][1] is target
    assert editor.redo()
    assert editor.storage_dict[10 if same_owner else 11]['geometry'][-1] is cutter


def arc_tool(editor):
    editor.last_aperture_selected = 10
    editor.loaded_as_regions = False
    editor.jump_holder = JumpHolder()
    editor.app.jump_signal = editor.jump_holder.signal
    tool = parity_module.ArcEditorGrb(editor)
    editor.active_tool = tool
    return tool


@pytest.mark.parametrize('mode,points', [
    ('c12', [(0, 0), (1, 0), (0, 1)]),
    ('132', [(1, 0), (0, 1), (math.sqrt(0.5), math.sqrt(0.5))])])
def test_arc_modes_preview_commit_and_history(mode, points):
    editor = make_editor({10: aperture([])})
    tool = arc_tool(editor)
    tool.mode, tool.direction = mode, 'ccw'
    tool.click(points[0])
    tool.click(points[1])
    preview = tool.utility_geometry(points[2]).geo
    tool.click(points[2])
    assert tool.complete
    assert tool.geometry.geo['solid'].equals(preview['solid'])
    follow = tool.geometry.geo['follow']
    assert follow.geom_type == 'LineString' and follow.is_simple
    assert follow.coords[0] == pytest.approx((1, 0))
    assert follow.coords[-1] == pytest.approx((0, 1))
    assert all(math.hypot(x, y) == pytest.approx(1) for x, y in follow.coords)
    assert tool.geometry.geo['solid'].equals(follow.buffer(0.05, quad_segs=gerber_quad_segs(editor.app.options, 0.05, editor.app.app_units)))
    editor.on_grb_shape_complete()
    assert len(editor.storage_dict[10]['geometry']) == 1
    assert not editor.loaded_as_regions
    assert editor.undo() and editor.storage_dict[10]['geometry'] == []
    assert editor.redo() and len(editor.storage_dict[10]['geometry']) == 1
    tool.clean_up()


@pytest.mark.parametrize('mode,kept,bad', [
    ('132', [(0, 0), (2, 0)], (1, 0)),  # Point2 on the line of Point1 and Point3
    ('12c', [(1, 1)], (1, 1))])  # the two ends of the chord on one point
def test_semidisc_refuses_a_point_without_an_arc_and_takes_the_next_one(mode, kept, bad):
    editor = make_editor({10: aperture([])})
    editor.last_aperture_selected = 10
    tool = parity_module.DiscSemiEditorGrb(editor)
    editor.active_tool = tool
    tool.mode = mode
    for point in kept + [bad]:
        tool.click(point)
    assert tool.points == kept and not tool.complete
    assert 'non-collinear' in editor.app.inform.emit.call_args.args[0]
    for point in [(2, 2), (3, 1)][len(kept) - 1:]:
        tool.click(point)
    assert tool.complete
    assert tool.geometry.geo['solid'].is_valid and not tool.geometry.geo['solid'].is_empty


@pytest.mark.parametrize('operation,steps,direction', [('arc', 64, 'ccw'), ('difference_keep', 16, 'cw')])
def test_new_actions_native_export_reparse(app, tmp_path, operation, steps, direction):
    editor = make_editor({10: aperture([])})
    for options in (editor.app.options, app.options):
        options.update(gerber_circle_steps_mode='fixed', gerber_circle_steps=steps)
    if operation == 'arc':
        tool = arc_tool(editor)
        tool.direction = direction
        for point in [(0, 0), (2, 0), (0, 2)]:
            tool.click(point)
        editor.on_grb_shape_complete()
        tool.clean_up()
    else:
        first = DrawToolShape({'solid': box(0, 0, 10, 10)})
        cutter = DrawToolShape({'solid': box(2, 2, 8, 8)})
        editor.storage_dict[10]['geometry'] = [first]
        editor.storage_dict[11] = {'type': 'REG', 'size': 0, 'geometry': [cutter]}
        editor.selected = [first, cutter]
        assert editor.apply_region_operation(operation) != 'fail'
    tools = {key: {**value, 'geometry': [s.geo for s in value['geometry']]}
             for key, value in editor.storage_dict.items() if value['geometry']}
    expected = unary_union([s.geo['solid'] for value in editor.storage_dict.values() for s in value['geometry']])
    edited = editor_save(app, make_gerber(app, NATIVE_GBR), tools, loaded_as_regions=editor.loaded_as_regions)
    again = reparse_gerber(app, edited.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)


NEW_MENU_ACTIONS = ['grb_invert_selection', 'grb_select_pads', 'grb_select_tracks', 'grb_select_regions',
                    'grb_select_chain', 'grb_step_repeat', 'grb_transcode', 'grb_snap_grid', 'grb_clip',
                    'grb_clip_pad', 'grb_stretch', 'grb_elbow', 'grb_elbow90', 'grb_delete_unused', 'grb_renumber',
                    'grb_merge_duplicates', 'grb_round_sizes', 'grb_select_same_type', 'grb_pads_to_flashes',
                    'grb_teardrops', 'grb_snowman']


def track_gerber_track_bend(coords, width=0.4, divisor=2, steps=16):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / divisor, steps)})


def editor_for_gerber_track_bend(shapes, width=0.4):
    editor = make_editor({10: {'type': 'C', 'size': width, 'geometry': shapes, 'tag': 'unchanged'}})
    editor.app.abort_flag = False
    editor.select_tool = AppGerberEditor.select_tool.__get__(editor)
    editor.draw_utility_geometry = lambda geo_shape: None
    return editor


def start(editor, point):
    editor.ui.bend_move_btn.click()
    editor.on_canvas_click(event(*point))
    return editor.active_tool


def release_gerber_track_bend(editor, point):
    editor.on_canvas_click_release(event(*point))


@pytest.mark.parametrize('divisor,steps', [(2, 16)])
def test_internal_bend_stretches_runs_preserves_width_and_fixed_bends(divisor, steps):
    coords = [(0, 0), (2, 0), (4, 0), (4, 2), (4, 4), (8, 4)]
    original = track_gerber_track_bend(coords, divisor=divisor, steps=steps)
    editor = editor_for_gerber_track_bend([original])
    editor.gerber_obj = SimpleNamespace(steps_per_circle=steps * 4)
    editor.selected = [original]
    revision = editor.geometry_revision
    tool = start(editor, (4, 0))
    preview = tool.utility_geometry((5, 1))
    assert preview is not None
    assert original.geo['follow'].coords[:] == coords
    release_gerber_track_bend(editor, (5, 1))
    result = editor.storage_dict[10]['geometry'][0]
    assert result.geo['follow'].coords[:] == [(0, 0), (2.5, 0.5), (5, 1), (4.5, 2.5), (4, 4), (8, 4)]
    assert result.geo['solid'].equals_exact(result.geo['follow'].buffer(0.4 / divisor, steps), 1e-10)
    assert editor.storage_dict[10]['tag'] == 'unchanged'
    assert editor.selected == [result]
    assert editor.geometry_revision == revision + 1
    assert editor.is_modified
    assert float(editor.ui.area_entry.get_value()) == pytest.approx(result.geo['solid'].area, abs=1e-4)
    editor.plot_all.assert_called_once_with()
    assert editor.calls == {'row': 0, 'sel': 0}


@pytest.mark.parametrize('problem', ['branch'])
def test_unsupported_or_ambiguous_targets_never_mutate(problem):
    shape = track_gerber_track_bend([(0, 0), (4, 0), (4, 4)])
    editor = editor_for_gerber_track_bend([shape])
    if problem == 'branch':
        editor.storage_dict[10]['geometry'].append(track_gerber_track_bend([(4, 0), (8, 0)]))
    elif problem == 'different_aperture':
        shape.geo = track_gerber_track_bend([(0, 0), (4, 0)]).geo
        editor.storage_dict[11] = {'type': 'C', 'size': 0.4, 'geometry': [track_gerber_track_bend([(4, 0), (4, 4)])]}
    elif problem == 'noncircular':
        editor.storage_dict[10]['type'] = 'R'
    elif problem == 'clear':
        shape.geo['clear'] = box(1, 1, 2, 2)
    elif problem == 'modified':
        shape.geo['solid'] = shape.geo['solid'].buffer(0.2)
    elif problem == 'region':
        editor.storage_dict[10]['type'] = 'REG'
    elif problem == 'closed':
        shape.geo = track_gerber_track_bend([(0, 0), (4, 0), (4, 4), (0, 0)]).geo
    elif problem == 'endpoint':
        shape.geo = track_gerber_track_bend([(0, 0), (4, 0)]).geo
    else:
        shape.geo = {'follow': Point(4, 0), 'solid': Point(4, 0).buffer(0.2)}
    revision = editor.geometry_revision
    before = shape.geo
    start(editor, (4, 0))
    release_gerber_track_bend(editor, (5, 1))
    assert shape.geo is before
    assert editor.storage_dict[10]['geometry'][0] is shape
    assert editor.geometry_revision == revision
    editor.plot_all.assert_not_called()


@pytest.mark.parametrize('position', [(float('nan'), 1)])
def test_invalid_destination_is_atomic(position):
    shape = track_gerber_track_bend([(0, 0), (4, 0), (4, 4)])
    editor = editor_for_gerber_track_bend([shape])
    revision = editor.geometry_revision
    start(editor, (4, 0))
    release_gerber_track_bend(editor, position)
    assert editor.storage_dict[10]['geometry'] == [shape]
    assert editor.geometry_revision == revision


def test_bend_is_undone_and_redone_by_the_editor_history():
    shape = track_gerber_track_bend([(0, 0), (4, 0), (4, 4)])
    editor = editor_for_gerber_track_bend([shape])
    editor.selected = [shape]
    start(editor, (4, 0))
    release_gerber_track_bend(editor, (5, 1))
    changed = editor.storage_dict[10]['geometry'][0]
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [shape]
    assert not editor.is_modified
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'][0].geo['follow'].equals(changed.geo['follow'])
    assert editor.is_modified


def test_real_editor_save_build_reparses_without_region_conversion(app):

    source = make_gerber(app, NATIVE_GBR, name='board')
    original_tools = source.tools
    shape = track_gerber_track_bend([(0, 0), (4, 0), (4, 4)])
    editor = editor_for_gerber_track_bend([shape])
    start(editor, (4, 0))
    release_gerber_track_bend(editor, (5, 1))
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    saved = result['obj']
    assert saved.tools[10]['size'] == 0.4
    assert not saved.export_as_regions
    assert 'G36*' not in saved.source_file
    assert source.tools is original_tools
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(saved.source_file.splitlines()) is None
    record = parsed.tools[10]['geometry'][0]
    assert record['follow'].coords[:] == [(0, 0), (5, 1), (4, 4)]
    assert record['solid'].hausdorff_distance(editor.storage_dict[10]['geometry'][0].geo['solid']) < 1e-6


NO_MOD = QtCore.Qt.KeyboardModifier.NoModifier


SQUARE = box(0, 0, 10, 10)   # exterior: (10,0) (10,10) (0,10) (0,0) (10,0) in this shapely version


LABEL = 'Edit vertices'


def region_editor(geometry=SQUARE):
    shape = DrawToolShape({'solid': geometry, 'follow': geometry.exterior})
    editor = make_editor({0: aperture([shape], kind='REG')})
    editor.history.reset()
    make_select(editor)
    editor.selected = [shape]
    return editor, shape


def track_editor(follow=None):
    follow = follow or LineString([(0, 0), (10, 0), (10, 10)])
    shape = DrawToolShape({'solid': follow.buffer(0.2, 16), 'follow': follow})
    editor = make_editor({10: {'type': 'C', 'size': 0.4, 'geometry': [shape]}})
    editor.history.reset()
    make_select(editor)
    editor.selected = [shape]
    return editor, shape


def warnings(editor):
    return [c[0][0] for c in editor.app.inform.emit.call_args_list]


def warned(editor):
    return any('[WARNING_NOTCL]' in text for text in warnings(editor))


def storage_shape(editor, apid):
    return editor.storage_dict[apid]['geometry'][0]


def test_plan_point_follow_pad_has_no_grips():
    shape = pad(1, 1, 0.5)
    editor = make_editor({10: aperture([shape], kind='C')})
    make_select(editor)
    editor.selected = [shape]
    assert vertex_plan(editor, shape) is None
    assert editor.on_edit_vertices() == 'fail'
    assert any('pad' in text for text in warnings(editor))


def follow_matches_solid(shape):
    solid = shape.geo['solid']
    return list(shape.geo['follow'].coords) == list(solid.exterior.coords)


def test_region_move_updates_solid_and_follow_in_one_step_and_undo_redo_restore_exactly():
    editor, shape = region_editor()
    editor.apply_vertex_edit('move', 1, (12, 12), target=shape)
    result = storage_shape(editor, 0)
    assert (12, 12) in list(result.geo['solid'].exterior.coords)
    assert follow_matches_solid(result)
    assert len(editor.history.undo_stack) == 1
    assert editor.history.undo_stack[0]['label'] == LABEL
    assert editor.undo()
    assert storage_shape(editor, 0) is shape
    assert shape.geo['solid'].equals_exact(SQUARE, 0.0)
    assert list(shape.geo['follow'].coords) == list(SQUARE.exterior.coords)
    assert not editor.is_modified
    assert editor.redo()
    result = storage_shape(editor, 0)
    assert (12, 12) in list(result.geo['solid'].exterior.coords)
    assert follow_matches_solid(result)


def test_region_remove_drops_vertex_from_solid_and_follow():
    editor, shape = region_editor()
    editor.apply_vertex_edit('remove', 1, target=shape)
    result = storage_shape(editor, 0)
    assert (10, 10) not in list(result.geo['solid'].exterior.coords)
    assert follow_matches_solid(result)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert shape.geo['solid'].equals_exact(SQUARE, 0.0)


def test_track_move_edits_follow_and_rebuilds_solid_with_same_width():
    editor, shape = track_editor()
    editor.apply_vertex_edit('move', 1, (14, 3), target=shape)
    result = storage_shape(editor, 10)
    assert list(result.geo['follow'].coords) == [(0, 0), (14, 3), (10, 10)]
    assert result.geo['solid'].equals_exact(result.geo['follow'].buffer(0.2, 16), 1e-9)
    # the solid keeps the radius of the original track: the buffer radius is the same
    assert result.geo['solid'].hausdorff_distance(result.geo['follow'].buffer(0.2, 16)) < 1e-9
    assert len(editor.history.undo_stack) == 1


def test_track_closing_edit_is_rejected_without_a_history_step():
    editor, shape = track_editor()
    outcome = editor.apply_vertex_edit('move', 2, (0, 0), target=shape)
    assert outcome == 'fail'
    assert warned(editor)
    assert storage_shape(editor, 10) is shape
    assert editor.history.undo_stack == []


def track_gerber_editor_auto(coords, width=0.4):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / 2, 16)})


def flash_gerber_editor_auto(x, y=0, diameter=2):
    follow = Point(x, y)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(diameter / 2, 64)})


def region_gerber_editor_auto(polygon):
    return DrawToolShape({'solid': polygon, 'follow': polygon.exterior})


def editor_for_gerber_editor_auto(storage):
    editor = make_editor(storage)
    editor.loaded_as_regions = False
    editor.selected = []
    return editor


def snapshot_gerber_editor_auto(editor):
    return {key: list(value['geometry']) for key, value in editor.storage_dict.items()}


def flash_at_gerber_editor_auto(editor, x, y):
    """The aperture and record of the flash whose follow point is (x, y)."""
    for aperture in editor.storage_dict.values():
        for shape in aperture['geometry']:
            follow = shape.geo.get('follow')
            if isinstance(follow, Point) and follow.equals_exact(Point(x, y), 1e-9):
                return aperture, shape
    return None, None


def is_stored(editor, record):
    return any(shape is record for aperture in editor.storage_dict.values() for shape in aperture['geometry'])


def pad_and_track_editor(track_width=0.4):
    pad, path = flash_gerber_editor_auto(0), track_gerber_editor_auto([(0, 0), (10, 0)], width=track_width)
    return editor_for_gerber_editor_auto({10: {'type': 'C', 'size': 2, 'geometry': [pad, path]}}), pad, path


def test_drawn_circle_and_rectangle_become_flashes_and_l_shape_is_skipped():
    circle = region_gerber_editor_auto(Point(0, 0).buffer(1, 64))
    rect = region_gerber_editor_auto(box(10, 0, 12, 1))
    ell = region_gerber_editor_auto(Polygon([(20, 0), (22, 0), (22, 1), (21, 1), (21, 2), (20, 2)]))
    editor = editor_for_gerber_editor_auto({11: {'type': 'REG', 'size': 0, 'geometry': [circle, rect, ell]}})
    before, keys, before_footprint = snapshot_gerber_editor_auto(editor), set(editor.storage_dict), footprint(editor)
    assert auto_module.apply_pads_to_flashes(editor, 0.01) == (2, 1)
    c_aperture, c_shape = flash_at_gerber_editor_auto(editor, 0, 0)
    assert c_aperture['type'] == 'C' and c_aperture['size'] == 2
    assert c_shape.geo['solid'].symmetric_difference(circle.geo['solid']).area < 0.01
    r_aperture, _ = flash_at_gerber_editor_auto(editor, 11, 0.5)
    assert r_aperture['type'] == 'R' and (r_aperture['width'], r_aperture['height']) == (2, 1)
    assert editor.storage_dict[11]['geometry'] == [ell]
    assert footprint(editor).symmetric_difference(before_footprint).area < 0.01
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot_gerber_editor_auto(editor) == before
    assert set(editor.storage_dict) == keys


def test_teardrop_fills_the_neck_and_is_skipped_for_a_track_wider_than_the_pad():
    editor, pad, path = pad_and_track_editor()
    before, keys, before_footprint = snapshot_gerber_editor_auto(editor), set(editor.storage_dict), footprint(editor)
    added = auto_module.apply_teardrops(editor, 50, 80)
    assert len(added) == 1
    assert is_stored(editor, added[0])
    drop = added[0].geo['solid']
    assert drop.intersection(pad.geo['solid']).area > 0
    assert drop.intersection(path.geo['solid']).area > 0
    assert footprint(editor).area > before_footprint.area + 0.05
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot_gerber_editor_auto(editor) == before
    assert set(editor.storage_dict) == keys
    wide, _, _ = pad_and_track_editor(track_width=2.5)
    wide_before = snapshot_gerber_editor_auto(wide)
    assert auto_module.apply_teardrops(wide, 50, 80) == []
    assert snapshot_gerber_editor_auto(wide) == wide_before


def test_snowman_adds_a_circle_of_the_given_size_on_the_track_outside_the_pad():
    editor, _, _ = pad_and_track_editor()
    before, keys = snapshot_gerber_editor_auto(editor), set(editor.storage_dict)
    added = auto_module.apply_snowman(editor, 50, 50)
    assert len(added) == 1
    assert is_stored(editor, added[0])
    ball = added[0].geo['solid']
    assert ball.area == pytest.approx(math.pi * 0.5 ** 2, rel=0.01)
    assert ball.centroid.distance(Point(1.5, 0)) < 0.01
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot_gerber_editor_auto(editor) == before
    assert set(editor.storage_dict) == keys


@pytest.mark.parametrize('kind', ['O'])
def test_drawn_pads_save_as_flashes_of_rectangular_and_oblong_apertures(app, kind):
    pad = box(0, 0, 2, 1) if kind == 'R' else LineString([(0.5, 0.5), (3.5, 0.5)]).buffer(0.5, 64)
    editor = editor_for_gerber_editor_auto({10: {'type': 'C', 'size': 2, 'geometry': [flash_gerber_editor_auto(20)]},
                         0: {'type': 'REG', 'size': 0, 'geometry': [region_gerber_editor_auto(pad)]}})
    assert auto_module.apply_pads_to_flashes(editor, 0.01)[0] == 1
    assert any(aperture['type'] == kind for aperture in editor.storage_dict.values())
    assert not editor.loaded_as_regions
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for tool in parsed.tools.values()
               if tool.get('type') == kind for record in tool['geometry'])


sys.path.insert(0, str(Path(__file__).resolve().parent))


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _gerber():
    app = make_app()
    obj = make_gerber(app)
    obj.mark_shapes = Mock()
    counter = iter(range(1000))
    obj.add_mark_shape = Mock(side_effect=lambda **kw: next(counter))
    obj.mark_shapes.add_batch.side_effect = lambda batch, **kw: [next(counter) for _ in batch]
    return app, obj


def _aperture_with_solid(obj):
    for ap, tool in obj.tools.items():
        if any('solid' in el for el in tool['geometry']):
            return ap
    raise AssertionError('no aperture with solid geometry')


def test_plot_aperture_without_storage_key_stores_shape_keys():
    app, obj = _gerber()
    ap = _aperture_with_solid(obj)
    obj.mark_shapes_storage.clear()

    obj.plot_aperture(run_thread=False, marked_aperture=ap, color='#ff0000')

    assert obj.mark_shapes_storage[ap]
    assert len(obj.mark_shapes_storage[ap]) == len(obj.mark_shapes.add_batch.call_args.args[0])
    app.log.error.assert_not_called()
    obj.mark_shapes.redraw.assert_called_once()


@pytest.mark.parametrize('only_flashes, run_thread', [(False, False), (True, True)])
def test_marking_batches_shapes_and_keeps_aperture_keys(only_flashes, run_thread):
    app, obj = _gerber()
    first = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    second = Polygon([(3, 0), (4, 0), (4, 1), (3, 1)])
    line = LineString([(0, 0), (2, 2)])
    obj.tools = {10: {'geometry': [
        {'solid': MultiPolygon([first, second]), 'follow': Point(0, 0)},
        {'solid': MultiLineString([line]), 'follow': line},
        {'solid': first, 'follow': Point(0, 0)}, {'follow': Point(0, 0)}]}}
    expected = [first, second, first] if only_flashes else [first, second, line, first]
    keys = list(range(100, 100 + len(expected)))
    obj.mark_shapes_storage = {10: ['existing'], 20: ['other']}
    obj.mark_shapes.add_batch.side_effect = None
    obj.mark_shapes.add_batch.return_value = keys

    obj.plot_aperture(only_flashes=only_flashes, run_thread=run_thread,
                      marked_aperture=10, color='#12345678', visible=False)
    if run_thread:
        obj.mark_shapes.add_batch.assert_not_called()
        task = app.worker_task.emit.call_args.args[0]
        task['fcn'](*task['params'])

    obj.mark_shapes.add_batch.assert_called_once()
    batch = obj.mark_shapes.add_batch.call_args.args[0]
    assert [item['shape'].wkb for item in batch] == [shape.wkb for shape in expected]
    assert all(item['color'] == item['face_color'] == '#12345678' for item in batch)
    assert obj.mark_shapes.add_batch.call_args.kwargs == {
        'tolerance': obj.drawing_tolerance, 'layer': 0, 'visible': False}
    assert obj.mark_shapes_storage == {10: ['existing'] + keys, 20: ['other']}
    obj.add_mark_shape.assert_not_called()
    obj.mark_shapes.redraw.assert_called_once()
    app.log.error.assert_not_called()

    obj.clear_plot_apertures(10)
    assert [call.args[0] for call in obj.mark_shapes.remove.call_args_list] == ['existing'] + keys
    assert obj.mark_shapes_storage == {10: [], 20: ['other']}


@pytest.mark.parametrize('operation', ['union', 'difference'])
def test_material_regions_preserve_holes_islands_and_history(operation):
    ring = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
    island = box(4, 4, 5, 5)
    first = DrawToolShape({'solid': MultiPolygon([ring, island])})
    second = DrawToolShape({'solid': box(7, -1, 11, 11)})
    editor = make_editor({10: aperture([first]), 11: aperture([second])})
    editor.selected = [first, second]
    before = editor.geometry_revision
    expected = {'union': first.geo['solid'].union(second.geo['solid']),
                'intersection': first.geo['solid'].intersection(second.geo['solid']),
                'difference': first.geo['solid'].difference(second.geo['solid']),
                'explode': first.geo['solid'].union(second.geo['solid'])}[operation]
    assert editor.apply_region_operation(operation) != 'fail'
    assert editor.geometry_revision == before + 1
    assert editor.loaded_as_regions
    assert all(editor.storage_dict[key]['type'] == 'REG' for key in editor.storage_dict
               if editor.storage_dict[key]['geometry'])
    assert unary_union([shape.geo['solid'] for shape in editor.selected]).equals(expected)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.storage_dict[11]['geometry'] == [second]
    assert editor.redo()
    assert unary_union([shape.geo['solid'] for shape in editor.selected]).equals(expected)


@pytest.mark.parametrize('kind', ['gerber'])
def test_import_polygon_sources_and_index_lifecycle(kind):
    polygon = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
    source = SimpleNamespace(kind=kind, obj_options={'name': 'source', 'plot': True},
                             solid_geometry=[polygon], drawing_tolerance=0.001, multigeo=False)
    tool = SimpleNamespace(app=SimpleNamespace(collection=MagicMock()),
                           draw_app=SimpleNamespace(gerber_obj=object()))
    tool.app.collection.get_list.return_value = [source]
    assert list(ImportEditorGrb.import_candidates(tool, box(3, 3, 4, 4))) == []
    found = list(ImportEditorGrb.import_candidates(tool, box(-1, -1, 11, 11)))
    assert found[0][1][0]['type'] == 'REG'
    source.solid_geometry[:] = [box(20, 20, 21, 21)]
    assert list(ImportEditorGrb.import_candidates(tool, box(-1, -1, 11, 11))) == []
    tool.app.collection.get_list.return_value = []
    assert list(ImportEditorGrb.import_candidates(tool, box(-1, -1, 11, 11))) == []


def test_import_preflight_rejects_the_whole_batch():
    editor = make_editor()
    tool = SimpleNamespace(draw_app=editor, app=editor.app, sel_storage=[{
        'type': 'REG', 'size': 0, 'geometry': [DrawToolShape({'solid': box(0, 0, 1, 1)}),
                                             DrawToolShape({'solid': LineString([(0, 0), (1, 1)])})]}])
    before = editor.geometry_revision
    assert ImportEditorGrb.import_shapes(tool) == 'fail'
    assert editor.storage_dict == {}
    assert editor.geometry_revision == before
    assert not editor.history.undo_stack


@pytest.mark.parametrize('operation', ['union', 'difference'])
def test_material_regions_of_a_board_match_geos_without_clipper(operation):
    from unittest import mock
    from appCommon import clipper_ops
    tiles = [box(x * 1.5, y * 1.5, x * 1.5 + 2, y * 1.5 + 2) for x in range(10) for y in range(10)]
    expected = unary_union(tiles) if operation == 'union' else tiles[0].difference(unary_union(tiles[1:]))
    for clipper in (True, False):
        shapes = [DrawToolShape({'solid': tile}) for tile in tiles]
        editor = make_editor({10: aperture(shapes)})
        editor.selected = shapes
        with mock.patch.object(clipper_ops, 'pyclipr', clipper_ops.pyclipr if clipper else None):
            assert editor.apply_region_operation(operation) != 'fail'
        result = unary_union([shape.geo['solid'] for shape in editor.selected])
        assert result.symmetric_difference(expected).area < 1e-6


def test_saving_a_board_of_records_fuses_the_copper_like_geos(app):
    from unittest import mock
    from appCommon import clipper_ops
    source = make_gerber(app, NATIVE_GBR)
    tiles = [box(x * 1.5, y * 1.5, x * 1.5 + 2, y * 1.5 + 2) for x in range(10) for y in range(10)]
    tools = {10: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': tile} for tile in tiles]}}
    saved = []
    for clipper in (True, False):
        with mock.patch.object(clipper_ops, 'pyclipr', clipper_ops.pyclipr if clipper else None):
            saved.append(unary_union(editor_save(app, source, tools, loaded_as_regions=True).solid_geometry))
    assert saved[0].symmetric_difference(saved[1]).area < 1e-6
    assert saved[0].symmetric_difference(unary_union(tiles)).area < 1e-6


def counting_app():
    return SimpleNamespace(inform=Mock(), jump_signal=Mock(), proc_container=MagicMock())


def grb_tool_editor_move_batch(cls, storage_dict, selected, apertures, dx=2.0, dy=-1.0):
    tool = cls.__new__(cls)
    tool.origin = (0.0, 0.0)
    tool.destination = (dx, dy)
    tool.selected_apertures = apertures
    tool.draw_app = SimpleNamespace(
        storage_dict=storage_dict, selected=selected, get_selected=lambda: selected,
        touch_geometry=Mock(), delete_utility_geometry=Mock(), tool_shape=Mock(), plot_all=Mock(),
        build_ui=Mock(), app=counting_app())
    return tool


class TestGerberEditorMove(unittest.TestCase):

    def setUp(self):
        self.pad = GrbShape({'solid': box(0, 0, 1, 1), 'follow': Point(0.5, 0.5)})
        self.track = GrbShape({'solid': LineString([(0, 0), (3, 0)]).buffer(0.1),
                               'follow': LineString([(0, 0), (3, 0)]), 'clear': box(1, -0.05, 1.1, 0.05)})
        self.other = GrbShape({'solid': box(5, 5, 6, 6)})
        self.storage = {'10': {'geometry': [self.pad, self.other]}, '11': {'geometry': [self.track]}}

    def test_move_translates_every_key_and_removes_the_source(self):
        selected = [self.pad, self.track]
        tool = grb_tool_editor_move_batch(MoveEditorGrb, self.storage, selected, ['10', '11'])
        tool.make()

        ap10 = self.storage['10']['geometry']
        self.assertIs(ap10[0], self.other)
        self.assertEqual(ap10[1].geo['solid'].bounds, (2.0, -1.0, 3.0, 0.0))
        self.assertEqual(ap10[1].geo['follow'].coords[0], (2.5, -0.5))
        ap11 = self.storage['11']['geometry']
        self.assertEqual(len(ap11), 1)
        self.assertEqual(set(ap11[0].geo), {'solid', 'follow', 'clear'})
        self.assertEqual(ap11[0].geo['clear'].bounds, (3.0, -1.05, 3.1, -0.95))
        self.assertEqual(selected, [])
        tool.draw_app.plot_all.assert_called_once()
        tool.draw_app.touch_geometry.assert_called_once()


    def test_copy_keeps_the_source(self):
        selected = [self.pad]
        tool = grb_tool_editor_move_batch(CopyEditorGrb, self.storage, selected, ['10'])
        tool.make()
        ap10 = self.storage['10']['geometry']
        self.assertEqual(len(ap10), 3)
        self.assertIs(ap10[0], self.pad)
        self.assertEqual(ap10[2].geo['solid'].bounds, (2.0, -1.0, 3.0, 0.0))
        tool.draw_app.plot_all.assert_called_once()


class TestGeometryEditorMove(unittest.TestCase):

    def test_move_prepares_without_mutation_then_commits_once(self):
        a, b, c = GeoShape(box(0, 0, 1, 1)), GeoShape(LineString([(0, 0), (1, 1)])), GeoShape(box(5, 5, 6, 6))
        storage = AppGeoEditor.make_storage()
        for shp in (a, b, c):
            storage.insert(shp)
        editor = SimpleNamespace(storage=storage, selected=[a, b], utility=[b], plot_all=Mock(), build_ui=Mock(),
                                 sel_shapes=Mock(), app=counting_app())
        editor.get_selected = lambda: editor.selected
        editor.delete_shape = lambda shapes: AppGeoEditor.delete_shape(editor, shapes)

        tool = FCMove.__new__(FCMove)
        tool.draw_app = editor
        tool.origin, tool.destination = (0.0, 0.0), (3.0, 0.0)
        tool.draw_cursor_data = Mock()
        tool.make()

        self.assertEqual([shp.geo.bounds for shp in tool.geometry], [(3.0, 0.0, 4.0, 1.0), (3.0, 0.0, 4.0, 1.0)])
        self.assertEqual(list(storage.get_objects()), [a, b, c])
        self.assertEqual(editor.selected, [a, b])
        self.assertEqual(editor.utility, [b])
        editor.plot_all.assert_not_called()      # on_shape_complete() plots after the new shapes are added
        self.assertTrue(tool.complete)
        editor.active_tool = tool
        editor.app.options = {'geometry_editor_milling_type': 'cv'}
        editor.app.defaults = SimpleNamespace(get=lambda k, d=None: d, factory_defaults={})
        editor.app.log = Mock()
        editor.build_ui_sig = Mock()
        editor.tool_shape = Mock()
        editor.plot_changes = Mock()
        editor._collect_shape = lambda shp, acc: AppGeoEditor._collect_shape(editor, shp, acc)
        editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
        AppGeoEditor.on_shape_complete(editor)
        self.assertEqual(set(storage.get_objects()), {c, *tool.geometry})
        self.assertEqual(editor.selected, tool.geometry)
        self.assertEqual(editor.utility, [])
        editor.plot_changes.assert_called_once()
        self.assertTrue(AppGeoEditor.undo(editor))
        self.assertEqual(set(storage.get_objects()), {a, b, c})
        self.assertEqual(editor.selected, [a, b])


class TestClimbMillingDirection(unittest.TestCase):
    """With the 'cl' milling type only a new drawn shape is reversed; a moved shape keeps its direction."""

    def complete(self, tool_cls):
        line = LineString([(0, 0), (1, 0), (2, 1)])
        tool = tool_cls.__new__(tool_cls)
        tool.geometry = [GeoShape(line)]
        app = SimpleNamespace(options={'geometry_editor_milling_type': 'cl'}, log=Mock(),
                              defaults=SimpleNamespace(get=lambda k, d=None: d, factory_defaults={}))
        editor = SimpleNamespace(app=app, active_tool=tool, add_shape=Mock(), delete_utility_geometry=Mock(),
                                 tool_shape=Mock(), plot_changes=Mock(), selected=[])
        AppGeoEditor.on_shape_complete(editor)
        return list(editor.add_shape.call_args[0][0][0].geo.coords)

    def test_drawn_shape_is_reversed(self):
        from appEditors.appGeoEditor import FCPath
        self.assertEqual(self.complete(FCPath), [(2, 1), (1, 0), (0, 0)])

    def test_moved_copied_and_exploded_shapes_keep_the_direction(self):
        from appEditors.appGeoEditor import FCCopy, FCExplode
        for tool_cls in (FCMove, FCCopy, FCExplode):
            self.assertEqual(self.complete(tool_cls), [(0, 0), (1, 0), (2, 1)], tool_cls.__name__)


if __name__ == '__main__':
    unittest.main()


CTRL = QtCore.Qt.KeyboardModifier.ControlModifier


SHIFT_ONLY = QtCore.Qt.KeyboardModifier.ShiftModifier


STRETCH = CTRL | QtCore.Qt.KeyboardModifier.ShiftModifier


CORDS = CTRL | QtCore.Qt.KeyboardModifier.AltModifier


NONE = QtCore.Qt.KeyboardModifier.NoModifier


def test_alt_connected_polyline_gets_cords_only_at_outer_extremities():
    line = LineString([(0, 0), (2, 0), (4, 0), (6, 0), (8, 0)])
    result = segment_geometries({'line': line}, {'line': {(0, 0, 1), (0, 0, 2)}}, (0, 2), cords=True)
    assert list(result['line'].coords) == [(0, 0), (2, 0), (2, 2), (4, 2), (6, 2), (6, 0), (8, 0)]


def test_alt_closed_ring_wraparound_is_one_group_and_preserves_holes():
    polygon = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)], [[(3, 3), (4, 3), (4, 4), (3, 4)]])
    result = segment_geometries({'p': polygon}, {'p': {(0, 0, 3), (0, 0, 0)}}, (-1, -1), cords=True)['p']
    assert list(result.exterior.coords) == [(-1, -1), (7, -1), (8, 0), (8, 8), (0, 8), (-1, 7), (-1, -1)]
    assert list(result.interiors[0].coords) == list(polygon.interiors[0].coords)


def test_invalid_result_is_rejected_and_zero_offset_is_a_noop():
    line = LineString([(0, 0), (2, 0), (2, 2), (0, 2)])
    selected = {'l': {(0, 0, 1)}}
    with pytest.raises(ValueError):
        segment_geometries({'l': line}, selected, (-2, 0))
    assert segment_geometries({'l': line}, selected, (0, 0), cords=True) == {}
    with pytest.raises(ValueError):
        segment_geometries({'l': line}, selected, (float('nan'), 0))


def geo_drag(geometry):
    editor, original = drag_editor(geometry)
    editor.apply_selection_drag = lambda *a, **k: AppGeoEditor.apply_selection_drag(editor, *a, **k)
    editor.get_pick_index = lambda: AppGeoEditor.get_pick_index(editor)
    editor.pick_candidates = lambda point: AppGeoEditor.pick_candidates(editor, point)
    return editor, original


@pytest.mark.parametrize('modifier,expected', [
    (STRETCH, [(0, 0), (2, 2), (6, 2), (8, 0)]),
    (CORDS, [(0, 0), (2, 0), (2, 2), (6, 2), (6, 0), (8, 0)]),
])
def test_geo_direct_drag_commits_once_and_undo_redo_restore(modifier, expected):
    editor, original = geo_drag(LineString([(0, 0), (2, 0), (6, 0), (8, 0)]))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (3, 0), modifier)
    assert grips.move((20, 0), (3, 2))
    assert editor.selected == [original]
    assert grips.release((20, 0), (3, 2))
    assert list(editor.selected[0].geo.coords) == expected
    assert len(editor._history_undo) == 1
    assert editor.undo() and editor.selected == [original]
    assert editor.redo() and list(editor.selected[0].geo.coords) == expected


def test_cancel_and_tool_change_do_not_commit_segment_drags():
    editor, original = geo_drag(LineString([(0, 0), (4, 0)]))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (1, 0), CORDS)
    grips.move((20, 0), (1, 2))
    grips.cancel()
    assert not grips.release((20, 0), (1, 2))
    assert editor.selected == [original] and not editor._history_undo
    editor.active_tool.name = 'move'
    assert not grips.press((0, 0), (1, 0), STRETCH)


@pytest.mark.parametrize('modifier', [CORDS])
def test_gerber_trace_drag_preserves_width_and_one_undo_step(modifier):
    editor, original = track_editor(LineString([(0, 0), (4, 0), (8, 0), (12, 0)]))
    editor.tool_shape = FakeCollection()
    grips = editor.vertex_grips
    grips.sync()
    assert grips.press((0, 0), (5, 0), modifier)
    grips.release((20, 0), (5, 2))
    result = editor.selected[0]
    assert result is not original
    assert result.geo['solid'].equals_exact(result.geo['follow'].buffer(0.2, 16), 1e-9)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo() and editor.selected == [original]
    assert editor.redo()


def test_grid_snaps_the_displacement_without_a_jump_at_the_press():
    editor, original = geo_drag(LineString([(0, 0), (4, 0)]))
    editor.app.grid_status = lambda: True
    editor.editor_options = {'global_gridx': 0.1, 'global_gridy': 0.1}
    editor.snap = lambda x, y: (round(x), round(y))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (1.2, 0), CORDS)
    grips.release((20, 0), (1.3, 1.7))
    assert list(editor.selected[0].geo.coords) == [(0, 0), (0, 2), (4, 2), (4, 0)]
