"""Gerber-local snapping, the Space key of the Track and Pad Array tools, feature selection and range deletion
controls."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import unary_union
from vispy.geometry.rect import Rect
from vispy.util import keys
from vispy.util.event import EventEmitter

from appEditors.appGeoEditor import AppGeoEditor
from appEditors.appGerberEditor import AppGerberEditor, PadArrayEditorGrb
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appEditors.grb_plugins.GrbTrackPlugin import GrbTrackEditorUI
from appGUI.MainGUI import MainGUI
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from test_gerber_editor_pick import aperture, make_editor
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


@pytest.mark.parametrize('grid', [True, False])
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


def test_track_pad_snap_survives_completed_routes_until_tool_is_closed():
    pad = DrawToolShape({'solid': Point(0.23, 0.37).buffer(0.8), 'follow': Point(0.23, 0.37)})
    editor = snap_editor([pad])
    editor.last_aperture_selected = 10
    editor.select_tool = AppGerberEditor.select_tool.__get__(editor)
    with patch('appEditors.appGerberEditor.GrbTrackEditorTool.run'):
        editor.select_tool('track')
        editor.active_tool.ui.snap_to_pad_cb.setChecked(True)
        editor.active_tool.click((2, 2))
        editor.active_tool.click((4, 2))
        editor.app.ui.popMenu.mouse_is_panning = False
        editor.on_canvas_click_release(SimpleNamespace(pos=(400, 200), button=2, is_dragging=False))
        assert editor.active_tool.ui.snap_to_pad_cb.isChecked()
        assert editor.snap(0.01, 0.01) == (0.23, 0.37)
        editor.select_tool('select')
        editor.select_tool('track')
        assert not editor.active_tool.ui.snap_to_pad_cb.isChecked()


@pytest.mark.parametrize('linked', [False, True])
def test_holding_space_refines_live_track_preview_and_committed_endpoint(linked):
    pad = DrawToolShape({'solid': Point(10.23, 10.37).buffer(0.8), 'follow': Point(10.23, 10.37)})
    editor = snap_editor([pad])
    options = editor.app.geo_editor.editor_options
    options.update(grid_snap=True, global_gridx=1.0, global_gridy=2.0)
    editor.app.ui.grid_gap_link_cb.isChecked.return_value = linked
    editor.last_aperture_selected = 10
    editor.select_tool = AppGerberEditor.select_tool.__get__(editor)
    key_release = EventEmitter(type='key_release')
    editor.canvas.graph_event_connect = lambda name, callback: key_release.connect(callback) \
        if name == 'key_release' else None
    editor.canvas.graph_event_disconnect = lambda name, callback: key_release.disconnect(callback) \
        if name == 'key_release' else None
    editor.connect_canvas_event_handlers()
    with patch('appEditors.appGerberEditor.GrbTrackEditorTool.run'):
        editor.select_tool('track')
        editor.active_tool.ui.snap_to_pad_cb.setChecked(True)
        editor.active_tool.click((0, 0))
        editor.active_tool.cursor_data_control = False
        move = SimpleNamespace(pos=(126, 226), button=None, is_dragging=False)
        editor.on_canvas_move(move)
        normal = (1.0, 2.0)
        fine = (1.3, 2.3 if linked else 2.2)
        assert (editor.snap_x, editor.snap_y) == normal
        gui = SimpleNamespace(app=editor.app, grb_select_btn=SimpleNamespace(isChecked=lambda: False))
        MainGUI.keyPressEvent(gui, SimpleNamespace(key='Space', native=SimpleNamespace()))
        assert (editor.snap_x, editor.snap_y) == pytest.approx(fine)
        assert editor.active_tool.temp_points[-1] == pytest.approx(fine)
        assert editor.snap(10.01, 10.01) == (10.23, 10.37)
        editor.on_canvas_click(SimpleNamespace(pos=move.pos, button=1, is_dragging=False))
        follow = editor.storage_dict[10]['geometry'][-1].geo['follow']
        assert tuple(follow.coords[-1]) == pytest.approx(fine)
        # Auto-repeat releases mustn't restore the coarse grid while Space remains held.
        key_release(key='Space', native=SimpleNamespace(isAutoRepeat=lambda: True))
        assert editor.snap(1.26, 2.26) == pytest.approx(fine)
        key_release(key='Space', native=SimpleNamespace(isAutoRepeat=lambda: False))
        assert (editor.snap_x, editor.snap_y) == normal
        assert editor.active_tool.temp_points[-1] == normal
        MainGUI.keyPressEvent(gui, int(QtCore.Qt.Key.Key_Space))
        editor.app.ui.popMenu.mouse_is_panning = False
        editor.on_canvas_click_release(SimpleNamespace(pos=move.pos, button=2, is_dragging=False))
        assert editor.snap(1.26, 2.26) == pytest.approx(fine)
        key_release(key=QtCore.Qt.Key.Key_Space, native=SimpleNamespace(isAutoRepeat=lambda: False))
        assert editor.snap(1.26, 2.26) == normal
        assert options['global_gridx'] == 1.0
        assert options['global_gridy'] == 2.0
    editor.disconnect_canvas_event_handlers()


def test_space_cycles_the_pad_array_direction():
    editor = make_editor({10: {'type': 'C', 'size': 1.0, 'geometry': []}})
    editor.last_aperture_selected = 10
    editor.snap_x = editor.snap_y = 0.0
    editor.last_parray_type, editor.last_parray_lin_dir, editor.last_parray_circ_dir = 'linear', 'X', 'CW'
    editor.app.call_source = 'grb_editor'
    editor.app.grb_editor = editor
    editor.active_tool = PadArrayEditorGrb(editor)
    ui = editor.active_tool.ui
    gui = SimpleNamespace(app=editor.app, grb_select_btn=SimpleNamespace(isChecked=lambda: False))
    seen = []
    for _ in range(3):
        MainGUI.keyPressEvent(gui, SimpleNamespace(key=keys.SPACE, native=SimpleNamespace()))
        seen.append((ui.axis_radio.get_value(), ui.linear_angle_entry.isEnabled()))
    # the Angle entry follows the direction, like a click on the radio button
    assert seen == [('Y', False), ('A', True), ('X', False)]


@pytest.mark.parametrize('kind', ['R', 'O'])
def test_aperture_creation_uses_separate_width_and_height(kind):
    editor = make_editor()
    editor.ui.aptype_cb.set_value(kind)
    editor.ui.apdim_entry.set_value(0.6)
    editor.ui.apheight_entry.set_value(0.8)
    assert editor.ui.apdim_entry.isEnabled()
    assert editor.ui.apheight_entry.isEnabled()
    # Size is disabled for these types; dimensions must work even when its old value is zero.
    editor.ui.apsize_entry.set_value(0)
    editor.on_aperture_add(12)
    aperture = editor.storage_dict[12]
    assert aperture['type'] == kind
    assert (aperture['width'], aperture['height']) == (0.6, 0.8)
    assert aperture['size'] == pytest.approx(1.0)
    editor.ui.aptype_cb.set_value('C')
    assert not editor.ui.apdim_entry.isEnabled()
    assert not editor.ui.apheight_entry.isEnabled()


def test_feature_rows_select_individual_records_and_sync_without_aperture_signal_loops():
    first = DrawToolShape({'solid': box(0, 0, 2, 3), 'follow': Point(1, 1)})
    second = DrawToolShape({'solid': box(5, 0, 9, 3), 'follow': LineString([(5, 0), (9, 0)])})
    editor = make_editor({10: aperture([first, second]), '11': aperture([first])})
    editor.refresh_features()
    table = editor.ui.features_table
    assert table.rowCount() == 3
    table.selectRow(1)
    assert editor.selected == [second]
    assert editor.calls == {'row': 0, 'sel': 0}
    assert 'LINESTRING' in editor.ui.geo_coords_entry.toPlainText() or 'POLYGON' in editor.ui.geo_coords_entry.toPlainText()
    editor.selected = [first]
    editor.update_ui()
    assert {idx.row() for idx in table.selectionModel().selectedRows()} == {0, 2}
    assert len(editor.ui.apertures_table.selectionModel().selectedRows()) == 2


@pytest.mark.parametrize('selection', ['aperture', 'all', 'feature'])
def test_table_selection_respects_zoom_checkbox(selection):
    first = DrawToolShape({'solid': box(0, 0, 2, 3), 'follow': Point(1, 1)})
    second = DrawToolShape({'solid': box(5, 10, 9, 13), 'follow': Point(7, 11)})
    editor = make_editor({10: aperture([first]), 11: aperture([second])})
    editor.refresh_features()
    camera = editor.app.plotcanvas.view.camera
    original_rect = Rect(100, 100, 20, 20)
    for enabled in (False, True):
        editor.ui.geo_zoom.set_value(enabled)
        camera.rect = original_rect
        if selection == 'aperture':
            editor.ui.apertures_table.cellPressed.emit(1, 1)
        elif selection == 'all':
            editor.ui.apertures_table.clearSelection()
            editor.ui.apertures_table.selectAll()
        else:
            editor.ui.features_table.clearSelection()
            editor.ui.features_table.selectRow(1)
        assert second in editor.selected
        if enabled:
            assert (camera.rect.left, camera.rect.bottom, camera.rect.right, camera.rect.top) == pytest.approx(
                (4.92, 9.94, 9.08, 13.06))
        else:
            assert camera.rect == original_rect
    camera.rect = original_rect
    editor.update_ui(zoom=False)
    assert camera.rect == original_rect


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
    ('size_area_min_entry', 'nan'), ('size_area_min_entry', '7')])
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


def test_length_only_handles_missing_solids_multipart_and_zero_length_flashes():
    follow = MultiLineString([[(0, 0), (3, 0)], [(0, 0), (0, 4)]])
    track = DrawToolShape({'follow': follow})
    flash = DrawToolShape({'follow': Point(4, 4)})
    missing = DrawToolShape({'solid': box(0, 0, 1, 1)})
    editor = make_editor({10: aperture([track, flash, missing])})
    editor.ui.size_length_chk.setChecked(True)
    editor.ui.size_length_min_entry.setText('7')
    editor.ui.size_length_max_entry.setText('7')
    assert editor.on_select_by_size() == [track]
    editor.selected = []
    editor.ui.size_length_min_entry.setText('0')
    editor.ui.size_length_max_entry.setText('0')
    assert editor.on_select_by_size() == [flash]


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
