"""Excellon Editor tests: history, transforms, slots, arrays, filters, snapping, save and defects."""
from concurrent.futures import ThreadPoolExecutor
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
from unittest.mock import MagicMock, patch
import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.affinity import rotate, translate
from appEditors.appExcEditor import (AppExcEditor, AppExcEditorUI, MoveEditorExc, ResizeEditorExc,
                                     DrillArray, SlotArray)
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape
from camlib import circle_quad_segs, object_circle_steps
from appEditors.exc_plugins.ExcHistory import ExcellonHistory
from appGUI.MainGUI import MainGUI
from PyQt6 import QtCore, QtGui, QtWidgets
from unittest.mock import MagicMock, Mock
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import Point
from appEditors.appExcEditor import AppExcEditor, CopyEditorExc, DrillArray, MoveEditorExc
from appEditors.appGeoEditor import DrawTool
from defaults import AppDefaults
from tests.helpers_objects import _headless_qt  # noqa: F401,E402
from types import MethodType
from unittest.mock import Mock, MagicMock, patch
from appEditors.appExcEditor import AppExcEditor
from appEditors.exc_plugins.ExcProperties import native_feature_properties
from tests.helpers_objects import app, _headless_qt, reparse_excellon  # noqa: F401,E402
from appHandlers.appIO import appIO
from PyQt6 import QtCore
from PyQt6 import QtWidgets
from appGUI.GUIElements import FCTable
from appEditors.appExcEditor import CopyEditorExc, MoveEditorExc
from shapely.geometry import box
from tests.helpers_objects import app, _headless_qt, make_same_diameter_excellon, reparse_excellon  # noqa: F401,E402
from appEditors.appGeoEditor import AppGeoEditor
from threading import get_ident
from shapely.affinity import rotate, scale, skew
from shapely.geometry import LineString, Point, Polygon
import math
from types import SimpleNamespace
from shapely.geometry import LineString
from shapely.affinity import translate
from appEditors.appExcEditor import SlotAdd, SlotArray
from appEditors.appExcEditor import AppExcEditor, translated_exc_shape
from appEditors.exc_plugins.ExcProperties import feature_properties
from unittest.mock import Mock, MagicMock
from shapely import LineString, Point
from shapely.affinity import scale
from appEditors import appExcEditor as exc
from test_geo_editor import FakeCollection
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock, patch
from shapely.geometry import LineString, MultiLineString
from appEditors.appExcEditor import AppExcEditor, EDITOR_ACTION_PROPERTY
from appEditors.appGeoEditor import DrawToolShape
from appEditors.appExcEditor import AppExcEditor, AppExcEditorUI
import importlib
import re
from copy import deepcopy
from appObjects.ExcellonObject import ExcellonObject
from appParsers.ParseExcellon import Excellon
from camlib import CNCjob
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
from tests.helpers_objects import make_app_0b as make_app, no_shape_collection  # noqa: F401,E402


def drill(x=0):
    return DrawToolShape(MultiLineString([[(x - 0.5, 0), (x + 0.5, 0)], [(x, -0.5), (x, 0.5)]]))


def scene():
    first, second = drill(), DrawToolShape(LineString([(4, 0), (6, 1)]).buffer(0.5))
    storage = AppGeoEditor.make_storage()
    storage.insert_many([first, second])
    editor = SimpleNamespace(
        storage_dict={1.0: storage}, selected=[first], utility=[], current_storage=storage,
        points_edit={1.0: [(0, 0)]}, slot_points_edit={1.0: [((4, 0), (6, 1))]},
        olddia_newdia={1.0: 1.0}, tool2tooldia={1: 1.0}, last_tool_selected=1,
        is_modified=False, decimals=4, active_tool=None, in_action=False, tool_shape=Mock(),
        build_ui=Mock(), replot=Mock(), select_tool=Mock(),
        app=SimpleNamespace(options={'excellon_circle_steps_mode': 'fixed', 'excellon_circle_steps': 64}, inform=Mock(), log=Mock(), jump_signal=Mock()),
        history_changed=Mock())
    for name in ('_collect_exc_shape', 'add_exc_shape', 'delete_shape', 'delete_selected',
                 'get_selected', 'undo', 'redo', 'resize_features', 'refresh_feature_table',
                 'feature_filter_values', 'filter_features', 'sync_tool_rows'):
        setattr(editor, name, MethodType(getattr(AppExcEditor, name), editor))
    editor.make_storage = AppGeoEditor.make_storage
    editor.history = ExcellonHistory(editor)
    return editor, first, second


def test_delete_group_restores_wrapper_selection_counts_and_dirty_state():
    editor, first, second = scene()
    editor.selected = [first, second]
    editor.delete_selected()
    assert list(editor.storage_dict[1.0].get_objects()) == []
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict[1.0].get_objects()) == [first, second]
    assert editor.selected == [first, second]
    assert editor.points_edit == {1.0: [(0, 0)]}
    assert not editor.is_modified
    assert editor.redo()
    assert list(editor.storage_dict[1.0].get_objects()) == []
    assert editor.is_modified


def test_nested_add_and_geometry_change_are_one_delta_and_branch():
    editor, first, second = scene()
    old = first.geo
    added = drill(9)
    with editor.history.operation('Move and add') as accepted:
        assert accepted
        first.geo = translate(first.geo, xoff=2)
        editor.add_exc_shape(added, editor.storage_dict[1.0])
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert first.geo is old
    assert list(editor.storage_dict[1.0].get_objects()) == [first, second]
    editor.add_exc_shape(drill(12), editor.storage_dict[1.0])
    assert not editor.redo()
    assert editor.history.redo_stack == []


def test_exception_rolls_back_tools_counters_geometry_and_order():
    editor, first, second = scene()
    old = first.geo
    with pytest.raises(ValueError):
        with editor.history.operation('Broken'):
            first.geo = translate(first.geo, xoff=8)
            editor.storage_dict.pop(1.0)
            editor.storage_dict[2.0] = AppGeoEditor.make_storage()
            editor.points_edit.clear()
            editor.olddia_newdia.clear()
            editor.selected.clear()
            raise ValueError('failed calculation')
    assert list(editor.storage_dict) == [1.0]
    assert list(editor.storage_dict[1.0].get_objects()) == [first, second]
    assert first.geo is old
    assert editor.points_edit == {1.0: [(0, 0)]}
    assert editor.selected == [first]
    assert not editor.history.undo_stack


def test_diagnostics_are_read_only_and_do_not_report_open_drill_crosses():
    editor, first, second = scene()
    editor.storage_dict[1.0].insert(drill(0.00001))
    report = AppExcEditor.diagnose_features(editor, selected_only=False, tolerance=0.001)
    assert report['drills'] == 2
    assert report['slots'] == 1
    assert len(report['duplicate']) == 1
    assert not report['invalid']
    assert not report['degenerate']
    assert not editor.history.undo_stack
    assert not editor.is_modified
    assert editor.selected == [first]
    selected = AppExcEditor.diagnose_features(editor, selected_only=True, tolerance=0.001)
    assert selected['drills'] == 1
    assert selected['slots'] == 0


@pytest.mark.parametrize('mode', ['adaptive'])
def test_selected_resize_preserves_native_slot_endpoints_and_unselected_drill(mode):
    editor, first, second = scene()
    editor.app.options['excellon_circle_steps_mode'] = mode
    second.exc_slot = ((4.0, 0.0), (6.0, 1.0))
    second.exc_slot_geometry = second.geo
    editor.selected = [second]
    editor.resize_features = MethodType(AppExcEditor.resize_features, editor)
    tool = ResizeEditorExc.__new__(ResizeEditorExc)
    tool.draw_app = editor
    tool.ui = SimpleNamespace(res_dia_entry=Mock())
    tool.ui.res_dia_entry.get_value.return_value = 2.0
    tool.make()
    assert list(editor.storage_dict[1.0].get_objects()) == [first]
    resized = list(editor.storage_dict[2.0].get_objects())[0]
    assert resized.exc_slot == second.exc_slot
    # the new slot ends have the segments that the Excellon object gives to a hole of this tool
    quad_segs = circle_quad_segs(object_circle_steps(editor.app.options, 'excellon', 1.0, 'MM'))
    assert resized.geo.equals(LineString(second.exc_slot).buffer(1.0, quad_segs))
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict) == [1.0]
    assert list(editor.storage_dict[1.0].get_objects()) == [first, second]
    assert editor.selected == [second]
    assert editor.redo()
    assert list(editor.storage_dict[2.0].get_objects()) == [resized]


def test_tool_edit_resizes_all_features_atomically_and_is_one_history_step():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    editor, first, second = scene()
    editor.ui = SimpleNamespace(tools_table_exc=QtWidgets.QTableWidget(1, 2))
    diameter = QtWidgets.QTableWidgetItem('2.0')
    diameter.setData(QtCore.Qt.ItemDataRole.UserRole, 1.0)
    editor.ui.tools_table_exc.setItem(0, 1, diameter)
    second.exc_slot = ((4.0, 0.0), (6.0, 1.0))
    second.exc_slot_geometry = second.geo
    AppExcEditor.on_tool_edit(editor, diameter)
    assert list(editor.storage_dict) == [2.0]
    assert len(list(editor.storage_dict[2.0].get_objects())) == 2
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict[1.0].get_objects()) == [first, second]
    assert editor.tool2tooldia == {1: 1.0}
    assert qapp is not None


def test_tool_add_delete_and_slot_conversion_restore_counts_and_selection():
    editor, first, second = scene()
    editor.ui = SimpleNamespace(addtool_entry=Mock(), tools_table_exc=MagicMock())
    editor.units, editor.decimals, editor.dec_format = 'MM', 4, lambda value: round(value, 4)
    editor.selected = [second]
    AppExcEditor.on_tool_delete(editor, dia=1.0)
    assert editor.selected == []
    assert not editor.slot_points_edit
    assert editor.undo()
    assert editor.selected == [second]
    AppExcEditor.on_tool_add(editor, tooldia=2.0)
    assert list(editor.storage_dict) == [1.0, 2.0]
    assert editor.undo()
    item = Mock()
    item.row.return_value = 0
    editor.ui.tools_table_exc.selectedItems.return_value = [item]
    editor.ui.tools_table_exc.item().text.return_value = '1.0'
    AppExcEditor.on_slots_conversion(editor)
    assert all(isinstance(shape.geo, MultiLineString) for shape in editor.storage_dict[1.0].get_objects())
    assert all(shape in set(editor.storage_dict[1.0].get_objects()) for shape in editor.selected)
    assert len(editor.points_edit[1.0]) == 2
    assert editor.slot_points_edit[1.0] == []
    assert editor.undo()
    assert list(editor.storage_dict[1.0].get_objects()) == [first, second]


def test_native_slot_and_rotated_drill_save_extraction_preserve_coordinates():
    editor, first, second = scene()
    first.geo = rotate(first.geo, 37, origin=(0, 0))
    second.exc_slot = ((4.0, 0.0), (6.0, 1.0))
    second.exc_slot_geometry = second.geo
    editor._save_excellon_in_place = Mock()
    source = SimpleNamespace(geo_steps_per_circle=64, obj_options={'name': 'original'}, tools={'sentinel': 1})
    AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    tools = editor.new_tools
    assert len(tools) == 1
    assert tools[1]['drills'][0].distance(first.geo.centroid) < 1e-12
    assert tuple(tools[1]['slots'][0][0].coords[0]) == second.exc_slot[0]
    assert tuple(tools[1]['slots'][0][1].coords[0]) == second.exc_slot[1]
    assert source.tools == {'sentinel': 1}


EDITOR_ACTION_LABELS = ('Undo', 'Redo', 'Edit slot endpoints', 'Rotate selected features', 'Mirror X',
                        'Mirror Y', 'Scale selected features')


def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def qt_editor(app_units='MM'):
    """A real AppExcEditor on a stand-in app with real menu and toolbar (same setup as test_exc_selected_transforms)."""
    qapp()
    app = MagicMock()
    app.decimals, app.app_units, app.resource_location = 4, app_units, 'assets/resources'
    app.dec_format = lambda value, decimals=None: round(float(value), 4 if decimals is None else decimals)
    app.defaults = AppDefaults()
    app.options = dict(app.defaults.defaults)
    app.ui.exc_editor_menu = QtWidgets.QMenu()
    app.ui.exc_edit_toolbar = QtWidgets.QToolBar()
    return app, AppExcEditor(app)


def bind(editor, *names):
    """The scene() stand-in has only some AppExcEditor methods: bind the named ones from the real class."""
    for name in names:
        setattr(editor, name, MethodType(getattr(AppExcEditor, name), editor))


def warned(editor):
    """True when the app was told a WARNING_NOTCL message (the inform signal is a Mock)."""
    return any('WARNING_NOTCL' in str(call.args[0]) for call in editor.app.inform.emit.call_args_list if call.args)


def native_obj(units, tooldia, xy):
    """Minimal stand-in for an ExcellonObject with one tool and one drill, in the given units."""
    return SimpleNamespace(units=units, obj_options={'name': 'job'}, diameterless=False, visible=True,
                           geo_steps_per_circle=64, zeros='L',
                           tools={1: {'tooldia': tooldia, 'drills': [Point(*xy)], 'slots': []}})


def test_editor_created_in_mm_edits_in_object_in_inches_without_scaling():
    app, editor = qt_editor('MM')
    app.app_units = 'IN'
    editor.edit_fcexcellon(native_obj('IN', 0.04, (1.0, 2.0)))
    assert editor.units == 'IN'
    assert list(editor.points_edit) == [0.04]
    drill = editor.points_edit[0.04][0]
    assert (drill.x, drill.y) == pytest.approx((1.0, 2.0))
    # the '+' drill symbol is as wide as the tool, so an unscaled 0.04 in tool gives 0.04 bounds
    assert editor.storage_dict[0.04].get_objects()
    bounds = list(editor.storage_dict[0.04].get_objects())[0].geo.bounds
    assert bounds[2] - bounds[0] == pytest.approx(0.04)


def test_object_attribute_ids_follow_the_holes_through_edit_and_save():
    app, editor = qt_editor('MM')
    obj = native_obj('MM', 1.0, (1.0, 1.0))
    obj.tools[1].update(drills=[Point(1, 1), Point(2, 2), Point(3, 3)], slots=[(Point(5, 5), Point(6, 5))],
                        drills_to=[7, 8, 0], slots_to=[9])
    editor.edit_fcexcellon(obj)
    storage = editor.storage_dict[1.0]
    gone = next(s for s in storage.get_objects() if getattr(s, 'exc_slot', None) is None
                and s.geo.centroid.coords[0] == pytest.approx((1.0, 1.0)))
    storage.replace(removed=[gone], inserted=[])
    mover = next(s for s in storage.get_objects() if getattr(s, 'exc_slot', None) is None
                 and s.geo.centroid.coords[0] == pytest.approx((2.0, 2.0)))
    editor.selected = [mover]
    assert editor.transform_features('rotate', 90) != 'fail'
    editor._save_excellon_in_place = Mock()
    editor.update_fcexcellon(obj, in_place=True)
    tool = editor.new_tools[1]
    by_xy = {(round(p.x, 3), round(p.y, 3)): i for p, i in zip(tool['drills'], tool['drills_to'])}
    # the deleted hole dropped its id, the rotated one keeps 8, the untouched one keeps 0; the slot keeps 9
    assert sorted(by_xy.values()) == [0, 8] and by_xy[(3.0, 3.0)] == 0
    assert tool['slots_to'] == [9]


def test_scale_keeps_tool_table_sorted_and_next_add_selects_its_row():
    editor, drill, slot = scene()
    editor.transform_features = MethodType(AppExcEditor.transform_features, editor)
    editor.ui = SimpleNamespace(addtool_entry=Mock(), tools_table_exc=MagicMock())
    editor.units, editor.dec_format = 'MM', lambda value: round(value, 4)
    editor.selected = [slot]
    # the slot moves to 0.5 mm: it sorts before the 1.0 mm drill
    assert editor.transform_features('scale', 0.5) != 'fail'
    assert editor.tool2tooldia == {1: 0.5, 2: 1.0}
    assert list(editor.tool2tooldia.values()) == sorted(editor.tool2tooldia.values())

    def rebuild_like_build_ui():
        # build_ui() rebuilds tool2tooldia in diameter order from the table; the scene's build_ui is a Mock
        editor.tool2tooldia = {index: dia for index, dia in enumerate(sorted(editor.storage_dict), 1)}

    editor.build_ui = Mock(side_effect=rebuild_like_build_ui)
    AppExcEditor.on_tool_add(editor, tooldia=0.75)
    assert editor.tool2tooldia == {1: 0.5, 2: 0.75, 3: 1.0}
    assert editor.last_tool_selected == 2
    editor.ui.tools_table_exc.selectRow.assert_called_with(1)


class Entry:
    """A panel field or radio set of the stand-in panels: get_value() / set_value()."""

    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


DRILL_ARRAY_PANEL = dict(array_type_radio='linear', array_size_entry=5, axis_radio='X', pitch_entry=2.54,
                         linear_angle_entry=0.0, array_dir_radio='CW', circular_angle_entry=30.0, radius_entry=0.0)


def array_tool(cls, panel):
    """An array tool (made without its plugin panel) on the scene() editor; the panel holds the given values."""
    editor, _first, _second = scene()
    bind(editor, 'on_exc_shape_complete', 'delete_utility_geometry', 'on_shape_complete', 'add_shape')
    editor.snap = lambda x, y: (x, y)
    editor.snap_x = editor.snap_y = 0.0
    tool = cls.__new__(cls)
    DrawTool.__init__(tool, editor)
    tool.app, tool.selected_dia, tool.cursor_data_control = editor.app, 1.0, True
    tool.origin = tool.destination = tool.flag_for_circ_array = None
    tool.last_dx = tool.last_dy = 0
    tool.pt = []
    tool.ui = SimpleNamespace(**{name: Entry(value) for name, value in panel.items()})
    editor.active_tool = tool
    return editor, tool


def added_centers(editor):
    """The x, y of the center of each feature added after the 2 features of the scene() editor, in 1 flat list."""
    return [value for shape in list(editor.storage_dict[1.0].get_objects())[2:]
            for value in (shape.geo.centroid.x, shape.geo.centroid.y)]


def test_drill_array_add_button_uses_the_panel_values_changed_after_the_last_mouse_move():
    editor, tool = array_tool(DrillArray, DRILL_ARRAY_PANEL)
    tool.utility_geometry(data=(0.0, 0.0))  # the mouse goes over the canvas: 5 drills, 2.54 along X
    tool.ui.array_size_entry.set_value(3)
    tool.ui.pitch_entry.set_value(1.27)
    tool.ui.axis_radio.set_value('Y')
    tool.add_drill_array((10.0, 20.0))
    assert added_centers(editor) == pytest.approx([10, 20, 10, 21.27, 10, 22.54])
    assert len(editor.points_edit[1.0]) == 1 + 3


SLOT_ARRAY_PANEL = dict(array_type_radio='linear', array_size_entry=5, array_axis_radio='X', array_pitch_entry=2.54,
                        array_linear_angle_entry=0.0, array_direction_radio='CW', array_angle_entry=30.0,
                        radius_entry=0.0, slot_length_entry=2.0, slot_direction_radio='X', slot_angle_entry=0.0)


@pytest.mark.parametrize('cls', [DrillArray])
def test_circular_array_add_button_keeps_the_typed_radius_after_a_mouse_move(cls):
    if cls is DrillArray:
        panel = dict(DRILL_ARRAY_PANEL, array_dir_radio='CCW', circular_angle_entry=90.0)
    else:
        panel = dict(SLOT_ARRAY_PANEL, array_direction_radio='CCW', array_angle_entry=90.0)
    panel.update(array_type_radio='circular', array_size_entry=4, radius_entry=5.0)
    editor, tool = array_tool(cls, panel)
    tool.utility_geometry(data=(3.0, 4.0))  # the mouse goes over the canvas before a center is clicked
    assert tool.ui.radius_entry.get_value() == 5.0
    (tool.add_drill_array if cls is DrillArray else tool.add_slot_array)((10.0, 20.0))
    assert added_centers(editor) == pytest.approx([15, 20, 10, 25, 5, 20, 10, 15])
    # after a center click the mouse move still shows the radius
    tool.set_origin((0.0, 0.0))
    tool.utility_geometry(data=(6.0, 8.0))
    assert tool.ui.radius_entry.get_value() == pytest.approx(10.0)


def copy_tool(**panel):
    """A Copy tool (made without its plugin panel) on the scene() editor, the drill at (0, 0) selected."""
    editor, _first, _second = scene()
    editor.delete_utility_geometry = Mock()
    tool = CopyEditorExc.__new__(CopyEditorExc)
    DrawTool.__init__(tool, editor)
    tool.name, tool.app, tool.has_selection, tool.sel_limit = 'drill_copy', editor.app, True, 30
    tool.origin = tool.destination = tool.util_geo = None
    tool.selected_dia_list = [1.0]
    tool.draw_cursor_data = Mock()
    tool.ui = SimpleNamespace(**{name: Entry(value) for name, value in panel.items()})
    editor.active_tool = tool
    return editor, tool


def test_circular_copy_array_previews_and_copies_without_a_linear_preview_first():
    editor, tool = copy_tool(mode_radio='a', array_type_radio='circular', array_size_entry=4, array_dir_radio='CCW',
                             angle_entry=90.0)
    tool.click((0.0, 0.0))
    preview = tool.utility_geometry(data=(10.0, 0.0))
    assert preview is not None and len(preview.geo) == 4 + 1  # the 4 copies and the radius line
    tool.click((10.0, 0.0))
    assert added_centers(editor) == pytest.approx([10, 0, 0, 10, -10, 0, 0, -10])
    assert len(editor.points_edit[1.0]) == 1 + 4


def press(editor, key, monkeypatch):
    """Press a key without modifier in the Excellon editor, through the key handler of the main window."""
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.NoModifier))
    editor.app.call_source, editor.app.exc_editor, editor.app.collection = 'exc_editor', editor, Mock()
    gui = SimpleNamespace(app=editor.app, select_drill_btn=SimpleNamespace(isChecked=lambda: False))
    MainGUI.keyPressEvent(gui, SimpleNamespace(key=key, native=SimpleNamespace()))


def test_copy_enter_with_a_typed_length_copies_towards_the_cursor(monkeypatch):
    editor, tool = copy_tool(mode_radio='n')
    tool.copy_tool = SimpleNamespace(length=10.0)  # the Projection field: '10' typed on the canvas
    editor.app.on_jump_to = Mock()
    editor.snap_x, editor.snap_y = 3.0, 4.0  # the cursor
    press(editor, 'Enter', monkeypatch)
    assert warned(editor) and added_centers(editor) == []  # no reference point yet
    tool.click((0.0, 0.0))
    press(editor, 'Enter', monkeypatch)
    assert added_centers(editor) == pytest.approx([6, 8])
    editor.select_tool.assert_called_with('drill_select')


def test_enter_with_the_move_tool_moves_the_selection_to_the_cursor(monkeypatch):
    editor, first, _second = scene()
    editor.delete_utility_geometry = Mock()
    tool = MoveEditorExc.__new__(MoveEditorExc)
    DrawTool.__init__(tool, editor)
    tool.name, tool.origin, tool.destination, tool.selected_dia_list = 'drill_move', (0.0, 0.0), None, [1.0]
    editor.active_tool = tool
    editor.on_canvas_click_left_handler = tool.click
    editor.snap_x, editor.snap_y = 2.0, 3.0  # the cursor
    press(editor, 'Enter', monkeypatch)
    drills = [shape for shape in editor.storage_dict[1.0].get_objects() if shape.geo.geom_type == 'MultiLineString']
    assert len(drills) == 1 and drills[0] is not first
    assert (drills[0].geo.centroid.x, drills[0].geo.centroid.y) == pytest.approx((2, 3))


def editor_scene():
    editor, drill, slot = scene()
    editor.transform_features = MethodType(AppExcEditor.transform_features, editor)
    return editor, drill, slot


@pytest.mark.parametrize('reference,expected', [
    ('upper_right', (1, 1)),
])
def test_selection_corner_reference(reference, expected):
    editor, first, _ = editor_scene()
    assert editor.transform_features('scale', 2, reference) != 'fail'
    assert native_feature_properties(editor.selected[0], 2, 0)['center'] == pytest.approx(
        (-expected[0] / 2, -expected[1] / 2))
    assert editor.undo() and editor.selected == [first]


@pytest.mark.parametrize('diameter', [1.0])
def test_real_table_keeps_exact_scaled_diameters(diameter):
    from types import SimpleNamespace
    from shapely.affinity import scale
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    editor, first, _ = editor_scene()
    storage = editor.make_storage()
    first.geo = scale(first.geo, diameter, diameter, origin=(0, 0))
    storage.insert(first)
    other = drill(4)
    other.geo = scale(other.geo, diameter * 1.000001, diameter * 1.000001, origin=(4, 0))
    second = editor.make_storage()
    second.insert(other)
    editor.storage_dict = {diameter: storage, diameter * 1.000001: second}
    editor.olddia_newdia = {key: key for key in editor.storage_dict}
    editor.points_edit = {key: [(0, 0)] for key in editor.storage_dict}
    editor.slot_points_edit = {key: [] for key in editor.storage_dict}
    editor.tool2tooldia = dict(enumerate(editor.storage_dict, 1))
    editor.app.ui = MagicMock()
    editor.app.app_units = 'MM'
    editor.app.defaults = MagicMock()
    editor.app.options['global_mselect_key'] = 'Control'
    editor.edited_obj = SimpleNamespace(obj_options={'name': 'exact'})
    editor.ui = SimpleNamespace(tools_table_exc=FCTable(), name_entry=Mock(), exc_edit_widget=QtWidgets.QWidget())
    editor.ui.tools_table_exc.setColumnCount(4)
    for name in ('build_ui', 'on_row_selected', 'on_tool_edit', 'on_tool_delete', 'sync_tool_table_selection'):
        method = getattr(AppExcEditor, name)
        setattr(editor, name, lambda *args, method=method: method(editor, *args))
    editor.build_ui()
    editor.transform_features('scale', 1.234567)
    exact = diameter * 1.234567
    assert exact in editor.tool2tooldia.values()
    row = next(row for row in range(2)
               if editor.ui.tools_table_exc.item(row, 1).data(QtCore.Qt.ItemDataRole.UserRole) == exact)
    editor.on_row_selected(row, 0)
    assert editor.selected and editor.selected[0] in editor.storage_dict[exact].get_objects()
    assert editor.undo()
    assert editor.redo()
    assert exact in editor.tool2tooldia.values()
    editor.ui.tools_table_exc.clearSelection()
    editor.ui.tools_table_exc.selectRow(row)
    editor.app.plotcanvas = MagicMock()
    editor.launched_from_shortcuts = False
    assert set(MoveEditorExc(editor).selected_dia_list) == {exact}
    with patch('appEditors.appExcEditor.ExcCopyEditorTool'):
        assert set(CopyEditorExc(editor).selected_dia_list) == {exact}
    editor.on_tool_delete()
    assert exact not in editor.storage_dict
    assert editor.undo() and exact in editor.storage_dict
    row = next(row for row in range(2)
               if editor.ui.tools_table_exc.item(row, 1).data(QtCore.Qt.ItemDataRole.UserRole) == exact)
    editor.ui.tools_table_exc.setCurrentCell(row, 0)
    editor.ui.tools_table_exc.item(row, 1).setText('2.3456789')
    assert 2.3456789 in editor.storage_dict
    assert exact not in editor.storage_dict
    assert editor.undo() and exact in editor.storage_dict
    editor.ui.tools_table_exc.clearSelection()
    editor.ui.tools_table_exc.selectRow(row)
    other_storage = editor.storage_dict[diameter * 1.000001]
    AppExcEditor.on_slots_conversion(editor)
    assert set(editor.storage_dict) == {diameter * 1.000001, exact}
    assert editor.storage_dict[diameter * 1.000001] is other_storage
    editor.ui.tools_table_exc.setCurrentCell(row, 1)
    editor.ui.tools_table_exc.item(row, 1).setText('2.3456789')
    table = editor.ui.tools_table_exc

    def selected_diameters():
        return {table.item(index.row(), 1).data(QtCore.Qt.ItemDataRole.UserRole)
                for index in table.selectionModel().selectedRows()}

    AppExcEditor.on_tool_add(editor, tooldia=10)
    added_rows = selected_diameters()
    editor.sync_tool_table_selection()
    canvas_rows = selected_diameters()
    editor.app.delete_selection_shape = Mock()
    AppExcEditor.draw_selection_area_handler(editor, (-2, -2), (2, 2), True)
    assert editor.selected and editor.selected[0] in editor.storage_dict[2.3456789].get_objects()
    assert (added_rows, canvas_rows, selected_diameters()) == ({10}, {2.3456789}, {2.3456789})
    for diameter_to_delete in (2.3456789, [2.3456789]):
        editor.on_tool_delete(diameter_to_delete)
        assert 2.3456789 not in editor.storage_dict
        assert editor.undo() and 2.3456789 in editor.storage_dict
    editor.selected.append(other)
    editor.sync_tool_table_selection()
    assert selected_diameters() == {diameter * 1.000001, 2.3456789}
    assert qapp is not None


@pytest.mark.parametrize('array_type', ['circular'])
def test_large_copy_preview_is_box_but_commit_keeps_native_slots(array_type):
    editor, first, slot = editor_scene()
    editor.selected = [slot]
    editor.delete_utility_geometry = Mock()
    tool = CopyEditorExc.__new__(CopyEditorExc)
    tool.draw_app, tool.ui = editor, MagicMock()
    tool.origin, tool.destination, tool.points = (0, 0), (10, 0), [(0, 0)]
    tool.sel_limit, tool.selection_shape = 2, box(*slot.geo.bounds)
    tool.draw_cursor_data = Mock()
    for name, value in {'mode_radio': 'a', 'array_type_radio': array_type, 'array_size_entry': 3,
                        'rows': 2, 'columns': 2, 'axis_radio': 'X', 'pitch_entry': 2,
                        'linear_angle_spinner': 0, 'placement_radio': 'o', 'offsetx_entry': 3,
                        'offsety_entry': 4, 'spacing_rows': 1, 'spacing_columns': 1,
                        'array_dir_radio': 'CCW', 'angle_entry': 90}.items():
        getattr(tool.ui, name).get_value.return_value = value
    preview = tool.utility_geometry((10, 0))
    assert preview.geo.geom_type == 'Polygon'
    assert tool.make() != 'fail'
    copies = [shape for shape in editor.storage_dict[1].get_objects() if shape not in (first, slot)]
    assert len(copies) == (4 if array_type == '2D' else 3)
    assert all(native_feature_properties(shape, 1, 0)['type'] == 'slot' for shape in copies)
    if array_type == 'circular':
        from shapely import LineString
        from shapely.affinity import rotate, translate
        endpoints = native_feature_properties(slot, 1, 0)['endpoints']
        expected = translate(rotate(LineString(endpoints), 90, origin=(0, 0)), 0, 10)
        actual = native_feature_properties(copies[1], 1, 0)['endpoints']
        assert all(tuple(point) == pytest.approx(wanted)
                   for point, wanted in zip(actual, expected.coords))
    assert editor.undo()
    assert list(editor.storage_dict[1].get_objects()) == [first, slot]


@pytest.mark.parametrize('operation,value,reference,expected', [
    ('mirror_x', 1, 'origin', (5, -0.5)),
    ('scale', 2, (2, 3), (8, -2)),
])
def test_native_slot_transform_and_history(operation, value, reference, expected):
    editor, drill, slot = editor_scene()
    editor.selected = [slot]
    assert editor.transform_features(operation, value, reference) != 'fail'
    transformed = editor.selected[0]
    diameter = 2 if operation == 'scale' else 1
    record = native_feature_properties(transformed, diameter, 0)
    assert record['center'] == pytest.approx(expected)
    assert len(transformed.exc_slot) == 2
    assert drill in editor.storage_dict[1].get_objects()
    assert len(editor.history.undo_stack) == 1
    assert editor.history.revision == 1
    assert editor.undo()
    assert editor.selected == [slot]
    assert editor.redo()
    assert editor.selected == [transformed]


@pytest.mark.parametrize('operation,value,reference', [
    ('scale', 0, 'origin'), ('rotate', float('nan'), 'origin'), ('shear', 2, 'origin'),
])
def test_invalid_transform_is_atomic(operation, value, reference):
    editor, drill, slot = editor_scene()
    assert editor.transform_features(operation, value, reference) == 'fail'
    assert list(editor.storage_dict[1].get_objects()) == [drill, slot]
    assert not editor.history.undo_stack
    assert not editor.is_modified


@pytest.mark.parametrize('source_units', ['MM'])
@pytest.mark.parametrize('editor_units, export_format', [('MM', 'dec'), ('IN', 'ndec')])
@pytest.mark.parametrize('export_units', ['METRIC'])
@pytest.mark.parametrize('in_place', [True])
@pytest.mark.parametrize('string_ids', [True])
def test_source_loader_transform_save_native_unit_roundtrip(app, tmp_path, source_units, editor_units,
                                                           export_units, in_place, string_ids, export_format):
    header = 'METRIC' if source_units == 'MM' else 'INCH'
    text = 'M48\n%s,TZ\nT01C0.100\nT02C0.200\n%%\nT01\nX1.0Y2.0\nX3.0Y4.0\nT02\nX5.0Y6.0G85X7.0Y9.0\nM30\n' % header
    source = reparse_excellon(app, text, tmp_path, name='source')
    source.tools[1]['data'] = {'tag': 'drill'}
    source.tools[2]['data'] = {'tag': 'slot'}
    if string_ids:
        source.tools = {str(key): values for key, values in source.tools.items()}
    original = source.tools
    editor, _, _ = editor_scene()
    editor.app, editor.units, editor.decimals = app, editor_units, 8
    app.app_units = editor_units    # the editor takes its units from the application when it loads
    editor.storage_dict.clear()
    editor.selected.clear()
    editor.dec_format = lambda value: round(value, 8)
    editor.deactivate, editor.activate, editor.set_editor_ui = Mock(), Mock(), Mock()
    editor.on_tool_edit = Mock()
    editor._save_excellon_in_place = MethodType(AppExcEditor._save_excellon_in_place, editor)
    editor.ui = MagicMock()
    AppExcEditor.edit_fcexcellon(editor, source)
    editor.edited_obj_name = 'source'
    factor = 1 if source_units == editor_units else (25.4 if editor_units == 'MM' else 1 / 25.4)
    diameters = sorted(editor.storage_dict)
    first, untouched = list(editor.storage_dict[diameters[0]].get_objects())
    slot = next(iter(editor.storage_dict[diameters[1]].get_objects()))
    assert first.geo.centroid.x == pytest.approx(factor)
    editor.selected = [first, slot]
    editor.transform_features('rotate', 37)
    editor.transform_features('scale', 1.5)
    expected = [(diameter, native_feature_properties(shape, diameter, 0))
                for diameter, storage in editor.storage_dict.items() for shape in storage.get_objects()]
    assert untouched.geo.centroid.x == pytest.approx(3 * factor)
    assert source.tools is original and source.units == source_units
    editor.worker_result = Mock()
    editor.deactivate = Mock(side_effect=editor.history.reset)
    app.worker_task = Mock()
    app.app_units = editor_units
    app.f_handlers = appIO(app)
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 8
    app.options['excellon_exp_format'] = export_format
    app.main_thread = QtCore.QThread.currentThread()
    created = []

    def create(kind, name, initialize):
        obj = type(source)(name, app=app)
        initialize(obj, app)
        created.append(obj)
        return obj

    app.app_obj.new_object.side_effect = create
    assert AppExcEditor.update_fcexcellon(editor, source, in_place=in_place) == 'pending'
    app.worker_task.emit.call_args.args[0]['fcn']()
    assert 'error' not in editor.worker_result.emit.call_args.args[0], app.log.error.call_args_list
    editor.history.finish(editor.worker_result.emit.call_args.args[0])
    saved = source if in_place else created[0]
    assert saved.units == editor_units
    again = reparse_excellon(app, saved.source_file, tmp_path)
    output_factor = 1 if again.units == editor_units else (25.4 if editor_units == 'MM' else 1 / 25.4)
    actual = []
    actual_slots = []
    for values in again.tools.values():
        diameter = values['tooldia'] * output_factor
        actual.extend((diameter, (point.x * output_factor, point.y * output_factor))
                      for point in values.get('drills', []))
        for start, stop in values.get('slots', []):
            actual_slots.append(((start.x * output_factor, start.y * output_factor),
                                 (stop.x * output_factor, stop.y * output_factor)))
            actual.append((diameter, ((start.x + stop.x) / 2 * output_factor,
                                      (start.y + stop.y) / 2 * output_factor)))
    assert len(actual) == len(expected)
    for diameter, record in expected:
        assert any(abs(diameter - d) < 1e-6 and center == pytest.approx(record['center'], abs=1e-6)
                   for d, center in actual)
        if record['type'] == 'slot':
            assert any(all(point == pytest.approx(wanted, abs=1e-6)
                           for point, wanted in zip(sorted(endpoints), sorted(record['endpoints'])))
                       for endpoints in actual_slots)
    assert {values['data']['tag'] for values in saved.tools.values()} == {'drill', 'slot'}
    if not in_place:
        assert source.tools is original and source.units == source_units


def test_transform_order_merge_and_rollback():
    editor, first, slot = editor_scene()
    editor.transform_features('rotate', 45)
    assert list(editor.storage_dict[1].get_objects()) == [editor.selected[0], slot]
    assert editor.undo()
    target = editor.make_storage()
    other = drill(10)
    target.insert_many([other])
    editor.storage_dict[2] = target
    editor.points_edit[2], editor.slot_points_edit[2] = [(10, 0)], []
    editor.olddia_newdia[2] = 2
    editor.tool2tooldia['second'] = 2
    editor.transform_features('scale', 2)
    assert list(editor.storage_dict) == [1, 2]
    assert list(target.get_objects()) == [other, editor.selected[0]]
    assert editor.undo()
    editor.build_ui.side_effect = RuntimeError('render failed')
    with pytest.raises(RuntimeError, match='render failed'):
        editor.transform_features('rotate', 17)
    assert list(editor.storage_dict[1].get_objects()) == [first, slot]
    assert editor.selected == [first]


@pytest.mark.parametrize('in_place', [True])
@pytest.mark.parametrize('cancel', [False, 'export_failure'])
@pytest.mark.parametrize('export_units', ['METRIC'])
def test_real_save_build_is_detached_and_commit_is_guarded(app, tmp_path, monkeypatch, in_place, cancel, export_units):
    editor, first, second = scene()
    source = make_same_diameter_excellon(app, name='holes')
    original = source.tools
    editor.app = app
    editor.edited_obj = source
    editor.edited_obj_name = 'holes'
    source.units = 'MM'
    editor.new_tools = {1: {'tooldia': 1.0, 'drills': [Point(8, 9)],
                            'slots': [(Point(1.2, 2.3), Point(4.5, 6.7))]}}
    editor.data_defaults = {}
    editor.decimals = 4
    editor.worker_result = Mock()
    editor.deactivate = Mock(side_effect=editor.history.reset)
    app.f_handlers = appIO(app)
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 6
    app.main_thread = QtCore.QThread.currentThread()
    app.worker_task = Mock()
    created = []

    def create(kind, name, initialize):
        obj = type(source)(name, app=app)
        initialize(obj, app)
        created.append(obj)
        return obj

    app.app_obj.new_object.side_effect = create
    complete = Mock()
    assert AppExcEditor._queue_excellon_save(editor, source, in_place, complete) == 'pending'
    task = app.worker_task.emit.call_args.args[0]
    if cancel is True:
        editor.history.reset()
        editor.edited_obj = None
    elif cancel == 'export_failure':
        monkeypatch.setattr(type(source), 'regenerate_source', lambda self, **kwargs: False)
    task['fcn']()
    assert source.tools is original and not created
    editor.history.finish(editor.worker_result.emit.call_args.args[0])
    assert editor.history.pending is None
    if cancel:
        complete.assert_not_called()
        assert source.tools is original and not created
        if cancel == 'export_failure':
            assert editor.edited_obj is source
            editor.deactivate.assert_not_called()
    else:
        complete.assert_called_once()
        saved = source if in_place else created[0]
        assert saved.tools[1]['drills'][0].equals(Point(8, 9))
        assert saved.tools[1]['data']['tag'] == 'first'
        assert saved.source_file
        again = reparse_excellon(app, saved.source_file, tmp_path)
        factor = 25.4 if again.units == 'IN' else 1.0
        start, stop = again.tools[1]['slots'][0]
        assert (start.x * factor, start.y * factor) == pytest.approx((1.2, 2.3), abs=0.001)
        assert (stop.x * factor, stop.y * factor) == pytest.approx((4.5, 6.7), abs=0.001)
        if not in_place:
            assert source.tools is original
            assert saved.history_uid != source.history_uid


@pytest.mark.parametrize('variant', ['linear', 'circular'])
@pytest.mark.parametrize('selection_limit', [1])
@pytest.mark.parametrize('direction', ['CW'])
def test_copy_variants_keep_source_diameters_native_slots_counts_and_history(variant, selection_limit, direction):
    editor, first, slot = scene()
    editor.storage_dict[1.0].remove(slot)
    editor.storage_dict[2.0] = AppGeoEditor.make_storage()
    editor.storage_dict[2.0].insert(slot)
    editor.points_edit = {1.0: [(0, 0)]}
    editor.slot_points_edit = {2.0: [((4, 0), (6, 1))]}
    editor.selected = [first, slot]
    slot.exc_slot = ((4, 0), (6, 1))
    slot.exc_slot_geometry = slot.geo
    editor.delete_utility_geometry = Mock()
    tool = CopyEditorExc.__new__(CopyEditorExc)
    tool.draw_app = editor
    tool.origin, tool.destination = (0, 0), (10, 20)
    tool.selected_dia_list = [1.0, 2.0]
    tool.sel_limit = selection_limit
    tool.selection_shape = first.geo.envelope
    tool.draw_cursor_data = Mock()
    tool.ui = Mock()
    tool.ui.mode_radio.get_value.return_value = 'n' if variant == 'n' else 'a'
    tool.ui.array_type_radio.get_value.return_value = variant
    tool.ui.axis_radio.get_value.return_value = 'X'
    tool.ui.pitch_entry.get_value.return_value = 3
    tool.ui.linear_angle_spinner.get_value.return_value = 0
    tool.ui.array_size_entry.get_value.return_value = 2
    tool.ui.placement_radio.get_value.return_value = 'o'
    tool.ui.rows.get_value.return_value = 1
    tool.ui.columns.get_value.return_value = 2
    tool.ui.offsetx_entry.get_value.return_value = 3
    tool.ui.offsety_entry.get_value.return_value = 0
    tool.ui.array_dir_radio.get_value.return_value = direction
    tool.ui.angle_entry.get_value.return_value = 90
    tool.points = [(0, 0)]
    tool.utility_geometry((10, 20))
    tool.make()
    count = 1 if variant == 'n' else 2
    assert len(list(editor.storage_dict[1.0].get_objects())) == 1 + count
    slots = list(editor.storage_dict[2.0].get_objects())
    assert len(slots) == 1 + count
    for index, copied in enumerate(slots[1:]):
        dx, dy = copied.geo.centroid.x - slot.geo.centroid.x, copied.geo.centroid.y - slot.geo.centroid.y
        if variant == 'circular' and index == 1:
            sign = 1 if direction == 'CCW' else -1
            assert copied.exc_slot[0] == pytest.approx((-20 * sign, 14 * sign))
            assert copied.exc_slot[1] == pytest.approx((-21 * sign, 16 * sign))
        else:
            assert copied.exc_slot == ((4 + dx, dy), (6 + dx, 1 + dy))
        assert copied.exc_slot_geometry is copied.geo
    assert len(editor.points_edit[1.0]) == 1 + count
    assert len(editor.slot_points_edit[2.0]) == 1 + count
    assert editor.undo()
    assert list(editor.storage_dict[2.0].get_objects()) == [slot]
    assert editor.redo()
    assert list(editor.storage_dict[2.0].get_objects()) == slots


def test_move_preserves_native_slot_endpoints_and_undo_redo():
    editor, first, slot = scene()
    editor.selected = [slot]
    slot.exc_slot = ((4, 0), (6, 1))
    slot.exc_slot_geometry = slot.geo
    editor.delete_utility_geometry = Mock()
    tool = MoveEditorExc.__new__(MoveEditorExc)
    tool.draw_app = editor
    tool.origin, tool.destination = (0, 0), (2, 3)
    tool.selected_dia_list = [1.0]
    tool.make()
    moved = list(editor.storage_dict[1.0].get_objects())[-1]
    assert moved.exc_slot[0] == pytest.approx((6, 3))
    assert moved.exc_slot[1] == pytest.approx((8, 4))
    assert moved.exc_slot_geometry is moved.geo
    assert editor.undo()
    assert list(editor.storage_dict[1.0].get_objects())[-1] is slot
    assert editor.redo()
    assert list(editor.storage_dict[1.0].get_objects())[-1] is moved


@pytest.mark.parametrize('kind', ['drill_scale', 'unknown'])
def test_save_rejects_unrepresentable_features_before_commit(app, kind):
    editor, first, slot = scene()
    source = make_same_diameter_excellon(app)
    original = source.tools
    if kind == 'drill_scale':
        first.geo = scale(first.geo, xfact=2, yfact=1)
    elif kind == 'drill_skew':
        first.geo = skew(first.geo, xs=20)
    elif kind == 'slot_scale':
        slot.geo = scale(slot.geo, xfact=1, yfact=2)
    elif kind == 'slot_skew':
        slot.geo = skew(slot.geo, xs=30)
    elif kind == 'unknown':
        first.geo = LineString([(0, 0), (1, 1)])
    else:
        slot.geo = Polygon()
    editor._save_excellon_in_place = Mock(return_value='pending')
    assert AppExcEditor.update_fcexcellon(editor, source, in_place=True) == 'fail'
    editor._save_excellon_in_place.assert_not_called()
    assert source.tools is original
    assert editor.history.pending is None


@pytest.mark.parametrize('axis,pitch,angle,point', [
    ('X', float('nan'), 0, (0, 0)), ('bad', 1, 0, (0, 0))])
def test_invalid_linear_drill_array_does_not_touch_counters(axis, pitch, angle, point):
    editor, first, slot = scene()
    tool = DrillArray.__new__(DrillArray)
    tool.draw_app = editor
    tool.drill_array, tool.drill_array_size = 'linear', 3
    tool.drill_axis, tool.drill_pitch, tool.drill_linear_angle = axis, pitch, angle
    tool.points = point
    tool.ui = Mock()
    before = list(editor.points_edit[1.0])
    assert tool.make() == 'fail'
    assert editor.points_edit[1.0] == before
    assert tool.geometry == []
    assert not tool.complete


def slot_tool(cls, direction='A', length=5.0, angle=31.0):
    editor, _, _ = scene()
    editor.app.defaults = SimpleNamespace(get=lambda *args: 64, factory_defaults={})
    tool = cls.__new__(cls)
    tool.draw_app = editor
    tool.ui = Mock()
    tool.ui.slot_length_entry.get_value.return_value = length
    tool.ui.slot_direction_radio.get_value.return_value = direction
    tool.ui.slot_angle_spinner.get_value.return_value = angle
    tool.ui.slot_angle_entry.get_value.return_value = angle
    tool.slot_point = (10, 20)
    tool.points = (10, 20)
    tool.selected_dia = 1.0
    tool.slot_array = 'linear'
    tool.slot_array_size = 3
    tool.slot_axis = 'X'
    tool.slot_pitch = 7
    return tool


@pytest.mark.parametrize('cls, direction', [(SlotAdd, 'A'), (SlotArray, 'X')])
def test_native_creation_and_linear_array(cls, direction):
    tool = slot_tool(cls, direction)
    tool.make()
    shapes = tool.geometry if isinstance(tool.geometry, list) else [tool.geometry]
    assert len(shapes) == (3 if cls is SlotArray else 1)
    angle = math.radians(31)
    vector = (2, 0) if direction == 'X' else (0, 2) if direction == 'Y' else (
        2 * math.sin(angle), 2 * math.cos(angle))
    for index, shape in enumerate(shapes):
        x, y = 10 + index * 7, 20
        assert shape.exc_slot[0] == pytest.approx((x - vector[0], y - vector[1]))
        assert shape.exc_slot[1] == pytest.approx((x + vector[0], y + vector[1]))
        assert shape.exc_slot_geometry is shape.geo
        assert not feature_properties(shape, 1, index)['inferred']
        assert LineString(shape.exc_slot).buffer(0.5).hausdorff_distance(shape.geo) < 0.01


@pytest.mark.parametrize('direction', ['CCW'])
def test_circular_array_rotates_native_endpoints_with_geometry(direction):
    tool = slot_tool(SlotArray)
    tool.origin = (4, 9)
    tool.ui.array_direction_radio.get_value.return_value = direction
    tool.ui.array_angle_entry.get_value.return_value = 60
    tool.ui.array_size_entry.get_value.return_value = 3
    shapes = tool.circular_util_shape(10, 0.4)
    for shape in shapes:
        assert math.dist(*shape.exc_slot) == pytest.approx(4)
        assert shape.exc_slot_geometry is shape.geo
        assert LineString(shape.exc_slot).buffer(0.5).hausdorff_distance(shape.geo) < 0.01


@pytest.mark.parametrize('cls, length', [(SlotAdd, 0), (SlotArray, float('nan'))])
def test_invalid_slot_creation_leaves_counts_and_storage_unchanged(cls, length):
    tool = slot_tool(cls, length=length)
    before = list(tool.draw_app.slot_points_edit[1.0])
    assert tool.make() == 'fail'
    assert not tool.complete
    assert tool.draw_app.slot_points_edit[1.0] == before
    assert len(list(tool.draw_app.storage_dict[1.0].get_objects())) == 2


@pytest.mark.parametrize('cls, circular', [(SlotAdd, False), (SlotArray, True)])
@pytest.mark.parametrize('export_units', ['METRIC'])
def test_created_and_copied_slots_history_save_and_native_roundtrip(app, tmp_path, cls, export_units, circular):
    tool = slot_tool(cls)
    if circular and cls is SlotArray:
        tool.slot_array = 'circular'
        tool.slot_angle = 60
        tool.origin, tool.destination = (4, 9), (14, 9)
        tool.ui.array_direction_radio.get_value.return_value = 'CCW'
        tool.ui.array_angle_entry.get_value.return_value = 60
        tool.ui.array_size_entry.get_value.return_value = 3
    tool.make()
    editor = tool.draw_app
    storage = editor.storage_dict[1.0]
    storage.replace(removed=list(storage.get_objects()), inserted=[])
    shapes = tool.geometry if isinstance(tool.geometry, list) else [tool.geometry]
    copied = translated_exc_shape(shapes[0], translate(shapes[0].geo, xoff=2.3, yoff=-4.5))
    shapes = shapes + [copied]
    editor.add_exc_shape(shapes, storage)
    assert editor.undo()
    assert list(storage.get_objects()) == []
    assert editor.redo()
    assert list(storage.get_objects()) == shapes
    source = make_same_diameter_excellon(app)
    editor._save_excellon_in_place = Mock()
    AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    source.tools = editor.new_tools
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 6
    app.f_handlers = appIO(app)
    assert source.regenerate_source()
    again = reparse_excellon(app, source.source_file, tmp_path)
    factor = 25.4 if again.units == 'IN' else 1.0
    slots = again.tools[1]['slots']
    assert len(slots) == len(shapes)
    for shape, (start, stop) in zip(shapes, slots):
        assert (start.x * factor, start.y * factor) == pytest.approx(shape.exc_slot[0], abs=0.001)
        assert (stop.x * factor, stop.y * factor) == pytest.approx(shape.exc_slot[1], abs=0.001)


def endpoint_scene():
    editor, first, slot = scene()
    slot.exc_slot = ((4.0, 0.0), (6.0, 1.0))
    slot.exc_slot_geometry = slot.geo
    editor.selected = [slot]
    editor.delete_utility_geometry = Mock()
    editor.ui = SimpleNamespace()
    for name in ('edit_slot_endpoints', 'on_slot_endpoints_apply', 'load_slot_endpoints'):
        method = getattr(exc.AppExcEditor, name, None)
        assert method is not None, 'Native endpoint editing is missing: ' + name
        setattr(editor, name, MethodType(method, editor))
    return editor, first, slot


def test_endpoint_replacement_preserves_order_width_selection_and_history():
    editor, first, slot = endpoint_scene()
    before_counts = list(editor.slot_points_edit[1.0])
    result = editor.edit_slot_endpoints(slot, ((4, 0), (10, 3)))
    replacement = list(editor.storage_dict[1.0].get_objects())[1]
    assert result != 'fail'
    assert replacement is not slot
    assert replacement.exc_slot == ((4, 0), (10, 3))
    assert replacement.exc_slot_geometry is replacement.geo
    assert replacement.geo.equals(LineString(replacement.exc_slot).buffer(0.5))
    assert editor.selected == [replacement]
    assert list(editor.storage_dict[1.0].get_objects())[0] is first
    assert editor.slot_points_edit[1.0] == before_counts
    assert editor.tool2tooldia == {1: 1.0}
    assert editor.history.revision == 1
    assert len(editor.history.undo_stack) == 1
    editor.replot.assert_called_once()
    assert editor.undo()
    assert editor.selected == [slot]
    assert list(editor.storage_dict[1.0].get_objects()) == [first, slot]
    assert slot.exc_slot_geometry is slot.geo
    assert editor.redo()
    assert editor.selected == [replacement]


@pytest.mark.parametrize('endpoints', [((4, 0), (4, 0)), ((float('nan'), 0), (9, 1))])
def test_invalid_endpoints_are_atomic(endpoints):
    editor, first, slot = endpoint_scene()
    assert editor.edit_slot_endpoints(slot, endpoints) == 'fail'
    assert list(editor.storage_dict[1.0].get_objects()) == [first, slot]
    assert not editor.history.undo_stack
    assert not editor.is_modified
    editor.replot.assert_not_called()


def test_numeric_endpoint_controls_use_the_same_operation():
    editor, first, slot = endpoint_scene()
    editor.ui.slot_endpoint_entries = [Mock() for _ in range(4)]
    editor.load_slot_endpoints()
    for entry, value in zip(editor.ui.slot_endpoint_entries, (4, 0, 6, 1)):
        entry.set_value.assert_called_once_with(value)
    for entry, value in zip(editor.ui.slot_endpoint_entries, (4, 0, 9, 7)):
        entry.get_value.return_value = value
    editor.on_slot_endpoints_apply()
    assert editor.selected[0].exc_slot == ((4, 0), (9, 7))
    assert len(editor.history.undo_stack) == 1
    for entry, value in zip(editor.ui.slot_endpoint_entries, (3, -1, 9, 7)):
        entry.get_value.return_value = value
    editor.on_slot_endpoints_apply()
    assert editor.selected[0].exc_slot == ((3, -1), (9, 7))
    assert len(editor.history.undo_stack) == 2


@pytest.mark.parametrize('units, export_units', [('MM', 'INCH'), ('IN', 'METRIC')])
def test_edited_resized_slot_roundtrip_both_document_and_export_units(app, tmp_path, units, export_units):
    editor, first, slot = endpoint_scene()
    factor = 25.4 if units == 'IN' else 1.0
    diameter = 1.0 / factor
    slot.geo = scale(slot.geo, xfact=1 / factor, yfact=1 / factor, origin=(0, 0))
    slot.exc_slot = tuple(tuple(v / factor for v in point) for point in slot.exc_slot)
    slot.exc_slot_geometry = slot.geo
    storage = AppGeoEditor.make_storage()
    storage.insert(slot)
    editor.storage_dict = {diameter: storage}
    editor.tool2tooldia = {1: diameter}
    editor.olddia_newdia = {diameter: diameter}
    editor.points_edit, editor.slot_points_edit = {diameter: []}, {diameter: [slot.exc_slot]}
    endpoints = ((4 / factor, 0), (11.1234 / factor, 7.6789 / factor))
    replacement = editor.edit_slot_endpoints(slot, endpoints)
    # 2.54 mm / 0.1 inch is exactly representable in both existing tool-header formats.
    editor.resize_features([replacement], 2.54 / factor)
    resized = editor.selected[0]
    assert resized.exc_slot == endpoints
    assert editor.undo() and editor.redo()
    source = make_same_diameter_excellon(app)
    source.units = units
    app.app_units = units
    editor._save_excellon_in_place = Mock()
    exc.AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    source.tools = editor.new_tools
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 6
    app.f_handlers = appIO(app)
    assert source.regenerate_source()
    again = reparse_excellon(app, source.source_file, tmp_path)
    back = (25.4 if again.units == 'IN' else 1) / factor
    tool = next(iter(again.tools.values()))
    start, stop = tool['slots'][0]
    assert (start.x * back, start.y * back) == pytest.approx(endpoints[0], abs=0.001 / factor)
    assert (stop.x * back, stop.y * back) == pytest.approx(endpoints[1], abs=0.001 / factor)
    assert tool['tooldia'] * back == pytest.approx(2.54 / factor, abs=0.001 / factor)


OFFSET_LABEL = 'Offset selected features'


MSELECT_KEY = 'Control'


CONTROL = QtCore.Qt.KeyboardModifier.ControlModifier


NO_MODIFIER = QtCore.Qt.KeyboardModifier.NoModifier


def cross(x, y, size=1.0):
    """A drill as the editor stores it: a MultiLineString cross whose arms are the drill diameter."""
    r = size / 2
    return DrawToolShape(MultiLineString([[(x - r, y), (x + r, y)], [(x, y - r), (x, y + r)]]))


def slot_shape():
    """A native-looking slot 4 units long, 1 unit wide, from (0, 0) to (4, 0)."""
    return DrawToolShape(LineString([(0, 0), (4, 0)]).buffer(0.5))


def pick_editor():
    """A real AppExcEditor with no storages: the canvas reports 0.01 plot units per pixel (PICK_TOLERANCE_PX = 5)."""
    app, editor = qt_editor('MM')
    app.plotcanvas.pixel_size = Mock(return_value=0.01)
    app.options['global_mselect_key'] = MSELECT_KEY
    editor.ui = SimpleNamespace(tools_table_exc=MagicMock())
    editor.storage_dict, editor.tool2tooldia, editor.selected = {}, {}, []
    editor.last_tool_selected = 0
    return app, editor


def put(editor, dia, *shapes):
    """Store shapes in the tool storage of diameter dia, in the given order, and number the tools like build_ui()."""
    storage = AppExcEditor.make_storage()
    storage.insert_many(list(shapes))
    editor.storage_dict[dia] = storage
    editor.tool2tooldia = {index: d for index, d in enumerate(sorted(editor.storage_dict), 1)}
    return storage


@contextmanager
def held(modifier):
    """Pretend the keyboard modifier is held during the click."""
    with patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=modifier):
        yield


def same(actual, expected):
    """Same objects in the same order (DrawToolShape equality is identity, this states it explicitly)."""
    return len(actual) == len(expected) and all(a is b for a, b in zip(actual, expected))


def flat(points):
    return [coordinate for point in points for coordinate in point]


def offset_editor():
    """The scene() stand-in with the real transform_features: a drill at (0, 0) and a slot (4, 0)-(6, 1), both 1.0."""
    editor, drill_shape, slot = scene()
    editor.transform_features = MethodType(AppExcEditor.transform_features, editor)
    return editor, drill_shape, slot


def test_repeated_click_on_stacked_drills_cycles_a_b_a():
    app, editor = pick_editor()
    first, second = cross(5, 5), cross(5, 5)
    put(editor, 1.0, first, second)
    picked = []
    for _ in range(3):
        assert click(editor, (5, 5)) == 1
        picked.append(editor.selected[0])
    assert picked[0] is first and picked[1] is second and picked[2] is first


def test_mselect_modifier_adds_and_removes_features():
    app, editor = pick_editor()
    first, second = cross(0, 0), cross(10, 0)
    put(editor, 1.0, first, second)
    click(editor, (0, 0))
    assert same(editor.selected, [first])
    with held(CONTROL):
        click(editor, (10, 0))
        assert same(editor.selected, [first, second])
        click(editor, (0, 0))            # toggles the feature off again
    assert same(editor.selected, [second])


def click(editor, point):
    """Click at point; returns the number of selected features (the cycle sets one at a time)."""
    editor.click_select(point)
    return len(editor.selected)


def test_offset_moves_drill_and_slot_and_keeps_diameters():
    editor, drill_shape, slot = offset_editor()
    assert flat(native_feature_properties(slot, 1.0, 0)['endpoints']) == pytest.approx([4, 0, 6, 1])
    slot_area = slot.geo.area
    editor.selected = [drill_shape, slot]
    assert editor.transform_features('offset', (2, 3)) != 'fail'

    shapes = list(editor.storage_dict[1.0].get_objects())
    assert len(shapes) == 2 and list(editor.storage_dict) == [1.0]
    moved_drill = next(s for s in shapes if native_feature_properties(s, 1.0, 0)['type'] == 'drill')
    moved_slot = next(s for s in shapes if s is not moved_drill)

    assert native_feature_properties(moved_drill, 1.0, 0)['center'] == pytest.approx((2, 3))
    bounds = moved_drill.geo.bounds
    assert bounds[2] - bounds[0] == pytest.approx(1.0)          # the drill keeps its 1.0 diameter
    assert flat(native_feature_properties(moved_slot, 1.0, 0)['endpoints']) == pytest.approx([6, 3, 8, 4])
    assert flat(moved_slot.exc_slot) == pytest.approx([6, 3, 8, 4])   # the native centerline moved too
    assert moved_slot.geo.area == pytest.approx(slot_area)      # same width, only translated
    assert editor.tool2tooldia == {1: 1.0}
    assert editor.is_modified


def test_offset_with_nothing_selected_fails_and_warns():
    editor, drill_shape, slot = offset_editor()
    editor.selected = []
    assert editor.transform_features('offset', (1, 1)) == 'fail'
    assert warned(editor)
    assert not editor.history.undo_stack


def editor_scene_exc_feature_filters():
    editor, drill, slot = scene()
    for name in ('filter_features', 'select_filtered_features', 'delete_filtered_features', 'on_history_row'):
        setattr(editor, name, MethodType(getattr(AppExcEditor, name), editor))
    return editor, drill, slot


def test_filter_selection_scope_and_one_step_deletion():
    editor, drill, slot = editor_scene_exc_feature_filters()
    assert [r['shape'] for r in editor.filter_features(kind='slot')] == [slot]
    assert not editor.filter_features(kind='slot', selected_only=True)
    editor.selected = [drill, slot]
    assert [r['shape'] for r in editor.filter_features(length=(2, 3))] == [slot]
    assert [r['shape'] for r in editor.filter_features(bounds=(-1, -1, 1, 1))] == [drill]
    assert not editor.filter_features(diameter=(2, 3))
    assert editor.delete_filtered_features(kind='slot', selected_only=True) == 1
    assert editor.selected == [drill]
    assert list(editor.storage_dict[1.0].get_objects()) == [drill]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.selected == [drill, slot]
    assert list(editor.storage_dict[1.0].get_objects()) == [drill, slot]


@pytest.mark.parametrize('filters', [dict(diameter=(float('nan'), 2)), dict(kind='polygon')])
def test_invalid_filters_are_atomic(filters):
    editor, drill, slot = editor_scene_exc_feature_filters()
    assert editor.delete_filtered_features(**filters) == 'fail'
    assert editor.selected == [drill]
    assert list(editor.storage_dict[1.0].get_objects()) == [drill, slot]
    assert not editor.history.undo_stack


def test_real_feature_controls_selection_filters_and_undo():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    editor, drill, slot = editor_scene_exc_feature_filters()
    editor.ui = AppExcEditorUI(SimpleNamespace(decimals=4, app_units='MM', resource_location='assets/resources'))
    for name in ('on_feature_filter_action', 'on_feature_selection'):
        setattr(editor, name, MethodType(getattr(AppExcEditor, name), editor))
    editor.ui.tools_table_exc.setRowCount(3)  # One tool and two summary rows.
    editor.ui.tools_table_exc.setItem(0, 0, QtWidgets.QTableWidgetItem('1'))
    editor.ui.tools_table_exc.setItem(0, 1, QtWidgets.QTableWidgetItem('1.0'))
    editor.ui.feature_table.itemSelectionChanged.connect(lambda: editor.on_feature_selection())
    editor.ui.feature_select.clicked.connect(lambda: editor.on_feature_filter_action())
    editor.ui.feature_delete.clicked.connect(lambda: editor.on_feature_filter_action(delete=True))
    editor.refresh_feature_table()
    assert editor.ui.feature_table.rowCount() == 2
    editor.ui.feature_table.selectRow(1)
    assert editor.selected == [slot]
    assert editor.ui.tools_table_exc.item(0, 0).isSelected()
    editor.ui.feature_kind.setCurrentIndex(2)
    editor.ui.feature_ranges['length'].setText('2, 3')
    editor.ui.feature_select.click()
    assert editor.selected == [slot]
    editor.ui.feature_delete.click()
    assert list(editor.storage_dict[1.0].get_objects()) == [drill]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict[1.0].get_objects()) == [drill, slot]
    assert editor.selected == [slot]


def snapping_scene():
    editor, drill, slot = scene()
    slot.exc_slot = ((4, 0), (6, 1))
    slot.exc_slot_geometry = slot.geo
    editor.snap = MethodType(AppExcEditor.snap, editor)
    editor.app.grid_status = lambda: True
    editor.app.geo_editor = SimpleNamespace(editor_options={'global_snap_max': 0.3, 'grid_snap': False},
                                           storage=Mock())
    editor.ui = SimpleNamespace(snap_modes={kind: Mock(isChecked=Mock(return_value=True))
                                           for kind in ('center', 'endpoint', 'midpoint', 'nearest')},
                                snap_result=Mock())
    return editor, drill, slot


def test_cache_reuse_edit_undo_reset_and_grid_priority():
    editor, drill, slot = snapping_scene()
    editor.snap(4, 0)
    tree = editor._exc_snap_index
    storage = editor.storage_dict[1.0]
    original = storage.get_objects
    storage.get_objects = Mock(side_effect=AssertionError('Unexpected mouse-move scan'))
    for _ in range(100):
        assert editor.snap(4.01, 0) == (4, 0)
    assert editor._exc_snap_index is tree
    storage.get_objects = original
    editor.selected = [slot]
    editor.delete_selected()
    assert editor.snap(4.01, 0) == (4.01, 0)
    editor.undo()
    assert editor.snap(4.01, 0) == (4, 0)
    editor.history.reset()
    assert editor._exc_snap_index is None
    options = editor.app.geo_editor.editor_options
    options.update(grid_snap=True, global_gridx=0.1, global_gridy=0.1)
    editor.app.ui = SimpleNamespace(grid_gap_link_cb=Mock(isChecked=Mock(return_value=True)))
    assert editor.snap(4.11, 0) == pytest.approx((4.1, 0))
    assert editor.snap_kind == 'grid'
    editor.app.grid_status = lambda: False
    assert editor.snap(4.01, 0.01) == (4.01, 0.01)
    assert editor.snap_kind is None


@pytest.mark.parametrize('tool_name', ['drill_select', 'slot_add'])
def test_canvas_click_picks_raw_but_places_at_local_snap(tool_name):
    editor, _, _ = snapping_scene()
    editor.active_tool = SimpleNamespace(name=tool_name)
    editor.canvas = SimpleNamespace(translate_coords=lambda point: point)
    editor.on_canvas_click_left_handler = Mock()
    AppExcEditor.on_canvas_click(editor, SimpleNamespace(button=1, pos=(4.01, 0.01)))
    assert editor.clicked_pos == ((4.01, 0.01) if tool_name == 'drill_select' else (4, 0))
    editor.on_canvas_click_left_handler.assert_called_once()


def drill_shape(dia, x, y):
    # the '+' sign of a drill in the editor, the line length is the tool diameter
    r = dia / 2
    return DrawToolShape(MultiLineString([((x - r, y), (x + r, y)), ((x, y - r), (x, y + r))]))


def slot_shape_exc_editor_slot_tools(dia, start, stop):
    return DrawToolShape(LineString([start, stop]).buffer(dia / 2))


def save(drills=None, slots=None):
    """Run the editor save. drills: {dia: [(x, y), ...]}, slots: {dia: [((x1, y1), (x2, y2)), ...]}.
    Returns the tools dict passed to the new Excellon object."""
    storage_dict = {}
    for dia, points in (drills or {}).items():
        storage = storage_dict.setdefault(dia, AppGeoEditor.make_storage())
        for x, y in points:
            storage.insert(drill_shape(dia, x, y))
    for dia, segments in (slots or {}).items():
        storage = storage_dict.setdefault(dia, AppGeoEditor.make_storage())
        for start, stop in segments:
            storage.insert(slot_shape_exc_editor_slot_tools(dia, start, stop))

    editor = SimpleNamespace(
        storage_dict=storage_dict, olddia_newdia={dia: dia for dia in storage_dict}, new_tools={},
        history=SimpleNamespace(ready=lambda: True, pending=None), _save_excellon_in_place=Mock())
    source = SimpleNamespace(geo_steps_per_circle=64)
    AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    editor._save_excellon_in_place.assert_called_once_with(source, None)
    return editor.new_tools


def summary(tools):
    """{tool number: (diameter, sorted drill centers, sorted slot ends)} with rounded coordinates.
    The save finds the slot ends again from the slot outline and they move a few hundredths of a mm (not the subject
    of these tests), so the slot ends are rounded to 0.1 mm."""
    out = {}
    for tid, tool in tools.items():
        drills = sorted((round(p.x, 2), round(p.y, 2)) for p in tool.get('drills', []))
        slots = sorted(((round(a.x, 1), round(a.y, 1)), (round(b.x, 1), round(b.y, 1)))
                       for a, b in tool.get('slots', []))
        out[tid] = (tool['tooldia'], drills, slots)
    return out


def assert_geometry_matches(tools):
    # one shape for each drill and slot of the tool, made with the diameter of the tool
    for tid, tool in tools.items():
        dia = tool['tooldia']
        drills, slots = tool.get('drills', []), tool.get('slots', [])
        assert len(tool['solid_geometry']) == len(drills) + len(slots), tid
        for poly, point in zip(tool['solid_geometry'], drills):
            assert poly.distance(point) == 0, tid
            assert poly.bounds[2] - poly.bounds[0] == pytest.approx(dia, abs=1e-3), tid
        for poly in tool['solid_geometry'][len(drills):]:
            # all the slots of these tests are horizontal
            assert poly.bounds[3] - poly.bounds[1] == pytest.approx(dia, abs=1e-3), tid


SLOT_10 = ((10.0, 0.0), (14.0, 0.0))


SLOT_15 = ((20.0, 0.0), (25.0, 0.0))


# drills 0.8 / 1.0 / 3.2 mm and slots 1.0 / 1.5 mm (KiCad oval pads and mounting holes)
BOARD = dict(drills={0.8: [(0, 0), (1, 0)], 1.0: [(0, 5)], 3.2: [(30, 30), (-30, 30)]},
             slots={1.0: [SLOT_10], 1.5: [SLOT_15]})


# drill-only tools and a slot-only tool
SLOT_ONLY_TOOL = dict(drills={0.8: [(0, 0)], 3.2: [(30, 30)]}, slots={1.5: [SLOT_15]})


def test_new_slot_diameter_after_a_matching_one_does_not_overwrite_a_drill_tool():
    tools = save(**BOARD)

    assert summary(tools) == {
        1: (0.8, [(0, 0), (1, 0)], []),
        2: (1.0, [(0, 5)], [SLOT_10]),
        3: (3.2, [(-30, 30), (30, 30)], []),
        4: (1.5, [], [SLOT_15]),
    }
    assert_geometry_matches(tools)


def exported_holes(code):
    """{tool number: (sorted drill lines, number of slots)} of the body made by ExcellonObject.export_excellon():
    first the drills of each tool, then from the first G05 the slots of each tool."""
    holes, tool, in_slots = {}, None, False
    for line in code.splitlines():
        if line == 'G05':
            in_slots = True
        elif line.startswith('T'):
            tool = int(line[1:])
            holes.setdefault(tool, [[], 0])
        elif in_slots and line.startswith('G00X'):
            holes[tool][1] += 1
        elif not in_slots and line.startswith('X'):
            holes[tool][0].append(line)
    return {tid: (sorted(drills), slots) for tid, (drills, slots) in holes.items()}


@pytest.mark.parametrize('case, expected', [
    (BOARD, {1: (['X0.000Y0.000', 'X1.000Y0.000'], 0), 2: (['X0.000Y5.000'], 1),
             3: (['X-30.000Y30.000', 'X30.000Y30.000'], 0), 4: ([], 1)}),
], ids=['board'])
def test_excellon_export_of_the_saved_object(no_shape_collection, case, expected):  # noqa: F811
    # the edited object gets the saved tools like AppExcEditor.new_edited_excellon() does
    obj = ExcellonObject('board_edit', make_app())
    obj.units = 'MM'
    obj.tools = deepcopy(save(**case))
    for tool in obj.tools.values():
        tool['data'] = {}
    obj.create_geometry()

    has_slots, code = obj.export_excellon(3, 3, e_zeros='LZ', form='dec', slot_type='routing')
    assert has_slots == 1
    assert exported_holes(code) == expected


def drill_job_gcode(tools):
    """G-code of the Tcl drillcncjob with all the tools ('default' preprocessor) of an Excellon with these tools."""
    app = make_app(tools_drill_toolchange=True)
    app.preprocessors['default'] = importlib.import_module('preprocessors.default').default()
    app.dec_format = round
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[],
                                    travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    source = Excellon(app)
    source.units = 'MM'
    source.tools = deepcopy(tools)
    for tool in source.tools.values():
        tool['data'] = {}
    source.create_geometry()
    source.kind = 'excellon'
    source.obj_options = {'name': 'board_edit'}
    source.obj_options.update(app.options)
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    app.collection.get_by_name.return_value = source

    job = CNCjob(app)
    job.kind = 'cncjob'
    job.obj_options = {}
    job.excellon_tool_gcode_parse = Mock(return_value=[])

    def create(kind, name, init, **kwargs):
        assert init(job, app) != 'fail'
        return job

    app.app_obj.new_object.side_effect = create
    TclCommandDrillcncjob(app).execute({'name': 'board_edit', 'pp': 'default', 'drilled_dias': 'all'}, [])
    return job.source_file


def drills_by_tool_change(gcode):
    """{tool change message: sorted drill positions after it}; a drill is a rapid X-Y move followed by a plunge"""
    out, message, rapid = {}, None, None
    for line in gcode.splitlines():
        if line.startswith('(MSG, Change to Tool Dia'):
            message = line
            out[message] = []
        elif message and re.fullmatch(r'G00 X-?\d+\.\d{4} Y-?\d+\.\d{4}', line):
            rapid = line[4:]
        elif message and rapid and line.startswith('G01 Z'):
            out[message].append(rapid)
            rapid = None
    return {msg: sorted(positions) for msg, positions in out.items()}


@pytest.mark.parametrize('case, expected', [
    (SLOT_ONLY_TOOL, {
        '(MSG, Change to Tool Dia = 0.8000 ||| Total drills for tool T1 = 1)': ['X0.0000 Y0.0000'],
        '(MSG, Change to Tool Dia = 3.2000 ||| Total drills for tool T2 = 1)': ['X30.0000 Y30.0000'],
    }),
], ids=['slot-only-tool'])
def test_drillcncjob_with_all_tools_of_the_saved_object(case, expected):
    assert drills_by_tool_change(drill_job_gcode(save(**case))) == expected
