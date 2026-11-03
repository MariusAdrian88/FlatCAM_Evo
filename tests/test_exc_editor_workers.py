"""Reservations reject late results without changing a subsequent editor session."""

from unittest.mock import Mock

import pytest

from test_exc_editor_history import scene, drill
from tests.helpers_objects import app, _headless_qt, make_same_diameter_excellon, reparse_excellon  # noqa: F401,E402
from appEditors.appExcEditor import AppExcEditor, CopyEditorExc, DrillArray, MoveEditorExc
from appEditors.appGeoEditor import AppGeoEditor
from concurrent.futures import ThreadPoolExecutor
from threading import get_ident
from appHandlers.appIO import appIO
from PyQt6 import QtCore
from shapely.affinity import rotate, scale, skew
from shapely.geometry import LineString, Point, Polygon


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
