"""Regression tests for the Excellon Editor defect fixes: units, menu actions, tool table, history labels, warnings,
translated labels and the diagnostics tolerance. Headless: the editor is real, the app is a stand-in."""

from concurrent.futures import ThreadPoolExecutor
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.affinity import translate
from shapely.geometry import Point

from appEditors.appExcEditor import AppExcEditor, CopyEditorExc, DrillArray, MoveEditorExc, SlotArray
from appEditors.appGeoEditor import AppGeoEditor, DrawTool
from appEditors.exc_plugins.ExcCopyPlugin import ExcCopyEditorUI
from appGUI.MainGUI import MainGUI
from defaults import AppDefaults
from test_exc_editor_history import drill, scene
from tests.helpers_objects import _headless_qt  # noqa: F401,E402

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


# ---------------------------------------------------------------------------------------------------------------
# 1. units: edit_fcexcellon takes the units from the app at edit time
# ---------------------------------------------------------------------------------------------------------------

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


@pytest.mark.parametrize('units,tooldia,xy', [('MM', 0.8, (3.25, -1.5)), ('IN', 0.04, (1.0, 2.0))])
def test_load_then_save_round_trip_is_identity(units, tooldia, xy):
    app, editor = qt_editor(units)
    editor.edit_fcexcellon(native_obj(units, tooldia, xy))
    editor._save_excellon_in_place = Mock()
    editor.update_fcexcellon(SimpleNamespace(geo_steps_per_circle=64), in_place=True)
    tool = editor.new_tools[1]
    assert tool['tooldia'] == pytest.approx(tooldia)
    assert (tool['drills'][0].x, tool['drills'][0].y) == pytest.approx(xy)


# ---------------------------------------------------------------------------------------------------------------
# 2. building the editor twice leaves one set of editor actions
# ---------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------
# 3. transform keeps tool2tooldia sorted; the next add lands on its own row
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# 4. diameters are rounded to self.decimals on add and on edit
# ---------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------
# 5. nested operation labels: the innermost real operation names the step; still one undo step
# ---------------------------------------------------------------------------------------------------------------

def test_nested_move_inside_generic_draw_records_inner_label_as_one_step():
    editor, first, second = scene()
    with editor.history.operation('Draw features', True) as accepted:
        assert accepted
        with editor.history.operation('Move features') as inner:
            assert inner
            first.geo = translate(first.geo, xoff=3)
    assert len(editor.history.undo_stack) == 1
    assert editor.history.undo_stack[0]['label'] == 'Move features'
    assert editor.undo()
    assert len(editor.history.undo_stack) == 0


# ---------------------------------------------------------------------------------------------------------------
# 6. activate() hides the app corner snap button
# ---------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------
# 7. warnings instead of silent failures
# ---------------------------------------------------------------------------------------------------------------


def test_history_rollback_refreshes_when_only_selection_changed():
    editor, first, second = scene()
    with pytest.raises(ValueError):
        with editor.history.operation('Select then fail'):
            editor.selected = [second]
            raise ValueError('boom')
    assert editor.selected == [first]
    editor.build_ui.assert_called()


# ---------------------------------------------------------------------------------------------------------------
# 9. translated display and units-aware diagnostics tolerance
# ---------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------
# 10. the 'Add' button of the Drill Array panel uses the values of the panel
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# 11. a mouse move before the center click keeps the radius typed for a circular array
# ---------------------------------------------------------------------------------------------------------------

SLOT_ARRAY_PANEL = dict(array_type_radio='linear', array_size_entry=5, array_axis_radio='X', array_pitch_entry=2.54,
                        array_linear_angle_entry=0.0, array_direction_radio='CW', array_angle_entry=30.0,
                        radius_entry=0.0, slot_length_entry=2.0, slot_direction_radio='X', slot_angle_entry=0.0)


@pytest.mark.parametrize('cls', [DrillArray, SlotArray])
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


# ---------------------------------------------------------------------------------------------------------------
# 12. Copy, Array mode, Circular type: preview and copies without an earlier Linear preview
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# 13. Copy: a typed length + Enter copies at that distance from the reference point
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# 14. Enter with the Move tool clicks at the cursor
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# 15. the Copy panel has no 'Add' button that does nothing
# ---------------------------------------------------------------------------------------------------------------

def test_copy_panel_hides_its_unconnected_add_button():
    qapp()
    panel = QtWidgets.QWidget()
    app = SimpleNamespace(decimals=4, resource_location='assets/resources', inform=Mock())
    ui = ExcCopyEditorUI(layout=QtWidgets.QVBoxLayout(panel), copy_class=SimpleNamespace(app=app), plugin_name='Copy')
    assert not ui.add_button.isVisibleTo(panel)
    assert ui.clear_btn.isVisibleTo(panel) and ui.mode_radio.isVisibleTo(panel)
