"""Gerber Editor: local snapping, the Space key of the Track tool, range deletion, and the fixes of the editor
history, size selection, region operations, aperture edits, obround pads and Mark Area.

The editor is the real AppGerberEditor built by test_gerber_editor_pick.make_editor; the application, the canvas and
the worker are stand-ins. Everything is headless and does not sleep."""
import math
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import unary_union

from appEditors.appGerberEditor import AppGerberEditor, MarkEditorGrb, PadArrayEditorGrb, PadEditorGrb
from appEditors.grb_plugins.GrbBufferPlugin import BufferEditorTool
from appEditors.grb_plugins.GrbCommon import DrawToolShape, gerber_steps
from appEditors.grb_plugins.GrbTrackPlugin import GrbTrackEditorUI
from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from test_gerber_editor_pick import aperture, make_editor, pad
from test_editor_corner import track_tool_with_storage
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402


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


def test_filtered_delete_save_reparse_after_undo_redo(app):

    source = make_gerber(app, NATIVE_GBR, name='board')
    first = DrawToolShape({'solid': Point(4, 4).buffer(1, 16), 'follow': Point(4, 4)})
    second = DrawToolShape({'solid': Point(8, 4).buffer(1, 16), 'follow': Point(8, 4)})
    editor = make_editor({10: {'type': 'C', 'size': 2, 'geometry': [first, second]}})
    editor.selected = [first]
    editor.ui.size_width_chk.setChecked(True)
    editor.ui.size_width_entry.setText('2')
    editor.ui.size_tolerance_entry.setText('0')
    editor.on_delete_by_size()
    for mode in ('deleted', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = getattr(editor, 'loaded_as_regions', False)
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
        expected = unary_union([shape.geo['solid'] for shape in editor.storage_dict[10]['geometry']])
        assert unary_union(parsed.solid_geometry).hausdorff_distance(expected) < 0.001


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


# 2. Scale and Simplification menu items and the Scale tool connection

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

@pytest.mark.parametrize('kind, cut', [('R', False), ('R', True)])
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
