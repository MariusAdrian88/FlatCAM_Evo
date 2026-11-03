"""Excellon-local transactions retain changed wrappers, not deep board snapshots."""

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


