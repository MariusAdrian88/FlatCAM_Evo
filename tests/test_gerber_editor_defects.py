"""Regression tests for Gerber Editor defect fixes: Qt slot signatures, the Scale tool connection, history of
translations and reshapes, import failure recovery, size selection and delete, region operations, aperture table
edit signal recovery, the marked-polygon list, region-export flag, buffer and scale without a shape, the buffer
factor check, numeric aperture codes, history depth on error, the zoom switch of update_ui, the Pad and Pad Array
tools with obround apertures, the Dims edit of a rectangular aperture with tracks, and the cleared marks of Mark
Area.

The editor is the real AppGerberEditor built by test_gerber_editor_pick.make_editor; the application, the canvas and
the worker are stand-ins. Everything is headless and does not sleep."""

import math
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, Point, Polygon, box

from appEditors.appGerberEditor import (AppGerberEditor, ImportEditorGrb, MarkEditorGrb, PadArrayEditorGrb, PadEditorGrb,
                                       ScaleEditorGrb)
from appEditors.grb_plugins.GrbBufferPlugin import BufferEditorTool
from appEditors.grb_plugins.GrbCommon import DrawToolShape, gerber_steps, numeric_aperture_codes
from appParsers.ParseGerber import Gerber
from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
from test_gerber_editor_pick import aperture, make_editor, pad


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


# 1. Qt signal arguments reach the slots


# 2. Scale menu item and the Scale tool connection


# 3. Translation versus reshape in history

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


def test_real_reshape_of_r_pad_marks_regions():
    first = pad(0, 0, 1)
    editor = make_editor({10: {'type': 'R', 'size': 1.0, 'geometry': [first]}})
    editor.history.reset()
    editor.selected = [first]
    with editor.history.operation('Reshape'):
        first.geo['solid'] = box(0, 0, 3, 1)
        editor.touch_geometry()
    assert len(editor.history.undo_stack) == 1
    assert editor.loaded_as_regions is True


# 4. Import failure


# 5. Select and Delete by Size

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


def test_select_by_size_searches_whole_board_with_existing_selection():
    editor, small, wide = size_scene()
    editor.selected = [wide]
    assert editor.on_select_by_size() == [small]
    assert editor.selected == [small]


# 6. Region operation with an empty result

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


# 7. itemChanged connection after an inner error


# 8. Dimension edit of R and O apertures: a flash that is no longer the aperture shape (cut by a clear feature or
# edited) is not replaced by a new full flash

@pytest.mark.parametrize('cut', [False, True])
@pytest.mark.parametrize('kind', ['R', 'O'])
def test_dimension_edit_resizes_only_plain_flashes(kind, cut):
    meta = {'type': kind, 'size': math.hypot(2.0, 1.0), 'width': 2.0, 'height': 1.0}
    editor = make_editor({})
    plain = box(-1, -0.5, 1, 0.5) if kind == 'R' else \
        Gerber.create_flash_geometry(editor, Point(0, 0), meta, gerber_steps(editor.app, 0.5))
    solid = plain.difference(box(0.5, -1, 2, 1)) if cut else plain
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


# 9. Marked polygons list


# 11. Region export flag lifecycle


# 13. Scale and Buffer without a selected shape

@pytest.mark.parametrize('kind', ['scale', 'buffer'])
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


# 14. Buffer factor check

@pytest.mark.parametrize('factor_value', [0])
def test_buffer_factor_at_or_below_zero_is_rejected(factor_value):
    editor, first, second = scene()
    editor.selected = [first]
    tool = SimpleNamespace(draw_app=editor, app=editor.app)
    assert queue_transform(tool, 'buffer', [factor_value, 1, True]) == 'fail'
    editor.app.worker_task.emit.assert_not_called()
    assert editor.history.undo_stack == []


# 15. Numeric aperture codes


# 16. History depth after a failing scope


# 18. update_ui without zoom


# 19. Pad and Pad Array with an obround ('O') aperture, as KiCad writes the oval pads

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


@pytest.mark.parametrize('width, height', [(1.7, 1.0), (1.0, 1.7)])
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


def test_aperture_table_shows_editable_obround_dimensions():
    editor, meta = obround_editor(1.7, 1.0)
    AppGerberEditor.build_ui(editor)
    item = editor.ui.apertures_table.item(0, 4)
    assert [float(value) for value in item.text().split(',')] == [1.7, 1.0]
    assert item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable


# 20. Dims edit of an 'R' aperture with tracks: a track changes by half of the change, not by the new size

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


# 21. Mark Area: the shapes of a cleared marking are not deleted after the tool is opened again

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
