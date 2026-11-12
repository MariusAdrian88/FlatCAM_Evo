"""Geometry Editor tests: history, input, vertices, source, import, snapping, split/join, resize, vertex ops and grips."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from shapely.geometry import LineString, MultiLineString
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape
from unittest.mock import Mock
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, Polygon, box
from contextlib import nullcontext
from threading import Event, Thread, Timer, get_ident
from unittest.mock import Mock, MagicMock
import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import unary_union
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape, DrawToolUtilityShape, FCMove, FCExplode, FCEraser, FCShapeTool
from camlib import Geometry
from appGUI.MainGUI import MainGUI
from appGUI.GUIElements import FCTree
from appEditors.geo_plugins.GeoCirclePlugin import CircleEditorTool
from appEditors.geo_plugins.GeoRectanglePlugin import RectangleEditorTool
from appEditors.geo_plugins.GeoPaintPlugin import PaintOptionsTool
from appMain import App
from appHandlers.appHistory import DEFAULT_STEPS, HistoryManager
from appHandlers.appIO import appIO
from appHandlers.appSignalConnector import AppSignalConnector
from shapely.affinity import rotate, translate
from shapely.geometry import LineString, box
from appEditors.appGeoEditor import FCCopy, DrawToolShape
from PyQt6 import QtWidgets
from shapely.geometry import LineString, LinearRing, Polygon, MultiLineString, box
from appEditors.appGeoEditor import AppGeoEditor, AppGeoEditorUI, DrawToolShape
from shapely import STRtree
from appEditors.cursor_text import show_cursor_text, hide_cursor_text
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape, DrawToolUtilityShape, FCPath, FCCircle, FCRectangle, FCMove, FCCopy
from appEditors.geo_plugins.GeoPathPlugin import PathEditorTool
from appEditors.geo_plugins.GeoCopyPlugin import CopyEditorTool
from appEditors.geo_plugins.GeoTextPlugin import TextInputTool
from appGUI.GUIElements import FCDoubleSpinner, NumericalEvalEntry
from io import StringIO
from time import perf_counter
from threading import Thread
from unittest.mock import Mock, patch
import ezdxf
from shapely.geometry import LineString, Polygon
from appEditors.appGeoEditor import AppGeoEditor
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appProjects import ProjectManager
from appObjects.GeometryObject import GeometryObject
from appWorker import Worker
from appProcess import FCVisibleProcessContainer
from appGUI.GUIElements import FlatCAMActivityView
from tests.helpers_objects import make_app, save_and_load  # noqa: F401,E402
from PIL import Image, ImageDraw
from shapely.geometry import GeometryCollection, LinearRing, LineString, Polygon, box
from appEditors.appGeoEditor import (AppGeoEditor, DrawToolShape, FCImport, FCImportImage, import_source_geometries)
from appEditors.geo_plugins.GeoImportImagePlugin import GeoImportImageTool, image_paths
from appPlugins.ToolImage.ImageImportMixin import ImageImportMixin
from shapely.geometry import LineString, LinearRing
from appEditors.appGeoEditor import FCPath, FCPolygon, bend_corner_point
from appEditors.appGerberEditor import RegionEditorGrb, TrackEditorGrb
from appEditors.editor_corner import corner_point, next_bend_mode
from PyQt6 import QtGui, QtWidgets
from threading import Event, Thread
from shapely.geometry import LineString, MultiLineString, Point
from appEditors.appGeoEditor import AppGeoEditor, AppGeoEditorUI, DrawToolShape, FCPath
import shapely
from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, box
from appEditors.appGeoEditor import AppGeoEditor, FCMove, FCCopy, FCEraser, DrawToolShape, FCCircle, FCRectangle
from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
from appEditors.geo_plugins.GeoPaintPlugin import PaintEditorUI, PaintOptionsTool
from appEditors.geo_plugins.GeoTransformationPlugin import TransformEditorTool
from camlib import Geometry, translate_geometry
from shapely.geometry import LineString, LinearRing, MultiLineString, Polygon
from appEditors import appGeoEditor as geo
from shapely.geometry import LineString
import math
from shapely.affinity import scale
from shapely.geometry import LinearRing, LineString, Point, Polygon, box
from copy import deepcopy
import pickle
import numpy as np
from shapely import LineString, Polygon, box
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape, FCSelect, FCMove
from appGUI.VisPyVisuals import ShapeCollectionVisual
from shapely.geometry import LineString, Point, Polygon, MultiLineString, box
from PyQt6 import QtCore
from shapely import BufferJoinStyle
from shapely.geometry import LineString, MultiPolygon, Point, box
from appEditors.vertex_grips import VertexGrips, GRIP_COLOR, INVALID_COLOR
from shapely.geometry import MultiPolygon, Polygon, box
from appEditors.appGeoEditor import AppGeoEditor, AppGeoEditorUI
from appEditors.vertex_ops import circle_center, find_arcs, vertex_grips
from appEditors.vertex_grips import VertexGrips
from shapely.geometry import LineString, Polygon, GeometryCollection
from shapely.geometry import LineString, LinearRing, Point, Polygon, MultiPolygon, box
from appEditors.vertex_ops import (vertex_grips, ring_after_edit, edit_vertex, check_vertex_result,
                                   contrast_color, circle_center, arc_middle, arc_points, find_arcs, ring_skeleton,
                                   expand_skeleton)


def save_editor(geos, tooldia=0.5, multigeo_tool=None):
    storage = AppGeoEditor.make_storage()
    storage.replace(inserted=[DrawToolShape(geo) for geo in geos])
    editor = SimpleNamespace(
        storage=storage, multigeo_tool=multigeo_tool,
        app=SimpleNamespace(proc_container=MagicMock(), inform=Mock()),
        ui=SimpleNamespace(tooldia_entry=SimpleNamespace(get_value=lambda: tooldia)))
    editor.deactivate = Mock()
    return editor


def geometry_object(tooldia=0.5, tools=None):
    obj = SimpleNamespace(solid_geometry=[LineString([(9, 9), (10, 10)])], tools=tools or {},
                          obj_options={'tools_mill_tooldia': tooldia}, regenerate_source=Mock(return_value=True))
    obj.bounds = lambda: (0.0, 0.0, 2.0, 1.0)
    return obj


class TestGeoEditorSave(unittest.TestCase):
    def test_object_has_the_new_geometry_before_the_editor_ends(self):
        a, b = LineString([(0, 0), (1, 0)]), LineString([(0, 1), (2, 1)])
        editor = save_editor([a, b])
        obj = geometry_object()
        seen = []
        editor.deactivate.side_effect = lambda: seen.append(list(obj.solid_geometry))

        AppGeoEditor.update_editor_geometry(editor, obj)

        # the copy is done in the call (no worker thread), then the editor is deactivated 1 time
        self.assertEqual(obj.solid_geometry, [a, b])
        self.assertEqual(seen, [[a, b]])
        self.assertEqual((obj.obj_options['xmin'], obj.obj_options['ymax']), (0.0, 1.0))
        obj.regenerate_source.assert_called_once_with()
        editor.app.inform.emit.assert_called_once()

    def test_multilinestring_is_merged(self):
        multi = MultiLineString([[(0, 0), (1, 0)], [(1, 0), (1, 1)]])
        editor = save_editor([multi])
        obj = geometry_object()
        AppGeoEditor.update_editor_geometry(editor, obj)
        self.assertEqual(len(obj.solid_geometry), 1)
        self.assertEqual(obj.solid_geometry[0].geom_type, 'LineString')
        self.assertEqual(len(obj.solid_geometry[0].coords), 3)


    def test_multigeo_tool_gets_the_geometry(self):
        a = LineString([(0, 0), (1, 0)])
        tools = {1: {'tooldia': 0.5, 'data': {'tools_mill_tooldia': 0.5}, 'solid_geometry': []}}
        editor = save_editor([a], tooldia=0.6, multigeo_tool=1)
        obj = geometry_object(tools=tools)
        AppGeoEditor.update_editor_geometry(editor, obj)
        self.assertEqual(tools[1]['solid_geometry'], [a])
        self.assertEqual(tools[1]['tooldia'], 0.6)
        self.assertEqual(tools[1]['data']['tools_mill_tooldia'], 0.6)
        self.assertIsNone(editor.multigeo_tool)


class FakeCollection:
    """Records clears, plotted shapes and redraws like a ShapeCollection."""

    def __init__(self):
        self.calls = []
        self.data = {}
        self.hidden = set()
        self.last_key = -1

    @property
    def items(self):
        """The shown shapes."""
        return [shape for key, shape in self.data.items() if key not in self.hidden]

    def clear(self, update=False):
        self.calls.append(('clear', update))
        self.data = {}
        self.hidden = set()
        self.last_key = -1

    def _new_key(self, shape):
        self.last_key += 1
        self.data[self.last_key] = shape
        return self.last_key

    def add(self, shape=None, color=None, **kwargs):
        self.calls.append(('add', color))
        return self._new_key(shape)

    def add_copy(self, source, source_key, color=None, face_color=None, linewidth=None):
        if source_key not in source.data:
            return None
        self.calls.append(('copy', color))
        return self._new_key(source.data[source_key])

    def remove(self, key, update=False):
        self.calls.append(('remove', key))
        self.data.pop(key, None)
        self.hidden.discard(key)

    def update_visibility(self, state, indexes=None):
        self.calls.append(('visible', state, len(indexes)))
        if state:
            self.hidden -= set(indexes)
        else:
            self.hidden |= set(indexes)

    def redraw(self, *args, **kwargs):
        self.calls.append(('redraw',))


def make_editor(shapes, selected=(), utility=()):
    storage = AppGeoEditor.make_storage()
    for shp in shapes:
        storage.insert(shp)
    app = SimpleNamespace(
        options={'global_draw_color': '#112233FF', 'global_sel_draw_color': '#AABBCCFF', 'global_theme': 'default'},
        app_units='MM', defaults=SimpleNamespace(get=lambda k, d=None: d, factory_defaults={}), log=Mock())
    editor = SimpleNamespace(
        app=app, storage=storage, selected=list(selected), utility=list(utility),
        shapes=FakeCollection(), sel_shapes=FakeCollection(),
        fcgeometry=SimpleNamespace(drawing_tolerance=0.01), build_ui=Mock(), plot_all=Mock(), plot_changes=Mock())
    editor.get_selected = lambda: editor.selected
    editor.get_draw_color = lambda: AppGeoEditor.get_draw_color(editor)
    editor.color_with_alpha = AppGeoEditor.color_with_alpha
    editor.get_sel_color = lambda: AppGeoEditor.get_sel_color(editor)
    editor.plot_shape = lambda *a, **k: AppGeoEditor.plot_shape(editor, *a, **k)
    editor.delete_shape = lambda s, inserted=(): AppGeoEditor.delete_shape(editor, s, inserted)
    editor.get_render_state = lambda: AppGeoEditor.get_render_state(editor)
    editor.copy_to_selection = lambda entry, color: AppGeoEditor.copy_to_selection(editor, entry, color)
    editor.unplot_entry = lambda entry: AppGeoEditor.unplot_entry(editor, entry)
    editor.submit_worker = lambda fcn, params: AppGeoEditor.submit_worker(editor, fcn, params)
    return editor


def line(x):
    return DrawToolShape(LineString([(x, 0), (x, 1)]))


class TestGeoEditorDelete(unittest.TestCase):
    def test_delete_selected_removes_every_selected_shape(self):
        a, b, c, d = line(0), line(1), line(2), line(3)
        editor = make_editor([a, b, c, d], selected=[a, b, c], utility=[b])
        sel_list = editor.selected
        editor.sel_shapes.clear = Mock()
        editor.sel_shapes.redraw = Mock()
        AppGeoEditor.delete_selected(editor)
        self.assertEqual(list(editor.storage.get_objects()), [d])
        self.assertEqual(editor.selected, [])
        self.assertEqual(editor.utility, [])
        self.assertIs(editor.selected, sel_list)


def selection_editor(shapes, selected=()):
    editor = make_editor(shapes, selected=selected)
    editor.plot_all = lambda: AppGeoEditor.plot_all(editor)
    editor.plot_changes = lambda: AppGeoEditor.plot_changes(editor)
    editor.plot_all()
    editor.shapes.calls.clear()
    editor.sel_shapes.calls.clear()
    return editor


def tree_editor(shapes):
    """An editor with a real tree, rows like build_ui() makes them: column 0 is str(id(shape))."""
    editor = selection_editor(shapes)
    editor.ui = Mock()
    tw = QtWidgets.QTreeWidget()
    tw.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
    parent = QtWidgets.QTreeWidgetItem(tw, ['Geometry'])
    for shp in shapes:
        QtWidgets.QTreeWidgetItem(parent, [str(id(shp))])
    editor.ui.tw = tw
    editor.tree_handler_runs = 0

    def on_tree_selection():
        editor.tree_handler_runs += 1
        AppGeoEditor.on_tree_selection(editor)

    editor.on_tree_selection = on_tree_selection
    editor.active_tool = None
    tw.itemSelectionChanged.connect(editor.on_tree_selection)
    editor.shapes_by_tree_id = lambda: AppGeoEditor.shapes_by_tree_id(editor)
    editor.sync_tree_selection = lambda: AppGeoEditor.sync_tree_selection(editor)
    editor.update_all_vertex_count = lambda: AppGeoEditor.update_all_vertex_count(editor)
    editor.app.delete_selection_shape = Mock()
    editor.app.options['global_mselect_key'] = 'Control'
    return editor


def tree_selected(editor):
    return sorted(item.text(0) for item in editor.ui.tw.selectedItems())


class TestGeoEditorSelectionTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


    def test_area_selection_with_the_key_toggles(self):
        shapes = [line(x) for x in range(6)]
        editor = tree_editor(shapes)
        editor.selected = [shapes[0], shapes[1]]
        with unittest.mock.patch.object(QtWidgets.QApplication, 'keyboardModifiers',
                                        return_value=QtCore.Qt.KeyboardModifier.ControlModifier):
            AppGeoEditor.draw_selection_area_handler(editor, (0.5, -0.5), (2.5, 2), True)
        self.assertEqual(editor.selected, [shapes[0], shapes[2]])


def pick_editor(shapes):
    """A tree editor with the click selection: 1 screen pixel is 0.01 plot units, so the pick tolerance is 0.05."""
    editor = tree_editor(shapes)
    editor.canvas = SimpleNamespace(pixel_size=lambda: 0.01)
    editor.interdict_selection = False
    editor.update_ui = Mock()
    editor.app.inform = Mock()
    editor.get_pick_index = lambda: AppGeoEditor.get_pick_index(editor)
    editor.pick_at = lambda point: AppGeoEditor.pick_at(editor, point)
    editor.click_select = lambda point: AppGeoEditor.click_select(editor, point)
    return editor


CONTROL = QtCore.Qt.KeyboardModifier.ControlModifier


class TestGeoEditorClickSelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        # click_select() reads the keyboard state of the application; a Shift or Ctrl state left by an earlier test
        # would extend the selection. A test that needs a key patches it again inside.
        patcher = unittest.mock.patch.object(QtWidgets.QApplication, 'keyboardModifiers',
                                             return_value=QtCore.Qt.KeyboardModifier.NoModifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_click_on_a_line_selects_it_and_the_row(self):
        shapes = [line(x) for x in range(4)]
        editor = pick_editor(shapes)
        # the middle of the line: far from its vertices, on the line
        self.assertTrue(editor.click_select((1.02, 0.5)))
        self.assertEqual(editor.selected, [shapes[1]])
        self.assertEqual(tree_selected(editor), [str(id(shapes[1]))])


    def test_click_with_the_key_adds_and_removes(self):
        shapes = [line(x) for x in range(4)]
        editor = pick_editor(shapes)
        with unittest.mock.patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=CONTROL):
            editor.click_select((1.0, 0.5))
            editor.click_select((3.0, 0.5))
            self.assertEqual(editor.selected, [shapes[1], shapes[3]])
            editor.click_select((1.0, 0.5))
            self.assertEqual(editor.selected, [shapes[3]])

    def test_click_inside_of_nested_polygons_picks_the_smaller(self):
        big = DrawToolShape(box(0, 0, 10, 10))
        small = DrawToolShape(box(4, 4, 6, 6))
        editor = pick_editor([big, small])
        editor.click_select((5, 5))
        self.assertEqual(editor.selected, [small])
        editor.click_select((1, 1))
        self.assertEqual(editor.selected, [big])


class TestGeoEditorSimplify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def simplify_tool(self, editor):
        from appEditors.appGeoEditor import FCSimplification
        tool = FCSimplification.__new__(FCSimplification)
        tool.draw_app = editor
        tool.simp_tool = Mock()
        return tool


    def test_simplify_replaces_only_the_selected_shapes(self):
        from appEditors.geo_plugins.GeoSimplificationPlugin import SimplificationTool
        from shapely.geometry import Point
        circle = DrawToolShape(Point(5, 5).buffer(2, quad_segs=32))
        others = [line(x) for x in range(3)]
        editor = selection_editor(others + [circle], selected=[circle])
        editor.interdict_selection = False
        editor._collect_shape = lambda shape, to_insert: AppGeoEditor._collect_shape(editor, shape, to_insert)
        editor.build_ui_sig = Mock()
        jobs = []
        editor.app.worker_task = SimpleNamespace(emit=jobs.append)
        editor.app.proc_container = unittest.mock.MagicMock()
        editor.app.inform = Mock()
        tool = SimpleNamespace(app=editor.app, geo_editor=editor, update_ui=Mock(),
                               ui=SimpleNamespace(geo_tol_entry=SimpleNamespace(get_value=lambda: 0.1)))
        tool.calculate_coordinates_vertex = lambda geos: SimplificationTool.calculate_coordinates_vertex(tool, geos)

        SimplificationTool.on_simplification_click(tool)
        jobs[0]['fcn'](*jobs[0]['params'])

        objects = list(editor.storage.get_objects())
        self.assertEqual(len(objects), 4)
        self.assertEqual(objects[:3], others)
        new = objects[3]
        self.assertIsNot(new, circle)
        self.assertLess(len(new.geo.exterior.coords), len(circle.geo.exterior.coords))
        self.assertEqual(editor.selected, [new])
        self.assertEqual(editor.sel_shapes.items, [new.geo])
        self.assertFalse(editor.interdict_selection)


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def editor_with_history(shapes=(), selected=()):
    editor = make_editor(shapes, selected)
    for name in ('add_shape', '_collect_shape', 'delete_shape', 'undo', 'redo', 'reset_history',
                 'history_operation', 'replace_shapes', 'submit_worker', 'on_shape_complete'):
        setattr(editor, name, getattr(AppGeoEditor, name).__get__(editor))
    editor.build_ui_sig = SimpleNamespace(emit=Mock())
    editor.tool_shape = SimpleNamespace(clear=Mock(), redraw=Mock())
    editor.app.inform = SimpleNamespace(emit=Mock())
    editor.app.proc_container = SimpleNamespace(new=lambda *a: nullcontext())
    editor.app.worker_task = SimpleNamespace(emit=Mock())
    editor.reset_history()
    return editor


def run_worker(editor):
    job = editor.app.worker_task.emit.call_args.args[0]
    return job['fcn'](*job['params'])


def interactive_move_editor():
    original = DrawToolShape(LineString([(0, 0), (1, 0)]))
    editor = editor_with_history([original], [original])
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: pos)
    editor.snap = lambda x, y: (x, y)
    editor.x, editor.y = 3, 2
    editor.in_action = True
    editor.select_tool = Mock()
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    editor.app.grid_status = lambda: False
    editor.app.ui = SimpleNamespace(rel_position_label=Mock(), update_location_labels=Mock(),
                                    popMenu=SimpleNamespace(mouse_is_panning=False))
    editor.app.plotcanvas = SimpleNamespace(on_update_text_hud=Mock())
    editor.app.jump_signal = Mock()
    editor.app.mouse_pos, editor.app.on_jump_to = (3, 2), Mock()
    editor.app.geo_editor = editor
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    tool = FCMove.__new__(FCMove)
    tool.draw_app, tool.storage, tool.name = editor, editor.storage, 'move'
    tool.origin, tool.destination = (0, 0), (3, 2)
    tool.has_selection, tool.complete = True, False
    tool.geometry, tool.points = [], [(0, 0)]
    tool.draw_cursor_data = Mock()
    tool.move_tool = SimpleNamespace(length=0, width=0)
    editor.active_tool = tool
    return editor, tool, original


def assert_no_success(editor):
    assert not any('[success]' in str(call) for call in editor.app.inform.emit.call_args_list)


def test_add_delete_redo_invalidation_and_metadata():
    editor = editor_with_history()
    shape = DrawToolShape(LineString([(0, 0), (1, 0)]))
    shape.data['custom'] = {'name': 'original'}
    editor.add_shape(shape)
    assert editor.undo()
    assert not list(editor.storage.get_objects())
    assert editor.redo()
    editor.selected = [shape]
    editor.delete_shape(shape)
    assert editor.undo()
    assert editor.selected == [shape]
    assert shape.data['custom'] == {'name': 'original'}
    assert editor.storage.nearest((0, 0))[1] is shape
    editor.add_shape(DrawToolShape(box(3, 3, 4, 4)))
    assert not editor.redo()


def test_editor_and_application_histories_are_independent():
    editor = editor_with_history()
    app = editor.app
    # the app history checkpoints the object (object_state) before undo, so the stand-in needs its state attributes
    obj = SimpleNamespace(history_uid='project-object', name='Renamed', kind='geometry',
                          ser_attrs=['obj_options'], obj_options={'name': 'Renamed'})
    collection = SimpleNamespace(get_list=lambda: [obj],
                                 rename_object=lambda target, name: setattr(target, 'name', name))
    app.call_source, app.headless, app.projects = 'app', False, None
    app.history_changed = SimpleNamespace(emit=Mock())
    app.history = HistoryManager(app, collection)
    app.history.record_rename(obj, 'Original', 'Renamed')
    project_step = app.history.undo_stack[0]
    assert editor._history_undo is not app.history.undo_stack
    assert editor._history_redo is not app.history.redo_stack

    # A direct subtool commit and a worker subtool commit use the same editor stack.
    editor.active_tool = SimpleNamespace(name='circle')
    editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    editor.active_tool = SimpleNamespace(name='buffer')
    editor.submit_worker(lambda: editor.add_shape(DrawToolShape(box(2, 0, 3, 1))), [])
    run_worker(editor)
    assert AppGeoEditor.history_labels(editor) == ['Circle', 'buffer']
    assert app.history.undo_stack == [project_step]
    assert not app.history.redo_stack

    assert editor.undo()
    editor_undo, editor_redo = list(editor._history_undo), list(editor._history_redo)
    app.call_source = 'geo_editor'
    assert not app.history.undo()
    assert app.history.undo_stack == [project_step]
    app.call_source = 'app'
    assert app.history.undo()
    assert obj.name == 'Original'
    assert editor._history_undo == editor_undo
    assert editor._history_redo == editor_redo
    assert app.history.redo()
    assert obj.name == 'Renamed'
    assert editor._history_undo == editor_undo
    assert editor._history_redo == editor_redo

    editor.reset_history()
    assert app.history.undo_stack == [project_step]
    assert not app.history.redo_stack
    editor.add_shape(DrawToolShape(box(4, 0, 5, 1)))
    editor_step = editor._history_undo[0]
    app.history.clear()
    assert editor._history_undo == [editor_step]
    assert editor.undo()


def test_move_completion_restores_metadata_selection_and_index():
    original = DrawToolShape(LineString([(0, 0), (2, 0)]))
    original.data['custom'] = {'label': 'track'}
    editor = editor_with_history([original], [original])
    tool = FCMove.__new__(FCMove)
    tool.draw_app = editor
    tool.origin, tool.destination = (0, 0), (4, 3)
    tool.draw_cursor_data = Mock()
    editor.app.jump_signal = SimpleNamespace(disconnect=Mock())
    editor.app.options['geometry_editor_milling_type'] = 'cl'
    editor.active_tool = tool
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    tool.make()
    assert list(editor.storage.get_objects()) == [original]
    assert not editor.undo()
    AppGeoEditor.on_shape_complete(editor)
    moved = tool.geometry[0]
    assert moved.data['custom'] == original.data['custom']
    assert editor.selected == [moved]
    assert editor.storage.nearest((4, 3))[1] is moved
    assert editor.undo()
    assert list(editor.storage.get_objects()) == [original]
    assert editor.selected == [original]
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == [moved]
    assert editor.selected == [moved]


@pytest.mark.parametrize('operation', ['union'])
def test_boolean_variants_undo_and_redo_as_one_operation(operation):
    a, b = DrawToolShape(box(0, 0, 2, 2).exterior), DrawToolShape(box(1, 1, 3, 3).exterior)
    editor = editor_with_history([a, b], [a, b])
    editor.flatten = lambda geo: AppGeoEditor.flatten(editor, geo)
    getattr(AppGeoEditor, operation)(editor)
    run_worker(editor)
    result = list(editor.storage.get_objects())
    result_selection = list(editor.selected)
    assert editor.undo()
    assert set(editor.storage.get_objects()) == {a, b}
    assert editor.selected == [a, b]
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == result
    assert editor.selected == result_selection


@pytest.mark.parametrize('operation', ['subtract', 'cutpath'])
@pytest.mark.parametrize('target_kind', ['ring'])
@pytest.mark.parametrize('cutter', [box(1, 1, 3, 3)])
def test_subtraction_uses_areas_while_cutpath_uses_outlines(operation, target_kind, cutter):
    polygon = box(0, 0, 2, 2)
    geometry = {
        'ring': polygon.exterior,
        'closed_path': LineString(polygon.exterior.coords),
        'polygon': polygon,
        'open_path': LineString([(0, 1), (2, 1)])
    }[target_kind]
    target, tool = DrawToolShape(geometry), DrawToolShape(cutter.exterior)
    editor = editor_with_history([target, tool], [target, tool])
    editor.flatten = lambda geo: AppGeoEditor.flatten(editor, geo)
    getattr(AppGeoEditor, operation)(editor)
    run_worker(editor)
    shapes = list(editor.storage.get_objects())
    assert (tool in shapes) == (operation != 'subtract')
    results = [shape.geo for shape in shapes if shape is not tool]
    assert results
    expected_target = geometry if target_kind == 'open_path' else \
        polygon.exterior if operation == 'cutpath' else polygon
    assert unary_union(results).equals(expected_target.difference(cutter))


@pytest.mark.parametrize('transform,args,kwargs,expected', [
    ('scale', (-2, 1), {}, (-4, 0, 0, 2)),
    ('buffer', (-0.25,), {'join': 2}, (0.25, 0.25, 1.75, 1.75)),
])
def test_transform_reflection_and_erosion_preserve_metadata_and_replay(transform, args, kwargs, expected):
    shape = DrawToolShape(box(0, 0, 2, 2))
    shape.data['custom'] = {'name': 'source'}
    editor = editor_with_history([shape], [shape])
    AppGeoEditor.transform_shapes(editor, transform, *args, **kwargs)
    result = editor.selected[0]
    assert result.geo.is_valid and not result.geo.is_empty
    assert result.geo.bounds == expected
    assert result.data['bounds'] == result.bounds()
    assert result.data['custom'] == shape.data['custom']
    assert editor.storage.nearest(expected[:2])[1] is result
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.selected == [shape]
    assert shape.geo.bounds == (0, 0, 2, 2)
    assert list(editor.storage.get_objects()) == [shape]
    assert editor.redo()
    assert editor.selected[0].geo.bounds == expected


@pytest.mark.filterwarnings('error::RuntimeWarning')
def test_affine_overflow_keeps_all_originals_atomically():
    shapes = [DrawToolShape(LineString([(0, 0), (1, 1)])),
              DrawToolShape(LineString([(1e308, 0), (1e308, 1)]))]
    editor = editor_with_history(shapes, shapes)
    with pytest.raises(ValueError):
        AppGeoEditor.transform_shapes(editor, 'scale', 2, 2)
    assert list(editor.storage.get_objects()) == shapes
    assert editor.selected == shapes
    assert not editor._history_undo


def test_eraser_restores_replacement_selection_on_redo():
    original = DrawToolShape(box(0, 0, 5, 5))
    editor = editor_with_history([original], [original])
    editor.get_pick_index = lambda: AppGeoEditor.get_pick_index(editor)
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    editor.app.jump_signal = SimpleNamespace(disconnect=Mock())
    tool = FCEraser.__new__(FCEraser)
    tool.draw_app = editor
    tool.destination = (0, 0)
    tool.utility_geometry = lambda **kwargs: SimpleNamespace(geo=[box(-1, -1, 2, 6)])
    tool.make()
    replacement = list(editor.selected)
    assert replacement and replacement != [original]
    assert editor.undo()
    assert editor.selected == [original]
    assert editor.redo()
    assert editor.selected == replacement


def test_explode_replaces_every_selected_shape_by_its_lines():
    # a ring before a MultiLineString (its segments were thrown away), an open path and a polygon (both were
    # removed with nothing in their place) and a single segment (kept as it is)
    segment = DrawToolShape(LineString([(9, 0), (9, 1)]))
    originals = [DrawToolShape(box(0, 0, 1, 1).exterior),
                 DrawToolShape(MultiLineString([[(3, 0), (4, 0)], [(3, 1), (4, 1)]])),
                 DrawToolShape(LineString([(5, 0), (6, 0), (6, 1)])),
                 DrawToolShape(Polygon(box(10, 0, 14, 4).exterior.coords, [box(11, 1, 12, 2).exterior.coords])),
                 segment]
    editor = editor_with_history(originals, originals)
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    tool = FCExplode.__new__(FCExplode)
    tool.draw_app = editor
    editor.active_tool = tool
    tool.make()
    shapes = list(editor.storage.get_objects())
    assert segment in shapes and not set(originals[:-1]) & set(shapes)
    assert len(shapes) == 4 + 2 + 2 + 8 + 1
    assert unary_union([s.geo for s in shapes]).equals(unary_union([s.geo.boundary if s.geo.geom_type == 'Polygon'
                                                                    else s.geo for s in originals]))
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(originals)


@pytest.mark.parametrize('tool_class', [CircleEditorTool])
@pytest.mark.parametrize('valid', [True, False])
def test_numeric_apply_replaces_geometry_and_selection_in_one_step(tool_class, valid):
    original = DrawToolShape(box(0, 0, 1, 1).exterior)
    original.data['custom'] = {'label': 'original'}
    editor = editor_with_history([original], [original])
    editor.delete_selected = lambda: AppGeoEditor.delete_selected(editor)
    editor.storage.replace = Mock(wraps=editor.storage.replace)
    values = dict(x_entry=5, y_entry=6, radius_x_entry=2 if valid else 0, radius_y_entry=2, angle_entry=0,
                  anchor_radio='c', corner_radio='s', radius_entry=0, length_entry=4 if valid else 0, width_entry=2)
    ui = SimpleNamespace(**{name: SimpleNamespace(get_value=lambda v=value: v)
                            for name, value in values.items()})
    ui.radius_link_btn = SimpleNamespace(isChecked=lambda: True)
    tool = SimpleNamespace(draw_app=editor, app=editor.app, ui=ui, mode='change')
    tool.on_add = lambda: tool_class.on_add(tool)
    tool_class.on_execute(tool)
    replacements = list(editor.storage.get_objects())
    if not valid:
        assert replacements == [original]
        assert editor.selected == [original]
        editor.storage.replace.assert_not_called()
        assert not editor.undo()
        return
    assert replacements and original not in replacements
    editor.storage.replace.assert_called_once()
    assert editor.selected == replacements
    assert editor.undo()
    assert list(editor.storage.get_objects()) == [original]
    assert editor.selected == [original]
    assert original.data['custom'] == {'label': 'original'}
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == replacements
    assert editor.selected == replacements


def test_project_restore_honors_rejected_editor_exit():
    app = MagicMock()
    handler = appIO(app)
    handler.on_file_new_project = Mock(return_value='fail')
    assert handler._restore_project_handler({'objs': [], 'options': {}},
                                            'unused.FlatPrj', False, False, False, False) is False
    app.options.load_project.assert_not_called()


def test_failed_operation_rolls_back_without_clearing_redo():
    editor = editor_with_history()
    editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    editor.undo()
    with pytest.raises(ValueError, match='cancel operation'):
        with editor.history_operation():
            editor.add_shape(DrawToolShape(box(2, 2, 3, 3)))
            raise ValueError('cancel operation')
    assert not list(editor.storage.get_objects())
    assert not editor.undo()
    assert editor.redo()


def radio(value):
    return SimpleNamespace(get_value=lambda: value)


def copy_tool(selected, mode='n'):
    """An FCCopy with the shapes already selected, without the Copy plugin GUI."""
    editor = SimpleNamespace(selected=list(selected), app=SimpleNamespace(inform=Mock(), jump_signal=Mock()))
    editor.get_selected = lambda: editor.selected

    tool = FCCopy.__new__(FCCopy)
    tool.draw_app = editor
    tool.complete = False
    tool.points = []
    tool.geometry = None
    tool.origin = None
    tool.destination = None
    tool.util_geo = None
    tool.clicked_postion = None
    tool.has_selection = True
    tool.sel_limit = 30
    tool.draw_cursor_data = Mock()
    tool.copy_tool = SimpleNamespace(ui=SimpleNamespace(
        mode_radio=radio(mode), array_type_radio=radio('linear'), axis_radio=radio('X'), pitch_entry=radio(10.0),
        linear_angle_spinner=radio(0.0), array_size_entry=radio(3)))
    tool.selection_shape = tool.selection_bbox()
    return tool


class TestGeoEditorCopy(unittest.TestCase):

    def setUp(self):
        self.square = DrawToolShape(box(0, 0, 1, 1))
        self.line = DrawToolShape(LineString([(0, 0), (2, 0)]))

    def test_second_click_without_cursor_move_copies_to_the_destination(self):
        tool = copy_tool([self.square, self.line])
        tool.click((0.0, 0.0))      # reference point
        tool.click((3.0, 1.0))      # destination, no mouse move event in between
        self.assertTrue(tool.complete)
        self.assertEqual([shp.geo.bounds for shp in tool.geometry], [(3.0, 1.0, 4.0, 2.0), (3.0, 1.0, 5.0, 1.0)])
        # the source shapes are not changed
        self.assertEqual(self.square.geo.bounds, (0.0, 0.0, 1.0, 1.0))


@pytest.mark.parametrize('direction,sign', [('CW', -1)])
def test_circular_copy_rotates_radially_at_final_destination(direction, sign):
    source = DrawToolShape(LineString([(2, 3), (4, 3)]))
    tool = copy_tool([source], 'a')
    ui = tool.copy_tool.ui
    ui.array_type_radio = radio('circular')
    ui.array_dir_radio, ui.angle_entry = radio(direction), radio(90)
    tool.click((2, 3))
    tool.util_geo = DrawToolShape(box(100, 100, 101, 101))
    tool.click((7, 3))
    for i, result in enumerate(tool.geometry):
        import math
        angle = sign * 90 * i
        expected = translate(rotate(source.geo, angle, origin=(2, 3)),
                             5 * math.cos(math.radians(angle)), 5 * math.sin(math.radians(angle)))
        assert result.geo.equals_exact(expected, 1e-12)
    assert list(source.geo.coords) == [(2, 3), (4, 3)]


def vertex_editor(geometry):
    shape = DrawToolShape(geometry)
    shape.data['custom'] = {'label': 'track'}
    editor = editor_with_history([shape], [shape])
    editor.plot_all = lambda: AppGeoEditor.plot_all(editor)
    editor.plot_changes = lambda: AppGeoEditor.plot_changes(editor)
    editor.plot_all()
    editor.apply_vertex_edit = lambda *a, **k: AppGeoEditor.apply_vertex_edit(editor, *a, **k)
    return editor, shape


@pytest.mark.parametrize('action,index,point,expected', [
    ('move', 1, (2, 1), [(0, 0), (2, 1), (4, 0)]),
    ('insert', 0, (1, 0), [(0, 0), (1, 0), (2, 0), (4, 0)]),
])
def test_open_path_edit_is_one_replacement_with_history(action, index, point, expected):
    editor, original = vertex_editor(LineString([(0, 0), (2, 0), (4, 0)]))
    assert editor.apply_vertex_edit(action, index, point) != 'fail'
    replacement, = editor.selected
    assert list(replacement.geo.coords) == expected
    assert replacement.data['custom'] == original.data['custom']
    assert replacement.data['bounds'] == replacement.geo.bounds
    assert editor.storage.nearest(expected[index if index < len(expected) else -1])[1] is replacement
    assert editor.sel_shapes.items == [replacement.geo]
    editor.build_ui_sig.emit.assert_called_once()
    assert editor.undo()
    assert editor.selected == [original]
    assert list(editor.storage.get_objects()) == [original]
    assert editor.sel_shapes.items == [original.geo]
    assert not editor.undo()
    assert editor.redo()
    assert editor.selected == [replacement]
    assert editor.sel_shapes.items == [replacement.geo]


@pytest.mark.parametrize('factory', [Polygon])
@pytest.mark.parametrize('index', [0])
def test_closing_vertex_alias_moves_both_ends(factory, index):
    editor, original = vertex_editor(factory([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]))
    assert editor.apply_vertex_edit('move', index, (-1, -1)) != 'fail'
    geometry = editor.selected[0].geo
    coords = list(geometry.exterior.coords if isinstance(geometry, Polygon) else geometry.coords)
    assert coords[0] == coords[-1] == (-1, -1)
    assert type(geometry) is type(original.geo)
    assert len(coords) == 5


def test_polygon_holes_survive_move_insert_and_remove():
    hole = [(2, 2), (3, 2), (3, 3), (2, 3), (2, 2)]
    editor, original = vertex_editor(Polygon([(0, 0), (6, 0), (6, 6), (0, 6)], [hole]))
    for action, index, point in [('move', 0, (-1, -1)), ('insert', 1, (6, 3)), ('remove', 2, None)]:
        assert editor.apply_vertex_edit(action, index, point) != 'fail'
        assert list(editor.selected[0].geo.interiors[0].coords) == hole
    assert len(editor._history_undo) == 3
    for _ in range(3):
        assert editor.undo()
    assert editor.selected == [original]


@pytest.mark.parametrize('factory', [Polygon])
def test_insert_on_closing_segment_and_remove_closing_alias(factory):
    editor, original = vertex_editor(factory([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]))
    assert editor.apply_vertex_edit('insert', 3, (0, 2)) != 'fail'
    assert editor.apply_vertex_edit('remove', 5) != 'fail'
    geometry = editor.selected[0].geo
    coords = list(geometry.exterior.coords if isinstance(geometry, Polygon) else geometry.coords)
    assert coords == [(4, 0), (4, 4), (0, 4), (0, 2), (4, 0)]
    assert geometry.is_valid


@pytest.mark.parametrize('geometry,action,index,point', [
    (LineString([(0, 0), (1, 0)]), 'remove', 0, None),
    (LineString([(0, 0), (2, 0), (4, 0)]), 'move', 1, (float('nan'), 0)),
    (Polygon([(0, 0), (6, 0), (6, 6), (0, 6)], [[(1, 1), (2, 1), (2, 2), (1, 2)]]),
     'move', 0, (4, 4)),
])
def test_invalid_edits_preserve_storage_selection_and_redo(geometry, action, index, point):
    editor, original = vertex_editor(geometry)
    assert editor.apply_vertex_edit(action, index, point) == 'fail'
    assert editor.selected == [original]
    assert list(editor.storage.get_objects()) == [original]
    assert not editor.undo()
    editor.build_ui_sig.emit.assert_not_called()
    assert editor.app.inform.emit.called


@pytest.mark.parametrize('geometry', [LineString([(0, 0), (1, 0), (1, 1), (0, 1)])])
def test_3d_or_self_crossing_result_is_rejected(geometry):
    editor, original = vertex_editor(geometry)
    assert editor.apply_vertex_edit('move', 0, (0, 2)) == 'fail'
    assert editor.selected == [original]
    assert not editor.undo()


NAMES = dict(zip([FCPath, FCCircle, FCRectangle, FCMove, FCCopy], ['path', 'circle', 'rectangle', 'move', 'copy']))


TOOLS = [FCPath, FCCircle, FCMove]


def value(number):
    return SimpleNamespace(get_value=lambda: number, set_value=Mock())


def input_tool(cls, points=((0.0, 0.0),), length=5.0):
    original = DrawToolShape(LineString([(0, 0), (1, 0)]))
    editor = editor_with_history([original], [original])
    editor.app.mouse_pos, editor.app.on_jump_to = (3.0, 4.0), Mock()
    editor.app.jump_signal = Mock()
    editor.app.plotcanvas = SimpleNamespace(text_cursor=SimpleNamespace(parent=object()),
                                          view=SimpleNamespace(camera=SimpleNamespace(zoom_callback=Mock())))
    editor.app.ui = SimpleNamespace(notebook=Mock())
    editor.app.call_source, editor.app.geo_editor, editor.app.collection = 'geo_editor', editor, Mock()
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    editor.x, editor.y = editor.app.mouse_pos
    editor.snap_x, editor.snap_y = editor.app.mouse_pos
    editor.snap = lambda x, y: (x, y)
    editor.in_action = bool(points)
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    editor.draw_utility_geometry = Mock()
    editor.select_tool = Mock()
    tool = cls.__new__(cls)
    tool.draw_app, tool.app, tool.storage = editor, editor.app, editor.storage
    tool.name, tool.points, tool.complete = NAMES[cls], list(points), False
    tool.geometry, tool.util_geo = None, None
    tool.new_segment, tool.cursor_data_control = False, True
    tool.draw_cursor_data = Mock()
    tool.origin = points[0] if points else None
    tool.destination, tool.has_selection, tool.sel_limit = None, True, 30
    tool.steps_per_circ = 16
    ui = SimpleNamespace(x_entry=value(0.0), y_entry=value(0.0), angle_entry=value(0.0),
                         radius_link_btn=SimpleNamespace(isChecked=lambda: True),
                         radius_x_entry=value(length), radius_y_entry=value(length),
                         mode_radio=value('n'), corner_radio=value('s'), radius_entry=value(0.0),
                         add_button=QtWidgets.QPushButton(), clear_btn=QtWidgets.QPushButton(),
                         path_tool_frame=QtWidgets.QWidget(), copy_frame=QtWidgets.QWidget(),
                         circle_frame=QtWidgets.QWidget(), rect_frame=QtWidgets.QWidget())
    panel = SimpleNamespace(length=length, width=length, radius_x=length, radius_y=length, ui=ui,
                            on_tab_close=Mock(), disconnect_signals=Mock())
    setattr(tool, {'path': 'path_tool', 'circle': 'circle_tool', 'rectangle': 'rect_tool',
                   'move': 'move_tool', 'copy': 'copy_tool'}[tool.name], panel)
    tool.ui = ui
    editor.active_tool = tool
    return editor, tool, panel, original


def dispatch(editor, key, monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.NoModifier))
    gui = SimpleNamespace(app=editor.app, geo_select_btn=SimpleNamespace(isChecked=lambda: False))
    event = key if isinstance(key, int) else SimpleNamespace(key=key, native=SimpleNamespace())
    return MainGUI.keyPressEvent(gui, event)


def assert_uncommitted(editor, tool, original):
    assert list(editor.storage.get_objects()) == [original]
    assert original.geo.equals_exact(LineString([(0, 0), (1, 0)]), 0)
    assert not tool.complete
    assert not editor.undo()


@pytest.mark.parametrize('cls', [FCPath])
@pytest.mark.parametrize('number', [float('nan')])
def test_invalid_numeric_input_keeps_construction_and_history(cls, number, monkeypatch):
    editor, tool, panel, original = input_tool(cls, length=number)
    dispatch(editor, 'Enter', monkeypatch)
    assert_uncommitted(editor, tool, original)
    assert tool.points == [(0.0, 0.0)]
    editor.app.on_jump_to.assert_not_called()
    editor.select_tool.assert_not_called()
    assert editor.app.inform.emit.called


@pytest.mark.parametrize('cls', [FCPath])
def test_valid_numeric_projection_and_coordinate_jump(cls, monkeypatch):
    editor, tool, panel, original = input_tool(cls, length=10.0)
    # Path has no width and move/copy use Path/CopyEditorTool, which have no width property.
    del panel.width
    dispatch(editor, 'Enter', monkeypatch)
    editor.app.on_jump_to.assert_called_once_with(custom_location=(6.0, 8.0), fit_center=False)
    if cls is FCPath:
        assert tool.points[-1] == (6.0, 8.0)
        assert_uncommitted(editor, tool, original)
    else:
        assert editor.selected[0].geo.bounds == (6.0, 8.0, 7.0, 8.0) if cls is FCMove else \
            len(list(editor.storage.get_objects())) == 2
        assert editor.undo()
        assert list(editor.storage.get_objects()) == [original]


@pytest.mark.parametrize('cls', [FCPath])
@pytest.mark.parametrize('qt_keys', [True])
@pytest.mark.parametrize('text, expected', [('2*5', 10.0)])
def test_numeric_expression_keys_use_existing_evaluator(cls, qt_keys, text, expected):
    editor, tool, panel, original = input_tool(cls, length=0.0)
    tool.new_segment = True
    panel.ui.project_line_entry = NumericalEvalEntry()
    panel.ui.project_line_entry.set_value(0.0)

    class LengthPanel:
        length = PathEditorTool.length

    numeric_panel = LengthPanel()
    numeric_panel.ui = panel.ui
    setattr(tool, {'path': 'path_tool', 'move': 'move_tool', 'copy': 'copy_tool'}[tool.name], numeric_panel)
    for char in text:
        tool.on_key(ord(char) if qt_keys else char)
    assert numeric_panel.length == expected
    tool.on_key('Enter')
    location = editor.app.on_jump_to.call_args.kwargs['custom_location']
    assert location == pytest.approx((0.6 * expected, 0.8 * expected))


@pytest.mark.parametrize('cls', [FCCircle])
@pytest.mark.parametrize('point', [(float('nan'), 3.0)])
def test_invalid_completion_click_is_non_destructive(cls, point):
    editor, tool, panel, original = input_tool(cls)
    assert tool.click(point) == 'fail'
    assert_uncommitted(editor, tool, original)
    assert tool.points == [(0.0, 0.0)]


@pytest.mark.parametrize('cls', [FCCircle])
def test_completion_click_without_mouse_move_builds_at_current_point(cls):
    editor, tool, panel, original = input_tool(cls)
    tool.util_geo = box(100, 100, 101, 101)
    tool.click((3.0, 4.0))
    editor.on_shape_complete()
    added = [s for s in editor.storage.get_objects() if s is not original]
    assert len(added) == 1
    assert added[0].geo.bounds == ((-5.0, -5.0, 5.0, 5.0) if cls is FCCircle else (0.0, 0.0, 3.0, 4.0))
    assert editor.undo()


def cursor_text_editor(*obstacles):
    # 10 pixels for 1 unit; the pixel y axis points down as on the canvas
    canvas = SimpleNamespace(dpi=72, view=SimpleNamespace(scene=object(), camera=SimpleNamespace()),
                             text_cursor=SimpleNamespace(text='', pos=None, anchors=None, font_size=None, parent=None),
                             translate_coords_2=lambda p: (500 + 10 * p[0], 500 - 10 * p[1], 0, 1),
                             translate_coords=lambda p: ((p[0] - 500) / 10, (500 - p[1]) / 10, 0, 1))
    app = SimpleNamespace(plotcanvas=canvas, decimals=4, app_units='MM', dec_format=lambda v, d: round(v, d))
    tree = STRtree(list(obstacles)) if obstacles else None
    return SimpleNamespace(app=app, get_pick_index=lambda: (tree, list(obstacles))), canvas.text_cursor


def text_tool(bold=False, italic=False, size='12', parser=None):
    app = SimpleNamespace(app_units='MM', inform=Mock(), log=Mock())
    tool = SimpleNamespace(app=app, font_bold=bold, font_italic=italic, font_name='Arial', text_path=None,
                           f_parse=SimpleNamespace(
                               font_to_geometry=parser or (lambda **kwargs: box(0, 0, 2, 1))),
                           ui=SimpleNamespace(text_input_entry=value('A'), font_size_cb=value(size),
                                              bitmap_smoothing_entry=value(0.5)))
    tool._valid_font_size = lambda: TextInputTool._valid_font_size(tool)
    return tool


STYLE_GEOMETRY = {'regular': box(0, 0, 1, 1), 'bold': box(0, 0, 2, 1),
                  'italic': box(0, 0, 1, 2), 'bi': box(0, 0, 2, 2)}


def transformation_tool(geometry=None):
    from appEditors.geo_plugins.GeoTransformationPlugin import TransformEditorTool
    shape = DrawToolShape(box(2, 3, 6, 9) if geometry is None else geometry)
    editor = editor_with_history([shape], [shape])
    editor.transform_shapes = lambda *a, **kw: AppGeoEditor.transform_shapes(editor, *a, **kw)
    editor.app.decimals, editor.app.app_units = 4, 'MM'
    editor.app.options = {'tools_transform_reference': 'Point', 'tools_transform_ref_point': '(10, 20)'}
    defaults = __import__('defaults').AppDefaults()
    editor.app.defaults = defaults
    editor.app.ui = QtWidgets.QWidget()
    editor.app.resource_location = ''
    tool = TransformEditorTool(editor.app, editor)
    return editor, tool, shape


@pytest.mark.parametrize('method', ['on_offy_key'])
def test_transform_y_shortcut_changes_y_only_after_worker(method, monkeypatch):
    import appEditors.geo_plugins.GeoTransformationPlugin as plugin
    editor, tool, shape = transformation_tool()
    monkeypatch.setattr(plugin, 'FCInputDoubleSpinner', lambda **kw: SimpleNamespace(
        set_icon=lambda icon: None, get_value=lambda: (10.0, True)))
    editor.app.call_source, editor.app.geo_editor = 'geo_editor', editor
    editor.app.collection = Mock()
    editor.transform_tool = tool
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers', staticmethod(lambda:
        QtCore.Qt.KeyboardModifier.AltModifier if method == 'on_offy_key'
        else QtCore.Qt.KeyboardModifier.ShiftModifier))
    MainGUI.keyPressEvent(SimpleNamespace(app=editor.app), SimpleNamespace(key='Y', native=SimpleNamespace()))
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    run_worker(editor)
    changed = editor.selected[0].geo
    assert changed.bounds[0::2] == pytest.approx(shape.geo.bounds[0::2])
    assert changed.bounds[1::2] != shape.geo.bounds[1::2]
    assert any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    assert editor.undo() and editor.selected == [shape]


@pytest.mark.parametrize('method,expected', [('on_rotate', (3, -6, 9, -2))])
def test_transform_rotation_is_clockwise_and_mirror_reflects_opposite_coordinate(method, expected):
    editor, tool, shape = transformation_tool()
    if method == 'on_rotate':
        tool.on_rotate(val=90, ref=0)
    else:
        getattr(tool, method)(ref=0)
    run_worker(editor)
    assert editor.selected[0].geo.bounds == pytest.approx(expected)


def test_transform_buffer_offsets_a_polygon_outline():
    # the editor loads a polygon as its outline (a closed line): 'Buffer D' moves the outline by the distance
    for distance, expected in ((1, (1, 2, 7, 10)), (-1, (3, 4, 5, 8))):
        editor, tool, shape = transformation_tool(box(2, 3, 6, 9).exterior)
        assert tool.on_buffer_action(distance, 2) is None
        assert list(editor.storage.get_objects()) == editor.selected
        assert len(editor.selected) == 1 and editor.selected[0].geo.geom_type == 'LinearRing'
        assert editor.selected[0].geo.bounds == pytest.approx(expected)


def test_transform_buffer_that_erodes_a_shape_away_keeps_it():
    editor, tool, shape = transformation_tool(box(2, 3, 6, 9).exterior)
    assert tool.on_buffer_action(-3, 2) == 'fail'
    assert list(editor.storage.get_objects()) == [shape] and editor.selected == [shape]
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)


def panel_add(cls, linked=True, **values):
    editor = editor_with_history()
    ui = SimpleNamespace(**{name: value(number) for name, number in values.items()})
    ui.radius_link_btn = SimpleNamespace(isChecked=lambda: linked)
    cls.on_add(SimpleNamespace(draw_app=editor, app=editor.app, ui=ui, mode='add'))
    return [shape.geo for shape in editor.storage.get_objects()]


def test_rectangle_panel_keeps_the_size_when_the_corner_radius_does_not_fit():
    # the default corners are Round with radius 1; when they do not fit the corners are square (as with the mouse)
    for length, width, rounded in ((10, 1, False), (-10, 4, True), (10, 4, True)):
        added = panel_add(RectangleEditorTool, anchor_radio='c', x_entry=0, y_entry=0, corner_radio='r',
                          radius_entry=1, length_entry=length, width_entry=width)
        assert len(added) == 1
        assert added[0].bounds == pytest.approx((-5, -width / 2, 5, width / 2))
        assert (Polygon(added[0]).area < 10 * width) == rounded


def test_circle_panel_adds_a_circle_with_the_y_radius_field_left_at_0():
    # in the default (linked) circle mode the Y radius field is disabled and does not follow the X field
    circle = dict(x_entry=10, y_entry=10, radius_y_entry=0, angle_entry=0)
    added = panel_add(CircleEditorTool, radius_x_entry=5, **circle)
    assert len(added) == 1 and added[0].bounds == pytest.approx((5, 5, 15, 15))
    assert panel_add(CircleEditorTool, radius_x_entry=0, **circle) == []


def test_circle_panel_rotates_an_ellipse_like_the_circle_tool():
    from shapely import Point
    from shapely.affinity import rotate, scale
    added = panel_add(CircleEditorTool, linked=False, x_entry=0, y_entry=0, radius_x_entry=4, radius_y_entry=1,
                      angle_entry=30)
    # the preview and the Enter path of FCCircle, which write the angle field, rotate by +angle (counter-clockwise)
    expected = rotate(scale(Point(0, 0).buffer(1), 4, 1), 30)
    assert len(added) == 1 and Polygon(added[0]).symmetric_difference(expected).area < 1e-9


@pytest.fixture(scope='module', autouse=True)
def qt_application_geo_editor_source():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield app


def make_geometry(units='MM', multigeo=False):
    app = make_app()
    app.app_units = units
    app.options['geometry_dxf_format'] = 'R2010'
    app.f_handlers = appIO(app)
    obj = GeometryObject('geo', app)
    obj.obj_options['tools_mill_tooldia'] = 0.5
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]])
    line = LineString([(5, 0), (6, 1)])
    obj.solid_geometry = [polygon, line]
    obj.multigeo = multigeo
    if multigeo:
        obj.tools = {1: {'tooldia': 0.5, 'data': {'tools_mill_tooldia': 0.5}, 'solid_geometry': [polygon]},
                     2: {'tooldia': 0.8, 'data': {'custom': 'keep'}, 'solid_geometry': [line]}}
    return app, obj


def app_save_editor(app, obj, shapes, monkeypatch):
    """Real QObject/queued completion, with only the editor's rendering/UI stubbed."""
    editor = AppGeoEditor.__new__(AppGeoEditor)
    QtCore.QObject.__init__(editor)
    editor.__dict__.update(save_editor(shapes).__dict__)
    editor.app = app
    if hasattr(editor, 'source_save_done'):
        editor.source_save_done.connect(editor.on_source_save_done, QtCore.Qt.ConnectionType.QueuedConnection)
    app.geo_editor, app.call_source = editor, 'geo_editor'
    app.collection.get_active.return_value = obj
    app.ui.notebook.count.return_value = 0
    app.old_state_of_tools_toolbar = False
    app.resource_location = ''
    app.post_edit_sig = Mock()
    app._history_edit_saved = Mock()
    obj.build_ui, obj.plot = Mock(), Mock()
    dialog = Mock()
    dialog.clickedButton.return_value = dialog.addButton.return_value
    monkeypatch.setattr('appMain.FCMessageBox', Mock(return_value=dialog))
    return editor


def test_save_exports_in_the_worker_and_completes_on_the_gui_thread(monkeypatch):
    app, obj = make_geometry()
    obj.source_file = 'previous source'
    line = LineString([(0, 0), (1, 0)])
    editor = app_save_editor(app, obj, [], monkeypatch)
    editor.storage = SimpleNamespace(get_objects=lambda: [SimpleNamespace(geo=line) for _ in range(50)])
    view = Mock(spec=FlatCAMActivityView)
    app.proc_container = FCVisibleProcessContainer(view, app)
    worker_thread = QtCore.QThread()
    worker = Worker(app, 'source-test')
    worker.moveToThread(worker_thread)
    worker.worker_task_signal.connect(worker.do_worker_task, QtCore.Qt.ConnectionType.QueuedConnection)
    worker_thread.finished.connect(worker.deleteLater)
    app.worker_task.emit.side_effect = worker.worker_task_signal.emit
    worker_thread.start()
    gui_thread = QtCore.QThread.currentThread()
    export_threads, finalized_threads = [], []
    export_dxf = app.f_handlers.export_dxf

    def recorded_export(*args, **kwargs):
        export_threads.append(QtCore.QThread.currentThread())
        return export_dxf(*args, **kwargs)
    monkeypatch.setattr(app.f_handlers, 'export_dxf', recorded_export)
    editor.deactivate.side_effect = lambda: finalized_threads.append(QtCore.QThread.currentThread())
    try:
        assert App.on_editing_finished(app) == 'pending'
        # nothing is exported or finished before the event loop runs
        assert obj.source_file == 'previous source'
        assert not export_threads
        editor.deactivate.assert_not_called()
        app.post_edit_sig.emit.assert_not_called()
        loop = QtCore.QEventLoop()
        poll = QtCore.QTimer()
        poll.timeout.connect(lambda: loop.quit() if app.post_edit_sig.emit.called else None)
        poll.start(10)
        QtCore.QTimer.singleShot(10000, loop.quit)
        loop.exec()
        poll.stop()
        assert app.post_edit_sig.emit.call_count == 1
        assert len(export_threads) == 1 and export_threads[0] is not gui_thread
        assert finalized_threads == [gui_thread]
        assert app.proc_container.get_procs_count() == 0
        assert len(ezdxf.read(StringIO(obj.source_file)).modelspace()) == 50
    finally:
        worker_thread.quit()
        assert worker_thread.wait(30000)


@pytest.mark.parametrize('units,unit_code', [('MM', 4)])
@pytest.mark.parametrize('multigeo', [False, True])
def test_editor_snapshot_project_restore_and_disk_export(tmp_path, units, unit_code, multigeo):
    app, obj = make_geometry(units, multigeo)
    polygon, line = obj.solid_geometry
    editor = save_editor([polygon] if multigeo else [polygon, line], multigeo_tool=1 if multigeo else None)
    editor.app = app
    obj.source_file = 'stale'
    seen = []
    editor.deactivate.side_effect = lambda: seen.append(obj.source_file)
    assert AppGeoEditor.update_editor_geometry(editor, obj) != 'fail'
    assert seen == [obj.source_file] and obj.source_file != 'stale'
    app.file_saved.emit.assert_not_called()
    project, restored = save_and_load(app, [obj], tmp_path)
    assert project['objs'][0]['source_file'] == obj.source_file
    assert len(restored) == 1
    again = restored[0]
    assert again.source_file == obj.source_file
    assert again.multigeo == multigeo
    if multigeo:
        assert again.tools[2]['data'] == {'custom': 'keep'}
        assert again.tools[2]['solid_geometry'][0].equals(line)
    snapshot = ezdxf.read(StringIO(again.source_file))
    assert snapshot.units == unit_code
    assert snapshot.header['$MEASUREMENT'] == (1 if units == 'MM' else 0)
    paths = list(snapshot.modelspace())
    assert [entity.closed for entity in paths] == [True, True, False]
    expected = [list(polygon.exterior.coords), list(polygon.interiors[0].coords), list(line.coords)]
    assert [list(entity.get_points('xy')) for entity in paths] == expected
    filename = tmp_path / 'export.dxf'
    app.collection.get_by_name.return_value = again
    app.f_handlers.export_dxf('geo', str(filename), use_thread=False)
    exported = ezdxf.readfile(filename)
    assert exported.units == unit_code
    assert [list(entity.get_points('xy')) for entity in exported.modelspace()] == expected
    # Reimport is an explicit new object, not replacement of the original tool assignments.
    imported = GeometryObject('external', app)

    def new_object(kind, name, initialize, **kwargs):
        assert kind == 'geometry' and name == 'external'
        assert initialize(imported, app) != 'fail'
        return imported

    app.app_obj.new_object.side_effect = new_object
    assert app.f_handlers.import_dxf(str(filename), outname='external', plot=False) != 'fail'
    assert imported.source_file == filename.read_text(encoding='utf-8')
    assert again.source_file == obj.source_file
    assert len(imported.solid_geometry) == 3
    assert sorted(geo.length for geo in imported.solid_geometry) == pytest.approx(
        sorted([polygon.exterior.length, polygon.interiors[0].length, line.length]))


def test_old_project_without_source_still_loads():
    app, obj = make_geometry()
    data = obj.to_dict()
    data.pop('source_file', None)
    again = GeometryObject('loaded', app)
    again.from_dict(data)
    assert again.source_file == ''
    assert again.solid_geometry == obj.solid_geometry


def test_svg_and_dxf_import_with_letters_that_are_not_ascii(tmp_path):
    # the import reads the parsed file again for its source text: a UTF-8 SVG on a cp1254 system and an ANSI
    # (cp1254) DXF made the import fail there, after a good parse
    app, _ = make_geometry()
    svg = tmp_path / 'title.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" viewBox="0 0 20 10">'
                   '<title>KARŞI ŞASE</title><rect x="2" y="2" width="10" height="5"/></svg>', encoding='utf-8')
    dxf = tmp_path / 'ansi.dxf'
    doc = ezdxf.new('R2000')
    doc.encoding = 'cp1254'
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True)
    doc.modelspace().add_text('KARŞI', dxfattribs={'height': 2})
    doc.saveas(dxf)
    assert b'KAR\xdeI' in dxf.read_bytes()
    created = {}

    def new_object(kind, name, initialize, **kwargs):
        created[name] = GeometryObject(name, app)
        return initialize(created[name], app)

    app.app_obj.new_object.side_effect = new_object
    assert app.f_handlers.import_svg(str(svg), outname='svg', plot=False) != 'fail'
    assert app.f_handlers.import_dxf(str(dxf), outname='dxf', plot=False, text_mode='none') != 'fail'
    assert created['svg'].solid_geometry and '<title>KARŞI ŞASE</title>' in created['svg'].source_file
    assert len(created['dxf'].solid_geometry) == 1 and 'ANSI_1254' in created['dxf'].source_file


@pytest.mark.parametrize('failure', ['new'])
def test_dxf_failure_preserves_good_source_and_does_not_export(tmp_path, monkeypatch, failure):
    app, obj = make_geometry()
    assert obj.regenerate_source()
    old_source = obj.source_file
    real_doc = ezdxf.new('R2010')
    if failure == 'new':
        monkeypatch.setattr('appObjects.GeometryObject.ezdxf.new', Mock(side_effect=ValueError('new failed')))
    elif failure == 'partial':
        real_add = real_doc.modelspace().add_lwpolyline
        calls = []

        def add(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:
                raise ValueError('entity failed after first path')
            return real_add(*args, **kwargs)

        monkeypatch.setattr(real_doc.modelspace(), 'add_lwpolyline', add)
        monkeypatch.setattr('appObjects.GeometryObject.ezdxf.new', lambda *a: real_doc)
        assert obj.export_dxf() is None
    else:
        monkeypatch.setattr(real_doc, 'write', Mock(side_effect=ValueError('text serialization failed')))
        monkeypatch.setattr('appObjects.GeometryObject.ezdxf.new', lambda *a: real_doc)
    assert obj.regenerate_source() is False
    assert obj.source_file == old_source
    if failure != 'write':
        filename = tmp_path / 'failed.dxf'
        app.collection.get_by_name.return_value = obj
        app.f_handlers.export_dxf('geo', str(filename), use_thread=False)
        assert not filename.exists()
        app.file_saved.emit.assert_not_called()


@pytest.mark.parametrize('failure', [False, 'raise'])
def test_actual_app_edit_finish_commits_then_refreshes_before_close(tmp_path, monkeypatch, failure):
    app, obj = make_geometry()
    assert obj.regenerate_source()
    old_source = obj.source_file
    line = LineString([(10, 0), (12, 0)])
    editor = app_save_editor(app, obj, [line], monkeypatch)
    if failure == 'return':
        monkeypatch.setattr(obj, 'export_dxf', lambda: None)
    elif failure == 'raise':
        monkeypatch.setattr(obj, 'regenerate_source', Mock(side_effect=ValueError('unexpected exporter failure')))
    seen = []
    editor.deactivate.side_effect = lambda: seen.append((list(obj.solid_geometry), obj.source_file))
    assert App.on_editing_finished(app) == 'pending'
    assert obj.solid_geometry == [line]
    assert not seen
    app.post_edit_sig.emit.assert_not_called()
    job = app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    assert obj.source_file == old_source
    QtWidgets.QApplication.processEvents()
    assert seen == [([line], obj.source_file)]
    editor.deactivate.assert_called_once()
    app.post_edit_sig.emit.assert_called_once()
    if failure:
        assert obj.source_file == old_source
        assert any('stale' in str(call).lower() for call in app.inform.emit.call_args_list)
    else:
        doc = ezdxf.read(StringIO(obj.source_file))
        assert [list(e.get_points('xy')) for e in doc.modelspace()] == [list(line.coords)]
    _, restored = save_and_load(app, [obj], tmp_path)
    assert len(restored) == 1
    assert restored[0].solid_geometry == [line]
    assert restored[0].source_file == obj.source_file


def test_export_of_two_objects_writes_one_file_with_both_layers(tmp_path):
    app, first = make_geometry()
    second = GeometryObject('other', app)
    first.dxf_layers = lambda: [{'name': 'first', 'geometry': Polygon([(0, 0), (1, 0), (1, 1)])}]
    second.dxf_layers = lambda: [{'name': 'second', 'geometry': LineString([(2, 2), (3, 3)])}]
    app.collection.get_by_name.side_effect = lambda name: {'geo': first, 'other': second}[name]
    filename = tmp_path / 'both.dxf'
    app.f_handlers.export_dxf(['geo', 'other'], str(filename), use_thread=False)
    layers = {layer.dxf.name for layer in ezdxf.readfile(filename).layers}
    assert {'first', 'second'} <= layers
    app.file_saved.emit.assert_called_once()


def source(kind, geometry, plot=True, units='MM', **extra):
    return SimpleNamespace(kind=kind, solid_geometry=geometry, obj_options={'plot': plot}, units=units, **extra)


@pytest.fixture
def setup():
    edited = source('geometry', [])
    gerber = source('gerber', [box(10, 10, 12, 12), box(20, 20, 22, 22)])
    excellon = source('excellon', [box(30, 30, 31, 31)])
    geometry = source('geometry', [LineString([(40, 0), (50, 0)])])
    hidden = source('gerber', [box(60, 60, 62, 62)], plot=False)
    cncjob = source('cncjob', [box(70, 70, 72, 72)])
    existing = DrawToolShape(LineString([(0, 0), (1, 0)]))

    editor = editor_with_history([existing])
    editor.fcgeometry = edited
    editor.canvas = SimpleNamespace(pixel_size=lambda: 0.01)
    editor.tool_shape = SimpleNamespace(clear=Mock(), redraw=Mock(), add=Mock())
    editor.app.collection = SimpleNamespace(get_list=lambda: [edited, gerber, excellon, geometry, hidden, cncjob])
    editor.app.app_units = 'MM'
    editor.app.inform = SimpleNamespace(emit=Mock())
    editor.select_tool = Mock()
    tool = FCImport(editor)
    editor.active_tool = tool
    return SimpleNamespace(editor=editor, tool=tool, existing=existing, edited=edited, gerber=gerber,
                           excellon=excellon, geometry=geometry, hidden=hidden, cncjob=cncjob)


def stored(editor):
    return list(editor.storage.get_objects())


def test_area_select_enclosing_and_touching(setup):
    setup.tool.area_select((0, 0), (25, 25), True)
    assert len(setup.tool.picked) == 2
    setup.tool.picked = []
    setup.tool.area_select((11, 11), (21, 21), False)
    assert len(setup.tool.picked) == 2
    setup.tool.picked = []
    setup.tool.area_select((11, 11), (21, 21), True)
    assert setup.tool.picked == []


def test_commit_adds_copies_source_unchanged_and_one_undo(setup):
    original = setup.gerber.solid_geometry[0]
    setup.tool.click_release((11, 11))
    setup.tool.click_release((45, 0))
    assert setup.tool.commit() is None
    shapes = stored(setup.editor)
    assert len(shapes) == 3
    added = [shp for shp in shapes if shp is not setup.existing]
    assert sorted(shp.geo.bounds for shp in added) == [(10, 10, 12, 12), (40, 0, 50, 0)]
    assert setup.gerber.solid_geometry[0] is original and len(setup.gerber.solid_geometry) == 2
    assert setup.editor.undo()
    assert stored(setup.editor) == [setup.existing]
    assert setup.editor.redo()
    assert len(stored(setup.editor)) == 3


def test_source_in_other_units_is_scaled():
    inch = source('gerber', [box(0, 0, 1, 1)], units='IN')
    assert import_source_geometries(inch, 'MM')[0].bounds == (0, 0, 25.4, 25.4)
    assert import_source_geometries(inch, 'IN')[0].bounds == (0, 0, 1, 1)


def image_tool_for_conversion():
    class ImageTool(ImageImportMixin):
        def __init__(self):
            self.app = SimpleNamespace(
                log=Mock(), options={}, app_units='MM', decimals=4,
                defaults=SimpleNamespace(get=lambda key, default=None: default, factory_defaults={}))

    return ImageTool()


def test_raster_conversion_keeps_holes_and_their_relative_position(tmp_path):
    filename = tmp_path / 'ring.png'
    image = Image.new('RGB', (20, 20), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 17, 17), fill='black')
    draw.rectangle((5, 5, 14, 14), fill='white')
    image.save(filename)

    geos = image_tool_for_conversion().image_geometry(
        str(filename), 'raster', 25.4, 'black', [250, 250, 250, 250], None, 0.0, 'MM')
    paths = image_paths(geos)

    assert len(paths) == 2
    assert all(path.geom_type == 'LinearRing' for path in paths)
    assert sorted(path.bounds for path in paths) == [(2.0, -18.0, 18.0, -2.0), (5.0, -15.0, 15.0, -5.0)]


def test_place_image_uses_lower_left_anchor_and_is_one_undo(tmp_path):
    existing = DrawToolShape(LineString([(0, 0), (1, 0)]))
    editor = editor_with_history([existing])
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)

    outer = Polygon([(2, -18), (18, -18), (18, -2), (2, -2)])
    hole = Polygon([(5, -15), (15, -15), (15, -5), (5, -5)])
    paths = image_paths([Polygon(outer.exterior.coords, [hole.exterior.coords])])

    tool = FCImportImage.__new__(FCImportImage)
    tool.name, tool.draw_app, tool.app = 'import_image', editor, editor.app
    tool.image_tool = SimpleNamespace(paths=paths, anchor=(2.0, -18.0))
    tool.points, tool.geometry, tool.complete = [], [], False
    editor.active_tool = tool

    assert tool.click((10, 20)) != 'fail'
    assert tool.complete
    assert tool.image_tool.paths == []
    AppGeoEditor.on_shape_complete(editor)

    added = [shape for shape in stored(editor) if shape is not existing]
    assert sorted(shape.geo.bounds for shape in added) == [(10.0, 20.0, 26.0, 36.0), (13.0, 23.0, 23.0, 33.0)]
    assert all(isinstance(shape.geo, LinearRing) for shape in added)

    # a second click without a new conversion cannot stamp the image again
    assert tool.click((40, 40)) == 'fail'
    assert len(stored(editor)) == 3

    assert editor.undo()
    assert stored(editor) == [existing]
    assert editor.redo()
    assert len(stored(editor)) == 3


def test_editor_geometry_keeps_paths_and_filters_polygon_leaves():
    tool = image_tool_for_conversion()
    ring = LinearRing([(0, 0), (2, 0), (2, 2), (0, 2)])
    small = box(0, 0, 0.1, 0.1)
    big = box(0, 0, 2, 2)
    collection = GeometryCollection([ring, small, big])
    tool.import_image_handler = lambda *args, **kwargs: [collection]

    geos = tool.image_geometry('ignored', 'raster', 96, 'black', None, None, 0.3, 'MM', keep_paths=True)
    assert any(geometry.geom_type == 'LinearRing' and geometry.bounds == (0, 0, 2, 2) for geometry in geos)
    assert any(geometry.bounds == big.bounds for geometry in geos)
    assert all(geometry.bounds != small.bounds for geometry in geos)

    # the main app keeps its existing area-only behavior: a standalone path is dropped
    tool.import_image_handler = lambda *args, **kwargs: [ring]
    assert tool.image_geometry('ignored', 'raster', 96, 'black', None, None, 0.3, 'MM') == []
    assert tool.image_geometry('ignored', 'raster', 96, 'black', None, None, 0.3, 'MM',
                               keep_paths=True) == [ring]


def test_large_image_moving_extent_keeps_every_path_for_placement(qt_application, tmp_path):
    image = Image.new('L', (66, 66), 'white')
    for y in range(0, 66, 2):
        for x in range(0, 66, 2):
            image.putpixel((x, y), 0)
    filename = tmp_path / 'many_islands.png'
    image.save(filename)
    app = MagicMock()
    app.decimals, app.app_units, app.resource_location = 4, 'MM', 'assets/resources'
    editor = SimpleNamespace(app=app, delete_utility_geometry=Mock(), snap_x=0, snap_y=0,
                             update_utility_geometry=Mock(), in_action=False)
    plugin = GeoImportImageTool(app, editor)
    placement = FCImportImage.__new__(FCImportImage)
    placement.draw_app, placement.app, placement.image_tool = editor, app, plugin
    placement.points = []
    editor.active_tool = placement
    plugin.filename = str(filename)
    plugin.ui.min_area_entry.set_value(0)
    plugin.ui.dpi_entry.set_value(25)
    plugin.on_image_import_action()
    task = app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    assert len(plugin.paths) == 1089
    assert len(plugin.preview_paths) == 1
    assert placement.utility_geometry((10, 20)).geo[0].bounds[:2] == (10, 20)
    assert placement.click((10, 20)) != 'fail'
    assert len(placement.geometry) == 1089
    assert all(shape.geo.geom_type == 'LinearRing' for shape in placement.geometry)
    plugin.cancel()


@pytest.fixture(autouse=True)
def no_modifier(monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.NoModifier))


def corner(mode, old, new):
    return corner_point(mode, old, new)


# ---------------------------------------------------------------------------------------------------------------------
# the shared function
# ---------------------------------------------------------------------------------------------------------------------
# (mode, new point) -> corner, starting from (0, 0); every quadrant (left/right, up/down), X-major and Y-major moves
SHARED = [
    # mode 1: 45 degrees, the diagonal part is last
    (1, (5, 2), (3, 0)), (1, (-5, 2), (-3, 0)), (1, (5, -2), (3, 0)), (1, (-5, -2), (-3, 0)),
    (1, (2, 5), (0, 3)), (1, (-2, 5), (0, 3)), (1, (2, -5), (0, -3)), (1, (-2, -5), (0, -3)),
    # mode 2: reverse 45 degrees, the diagonal part is first
    (2, (5, 2), (2, 2)), (2, (-5, 2), (-2, 2)), (2, (5, -2), (2, -2)), (2, (-5, -2), (-2, -2)),
    (2, (2, 5), (2, 2)), (2, (-2, 5), (-2, 2)), (2, (2, -5), (2, -2)), (2, (-2, -5), (-2, -2)),
    # mode 3: 90 degrees, horizontal first
    (3, (5, 2), (5, 0)), (3, (-5, 2), (-5, 0)), (3, (5, -2), (5, 0)), (3, (-2, -5), (-2, 0)),
    # mode 4: reverse 90 degrees, vertical first
    (4, (5, 2), (0, 2)), (4, (-5, 2), (0, 2)), (4, (5, -2), (0, -2)), (4, (-2, -5), (0, -5)),
    # mode 5: free angle, no corner
    (5, (5, 2), None), (5, (-2, -5), None),
    # a pure diagonal needs no corner in the 45 degrees modes
    (1, (3, 3), None), (2, (-3, 3), None),
]


@pytest.mark.parametrize('mode, new, expected', SHARED[::8])
def test_shared_corner_all_modes_and_directions(mode, new, expected):
    assert corner(mode, (0, 0), new) == expected


# ---------------------------------------------------------------------------------------------------------------------
# Geometry Editor Path
# ---------------------------------------------------------------------------------------------------------------------
def path_tool(mode=5, points=()):
    editor, tool, panel, original = input_tool(FCPath, points=points)
    editor.bend_mode = mode
    tool.points = list(points)
    tool.set_bend_cursor = Mock()
    return editor, tool


def click_all(tool, *clicks):
    for point in clicks:
        tool.click(point)


def test_path_mode_1_click_adds_corner_and_point():
    editor, tool = path_tool(1)
    click_all(tool, (0.0, 0.0), (5.0, 2.0))
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0)]
    click_all(tool, (5.0, 8.0))   # vertical move: no corner
    assert tool.points[-1] == (5.0, 8.0) and len(tool.points) == 4
    click_all(tool, (9.0, 10.0))  # 45 degrees mode again
    assert tool.points[-3:] == [(5.0, 8.0), (7.0, 8.0), (9.0, 10.0)]


def test_path_made_geometry_has_the_corner_points():
    editor, tool = path_tool(3)
    click_all(tool, (0.0, 0.0), (5.0, 2.0))
    assert tool.make() != 'fail'
    assert list(tool.geometry.geo.coords) == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0)]


# ---------------------------------------------------------------------------------------------------------------------
# Geometry Editor Polygon
# ---------------------------------------------------------------------------------------------------------------------
def polygon_tool(mode=5, points=()):
    editor, path, _panel, _original = input_tool(FCPath, points=points)
    editor.bend_mode = mode
    tool = FCPolygon.__new__(FCPolygon)
    tool.draw_app, tool.app, tool.name, tool.points = editor, editor.app, 'polygon', list(points)
    tool.complete, tool.geometry, tool.new_segment, tool.cursor_data_control = False, None, True, True
    tool.plugin_name = 'Polygon'
    tool.draw_cursor_data = Mock()
    tool.polygon_tool = SimpleNamespace(length=0.0)
    return editor, tool


def test_polygon_mode_3_preview_and_click():
    editor, tool = polygon_tool(3)
    click_all(tool, (0.0, 0.0))
    preview = tool.utility_geometry(data=(5.0, 2.0)).geo
    assert isinstance(preview, LineString)
    assert list(preview.coords) == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0)]
    click_all(tool, (5.0, 2.0))
    assert tool.points == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0)]
    ring = tool.utility_geometry(data=(1.0, 6.0)).geo
    assert isinstance(ring, LinearRing)
    assert list(ring.coords)[:-1] == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0), (1.0, 2.0), (1.0, 6.0)]


@pytest.mark.parametrize('kind', ['segments'])
def test_polygonize_closed_paths_and_separate_segments_with_one_undo(kind):
    polygon = box(0, 0, 2, 2)
    coords = list(polygon.exterior.coords)
    geometries = {
        'ring': [polygon.exterior],
        'closed_path': [LineString(coords)],
        'segments': [LineString([a, b]) for a, b in zip(coords, coords[1:])],
        'crossing': [polygon.exterior, LineString([(0, 1), (2, 1)])]
    }[kind]
    originals = [DrawToolShape(geo) for geo in geometries]
    editor = editor_with_history(originals, originals)
    AppGeoEditor.polygonize(editor)
    assert list(editor.storage.get_objects()) == originals
    run_worker(editor)
    results = list(editor.storage.get_objects())
    assert len(results) == (2 if kind == 'crossing' else 1)
    assert all(isinstance(shape.geo, Polygon) and shape.geo.is_valid for shape in results)
    assert unary_union([shape.geo for shape in results]).equals(polygon)
    assert set(editor.selected) == set(results)
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(originals)
    assert editor.selected == originals
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == results


def test_polygonize_preserves_unused_lines_existing_polygons_and_partial_record_metadata():
    outline = box(0, 0, 2, 2).exterior
    # This path contributes an edge and has a dangling tail that must survive.
    partial = DrawToolShape(LineString([(2, 2), (2, 0), (3, 0)]))
    partial.data['custom'] = {'label': 'tail'}
    rest = DrawToolShape(LineString([(2, 0), (0, 0), (0, 2), (2, 2)]))
    unused = DrawToolShape(LineString([(8, 0), (9, 0)]))
    existing = DrawToolShape(box(10, 0, 12, 2))
    unselected = DrawToolShape(box(20, 0, 22, 2).exterior)
    originals = [partial, rest, unused, existing, unselected]
    editor = editor_with_history(originals, originals[:-1])
    AppGeoEditor.polygonize(editor)
    run_worker(editor)
    shapes = list(editor.storage.get_objects())
    assert unused in shapes and existing in shapes and unselected in shapes
    assert partial not in shapes and rest not in shapes
    result, = [shape for shape in shapes if shape not in originals and isinstance(shape.geo, Polygon)]
    assert result.geo.equals(Polygon(outline))
    tail, = [shape for shape in shapes if shape not in originals and isinstance(shape.geo, LineString)]
    assert tail.geo.equals(LineString([(2, 0), (3, 0)]))
    assert tail.data['custom'] == partial.data['custom']
    assert tail.data['bounds'] == tail.geo.bounds
    assert editor.undo() and set(editor.storage.get_objects()) == set(originals)


def test_polygonize_nodes_multipart_linework_and_preserves_nested_faces():
    outer, inner = box(0, 0, 4, 4), box(1, 1, 2, 2)
    original = DrawToolShape(MultiLineString([outer.exterior.coords, inner.exterior.coords]))
    editor = editor_with_history([original], [original])
    AppGeoEditor.polygonize(editor)
    run_worker(editor)
    polygons = [shape.geo for shape in editor.storage.get_objects()]
    assert len(polygons) == 2
    assert any(poly.equals(outer.difference(inner)) for poly in polygons)
    assert any(poly.equals(inner) for poly in polygons)
    assert sum(poly.area for poly in polygons) == outer.area


@pytest.mark.parametrize('geometries', [[], [box(0, 0, 2, 2)]])
def test_polygonize_without_usable_enclosure_leaves_storage_and_history_unchanged(geometries):
    originals = [DrawToolShape(geo) for geo in geometries]
    editor = editor_with_history(originals, originals)
    AppGeoEditor.polygonize(editor)
    run_worker(editor)
    assert list(editor.storage.get_objects()) == originals
    assert editor.selected == originals
    assert not editor.undo()
    assert not any('[success]' in str(call) for call in editor.app.inform.emit.call_args_list)


@pytest.mark.parametrize('operation', ['union', 'subtract'])
def test_union_and_subtract_of_a_board_of_polygons_match_geos_without_clipper(operation):
    from unittest import mock
    from appCommon import clipper_ops
    tiles = [box(x * 1.5, y * 1.5, x * 1.5 + 2, y * 1.5 + 2) for x in range(10) for y in range(10)]
    results = []
    for clipper in (True, False):
        originals = [DrawToolShape(tile) for tile in tiles]
        editor = editor_with_history(originals, originals)
        with mock.patch.object(clipper_ops, 'pyclipr', clipper_ops.pyclipr if clipper else None):
            getattr(AppGeoEditor, operation)(editor)
            run_worker(editor)
        results.append(unary_union([shape.geo for shape in editor.storage.get_objects()]))
    expected = unary_union(tiles) if operation == 'union' else tiles[0].difference(unary_union(tiles[1:]))
    for result in results:
        assert result.symmetric_difference(expected).area < 1e-6


def snapping_editor(geometry=(), **options):
    editor = editor_with_history([DrawToolShape(geo) for geo in geometry])
    editor.app.call_source = 'geo_editor'
    editor.app.grid_status = lambda: True
    editor.app.ui = SimpleNamespace(grid_gap_link_cb=SimpleNamespace(isChecked=lambda: False))
    editor.ui = SimpleNamespace(snap_type_label=Mock())
    editor.editor_options = dict(grid_snap=False, corner_snap=False, midpoint_snap=False,
                                 segment_snap=False, snap_radius_px=8.0, global_snap_max=0.05,
                                 global_gridx=1.0, global_gridy=1.0)
    editor.editor_options.update(options)
    editor.snap = lambda x, y: AppGeoEditor.snap(editor, x, y)
    return editor


def canvas(scale):
    return SimpleNamespace(translate_coords_2=lambda pos: (pos[0] * scale, pos[1] * scale),
                           translate_coords=lambda pos: (pos[0] / scale, pos[1] / scale))


def test_master_gate_does_not_change_legacy_direct_corner_or_grid_snapping():
    editor = snapping_editor([LineString([(0, 0), (10, 0)])], corner_snap=True,
                             midpoint_snap=True, segment_snap=True)
    editor.app.grid_status = lambda: False
    assert editor.snap(0.02, 0.01) == (0, 0)
    assert editor.active_snap_type == 'corner'
    editor.editor_options['grid_snap'] = True
    assert editor.snap(1.1, 1.2) == (1, 1)
    assert editor.active_snap_type == 'grid'
    assert editor._snap_cache is None


def test_projection_is_clamped_to_segment_not_extended_line():
    editor = snapping_editor([LineString([(0, 0), (10, 0)])], segment_snap=True)
    assert editor.snap(3, 0.02) == pytest.approx((3, 0))
    assert editor.active_snap_type == 'segment'
    assert editor.snap(-0.02, 0) == (0, 0)
    assert editor.snap(-1, 0) == (-1, 0)


def test_competition_nearest_wins_and_type_ties_have_stable_priority():
    editor = snapping_editor([LineString([(0, 0), (10, 0)])], midpoint_snap=True,
                             segment_snap=True, grid_snap=True)
    assert editor.snap(5, 0.02) == (5, 0)
    assert editor.active_snap_type == 'midpoint'
    assert editor.snap(4.99, 0.02) == pytest.approx((4.99, 0))
    assert editor.active_snap_type == 'segment'
    editor.editor_options['corner_snap'] = True
    assert editor.snap(0, 0.02) == (0, 0)
    assert editor.active_snap_type == 'corner'


@pytest.mark.parametrize('linked', [False])
def test_empty_storage_zero_grid_and_existing_corner_behavior(linked):
    editor = snapping_editor(grid_snap=True, corner_snap=True, midpoint_snap=True,
                             segment_snap=True, global_gridx=0, global_gridy=0)
    editor.app.ui.grid_gap_link_cb.isChecked = lambda: linked
    assert editor.snap(0.12, 0.34) == (0.12, 0.34)
    editor.add_shape(DrawToolShape(LineString([(0, 0), (10, 0)])))
    editor.editor_options.update(grid_snap=False)
    assert editor.snap(0.02, 0.01) == (0.02, 0)
    editor.editor_options.update(segment_snap=False)
    assert editor.snap(0.02, 0.01) == (0, 0)
    editor.canvas = canvas(10000)
    assert editor.snap(0.02, 0.01) == (0, 0)  # corner distance is still model units


def test_grid_link_and_grid_corner_competition_regressions():
    editor = snapping_editor([LineString([(0, 0), (10, 0)])], grid_snap=True,
                             corner_snap=True, global_gridx=0.1, global_gridy=0.2)
    assert editor.snap(0.14, 0.14) == pytest.approx((0.1, 0.2))
    editor.app.ui.grid_gap_link_cb.isChecked = lambda: True
    assert editor.snap(0.14, 0.14) == pytest.approx((0.1, 0.1))
    assert editor.snap(0.01, 0.01) == (0, 0)


def batch_editor(shapes, selected=()):
    editor = selection_editor(shapes, selected=selected)
    editor.app.proc_container = SimpleNamespace(new=lambda *args: nullcontext())
    editor.app.inform = Mock()
    editor.app.inform_shell = Mock()
    editor.app.worker_task = SimpleNamespace(emit=lambda job: job['fcn'](*job['params']))
    editor.build_ui_sig = Mock()
    editor._collect_shape = lambda shape, dest: AppGeoEditor._collect_shape(editor, shape, dest)
    editor.add_shape = lambda shape, **kwargs: AppGeoEditor.add_shape(editor, shape, **kwargs)
    editor.transform_shapes = lambda *args, **kwargs: AppGeoEditor.transform_shapes(editor, *args, **kwargs)
    editor.storage.replace = Mock(wraps=editor.storage.replace)
    editor.flatten = lambda *args, **kwargs: AppGeoEditor.flatten(editor, *args, **kwargs)
    return editor


def test_copy_2d_array_uses_relative_offsets():
    tool = copy_tool([line(0)], mode='a')
    ui = tool.copy_tool.ui
    ui.array_type_radio = radio('2D')
    ui.placement_radio = radio('o')
    ui.rows, ui.columns = radio(2), radio(2)
    ui.spacing_rows, ui.spacing_columns = radio(0), radio(0)
    ui.offsetx_entry, ui.offsety_entry = radio(10), radio(20)
    tool.sel_limit = 0
    tool.click((5, 5))
    tool.click((8, 9))

    assert [shape.geo.bounds[:2] for shape in tool.geometry] == [(3, 4), (13, 4), (3, 24), (13, 24)]


def test_batch_translation_preserves_mixed_dimensions_and_none():
    line_2d = LineString([(0, 0), (1, 1)])
    line_3d = LineString([(0, 0, 7), (1, 1, 8)])

    result = translate_geometry([line_2d, None, line_3d], 2, 3)

    assert list(result[0].coords) == [(2, 3), (3, 4)]
    assert result[1] is None
    assert list(result[2].coords) == [(2, 3, 7), (3, 4, 8)]
    assert list(translate_geometry(line_3d, 2, 3).coords) == [(2, 3, 7), (3, 4, 8)]


def test_buffer_follows_the_adaptive_circle_preference():
    counts = {}
    for mode, distance in (('adaptive', 0.05), ('adaptive', 5.0), ('fixed', 0.05), ('fixed', 5.0)):
        shape = DrawToolShape(LineString([(0, 0), (1, 0)]))
        editor = batch_editor([shape], selected=[shape])
        editor.app.options.update({'geometry_circle_steps_mode': mode, 'geometry_circle_steps': 32})
        BufferSelectionTool.buffer(SimpleNamespace(app=editor.app, draw_app=editor), distance, 'round')
        counts[(mode, distance)] = max(len(s.geo.coords) for s in editor.storage.get_objects()
                                       if s.geo.geom_type in ('LinearRing', 'LineString'))

    assert counts[('adaptive', 0.05)] < counts[('adaptive', 5.0)]
    assert counts[('fixed', 0.05)] == counts[('fixed', 5.0)]


def test_paint_refuses_a_tool_diameter_of_zero_or_less():
    # a negative diameter made the Standard method grow its passes outwards forever; zero failed or painted the outline
    for dia, accepted in ((0.0, False), (-0.5, False), (0.5, True)):
        tool = SimpleNamespace(app=SimpleNamespace(inform=Mock()), paint=Mock(return_value=None),
                               fcdraw=SimpleNamespace(selected=[line(0)], select_tool=Mock()))
        tool.ui = SimpleNamespace(painttooldia_entry=radio(dia), paintoverlap_entry=radio(15.0),
                                  paintmargin_entry=radio(0.0), paintmethod_combo=radio('Standard'),
                                  paintcontour_cb=radio(True), pathconnect_cb=radio(False))

        PaintOptionsTool.on_paint(tool)

        assert tool.paint.called is accepted
        if not accepted:
            assert 'greater than zero' in tool.app.inform.emit.call_args.args[0]


def test_paint_lines_method_paints_in_the_editor():
    # the editor (not a camlib Geometry) is the self of clear_polygon_lines(): a class constant read through self
    # failed there, nothing was painted and the Paint still said "Done."
    square = DrawToolShape(box(0, 0, 10, 10).exterior)
    editor = batch_editor([square], selected=[square])
    editor.app.abort_flag = False
    editor.app.options.update({'geometry_circle_steps_mode': 'fixed', 'geometry_circle_steps': 32})

    PaintOptionsTool.paint(SimpleNamespace(fcdraw=editor), 1.0, 0.1, 0.0, connect=False, contour=False,
                           method='Lines')

    painted = [shape.geo for shape in editor.storage.get_objects() if shape is not square]
    # the scan lines, 0.5 mm (the tool radius) from the edges and 0.9 mm apart
    assert len(painted) == 11
    assert all(box(0.4, 0.4, 9.6, 9.6).contains(line_geo) for line_geo in painted)
    editor.app.log.error.assert_not_called()


def split_editor(geometry=None):
    editor, original = vertex_editor(geometry if geometry is not None else
                                     LineString([(0, 0), (2, 0), (4, 2)]))
    editor.multigeo_tool = 7
    original.data['tool'] = 7
    editor.apply_path_split = lambda *a, **k: geo.AppGeoEditor.apply_path_split(editor, *a, **k)
    return editor, original


@pytest.mark.parametrize('point', [(1, 0.05)])
@pytest.mark.parametrize('reverse', [False])
def test_split_conserves_length_direction_metadata_and_replays(point, reverse):
    coords = [(0, 0), (2, 0), (4, 2)]
    editor, original = split_editor(LineString(coords[::-1] if reverse else coords))
    editor.storage.replace = Mock(wraps=editor.storage.replace)
    editor._snap_cache = object()
    assert editor.apply_path_split(point, 0.1) != 'fail'
    a, b = editor.selected
    assert a.geo.length > 0 and b.geo.length > 0
    assert a.geo.length + b.geo.length == pytest.approx(original.geo.length)
    assert a.geo.coords[0] == original.geo.coords[0]
    assert b.geo.coords[-1] == original.geo.coords[-1]
    assert a.geo.coords[-1] == b.geo.coords[0]
    assert original.geo.distance(geo.Point(a.geo.coords[-1])) < 1e-12
    for shape in (a, b):
        assert shape.data['custom'] == original.data['custom']
        assert shape.data['tool'] == 7
        assert shape.data['bounds'] == shape.geo.bounds
        assert shape.data is not original.data
    assert editor.multigeo_tool == 7
    assert editor._snap_cache is None
    assert set(editor.storage.indexes) == {id(a), id(b)}
    assert editor.storage.nearest(a.geo.coords[0])[1] is a
    assert editor.sel_shapes.items == [a.geo, b.geo]
    editor.storage.replace.assert_called_once()
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.selected == [original]
    assert list(editor.storage.get_objects()) == [original]
    assert editor.sel_shapes.items == [original.geo]
    assert not editor.undo()
    assert editor.redo()
    assert editor.selected == [a, b]
    assert editor.sel_shapes.items == [a.geo, b.geo]


@pytest.mark.parametrize('point,tolerance', [
    ((2, 0.101), 0.1),
])
def test_rejection_preserves_original_selection_render_cache_and_redo(point, tolerance):
    editor, original = split_editor(LineString([(0, 0), (4, 0)]))
    assert editor.apply_path_split((2, 0), 0.1) != 'fail'
    assert editor.undo()
    redo = list(editor._history_redo)
    cache = editor._snap_cache = object()
    editor.build_ui_sig.emit.reset_mock()
    assert editor.apply_path_split(point, tolerance) == 'fail'
    assert list(editor.storage.get_objects()) == [original]
    assert editor.selected == [original]
    assert editor.sel_shapes.items == [original.geo]
    assert editor._snap_cache is cache
    assert editor._history_redo == redo
    editor.build_ui_sig.emit.assert_not_called()
    assert editor.app.inform.emit.called
    assert editor.redo()


@pytest.mark.parametrize('geometry', [
    Polygon([(0, 0), (4, 0), (0, 4)]),
])
def test_unsupported_shapes_explain_open_linestring_scope(geometry):
    editor, original = split_editor(geometry)
    assert editor.apply_path_split((2, 0), 0.1) == 'fail'
    assert 'open' in str(editor.app.inform.emit.call_args).lower()
    assert 'LineString' in str(editor.app.inform.emit.call_args)
    assert editor.selected == [original]
    assert not editor.undo()


def test_zero_tolerance_accepts_exact_interior_and_rejects_offpath():
    editor, original = split_editor()
    assert editor.apply_path_split((1, 0.001), 0) == 'fail'
    assert editor.apply_path_split((1, 0), 0) != 'fail'


def interactive_editor():
    editor, original = split_editor(LineString([(0, 0), (4, 0)]))
    editor.decimals = 4
    editor.app.decimals = 4
    editor.app.app_units = 'mm'
    editor.app.resource_location = ''
    editor.app.on_editing_finished = Mock()
    editor.ui = geo.AppGeoEditorUI(editor.app)
    editor.select_tool = Mock()
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: pos)
    editor.app.grid_status = lambda: False
    editor.app.ui = SimpleNamespace(rel_position_label=Mock(), update_location_labels=Mock())
    editor.app.plotcanvas = SimpleNamespace(on_update_text_hud=Mock())
    editor.snap = Mock(return_value=(2, 0.05))
    editor.active_tool = geo.FCSplit(editor)
    return editor, original


def join_editor(paths):
    editor, first = interactive_editor()
    editor.storage.remove(first)
    shapes = [geo.DrawToolShape(LineString(coords)) for coords in paths]
    for shape in shapes:
        shape.data['custom'] = {'label': 'track'}
        shape.data['tool'] = 7
        editor.storage.insert(shape)
    editor.selected = list(shapes)
    editor.multigeo_tool = 7
    editor.plot_changes()
    editor.apply_path_join = lambda tolerance: geo.AppGeoEditor.apply_path_join(editor, tolerance)
    return editor, shapes


@pytest.mark.parametrize('reverse', [True])
@pytest.mark.parametrize('gap', [0.05])
def test_exact_and_near_preserve_vertices_length_metadata_and_replay(reverse, gap):
    a, b = [(0, 0), (1, 1), (2, 0)], [(2 + gap, 0), (3, 1), (4, 0)]
    editor, originals = join_editor([a, b[::-1] if reverse else b])
    editor._snap_cache = object()
    assert editor.apply_path_join(0.125) != 'fail'
    result, = editor.selected
    assert result.geo.is_valid and result.geo.is_simple
    assert set(a + b) <= set(result.geo.coords)
    assert result.geo.length == pytest.approx(sum(s.geo.length for s in originals) + gap)
    assert result.data['custom'] == originals[0].data['custom']
    assert result.data['tool'] == editor.multigeo_tool == 7
    assert result.data['bounds'] == result.geo.bounds
    assert editor._snap_cache is None
    assert set(editor.storage.indexes) == {id(result)}
    assert editor.storage.nearest((0, 0))[1] is result
    assert editor.sel_shapes.items == [result.geo]
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.selected == originals
    assert editor.sel_shapes.items == [s.geo for s in originals]
    assert editor.redo()
    assert editor.selected == [result]
    assert editor.sel_shapes.items == [result.geo]


@pytest.mark.parametrize('paths,tolerance', [
    ([[(0, 0), (1, 0)], [(1.101, 0), (2, 0)]], 0.1),
    ([[(0, 0), (1, 0)], [(1, 0), (2, 0)], [(1, 0), (1, 1)]], 0),
])
def test_reject_gap_branching_unsupported_and_crossing_without_mutation(paths, tolerance):
    editor, originals = join_editor(paths)
    cache = editor._snap_cache = object()
    assert editor.apply_path_join(tolerance) == 'fail'
    assert editor.selected == originals
    assert set(editor.storage.get_objects()) == set(originals)
    assert editor._snap_cache is cache
    assert not editor.undo()
    assert editor.app.inform.emit.called


def test_closed_output_and_disjoint_component_stay_distinguishable():
    editor, originals = join_editor([
        [(0, 0), (2, 0), (2, 2)], [(2, 2), (0, 2), (0, 0)],
        [(10, 0), (11, 0)], [(11, 0), (12, 0)], [(20, 0), (21, 0)]])
    assert editor.apply_path_join(0) != 'fail'
    assert len(editor.selected) == 3
    assert sum(s.geo.is_closed for s in editor.selected) == 1
    assert originals[-1] in editor.selected
    assert all(s.geo.is_valid for s in editor.selected)
    assert editor.undo()
    assert editor.selected == originals


@pytest.mark.parametrize('key,value', [('tool', 8)])
def test_incompatible_metadata_or_owner_is_not_silently_lost(key, value):
    editor, originals = join_editor([[(0, 0), (1, 0)], [(1, 0), (2, 0)]])
    originals[1].data[key] = value
    assert editor.apply_path_join(0) == 'fail'
    assert editor.selected == originals
    assert not editor.undo()


def circle_polygon(center=(3.0, 4.0), radius=2.0, quad_segs=16):
    return Point(center).buffer(radius, quad_segs)


def circle_outline(center=(3.0, 4.0), radius=2.0, quad_segs=16):
    return LinearRing(circle_polygon(center, radius, quad_segs).exterior.coords)


def circle_editor(geometries, selected=None):
    editor, shapes = join_editor([[(0, 0), (1, 0)]])
    for shape in shapes:
        editor.storage.remove(shape)
    shapes = []
    for geometry in geometries:
        shape = geo.DrawToolShape(geometry)
        editor.storage.insert(shape)
        shapes.append(shape)
    editor.selected = list(shapes) if selected is None else [shapes[i] for i in selected]
    editor.update_all_vertex_count = lambda: None
    editor.plot_changes = Mock()
    return editor, shapes


def last_message(editor):
    return editor.app.inform.emit.call_args.args[0]


@pytest.mark.parametrize('make', [circle_polygon])
def test_resize_keeps_center_type_and_vertex_count(make):
    original = make()
    result = geo.resized_circle(original, 10.0)
    assert type(result) is type(original)
    assert len(result.exterior.coords if isinstance(result, Polygon) else result.coords) == \
        len(original.exterior.coords if isinstance(original, Polygon) else original.coords)
    (cx, cy), radius = geo.circle_of(result)
    assert (cx, cy) == pytest.approx((3.0, 4.0))
    assert radius == pytest.approx(5.0)


def test_mixed_selection_skips_non_circles_and_keeps_selection():
    editor, shapes = circle_editor([circle_polygon(), box(0, 0, 1, 1), circle_outline((9, 9), 1.0)])
    geo.AppGeoEditor.apply_resize_circles(editor, 6.0)
    assert last_message(editor) == '2 circles resized, 1 shapes skipped'
    stored = set(editor.storage.get_objects())
    assert shapes[1] in stored and shapes[0] not in stored and shapes[2] not in stored
    assert editor.selected[1] is shapes[1]
    assert {id(s) for s in editor.selected} == {id(s) for s in stored}
    assert geo.circle_of(editor.selected[0].geo)[1] == pytest.approx(3.0)
    assert geo.circle_of(editor.selected[2].geo) == (pytest.approx((9, 9)), pytest.approx(3.0))
    editor.plot_changes.assert_called()
    assert len(editor._history_undo) == 1


def test_resize_is_one_undo_step():
    editor, shapes = circle_editor([circle_polygon(), circle_polygon((10, 10), 1.0)])
    before = [s.geo.wkt for s in shapes]
    geo.AppGeoEditor.apply_resize_circles(editor, 8.0)
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(shapes)
    assert [s.geo.wkt for s in shapes] == before
    assert editor.redo()
    assert set(editor.storage.get_objects()).isdisjoint(shapes)


@pytest.mark.parametrize('diameter', [float('nan')])
def test_invalid_diameter_warns_and_changes_nothing(diameter):
    editor, shapes = circle_editor([circle_polygon()])
    assert geo.AppGeoEditor.apply_resize_circles(editor, diameter) == 'fail'
    assert 'WARNING' in last_message(editor)
    assert set(editor.storage.get_objects()) == set(shapes)
    assert not editor._history_undo


def test_overlap_cycles_and_preserves_unrelated_multiselection(monkeypatch):
    big, small, other = [DrawToolShape(geo) for geo in (box(0, 0, 10, 10), box(4, 4, 6, 6), box(20, 0, 21, 1))]
    editor = pick_editor([big, small, other])
    editor.selected = [other]
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        lambda: QtCore.Qt.KeyboardModifier.ControlModifier)
    editor.click_select((5, 5))
    assert editor.selected == [other, small]
    editor.click_select((5.01, 5))
    assert editor.selected == [other, big]
    editor.click_select((5, 5))
    assert editor.selected == [other, small]
    assert '2' in str(editor.app.inform.emit.call_args_list)
    # Moving farther than five pixels resets even when the same polygons overlap.
    editor.click_select((5.2, 5))
    assert editor.selected == [other]


def canvas_editor():
    editor, _, _ = interactive_move_editor()
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: (pos[0] / 100, pos[1] / 100))
    editor.snap = Mock(return_value=(0, 0))
    editor.app.grid_status = lambda: True
    editor.app.delete_selection_shape = Mock()
    editor.draw_selection_area_handler = Mock()
    editor.app.selection_type = None
    tool = FCSelect.__new__(FCSelect)
    tool.draw_app, tool.name = editor, 'select'
    tool.click_release = Mock()
    editor.active_tool = tool
    return editor


def test_selection_marquee_uses_only_x_and_y_of_the_vispy_coords():
    # VisPy translate_coords() gives (x, y, z, w); the marquee and the area pick need (x, y)
    editor = canvas_editor()
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: np.array([pos[0] / 100, pos[1] / 100, 0.0, 1.0]))
    editor.app.app_cursor = Mock()
    editor.app.plotcanvas = SimpleNamespace(cursor_color='black', on_update_text_hud=Mock())
    editor.app.ui.update_location_labels = Mock()
    editor.app.draw_moving_selection_shape = Mock()
    editor.update_utility_geometry = Mock()
    AppGeoEditor.on_canvas_click(editor, SimpleNamespace(pos=(23, 37), button=1))
    AppGeoEditor.on_canvas_move(editor, SimpleNamespace(pos=(3, 57), button=1, is_dragging=True))
    start, end = editor.app.draw_moving_selection_shape.call_args.args[:2]
    assert len(start) == 2 and len(end) == 2
    AppGeoEditor.on_canvas_click_release(editor, SimpleNamespace(pos=(3, 57), button=1))
    start, end, __ = editor.draw_selection_area_handler.call_args.args
    assert len(start) == 2 and len(end) == 2


@pytest.mark.parametrize('method,kind', [('buffer_int', 'polygon'), ('buffer_int', 'open')])
def test_buffer_adds_all_usable_contours_and_keeps_original(method, kind):
    from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
    from camlib import flatten_shapely_geometry
    from shapely import MultiLineString
    dumbbell = box(0, 0, 4, 4).union(box(6, 0, 10, 4)).union(box(3, 1.8, 7, 2.2))
    geo = {'polygon': dumbbell, 'ring': dumbbell.exterior,
           'multipart': MultiLineString([dumbbell.exterior]),
           'split_multipart': dumbbell.buffer(-0.3, join_style='mitre'),
           'open': LineString([(0, 0), (4, 0)])}[kind]
    shape = DrawToolShape(geo)
    editor = editor_with_history([shape], [shape])
    editor.app.app_units = 'MM'
    tool = BufferSelectionTool.__new__(BufferSelectionTool)
    tool.draw_app = editor
    getattr(tool, method)(0.5, 'mitre')
    run_worker(editor)
    added = [part for s in editor.storage.get_objects() if s is not shape
             for part in flatten_shapely_geometry(s.geo)]
    assert shape in list(editor.storage.get_objects())
    if kind == 'open' and method == 'buffer_int':
        assert not added
        assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
        assert not editor.undo()
    else:
        assert added and all(not part.is_empty for part in added)
        if method == 'buffer_int':
            assert len(added) == 2
            assert all(dumbbell.covers(part) for part in added)
        assert editor.undo()
        assert list(editor.storage.get_objects()) == [shape]


@pytest.mark.parametrize('method,distance', [('buffer_int', 10.0)])
def test_buffer_without_usable_result_never_reports_done_or_adds_history(method, distance):
    from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
    shape = DrawToolShape(box(0, 0, 2, 2))
    editor = editor_with_history([shape], [shape])
    editor.app.app_units = 'MM'
    tool = BufferSelectionTool.__new__(BufferSelectionTool)
    tool.draw_app = editor
    getattr(tool, method)(distance, 'mitre')
    assert run_worker(editor) == 'fail'
    assert list(editor.storage.get_objects()) == [shape]
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    assert not editor.undo()


def size_editor(selected_all=True):
    """Editor with: lines of length 1, 3, 10; boxes of area 1, 4, 100; a point."""
    editor, shapes = join_editor([[(0, 0), (1, 0)], [(0, 0), (3, 0)], [(0, 0), (10, 0)]])
    for side in (1, 2, 10):
        shape = geo.DrawToolShape(box(0, 0, side, side))
        editor.storage.insert(shape)
        shapes.append(shape)
    point = geo.DrawToolShape(Point(5, 5))
    editor.storage.insert(point)
    shapes.append(point)
    editor.selected = list(shapes) if selected_all else []
    editor.sync_tree_selection = lambda: geo.AppGeoEditor.sync_tree_selection(editor)
    editor.update_all_vertex_count = lambda: geo.AppGeoEditor.update_all_vertex_count(editor)
    return editor, shapes


def configure(editor, area=None, length=None):
    ui = editor.ui
    ui.size_area_chk.setChecked(area is not None)
    ui.size_length_chk.setChecked(length is not None)
    if area is not None:
        ui.size_min_area_entry.setText(str(area[0]))
        ui.size_max_area_entry.setText(str(area[1]))
    if length is not None:
        ui.size_min_length_entry.setText(str(length[0]))
        ui.size_max_length_entry.setText(str(length[1]))


def test_area_filter():
    editor, shapes = size_editor()
    configure(editor, area=(2, 10))
    assert geo.AppGeoEditor.size_filter_matches(editor) == [shapes[4]]


def test_scope_is_selection_when_present_else_all():
    editor, shapes = size_editor()
    configure(editor, length=(0, 100))
    editor.selected = [shapes[0], shapes[3]]
    assert geo.AppGeoEditor.size_filter_matches(editor) == [shapes[0]]
    editor.selected = []
    assert len(geo.AppGeoEditor.size_filter_matches(editor)) == 3


def test_delete_is_one_undo_step():
    editor, shapes = size_editor(selected_all=False)
    configure(editor, area=(0, 5), length=(0, 5))
    assert len(geo.AppGeoEditor.on_delete_by_size(editor)) == 4
    remaining = set(editor.storage.get_objects())
    assert remaining == {shapes[2], shapes[5], shapes[6]}
    assert len(editor._history_undo) == 1
    assert last_message(editor) == '4 shapes deleted'
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(shapes)
    assert editor.redo()
    assert set(editor.storage.get_objects()) == remaining


@pytest.mark.parametrize('low,high', [('abc', '5')])
@pytest.mark.parametrize('action', ['on_select_by_size'])
def test_invalid_input_warns_and_changes_nothing(action, low, high):
    editor, shapes = size_editor()
    editor.selected = shapes[:2]
    editor.ui.size_area_chk.setChecked(True)
    editor.ui.size_min_area_entry.setText(low)
    editor.ui.size_max_area_entry.setText(high)
    assert getattr(geo.AppGeoEditor, action)(editor) == 'fail'
    assert 'WARNING' in last_message(editor)
    assert editor.selected == shapes[:2]
    assert set(editor.storage.get_objects()) == set(shapes)
    assert not editor._history_undo


NO_MOD = QtCore.Qt.KeyboardModifier.NoModifier


CTRL = QtCore.Qt.KeyboardModifier.ControlModifier


PIXEL = 0.01  # canvas units for each screen pixel: the hit radius is 0.06


class FakeCollection_vertex_grips:
    def __init__(self):
        self.items = []
        self.enabled = True

    def add(self, shape=None, **kwargs):
        self.items.append((shape, kwargs))
        return len(self.items)

    def clear(self, update=False):
        self.items = []

    def redraw(self):
        pass


def make_grips(geometry, grid=False, snap=None, busy=False):
    shape = SimpleNamespace(geo=geometry)
    app = SimpleNamespace(app_units='MM', grid_status=lambda: grid, inform=SimpleNamespace(emit=Mock()),
                          options={'global_sel_line': '#0000ffbf'},
                          plotcanvas=SimpleNamespace(new_shape_collection=lambda layers: FakeCollection_vertex_grips()))
    editor = SimpleNamespace(selected=[shape], active_tool=SimpleNamespace(name='select'), app=app,
                             apply_vertex_edit=Mock(return_value=None), reject_busy=lambda: busy,
                             snap=snap or (lambda x, y: (x, y)), active_snap_type=None,
                             canvas=SimpleNamespace(pixel_size=lambda: PIXEL), tool_shape=FakeCollection_vertex_grips(),
                             get_draw_color=lambda: '#00000080', get_sel_color=lambda: '#0000FF80')
    grips = VertexGrips(editor)
    grips.sync()
    return grips, editor, shape


def drag(grips, start, end):
    """Press at the canvas point start, move 20 px, release at the canvas point end."""
    assert grips.press((0, 0), start, NO_MOD)
    grips.move((20, 0), end)
    return grips.release((20, 0), end)


def test_sync_shows_grips_for_one_selected_shape_in_select_tool_only():
    grips, editor, shape = make_grips(LineString([(0, 0), (1, 0), (2, 0)]))
    assert grips.shape is shape
    assert len(grips.collection().items) == 5  # 3 vertices + 2 midpoints
    editor.active_tool = SimpleNamespace(name='move')
    grips.sync()
    assert grips.shape is None and grips.collection().items == []
    editor.active_tool = SimpleNamespace(name='select')
    editor.selected = [shape, SimpleNamespace(geo=LineString([(5, 5), (6, 6)]))]
    grips.sync()
    assert grips.shape is None


def test_drag_of_a_midpoint_inserts_a_vertex():
    grips, editor, shape = make_grips(LineString([(0, 0), (2, 0)]))
    assert drag(grips, (1, 0), (1, 1))
    editor.apply_vertex_edit.assert_called_once_with('insert', (0, 0, 0), (1.0, 1.0), target=shape,
                                                     on_segment=False)


def test_ctrl_click_removes_a_vertex():
    grips, editor, shape = make_grips(LineString([(0, 0), (1, 0), (2, 0)]))
    assert grips.press((0, 0), (1, 0), CTRL)
    assert grips.release((0, 0), (1, 0))
    editor.apply_vertex_edit.assert_called_once_with('remove', (0, 0, 1), target=shape)


def test_cancel_drops_the_drag():
    grips, editor, shape = make_grips(LineString([(0, 0), (2, 0)]))
    assert grips.press((0, 0), (2, 0), NO_MOD)
    grips.move((20, 0), (2, 1))
    assert editor.tool_shape.items
    grips.cancel()
    assert editor.tool_shape.items == []
    assert not grips.release((20, 0), (2, 1))
    editor.apply_vertex_edit.assert_not_called()


ROUNDED = box(1, 1, 9, 5).buffer(1, join_style=BufferJoinStyle.round)


def grip_of(grips, kind):
    return next(grip for grip in grips.grips if grip[0] == kind)


def test_drag_of_an_arc_middle_bends_the_arc():
    grips, editor, shape = make_grips(ROUNDED)
    kind, address, middle = grip_of(grips, 'arc_mid')
    cursor = (middle[0] - 0.2, middle[1] - 0.2)
    assert drag(grips, middle, cursor)
    editor.apply_vertex_edit.assert_called_once_with('arc_mid', address, cursor, target=shape, on_segment=False)


def test_full_circle_radius_drag():
    grips, editor, shape = make_grips(Point(0, 0).buffer(2))
    assert [grip[0] for grip in grips.grips] == ['radius'] * 4
    kind, address, point = grips.grips[0]
    assert drag(grips, point, (3, 0))
    editor.apply_vertex_edit.assert_called_once_with('arc_radius', address, (3.0, 0.0), target=shape,
                                                     on_segment=False)


def test_address_edits_a_hole_with_one_undo_step():
    hole = [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]
    editor, original = vertex_editor(Polygon([(0, 0), (6, 0), (6, 6), (0, 6)], [hole]))
    assert editor.apply_vertex_edit('move', (0, 1, 0), (1.5, 0.5)) != 'fail'
    assert list(editor.selected[0].geo.interiors[0].coords)[0] == (1.5, 0.5)
    assert editor.undo()
    assert editor.selected == [original]


PIXEL_geo_editor_vertex_drag = 0.01


def drag_editor(geometry):
    """A history editor with real apply_vertex_edit, real VertexGrips and a VisPy-like 4-value translate_coords."""
    editor, original = vertex_editor(geometry)
    editor.active_tool = SimpleNamespace(name='select')
    editor.canvas = SimpleNamespace(pixel_size=lambda: PIXEL_geo_editor_vertex_drag, translate_coords=lambda pos: (pos[0], pos[1], 0, 1))
    editor.tool_shape = FakeCollection_vertex_grips()
    editor.get_draw_color = lambda: '#00000080'
    editor.get_sel_color = lambda: '#0000FF80'
    editor.reject_busy = lambda: AppGeoEditor.reject_busy(editor)
    editor.snap = lambda x, y: (x, y)
    editor.app.grid_status = lambda: False
    editor.app.plotcanvas = SimpleNamespace(new_shape_collection=lambda layers: FakeCollection_vertex_grips())
    editor.vertex_grips = VertexGrips(editor)
    editor.vertex_grips.sync()
    return editor, original


def test_one_drag_is_one_undo_step():
    editor, original = drag_editor(box(0, 0, 4, 4))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (4, 0), QtCore.Qt.KeyboardModifier.NoModifier)
    grips.move((20, 0), (5, -1))
    assert grips.release((20, 0), (5, -1))
    assert (5, -1) in list(editor.selected[0].geo.exterior.coords)
    assert len(editor._history_undo) == 1
    assert grips.shape is editor.selected[0]  # plot_changes() ran sync(): grips on the new shape
    assert editor.undo()
    assert editor.selected == [original]


def test_invalid_drag_does_not_change_the_shape():
    editor, original = drag_editor(box(0, 0, 4, 4))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (4, 0), QtCore.Qt.KeyboardModifier.NoModifier)
    grips.move((20, 0), (0, 4))  # across the square: self-intersection
    assert grips.release((20, 0), (0, 4))
    assert editor.selected == [original]
    assert not editor._history_undo
    assert editor.app.inform.emit.called


ROUNDED = box(1, 1, 9, 5).buffer(1, join_style=BufferJoinStyle.round)


def on_some_arc(ring, point):
    """True if point is on the circle of 1 of the arcs of the closed ring."""
    vertices = list(ring.coords)[:-1]
    for arc in find_arcs(vertices, True):
        center = circle_center(vertices[arc.start], arc.middle, vertices[arc.end])
        if abs(math.dist(center, point) - math.dist(center, vertices[arc.start])) < 1e-9:
            return True
    return False


def inspect(editor, tolerance=0):
    return geo.AppGeoEditor.diagnose_selected(editor, tolerance)


def test_open_closed_duplicates_zero_and_self_intersection():
    editor, shapes = join_editor([
        [(0, 0), (1, 0)], [(1, 0), (0, 0)],
        [(3, 0), (4, 0), (4, 1), (3, 0)],
        [(5, 0), (5, 0)], [(6, 0), (7, 1), (6, 1), (7, 0)]])
    report = inspect(editor)
    assert report['duplicate'] == shapes[:2]
    assert report['zero_length'] == [shapes[3]]
    assert shapes[4] in report['invalid']
    assert report['open'] == [shapes[0], shapes[1], shapes[4]]
    assert len(report['endpoints']) == 6
    assert shapes[2] not in report['open']


SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]


HOLE = [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]


def kinds(grips, kind):
    return [(address, point) for k, address, point in grips if k == kind]


def test_grips_of_open_line_polygon_hole_and_multipolygon():
    grips = vertex_grips(LineString([(0, 0), (2, 0), (2, 2)]))
    assert kinds(grips, 'vertex') == [((0, 0, 0), (0, 0)), ((0, 0, 1), (2, 0)), ((0, 0, 2), (2, 2))]
    assert kinds(grips, 'mid') == [((0, 0, 0), (1, 0)), ((0, 0, 1), (2, 1))]
    grips = vertex_grips(MultiPolygon([Polygon(SQUARE, [HOLE]), box(10, 10, 11, 11)]))
    rings = {address[:2] for address, __ in kinds(grips, 'vertex')}
    assert rings == {(0, 0), (0, 1), (1, 0)}


def test_insert_off_segment_needs_on_segment_false():
    line = LineString([(0, 0), (2, 0)])
    with pytest.raises(ValueError):
        edit_vertex(line, 'insert', (0, 0, 0), (1, 1), on_segment=True)
    assert list(edit_vertex(line, 'insert', (0, 0, 0), (1, 1)).coords) == [(0, 0), (1, 1), (2, 0)]


def test_bad_edits_raise():
    short, line = LineString([(0, 0), (1, 0)]), LineString([(0, 0), (2, 0)])
    for geometry, action, address, point in ((short, 'remove', (0, 0, 0), None), (line, 'move', (0, 0, 0), (1,)),
                                             (line, 'move', (0, 1, 0), (1, 0)), (line, 'unknown', (0, 0, 0), (1, 0)),
                                             (box(0, 0, 4, 4), 'arc_end', (0, 0, 1), (5, 0)),
                                             (ROUNDED_vertex_ops, 'arc_mid', (0, 0, 1), (0, 0)),
                                             (ROUNDED_vertex_ops, 'arc_mid', (0, 0, 1, 17), None)):
        with pytest.raises(ValueError):
            edit_vertex(geometry, action, address, point)


ROUNDED_vertex_ops = box(1, 1, 9, 5).buffer(1, join_style=BufferJoinStyle.round)   # 4 corner arcs of radius 1


CORNERS = [(1, 1), (9, 1), (9, 5), (1, 5)]


def ring_vertices(ring):
    coords = [tuple(coord) for coord in ring.coords]
    return coords[:-1] if coords[0] == coords[-1] else coords


def s_curve():
    """An open line: 1 straight segment, 2 quarter arcs that share the vertex (1, 1), 1 straight segment."""
    half = math.sqrt(0.5)
    first = arc_points((0, 0), (1 - half, half), (1, 1), 64)
    second = arc_points((1, 1), (1 + half, 2 - half), (2, 2), 64)
    return [(-3, 0)] + first + second[1:] + [(2, 5)]


def test_arc_middle_and_arc_points():
    through = (math.cos(0.3), math.sin(0.3))
    assert arc_middle((1, 0), through, (0, 1)) == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))
    assert arc_middle((0, 0), (1, 0), (2, 0)) == (1, 0)   # on 1 line: the middle of the segment
    points = arc_points((1, 0), (math.sqrt(0.5), math.sqrt(0.5)), (0, 1), 32)
    assert len(points) == 9   # a quarter of 32 steps
    assert points[0] == (1, 0) and points[-1] == (0, 1)
    assert all(math.dist(point, (0, 0)) == pytest.approx(1) for point in points)
    assert len(arc_points((1, 0), (math.cos(0.05), math.sin(0.05)), (math.cos(0.1), math.sin(0.1)), 32)) == 4
    assert len(arc_points((1, 0), (0, 1), (-1, 0), 4)) == 7   # 4 steps: 2 segments, but at most 30 degrees each
    assert arc_points((0, 0), (1, 0), (2, 0), 32) == [(0, 0), (2, 0)]


def test_find_arcs_rounded_rectangle_and_s_curve():
    vertices = ring_vertices(ROUNDED_vertex_ops.exterior)
    arcs = find_arcs(vertices, True)
    assert len(arcs) == 4 and not any(arc.full for arc in arcs)
    for arc in arcs:
        center = circle_center(vertices[arc.start], arc.middle, vertices[arc.end])
        assert math.dist(center, vertices[arc.start]) == pytest.approx(1)
        assert min(math.dist(center, corner) for corner in CORNERS) == pytest.approx(0, abs=1e-9)
    # an open S curve: the 2 arcs share a vertex
    vertices = s_curve()
    arcs = find_arcs(vertices, False)
    assert len(arcs) == 2
    assert arcs[0].end == arcs[1].start
    assert vertices[arcs[0].end] == (1, 1)


def test_skeleton_round_trip_of_a_rounded_rectangle_and_others():
    vertices = ring_vertices(ROUNDED_vertex_ops.exterior)
    nodes, links = ring_skeleton(vertices, True)
    assert len(nodes) == 8 and len(links) == 8
    assert sum(1 for link in links if link is not None) == 4
    rebuilt = expand_skeleton([vertices[node] for node in nodes], links, True, 64)
    assert rebuilt[0] == rebuilt[-1]
    assert Polygon(rebuilt).symmetric_difference(ROUNDED_vertex_ops).area < 1e-9
    # an open S curve and a full circle
    vertices = s_curve()
    nodes, links = ring_skeleton(vertices, False)
    assert nodes == [0, 1, 17, 33, 34]
    assert [link is None for link in links] == [True, False, False, True]
    assert ring_skeleton(ring_vertices(Point(0, 0).buffer(2).exterior), True) == ([], [])


def grips_of(geometry, kind):
    return [(address, point) for k, address, point in vertex_grips(geometry) if k == kind]


def on_some_arc_vertex_ops(ring, point, closed=True):
    """True if point is on the circle of 1 of the arcs of ring. A point on the circle is not always on the chords."""
    vertices = ring_vertices(ring)
    for arc in find_arcs(vertices, closed):
        center = circle_center(vertices[arc.start], arc.middle, vertices[arc.end])
        if abs(math.dist(center, point) - math.dist(center, vertices[arc.start])) < 1e-9:
            return True
    return False


def test_grips_of_a_rounded_rectangle_and_a_full_circle():
    assert len(grips_of(ROUNDED_vertex_ops, 'arc_end')) == 8
    assert grips_of(ROUNDED_vertex_ops, 'vertex') == []
    assert len(grips_of(ROUNDED_vertex_ops, 'mid')) == 4
    arc_mids = grips_of(ROUNDED_vertex_ops, 'arc_mid')
    assert len(arc_mids) == 4 and all(len(address) == 4 for address, __ in arc_mids)
    assert all(on_some_arc_vertex_ops(ROUNDED_vertex_ops.exterior, point) for __, point in arc_mids)
    # a full circle
    radius_grips = grips_of(Point(0, 0).buffer(2), 'radius')
    assert [address for address, __ in radius_grips] == [(0, 0, 0)] * 4
    assert [value for __, point in radius_grips for value in point] == pytest.approx([2, 0, 0, 2, -2, 0, 0, -2])
    assert len(vertex_grips(Point(0, 0).buffer(2))) == 4


def test_arc_mid_and_arc_end_edits():
    address, middle = grips_of(ROUNDED_vertex_ops, 'arc_mid')[0]
    cursor = (middle[0] - 0.3, middle[1] - 0.3)
    result = edit_vertex(ROUNDED_vertex_ops, 'arc_mid', address, cursor, steps=64)
    check_vertex_result(result, address)
    assert on_some_arc_vertex_ops(result.exterior, cursor)
    assert len(find_arcs(ring_vertices(result.exterior), True)) == 4

    (address, point), = [(a, p) for a, p in grips_of(ROUNDED_vertex_ops, 'arc_end') if p == (1.0, 0.0)]
    middle = arc_middle((1, 0), (1 - math.sqrt(0.5), 1 - math.sqrt(0.5)), (0, 1))
    result = edit_vertex(ROUNDED_vertex_ops, 'arc_end', address, (1.3, 0), steps=64)
    check_vertex_result(result, address)
    assert (1.3, 0) in list(result.exterior.coords)
    assert on_some_arc_vertex_ops(result.exterior, middle)
    assert len(find_arcs(ring_vertices(result.exterior), True)) == 4

    # an end shared by 2 arcs changes both
    line = LineString(s_curve())
    result = edit_vertex(line, 'arc_end', (0, 0, 17), (1.2, 1), steps=64)
    arcs = find_arcs(list(result.coords), False)
    assert len(arcs) == 2 and arcs[0].end == arcs[1].start
    assert result.coords[arcs[0].end] == (1.2, 1)
